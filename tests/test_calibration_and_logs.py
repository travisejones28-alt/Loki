import logging

import numpy as np
import pytest

from loki.calibration import extract_assets, save_calibration
from loki.config import AppConfig, ConfigError, Rect, load_config
from loki.detector import TemplateDetector
from loki.logging_utils import save_debug_image, setup_logging


def test_calibration_saves_template_context_and_detection(paths, monitor, popup):
    search = Rect(0, 0, 420, 240)
    button = Rect(135, 146, 140, 42)
    config = save_calibration(AppConfig(), monitor, search, button, popup, paths)
    assert load_config(paths)[0] == config
    assert (paths.root / config.template_path).is_file()
    assert (paths.root / config.context_path).is_file()
    result = TemplateDetector.from_config(config, paths.root).detect(popup, origin=(-1920, 0))
    assert result.matched and (result.x, result.y) == (-1785, 146)
    assert config.context_offset_x == config.context_offset_y == 6


def test_recalibration_retains_preferences_and_removes_previous_assets(paths, monitor, popup):
    first = save_calibration(
        AppConfig(sound_enabled=False, fps=5),
        monitor,
        Rect(0, 0, 420, 240),
        Rect(135, 146, 140, 42),
        popup,
        paths,
    )
    second = save_calibration(
        first, monitor, Rect(0, 0, 420, 240), Rect(135, 146, 140, 42), popup, paths
    )
    assert not second.sound_enabled and second.fps == 5
    assert len(list((paths.root / "templates").glob("*.png"))) == 2
    assert not (paths.root / first.template_path).exists()


def test_context_clipped_at_search_edges_still_works(popup):
    button = Rect(135, 146, 140, 42)
    assets = extract_assets(popup, button, button)
    assert assets.offset_x == assets.offset_y == 0
    assert np.array_equal(assets.button, assets.context)


def test_button_cannot_be_outside_search(popup):
    with pytest.raises(ConfigError):
        extract_assets(popup, Rect(0, 0, 100, 100), Rect(135, 146, 140, 42))


def test_save_failure_keeps_previous_calibration(paths, monitor, popup, monkeypatch):
    args = (monitor, Rect(0, 0, 420, 240), Rect(135, 146, 140, 42), popup, paths)
    original = save_calibration(AppConfig(), *args)

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("loki.calibration.save_config", fail)
    with pytest.raises(OSError):
        save_calibration(original, *args)
    assert load_config(paths)[0] == original
    assert len(list((paths.root / "templates").glob("*.png"))) == 2


def test_debug_screenshots_are_annotated_and_bounded(paths, button, popup):
    result = TemplateDetector(button, AppConfig()).detect(popup)
    original = popup.copy()
    for _ in range(4):
        path = save_debug_image(paths, popup, result, (0, 0), maximum=3)
    assert path.exists()
    assert len(list((paths.root / "debug").glob("*.png"))) == 3
    assert np.array_equal(popup, original)


def test_logs_rotate_at_configured_limit(paths):
    logger = setup_logging(paths)
    for _ in range(750):
        logger.info("x" * 3100)
    for handler in logger.handlers:
        handler.flush()
    files = list((paths.root / "logs").glob("loki.log*"))
    assert 2 <= len(files) <= 4
    assert all(path.stat().st_size < 516_000 for path in files)


def test_new_logging_setup_does_not_duplicate_handlers(paths):
    setup_logging(paths)
    setup_logging(paths)
    assert len(logging.getLogger("loki").handlers) == 1
