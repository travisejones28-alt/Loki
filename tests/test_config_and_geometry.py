import json
from dataclasses import asdict, replace

import pytest

from loki.capture import CaptureError, validate_display
from loki.config import (
    AppConfig,
    ConfigError,
    Rect,
    atomic_write,
    load_config,
    save_config,
)
from loki.geometry import physical_to_logical, selection_to_pixels


@pytest.mark.parametrize(
    "changes",
    [
        {"fps": 0},
        {"fps": 16},
        {"fps": True},
        {"confirmation_frames": 1},
        {"disappearance_frames": 31},
        {"confidence_threshold": float("nan")},
        {"confidence_threshold": 1.1},
        {"release_threshold": 0.99},
        {"sound_enabled": "true"},
        {"stop_hotkey": "Ctrl+Shift+F12"},
        {"template_path": "../other.png"},
        {"context_path": "C:\\other.png"},
        {"context_offset_x": -1},
        {"schema_version": True},
    ],
)
def test_invalid_config(changes):
    with pytest.raises(ConfigError):
        replace(AppConfig(), **changes).validate()


def test_defaults_are_valid_but_monitoring_requires_calibration():
    AppConfig().validate()
    with pytest.raises(ConfigError, match="Calibrate"):
        AppConfig().validate(require_calibration=True)


def test_incomplete_and_out_of_bounds_calibration_rejected(monitor):
    with pytest.raises(ConfigError, match="incomplete"):
        AppConfig(monitor=monitor).validate()
    with pytest.raises(ConfigError, match="outside"):
        AppConfig(
            monitor=monitor,
            search_region=Rect(0, 0, 200, 100),
            template_path="templates/runwho.png",
        ).validate()


def test_config_round_trip_without_manual_pixel_edits(paths, monitor):
    config = AppConfig(
        monitor=monitor,
        search_region=Rect(-1800, 100, 500, 300),
        template_path="templates/runwho.png",
    )
    save_config(config, paths)
    loaded, warning = load_config(paths)
    assert loaded == config and warning is None
    assert json.loads(paths.config.read_text())["monitor"]["scale"] == 1.25


def test_corrupt_configuration_is_preserved_and_disabled(paths):
    paths.config.write_text("{not json")
    loaded, warning = load_config(paths)
    assert not loaded.calibrated
    assert "recalibrate" in warning.lower() or "calibrate" in warning.lower()
    assert len(list(paths.root.glob("config.corrupt-*.json"))) == 1


@pytest.mark.parametrize("data", [[], {"unknown": 1}, {"monitor": {}}, {"search_region": []}])
def test_bad_json_shapes_rejected(data):
    with pytest.raises(ConfigError):
        AppConfig.from_dict(data)


def test_invalid_saved_calibration_never_used(paths, monitor):
    config = AppConfig(
        monitor=monitor,
        search_region=Rect(-1800, 100, 500, 300),
        template_path="templates/runwho.png",
    )
    data = asdict(config)
    data["search_region"]["width"] = "500"
    paths.config.write_text(json.dumps(data))
    assert load_config(paths)[0] == AppConfig()


def test_atomic_config_write_failure_preserves_old_file(paths, monkeypatch):
    paths.config.write_bytes(b"old config")

    def fail(*_args):
        raise OSError("disk failure")

    monkeypatch.setattr("loki.config.os.replace", fail)
    with pytest.raises(OSError):
        atomic_write(paths.config, b"replacement")
    assert paths.config.read_bytes() == b"old config"
    assert not list(paths.root.glob("*.tmp"))


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_dpi_mapping_on_negative_origin_monitor(scale):
    physical = Rect(-1920, -240, 1920, 1080)
    logical = Rect(-1920, -240, round(1920 / scale), round(1080 / scale))
    target = Rect(-1670, -115, 140, 42)
    mapped = physical_to_logical(target, physical, logical, scale)
    assert mapped.left == logical.left + int(250 // scale)
    assert mapped.top == logical.top + int(125 // scale)
    assert mapped.width * scale >= target.width
    assert mapped.height * scale >= target.height


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_drag_selection_maps_back_to_physical_pixels(scale):
    selected = selection_to_pixels(
        (100 / scale, 60 / scale),
        (300 / scale, 180 / scale),
        (1920 / scale, 1080 / scale),
        (1920, 1080),
    )
    assert selected == Rect(100, 60, 200, 120)


def test_drag_direction_and_allowed_region_clamping():
    allowed = Rect(100, 50, 200, 100)
    forward = selection_to_pixels((10, 10), (400, 200), (800, 600), (800, 600), allowed)
    backward = selection_to_pixels((400, 200), (10, 10), (800, 600), (800, 600), allowed)
    assert forward == backward == allowed


def test_resolution_scale_change_and_disconnect_stop_monitoring(monitor):
    config = AppConfig(
        monitor=monitor,
        search_region=Rect(-1800, 100, 500, 300),
        template_path="templates/runwho.png",
    )
    assert validate_display(config, [monitor]) == monitor
    assert validate_display(config, [replace(monitor, number=1)]).number == 1
    for displays in (
        [],
        [replace(monitor, scale=1.5)],
        [replace(monitor, bounds=Rect(-1920, 0, 1280, 720))],
    ):
        with pytest.raises(CaptureError):
            validate_display(config, displays)
