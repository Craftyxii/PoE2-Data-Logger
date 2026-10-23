"""Qt checks for the Map / Tablets Reset map button and its Undo."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ResetMapTests(unittest.TestCase):
    """Reset clears only the current map's logs after confirmation, and Undo restores them exactly."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication required by the Qt test fixtures."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Log one finished map and activity on the current map before opening the window."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-reset-map-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 4}])
        logger.finish_map(10, 2, 1)
        logger.start_map()
        logger.save_settings({"tier": 16, "waystone": 87})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 9}])
        logger.save_ritual_page([{"category": "Item", "name": "Chaos Orb", "quantity": 2}])
        logger.save_kills(20, 3, 2, unique=1)
        self.window = LoggerWindow()
        self.window._poll.stop()

    def tearDown(self):
        """Close the logger window, stop its workers and restore the original data directory."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def rows(self, map_id):
        """Read the saved logs one map owns across its activity tables."""
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} WHERE map_id=?", (map_id,))]
                    for table in ("maps", "expeditions", "currency_snapshots", "ritual_pages",
                                  "map_unique_kills", "new_export", "commits")}

    def click_reset(self, answer):
        """Press Reset map and answer its confirmation question."""
        with patch.object(QMessageBox, "question", return_value=answer) as question:
            self.window.reset_map_button.click()
        self.assertIn("M0002", question.call_args.args[2])

    def test_cancel_keeps_every_log(self):
        """Verify the question's Cancel leaves both maps exactly as saved."""
        before = (self.rows("M0001"), self.rows("M0002"))
        self.click_reset(QMessageBox.StandardButton.Cancel)
        self.assertEqual((self.rows("M0001"), self.rows("M0002")), before)

    def test_reset_clears_only_the_current_map_and_undo_restores_it(self):
        """Verify reset keeps the map ID and settings, leaves the earlier map, and Undo restores all logs."""
        earlier, current = self.rows("M0001"), self.rows("M0002")
        settings = logger.get_state()["settings"]
        self.click_reset(QMessageBox.StandardButton.Yes)
        state = logger.get_state()
        self.assertEqual(state["current_map_id"], "M0002")
        self.assertEqual(state["settings"], settings)
        self.assertEqual((state["kills"], state["unique_kills"]), ([None, None, None], None))
        self.assertEqual(self.rows("M0001"), earlier)
        after = self.rows("M0002")
        self.assertEqual(after["maps"][0][:2], current["maps"][0][:2])
        for table in ("currency_snapshots", "ritual_pages", "map_unique_kills", "expeditions"):
            self.assertEqual(after[table], [], table)
        self.assertEqual([row[1] for row in after["commits"]], ["Map logs discard"])
        self.assertEqual(self.window.state["current_map_id"], "M0002")
        self.window.undo_map()
        self.assertEqual((self.rows("M0001"), self.rows("M0002")), (earlier, current))


if __name__ == "__main__":
    unittest.main()
