"""Qt inventory-review checks attaching each row to its captured slot without borrowing unrelated image evidence."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QTableWidget

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import item_ocr
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class CurrencyReviewIconsTests(unittest.TestCase):
    """Exercise inventory-review icons, slot evidence, callback freshness and saved preview locking."""
    @classmethod
    def setUpClass(cls):
        """Reuse or create the QApplication required by inventory-review widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated desktop window and queue scan jobs for controlled callback delivery."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-currency-review-icons-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))

    def tearDown(self):
        """Close desktop workers, restore the data directory and remove temporary storage."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def grid(self, offset=0):
        """Paint a normalized inventory grid with a distinct color for every slot."""
        image = Image.new("RGB", (481, 203))
        image.info["poe2_inventory_aligned"] = True
        draw = ImageDraw.Draw(image)
        for slot in range(1, 61):
            column, row = (slot - 1) % 12, (slot - 1) // 12
            color = ((slot * 37 + offset) % 190 + 30,
                     (slot * 67 + offset) % 180 + 40,
                     (slot * 101 + offset) % 170 + 40)
            box = (round(column * image.width / 12), round(row * image.height / 5),
                   round((column + 1) * image.width / 12) - 1,
                   round((row + 1) * image.height / 5) - 1)
            draw.rectangle(box, fill=color)
        return image

    def capture(self, image):
        """Deliver a capture and return its queued recognition callback."""
        self.window._inventory_captured(image, live=False)
        return self.jobs[-1][1]

    def result(self, known=(1,), unknown=()):
        """Build known and unknown inventory result rows for selected slots."""
        return {"items": [{"slot": slot, "name": "Chaos Orb", "quantity": slot + 1}
                          for slot in known],
                "unknown": [{"slot": slot, "candidate": "Regal Orb"} for slot in unknown]}

    def assert_icon(self, row, slot, image):
        """Check that a noneditable icon cell represents the requested screenshot slot."""
        table = self.window.inventory_table
        cell = table.item(row, 0)
        self.assertEqual(cell.text(), "")
        self.assertEqual(cell.data(Qt.ItemDataRole.UserRole), slot)
        self.assertIn(str(slot), cell.toolTip())
        self.assertFalse(cell.icon().isNull())
        self.assertFalse(cell.flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertTrue(table.item(row, 1).icon().isNull())
        expected = item_ocr.inventory_cell(image, slot)
        actual = cell.icon().pixmap(48, 48).toImage()
        self.assertEqual(actual.pixelColor(actual.width() // 2, actual.height() // 2).getRgb()[:3],
                         expected.getpixel((expected.width // 2, expected.height // 2)))

    def test_known_and_unknown_rows_show_canonical_slot_crops_in_existing_icon_column_only(self):
        """Verify known and unknown rows crop their original slots in the existing icon column."""
        image = self.grid()
        before = image.tobytes(), dict(image.info)
        callback = self.capture(image)
        with patch.object(item_ocr, "inventory_cell", wraps=item_ocr.inventory_cell) as crop:
            callback(self.result(known=(1, 60), unknown=(14,)))
        self.assertEqual([(call.args[0] is image, call.args[1]) for call in crop.call_args_list],
                         [(True, 1), (True, 60), (True, 14)])
        table = self.window.inventory_table
        self.assertEqual(table.columnCount(), 4)
        self.assertEqual([table.horizontalHeaderItem(column).text() for column in range(4)],
                         ["Icon", "Currency / Item", "Count", "Review"])
        for row, slot in enumerate((1, 60, 14)):
            self.assert_icon(row, slot, image)
        self.assertEqual(table.item(2, 1).text(), "")
        self.assertEqual(table.item(2, 2).text(), "")
        self.assertEqual(table.cellWidget(2, 3).property("reviewStatus"), "pending")
        self.assertEqual((image.tobytes(), dict(image.info)), before)

    def test_rows_group_certainty_then_named_candidates_then_unknown_with_original_slot_evidence(self):
        """Verify certainty and candidate sorting retain each row's original slot evidence."""
        image = self.grid()
        result = {"items": [
            {"slot": 60, "name": "Chaos Orb", "quantity": 7, "score": .1},
            {"slot": 25, "name": "Exalted Orb", "quantity": 9,
             "score": .99, "count_needs_review": True},
            {"slot": 3, "name": "Regal Orb", "quantity": 2, "score": -900}],
            "unknown": [
                {"slot": 11, "candidate": "Transmutation Orb / Greater Transmutation Orb",
                 "score": .7, "reason": "shared icon; check tier", "margin": 410},
                {"slot": 2, "candidate": "Unrecognized item", "score": .98},
                {"slot": 4, "candidate": "Alchemy Orb", "score": .6,
                 "reason": "shared reference; check name"},
                {"slot": 1, "candidate": "", "score": .9}]}
        before = copy.deepcopy(result)
        self.capture(image)(result)

        table = self.window.inventory_table
        slots = [table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(table.rowCount())]
        self.assertEqual(slots, [3, 60, 4, 11, 25, 1, 2])
        self.assertEqual([table.cellWidget(row, 3).property("reviewStatus") for row in range(7)],
                         ["approved", "approved", "pending", "pending", "pending", "pending", "pending"])
        for row, slot in enumerate(slots):
            self.assert_icon(row, slot, image)

        originals = {item["slot"]: item for item in result["unknown"]}
        for row in (2, 3, 5, 6):
            self.assertEqual(table.item(row, 1).text(), "")
            self.assertEqual(table.item(row, 2).text(), "")
            metadata = table.item(row, 1).data(Qt.ItemDataRole.UserRole)
            self.assertTrue(metadata["has_capture"])
            for key, expected in originals[slots[row]].items():
                self.assertEqual(metadata["original"].get(key), expected)
        for row in (2, 3):
            status = table.cellWidget(row, 3).findChild(QLabel, "currencyReviewStatus")
            self.assertIn("Likely match", status.text())
            self.assertIn(originals[slots[row]]["candidate"], table.item(row, 1).toolTip())
        for row in (5, 6):
            status = table.cellWidget(row, 3).findChild(QLabel, "currencyReviewStatus")
            self.assertNotIn("Likely match", status.text())
        self.assertEqual(result, before)

    def test_manual_and_captureless_rows_keep_slot_text_without_reusing_old_screenshot(self):
        """Verify manual and captureless rows show slot text without borrowing prior screenshot icons."""
        self.window.add_inventory_row({"slot": 23, "name": "Chaos Orb", "quantity": 2})
        cell = self.window.inventory_table.item(0, 0)
        self.assertEqual(cell.text(), "23")
        self.assertTrue(cell.icon().isNull())
        self.assertEqual(cell.data(Qt.ItemDataRole.UserRole), 23)

        image = self.grid()
        self.capture(image)(self.result())
        self.assert_icon(0, 1, image)
        self.window._inventory_read(self.result(known=(14,)), live=False)
        cell = self.window.inventory_table.item(0, 0)
        self.assertEqual(cell.text(), "14")
        self.assertEqual(cell.data(Qt.ItemDataRole.UserRole), 14)
        self.assertTrue(cell.icon().isNull())
        self.assertTrue(self.window.inventory_table.item(0, 1).icon().isNull())

    def test_invalid_or_absent_manual_slots_do_not_crop_unrelated_cells(self):
        """Verify invalid manual slot identifiers produce no unrelated icon crop."""
        image = self.grid()
        for slot in (0, 61, "bad", ""):
            with self.subTest(slot=slot):
                self.window.add_inventory_row({"slot": slot, "name": "Chaos Orb", "quantity": 1}, image=image)
                cell = self.window.inventory_table.item(self.window.inventory_table.rowCount() - 1, 0)
                self.assertEqual(cell.text(), str(slot))
                self.assertTrue(cell.icon().isNull())

    def test_full_capture_uses_normalized_inventory_grid_for_icons_and_preserves_input(self):
        """Verify full captures normalize the grid for icons without modifying either source image."""
        grid = self.grid()
        raw = Image.new("RGB", (700, 400), "black")
        raw.paste(grid, (80, 60))
        before_raw, before_grid = raw.tobytes(), grid.tobytes()
        with patch.object(item_ocr, "inventory_grid", return_value=grid) as normalize:
            callback = self.capture(raw)
            normalize.assert_called_once_with(raw)
            callback(self.result(known=(1, 60), unknown=(14,)))
        self.assertIs(self.window._inventory_capture, grid)
        for row, slot in enumerate((1, 60, 14)):
            self.assert_icon(row, slot, grid)
        self.assertEqual(raw.tobytes(), before_raw)
        self.assertEqual(grid.tobytes(), before_grid)

    def test_stale_callbacks_cannot_restore_old_row_icons_before_or_after_latest_capture(self):
        """Verify outdated callbacks cannot replace icons from the latest inventory capture."""
        older_image, newer_image = self.grid(), self.grid(offset=51)
        older = self.capture(older_image)
        newer = self.capture(newer_image)
        older(self.result(known=(1, 60)))
        self.assertEqual(self.window.inventory_table.rowCount(), 0)
        newer(self.result(known=(14,)))
        self.assert_icon(0, 14, newer_image)
        before = self.window.inventory_table.item(0, 0).icon().cacheKey()
        older(self.result(known=(1, 60)))
        self.assertEqual(self.window.inventory_table.rowCount(), 1)
        self.assertEqual(self.window.inventory_table.item(0, 0).icon().cacheKey(), before)
        self.assertIs(self.window._inventory_capture, newer_image)
        self.assert_icon(0, 14, newer_image)

    def test_new_scan_replaces_previous_row_icons_slots_and_unknown_review_state(self):
        """Verify a new scan replaces prior icons, slots and unknown-row review status."""
        older_image, newer_image = self.grid(), self.grid(offset=83)
        self.capture(older_image)(self.result(known=(1,), unknown=(14,)))
        next_callback = self.capture(newer_image)
        self.assertEqual(self.window.inventory_table.rowCount(), 0)
        next_callback(self.result(known=(), unknown=(60,)))
        self.assertEqual(self.window.inventory_table.rowCount(), 1)
        self.assert_icon(0, 60, newer_image)
        self.assertEqual(self.window.inventory_table.cellWidget(0, 3).property("reviewStatus"), "pending")

    def test_saving_locks_review_and_retains_icon_preview_and_original_capture(self):
        """Verify saving locks review controls while retaining the original screenshot and icon preview."""
        image = self.grid()
        before_image = image.tobytes()
        self.capture(image)(self.result(known=(14,)))
        before_icon = self.window.inventory_table.item(0, 0).icon().cacheKey()
        self.window.approve_review()

        table = self.window.inventory_table
        self.assertEqual(table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        self.assertFalse(table.cellWidget(0, 3).isEnabled())
        self.assertFalse(self.window.inventory_add_row_button.isEnabled())
        self.window.inventory_add_row_button.click()
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).icon().cacheKey(), before_icon)
        self.assert_icon(0, 14, image)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 15})
        self.assertEqual(image.tobytes(), before_image)
        self.assertIsNone(self.window.pending_review_kind)
        self.window.refresh()
        self.assertEqual(table.item(0, 0).icon().cacheKey(), before_icon)


if __name__ == "__main__":
    unittest.main()
