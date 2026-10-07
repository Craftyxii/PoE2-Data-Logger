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
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def records(self):
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("maps", "expeditions", "new_export", "commits")}

    def test_draft_preserves_scan_order_and_pairs_then_advances_same_map(self):
        context = logger.scan_context()
        saved = logger.commit_chain_draft([
            {"rune1": "Death", "rune2": "Power"}, {"rune1": "Opulent"}
        ], expected_context=context)
        self.assertEqual(saved["map_id"], "M0001")
        self.assertEqual(saved["expedition_id"], "M0001-E01")
        self.assertEqual(saved["steps"], [1, 2])
        self.assertEqual(saved["next_expedition"], 2)
        self.assertEqual(saved["next_expedition_id"], "M0001-E02")
        state = logger.get_state()
        self.assertEqual(state["current_map_id"], "M0001")
        self.assertEqual(state["current_expedition_id"], "M0001-E02")
        self.assertEqual(state["chain"], [])
        self.assertEqual(state["next_remnant_id"], "R0001")
        with logger._connect() as db:
            rows = [json.loads(row[0]) for row in db.execute(
                "SELECT row_json FROM new_export ORDER BY chain_step")]
            commit = db.execute("SELECT * FROM commits WHERE kind='Chain'").fetchone()
            self.assertEqual(db.execute("SELECT count(*) FROM scan_links").fetchone()[0], 0)
        self.assertEqual([(row[26], row[27]) for row in rows], [("Death", "Power"), ("Opulent", "")])
        self.assertTrue(all(row[22] == "M0001" and row[31:33] == [1, "M0001-E01"] for row in rows))
        self.assertEqual(json.loads(commit["snapshot_json"])["expedition"], 1)
        self.assertEqual(json.loads(commit["details_json"])["steps"], [
            {"step": 1, "rune1": "Death", "rune2": "Power"},
            {"step": 2, "rune1": "Opulent", "rune2": ""}
        ])
        logger.save_settings({"expedition": 1})
        self.assertEqual(state["current_map_id"], logger.get_state()["current_map_id"])
        self.assertEqual([(row["rune1"], row["rune2"]) for row in logger.get_state()["chain"]],
                         [("Death", "Power"), ("Opulent", "")])

    def test_later_expeditions_preserve_prior_commits_and_export_ids(self):
        first = logger.commit_chain_draft([{"rune1": "Death"}], logger.scan_context())
        before = self.records()
        second = logger.commit_chain_draft([{"rune1": "Power"}], logger.scan_context())
        third = logger.commit_chain_draft([{"rune1": "Opulent"}], logger.scan_context())
        self.assertEqual([row["expedition_id"] for row in (first, second, third)],
                         ["M0001-E01", "M0001-E02", "M0001-E03"])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E04")
        after = self.records()
        for table in before:
            self.assertEqual(after[table][:len(before[table])], before[table])
        rows = list(csv.reader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertEqual([(row[26], row[32]) for row in rows[1:]],
                         [("Death", "M0001-E01"), ("Power", "M0001-E02"), ("Opulent", "M0001-E03")])
        logger.save_settings({"expedition": 3})
        self.assertEqual(logger.get_state()["chain"][0]["rune1"], "Opulent")
        logger.finish_map(0, 0, 0, 0)
        self.assertEqual(logger.start_map()["current_expedition_id"], "M0002-E01")

    def test_rejected_empty_or_invalid_chain_does_not_advance(self):
        before = self.records()
        for steps in ([], [{"rune1": ""}], ["Death"], [{"rune1": "Death"}, {"rune1": ""}]):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                logger.commit_chain_draft(steps, logger.scan_context())
        self.assertEqual(self.records(), before)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")

    def test_write_failure_rolls_back_chain_commit_and_advance(self):
        before = self.records()
        insert = logger._add_new
        calls = []

        def failing_insert(db, row):
            calls.append(row)
            if len(calls) == 2:
                raise RuntimeError("write failed")
            insert(db, row)

        with patch.object(logger, "_add_new", side_effect=failing_insert):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                logger.commit_chain_draft([{"rune1": "Death"}, {"rune1": "Power"}], logger.scan_context())
        self.assertEqual(self.records(), before)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.assertEqual(logger.commit_chain_draft([{"rune1": "Death"}])["scan_commit_number"], 1)

    def test_stale_context_cannot_commit_into_another_expedition(self):
        context = logger.scan_context()
        logger.save_settings({"expedition": 2})
        before = self.records()
        with self.assertRaisesRegex(ValueError, "map, expedition or session changed"):
            logger.commit_chain_draft([{"rune1": "Death"}], context)
        self.assertEqual(self.records(), before)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        logger.finish_map(0, 0, 0, 0)
        logger.start_map()
        with self.assertRaisesRegex(ValueError, "map, expedition or session changed"):
            logger.commit_chain_draft([{"rune1": "Death"}], context)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0002-E01")

    def test_same_frozen_draft_can_only_commit_once_when_concurrent(self):
        context = logger.scan_context()

        def commit():
            try:
                return logger.commit_chain_draft([{"rune1": "Death"}], context)
            except ValueError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: commit(), range(2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM new_export").fetchone()[0], 1)

    def test_pending_remnant_keeps_its_expedition_when_independent_chain_advances(self):
        pending = logger.assign_ocr_id("opened")
        logger.commit_chain_draft([{"rune1": "Death"}], logger.scan_context())
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(pending["expedition_id"], "M0001-E01")
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_existing_step_apis_keep_their_expedition_and_sequence(self):
        logger.commit_chain("Rage", "Time")
        saved = logger.commit_chain_runes(["Death", "Power"])
        self.assertEqual(saved["steps"], [2, 3])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.assertEqual([row["rune1"] for row in logger.get_state()["chain"]], ["Rage", "Death", "Power"])
        logger.start_next_chain()
        self.assertEqual(logger.start_next_chain()["current_expedition_id"], "M0001-E03")


if __name__ == "__main__":
    unittest.main()
