"""Qt refresh/navigation checks preserving unsaved map, tablet, kill and chain fields within their owning context."""

import csv
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr.item_text import parse_item_text
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select, value


class UIFormDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-form-drafts-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.save_settings({"tablets_used": 1})
        self.window = LoggerWindow()
        self.window._poll.stop()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def counts(self):
        return tuple(field.text() for field in (self.window.normal, self.window.magic, self.window.rare))

    def enter_counts(self, normal="1234", magic="56", rare="7", detonated=""):
        for field, text in zip((self.window.normal, self.window.magic, self.window.rare), (normal, magic, rare)):
            field.setText(text)
        if detonated:
            logger.save_detonated(detonated)

    def enter_waystone(self):
        select(self.window.tier, 16)
        for field, text in ((self.window.waystone, "132.5"), (self.window.map_mods, "2"),
                            (self.window.item_rarity, "75"), (self.window.monster_rarity, "25"),
                            (self.window.pack_size, "10"), (self.window.effectiveness, "90"),
                            (self.window.waystone_name, "Draft waystone"),
                            (self.window.waystone_mod_fields[0], "First draft modifier")):
            field.setText(text)
        self.window._extra_waystone_mods = ["Additional draft modifier"]
        if self.window.aldur.count() > 1:
            self.window.aldur.setCurrentIndex(1)
        return self.window._waystone_form()

    def held_waystone(self):
        result = parse_item_text("""Item Class: Waystones
Rarity: Rare
Draft Waystone
Waystone (Tier 16)
Waystone Drop Chance: +85%
--------
Monsters have 20% increased maximum Life
""")
        result.update(logger.scan_context())
        self.window._hover_item_read(result)
        self.assertEqual(self.window.pending_review_kind, "waystone")
        self.window.map_mods.setText("1")
        return result

    def enter_tablet(self, number=1, amount="25"):
        start = (number - 1) * 4
        select(self.window.tablets_used, max(number, int(value(self.window.tablets_used))))
        select(self.window.tablet_affixes[start], "Pack Size")
        self.window.tablet_values[start].setText(amount)
        self.window.tablet_raw_mods[number - 1] = [f"{amount}% increased Pack Size"]

    def scan_tablet(self, amount=30):
        return self.window._tablet_read(1, {"kind": "tablet", "source": "clipboard",
            "mods": [f"{amount}% increased Pack Size"], "matches": [], "uncertain": [],
            **logger.scan_context()})

    def test_chain_commit_preserves_map_kill_draft_until_map_finish_and_export(self):
        self.enter_counts()
        self.window.rune_inputs[0].setText("Death")
        self.window.commit_chain()
        self.assertEqual(self.window.state["current_expedition_id"], "M0001-E01")
        self.assertEqual(self.counts(), ("1234", "56", "7"))
        self.window.complete_chain()
        self.assertEqual(self.window.state["current_expedition_id"], "M0001-E02")
        self.assertEqual(self.counts(), ("1234", "56", "7"))
        self.window.finish_map()
        rows = list(csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig"))))
        closed = next(row for row in rows if row["Map ID"] == "M0001")
        self.assertEqual(tuple(closed[key] for key in ("Normal Kills", "Magic Kills", "Rare Kills")),
                         ("1234", "56", "7"))
        self.assertEqual(self.counts(), ("", "", ""))

    def test_count_drafts_survive_repeated_settings_refresh_and_clear_after_save(self):
        self.enter_counts("123 ", "4", "5", "6")
        self.window.save_active_master()
        self.window.set_auto_all(False)
        self.window.refresh()
        self.assertEqual(self.counts(), ("123 ", "4", "5"))
        self.assertEqual(logger.get_state()["detonated"], 6)
        self.window.save_counts()
        self.assertEqual(self.counts(), ("123", "4", "5"))
        logger.save_counts(456, 8, 9, 10)
        self.window.refresh()
        self.assertEqual(self.counts(), ("456", "8", "9"))
        self.assertEqual(logger.get_state()["detonated"], 10)

    def test_only_dirty_count_fields_override_new_persisted_values(self):
        self.window.normal.setText("150")
        logger.save_counts(10, 20, 30, 4)
        self.window.refresh()
        self.assertEqual(self.counts(), ("150", "20", "30"))
        self.assertEqual(logger.get_state()["detonated"], 4)

    def test_persisted_detonation_stays_with_expedition_while_kill_drafts_stay_with_map(self):
        self.enter_counts(detonated="8")
        self.window.save_active_master()
        self.assertEqual(logger.get_state()["detonated"], 8)
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.counts(), ("1234", "56", "7"))
        self.assertIsNone(logger.get_state()["detonated"])
        logger.save_counts(12, 3, 1, 4)
        self.window.refresh()
        self.assertEqual(logger.get_state()["detonated"], 4)

    def test_manual_waystone_draft_survives_other_settings_saves(self):
        draft = self.enter_waystone()
        self.window.save_active_master()
        self.window.save_counts()
        self.window.refresh()
        self.assertEqual(self.window._waystone_form(), draft)
        self.assertEqual(logger.get_state()["settings"]["waystone"], 0)
        self.window.save_map_settings()
        self.assertEqual(logger.get_state()["settings"]["waystone"], 132.5)
        logger.save_settings({"waystone": 33, "map_mods": 1})
        self.window.refresh()
        self.assertEqual(float(self.window.waystone.text()), 33)
        self.assertEqual(self.window.map_mods.text(), "1")

    def test_pending_waystone_reject_discards_fields_and_save_clears_draft(self):
        self.held_waystone()
        self.window.waystone.setText("0123.50")
        self.window.save_active_master()
        self.assertEqual(self.window.waystone.text(), "0123.50")
        self.window.reject_review()
        self.assertEqual(self.window.waystone.text(), "0")
        self.assertIsNone(self.window.pending_review_kind)
        self.held_waystone()
        self.window.waystone.setText("0123.50")
        self.window.save_map_settings()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(self.window.waystone.text(), "123.5")
        logger.save_settings({"waystone": 55})
        self.window.refresh()
        self.assertEqual(float(self.window.waystone.text()), 55)

    def test_new_map_and_undo_reload_their_own_waystone_and_count_values(self):
        self.window.waystone.setText("85")
        self.window.save_map_settings()
        self.enter_counts("12", "3", "1", "2")
        self.window.finish_map()
        self.assertEqual(self.window.state["current_map_id"], "M0002")
        self.assertEqual(self.window.waystone.text(), "0")
        self.assertEqual(self.counts(), ("", "", ""))
        self.enter_waystone()
        self.enter_counts("999", "88", "7")
        self.window.undo_map()
        self.assertEqual(self.window.state["current_map_id"], "M0001")
        self.assertEqual(float(self.window.waystone.text()), 85)
        self.assertEqual(self.counts(), ("12", "3", "1"))
        self.assertEqual(logger.get_state()["detonated"], 2)

    def test_reset_same_map_id_cannot_restore_previous_session_drafts(self):
        self.enter_counts()
        self.window.waystone.setText("155")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.reset_logger()
        self.assertEqual(self.window.state["current_map_id"], "M0001")
        self.assertEqual(self.counts(), ("", "", ""))
        self.assertEqual(self.window.waystone.text(), "0")
        self.window.refresh()
        self.assertEqual(self.counts(), ("", "", ""))

    def test_manual_tablet_draft_survives_waystone_save_without_becoming_saved_data(self):
        self.enter_tablet(1, "25")
        self.enter_tablet(2, "35")
        draft = self.window._tablet_form()
        self.window.waystone.setText("85")
        self.window.save_map_settings()
        self.window.save_active_master()
        self.assertEqual(self.window._tablet_form(), draft)
        settings = logger.get_state()["settings"]
        self.assertEqual(settings["tablets_used"], 1)
        self.assertFalse(any(item["affix"] for item in settings["tablet_affixes"]))
        self.window.save_tablets()
        settings = logger.get_state()["settings"]
        self.assertEqual(settings["tablets_used"], 2)
        self.assertEqual([settings["tablet_affixes"][index]["value"] for index in (0, 4)], [25, 35])
        changed = list(settings["tablet_affixes"])
        changed[0] = {**changed[0], "value": 45}
        logger.save_settings({"tablet_affixes": changed})
        self.window.refresh()
        self.assertEqual(float(self.window.tablet_values[0].text()), 45)

    def test_reviewed_tablet_updates_target_slot_and_keeps_other_manual_slot_draft(self):
        self.window.set_auto_tablets(False)
        self.enter_tablet(1, "99")
        self.enter_tablet(2, "88")
        self.scan_tablet(30)
        self.assertEqual(self.window.tablet_values[0].text(), "30")
        self.window.save_tablets()
        self.window.refresh()
        self.assertEqual(self.window.tablet_values[0].text(), "30")
        self.assertEqual(self.window.tablet_values[4].text(), "88")
        settings = logger.get_state()["settings"]
        self.assertEqual(settings["tablet_affixes"][0]["value"], 30)
        self.assertEqual(settings["tablet_affixes"][4]["affix"], "")

    def test_automatic_tablet_save_never_replaces_target_with_old_draft_or_saves_other_slots(self):
        self.enter_tablet(1, "99")
        self.enter_tablet(2, "88")
        self.window.set_auto_tablets(True)
        self.assertEqual(self.scan_tablet(30), 1)
        self.window.refresh()
        self.assertEqual(self.window.tablet_values[0].text(), "30")
        self.assertEqual(self.window.tablet_values[4].text(), "88")
        self.assertEqual(value(self.window.tablets_used), 2)
        settings = logger.get_state()["settings"]
        self.assertEqual(settings["tablets_used"], 1)
        self.assertEqual(settings["tablet_affixes"][0]["value"], 30)
        self.assertEqual(settings["tablet_affixes"][4]["affix"], "")

    def test_tablet_reject_and_explicit_clear_remove_target_drafts(self):
        self.window.set_auto_tablets(False)
        self.enter_tablet(2, "88")
        self.scan_tablet(30)
        self.window.reject_review()
        self.assertEqual(value(self.window.tablet_affixes[0]), "")
        self.assertEqual(self.window.tablet_values[0].text(), "")
        self.assertEqual(self.window.tablet_values[4].text(), "88")
        self.window.clear_tablets()
        self.window.refresh()
        self.assertEqual(value(self.window.tablets_used), logger.get_state()["settings"]["tablets_used"])
        self.assertTrue(all(field.text() == "" for field in self.window.tablet_values))

    def test_active_master_draft_survives_saving_perks_then_saves_correct_master(self):
        select(self.window.master, "Jado")
        select(self.window.perk_master, "Jado")
        perk = self.window.state["masters"]["Jado"][0]["name"]
        select(self.window.perk_boxes[0], perk)
        self.window.save_perks()
        self.assertEqual(value(self.window.master), "Jado")
        self.assertEqual(logger.get_state()["settings"]["atlas_master"], "None")
        self.window.save_active_master()
        self.assertEqual(logger.get_state()["settings"]["atlas_master"], "Jado")
        self.assertEqual(logger.get_state()["settings"]["master_selections"]["Jado"][0], perk)

    def test_perk_draft_and_configure_master_survive_unrelated_saves(self):
        self.window.perk_master.setCurrentIndex(self.window.perk_master.findData("Hilda"))
        perk = self.window.state["masters"]["Hilda"][0]["name"]
        select(self.window.perk_boxes[0], perk)
        self.window.save_map_settings()
        self.window.save_counts()
        self.assertEqual(value(self.window.perk_master), "Hilda")
        self.assertEqual(value(self.window.perk_boxes[0]), perk)
        self.assertEqual(logger.get_state()["settings"]["master_selections"]["Hilda"][0], "None")
        self.window.save_perks()
        selections = logger.get_state()["settings"]["master_selections"]
        selections["Hilda"] = ["None"] * 4
        logger.save_settings({"master_selections": selections})
        self.window.refresh()
        self.assertEqual(value(self.window.perk_master), "Hilda")
        self.assertEqual(value(self.window.perk_boxes[0]), "None")

    def test_new_map_discards_unsaved_tablet_master_and_perk_drafts(self):
        self.enter_tablet(2, "88")
        select(self.window.master, "Jado")
        self.window.perk_master.setCurrentIndex(self.window.perk_master.findData("Hilda"))
        select(self.window.perk_boxes[0], self.window.state["masters"]["Hilda"][0]["name"])
        self.window.finish_map()
        self.assertEqual(self.window.state["current_map_id"], "M0002")
        self.assertTrue(all(field.text() == "" for field in self.window.tablet_values))
        self.assertEqual(value(self.window.master), "None")
        self.assertEqual(value(self.window.perk_master), "Jado")
        self.assertTrue(all(value(field) == "None" for field in self.window.perk_boxes))


if __name__ == "__main__":
    unittest.main()
