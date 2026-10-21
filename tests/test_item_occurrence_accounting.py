"""Approved equipment occurrences count once per latest inventory scan or Ritual offer."""

from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


class ItemOccurrenceAccountingTests(unittest.TestCase):
    """Reconcile separate equipment footprints, phase-independent counts and audit history."""

    def setUp(self):
        """Create an isolated profile with one active map."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-item-occurrences-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Restore the selected profile and remove the temporary database."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def equipment(self, *, deferred=False, slot=1):
        """Represent one whole belt; separate slots describe separate occurrences."""
        return {"category": "Item", "name": "Headhunter", "quantity": 1,
                "slot": slot, "grid_slots": [slot, slot + 1],
                "box": [(slot - 1) * 54, 0, (slot + 1) * 54, 54],
                "deferred": deferred}

    def inventory(self, phase, count, chaos=None):
        """Approve a complete scan with disjoint equipment and an optional currency stack."""
        rows = [self.equipment(slot=1 + index * 4) for index in range(count)]
        if chaos is not None:
            rows.append({"category": "Currency", "name": "Chaos Orb", "quantity": chaos})
        return logger.save_currency_snapshot(phase, rows, register_names=True)

    def rows(self, data):
        """Read named CSV rows after checking unique headers and consistent widths."""
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def totals(self):
        """Read credited session quantities without relying on export calculations."""
        return {row["name"]: row["quantity"] for row in logger.session_currency_totals()["items"]}

    def raw_commits(self, kind):
        """Capture original database events to detect historical rewrites."""
        with logger._connect() as db:
            return [tuple(row) for row in db.execute(
                "SELECT * FROM commits WHERE kind=? ORDER BY number", (kind,))]

    def reopen(self):
        """Reload the database through the same initialization path as a restart."""
        logger._READY = False
        logger.initialize()

    def assert_workbook_matches_csv(self):
        """Check current map counts and full audit rows, including numeric quantity cells."""
        expected = {"Export": self.rows(logger.export_primary_csv()),
                    "Scan History": self.rows(logger.export_all_csv())}
        numeric = {"Headhunter", "Chaos Orb", "Item: Headhunter", "Currency: Chaos Orb",
                   "Quantity", "Start Count", "End Count", "Net Change", "New Find Quantity",
                   "Session Found Quantity"}
        ns = {"s": workbook_export.NS}
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            manifest = ET.fromstring(archive.read("xl/workbook.xml"))
            for number, entry in enumerate(manifest.findall("s:sheets/s:sheet", ns), 1):
                name = entry.get("name")
                if name not in expected:
                    continue
                root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
                sheet_rows = root.findall("s:sheetData/s:row", ns)
                headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext())
                           for cell in sheet_rows[0]}
                actual = []
                for row in sheet_rows[1:]:
                    values = dict.fromkeys(headers.values(), "")
                    for cell in row:
                        header = headers[cell.get("r").rstrip("0123456789")]
                        value = "".join(cell.itertext())
                        values[header] = value
                        self.assertIsNone(cell.find("s:f", ns))
                        if header in numeric and value:
                            self.assertIsNone(cell.get("t"), f"{name}: {header} must be numeric")
                    actual.append(values)
                self.assertEqual(actual, expected.pop(name), name)
        self.assertEqual(expected, {})

    def test_two_disjoint_item_footprints_count_without_an_end_scan_in_every_export(self):
        """A Start approval records two belts as two equipment finds immediately."""
        saved = self.inventory("start", 2, chaos=10)
        self.assertEqual(saved["start"], {"Headhunter": 2, "Chaos Orb": 10})
        self.assertEqual(saved["end"], {})
        self.assertEqual(saved["net"], {"Headhunter": 2})
        self.assertEqual(self.totals(), {"Headhunter": 2})
        with logger._connect() as db:
            snapshot = db.execute("SELECT phase,items_json FROM currency_snapshots").fetchone()
            self.assertEqual((snapshot["phase"], json.loads(snapshot["items_json"])),
                             ("start", {"Headhunter": 2, "Chaos Orb": 10}))
            details = json.loads(db.execute("SELECT details_json FROM commits WHERE kind='Currency'").fetchone()[0])
            self.assertEqual(details["item_accounting"], "latest_scan")
            self.assertEqual(details["item_kinds"]["Headhunter"], "Item")
        for exporter in (logger.export_primary_csv, logger.export_map_summary_csv):
            row = next(row for row in self.rows(exporter()) if row["Map ID"] == "M0001")
            self.assertEqual(row["Headhunter"], "2")
            self.assertEqual(row["Chaos Orb"], "")
        currency = {row["Currency"]: row for row in self.rows(logger.export_currency_csv())}
        self.assertEqual(currency["Headhunter"]["Session Found Quantity"], "2")
        self.assertEqual(currency["Chaos Orb"]["Session Found Quantity"], "")
        for exporter in (logger.export_record_history_csv, logger.export_all_csv):
            rows = self.rows(exporter())
            belt = next(row for row in rows if row["Item Name"] == "Headhunter")
            self.assertEqual((belt["Inventory Phase"], belt["Quantity"], belt["Session Found Quantity"]),
                             ("start", "2", "2"))
            self.assertEqual(belt["Current Inventory Snapshot"], "True")
            self.assertFalse(any(row["Currency"] == "Headhunter" for row in rows))
        self.assert_workbook_matches_csv()

    def test_latest_scan_replaces_item_counts_across_phases_while_currency_keeps_subtraction(self):
        """Repeats, a late Start and an absent item never accumulate equipment counts."""
        self.inventory("start", 2, chaos=10)
        original = self.raw_commits("Currency")[0]
        self.inventory("end", 1, chaos=13)
        self.assertEqual(self.totals(), {"Headhunter": 1, "Chaos Orb": 3})
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Headhunter": 1, "Chaos Orb": 3})
        self.inventory("start", 2, chaos=11)
        self.inventory("start", 2, chaos=11)
        self.assertEqual(self.totals(), {"Headhunter": 2, "Chaos Orb": 2})
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Headhunter": 2, "Chaos Orb": 2})
        for exporter in (logger.export_record_history_csv, logger.export_all_csv):
            rows = self.rows(exporter())
            belts = [row for row in rows if row["Item Name"] == "Headhunter"]
            self.assertEqual([row["Quantity"] for row in belts], ["2", "1", "2", "2"])
            self.assertEqual([row["Session Found Quantity"] for row in belts], ["0", "0", "0", "2"])
            chaos = [row for row in rows if row["Currency"] == "Chaos Orb"]
            self.assertEqual([row["Session Found Quantity"] for row in chaos], ["0", "2", "0", "0"])
        self.inventory("end", 0, chaos=13)
        self.assertEqual(self.totals(), {"Chaos Orb": 2})
        for exporter in (logger.export_primary_csv, logger.export_map_summary_csv):
            row = self.rows(exporter())[0]
            self.assertEqual((row["Headhunter"], row["Chaos Orb"]), ("0", "2"))
        self.assertEqual(self.raw_commits("Currency")[0], original)
        self.assertEqual(len(self.raw_commits("Currency")), 5)
        self.assert_workbook_matches_csv()

    def test_untagged_legacy_history_keeps_its_subtraction_when_new_maps_use_occurrences(self):
        """Opening a pre-policy map must not reinterpret its equipment baseline."""
        self.inventory("start", 2, chaos=10)
        self.inventory("end", 3, chaos=14)
        with logger._connect() as db:
            for row in db.execute("SELECT number,details_json FROM commits WHERE kind='Currency'").fetchall():
                details = json.loads(row["details_json"])
                details.pop("item_accounting")
                db.execute("UPDATE commits SET details_json=? WHERE number=?", (json.dumps(details), row["number"]))
        legacy = self.raw_commits("Currency")
        self.reopen()
        self.assertEqual(self.totals(), {"Headhunter": 1, "Chaos Orb": 4})
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Headhunter": 1, "Chaos Orb": 4})
        logger.finish_map(0, 0, 0)
        logger.start_map()
        self.inventory("start", 2)
        self.assertEqual(self.totals(), {"Headhunter": 3, "Chaos Orb": 4})
        maps = {row["Map ID"]: row for row in self.rows(logger.export_primary_csv())}
        self.assertEqual([maps[map_id]["Headhunter"] for map_id in ("M0001", "M0002")], ["1", "2"])
        for exporter in (logger.export_record_history_csv, logger.export_all_csv):
            belts = [row for row in self.rows(exporter())
                     if row["Item Name"] == "Headhunter" and row["Map ID"] == "M0001"]
            self.assertEqual([row["Quantity"] for row in belts], ["2", "3"])
            self.assertEqual([row["Net Change"] for row in belts], ["", "1"])
            self.assertEqual([row["Session Found Quantity"] for row in belts], ["0", "1"])
        self.assertEqual(self.raw_commits("Currency")[:2], legacy)
        self.assert_workbook_matches_csv()

    def test_inventory_undo_after_restart_restores_counts_and_sql_backup(self):
        """The saved policy and latest occurrence survive restart and persistent Undo."""
        self.inventory("start", 2, chaos=10)
        original = self.raw_commits("Currency")
        self.inventory("end", 1, chaos=13)
        self.reopen()
        self.assertEqual(self.totals(), {"Headhunter": 1, "Chaos Orb": 3})
        self.assertEqual(logger.undo_last_action()["action"], "Currency approval")
        self.reopen()
        self.assertEqual(self.totals(), {"Headhunter": 2})
        self.assertEqual(self.raw_commits("Currency"), original)
        backup = Path(self.temporary.name) / "occurrence-backup.sqlite3"
        backup.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM currency_snapshots WHERE phase='end'").fetchone()[0], 0)
            self.assertEqual(json.loads(db.execute("SELECT items_json FROM currency_snapshots").fetchone()[0]),
                             {"Headhunter": 2, "Chaos Orb": 10})
            self.assertEqual(json.loads(db.execute("SELECT details_json FROM commits WHERE kind='Currency'").fetchone()[0])[
                "item_accounting"], "latest_scan")
        self.assert_workbook_matches_csv()

    def test_removed_currency_reference_keeps_saved_kind_despite_matching_a_unique_name(self):
        """Source-only unique names must not change a saved Currency into equipment."""
        logger.add_currency_item("Headhunter")
        logger.save_currency_snapshot("end", [{"name": "Headhunter", "quantity": 3}])
        with logger._connect() as db:
            db.execute("DELETE FROM currency_items WHERE name='Headhunter'")
        logger.save_currency_snapshot("start", [])
        self.reopen()
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Headhunter": 3})
        self.assertEqual(logger.session_currency_totals()["items"], [
            {"name": "Headhunter", "kind": "Currency", "quantity": 3}])
        for exporter in (logger.export_primary_csv, logger.export_map_summary_csv):
            self.assertEqual(self.rows(exporter())[0]["Headhunter"], "3")
        current = self.rows(logger.export_currency_csv())[0]
        self.assertEqual((current["Net Change"], current["Session Found Quantity"]), ("3", "3"))
        for exporter in (logger.export_record_history_csv, logger.export_all_csv):
            saved = next(row for row in self.rows(exporter()) if row["Currency"] == "Headhunter")
            self.assertEqual((saved["Item Name"], saved["Session Found Quantity"]), ("", "3"))

    def test_ritual_duplicate_items_follow_the_same_deferred_rule_as_omens(self):
        """Each nondeferred footprint contributes one New Find, even with an identical name."""
        for first_deferred, expected in ((True, ["0", "1", "0", "1"]),
                                         (False, ["1", "1", "1", "1"])):
            with self.subTest(first_deferred=first_deferred):
                rewards = [self.equipment(deferred=first_deferred, slot=1), self.equipment(slot=5),
                           {"category": "Omen", "name": "Omen of Whittling", "quantity": 1,
                            "deferred": first_deferred},
                           {"category": "Omen", "name": "Omen of Whittling", "quantity": 1}]
                saved = logger.save_ritual_page(rewards, register_names=True)
                for exporter in (logger.export_ritual_csv, logger.export_record_history_csv, logger.export_all_csv):
                    rows = [row for row in self.rows(exporter())
                            if row["Scan Commit #"] == str(saved["scan_commit_number"])]
                    self.assertEqual([row["Quantity"] for row in rows], ["1"] * 4)
                    self.assertEqual([row["New Find Quantity"] for row in rows], expected)
                main = [row for row in self.rows(logger.export_all_csv())
                        if row["Scan Commit #"] == str(saved["scan_commit_number"])]
                self.assertEqual([row["Item: Headhunter"] for row in main[:2]], expected[:2])
                self.assertEqual([row["Omen: Omen of Whittling"] for row in main[2:]], expected[2:])
        self.assertEqual(self.totals(), {})
        self.assertFalse(any(int(row["Headhunter"] or 0) for row in self.rows(logger.export_primary_csv())))
        self.assert_workbook_matches_csv()

    def test_same_ritual_capture_replaces_current_page_but_preserves_history_and_undo(self):
        """Correcting a capture counts its current two occurrences once and keeps both approvals."""
        first = logger.save_ritual_page([self.equipment(deferred=True), self.equipment(slot=5)],
                                       scan_hash="a" * 64, register_names=True)
        original = self.raw_commits("Ritual")[0]
        second = logger.save_ritual_page([self.equipment(), self.equipment(slot=5)],
                                        scan_hash="a" * 64, register_names=True)
        self.assertTrue(second["updated"])
        self.assertEqual(second["page_number"], first["page_number"])
        self.reopen()
        current = self.rows(logger.export_ritual_csv())
        self.assertEqual(len(current), 2)
        self.assertEqual([row["New Find Quantity"] for row in current], ["1", "1"])
        self.assertEqual({row["Scan Commit #"] for row in current}, {str(second["scan_commit_number"])})
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)
        self.assertEqual(self.raw_commits("Ritual")[0], original)
        history = [row for row in self.rows(logger.export_all_csv()) if row["Type"] == "Ritual"]
        self.assertEqual([row["Item: Headhunter"] for row in history], ["0", "1", "1", "1"])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ritual_pages").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM commits WHERE kind='Ritual'").fetchone()[0], 2)
        self.assert_workbook_matches_csv()
        self.assertEqual(logger.undo_last_action()["action"], "Ritual approval")
        self.reopen()
        self.assertEqual([row["New Find Quantity"] for row in self.rows(logger.export_ritual_csv())], ["0", "1"])
        self.assertEqual(self.raw_commits("Ritual"), [original])
        self.assertEqual(self.totals(), {})


if __name__ == "__main__":
    unittest.main()
