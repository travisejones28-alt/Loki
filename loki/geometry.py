"""Explicit conversion between physical pixels and per-screen logical coordinates."""

from math import ceil, floor

from .config import Rect


def physical_to_logical(rect: Rect, physical_monitor: Rect, logical_monitor: Rect, scale: float):
    # Screen origins must not be divided globally: mixed-DPI Windows desktops contain gaps
    # in Qt's logical coordinate space. Convert relative to the chosen screen instead.
    x = logical_monitor.left + floor((rect.left - physical_monitor.left) / scale)
    y = logical_monitor.top + floor((rect.top - physical_monitor.top) / scale)
    right = logical_monitor.left + ceil((rect.right - physical_monitor.left) / scale)
    bottom = logical_monitor.top + ceil((rect.bottom - physical_monitor.top) / scale)
    return Rect(x, y, right - x, bottom - y)


def selection_to_pixels(start, end, logical_size, physical_size, allowed: Rect | None = None):
    lw, lh = logical_size
    pw, ph = physical_size
    if min(lw, lh, pw, ph) <= 0:
        raise ValueError("Screen dimensions must be positive.")
    allowed = allowed or Rect(0, 0, pw, ph)
    x1 = max(allowed.left, min(allowed.right, floor(min(start[0], end[0]) * pw / lw)))
    y1 = max(allowed.top, min(allowed.bottom, floor(min(start[1], end[1]) * ph / lh)))
    x2 = max(allowed.left, min(allowed.right, ceil(max(start[0], end[0]) * pw / lw)))
    y2 = max(allowed.top, min(allowed.bottom, ceil(max(start[1], end[1]) * ph / lh)))
    return Rect(x1, y1, max(0, x2 - x1), max(0, y2 - y1))
