import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2
import numpy as np
import pytest

from loki.config import AppPaths, Monitor, Rect


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication

    from loki.windows import enable_dpi_awareness

    enable_dpi_awareness()
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def paths(tmp_path):
    result = AppPaths(tmp_path / "Loki")
    result.ensure()
    return result


@pytest.fixture
def button():
    image = np.full((42, 140, 3), (35, 42, 46), dtype=np.uint8)
    cv2.rectangle(image, (1, 1), (138, 40), (145, 151, 162), 2)
    cv2.putText(
        image, "Run WHO", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.63, (242, 245, 235), 1, cv2.LINE_AA
    )
    return image


@pytest.fixture
def popup(button):
    rng = np.random.default_rng(48)
    image = rng.integers(16, 42, (240, 420, 3), dtype=np.uint8)
    cv2.rectangle(image, (20, 20), (400, 220), (113, 120, 136), 2)
    cv2.putText(
        image,
        "VoidLink WHO Request",
        (35, 55),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (220, 230, 240),
        1,
        cv2.LINE_AA,
    )
    image[146:188, 135:275] = button
    return image


@pytest.fixture
def monitor():
    return Monitor(2, "TEST-DISPLAY", Rect(-1920, 0, 1920, 1080), 1.25)
