"""Saved propagation parts accumulate until explicit, durable completion."""
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store


class ChainCommitAdvanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
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

    def records(self):
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("meta", "maps", "expeditions", "new_export", "commits",
                                  "chain_completions", "chain_append_receipts")}

    def commits(self):
        with logger._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]

    def read_csv(self, data):
        return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))

    def test_repeat_append_preserves_pairs_and_order_until_complete_then_restart(self):
        context = logger.scan_context()
        first = logger.commit_chain_draft([{"rune1": "Death", "rune2": "Power"}], context, request_id="scan-a")
        second = logger.commit_chain_draft([{"rune1": "Opulent"}], context, request_id="scan-b")
        self.assertEqual((first["steps"], second["steps"]), ([1], [2]))
        self.assertNotIn("next_expedition", second)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        expected = [{"step": 1, "rune1": "Death", "rune2": "Power"},
                    {"step": 2, "rune1": "Opulent", "rune2": ""}]
        logger._READY = False
        logger.initialize()
        self.assertEqual(logger.get_state()["chain"], expected)
        self.assertFalse(logger.get_state()["chain_completed"])
        done = logger.complete_chain(context)
        self.assertEqual((done["expedition_id"], done["next_expedition_id"], done["step_count"]),
                         ("M0001-E01", "M0001-E02", 2))
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        completion = self.commits()[-1]
        self.assertEqual(completion["kind"], "Chain completion")
        self.assertEqual(json.loads(completion["details_json"])["steps"], expected)
        logger._READY = False
        logger.initialize()
        logger.save_settings({"expedition": 1})
        self.assertEqual(logger.get_state()["chain"], expected)
        self.assertTrue(logger.get_state()["chain_completed"])
        self.assertTrue(all(row["Chain Status"] == "Completed" for row in self.read_csv(logger.export_csv())))

    def test_concurrent_identical_request_saves_once_but_new_scan_may_repeat_same_rune(self):
        context = logger.scan_context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: logger.commit_chain_draft(
                [{"rune1": "Death"}], context, request_id="same-callback"), range(2)))
        self.assertEqual(sorted(row["reused"] for row in results), [False, True])
        self.assertEqual([row["scan_commit_number"] for row in results], [1, 1])
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(len(logger.get_state()["chain"]), 1)
        repeated = logger.commit_chain_draft([{"rune1": "Death"}], context, request_id="another-scan")
        self.assertEqual(repeated["steps"], [2])
        before = self.records()
        with self.assertRaisesRegex(ValueError, "different scan"):
            logger.commit_chain_draft([{"rune1": "Power"}], context, request_id="same-callback")
        self.assertEqual(self.records(), before)

    def test_atomic_confident_accept_counts_once_per_part_and_replays_after_completion(self):
        context = logger.scan_context()
        first = logger.accept_propagation_part(context, runes=["Death", "Power"], recipe="Unique Belt", request_id="a")
        second = logger.accept_propagation_part(context, runes=["Time"], recipe="Chaos Orb", request_id="b")
        self.assertEqual((first["detonated"], second["detonated"]), (1, 2))
        self.assertEqual((first["propagation_commit_number"], first["scan_commit_number"]), (1, 2))
        self.assertEqual((second["propagation_commit_number"], second["scan_commit_number"]), (3, 4))
        self.assertEqual([row["kind"] for row in self.commits()], ["Propagation", "Chain", "Propagation", "Chain"])
        self.assertEqual([(row["rune1"], row["rune2"]) for row in logger.get_state()["chain"]],
                         [("Death", "Power"), ("Time", "")])
        logger.complete_chain(context)
        before = self.records()
        replay = logger.accept_propagation_part(context, runes=["Death", "Power"], recipe="Unique Belt", request_id="a")
        self.assertTrue(replay["reused"])
        self.assertEqual(replay["scan_commit_number"], first["scan_commit_number"])
        self.assertEqual(self.records(), before)
        logger.save_settings({"expedition": 1})
        self.assertEqual(logger.get_state()["detonated"], 2)
        self.assertEqual([row[33] for row in self._raw_rows()], [2, ""])

    def _raw_rows(self):
        with logger._connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT row_json FROM new_export ORDER BY position")]

    def test_auto_save_failure_rolls_back_count_audits_parts_and_receipt(self):
        before = self.records()
        with patch.object(logger, "_add_new", side_effect=RuntimeError("write failed")):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                logger.accept_propagation_part(logger.scan_context(), runes=["Death", "Power"], request_id="retry")
        self.assertEqual(self.records(), before)
        result = logger.accept_propagation_part(logger.scan_context(), runes=["Death", "Power"], request_id="retry")
        self.assertEqual((result["detonated"], result["steps"], result["scan_commit_number"]), (1, [1], 2))

    def test_append_batch_failure_is_atomic(self):
        before = self.records()
        insert = logger._add_new
        count = 0
        def fail_second(db, row):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("write failed")
            insert(db, row)
        with patch.object(logger, "_add_new", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                logger.commit_chain_draft([{"rune1": "Death"}, {"rune1": "Power"}], request_id="draft")
        self.assertEqual(self.records(), before)

    def test_completion_repeated_callbacks_are_idempotent_and_do_not_overwrite_existing_eid(self):
        logger.save_settings({"expedition": 2})
        logger.commit_chain("Power")
        logger.save_settings({"expedition": 1})
        context = logger.scan_context()
        logger.commit_chain("Death")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: logger.complete_chain(context), range(2)))
        self.assertEqual(sorted(row["already_completed"] for row in results), [False, True])
        self.assertEqual([row["next_expedition_id"] for row in results], ["M0001-E03"] * 2)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E03")
        self.assertEqual(sum(row["kind"] == "Chain completion" for row in self.commits()), 1)
        logger.save_settings({"expedition": 2})
        self.assertEqual(logger.get_state()["chain"][0]["rune1"], "Power")
        self.assertFalse(logger.get_state()["chain_completed"])

    def test_completion_failure_rolls_back_closure_and_advance(self):
        logger.commit_chain("Death")
        before = self.records()
        save = logger._set_meta
        def fail_selection(db, key, value):
            if key == "settings":
                raise RuntimeError("settings write failed")
            return save(db, key, value)
        with patch.object(logger, "_set_meta", side_effect=fail_selection):
            with self.assertRaisesRegex(RuntimeError, "settings write failed"):
                logger.complete_chain(logger.scan_context())
        self.assertEqual(self.records(), before)

    def test_empty_invalid_or_stale_operations_do_not_mutate(self):
        before = self.records()
        for steps in ([], [{"rune1": ""}], ["Death"], [{"rune1": 7}]):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                logger.commit_chain_draft(steps)
        with self.assertRaisesRegex(ValueError, "at least one"):
            logger.complete_chain(logger.scan_context())
        self.assertEqual(self.records(), before)
        stale = logger.scan_context()
        logger.save_settings({"expedition": 2})
        before = self.records()
        for operation in (lambda: logger.commit_chain_draft([{"rune1": "Death"}], stale),
                          lambda: logger.accept_propagation_part(stale, runes=["Death"]),
                          lambda: logger.complete_chain(stale)):
            with self.assertRaisesRegex(ValueError, "map, expedition or session changed"):
                operation()
            self.assertEqual(self.records(), before)

    def test_closed_chain_rejects_new_parts_counts_and_corrections_but_remnant_is_independent(self):
        context = logger.scan_context()
        pending = logger.assign_ocr_id("opened")
        logger.accept_propagation_part(context, runes=["Death"], request_id="accepted")
        logger.complete_chain(context)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        with logger._connect() as db:
            recipes = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=26").fetchone()[0])[1:]
        saved = logger.commit_remnant(recipes[0], recipes[1], 26, expected_context={**context, **pending})
        self.assertEqual(saved["expedition_id"], "M0001-E01")
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        logger.save_settings({"expedition": 1})
        before = self.records()
        for operation in (lambda: logger.commit_chain("Power"),
                          lambda: logger.increment_propagation_detonated(context, runes=["Power"]),
                          lambda: logger.accept_propagation_part(context, runes=["Power"], request_id="new"),
                          lambda: logger.update_chain_steps([{"step": 1, "rune1": "Time"}], context)):
            with self.assertRaisesRegex(ValueError, "completed"):
                operation()
            self.assertEqual(self.records(), before)

    def test_building_correction_preserves_ids_counts_original_commits_and_final_snapshot(self):
        context = logger.scan_context()
        logger.accept_propagation_part(context, runes=["Death", "Power"], request_id="a")
        original = self.commits()
        before_rows = self._raw_rows()
        result = logger.update_chain_steps([{"step": 1, "rune1": "Time", "rune2": "Power"}], context)
        self.assertTrue(result["changed"])
        self.assertEqual(logger.get_state()["detonated"], 1)
        after_rows = self._raw_rows()
        expected = list(before_rows[0]); expected[26] = "Time"
        self.assertEqual(after_rows, [expected])
        self.assertEqual(self.commits()[:2], original)
        history = self.read_csv(logger.export_record_history_csv())
        correction = next(row for row in history if row["Type"] == "Chain correction")
        self.assertEqual((correction["Previous Rune 1"], correction["Propagation Rune 1"], correction["Chain Step #"]),
                         ("Death", "Time", "1"))
        before = self.records()
        self.assertFalse(logger.update_chain_steps([{"step": 1, "rune1": "Time", "rune2": "Power"}], context)["changed"])
        self.assertEqual(self.records(), before)
        for steps in ([{"step": 2, "rune1": "Time"}], [{"step": 1, "rune1": "Made Up"}],
                      [{"step": 1, "rune1": "Time"}, {"step": 1, "rune1": "Death"}]):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                logger.update_chain_steps(steps, context)
            self.assertEqual(self.records(), before)
        logger.complete_chain(context)
        self.assertEqual(json.loads(self.commits()[-1]["details_json"])["steps"],
                         [{"step": 1, "rune1": "Time", "rune2": "Power"}])

    def test_reset_removes_closure_receipts_and_invalidates_old_captures(self):
        context = logger.scan_context()
        logger.commit_chain_draft([{"rune1": "Death"}], context, request_id="a")
        logger.complete_chain(context)
        logger.clear_export_and_reset_ids()
        logger.start_map()
        before = self.records()
        with self.assertRaisesRegex(ValueError, "session changed"):
            logger.commit_chain_draft([{"rune1": "Death"}], context, request_id="a")
        self.assertEqual(self.records(), before)
        self.assertFalse(logger.get_state()["chain_completed"])
        self.assertFalse(self.records()["chain_completions"])
        self.assertFalse(self.records()["chain_append_receipts"])

    def test_long_saved_chain_can_be_corrected_without_using_draft_size_limit(self):
        context = logger.scan_context()
        logger.commit_chain_draft([{"rune1": "Death"} for _ in range(96)], context)
        logger.commit_chain_draft([{"rune1": "Power"} for _ in range(4)], context)
        edits = logger.get_state()["chain"]
        edits[99]["rune1"] = "Time"
        changed = logger.update_chain_steps(edits, context)
        self.assertEqual(changed["steps"], [100])
        self.assertEqual(logger.get_state()["chain"][-1], {"step": 100, "rune1": "Time", "rune2": ""})
        self.assertEqual(logger.complete_chain(context)["step_count"], 100)

    def test_legacy_parts_are_not_inferred_completed_and_next_chain_cannot_bypass_closure(self):
        logger.commit_chain("Rage", "Time")
        saved = logger.commit_chain_runes(["Death", "Power"])
        self.assertEqual(saved["steps"], [2, 3])
        logger._READY = False
        logger.initialize()
        self.assertFalse(logger.get_state()["chain_completed"])
        self.assertEqual(logger.start_next_chain()["current_expedition_id"], "M0001-E02")
        self.assertEqual(self.commits()[-1]["kind"], "Chain completion")
        self.assertEqual(logger.start_next_chain()["current_expedition_id"], "M0001-E03")


if __name__ == "__main__":
    unittest.main()
