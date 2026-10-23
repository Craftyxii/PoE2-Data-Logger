"""Real inventory captures must never skip review with a misread quantity."""

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
    """Check sourced artwork and real count readers against visible quantities."""

    def test_every_stack_skipping_review_matches_its_visible_quantity(self):
        """Exercise seven complete captures without accepting guessed labels or holding everything."""
        expected = json.loads((FIXTURES / "expected_quantities.json").read_text())["captures"]
        self.assertEqual(len(expected), 7)
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
                    # A scanner holding everything must not pass without
                    # exercising actual accepted stacks, including stacks >1.
                    self.assertGreaterEqual(len(clear), 5)
                    self.assertTrue(any(item["quantity"] > 1 for item in clear))
                    for item in clear:
                        slot = item["slot"]
                        self.assertIn(slot, range(1, 61))
                        self.assertIsNotNone(quantities[slot - 1], (filename, slot, item))
                        self.assertEqual(item["quantity"], quantities[slot - 1], (filename, slot, item))
                    self.assertEqual((source.tobytes(), dict(source.info)), before)

    def scan(self, filename):
        """Scan one capture in a disposable profile and index rows by slot."""
        with tempfile.TemporaryDirectory(prefix="poe2-currency-captures-") as folder, \
                patch.object(store, "DATA_DIR", Path(folder)), patch.object(logger, "_READY", False):
            result = item_ocr.scan_inventory_grid(FIXTURES / filename)
        return ({item["slot"]: item for item in result["items"]},
                {item["slot"]: item for item in result["unknown"]})

    def test_decisive_artwork_names_badged_and_count_covered_stacks(self):
        """A far-ahead artwork match names stacks whose digits and II badge cost score.

        Slots 38 and 50 carry a II badge and slot 52 is a 16-stack; each was
        held with an unused count before the lower-score/larger-lead gate.
        """
        items, _unknown = self.scan("inventory_120330.png")
        for slot, name, quantity in ((38, "Greater Orb of Augmentation", 30),
                                     (50, "Greater Orb of Augmentation", 23),
                                     (52, "Greater Jeweller's Orb", 16)):
            with self.subTest(slot=slot):
                self.assertEqual((items[slot]["name"], items[slot]["quantity"]), (name, quantity))
                self.assertTrue(clear_currency_stack(items[slot]))
        # The unbadged neighbour of slot 38 keeps its base tier.
        self.assertEqual((items[39]["name"], items[39]["quantity"]), ("Orb of Augmentation", 30))

    def test_ten_stacks_on_pale_art_suggest_ten_and_stay_held(self):
        """Pale crystal art seen through the 0 no longer turns a held 10 into a suggested 1."""
        items, _unknown = self.scan("inventory_121810.png")
        for slot, name in ((18, "Perfect Essence of Thawing"), (30, "Perfect Essence of Thawing"),
                           (43, "Perfect Essence of Thawing"), (55, "Perfect Essence of Ice")):
            with self.subTest(slot=slot):
                self.assertEqual((items[slot]["name"], items[slot]["quantity"]), (name, 10))
                self.assertTrue(items[slot]["count_needs_review"])

    def test_tier_fifteen_waystones_are_never_suggested_as_tier_sixteen(self):
        """Ring waystones marked XV suggest Tier 15 and still wait for a label."""
        _items, unknown = self.scan("inventory_115801.png")
        for slot in (13, 50):
            with self.subTest(slot=slot):
                self.assertEqual(unknown[slot]["candidate"], "Waystone (Tier 15)")
                self.assertEqual(unknown[slot]["reason"], "label if tracked")


if __name__ == "__main__":
    unittest.main()
