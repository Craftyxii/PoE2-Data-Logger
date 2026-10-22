"""Keep verified scan rows saved while the existing lists show only uncertain rewards."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, clear_currency_read, clear_ritual_read


class UncertainOnlyReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-uncertain-only-review-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def inventory(self, items, unknown=(), *, auto=False):
        self.window.state["settings"]["ocr_auto_commit"] = auto
        result = {"items": items, "unknown": list(unknown)}
        self.window._inventory_read(result, live=True)
        return result

    def ritual(self, items, *, auto=False):
        self.window.state["settings"]["ocr_auto_commit"] = auto
        result = {"items": items, "raw_text": "Review certainty evidence", "unmatched": [],
                  "tribute_available": 5000, "rerolls_remaining": 1}
        self.window._ritual_read(result, live=True)
        return result

    def reward(self, **changes):
        return {"category": "Item", "name": "Chaos Orb", "quantity": 4,
                "tribute": None, "deferred": False, "score": 1,
                "source": "icon reference: Chaos Orb", **changes}

    def test_verified_currency_rows_are_hidden_and_automatic_snapshot_keeps_their_counts(self):
        result = self.inventory([{"slot": 1, "name": "Chaos Orb", "quantity": 7},
                                 {"slot": 6, "name": "Regal Orb", "quantity": 3}], auto=True)
        table = self.window.inventory_table
        self.assertTrue(clear_currency_read(result))
        self.assertEqual(table.rowCount(), 2)
        self.assertTrue(all(table.isRowHidden(row) for row in range(table.rowCount())))
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7, "Regal Orb": 3})

    def test_mixed_currency_review_retains_verified_rows_and_unknown_equipment_needs_only_name(self):
        self.inventory([{"slot": 1, "name": "Chaos Orb", "quantity": 7},
                        {"slot": 2, "name": "Regal Orb", "quantity": 3, "count_needs_review": True}],
                       [{"slot": 14, "category": "Item", "category_verified": True,
                         "quantity": 1, "count_needs_review": False, "name_needs_review": True,
                         "grid_slots": [14, 26], "grid_columns": 1, "grid_rows": 2}])
        table = self.window.inventory_table
        rows = {table.item(row, 0).data(Qt.ItemDataRole.UserRole): row for row in range(table.rowCount())}
        self.assertTrue(table.isRowHidden(rows[1]))
        self.assertEqual({slot for slot, row in rows.items() if not table.isRowHidden(row)}, {2, 14})
        gear = rows[14]
        controls = table.cellWidget(gear, 3)
        self.assertEqual(table.item(gear, 2).text(), "1")
        self.assertTrue(controls.property("reviewNameRequired"))
        self.assertFalse(controls.property("reviewCountRequired"))
        table.item(gear, 1).setText("Reviewed whole equipment")
        self.window.review_currency_row(controls, True)
        self.window.review_currency_row(table.cellWidget(rows[2], 3), True)
        self.window.approve_review()
        self.assertEqual(logger.currency_for_map("M0001")["start"],
                         {"Chaos Orb": 7, "Regal Orb": 3, "Reviewed whole equipment": 1})
        self.assertIn("Reviewed whole equipment", logger.item_names())
        self.assertNotIn("Reviewed whole equipment", logger.currency_names())

    def test_named_currency_with_unverified_identity_or_invalid_count_stays_visible(self):
        for change in ({"needs_review": True}, {"unresolved": True},
                       {"name_needs_review": True}, {"count_needs_review": True}, {"quantity": True}):
            with self.subTest(change=change):
                result = self.inventory([{"slot": 1, "name": "Chaos Orb", "quantity": 7, **change}])
                table = self.window.inventory_table
                self.assertFalse(clear_currency_read(result))
                self.assertFalse(table.isRowHidden(0))
                self.assertEqual(table.cellWidget(0, 3).property("reviewStatus"), "pending")
                self.window.reject_review()

    def test_verified_ritual_icon_reference_with_optional_tribute_saves_without_review_rows(self):
        result = self.ritual([self.reward()], auto=True)
        table = self.window.ritual_table
        self.assertTrue(clear_ritual_read(result, logger.ritual_names()))
        self.assertEqual(table.rowCount(), 1)
        self.assertTrue(table.isRowHidden(0))
        self.assertIsNone(self.window.pending_review_kind)
        saved = logger.ritual_pages_for_map("M0001")[0]["items"]
        self.assertEqual([(item["name"], item["quantity"], item["tribute"]) for item in saved],
                         [("Chaos Orb", 4, None)])

    def test_mixed_ritual_review_retains_hidden_rewards_and_asks_once_for_whole_equipment_name(self):
        self.ritual([self.reward(grid_slots=[1]),
                     self.reward(name="", quantity=1, score=0, source="unidentified whole artwork",
                                 category_verified=True, unresolved=True, name_needs_review=True,
                                 needs_review=True, grid_slots=[14, 26])])
        table = self.window.ritual_table
        self.assertEqual(table.rowCount(), 2)
        self.assertTrue(table.isRowHidden(0))
        self.assertFalse(table.isRowHidden(1))
        self.assertEqual([table.item(1, column).text() for column in (0, 1, 2)], ["Item", "", "1"])
        reason = table.item(1, 6).text().casefold()
        self.assertIn("name", reason)
        self.assertNotIn("type", reason)
        self.assertNotIn("quantity", reason)
        table.item(1, 1).setText("Reviewed Ritual equipment")
        controls = table.cellWidget(1, 6)
        controls.findChild(QPushButton, "approveRitual").click()
        self.assertEqual(controls.property("reviewStatus"), "approved")
        self.window.approve_review()
        saved = logger.ritual_pages_for_map("M0001")[0]["items"]
        self.assertEqual([(item["name"], item["quantity"]) for item in saved],
                         [("Chaos Orb", 4), ("Reviewed Ritual equipment", 1)])


if __name__ == "__main__":
    unittest.main()
