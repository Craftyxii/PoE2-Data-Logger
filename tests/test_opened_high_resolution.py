"""Real opened panels retain heading and complete-list evidence at large display scales."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import auto_commit, logger_store as logger, store
from PoE2_Data_Logger.ocr import opened_scan, runehelper_ocr


CAPTURE = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger/region_examples/opened.jpg"


class OpenedHighResolutionTests(unittest.TestCase):
    """Exercise actual screenshot pixels and both bundled OCR models at enlarged scales."""

    def setUp(self):
        """Isolate catalog initialization from the user's saved logger data."""
        self.data = tempfile.TemporaryDirectory(prefix="poe2-opened-high-resolution-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.data.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        """Restore logger storage and remove the temporary catalog."""
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.data.cleanup()

    def test_enlarged_full_window_preserves_heading_and_empty_list_tail(self):
        """Keep the real title and all three ordered rewards at 150 and 200 percent scale."""
        with Image.open(CAPTURE) as source:
            source = source.convert("RGB")
            for scale in (1.5, 2):
                with self.subTest(scale=scale):
                    image = source.resize((round(source.width * scale), round(source.height * scale)),
                                          Image.Resampling.LANCZOS)
                    result = opened_scan.scan_opened(image)
                    self.assertEqual([row["recipe"] for row in result["opened_recipes"]],
                                     ["Lesser Jeweller's Orb", "Regal Orb x3", "Exalted Orb x2"], result)
                    self.assertEqual((result["family"], result["sockets"]), ("Family 49", 3), result)
                    self.assertTrue(result["header_verified"], result)
                    self.assertTrue(result["list_complete"], result)
                    self.assertTrue(result["can_use"], result)
                    self.assertTrue(auto_commit.candidate(result)["ready"], result)
                    detected = runehelper_ocr.recognize(image)
                    with patch.object(runehelper_ocr, "recognize", return_value=detected[:-1]):
                        incomplete = opened_scan.scan_opened(image)
                    self.assertFalse(incomplete["list_complete"],
                        "A visible but unread final reward must prevent complete-list evidence.")


if __name__ == "__main__":
    unittest.main()
