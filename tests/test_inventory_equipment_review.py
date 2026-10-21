"""Whole inventory equipment retains one count and its Item category through review."""

import csv
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import item_ocr
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class InventoryEquipmentReviewTests(unittest.TestCase):
    """Exercise existing review controls with whole equipment and mixed hidden currency."""

    @classmethod
    def setUpClass(cls):
        """Reuse the Qt application used by inventory review widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated current map and retain OCR callbacks for captured evidence."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-equipment-review-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        self.image = Image.new("RGB", (648, 270), (26, 26, 40))
        self.image.info["poe2_inventory_aligned"] = True
        art = np.random.default_rng(34).integers(35, 220, (162, 108, 3), dtype=np.uint8)
        art[:54] = (210, 40, 30)
        art[54:] = (30, 60, 210)
        self.image.paste(Image.fromarray(art), (270, 54))

    def tearDown(self):
        """Close workers and restore the caller's saved profile."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def gear(self, name="", **changes):
        """Describe one captured two-column, three-row equipment footprint."""
        return {"slot": 18, "grid_slots": [18, 19, 30, 31, 42, 43],
                "box": (270, 54, 378, 216), "box_space": "inventory_grid",
                "category": "Item", "name": name, "quantity": 1,
                "count_needs_review": False, **changes}

    def read(self, items=(), unknown=()):
        """Deliver an End scan bound to its original image, map and phase."""
        self.window._inventory_captured(self.image, live=False, expected_phase="end")
        self.jobs[-1][1]({"items": list(items), "unknown": list(unknown)})

    def approve_row(self, row):
        """Approve a corrected name using the existing row action."""
        controls = self.window.inventory_table.cellWidget(row, 3)
        controls.findChild(QPushButton, "approveCurrency").click()
        self.assertEqual(controls.property("reviewStatus"), "approved")

    def test_unknown_equipment_shows_whole_artwork_and_only_needs_name(self):
        """One unknown equipment row preserves count one and full capture ownership."""
        original = self.image.tobytes(), dict(self.image.info)
        self.read(unknown=[self.gear(candidate="Possible armour")])
        table = self.window.inventory_table
        self.assertEqual(table.rowCount(), 1)
        self.assertFalse(table.isRowHidden(0))
        self.assertEqual(table.item(0, 2).text(), "1")
        status = table.cellWidget(0, 3).findChild(QLabel, "currencyReviewStatus").text()
        self.assertIn("Confirm name", status)
        self.assertNotIn("count", status)
        pixmap = table.item(0, 0).icon().pixmap(48, 48)
        self.assertLess(pixmap.width(), pixmap.height())
        artwork = pixmap.toImage()
        self.assertGreater(artwork.pixelColor(artwork.width() // 2, 3).red(), 150)
        self.assertGreater(artwork.pixelColor(artwork.width() // 2, artwork.height() - 4).blue(), 150)
        metadata = table.item(0, 1).data(Qt.ItemDataRole.UserRole)
        self.assertEqual(metadata["original"]["grid_slots"], self.gear()["grid_slots"])
        self.assertEqual(metadata["original"]["box"], self.gear()["box"])
        self.assertEqual((self.image.tobytes(), dict(self.image.info)), original)

    def test_approved_equipment_learns_whole_item_and_preserves_hidden_currency(self):
        """Name approval saves an Item example and bound End totals without extra count edits."""
        self.read(items=[{"slot": 1, "name": "Chaos Orb", "quantity": 7}],
                  unknown=[self.gear()])
        table = self.window.inventory_table
        self.assertTrue(table.isRowHidden(0))
        self.assertFalse(table.isRowHidden(1))
        table.item(1, 1).setText("Review Learned Armour")
        self.approve_row(1)
        self.window.approve_review()
        self.assertEqual(logger.currency_for_map("M0001")["end"],
                         {"Chaos Orb": 7, "Review Learned Armour": 1})
        self.assertIn("Review Learned Armour", logger.item_names())
        self.assertNotIn("Review Learned Armour", logger.currency_names())
        examples = logger.review_icons()
        self.assertEqual([(row["name"], row["kind"], row["columns"], row["rows"]) for row in examples],
                         [("Review Learned Armour", "item", 2, 3)])
        with Image.open(io.BytesIO(examples[0]["image"])) as learned:
            self.assertEqual(learned.size, (192, 288))
        rows = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        self.assertTrue(any(row.get("Item: Review Learned Armour") == "1" for row in rows))
        self.assertEqual(self.window._inventory_capture_context["map_id"], "M0001")
        self.assertEqual(self.window._inventory_capture_context["phase"], "end")

    def test_known_equipment_is_hidden_and_saves_without_individual_approval(self):
        """A known whole item is retained with currency while only unknown names need review."""
        logger.add_item_name("Known Armour")
        self.read(items=[{"slot": 1, "name": "Chaos Orb", "quantity": 3}, self.gear("Known Armour")])
        table = self.window.inventory_table
        self.assertEqual(table.rowCount(), 2)
        self.assertTrue(all(table.isRowHidden(row) for row in range(2)))
        self.window.approve_review()
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Chaos Orb": 3, "Known Armour": 1})

    def test_captured_whole_item_name_correction_is_clear_on_actual_rescan(self):
        """Approve one real staff footprint, then recognize and hide its saved whole artwork."""
        path = Path(__file__).parent / "fixtures/currency_training/inventory_122006.png"
        with Image.open(path) as source:
            self.image = item_ocr.inventory_grid(source.convert("RGB"))
        staff = item_ocr.inventory_item_footprints(self.image)[0]
        # Inject only the initial unresolved result; learning and the rescan
        # below use the original capture, actual matcher and saved references.
        self.read(unknown=[self.gear(slot=staff["slots"][0], grid_slots=staff["slots"], box=staff["box"])])
        self.window.inventory_table.item(0, 1).setText("Captured Staff")
        self.approve_row(0)
        self.window.approve_review()
        result = item_ocr.scan_inventory_grid(self.image, logger.inventory_icons())
        known = next(item for item in result["items"] if item["slot"] == staff["slots"][0])
        self.assertEqual((known["name"], known["category"], known["quantity"]), ("Captured Staff", "Item", 1))
        self.assertFalse(known["count_needs_review"])
        self.read(result["items"], result["unknown"])
        table = self.window.inventory_table
        row = next(row for row in range(table.rowCount())
                   if table.item(row, 0).data(Qt.ItemDataRole.UserRole) == staff["slots"][0])
        self.assertTrue(table.isRowHidden(row))
        self.assertEqual(table.cellWidget(row, 3).property("reviewStatus"), "approved")

    def test_snapshot_item_category_preserves_existing_catalog_rules(self):
        """New equipment registers as Item while existing currency keeps its canonical category."""
        logger.save_currency_snapshot("end", [
            {"name": "New Gear", "quantity": 1, "category": "Item"},
            {"name": "chaos orb", "quantity": 2, "category": "Item"}], register_names=True)
        self.assertIn("New Gear", logger.item_names())
        self.assertNotIn("New Gear", logger.currency_names())
        self.assertNotIn("Chaos Orb", logger.item_names())
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"New Gear": 1, "Chaos Orb": 2})


if __name__ == "__main__":
    unittest.main()
