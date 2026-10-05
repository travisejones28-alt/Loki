import sys
import time
import types
import wave

import numpy as np
import pytest

from loki.alerts import AudioAlert, resource_path
from loki.calibration import save_calibration
from loki.config import AppConfig, Rect
from loki.engine import MonitorWorker


def test_worker_capture_confirmation_latch_and_rearm(app, paths, monitor, popup):
    config = save_calibration(
        AppConfig(confirmation_frames=3, disappearance_frames=3, fps=15),
        monitor,
        Rect(0, 0, 420, 240),
        Rect(135, 146, 140, 42),
        popup,
        paths,
    )
    missing = np.zeros_like(popup)
    frames = [popup] * 6 + [missing] * 3 + [popup] * 4
    reports, errors, captures = [], [], []

    class FakeCapture:
        def __enter__(self):
            self.index = 0
            return self

        def __exit__(self, *_args):
            captures.append("closed")

        def grab(self, region):
            assert region == config.search_region
            captures.append(time.monotonic())
            if self.index == len(frames):
                worker.stop()
                return missing
            image = frames[self.index]
            self.index += 1
            return image.copy()

    worker = MonitorWorker(
        config, paths, capture_factory=FakeCapture, monitor_provider=lambda: [monitor]
    )
    worker.report.connect(reports.append)
    worker.failed.connect(errors.append)
    worker.run()
    assert not errors
    assert sum(report.update.triggered for report in reports) == 2
    assert sum(report.update.rearmed for report in reports) == 1
    assert all(report.frame is None for report in reports)
    assert captures[-1] == "closed"
    intervals = np.diff(captures[:-1])
    assert min(intervals) > 0.04  # The worker waits; it cannot busy-loop.
    assert not (paths.root / "debug").exists()


def test_diagnostic_worker_never_saves_debug_images(app, paths, monitor, popup):
    config = save_calibration(
        AppConfig(confirmation_frames=2, debug_screenshots=True, fps=15),
        monitor,
        Rect(0, 0, 420, 240),
        Rect(135, 146, 140, 42),
        popup,
        paths,
    )
    reports = []

    class FakeCapture:
        def __enter__(self):
            self.index = 0
            return self

        def __exit__(self, *_args):
            pass

        def grab(self, _region):
            self.index += 1
            if self.index > 5:
                worker.stop()
            return popup.copy()

    worker = MonitorWorker(
        config,
        paths,
        diagnostic=True,
        capture_factory=FakeCapture,
        monitor_provider=lambda: [monitor],
    )
    worker.report.connect(reports.append)
    worker.run()
    assert reports and all(report.frame is not None for report in reports)
    assert not (paths.root / "debug").exists()


@pytest.mark.parametrize("kind", ["missing-template", "disconnected-monitor", "capture-failed"])
def test_worker_fails_with_understandable_message(app, paths, monitor, popup, kind):
    config = save_calibration(
        AppConfig(), monitor, Rect(0, 0, 420, 240), Rect(135, 146, 140, 42), popup, paths
    )
    if kind == "missing-template":
        (paths.root / config.template_path).unlink()

    def broken_capture():
        raise RuntimeError("Capture unavailable")

    worker = MonitorWorker(
        config,
        paths,
        capture_factory=broken_capture,
        monitor_provider=lambda: [] if kind == "disconnected-monitor" else [monitor],
    )
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert errors and len(errors) == 1
    assert any(word in errors[0].lower() for word in ("template", "disconnected", "capture"))


def test_windows_audio_is_short_async_and_optional(monkeypatch):
    calls = []
    fake = types.SimpleNamespace(
        SND_FILENAME=1,
        SND_ASYNC=2,
        SND_NODEFAULT=4,
        PlaySound=lambda path, flags: calls.append((path, flags)),
    )
    monkeypatch.setitem(sys.modules, "winsound", fake)
    monkeypatch.setattr("loki.alerts.sys.platform", "win32")
    audio = AudioAlert()
    audio.play(False)
    assert not calls
    audio.play(True)
    assert calls == [(str(resource_path("alert.wav")), 7)]
    with wave.open(str(resource_path("alert.wav"))) as sound:
        assert sound.getnchannels() == 1
        assert 0.2 < sound.getnframes() / sound.getframerate() < 0.5


def test_audio_failure_does_not_crash(monkeypatch):
    def fail(*_args):
        raise RuntimeError("No audio device")

    monkeypatch.setitem(
        sys.modules,
        "winsound",
        types.SimpleNamespace(PlaySound=fail, SND_FILENAME=1, SND_ASYNC=2, SND_NODEFAULT=4),
    )
    monkeypatch.setattr("loki.alerts.sys.platform", "win32")
    AudioAlert().play(True)
