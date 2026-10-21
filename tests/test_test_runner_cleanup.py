"""Closed Qt fixtures must release their widgets without deleting a live logger window."""

from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow
from tools.run_tests import release_closed_test_windows


class TestRunnerCleanupTests(unittest.TestCase):
    """Check fixture cleanup against real desktop widgets and a temporary saved profile."""

    @classmethod
    def setUpClass(cls):
        """Create or reuse the Qt application needed by the real widget lifecycle."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open one logger window using isolated data."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-runner-cleanup-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        self.window = LoggerWindow()
        self.window._poll.stop()

    def tearDown(self):
        """Release any remaining window, restore storage and remove temporary files."""
        if isValid(self.window):
            self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        release_closed_test_windows()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def test_closed_window_and_children_are_destroyed_after_cleanup(self):
        """A closed window's signal cycles must not retain its native widgets between tests."""
        table = self.window.inventory_table
        self.window.close()
        self.assertTrue(isValid(self.window))
        release_closed_test_windows()
        self.assertFalse(isValid(self.window))
        self.assertFalse(isValid(table))

    def test_active_window_is_preserved_by_fixture_cleanup(self):
        """Cleaning earlier test fixtures must not delete a live window."""
        release_closed_test_windows()
        self.assertTrue(isValid(self.window))
        self.assertTrue(isValid(self.window.inventory_table))


if __name__ == "__main__":
    unittest.main()
