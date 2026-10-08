import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr.item_text import parse_item_text
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class ChainReviewVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-chain-review-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()
        raw = io.BytesIO()
        Image.new("RGB", (575, 720), "tan").save(raw, format="PNG")
        self.raw = raw.getvalue()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def propagate(self, runes=("Death", "Power"), recipe="Divine Orb x2", clear=True, **extra):
        result = {"mode": "propagation", "runes": list(runes),
                  "positions": list(range(1, len(runes) + 1)), "selected_recipe": recipe,
                  "can_use": clear, "status": "Propagation read", **logger.scan_context(), **extra}
        self.window._propagation_read(result, self.raw)
        return result

    def draft_rows(self):
        table = self.window.chain_review_table
        return [tuple(table.item(row, column).text() for column in range(3))
                for row in range(table.rowCount())]

    def expedition_chain_rows(self):
        return [self.window.chain_list.item(row).text()
                for row in range(self.window.chain_list.count())]

    def expedition_counts(self):
        with logger._connect() as db:
            return {row[0]: row[1] for row in db.execute(
                "SELECT expedition_id,detonated FROM expeditions ORDER BY expedition_id")}

    def propagation_audit(self):
        with logger._connect() as db:
            return [tuple(row) for row in db.execute(
                "SELECT number,map_id,expedition_id,details_json FROM commits "
                "WHERE kind='Propagation' ORDER BY number")]

    def exported_chain(self):
        rows = csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig")))
        return [(row["Map ID"], row["Expedition ID"], row["Chain Step #"],
                 row["Propagation Rune 1"], row["Propagation Rune 2"])
                for row in rows if row["Chain Step #"]]

    def assert_discarded(self, counts, audit):
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.assertEqual(self.draft_rows(), [])
        self.assertTrue(all(field.text() == "" and not field.property("chainPart")
                            and not field.property("chainRecipe")
                            for field in self.window.rune_inputs))
        self.assertEqual(self.window._chain_drafts, {})
        self.assertIsNone(self.window._manual_propagation_context)
        self.assertEqual(self.window.propagation_recipe_table.rowCount(), 0)
        self.assertTrue(all(field.currentText() == "" for field in self.window.propagation_rune_inputs))
        self.assertFalse(self.window.review_commit_chain_button.isEnabled())
        self.assertEqual(self.expedition_counts(), counts)
        self.assertEqual(self.propagation_audit(), audit)
        self.assertEqual(self.expedition_chain_rows(), [])
        self.assertTrue(self.window.chain_list.isHidden())
        self.window.refresh()
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.assertEqual(self.draft_rows(), [])
        self.assertEqual(self.window._chain_drafts, {})
        self.assertEqual(self.expedition_counts(), counts)
        self.assertEqual(self.propagation_audit(), audit)
        self.assertEqual(self.expedition_chain_rows(), [])

    def opened_remnant(self, clear=True):
        if clear:
            with logger._connect() as db:
                names = json.loads(db.execute(
                    "SELECT recipes_json FROM families WHERE id=26").fetchone()[0])[1:]
            resolved = logger.resolve(names[0], names[1], 26)
            result = {"mode": "opened", "status": "Review the opened rewards before logging.", "can_use": True,
                      "family": "Family 26", "candidates": [26],
                      "sockets": resolved["rows"][0]["sockets"],
                      "recipe_sockets": resolved["rows"][0]["sockets"],
                      "socket_source": "opened icons", "first_line_gap": 60, "list_complete": False,
                      "first_recipe": names[0], "next_recipe": names[1],
                      "opened_recipes": [{"recipe": name, "raw": name, "ocr_score": .99,
                                          "match_score": 1} for name in names[:2]]}
        else:
            result = {"mode": "opened", "status": "First reward needs review", "can_use": False,
                      "first_recipe": None,
                      "opened_recipes": [{"raw": "Unreadable reward", "recipe": None}]}
        result.update(logger.scan_context())
        self.window.show_result("opened", result, self.raw)
        return result

    def tablet(self, automatic=False):
        self.window.set_auto_tablets(automatic)
        return self.window._tablet_read(1, {"kind": "tablet", "source": "clipboard",
            "mods": ["30% increased Pack Size"], "matches": [], "uncertain": [],
            **logger.scan_context()})

    def currency(self, automatic=False):
        select(self.window.inventory_phase, "end")
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 4}],
                                     "unknown": []}, live=automatic)

    def ritual(self, automatic=False):
        self.window._ritual_read({"items": [{"category": "Item", "name": "Chaos Orb", "quantity": 1,
             "tribute": 100, "source": "Chaos Orb", "score": 1}],
             "raw_text": "Chaos Orb", "unmatched": [],
             "tribute_available": 5430, "rerolls_remaining": 2}, live=automatic)

    def waystone(self):
        result = parse_item_text(
            "Item Class: Waystones\nRarity: Rare\nStorm Peak\nWaystone (Tier 16)\n"
            "--------\nWaystone Drop Chance: +87%\n--------\nItem Level: 82\n"
            "--------\n30% increased Rarity of Items found in this Area", self.window.state["affixes"])
        result.update(logger.scan_context())
        self.window._hover_item_read(result, self.raw)

    def test_unresolved_remnant_discards_draft_and_next_propagation_remains_independent(self):
        self.propagate()
        counts, audit = self.expedition_counts(), self.propagation_audit()
        self.opened_remnant(clear=False)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        held = logger.get_state()["ocr_pending"]
        first_recipe = self.window.first_recipe.text()
        self.assert_discarded(counts, audit)
        self.assertEqual(self.exported_chain(), [])

        self.propagate(("Rage", "Time"), "Chaos Orb x2")
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertEqual(self.draft_rows(), [("1", "Rage", "Chaos Orb x2"),
                                             ("1", "Time", "Chaos Orb x2")])
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.window.first_recipe.text(), first_recipe)
        self.assertEqual(logger.get_state()["ocr_pending"], held)
        self.assertEqual(self.expedition_counts()["M0001-E01"], 2)

    def test_automatically_saved_remnant_keeps_saved_preview_and_accepted_counts(self):
        self.propagate()
        counts, audit = self.expedition_counts(), self.propagation_audit()
        self.window.set_auto_commit(True)
        reading = self.opened_remnant()
        self.window.maybe_auto_commit(reading)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(self.window.recipe_table.property("savedRemnant"), "R0001")
        self.assertGreater(self.window.recipe_table.rowCount(), 0)
        self.assert_discarded(counts, audit)
        self.assertEqual(self.exported_chain(), [])
        self.window.start_manual_propagation()
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertEqual(self.draft_rows(), [])
        self.assertEqual(self.expedition_counts(), counts)
        self.assertEqual(self.window.recipe_table.property("savedRemnant"), "R0001")

    def test_manual_remnant_entry_clears_manual_choices_and_unsaved_rune_fields(self):
        self.propagate()
        self.propagate((), clear=False, choices=[{
            "selected_recipe": "Chaos Orb x2", "runes": ["Rage", "Time"], "can_use": True}])
        self.window.propagation_rune_inputs[0].setCurrentText("Rebirth")
        self.assertEqual(self.window.propagation_recipe_table.rowCount(), 1)
        counts, audit = self.expedition_counts(), self.propagation_audit()
        self.window.start_manual_remnant_review()
        held = logger.get_state()["ocr_pending"]
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assert_discarded(counts, audit)

        self.propagate((), clear=False)
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertEqual(self.draft_rows(), [])
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(logger.get_state()["ocr_pending"], held)
        self.window.propagation_rune_inputs[0].setCurrentText("Opulent")
        self.window.add_manual_propagation()
        self.assertEqual(self.window._chain_steps(), [{"rune1": "Opulent", "rune2": ""}])
        self.assertEqual(self.expedition_counts()["M0001-E01"], 2)
        self.assertEqual(logger.get_state()["ocr_pending"], held)

    def test_pending_tablet_waystone_currency_and_ritual_discard_active_draft(self):
        for kind, handler in (("tablet", self.tablet), ("waystone", self.waystone),
                              ("currency", self.currency), ("ritual", self.ritual)):
            with self.subTest(kind=kind):
                self.propagate()
                counts, audit = self.expedition_counts(), self.propagation_audit()
                handler()
                self.assertEqual(self.window.pending_review_kind, kind)
                self.assert_discarded(counts, audit)
                self.assertEqual(self.exported_chain(), [])
                self.window.reject_review()

    def test_automatic_tablet_save_discards_draft_without_losing_tablet_data(self):
        tablet_count = logger.get_state()["settings"]["tablets_used"]
        self.propagate()
        counts, audit = self.expedition_counts(), self.propagation_audit()
        self.assertEqual(self.tablet(automatic=True), 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.assert_discarded(counts, audit)
        settings = logger.get_state()["settings"]
        self.assertEqual(settings["tablet_affixes"][0]["value"], 30)
        self.assertEqual(settings["tablets_used"], tablet_count)

    def test_automatic_inventory_and_ritual_saves_discard_draft_without_losing_snapshots(self):
        self.window.set_auto_all(True)
        for kind, handler in (("currency", self.currency), ("ritual", self.ritual)):
            with self.subTest(kind=kind):
                self.propagate()
                counts, audit = self.expedition_counts(), self.propagation_audit()
                handler(automatic=True)
                self.assertIsNone(self.window.pending_review_kind)
                self.assert_discarded(counts, audit)
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Chaos Orb": 4})
        page = logger.ritual_pages_for_map("M0001")[0]
        self.assertEqual(page["items"][0]["name"], "Chaos Orb")
        exported = list(csv.DictReader(io.StringIO(logger.export_ritual_csv().decode("utf-8-sig"))))
        self.assertEqual(exported[0]["Ritual Tribute Available"], "5430")
        self.assertEqual(exported[0]["Ritual Rerolls Remaining"], "2")
        self.assertEqual(self.exported_chain(), [])

    def test_repeated_propagation_accumulates_until_commit_then_saved_chain_survives_reviews(self):
        self.propagate()
        self.propagate(("Opulent",), "Greater Regal Orb x3")
        self.window.refresh()
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertEqual(self.draft_rows(), [("1", "Death", "Divine Orb x2"),
            ("1", "Power", "Divine Orb x2"), ("2", "Opulent", "Greater Regal Orb x3")])
        self.window.review_commit_chain_button.click()
        expected = [("M0001", "M0001-E01", "1", "Death", "Power"),
                    ("M0001", "M0001-E01", "2", "Opulent", "")]
        self.assertEqual(self.exported_chain(), expected)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertTrue(self.window.chain_review_group.isHidden())

        # A later draft belongs to E02; discarding it must not rewrite the saved E01 chain.
        self.propagate(("Rage", "Time"), "Chaos Orb x2")
        counts, audit = self.expedition_counts(), self.propagation_audit()
        self.tablet(automatic=True)
        self.assert_discarded(counts, audit)
        self.assertEqual(self.exported_chain(), expected)
        self.assertEqual(counts, {"M0001-E01": 2, "M0001-E02": 1})

        self.propagate(("Rebirth",), "Uncut Spirit Gem")
        self.window.commit_chain()
        self.assertEqual(self.exported_chain(), expected + [
            ("M0001", "M0001-E02", "1", "Rebirth", "")])
        self.assertEqual(self.expedition_counts()["M0001-E02"], 2)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E03")
        self.opened_remnant()
        self.window.approve_remnant_scan()
        self.assertEqual(self.exported_chain(), expected + [
            ("M0001", "M0001-E02", "1", "Rebirth", "")])
        self.assertTrue(self.window.chain_review_group.isHidden())

    def test_discarded_draft_does_not_reappear_after_expedition_context_round_trip(self):
        self.propagate()
        counts, audit = self.expedition_counts(), self.propagation_audit()
        self.currency()
        self.window.reject_review()
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assert_discarded(counts, audit)
        self.window.start_manual_propagation()
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertEqual(self.draft_rows(), [])

    def test_existing_expedition_chain_list_updates_live_and_preserves_committed_parts(self):
        self.propagate()
        self.assertEqual(self.expedition_chain_rows(), ["#1  Death → Power · Divine Orb x2"])
        self.assertFalse(self.window.chain_list.isHidden())
        self.propagate(("Opulent",), "Greater Regal Orb x3")
        self.assertEqual(self.expedition_chain_rows(), [
            "#1  Death → Power · Divine Orb x2", "#2  Opulent · Greater Regal Orb x3"])

        # Correcting the review table must update the same Expedition list and eventual export.
        self.window.chain_review_table.item(1, 1).setText("Time")
        self.app.processEvents()
        self.assertEqual(self.expedition_chain_rows(), [
            "#1  Death → Time · Divine Orb x2", "#2  Opulent · Greater Regal Orb x3"])
        self.window.commit_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(self.expedition_chain_rows(), [])
        self.assertTrue(self.window.chain_list.isHidden())

        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        saved = ["#1  Death → Time", "#2  Opulent"]
        self.assertEqual(self.expedition_chain_rows(), saved)
        self.assertFalse(self.window.chain_list.isHidden())
        exported = self.exported_chain()
        self.propagate(("Rage",), "Chaos Orb x2")
        self.assertEqual(self.expedition_chain_rows(), saved + ["#3  Rage · Chaos Orb x2"])
        counts, audit = self.expedition_counts(), self.propagation_audit()
        self.currency()
        self.assertTrue(self.window.chain_review_group.isHidden())
        self.assertEqual(self.draft_rows(), [])
        self.assertEqual(self.expedition_chain_rows(), saved)
        self.assertEqual(self.exported_chain(), exported)
        self.assertEqual(self.expedition_counts(), counts)
        self.assertEqual(self.propagation_audit(), audit)
        self.window.refresh()
        self.assertEqual(self.expedition_chain_rows(), saved)
        self.assertEqual(self.window._chain_drafts, {})

    def test_new_map_and_session_reset_do_not_reuse_previous_draft(self):
        self.propagate()
        audit = self.propagation_audit()
        self.window.finish_map()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assert_discarded({"M0001-E01": 1, "M0002-E01": None}, audit)
        self.assertEqual(self.exported_chain(), [])
        self.propagate(("Rage",), "Chaos Orb x2")
        self.assertEqual(self.draft_rows(), [("1", "Rage", "Chaos Orb x2")])
        self.assertEqual(self.expedition_counts(), {"M0001-E01": 1, "M0002-E01": 1})
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.reset_logger()
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assert_discarded({"M0001-E01": None}, [])
        self.assertEqual(self.exported_chain(), [])
        self.window.start_manual_propagation()
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertEqual(self.draft_rows(), [])


if __name__ == "__main__":
    unittest.main()
