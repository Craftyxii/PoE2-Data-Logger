from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import propagation_scan, runehelper_ocr


ROOT = Path(__file__).resolve().parents[1]


class PropagationOCRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.source = runehelper_ocr.default_frame(Image.open(
            ROOT / "PoE2_Data_Logger/region_examples/opened.jpg").convert("RGB"))
        self.glyph = self.source.crop((57, 73, 86, 102))

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def cursor(self, image, y):
        draw = ImageDraw.Draw(image)
        draw.polygon([(1, y), (15, y - 12), (37, y), (15, y + 12)], fill=(242, 209, 124))
        draw.polygon([(5, y), (15, y - 8), (31, y), (15, y + 8)],
                     outline=(215, 180, 90), width=2)

    def panel(self, recipes, selected=0, marks=None, scale=1):
        image = Image.new("RGB", (575, 720), (176, 161, 130))
        draw = ImageDraw.Draw(image)
        rows = []
        with logger._connect() as db:
            for row_index, recipe in enumerate(recipes):
                y = 86 + row_index * 81
                sockets = db.execute("SELECT sockets FROM recipes WHERE name=?", (recipe,)).fetchone()[0]
                for position in range(1, sockets + 1):
                    cx = 71 + 41 * (position - 1)
                    draw.rectangle((cx - 19, y - 18, cx + 18, y + 18), fill=(194, 178, 146),
                                   outline=(92, 63, 43), width=2)
                    image.paste(self.glyph, (cx - 14, y - 14))
                    if position in (marks or {}).get(row_index, []):
                        draw.rectangle((cx - 19, y - 18, cx + 18, y + 18),
                                       outline=(242, 213, 144), width=2)
                        for offset in (-9, 0, 9):
                            draw.polygon([(cx + offset, y - 23), (cx + offset - 2, y - 19),
                                          (cx + offset + 2, y - 19)], fill=(242, 213, 144))
                quantity = propagation_scan.opened_scan._quantity(recipe)
                name = recipe.rsplit(" x", 1)[0] if quantity > 1 else recipe
                text = f"{quantity}x {name}" if not name.startswith("Unique ") else name
                rows.append({"text": text, "score": .99, "x1": 230, "x2": 540,
                             "y1": y + 23, "y2": y + 51})
        if selected is not None:
            self.cursor(image, 86 + selected * 81)
        if scale != 1:
            image = image.resize((round(image.width * scale), round(image.height * scale)),
                                 Image.Resampling.LANCZOS)
        return image, rows

    def scan(self, image, rows):
        title = {"text": "Runeshape Combinations", "score": .99, "x1": 185, "x2": 390,
                 "y1": 32, "y2": 61}
        with patch.object(propagation_scan, "_read_panel_rows", return_value=(rows, title)):
            return propagation_scan.scan_propagation(image)

    def test_cursor_selects_only_its_row_even_when_every_row_has_marks(self):
        recipes = ["Lesser Jeweller's Orb", "Regal Orb x3", "Exalted Orb x2"]
        image, rows = self.panel(recipes, 1, {0: [3], 1: [2], 2: [3]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Regal Orb x3")
        self.assertEqual(result["runes"], ["Arcane"])
        self.assertEqual(result["positions"], [2])

    def test_two_marks_preserve_left_to_right_order(self):
        image, rows = self.panel(["Medved's Saga"], marks={0: [5, 1]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Rage", "Time"])
        self.assertEqual(result["positions"], [1, 5])

    def test_adjacent_marks(self):
        image, rows = self.panel(["Divine Orb x2"], marks={0: [2, 3]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Soul", "Power"])

    def test_repeated_rune_uses_marked_position_only(self):
        image, rows = self.panel(["Swift Alloy"], marks={0: [4]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Rebirth"])
        self.assertEqual(result["positions"], [4])

    def test_long_single_recipe_does_not_need_unique_family(self):
        image, rows = self.panel(["Perfect Exalted Orb x3"], marks={0: [7]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Power"])

    def test_crop_with_partial_family_list(self):
        image, rows = self.panel(["Greater Exalted Orb", "Greater Regal Orb"], 0, {0: [4], 1: [4]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["runes"], ["Prismatic"])

    def test_quantity_is_preserved(self):
        image, rows = self.panel(["Greater Exalted Orb x3"], marks={0: [4]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Greater Exalted Orb x3")
        self.assertEqual(result["runes"], ["Electrocuting"])

    def test_explicit_level_is_preserved(self):
        image, rows = self.panel(["Thaumaturgic Flux (Level 18)"], marks={0: [2]})
        result = self.scan(image, rows)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Thaumaturgic Flux (Level 18)")
        self.assertEqual(result["runes"], ["Ward"])

    def test_missing_level_cannot_use_a_different_level_recipe(self):
        image, rows = self.panel(["Thaumaturgic Flux (Level 18)"], marks={0: [2]})
        rows[0]["text"] = "1x Thaumaturgic Flux"
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])

    def test_missing_cursor_does_not_guess_selected_recipe(self):
        image, rows = self.panel(["Medved's Saga"], None, {0: [1]})
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["selected_recipe"], None)

    def test_multiple_cursors_are_ambiguous(self):
        image, rows = self.panel(["Medved's Saga", "Greater Regal Orb x3"], 0, {0: [1]})
        self.cursor(image, 167)
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])

    def test_no_marks_and_three_marks_are_not_accepted(self):
        for marks in ([], [1, 2, 3]):
            image, rows = self.panel(["Medved's Saga"], marks={0: marks})
            result = self.scan(image, rows)
            self.assertFalse(result["can_use"], result)
            self.assertEqual(result["runes"], [])

    def test_gold_highlight_without_three_peak_marker_is_not_a_mark(self):
        image, rows = self.panel(["Medved's Saga"], marks={0: []})
        ImageDraw.Draw(image).rectangle((52, 68, 89, 104), outline=(242, 213, 144), width=2)
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])

    def test_inconsistent_visible_recipe_sequence_is_not_accepted(self):
        image, rows = self.panel(["Medved's Saga", "Divine Orb"], marks={0: [1]})
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])

    def test_visible_tile_count_must_match_recipe(self):
        image, rows = self.panel(["Greater Exalted Orb x3"], marks={0: [4]})
        rows[0]["text"] = "1x Greater Exalted Orb"
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"], result)
        self.assertEqual(result["runes"], [])

    def test_selected_reward_low_confidence_is_not_accepted(self):
        image, rows = self.panel(["Medved's Saga"], marks={0: [1]})
        rows[0]["score"] = .7
        result = self.scan(image, rows)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])

    def test_screen_scale_changes_preserve_order(self):
        for scale in (.7, 1.5, 2):
            image, rows = self.panel(["Gemcutter's Prism x2"], marks={0: [1, 2]}, scale=scale)
            result = self.scan(image, rows)
            self.assertTrue(result["can_use"], (scale, result))
            self.assertEqual(result["runes"], ["Prismatic", "Celestial"])

    def test_scan_does_not_create_remnants_or_commit_records(self):
        image, rows = self.panel(["Medved's Saga"], marks={0: [1, 5]})
        with logger._connect() as db:
            before = list(db.iterdump())
        self.assertTrue(self.scan(image, rows)["can_use"])
        with logger._connect() as db:
            after = list(db.iterdump())
        self.assertEqual(before, after)

    def test_real_bundled_ocr_and_marks_with_controlled_cursor(self):
        self.cursor(self.source, 167)
        result = propagation_scan.scan_propagation(self.source)
        self.assertTrue(result["can_use"], result)
        self.assertEqual(result["selected_recipe"], "Regal Orb x3")
        self.assertEqual(result["runes"], ["Tidal"])

    def test_real_screenshot_without_cursor_does_not_scan(self):
        result = propagation_scan.scan_propagation(self.source)
        self.assertFalse(result["can_use"])
        self.assertEqual(result["runes"], [])


if __name__ == "__main__":
    unittest.main()
