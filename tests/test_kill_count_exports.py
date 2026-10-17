"""Keep screenshot kill observations additive to established manual map exports."""

from contextlib import closing
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PIL import Image

from PoE2_Data_Logger.core import analysis_context, kill_counts, logger_store as logger
from PoE2_Data_Logger.core import store, workbook_export, workbook_references


class KillCountExportTests(unittest.TestCase):
    """Exercise real snapshot storage, CSV bundles, SQLite backups and workbook cells."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-kill-count-export-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        image = io.BytesIO()
        # Storage evidence only: OCR output is injected explicitly in these export tests.
        Image.new("RGB", (8, 4), (20, 30, 40)).save(image, format="PNG")
        self.evidence = image.getvalue()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def save_reading(self, counts=(984, 719, 656, 4), *, scope="map_stage", source="currency"):
        result = {**dict(zip(kill_counts.CATEGORIES, counts)), "complete": True,
                  "raw_text": " ".join(map(str, counts)), "confidence": 0.99}
        context = kill_counts.capture_context(source)
        saved = kill_counts.save_snapshot(result, context=context, request_id=str(uuid.uuid4()),
                                          source_mode=source, scope=scope, image_bytes=self.evidence)
        return saved, result, context

    def csv_rows(self, data):
        raw = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(raw[0]), len(set(raw[0])))
        self.assertTrue(all(len(row) == len(raw[0]) for row in raw))
        return raw[0], [dict(zip(raw[0], row)) for row in raw[1:]]

    def worksheet(self, archive, number):
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
        rows = root.findall(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row")
        headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext())
                   for cell in rows[0]}
        decoded = []
        for row in rows[1:]:
            values = dict.fromkeys(headers.values(), "")
            for cell in row:
                self.assertIsNone(cell.find(f"{{{workbook_export.NS}}}f"))
                values[headers[cell.get("r").rstrip("0123456789")]] = "".join(cell.itertext())
            decoded.append(values)
        return root, decoded

    def test_stage_and_final_readings_without_chain_do_not_replace_manual_totals(self):
        manual = logger.save_kills(11, 22, 33, unique=4)
        before_headers, before_maps = self.csv_rows(logger.export_maps_csv())
        stage, _, _ = self.save_reading(scope="chain_stage")
        final, result, context = self.save_reading((999, 720, 660, 5), scope="map_final", source="kills")
        headers, readings = self.csv_rows(kill_counts.export_csv())
        self.assertEqual(headers, list(kill_counts.CSV_HEADERS))
        self.assertEqual([row["Kill Count Scope"] for row in readings], ["map_stage", "map_final"])
        self.assertTrue(all(row["Expedition ID"] == "" for row in readings))
        self.assertEqual([row["Total Kills (Observed)"] for row in readings], ["2363", "2384"])
        self.assertEqual([readings[1][f"{name} Kills (Observed)"]
                          for name in ("Normal", "Magic", "Rare", "Unique")], ["999", "720", "660", "5"])
        self.assertEqual(json.loads(readings[1]["OCR JSON"]), result)
        self.assertEqual(json.loads(readings[1]["Capture Context JSON"]), context)
        self.assertEqual(readings[1]["Image SHA256"], hashlib.sha256(self.evidence).hexdigest())
        self.assertEqual(readings[1]["Scan Commit #"], str(final["scan_commit_number"]))
        self.assertNotEqual(stage["snapshot_id"], final["snapshot_id"])
        # Existing map exports project their latest event link; manual metric values stay exact.
        before_maps[0]["Last Commit #"] = str(final["scan_commit_number"])
        self.assertEqual(self.csv_rows(logger.export_maps_csv()), (before_headers, before_maps))
        _, history = self.csv_rows(logger.export_all_csv())
        saved_manual = [row for row in history if row["Scan Commit #"] == str(manual["scan_commit_number"])]
        self.assertTrue(saved_manual)
        self.assertTrue(all([row[name] for name in ("Normal Kills (Map)", "Magic Kills (Map)",
                                                   "Rare Kills (Map)", "Unique Kills (Map)")]
                            == ["11", "22", "33", "4"] for row in saved_manual))
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM new_export").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_step_provenance").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM expeditions").fetchone()[0], 1)

    def test_chain_association_stays_optional_and_final_manual_save_stays_independent(self):
        logger.increment_propagation_detonated(logger.scan_context(), runes=["Rage"], recipe="1x Regal Orb")
        logger.commit_chain_draft([{"rune1": "Rage", "rune2": ""}])
        associated, _, _ = self.save_reading((10, 20, 30, 0), scope="chain_stage", source="propagation")
        self.assertEqual(associated["expedition_id"], "M0001-E01")
        logger.complete_chain(logger.scan_context())
        final, _, _ = self.save_reading((11, 21, 31, 1), scope="map_final", source="kills")
        self.assertEqual(final["expedition_id"], "")
        logger.finish_map(1, 2, 3, unique=4)
        _, rows = self.csv_rows(kill_counts.export_csv())
        self.assertEqual([(row["Kill Count Scope"], row["Expedition ID"], row["Total Kills (Observed)"])
                          for row in rows], [("chain_stage", "M0001-E01", "60"), ("map_final", "", "64")])
        _, maps = self.csv_rows(logger.export_maps_csv())
        self.assertEqual([maps[0][name] for name in ("Normal Kills", "Magic Kills", "Rare Kills",
                                                   "Unique Kills", "Total Kills")], ["1", "2", "3", "4", "10"])

    def test_workbook_appends_ninth_sheet_and_matches_all_existing_csv_sheets(self):
        logger.save_kills(1, 2, 3, unique=4)
        self.save_reading()
        expected = [self.csv_rows(producer())[1] for producer in
                    (logger.export_primary_csv, logger.export_atlas_csv, logger.export_all_csv)]
        with logger._connect() as db:
            reference_rows = [self.csv_rows(data) for _, data in workbook_references.export_sheets(db)]
        _, expected_readings = self.csv_rows(kill_counts.export_csv())
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            manifest = ET.fromstring(archive.read("xl/workbook.xml"))
            sheets = manifest.findall(f"{{{workbook_export.NS}}}sheets/{{{workbook_export.NS}}}sheet")
            self.assertEqual([(sheet.get("name"), sheet.get("sheetId")) for sheet in sheets], [
                ("Export", "1"), ("Atlas Character Settings", "2"), ("Scan History", "3"),
                ("Analysis Context", "4"), ("Family Database", "5"), ("Recipe Database", "6"),
                ("Seed Database", "7"), ("Reference Aliases", "8"), ("Kill Count Readings", "9")])
            for number, rows in enumerate(expected, 1):
                self.assertEqual(self.worksheet(archive, number)[1], rows)
            for number, (headers, rows) in enumerate(reference_rows, 5):
                root, actual = self.worksheet(archive, number)
                cells = root.find(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row")
                self.assertEqual(["".join(cell.itertext()) for cell in cells], headers)
                self.assertEqual(actual, rows)
            root, rows = self.worksheet(archive, 9)
            self.assertEqual(rows, expected_readings)
            self.assertIsNone(root.find(f"{{{workbook_export.NS}}}hyperlinks"))
            cells = root.findall(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row")[1]
            numeric = [cell for cell in cells if cell.get("r").rstrip("0123456789") in ("E", "F", "G", "H", "I")]
            self.assertEqual([cell.findtext(f"{{{workbook_export.NS}}}v") for cell in numeric],
                             ["984", "719", "656", "4", "2363"])
            self.assertTrue(all(cell.get("t") is None for cell in numeric))

    def test_workbook_kill_readings_share_data_snapshot_during_concurrent_save(self):
        self.save_reading()
        _, expected = self.csv_rows(kill_counts.export_csv())
        original = logger.export_primary_csv

        def export_then_save(*, _db):
            data = original(_db=_db)
            self.save_reading((1000, 721, 657, 5))
            return data

        with patch.object(logger, "export_primary_csv", side_effect=export_then_save):
            workbook = workbook_export.export_xlsx()
        with ZipFile(io.BytesIO(workbook)) as archive:
            self.assertEqual(self.worksheet(archive, 9)[1], expected)
        self.assertEqual(len(self.csv_rows(kill_counts.export_csv())[1]), 2)

    def test_csv_bundle_and_database_backup_keep_observation_values_and_evidence(self):
        self.save_reading()
        folder = Path(self.temporary.name) / "exports"
        folder.mkdir()
        logger.save_export_folder(str(folder))
        saved = logger.save_export_file("csv")
        self.assertEqual(Path(saved["kill_counts_path"]).read_bytes(), kill_counts.export_csv())
        self.assertEqual(Path(saved["kill_counts_path"]).name,
                         Path(saved["path"]).stem + "_Kill_Counts.csv")
        self.assertEqual(saved["kill_counts_bytes"], len(kill_counts.export_csv()))
        for name, producer in (("path", logger.export_primary_csv), ("atlas_path", logger.export_atlas_csv),
                               ("history_path", logger.export_all_csv)):
            self.assertEqual(Path(saved[name]).read_bytes(), producer())
        backup = logger.save_export_file("sqlite3")
        with closing(sqlite3.connect(backup["path"])) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT normal,magic,rare,unique_kills,image_png FROM kill_count_snapshots")
                             .fetchall(), [(984, 719, 656, 4, self.evidence)])

    def test_context_explains_every_new_field_and_refresh_is_idempotent(self):
        patterns = [re.compile(pattern) for _, pattern, _, _ in analysis_context.COLUMN_GUIDANCE]
        self.assertEqual([header for header in kill_counts.CSV_HEADERS
                          if not any(pattern.fullmatch(header) for pattern in patterns)], [])
        self.save_reading()
        with logger._connect() as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM kill_count_snapshots")]
            guidance = {row[0]: row[1] for row in db.execute("SELECT key,guidance FROM analysis_context")}
            self.assertIn("Never sum rows", guidance["kill_count_readings"])
            self.assertIn("no chain is required", guidance["kill_count_scopes"])
            self.assertIn("unknown, not zero", guidance["kill_count_provenance"])
            self.assertIn("maps.kills_json", guidance["sql_kill_count_readings"])
            changes = db.total_changes
            analysis_context.initialize(db)
            self.assertEqual(db.total_changes, changes)
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM kill_count_snapshots")], before)

    def test_no_readings_is_header_only_and_undo_removes_the_saved_observation(self):
        headers, rows = self.csv_rows(kill_counts.export_csv())
        self.assertEqual(headers, list(kill_counts.CSV_HEADERS))
        self.assertEqual(rows, [])
        self.save_reading((0, 0, 0, 0), scope="map_final", source="kills")
        self.assertEqual(self.csv_rows(kill_counts.export_csv())[1][0]["Total Kills (Observed)"], "0")
        self.assertEqual(logger.undo_last_action()["action"], "Kill count OCR")
        self.assertEqual(self.csv_rows(kill_counts.export_csv())[1], [])


if __name__ == "__main__":
    unittest.main()
