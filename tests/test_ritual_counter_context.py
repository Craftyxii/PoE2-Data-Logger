"""Keep first/final Ritual resources intelligible in standalone SQLite Context."""

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from PoE2_Data_Logger.core import analysis_context, logger_store as logger, ritual_counters, store


class RitualCounterContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-ritual-counter-context-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def save(self, tribute, rerolls, fingerprint="a", map_id="M0001"):
        return logger.save_ritual_page(
            [{"name": "Ritual counter context reward", "quantity": 1}],
            scan_hash=fingerprint * 64, expected_map_id=map_id,
            tribute_available=tribute, rerolls_remaining=rerolls)

    @staticmethod
    def context(db, map_id="M0001"):
        return json.loads(db.execute("SELECT guidance FROM analysis_context_map_values WHERE map_id=?",
                                     (map_id,)).fetchone()[0])

    @staticmethod
    def observed_rows(db):
        return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("maps", "ritual_pages", "commits")}

    def test_same_hash_rescans_keep_earliest_approval_in_context_and_standalone_backup(self):
        logger.start_map()
        first = self.save(0, 3)
        logger.record_commit("Map settings")
        self.save(450, 2, "b")
        last = self.save(170, 1)
        self.assertTrue(last["updated"])
        with logger._connect() as db:
            expected = ritual_counters.summary(db, "M0001")
            before = self.observed_rows(db)
            self.assertEqual(self.context(db)["ritual_counters"], expected)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM analysis_context_complete "
                                        "WHERE section='Map settings' AND topic='M0001'").fetchone()[0], 1)
        self.assertEqual(expected["reading_count"], 3)
        self.assertEqual(expected["first_commit_number"], first["scan_commit_number"])
        self.assertEqual(expected["final_commit_number"], last["scan_commit_number"])
        self.assertEqual(expected["first_tribute_available"], 0)
        self.assertEqual(expected["final_tribute_available"], 170)
        target = Path(self.temporary.name) / "standalone.sqlite3"
        target.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(self.context(db)["ritual_counters"], expected)
            self.assertEqual(json.loads(db.execute("SELECT guidance FROM analysis_context_ritual_counters "
                                                  "WHERE map_id='M0001'").fetchone()[0]), expected)
            self.assertEqual(self.observed_rows(db), before)
        with logger._connect() as db:
            self.assertEqual(self.observed_rows(db), before)

    def test_unknown_boundary_fields_zero_and_other_maps_remain_independent(self):
        logger.start_map()
        self.save(None, 4)
        self.save(700, 3, "b")
        self.save(0, None, "c")
        logger.finish_map(1, 2, 3)
        logger.start_map()
        self.save(900, 8, map_id="M0002")
        with logger._connect() as db:
            for mid in ("M0001", "M0002"):
                self.assertEqual(self.context(db, mid)["ritual_counters"], ritual_counters.summary(db, mid))
            first = self.context(db)["ritual_counters"]
            second = self.context(db, "M0002")["ritual_counters"]
        self.assertIsNone(first["first_tribute_available"])
        self.assertEqual(first["first_rerolls_remaining"], 4)
        self.assertEqual(first["final_tribute_available"], 0)
        self.assertIsNone(first["final_rerolls_remaining"])
        self.assertEqual(second["reading_count"], 1)
        self.assertEqual(second["first_tribute_available"], second["final_tribute_available"])

    def test_unscanned_map_has_zero_readings_and_null_boundaries_without_new_observations(self):
        logger.start_map()
        with logger._connect() as db:
            before = self.observed_rows(db)
            result = self.context(db)["ritual_counters"]
            self.assertEqual(result, ritual_counters.summary(db, "M0001"))
            self.assertEqual(result["reading_count"], 0)
            self.assertTrue(all(value is None for key, value in result.items() if key != "reading_count"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM analysis_context_ritual_counters").fetchone()[0], 0)
            self.assertEqual(self.observed_rows(db), before)

    def test_sql_context_uses_its_existing_read_transaction(self):
        logger.start_map()
        self.save(600, 3)
        with logger._connect() as reader:
            reader.execute("BEGIN")
            first = self.context(reader)["ritual_counters"]
            self.save(100, 1, "b")
            self.assertEqual(self.context(reader)["ritual_counters"], first)
        with logger._connect() as db:
            self.assertEqual(self.context(db)["ritual_counters"]["final_tribute_available"], 100)

    def test_exact_counter_and_excel_continuation_headers_have_plain_interpretation_guides(self):
        patterns = [(key, re.compile(pattern), guidance)
                    for key, pattern, _, guidance in analysis_context.COLUMN_GUIDANCE]
        counters = ("Ritual Reading Count", "Ritual First Tribute Available", "Ritual First Rerolls Remaining",
                    "Ritual Final Tribute Available", "Ritual Final Rerolls Remaining", "Ritual First Commit #",
                    "Ritual Final Commit #")
        for header in counters:
            self.assertTrue(any(pattern.fullmatch(header) for _, pattern, _ in patterns), header)
        for prefix in ("", "Rite metadata | ", "Rite metadata | Rite metadata | "):
            for field in ("Ritual Rite Affixes JSON", "Ritual Rite Inherited Sources JSON"):
                self.assertTrue(any(pattern.fullmatch(f"{prefix}{field} continuation 2")
                                    for _, pattern, _ in patterns))
        entries = {key: guidance for key, _, _, guidance in analysis_context.ENTRIES}
        self.assertIn("earliest/latest surviving approved", entries["ritual_boundaries"])
        self.assertIn("Rejected or waiting reviews do not contribute", entries["ritual_boundaries"])


if __name__ == "__main__":
    unittest.main()
