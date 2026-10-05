"""Sound-only notification; Windows PlaySound is asynchronous."""

import logging
import sys
from pathlib import Path


def resource_path(name):
    return Path(__file__).resolve().parent / "resources" / name


class AudioAlert:
    def __init__(self):
        self.logger = logging.getLogger("loki")

    def play(self, enabled=True):
        if not enabled:
            return
        try:
            if sys.platform == "win32":
                import winsound

                winsound.PlaySound(
                    str(resource_path("alert.wav")),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
            else:
                # Developer fallback, not a substitute for a Windows speaker check.
                from PySide6.QtWidgets import QApplication

                QApplication.beep()
        except (RuntimeError, OSError) as exc:
            self.logger.warning("Audio notification failed: %s", exc)
