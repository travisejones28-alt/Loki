"""Offline-only action scheduling. No detector/capture algorithm or implicit arming."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

from .safety import SafetyGuard


@dataclass(frozen=True)
class PendingAction:
    result: object
    due: float
    epoch: int
    input_stamp: int
    cursor: tuple[int, int]


class ActionController:
    def __init__(
        self, profile, workspace, desktop, visual_verify, input_factory=None, clock=time.monotonic
    ):
        self.profile = profile
        self.desktop = desktop
        self.guard = SafetyGuard(profile, workspace, desktop)
        self.visual_verify = visual_verify
        self.input_factory = input_factory
        self.clock = clock
        self.armed = False
        self.pending = None
        self.backend = None
        self.latest = None
        self.latest_at = 0.0
        self.claimed_appearance = False
        self.blocked = ""
        self.focus_epoch = desktop.focus_epoch
        self._authorization = None
        self.logger = logging.getLogger("loki")

    def arm(self):
        self.disarm("explicit re-arm")
        decision = self.guard.validate(require_foreground=False)
        if not decision.allowed:
            raise ValueError(decision.reason)
        self.armed = True
        self.focus_epoch = self.desktop.focus_epoch
        self.logger.info(
            "AUTOMATION ARMED profile=%s dry_run=%s action=%s",
            self.profile.name,
            self.profile.dry_run,
            self.profile.action,
        )

    def cancel(self, reason="cancelled"):
        if self.pending is not None:
            self.logger.info("ACTION cancelled reason=%s", reason)
        self.pending = None
        self._authorization = None

    def disarm(self, reason="user stop"):
        self.cancel(reason)
        if self.backend is not None:
            self.backend.disarm()
        self.armed = False
        if getattr(self, "logger", None):
            self.logger.info("AUTOMATION DISARMED reason=%s", reason)

    def close(self):
        self.disarm("mode/profile transition")
        self.desktop.close()

    def _blocked(self, decision):
        self.cancel(decision.reason)
        if self.blocked != decision.reason:
            self.logger.warning("AUTOMATION BLOCKED %s", decision.reason)
        self.blocked = decision.reason
        if not decision.temporary:
            self.disarm(decision.reason)

    def observe(self, report):
        self.latest, self.latest_at = report.result, report.captured_at
        if report.update.rearmed:
            self.claimed_appearance = False
        if self.pending and (
            not report.result.matched or report.result.bounds != self.pending.result.bounds
        ):
            self.cancel("Target disappeared, changed, or moved during delay")
        if not self.armed:
            return
        # Every report is checked for a focus change before it can queue an action.
        self.tick(execute=False)
        if not self.armed or self.blocked:
            if report.update.triggered:
                self.claimed_appearance = True  # blocked appearance is never queued for later.
            return
        if not report.update.triggered or self.claimed_appearance:
            return
        self.claimed_appearance = True
        if self.profile.action == "alert":
            return
        if report.captured_at <= self.desktop.focus_changed_at:
            self.logger.info("ACTION cancelled stale detection predates foreground change")
            return
        decision = self.guard.validate(report.result)
        if not decision.allowed:
            self._blocked(decision)
            return
        try:
            self.pending = PendingAction(
                report.result,
                self.clock() + self.profile.delay_ms / 1000,
                self.desktop.focus_epoch,
                self.desktop.input_stamp(),
                self.desktop.cursor_position(),
            )
            self.logger.info(
                "ACTION pending action=%s delay_ms=%d", self.profile.action, self.profile.delay_ms
            )
            self.tick()
        except Exception as exc:
            self.disarm(str(exc))

    def _authorize(self, point):
        lease = self._authorization
        if not self.armed or lease is None or point != self.guard.point(lease.result):
            return False
        if self.desktop.focus_epoch != lease.epoch or self.clock() - self._verified_at > 0.15:
            return False
        try:
            return (
                self.desktop.input_stamp() == lease.input_stamp
                and not self.desktop.buttons_or_modifiers_down()
                and self.guard.validate(lease.result).allowed
            )
        except Exception:
            return False

    def tick(self, execute=True):
        if not self.armed:
            return
        if self.desktop.focus_epoch != self.focus_epoch:
            self.cancel("foreground changed; no queued action can survive Alt-Tab")
            self.focus_epoch = self.desktop.focus_epoch
        decision = self.guard.validate()
        if not decision.allowed:
            self._blocked(decision)
            return
        self.blocked = ""
        if not execute or self.pending is None:
            return
        pending = self.pending
        try:
            if self.desktop.focus_epoch != pending.epoch:
                self.cancel("foreground epoch changed")
                return
            if (
                self.desktop.input_stamp() != pending.input_stamp
                or math.dist(self.desktop.cursor_position(), pending.cursor) > 12
                or self.desktop.buttons_or_modifiers_down()
            ):
                self.cancel("manual mouse/keyboard intervention")
                return
            if (
                not self.latest
                or not self.latest.matched
                or self.latest.bounds != pending.result.bounds
                or self.clock() - self.latest_at > max(0.35, 3 / self.profile.detection.fps)
            ):
                self.cancel("detection stale or missing")
                return
            if self.clock() < pending.due:
                return
            # Probe the SAME visual pipeline once more immediately before dispatch.
            verified = self.visual_verify(pending.result)
            if verified is None or not verified.matched or verified.bounds != pending.result.bounds:
                self.cancel("final visual validation failed")
                return
            decision = self.guard.validate(verified)
            if not decision.allowed:
                self._blocked(decision)
                return
            self._authorization = pending
            self._verified_at = self.clock()
            point = self.guard.point(verified)
            self.pending = None  # consume once before calling anything capable of input.
            if not self._authorize(point):
                self.cancel("final foreground or input validation failed")
                return
            if self.profile.dry_run:
                self.logger.info(
                    "DRY RUN WOULD %s x=%d y=%d",
                    "CLICK" if self.profile.action == "click" else "MOVE",
                    *point,
                )
                return
            if self.backend is None:
                if self.input_factory is None:
                    from .input_controller import windows_input_factory

                    self.input_factory = windows_input_factory
                self.backend = self.input_factory(self._authorize)
            self.backend.arm()
            self.backend.execute(self.profile.action, point)
        except Exception as exc:
            self.disarm(str(exc))
            self.blocked = "Input/validation error: " + str(exc)
            self.logger.exception("Automation failed closed")
        finally:
            self._authorization = None
