"""Keep malformed signs/decimals visible through Tablet and Waystone parsing."""

import unittest

from PIL import Image

from PoE2_Data_Logger.ocr import item_ocr, item_text
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog, modifier_value, new_affix_names
from PoE2_Data_Logger.ui.native_desktop import clear_waystone_read


BAD_ROLLS = (".5%", ".05%", "-.5%", "−5%", "–5%", "++5%", "—5%",
             ",5%", "－5%", "＋5%", "- 5%", "5%%", ". 5%", ", 5%")


class NumericTokenSafetyTests(unittest.TestCase):
    """Trace complete numeric tokens before exact affix matching can mark a read ready."""

    @classmethod
    def setUpClass(cls):
        """Load production names without adding synthetic catalog entries."""
        cls.affixes = [entry["name"] for entry in affix_catalog()]

    def test_malformed_tablet_tokens_cannot_become_known_affix_values(self):
        """Reject sign/decimal fragments even with exact wording and maximum OCR confidence."""
        for roll in BAD_ROLLS:
            raw = f"{roll} increased Effectiveness"
            with self.subTest(roll=roll):
                self.assertIsNone(modifier_value(raw))
                result = item_ocr.parse_tablet([{"text": raw, "score": 1}], self.affixes)
                self.assertEqual(result["matches"], [])
                self.assertEqual(result["uncertain"], [raw])
                self.assertEqual(result["status"], "review")
                self.assertEqual(new_affix_names([raw], []), [])

    def test_count_tokens_and_implicit_counts_cannot_hide_malformed_digits(self):
        """Prevent the same suffix loss in counts and implicit first-unearthed/flag parsing."""
        for raw in ("−5 additional random Modifiers", "–5 additional random Modifiers",
                    "++5 additional random Modifiers", ".5 additional random Modifiers",
                    "5. additional random Modifiers", "− 5 additional random Modifiers",
                    ".5 The first unearthed Remnant has no Downsides"):
            with self.subTest(raw=raw):
                self.assertIsNone(modifier_value(raw))

    def test_valid_decimal_count_and_roll_range_tokens_are_preserved(self):
        """Keep established explicit signs, comma decimals, advanced roll ranges and counts."""
        for raw, expected, unit in (("+5% increased Effectiveness", 5, "%"),
                                    ("0.5% increased Effectiveness", .5, "%"),
                                    ("0,05% increased Effectiveness", .05, "%"),
                                    ("10(5-15)% increased Effectiveness", 10, "%"),
                                    ("10% (5%–15%) increased Effectiveness", 10, "%"),
                                    ("Map has +2 additional random Modifiers", 2, "count"),
                                    ("In Map, 2 additional Shrines appear", 2, "count"),
                                    ("Map has an additional Shrine", 1, "count")):
            with self.subTest(raw=raw):
                result = modifier_value(raw)
                self.assertIsNotNone(result)
                self.assertEqual((result["value"], result["unit"]), (expected, unit))

    def test_waystone_properties_hold_malformed_tokens_from_copy_and_screen_at_any_strictness(self):
        """Retain malformed observed properties instead of passing the automatic-save gate."""
        properties = (("waystone", "Waystone Drop Chance"), ("item_rarity", "Item Rarity"),
                      ("monster_rarity", "Monster Rarity"), ("pack_size", "Monster Pack Size"),
                      ("effectiveness", "Monster Effectiveness"))
        for key, label in properties:
            for roll in BAD_ROLLS:
                lines = ["Waystone (Tier 15)", "Waystone Drop Chance: +80%",
                         *([] if key == "waystone" else [f"{label}: {roll}"]),
                         "Monsters have 20% increased Attack Speed"]
                if key == "waystone":
                    lines[1] = f"{label}: {roll}"
                for strictness in (0, 50, 100):
                    with self.subTest(property=key, roll=roll, strictness=strictness):
                        copied = item_text.parse_item_text("\n".join([
                            "Item Class: Waystones", *lines[:2],
                            *lines[2:-1], "Item Level: 82", lines[-1]]), self.affixes)
                        screen = item_text.read_screen_tooltip(
                            Image.new("RGB", (500, 400)), self.affixes,
                            ocr_rows=[{"text": line, "score": 1} for line in lines],
                            strictness=strictness)
                        for result in (copied, screen):
                            self.assertIsNone(result["fields"][key])
                            self.assertEqual(result["uncertain"], [f"{label}: {roll}"])
                            self.assertFalse(clear_waystone_read(result))

    def test_valid_waystone_signed_decimals_and_augmented_text_remain_supported(self):
        """Preserve supported positive/negative ASCII signs and standard percentage decimals."""
        for roll, expected in (("-5%", -5), ("+5%", 5), ("0.5%", .5),
                               ("+10,5% (augmented)", 10.5)):
            with self.subTest(roll=roll):
                self.assertEqual(item_text._number(f"Waystone Drop Chance: {roll}"), expected)


if __name__ == "__main__":
    unittest.main()
