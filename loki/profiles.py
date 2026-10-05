"""Schema 2 workspace and named profiles; the existing detection config stays intact."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime

from .config import STOP_KEYS, AppConfig, AppPaths, ConfigError, atomic_write
from .identity import TargetBinding
from .protection import profile_is_protected

MONITOR = "monitor"
OFFLINE = "offline"
ACTIONS = ("alert", "move", "click")


@dataclass(frozen=True)
class Profile:
    id: str = "wow-voidlink-who"
    name: str = "WoW - VoidLink WHO"
    detection: AppConfig = field(default_factory=AppConfig)
    monitor_only: bool = True
    intended_mode: str = MONITOR
    action: str = "alert"
    delay_ms: int = 0
    dry_run: bool = True
    target: TargetBinding | None = None

    @property
    def automation_capable(self):
        return not profile_is_protected(self)

    def validate(self):
        if not isinstance(self.id, str) or not self.id or len(self.id) > 100:
            raise ConfigError("Profile identity is invalid.")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 120:
            raise ConfigError("Profile name must contain 1–120 characters.")
        self.detection.validate()
        if self.intended_mode not in (MONITOR, OFFLINE) or self.action not in ACTIONS:
            raise ConfigError("Profile mode or action is invalid.")
        if type(self.monitor_only) is not bool or type(self.dry_run) is not bool:
            raise ConfigError("Profile flags must be true or false.")
        if type(self.delay_ms) is not int or not 0 <= self.delay_ms <= 5000:
            raise ConfigError("Action delay must be between 0 and 5000 milliseconds.")
        if self.target is not None:
            self.target.validate()
        if profile_is_protected(self) and (
            not self.monitor_only or self.intended_mode != MONITOR or self.action != "alert"
        ):
            raise ConfigError("World of Warcraft/protected profiles must be Monitor Only.")

    @classmethod
    def from_dict(cls, data):
        values = dict(data)
        values["detection"] = AppConfig.from_dict(values["detection"])
        if values.get("target") is not None:
            values["target"] = TargetBinding.from_dict(values["target"])
        profile = cls(**values)
        # Protection cannot be removed by editing JSON flags.
        if profile_is_protected(profile):
            profile = replace(profile, monitor_only=True, intended_mode=MONITOR, action="alert")
        profile.validate()
        return profile


@dataclass(frozen=True)
class WorkspaceConfig:
    schema_version: int = 2
    profiles: tuple[Profile, ...] = field(default_factory=lambda: (Profile(),))
    active_profile_id: str = "wow-voidlink-who"
    start_with_windows: bool = False
    monitor_on_launch: bool = False
    stop_hotkey: str = "Ctrl+Shift+F11"
    protected_executables: tuple[str, ...] = ()
    protected_titles: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()

    @property
    def active(self):
        return next(profile for profile in self.profiles if profile.id == self.active_profile_id)

    @property
    def runtime_detection(self):
        return replace(
            self.active.detection,
            start_with_windows=self.start_with_windows,
            monitor_on_launch=self.monitor_on_launch,
            stop_hotkey=self.stop_hotkey,
        )

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ConfigError("Unsupported Loki workspace version.")
        if not isinstance(self.profiles, tuple) or not 1 <= len(self.profiles) <= 50:
            raise ConfigError("Loki supports 1–50 profiles.")
        for profile in self.profiles:
            profile.validate()
        ids = [profile.id for profile in self.profiles]
        if len(set(ids)) != len(ids) or self.active_profile_id not in ids:
            raise ConfigError("Profile IDs must be unique and the active profile must exist.")
        for flag in (self.start_with_windows, self.monitor_on_launch):
            if type(flag) is not bool:
                raise ConfigError("Startup settings must be true or false.")
        if self.stop_hotkey not in STOP_KEYS:
            raise ConfigError("Invalid emergency stop shortcut.")
        for rules in (self.protected_executables, self.protected_titles, self.protected_paths):
            if (
                not isinstance(rules, tuple)
                or len(rules) > 100
                or any(
                    not isinstance(rule, str) or not rule.strip() or len(rule) > 1024
                    for rule in rules
                )
            ):
                raise ConfigError("Additional protected-application rules are invalid.")

    def with_profile(self, profile):
        updated = replace(
            self,
            profiles=tuple(profile if item.id == profile.id else item for item in self.profiles),
        )
        updated.validate()
        return updated

    def with_detection(self, detection):
        updated = self.with_profile(replace(self.active, detection=detection))
        return replace(
            updated,
            start_with_windows=detection.start_with_windows,
            monitor_on_launch=detection.monitor_on_launch,
            stop_hotkey=detection.stop_hotkey,
        )

    def add_profile(self, name, monitor_only=False):
        profile = Profile(
            id=uuid.uuid4().hex,
            name=name.strip(),
            monitor_only=monitor_only,
            intended_mode=MONITOR if monitor_only else OFFLINE,
        )
        if profile_is_protected(profile):
            profile = replace(profile, monitor_only=True, intended_mode=MONITOR)
        updated = replace(self, profiles=self.profiles + (profile,), active_profile_id=profile.id)
        updated.validate()
        return updated

    @classmethod
    def from_legacy(cls, config):
        config.validate()
        return cls(
            profiles=(Profile(detection=config),),
            start_with_windows=config.start_with_windows,
            monitor_on_launch=config.monitor_on_launch,
            stop_hotkey=config.stop_hotkey,
        )

    @classmethod
    def from_dict(cls, data):
        try:
            if not isinstance(data, dict):
                raise ConfigError("Loki configuration must be a JSON object.")
            version = data.get("schema_version", 1)
            if type(version) is not int:
                raise ConfigError("Invalid schema version.")
            if version == 1:
                return cls.from_legacy(AppConfig.from_dict(data))
            values = dict(data)
            values["profiles"] = tuple(Profile.from_dict(profile) for profile in values["profiles"])
            for name in ("protected_executables", "protected_titles", "protected_paths"):
                if name in values:
                    if not isinstance(values[name], list):
                        raise ConfigError("Protected rules must be JSON lists.")
                    values[name] = tuple(values[name])
            workspace = cls(**values)
            workspace.validate()
            return workspace
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError("Invalid profile configuration.") from exc


def save_workspace(workspace: WorkspaceConfig, paths: AppPaths):
    workspace.validate()
    atomic_write(
        paths.config, (json.dumps(asdict(workspace), indent=2, allow_nan=False) + "\n").encode()
    )


def load_workspace(paths: AppPaths):
    if not paths.config.exists():
        return WorkspaceConfig(), None
    try:
        if paths.config.stat().st_size > 1_000_000:
            raise ConfigError("Configuration is too large.")
        raw = paths.config.read_bytes()
        data = json.loads(raw)
        workspace = WorkspaceConfig.from_dict(data)
        if data.get("schema_version", 1) == 1:
            # Write a byte-identical backup before migrating; no calibration image is changed.
            atomic_write(paths.root / "config.v1.backup.json", raw)
            save_workspace(workspace, paths)
            logging.getLogger("loki").info(
                "CONFIG MIGRATED schema=1->2 calibration preserved Monitor Only"
            )
        return workspace, None
    except OSError as exc:
        # I/O/migration failure must not move or destroy an otherwise valid v1 config.
        return (
            WorkspaceConfig(),
            f"Loki could not read or migrate configuration: {exc}. The original file was retained.",
        )
    except (ValueError, TypeError) as exc:
        backup = paths.root / (
            "config.corrupt-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json"
        )
        try:
            paths.config.replace(backup)
            for old in sorted(paths.root.glob("config.corrupt-*.json"))[:-3]:
                old.unlink(missing_ok=True)
        except OSError:
            pass
        return (
            WorkspaceConfig(),
            f"Loki could not load profiles: {exc}. Please restore the backup or calibrate again.",
        )
