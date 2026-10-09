"""Qt checks separating editable kill totals from accepted propagation detonation counts and completed expeditions."""

import csv
import io
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QLineEdit

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class UniquePropagationCountsUITests(unittest.TestCase):
    """Exercise editable unique kill totals and independently persisted propagation detonation counts."""
    @classmethod
    def setUpClass(cls):
        """Reuse or create the QApplication required by desktop count widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated desktop window and PNG evidence for controlled propagation results."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-unique-propagation-ui-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        raw = io.BytesIO()
        Image.new("RGB", (575, 720), "tan").save(raw, format="PNG")
        self.raw = raw.getvalue()

    def tearDown(self):
        """Close desktop workers, restore logger storage and remove temporary data."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def scan(self, runes, clear=True, context=None):
        """Deliver a propagation result with supplied runes, clarity and optional capture context."""
        result = {"mode": "propagation", "runes": runes,
                  "positions": list(range(1, len(runes) + 1)),
                  "selected_recipe": "Medved's Saga", "can_use": clear,
                  "status": "Propagation read", **(logger.scan_context() if context is None else context)}
        self.window._propagation_read(result, self.raw)

    def counts(self, normal="", magic="", rare="", unique=""):
        """Populate all four editable kill-count fields."""
        for widget, value in ((self.window.normal, normal), (self.window.magic, magic),
                              (self.window.rare, rare), (self.window.unique, unique)):
            widget.setText(str(value))

    def map_rows(self):
        """Decode map-summary export rows into a map-ID lookup."""
        rows = list(csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig"))))
        return {row["Map ID"]: row for row in rows}

    def total_rows(self):
        """Decode only map-total rows from the combined export."""
        return [row for row in csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig")))
                if row["Type"] == "Map totals"]

    def assert_current_detonated(self, count):
        """Check that persisted detonation count matches both chain status labels."""
        self.assertEqual(logger.get_state()["detonated"], count)
        for label in (self.window.chain_note, self.window.chain_review_status):
            self.assertIn(f"{count or 0} remnants detonated", label.text())

    def test_unique_normal_magic_and_rare_save_refresh_and_export(self):
        """Verify all four kill categories save, refresh and export with their entered values."""
        for rune in ("Death", "Power", "Opulent"):
            self.scan([rune])
        self.counts(normal=101, magic=23, rare=4, unique=2)
        self.assertEqual(self.window.counts_data(),
                         {"normal": "101", "magic": "23", "rare": "4", "unique": "2"})
        self.window.save_counts()
        self.window.refresh()

        state = logger.get_state()
        self.assertEqual(state["kills"], [101, 23, 4])
        self.assertEqual(state["unique_kills"], 2)
        self.assertEqual([self.window.normal.text(), self.window.magic.text(),
                          self.window.rare.text(), self.window.unique.text()], ["101", "23", "4", "2"])
        for row, suffix in ((self.map_rows()["M0001"], ""), (self.total_rows()[-1], " (Map)")):
            self.assertEqual([row[name + suffix] for name in
                              ("Normal Kills", "Magic Kills", "Rare Kills", "Unique Kills")],
                             ["101", "23", "4", "2"])

    def test_kills_form_has_four_editable_kill_fields_and_no_detonated_input(self):
        """Verify kill editing exposes four kill fields without an editable detonation field."""
        fields = self.window.normal.parentWidget().findChildren(QLineEdit)
        self.assertEqual(set(fields), {self.window.normal, self.window.magic, self.window.rare, self.window.unique})
        self.assertFalse(isinstance(getattr(self.window, "detonated", None), QLineEdit))

    def test_finish_map_exports_unique_and_magic_zero_without_converting_blank_rare(self):
        """Verify map finish preserves explicit zero kills and blank rare kills in exports, then clears new-map fields."""
        self.scan(["Death"])
        self.counts(normal=10, magic=0, rare="", unique=0)
        self.window.finish_map()

        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertEqual(logger.get_state()["kills"], [None, None, None])
        self.assertIsNone(logger.get_state()["unique_kills"])
        self.assertEqual([self.window.normal.text(), self.window.magic.text(),
                          self.window.rare.text(), self.window.unique.text()], ["", "", "", ""])
        for row, suffix in ((self.map_rows()["M0001"], ""), (self.total_rows()[-1], " (Map)")):
            self.assertEqual([row[name + suffix] for name in
                              ("Normal Kills", "Magic Kills", "Rare Kills", "Unique Kills")],
                             ["10", "0", "", "0"])

    def test_each_accepted_scan_counts_one_remnant_and_completion_resets_next_expedition(self):
        """Verify each accepted scan counts once and chain completion starts a fresh expedition count."""
        self.scan(["Death", "Power"])
        self.assert_current_detonated(1)
        self.scan(["Opulent"])
        self.assert_current_detonated(2)
        self.window.refresh()
        self.assert_current_detonated(2)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.window.review_complete_chain_button.click()

        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assert_current_detonated(None)
        self.assertEqual(self.map_rows()["M0001"]["Expedition 1 Detonated"], "2")
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assert_current_detonated(2)
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assert_current_detonated(None)
        self.scan(["Rage", "Time"])
        self.assert_current_detonated(1)
        self.window.complete_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E03")
        self.assert_current_detonated(None)
        row = self.map_rows()["M0001"]
        self.assertEqual((row["Expedition 1 Detonated"], row["Expedition 2 Detonated"]), ("2", "1"))

    def test_auto_increment_preserves_unsaved_kills_and_save_reads_fresh_persisted_total(self):
        """Verify automatic detonation increments preserve kill drafts and count saving reads the latest persisted total."""
        self.counts(normal=101, magic=23, rare=4, unique=9)
        self.scan(["Death", "Power"])
        self.assert_current_detonated(1)
        self.assertEqual([self.window.normal.text(), self.window.magic.text(),
                          self.window.rare.text(), self.window.unique.text()], ["101", "23", "4", "9"])
        self.window.refresh()
        self.assert_current_detonated(1)
        self.assertEqual(self.window.unique.text(), "9")
        logger.increment_propagation_detonated(logger.scan_context(), runes=["Time"], recipe="Medved's Saga")
        self.window.save_counts()
        self.assert_current_detonated(2)
        self.scan(["Opulent"])
        self.assert_current_detonated(3)
        self.assertEqual(self.map_rows()["M0001"]["Expedition 1 Detonated"], "3")

    def test_finish_map_reads_fresh_auto_count_without_clearing_it(self):
        """Verify finishing a map exports newly persisted detonation counts before starting the next map."""
        self.scan(["Death"])
        self.counts(normal=10, magic=2, rare=3, unique=1)
        logger.increment_propagation_detonated(logger.scan_context(), runes=["Rage", "Time"],
                                              recipe="Medved's Saga")
        self.window.finish_map()
        self.assertEqual(self.map_rows()["M0001"]["Expedition 1 Detonated"], "2")
        self.assert_current_detonated(None)

    def test_unique_draft_survives_master_save_and_chain_commit_then_clean_field_refreshes(self):
        """Verify unique kill drafts survive unrelated saves and clean fields later refresh from storage."""
        self.counts(normal=1, magic=2, rare=3, unique=2)
        self.window.save_counts()
        self.window.unique.setText("9")
        select(self.window.master, "Jado")
        self.window.save_active_master()
        self.assertEqual(self.window.unique.text(), "9")
        self.assertEqual(logger.get_state()["unique_kills"], 2)
        self.window.rune_inputs[0].setText("Death")
        self.window.commit_chain()
        self.assertEqual(self.window.unique.text(), "9")
        self.assertEqual(logger.get_state()["unique_kills"], 2)
        self.window.save_counts()
        logger.save_counts(1, 2, 3, "", unique=11)
        self.window.refresh()
        self.assertEqual(self.window.unique.text(), "11")
        self.assertEqual(logger.get_state()["unique_kills"], 11)

    def test_legacy_count_api_payloads_preserve_unique_when_omitted(self):
        """Verify legacy count and finish API payloads preserve unique kills when that field is omitted."""
        self.counts(normal=1, magic=2, rare=3, unique=7)
        self.window.save_counts()
        service.dispatch("/api/kills", {"normal": 9, "magic": 8, "rare": 7, "detonated": 6})
        self.assertEqual(logger.get_state()["kills"], [9, 8, 7])
        self.assertEqual(logger.get_state()["unique_kills"], 7)
        service.dispatch("/api/finish-map", {"normal": 10, "magic": 11, "rare": 12, "detonated": 3})
        self.assertEqual(self.map_rows()["M0001"]["Unique Kills"], "7")
        self.assertEqual(self.total_rows()[-1]["Unique Kills (Map)"], "7")

    def test_finish_api_preserves_auto_count_when_detonated_is_omitted(self):
        """Verify finish API preserves automatic detonation totals when the field is omitted."""
        self.scan(["Rage", "Time"])
        service.dispatch("/api/finish-map", {"normal": 10, "magic": 2, "rare": 3, "unique": 4})
        row = self.map_rows()["M0001"]
        self.assertEqual(row["Expedition 1 Detonated"], "1")
        self.assertEqual(row["Unique Kills"], "4")
        self.assertTrue(logger.get_state()["pending_new_map"])
        self.assertEqual(logger.get_state()["detonated"], 1)

    def test_finish_api_explicit_none_still_clears_legacy_detonated_total(self):
        """Verify explicit null detonation in the legacy finish API clears the persisted total."""
        self.scan(["Death"])
        service.dispatch("/api/finish-map", {"normal": 10, "magic": 2, "rare": 3,
                                              "unique": 4, "detonated": None})
        self.assertEqual(self.map_rows()["M0001"]["Expedition 1 Detonated"], "")
        self.assertIsNone(logger.get_state()["detonated"])

    def test_unclear_and_rejected_scans_do_not_contribute_remnants(self):
        """Verify unclear or rejected propagation scans never increase detonation counts."""
        for runes in ([], ["Death"]):
            with self.subTest(runes=runes):
                self.scan(runes, clear=False)
                self.assert_current_detonated(None)
                self.assertEqual(self.window._chain_steps(), [])
                self.window.reject_scan_button.click()
                self.assertIsNone(self.window.pending_review_kind)
                self.assert_current_detonated(None)
        self.scan(["Death"])
        self.assert_current_detonated(1)
        self.scan([], clear=False)
        self.window.reject_review()
        self.assert_current_detonated(1)

    def test_malformed_scans_neither_count_nor_append_runes(self):
        """Verify malformed propagation runes neither increment counts nor append chain parts."""
        for runes in ([None], [""], ["x" * 81], ["Death", 1]):
            with self.subTest(runes=runes):
                with self.assertRaises(ValueError):
                    self.scan(runes)
                self.assert_current_detonated(None)
                self.assertEqual(self.window._chain_steps(), [])
                self.window.reject_review()

    def test_full_chain_draft_does_not_count_an_unaccepted_scan(self):
        """Verify a full chain draft rejects extra scan runes without counting the rejected scan."""
        self.window.add_runes(96 - len(self.window.rune_inputs))
        for field in self.window.rune_inputs:
            field.setText("Death")
        before = [field.text() for field in self.window.rune_inputs]
        with self.assertRaisesRegex(ValueError, "maximum of 96"):
            self.scan(["Rage", "Time"])
        self.assert_current_detonated(None)
        self.assertEqual([field.text() for field in self.window.rune_inputs], before)

    def test_pending_remnant_allows_propagation_count_and_keeps_binding(self):
        """Verify independent propagation acceptance preserves a pending remnant's reservation and count."""
        self.scan(["Death"])
        self.window.show_result("opened", {"mode": "opened", "status": "Review needed.",
                                           "can_use": False, "first_recipe": None,
                                           "opened_recipes": [], **logger.scan_context()}, self.raw)
        pending = logger.get_state()["ocr_pending"]
        self.assertEqual(self.window._chain_steps(), [])
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.assert_current_detonated(1)
        self.scan(["Opulent"])
        self.assert_current_detonated(2)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.window._chain_steps(), [])
        self.assertEqual([(part["rune1"], part["rune2"]) for part in logger.get_state()["chain"]],
                         [("Death", ""), ("Opulent", "")])
        self.window.reject_review()
        self.assert_current_detonated(2)

    def test_stale_scan_after_chain_completion_and_new_map_does_not_count(self):
        """Verify captures from completed expeditions or prior maps cannot increase current counts."""
        context = logger.scan_context()
        self.scan(["Death"])
        self.window.complete_chain()
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            self.scan(["Time"], context=context)
        self.assert_current_detonated(None)
        context = logger.scan_context()
        self.scan(["Power"])
        self.window.finish_map()
        with self.assertRaisesRegex(ValueError, "map changed"):
            self.scan(["Rage"], context=context)
        self.assert_current_detonated(None)
        row = self.map_rows()["M0001"]
        self.assertEqual((row["Expedition 1 Detonated"], row["Expedition 2 Detonated"]), ("1", "1"))

    def test_manual_chain_parts_do_not_create_fake_scanned_remnants(self):
        """Verify manually entered chain parts never invent scanned-remnant counts."""
        self.window.rune_inputs[0].setText("Death")
        self.window.rune_inputs[1].setText("Power")
        self.window.commit_chain()
        self.assert_current_detonated(None)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.assertEqual(self.map_rows()["M0001"]["Expedition 1 Detonated"], "")
        self.window.complete_chain()
        self.scan(["Rage", "Time"])
        self.window.rune_inputs[0].setText("Opulent")
        self.assert_current_detonated(1)
        self.window.commit_chain()
        self.assert_current_detonated(1)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.window.complete_chain()
        self.assert_current_detonated(None)
        self.assertEqual(self.map_rows()["M0001"]["Expedition 2 Detonated"], "1")


if __name__ == "__main__":
    unittest.main()
