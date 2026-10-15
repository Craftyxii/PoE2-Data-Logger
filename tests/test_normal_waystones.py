"""Accept proven unmodified Normal waystones without turning missing OCR into zeroes."""

import unittest

from PIL import Image

from PoE2_Data_Logger.ocr import item_text
from PoE2_Data_Logger.ui.native_desktop import clear_waystone_read


class NormalWaystoneTests(unittest.TestCase):
    """Check copied and bounded screen tooltip evidence before allowing empty map modifiers."""

    lines = ["Rarity: Normal", "Waystone (Tier 16)", "Revives Available: 6",
             "--------", "Can be used in a Map Device"]

    def read(self, lines, source, *, score=.99, strictness=50):
        """Run both production parsers; screen tooltips intentionally omit copied Item Level."""
        if source == "clipboard":
            return item_text.parse_item_text("\n".join(["Item Class: Waystones", *lines]))
        return item_text.read_screen_tooltip(
            Image.new("RGB", (700, 700)),
            ocr_rows=[{"text": line, "score": score} for line in lines], strictness=strictness)

    def test_complete_normal_tooltips_have_explicit_zeroes_from_both_sources(self):
        """Normal rarity plus a tier title and usage boundary proves an empty modifier region."""
        for source in ("clipboard", "screen OCR"):
            for drop_line in ([], ["Item Level: 82"], ["Waystone Drop Chance: +0%"]):
                with self.subTest(source=source, extra_properties=drop_line):
                    result = self.read([*self.lines[:-1], *drop_line, self.lines[-1]], source)
                    self.assertEqual((result["fields"]["tier"], result["fields"]["waystone"],
                                      result["fields"]["map_mods"], result["mods"]), (16, 0, 0, []))
                    self.assertEqual(result["name"], "")
                    self.assertTrue(clear_waystone_read(result))

    def test_missing_rarity_footer_or_identification_cannot_prove_zero(self):
        """Incomplete, unidentified and non-Normal captures retain unknown values or fail routing."""
        cases = [self.lines[1:], self.lines[:-1],
                 ["Rarity: Rare", *self.lines[1:]], ["Rarity: Magic", *self.lines[1:]],
                 [*self.lines[:-1], "Unidentified", self.lines[-1]],
                 [*self.lines, "Unidentified"],
                 [*self.lines[:-1], "Monsters are Hexproof", self.lines[-1]]]
        for lines in cases:
            for source in ("clipboard", "screen OCR"):
                with self.subTest(lines=lines, source=source):
                    result = self.read(lines, source)
                    if result is not None:
                        self.assertIsNone(result["fields"]["waystone"])
                        self.assertIsNone(result["fields"]["map_mods"])
                        self.assertFalse(clear_waystone_read(result))
        self.assertIsNone(item_text.parse_item_text(""))
        self.assertIsNone(self.read([], "screen OCR"))

    def test_observed_bad_or_nonzero_rolls_are_never_replaced_with_zero(self):
        """Normal rarity cannot hide a malformed percentage or contradictory observed bonus."""
        for property_line in ("Waystone Drop Chance: ++5%", "Item Rarity: +?%",
                              "Waystone Drop Chance: +5%", "Monster Pack Size: +5%"):
            for source in ("clipboard", "screen OCR"):
                with self.subTest(property=property_line, source=source):
                    result = self.read([*self.lines[:-1], property_line, self.lines[-1]], source)
                    if result is not None:
                        self.assertFalse(result.get("_normal_unmodified", False))
                        self.assertFalse(clear_waystone_read(result))
                        if property_line == "Waystone Drop Chance: +5%":
                            self.assertEqual(result["fields"]["waystone"], 5)

    def test_zero_modifier_exception_preserves_tier_count_and_uncertainty_guards(self):
        """Only parser-proven empty Normal reads may bypass the positive modifier minimum."""
        result = self.read(self.lines, "clipboard")
        for patch in ({"fields": {**result["fields"], "tier": 14}},
                      {"fields": {**result["fields"], "map_mods": 1}},
                      {"fields": {**result["fields"], "waystone": None}},
                      {"uncertain": ["unreadable row"]}, {"_normal_unmodified": False}):
            with self.subTest(patch=patch):
                self.assertFalse(clear_waystone_read({**result, **patch}))
        self.assertFalse(clear_waystone_read({"fields": {"tier": 16, "waystone": 0, "map_mods": 0},
                                              "mods": [], "rarity": "Normal"}))

    def test_conflicting_tier_or_duplicate_rolls_cannot_prove_zero(self):
        """The empty-Normal exception checks every observed tier and roll rather than only the first."""
        properties = [["Waystone Tier: 15"], ["Waystone Tier: unknown"]]
        properties += [[f"{label}: +0%", f"{label}: +100%"] for label in
                       ("Waystone Drop Chance", "Item Rarity", "Monster Rarity",
                        "Monster Pack Size", "Monster Effectiveness")]
        properties += [["Waystone Drop Chance: +0%", "Waystone Drop Chance: +?%"]]
        for extra in properties:
            for source in ("clipboard", "screen OCR"):
                with self.subTest(properties=extra, source=source):
                    result = self.read([*self.lines[:-1], *extra, self.lines[-1]], source)
                    if result is not None:
                        self.assertFalse(result.get("_normal_unmodified", False))
                        self.assertIsNone(result["fields"]["map_mods"])
                        self.assertFalse(clear_waystone_read(result))
        self.assertIsNone(self.read(["Waystone Tier: 15", *self.lines], "screen OCR"))

    def test_screen_confidence_and_always_review_still_hold_proven_zeroes(self):
        """A valid zero stays editable when OCR evidence is weak or automatic approval is disabled."""
        for score, strictness in ((.5, 50), (.99, 100)):
            with self.subTest(score=score, strictness=strictness):
                result = self.read(self.lines, "screen OCR", score=score, strictness=strictness)
                self.assertEqual((result["fields"]["waystone"], result["fields"]["map_mods"]), (0, 0))
                self.assertFalse(clear_waystone_read(result))


if __name__ == "__main__":
    unittest.main()
