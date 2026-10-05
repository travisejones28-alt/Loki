"""Main window, settings, live diagnostics and visible system-tray controls."""

from __future__ import annotations

import logging
from ctypes import wintypes
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import cv2
from PySide6.QtCore import QAbstractNativeEventFilter, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .alerts import AudioAlert
from .calibration import CalibrationDialog, preview_pixmap
from .config import STOP_KEYS, AppConfig, AppPaths, save_config
from .engine import FrameReport, MonitorWorker
from .modes import MonitorModeController, OfflineModeController
from .overlay import TargetOverlay
from .profiles import MONITOR, OFFLINE, WorkspaceConfig, save_workspace
from .windows import IS_WINDOWS, register_stop_hotkey, set_startup, unregister_stop_hotkey

STYLE = """
QWidget { background: #111a23; color: #eaf2f5; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #111a23; }
QPushButton { background: #213241; border: 1px solid #344b5e; border-radius: 6px; padding: 10px 16px; }
QPushButton:hover { background: #304b5d; }
QPushButton:disabled { color: #637483; background: #19242e; }
QPushButton#primary { background: #316f55; border-color: #4a9775; font-weight: 600; }
QLabel#title { font-size: 32px; font-weight: 700; letter-spacing: 4px; }
QLabel#state { color: #7bffb3; font-size: 18px; font-weight: 600; padding: 12px 0; }
QLabel#muted { color: #93a9b8; }
QComboBox, QSpinBox, QDoubleSpinBox { background: #213241; border: 1px solid #344b5e; padding: 6px; }
QCheckBox { spacing: 8px; padding: 4px; }
QMenu { background: #213241; }
QMenu::item { padding: 7px 22px; }
QMenu::item:selected { background: #316f55; }
QStatusBar { color: #93a9b8; }
"""


def tray_icon(active=False, diagnostic=False):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#e4bc62" if diagnostic else "#63dfa1" if active else "#758896"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setBrush(QColor("#111a23"))
    painter.drawRoundedRect(20, 16, 8, 32, 2, 2)
    painter.drawRoundedRect(20, 40, 26, 8, 2, 2)
    painter.end()
    return QIcon(pixmap)


class StopHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, _event_type, message):
        if IS_WINDOWS:
            event = wintypes.MSG.from_address(int(message))
            if event.message == 0x0312 and event.wParam == 0x4C01:  # WM_HOTKEY
                self.callback()
                return True, 0
        return False, 0


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, paths: AppPaths, parent=None, save_callback=None):
        super().__init__(parent)
        self.original = config
        self.paths = paths
        self.save_callback = save_callback
        self.new_config = None
        self.setWindowTitle("Loki · Settings")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.70, 1.0)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(2)
        self.threshold.setValue(config.confidence_threshold)
        form.addRow("Detection confidence", self.threshold)
        self.fps = QSpinBox()
        self.fps.setRange(5, 15)
        self.fps.setValue(config.fps)
        form.addRow("Checks per second", self.fps)
        self.confirmation = QSpinBox()
        self.confirmation.setRange(2, 30)
        self.confirmation.setValue(config.confirmation_frames)
        form.addRow("Consecutive matching frames", self.confirmation)
        self.disappearance = QSpinBox()
        self.disappearance.setRange(2, 30)
        self.disappearance.setValue(config.disappearance_frames)
        form.addRow("Consecutive missing frames", self.disappearance)
        self.hotkey = QComboBox()
        self.hotkey.addItems(STOP_KEYS)
        self.hotkey.setCurrentText(config.stop_hotkey)
        form.addRow("Emergency stop", self.hotkey)
        layout.addLayout(form)
        self.sound = QCheckBox("Play a sound for each new request")
        self.sound.setChecked(config.sound_enabled)
        self.overlay = QCheckBox("Highlight the detected button")
        self.overlay.setChecked(config.overlay_enabled)
        self.debug = QCheckBox("Save a screenshot on detection (keep the newest 20)")
        self.debug.setChecked(config.debug_screenshots)
        self.startup = QCheckBox("Start Loki with Windows, in the tray, and begin monitoring")
        self.startup.setChecked(config.start_with_windows)
        self.startup.setEnabled(IS_WINDOWS)
        self.auto_monitor = QCheckBox("Begin monitoring when I open Loki")
        self.auto_monitor.setChecked(config.monitor_on_launch)
        for checkbox in (self.sound, self.overlay, self.debug, self.startup, self.auto_monitor):
            layout.addWidget(checkbox)
        actions = QHBoxLayout()
        test_sound = QPushButton("Test sound")
        test_sound.clicked.connect(lambda: AudioAlert().play(True))
        folder = QPushButton("Open data folder")
        folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.root)))
        )
        actions.addWidget(test_sound)
        actions.addWidget(folder)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        candidate = replace(
            self.original,
            confidence_threshold=self.threshold.value(),
            release_threshold=min(self.original.release_threshold, self.threshold.value() - 0.04),
            confirmation_frames=self.confirmation.value(),
            disappearance_frames=self.disappearance.value(),
            fps=self.fps.value(),
            sound_enabled=self.sound.isChecked(),
            overlay_enabled=self.overlay.isChecked(),
            debug_screenshots=self.debug.isChecked(),
            start_with_windows=self.startup.isChecked(),
            monitor_on_launch=self.auto_monitor.isChecked(),
            stop_hotkey=self.hotkey.currentText(),
        )
        startup_changed = candidate.start_with_windows != self.original.start_with_windows
        try:
            candidate.validate()
            if startup_changed:
                set_startup(candidate.start_with_windows)
            if self.save_callback is None:
                save_config(candidate, self.paths)
            else:
                self.save_callback(candidate)
        except Exception as exc:
            if startup_changed:
                try:
                    set_startup(self.original.start_with_windows)
                except Exception:
                    logging.getLogger("loki").exception("Could not roll back startup setting")
            QMessageBox.warning(self, "Save settings", str(exc))
            return
        self.new_config = candidate
        self.accept()


class DiagnosticDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Loki · Test Detection")
        self.setMinimumSize(640, 420)
        layout = QVBoxLayout(self)
        self.score = QLabel("Waiting for the first frame…")
        self.score.setWordWrap(True)
        layout.addWidget(self.score)
        note = QLabel(
            "Live region preview. Sounds and target highlights are disabled during this test.\n"
            "Keep this window outside the capture area so it does not cover the popup."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.image, 1)
        close = QPushButton("Stop test")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def update_report(self, report: FrameReport):
        result = report.result
        self.score.setText(
            f"{'MATCH' if result.matched else 'NO MATCH'} · confidence {result.confidence:.3f} "
            f"(required {self.config.confidence_threshold:.2f})\n"
            f"Context {result.context_confidence:.3f} · appearance {result.appearance:.3f} · "
            f"box ({result.x}, {result.y}, {result.width}, {result.height})"
        )
        if report.frame is not None:
            frame = report.frame.copy()
            x = result.x - self.config.search_region.left
            y = result.y - self.config.search_region.top
            cv2.rectangle(
                frame,
                (x, y),
                (x + result.width - 1, y + result.height - 1),
                (120, 255, 60) if result.matched else (80, 170, 255),
                2,
            )
            self.image.setPixmap(
                preview_pixmap(frame, max(200, self.width() - 40), max(100, self.height() - 170))
            )


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, paths: AppPaths):
        super().__init__()
        self.workspace = (
            config if isinstance(config, WorkspaceConfig) else WorkspaceConfig.from_legacy(config)
        )
        self.config = self.workspace.runtime_detection
        self.operating_mode = MONITOR  # Never restore or infer Offline Automation on startup.
        self.controller = MonitorModeController(self.notify_report)
        self.desktop_factory = None
        self.paths = paths
        self.logger = logging.getLogger("loki")
        self.worker = None
        self._run_id = 0
        self.mode = "STOPPED"
        self._quitting = False
        self._diagnostic = None
        self._hide_tip_shown = False
        self.audio = AudioAlert()
        self.target_overlay = TargetOverlay()
        self.setWindowTitle("Loki")
        self.setWindowIcon(tray_icon())
        self.setMinimumWidth(500)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 24)
        title = QLabel("LOKI")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("Visual desktop assistant")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)
        profile_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self.select_profile)
        profile_row.addWidget(self.profile_combo, 1)
        add_profile = QPushButton("New profile")
        add_profile.clicked.connect(self.add_profile)
        profile_row.addWidget(add_profile)
        edit_profile = QPushButton("Profile / Target")
        edit_profile.clicked.connect(self.edit_profile)
        profile_row.addWidget(edit_profile)
        layout.addLayout(profile_row)
        layout.addWidget(QLabel("OPERATING MODE"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Monitor — passive", MONITOR)
        self.mode_combo.addItem("Offline Automation", OFFLINE)
        self.mode_combo.currentIndexChanged.connect(self.operating_mode_changed)
        layout.addWidget(self.mode_combo)
        self.automation_status = QLabel("PASSIVE MONITORING · Input automation disabled")
        self.automation_status.setWordWrap(True)
        layout.addWidget(self.automation_status)
        self.arm_button = QPushButton("ARM AUTOMATION")
        self.arm_button.clicked.connect(self.toggle_arm)
        layout.addWidget(self.arm_button)
        self.state = QLabel("STOPPED")
        self.state.setObjectName("state")
        layout.addWidget(self.state)
        self.details = QLabel("Calibrate the button to get started.")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        form = QFormLayout()
        self.monitor_label = QLabel()
        self.confidence_label = QLabel("—")
        self.last_detection_label = QLabel("—")
        self.template_label = QLabel()
        self.fps_label = QLabel("0")
        self.coordinates_label = QLabel("—")
        for label, value in (
            ("Selected monitor", self.monitor_label),
            ("Detection confidence", self.confidence_label),
            ("Last detection", self.last_detection_label),
            ("Current template", self.template_label),
            ("Capture FPS", self.fps_label),
            ("Detected coordinates", self.coordinates_label),
        ):
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(label, value)
        layout.addLayout(form)
        buttons = QGridLayout()
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_monitoring)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_monitoring)
        self.calibrate_button = QPushButton("Calibrate")
        self.calibrate_button.clicked.connect(self.calibrate)
        self.test_button = QPushButton("Test Detection")
        self.test_button.clicked.connect(self.test_detection)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.settings)
        for i, button in enumerate(
            (self.start_button, self.stop_button, self.calibrate_button, self.test_button)
        ):
            buttons.addWidget(button, i // 2, i % 2)
        buttons.addWidget(self.settings_button, 2, 0, 1, 2)
        layout.addLayout(buttons)
        self.hotkey_label = QLabel()
        self.hotkey_label.setObjectName("muted")
        layout.addWidget(self.hotkey_label)
        self.tray = QSystemTrayIcon(tray_icon(), self)
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        menu = QMenu(self)
        actions = [
            ("Open Loki", self.open_window),
            ("Start Monitoring", self.start_monitoring),
            ("Stop Monitoring", self.stop_monitoring),
            ("Calibrate", self.calibrate),
            ("Test Detection", self.test_detection),
            ("Exit", self.exit_app),
        ]
        self.tray_actions = {}
        for name, callback in actions:
            action = QAction(name, self)
            action.triggered.connect(callback)
            menu.addAction(action)
            self.tray_actions[name] = action
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        if self.tray_available:
            self.tray.show()
        self._hotkey_filter = StopHotkeyFilter(self.emergency_stop)
        QApplication.instance().installNativeEventFilter(self._hotkey_filter)
        self._hotkey_registered = False
        self._action_timer = QTimer(self)
        self._action_timer.setInterval(50)
        self._action_timer.timeout.connect(self.poll_actions)
        self.refresh_profiles()
        self.refresh_config()
        self.register_hotkey()
        self.update_controls()

    def refresh_profiles(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self.workspace.profiles:
            self.profile_combo.addItem(
                profile.name + (" [MONITOR ONLY]" if not profile.automation_capable else ""),
                profile.id,
            )
        self.profile_combo.setCurrentIndex(
            self.profile_combo.findData(self.workspace.active_profile_id)
        )
        self.profile_combo.blockSignals(False)

    def persist_detection(self, config):
        previous = self.workspace.active.detection
        workspace = self.workspace.with_detection(config)
        save_workspace(workspace, self.paths)
        self.workspace = workspace
        self.config = workspace.runtime_detection
        self.set_operating_mode(MONITOR)
        # Never remove a template still referenced by another profile.
        referenced = {
            name
            for profile in workspace.profiles
            for name in (profile.detection.template_path, profile.detection.context_path)
        }
        for name in (previous.template_path, previous.context_path):
            old = self.paths.root / name
            if (
                name
                and name not in referenced
                and old.parent.resolve() == (self.paths.root / "templates").resolve()
            ):
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    self.logger.warning("Old template cleanup failed: %s", name)

    def select_profile(self, index):
        profile_id = self.profile_combo.itemData(index)
        if not profile_id or profile_id == self.workspace.active_profile_id:
            return
        self.set_operating_mode(MONITOR)
        try:
            workspace = replace(self.workspace, active_profile_id=profile_id)
            save_workspace(workspace, self.paths)
            self.workspace = workspace
            self.config = workspace.runtime_detection
            self.logger.info("PROFILE selected=%s", workspace.active.name)
            self.refresh_config()
            self.update_controls()
        except Exception as exc:
            self.refresh_profiles()
            self.show_error(str(exc))

    def add_profile(self):
        self.set_operating_mode(MONITOR)
        name, accepted = QInputDialog.getText(self, "New profile", "Profile name:")
        if accepted and name.strip():
            try:
                workspace = self.workspace.add_profile(name)
                save_workspace(workspace, self.paths)
                self.workspace = workspace
                self.config = workspace.runtime_detection
                self.refresh_profiles()
                self.refresh_config()
                self.update_controls()
            except Exception as exc:
                self.show_error(str(exc))

    def edit_profile(self):
        self.set_operating_mode(MONITOR)
        self.open_window()
        from .profile_ui import ProfileDialog

        dialog = ProfileDialog(
            self.workspace, self.paths, self, desktop_factory=self.desktop_factory
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.new_workspace is not None:
            self.workspace = dialog.new_workspace
            self.config = self.workspace.runtime_detection
            self.logger.info(
                "PROFILE saved=%s TARGET WINDOW selected=%s",
                self.workspace.active.name,
                self.workspace.active.target,
            )
            self.refresh_profiles()
            self.refresh_config()
            self.update_controls()

    def operating_mode_changed(self, index):
        self.set_operating_mode(self.mode_combo.itemData(index))

    def set_operating_mode(self, mode):
        self.stop_monitoring()
        if self._diagnostic is not None:
            self._diagnostic.close()
        self._action_timer.stop()
        self.controller.close()
        self.operating_mode = MONITOR
        self.controller = MonitorModeController(self.notify_report)
        try:
            if mode == OFFLINE:
                if not self.workspace.active.automation_capable:
                    raise ValueError("MONITOR ONLY: Automation unavailable for this profile.")
                if self.desktop_factory is None:
                    from .desktop import WindowsDesktop

                    self.desktop_factory = WindowsDesktop
                desktop = self.desktop_factory()
                try:
                    self.controller = OfflineModeController(
                        self.notify_report,
                        self.workspace.active,
                        self.workspace,
                        desktop,
                        self.verify_visual,
                    )
                except Exception:
                    desktop.close()
                    raise
                self.operating_mode = OFFLINE
                self._action_timer.start()
            self.logger.info(
                "MODE %s", "OfflineAutomation" if self.operating_mode == OFFLINE else "Monitor"
            )
        except Exception as exc:
            self.show_error(str(exc))
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(self.operating_mode))
        self.mode_combo.blockSignals(False)
        self.update_controls()

    def verify_visual(self, expected):
        if (
            self.mode != "MONITORING"
            or self.operating_mode != OFFLINE
            or self.worker is None
            or not self.worker.isRunning()
            or self.worker.pipeline is None
        ):
            return None
        return self.worker.pipeline.probe(expected)

    def toggle_arm(self):
        if self.operating_mode != OFFLINE or not self.workspace.active.automation_capable:
            self.show_error("Monitor Mode cannot arm automation.")
            return
        if self.controller.armed:
            self.controller.disarm("user disarmed")
        else:
            try:
                if self.mode != "MONITORING" and not self._start(False):
                    return
                self.controller.arm()
            except Exception as exc:
                self.controller.disarm("arming validation failed")
                self.show_error(str(exc))
        self.update_controls()

    def poll_actions(self):
        if self.operating_mode == OFFLINE and self.mode == "MONITORING":
            self.controller.poll()
        self.update_automation_display()

    def update_automation_display(self):
        protected = not self.workspace.active.automation_capable
        self.mode_combo.model().item(1).setEnabled(not protected and IS_WINDOWS)
        self.arm_button.setVisible(self.operating_mode == OFFLINE and not protected)
        self.arm_button.setText("DISARM AUTOMATION" if self.controller.armed else "ARM AUTOMATION")
        if protected:
            text = "MONITOR ONLY · Automation unavailable for this profile"
        elif self.operating_mode == MONITOR:
            text = "PASSIVE MONITORING · Input automation disabled"
        else:
            text = "OFFLINE AUTOMATION — " + ("ARMED" if self.controller.armed else "DISARMED")
            text += " · " + self.workspace.active.action.upper()
            if self.workspace.active.dry_run:
                text += " · DRY RUN"
            if self.controller.blocked:
                text += "\nAUTOMATION TEMPORARILY BLOCKED: " + self.controller.blocked
        self.automation_status.setText(text)
        tooltip = "Loki · " + self.mode + " · " + text.replace("\n", " · ")
        self.tray.setToolTip(tooltip)
        icon = tray_icon(self.mode != "STOPPED", self.mode == "TESTING")
        if self.operating_mode == OFFLINE:
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setBrush(
                QColor(
                    "#ff705e"
                    if self.controller.blocked
                    else "#dd8cff"
                    if self.controller.armed
                    else "#e4bc62"
                )
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
            painter.end()
            icon = QIcon(pixmap)
        self.tray.setIcon(icon)

    def refresh_config(self):
        monitor = self.config.monitor
        self.monitor_label.setText(
            f"{monitor.number}: {monitor.name}" if monitor else "Not calibrated"
        )
        self.template_label.setText(
            Path(self.config.template_path).name if self.config.template_path else "Not selected"
        )
        self.template_label.setToolTip(
            str(self.paths.root / self.config.template_path) if self.config.template_path else ""
        )
        if self.config.calibrated and self.mode == "STOPPED":
            self.details.setText("Ready. Keep Loki outside the selected capture area.")

    def register_hotkey(self):
        if self._hotkey_registered:
            unregister_stop_hotkey(int(self.winId()))
        self._hotkey_registered = register_stop_hotkey(int(self.winId()), self.config.stop_hotkey)
        if IS_WINDOWS and not self._hotkey_registered:
            self.hotkey_label.setText(
                "Emergency hotkey unavailable. Choose another in Settings; tray Stop remains available."
            )
            self.logger.warning(
                "Emergency stop hotkey registration failed: %s", self.config.stop_hotkey
            )
        elif IS_WINDOWS:
            self.hotkey_label.setText(f"Emergency stop: {self.config.stop_hotkey}")
        else:
            self.hotkey_label.setText(
                "Windows build: global emergency stop · " + self.config.stop_hotkey
            )

    def update_controls(self):
        stopping = self.mode == "STOPPED" and self.worker is not None and self.worker.isRunning()
        active = self.mode != "STOPPED"
        self.state.setText(self.mode)
        self.start_button.setEnabled(not active and not stopping)
        self.stop_button.setEnabled(active or stopping)
        self.test_button.setEnabled(not stopping)
        self.calibrate_button.setEnabled(not stopping)
        self.settings_button.setEnabled(not stopping)
        self.tray_actions["Start Monitoring"].setEnabled(not active and not stopping)
        self.tray_actions["Stop Monitoring"].setEnabled(active or stopping)
        for name in ("Calibrate", "Test Detection"):
            self.tray_actions[name].setEnabled(not stopping)
        self.tray.setIcon(tray_icon(active, self.mode == "TESTING"))
        self.tray.setToolTip("Loki · " + self.mode)
        self.update_automation_display()

    def show_error(self, message):
        self.details.setText(message)
        if self.isVisible():
            QMessageBox.warning(self, "Loki", message)
        elif self.tray_available:
            self.tray.showMessage(
                "Loki stopped", message, QSystemTrayIcon.MessageIcon.Warning, 8000
            )

    def _start(self, diagnostic=False):
        if self.mode != "STOPPED":
            self.stop_monitoring()
        if self.worker is not None and self.worker.isRunning():
            self.show_error("Loki is finishing the previous capture. Try again in a moment.")
            return False
        try:
            self.config.validate(require_calibration=True)
        except Exception as exc:
            self.show_error(str(exc))
            return False
        if self.worker is not None:
            self.worker.deleteLater()
        self._run_id += 1
        token = self._run_id
        worker = MonitorWorker(self.config, self.paths, diagnostic, parent=self)
        self.worker = worker
        worker.report.connect(lambda report, run=token: self.on_report(report, run))
        worker.failed.connect(lambda message, run=token: self.on_failure(message, run))
        worker.finished.connect(lambda run=token: self.on_worker_finished(run))
        self.mode = "TESTING" if diagnostic else "MONITORING"
        self.details.setText(
            "Diagnostic preview only." if diagnostic else "Waiting for a confirmed target"
        )
        self.logger.info(
            "%s started region=%s fps=%s", self.mode, self.config.search_region, self.config.fps
        )
        self.update_controls()
        worker.start()
        return True

    def start_monitoring(self):
        if self.mode == "MONITORING":
            return
        if self._diagnostic is not None:
            self._diagnostic.close()
        self._start(False)

    def stop_monitoring(self):
        self.controller.disarm("monitoring stopped")
        previous = self.mode
        self._run_id += 1  # Discard queued frames so stopping can never emit a late alert.
        self.mode = "STOPPED"
        self.target_overlay.hide_target()
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1500)
        if previous != "STOPPED":
            self.logger.info("%s stopped", previous)
        self.details.setText("Monitoring stopped.")
        self.fps_label.setText("0")
        self.update_controls()

    def emergency_stop(self):
        self.logger.info("EMERGENCY STOP")
        self.stop_monitoring()
        if self._diagnostic is not None:
            self._diagnostic.close()

    def on_report(self, report: FrameReport, token):
        if token != self._run_id or self.mode == "STOPPED":
            return
        result = report.result
        self.confidence_label.setText(f"{result.confidence:.3f}")
        self.fps_label.setText(f"{report.fps:.1f}")
        if self.mode == "TESTING":
            if self._diagnostic is not None:
                self._diagnostic.update_report(report)
            return
        self.coordinates_label.setText(f"{result.x}, {result.y} · {result.width} × {result.height}")
        self.controller.consume(report)
        self.update_automation_display()

    def notify_report(self, report):
        result, update = report.result, report.update
        self.details.setText(
            (
                "Target detected · manual click"
                if self.operating_mode == MONITOR
                else "Target detected · Offline Automation"
            )
            if update.visible
            else "Waiting for disappearance confirmation"
            if update.state.value == "TRIGGERED"
            else update.state.value + " · waiting for a target"
        )
        if update.triggered:
            self.last_detection_label.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.audio.play(self.config.sound_enabled)
        if update.visible and self.config.overlay_enabled:
            try:
                self.target_overlay.show_target(result.bounds, self.config.monitor)
            except Exception as exc:
                self.stop_monitoring()
                self.logger.exception("Overlay failed")
                self.show_error(str(exc))
        else:
            self.target_overlay.hide_target()

    def on_failure(self, message, token):
        if token == self._run_id:
            self.stop_monitoring()
            self.show_error(message)

    def on_worker_finished(self, token):
        if token == self._run_id and self.mode != "STOPPED":
            self.stop_monitoring()
        self.update_controls()
        if self._quitting:
            self.finish_exit()

    def calibrate(self):
        self.open_window()
        self.stop_monitoring()
        if self._diagnostic is not None:
            self._diagnostic.close()
        try:
            dialog = CalibrationDialog(
                self.config,
                self.paths,
                self,
                save_callback=self.persist_detection,
                target_label=self.workspace.active.name,
            )
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_config is not None:
                self.config = dialog.saved_config
                self.logger.info(
                    "Calibration saved monitor=%s region=%s template=%s",
                    self.config.monitor.name,
                    self.config.search_region,
                    self.config.template_path,
                )
                self.refresh_config()
        except Exception as exc:
            self.show_error(str(exc))

    def test_detection(self):
        if self._diagnostic is not None:
            self._diagnostic.raise_()
            self._diagnostic.activateWindow()
            return
        self.open_window()
        self.stop_monitoring()
        try:
            self.config.validate(require_calibration=True)
        except Exception as exc:
            self.show_error(str(exc))
            return
        self._diagnostic = DiagnosticDialog(self.config, self)
        self._diagnostic.finished.connect(self.diagnostic_closed)
        self._diagnostic.show()
        if not self._start(True):
            self._diagnostic.close()

    def diagnostic_closed(self, _result):
        dialog = self._diagnostic
        self._diagnostic = None
        if self.mode == "TESTING":
            self.stop_monitoring()
        if dialog is not None:
            dialog.deleteLater()

    def settings(self):
        self.open_window()
        self.stop_monitoring()
        if self._diagnostic is not None:
            self._diagnostic.close()
        dialog = SettingsDialog(self.config, self.paths, self, save_callback=self.persist_detection)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.new_config is not None:
            self.config = dialog.new_config
            self.refresh_config()
            self.register_hotkey()
            self.logger.info("Settings saved")

    def tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_window()

    def open_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
        elif self.tray_available:
            self.hide()
            event.ignore()
            if not self._hide_tip_shown:
                self.tray.showMessage(
                    "Loki",
                    "Loki is still available in the tray. Use Exit there to close it.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
                self._hide_tip_shown = True
        else:
            event.ignore()
            self.exit_app()

    def exit_app(self):
        if self._quitting:
            return
        self._quitting = True
        self.stop_monitoring()
        if self._diagnostic is not None:
            self._diagnostic.close()
        if self.worker is not None and self.worker.isRunning():
            # Keep the event loop alive until the capture returns; never kill a QThread.
            self.details.setText("Finishing screen capture before exit…")
            return
        self.finish_exit()

    def finish_exit(self):
        self._action_timer.stop()
        self.controller.close()
        if self._hotkey_registered:
            unregister_stop_hotkey(int(self.winId()))
            self._hotkey_registered = False
        QApplication.instance().removeNativeEventFilter(self._hotkey_filter)
        self.tray.hide()
        self.target_overlay.hide_target()
        self.logger.info("Application exit")
        QTimer.singleShot(0, QApplication.instance().quit)
