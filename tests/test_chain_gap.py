"""Persisted same-chain timing checks without OCR, UI completion or new writes."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import chain_gap, logger_store as logger, store


class ChainGapTests(unittest.TestCase):
    """Measure only accepted entries belonging to the captured live open chain."""

    def setUp(self):
        """Start an isolated map with a fixed UTC clock for accepted entries."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-chain-gap-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.entered = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        """Restore the data directory after closing the isolated test profile."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def append(self, seconds=0):
        """Append a real accepted step at a known persisted timestamp."""
        with patch.object(logger, "_now", return_value=(self.entered + timedelta(seconds=seconds)).isoformat()):
            return logger.commit_chain("Death")

    def test_strictly_more_than_sixty_seconds_requires_confirmation(self):
        """Exactly sixty seconds remains part of the ordinary uninterrupted flow."""
        saved = self.append()
        context = logger.scan_context()
        for seconds, expected in ((59, False), (60, False), (60.1, True), (61, True)):
            with self.subTest(seconds=seconds):
                result = chain_gap.inspect_gap(context, self.entered + timedelta(seconds=seconds))
                self.assertEqual(result.needs_prompt, expected)
                self.assertEqual(result.last_commit_number, saved["scan_commit_number"])
                self.assertEqual(result.elapsed_seconds, seconds)
                self.assertEqual((result.map_id, result.expedition_id), ("M0001", "M0001-E01"))
                self.assertEqual(result.recorded_at, self.entered.isoformat())

    def test_latest_accepted_chain_step_resets_the_gap(self):
        """The last saved step, rather than the first step, establishes the interval."""
        self.append()
        saved = self.append(50)
        result = chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(seconds=80))
        self.assertFalse(result.needs_prompt)
        self.assertEqual(result.elapsed_seconds, 30)
        self.assertEqual(result.last_commit_number, saved["scan_commit_number"])

    def test_settings_correction_and_propagation_records_do_not_reset_entry_time(self):
        """Only a new accepted Chain entry can restart the timeout interval."""
        saved = self.append()
        with patch.object(logger, "_now", return_value=(self.entered + timedelta(seconds=60)).isoformat()):
            logger.record_commit("Map settings")
            logger.update_chain_steps([{"step": 1, "rune1": "Power"}])
            logger.increment_propagation_detonated(logger.scan_context(), runes=["Time"])
        result = chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(seconds=61))
        self.assertTrue(result.needs_prompt)
        self.assertEqual(result.last_commit_number, saved["scan_commit_number"])

    def test_an_empty_chain_has_no_timing_prompt(self):
        """A first entry must not be confused with a continuation of an older chain."""
        result = chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(days=1))
        self.assertIsNone(result.last_commit_number)
        self.assertIsNone(result.elapsed_seconds)
        self.assertFalse(result.needs_prompt)

    def test_discarded_chain_history_does_not_prompt_an_empty_chain(self):
        """Retained Chain audit events do not turn a fully discarded chain into a live one."""
        self.append()
        logger.discard_chain()
        self.assertFalse(chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(days=1)).needs_prompt)

    def test_completed_chain_does_not_prompt_when_selected_again(self):
        """A completed chain retains its history without being treated as an open continuation."""
        self.append()
        logger.complete_chain(logger.scan_context())
        logger.save_settings({"expedition": 1})
        self.assertFalse(chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(days=1)).needs_prompt)

    def test_restart_uses_persisted_entry_time(self):
        """The interval survives process-style logger reinitialization without a memory clock."""
        self.append()
        logger._READY = False
        logger.initialize()
        self.assertTrue(chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(seconds=61)).needs_prompt)

    def test_imported_style_rows_without_a_chain_audit_keep_timing_unknown(self):
        """Missing historical entry times never become an invented timeout interval."""
        self.append()
        with logger._connect() as db:
            db.execute("DELETE FROM commits WHERE kind='Chain'")
        result = chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(days=1))
        self.assertIsNone(result.recorded_at)
        self.assertFalse(result.needs_prompt)

    def test_other_expedition_and_map_history_are_not_used(self):
        """Only this map's selected captured expedition can establish entry timing."""
        self.append()
        logger.save_settings({"expedition": 2})
        self.assertFalse(chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(days=1)).needs_prompt)
        logger.finish_map(0, 0, 0)
        logger.start_map()
        self.assertFalse(chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(days=1)).needs_prompt)

    def test_stale_generation_map_expedition_and_revision_are_rejected(self):
        """The question cannot validate a capture after its saved ownership changes."""
        self.append()
        context = logger.scan_context()
        for key, value in (("_scan_generation", context["_scan_generation"] + 1),
                           ("_capture_map_id", "M0999"), ("_capture_expedition", 2),
                           ("_capture_map_pending", True), ("_capture_chain_revision", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                chain_gap.inspect_gap({**context, key: value}, self.entered + timedelta(seconds=61)).needs_prompt

    def test_equivalent_timezones_and_backward_clock_do_not_invent_a_gap(self):
        """UTC normalization compares instants, while an earlier wall clock is not a timeout."""
        self.append()
        shifted = (self.entered + timedelta(seconds=61)).astimezone(timezone(timedelta(hours=-6)))
        self.assertTrue(chain_gap.inspect_gap(logger.scan_context(), shifted).needs_prompt)
        self.assertFalse(chain_gap.inspect_gap(logger.scan_context(), self.entered - timedelta(days=1)).needs_prompt)

    def test_missing_context_and_ambiguous_clock_are_rejected(self):
        """Ownership and the timing reference must both be explicit and unambiguous."""
        with self.assertRaisesRegex(ValueError, "capture context"):
            chain_gap.inspect_gap(None, self.entered).needs_prompt
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            chain_gap.inspect_gap(logger.scan_context(), self.entered.replace(tzinfo=None)).needs_prompt

    def test_inspection_does_not_change_saved_history_or_settings(self):
        """The question helper only reads; it cannot complete, append, or alter the chain."""
        self.append()
        with logger._connect() as db:
            before = list(db.iterdump())
        self.assertTrue(chain_gap.inspect_gap(logger.scan_context(), self.entered + timedelta(seconds=61)).needs_prompt)
        with logger._connect() as db:
            self.assertEqual(list(db.iterdump()), before)


if __name__ == "__main__":
    unittest.main()
