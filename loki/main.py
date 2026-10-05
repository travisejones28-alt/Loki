"""Application startup with DPI awareness, a single-instance lock, and a GUI smoke mode."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .windows import enable_dpi_awareness


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Loki · visual desktop assistant · passive startup"
    )
    parser.add_argument(
        "--tray", action="store_true", help="Start minimized if a system tray is available"
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Begin passive Monitor Mode with an existing calibration",
    )
    parser.add_argument("--data-dir", type=Path, help="Use a separate configuration folder")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Open and close the GUI without capture (build check)",
    )
    parser.add_argument("--version", action="version", version=f"Loki {__version__}")
    args = parser.parse_args(argv)
    enable_dpi_awareness()

    from PySide6.QtCore import QLockFile, Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .config import AppPaths
    from .logging_utils import setup_logging
    from .profiles import load_workspace
    from .ui import STYLE, MainWindow

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Loki")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Loki")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    paths = AppPaths(args.data_dir.resolve()) if args.data_dir else AppPaths.default()
    try:
        paths.ensure()
        lock = QLockFile(str(paths.root / "loki.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            QMessageBox.information(
                None,
                "Loki is already open",
                "Loki is already running. Open it using the L icon in the Windows tray.",
            )
            return 0
        logger = setup_logging(paths)
        logger.info("Application startup version=%s platform=%s", __version__, sys.platform)
        config, warning = load_workspace(paths)
        window = MainWindow(config, paths)
        if not args.tray or not window.tray_available or warning or args.smoke_test:
            window.show()
        if warning and not args.smoke_test:
            QTimer.singleShot(0, lambda: window.show_error(warning))
        if args.smoke_test:
            from .modes import MonitorModeController

            assert (
                isinstance(window.controller, MonitorModeController) and not window.controller.armed
            )
            # Packaging check for the lazy metadata module, never for OS input generation.
            if sys.platform == "win32":
                from .desktop import WindowsDesktop

                desktop = WindowsDesktop()
                desktop.close()
            logger.info("SMOKE passive startup verified; no automated input")
            QTimer.singleShot(300, window.exit_app)
        elif args.monitor or config.monitor_on_launch:
            QTimer.singleShot(100, window.start_monitoring)
        result = app.exec()
        lock.unlock()
        return result
    except Exception as exc:
        logging.getLogger("loki").exception("Application startup failed")
        QMessageBox.critical(None, "Loki could not start", str(exc))
        return 1
