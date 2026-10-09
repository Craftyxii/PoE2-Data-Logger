"""Exercise real Ritual reward pixels through the scanner at doubled UI scale."""

import json
from pathlib import Path
import unittest

from PIL import Image, ImageDraw, ImageEnhance

from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import item_ocr
from PoE2_Data_Logger.ocr.ritual_grid import detect_reward_grid


class Ritual4KRecognitionTests(unittest.TestCase):
    """Exercise captured Ritual rewards and header counters at doubled UI scale."""
    def test_dimmed_scaled_equipment_retains_one_row_per_item(self):
        """Dimmed ornaments must not turn large gear into several omen cells."""
        folder = Path(__file__).parent / "fixtures/ritual_rewards"
        cases = json.loads((folder / "expected.json").read_text())
        for case in (row for row in cases if row["file"] in ("01.png", "08.png", "15.png")):
            with Image.open(folder / case["file"]) as source:
                original = source.convert("RGB")
            for brightness in (.7, .5):
                with self.subTest(capture=case["file"], brightness=brightness):
                    image = original.resize((original.width * 2, original.height * 2),
                                            Image.Resampling.LANCZOS)
                    image = ImageEnhance.Brightness(image).enhance(brightness)
                    grid = detect_reward_grid(image)
                    self.assertIsNotNone(grid)
                    self.assertEqual([row["slots"] for row in grid["rewards"]], case["footprints"])

    def test_scaled_actual_reward_grid_keeps_named_omens_and_equipment_rows(self):
        """A larger frame must not change the identity of the same reward art."""
        folder = Path(__file__).parent / "fixtures/ritual_rewards"
        case = next(row for row in json.loads((folder / "expected.json").read_text())
                    if row["file"] == "08.png")
        with Image.open(folder / case["file"]) as source:
            original = source.convert("RGB")
        for scale in (1, 2):
            with self.subTest(scale=scale):
                image = original.resize((original.width * scale, original.height * scale),
                                        Image.Resampling.LANCZOS)
                result = item_ocr.scan_ritual_page(image, OMEN_NAMES)
                self.assertTrue(result["grid_detected"])
                self.assertEqual([row["grid_slots"] for row in result["items"]],
                                 case["footprints"])
                by_slots = {tuple(row["grid_slots"]): row for row in result["items"]}
                for slot in (87, 111):
                    self.assertEqual(by_slots[(slot,)]["name"], "Omen of Resurgence")
                for slots in ((38, 39, 50, 51), (73, 74, 85, 86)):
                    self.assertEqual(by_slots[slots]["category"], "Item")
                    self.assertEqual(by_slots[slots]["quantity"], 1)

    def test_button_art_without_numerals_cannot_populate_rerolls(self):
        """Keep the real button art while removing the separately read counter."""
        path = Path(__file__).parent / "fixtures/ritual_headers/header_36.png"
        with Image.open(path) as source:
            original = source.convert("RGB")
        ImageDraw.Draw(original).rectangle((84, 83, 116, 111), fill=(65, 22, 0))
        for scale in (1, 2):
            with self.subTest(scale=scale):
                image = original.resize((original.width * scale, original.height * scale),
                                        Image.Resampling.LANCZOS)
                grid = {"bounds": tuple(value * scale for value in (28, 131, 660, 657))}
                self.assertIsNone(item_ocr._ritual_grid_rerolls(image, grid))


if __name__ == "__main__":
    unittest.main()
