"""Analysis notes travel with both export formats without changing session accounting."""

from contextlib import closing
import csv
import io
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import analysis_context, logger_store as logger, store, workbook_export
from PoE2_Data_Logger.core import workbook_references


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

    def context_rows(self, db, *, include_data=False):
        """Read worksheet-equivalent context directly through SQL, without application projection."""
        table = "analysis_context_complete" if include_data else "analysis_context"
        return [tuple(row) for row in db.execute(
            f"SELECT section,topic,guidance,context_version FROM {table} ORDER BY position,key")]

    def worksheet_rows(self, archive, number):
        """Decode four-column inline-string notes in worksheet reading order."""
        namespace = {"s": workbook_export.NS}
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
        rows = [["".join(cell.itertext()) for cell in row]
                for row in root.findall("s:sheetData/s:row", namespace)]
        return root, rows

    def test_every_current_export_field_has_interpretation_guidance(self):
        """Catch new unexplained export fields, including phase-prefixed settings and dynamic Atlas columns."""
        patterns = [(key, re.compile(pattern)) for key, pattern, _, _ in analysis_context.COLUMN_GUIDANCE]
        item_headers = set(logger.map_summary_item_headers())
        producers = (logger.export_primary_csv, logger.export_atlas_csv, logger.export_record_history_csv,
                     logger.export_currency_csv, logger.export_maps_csv, logger.export_ritual_csv,
                     logger.export_map_summary_csv, logger.export_commits_csv, logger.export_csv,
                     logger.export_all_csv)
        datasets = [(producer.__name__, producer()) for producer in producers]
        with logger._connect() as db:
            datasets.extend(workbook_references.export_sheets(db))
        for name, data in datasets:
            with self.subTest(export=name):
                headers = next(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
                unexplained = []
                for header in headers:
                    if header in item_headers:
                        continue
                    matches = [key for key, pattern in patterns if pattern.fullmatch(header)]
                    if matches == ["field_phase_settings"]:
                        underlying = header.split(" ", 1)[1]
                        matches = [key for key, pattern in patterns
                                   if key != "field_phase_settings" and pattern.fullmatch(underlying)]
                    if not matches:
                        unexplained.append(header)
                self.assertEqual(unexplained, [], "Every current exported field needs a meaning and analysis rule.")

    def test_both_exports_carry_identical_sql_readable_context(self):
        """Deliver header mappings and analysis rules with identical SQL, CSV and worksheet-four notes."""
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 7}])
        export_folder = Path(self.temporary.name) / "exports"
        export_folder.mkdir()
        logger.save_export_folder(str(export_folder))
        with logger._connect() as db:
            expected = self.context_rows(db, include_data=True)
            keys = {row[0] for row in db.execute("SELECT key FROM analysis_context")}
            self.assertTrue({"export_sheet", "history_sheet", "map_metrics", "analysis_method", "effectiveness_analysis", "inventory_gains",
                             "inventory_baseline", "ritual_offers", "missing_values", "recipe_ids",
                             "family_provenance", "rune_parts", "rune_chain_analysis", "timestamps",
                             "undo", "sql_provenance"} <= keys)
            saved_guides = {row[0]: row[1] for row in db.execute("SELECT key,guidance FROM analysis_context")}
            for key, pattern, _, guidance in analysis_context.COLUMN_GUIDANCE:
                self.assertEqual(saved_guides[key],
                                 f"Column headers (full-match regular expression): {pattern}. {guidance}")
        result = logger.save_export_file("sqlite3")
        with closing(sqlite3.connect(result["path"])) as exported:
            self.assertEqual(exported.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(self.context_rows(exported, include_data=True), expected)
            self.assertEqual(exported.execute("SELECT items_json FROM currency_snapshots WHERE phase='end'")
                             .fetchone()[0], '{"Chaos Orb":7}')
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            manifest = ET.fromstring(archive.read("xl/workbook.xml"))
            self.assertEqual([(sheet.get("name"), sheet.get("sheetId")) for sheet in
                              manifest.findall(f"{{{workbook_export.NS}}}sheets/{{{workbook_export.NS}}}sheet")],
                             [("Export", "1"), ("Atlas Character Settings", "2"),
                              ("Scan History", "3"), ("Analysis Context", "4"),
                              ("Family Database", "5"), ("Recipe Database", "6"),
                              ("Seed Database", "7"), ("Reference Aliases", "8")])
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
            expected = self.context_rows(db, include_data=True)
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

    def test_context_guidance_continuations_preserve_boundaries_and_normal_columns(self):
        """Oversized notes reconstruct exactly while ordinary rows and existing columns stay intact."""
        for length in (32767, 32768, 65534, 65535):
            with self.subTest(length=length):
                token = " é=literal\n"
                guidance = (token * (length // len(token) + 1))[:length]
                buffer = io.StringIO(newline="")
                writer = csv.writer(buffer)
                writer.writerow(analysis_context.HEADERS)
                writer.writerow(("Long", "Boundary", guidance, "5"))
                writer.writerow(("Short", "Ordinary", "Unchanged note", "5"))
                root = ET.fromstring(workbook_export._analysis_context_sheet(buffer.getvalue().encode("utf-8-sig")))
                rows = root.findall(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row")
                headers = ["".join(cell.itertext()) for cell in rows[0]]
                extra = (length - 1) // 32767
                self.assertEqual(headers, [*analysis_context.HEADERS,
                                           *(f"Guidance continuation {n}" for n in range(1, extra + 1))])
                decoded = []
                for row in rows[1:]:
                    values = {headers[workbook_export._col_number(cell.get("r")) - 1]: "".join(cell.itertext())
                              for cell in row}
                    self.assertEqual(values["Context Version"], "5")
                    self.assertTrue(all(len(value) <= 32767 for value in values.values()))
                    decoded.append(values)
                parts = [decoded[0]["Guidance"], *(decoded[0].get(f"Guidance continuation {n}", "")
                                                  for n in range(1, extra + 1))]
                self.assertEqual("".join(parts), guidance)
                self.assertEqual(parts[0], guidance[:32767])
                self.assertEqual(decoded[1], {"Section": "Short", "Topic": "Ordinary",
                                             "Guidance": "Unchanged note", "Context Version": "5"})
                self.assertFalse(root.findall(f".//{{{workbook_export.NS}}}f"))


if __name__ == "__main__":
    unittest.main()
