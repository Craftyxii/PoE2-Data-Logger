"""Opened-remnant OCR reads the real panel independently of window scale."""
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
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


if __name__ == "__main__":
    unittest.main()
