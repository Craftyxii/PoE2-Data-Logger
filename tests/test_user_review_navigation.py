"""Qt normal-navigation regressions for context-owned corrections, saved export paths and restored review controls."""

import csv
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class UserReviewNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-review-navigation-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        raw = io.BytesIO()
        Image.new("RGB", (575, 720), "tan").save(raw, format="PNG")
        self.raw = raw.getvalue()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def saved_part(self):
        self.window._propagation_read({
            "mode": "propagation", "can_use": True, "runes": ["Tidal"],
            "selected_recipe": "Regal Orb x3", "positions": [3],
            **logger.scan_context()}, self.raw)

    def test_return_from_unaccepted_propagation_does_not_leave_a_hidden_chain_blocker(self):
        self.saved_part()
        self.window.manual_remnant_button.click()
        self.window.first_recipe.setText("Reward being corrected")
        pending = dict(logger.get_state()["ocr_pending"])
        self.window._propagation_read({
            "mode": "propagation", "can_use": False, "runes": [],
            "choices": [{"selected_recipe": "Prismatic Alloy", "runes": [], "can_use": False}],
            **logger.scan_context()}, self.raw)
        self.window.propagation_recipe_table.setCurrentCell(0, 0)
        self.window.propagation_rune_inputs[0].setEditText("Opulent")
        self.window.manual_remnant_button.click()
        self.assertEqual(self.window.review_kind.property("scanKind"), "remnant")
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.assertIsNone(self.window._manual_propagation_context)
        self.assertEqual(self.window.propagation_recipe_table.rowCount(), 0)
        self.assertEqual(self.window.first_recipe.text(), "Reward being corrected")
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.window.tabs.setCurrentIndex(1)
        self.assertTrue(self.window.expedition_complete_chain_button.isEnabled())
        self.window.expedition_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(logger.get_state()["ocr_pending"], pending)

    def test_saved_chain_corrections_survive_viewing_another_expedition(self):
        self.saved_part()
        self.window.tabs.setCurrentIndex(1)
        self.window.expedition_chain_table.cellWidget(0, 1).setCurrentText("Death")
        self.assertTrue(self.window.expedition_save_chain_button.isEnabled())
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual(self.window.expedition_chain_table.cellWidget(0, 1).currentText(), "Death")
        self.assertTrue(self.window.expedition_save_chain_button.isEnabled())
        self.assertFalse(self.window.expedition_complete_chain_button.isEnabled())
        self.window.expedition_save_chain_button.click()
        self.assertEqual(logger.get_state()["chain"][0]["rune1"], "Death",
                         self.window.statusBar().currentMessage())
        self.assertTrue(self.window.expedition_complete_chain_button.isEnabled())
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")

    def test_saved_corrections_for_two_expeditions_do_not_cross_ids(self):
        self.saved_part()
        self.window.expedition_chain_table.cellWidget(0, 1).setCurrentText("Death")
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.saved_part()
        self.window.expedition_chain_table.cellWidget(0, 1).setCurrentText("Opulent")
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual(self.window.expedition_chain_table.cellWidget(0, 1).currentText(), "Death")
        self.window.expedition_save_chain_button.click()
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.window.expedition_chain_table.cellWidget(0, 1).currentText(), "Opulent")
        self.window.expedition_save_chain_button.click()
        rows = csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig")))
        self.assertEqual([(row["Expedition ID"], row["Propagation Rune 1"])
                          for row in rows if row["Chain Step #"]],
                         [("M0001-E01", "Death"), ("M0001-E02", "Opulent")])

    def test_new_map_discards_unsaved_corrections_and_perk_drafts(self):
        self.saved_part()
        self.window.expedition_chain_table.cellWidget(0, 1).setCurrentText("Death")
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.window.perk_master.setCurrentText("Jado")
        self.window.perk_boxes[0].setCurrentText("Trove Seekers")
        self.window.perk_master.setCurrentText("Hilda")
        self.window.finish_map()
        self.window.perk_master.setCurrentText("Jado")
        self.assertEqual(self.window.perk_boxes[0].currentText(), "None")
        self.assertEqual(self.window._saved_chain_correction_drafts, {})
        self.assertEqual(self.window._perk_drafts, {})
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        rows = list(csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertEqual(next(row["Propagation Rune 1"] for row in rows if row["Chain Step #"]), "Tidal")

    def test_chosen_export_folder_survives_saving_an_unrelated_setting(self):
        folder = str(Path(self.tmp.name) / "shared exports")
        Path(folder).mkdir()
        self.window.tabs.setCurrentIndex(5)
        with patch.object(QFileDialog, "getExistingDirectory", return_value=folder):
            self.window.choose_folder()
        self.window.tabs.setCurrentIndex(3)
        next(button for button in self.window.findChildren(QPushButton)
             if button.text() == "Save active master").click()
        self.window.tabs.setCurrentIndex(5)
        self.assertEqual(self.window.export_folder.text(), folder)
        def immediate(_label, write, done):
            done(write())
        with patch.object(self.window, "_submit", side_effect=immediate):
            next(button for button in self.window.findChildren(QPushButton)
                 if button.text() == "Save XLSX to folder").click()
        self.assertEqual(logger.get_state()["export_folder"], folder,
                         self.window.statusBar().currentMessage())
        self.assertTrue(list(Path(folder).glob("*.xlsx")))
        self.assertEqual(self.window.export_folder.text(), folder)

    def test_perk_selection_survives_viewing_another_master(self):
        self.window.tabs.setCurrentIndex(3)
        self.window.perk_master.setCurrentText("Jado")
        self.window.perk_boxes[0].setCurrentText("Trove Seekers")
        self.window.perk_master.setCurrentText("Hilda")
        self.window.perk_master.setCurrentText("Jado")
        self.assertEqual(self.window.perk_boxes[0].currentText(), "Trove Seekers")
        next(button for button in self.window.findChildren(QPushButton)
             if button.text() == "Save perks").click()
        self.assertEqual(logger.get_state()["settings"]["master_selections"]["Jado"][0], "Trove Seekers")
        self.window.perk_master.setCurrentText("Hilda")
        self.window.perk_master.setCurrentText("Jado")
        self.assertEqual(self.window.perk_boxes[0].currentText(), "Trove Seekers")

    def test_in_game_reselection_repairs_one_legacy_region_without_converting_another(self):
        """Use the selection action to repair a stale region on a 4K capture."""
        from PoE2_Data_Logger.ui import native_desktop, region_select
        legacy = {"x": 600, "y": 300, "w": 1000, "h": 600}
        with logger._connect() as db:
            for key in ("inventory_region", "ritual_region"):
                logger._set_meta(db, key, legacy)
        self.window.scan_regions._legacy_regions.update(("inventory_region", "ritual_region"))
        editor = Mock()
        editor.exec.return_value = QDialog.DialogCode.Accepted
        editor.region.return_value = {"x": 960, "y": 1080, "w": 1920, "h": 540}
        callbacks = []
        with patch.object(native_desktop.sys, "platform", "win32"), patch.object(
                native_desktop.QTimer, "singleShot", side_effect=lambda delay, callback: callbacks.append(callback)), patch(
                "PoE2_Data_Logger.platform.live_watch.game_foreground", return_value=True), patch(
                "PoE2_Data_Logger.platform.hover_copy._tooltip_bounds", return_value=(0, 0, 3840, 2160)), patch(
                "PIL.ImageGrab.grab", return_value=Image.new("RGB", (3840, 2160))), patch.object(
                native_desktop, "RegionEditor", return_value=editor):
            self.window.select_region_in_game("inventory_region")
            next(callback for callback in callbacks if callback.__name__ == "select_now")()
        self.assertFalse(self.window._region_selection_pending)
        self.assertEqual(region_select.region_for("inventory_region", (0, 0, 3840, 2160)),
                         {"x": 960, "y": 1080, "w": 1920, "h": 540})
        with self.assertRaisesRegex(ValueError, "Select.*again"):
            region_select.region_for("ritual_region", (0, 0, 3840, 2160))
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "ritual_region", None), legacy)

    def test_saving_a_changed_setup_explains_that_logged_map_data_remains_frozen(self):
        logger.save_settings({"waystone": 85})
        self.window.refresh()
        self.saved_part()
        self.window.waystone.setText("120")
        self.window.save_map_settings()
        self.assertIn("M0001", self.window.statusBar().currentMessage())
        self.assertIn("recorded setup is unchanged", self.window.statusBar().currentMessage())
        with logger._connect() as db:
            import json
            snapshot = json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])
        self.assertEqual(snapshot["waystone"], 85)

    def test_region_picker_uses_current_resolution_draft_and_auto_recovers(self):
        """A mismatched unsaved preset cannot open a misleading calibration UI."""
        from PoE2_Data_Logger.ui import native_desktop
        page = self.window.scan_regions
        page.game_resolution.setCurrentIndex(page.game_resolution.findData("3840x2160"))
        editor = Mock()
        editor.exec.return_value = QDialog.DialogCode.Rejected
        callbacks = []
        with patch.object(native_desktop.sys, "platform", "win32"), patch.object(
                native_desktop.QTimer, "singleShot", side_effect=lambda delay, callback: callbacks.append(callback)), patch(
                "PoE2_Data_Logger.platform.live_watch.game_foreground", return_value=True), patch(
                "PoE2_Data_Logger.platform.hover_copy._tooltip_bounds", return_value=(0, 0, 1920, 1080)), patch(
                "PIL.ImageGrab.grab", return_value=Image.new("RGB", (1920, 1080))) as grab, patch.object(
                native_desktop, "RegionEditor", return_value=editor) as make_editor:
            self.window.select_region_in_game("inventory_region")
            next(callback for callback in callbacks if callback.__name__ == "select_now")()
            self.assertFalse(self.window._region_selection_pending)
            self.assertIn("Auto", self.window.statusBar().currentMessage())
            grab.assert_not_called()
            make_editor.assert_not_called()
            callbacks.clear()
            page.game_resolution.setCurrentIndex(page.game_resolution.findData("auto"))
            self.window.select_region_in_game("inventory_region")
            next(callback for callback in callbacks if callback.__name__ == "select_now")()
            grab.assert_called_once()
            make_editor.assert_called_once()
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "scan_region_resolution", "auto"), "auto")

    def test_auto_saved_waystone_does_not_claim_to_change_an_already_logged_setup(self):
        from PoE2_Data_Logger.ocr.item_text import parse_item_text
        logger.save_settings({"waystone": 85, "ocr_auto_commit": True})
        self.window.refresh()
        self.saved_part()
        reading = parse_item_text("""Item Class: Waystones
Rarity: Rare
Changed Waystone
Waystone (Tier 15)
--------
Waystone Drop Chance: +120%
--------
Item Level: 79
--------
30% increased Rarity of Items found in this Area
""")
        self.window._hover_item_read({**reading, **logger.scan_context()})
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIn("M0001", self.window.map_scan_status.text())
        self.assertIn("recorded setup is unchanged", self.window.map_scan_status.text())
        self.assertIn("recorded setup is unchanged", self.window.review_summary.text())
