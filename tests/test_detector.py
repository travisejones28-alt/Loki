from dataclasses import replace

import cv2
import numpy as np
import pytest

from loki.config import AppConfig
from loki.detector import DetectionError, TemplateDetector, grayscale, read_image


def test_exact_match_coordinates_and_scores(button, popup):
    result = TemplateDetector(button, AppConfig()).detect(popup, origin=(-1920, 70))
    assert result.matched and result.present
    assert result.confidence > 0.999
    assert result.appearance == 1
    assert (result.x, result.y, result.width, result.height) == (-1785, 216, 140, 42)


def test_threshold_is_inclusive_and_rejects_lower_score(button):
    rng = np.random.default_rng(23)
    gray = grayscale(button)
    noisy = np.clip(gray.astype(float) + rng.normal(0, 24, gray.shape), 0, 255).astype(np.uint8)
    base = AppConfig(confidence_threshold=0.80, release_threshold=0.70, appearance_threshold=0.70)
    score = TemplateDetector(gray, base).detect(noisy).confidence
    assert 0.80 < score < 0.99
    equal = replace(base, confidence_threshold=score)
    above = replace(base, confidence_threshold=score + 0.005)
    assert TemplateDetector(gray, equal).detect(noisy).matched
    assert not TemplateDetector(gray, above).detect(noisy).matched


def test_constant_screen_never_triggers(button):
    result = TemplateDetector(button, AppConfig()).detect(np.zeros((240, 420, 3), np.uint8))
    assert not result.matched and not result.present


def test_wrong_text_and_random_noise_rejected(button):
    rng = np.random.default_rng(48)
    detector = TemplateDetector(button, AppConfig())
    for _ in range(12):
        assert not detector.detect(rng.integers(0, 256, (240, 420, 3), dtype=np.uint8)).matched


def test_correlation_alone_cannot_accept_a_brightened_button(button):
    changed = np.clip(button.astype(np.int16) + 65, 0, 255).astype(np.uint8)
    result = TemplateDetector(button, AppConfig()).detect(changed)
    assert result.confidence > 0.94
    assert result.appearance < 0.85
    assert not result.matched
    assert result.present  # hover brightness should keep an existing latch alive


def test_surrounding_context_rejects_same_button_in_wrong_popup(button, popup):
    config = AppConfig(context_offset_x=6, context_offset_y=6)
    context = popup[140:194, 129:281]
    detector = TemplateDetector(button, config, context)
    assert detector.detect(popup).matched
    wrong = popup.copy()
    wrong[140:194, 129:281] = 225
    wrong[146:188, 135:275] = button
    result = detector.detect(wrong)
    assert result.confidence > 0.999
    assert result.context_confidence < config.context_threshold
    assert not result.matched


def test_context_must_be_inside_search_area(button, popup):
    context = popup[140:194, 129:281]
    detector = TemplateDetector(button, AppConfig(context_offset_x=6, context_offset_y=6), context)
    assert not detector.detect(button).matched


@pytest.mark.parametrize("template", [np.zeros((42, 140), np.uint8), np.zeros((5, 5), np.uint8)])
def test_rejects_bad_templates(template):
    with pytest.raises(DetectionError):
        TemplateDetector(template, AppConfig())


@pytest.mark.parametrize(
    "image",
    [
        None,
        np.array([], dtype=np.uint8),
        np.zeros((40, 40), np.float32),
        np.zeros((30, 30, 2), np.uint8),
    ],
)
def test_rejects_bad_frames(image, button):
    with pytest.raises(DetectionError):
        TemplateDetector(button, AppConfig()).detect(image)


def test_template_larger_than_frame(button):
    with pytest.raises(DetectionError, match="larger"):
        TemplateDetector(button, AppConfig()).detect(np.zeros((20, 20, 3), np.uint8))


def test_reuses_match_buffer(button, popup):
    detector = TemplateDetector(button, AppConfig())
    detector.detect(popup)
    scores = detector._scores
    detector.detect(popup)
    assert detector._scores is scores


def test_loads_unicode_template_path(tmp_path, button):
    path = tmp_path / "Rün WHO.png"
    path.write_bytes(cv2.imencode(".png", button)[1].tobytes())
    assert np.array_equal(read_image(path), button)
    with pytest.raises(DetectionError):
        read_image(tmp_path / "missing.png")
    path.write_bytes(b"not an image")
    with pytest.raises(DetectionError):
        read_image(path)
