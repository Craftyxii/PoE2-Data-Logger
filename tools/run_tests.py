"""Discover regression tests in an isolated profile with Qt offscreen by default.

The command runs discovery in an isolated profile; assertion failures set its exit code.
Injected scan-result simulations and mocked Windows tests do not exercise native
game-overlay input or prove capture recognition."""

import argparse
import gc
import os
from pathlib import Path
import sys
import tempfile
import unittest


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Smoke readers consult OCR settings before an individual test fixture starts.
# Keep that initialization away from the user's saved application profile.
test_data = tempfile.TemporaryDirectory(prefix="poe2-regression-data-", ignore_cleanup_errors=True)
os.environ.setdefault("RUNESHAPE_SCAN_DATA_DIR", test_data.name)

def release_closed_test_windows():
    """Destroy closed Qt test windows so widgets and signal cycles cannot accumulate."""
    desktop = sys.modules.get("PoE2_Data_Logger.ui.native_desktop")
    widgets = sys.modules.get("PySide6.QtWidgets")
    core = sys.modules.get("PySide6.QtCore")
    if desktop is None or widgets is None or core is None:
        return
    app = widgets.QApplication.instance()
    if app is None:
        return
    for window in app.topLevelWidgets():
        if isinstance(window, desktop.LoggerWindow) and getattr(window, "_closed", False):
            window.pool.shutdown(wait=True, cancel_futures=True)
            window.deleteLater()
    core.QCoreApplication.sendPostedEvents(None, core.QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()


class CleanupTestResult(unittest.TextTestResult):
    """Release closed desktop fixtures after tearDown while preserving live test windows."""

    def stopTest(self, test):
        """Flush deferred Qt destruction before the next regression case starts."""
        try:
            release_closed_test_windows()
        finally:
            super().stopTest(test)


def main():
    """Discover requested cases and report failures with bounded desktop fixture memory."""
    parser = argparse.ArgumentParser(description="Run isolated regression checks.")
    parser.add_argument("--pattern", default="test*.py", help="Test module filename pattern")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=2, resultclass=CleanupTestResult).run(suite)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for test, trace in result.failures + result.errors:
            trace = trace.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title={test.id()}::{trace}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
