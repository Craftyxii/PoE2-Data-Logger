"""Real inventory digits must verify complete counts without trusting native prefixes."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.ocr import currency_ocr, inventory_labels, item_ocr


class CurrencyCountCoverageTests(unittest.TestCase):
    """Keep full label coverage separate from the native reader's glyph confidence."""

    def cell(self, slot):
        """Extract an actual labelled cell from the supplied October 21 inventory."""
        path = Path(__file__).parent / "fixtures/currency_training/inventory_120330.png"
        with Image.open(path) as source:
            grid = item_ocr.inventory_grid(source.convert("RGB"))
        return item_ocr.inventory_cell(grid, slot)

    def grid(self, cell):
        """Place one real captured stack in an otherwise empty aligned inventory."""
        grid = Image.new("RGB", (cell.width * 12, cell.height * 5), (26, 26, 40))
        grid.paste(cell, (0, 0))
        grid.info["poe2_inventory_aligned"] = True
        return grid

    def scan(self, cell, name, *, native=None, clipped=False, uncertain_grid=False, read=None):
        """Use real count crops/OCR, replacing only icon identity and optional native output."""
        reader = currency_ocr.get_reader()
        grid = self.grid(cell)
        if clipped:
            grid.info["poe2_inventory_clipped_count_slots"] = [1]
        if uncertain_grid:
            grid.info["poe2_inventory_geometry_uncertain"] = True
        icon = {"family": "known", "members": [name], "score": .99}
        with patch.object(reader, "icon", return_value=icon):
            if native is None:
                result = item_ocr.scan_inventory_grid(grid, read=read)
            else:
                with patch.object(reader, "read_count", return_value=json.dumps(native)):
                    result = item_ocr.scan_inventory_grid(grid, read=read)
        return next(item for item in result["items"] if item["slot"] == 1)

    def test_captured_tens_and_thirties_verify_without_native_prefix_holds(self):
        """A native match for 1 or 3 cannot contradict a complete captured 10 or 30."""
        for slot, name, count in ((7, "Chilling Flux", 10), (38, "Greater Orb of Augmentation", 30)):
            with self.subTest(count=count):
                cell = self.cell(slot)
                self.assertEqual(inventory_labels.complete_count_glyphs(cell), 2)
                self.assertIn(currency_ocr.get_reader().count(cell), (None, count))
                item = self.scan(cell, name)
                self.assertEqual(item["quantity"], count)
                self.assertFalse(item["count_needs_review"])

    def test_same_length_conflicting_native_count_still_requires_review(self):
        """Coverage alone cannot resolve an actual two-digit value disagreement."""
        item = self.scan(self.cell(7), "Chilling Flux", native={"text": "11", "conf": .99})
        self.assertEqual(item["quantity"], 10)
        self.assertTrue(item["count_needs_review"])

    def test_count_obscured_by_bright_art_cannot_default_to_one(self):
        """A real 3 beside bright essence artwork retains its uncertain marker evidence."""
        path = Path(__file__).parent / "fixtures/currency_training/inventory_121810.png"
        with Image.open(path) as source:
            cell = item_ocr.inventory_cell(item_ocr.inventory_grid(source.convert("RGB")), 6)
        crops, present = inventory_labels.count_crops(cell, complete_only=True)
        self.assertTrue(present)
        self.assertEqual(crops, [])
        item = self.scan(cell, "Greater Essence of Grounding")
        self.assertTrue(item["count_needs_review"])

    def test_clipped_capture_count_cannot_be_verified_from_its_visible_prefix(self):
        """Capture clipping metadata must remain a hold even when visible OCR agrees."""
        item = self.scan(self.cell(7), "Chilling Flux", clipped=True)
        self.assertEqual(item["quantity"], 10)
        self.assertTrue(item["count_needs_review"])

    def test_clipped_empty_corner_cannot_default_to_an_unmarked_single_item(self):
        """A missing count corner is not evidence that its quantity marker was absent."""
        grid = self.grid(Image.new("RGB", (54, 54), (26, 26, 40)))
        grid.info["poe2_inventory_clipped_count_slots"] = [1]
        label = item_ocr._inventory_labels(grid)[1]
        self.assertTrue(label["count_present"])
        self.assertTrue(label["count_clipped"])
        self.assertNotIn("count", label)

    def test_unverified_grid_geometry_cannot_approve_an_otherwise_clear_count(self):
        """Agreeing OCR cannot establish a full count when cell boundaries are unverified."""
        item = self.scan(self.cell(7), "Chilling Flux", uncertain_grid=True)
        self.assertEqual(item["quantity"], 10)
        self.assertTrue(item["count_needs_review"])

    def test_conflicting_complete_glyph_geometry_cannot_discard_a_native_read(self):
        """Different full group lengths cannot prove that the native output is a prefix."""
        with patch.object(inventory_labels, "_count_groups", return_value=([(1, ()), (2, ())], True)):
            self.assertIsNone(inventory_labels.complete_count_glyphs(Image.new("RGB", (54, 54))))

    def test_custom_text_reader_cannot_bypass_uncertain_or_clipped_count_geometry(self):
        """A native count remains held when a caller supplies its own OCR callback."""
        for flags in ({"uncertain_grid": True}, {"clipped": True}):
            with self.subTest(flags=flags):
                item = self.scan(self.cell(7), "Chilling Flux", read=lambda image: [],
                                 native={"text": "10", "conf": .99}, **flags)
                self.assertEqual(item["quantity"], 10)
                self.assertTrue(item["count_needs_review"])


if __name__ == "__main__":
    unittest.main()
