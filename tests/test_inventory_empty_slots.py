"""Empty cells are skipped while occupied cells retain local reference overrides."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.ocr import currency_ocr, item_ocr


class InventoryEmptySlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = currency_ocr.CurrencyReader()
        cls.source = Image.open(Path(item_ocr.__file__).resolve().parent.parent /
                                "region_examples" / "inventory.jpg").convert("RGB")
        cls.entries = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())["icons"]

    def grid(self, size=(648, 270)):
        image = Image.new("RGB", size, (26, 26, 40))
        image.info["poe2_inventory_aligned"] = True
        return image

    def art(self, name):
        entry = next(entry for entry in self.entries if name in entry["members"])
        image = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
        image.alpha_composite(Image.fromarray(np.asarray(entry["rgba"], dtype=np.uint8).reshape(40, 40, 4)))
        return image.convert("RGB")

    def reference(self, name, image):
        raw = io.BytesIO()
        image.save(raw, format="PNG")
        return {"name": name, "image": raw.getvalue()}

    def scan(self, image, references=()):
        with patch.object(currency_ocr, "get_reader", return_value=self.reader):
            return item_ocr.scan_inventory_grid(image, references, read=lambda _: [])

    def selected_empty(self):
        original = item_ocr.inventory_grid(self.source.crop((1153, 657, 1804, 937)))
        return item_ocr.inventory_cell(original, 59)

    def test_selected_empty_cannot_be_resurrected_by_saved_reference(self):
        cell = self.selected_empty()
        image = self.grid()
        image.paste(cell, (0, 0))
        self.assertEqual(self.reader.icon(cell)["all"], [])
        references = [self.reference("Accidentally captured empty slot", cell)]
        with patch.object(self.reader, "examples", wraps=self.reader.examples) as examples:
            result = self.scan(image, references)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["unknown"], [])
        examples.assert_not_called()

    def test_explicit_local_reference_can_override_ignored_catalog_art(self):
        cell = self.art("Lesser Ward Rune").resize((54, 54))
        image = self.grid()
        image.paste(cell, (0, 0))
        self.assertTrue(self.reader.icon(cell).get("ignored"))
        references = [self.reference("Reviewed tracked item", cell)]
        with patch.object(self.reader, "examples", wraps=self.reader.examples) as examples:
            result = self.scan(image, references)
        self.assertEqual([(row["slot"], row["name"]) for row in result["items"]],
                         [(1, "Reviewed tracked item")])
        self.assertEqual(result["unknown"], [])
        examples.assert_called_once()

    def test_occupied_unknown_icon_can_still_use_local_omen_reference(self):
        cell = Image.fromarray(np.random.default_rng(729).integers(45, 190, (54, 54, 3), dtype=np.uint8))
        image = self.grid()
        image.paste(cell, (0, 0))
        unknown = {"family": None, "members": [], "score": .6, "uncertain": True,
                   "all": [{"name": "Unrecognized item"}]}
        references = [self.reference("Reviewed Omen", cell)]
        with patch.object(self.reader, "icon", return_value=unknown), patch.object(self.reader, "count", return_value=2):
            result = self.scan(image, references)
        self.assertEqual([(row["slot"], row["name"], row["quantity"]) for row in result["items"]],
                         [(1, "Reviewed Omen", 2)])
        self.assertEqual(result["unknown"], [])

    def test_dark_catalog_currency_still_reaches_matcher_and_is_not_marked_empty(self):
        for name in ("Petition Splinter", "Runic Alloy", "Orb of Extraction", "Preserved Vertebrae"):
            cell = Image.fromarray((np.asarray(self.art(name)).astype(np.float32) * .65).astype(np.uint8))
            for size in (40, 54, 72):
                with self.subTest(name=name, size=size), patch.object(
                        self.reader, "inventory_ranked", wraps=self.reader.inventory_ranked) as ranked:
                    result = self.reader.icon(cell.resize((size, size)))
                ranked.assert_called_once()
                self.assertFalse(result.get("empty", False))

    def test_unknown_single_cell_beside_body_armour_remains_reviewable(self):
        image = self.grid((480, 200))
        image.paste(self.source.crop((1425, 284, 1524, 431)).resize((80, 120)), (200, 40))
        image.paste(self.source.crop((1352, 322, 1402, 378)).resize((40, 40)), (40, 120))
        self.assertNotIn(38, item_ocr._inventory_equipment_slots(image))
        result = self.scan(image)
        self.assertEqual([entry["slot"] for entry in result["unknown"]], [38])

    def test_currency_adjacent_to_multicell_gear_is_not_discarded(self):
        image = self.grid((480, 200))
        image.paste(self.source.crop((1425, 284, 1524, 431)).resize((80, 120)), (200, 40))
        image.paste(self.art("Chaos Orb"), (160, 40))
        image.paste(self.art("Runic Alloy"), (280, 80))
        self.assertFalse({17, 32} & item_ocr._inventory_equipment_slots(image))
        result = self.scan(image)
        self.assertEqual([(row["slot"], row["name"]) for row in result["items"]],
                         [(17, "Chaos Orb"), (32, "Runic Alloy")])
        self.assertEqual(result["unknown"], [])


if __name__ == "__main__":
    unittest.main()
