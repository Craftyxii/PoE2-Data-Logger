"""Scan Rite modifiers from actual atlas map tooltips into the current map's Rite stage."""

import copy
import csv
import io
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

    def test_rites_stay_inactive_unless_ritual_is_the_selected_activity(self):
        logger.save_settings({"activity_type": "Expedition"})
        with self.assertRaisesRegex(ValueError, "Activity Type is Ritual"):
            self.window._rite_read(scan("ravine.png"))
        self.assertEqual(logger.ritual_rite_state()["available_rites"], [])
        # An already selected Rite does not take a stage from a non-Ritual map.
        logger.save_settings({"activity_type": "Ritual"})
        self.window._rite_read(scan("ravine.png"))
        logger.finish_map(1, 2, 3)
        logger.start_map()
        logger.save_settings({"activity_type": "Expedition"})
        idle = logger.ritual_rite_state()
        self.assertTrue(idle["selected_rite_id"])
        self.assertFalse(idle["ritual_activity"])
        self.assertEqual((idle["map_ordinal"], idle["inherited_sources"]), (None, []))
        logger.finish_map(1, 2, 3)
        logger.save_settings({"activity_type": "Ritual"})
        resumed = logger.ritual_rite_state()
        self.assertEqual(resumed["map_ordinal"], 2)
        self.assertEqual(len(resumed["inherited_sources"]), 1)

    def test_scans_start_a_rite_replace_the_stage_and_new_map_advances_it(self):
        logger.save_settings({"activity_type": "Ritual"})
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

    def test_waystone_setup_saved_after_map_start_keeps_the_rite_stage_in_exports(self):
        """Saving the new map's waystone rebuilds its setup without dropping the live Rite stage."""
        logger.save_settings({"activity_type": "Ritual"})
        self.window._rite_read(scan("ravine.png"))
        logger.finish_map(1, 2, 3)
        logger.start_map()
        self.window._rite_read(scan("necropolis_wildwood.png"))
        logger.save_settings({"tier": 16, "waystone": 0, "map_mods": 0})

        def rite_columns():
            rows = csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig")))
            return {row["Map ID"]: (row["Tier"], row["Ritual Rite Name"], row["Ritual Rite Map Ordinal"])
                    for row in rows}
        self.assertEqual(rite_columns()["M0002"], ("16", "Rite 1", "2"))
        logger.save_settings({"activity_type": "Expedition"})
        self.assertEqual(rite_columns()["M0002"], ("16", "", ""))


if __name__ == "__main__":
    unittest.main()
