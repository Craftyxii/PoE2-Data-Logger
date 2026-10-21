"""Whole gear artwork must retain one rectangular inventory footprint per item."""

from pathlib import Path
import unittest

from PIL import Image

from PoE2_Data_Logger.ocr import item_ocr


FIXTURES = Path(__file__).parent / "fixtures" / "currency_training"


class InventoryItemFootprintTests(unittest.TestCase):
    """Check real shields, narrow weapon handles and separate adjacent single-cell items."""

    def grid(self, stamp):
        """Normalize one provided inventory without substituting its divider geometry."""
        with Image.open(FIXTURES / f"inventory_{stamp}.png") as image:
            return item_ocr.inventory_grid(image.convert("RGB"))

    def test_real_red_shield_is_one_complete_two_by_four_item(self):
        """Textured shield artwork crosses eight cells and must not fragment into currency slots."""
        expected = [{"slots": [11, 12, 23, 24, 35, 36, 47, 48], "box": (540, 0, 648, 216)}]
        for stamp in ("115801", "120205"):
            with self.subTest(capture=stamp):
                self.assertEqual(item_ocr.inventory_item_footprints(self.grid(stamp)), expected)

    def test_weapons_and_armour_keep_their_whole_footprints_in_the_raw_gear_captures(self):
        """Thin handles and blank item backing complete three-row weapons without merging jewellery."""
        expected = [[1, 2, 13, 14, 25, 26], [3, 4, 15, 16, 27, 28], [5, 6, 17, 18],
                    [7, 8, 19, 20, 31, 32], [9, 10, 21, 22, 33, 34, 45, 46],
                    [29, 30, 41, 42], [37, 38, 49, 50]]
        for stamp in ("122006", "122258"):
            with self.subTest(capture=stamp):
                groups = item_ocr.inventory_item_footprints(self.grid(stamp))
                wanted = expected + ([[11, 23, 35]] if stamp == "122258" else [])
                self.assertCountEqual([group["slots"] for group in groups], wanted)
                covered = [slot for group in groups for slot in group["slots"]]
                self.assertEqual(len(covered), len(set(covered)))
                self.assertFalse(set(covered) & {39, 40, 43, 51, 52, 53, 54})
                for group in groups:
                    left, top, right, bottom = group["box"]
                    self.assertGreater(right, left)
                    self.assertGreater(bottom, top)

    def test_separate_currency_captures_never_merge_known_stacks_into_gear(self):
        """All seven provided captures containing only single-cell objects retain independent stacks."""
        for stamp in ("120330", "121050", "121224", "121340", "121445", "121548", "121810"):
            with self.subTest(capture=stamp):
                self.assertEqual(item_ocr.inventory_item_footprints(self.grid(stamp)), [])

    def test_uncertain_cell_geometry_cannot_prove_a_whole_item_footprint(self):
        """A failed grid fit must keep identification reviewable rather than guessing a rectangle."""
        grid = self.grid("122006")
        grid.info["poe2_inventory_geometry_uncertain"] = True
        self.assertEqual(item_ocr.inventory_item_footprints(grid), [])


if __name__ == "__main__":
    unittest.main()
