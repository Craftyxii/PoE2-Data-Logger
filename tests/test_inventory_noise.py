import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.ocr import currency_ocr, inventory_labels, item_ocr


class InventoryNoiseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = currency_ocr.CurrencyReader()
        cls.source = Image.open(Path(item_ocr.__file__).resolve().parent.parent /
                                "region_examples" / "inventory.jpg").convert("RGB")
        cls.entries = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())["icons"]

    def grid(self):
        image = Image.new("RGB", (480, 200), (26, 26, 40))
        image.info["poe2_inventory_aligned"] = True
        return image

    def art(self, entry):
        image = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
        image.alpha_composite(Image.fromarray(np.asarray(entry["rgba"], dtype=np.uint8).reshape(
            40, 40, 4), "RGBA"))
        return image.convert("RGB")

    def test_real_selected_empty_inventory_has_no_review_rows(self):
        image = self.source.crop((1153, 657, 1804, 937))
        for size in (image.size, (521, 224), (781, 336)):
            with self.subTest(size=size), patch.object(currency_ocr, "get_reader", return_value=self.reader):
                result = item_ocr.scan_inventory_grid(image.resize(size))
                self.assertEqual(result["items"], [])
                self.assertEqual(result["unknown"], [])

    def test_selected_empty_glow_never_reaches_the_matcher(self):
        image = item_ocr.inventory_grid(self.source.crop((1153, 657, 1804, 937)))
        cell = item_ocr.inventory_cell(image, 59)
        with patch.object(self.reader, "inventory_ranked", side_effect=AssertionError("empty icon ranked")):
            self.assertEqual(self.reader.icon(cell)["all"], [])

    def test_catalog_icons_keep_content_inside_the_occupancy_core(self):
        for entry in self.entries:
            with self.subTest(family=entry["family"]):
                core = np.asarray(self.art(entry))[7:-7, 7:-7]
                self.assertGreaterEqual(float(np.percentile(core, 95)), 32)

    def test_dim_catalog_currency_still_reaches_the_matcher(self):
        for name in ("Petition Splinter", "Runic Alloy", "Orb of Extraction", "Preserved Vertebrae"):
            entry = next(entry for entry in self.entries if name in entry["members"])
            image = Image.fromarray((np.asarray(self.art(entry)).astype(np.float32) * .65).astype(np.uint8))
            for size in (40, 54, 72):
                with self.subTest(name=name, size=size), patch.object(
                        self.reader, "inventory_ranked", wraps=self.reader.inventory_ranked) as ranked:
                    self.reader.icon(image.resize((size, size)))
                    ranked.assert_called_once()

    def test_continuous_body_armour_does_not_create_currency_rows(self):
        image = self.grid()
        image.paste(self.source.crop((1425, 284, 1524, 431)).resize((80, 120)), (200, 40))
        self.assertEqual(item_ocr._inventory_equipment_slots(image), {18, 19, 30, 31, 42, 43})
        with patch.object(currency_ocr, "get_reader", return_value=self.reader):
            result = item_ocr.scan_inventory_grid(image)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["unknown"], [])

    def test_belt_spanning_slots_38_and_39_does_not_create_review_rows(self):
        image = self.grid()
        image.paste(self.source.crop((1424, 450, 1525, 501)).resize((80, 40)), (40, 120))
        self.assertEqual(item_ocr._inventory_equipment_slots(image), {38, 39})
        with patch.object(currency_ocr, "get_reader", return_value=self.reader):
            result = item_ocr.scan_inventory_grid(image)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["unknown"], [])

    def test_unknown_single_slot_item_stays_reviewable_beside_large_gear(self):
        image = self.grid()
        image.paste(self.source.crop((1425, 284, 1524, 431)).resize((80, 120)), (200, 40))
        image.paste(self.source.crop((1352, 322, 1402, 378)).resize((40, 40)), (40, 120))
        with patch.object(currency_ocr, "get_reader", return_value=self.reader):
            result = item_ocr.scan_inventory_grid(image)
        self.assertEqual([entry["slot"] for entry in result["unknown"]], [38])

    def test_separate_catalog_tiles_do_not_become_equipment(self):
        image = self.grid()
        for index, name in enumerate(("Chaos Orb", "Exalted Orb", "Orb of Annulment", "Runic Alloy")):
            entry = next(entry for entry in self.entries if name in entry["members"])
            image.paste(self.art(entry), (40 * index, 0))
        self.assertEqual(item_ocr._inventory_equipment_slots(image), set())

    def test_isolated_white_pixel_does_not_create_a_stack_count(self):
        image = Image.new("RGB", (54, 54), (26, 26, 40))
        image.putpixel((4, 4), (255, 255, 255))
        patches, present = inventory_labels.count_crops(image)
        self.assertFalse(present)
        self.assertEqual(patches, [])
        image.paste((255, 255, 255), (4, 3, 6, 12))
        patches, present = inventory_labels.count_crops(image)
        self.assertTrue(present)
        self.assertTrue(patches)

    def test_unreadable_tier_art_does_not_block_an_untiered_item(self):
        image = self.grid()
        image.paste(Image.fromarray(np.random.default_rng(71).integers(
            40, 230, (40, 40, 3), dtype=np.uint8)), (0, 0))
        labels = {slot: {"count_present": False, "count": 1, "tier_present": slot == 1}
                  for slot in range(1, 61)}
        icon = {"family": "ChaosOrb", "members": ["Chaos Orb"], "score": .99}
        with patch.object(currency_ocr, "get_reader", return_value=self.reader), patch.object(
                self.reader, "icon", return_value=icon), patch.object(
                item_ocr, "_inventory_labels", return_value=labels):
            result = item_ocr.scan_inventory_grid(image)
        self.assertEqual([(entry["slot"], entry["name"]) for entry in result["items"]], [(1, "Chaos Orb")])
        self.assertFalse(result["items"][0]["count_needs_review"])
        self.assertEqual(result["unknown"], [])

    def test_unreadable_tier_on_a_shared_icon_still_requires_review(self):
        image = self.grid()
        image.paste(Image.fromarray(np.random.default_rng(72).integers(
            40, 230, (40, 40, 3), dtype=np.uint8)), (0, 0))
        labels = {slot: {"count_present": False, "count": 1, "tier_present": slot == 1}
                  for slot in range(1, 61)}
        icon = {"family": "ExaltedOrb", "members": ["Exalted Orb", "Greater Exalted Orb"], "score": .99}
        with patch.object(currency_ocr, "get_reader", return_value=self.reader), patch.object(
                self.reader, "icon", return_value=icon), patch.object(
                item_ocr, "_inventory_labels", return_value=labels):
            result = item_ocr.scan_inventory_grid(image)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["unknown"][0]["reason"], "check tier badge")


if __name__ == "__main__":
    unittest.main()
