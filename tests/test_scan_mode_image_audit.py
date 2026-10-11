"""Exercise production OCR with real Ritual captures and rendered tooltip text.

The repository has no game-captured waystone or tablet tooltips. Those checks
render representative text and run the actual OCR engine, rather than supplying
recognized rows; they do not establish accuracy on unseen game screenshots.
"""

import json
from pathlib import Path
import unittest

from PIL import Image, ImageDraw, ImageFont

from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import item_ocr, item_text
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog


ROOT = Path(__file__).resolve().parents[1]


class TooltipImageAuditTests(unittest.TestCase):
    """Check complete text recognition and parsing without injecting OCR rows."""

    @classmethod
    def setUpClass(cls):
        """Load the bundled display font and affix catalog for rendered proposals."""
        cls.font = ImageFont.truetype(str(ROOT / "PoE2_Data_Logger/fonts/DejaVuSans.ttf"), 23)
        cls.affixes = [entry["name"] for entry in affix_catalog()]

    def tooltip(self, lines, scale):
        """Render centered tooltip lines and resample the complete image at the requested scale."""
        image = Image.new("RGB", (1000, 40 * len(lines) + 40), (15, 15, 20))
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            draw.text((image.width // 2, 20 + index * 40), line, font=self.font,
                      fill=(210, 210, 230), anchor="mt")
        return image.resize((round(image.width * scale), round(image.height * scale)),
                            Image.Resampling.LANCZOS)

    def test_waystone_pixels_preserve_properties_and_both_modifier_rows(self):
        """Read rendered waystone pixels through the real engine at four display scales."""
        lines = ["Wasteland", "Waystone (Tier 15)", "Revives Available: 6",
                 "Item Rarity: +125%", "Monster Pack Size: +35%",
                 "Waystone Drop Chance: +215%", "Monsters have 20% increased Attack Speed",
                 "Players have 15% less Recovery Rate of Life and Energy Shield",
                 "Can be used in a Map Device"]
        for scale in (.75, 1, 1.25, 2):
            with self.subTest(scale=scale):
                result = item_text.read_screen_tooltip(self.tooltip(lines, scale), self.affixes)
                self.assertIsNotNone(result)
                self.assertEqual(result["kind"], "waystone")
                self.assertEqual(result["fields"], {
                    "tier": 15, "waystone": 215, "item_rarity": 125,
                    "monster_rarity": None, "pack_size": 35,
                    "effectiveness": None, "map_mods": 2})
                self.assertEqual(result["mods"], lines[-3:-1])
                self.assertEqual(result["status"], "review")

    def test_tablet_pixels_preserve_both_affixes_and_values(self):
        """Read rendered tablet pixels through the real engine and retain both numeric rolls."""
        lines = ["Precursor Tablet", "Uses Remaining: 10",
                 "10% increased Pack Size in Map",
                 "25% increased Rarity of Items found in Map",
                 "Can be used at a completed Tower"]
        for scale in (.75, 1, 1.25, 2):
            with self.subTest(scale=scale):
                result = item_text.read_screen_tooltip(self.tooltip(lines, scale), self.affixes)
                self.assertIsNotNone(result)
                self.assertEqual(result["kind"], "tablet")
                self.assertEqual([(entry["affix"], entry["value"], entry["unit"])
                                  for entry in result["matches"]], [
                    ("increased Pack Size in Map", 10, "%"),
                    ("increased Rarity of Items found in Map", 25, "%")])
                self.assertEqual(result["uncertain"], [])

    def test_title_free_pixels_cannot_route_or_invent_an_item(self):
        """Keep blank and isolated modifier text from becoming a waystone or tablet proposal."""
        for image in (Image.new("RGB", (1000, 400), (15, 15, 20)),
                      self.tooltip(["10% increased Pack Size in Map"], 1)):
            with self.subTest(size=image.size):
                self.assertIsNone(item_text.read_screen_tooltip(image, self.affixes))


class RitualCaptureImageAuditTests(unittest.TestCase):
    """Scan every supplied reward panel with the real engine and bundled icon reader."""

    def test_all_real_panels_keep_occupied_footprints_and_deferred_states_at_two_scales(self):
        """Verify all fifteen real pages preserve equipment grouping and deferred anchors at native and doubled size."""
        folder = ROOT / "tests/fixtures/ritual_rewards"
        cases = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        for case in cases:
            with Image.open(folder / case["file"]) as source:
                original = source.convert("RGB")
            for scale in (1, 2):
                with self.subTest(capture=case["file"], scale=scale):
                    image = original.resize((original.width * scale, original.height * scale),
                                            Image.Resampling.LANCZOS)
                    result = item_ocr.scan_ritual_page(image, OMEN_NAMES)
                    self.assertTrue(result["grid_detected"])
                    self.assertFalse(result["coverage_uncertain"])
                    self.assertEqual([row["grid_slots"] for row in result["items"]],
                                     case["footprints"])
                    self.assertEqual([row["grid_slots"][0] for row in result["items"]
                                      if row["deferred"]], case["deferred_anchors"])
                    for row in result["items"]:
                        if len(row["grid_slots"]) > 1:
                            self.assertEqual((row["category"], row["quantity"]), ("Item", 1))
                        if not row.get("name"):
                            self.assertTrue(row["name_needs_review"])


if __name__ == "__main__":
    unittest.main()
