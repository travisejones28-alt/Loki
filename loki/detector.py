"""Deterministic visual matching. This module has no GUI or input API dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import AppConfig, ConfigError, Rect


class DetectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DetectionResult:
    matched: bool
    confidence: float
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    appearance: float = 0.0
    context_confidence: float = 0.0
    present: bool = False

    @property
    def bounds(self):
        return Rect(self.x, self.y, self.width, self.height)


def grayscale(image):
    if (
        image is None
        or not isinstance(image, np.ndarray)
        or image.size == 0
        or image.dtype != np.uint8
    ):
        raise DetectionError("The captured image is empty or invalid.")
    if image.ndim == 2:
        return np.ascontiguousarray(image)
    if image.ndim == 3 and image.shape[2] in (3, 4):
        return cv2.cvtColor(
            image, cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        )
    raise DetectionError("Images must be grayscale, BGR or BGRA.")


def read_image(path: Path):
    try:
        if not path.is_file() or path.stat().st_size > 10_000_000:
            raise DetectionError("The saved template is missing or too large. Please recalibrate.")
        # imdecode supports Windows filenames containing non-ASCII characters.
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise DetectionError("The saved template is unreadable. Please recalibrate.")
        return image
    except OSError as exc:
        raise DetectionError("The saved template could not be opened. Please recalibrate.") from exc


class TemplateDetector:
    def __init__(self, template, config: AppConfig, context=None):
        config.validate()
        self.config = config
        try:
            self.template = grayscale(template)
            self.height, self.width = self.template.shape
            if self.width < 8 or self.height < 8 or self.width * self.height > 500_000:
                raise DetectionError(
                    "Select the full Run WHO button, between 8 pixels and 500,000 pixels in area."
                )
            if float(self.template.std()) < 8:
                raise DetectionError(
                    "The selected template has too little detail. Include the Run WHO text and button edges."
                )
            self.context = grayscale(context) if context is not None else None
            if self.context is not None:
                ox, oy = config.context_offset_x, config.context_offset_y
                ch, cw = self.context.shape
                if ox + self.width > cw or oy + self.height > ch:
                    raise DetectionError(
                        "The context template does not contain the button. Please recalibrate."
                    )
                if float(self.context.std()) < 5:
                    raise DetectionError(
                        "The popup context has too little detail. Please recalibrate."
                    )
            self._scores = None
            self._score_shape = None
        except cv2.error as exc:
            raise DetectionError("OpenCV could not read the selected template.") from exc

    @classmethod
    def from_config(cls, config: AppConfig, root: Path):
        config.validate(require_calibration=True)
        template = read_image(root / config.template_path)
        context = read_image(root / config.context_path) if config.context_path else None
        return cls(template, config, context)

    def detect(self, frame, origin=(0, 0)) -> DetectionResult:
        try:
            gray = grayscale(frame)
            fh, fw = gray.shape
            if self.width > fw or self.height > fh:
                raise DetectionError(
                    "The button template is larger than the search area. Please recalibrate."
                )
            shape = (fh - self.height + 1, fw - self.width + 1)
            if shape != self._score_shape:
                self._scores = np.empty(shape, dtype=np.float32)
                self._score_shape = shape
            cv2.matchTemplate(gray, self.template, cv2.TM_CCOEFF_NORMED, self._scores)
            _, maximum, _, position = cv2.minMaxLoc(self._scores)
            x, y = position
            confidence = float(np.clip(maximum, 0, 1)) if np.isfinite(maximum) else 0.0
            patch = gray[y : y + self.height, x : x + self.width]
            appearance = 1 - float(np.abs(patch.astype(np.int16) - self.template).mean()) / 255
            context_confidence = 1.0
            if self.context is not None:
                cx, cy = x - self.config.context_offset_x, y - self.config.context_offset_y
                ch, cw = self.context.shape
                if cx < 0 or cy < 0 or cx + cw > fw or cy + ch > fh:
                    context_confidence = 0.0
                else:
                    surrounding = gray[cy : cy + ch, cx : cx + cw]
                    score = cv2.matchTemplate(surrounding, self.context, cv2.TM_CCOEFF_NORMED)[0, 0]
                    context_confidence = float(np.clip(score, 0, 1)) if np.isfinite(score) else 0.0
            matched = (
                confidence >= self.config.confidence_threshold
                and appearance >= self.config.appearance_threshold
                and context_confidence >= self.config.context_threshold
            )
            # Hysteresis keeps hover/brightness changes from falsely re-arming a visible popup.
            present = (
                confidence >= self.config.release_threshold
                and appearance >= 0.65
                and context_confidence >= self.config.release_threshold
            )
            return DetectionResult(
                matched,
                confidence,
                x + origin[0],
                y + origin[1],
                self.width,
                self.height,
                appearance,
                context_confidence,
                present,
            )
        except (cv2.error, ConfigError) as exc:
            raise DetectionError(
                "Visual matching failed. Please test or recalibrate the button."
            ) from exc
