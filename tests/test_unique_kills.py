"""Storage/export checks distinguishing unknown, zero and saved kill counts, including unique kills and legacy callers."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store


class UniqueKillsTests(unittest.TestCase):
    """Check nullable unique counts, legacy callers, rollback and per-map persistence."""
    def setUp(self):
        """Initialize a temporary database with a clean active map."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-unique-kills-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Restore the data directory and remove the temporary kill-count database."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def records(self):
        """Snapshot metadata, maps, expeditions, unique counts and commits for rollback checks."""
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("meta", "maps", "expeditions", "map_unique_kills", "commits")}

    def test_unknown_and_zero_are_distinct_and_kills_remain_three(self):
        """Verify explicit zero unique kills remains distinct from unknown and separate from other kills."""
        self.assertIsNone(logger.get_state()["unique_kills"])
        result = logger.save_kills(100, 10, 3, unique=0)
        self.assertEqual(result["unique_kills"], 0)
        self.assertEqual(logger.get_state()["kills"], [100, 10, 3])
        self.assertEqual(logger.get_state()["unique_kills"], 0)
        with logger._connect() as db:
            details = json.loads(db.execute("SELECT details_json FROM commits WHERE kind='Map kills'").fetchone()[0])
        self.assertEqual(details, {"kills": [100, 10, 3], "unique_kills": 0})

    def test_old_callers_preserve_unique_and_fourth_positional_remains_detonated(self):
        """Verify legacy calls preserve unique kills and the fourth positional detonated count."""
        logger.save_kills(1, 2, 3, unique=4)
        self.assertEqual(logger.save_kills(5, 6, 7)["unique_kills"], 4)
        result = logger.save_counts(8, 9, 10, 11)
        self.assertEqual((result["kills"], result["detonated"], result["unique_kills"]), ([8, 9, 10], 11, 4))
        state = logger.finish_map(12, 13, 14, 15)
        self.assertEqual((state["kills"], state["detonated"], state["unique_kills"]), ([12, 13, 14], 15, 4))
        with logger._connect() as db:
            details = [json.loads(row[0]) for row in db.execute("SELECT details_json FROM commits ORDER BY number")]
        self.assertEqual([item["unique_kills"] for item in details], [4, 4, 4, 4])

    def test_explicit_blank_and_none_clear_but_omission_preserves(self):
        """Verify blank or None clears unique kills while omitted arguments preserve them."""
        logger.save_counts(0, 0, 0, 0, unique=2)
        logger.save_kills(0, 0, 0, unique="")
        self.assertIsNone(logger.get_state()["unique_kills"])
        logger.save_kills(0, 0, 0, unique="3")
        logger.save_counts(0, 0, 0, 0)
        self.assertEqual(logger.get_state()["unique_kills"], 3)
        logger.finish_map(0, 0, 0, 0, unique=None)
        self.assertIsNone(logger.get_state()["unique_kills"])

    def test_invalid_unique_rolls_back_entire_save_or_finish(self):
        """Verify invalid unique counts leave all save and finish state unchanged."""
        logger.save_counts(1, 2, 3, 4, unique=5)
        with logger._connect() as db:
            logger._set_meta(db, "ocr_pending", {"remnant_id": "R0001"})
        before = self.records()
        for value in (-1, True, 1.5, "lots", "0.5"):
            for function in (lambda: logger.save_kills(9, 9, 9, unique=value),
                             lambda: logger.save_counts(9, 9, 9, 9, unique=value),
                             lambda: logger.finish_map(9, 9, 9, 9, unique=value)):
                with self.subTest(value=value, function=function), self.assertRaises(ValueError):
                    function()
                self.assertEqual(self.records(), before)

    def test_each_map_keeps_its_unique_total_and_reset_removes_all(self):
        """Verify each map owns its unique total and export reset removes all totals."""
        logger.finish_map(10, 2, 1, 3, unique=4)
        logger.start_map()
        self.assertIsNone(logger.get_state()["unique_kills"])
        logger.save_kills(20, 4, 2, unique=7)
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM map_unique_kills ORDER BY map_id")],
                             [("M0001", 4), ("M0002", 7)])
        logger.clear_export_and_reset_ids()
        self.assertIsNone(logger.get_state()["unique_kills"])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM map_unique_kills").fetchone()[0], 0)
        logger.start_map()
        self.assertIsNone(logger.get_state()["unique_kills"])

    def test_unique_only_activity_blocks_first_waystone_exception_and_undo(self):
        """Verify a saved unique count blocks empty-map undo and late waystone replacement."""
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 1}])
        logger.save_kills(None, None, None, unique=0)
        with logger._connect() as db:
            original = db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0]
        logger.save_settings({"tier": 16, "waystone": 87, "map_mods": 5})
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0], original)
        with self.assertRaisesRegex(ValueError, "saved activity"):
            logger.undo_empty_map()

    def test_undo_checks_unique_table_even_without_commit_and_cleans_empty_row(self):
        """Verify unique-table activity blocks undo while an unknown row is removable."""
        with logger._connect() as db:
            db.execute("INSERT INTO map_unique_kills VALUES('M0001',0)")
        with self.assertRaisesRegex(ValueError, "saved activity"):
            logger.undo_empty_map()
        with logger._connect() as db:
            db.execute("UPDATE map_unique_kills SET unique_kills=NULL")
        self.assertEqual(logger.undo_empty_map()["current_map_id"], "")
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM map_unique_kills").fetchone()[0], 0)

    def test_schema_upgrade_leaves_existing_map_history_unknown_and_unchanged(self):
        """Verify schema upgrade preserves old map records and leaves unique kills unknown."""
        logger.save_counts(10, 2, 1, 3)
        with logger._connect() as db:
            original = tuple(db.execute("SELECT * FROM maps WHERE map_id='M0001'").fetchone())
            db.execute("DROP TABLE map_unique_kills")
        logger._READY = False
        logger.initialize()
        self.assertIsNone(logger.get_state()["unique_kills"])
        self.assertEqual(logger.get_state()["kills"], [10, 2, 1])
        self.assertEqual(logger.get_state()["detonated"], 3)
        with logger._connect() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM maps WHERE map_id='M0001'").fetchone()), original)

    def test_backup_preserves_nullable_unique_and_propagation_counter(self):
        """Verify backups retain explicit zero unique kills and propagation detonation counts."""
        logger.save_kills(10, 2, 1, unique=0)
        logger.increment_propagation_detonated(logger.scan_context(), runes=["Rage", "Time"])
        backup = Path(self.tmp.name) / "backup.sqlite3"
        backup.write_bytes(logger.backup_bytes())
        db = sqlite3.connect(backup)
        try:
            self.assertEqual(db.execute("SELECT unique_kills FROM map_unique_kills WHERE map_id='M0001'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT detonated FROM expeditions WHERE expedition_id='M0001-E01'").fetchone()[0], 1)
            self.assertEqual(json.loads(db.execute("SELECT kills_json FROM maps WHERE map_id='M0001'").fetchone()[0]),
                             [10, 2, 1])
        finally:
            db.close()

    def test_commit_failure_rolls_back_new_unique_value(self):
        """Verify commit failure rolls back the attempted unique-count update."""
        logger.save_kills(1, 2, 3, unique=4)
        before = self.records()
        with patch.object(logger, "_record_commit", side_effect=RuntimeError("failed")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                logger.save_counts(9, 9, 9, 9, unique=9)
        self.assertEqual(self.records(), before)


if __name__ == "__main__":
    unittest.main()
