"""Verify reference defaults can be restored without resetting recorded sessions."""

import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PIL import Image, ImageDraw

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store, workbook_export


class ReferenceResetTests(unittest.TestCase):
    """Exercise baseline restoration, historical records and transactional failures in isolated profiles."""

    def setUp(self):
        """Initialize a temporary profile and retain its untouched shipped reference rows."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-reference-reset-")
        self.previous = store.DATA_DIR
        self.previous_ready = logger._READY
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.baseline = self.rows(reference_pack._DEFAULT_TABLES)

    def tearDown(self):
        """Restore the selected profile and initialization flag before removing test files."""
        store.DATA_DIR = self.previous
        logger._READY = self.previous_ready
        self.temporary.cleanup()

    def rows(self, tables=None):
        """Read exact rows in stable row order, including metadata and SQLite ID sequences."""
        with logger._connect() as db:
            if tables is None:
                tables = [row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in tables}

    def custom_references(self):
        """Add a local seed family, retired catalog name and every kind of learned image evidence."""
        logger.save_recipe({"name": "Local reset reward", "sockets": 3, "combo": "Rage + Rage + Rage"})
        logger.save_family({"family": 9999, "top_socket": 3, "recipes": ["Local reset reward"]})
        logger.save_seed_state({"family": 9999, "sockets": 3, "seed_slot": "P1",
                                "seed_rune": "Local reset rune", "rewards": ["Local reset reward"]})
        logger.add_affix("Local reset affix")
        logger.add_currency_item("Local reset currency")
        logger.add_currency_item("Aldur's Saga")
        logger.add_item_name("Local reset armour")
        logger.add_ritual_name("Omen of Local Reset")
        art = Image.new("RGB", (96, 96), (25, 40, 60))
        ImageDraw.Draw(art).ellipse((12, 12, 80, 80), fill=(220, 120, 30))
        logger.save_currency_icon("Local reset currency", art)
        logger.save_item_icon("Local reset armour", art)
        logger.save_omen_icon("Omen of Local Reset", art)
        with logger._connect() as db:
            logger._save_review_examples(db, [{"name": "Local reset armour", "category": "Item",
                                               "image": art, "columns": 1, "rows": 1}],
                                        {"local reset armour": ("item", 1)})
            db.execute("INSERT INTO aliases VALUES(99999,'Local reset alias','Local reset reward')")
            logger.rebuild_alias_keys(db)
            db.execute("UPDATE master_perks SET effect='Locally edited perk'")
            db.execute("UPDATE recipes SET source='Locally edited recipe' WHERE name!=?",
                       ("Local reset reward",))
            db.execute("UPDATE families SET valid=0 WHERE id=47")
            db.execute("DELETE FROM affixes WHERE name='Chance to Contain Essences'")
        raw = io.BytesIO()
        art.save(raw, format="PNG")
        with patch.object(store, "_reviewed_vector", return_value=bytes(1296 * 4)):
            scan = store.save_scan(raw.getvalue(), "reset-seed.png", 3, "P1", "Local reset rune", 9999)
        self.assertEqual(len(store.reviewed_glyphs()), 1)
        self.assertEqual(len(logger.review_icons()), 1)
        self.assertTrue(any(row["family"] == 9999 for row in store.states()))
        return scan

    def worksheet_records(self):
        """Read workbook cell values by column header so catalog-only column changes can be compared."""
        records = []
        ns = {"s": workbook_export.NS}
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            for number in (1, 2, 3):
                root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
                rows = root.findall("s:sheetData/s:row", ns)
                headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext())
                           for cell in rows[0]}
                records.append([{headers[cell.get("r").rstrip("0123456789")]: "".join(cell.itertext())
                                 for cell in row} for row in rows[1:]])
        return records

    def test_reset_restores_complete_baseline_and_clears_learning_without_restart(self):
        """Verify edits/additions disappear, deleted defaults return, and next-scan evidence is empty."""
        scan = self.custom_references()
        images = {path.name: path.read_bytes() for path in (store.DATA_DIR / "images").iterdir()}
        result = reference_pack.reset_to_defaults()
        self.assertEqual(result["removed_examples"], 5)
        self.assertEqual(result["reference_rows"], sum(map(len, self.baseline.values())))
        self.assertEqual(self.rows(reference_pack._DEFAULT_TABLES), self.baseline)
        self.assertTrue(all(not rows for rows in self.rows(reference_pack._EXAMPLE_TABLES).values()))
        self.assertEqual(store.reviewed_glyphs(), [])
        self.assertEqual(logger.inventory_icons(), [])
        self.assertEqual(logger.ritual_icons(), [])
        self.assertNotIn("Aldur's Saga", logger.currency_names())
        self.assertFalse(any(row["family"] == 9999 for row in store.states()))
        self.assertEqual(store.list_scans()[0]["id"], scan["id"])
        self.assertEqual({path.name: path.read_bytes() for path in (store.DATA_DIR / "images").iterdir()}, images)
        self.assertNotIn("active_retired_currency_names", dict(self.rows(["meta"])["meta"]))

    def test_reset_preserves_history_settings_ids_and_spreadsheet_values(self):
        """Verify logged local references retain frozen data and all spreadsheet record values after reset."""
        logger.clear_export_and_reset_ids()
        logger.start_map()
        scan = self.custom_references()
        logger.commit_remnant("Local reset reward", family=9999, scan_id=scan["id"])
        logger.commit_chain("Rage")
        logger.save_kills(12, 3, 1, unique=2)
        logger.save_detonated(4)
        logger.save_currency_snapshot("start", [{"name": "Local reset currency", "quantity": 2},
                                                {"name": "Local reset armour", "quantity": 1}])
        logger.save_currency_snapshot("end", [{"name": "Local reset currency", "quantity": 7},
                                              {"name": "Local reset armour", "quantity": 2}])
        logger.save_ritual_page([{"name": "Omen of Local Reset", "category": "Omen", "quantity": 3},
                                 {"name": "Local reset armour", "category": "Item", "quantity": 1}])
        with logger._connect() as db:
            logger._set_meta(db, "reference_export_folder", str(store.DATA_DIR))
            logger._set_meta(db, "unrelated_profile_preference", {"value": "keep"})
        before = self.rows()
        stable_exports = (logger.export_csv, logger.export_record_history_csv, logger.export_commits_csv,
                          logger.export_maps_csv, logger.export_ritual_csv, logger.export_currency_csv,
                          logger.export_atlas_csv, store.export_csv)
        exports = [export() for export in stable_exports]
        workbook = self.worksheet_records()
        totals = logger.session_currency_totals()
        reference_pack.reset_to_defaults()
        after = self.rows()
        preserved = set(before) - set(reference_pack._DEFAULT_TABLES) - set(reference_pack._EXAMPLE_TABLES)
        before["meta"] = [row for row in before["meta"] if row[0] != "active_retired_currency_names"]
        self.assertEqual({table: before[table] for table in preserved},
                         {table: after[table] for table in preserved})
        self.assertEqual([export() for export in stable_exports], exports)
        self.assertEqual(logger.session_currency_totals(), totals)
        current = self.worksheet_records()
        for old_sheet, new_sheet in zip(workbook, current):
            self.assertEqual(len(old_sheet), len(new_sheet))
            for old_record, new_record in zip(old_sheet, new_sheet):
                for header, value in old_record.items():
                    if header in new_record or value not in ("", "0"):
                        self.assertEqual(new_record.get(header), value, header)
        self.assertIn(b"Local reset reward", logger.export_csv())
        self.assertIn(b"Local reset armour", logger.export_all_csv())

    def test_repeated_reset_and_startup_keep_identical_defaults_and_preserved_ids(self):
        """Verify resetting twice and reopening the profile cannot reseed session data or image IDs."""
        self.custom_references()
        reference_pack.reset_to_defaults()
        before = self.rows()
        self.assertEqual(reference_pack.reset_to_defaults()["removed_examples"], 0)
        self.assertEqual(self.rows(), before)
        logger._READY = False
        logger.initialize()
        self.assertEqual(self.rows(), before)

    def test_failed_restore_rolls_back_cleared_evidence_and_every_reference_table(self):
        """Inject a late catalog insertion failure and verify all reset writes roll back together."""
        self.custom_references()
        before = self.rows()
        with logger._connect() as db:
            db.execute("CREATE TRIGGER reject_reset_catalog BEFORE INSERT ON ritual_names "
                       "BEGIN SELECT RAISE(ABORT,'injected reference reset failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected reference reset failure"):
            reference_pack.reset_to_defaults()
        self.assertEqual(self.rows(), before)
        with patch.object(reference_pack, "_default_reference_rows", side_effect=ValueError("broken bundle")):
            with self.assertRaisesRegex(ValueError, "broken bundle"):
                reference_pack.reset_to_defaults()
        self.assertEqual(self.rows(), before)


if __name__ == "__main__":
    unittest.main()
