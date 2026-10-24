"""Scan Rite modifiers from actual atlas map tooltips into the current map's Rite stage."""

import copy
import csv
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtWidgets import QMessageBox

from tests import test_hotkey_presentation_retry as fixtures
from PoE2_Data_Logger.core import logger_store as logger, ritual_rites_catalog
from PoE2_Data_Logger.ocr.item_ocr import ocr_lines
from PoE2_Data_Logger.ocr.item_text import read_rite_tooltip
from PoE2_Data_Logger.ui.native_desktop import value

FIXTURES = Path(__file__).parent / "fixtures/rite_tooltips"
FONT = Path(__file__).parents[1] / "PoE2_Data_Logger/fonts/DejaVuSans-Bold.ttf"
EXPECTED = {
    "ravine.png": ["Rerolling Favours at Ritual Altars in Area costs 20% reduced Tribute",
                   "Foretold Proliferation: Monsters Sacrificed at Ritual Altars grant 25% increased Tribute"],
    "necropolis_wildwood.png": ["Rerolling Favours at Ritual Altars in Area costs 20% reduced Tribute",
                                "Foretold Proliferation: Rituals contain 1 additional pack of Wildwood Monsters"],
    "necropolis_bounty.png": ["Foretold Bounty: Omen of Sinistral Exaltation",
                              "Rerolling Favours at Ritual Altars in Area costs 20% reduced Tribute",
                              "Foretold Proliferation: Monsters Sacrificed at Ritual Altars grant 25% increased Tribute"],
    "savannah.png": ["Foretold Bounty: 4 Chaos Orbs"],
    "seepage.png": ["Foretold Bounty: Omen of Dextral Exaltation"],
    "channel.png": ["Foretold Proliferation: Rerolling Favours at Ritual Altars in your Maps costs 20% reduced Tribute"],
    "oasis.png": ["Foretold Proliferation: Rerolling Favours at Ritual Altars in your Maps costs 20% reduced Tribute"],
}
# Each tooltip's title and Biome line, as the game shows them.
MAPS = {"ravine.png": ("Ravine", "Forest"), "necropolis_wildwood.png": ("Necropolis", "Ezomyte City"),
        "necropolis_bounty.png": ("Necropolis", "Ezomyte City"), "savannah.png": ("Savannah", "Grass"),
        "seepage.png": ("Seepage", "Grass"), "channel.png": ("Channel", "Faridun City"),
        "oasis.png": ("Oasis", "Faridun City")}


def texts(affixes):
    """Show saved Rite modifiers as their catalog wording."""
    return [ritual_rites_catalog.affix(row["id"])["text"] if row.get("id") else row["text"] for row in affixes]


def full_window(name, fps="94 FPS"):
    """Place an actual tooltip in a game-window capture, as the hotkey grabs it, with an FPS counter."""
    with Image.open(FIXTURES / name) as source:
        tooltip = source.convert("RGB")
    frame = Image.new("RGB", (1920, 1080), (18, 16, 14))
    frame.paste(tooltip, (760, 300))
    ImageDraw.Draw(frame).text((1830, 8), fps, font=ImageFont.truetype(str(FONT), 18), fill=(235, 220, 60))
    return frame


def scan_window(name, fps="94 FPS"):
    """Read one actual tooltip from a whole game-window capture."""
    image = full_window(name, fps)
    return read_rite_tooltip(ocr_lines(image), image)


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

    def test_whole_window_capture_names_the_map_from_its_title_not_other_screen_text(self):
        """The hotkey grabs the game window; an FPS counter read first is not the map's name."""
        for name, (title, biome) in MAPS.items():
            with self.subTest(name=name):
                result = scan_window(name)
                self.assertEqual((result["map_name"], result["biome"]), (title, biome))
                self.assertEqual(result["affixes"], scan(name)["affixes"])
                self.assertEqual((scan(name)["map_name"], scan(name)["biome"]), (title, biome))

    def test_tooltip_biomes_map_to_the_biome_and_city_type_fields(self):
        """A city map sets City Type, leaving its biome to the Atlas; unknown wording sets nothing."""
        self.assertEqual(logger._rite_biome_settings("Grass"), {"biome": "Grass", "city_type": "None"})
        self.assertEqual(logger._rite_biome_settings("Forest"), {"biome": "Forest", "city_type": "None"})
        self.assertEqual(logger._rite_biome_settings("Faridun City"), {"city_type": "Faridun", "biome": "None"})
        self.assertEqual(logger._rite_biome_settings("Ezomyte City"), {"city_type": "Ezomyte", "biome": "None"})
        for unknown in ("", "Gras5", "Atlantis City", "None"):
            with self.subTest(unknown=unknown):
                self.assertEqual(logger._rite_biome_settings(unknown), {})

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

    def test_scans_queue_a_rites_maps_in_order_and_each_new_map_plays_the_next(self):
        """Scan a Rite's maps one after another, as Tablets fill slots, with no other input."""
        logger.save_settings({"activity_type": "Ritual"})
        self.assertIsNone(logger.ritual_rite_state()["selected_rite_id"])
        for name in ("ravine.png", "necropolis_bounty.png", "necropolis_wildwood.png", "necropolis_bounty.png"):
            self.window._rite_read(scan(name))
        first = logger.ritual_rite_state()
        self.assertTrue(first["selected_rite_id"])
        self.assertEqual(first["map_ordinal"], 1)
        self.assertEqual(texts(first["current_affixes"]), EXPECTED["ravine.png"])
        # The same map scanned again replaces its stage; two Necropolis maps stay two stages.
        self.assertEqual([(stage["map_ordinal"], stage["map_name"], texts(stage["affixes"]))
                          for stage in first["queued_stages"]],
                         [(2, "Necropolis", EXPECTED["necropolis_bounty.png"]),
                          (3, "Necropolis", EXPECTED["necropolis_wildwood.png"])])
        logger.finish_map(1, 2, 3)  # + New map plays the next queued stage.
        logger.start_map()
        second = logger.ritual_rite_state()
        self.assertEqual((second["map_ordinal"], second["current_from_queue"]), (2, True))
        self.assertEqual(texts(second["current_affixes"]), EXPECTED["necropolis_bounty.png"])
        self.assertEqual([source["text"] for source in second["inherited_sources"]],
                         ["Monsters Sacrificed at Ritual Altars grant 25% increased Tribute"])
        logger.finish_map(1, 2, 3)
        logger.start_map()
        # A map played outside Ritual leaves the remaining stage queued for the next Ritual map.
        logger.save_settings({"activity_type": "Expedition"})
        self.assertFalse(logger.ritual_rite_state()["ritual_activity"])
        logger.finish_map(1, 2, 3)
        logger.start_map()
        logger.save_settings({"activity_type": "Ritual"})
        third = logger.ritual_rite_state()
        self.assertEqual((third["map_ordinal"], texts(third["current_affixes"]), third["queued_stages"]),
                         (3, EXPECTED["necropolis_wildwood.png"], []))
        rows = {row["Map ID"]: (row["Ritual Rite Map Ordinal"], row["Ritual Rite Affixes JSON"])
                for row in csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig")))}
        self.assertEqual([rows[map_id][0] for map_id in ("M0001", "M0002", "M0003", "M0004")], ["1", "2", "", "3"])
        self.assertIn("Foretold Bounty: Omen of Sinistral Exaltation", rows["M0002"][1])
        self.assertIn("Wildwood", rows["M0004"][1])

    def test_queued_scans_undo_and_clear_like_other_saved_actions(self):
        """Undo removes the latest queued scan; Clear queued stages removes the rest."""
        logger.save_settings({"activity_type": "Ritual"})
        for name in ("ravine.png", "necropolis_bounty.png", "necropolis_wildwood.png"):
            self.window._rite_read(scan(name))
        self.assertEqual(len(logger.ritual_rite_state()["queued_stages"]), 2)
        table = self.window.ritual_rite_queue_table
        self.assertEqual([(table.item(row, 0).text(), table.item(row, 1).text()) for row in range(table.rowCount())],
                         [("2", "Necropolis · Ezomyte City"), ("3", "Necropolis · Ezomyte City")])
        self.assertEqual(logger.undo_last_action()["action"], "Ritual Rite scan")
        self.assertEqual([texts(stage["affixes"]) for stage in logger.ritual_rite_state()["queued_stages"]],
                         [EXPECTED["necropolis_bounty.png"]])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.clear_ritual_rite_queue()
        state = logger.ritual_rite_state()
        self.assertEqual((state["queued_stages"], texts(state["current_affixes"])), ([], EXPECTED["ravine.png"]))

    def test_new_map_shows_the_biome_of_the_rite_map_it_plays_ready_to_correct(self):
        """Scanned ahead from whole-window captures, each + New map sets and shows that map's biome."""
        logger.save_settings({"activity_type": "Ritual", "biome": "Mountain"})
        self.window.refresh()
        for name, fps in (("savannah.png", "94 FPS"), ("channel.png", "98 FPS"), ("ravine.png", "81 FPS"),
                          ("ravine.png", "85 FPS")):
            self.window._rite_read(scan_window(name, fps))
        # Ravine rescanned under another FPS reading replaces its stage instead of adding one.
        table = self.window.ritual_rite_queue_table
        self.assertEqual([(table.item(row, 0).text(), table.item(row, 1).text()) for row in range(table.rowCount())],
                         [("2", "Channel · Faridun City"), ("3", "Ravine · Forest")])
        shown = []
        for _ in range(2):
            self.window.finish_map()
            shown.append((value(self.window.header_biome), value(self.window.header_city_type),
                          self.window.statusBar().currentMessage()))
        self.assertEqual(shown[0][:2], ("None", "Faridun"))
        self.assertIn("Biome from Rite stage 2 (Channel): Faridun City.", shown[0][2])
        self.assertEqual(shown[1][:2], ("Forest", "None"))
        self.assertIn("Biome from Rite stage 3 (Ravine): Forest.", shown[1][2])
        self.assertIn("This map plays stage 3 (Ravine · Forest)", self.window.ritual_rite_queue_note.text())
        # The map that was current when scanning keeps the biome it was given.
        rows = {row["Map ID"]: row for row in csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig")))}
        self.assertEqual(rows["M0001"]["Biome"], "Mountain")
        # A wrong biome is corrected in the header and saved on the new map.
        self.window.header_biome.setCurrentText("Swamp")
        rows = {row["Map ID"]: row for row in csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig")))}
        self.assertEqual((rows["M0002"]["City Type"], rows["M0003"]["Biome"]), ("Faridun", "Swamp"))

    def test_maps_sharing_a_biome_stay_separate_stages(self):
        """Biome never identifies a stage: Savannah and Seepage are both Grass and stay two stages."""
        logger.save_settings({"activity_type": "Ritual"})
        for name in ("ravine.png", "savannah.png", "seepage.png", "savannah.png"):
            self.window._rite_read(scan_window(name))
        table = self.window.ritual_rite_queue_table
        self.assertEqual([(table.item(row, 0).text(), table.item(row, 1).text()) for row in range(table.rowCount())],
                         [("2", "Savannah · Grass"), ("3", "Seepage · Grass")])
        for ordinal, name in ((2, "savannah.png"), (3, "seepage.png")):
            self.window.finish_map()
            state = logger.ritual_rite_state()
            self.assertEqual((state["map_ordinal"], state["current_map_name"], texts(state["current_affixes"])),
                             (ordinal, MAPS[name][0], EXPECTED[name]))
            self.assertEqual(value(self.window.header_biome), "Grass")

    def test_city_stage_takes_its_city_type_and_the_atlas_keystone_biome(self):
        """A Faridun City stage sets City Type Faridun and, with the keystone allocated, its Atlas biome."""
        from tests.test_atlas_biome_sync import RULES
        from PoE2_Data_Logger.core.atlas_catalog import catalog
        logger.save_settings({"activity_type": "Ritual", "biome": "Mountain"})
        self.window._rite_read(scan_window("ravine.png"))  # the current map's own stage
        for (_, node, choice, biome), name in zip(RULES[:2], ("channel.png", "oasis.png")):
            with self.subTest(biome=biome):
                logger.save_atlas_settings({"catalog_version": catalog()["version"], "allocated": [node],
                                            "choices": {node: choice}, "gear_item_rarity": None})
                self.window._rite_read(scan_window(name))
                self.window.finish_map()
                self.assertEqual(logger.ritual_rite_state()["current_map_name"], MAPS[name][0])
                self.assertEqual((value(self.window.header_city_type), value(self.window.header_biome)),
                                 ("Faridun", biome))
                self.assertEqual((value(self.window.city_type), value(self.window.biome)), ("Faridun", biome))
                settings = logger.get_state()["settings"]
                self.assertEqual((settings["city_type"], settings["biome"]), ("Faridun", biome))
        # Without the keystone a city map has no biome of its own.
        logger.save_atlas_settings({"catalog_version": catalog()["version"], "allocated": [],
                                    "choices": {}, "gear_item_rarity": None})
        self.window._rite_read(scan_window("channel.png"))
        self.window.finish_map()
        self.assertEqual(logger.ritual_rite_state()["current_map_name"], "Channel")
        self.assertEqual((value(self.window.header_city_type), value(self.window.header_biome)), ("Faridun", "None"))

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
