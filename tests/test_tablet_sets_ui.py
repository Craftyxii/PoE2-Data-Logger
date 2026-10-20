"""Saved tablet sets retain independent forms, scan slots and completion ownership."""

import copy
import json
import unittest
from unittest.mock import patch

from tests import test_hotkey_presentation_retry as fixtures
from PoE2_Data_Logger.core import logger_store as logger, service
from PoE2_Data_Logger.ui import native_desktop


class TabletSetsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.HotkeyPresentationRetryTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.HotkeyPresentationRetryTests(
            "test_opened_result_and_png_stay_owned_until_writer_releases")
        self.fixture.setUp()
        self.window = self.fixture.window
        self.first_id = logger.get_state()["selected_tablet_set_id"]

    def tearDown(self):
        self.fixture.tearDown()

    def select(self, set_id):
        self.window.tablet_set.setCurrentIndex(self.window.tablet_set.findData(set_id))
        self.assertEqual(logger.get_state()["selected_tablet_set_id"], set_id)

    def reading(self, base=10):
        return {"kind": "tablet", "source": "clipboard", "mods": [
            f"{base}% increased Rarity of Items found in Map",
            f"Map contains {base + 1} additional Summoning Circles",
            f"{base + 2}% increased Quantity of Waystones found in Map",
            f"Map is inhabited by {base + 3} additional Rogue Exiles"],
            "matches": [], "uncertain": [], **logger.scan_context()}

    def scan_full_set(self, base):
        self.window.set_auto_tablets(True)
        for number in range(4):
            self.window._hover_item_read(self.reading(base + number * 4), self.fixture.raw)
            self.assertEqual(logger.tablet_next_slot(), number + 2)
        config = copy.deepcopy(logger.get_state()["settings"])
        self.assertEqual(config["tablets_used"], 4)
        self.assertEqual(len(config["tablet_affixes"]), 16)
        self.assertTrue(all(item["affix"] for item in config["tablet_affixes"]))
        self.assertEqual([item.get("unit", "%") for item in config["tablet_affixes"]], ["%", "count", "%", "count"] * 4)
        self.assertTrue(all(len(mods) == 4 for mods in config["tablet_raw_mods"]))
        return config

    def map_snapshot(self, map_id="M0001"):
        with logger._connect() as db:
            return json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()[0])

    def test_two_full_sets_switch_update_and_restart_without_rescanning(self):
        first = self.scan_full_set(10)
        self.window.new_tablet_set()
        second_id = logger.get_state()["selected_tablet_set_id"]
        self.assertNotEqual(first_id := self.first_id, second_id)
        self.assertEqual(self.window.tablet_set.currentText(), "Set 2")
        second = self.scan_full_set(70)
        self.select(first_id)
        self.assertEqual(logger.tablet_next_slot(), 5)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"], first["tablet_affixes"])
        self.assertEqual(self.window._tablet_form()[1][3][1], tuple(first["tablet_raw_mods"][3]))
        self.window.tablet_values[15].setText("99")
        self.window.save_tablets()
        self.select(second_id)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"], second["tablet_affixes"])
        self.assertEqual(logger.tablet_next_slot(), 5)
        with patch.object(native_desktop.QInputDialog, "getText", return_value=("Ritual tablets", True)):
            self.window.rename_tablet_set()
        self.assertEqual(self.window.tablet_set.currentText(), "Ritual tablets")
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        logger._READY = False
        logger.initialize()
        self.fixture.window = self.window = native_desktop.LoggerWindow()
        self.window._poll.stop()
        self.assertEqual(self.window.tablet_set.count(), 2)
        self.assertEqual(self.window.tablet_set.currentData(), second_id)
        self.assertEqual(self.window.tablet_set.currentText(), "Ritual tablets")
        self.assertEqual(logger.get_state()["settings"]["tablet_raw_mods"], second["tablet_raw_mods"])
        self.select(first_id)
        self.assertEqual(self.window.tablet_values[15].text(), "99")
        self.assertEqual(self.window.tablet_values[15].property("unit"), "count")
        self.assertEqual(logger.tablet_next_slot(), 5)

    def test_each_unsaved_form_survives_switch_refresh_and_other_set_save(self):
        native_desktop.select(self.window.tablets_used, 2)
        native_desktop.select(self.window.tablet_affixes[0], "Pack Size")
        self.window.tablet_values[0].setText("33.5")
        self.window.tablet_raw_mods[0] = ["first unsaved raw modifier"]
        first_draft = self.window._tablet_form()
        self.window.new_tablet_set()
        second_id = logger.get_state()["selected_tablet_set_id"]
        native_desktop.select(self.window.tablets_used, 3)
        native_desktop.select(self.window.tablet_affixes[15], "Map contains additional Summoning Circles")
        self.window.tablet_values[15].setText("7")
        self.window.tablet_raw_mods[3] = ["second unsaved raw modifier"]
        second_draft = self.window._tablet_form()
        for _ in range(2):
            self.window.refresh()
            self.assertEqual(self.window._tablet_form(), second_draft)
            self.select(self.first_id)
            self.assertEqual(self.window._tablet_form(), first_draft)
            self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][0]["affix"], "")
            self.select(second_id)
        self.window.save_tablets()
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][15]["value"], 7)
        self.select(self.first_id)
        self.assertEqual(self.window._tablet_form(), first_draft)
        self.window.save_tablets()
        self.assertEqual(logger.get_state()["settings"]["tablets_used"], 2)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][0]["value"], 33.5)
        self.assertEqual(logger.get_state()["settings"]["tablet_raw_mods"][0], ["first unsaved raw modifier"])

    def test_pending_review_retains_its_set_and_late_result_cannot_touch_another(self):
        original = self.reading()
        self.window._hover_item_read(original, self.fixture.raw)
        draft = self.window._tablet_form()
        self.assertEqual(self.window._pending_tablet_set_id, self.first_id)
        self.window.new_tablet_set()
        second_id = logger.get_state()["selected_tablet_set_id"]
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        with self.assertRaisesRegex(ValueError, "tablet set changed"):
            self.window.commit_review()
        with self.assertRaisesRegex(ValueError, "tablet set changed"):
            self.window._hover_item_read(original, self.fixture.raw)
        with patch.object(self.window, "_prepare_tablet_modifiers") as prepare:
            with self.assertRaisesRegex(ValueError, "tablet set changed"):
                self.window._prepare_hotkey_presentation({"mode": "item", "result": original, "error": ""})
        prepare.assert_not_called()
        self.assertEqual(logger.tablet_next_slot(), 1)
        self.assertTrue(all(not item["affix"] for item in logger.get_state()["settings"]["tablet_affixes"]))
        native_desktop.select(self.window.tablet_affixes[0], "Pack Size")
        self.window.tablet_values[0].setText("77")
        self.window.save_tablets()
        self.assertEqual(self.window._pending_tablet_set_id, self.first_id)
        self.assertEqual(self.window._pending_tablet_slot, 1)
        self.window.clear_tablets()
        self.assertEqual(self.window._pending_tablet_set_id, self.first_id)
        self.assertEqual(self.window._pending_tablet_slot, 1)
        self.select(self.first_id)
        self.assertEqual(self.window._tablet_form(), draft)
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.window.commit_review()
        self.assertEqual(logger.tablet_next_slot(), 2)
        self.select(second_id)
        self.assertEqual(logger.tablet_next_slot(), 1)

    def test_reject_original_review_from_another_set_keeps_that_sets_draft(self):
        self.window._hover_item_read(self.reading(), self.fixture.raw)
        self.window.new_tablet_set()
        second_id = logger.get_state()["selected_tablet_set_id"]
        native_desktop.select(self.window.tablet_affixes[4], "Pack Size")
        self.window.tablet_values[4].setText("88")
        second_draft = self.window._tablet_form()
        self.window.reject_review()
        self.assertEqual(self.window._tablet_form(), second_draft)
        self.select(self.first_id)
        self.assertEqual(self.window.tablet_values[0].text(), "")
        self.assertEqual(self.window.tablet_raw_mods[0], [])
        self.select(second_id)
        self.assertEqual(self.window._tablet_form(), second_draft)

    def test_new_map_attaches_saved_values_and_discards_all_sets_unsaved_edits(self):
        native_desktop.select(self.window.tablet_affixes[0], "Pack Size")
        self.window.tablet_values[0].setText("27")
        self.window.save_tablets()
        self.window.tablet_values[0].setText("99")
        self.window.new_tablet_set()
        second_id = logger.get_state()["selected_tablet_set_id"]
        native_desktop.select(self.window.tablet_affixes[0], "Pack Size")
        self.window.tablet_values[0].setText("41")
        self.window.save_tablets()
        self.window.tablet_values[0].setText("88")
        self.window.finish_map()
        self.assertEqual(self.map_snapshot()["tablet_affixes"][0]["value"], 41)
        self.assertEqual(self.map_snapshot()["tablet_set_id"], second_id)
        self.assertEqual(self.window.tablet_values[0].text(), "41")
        self.select(self.first_id)
        self.assertEqual(self.window.tablet_values[0].text(), "27")
        self.assertEqual(self.window.tablet_set.count(), 2)

    def test_only_selected_saved_set_attaches_when_existing_new_map_action_finishes_map(self):
        first = self.scan_full_set(10)
        self.window.new_tablet_set()
        second_id = logger.get_state()["selected_tablet_set_id"]
        second = self.scan_full_set(70)
        before = self.map_snapshot()
        self.window.normal.setText("101")
        self.window.magic.setText("22")
        self.window.rare.setText("3")
        self.window.unique.setText("1")
        self.window.save_counts()
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assertFalse(logger.get_state()["pending_new_map"])
        self.assertEqual(self.map_snapshot(), before)
        self.select(self.first_id)
        self.select(second_id)
        self.assertEqual(self.map_snapshot(), before)
        with patch.object(service, "dispatch", wraps=service.dispatch) as dispatch:
            self.window.finish_map()
        dispatch.assert_called_once_with("/api/finish-map", {"normal": "101", "magic": "22", "rare": "3", "unique": "1"})
        completed = self.map_snapshot()
        self.assertEqual(completed["tablet_set_id"], second_id)
        self.assertEqual(completed["tablet_affixes"], [{**item, "unit": item.get("unit", "%")}
                                                     for item in second["tablet_affixes"]])
        self.assertEqual(completed["tablet_raw_mods"], second["tablet_raw_mods"])
        self.assertNotEqual(completed["tablet_affixes"], first["tablet_affixes"])
        self.assertEqual(len(completed["tablet_affixes"]), 16)
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        with logger._connect() as db:
            self.assertEqual(json.loads(db.execute("SELECT kills_json FROM maps WHERE map_id='M0001'").fetchone()[0]), [101, 22, 3])


if __name__ == "__main__":
    unittest.main()
