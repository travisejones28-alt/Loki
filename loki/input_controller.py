"""Auditable, lazy Windows mouse input. Never imported/constructed by Monitor Mode.

No keyboard synthesis. A required authorizer rechecks the full target policy directly
before SendInput. The controller additionally enforces arming and its own hard rate limit.
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from collections import deque
from ctypes import wintypes


class InputError(RuntimeError):
    pass


class RateLimiter:
    MIN_INTERVAL = 1.0
    MAX_PER_MINUTE = 30

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.history = deque()

    def allow(self):
        now = self.clock()
        while self.history and now - self.history[0] >= 60:
            self.history.popleft()
        if self.history and now - self.history[-1] < self.MIN_INTERVAL:
            return False
        if len(self.history) >= self.MAX_PER_MINUTE:
            return False
        self.history.append(now)
        return True


class InputController:
    """Policy/rate-limited facade; tests inject a fake sender, never OS input."""

    def __init__(self, authorize, sender, clock=time.monotonic):
        if not callable(authorize) or not callable(sender):
            raise ValueError("Input requires a safety authorizer and sender.")
        self.authorize = authorize
        self.sender = sender
        self.armed = False
        self.limiter = RateLimiter(clock)
        self.logger = logging.getLogger("loki")

    def arm(self):
        self.armed = True

    def disarm(self):
        self.armed = False

    def cancel(self):
        self.disarm()

    def execute(self, action, point):
        if not self.armed or action not in ("move", "click"):
            return False
        if len(point) != 2 or any(type(value) is not int for value in point):
            return False
        if not self.authorize(point):
            self.logger.warning("ACTION blocked by final input authorization")
            return False
        if not self.limiter.allow():
            self.logger.warning("ACTION rate-limited")
            return False
        # Last gate is next to the actual OS dispatch, not just detection time.
        if not self.armed or not self.authorize(point):
            return False
        try:
            self.sender(action, point)
            self.logger.info("ACTION %s x=%d y=%d", action, *point)
            return True
        except Exception as exc:
            self.disarm()
            raise InputError("Input failed; automation disarmed: " + str(exc)) from exc

    def move_to(self, x, y):
        return self.execute("move", (x, y))

    def click(self, x, y):
        return self.execute("click", (x, y))


def normalized_absolute(point, desktop):
    left, top, width, height = desktop
    if (
        width <= 1
        or height <= 1
        or not left <= point[0] < left + width
        or not top <= point[1] < top + height
    ):
        raise InputError("Input coordinate is outside the virtual desktop.")
    return round((point[0] - left) * 65535 / (width - 1)), round(
        (point[1] - top) * 65535 / (height - 1)
    )


class WindowsMouseSender:
    def __init__(self, authorize):
        self.authorize = authorize
        if os.name != "nt":
            raise InputError("Windows input cannot run on this platform.")
        self.user = ctypes.WinDLL("user32", use_last_error=True)

    def __call__(self, action, point):
        # INPUT must include the complete union for the correct Win32/x64 structure size.
        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class HardwareInput(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class Payload(ctypes.Union):
            _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("payload",)
            _fields_ = [("type", wintypes.DWORD), ("payload", Payload)]

        self.user.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user.GetSystemMetrics.restype = ctypes.c_int
        virtual = tuple(self.user.GetSystemMetrics(index) for index in (76, 77, 78, 79))
        x, y = normalized_absolute(point, virtual)
        flags = [0x0001 | 0x8000 | 0x4000]  # MOVE | ABSOLUTE | VIRTUALDESK
        if action == "click":
            flags.extend((0x0002, 0x0004))  # LEFTDOWN, LEFTUP in the same serial batch.
        records = (Input * len(flags))()
        for index, flag in enumerate(flags):
            records[index].type = 0  # INPUT_MOUSE only, never INPUT_KEYBOARD.
            records[index].mi = MouseInput(
                x if index == 0 else 0, y if index == 0 else 0, 0, flag, 0, 0
            )
        self.user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user.SendInput.restype = wintypes.UINT
        if not self.authorize(point):
            raise InputError("Final native dispatch authorization was revoked.")
        if self.user.SendInput(len(records), records, ctypes.sizeof(Input)) != len(records):
            raise InputError("Windows rejected or partially delivered the input batch.")


def windows_input_factory(authorize):
    return InputController(authorize, WindowsMouseSender(authorize))
