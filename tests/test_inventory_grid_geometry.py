"""Real inventory divider lattices retain each complete count field after alignment."""

from pathlib import Path
import unittest

import numpy as np
from PIL import Image

from PoE2_Data_Logger.ocr import item_ocr


FIXTURES = Path(__file__).parent / "fixtures" / "currency_training"


class InventoryGridGeometryTests(unittest.TestCase):
    """Check observed 54-pixel cells, narrow clipped borders and normalization ownership."""

    def source(self, stamp):
        """Load a user-supplied inventory capture at its original pixel scale."""
        with Image.open(FIXTURES / f"inventory_{stamp}.png") as image:
            return image.convert("RGB")

    def test_real_captures_preserve_the_grid_pitch_and_every_original_count_field(self):
        """Alignment must retain native count pixels rather than squeezing the reward rows."""
        # Divider centres were measured directly in the original captures.
        origins = {"115801": (10, 16), "120205": (1, 8), "120330": (13, 12),
                   "121810": (18, 12)}
        for stamp, (left, top) in origins.items():
            with self.subTest(stamp=stamp):
                source = self.source(stamp)
                original = source.tobytes(), dict(source.info)
                aligned = item_ocr.inventory_grid(source)
                self.assertEqual(aligned.size, (648, 270))
                self.assertTrue(aligned.info.get("poe2_inventory_aligned"))
                self.assertFalse(aligned.info.get("poe2_inventory_clipped_count_slots"))
                for slot in range(1, 61):
                    column, row = (slot - 1) % 12, (slot - 1) // 12
                    # Inspect the whole upper count field, independently of OCR.
                    expected = source.crop((left + column * 54, top + row * 54,
                                            left + column * 54 + 46, top + row * 54 + 19))
                    actual = item_ocr.inventory_cell(aligned, slot).crop((0, 0, 46, 19))
                    self.assertTrue(np.array_equal(np.asarray(actual), np.asarray(expected)), slot)
                self.assertEqual((source.tobytes(), dict(source.info)), original)

    def test_missing_bottom_border_is_padded_without_shifting_the_last_row_counts(self):
        """The tight 120205 capture lacks four bottom pixels but includes every count field."""
        aligned = item_ocr.inventory_grid(self.source("120205"))
        self.assertEqual(aligned.size, (648, 270))
        self.assertFalse(np.asarray(aligned)[-4:].any())
        self.assertFalse(aligned.info.get("poe2_inventory_clipped_count_slots"))
        self.assertIs(item_ocr.inventory_grid(aligned), aligned)

    def test_missing_top_and_left_count_pixels_are_retained_as_uncertainty_metadata(self):
        """Padding must never turn a truncated first-row/column count into an absent marker."""
        source = self.source("120330").crop((15, 14, 664, 282))
        aligned = item_ocr.inventory_grid(source)
        self.assertEqual(aligned.size, (648, 270))
        expected = set(range(1, 13)) | {1, 13, 25, 37, 49}
        self.assertEqual(set(aligned.info.get("poe2_inventory_clipped_count_slots", [])), expected)
        self.assertIs(item_ocr.inventory_grid(aligned), aligned)

    def test_failed_lattice_retains_pixels_without_claiming_count_geometry_is_known(self):
        """A tighter partial capture cannot silently fall back to verified equal-size cells."""
        source = self.source("120330").crop((15, 15, 664, 282))
        original = source.tobytes(), dict(source.info)
        unaligned = item_ocr.inventory_grid(source)
        self.assertIsNot(unaligned, source)
        self.assertEqual(unaligned.size, source.size)
        self.assertEqual(unaligned.tobytes(), source.tobytes())
        self.assertTrue(unaligned.info.get("poe2_inventory_geometry_uncertain"))
        self.assertFalse(unaligned.info.get("poe2_inventory_aligned"))
        self.assertEqual((source.tobytes(), dict(source.info)), original)


if __name__ == "__main__":
    unittest.main()
