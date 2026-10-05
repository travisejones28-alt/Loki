"""Small, focus-free, click-through border drawn OUTSIDE the sampled target/context."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .config import Monitor, Rect
from .geometry import physical_to_logical
from .windows import IS_WINDOWS, make_overlay_passive, position_overlay


def screen_for_monitor(monitor: Monitor):
    screens = QGuiApplication.screens()
    exact = next((screen for screen in screens if screen.name() == monitor.name), None)
    if exact is not None:
        return exact
    # Non-Windows development fallback; Windows capture names come from GetMonitorInfo.
    for screen in screens:
        geometry = screen.geometry()
        scale = screen.devicePixelRatio()
        if (
            round(geometry.width() * scale) == monitor.bounds.width
            and round(geometry.height() * scale) == monitor.bounds.height
            and geometry.x() == monitor.bounds.left
            and geometry.y() == monitor.bounds.top
        ):
            return screen
    raise RuntimeError("The calibrated screen no longer matches the desktop. Please recalibrate.")


class TargetOverlay(QWidget):
    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._native_ready = False
        self._last = None
        self.setWindowTitle("Loki target highlight")

    def show_target(self, target: Rect, monitor: Monitor):
        if self._last == target and self.isVisible():
            return
        screen = screen_for_monitor(monitor)
        # The calibration context extends at most 6 physical pixels beyond the button.
        # The border is 12+ pixels away, so it cannot feed back into template matching.
        padding = round(16 * screen.devicePixelRatio())
        outer = Rect(
            target.left - padding,
            target.top - padding,
            target.width + padding * 2,
            target.height + padding * 2,
        )
        sg = screen.geometry()
        logical = physical_to_logical(
            outer,
            monitor.bounds,
            Rect(sg.x(), sg.y(), sg.width(), sg.height()),
            screen.devicePixelRatio(),
        )
        self.winId()
        self.windowHandle().setScreen(screen)
        self.setGeometry(logical.left, logical.top, logical.width, logical.height)
        if not self._native_ready:
            make_overlay_passive(int(self.winId()))
            self._native_ready = True
        self._last = target
        self.show()
        if IS_WINDOWS:
            position_overlay(int(self.winId()), outer)
        self.update()

    def hide_target(self):
        self._last = None
        self.hide()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rectangle = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        painter.setPen(QPen(QColor("#101c1a"), 5))
        painter.drawRoundedRect(rectangle, 5, 5)
        painter.setPen(QPen(QColor("#7bffb3"), 2.5))
        painter.drawRoundedRect(rectangle, 5, 5)
