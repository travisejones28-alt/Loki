"""Bounded logs and opt-in, bounded trigger screenshots."""

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

import cv2

from .config import AppPaths, atomic_write
from .detector import DetectionResult


def setup_logging(paths: AppPaths):
    paths.ensure()
    logger = logging.getLogger("loki")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(
        paths.root / "logs" / "loki.log", maxBytes=512_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    return logger


def save_debug_image(paths: AppPaths, frame, result: DetectionResult, origin, maximum=20):
    folder = paths.root / "debug"
    folder.mkdir(parents=True, exist_ok=True)
    # Delete before adding a new image so there is never unbounded accumulation.
    files = sorted(folder.glob("detect-*.png"))
    for old in files[: max(0, len(files) - maximum + 1)]:
        old.unlink(missing_ok=True)
    annotated = frame.copy()
    x, y = result.x - origin[0], result.y - origin[1]
    cv2.rectangle(
        annotated, (x, y), (x + result.width - 1, y + result.height - 1), (120, 255, 60), 2
    )
    cv2.putText(
        annotated,
        f"Target {result.confidence:.3f}",
        (max(0, x), max(18, y - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (120, 255, 60),
        1,
        cv2.LINE_AA,
    )
    ok, encoded = cv2.imencode(".png", annotated)
    if not ok:
        raise OSError("Debug screenshot could not be encoded.")
    path = folder / ("detect-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".png")
    atomic_write(path, encoded.tobytes())
    return path
