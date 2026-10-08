"""End-map-only currency scans must agree across saved state, totals and exports."""
import csv
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


class CurrencyEndOnlyExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-end-only-currency-")
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

    def snapshot(self, phase, **quantities):
        return logger.save_currency_snapshot(phase, [
            {"name": name, "quantity": amount} for name, amount in quantities.items()])

    def rows(self, data):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def assert_workbook_matches_main(self):
        main = self.rows(logger.export_all_csv())
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        ns = {"s": workbook_export.NS}
        rows = root.findall("s:sheetData/s:row", ns)
        headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext()) for cell in rows[0]}
        result = []
        for row in rows[1:]:
            values = {name: "" for name in headers.values()}
            for cell in row:
                name = headers[cell.get("r").rstrip("0123456789")]
                values[name] = "".join(cell.itertext())
                self.assertIsNone(cell.find("s:f", ns))
                if name in ("Start Count", "End Count", "Net Change", "Quantity", "Session Found Quantity") or name.startswith(
                        ("Currency: ", "Item: ", "Omen: ")):
                    self.assertIsNone(cell.get("t"), f"{name} must remain a numeric cell")
            result.append(values)
        self.assertEqual(result, main)

    def test_end_only_currencies_and_learned_items_have_counts_in_every_export(self):
        logger.add_currency_item("New Session Token")
        logger.add_item_name("Session Equipment")
        saved = logger.save_currency_snapshot("end", [
            {"name": "Chaos Orb", "quantity": 2}, {"name": "chaos orb", "quantity": 3},
            {"name": "Divine Orb", "quantity": 1}, {"name": "New Session Token", "quantity": 8},
            {"name": "Session Equipment", "quantity": 2}], expected_map_id="M0001")
        expected = {"Chaos Orb": 5, "Divine Orb": 1, "New Session Token": 8, "Session Equipment": 2}
        self.assertEqual((saved["start"], saved["end"], saved["net"]), ({}, expected, expected))
        result = logger.session_currency_totals()
        self.assertEqual({row["name"]: row["quantity"] for row in result["items"]}, expected)
        self.assertEqual((result["maps_counted"], result["maps_assumed_empty"]), (1, 1))
        for data in (logger.export_currency_csv(), logger.export_record_history_csv(), logger.export_all_csv()):
            rows = self.rows(data)
            inventory = [row for row in rows if row.get("Inventory Phase", "end") == "end"]
            self.assertEqual(len(inventory), len(expected))
            for row in inventory:
                name = row.get("Item Name") or row["Currency"]
                self.assertEqual(row["Map ID"], "M0001")
                self.assertEqual(row["Start Baseline"], "Assumed empty")
                self.assertEqual((row["Start Count"], row["End Count"], row["Net Change"]),
                                 ("0", str(expected[name]), str(expected[name])))
                self.assertEqual(row["Session Found Quantity"], str(expected[name]))
                self.assertEqual(row["Start Recorded UTC"], "")
                self.assertTrue(row["End Recorded UTC"])
        main = self.rows(logger.export_all_csv())
        for name, amount in expected.items():
            kind = "Item" if name == "Session Equipment" else "Currency"
            matches = [row for row in main if row[f"{kind}: {name}"]]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0][f"{kind}: {name}"], str(amount))
        with logger._connect() as db:
            snapshot = db.execute("SELECT items_json FROM currency_snapshots WHERE map_id='M0001' "
                                  "AND phase='end'").fetchone()
            self.assertEqual(json.loads(snapshot[0]), expected)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM currency_snapshots WHERE phase='start'").fetchone()[0], 0)
            details = json.loads(db.execute("SELECT details_json FROM commits WHERE kind='Currency'").fetchone()[0])
            self.assertEqual(details["start_baseline"], "Assumed empty")
        self.assert_workbook_matches_main()

    def test_session_found_columns_reconcile_repeats_late_starts_and_mixed_maps(self):
        logger.add_currency_item("Counter Token")
        logger.add_item_name("Counter Equipment")
        logger.add_ritual_name("Omen of Counter Tests")
        first = {"Chaos Orb": 10, "Counter Token": 5, "Counter Equipment": 2,
                 "Omen of Counter Tests": 2}
        self.snapshot("end", **first)
        self.snapshot("end", **first)
        self.snapshot("end", **{"Chaos Orb": 7, "Counter Token": 3, "Counter Equipment": 1,
                                "Omen of Counter Tests": 1})
        logger.finish_map(0, 0, 0)
        logger.start_map()
        self.snapshot("start", **{"Chaos Orb": 7, "Divine Orb": 3, "Counter Token": 1})
        self.snapshot("end", **{"Chaos Orb": 11, "Divine Orb": 2, "Counter Token": 9,
                                "Exalted Orb": 4, "Counter Equipment": 2})
        logger.finish_map(0, 0, 0)
        logger.start_map()
        self.snapshot("end", **{"Chaos Orb": 10})
        self.snapshot("start", **{"Chaos Orb": 8})
        expected = {"Chaos Orb": 13, "Counter Token": 11, "Counter Equipment": 3,
                    "Omen of Counter Tests": 1, "Exalted Orb": 4}
        self.assertEqual({row["name"]: row["quantity"] for row in logger.session_currency_totals()["items"]},
                         expected)
        for exporter in (logger.export_currency_csv, logger.export_record_history_csv, logger.export_all_csv):
            totals = {}
            for row in self.rows(exporter()):
                name = row.get("Item Name") or row.get("Currency")
                amount = int(row["Session Found Quantity"] or 0)
                if amount:
                    self.assertEqual(row.get("Current Inventory Snapshot", "True"), "True")
                    totals[name] = totals.get(name, 0) + amount
            self.assertEqual(totals, expected, exporter.__name__)
        history = self.rows(logger.export_record_history_csv())
        first_chaos = [row for row in history if row["Map ID"] == "M0001" and row["Currency"] == "Chaos Orb"]
        self.assertEqual([row["Quantity"] for row in first_chaos], ["10", "10", "7"])
        self.assertEqual([row["Current Inventory Snapshot"] for row in first_chaos], ["False", "False", "True"])
        self.assertEqual([row["Session Found Quantity"] for row in first_chaos], ["0", "0", "7"])
        late = next(row for row in history if row["Map ID"] == "M0003" and row["Inventory Phase"] == "end")
        self.assertEqual((late["Net Change"], late["Start Baseline"], late["Session Found Quantity"]),
                         ("10", "Assumed empty", "2"))
        self.assert_workbook_matches_main()

    def test_repeated_end_scans_replace_latest_counts_but_preserve_each_approval(self):
        self.snapshot("end", **{"Chaos Orb": 10, "Divine Orb": 2})
        self.snapshot("end", **{"Chaos Orb": 10, "Divine Orb": 2})
        self.snapshot("end", **{"Chaos Orb": 7})
        logger.finish_map(0, 0, 0)
        logger.start_map()
        self.snapshot("end", **{"Chaos Orb": 3, "Exalted Orb": 4})
        result = logger.session_currency_totals()
        self.assertEqual({row["name"]: row["quantity"] for row in result["items"]},
                         {"Chaos Orb": 10, "Exalted Orb": 4})
        self.assertEqual((result["maps_counted"], result["maps_assumed_empty"]), (2, 2))
        latest = self.rows(logger.export_currency_csv())
        self.assertEqual([(row["Map ID"], row["Currency"], row["Net Change"]) for row in latest],
                         [("M0001", "Chaos Orb", "7"), ("M0002", "Chaos Orb", "3"),
                          ("M0002", "Exalted Orb", "4")])
        history = self.rows(logger.export_record_history_csv())
        first_map_chaos = [row for row in history if row["Map ID"] == "M0001" and row["Currency"] == "Chaos Orb"]
        self.assertEqual([row["Quantity"] for row in first_map_chaos], ["10", "10", "7"])
        self.assertTrue(all(row["Start Baseline"] == "Assumed empty" for row in first_map_chaos))
        self.assert_workbook_matches_main()

    def test_later_real_start_replaces_assumption_without_rewriting_old_audit_rows(self):
        self.snapshot("end", **{"Chaos Orb": 10})
        self.snapshot("start", **{"Chaos Orb": 8})
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Chaos Orb": 2})
        self.assertEqual(logger.session_currency_totals()["items"], [
            {"name": "Chaos Orb", "kind": "Currency", "quantity": 2}])
        self.assertEqual(logger.session_currency_totals()["maps_assumed_empty"], 0)
        latest = self.rows(logger.export_currency_csv())[0]
        self.assertEqual((latest["Start Count"], latest["End Count"], latest["Net Change"],
                          latest["Start Baseline"]), ("8", "10", "2", "Scanned"))
        self.snapshot("end", **{"Chaos Orb": 12})
        history = [row for row in self.rows(logger.export_record_history_csv()) if row["Type"] == "Currency"]
        self.assertEqual([(row["Inventory Phase"], row["Start Baseline"], row["Net Change"]) for row in history],
                         [("end", "Assumed empty", "10"), ("start", "Scanned", ""), ("end", "Scanned", "4")])
        self.assert_workbook_matches_main()

    def test_empty_snapshots_keep_map_settings_times_and_commit_metadata(self):
        logger.save_settings({"tier": 15, "waystone": 82, "deli": True, "wisp": True})
        self.snapshot("end")
        first = self.rows(logger.export_currency_csv())
        self.assertEqual(len(first), 1)
        row = first[0]
        self.assertEqual((row["Map ID"], row["Currency"], row["Start Baseline"]),
                         ("M0001", "", "Assumed empty"))
        self.assertEqual((row["Start Count"], row["End Count"], row["Net Change"]), ("", "", ""))
        self.assertEqual((row["End Tier"], row["End Waystone %"], row["End Deli"], row["End Wisp"]),
                         ("15", "82", "Yes", "Yes"))
        self.assertTrue(row["End Recorded UTC"])
        self.assertTrue(row["End Commit #"])
        self.assertEqual(row["Start Commit #"], "")
        self.snapshot("start")
        row = self.rows(logger.export_currency_csv())[0]
        self.assertEqual(row["Start Baseline"], "Scanned")
        self.assertTrue(row["Start Recorded UTC"])
        self.assertTrue(row["Start Commit #"])
        self.assertEqual(logger.session_currency_totals()["items"], [])
        self.assertEqual(logger.session_currency_totals()["maps_counted"], 1)
        self.assert_workbook_matches_main()

    def test_end_only_reopen_reset_and_failed_replacement_preserve_session_integrity(self):
        self.snapshot("end", **{"Chaos Orb": 6})
        with patch.object(logger, "_record_commit", side_effect=RuntimeError("disk write failed")):
            with self.assertRaisesRegex(RuntimeError, "disk write failed"):
                self.snapshot("end", **{"Chaos Orb": 999})
        logger._READY = False
        logger.initialize()
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Chaos Orb": 6})
        self.assertEqual(self.rows(logger.export_currency_csv())[0]["Net Change"], "6")
        self.assertEqual(logger.session_currency_totals()["maps_assumed_empty"], 1)
        logger.clear_export_and_reset_ids()
        self.assertEqual(logger.session_currency_totals()["items"], [])
        self.assertEqual(logger.session_currency_totals()["maps_assumed_empty"], 0)
        self.assertEqual(self.rows(logger.export_currency_csv()), [])
        self.assertEqual(self.rows(logger.export_all_csv()), [])
        self.assertEqual(self.rows(logger.export_record_history_csv()), [])

    def test_export_uses_same_snapshot_for_quantities_and_commit_numbers_during_approval(self):
        self.snapshot("end", **{"Chaos Orb": 5})
        with logger._connect() as db:
            first_commit = db.execute("SELECT MAX(number) FROM commits WHERE kind='Currency'").fetchone()[0]
        original_connect = logger._connect
        changed = False

        class Connection:
            def __init__(proxy, connection):
                proxy.connection = connection

            def __getattr__(proxy, name):
                return getattr(proxy.connection, name)

            def execute(proxy, sql, *arguments):
                nonlocal changed
                if sql.startswith("SELECT number,map_id,reference FROM commits") and not changed:
                    changed = True
                    self.snapshot("end", **{"Chaos Orb": 99})
                return proxy.connection.execute(sql, *arguments)

        @contextmanager
        def interleaved_connect():
            with original_connect() as db:
                yield Connection(db)

        with patch.object(logger, "_connect", interleaved_connect):
            row = self.rows(logger.export_currency_csv())[0]
        self.assertTrue(changed)
        self.assertEqual((row["End Count"], row["End Commit #"]), ("5", str(first_commit)))
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Chaos Orb": 99})


if __name__ == "__main__":
    unittest.main()
