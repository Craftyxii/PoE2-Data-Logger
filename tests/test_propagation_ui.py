import csv
import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class PropagationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.raw = io.BytesIO()
        Image.new("RGB", (575, 720), "tan").save(self.raw, format="PNG")

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def scan(self, runes, recipe="Medved's Saga", clear=True, context=None):
        result = {"mode": "propagation", "runes": runes, "positions": list(range(1, len(runes) + 1)),
                  "selected_recipe": recipe, "can_use": clear, "status": "Propagation read",
                  **(logger.scan_context() if context is None else context)}
        self.window._propagation_read(result, self.raw.getvalue())

    def draft(self):
        table = self.window.chain_review_table
        return [(table.item(row, 0).text(), table.item(row, 1).text()) for row in range(table.rowCount())]

    def review_state(self):
        window = self.window
        return {
            "kind": window.pending_review_kind,
            "heading": window.review_kind.text(),
            "summary": window.review_summary.text(),
            "preview": window.preview.pixmap().cacheKey(),
            "preview_hidden": window.preview.isHidden(),
            "review_hidden": window.review_group.isHidden(),
            "remnant_hidden": window.remnant_log_group.isHidden(),
            "approve": (window.approve_scan_button.isHidden(), window.approve_scan_button.isEnabled()),
            "reject": (window.reject_scan_button.isHidden(), window.reject_scan_button.isEnabled()),
            "recipes": (window.first_recipe.text(), window.next_recipe.text(),
                        window.recipe_family.currentData()),
            "recipe_rows": [[window.recipe_table.item(row, column).text() for column in range(3)]
                            for row in range(window.recipe_table.rowCount())],
            "recipe_hidden": window.recipe_table.isHidden(),
            "results": copy.deepcopy(window.results),
            "images": dict(window.images),
            "resolved": copy.deepcopy(window.resolved),
            "runes": [(field.text(), field.property("chainPart"), field.property("chainRecipe"))
                      for field in window.rune_inputs],
            "chain_rows": self.draft(),
            "chain_drafts": copy.deepcopy(window._chain_drafts),
            "chain_context": copy.deepcopy(window._chain_context),
            "propagation": copy.deepcopy(window._propagation_reading),
            "overlay_token": window._overlay_review_token,
        }

    def assert_remnant_review_preserved(self, before):
        after = self.review_state()
        for key in ("runes", "chain_rows", "chain_drafts"):
            before.pop(key, None)
            after.pop(key, None)
        self.assertEqual(after, before)

    def test_scans_append_in_scan_order_with_left_to_right_pairs(self):
        before = logger.get_state()["scan_commit_count"]
        self.scan(["Death", "Power"], "Divine Orb x2")
        self.scan(["Opulent"], "Greater Regal Orb x3")
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Power"), ("2", "Opulent")])
        self.assertEqual(self.window._chain_steps(), [
            {"rune1": "Death", "rune2": "Power"}, {"rune1": "Opulent", "rune2": ""}])
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertFalse(self.window.review_commit_chain_button.isHidden())
        self.assertEqual(logger.get_state()["scan_commit_count"], before + 2)
        self.assertEqual(logger.get_state()["detonated"], 2)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["next_remnant_id"], "R0001")
        self.assertIsNone(self.window.pending_review_kind)
        with self.assertRaises(ValueError):
            self.window.approve_review()
        self.assertEqual(len(self.draft()), 3)

    def test_review_commit_saves_pairs_then_advances_same_map(self):
        self.scan(["Rage", "Time"])
        self.scan(["Opulent"])
        self.window.review_commit_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(self.window.header_expedition.currentData(), 2)
        self.assertEqual(self.window.expedition.currentData(), 2)
        self.assertEqual(self.draft(), [])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual([(row["rune1"], row["rune2"]) for row in logger.get_state()["chain"]],
                         [("Rage", "Time"), ("Opulent", "")])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.scan(["Death"])
        self.window.commit_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E03")
        self.assertEqual(self.window.header_expedition.currentData(), 3)
        self.assertEqual(self.window.expedition.currentData(), 3)

    def test_keyboard_correction_preserves_pair_in_review_and_export(self):
        self.scan(["Death", "Power"], "Divine Orb x2")
        self.scan(["Opulent"], "Greater Regal Orb x3")
        field = self.window.rune_inputs[1]
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(field, Qt.Key.Key_Backspace)
        self.assertEqual(field.text(), "")
        with self.assertRaisesRegex(ValueError, "Fill runes in order"):
            self.window.commit_chain()
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)
        QTest.keyClicks(field, "Time")
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Time"), ("2", "Opulent")])
        self.assertEqual(self.window.chain_review_table.item(1, 2).text(), "Divine Orb x2")
        self.window.review_commit_chain_button.click()
        rows = list(csv.reader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertEqual([(row[26], row[27], row[32]) for row in rows[1:]],
                         [("Death", "Time", "M0001-E01"), ("Opulent", "", "M0001-E01")])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_empty_paired_correction_survives_expedition_switch(self):
        self.scan(["Death", "Power"], "Divine Orb x2")
        self.window.rune_inputs[1].clear()
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.draft(), [])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        QTest.keyClicks(self.window.rune_inputs[1], "Time")
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Time")])
        self.assertEqual(self.window.chain_review_table.item(1, 2).text(), "Divine Orb x2")

    def test_clearing_all_runes_discards_old_scan_grouping(self):
        self.scan(["Death", "Power"], "Divine Orb x2")
        for field in self.window.rune_inputs:
            field.clear()
        self.window.rune_inputs[0].setText("Death")
        self.window.rune_inputs[1].setText("Time")
        self.assertEqual(self.draft(), [("1", "Death"), ("2", "Time")])
        self.assertEqual(self.window.chain_review_table.item(0, 2).text(), "")
        self.assertEqual(self.window.chain_review_table.item(1, 2).text(), "")

    def test_new_scan_replaces_cleared_trailing_pair_member(self):
        self.scan(["Death", "Power"], "Divine Orb x2")
        self.window.rune_inputs[1].clear()
        self.scan(["Opulent"], "Greater Regal Orb x3")
        self.assertEqual(self.draft(), [("1", "Death"), ("2", "Opulent")])
        self.assertEqual(self.window.chain_review_table.item(1, 2).text(), "Greater Regal Orb x3")
        self.assertEqual(self.window._chain_steps(), [
            {"rune1": "Death", "rune2": ""}, {"rune1": "Opulent", "rune2": ""}])

    def test_switching_expedition_preserves_separate_drafts(self):
        self.scan(["Rage", "Time"])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.draft(), [])
        self.scan(["Death"])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual(self.draft(), [("1", "Rage"), ("1", "Time")])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.draft(), [("1", "Death")])

    def test_stale_scan_does_not_append_after_commit_or_new_map(self):
        context = logger.scan_context()
        self.scan(["Rage"])
        self.window.commit_chain()
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            self.scan(["Time"], context=context)
        self.assertEqual(self.draft(), [])
        context = logger.scan_context()
        self.window.finish_map()
        with self.assertRaisesRegex(ValueError, "map changed"):
            self.scan(["Time"], context=context)
        self.assertEqual(self.draft(), [])

    def test_unclear_scan_stays_reviewable_without_adding_a_part(self):
        self.scan([], clear=False)
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(self.draft(), [])
        self.window.reject_review()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_review_reject_remnant_button_keeps_inflight_propagation_result(self):
        self.window.manual_remnant_button.click()
        self.assertEqual(self.window.pending_review_kind, "remnant")

        def read(image):
            self.assertEqual(manager._active_capture_mode, "propagation")
            self.window.reject_scan_button.click()
            self.assertIsNone(logger.get_state()["ocr_pending"])
            return {"runes": ["Death", "Rebirth"], "positions": [1, 2],
                    "selected_recipe": "Medved's Saga", "can_use": True}

        manager = HotkeyManager(readers={"propagation": read}, supported=False,
                               grabber=lambda **kwargs: Image.new("RGB", (580, 730), "tan"))
        with patch.object(service, "HOTKEY", manager):
            manager.capture("propagation")
            event = manager.status()["latest"]
            self.assertIsNotNone(event)
            self.assertEqual(event["mode"], "propagation")
            self.window.poll()
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Rebirth")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_review_reject_propagation_button_keeps_inflight_opened_result(self):
        self.scan([], clear=False)
        self.assertEqual(self.window.pending_review_kind, "propagation")

        def read(path):
            self.assertEqual(manager._active_capture_mode, "opened")
            self.window.reject_scan_button.click()
            self.assertIsNone(self.window.pending_review_kind)
            return {"status": "Reward needs review.", "can_use": False,
                    "first_recipe": None, "opened_recipes": []}

        manager = HotkeyManager(readers={"opened": read}, supported=False,
                               grabber=lambda **kwargs: Image.new("RGB", (580, 730), "tan"))
        with patch.object(service, "HOTKEY", manager):
            manager.capture("remnant")
            event = manager.status()["latest"]
            self.assertIsNotNone(event)
            self.assertEqual(event["mode"], "opened")
            self.window.poll()
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(logger.get_state()["ocr_pending"]["remnant_id"], "R0001")
        self.assertEqual(self.draft(), [])

    def test_starting_remnant_file_review_keeps_inflight_propagation_result(self):
        path = Path(self.tmp.name) / "remnant.png"
        path.write_bytes(self.raw.getvalue())
        self.window.mode = "opened"
        jobs = []

        def read(image):
            self.assertEqual(manager._active_capture_mode, "propagation")
            self.window.scan_file()
            self.assertEqual(self.window.pending_review_kind, "remnant")
            self.assertIsNotNone(self.window._remnant_reading)
            return {"runes": ["Rage"], "positions": [1],
                    "selected_recipe": "Medved's Saga", "can_use": True}

        manager = HotkeyManager(readers={"propagation": read}, supported=False,
                               grabber=lambda **kwargs: Image.new("RGB", (580, 730), "tan"))
        with patch.object(service, "HOTKEY", manager), patch.object(
                QFileDialog, "getOpenFileName", return_value=(str(path), "Images")), patch.object(
                self.window, "_submit", side_effect=lambda label, work, done: jobs.append((work, done))):
            manager.capture("propagation")
            self.assertIsNotNone(manager.status()["latest"])
            pending_capture = self.window._remnant_reading
            self.window.poll()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(self.draft(), [("1", "Rage")])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertIs(self.window._remnant_reading, pending_capture)

    def test_propagation_restores_hidden_hud_with_pending_remnant_review(self):
        self.window._overlay_enabled = True
        for clear in (True, False):
            with self.subTest(clear=clear):
                self.window.manual_remnant_button.click()
                self.window.first_recipe.setText("A reward being corrected")
                self.app.processEvents()
                self.assertTrue(self.window.isVisible())
                # Hotkey capture hides the HUD before freezing its screenshot.
                self.window.hide()
                self.app.processEvents()
                self.assertFalse(self.window.isVisible())
                self.scan(["Rage"] if clear else [], clear=clear)
                self.app.processEvents()
                self.assertTrue(self.window.isVisible())
                self.assertEqual(self.window.pending_review_kind, "remnant")
                self.assertEqual(self.window.first_recipe.text(), "A reward being corrected")

    def test_pending_opened_remnant_allows_propagation_without_losing_review_or_chain(self):
        self.scan(["Death", "Power"], "Divine Orb x2")
        result = {"mode": "opened", "status": "The first reward needs review.",
                  "can_use": False, "first_recipe": None,
                  "opened_recipes": [{"raw": "Unreadable reward", "recipe": None}],
                  **logger.scan_context()}
        self.window.show_result("opened", result, self.raw.getvalue())
        pending = logger.get_state()["ocr_pending"]
        self.assertEqual(pending["remnant_id"], "R0001")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.window.first_recipe.setText("Perfect Chaos Orb x3")
        self.window.next_recipe.setText("Perfect Exalted Orb x3")
        self.window.resolve_recipe(preserve_review=True)
        self.assertFalse(self.window.remnant_log_group.isHidden())
        self.assertFalse(self.window.review_group.isHidden())
        self.assertFalse(self.window.preview.isHidden())
        self.assertFalse(self.window.approve_scan_button.isHidden())
        self.assertFalse(self.window.reject_scan_button.isHidden())
        self.assertGreater(self.window.recipe_table.rowCount(), 0)
        before = self.review_state()

        self.scan(["Opulent"], "Greater Regal Orb x3")
        self.assert_remnant_review_preserved(before)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)
        self.assertEqual(logger.get_state()["detonated"], 2)
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Power"), ("2", "Opulent")])
        self.window.commit_chain()
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(self.draft(), [])
        self.scan(["Rage"], "Greater Regal Orb x3")
        self.window.reject_scan_button.click()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(self.draft(), [("1", "Rage")])
        self.window.review_commit_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E03")
        self.assertEqual(self.draft(), [])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual([(row["rune1"], row["rune2"]) for row in logger.get_state()["chain"]],
                         [("Death", "Power"), ("Opulent", "")])

    def test_manual_remnant_keeps_original_expedition_while_propagation_advances(self):
        self.scan(["Rage", "Time"])
        self.window.manual_remnant_button.click()
        self.window.first_recipe.setText("Reward being corrected")
        self.window.next_recipe.setText("Next reward being corrected")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        pending = logger.get_state()["ocr_pending"]
        self.assertEqual(pending["expedition_id"], "M0001-E01")
        self.assertFalse(self.window.approve_scan_button.isHidden())
        self.assertFalse(self.window.reject_scan_button.isHidden())
        before = self.review_state()

        self.scan(["Death"])
        self.assert_remnant_review_preserved(before)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)
        self.assertEqual(self.draft(), [("1", "Rage"), ("1", "Time"), ("2", "Death")])
        self.window.commit_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.window.first_recipe.text(), "Reward being corrected")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.window.first_recipe.setText("Perfect Chaos Orb x3")
        self.window.next_recipe.setText("Perfect Exalted Orb x3")
        self.window.resolve_recipe(preserve_review=True)
        self.window.approve_review()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT expedition_id FROM commits WHERE kind='Remnant'").fetchone()[0],
                             "M0001-E01")

    def test_pending_seed_review_without_database_token_allows_propagation(self):
        self.scan(["Rage"])
        self.window._review_pending("seed", "Visible remnant still needs review.", False)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        before = self.review_state()

        self.scan(["Time"])
        self.assert_remnant_review_preserved(before)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(self.draft(), [("1", "Rage"), ("2", "Time")])

    def test_same_chain_orphan_database_remnant_token_is_preserved_during_propagation(self):
        self.scan(["Death", "Power"])
        pending = logger.assign_ocr_id("opened")
        self.assertIsNone(self.window.pending_review_kind)
        before = self.review_state()

        self.scan(["Opulent"])
        self.assert_remnant_review_preserved(before)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Power"), ("2", "Opulent")])

    def test_unclear_propagation_keeps_pending_remnant_edits_and_reports_problem(self):
        self.window.manual_remnant_button.click()
        self.window.first_recipe.setText("A reward being corrected")
        before = self.review_state()
        self.scan([], clear=False)
        self.assertEqual(self.review_state(), before)
        self.assertIn("pending remnant remains open", self.window.chain_review_status.text())
        self.assertIn("pending remnant remains open", self.window.statusBar().currentMessage())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_pending_opened_remnant_can_still_save_after_propagation(self):
        result = {"mode": "opened", "status": "Reward needs review.", "can_use": False,
                  "first_recipe": None, "opened_recipes": [], **logger.scan_context()}
        self.window.show_result("opened", result, self.raw.getvalue())
        pending = logger.get_state()["ocr_pending"]
        self.window.first_recipe.setText("Perfect Chaos Orb x3")
        self.window.next_recipe.setText("Perfect Exalted Orb x3")
        self.window.resolve_recipe(preserve_review=True)
        self.scan(["Rage", "Time"])
        self.window.approve_review()
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(self.draft(), [("1", "Rage"), ("1", "Time")])
        rows = list(csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertTrue(any(pending["remnant_id"] in row.values() for row in rows))
        self.window.commit_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_pending_opened_review_survives_chain_commit_and_next_chain_scan(self):
        result = {"mode": "opened", "status": "Reward needs review.", "can_use": False,
                  "first_recipe": None, "opened_recipes": [], **logger.scan_context()}
        self.window.show_result("opened", result, self.raw.getvalue())
        pending = logger.get_state()["ocr_pending"]
        self.window.first_recipe.setText("Farrul's Rune of Grace")
        self.window.next_recipe.setText("Farrul's Rune of the Hunt")
        self.window.resolve_recipe(preserve_review=True)
        self.scan(["Death", "Rebirth"])
        self.window.commit_chain()
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.window.first_recipe.text(), "Farrul's Rune of Grace")
        self.scan(["Power"])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.window.approve_review()
        self.assertEqual(self.draft(), [("1", "Power")])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertIsNone(logger.get_state()["ocr_pending"])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT expedition_id FROM commits WHERE kind='Remnant'").fetchone()[0],
                             "M0001-E01")
            self.assertEqual([tuple(row) for row in db.execute(
                "SELECT expedition_id,detonated FROM expeditions ORDER BY expedition_id")],
                [("M0001-E01", 1), ("M0001-E02", 1)])

    def test_delayed_remnant_result_arrives_after_chain_advance(self):
        capture = object()
        context = logger.scan_context()
        self.window._remnant_reading = capture
        self.window._review_pending("remnant", "Reading remnant image…", False)
        self.scan(["Rage"])
        self.window.commit_chain()
        self.assertIs(self.window._remnant_reading, capture)
        result = {"mode": "opened", "status": "Reward needs review.", "can_use": False,
                  "first_recipe": None, "opened_recipes": [], "_target_map_id": "M0001", **context}
        self.window._scan_done("opened", result, self.raw.getvalue(), capture)
        self.assertEqual(result["expedition_id"], "M0001-E01")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_remnant_file_scan_can_finish_while_service_chain_advances(self):
        context = logger.scan_context()

        def slow_read(path):
            logger.commit_chain_draft([{"rune1": "Rage"}], context)
            return {"status": "Reward needs review.", "opened_recipes": [], "first_recipe": None}

        import base64
        with patch.object(service, "scan_opened", side_effect=slow_read):
            result = service.dispatch("/api/scan?mode=opened", {
                "image": base64.b64encode(self.raw.getvalue()).decode("ascii"),
                "map_id": "M0001", "scan_context": context})
        self.assertEqual(result["expedition_id"], "M0001-E01")
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_manual_runes_and_scans_keep_existing_order(self):
        self.window.rune_inputs[0].setText("Death")
        self.window.rune_inputs[1].setText("Power")
        self.scan(["Opulent"])
        self.assertEqual(self.draft(), [("1", "Death"), ("2", "Power"), ("3", "Opulent")])
        self.window.rune_inputs[1].clear()
        with self.assertRaisesRegex(ValueError, "Fill runes in order"):
            self.window.commit_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")

    def test_poll_routes_propagation_without_remnant_assignment(self):
        event = {"id": 101, "mode": "propagation", "error": None,
                 "result": {"runes": ["Rage"], "selected_recipe": "Medved's Saga",
                            "can_use": True, **logger.scan_context()}}
        status = service.HOTKEY.status()
        with patch.object(service.HOTKEY, "status", return_value={**status, "latest": event}), \
             patch.object(service.HOTKEY, "image", return_value=self.raw.getvalue()), \
             patch.object(logger, "assign_ocr_id", side_effect=AssertionError("remnant path")):
            self.window.poll()
            self.window.poll()
        self.assertEqual(self.draft(), [("1", "Rage")])
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_propagation_binding_is_in_settings_and_ocr_indicator(self):
        self.assertIn("propagation", self.window.direct_hotkey_labels)
        old_combo, old_combos = service.HOTKEY.combo, dict(service.HOTKEY.combos)
        try:
            with patch.object(service.HOTKEY, "supported", True), \
                 patch.object(service.HOTKEY, "_register"), patch.object(service.HOTKEY, "_unregister"):
                service.HOTKEY.configure_for("propagation", "Ctrl+Shift+F8")
                self.window.refresh_hotkey()
                self.assertEqual(self.window.direct_hotkey_labels["propagation"].text(), "Ctrl+Shift+F8")
                self.window.clear_hotkey("propagation")
                self.assertEqual(self.window.direct_hotkey_labels["propagation"].text(), "Off")
        finally:
            service.HOTKEY.combo, service.HOTKEY.combos = old_combo, old_combos


if __name__ == "__main__":
    unittest.main()
