import csv
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
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
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

    def test_scans_append_in_scan_order_with_left_to_right_pairs(self):
        before = logger.get_state()["scan_commit_count"]
        self.scan(["Death", "Power"], "Divine Orb x2")
        self.scan(["Opulent"], "Greater Regal Orb x3")
        self.assertEqual(self.draft(), [("1", "Death"), ("1", "Power"), ("2", "Opulent")])
        self.assertEqual(self.window._chain_steps(), [
            {"rune1": "Death", "rune2": "Power"}, {"rune1": "Opulent", "rune2": ""}])
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertFalse(self.window.review_commit_chain_button.isHidden())
        self.assertEqual(logger.get_state()["scan_commit_count"], before)
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
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
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
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

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
