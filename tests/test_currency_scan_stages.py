"""Keep map-owned inventory stage identities separate from cumulative map totals."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


class CurrencyScanStageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-currency-stages-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    @staticmethod
    def rows(data):
        source = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        assert len(source[0]) == len(set(source[0])), "Duplicate export headers"
        assert all(len(row) == len(source[0]) for row in source), "Uneven export rows"
        return [dict(zip(source[0], row)) for row in source[1:]]

    def save(self, amounts, **kwargs):
        return logger.save_currency_snapshot("end", [
            {"name": name, "quantity": amount} for name, amount in amounts.items()], **kwargs)

    def commits(self):
        with logger._connect() as db:
            return [{**dict(row), "details": json.loads(row["details_json"]),
                     "context": json.loads(row["snapshot_json"])}
                    for row in db.execute("SELECT * FROM commits WHERE kind='Currency' ORDER BY number")]

    def test_each_scan_has_map_local_id_and_final_counts_replace_all_prior_items(self):
        self.save({"Divine Orb": 1, "Chaos Orb": 8})
        logger.save_settings({"expedition": 2})
        saved = self.save({"Divine Orb": 3, "Exalted Orb": 2})
        commits = self.commits()
        self.assertEqual([c["details"]["currency_scan_id"] for c in commits], ["M0001-C001", "M0001-C002"])
        self.assertEqual([c["details"]["currency_scan_number"] for c in commits], [1, 2])
        self.assertEqual((saved["currency_scan_id"], saved["currency_scan_number"], saved["scan_commit_number"]),
                         ("M0001-C002", 2, commits[-1]["number"]))
        self.assertEqual([c["expedition_id"] for c in commits], ["", ""])
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Divine Orb": 3, "Exalted Orb": 2})
        self.assertEqual({i["name"]: i["quantity"] for i in logger.session_currency_totals()["items"]},
                         {"Divine Orb": 3, "Exalted Orb": 2})
        history = [r for r in self.rows(logger.export_record_history_csv()) if r["Type"] == "Currency"]
        divine = [r for r in history if r["Currency"] == "Divine Orb"]
        self.assertEqual([r["Quantity"] for r in divine], ["1", "3"])
        self.assertEqual([r["Final Currency Scan"] for r in divine], ["False", "True"])
        self.assertEqual([r["Session Found Quantity"] for r in divine], ["0", "3"])
        summary = self.rows(logger.export_map_summary_csv())[0]
        self.assertEqual((summary["Divine Orb"], summary["Chaos Orb"], summary["Exalted Orb"]), ("3", "0", "2"))

    def test_reopen_keeps_ids_frozen_history_and_settings(self):
        logger.save_settings({"area": 80, "deli": True})
        self.save({"Divine Orb": 1})
        first = self.commits()[0]
        logger._READY = False
        logger.initialize()
        logger.save_settings({"area": 81, "deli": False})
        self.save({"Divine Orb": 3})
        second = self.commits()
        self.assertEqual(second[0], first)
        self.assertEqual(second[1]["details"]["currency_scan_id"], "M0001-C002")
        latest = self.rows(logger.export_currency_csv())[0]
        self.assertEqual((latest["End Currency Scan ID"], latest["End Currency Scan #"]), ("M0001-C002", "2"))
        self.assertEqual(latest["End Count"], "3")

    def test_final_stage_keeps_existing_start_subtraction_for_every_currency(self):
        logger.save_currency_snapshot("start", [{"name": "Divine Orb", "quantity": 1},
                                                {"name": "Chaos Orb", "quantity": 2}])
        self.save({"Divine Orb": 1, "Chaos Orb": 8})
        self.save({"Divine Orb": 3, "Chaos Orb": 7})
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Divine Orb": 2, "Chaos Orb": 5})
        totals = {row["name"]: row["quantity"] for row in logger.session_currency_totals()["items"]}
        self.assertEqual(totals, {"Divine Orb": 2, "Chaos Orb": 5})
        history = [row for row in self.rows(logger.export_record_history_csv()) if row["Type"] == "Currency"]
        self.assertEqual({row["Currency Scan ID"] for row in history},
                         {"M0001-C001", "M0001-C002", "M0001-C003"})
        self.assertEqual({row["Currency Scan ID"] for row in history if row["Final Currency Scan"] == "True"},
                         {"M0001-C003"})

    def test_ids_restart_per_map_and_all_export_identity_columns_are_appended(self):
        self.save({"Divine Orb": 1})
        logger.finish_map(0, 0, 0)
        logger.start_map()
        self.save({"Divine Orb": 3})
        self.assertEqual([c["details"]["currency_scan_id"] for c in self.commits()],
                         ["M0001-C001", "M0002-C001"])
        history = [r for r in self.rows(logger.export_all_csv()) if r["Type"] == "Currency"]
        self.assertEqual([r["Currency Scan ID"] for r in history], ["M0001-C001", "M0002-C001"])
        index = [r for r in self.rows(logger.export_commits_csv()) if r["Type"] == "Currency"]
        self.assertEqual([r["Currency Scan ID"] for r in index], ["M0001-C001", "M0002-C001"])
        headers = next(csv.reader(io.StringIO(logger.export_record_history_csv().decode("utf-8-sig"))))
        self.assertEqual(headers[-3:], list(logger.CURRENCY_STAGE_HEADERS))
        headers = next(csv.reader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        self.assertEqual(headers[-3:], list(logger.CURRENCY_STAGE_HEADERS))

    def test_legacy_identity_projection_does_not_rewrite_saved_history(self):
        self.save({"Divine Orb": 1})
        self.save({"Divine Orb": 3})
        with logger._connect() as db:
            for commit in self.commits():
                details = {key: value for key, value in commit["details"].items()
                           if key not in ("currency_scan_id", "currency_scan_number")}
                db.execute("UPDATE commits SET details_json=? WHERE number=?",
                           (json.dumps(details), commit["number"]))
        before = self.commits()
        history = [r for r in self.rows(logger.export_record_history_csv()) if r["Type"] == "Currency"]
        self.assertEqual([r["Currency Scan ID"] for r in history], ["M0001-C001", "M0001-C002"])
        self.assertEqual(self.commits(), before)
        self.save({"Divine Orb": 4})
        self.assertEqual(self.commits()[-1]["details"]["currency_scan_id"], "M0001-C003")

    def test_failed_save_and_undo_do_not_consume_or_corrupt_stage_identity(self):
        self.save({"Divine Orb": 1})
        before = self.commits()
        with patch.object(logger, "_record_commit", side_effect=RuntimeError("disk write failed")):
            with self.assertRaisesRegex(RuntimeError, "disk write failed"):
                self.save({"Divine Orb": 9})
        self.assertEqual(self.commits(), before)
        self.save({"Divine Orb": 3})
        self.assertEqual(self.commits()[-1]["details"]["currency_scan_id"], "M0001-C002")
        logger.undo_last_action()
        self.assertEqual(self.commits(), before)
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Divine Orb": 1})
        current = [r for r in self.rows(logger.export_record_history_csv()) if r["Type"] == "Currency"]
        self.assertEqual(current[0]["Final Currency Scan"], "True")

    def test_empty_final_scan_has_its_own_identity_and_zero_map_totals(self):
        self.save({"Divine Orb": 3})
        self.save({})
        current = [r for r in self.rows(logger.export_record_history_csv()) if r["Type"] == "Currency"]
        final = [r for r in current if r["Final Currency Scan"] == "True"]
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]["Currency Scan ID"], "M0001-C002")
        self.assertEqual(final[0]["Currency"], "")
        self.assertEqual(logger.session_currency_totals()["items"], [])
        summary = self.rows(logger.export_map_summary_csv())[0]
        self.assertEqual(summary["Divine Orb"], "0")

    def test_currency_context_ignores_expedition_but_preserves_other_activity_validation(self):
        context = logger.scan_context()
        logger.save_settings({"expedition": 2})
        logger.validate_currency_context(context)
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            logger.validate_scan_context(context)
        self.save({"Divine Orb": 3}, expected_map_id="M0001", expected_context=context)
        self.assertEqual(self.commits()[0]["expedition_id"], "")

    def test_stale_map_pending_and_generation_tokens_fail_inside_writer_before_mutation(self):
        context = logger.scan_context()
        logger.mark_next_map(True)
        before = self.commits()
        with self.assertRaisesRegex(ValueError, "map ended"):
            self.save({"Divine Orb": 3}, expected_context=context)
        self.assertEqual(self.commits(), before)
        logger.mark_next_map(False)
        context = logger.scan_context()
        logger.finish_map(0, 0, 0)
        logger.start_map()
        with self.assertRaisesRegex(ValueError, "map changed"):
            self.save({"Divine Orb": 3}, expected_context=context)
        logger.clear_export_and_reset_ids()
        logger.start_map()
        with self.assertRaisesRegex(ValueError, "previous session"):
            self.save({"Divine Orb": 3}, expected_context=context)
        self.assertEqual(self.commits(), [])

    def test_workbook_stage_number_cells_are_numeric_and_ids_remain_text(self):
        self.save({"Divine Orb": 1})
        self.save({"Divine Orb": 3})
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            for sheet in ("xl/worksheets/sheet3.xml",):
                root = ET.fromstring(archive.read(sheet))
                rows = root.findall(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row")
                headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext()) for cell in rows[0]}
                seen = False
                for row in rows[1:]:
                    for cell in row:
                        header = headers[cell.get("r").rstrip("0123456789")]
                        if header == "Currency Scan #" and "".join(cell.itertext()):
                            self.assertIsNone(cell.get("t"))
                            seen = True
                        if header == "Currency Scan ID" and "".join(cell.itertext()):
                            self.assertEqual(cell.get("t"), "inlineStr")
                self.assertTrue(seen, sheet)


if __name__ == "__main__":
    unittest.main()
