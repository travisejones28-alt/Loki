"""All input is fake, on both Windows and Linux runners."""

import json
import logging
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from loki.automation import ActionController
from loki.config import AppConfig, ConfigError, Monitor, Rect
from loki.detector import DetectionResult
from loki.engine import FrameReport
from loki.identity import TargetBinding, WindowIdentity
from loki.input_controller import InputController, InputError, normalized_absolute
from loki.modes import MonitorModeController
from loki.profiles import Profile, WorkspaceConfig, load_workspace, save_workspace
from loki.protection import protected_reason
from loki.state_machine import DetectionLatch


class Clock:
    value = 100.0

    def __call__(self):
        return self.value


class Desktop:
    def __init__(self, window, monitor):
        self.window = self.active = self.owner = window
        self.monitors = [monitor]
        self.focus_epoch = 0
        self.focus_changed_at = 0
        self.stamp = 1
        self.cursor = (0, 0)
        self.buttons = False

    def inspect_window(self, hwnd):
        if self.window is None or self.window.hwnd != hwnd:
            raise ValueError("Target exited")
        return self.window

    def foreground(self):
        if self.active is None:
            raise ValueError("Unknown foreground")
        return self.active

    def window_at(self, point):
        return self.owner

    def displays(self):
        return self.monitors

    def input_stamp(self):
        return self.stamp

    def cursor_position(self):
        return self.cursor

    def buttons_or_modifiers_down(self):
        return self.buttons

    def close(self):
        pass

    def focus(self, window, now):
        self.active = window
        self.focus_epoch += 1
        self.focus_changed_at = now


@pytest.fixture
def rig():
    monitor = Monitor(2, "DISPLAY2", Rect(-1920, 0, 1920, 1080), 1.25)
    window = WindowIdentity(
        100,
        200,
        300,
        r"C:\Games\Local\Game.exe",
        "Game.exe",
        "Local Game",
        "LocalWindow",
        monitor.bounds,
        monitor.bounds,
    )
    detection = AppConfig(
        monitor=monitor,
        search_region=Rect(-1500, 200, 400, 300),
        template_path="templates/button.png",
    )
    profile = Profile(
        id="local",
        name="Local Game",
        detection=detection,
        monitor_only=False,
        intended_mode="offline",
        action="click",
        dry_run=False,
        target=TargetBinding(window),
    )
    workspace = WorkspaceConfig(profiles=(profile,), active_profile_id="local")
    desktop, clock, sent, factories = Desktop(window, monitor), Clock(), [], []
    result = DetectionResult(True, 0.99, -1400, 250, 140, 42, present=True)

    def factory(authorize):
        factories.append(True)
        return InputController(authorize, lambda action, point: sent.append((action, point)), clock)

    controller = ActionController(profile, workspace, desktop, lambda _: result, factory, clock)
    latch = DetectionLatch(4, 5)

    def frame(value=None):
        value = result if value is None else value
        clock.value += 0.1
        controller.observe(FrameReport(value, latch.update(value), 10, captured_at=clock()))
        controller.tick()

    return SimpleNamespace(**locals())


def appearance(rig):
    for _ in range(4):
        rig.frame()


def missing(rig):
    for _ in range(5):
        rig.frame(DetectionResult(False, 0))


def test_monitor_has_no_input_service_or_arm(rig):
    notified = []
    monitor = MonitorModeController(notified.append)
    with pytest.raises(ValueError):
        monitor.arm()
    for _ in range(20):
        monitor.consume(FrameReport(rig.result, rig.latch.update(rig.result), 10))
    assert len(notified) == 20 and not monitor.armed
    assert set(vars(monitor)) == {"notify"}
    assert rig.sent == rig.factories == []


def test_fresh_controller_disarmed(rig):
    appearance(rig)
    assert not rig.controller.armed and rig.sent == rig.factories == []


@pytest.mark.parametrize(
    "name,only", [("WoW - VoidLink WHO", True), ("World of Warcraft", False), ("Local Game", True)]
)
def test_monitor_only_cannot_arm(rig, name, only):
    profile = replace(rig.profile, name=name, monitor_only=only)
    controller = ActionController(
        profile, rig.workspace, rig.desktop, lambda _: rig.result, rig.factory, rig.clock
    )
    with pytest.raises(ValueError):
        controller.arm()
    assert not controller.armed and rig.factories == []


def test_one_action_per_appearance_and_rearm(rig):
    rig.controller.arm()
    appearance(rig)
    for _ in range(30):
        rig.frame()
    assert rig.sent == [("click", (-1330, 271))]
    # Even a duplicate confirmed event cannot bypass the controller's appearance latch.
    rig.controller.observe(
        FrameReport(
            rig.result,
            replace(rig.latch.update(rig.result), triggered=True),
            10,
            captured_at=rig.clock(),
        )
    )
    assert len(rig.sent) == 1
    missing(rig)
    appearance(rig)
    assert len(rig.sent) == 2


@pytest.mark.parametrize("action", ["alert", "move", "click"])
def test_independent_actions(rig, action):
    rig.controller.profile = replace(rig.profile, action=action)
    rig.controller.arm()
    appearance(rig)
    assert rig.sent == ([] if action == "alert" else [(action, (-1330, 271))])


def test_dry_run_never_constructs_input(rig, caplog):
    rig.controller.profile = replace(rig.profile, dry_run=True)
    rig.controller.arm()
    with caplog.at_level(logging.INFO, logger="loki"):
        appearance(rig)
    assert "DRY RUN WOULD CLICK x=-1330 y=271" in caplog.text
    assert rig.factories == rig.sent == []


@pytest.mark.parametrize(
    "change",
    [
        "foreground",
        "exit",
        "pid",
        "created",
        "hwnd",
        "path",
        "title",
        "bounds",
        "scale",
        "monitor",
        "owner",
        "unknown",
        "coordinates",
    ],
)
def test_invalid_safety_blocks_input(rig, change):
    rig.controller.arm()
    other = replace(rig.window, hwnd=101, pid=201)
    if change == "foreground":
        rig.desktop.active = other
    elif change == "exit":
        rig.desktop.window = None
    elif change in ("pid", "created", "hwnd"):
        rig.desktop.window = replace(rig.window, **{change: 999})
    elif change == "path":
        rig.desktop.window = replace(rig.window, executable_path=r"D:\Game.exe")
    elif change == "title":
        rig.desktop.window = replace(rig.window, title="Changed")
    elif change == "bounds":
        rig.desktop.window = replace(rig.window, bounds=Rect(-1920, 0, 1800, 1080))
    elif change == "scale":
        rig.desktop.monitors = [replace(rig.monitor, scale=1.5)]
    elif change == "monitor":
        rig.desktop.monitors = []
    elif change == "owner":
        rig.desktop.owner = other
    elif change == "unknown":
        rig.desktop.active = None
    elif change == "coordinates":
        rig.result = replace(rig.result, x=100)
    if change == "coordinates":
        for _ in range(4):
            rig.clock.value += 0.1
            rig.controller.observe(
                FrameReport(rig.result, rig.latch.update(rig.result), 10, captured_at=rig.clock())
            )
    else:
        appearance(rig)
    assert rig.sent == rig.factories == []


@pytest.mark.parametrize(
    "metadata",
    [
        {"executable_name": "WoW.exe", "executable_path": r"C:\Games\WoW.exe"},
        {"executable_name": "WowClassic.exe", "executable_path": r"C:\Games\WowClassic.exe"},
        {"title": "World of Warcraft"},
        {"class_name": "GxWindowClass"},
        {"executable_path": r"C:\World of Warcraft\Game.exe"},
        {"original_filename": "WoW.exe"},
        {"product_name": "World of Warcraft"},
    ],
)
def test_wow_identifiers_protect_foreground(rig, metadata):
    rig.controller.arm()
    rig.desktop.active = replace(rig.window, **metadata)
    appearance(rig)
    assert rig.sent == rig.factories == []
    assert "PROTECTED APPLICATION" in rig.controller.blocked


def delayed(rig):
    rig.controller.profile = replace(rig.profile, delay_ms=500)
    rig.controller.arm()
    appearance(rig)
    assert rig.controller.pending is not None


def test_alt_tab_wow_cancels_and_return_never_replays(rig):
    delayed(rig)
    wow = replace(rig.window, hwnd=101, pid=201, title="World of Warcraft")
    rig.desktop.focus(wow, rig.clock())
    rig.controller.tick()
    assert rig.controller.pending is None and rig.controller.armed
    rig.desktop.focus(rig.window, rig.clock())
    for _ in range(15):
        rig.frame()
    assert rig.sent == []
    missing(rig)
    appearance(rig)
    for _ in range(6):
        rig.frame()
    assert len(rig.sent) == 1


def test_away_and_back_between_frames_cancels(rig):
    delayed(rig)
    rig.desktop.focus(replace(rig.window, title="World of Warcraft"), rig.clock())
    rig.desktop.focus(rig.window, rig.clock())
    for _ in range(12):
        rig.frame()
    assert rig.sent == [] and rig.controller.pending is None


@pytest.mark.parametrize(
    "cause",
    [
        "emergency",
        "disappeared",
        "moved",
        "manual",
        "buttons",
        "stale",
        "final_probe",
        "focus_during_probe",
        "display_during_probe",
    ],
)
def test_pending_cancelled(rig, cause):
    delayed(rig)
    if cause == "emergency":
        rig.controller.disarm("EMERGENCY STOP")
    elif cause == "disappeared":
        rig.frame(DetectionResult(False, 0))
    elif cause == "moved":
        rig.frame(replace(rig.result, x=rig.result.x + 1))
    elif cause == "manual":
        rig.desktop.cursor = (30, 0)
    elif cause == "buttons":
        rig.desktop.buttons = True
    elif cause == "stale":
        rig.clock.value += 2
        rig.controller.tick()
    elif cause == "final_probe":
        rig.controller.visual_verify = lambda _: None
    elif cause == "focus_during_probe":

        def probe(_):
            rig.desktop.focus(replace(rig.window, title="World of Warcraft"), rig.clock())
            return rig.result

        rig.controller.visual_verify = probe
    elif cause == "display_during_probe":

        def probe(_):
            rig.desktop.monitors = [replace(rig.monitor, scale=1.5)]
            return rig.result

        rig.controller.visual_verify = probe
    for _ in range(8):
        rig.frame()
    assert rig.sent == [] and rig.controller.pending is None


def test_battlenet_is_not_global_block(rig):
    launcher = replace(
        rig.window,
        title="Battle.net",
        executable_name="Battle.net.exe",
        executable_path=r"C:\Battle.net\Battle.net.exe",
    )
    assert protected_reason(launcher) is None
    # A running launcher is irrelevant: only the bound target/foreground/point owner is checked.
    rig.desktop.running_launcher = launcher
    rig.controller.arm()
    appearance(rig)
    assert len(rig.sent) == 1


def test_input_rate_limit_and_final_authorization(rig):
    gate = [True]
    controller = InputController(
        lambda _: gate[0], lambda action, point: rig.sent.append((action, point)), rig.clock
    )
    assert not controller.click(1, 2)
    controller.arm()
    assert controller.click(1, 2)
    assert not controller.click(1, 2)
    rig.clock.value += 1
    gate[0] = False
    assert not controller.click(1, 2)
    controller.disarm()
    assert not controller.move_to(1, 2)
    assert len(rig.sent) == 1


def test_hard_per_minute_limit(rig):
    controller = InputController(lambda _: True, lambda *_: None, rig.clock)
    controller.arm()
    for _ in range(30):
        assert controller.click(1, 2)
        rig.clock.value += 1
    assert not controller.click(1, 2)
    rig.clock.value += 31
    assert controller.click(1, 2)


def test_input_error_disarms(rig):
    def failure(*_):
        raise OSError("OS rejected input")

    controller = InputController(lambda _: True, failure)
    controller.arm()
    with pytest.raises(InputError):
        controller.click(1, 2)
    assert not controller.armed


@pytest.mark.parametrize("scale", [1, 1.25, 1.5])
def test_secondary_negative_origin_physical_coordinates(rig, scale):
    monitor = replace(rig.monitor, scale=scale)
    profile = replace(rig.profile, detection=replace(rig.detection, monitor=monitor))
    rig.desktop.monitors = [monitor]
    controller = ActionController(
        profile, rig.workspace, rig.desktop, lambda _: rig.result, rig.factory, rig.clock
    )
    controller.arm()
    for _ in range(4):
        rig.clock.value += 0.1
        controller.observe(
            FrameReport(rig.result, rig.latch.update(rig.result), 10, captured_at=rig.clock())
        )
    assert rig.sent == [("click", (-1330, 271))]
    assert normalized_absolute((-1920, 0), (-1920, 0, 3840, 1080)) == (0, 0)
    with pytest.raises(InputError):
        normalized_absolute((-1921, 0), (-1920, 0, 3840, 1080))


def test_v1_migration_preserves_every_setting_and_images(paths, rig):
    legacy = replace(
        rig.detection,
        context_path="templates/context.png",
        context_offset_x=6,
        context_offset_y=6,
        start_with_windows=True,
        monitor_on_launch=True,
        stop_hotkey="Ctrl+Shift+F6",
        sound_enabled=False,
        overlay_enabled=False,
        debug_screenshots=True,
        fps=15,
        confirmation_frames=5,
        disappearance_frames=7,
    )
    raw = json.dumps(asdict(legacy)).encode()
    paths.config.write_bytes(raw)
    for name in ("button.png", "context.png"):
        (paths.root / "templates" / name).write_bytes(b"unchanged")
    workspace, warning = load_workspace(paths)
    assert warning is None and workspace.schema_version == 2
    assert workspace.active.detection == legacy
    assert workspace.active.monitor_only and not workspace.active.automation_capable
    assert workspace.runtime_detection == legacy
    assert workspace.start_with_windows and workspace.monitor_on_launch
    assert (paths.root / "config.v1.backup.json").read_bytes() == raw
    assert (paths.root / "templates/button.png").read_bytes() == b"unchanged"
    assert json.loads(paths.config.read_text())["schema_version"] == 2
    assert load_workspace(paths) == (workspace, None)


def test_profile_roundtrip_and_bad_config(paths, rig):
    save_workspace(rig.workspace, paths)
    assert load_workspace(paths) == (rig.workspace, None)
    for bad in (replace(rig.profile, delay_ms=-1), replace(rig.profile, action="keyboard")):
        with pytest.raises(ConfigError):
            bad.validate()
    data = json.loads(json.dumps(asdict(rig.workspace)))
    data["profiles"][0]["name"] = None
    with pytest.raises(ConfigError):
        WorkspaceConfig.from_dict(data)


def test_protection_cannot_be_disabled_by_json(rig):
    data = json.loads(json.dumps(asdict(rig.workspace)))
    data["profiles"][0]["target"]["window"]["title"] = "World of Warcraft"
    config = WorkspaceConfig.from_dict(data)
    assert config.active.monitor_only and config.active.intended_mode == "monitor"


@pytest.fixture
def mode_window(app, paths, rig, monkeypatch):
    from loki.ui import MainWindow

    monkeypatch.setattr("loki.ui.register_stop_hotkey", lambda *_: False)
    monkeypatch.setattr("loki.ui.IS_WINDOWS", False)
    workspace = replace(
        rig.workspace,
        profiles=rig.workspace.profiles + (Profile(),),
        start_with_windows=True,
        monitor_on_launch=True,
    )
    window = MainWindow(workspace, paths)
    window.desktop_factory = lambda: rig.desktop
    window.show_error = lambda *_: None
    yield window
    window.stop_monitoring()
    window.controller.close()
    window._action_timer.stop()
    app.removeNativeEventFilter(window._hotkey_filter)
    window.tray.hide()
    window._quitting = True
    window.close()
    window.deleteLater()
    app.processEvents()


def test_startup_and_startup_settings_never_select_offline(mode_window):
    assert mode_window.workspace.monitor_on_launch and mode_window.workspace.start_with_windows
    assert mode_window.operating_mode == "monitor"
    assert isinstance(mode_window.controller, MonitorModeController)
    assert not mode_window.controller.armed


@pytest.mark.parametrize(
    "transition",
    ["monitor", "profile", "calibration", "settings", "target", "emergency", "diagnostics"],
)
def test_ui_transitions_disarm_cancel_and_discard_queued_reports(
    mode_window, rig, monkeypatch, transition
):
    from PySide6.QtWidgets import QDialog

    import loki.profile_ui

    window = mode_window
    window.set_operating_mode("offline")
    old = window.controller.actions
    old.input_factory = rig.factory
    old.clock = rig.clock
    old.profile = replace(rig.profile, delay_ms=500)
    old.visual_verify = lambda _: rig.result
    old.arm()
    for _ in range(4):
        rig.clock.value += 0.1
        old.observe(
            FrameReport(rig.result, rig.latch.update(rig.result), 10, captured_at=rig.clock())
        )
    assert old.pending
    token = window._run_id

    class CancelDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr("loki.ui.CalibrationDialog", CancelDialog)
    monkeypatch.setattr("loki.ui.SettingsDialog", CancelDialog)
    monkeypatch.setattr(loki.profile_ui, "ProfileDialog", CancelDialog)
    if transition == "monitor":
        window.set_operating_mode("monitor")
    elif transition == "profile":
        window.select_profile(1)
    elif transition == "calibration":
        window.calibrate()
    elif transition == "settings":
        window.settings()
    elif transition == "target":
        window.edit_profile()
    elif transition == "emergency":
        window.emergency_stop()
    elif transition == "diagnostics":
        monkeypatch.setattr(window, "_start", lambda *_: False)
        window.test_detection()
    assert not old.armed and old.pending is None
    window.on_report(
        FrameReport(rig.result, replace(rig.latch.update(rig.result), triggered=True), 10), token
    )
    old.tick()
    assert rig.sent == []


def test_shared_detector_and_passive_modules_have_no_input_dependencies():
    import ast
    from pathlib import Path

    for name in ("detector", "capture", "state_machine", "calibration", "engine"):
        tree = ast.parse((Path(__file__).parents[1] / "loki" / (name + ".py")).read_text())
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(
            module.endswith(("automation", "input_controller", "desktop")) for module in imports
        )
    import loki.modes

    assert "input_controller" not in vars(loki.modes)


def test_denied_target_and_extra_rules_cannot_arm(rig):
    wow = replace(rig.window, title="World of Warcraft")
    protected = replace(rig.profile, target=TargetBinding(wow))
    controller = ActionController(
        protected, rig.workspace, rig.desktop, lambda _: rig.result, rig.factory, rig.clock
    )
    with pytest.raises(ValueError):
        controller.arm()
    workspace = replace(rig.workspace, protected_executables=("game.exe",))
    controller = ActionController(
        rig.profile, workspace, rig.desktop, lambda _: rig.result, rig.factory, rig.clock
    )
    with pytest.raises(ValueError):
        controller.arm()
    assert rig.factories == []


def test_windows_native_sender_final_gate_uses_fake_api(monkeypatch):
    import loki.input_controller as module

    calls = []

    class Function:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    user = SimpleNamespace(
        GetSystemMetrics=Function(lambda index: {76: -1920, 77: 0, 78: 3840, 79: 1080}[index]),
        SendInput=Function(
            lambda count, records, size: (
                calls.append([record.mi.dwFlags for record in records]) or count
            )
        ),
    )
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module.ctypes, "WinDLL", lambda *_args, **_kwargs: user, raising=False)
    gate = [False]
    sender = module.WindowsMouseSender(lambda _: gate[0])
    with pytest.raises(InputError):
        sender("click", (-1330, 271))
    assert calls == []
    gate[0] = True
    sender("move", (-1330, 271))
    sender("click", (-1330, 271))
    assert calls == [[0xC001], [0xC001, 2, 4]]


def test_final_input_gate_foreground_race_suppresses(rig):
    def factory(authorize):
        # Foreground changes after action policy validation, before native dispatch.
        rig.desktop.active = replace(rig.window, title="World of Warcraft")
        return InputController(authorize, lambda *args: rig.sent.append(args), rig.clock)

    rig.controller.input_factory = factory
    rig.controller.arm()
    appearance(rig)
    assert rig.sent == []


def test_real_runwho_template_detection_survives_migration(paths, monitor, popup):
    from loki.calibration import save_calibration
    from loki.detector import TemplateDetector

    config = save_calibration(
        AppConfig(), monitor, Rect(0, 0, 420, 240), Rect(135, 146, 140, 42), popup, paths
    )
    before = TemplateDetector.from_config(config, paths.root).detect(popup, (-1920, 0))
    workspace, warning = load_workspace(paths)
    after = TemplateDetector.from_config(workspace.active.detection, paths.root).detect(
        popup, (-1920, 0)
    )
    assert warning is None and before.matched and before == after
    assert workspace.active.monitor_only


def test_profile_binding_editor_persists_and_protects_wow(app, paths, rig):
    from loki.profile_ui import ProfileDialog

    rig.desktop.list_windows = lambda: [rig.window]
    dialog = ProfileDialog(rig.workspace, paths, desktop_factory=lambda: rig.desktop)
    dialog.refresh_windows()
    dialog.bind_window()
    assert dialog.binding.window == rig.window
    dialog.delay.setValue(500)
    dialog.action.setCurrentIndex(dialog.action.findData("move"))
    dialog.save()
    saved, warning = load_workspace(paths)
    assert warning is None and saved.active.action == "move" and saved.active.delay_ms == 500
    wow = replace(rig.window, title="World of Warcraft")
    rig.desktop.window = wow
    rig.desktop.list_windows = lambda: [wow]
    dialog = ProfileDialog(rig.workspace, paths, desktop_factory=lambda: rig.desktop)
    dialog.refresh_windows()
    dialog.bind_window()
    assert dialog.only_monitor.isChecked() and not dialog.only_monitor.isEnabled()
    dialog.save()
    saved, _ = load_workspace(paths)
    assert saved.active.monitor_only and not saved.active.automation_capable
