"""Exercise session transitions interleaved with logger reads and remaining writes."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store


class StorageConcurrencyAuditTests(unittest.TestCase):
    """Keep each race isolated in a real WAL database with existing map activity."""

    def setUp(self):
        """Create a temporary profile containing a map and recorded totals."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-storage-races-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.start_map()
        logger.save_counts(12, 3, 1, 4, unique=2)

    def tearDown(self):
        """Restore the caller's profile and remove all temporary database files."""
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def reset_after_map_read(self):
        """Return a metadata hook that resets from another connection after the first map lookup."""
        original = logger._meta
        reset = False

        def read_then_reset(db, key, default=None):
            """Finish one read, commit a reset elsewhere, then resume the original reader."""
            nonlocal reset
            value = original(db, key, default)
            if key == "current_map_number" and not reset:
                reset = True
                logger.clear_export_and_reset_ids()
            return value

        return read_then_reset

    def test_scan_capture_cannot_mix_old_map_with_new_session_generation(self):
        """Keep capture tokens from one instant even when a reset commits between fields."""
        before = logger.scan_context()
        with patch.object(logger, "_meta", side_effect=self.reset_after_map_read()):
            captured = logger.scan_context()
        self.assertEqual(captured, before)
        self.assertEqual(logger.scan_context()["_capture_map_id"], None)
        with self.assertRaisesRegex(ValueError, "previous session"):
            logger.validate_scan_context(captured)

    def test_state_cannot_mix_old_map_with_cleared_counts_and_history(self):
        """Read map IDs, kills, commit counters and chain data from the same database snapshot."""
        before = logger.get_state()
        with patch.object(logger, "_meta", side_effect=self.reset_after_map_read()):
            captured = logger.get_state()
        self.assertEqual(captured, before)
        self.assertEqual(logger.get_state()["current_map_id"], "")

    def interleave_reset(self, operation):
        """Attempt a competing reset after target selection, retrying once if the writer owns the lock."""
        connect, meta = logger._connect, logger._meta
        attempted = delayed = False

        @contextmanager
        def impatient_connection():
            """Fail immediately on a write conflict so this single-threaded interleaving cannot deadlock."""
            with connect() as db:
                db.execute("PRAGMA busy_timeout=0")
                yield db

        def reset_after_target(db, key, default=None):
            """Commit a competing reset at the first map read when SQLite permits that ordering."""
            nonlocal attempted, delayed
            value = meta(db, key, default)
            if key == "current_map_number" and not attempted:
                attempted = True
                try:
                    logger.clear_export_and_reset_ids()
                except sqlite3.OperationalError as error:
                    if "locked" not in str(error).lower():
                        raise
                    delayed = True
            return value

        with patch.object(logger, "_connect", impatient_connection), patch.object(
                logger, "_meta", side_effect=reset_after_target):
            operation()
        if delayed:
            logger.clear_export_and_reset_ids()
        self.assertTrue(attempted)

    def test_detonated_write_cannot_resurrect_an_expedition_after_reset(self):
        """Serialize target selection with its count write so reset never leaves orphan activity."""
        self.interleave_reset(lambda: logger.save_detonated(9))
        with logger._connect() as db:
            for table in ("maps", "expeditions", "commits"):
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_next_map_marker_cannot_survive_a_reset_of_its_current_map(self):
        """Keep the next-map marker false after a reset, including a competing marker toggle."""
        self.interleave_reset(lambda: logger.mark_next_map(True))
        state = logger.get_state()
        self.assertEqual(state["current_map_id"], "")
        self.assertFalse(state["pending_new_map"])


if __name__ == "__main__":
    unittest.main()
