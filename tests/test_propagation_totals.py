"""Storage checks counting accepted propagation scans once while preserving pair order and expedition totals."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store


class PropagationTotalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-propagation-totals-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def accept(self, runes=None, **kwargs):
        return logger.increment_propagation_detonated(logger.scan_context(),
            runes=["Rage"] if runes is None else runes, recipe="Test recipe", **kwargs)

    def records(self):
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("meta", "maps", "expeditions", "new_export", "commits")}

    def test_one_and_two_runes_each_increment_once_in_scan_order(self):
        first = self.accept(["Rage"])
        second = self.accept(["Time", "Power"])
        self.assertEqual((first["detonated"], second["detonated"]), (1, 2))
        self.assertEqual((first["map_id"], second["expedition_id"]), ("M0001", "M0001-E01"))
        self.assertEqual(logger.get_state()["detonated"], 2)
        with logger._connect() as db:
            commits = [json.loads(row[0]) for row in db.execute(
                "SELECT details_json FROM commits WHERE kind='Propagation' ORDER BY number")]
        self.assertEqual(commits, [
            {"detonated": 1, "runes": ["Rage"], "recipe": "Test recipe"},
            {"detonated": 2, "runes": ["Time", "Power"], "recipe": "Test recipe"}])

    def test_chain_commit_keeps_total_then_next_expedition_starts_unknown(self):
        self.accept(["Death", "Power"])
        self.accept(["Opulent"])
        saved = logger.commit_chain_draft([
            {"rune1": "Death", "rune2": "Power"}, {"rune1": "Opulent"}], logger.scan_context())
        self.assertEqual(saved["expedition_id"], "M0001-E01")
        self.assertEqual(logger.get_state()["detonated"], 2)
        completed = logger.complete_chain(logger.scan_context())
        self.assertEqual(completed["next_expedition_id"], "M0001-E02")
        self.assertIsNone(logger.get_state()["detonated"])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT detonated FROM expeditions WHERE expedition_id='M0001-E01'")
                             .fetchone()[0], 2)
            details = json.loads(db.execute("SELECT details_json FROM commits WHERE kind='Chain'").fetchone()[0])
            rows = [json.loads(row[0]) for row in db.execute("SELECT row_json FROM new_export ORDER BY position")]
        self.assertEqual(details["detonated"], 2)
        self.assertEqual([row[33] for row in rows], [2, ""])
        self.assertEqual(self.accept()["detonated"], 1)
        logger.save_settings({"expedition": 1})
        self.assertEqual(logger.get_state()["detonated"], 2)

    def test_increment_updates_only_existing_first_expedition_row(self):
        logger.commit_chain_steps([{"rune1": "Rage"}, {"rune1": "Time"}])
        self.accept()
        self.accept()
        with logger._connect() as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT row_json FROM new_export ORDER BY position")]
        self.assertEqual([row[33] for row in rows], [2, ""])
        self.assertEqual([row[26] for row in rows], ["Rage", "Time"])

    def test_invalid_reads_and_correction_values_do_not_mutate(self):
        before = self.records()
        for runes in (None, [], ["Rage", "Time", "Power"], [""], [" "], [1], ["Rage", None], ["a" * 81]):
            with self.subTest(runes=runes), self.assertRaises(ValueError):
                logger.increment_propagation_detonated(logger.scan_context(), runes=runes)
            self.assertEqual(self.records(), before)
        for value in (-1, 1.5, True, "unknown"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.accept(current_value=value)
            self.assertEqual(self.records(), before)

    def test_missing_and_stale_context_do_not_mutate(self):
        current = logger.scan_context()
        before = self.records()
        for context in (None, {}, "bad", {**current, "_scan_generation": -1},
                        {**current, "_capture_map_id": "M0002"},
                        {**current, "_capture_map_pending": True},
                        {**current, "_capture_expedition": 2}):
            with self.subTest(context=context), self.assertRaises(ValueError):
                logger.increment_propagation_detonated(context, runes=["Rage"])
            self.assertEqual(self.records(), before)

    def test_pending_remnant_no_map_and_finished_map_reject(self):
        with logger._connect() as db:
            logger._set_meta(db, "ocr_pending", {"remnant_id": "R0001"})
        before = self.records()
        with self.assertRaisesRegex(ValueError, "scanned remnant"):
            self.accept()
        self.assertEqual(self.records(), before)
        logger.finish_map(0, 0, 0, 0)
        before = self.records()
        with self.assertRaisesRegex(ValueError, "next map"):
            self.accept()
        self.assertEqual(self.records(), before)
        logger.clear_export_and_reset_ids()
        before = self.records()
        with self.assertRaisesRegex(ValueError, "Start a map"):
            self.accept()
        self.assertEqual(self.records(), before)

    def test_same_chain_pending_remnant_is_preserved_while_counting_propagation(self):
        pending = logger.assign_ocr_id("opened")
        number = logger.get_state()["next_remnant_id"]
        self.assertEqual(self.accept(["Death", "Rebirth"])["detonated"], 1)
        state = logger.get_state()
        self.assertEqual(state["ocr_pending"], pending)
        self.assertEqual(state["next_remnant_id"], number)
        self.assertEqual(state["current_expedition_id"], pending["expedition_id"])
        self.assertEqual(state["scan_commit_count"], 1)

    def test_pending_remnant_for_another_map_cannot_receive_propagation(self):
        pending = {"remnant_id": "R0001", "map_id": "M0002", "expedition_id": "M0002-E01"}
        with logger._connect() as db:
            logger._set_meta(db, "ocr_pending", pending)
        before = self.records()
        with self.assertRaisesRegex(ValueError, "another map"):
            self.accept()
        self.assertEqual(self.records(), before)

    def test_pending_remnant_other_expedition_on_same_map_stays_independent(self):
        pending = logger.assign_ocr_id("opened")
        logger.commit_chain_draft([{"rune1": "Death"}], logger.scan_context())
        logger.complete_chain(logger.scan_context())
        accepted = self.accept(["Power"])
        self.assertEqual(accepted["expedition_id"], "M0001-E02")
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(pending["expedition_id"], "M0001-E01")

    def test_legacy_corrections_and_omitted_count_preservation(self):
        self.assertEqual(self.accept(current_value="7")["detonated"], 8)
        logger.save_counts(10, 2, 1, 3)
        self.assertEqual(self.accept()["detonated"], 4)
        logger.save_counts(12, 3, 2, unique=1)
        self.assertEqual(logger.get_state()["detonated"], 4)
        self.assertEqual(self.accept(current_value="")["detonated"], 1)
        logger.save_detonated(10)
        self.assertEqual(self.accept()["detonated"], 11)
        logger.finish_map(20, 4, 2, unique=2)
        self.assertEqual(logger.get_state()["detonated"], 11)
        logger.start_map()
        self.assertIsNone(logger.get_state()["detonated"])

    def test_write_failure_rolls_back_count_row_and_commit(self):
        logger.commit_chain("Rage")
        before = self.records()
        with patch.object(logger, "_record_commit", side_effect=RuntimeError("failed")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                self.accept()
        self.assertEqual(self.records(), before)

    def test_concurrent_accepted_reads_cannot_lose_increments(self):
        context = logger.scan_context()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: logger.increment_propagation_detonated(
                context, runes=["Rage"]), range(6)))
        self.assertEqual(sorted(item["detonated"] for item in results), list(range(1, 7)))
        self.assertEqual(sorted(item["scan_commit_number"] for item in results), list(range(1, 7)))
        self.assertEqual(logger.get_state()["detonated"], 6)

    def test_manual_draft_callback_receipt_counts_once_and_preserves_distinct_scan(self):
        context = logger.scan_context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            saved = list(pool.map(lambda _: logger.increment_propagation_detonated(
                context, runes=["Death", "Power"], recipe="Unique Belt", request_id="capture-a"), range(2)))
        self.assertEqual(sorted(item["reused"] for item in saved), [False, True])
        self.assertEqual([item["detonated"] for item in saved], [1, 1])
        self.assertEqual([item["scan_commit_number"] for item in saved], [1, 1])
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        another = logger.increment_propagation_detonated(context, runes=["Death", "Power"],
            recipe="Unique Belt", request_id="capture-b")
        self.assertEqual((another["detonated"], another["reused"]), (2, False))
        before = self.records()
        with self.assertRaisesRegex(ValueError, "different scan"):
            logger.increment_propagation_detonated(context, runes=["Time"], recipe="Unique Belt", request_id="capture-a")
        self.assertEqual(self.records(), before)
        logger.commit_chain_draft([{"rune1": "Death", "rune2": "Power"}])
        logger.complete_chain(context)
        before = self.records()
        replay = logger.increment_propagation_detonated(context, runes=["Death", "Power"],
            recipe="Unique Belt", request_id="capture-a")
        self.assertTrue(replay["reused"])
        self.assertEqual(self.records(), before)

    def test_propagation_freezes_setup_and_blocks_undo_and_reset_reuse(self):
        atlas = dict(logger.get_state()["settings"]["atlas_settings"])
        atlas["gear_item_rarity"] = 100
        logger.save_atlas_settings(atlas)
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 1}])
        context = logger.scan_context()
        self.accept()
        with logger._connect() as db:
            original = db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0]
        atlas["gear_item_rarity"] = 200
        logger.save_atlas_settings(atlas)
        logger.save_settings({"tier": 16, "waystone": 87, "map_mods": 5})
        self.accept()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0], original)
            snapshots = [json.loads(row[0]) for row in db.execute(
                "SELECT snapshot_json FROM commits WHERE kind='Propagation' ORDER BY number")]
        self.assertEqual([item["gear_item_rarity"] for item in snapshots], [100, 100])
        with self.assertRaisesRegex(ValueError, "saved activity"):
            logger.undo_empty_map()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        before = self.records()
        with self.assertRaisesRegex(ValueError, "session changed"):
            logger.increment_propagation_detonated(context, runes=["Rage"])
        self.assertEqual(self.records(), before)
        self.assertEqual(self.accept()["detonated"], 1)


if __name__ == "__main__":
    unittest.main()
