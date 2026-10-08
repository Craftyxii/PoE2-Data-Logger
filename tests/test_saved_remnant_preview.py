"""Qt checks retaining a locked saved remnant preview through refresh until a new scan/manual review replaces it."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QTableWidget

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class SavedRemnantPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-saved-preview-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def reading(self, family=26, offset=1):
        with logger._connect() as db:
            names = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=?", (family,)).fetchone()[0])[offset:]
        resolved = logger.resolve(names[0], names[1] if len(names) > 1 else None, family)
        opened = {
            "mode": "opened", "status": "Review the opened rewards before logging.",
            "can_use": True, "family": f"Family {family}", "candidates": [family],
            "sockets": resolved["rows"][0]["sockets"],
            "recipe_sockets": resolved["rows"][0]["sockets"],
            "socket_source": "opened icons", "first_line_gap": 60, "list_complete": False,
            "first_recipe": names[0], "next_recipe": names[1] if len(names) > 1 else None,
            "opened_recipes": [{"recipe": name, "raw": name, "ocr_score": .99, "match_score": 1}
                               for name in names[:2]],
            **logger.scan_context(),
        }
        raw = io.BytesIO()
        Image.new("RGB", (600, 400), (120, 130, 140)).save(raw, format="PNG")
        self.window.show_result("opened", opened, raw.getvalue())
        return opened, resolved["rows"]

    def rows(self):
        return [[self.window.recipe_table.item(row, column).text() for column in range(3)]
                for row in range(self.window.recipe_table.rowCount())]

    def assert_saved_preview(self, expected):
        self.assertFalse(self.window.recipe_table.isHidden())
        self.assertFalse(self.window.remnant_log_group.isHidden())
        self.assertEqual(self.rows(), [[str(row[key]) for key in ("recipe", "sockets", "combo")]
                                      for row in expected])
        self.assertEqual(self.window.recipe_table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        self.assertEqual(self.window.recipe_table.property("savedRemnant"), "R0001")
        self.assertIn("M0001", self.window.recipe_status.text())
        self.assertIn("R0001", self.window.recipe_status.text())
        self.assertIn("Family 26", self.window.recipe_status.text())
        self.assertIn("Saved", self.window.recipe_status.text())
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(self.window.resolved)
        self.assertEqual(self.window.results, {"seed": None, "opened": None})
        self.assertEqual(self.window.images, {"seed": None, "opened": None})
        self.assertEqual(self.window.first_recipe.text(), "")
        self.assertEqual(self.window.next_recipe.text(), "")
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        with logger._connect() as db:
            self.assertIsNone(logger._meta(db, "ocr_pending"))
            stored = json.loads(db.execute("SELECT details_json FROM commits WHERE kind='Remnant'").fetchone()[0])
            exported = db.execute("SELECT count(*) FROM new_export WHERE remnant_id='R0001'").fetchone()[0]
        self.assertEqual(stored["recipes"], expected)
        self.assertEqual(exported, len(expected))
        before = logger.get_state()["scan_commit_count"]
        with self.assertRaisesRegex(ValueError, "no scan waiting"):
            self.window.approve_review()
        self.assertEqual(logger.get_state()["scan_commit_count"], before)

    def test_manual_save_retains_all_saved_recipes_without_pending_tokens(self):
        self.window.set_auto_commit(False)
        opened, expected = self.reading()
        self.window.approve_remnant_scan()
        self.assert_saved_preview(expected)

    def test_automatic_save_retains_all_inferred_recipes(self):
        self.window.set_auto_commit(True)
        opened, expected = self.reading()
        self.window.maybe_auto_commit(opened)
        self.assert_saved_preview(expected)

    def test_settings_refresh_preserves_saved_preview(self):
        self.window.set_auto_commit(False)
        opened, expected = self.reading()
        self.window.approve_remnant_scan()
        self.window.refresh()
        self.assert_saved_preview(expected)

    def test_next_scan_replaces_saved_preview_with_new_pending_recipe_list(self):
        self.window.set_auto_commit(False)
        self.reading()
        self.window.approve_remnant_scan()
        opened, expected = self.reading(family=55, offset=0)
        self.assertEqual(self.rows(), [[str(row[key]) for key in ("recipe", "sockets", "combo")]
                                      for row in expected])
        self.assertIsNone(self.window.recipe_table.property("savedRemnant"))
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_manual_entry_starts_with_no_saved_recipe_rows(self):
        self.window.set_auto_commit(False)
        self.reading()
        self.window.approve_remnant_scan()
        self.window.manual_remnant_button.click()
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.window.recipe_table.rowCount(), 0)
        self.assertTrue(self.window.recipe_table.isHidden())
        self.assertIsNone(self.window.recipe_table.property("savedRemnant"))
        self.assertEqual(self.window.recipe_family.currentData(), "")
        self.assertFalse(self.window.approve_scan_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
