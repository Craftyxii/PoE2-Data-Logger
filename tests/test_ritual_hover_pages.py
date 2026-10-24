"""Real Ritual_7 pages: hovered rewards, covered corners, clipped captures and sprite footprints.

Each crop is scanned once at native scale. Footprints and names were checked
against the screenshots and the catalog artwork.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, ritual_catalog, store
from PoE2_Data_Logger.core.ritual_catalog import NOT_RITUAL_REWARDS, OMEN_NAMES
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, ritual_grid, unique_items
from PoE2_Data_Logger.ui.native_desktop import clear_ritual_read


FIXTURES = Path(__file__).parent / "fixtures" / "ritual_pages_hover"


class RitualHoverPagesTests(unittest.TestCase):
    """Check whole footprints and verified names on six real pages."""

    @classmethod
    def setUpClass(cls):
        """Scan every page once in a disposable profile."""
        cls.cases = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        cls.images, cls.results = {}, {}
        cls.folder = tempfile.TemporaryDirectory(prefix="poe2-ritual-hover-")
        cls.patches = (patch.object(store, "DATA_DIR", Path(cls.folder.name)), patch.object(logger, "_READY", False))
        for active in cls.patches:
            active.start()
        for case in cls.cases:
            with Image.open(FIXTURES / case["file"]) as source:
                cls.images[case["file"]] = source.convert("RGB")
            cls.results[case["file"]] = item_ocr.scan_ritual_page(cls.images[case["file"]], OMEN_NAMES)

    @classmethod
    def tearDownClass(cls):
        """Restore the caller's profile."""
        for active in reversed(cls.patches):
            active.stop()
        cls.folder.cleanup()

    def test_every_page_keeps_whole_footprints_and_verified_names(self):
        """A hovered 2x3, an amulet beside a 2x3 armour, a 1x2 and a clipped page each read whole."""
        self.assertEqual(len(self.cases), 6)
        for case in self.cases:
            with self.subTest(page=case["file"]):
                result = self.results[case["file"]]
                self.assertTrue(result["grid_detected"])
                self.assertFalse(result["coverage_uncertain"])
                self.assertEqual([row["grid_slots"] for row in result["items"]], case["footprints"])
                names = {str(row["grid_slots"][0]): row["name"] for row in result["items"] if row["name"]}
                self.assertEqual(names, case["names"])

    def test_hovered_reward_is_one_item_named_without_its_highlight(self):
        """Frostbreath under the cursor is one 2x3 reward, not six unnamed cells."""
        rewards = ritual_grid.detect_reward_grid(self.images["123830.png"])["rewards"]
        hovered = [reward for reward in rewards if reward.get("hovered")]
        self.assertEqual([reward["slots"] for reward in hovered], [[1, 2, 13, 14, 25, 26]])
        row = self.results["123830.png"]["items"][0]
        self.assertEqual((row["name"], row["name_needs_review"]), ("Frostbreath", False))

    def test_captures_ending_inside_the_grid_frame_read_the_same_rewards(self):
        """Up to a tenth of a cell may be cut from any side without losing or splitting rewards."""
        image = self.images["124949.png"]
        full = ritual_grid.detect_reward_grid(image)
        expected = [reward["slots"] for reward in full["rewards"]]
        left, top = (round(value) for value in ritual_grid._locate(np.asarray(image), None)["origin"])
        pitch = full["pitch"]
        right, bottom = round(left + 12 * pitch), round(top + 10 * pitch)
        for side, box in (("left", (left + 4, 0, image.width, image.height)),
                          ("top", (0, top + 4, image.width, image.height)),
                          ("right", (0, 0, right - 4, image.height)),
                          ("bottom", (0, 0, image.width, bottom - 4))):
            with self.subTest(side=side):
                clipped = ritual_grid.detect_reward_grid(image.crop(box))
                self.assertIsNotNone(clipped)
                self.assertEqual([reward["slots"] for reward in clipped["rewards"]], expected)
        # A cut deeper than that could hide artwork, so the page is not claimed complete.
        self.assertIsNone(ritual_grid.detect_reward_grid(image.crop((0, 0, image.width, bottom - 8))))

    def test_complete_pages_with_nothing_to_review_save_despite_leftover_text(self):
        """Tooltip prose or a Tribute counter left over beside a verified grid no longer holds a clean page."""
        names = list(OMEN_NAMES) + [name for case in self.cases for name in case["names"].values()]
        result = dict(self.results["124949.png"], unmatched=["8,770 TRIBUTE", "STACK SIZE: 1/10"])
        self.assertTrue(clear_ritual_read(result, names))
        self.assertFalse(clear_ritual_read(dict(result, coverage_uncertain=True), names))
        self.assertFalse(clear_ritual_read(dict(result, grid_detected=False), names))


class NotRitualRewardTests(unittest.TestCase):
    """Art matching a reward Ritual never offers is held, whatever matcher named it."""

    def scan(self, answer):
        """Scan a real page with every catalog and learned-reference lookup returning ``answer``."""
        with tempfile.TemporaryDirectory(prefix="poe2-not-ritual-") as folder, \
                patch.object(store, "DATA_DIR", Path(folder)), patch.object(logger, "_READY", False), \
                patch.object(item_ocr, "_ritual_reward_icon", return_value=answer), \
                Image.open(FIXTURES / "124949.png") as source:
            return item_ocr.scan_ritual_page(source.convert("RGB"), OMEN_NAMES)["items"]

    def test_origin_spark_match_is_held_unnamed_for_review(self):
        """A confident Origin Spark match becomes an unnamed reward that blocks automatic saving."""
        items = self.scan(("Origin Spark", .95, []))
        held = [item for item in items if item["source"] == "Occupied Ritual reward; name unresolved"]
        self.assertTrue(held)
        self.assertNotIn("Origin Spark", [item["name"] for item in items])
        for item in held:
            self.assertTrue(item["needs_review"] and item["name_needs_review"])

    def test_origin_spark_is_never_suggested(self):
        """Suggestions keep the other names and drop Origin Spark."""
        items = self.scan((None, .5, ["Origin Spark", "Orb of Chance"]))
        suggested = [item["candidate_names"] for item in items if item.get("candidate_names")]
        self.assertTrue(suggested)
        self.assertTrue(all(names == ["Orb of Chance"] for names in suggested))

    def test_catalog_exclusions_are_held_and_bounty_rewards_stay_possible(self):
        """Sourced boss-only, Abyss and removed names are held; Foretold Bounty names are not."""
        catalog = json.loads(ritual_catalog.CATALOG.read_text(encoding="utf-8"))
        excluded = {row["name"] for row in catalog["exclusions"]}
        self.assertEqual(NOT_RITUAL_REWARDS, excluded | ritual_catalog.PLAYER_CONFIRMED_EXCLUSIONS)
        # Every Distilled Emotion is a real catalog item; Liquid Verisium is not an emotion.
        reader_names = {name for members in currency_ocr.get_reader().inventory_members.values() for name in members}
        liquids = {name for name in reader_names if "Liquid " in name} - {"Liquid Verisium"}
        self.assertEqual(len(liquids), 26)
        self.assertLessEqual(liquids, NOT_RITUAL_REWARDS)
        self.assertNotIn("Liquid Verisium", NOT_RITUAL_REWARDS)
        self.assertFalse(NOT_RITUAL_REWARDS & {row["name"] for row in catalog["guaranteed_rewards"]})
        for name in ("Ab Aeterno", "Omen of Light", "Omen of Recombination", "Potent Liquid Contempt"):
            with self.subTest(name=name):
                self.assertNotIn(name, [item["name"] for item in self.scan((name, .95, []))])
        self.assertIn("Headhunter", [item["name"] for item in self.scan(("Headhunter", .95, []))])

    def test_excluded_omens_are_neither_gray_rivals_nor_siblings(self):
        """The gray matcher's Omen groups leave out Abyss and removed Omens."""
        path = currency_ocr.ROOT / "inventory-icons.json"
        groups = item_ocr._ritual_omen_shapes(str(path), path.stat().st_mtime_ns, path.stat().st_size)[0]
        omens = {name for group in groups for name in group if name.startswith("Omen of ")}
        self.assertFalse(omens & NOT_RITUAL_REWARDS)
        self.assertIn("Omen of Bartering", omens)


class SpriteFootprintTests(unittest.TestCase):
    """Uniques the game sizes to their sprite join that footprint's artwork bank."""

    def test_class_wide_footprints_also_offer_the_sprite_footprint(self):
        """A 2x4 class footprint keeps its bank and adds the sprite's own size."""
        rows = {row["name"]: row["footprints"] for row in unique_items._catalog().items}
        self.assertEqual(rows["Frostbreath"], ((2, 3), (2, 4)))
        self.assertEqual(rows["Rondel de Ezo"], ((2, 2), (2, 4)))
        self.assertEqual(rows["Dusk Vigil"], ((1, 4), (2, 4)))
        self.assertEqual(rows["Wulfsbane"], ((2, 4),))
        self.assertEqual(rows["Titanrot Cataphract"], ((2, 3),))


if __name__ == "__main__":
    unittest.main()
