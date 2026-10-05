"""Drag calibration UI and separately testable template persistence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .capture import ScreenCapture, list_monitors, validate_display
from .config import (
    MAX_REGION_PIXELS,
    AppConfig,
    AppPaths,
    ConfigError,
    Monitor,
    Rect,
    atomic_write,
    save_config,
)
from .detector import TemplateDetector
from .geometry import selection_to_pixels
from .overlay import screen_for_monitor


def qimage_from_bgr(frame):
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return QImage(
        rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888
    ).copy()


def preview_pixmap(frame, width=600, height=240):
    return QPixmap.fromImage(qimage_from_bgr(frame)).scaled(
        width,
        height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


@dataclass(frozen=True)
class CalibrationAssets:
    button: np.ndarray
    context: np.ndarray
    offset_x: int
    offset_y: int


def extract_assets(frame, search: Rect, button: Rect):
    search.validate()
    button.validate()
    bounds = Rect(0, 0, frame.shape[1], frame.shape[0])
    if not bounds.contains(search) or not search.contains(button):
        raise ConfigError("The button must be fully inside the selected search area.")
    if search.width * search.height > MAX_REGION_PIXELS:
        raise ConfigError(
            "Select a smaller search area around the popup (at most 2 million pixels)."
        )
    margin = 6
    context = Rect(
        max(search.left, button.left - margin), max(search.top, button.top - margin), 1, 1
    )
    context = replace(
        context,
        width=min(search.right, button.right + margin) - context.left,
        height=min(search.bottom, button.bottom + margin) - context.top,
    )
    return CalibrationAssets(
        frame[button.top : button.bottom, button.left : button.right].copy(),
        frame[context.top : context.bottom, context.left : context.right].copy(),
        button.left - context.left,
        button.top - context.top,
    )


def calibration_config(
    base: AppConfig,
    monitor: Monitor,
    search: Rect,
    assets: CalibrationAssets,
    template_path="templates/preview.png",
    context_path="templates/context-preview.png",
):
    return replace(
        base,
        monitor=monitor,
        search_region=Rect(
            monitor.bounds.left + search.left,
            monitor.bounds.top + search.top,
            search.width,
            search.height,
        ),
        template_path=template_path,
        context_path=context_path,
        context_offset_x=assets.offset_x,
        context_offset_y=assets.offset_y,
    )


def save_calibration(
    base: AppConfig, monitor: Monitor, search: Rect, button: Rect, frame, paths: AppPaths
):
    assets = extract_assets(frame, search, button)
    identity = uuid.uuid4().hex
    config = calibration_config(
        base,
        monitor,
        search,
        assets,
        f"templates/runwho-{identity}.png",
        f"templates/context-{identity}.png",
    )
    config.validate(require_calibration=True)
    TemplateDetector(assets.button, config, assets.context)
    new_paths = []
    try:
        for name, image in (
            (config.template_path, assets.button),
            (config.context_path, assets.context),
        ):
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                raise OSError("The selected button image could not be saved.")
            path = paths.root / name
            new_paths.append(path)
            atomic_write(path, encoded.tobytes())
        save_config(config, paths)
    except Exception:
        for path in new_paths:
            path.unlink(missing_ok=True)
        raise
    # Only remove old calibration assets owned by this app, after committing the new config.
    for name in (base.template_path, base.context_path):
        old = paths.root / name
        if (
            name
            and old.parent.resolve() == (paths.root / "templates").resolve()
            and old not in new_paths
        ):
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
    return config


class RegionSelector(QDialog):
    """Frozen monitor image; mouse gestures are read only within this calibration window."""

    def __init__(self, frame, monitor: Monitor, instruction: str, allowed=None):
        super().__init__(
            None,
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.image = qimage_from_bgr(frame)
        self.monitor = monitor
        self.instruction = instruction
        self.allowed = allowed or Rect(0, 0, frame.shape[1], frame.shape[0])
        self.selection = None
        self.start_point = None
        self.end_point = None
        self.dragging = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.winId()
        screen = screen_for_monitor(monitor)
        self.windowHandle().setScreen(screen)
        self.setGeometry(screen.geometry())

    def _pixels(self, start, end):
        return selection_to_pixels(
            (start.x(), start.y()),
            (end.x(), end.y()),
            (self.width(), self.height()),
            (self.image.width(), self.image.height()),
            self.allowed,
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_point = QPointF(event.position())
            self.end_point = self.start_point
            self.dragging = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.end_point = QPointF(event.position())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.dragging:
            self.dragging = False
            self.end_point = QPointF(event.position())
            selection = self._pixels(self.start_point, self.end_point)
            if selection.width >= 8 and selection.height >= 8:
                self.selection = selection
                self.accept()
            else:
                self.start_point = None
                self.end_point = None
                self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.drawImage(QRectF(self.rect()), self.image)
        sx, sy = self.width() / self.image.width(), self.height() / self.image.height()
        allowed = QRectF(
            self.allowed.left * sx,
            self.allowed.top * sy,
            self.allowed.width * sx,
            self.allowed.height * sy,
        )
        # Shade only outside the allowed area; the selected button remains easy to see.
        painter.fillRect(QRectF(0, 0, self.width(), allowed.top()), QColor(0, 0, 0, 120))
        painter.fillRect(
            QRectF(0, allowed.bottom(), self.width(), self.height() - allowed.bottom()),
            QColor(0, 0, 0, 120),
        )
        painter.fillRect(
            QRectF(0, allowed.top(), allowed.left(), allowed.height()), QColor(0, 0, 0, 120)
        )
        painter.fillRect(
            QRectF(
                allowed.right(), allowed.top(), self.width() - allowed.right(), allowed.height()
            ),
            QColor(0, 0, 0, 120),
        )
        painter.setPen(QPen(QColor("#7bffb3"), 2))
        painter.drawRect(allowed.adjusted(1, 1, -1, -1))
        if self.start_point is not None and self.end_point is not None:
            selected = self._pixels(self.start_point, self.end_point)
            box = QRectF(
                selected.left * sx, selected.top * sy, selected.width * sx, selected.height * sy
            )
            painter.fillRect(box, QColor(123, 255, 179, 35))
            painter.drawRect(box)
        banner = QRectF(20, 20, max(200, min(self.width() - 40, 800)), 70)
        painter.fillRect(banner, QColor(15, 23, 32, 235))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            banner.adjusted(14, 8, -14, -8),
            Qt.TextFlag.TextWordWrap,
            self.instruction + "\nDrag to select. Escape cancels.",
        )


class CalibrationDialog(QDialog):
    def __init__(self, config: AppConfig, paths: AppPaths, parent=None):
        super().__init__(parent)
        self.config = config
        self.paths = paths
        self.saved_config = None
        self.frame = self.search = self.button = self.assets = None
        self.selected_monitor = None
        self._busy = False
        self.setWindowTitle("Loki · Calibrate")
        self.setMinimumWidth(600)
        self.monitors = list_monitors()
        layout = QVBoxLayout(self)
        instructions = QLabel(
            "Open the VoidLink WHO Request popup in WoW first.\n"
            "Choose its monitor, select the search area, then select the complete Run WHO button.\n"
            "Keep the pointer off the button while the screenshot is taken."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.monitor_combo = QComboBox()
        for monitor in self.monitors:
            self.monitor_combo.addItem(
                f"{monitor.number}: {monitor.name} · {monitor.bounds.width} × {monitor.bounds.height} · {monitor.scale:.0%}"
            )
        if config.monitor:
            index = next(
                (i for i, item in enumerate(self.monitors) if item.name == config.monitor.name), 0
            )
            self.monitor_combo.setCurrentIndex(index)
        layout.addWidget(self.monitor_combo)
        self.capture_button = QPushButton("Select area and Run WHO button")
        self.capture_button.clicked.connect(self.begin_selection)
        layout.addWidget(self.capture_button)
        self.preview = QLabel("Your selected button will appear here.")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(100)
        layout.addWidget(self.preview)
        self.status = QLabel("No selection yet.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.live_preview = QLabel()
        self.live_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.live_preview)
        buttons = QHBoxLayout()
        self.test_button = QPushButton("Test live detection")
        self.test_button.setEnabled(False)
        self.test_button.clicked.connect(self.begin_test)
        self.save_button = QPushButton("Save calibration")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        for button in (self.test_button, self.save_button, cancel):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.monitor_combo.currentIndexChanged.connect(self.clear_selection)

    def clear_selection(self, _index=0):
        self.frame = self.search = self.button = self.assets = None
        self.selected_monitor = None
        self.preview.clear()
        self.preview.setText("Your selected button will appear here.")
        self.live_preview.clear()
        self.status.setText("No selection yet.")
        self.save_button.setEnabled(False)
        self.test_button.setEnabled(False)

    def _hide_for_capture(self, callback):
        self._busy = True
        self._parent_visible = self.parentWidget() is not None and self.parentWidget().isVisible()
        self.hide()
        if self._parent_visible:
            self.parentWidget().hide()
        QTimer.singleShot(900, callback)

    def _restore(self):
        self._busy = False
        if self._parent_visible:
            self.parentWidget().show()
        self.show()
        self.raise_()
        self.activateWindow()

    def begin_selection(self):
        if self._busy:
            return
        self.clear_selection()
        self.selected_monitor = self.monitors[self.monitor_combo.currentIndex()]
        self._hide_for_capture(self.select)

    def select(self):
        error = None
        try:
            current = next(
                (item for item in list_monitors() if item.name == self.selected_monitor.name), None
            )
            if current != self.selected_monitor:
                raise ConfigError("The monitor changed. Close calibration and select it again.")
            with ScreenCapture() as capture:
                frame = capture.grab(self.selected_monitor.bounds)
            selector = RegionSelector(
                frame,
                self.selected_monitor,
                "Step 1 of 2: Select a small search area containing the whole WHO popup.",
            )
            if selector.exec() != QDialog.DialogCode.Accepted:
                return
            search = selector.selection
            if search.width * search.height > MAX_REGION_PIXELS:
                raise ConfigError(
                    "The search area is too large. Select a smaller area around the popup."
                )
            selector = RegionSelector(
                frame,
                self.selected_monitor,
                "Step 2 of 2: Select the Run WHO button, including its text and edges.",
                search,
            )
            if selector.exec() != QDialog.DialogCode.Accepted:
                return
            button = selector.selection
            assets = extract_assets(frame, search, button)
            candidate = calibration_config(self.config, self.selected_monitor, search, assets)
            detector = TemplateDetector(assets.button, candidate, assets.context)
            result = detector.detect(frame[search.top : search.bottom, search.left : search.right])
            self.frame, self.search, self.button, self.assets = frame, search, button, assets
            self.preview.setPixmap(preview_pixmap(assets.button, 560, 160))
            self.live_preview.clear()
            self.status.setText(
                f"Selected {button.width} × {button.height} button in a {search.width} × {search.height} area.\n"
                f"Frozen-frame confidence: {result.confidence:.3f}. Test live detection before saving."
            )
            self.save_button.setEnabled(True)
            self.test_button.setEnabled(True)
        except Exception as exc:
            error = str(exc)
        finally:
            self._restore()
        if error:
            QMessageBox.warning(self, "Calibration", error)

    def begin_test(self):
        if not self._busy and self.assets is not None:
            self._hide_for_capture(self.test_live)

    def test_live(self):
        error = None
        try:
            config = calibration_config(
                self.config, self.selected_monitor, self.search, self.assets
            )
            validate_display(config, list_monitors())
            with ScreenCapture() as capture:
                frame = capture.grab(config.search_region)
            result = TemplateDetector(self.assets.button, config, self.assets.context).detect(frame)
            if result.matched:
                cv2.rectangle(
                    frame,
                    (result.x, result.y),
                    (result.x + result.width - 1, result.y + result.height - 1),
                    (120, 255, 60),
                    2,
                )
            self.live_preview.setPixmap(preview_pixmap(frame, 560, 230))
            self.status.setText(
                f"{'MATCH' if result.matched else 'NO MATCH'} · confidence {result.confidence:.3f} · "
                f"context {result.context_confidence:.3f}\n"
                "Live test does not play sounds or create target overlays."
            )
        except Exception as exc:
            error = str(exc)
        finally:
            self._restore()
        if error:
            QMessageBox.warning(self, "Test detection", error)

    def save(self):
        try:
            candidate = calibration_config(
                self.config, self.selected_monitor, self.search, self.assets
            )
            validate_display(candidate, list_monitors())
            self.saved_config = save_calibration(
                self.config, self.selected_monitor, self.search, self.button, self.frame, self.paths
            )
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Save calibration", str(exc))
