"""Controlled-rate background capture; the GUI alone owns sounds and overlays."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from .capture import ScreenCapture, list_monitors, validate_display
from .config import AppConfig, AppPaths
from .detector import DetectionResult, TemplateDetector
from .logging_utils import save_debug_image
from .state_machine import DetectionLatch, StateUpdate


@dataclass(frozen=True)
class FrameReport:
    result: DetectionResult
    update: StateUpdate
    fps: float
    frame: np.ndarray | None = None
    captured_at: float = field(default_factory=time.monotonic)


class VisualPipeline:
    """One detector shared by continuous recognition and the final action-time probe."""

    def __init__(self, config, paths):
        self.config = config
        self.detector = TemplateDetector.from_config(config, paths.root)
        self._lock = threading.RLock()

    def detect(self, frame, origin):
        with self._lock:
            return self.detector.detect(frame, origin)

    def probe(self, expected):
        validate_display(self.config, list_monitors())
        region = self.config.search_region
        with ScreenCapture() as capture:
            frame = capture.grab(region)
        result = self.detect(frame, (region.left, region.top))
        return result if result.matched and result.bounds == expected.bounds else None


class MonitorWorker(QThread):
    report = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths,
        diagnostic=False,
        capture_factory=ScreenCapture,
        monitor_provider=list_monitors,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.paths = paths
        self.diagnostic = diagnostic
        self.capture_factory = capture_factory
        self.monitor_provider = monitor_provider
        self._stop = threading.Event()
        self.pipeline = None

    def stop(self):
        self._stop.set()

    def run(self):
        logger = logging.getLogger("loki")
        try:
            cv2.setNumThreads(1)  # Avoid competing with WoW for a pool of worker threads.
            self.config.validate(require_calibration=True)
            validate_display(self.config, self.monitor_provider())
            self.pipeline = VisualPipeline(self.config, self.paths)
            latch = DetectionLatch(
                self.config.confirmation_frames, self.config.disappearance_frames
            )
            region = self.config.search_region
            origin = (region.left, region.top)
            interval = 1.0 / self.config.fps
            last_display_check = time.monotonic()
            last_report = last_display_check
            last_frame = None
            frames = 0
            actual_fps = 0.0
            with self.capture_factory() as capture:
                while not self._stop.is_set():
                    started = time.monotonic()
                    if started - last_display_check >= 2:
                        validate_display(self.config, self.monitor_provider())
                        last_display_check = started
                    frame = capture.grab(region)
                    result = self.pipeline.detect(frame, origin)
                    if self._stop.is_set():
                        break
                    update = latch.update(result)
                    if not self.diagnostic:
                        if update.candidate_started:
                            logger.info(
                                "CANDIDATE confidence=%.3f context=%.3f x=%d y=%d",
                                result.confidence,
                                result.context_confidence,
                                result.x,
                                result.y,
                            )
                        if update.triggered:
                            logger.info(
                                "DETECT confidence=%.3f x=%d y=%d width=%d height=%d",
                                result.confidence,
                                result.x,
                                result.y,
                                result.width,
                                result.height,
                            )
                            logger.info("TRIGGER target=%s", self.config.template_path)
                            if self.config.debug_screenshots:
                                try:
                                    save_debug_image(self.paths, frame, result, origin)
                                except Exception as exc:
                                    # A diagnostic disk failure must never disrupt normal monitoring.
                                    logger.warning("Debug screenshot failed: %s", exc)
                        if update.rearmed:
                            logger.info("TARGET LOST")
                            logger.info("ARMED")
                    frames += 1
                    if last_frame is not None and started > last_frame:
                        instantaneous = 1.0 / (started - last_frame)
                        actual_fps = (
                            instantaneous
                            if not actual_fps
                            else actual_fps * 0.8 + instantaneous * 0.2
                        )
                    last_frame = started
                    # Normal reports are tiny. Diagnostic images are limited to 5 per second.
                    if not self.diagnostic or frames == 1 or started - last_report >= 0.2:
                        self.report.emit(
                            FrameReport(
                                result,
                                update,
                                actual_fps,
                                frame if self.diagnostic else None,
                                started,
                            )
                        )
                        last_report = started
                    self._stop.wait(max(0.005, interval - (time.monotonic() - started)))
        except Exception as exc:
            logger.exception("Monitoring failed")
            self.failed.emit(
                str(exc) or "Screen monitoring failed. Please recalibrate and try again."
            )
