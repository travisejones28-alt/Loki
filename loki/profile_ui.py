"""Profile editing and explicit binding to a verified, currently running window."""

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .identity import TargetBinding
from .profiles import MONITOR, OFFLINE, save_workspace
from .protection import profile_is_protected, protected_reason


class ProfileDialog(QDialog):
    def __init__(self, workspace, paths, parent=None, desktop_factory=None):
        super().__init__(parent)
        self.workspace, self.paths = workspace, paths
        self.profile = workspace.active
        self.binding = self.profile.target
        self.desktop_factory = desktop_factory
        self.desktop = None
        self.identities = []
        self.new_workspace = None
        self.setWindowTitle("Loki · Profile and target")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        page = QWidget()
        tab = QVBoxLayout(page)
        form = QFormLayout()
        self.name = QLineEdit(self.profile.name)
        form.addRow("Profile name", self.name)
        self.only_monitor = QCheckBox("MONITOR ONLY — input unavailable")
        self.only_monitor.setChecked(self.profile.monitor_only)
        if profile_is_protected(self.profile):
            self.only_monitor.setEnabled(False)
        form.addRow(self.only_monitor)
        self.action = QComboBox()
        for label, value in (
            ("Alert only", "alert"),
            ("Move cursor to target", "move"),
            ("Move cursor and left-click", "click"),
        ):
            self.action.addItem(label, value)
        self.action.setCurrentIndex(self.action.findData(self.profile.action))
        form.addRow("Offline action", self.action)
        self.delay = QSpinBox()
        self.delay.setRange(0, 5000)
        self.delay.setSuffix(" ms")
        self.delay.setValue(self.profile.delay_ms)
        form.addRow("Action delay", self.delay)
        self.dry = QCheckBox("Dry Run — validate and log, never move/click")
        self.dry.setChecked(self.profile.dry_run)
        form.addRow(self.dry)
        tab.addLayout(form)
        self.bound = QLabel()
        self.bound.setWordWrap(True)
        tab.addWidget(self.bound)
        self.windows = QComboBox()
        self.windows.setMinimumContentsLength(30)
        tab.addWidget(self.windows)
        refresh = QPushButton("Refresh running windows")
        refresh.clicked.connect(self.refresh_windows)
        tab.addWidget(refresh)
        bind = QPushButton("Bind selected running window")
        bind.clicked.connect(self.bind_window)
        tab.addWidget(bind)
        self.pattern = QLineEdit(self.binding.title_pattern if self.binding else "")
        self.pattern.setPlaceholderText(
            "Blank = exact bound title; optional wildcard such as My Game*"
        )
        tab.addWidget(
            QLabel(
                "Window title pattern (executable, PID, creation time and handle are also checked)"
            )
        )
        tab.addWidget(self.pattern)
        note = QLabel(
            "Saving, binding, calibration and profile/mode changes disarm automation.\n"
            "Rebind after the application restarts or its window bounds change. WoW targets become Monitor Only."
        )
        note.setWordWrap(True)
        tab.addWidget(note)
        tabs.addTab(page, "Profile / Target")
        rules_page = QWidget()
        rules_layout = QVBoxLayout(rules_page)
        note = QLabel(
            "Built-in WoW names, installation paths, titles, classes and version metadata are always protected.\n"
            "You may add rules below; built-in protection cannot be removed. Battle.net is not a built-in block."
        )
        note.setWordWrap(True)
        rules_layout.addWidget(note)
        self.rule_editors = {}
        for name, label in (
            ("protected_executables", "Extra protected executable names / wildcards"),
            ("protected_titles", "Extra protected window title wildcards"),
            ("protected_paths", "Extra protected full executable path wildcards"),
        ):
            rules_layout.addWidget(QLabel(label + " — one per line"))
            editor = QPlainTextEdit("\n".join(getattr(workspace, name)))
            editor.setMaximumHeight(90)
            self.rule_editors[name] = editor
            rules_layout.addWidget(editor)
        tabs.addTab(rules_page, "Protected applications")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.only_monitor.toggled.connect(self.update_bound)
        self.finished.connect(self.close_desktop)
        self.update_bound()

    def update_bound(self, *_args):
        self.action.setEnabled(not self.only_monitor.isChecked())
        self.delay.setEnabled(not self.only_monitor.isChecked())
        self.dry.setEnabled(not self.only_monitor.isChecked())
        if self.binding:
            target = self.binding.window
            self.bound.setText(
                f"Bound: {target.title}\n{target.executable_path}\nPID {target.pid} · HWND {target.hwnd}"
            )
        else:
            self.bound.setText("No target bound. Offline Automation cannot be armed.")

    def refresh_windows(self):
        try:
            if self.desktop is None:
                if self.desktop_factory is None:
                    from .desktop import WindowsDesktop

                    self.desktop_factory = WindowsDesktop
                self.desktop = self.desktop_factory()
            self.identities = self.desktop.list_windows()
            self.windows.clear()
            for window in self.identities:
                suffix = " [PROTECTED / MONITOR ONLY]" if protected_reason(window) else ""
                self.windows.addItem(
                    f"{window.executable_name} · PID {window.pid} · {window.title}{suffix}"
                )
            if not self.identities:
                self.bound.setText("No verifiable running target windows found.")
        except Exception as exc:
            QMessageBox.warning(self, "Target windows", str(exc))

    def bind_window(self):
        try:
            index = self.windows.currentIndex()
            if index < 0 or index >= len(self.identities):
                raise ValueError("Refresh windows and select a running target first.")
            selected = self.identities[index]
            current = self.desktop.inspect_window(selected.hwnd)
            from .safety import same_process_window

            if not same_process_window(selected, current):
                raise ValueError("The selected window changed. Refresh the list.")
            self.binding = TargetBinding(current)
            self.pattern.clear()
            if protected_reason(current):
                self.only_monitor.setChecked(True)
                self.only_monitor.setEnabled(False)
            self.update_bound()
        except Exception as exc:
            QMessageBox.warning(self, "Bind target", str(exc))

    def save(self):
        try:
            binding = (
                replace(self.binding, title_pattern=self.pattern.text()) if self.binding else None
            )
            profile = replace(
                self.profile,
                name=self.name.text().strip(),
                target=binding,
                monitor_only=self.only_monitor.isChecked(),
                intended_mode=MONITOR if self.only_monitor.isChecked() else OFFLINE,
                action="alert" if self.only_monitor.isChecked() else self.action.currentData(),
                delay_ms=self.delay.value(),
                dry_run=self.dry.isChecked(),
            )
            if profile_is_protected(profile):
                profile = replace(profile, monitor_only=True, intended_mode=MONITOR, action="alert")
            rules = {
                name: tuple(
                    line.strip() for line in editor.toPlainText().splitlines() if line.strip()
                )
                for name, editor in self.rule_editors.items()
            }
            workspace = replace(self.workspace.with_profile(profile), **rules)
            save_workspace(workspace, self.paths)
            self.new_workspace = workspace
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Save profile", str(exc))

    def close_desktop(self, *_args):
        if self.desktop is not None:
            self.desktop.close()
            self.desktop = None
