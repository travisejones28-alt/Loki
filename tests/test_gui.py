import time
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from loki.calibration import CalibrationDialog, RegionSelector, save_calibration
from loki.config import AppConfig, Monitor, Rect, load_config
from loki.detector import DetectionResult
from loki.engine import FrameReport
from loki.state_machine import DetectorState, StateUpdate
from loki.ui import DiagnosticDialog, MainWindow, SettingsDialog


@pytest.fixture
def window(app, paths, monitor, popup, monkeypatch):
    monkeypatch.setattr("loki.ui.register_stop_hotkey", lambda *_args: False)
    monkeypatch.setattr("loki.ui.IS_WINDOWS", False)
    config = save_calibration(
        AppConfig(), monitor, Rect(0, 0, 420, 240), Rect(135, 146, 140, 42), popup, paths
    )
    win = MainWindow(config, paths)
    yield win
    win.stop_monitoring()
    app.removeNativeEventFilter(win._hotkey_filter)
    win.tray.hide()
    win.target_overlay.hide_target()
    win._quitting = True
    win.close()
    win.deleteLater()
    app.processEvents()


def test_main_window_launch_and_buttons(window):
    window.show()
    assert window.state.text() == "STOPPED"
    assert window.start_button.isEnabled()
    assert not window.stop_button.isEnabled()
    assert set(window.tray_actions) == {
        "Open Loki",
        "Start Monitoring",
        "Stop Monitoring",
        "Calibrate",
        "Test Detection",
        "Exit",
    }


def test_notifications_once_and_no_notifications_from_stale_frames(window, monkeypatch):
    sounds, highlights = [], []
    monkeypatch.setattr(window.audio, "play", lambda enabled: sounds.append(enabled))
    monkeypatch.setattr(
        window.target_overlay, "show_target", lambda *_args: highlights.append(True)
    )
    hit = DetectionResult(True, 0.99, -1785, 146, 140, 42, present=True)
    window.mode = "MONITORING"
    window._run_id = 11
    window.on_report(
        FrameReport(hit, StateUpdate(DetectorState.TRIGGERED, triggered=True, visible=True), 10), 11
    )
    for _ in range(10):
        window.on_report(
            FrameReport(hit, StateUpdate(DetectorState.TRIGGERED, visible=True), 10), 11
        )
    assert sounds == [True]
    assert len(highlights) == 11
    window.stop_monitoring()
    window.on_report(
        FrameReport(hit, StateUpdate(DetectorState.TRIGGERED, triggered=True, visible=True), 10), 11
    )
    assert sounds == [True]  # Queued old detections are discarded after Stop.
    assert window.mode == "STOPPED"


def test_test_detection_routes_to_preview_only(window, popup, monkeypatch):
    sounds, overlays = [], []
    monkeypatch.setattr(window.audio, "play", lambda enabled: sounds.append(enabled))
    monkeypatch.setattr(window.target_overlay, "show_target", lambda *_args: overlays.append(True))
    dialog = DiagnosticDialog(window.config, window)
    window._diagnostic = dialog
    window.mode = "TESTING"
    hit = DetectionResult(True, 0.99, -1785, 146, 140, 42, present=True)
    report = FrameReport(
        hit, StateUpdate(DetectorState.TRIGGERED, triggered=True, visible=True), 10, popup
    )
    window.on_report(report, window._run_id)
    assert dialog.score.text().startswith("MATCH")
    assert not dialog.image.pixmap().isNull()
    assert not sounds and not overlays
    window._diagnostic = None
    dialog.close()


def test_settings_save_and_persist_without_touching_startup(app, paths):
    dialog = SettingsDialog(AppConfig(), paths)
    dialog.sound.setChecked(False)
    dialog.fps.setValue(5)
    dialog.confirmation.setValue(5)
    dialog.save()
    config, warning = load_config(paths)
    assert warning is None and config == dialog.new_config
    assert config.fps == 5 and config.confirmation_frames == 5 and not config.sound_enabled


def test_drag_calibration_mouse_selection_on_frozen_image(app, monitor, popup, monkeypatch):
    screen = app.primaryScreen()
    monkeypatch.setattr("loki.calibration.screen_for_monitor", lambda _monitor: screen)
    selector = RegionSelector(popup, monitor, "Select the button")
    selector.show()
    QTest.mousePress(selector, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    QTest.mouseMove(selector, QPoint(300, 300))
    QTest.mouseRelease(selector, Qt.MouseButton.LeftButton, pos=QPoint(300, 300))
    assert selector.result() == QDialog.DialogCode.Accepted
    assert selector.selection.width > 0 and selector.selection.height > 0
    assert Rect(0, 0, 420, 240).contains(selector.selection)


def test_changing_monitor_invalidates_old_calibration_selection(
    app, paths, monitor, popup, monkeypatch
):
    monkeypatch.setattr(
        "loki.calibration.list_monitors",
        lambda: [monitor, replace(monitor, number=3, name="OTHER")],
    )
    dialog = CalibrationDialog(AppConfig(), paths)
    dialog.frame = popup
    dialog.assets = object()
    dialog.save_button.setEnabled(True)
    dialog.monitor_combo.setCurrentIndex(1)
    assert dialog.frame is None and dialog.assets is None
    assert not dialog.save_button.isEnabled()


def test_click_through_overlay_flags_and_render(app, monkeypatch):
    from loki.overlay import TargetOverlay

    screen = app.primaryScreen()
    monkeypatch.setattr("loki.overlay.screen_for_monitor", lambda _monitor: screen)
    monkeypatch.setattr("loki.overlay.make_overlay_passive", lambda _hwnd: None)
    monkeypatch.setattr("loki.overlay.IS_WINDOWS", False)
    overlay = TargetOverlay()
    bounds = screen.geometry()
    monitor = Monitor(
        1, screen.name(), Rect(bounds.x(), bounds.y(), bounds.width(), bounds.height()), 1
    )
    overlay.show_target(Rect(100, 100, 140, 42), monitor)
    assert overlay.isVisible()
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert not overlay.grab().isNull()
    overlay.hide_target()
    assert not overlay.isVisible()
    overlay.close()


def test_complete_calibration_preview_live_test_and_save(app, paths, popup, monkeypatch):
    monitor = Monitor(1, "TEST", Rect(0, 0, 420, 240))
    monkeypatch.setattr("loki.calibration.list_monitors", lambda: [monitor])

    class FakeCapture:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def grab(self, _region):
            return popup.copy()

    class SelectedRegion:
        def __init__(self, _frame, _monitor, _instructions, allowed=None):
            self.selection = Rect(0, 0, 420, 240) if allowed is None else Rect(135, 146, 140, 42)

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("loki.calibration.ScreenCapture", FakeCapture)
    monkeypatch.setattr("loki.calibration.RegionSelector", SelectedRegion)
    dialog = CalibrationDialog(AppConfig(), paths)
    dialog.selected_monitor = monitor
    dialog._parent_visible = False
    dialog.select()
    assert dialog.assets is not None and dialog.save_button.isEnabled()
    assert not dialog.preview.pixmap().isNull()
    dialog.test_live()
    assert dialog.status.text().startswith("MATCH")
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert load_config(paths)[0] == dialog.saved_config


def test_main_start_stop_with_real_worker_and_synthetic_capture(app, window, popup, monkeypatch):
    from loki.engine import MonitorWorker

    calls = []

    class FakeCapture:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def grab(self, _region):
            return popup.copy()

    def factory(config, paths, diagnostic, parent):
        return MonitorWorker(
            config,
            paths,
            diagnostic,
            capture_factory=FakeCapture,
            monitor_provider=lambda: [config.monitor],
            parent=parent,
        )

    monkeypatch.setattr("loki.ui.MonitorWorker", factory)
    monkeypatch.setattr(window.audio, "play", lambda enabled: calls.append(enabled))
    monkeypatch.setattr(window.target_overlay, "show_target", lambda *_args: None)
    window.start_monitoring()
    deadline = time.monotonic() + 3
    while not calls and time.monotonic() < deadline:
        app.processEvents()
        # Unlike repeated native QTest.qWait calls, sleep releases the Python GIL
        # so the real Python capture worker can run while testing the GUI thread.
        time.sleep(0.02)
    for _ in range(12):
        app.processEvents()
        time.sleep(0.02)
    assert window.mode == "MONITORING" and calls == [True]
    assert window.worker.isRunning()
    window.emergency_stop()
    assert window.mode == "STOPPED" and not window.worker.isRunning()
    assert window.fps_label.text() == "0"
