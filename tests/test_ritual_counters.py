"""Reduce map-owned Ritual counter snapshots independently of reward page reuse."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, ritual_counters, store


class RitualCounterSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-ritual-counters-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    @staticmethod
    def save(tribute, rerolls, *, fingerprint="a", map_id="M0001"):
        return logger.save_ritual_page(
            [{"name": "Counter audit reward", "quantity": 1}],
            scan_hash=fingerprint * 64, expected_map_id=map_id,
            tribute_available=tribute, rerolls_remaining=rerolls)

    @staticmethod
    def summary(map_id="M0001"):
        with logger._connect() as db:
            return ritual_counters.summary(db, map_id)

    def test_same_hash_correction_preserves_first_zero_and_uses_final_commit_not_page_number(self):
        logger.start_map()
        first = self.save(0, 3)
        middle = self.save(600, 2, fingerprint="b")
        final = self.save(275, 1)
        self.assertTrue(final["updated"])
        self.assertEqual(final["page_number"], 1)
        self.assertEqual(middle["page_number"], 2)
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 2)
        self.assertEqual(self.summary(), {
            "reading_count": 3,
            "first_commit_number": first["scan_commit_number"],
            "final_commit_number": final["scan_commit_number"],
            "first_tribute_available": 0, "first_rerolls_remaining": 3,
            "final_tribute_available": 275, "final_rerolls_remaining": 1})
        with logger._connect() as db:
            history = [json.loads(row[0]) for row in db.execute(
                "SELECT details_json FROM commits WHERE kind='Ritual' ORDER BY number")]
        self.assertEqual([row["tribute_available"] for row in history], [0, 600, 275])
        self.assertEqual([row["rerolls_remaining"] for row in history], [3, 2, 1])

    def test_final_zero_counters_are_snapshots_instead_of_sums(self):
        logger.start_map()
        self.save(1000, 4)
        self.save(0, 0, fingerprint="b")
        result = self.summary()
        self.assertEqual((result["first_tribute_available"], result["first_rerolls_remaining"]), (1000, 4))
        self.assertEqual((result["final_tribute_available"], result["final_rerolls_remaining"]), (0, 0))

    def test_boundary_fields_remain_unknown_independently_without_carrying_other_readings(self):
        logger.start_map()
        self.save(None, 4)
        self.save(200, 3, fingerprint="b")
        self.save(50, None, fingerprint="c")
        result = self.summary()
        self.assertIsNone(result["first_tribute_available"])
        self.assertEqual(result["first_rerolls_remaining"], 4)
        self.assertEqual(result["final_tribute_available"], 50)
        self.assertIsNone(result["final_rerolls_remaining"])

    def test_legacy_missing_fields_stay_blank_even_with_known_interior_readings(self):
        logger.start_map()
        with logger._connect() as db:
            logger._record_commit(db, "Ritual", "M0001", details={"page": 1})
        self.save(900, 5)
        with logger._connect() as db:
            logger._record_commit(db, "Ritual", "M0001", details={"page": 1, "rerolls_remaining": 0})
        result = self.summary()
        self.assertEqual(result["reading_count"], 3)
        self.assertIsNone(result["first_tribute_available"])
        self.assertIsNone(result["first_rerolls_remaining"])
        self.assertIsNone(result["final_tribute_available"])
        self.assertEqual(result["final_rerolls_remaining"], 0)

    def test_readings_remain_bound_to_their_original_map(self):
        logger.start_map()
        self.save(1500, 3)
        self.save(200, 0, fingerprint="b")
        logger.finish_map(0, 0, 0)
        logger.start_map()
        second = self.save(8000, 10, map_id="M0002")
        first_map, second_map = self.summary(), self.summary("M0002")
        self.assertEqual((first_map["first_tribute_available"], first_map["final_tribute_available"]), (1500, 200))
        self.assertEqual(first_map["reading_count"], 2)
        self.assertEqual(second_map["reading_count"], 1)
        self.assertEqual(second_map["first_commit_number"], second["scan_commit_number"])
        self.assertEqual(second_map["first_tribute_available"], second_map["final_tribute_available"])
        self.assertEqual(second_map["final_tribute_available"], 8000)

    def test_unstarted_or_unscanned_map_has_only_unknown_boundaries_without_writes(self):
        with logger._connect() as db:
            changes = db.total_changes
            for map_id in (None, "", "M0001", "M9999"):
                with self.subTest(map_id=map_id):
                    result = ritual_counters.summary(db, map_id)
                    self.assertEqual(result["reading_count"], 0)
                    self.assertTrue(all(value is None for key, value in result.items() if key != "reading_count"))
            self.assertEqual(db.total_changes, changes)
            self.assertFalse(db.in_transaction)

    def test_export_read_transaction_owns_one_snapshot_without_opening_another_connection(self):
        logger.start_map()
        self.save(1000, 4)
        with logger._connect() as reader:
            reader.execute("BEGIN")
            original = ritual_counters.summary(reader, "M0001")
            self.save(300, 1, fingerprint="b")
            changes = reader.total_changes
            with patch.object(logger, "_connect", side_effect=AssertionError("Separate export connection")), \
                    patch.object(store, "_connect", side_effect=AssertionError("Separate store connection")):
                self.assertEqual(ritual_counters.summary(reader, "M0001"), original)
            self.assertTrue(reader.in_transaction)
            self.assertEqual(reader.total_changes, changes)
        latest = self.summary()
        self.assertEqual(latest["reading_count"], 2)
        self.assertEqual((latest["final_tribute_available"], latest["final_rerolls_remaining"]), (300, 1))


if __name__ == "__main__":
    unittest.main()
