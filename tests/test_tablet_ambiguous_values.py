"""Ambiguous tablet numbers remain editable instead of becoming accepted affix rolls."""

import unittest

from PIL import Image

from PoE2_Data_Logger.ocr import item_ocr, item_text
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog, modifier_value, new_affix_names


class TabletAmbiguousValueTests(unittest.TestCase):
    """Hold extra numeric tokens across pasted text, screen OCR and catalog suggestions."""

    @classmethod
    def setUpClass(cls):
        """Use the bundled affix catalog for exact-match parser checks."""
        cls.affixes = [entry["name"] for entry in affix_catalog()]

    def test_extra_numbers_cannot_be_discarded_by_exact_affix_matching(self):
        """Reject an extra roll or malformed decimal even when wording matches a known affix."""
        for raw in ("10 20% increased Pack Size in Map",
                    "10% increased Pack Size in Map 20",
                    "1.2.3% increased Pack Size in Map"):
            with self.subTest(raw=raw):
                self.assertIsNone(modifier_value(raw))
                result = item_ocr.parse_tablet([{"text": raw, "score": .999}], self.affixes)
                self.assertEqual(result["matches"], [])
                self.assertEqual(result["uncertain"], [raw])
                self.assertEqual(result["status"], "review")
                self.assertEqual(new_affix_names([raw], []), [])

    def test_copied_and_screen_tablets_retain_the_ambiguous_row(self):
        """Keep the complete row visible beside a valid modifier at every strictness setting."""
        raw = "10 20% increased Pack Size in Map"
        good = "25% increased Rarity of Items found in Map"
        copied = item_text.parse_item_text("\n".join((
            "Item Class: Tablets", "Rarity: Magic", "Precursor Tablet",
            "Item Level: 82", raw, good)), self.affixes)
        self.assertEqual(copied["uncertain"], [raw])
        self.assertEqual(len(copied["matches"]), 1)
        rows = [{"text": text, "score": 1.0} for text in (
            "Precursor Tablet", "Uses Remaining: 10", raw, good)]
        for strictness in (0, 50, 100):
            with self.subTest(strictness=strictness):
                result = item_text.read_screen_tooltip(
                    Image.new("RGB", (500, 300)), self.affixes,
                    ocr_rows=rows, strictness=strictness)
                self.assertEqual(result["uncertain"], [raw])
                self.assertEqual(len(result["matches"]), 1)
                self.assertEqual(result["status"], "review")

    def test_single_roll_and_displayed_roll_ranges_still_parse(self):
        """Preserve decimal, signed and advanced-description roll syntax."""
        for raw, expected in (("10% increased Pack Size in Map", 10),
                              ("+10% increased Pack Size in Map", 10),
                              ("10,5% increased Pack Size in Map", 10.5),
                              ("10(5-15)% increased Pack Size in Map", 10),
                              ("10% (5%–15%) increased Pack Size in Map", 10)):
            with self.subTest(raw=raw):
                result = item_ocr.parse_tablet([raw], self.affixes)
                self.assertEqual(result["matches"][0]["value"], expected)
                self.assertEqual(result["status"], "ready")


if __name__ == "__main__":
    unittest.main()
