"""Test-runner cleanup releases terminal loggers while preserving active and unrelated Qt windows."""

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from shiboken6 import isValid

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow
from tools.run_tests import LoggerTestResult, dispose_closed_logger_windows


class RunnerQtCleanupTests(unittest.TestCase):
    """Check actual Qt ownership at unittest's post-teardown cleanup boundary."""

    @classmethod
    def setUpClass(cls):
        """Share the QApplication used by the surrounding Qt suite."""
        cls.app = QApplication.instance() or QApplication([])

    def test_cleanup_runs_after_teardown_and_preserves_hidden_logger_and_dialog(self):
        """Destroy a closed logger after teardown while retaining a hidden active logger, dialog and application."""
        previous = store.DATA_DIR
        active = closed = dialog = None
        with tempfile.TemporaryDirectory(prefix="poe2-runner-cleanup-") as folder:
            try:
                store.DATA_DIR = Path(folder)
                logger._READY = False
                logger.initialize()
                logger.clear_export_and_reset_ids()
                active, closed = LoggerWindow(), LoggerWindow()
                active._poll.stop()
                closed._poll.stop()
                dialog = QDialog()
                self.assertFalse(active.isVisible())
                self.assertFalse(active._closed)
                closed_widgets = len(closed.findChildren(QWidget))
                outer = self

                class ClosingFixture(unittest.TestCase):
                    """Retain a valid logger until the ordinary tearDown phase has finished."""

                    def runTest(self):
                        """Confirm the fixture owns a live logger before teardown."""
                        outer.assertTrue(isValid(closed))

                    def tearDown(self):
                        """Close workers and inspect controls before the result hook destroys Qt objects."""
                        closed.close()
                        closed.pool.shutdown(wait=True, cancel_futures=True)
                        outer.assertTrue(isValid(closed))
                        outer.assertIsNotNone(closed.tabs)

                before = len(self.app.allWidgets())
                result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=LoggerTestResult).run(ClosingFixture())
                self.assertTrue(result.wasSuccessful(), result.errors)
                self.assertFalse(isValid(closed))
                self.assertGreater(before - len(self.app.allWidgets()), closed_widgets)
                self.assertTrue(isValid(active))
                self.assertTrue(isValid(dialog))
                self.assertIs(QApplication.instance(), self.app)
            finally:
                for window in (active, closed):
                    if window is not None and isValid(window):
                        window.close()
                        window.pool.shutdown(wait=True, cancel_futures=True)
                        window.deleteLater()
                if dialog is not None and isValid(dialog):
                    dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                store.DATA_DIR = previous
                logger._READY = False

    def test_backend_only_cleanup_does_not_import_desktop(self):
        """Return without loading the desktop module when a backend-only runner has not imported it."""
        with patch.dict(sys.modules, {"PoE2_Data_Logger.ui.native_desktop": None}):
            with patch("builtins.__import__", side_effect=AssertionError("Cleanup imported a module")):
                dispose_closed_logger_windows()


if __name__ == "__main__":
    unittest.main()
