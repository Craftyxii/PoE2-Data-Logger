"""Discover regression tests in an isolated profile with Qt offscreen by default.

The CLI discovers tests; assertion or fixture-cleanup failures set its exit code.
Injected scan-result simulations and mocked Windows tests do not exercise native
game-overlay input or prove capture recognition."""

import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest


def dispose_closed_logger_windows():
    """Destroy terminal test logger trees after teardown, preserving active windows and backend-only imports."""
    desktop = sys.modules.get("PoE2_Data_Logger.ui.native_desktop")
    widgets = sys.modules.get("PySide6.QtWidgets")
    core = sys.modules.get("PySide6.QtCore")
    if desktop is None or widgets is None or core is None:
        return
    app = widgets.QApplication.instance()
    if app is None:
        return
    closed = [window for window in app.topLevelWidgets()
              if isinstance(window, desktop.LoggerWindow) and getattr(window, "_closed", False) is True]
    for window in closed:
        # Most fixtures already join workers. Complete any remaining callbacks
        # before deleting their Qt signal receivers; closeEvent has retired them.
        window.pool.shutdown(wait=True, cancel_futures=True)
        window.deleteLater()
    if closed:
        core.QCoreApplication.sendPostedEvents(None, core.QEvent.Type.DeferredDelete)


class LoggerTestResult(unittest.TextTestResult):
    """Release closed logger fixtures after each test's teardown and report cleanup errors as test failures."""

    def stopTest(self, test):
        """Dispose only closed logger windows after fixture cleanup, then finish normal unittest accounting."""
        try:
            dispose_closed_logger_windows()
        except Exception:
            self.addError(test, sys.exc_info())
        finally:
            super().stopTest(test)


def main(argv=None):
    """Run discovery in an isolated profile with per-test terminal-window cleanup and CI error annotations."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Smoke readers consult OCR settings before an individual test fixture starts.
    with tempfile.TemporaryDirectory(prefix="poe2-regression-data-", ignore_cleanup_errors=True) as test_data:
        os.environ.setdefault("RUNESHAPE_SCAN_DATA_DIR", test_data)
        parser = argparse.ArgumentParser(description="Run isolated regression checks.")
        parser.add_argument("--pattern", default="test*.py", help="Test module filename pattern")
        args = parser.parse_args(argv)
        suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=2, resultclass=LoggerTestResult).run(suite)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            for test, trace in result.failures + result.errors:
                trace = trace.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print(f"::error title={test.id()}::{trace}")
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
