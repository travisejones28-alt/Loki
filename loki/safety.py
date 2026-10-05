"""Fail-closed policy. All metadata access is injected and all coordinates are physical."""

from dataclasses import dataclass

from .capture import validate_display
from .identity import normalized_path
from .protection import matches, protected_reason


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str = ""
    temporary: bool = False


def same_process_window(expected, actual):
    return (
        actual.hwnd == expected.hwnd
        and actual.pid == expected.pid
        and actual.created == expected.created
        and normalized_path(actual.executable_path) == normalized_path(expected.executable_path)
        and actual.executable_name.casefold() == expected.executable_name.casefold()
        and actual.class_name == expected.class_name
    )


class SafetyGuard:
    def __init__(self, profile, workspace, desktop):
        self.profile = profile
        self.config = profile.detection
        self.workspace = workspace
        self.desktop = desktop

    def protected(self, identity):
        return protected_reason(
            identity,
            self.workspace.protected_executables,
            self.workspace.protected_titles,
            self.workspace.protected_paths,
        )

    def validate(self, result=None, require_foreground=True):
        try:
            self.profile.validate()
            if not self.profile.automation_capable or self.profile.intended_mode != "offline":
                return SafetyDecision(
                    False, "Profile is MONITOR ONLY or not configured for Offline Automation"
                )
            self.config.validate(require_calibration=True)
            binding = self.profile.target
            if binding is None:
                return SafetyDecision(False, "Bind a running target window before arming")
            if self.protected(binding.window):
                return SafetyDecision(False, "Protected application in target profile")
            target = self.desktop.inspect_window(binding.window.hwnd)
            target.validate()
            if self.protected(target):
                return SafetyDecision(False, "Protected target: " + self.protected(target))
            if not same_process_window(binding.window, target):
                return SafetyDecision(
                    False, "TARGET WINDOW lost or process identity changed; rebind"
                )
            title_valid = (
                matches(target.title, (binding.title_pattern,))
                if binding.title_pattern
                else target.title == binding.window.title
            )
            if (
                not title_valid
                or target.bounds != binding.window.bounds
                or target.client_bounds != binding.window.client_bounds
            ):
                return SafetyDecision(
                    False, "Target title or bounds changed; rebind and check calibration"
                )
            validate_display(self.config, self.desktop.displays())
            if require_foreground:
                foreground = self.desktop.foreground()
                foreground.validate()
                protected = self.protected(foreground)
                if protected:
                    return SafetyDecision(
                        False, "PROTECTED APPLICATION foreground: " + protected, True
                    )
                if not same_process_window(target, foreground):
                    return SafetyDecision(False, "Configured target is not foreground", True)
            if result is not None:
                if not result.matched:
                    return SafetyDecision(False, "Target disappeared or no longer matches", True)
                bounds = result.bounds
                bounds.validate()
                if (
                    not self.config.monitor.bounds.contains(bounds)
                    or not self.config.search_region.contains(bounds)
                    or not target.client_bounds.contains(bounds)
                ):
                    return SafetyDecision(
                        False,
                        "Detected target is outside monitor, search area or target client window",
                    )
                point = (bounds.left + bounds.width // 2, bounds.top + bounds.height // 2)
                owner = self.desktop.window_at(point)
                if self.protected(owner):
                    return SafetyDecision(
                        False, "Protected application under the target coordinate", True
                    )
                if not same_process_window(target, owner):
                    return SafetyDecision(False, "Another window covers the detected target", True)
            return SafetyDecision(True)
        except Exception as exc:
            return SafetyDecision(False, "Unable to verify target safely: " + str(exc))

    def point(self, result):
        result.bounds.validate()
        return result.x + result.width // 2, result.y + result.height // 2
