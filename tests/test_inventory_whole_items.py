"""Recognize and learn full inventory equipment without interpreting its pieces as currency."""

from pathlib import Path
import unittest

from PIL import Image

from PoE2_Data_Logger.core import review_learning
from PoE2_Data_Logger.ocr import item_ocr


class InventoryWholeItemTests(unittest.TestCase):
    """Exercise whole-item review and shared reviewed artwork on actual gear pixels."""

    def source(self):
        """Load the supplied capture containing boots, helmets and a tall shield."""
        path = Path(__file__).parent / "fixtures/currency_training/inventory_122006.png"
        with Image.open(path) as image:
            return image.convert("RGB")

    def test_unknown_equipment_is_one_named_item_draft_per_full_footprint(self):
        """No covered gear cell may become an independent currency suggestion."""
        source = self.source()
        grid = item_ocr.inventory_grid(source)
        footprints = item_ocr.inventory_item_footprints(grid)
        self.assertEqual(len(footprints), 7)
        result = item_ocr.scan_inventory_grid(source, read=lambda image: [])
        rows = result["items"] + result["unknown"]
        for reward in footprints:
            covered = [row for row in rows if row["slot"] in reward["slots"]]
            self.assertEqual(len(covered), 1)
            item = covered[0]
            self.assertEqual(item["grid_slots"], reward["slots"])
            self.assertEqual(item["box"], reward["box"])
            self.assertEqual((item["name"], item["category"], item["quantity"]), ("", "Item", 1))
            self.assertFalse(item["count_needs_review"])
            self.assertTrue(item["name_needs_review"])

    def test_reviewed_currency_or_ritual_gear_is_recognized_again_as_one_item(self):
        """Both review modes retain full artwork and recognize its trusted label on rescan."""
        source = self.source()
        original = next(row for row in item_ocr.scan_inventory_grid(source, read=lambda image: [])["unknown"]
                        if row.get("slot") == 1 and row.get("grid_slots"))
        for mode in ("currency", "ritual"):
            with self.subTest(mode=mode):
                image = source if mode == "currency" else item_ocr.inventory_grid(source)
                example = review_learning.make_example(image, original, "Reviewed Whole Staff", "Item", mode)
                self.assertIsNotNone(example)
                raw, columns, rows = review_learning.encode_example(example)
                self.assertEqual((columns, rows), (2, 3))
                reference = {"name": example["name"], "kind": "item", "reviewed": True,
                             "image": raw, "columns": columns, "rows": rows}
                result = item_ocr.scan_inventory_grid(source, [reference], read=lambda image: [])
                item = next(row for row in result["items"] if row["slot"] == 1)
                self.assertEqual((item["name"], item["category"], item["quantity"]),
                                 ("Reviewed Whole Staff", "Item", 1))
                self.assertEqual(item["grid_slots"], [1, 2, 13, 14, 25, 26])
                self.assertFalse(item["count_needs_review"])
                tied = [reference, {**reference, "name": "Other Full Item"}]
                held = next(row for row in item_ocr.scan_inventory_grid(
                    source, tied, read=lambda image: [])["unknown"] if row["slot"] == 1)
                self.assertEqual(held["name"], "")
                self.assertTrue(held["name_needs_review"])


if __name__ == "__main__":
    unittest.main()
