"""Read-only Win32 window/process metadata and desktop foreground notifications.

The WinEvent listener is OUTOFCONTEXT: callbacks run in Loki, with no DLL injection,
game hook, process-memory access, or input generation. It detects focus transitions
even if the user switches away and back between capture frames.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

from .config import Rect
from .identity import WindowIdentity
from .windows import native_monitors


class MetadataError(RuntimeError):
    pass


class WindowsDesktop:
    def __init__(self):
        if os.name != "nt":
            raise MetadataError("Offline Automation is available on Windows only.")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.version = ctypes.WinDLL("version", use_last_error=True)
        self.focus_epoch = 0
        self.focus_changed_at = time.monotonic()
        self._version_cache = {}
        self._configure()
        callback_type = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._callback = callback_type(self._focus_changed)
        self.user.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            callback_type,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.user.SetWinEventHook.restype = wintypes.HANDLE
        self.user.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        self.user.UnhookWinEvent.restype = wintypes.BOOL
        self._hook = self.user.SetWinEventHook(3, 3, None, self._callback, 0, 0, 0)
        if not self._hook:
            raise MetadataError(
                "Foreground changes cannot be tracked. Automation remains disarmed."
            )

    def _configure(self):
        signatures = {
            "GetForegroundWindow": ([], wintypes.HWND),
            "IsWindow": ([wintypes.HWND], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "IsIconic": ([wintypes.HWND], wintypes.BOOL),
            "GetWindowThreadProcessId": (
                [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
                wintypes.DWORD,
            ),
            "GetWindowTextLengthW": ([wintypes.HWND], ctypes.c_int),
            "GetWindowTextW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetClassNameW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "GetClientRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "ClientToScreen": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "WindowFromPoint": ([wintypes.POINT], wintypes.HWND),
            "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetCursorPos": ([ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "GetAsyncKeyState": ([ctypes.c_int], ctypes.c_short),
        }
        for name, (arguments, returns) in signatures.items():
            method = getattr(self.user, name)
            method.argtypes, method.restype = arguments, returns
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        self.kernel.GetProcessTimes.restype = wintypes.BOOL

    def _focus_changed(self, *_args):
        self.focus_epoch += 1
        self.focus_changed_at = time.monotonic()

    def close(self):
        if self._hook:
            self.user.UnhookWinEvent(self._hook)
            self._hook = None

    def _version_info(self, path):
        if path in self._version_cache:
            return self._version_cache[path]
        result = {"ProductName": "", "OriginalFilename": "", "FileDescription": ""}
        size_function = self.version.GetFileVersionInfoSizeW
        size_function.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        size_function.restype = wintypes.DWORD
        get_info = self.version.GetFileVersionInfoW
        get_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
        get_info.restype = wintypes.BOOL
        query = self.version.VerQueryValueW
        query.argtypes = [
            wintypes.LPCVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        ]
        query.restype = wintypes.BOOL
        size = size_function(path, None)
        if size and size <= 8_000_000:
            buffer = ctypes.create_string_buffer(size)
            if get_info(path, 0, size, buffer):
                pointer, length = ctypes.c_void_p(), wintypes.UINT()
                translations = [(0x0409, 0x04B0), (0x0409, 0x04E4)]
                if (
                    query(
                        buffer,
                        r"\VarFileInfo\Translation",
                        ctypes.byref(pointer),
                        ctypes.byref(length),
                    )
                    and length.value >= 4
                ):
                    values = ctypes.cast(pointer, ctypes.POINTER(wintypes.WORD))
                    translations.insert(0, (values[0], values[1]))
                for field in result:
                    for language, codepage in translations:
                        key = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{field}"
                        if (
                            query(buffer, key, ctypes.byref(pointer), ctypes.byref(length))
                            and length.value
                        ):
                            result[field] = ctypes.wstring_at(pointer, length.value).rstrip("\0")
                            break
        if len(self._version_cache) >= 64:
            self._version_cache.clear()
        self._version_cache[path] = result
        return result

    def inspect_window(self, hwnd):
        import ntpath

        if (
            not hwnd
            or not self.user.IsWindow(hwnd)
            or not self.user.IsWindowVisible(hwnd)
            or self.user.IsIconic(hwnd)
        ):
            raise MetadataError("TARGET WINDOW lost or minimized. Bind the running window again.")
        pid = wintypes.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            raise MetadataError("Target process identity is unavailable.")
        handle = self.kernel.OpenProcess(
            0x1000, False, pid.value
        )  # QUERY_LIMITED_INFORMATION only.
        if not handle:
            raise MetadataError("Unable to verify target executable. Automation is blocked.")
        try:
            path, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                raise MetadataError("Unable to verify target executable path.")
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                raise MetadataError("Unable to verify process creation identity.")
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            self.kernel.CloseHandle(handle)
        title = ctypes.create_unicode_buffer(min(32768, self.user.GetWindowTextLengthW(hwnd) + 1))
        self.user.GetWindowTextW(hwnd, title, len(title))
        class_name = ctypes.create_unicode_buffer(256)
        if not self.user.GetClassNameW(hwnd, class_name, len(class_name)):
            raise MetadataError("Unable to verify target window class.")
        frame, client, origin = wintypes.RECT(), wintypes.RECT(), wintypes.POINT()
        if (
            not self.user.GetWindowRect(hwnd, ctypes.byref(frame))
            or not self.user.GetClientRect(hwnd, ctypes.byref(client))
            or not self.user.ClientToScreen(hwnd, ctypes.byref(origin))
        ):
            raise MetadataError("Target window coordinates cannot be verified.")
        version = self._version_info(path.value)
        identity = WindowIdentity(
            int(hwnd),
            pid.value,
            created,
            path.value,
            ntpath.basename(path.value),
            title.value,
            class_name.value,
            Rect(frame.left, frame.top, frame.right - frame.left, frame.bottom - frame.top),
            Rect(origin.x, origin.y, client.right - client.left, client.bottom - client.top),
            version["ProductName"],
            version["OriginalFilename"],
            version["FileDescription"],
        )
        identity.validate()
        # Recheck after collecting metadata: the handle must not have changed owners meanwhile.
        final_pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(final_pid))
        if final_pid.value != pid.value or not self.user.IsWindow(hwnd):
            raise MetadataError("Target window changed while being inspected.")
        return identity

    def foreground(self):
        hwnd = self.user.GetForegroundWindow()
        identity = self.inspect_window(hwnd)
        if self.user.GetForegroundWindow() != hwnd:
            raise MetadataError("Foreground changed during validation.")
        return identity

    def window_at(self, point):
        hwnd = self.user.WindowFromPoint(wintypes.POINT(*point))
        root = self.user.GetAncestor(hwnd, 2)  # GA_ROOT, including child controls.
        return self.inspect_window(root)

    def cursor_position(self):
        point = wintypes.POINT()
        if not self.user.GetCursorPos(ctypes.byref(point)):
            raise MetadataError("Cursor position cannot be verified.")
        return point.x, point.y

    def input_stamp(self):
        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        record = LastInput()
        record.cbSize = ctypes.sizeof(record)
        function = self.user.GetLastInputInfo
        function.argtypes = [ctypes.POINTER(LastInput)]
        function.restype = wintypes.BOOL
        if not function(ctypes.byref(record)):
            raise MetadataError("Manual input activity cannot be verified.")
        return record.dwTime

    def buttons_or_modifiers_down(self):
        return any(self.user.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4, 0x10, 0x11, 0x12))

    def displays(self):
        return native_monitors()

    def list_windows(self):
        windows = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _data):
            try:
                if (
                    self.user.IsWindowVisible(hwnd)
                    and self.user.GetWindowTextLengthW(hwnd)
                    and not self.user.IsIconic(hwnd)
                ):
                    identity = self.inspect_window(hwnd)
                    if identity.pid != os.getpid():
                        windows.append(identity)
            except (MetadataError, ValueError, OSError):
                pass  # Unverifiable windows cannot be chosen as automation targets.
            return True

        callback = callback_type(visit)
        self.user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        self.user.EnumWindows.restype = wintypes.BOOL
        if not self.user.EnumWindows(callback, 0):
            raise MetadataError("Running windows could not be enumerated.")
        return sorted(
            windows, key=lambda window: (window.executable_name.casefold(), window.title.casefold())
        )
