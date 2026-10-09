"""Large inventory labels must not approve a cropped prefix as a full count."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import currency_ocr, inventory_labels, item_ocr
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, clear_currency_read


class InventoryCountClippingTests(unittest.TestCase):
    """Exercise large count glyphs and ambiguous clusters that must remain under manual review."""
    @classmethod
    def setUpClass(cls):
        """Reuse or create the QApplication required by the inventory-review check."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated database and load bundled captured digit templates."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-count-clipping-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.glyphs = json.loads((currency_ocr.ROOT / "digit-templates.json").read_text())["templates"]

    def tearDown(self):
        """Restore the logger data directory and remove the temporary database."""
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def cell(self, number, scale=1, *, taller_tail=False):
        """Reconstruct counts from bundled captured glyphs, not a new screenshot."""
        cell = Image.new("RGB", (54, 54), (26, 26, 40))
        x = 3
        baseline = 18 if taller_tail else 15
        for index, digit in enumerate(number):
            glyph = self.glyphs[digit]
            pixels = np.asarray(glyph["data"], dtype=np.uint8).reshape(glyph["h"], glyph["w"])
            image = Image.fromarray(pixels * 229 + 26).convert("RGB")
            if taller_tail and index:
                image = image.resize((image.width, 15), Image.Resampling.NEAREST)
            cell.paste(image, (x, baseline - image.height))
            x += image.width + 2
        size = round(cell.width * scale)
        return cell.resize((size, size), Image.Resampling.LANCZOS)

    def grid(self, cell):
        """Place the supplied count cell in the first slot of an aligned inventory grid."""
        grid = Image.new("RGB", (cell.width * 12, cell.height * 5), (26, 26, 40))
        grid.paste(cell, (0, 0))
        grid.info["poe2_inventory_aligned"] = True
        return grid

    def test_real_glyph_count_30_stays_complete_at_large_cell_sizes(self):
        """Verify all digits of the captured count 30 remain readable at enlarged cell sizes."""
        for scale in (1, 2, 2.5):
            with self.subTest(scale=scale):
                label = item_ocr._inventory_labels(self.grid(self.cell("30", scale)))[1]
                self.assertEqual(label.get("count"), 30)

    def test_real_glyph_count_783_remains_readable_at_large_cell_sizes(self):
        """Verify all digits of the captured count 783 remain readable at enlarged cell sizes."""
        for scale in (1, 2, 2.5):
            with self.subTest(scale=scale):
                label = item_ocr._inventory_labels(self.grid(self.cell("783", scale)))[1]
                self.assertEqual(label.get("count"), 783)

    def test_splinter_count_292_is_complete_at_inventory_capture_scales(self):
        """Exercise the reported three-digit count using captured glyphs at normal and 4K scales."""
        for scale in (1, 1.25, 2, 2.5):
            with self.subTest(scale=scale):
                label = item_ocr._inventory_labels(self.grid(self.cell("292", scale)))[1]
                self.assertEqual(label.get("count"), 292)

    def test_missed_count_presence_cannot_auto_approve_quantity_one(self):
        """Hold a recognised stack when geometry missed its count but the glyph reader sees 292."""
        labels = {slot: {"count_present": False, "count": 1, "tier_present": False}
                  for slot in range(1, 61)}
        reader = SimpleNamespace(icon=lambda cell, **kwargs: {
            "family": "splinter", "members": ["Simulacrum Splinter"], "score": .99},
            count=lambda cell: 292)
        with patch.object(currency_ocr, "get_reader", return_value=reader), \
                patch.object(item_ocr, "_inventory_labels", return_value=labels):
            result = item_ocr.scan_inventory_grid(self.grid(self.cell("292", 2)))
        self.assertEqual(result["items"][0]["quantity"], 292)
        self.assertTrue(result["items"][0]["count_needs_review"])
        self.assertFalse(clear_currency_read(result))

    def test_incomplete_cluster_cannot_approve_first_digit_or_auto_save(self):
        # Deliberately misalign the trailing captured glyphs to exercise a
        # cluster the geometry cannot verify. Only icon identity is stubbed;
        # count cropping and RapidOCR run on the reconstructed pixels.
        """Verify incomplete count geometry holds the item for review and blocks automatic saving."""
        cell = self.cell("783", 2.5, taller_tail=True)
        reader = SimpleNamespace(icon=lambda cell, **kwargs: {
            "family": "scroll", "members": ["Scroll of Wisdom"], "score": .99},
            count=lambda cell: None)
        with patch.object(currency_ocr, "get_reader", return_value=reader):
            result = item_ocr.scan_inventory_grid(self.grid(cell))
        stack = next(row for row in result["items"] if row["slot"] == 1)
        self.assertTrue(stack["count_needs_review"])
        self.assertFalse(clear_currency_read(result))

    def test_disagreeing_count_readers_require_review_even_with_confident_ocr(self):
        """Verify disagreement between native and OCR counts requires review despite a confident icon."""
        for number, native in (("30", 3), ("783", 7)):
            with self.subTest(number=number):
                reader = SimpleNamespace(icon=lambda cell, **kwargs: {
                    "family": "scroll", "members": ["Scroll of Wisdom"], "score": .99},
                    count=lambda cell, native=native: native)
                with patch.object(currency_ocr, "get_reader", return_value=reader):
                    result = item_ocr.scan_inventory_grid(self.grid(self.cell(number, 2.5)))
                stack = next(row for row in result["items"] if row["slot"] == 1)
                self.assertEqual(stack["quantity"], int(number))
                self.assertTrue(stack["count_needs_review"])
                self.assertFalse(clear_currency_read(result))

    def test_incomplete_ritual_count_cluster_never_becomes_verified(self):
        """Verify incomplete ritual count clusters remain present but unverified."""
        labels = item_ocr._ritual_cell_labels({0: self.cell("783", 2.5, taller_tail=True)})
        self.assertTrue(labels[0]["count_present"])
        self.assertFalse(labels[0]["count_verified"])

    def test_conflicting_count_stays_pending_in_gui_and_cannot_auto_commit(self):
        """Verify a conflicting count remains editable in GUI review without saving a snapshot."""
        logger.save_settings({"ocr_auto_commit": True})
        reader = SimpleNamespace(icon=lambda cell, **kwargs: {
            "family": "scroll", "members": ["Scroll of Wisdom"], "score": .99},
            count=lambda cell: 3)
        with patch.object(currency_ocr, "get_reader", return_value=reader):
            result = item_ocr.scan_inventory_grid(self.grid(self.cell("30", 2.5)))
        window = LoggerWindow()
        window._poll.stop()
        try:
            before = logger.get_state()["scan_commit_count"]
            window._inventory_read(result, live=True)
            self.assertEqual(window.pending_review_kind, "currency")
            self.assertEqual(window.inventory_table.cellWidget(0, 3).property("reviewStatus"), "pending")
            self.assertEqual(window.inventory_table.item(0, 2).text(), "30")
            self.assertEqual(logger.get_state()["scan_commit_count"], before)
            with logger._connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM currency_snapshots").fetchone()[0], 0)
        finally:
            window.close()
            window.pool.shutdown(wait=True, cancel_futures=True)
            self.app.processEvents()

    def test_two_and_three_stroke_tier_badges_survive_large_cell_sizes(self):
        """Verify scaled two- and three-stroke tier badges remain detected and yield both label crops."""
        for strokes in (2, 3):
            cell = Image.new("RGB", (54, 54), (26, 26, 40))
            for index in range(strokes):
                left = 40 - (strokes - 2) * 6 + index * 6
                cell.paste((240, 240, 240), (left, 41, left + 2, 51))
            for scale in (1, 1.25, 1.5, 2):
                with self.subTest(strokes=strokes, scale=scale):
                    shown = cell.resize((round(54 * scale), round(54 * scale)), Image.Resampling.NEAREST)
                    crops, present = inventory_labels.tier_crops(shown)
                    self.assertTrue(present)
                    self.assertEqual(len(crops), 2)


if __name__ == "__main__":
    unittest.main()
