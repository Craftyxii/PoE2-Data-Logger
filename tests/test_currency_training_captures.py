"""Real inventory captures must never skip review with a misread stack quantity."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import item_ocr
from PoE2_Data_Logger.ui.native_desktop import clear_currency_stack


FIXTURES = Path(__file__).parent / "fixtures" / "currency_training"


class CurrencyTrainingCapturesTests(unittest.TestCase):
    """Check complete recognition against quantities transcribed directly from the supplied images."""

    def test_every_stack_skipping_review_matches_its_visible_quantity(self):
        """Run real grid, catalog and count readers; uncertain identities may remain in review."""
        expected = json.loads((FIXTURES / "expected_quantities.json").read_text())["captures"]
        self.assertEqual(len(expected), 6)
        with tempfile.TemporaryDirectory(prefix="poe2-currency-captures-") as folder, \
                patch.object(store, "DATA_DIR", Path(folder)), patch.object(logger, "_READY", False):
            for filename, matrix in expected.items():
                with self.subTest(capture=filename):
                    self.assertEqual([len(row) for row in matrix], [12] * 5)
                    quantities = [quantity for row in matrix for quantity in row]
                    with Image.open(FIXTURES / filename) as capture:
                        source = capture.convert("RGB")
                    before = source.tobytes(), dict(source.info)
                    result = item_ocr.scan_inventory_grid(source)
                    clear = [item for item in result["items"] if clear_currency_stack(item)]
                    # A scanner that holds everything must not make this safety
                    # regression pass without exercising actual accepted stacks.
                    self.assertGreaterEqual(len(clear), 5)
                    self.assertTrue(any(item["quantity"] > 1 for item in clear))
                    for item in clear:
                        slot = item["slot"]
                        self.assertIn(slot, range(1, 61))
                        self.assertIsNotNone(quantities[slot - 1], (filename, slot, item))
                        self.assertEqual(item["quantity"], quantities[slot - 1], (filename, slot, item))
                    self.assertEqual((source.tobytes(), dict(source.info)), before)


if __name__ == "__main__":
    unittest.main()
