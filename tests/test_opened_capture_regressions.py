"""Opened-remnant OCR reads the real panel independently of window scale."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import auto_commit, logger_store as logger, store
from PoE2_Data_Logger.ocr import opened_scan, runehelper_ocr


CAPTURE = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger/region_examples/opened.jpg"


class OpenedCaptureRegressions(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory(prefix="poe2-real-opened-capture-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.data.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.data.cleanup()

    def test_real_full_window_keeps_recipe_quantities_and_three_sockets_at_each_scale(self):
        expected_recipes = ["Lesser Jeweller's Orb", "Regal Orb x3", "Exalted Orb x2"]
        with Image.open(CAPTURE) as source:
            source = source.convert("RGB")
            for scale in (.75, 1, 1.25):
                with self.subTest(scale=scale):
                    image = source.resize((round(source.width * scale), round(source.height * scale)),
                                          Image.Resampling.LANCZOS)
                    result = opened_scan.scan_opened(image)
                    self.assertEqual([row["recipe"] for row in result["opened_recipes"]], expected_recipes,
                                     result)
                    self.assertEqual((result["first_recipe"], result["next_recipe"]),
                                     tuple(expected_recipes[:2]))
                    self.assertEqual((result["family"], result["candidates"]), ("Family 49", [49]))
                    self.assertEqual((result["sockets"], result["recipe_sockets"], result["socket_source"]),
                                     (3, 3, "opened icons"), result)
                    self.assertTrue(result["header_verified"], result)
                    self.assertTrue(result["list_complete"], result)
                    self.assertTrue(result["can_use"], result)

    def test_visible_fourth_socket_is_held_instead_of_forced_to_recipe_count(self):
        with Image.open(CAPTURE) as source:
            panel = runehelper_ocr.default_frame(source.convert("RGB"))
        # Reuse a real complete socket frame in the adjacent fourth position.
        # The reward text still denotes a three-socket Family 49 stage.
        panel.paste(panel.crop((52, 70, 91, 107)), (175, 70))
        result = opened_scan.scan_opened(panel)
        self.assertEqual(result["family"], "Family 49", result)
        self.assertEqual(result["recipe_sockets"], 3, result)
        self.assertEqual(result["sockets"], 4, result)
        self.assertEqual(result["socket_source"], "opened icons")
        self.assertFalse(result["can_use"], result)
        self.assertIn("Opened icons show 4 sockets", result["status"])

    def test_database_expected_count_cannot_override_real_three_socket_geometry(self):
        with Image.open(CAPTURE) as source:
            image = source.convert("RGB")
        rows = runehelper_ocr.recognize(image)
        first_reward_y = min(row["y1"] for row in rows)
        # Background texture makes the cheap sampler report four here. It
        # must not win merely because a misidentified recipe also expects four.
        for expected in (3, 4):
            with self.subTest(database_expected=expected):
                self.assertEqual(opened_scan._icon_count(image, first_reward_y, expected), 3)

    def panel_with_icons(self, sockets):
        with Image.open(CAPTURE) as source:
            panel = runehelper_ocr.default_frame(source.convert("RGB"))
        icon = panel.crop((52, 70, 91, 107))
        for index in range(3, sockets):
            panel.paste(icon, (52 + 41 * index, 70))
        return panel

    def test_cropped_five_socket_geometry_survives_display_scale_without_db_override(self):
        panel = self.panel_with_icons(5)
        for scale in (.5, .75, 1, 1.25):
            image = panel.resize((round(panel.width * scale), round(panel.height * scale)),
                                 Image.Resampling.LANCZOS)
            for expected in (4, 5):
                with self.subTest(scale=scale, database_expected=expected):
                    self.assertEqual(opened_scan._icon_count(image, 109 * scale, expected), 5)

    def test_five_socket_rare_unique_sequence_is_usable_at_smaller_capture_scale(self):
        panel = self.panel_with_icons(5)
        scale = .75
        image = panel.resize((round(panel.width * scale), round(panel.height * scale)),
                             Image.Resampling.LANCZOS)
        names = ["Rare Unique Item", "Orb of Chance", "Unique Helmet", "Unique Gloves",
                 "Unique Boots", "Unique Body Armour"]
        rows = [{"text": "Runeshape Combinations", "score": .99,
                 "x": 180 * scale, "y": 32 * scale, "right": 400 * scale, "bottom": 55 * scale}]
        rows.extend({"text": "1x " + name, "score": .99,
                     "x": 250 * scale, "y": (109 + index * 81) * scale,
                     "right": 533 * scale, "bottom": (137 + index * 81) * scale}
                    for index, name in enumerate(names))
        result = opened_scan.scan_opened(image, ocr_rows=rows)
        self.assertEqual((result["family"], result["candidates"]), ("Family 37", [37]), result)
        self.assertEqual((result["sockets"], result["recipe_sockets"]), (5, 5), result)
        self.assertEqual([line["recipe"] for line in result["opened_recipes"]], names)
        self.assertTrue(auto_commit.candidate(result)["ready"], result)

    def test_complete_single_chase_reward_uses_panel_end_but_cropped_list_stays_ambiguous(self):
        panel = self.panel_with_icons(6)
        # Keep the real heading and first icon row, and reuse the fixture's
        # empty parchment to represent a complete one-reward panel.
        panel.paste(panel.crop((47, 307, 543, 540)), (47, 142))
        with Image.open(CAPTURE) as source:
            full_window = source.convert("RGB")
        full_window.paste(panel, (0, 199))
        name = "Farrul's Rune of the Chase"
        for image, offset, complete in ((full_window, 199, True),
                                        (panel.crop((0, 0, 575, 225)), 0, False)):
            with self.subTest(full_window=complete):
                reward_y = 109 + offset
                rows = [{"text": "1x " + name, "score": .99, "x1": 250,
                         "y1": reward_y, "x2": 533, "y2": reward_y + 28}]
                header_y = 32 + offset - max(0, reward_y - 180)
                header = SimpleNamespace(
                    boxes=[[[180, header_y], [400, header_y],
                            [400, header_y + 23], [180, header_y + 23]]],
                    txts=["Runeshape Combinations"], scores=[.99])
                with patch.object(runehelper_ocr, "recognize", return_value=rows), patch.object(
                        opened_scan, "_engine", return_value=lambda unused: header):
                    result = opened_scan.scan_opened(image)
                self.assertEqual(result["sockets"], 6, result)
                self.assertEqual(result["list_complete"], complete, result)
                if complete:
                    self.assertEqual((result["family"], result["candidates"]), ("Family 76", [76]), result)
                    self.assertTrue(auto_commit.candidate(result)["ready"], result)
                else:
                    self.assertIsNone(result["family"], result)
                    self.assertEqual(result["candidates"], [34, 76], result)
                    self.assertFalse(auto_commit.candidate(result)["ready"], result)


if __name__ == "__main__":
    unittest.main()
