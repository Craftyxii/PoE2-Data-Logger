"""Scan Rite modifiers from actual atlas map tooltips into the current map's Rite stage."""

import copy
from pathlib import Path
import unittest

from PIL import Image

from tests import test_hotkey_presentation_retry as fixtures
from PoE2_Data_Logger.core import logger_store as logger, ritual_rites_catalog
from PoE2_Data_Logger.ocr.item_ocr import ocr_lines
from PoE2_Data_Logger.ocr.item_text import read_rite_tooltip

FIXTURES = Path(__file__).parent / "fixtures/rite_tooltips"
EXPECTED = {
    "ravine.png": ["Rerolling Favours at Ritual Altars in Area costs 20% reduced Tribute",
                   "Foretold Proliferation: Monsters Sacrificed at Ritual Altars grant 25% increased Tribute"],
    "necropolis_wildwood.png": ["Rerolling Favours at Ritual Altars in Area costs 20% reduced Tribute",
                                "Foretold Proliferation: Rituals contain 1 additional pack of Wildwood Monsters"],
    "necropolis_bounty.png": ["Foretold Bounty: Omen of Sinistral Exaltation",
                              "Rerolling Favours at Ritual Altars in Area costs 20% reduced Tribute",
                              "Foretold Proliferation: Monsters Sacrificed at Ritual Altars grant 25% increased Tribute"],
}


def scan(name, edit=None):
    """Read one actual tooltip, optionally replacing OCR text to simulate unknown wording."""
    with Image.open(FIXTURES / name) as source:
        image = source.convert("RGB")
    rows = ocr_lines(image)
    if edit:
        rows = copy.deepcopy(rows)
        edit(rows)
    return read_rite_tooltip(rows, image)


class RiteTooltipParsingTests(unittest.TestCase):
    def test_actual_tooltips_rejoin_wrapped_lines_into_catalog_modifiers(self):
        for name, expected in EXPECTED.items():
            with self.subTest(name=name):
                result = scan(name)
                self.assertEqual(result["kind"], "rite")
                self.assertEqual([ritual_rites_catalog.affix(row["id"])["text"] for row in result["affixes"]],
                                 expected)
                self.assertTrue(all(row["count"] == 1 for row in result["affixes"]))

    def test_unknown_wording_is_added_as_custom_modifiers_of_the_labelled_kind(self):
        def edit(rows):
            rows[2]["text"] = "FORETOLD BOUNTY: THE NEWEST UNIQUE RING"
            rows[3]["text"] = "RITUAL ALTARS IN AREA SPAWN ADDITIONAL TORMENTED"
            rows[4]["text"] = "SPIRITS"
        affixes = ritual_rites_catalog.normalize_affixes(scan("necropolis_bounty.png", edit)["affixes"])
        self.assertEqual([(row["text"], row["kind"], row["custom"]) for row in affixes], [
            ("Foretold Bounty: The Newest Unique Ring", "bounty", True),
            ("Ritual Altars in Area spawn additional tormented spirits", "local", True),
            ("Foretold Proliferation: Monsters Sacrificed at Ritual Altars grant 25% increased Tribute",
             "proliferation", False)])

    def test_other_tooltips_are_not_rite_scans(self):
        rows = [{"text": text, "x": 0, "y": index * 20, "right": 300, "bottom": index * 20 + 18}
                for index, text in enumerate(("WAYSTONE (TIER 15)", "WAYSTONE TIER: 15", "ITEM RARITY: +40%",
                                              "REQUIRES: LEVEL 65"))]
        self.assertIsNone(read_rite_tooltip(rows, Image.new("RGB", (300, 100))))
        # A map without Ritual modifiers is not a Rite stage either.
        no_rite = scan("ravine.png", lambda rows: [row.update(text="AREA HAS PATCHES OF BURNING GROUND")
                                                   for row in rows[2:6]])
        self.assertIsNone(no_rite)


class RiteTooltipStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.HotkeyPresentationRetryTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.HotkeyPresentationRetryTests(
            "test_opened_result_and_png_stay_owned_until_writer_releases")
        self.fixture.setUp()
        self.window = self.fixture.window

    def tearDown(self):
        self.fixture.tearDown()

    def test_scans_start_a_rite_replace_the_stage_and_new_map_advances_it(self):
        self.assertIsNone(logger.ritual_rite_state()["selected_rite_id"])
        self.window._rite_read(scan("ravine.png"))
        self.window._rite_read(scan("necropolis_bounty.png"))
        first = logger.ritual_rite_state()
        self.assertTrue(first["selected_rite_id"])
        self.assertEqual(first["map_ordinal"], 1)
        # Rescanning a map replaces its modifiers rather than adding duplicates.
        self.assertEqual([row["text"] for row in first["current_affixes"]], EXPECTED["necropolis_bounty.png"])
        logger.finish_map(1, 2, 3)  # + New map closes this stage.
        self.window._rite_read(scan("necropolis_wildwood.png"))
        second = logger.ritual_rite_state()
        self.assertEqual((second["selected_rite_id"], second["map_ordinal"]), (first["selected_rite_id"], 2))
        self.assertNotEqual(second["map_id"], first["map_id"])
        self.assertEqual([row["text"] for row in second["current_affixes"]], EXPECTED["necropolis_wildwood.png"])
        self.assertEqual([source["text"] for source in second["inherited_sources"]],
                         ["Monsters Sacrificed at Ritual Altars grant 25% increased Tribute"])


if __name__ == "__main__":
    unittest.main()
