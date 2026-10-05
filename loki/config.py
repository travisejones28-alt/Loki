"""Validated configuration, physical-pixel geometry and atomic persistence."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

MAX_REGION_PIXELS = 2_000_000
STOP_KEYS = tuple(f"Ctrl+Shift+F{n}" for n in range(6, 12))


class ConfigError(ValueError):
    pass


def _integer(value, label: str, minimum: int | None = None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ConfigError(
            f"{label} must be an integer"
            + (f" of at least {minimum}." if minimum is not None else ".")
        )


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    def validate(self):
        _integer(self.left, "Rectangle left")
        _integer(self.top, "Rectangle top")
        _integer(self.width, "Rectangle width", 1)
        _integer(self.height, "Rectangle height", 1)

    @property
    def right(self):
        return self.left + self.width

    @property
    def bottom(self):
        return self.top + self.height

    def contains(self, other: Rect):
        return (
            self.left <= other.left
            and self.top <= other.top
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def as_capture(self):
        return asdict(self)


@dataclass(frozen=True)
class Monitor:
    number: int
    name: str
    bounds: Rect
    scale: float = 1.0

    def validate(self):
        _integer(self.number, "Monitor number", 1)
        if not isinstance(self.name, str) or not self.name:
            raise ConfigError("A monitor name is required.")
        self.bounds.validate()
        if (
            isinstance(self.scale, bool)
            or not isinstance(self.scale, (int, float))
            or not 0.5 <= self.scale <= 4
        ):
            raise ConfigError("Monitor DPI scale is invalid.")


@dataclass(frozen=True)
class AppConfig:
    schema_version: int = 1
    monitor: Monitor | None = None
    search_region: Rect | None = None
    template_path: str = ""
    context_path: str = ""
    context_offset_x: int = 0
    context_offset_y: int = 0
    confidence_threshold: float = 0.94
    appearance_threshold: float = 0.85
    context_threshold: float = 0.90
    release_threshold: float = 0.86
    confirmation_frames: int = 4
    disappearance_frames: int = 5
    fps: int = 10
    sound_enabled: bool = True
    overlay_enabled: bool = True
    debug_screenshots: bool = False
    start_with_windows: bool = False
    monitor_on_launch: bool = False
    stop_hotkey: str = "Ctrl+Shift+F11"

    @property
    def calibrated(self):
        return (
            self.monitor is not None and self.search_region is not None and bool(self.template_path)
        )

    def validate(self, require_calibration=False):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ConfigError("This configuration version is not supported. Please recalibrate.")
        for name in (
            "confidence_threshold",
            "appearance_threshold",
            "context_threshold",
            "release_threshold",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.5 <= value <= 1
            ):
                raise ConfigError(f"{name.replace('_', ' ')} must be between 0.50 and 1.00.")
        if self.release_threshold > self.confidence_threshold:
            raise ConfigError("Release threshold cannot exceed the detection threshold.")
        for name in ("confirmation_frames", "disappearance_frames"):
            value = getattr(self, name)
            _integer(value, name.replace("_", " "), 2)
            if value > 30:
                raise ConfigError("Frame counts cannot exceed 30.")
        _integer(self.fps, "Capture FPS", 5)
        if self.fps > 15:
            raise ConfigError("Capture FPS must be between 5 and 15.")
        for name in (
            "sound_enabled",
            "overlay_enabled",
            "debug_screenshots",
            "start_with_windows",
            "monitor_on_launch",
        ):
            if type(getattr(self, name)) is not bool:
                raise ConfigError(f"{name} must be true or false.")
        if self.stop_hotkey not in STOP_KEYS:
            raise ConfigError("Stop hotkey must be Ctrl+Shift+F6 through Ctrl+Shift+F11.")
        _integer(self.context_offset_x, "Context X offset", 0)
        _integer(self.context_offset_y, "Context Y offset", 0)
        for name in ("template_path", "context_path"):
            path = getattr(self, name)
            if not isinstance(path, str):
                raise ConfigError(f"{name} must be a relative filename.")
            if path and (
                Path(path).is_absolute() or ".." in Path(path).parts or "\\" in path or ":" in path
            ):
                raise ConfigError("Template filenames must stay inside Loki's data folder.")
        any_calibration = (
            self.monitor is not None
            or self.search_region is not None
            or bool(self.template_path)
            or bool(self.context_path)
        )
        if any_calibration and not self.calibrated:
            raise ConfigError("Calibration is incomplete. Please calibrate again.")
        if require_calibration and not self.calibrated:
            raise ConfigError("Select Calibrate and save a target template before starting.")
        if self.calibrated:
            self.monitor.validate()
            self.search_region.validate()
            if not self.monitor.bounds.contains(self.search_region):
                raise ConfigError(
                    "The search region is outside the selected monitor. Please recalibrate."
                )
            if self.search_region.width * self.search_region.height > MAX_REGION_PIXELS:
                raise ConfigError(
                    "Select a smaller area around the popup (at most 2 million pixels)."
                )

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ConfigError("Configuration must be a JSON object.")
        unknown = set(data) - {field.name for field in fields(cls)}
        if unknown:
            raise ConfigError("Unknown configuration fields: " + ", ".join(sorted(unknown)))
        try:
            values = dict(data)
            if values.get("monitor") is not None:
                record = dict(values["monitor"])
                record["bounds"] = Rect(**record["bounds"])
                values["monitor"] = Monitor(**record)
            if values.get("search_region") is not None:
                values["search_region"] = Rect(**values["search_region"])
            config = cls(**values)
            config.validate()
            return config
        except (TypeError, KeyError, ValueError) as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError("Calibration data is invalid. Please recalibrate.") from exc


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @classmethod
    def default(cls):
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        return cls(base / "Loki")

    @property
    def config(self):
        return self.root / "config.json"

    def ensure(self):
        for folder in (self.root, self.root / "templates", self.root / "logs"):
            folder.mkdir(parents=True, exist_ok=True)


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_config(config: AppConfig, paths: AppPaths):
    config.validate()
    atomic_write(
        paths.config, (json.dumps(asdict(config), indent=2, allow_nan=False) + "\n").encode()
    )


def load_config(paths: AppPaths) -> tuple[AppConfig, str | None]:
    if not paths.config.exists():
        return AppConfig(), None
    try:
        if paths.config.stat().st_size > 128_000:
            raise ConfigError("Configuration file is too large.")
        return AppConfig.from_dict(json.loads(paths.config.read_text(encoding="utf-8"))), None
    except (OSError, ValueError, TypeError) as exc:
        # Preserve the damaged file for diagnosis; never silently run bad calibration.
        backup = paths.root / (
            "config.corrupt-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json"
        )
        try:
            paths.config.replace(backup)
            backups = sorted(paths.root.glob("config.corrupt-*.json"))
            for old in backups[:-3]:
                old.unlink(missing_ok=True)
            note = f"The unreadable configuration was kept as {backup.name}."
        except OSError:
            note = "The original configuration could not be moved."
        return (
            AppConfig(),
            f"Loki could not load its configuration: {exc}\n{note}\nPlease calibrate again.",
        )
