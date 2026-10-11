"""Analysis notes travel with both export formats without changing session accounting."""

from contextlib import closing
import csv
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import analysis_context, logger_store as logger, store, workbook_export


class AnalysisContextExportTests(unittest.TestCase):
    """Exercise persistence, migrations, read consistency and readable workbook packaging."""

    def setUp(self):
        """Initialize a clean profile with persistent notes and no session rows."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-analysis-context-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        """Restore the selected profile and remove isolated export artifacts."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def context_rows(self, db):
        """Read worksheet-equivalent context directly through SQL, without application projection."""
        return [tuple(row) for row in db.execute(
            "SELECT section,topic,guidance,context_version FROM analysis_context ORDER BY position,key")]

    def worksheet_rows(self, archive, number):
        """Decode four-column inline-string notes in worksheet reading order."""
        namespace = {"s": workbook_export.NS}
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
        rows = [["".join(cell.itertext()) for cell in row]
                for row in root.findall("s:sheetData/s:row", namespace)]
        return root, rows

    def test_both_exports_carry_identical_sql_readable_context(self):
        """Deliver shared propagation-analysis guidance with identical SQL, CSV and worksheet-four notes."""
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 7}])
        export_folder = Path(self.temporary.name) / "exports"
        export_folder.mkdir()
        logger.save_export_folder(str(export_folder))
        with logger._connect() as db:
            expected = self.context_rows(db)
            keys = {row[0] for row in db.execute("SELECT key FROM analysis_context")}
            self.assertTrue({"export_sheet", "history_sheet", "map_metrics", "inventory_gains",
                             "inventory_baseline", "ritual_offers", "missing_values", "recipe_ids",
                             "family_provenance", "rune_parts", "rune_chain_analysis", "timestamps",
                             "undo", "sql_provenance"} <= keys)
        result = logger.save_export_file("sqlite3")
        with closing(sqlite3.connect(result["path"])) as exported:
            self.assertEqual(exported.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(self.context_rows(exported), expected)
            self.assertEqual(exported.execute("SELECT items_json FROM currency_snapshots WHERE phase='end'")
                             .fetchone()[0], '{"Chaos Orb":7}')
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            manifest = ET.fromstring(archive.read("xl/workbook.xml"))
            self.assertEqual([(sheet.get("name"), sheet.get("sheetId")) for sheet in
                              manifest.findall(f"{{{workbook_export.NS}}}sheets/{{{workbook_export.NS}}}sheet")],
                             [("Export", "1"), ("Atlas Character Settings", "2"),
                              ("Scan History", "3"), ("Analysis Context", "4")])
            _, rows = self.worksheet_rows(archive, 4)
            self.assertEqual(rows[0], list(analysis_context.HEADERS))
            self.assertEqual([tuple(row) for row in rows[1:]], expected)
        csv_rows = list(csv.reader(io.StringIO(logger.export_analysis_context_csv().decode("utf-8-sig"))))
        self.assertEqual([tuple(row) for row in csv_rows[1:]], expected)

    def test_upgrade_refreshes_notes_without_rewriting_saved_activity(self):
        """Replace old guidance and add a missing table on existing profiles while keeping undo/session rows."""
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 9}])
        with logger._connect() as db:
            expected_context = self.context_rows(db)
            saved = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
                     for table in ("currency_snapshots", "commits", "undo_actions")}
            db.execute("UPDATE analysis_context SET guidance='Old guidance',context_version='0' WHERE key='purpose'")
            db.execute("INSERT INTO analysis_context VALUES('retired',999,'Old','Retired','Obsolete','0')")
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            self.assertEqual(self.context_rows(db), expected_context)
            for table, rows in saved.items():
                self.assertEqual([tuple(row) for row in db.execute(f"SELECT * FROM {table}")], rows, table)
            before = db.total_changes
            analysis_context.initialize(db)
            self.assertEqual(db.total_changes, before, "Identical notes must not generate write churn")
            db.execute("DROP TABLE analysis_context")
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            self.assertEqual(self.context_rows(db), expected_context)
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM commits")], saved["commits"])

    def test_notes_survive_undo_and_session_reset(self):
        """Undo removes a saved End approval and reset removes activity while preserving explanatory metadata."""
        with logger._connect() as db:
            expected = self.context_rows(db)
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 2}])
        logger.undo_last_action()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM currency_snapshots").fetchone()[0], 0)
            self.assertEqual(self.context_rows(db), expected)
        logger.clear_export_and_reset_ids()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM commits").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0], 0)
            self.assertEqual(self.context_rows(db), expected)

    def test_context_uses_same_read_snapshot_as_data_sheets(self):
        """A writer between data and note reads cannot mix a newer note revision into the workbook."""
        logger.start_map()
        with logger._connect() as db:
            expected = self.context_rows(db)
        original = logger.export_primary_csv

        def export_then_change_notes(*, _db):
            """Write a newer guidance revision after the export transaction acquires its read snapshot."""
            self.assertTrue(_db.in_transaction)
            data = original(_db=_db)
            with store._connect() as writer:
                writer.execute("UPDATE analysis_context SET guidance='Concurrent revision' WHERE key='purpose'")
            return data

        with patch.object(logger, "export_primary_csv", side_effect=export_then_change_notes):
            data = workbook_export.export_xlsx()
        with ZipFile(io.BytesIO(data)) as archive:
            _, rows = self.worksheet_rows(archive, 4)
            self.assertEqual([tuple(row) for row in rows[1:]], expected)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT guidance FROM analysis_context WHERE key='purpose'")
                             .fetchone()[0], "Concurrent revision")

    def test_context_layout_wraps_notes_as_text_without_setup_links(self):
        """Keep long explanatory cells readable and literal in ordinary XLSX readers."""
        namespace = {"s": workbook_export.NS}
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            root, rows = self.worksheet_rows(archive, 4)
            self.assertEqual(root.find("s:dimension", namespace).get("ref"), f"A1:D{len(rows)}")
            self.assertEqual(root.find("s:sheetViews/s:sheetView/s:pane", namespace).get("state"), "frozen")
            self.assertIsNone(root.find("s:hyperlinks", namespace))
            self.assertFalse(root.findall(".//s:f", namespace))
            self.assertTrue(all(cell.get("t") == "inlineStr" for cell in root.findall(".//s:c", namespace)))
            self.assertTrue(all(float(row.get("ht")) >= 32 for row in
                                root.findall("s:sheetData/s:row", namespace)[1:]))
            guidance_cells = root.findall("s:sheetData/s:row/s:c[@s='3']", namespace)
            self.assertEqual(len(guidance_cells), 4 * (len(rows) - 1))
            styles = ET.fromstring(archive.read("xl/styles.xml"))
            alignment = styles.findall("s:cellXfs/s:xf", namespace)[3].find("s:alignment", namespace)
            self.assertEqual(alignment.get("wrapText"), "1")
            self.assertEqual(alignment.get("vertical"), "top")


if __name__ == "__main__":
    unittest.main()
