"""Read actual inventory capture pixels through the production recognizers.

Resized 1080p captures exercise scaling; they are not independently captured 4K
fixtures. Icon matching, count geometry and RapidOCR are not replaced by mocks.
"""

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import item_ocr


class InventoryCaptureRegressionTests(unittest.TestCase):
    """Keep visible real stacks reviewable without inventing empty-slot items or counts."""

    @classmethod
    def setUpClass(cls):
        """Load unchanged screenshots containing an empty grid and three visible stacks."""
        captures = Path(item_ocr.__file__).resolve().parent.parent / "region_examples"
        cls.captures = {}
        for filename in ("inventory.jpg", "ritual.jpg"):
            with Image.open(captures / filename) as source:
                cls.captures[filename] = source.convert("RGB").crop((1153, 657, 1804, 937))

    def setUp(self):
        """Isolate any OCR-settings reads from the developer's own logger database."""
        self.directory = tempfile.TemporaryDirectory(prefix="poe2-real-inventory-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.directory.name)
        logger._READY = False

    def tearDown(self):
        """Restore the normal profile after each actual-capture regression."""
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.directory.cleanup()

    def capture(self, filename, scale):
        """Resize a complete captured grid with normal image resampling."""
        source = self.captures[filename]
        return source.resize((round(source.width * scale), round(source.height * scale)),
                             Image.Resampling.LANCZOS)

    def test_real_empty_grid_and_selection_glow_never_create_items(self):
        """Keep all sixty empty cells empty across downscaled and doubled capture sizes."""
        for scale in (.75, 1, 1.25, 2):
            with self.subTest(scale=scale):
                result = item_ocr.scan_inventory_grid(self.capture("inventory.jpg", scale))
                self.assertEqual(result["items"], [])
                self.assertEqual(result["unknown"], [])

    def test_real_populated_grid_retains_only_the_three_visible_stacks(self):
        """Preserve splinter count fourteen whenever identified and hold uncertain artwork."""
        for scale in (.75, 1, 1.25, 2):
            with self.subTest(scale=scale):
                result = item_ocr.scan_inventory_grid(self.capture("ritual.jpg", scale))
                items = {row["slot"]: row for row in result["items"]}
                unknown = {row["slot"]: row for row in result["unknown"]}
                self.assertEqual(set(items) | set(unknown), {1, 13, 25})
                self.assertEqual(items[13]["quantity"], 1)
                if 1 in items:
                    self.assertEqual(items[1]["name"], "Simulacrum Splinter")
                    self.assertEqual(items[1]["quantity"], 14)
                    self.assertFalse(items[1]["count_needs_review"])
                else:
                    self.assertIn("Simulacrum Splinter", unknown[1]["candidate"])
                # The unlabelled tablet is a tracked-item suggestion, not an
                # automatically accepted currency stack with an invented count.
                self.assertIn(25, unknown)
                if scale >= 1.25:
                    self.assertIn(1, items)

    def test_maximum_strictness_keeps_real_matches_manual_with_complete_counts(self):
        """Hold named real capture matches at strictness one hundred without truncating fourteen."""
        result = item_ocr.scan_inventory_grid(self.capture("ritual.jpg", 2), strictness=100)
        rows = result["items"] + result["unknown"]
        self.assertEqual({row["slot"] for row in rows}, {1, 13, 25})
        for row in result["items"]:
            self.assertTrue(row["name_needs_review"])
            if row["slot"] == 1:
                self.assertEqual(row["quantity"], 14)
        self.assertEqual(result["_ocr_strictness"], 100)


if __name__ == "__main__":
    unittest.main()
