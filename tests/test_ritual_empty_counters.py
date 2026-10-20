"""Save approved Ritual resources without fabricating reward rows or learned names."""

from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import unittest

from tests import test_review_learning_ui as ui_fixtures
from tests import test_ritual_counters as counter_fixtures
from PoE2_Data_Logger.core import logger_store as logger, ritual_counters


def rows(producer):
    data = list(csv.reader(io.StringIO(producer().decode("utf-8-sig"))))
    assert len(data[0]) == len(set(data[0]))
    assert all(len(row) == len(data[0]) for row in data)
    return [dict(zip(data[0], row)) for row in data[1:]]


class EmptyRitualCounterStorageTests(unittest.TestCase):
    setUp = counter_fixtures.RitualCounterSummaryTests.setUp
    tearDown = counter_fixtures.RitualCounterSummaryTests.tearDown

    @staticmethod
    def saved_rows():
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("ritual_pages", "commits", "review_icon_examples", "ritual_names", "item_names")}

    def test_zero_and_one_known_counter_save_empty_pages_without_names_or_learning(self):
        logger.start_map()
        before = self.saved_rows()
        for tribute, rerolls in ((0, 0), (None, 0), (25, None)):
            with self.subTest(tribute=tribute, rerolls=rerolls):
                saved = logger.save_ritual_page([], tribute_available=tribute, rerolls_remaining=rerolls,
                                               register_names=True)
                self.assertEqual(saved["count"], 0)
                self.assertEqual(logger.ritual_pages_for_map("M0001")[-1]["items"], [])
        after = self.saved_rows()
        for table in ("review_icon_examples", "ritual_names", "item_names"):
            self.assertEqual(after[table], before[table])
        exported = rows(logger.export_ritual_csv)
        self.assertEqual(len(exported), 3)
        self.assertEqual([(row["Ritual Tribute Available"], row["Ritual Rerolls Remaining"])
                          for row in exported], [("0", "0"), ("", "0"), ("25", "")])
        empty_fields = ("Type", "Name", "Quantity", "Tribute", "OCR Source", "Deferred", "New Find Quantity")
        self.assertTrue(all(row[key] == "" for row in exported for key in empty_fields))

    def test_empty_same_hash_boundaries_history_backup_and_undo_are_atomic(self):
        logger.start_map()
        first = logger.save_ritual_page([], "Empty initial page", "a" * 64, "M0001", 0, 3)
        logger.save_ritual_page([{"name": "Chaos Orb", "quantity": 2}], "Reward page", "b" * 64,
                               "M0001", 700, 2)
        final = logger.save_ritual_page([], "Empty final page", "a" * 64, "M0001", 0, 0)
        self.assertTrue(final["updated"])
        self.assertEqual(final["page_number"], first["page_number"])
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 2)
        with logger._connect() as db:
            summary = ritual_counters.summary(db, "M0001")
        self.assertEqual((summary["reading_count"], summary["first_tribute_available"],
                          summary["first_rerolls_remaining"], summary["final_tribute_available"],
                          summary["final_rerolls_remaining"]), (3, 0, 3, 0, 0))
        for producer in (logger.export_record_history_csv, logger.export_all_csv):
            history = [row for row in rows(producer) if row["Type"] == "Ritual"]
            self.assertEqual(len(history), 3)
            self.assertEqual([row["Reward Name"] for row in history], ["", "Chaos Orb", ""])
            self.assertEqual([row["Ritual Rerolls Remaining"] for row in history], ["3", "2", "0"])
        separate = rows(logger.export_ritual_csv)
        self.assertEqual([row["Name"] for row in separate], ["", "Chaos Orb"])
        self.assertEqual(separate[0]["Page OCR Text"], "Empty final page")
        self.assertEqual(separate[0]["Ritual Rerolls Remaining"], "0")
        summary_row = rows(logger.export_map_summary_csv)[0]
        self.assertEqual(summary_row["Ritual First Rerolls Remaining"], "3")
        self.assertEqual(summary_row["Ritual Final Rerolls Remaining"], "0")
        backup = Path(self.temporary.name) / "empty-page-backup.sqlite3"
        backup.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(backup)) as db:
            details = [json.loads(row[0]) for row in db.execute(
                "SELECT details_json FROM commits WHERE kind='Ritual' ORDER BY number")]
        self.assertEqual([row["items"] for row in details][::2], [[], []])
        logger.undo_last_action()
        with logger._connect() as db:
            undone = ritual_counters.summary(db, "M0001")
        self.assertEqual((undone["reading_count"], undone["final_tribute_available"]), (2, 700))
        self.assertEqual(rows(logger.export_ritual_csv)[0]["Page OCR Text"], "Empty initial page")

    def test_unknown_invalid_and_wrong_map_empty_readings_do_not_write(self):
        with self.assertRaisesRegex(ValueError, "Start the current map"):
            logger.save_ritual_page([], tribute_available=0)
        logger.start_map()
        before = self.saved_rows()
        attempts = ({}, {"tribute_available": -1}, {"tribute_available": "bad"},
                    {"rerolls_remaining": -1}, {"rerolls_remaining": 1.5},
                    {"tribute_available": 0, "expected_map_id": "M0002"})
        for arguments in attempts:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                logger.save_ritual_page([], **arguments)
            self.assertEqual(self.saved_rows(), before)


class EmptyRitualCounterUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui_fixtures.ReviewLearningUITests.setUpClass()
        cls.app = ui_fixtures.ReviewLearningUITests.app

    setUp = ui_fixtures.ReviewLearningUITests.setUp
    tearDown = ui_fixtures.ReviewLearningUITests.tearDown

    def read(self, tribute, rerolls, *, live=False):
        self.window._ritual_captured(self.page, live=live)
        self.jobs[-1][1]({"items": [], "raw_text": "Empty Ritual page", "unmatched": [],
                          "tribute_available": tribute, "rerolls_remaining": rerolls})

    def test_approve_empty_zero_page_saves_only_counters_with_manual_approval(self):
        self.window.state["settings"]["ocr_auto_commit"] = True
        self.read(0, 0, live=True)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.window.approve_review()
        self.assertEqual(logger.ritual_pages_for_map("M0001")[0]["items"], [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.review_icons(), [])
        exported = rows(logger.export_ritual_csv)[0]
        self.assertEqual((exported["Name"], exported["Quantity"], exported["Ritual Tribute Available"],
                          exported["Ritual Rerolls Remaining"]), ("", "", "0", "0"))

    def test_one_readable_counter_survives_while_unknown_counter_stays_blank(self):
        self.read(None, 0)
        self.window.approve_review()
        exported = rows(logger.export_ritual_csv)[0]
        self.assertEqual((exported["Ritual Tribute Available"], exported["Ritual Rerolls Remaining"]), ("", "0"))

    def test_both_unknown_empty_page_and_explicit_reject_write_nothing(self):
        self.read(None, None)
        self.window.approve_review()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.read(1500, 4)
        self.window.reject_review()
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_invalid_empty_header_retains_review_and_writes_nothing(self):
        self.read(100, None)
        self.window.ritual_tribute.setText("-1")
        with self.assertRaises(ValueError):
            self.window.approve_review()
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)


if __name__ == "__main__":
    unittest.main()
