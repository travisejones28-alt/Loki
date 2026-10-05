"""Normal Win32 display, notification-window, hotkey and per-user startup APIs.

This passive-support module never generates input. Offline mouse input is isolated
in input_controller.py and guarded by explicit mode, arming and target validation.
There are no game hooks, injection, or process-memory reads in either path.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from .config import Monitor, Rect

IS_WINDOWS = os.name == "nt"


class WindowsError(RuntimeError):
    pass


def enable_dpi_awareness():
    """Must run before creating QApplication or capturing a screen."""
    if not IS_WINDOWS:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        function = user32.SetProcessDpiAwarenessContext
        function.argtypes = [ctypes.c_void_p]
        function.restype = wintypes.BOOL
        if function(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
        if ctypes.get_last_error() == 5:  # Manifest/Qt already set process awareness.
            return
    except AttributeError:
        pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        user32.SetProcessDPIAware()


def native_monitors() -> list[Monitor]:
    if not IS_WINDOWS:
        return []
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        callback_type,
        wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    records = []
    try:
        dpi_function = ctypes.WinDLL("shcore").GetDpiForMonitor
        dpi_function.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        ]
        dpi_function.restype = ctypes.c_long
    except (OSError, AttributeError):
        dpi_function = None

    def visit(handle, _hdc, _rect, _data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            rect = info.rcMonitor
            scale = 1.0
            if dpi_function is not None:
                x, y = wintypes.UINT(), wintypes.UINT()
                if dpi_function(handle, 0, ctypes.byref(x), ctypes.byref(y)) == 0:
                    scale = x.value / 96.0
            records.append(
                Monitor(
                    len(records) + 1,
                    info.szDevice,
                    Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
                    scale,
                )
            )
        return True

    callback = callback_type(visit)
    if not user32.EnumDisplayMonitors(None, None, callback, 0):
        raise OSError("Windows could not enumerate monitors.")
    return records


def make_overlay_passive(hwnd: int):
    if not IS_WINDOWS:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    getter = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    getter.restype = ctypes.c_ssize_t
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    setter.restype = ctypes.c_ssize_t
    style = getter(hwnd, -20)  # GWL_EXSTYLE
    # WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED
    ctypes.set_last_error(0)
    setter(hwnd, -20, style | 0x20 | 0x08000000 | 0x80 | 0x80000)
    if ctypes.get_last_error():
        raise WindowsError("Windows could not make the highlight click-through.")


def position_overlay(hwnd: int, bounds: Rect):
    if not IS_WINDOWS:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    # HWND_TOPMOST, SWP_NOACTIVATE. Showing is performed by Qt with ShowWithoutActivating.
    if not user32.SetWindowPos(
        hwnd, wintypes.HWND(-1), bounds.left, bounds.top, bounds.width, bounds.height, 0x0010
    ):
        raise WindowsError("Windows could not position the target highlight.")


def register_stop_hotkey(hwnd: int, key: str, identifier=0x4C01):
    if not IS_WINDOWS:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    number = int(key.rsplit("F", 1)[1])
    return bool(
        user32.RegisterHotKey(hwnd, identifier, 0x0002 | 0x0004 | 0x4000, 0x70 + number - 1)
    )


def unregister_stop_hotkey(hwnd: int, identifier=0x4C01):
    if IS_WINDOWS:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey(hwnd, identifier)


def startup_command():
    if getattr(sys, "frozen", False):
        arguments = [sys.executable, "--tray", "--monitor"]
    else:
        executable = Path(sys.executable)
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
        arguments = [
            str(executable),
            str(Path(__file__).resolve().parents[1] / "main.py"),
            "--tray",
            "--monitor",
        ]
    return subprocess.list2cmdline(arguments)


def set_startup(enabled: bool):
    if not IS_WINDOWS:
        if enabled:
            raise WindowsError("Start with Windows is available on Windows only.")
        return
    import winreg

    try:
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
        ) as key:
            if enabled:
                winreg.SetValueEx(key, "Loki", 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, "Loki")
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise WindowsError(
            "Windows could not update Loki's startup setting. No administrator rights are needed."
        ) from exc
