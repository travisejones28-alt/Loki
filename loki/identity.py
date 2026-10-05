"""Persistable normal Windows window/process metadata, never process memory."""

import ntpath
from dataclasses import dataclass

from .config import ConfigError, Rect


def normalized_path(path):
    return ntpath.normcase(ntpath.normpath(path))


@dataclass(frozen=True)
class WindowIdentity:
    hwnd: int
    pid: int
    created: int
    executable_path: str
    executable_name: str
    title: str
    class_name: str
    bounds: Rect
    client_bounds: Rect
    product_name: str = ""
    original_filename: str = ""
    file_description: str = ""

    def validate(self):
        for name in ("hwnd", "pid", "created"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ConfigError(
                    "Target process/window identity is invalid. Bind a running window again."
                )
        for name in (
            "executable_path",
            "executable_name",
            "title",
            "class_name",
            "product_name",
            "original_filename",
            "file_description",
        ):
            if not isinstance(getattr(self, name), str) or len(getattr(self, name)) > 32768:
                raise ConfigError("Target metadata is invalid.")
        if (
            not ntpath.isabs(self.executable_path)
            or not self.executable_name
            or not self.class_name
        ):
            raise ConfigError("A verified executable path and window class are required.")
        if (
            ntpath.basename(normalized_path(self.executable_path))
            != self.executable_name.casefold()
        ):
            raise ConfigError("Target executable filename does not match its path.")
        self.bounds.validate()
        self.client_bounds.validate()
        if not self.bounds.contains(self.client_bounds):
            raise ConfigError("Target client bounds are invalid.")

    @classmethod
    def from_dict(cls, data):
        values = dict(data)
        for name in ("bounds", "client_bounds"):
            values[name] = Rect(**values[name])
        identity = cls(**values)
        identity.validate()
        return identity


@dataclass(frozen=True)
class TargetBinding:
    window: WindowIdentity
    title_pattern: str = ""

    def validate(self):
        self.window.validate()
        if not isinstance(self.title_pattern, str) or len(self.title_pattern) > 1024:
            raise ConfigError("Target title pattern is invalid.")

    @classmethod
    def from_dict(cls, data):
        values = dict(data)
        values["window"] = WindowIdentity.from_dict(values["window"])
        binding = cls(**values)
        binding.validate()
        return binding
