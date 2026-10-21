"""Keep the supplied inventory shield whole without changing its currency stacks."""

import hashlib
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

from PoE2_Data_Logger.ocr import item_ocr, unique_items


class CapturedUniqueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(__file__).parent / "fixtures/unique_items/inventory.png"
        with Image.open(cls.path) as source:
            cls.image = source.convert("RGB")
        cls.slots = (11, 12, 23, 24, 35, 36, 47, 48)

    def test_original_capture_hash_and_full_shield_candidate_at_three_scales(self):
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),
                         "df67a0867e746e8f55bed4191c41471d378e41ee860b2b03e183a2663559c1f6")
        for scale in (.75, 1, 1.5):
            with self.subTest(scale=scale):
                image = self.image.resize((round(self.image.width * scale),
                                           round(self.image.height * scale)), Image.Resampling.LANCZOS)
                aligned = item_ocr.inventory_grid(image)
                cells = {slot: cell for slot in range(1, 61)
                         if float(np.asarray(cell := item_ocr.inventory_cell(aligned, slot),
                                             dtype=np.uint8).std(axis=(0, 1)).mean()) >= 8}
                self.assertIn(self.slots, item_ocr._inventory_unique_footprints(aligned, None, cells))
                box = item_ocr._inventory_unique_row({"score": 0}, aligned, self.slots)["box"]
                result = unique_items.match(aligned.crop(box), (2, 4))
                self.assertIsNotNone(result)
                self.assertIn("Redblade Banner", result["candidate_names"])
                self.assertIn(result["name"], ("", "Redblade Banner"))

    def test_currency_scan_delivers_one_full_shield_and_retains_all_chance_stacks(self):
        result = item_ocr.scan_inventory_grid(self.image)
        shields = [item for item in result["items"]
                   if item.get("catalog_unique") and tuple(item.get("grid_slots", ())) == self.slots]
        self.assertEqual(len(shields), 1, result)
        item = shields[0]
        self.assertEqual(tuple(item["grid_slots"]), self.slots)
        self.assertEqual((item["quantity"], item["category"]), (1, "Item"))
        self.assertTrue(item["name"] == "Redblade Banner" or
                        "Redblade Banner" in item.get("candidate", "").split(" / "))
        self.assertFalse(item["count_needs_review"])
        self.assertFalse(set(self.slots).intersection(row["slot"] for row in result["unknown"]))
        chance = [row for row in result["items"] if row["name"] == "Orb of Chance"]
        self.assertEqual([row["slot"] for row in chance], [3, 4, 5, 15, 16, 17, 27, 39, 41, 50, 51])
        self.assertEqual([row["quantity"] for row in chance], [10] * 11)
