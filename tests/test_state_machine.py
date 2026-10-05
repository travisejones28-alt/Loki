from dataclasses import replace

import pytest

from loki.detector import DetectionResult
from loki.state_machine import DetectionLatch, DetectorState

HIT = DetectionResult(True, 0.99, 100, 140, 140, 42, present=True)
MISS = DetectionResult(False, 0.1, present=False)


def test_confirmation_requires_consecutive_matching_frames():
    latch = DetectionLatch(4, 5)
    assert latch.update(HIT).candidate_started
    assert not latch.update(HIT).triggered
    assert not latch.update(HIT).triggered
    assert latch.update(HIT).triggered
    assert latch.state == DetectorState.TRIGGERED


def test_one_missing_frame_resets_confirmation():
    latch = DetectionLatch(3, 3)
    latch.update(HIT)
    latch.update(HIT)
    latch.update(MISS)
    assert not latch.update(HIT).triggered
    assert not latch.update(HIT).triggered
    assert latch.update(HIT).triggered


def test_candidate_must_be_stable_across_frames():
    latch = DetectionLatch(3, 3)
    latch.update(HIT)
    latch.update(replace(HIT, x=800))
    assert not latch.update(HIT).triggered
    assert not latch.update(HIT).triggered
    assert latch.update(HIT).triggered


def test_duplicate_alerts_suppressed_for_persistent_popup():
    latch = DetectionLatch(3, 3)
    updates = [latch.update(HIT) for _ in range(100)]
    assert sum(update.triggered for update in updates) == 1
    assert all(update.visible for update in updates[2:])


def test_disappearance_requires_consecutive_frames_before_rearm():
    latch = DetectionLatch(3, 4)
    for _ in range(3):
        latch.update(HIT)
    for _ in range(3):
        update = latch.update(MISS)
        assert not update.rearmed and not update.visible
    latch.update(HIT)  # transient loss did not clear the latch
    for _ in range(3):
        assert not latch.update(MISS).rearmed
    assert latch.update(MISS).rearmed
    assert latch.state == DetectorState.ARMED
    assert not latch.update(HIT).triggered
    assert not latch.update(HIT).triggered
    assert latch.update(HIT).triggered


def test_hover_hysteresis_preserves_latch_but_cannot_trigger():
    hover = replace(HIT, matched=False, confidence=0.90, present=True)
    latch = DetectionLatch(3, 3)
    for _ in range(10):
        assert not latch.update(hover).triggered
    for _ in range(3):
        latch.update(HIT)
    for _ in range(10):
        assert latch.update(hover).visible
        assert latch.state == DetectorState.TRIGGERED


def test_reset_allows_new_monitoring_session():
    latch = DetectionLatch(2, 2)
    latch.update(HIT)
    latch.update(HIT)
    latch.reset()
    assert latch.state == DetectorState.ARMED
    assert not latch.update(HIT).triggered
    assert latch.update(HIT).triggered


@pytest.mark.parametrize("counts", [(0, 4), (4, 1), (True, 4), (4, 2.5)])
def test_invalid_frame_counts(counts):
    with pytest.raises(ValueError):
        DetectionLatch(*counts)
