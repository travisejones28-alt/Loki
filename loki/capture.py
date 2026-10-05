"""MSS capture of a single configured physical-pixel region."""

from __future__ import annotations

import cv2
import mss
import numpy as np

from .config import AppConfig, ConfigError, Monitor, Rect
from .windows import native_monitors


class CaptureError(RuntimeError):
    pass


def list_monitors() -> list[Monitor]:
    try:
        native = native_monitors()
        with mss.mss() as session:
            monitors = []
            for number, data in enumerate(session.monitors[1:], 1):
                bounds = Rect(data["left"], data["top"], data["width"], data["height"])
                record = next((item for item in native if item.bounds == bounds), None)
                monitors.append(
                    Monitor(
                        number,
                        record.name if record else f"Monitor {number}",
                        bounds,
                        record.scale if record else 1.0,
                    )
                )
            if not monitors:
                raise CaptureError("No connected monitor was found.")
            return monitors
    except (mss.ScreenShotError, OSError) as exc:
        raise CaptureError(
            "Windows screen capture is unavailable. Check the connected display and try again."
        ) from exc


def validate_display(config: AppConfig, monitors: list[Monitor]):
    config.validate(require_calibration=True)
    saved = config.monitor
    current = next((monitor for monitor in monitors if monitor.name == saved.name), None)
    if current is None:
        raise CaptureError(
            "The calibrated monitor is disconnected. Reconnect it or calibrate another monitor."
        )
    if current.bounds != saved.bounds or abs(current.scale - saved.scale) > 0.01:
        raise CaptureError(
            "The display resolution, position or DPI scale changed. Please calibrate again."
        )
    if not current.bounds.contains(config.search_region):
        raise ConfigError("The capture area is outside the monitor. Please recalibrate.")
    return current


class ScreenCapture:
    """Construct and use on the same worker thread (MSS thread-local handles)."""

    def __init__(self):
        try:
            self._session = mss.mss()
        except (mss.ScreenShotError, OSError) as exc:
            raise CaptureError(
                "Screen capture could not start. Check your monitor and try again."
            ) from exc

    def grab(self, region: Rect):
        region.validate()
        try:
            image = np.asarray(self._session.grab(region.as_capture()))
            return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        except (mss.ScreenShotError, cv2.error, OSError) as exc:
            raise CaptureError(
                "The selected screen area could not be captured. Stop and recalibrate."
            ) from exc

    def close(self):
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
