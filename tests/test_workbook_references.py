"""Excel reference sheets provide the same master keys and recipe data as a SQL backup."""

from contextlib import closing
import csv
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export, workbook_references


class WorkbookReferenceTests(unittest.TestCase):
    """Verify complete frozen reference copies, stable joins and read-only export behavior."""

    def setUp(self):
        """Create isolated references with a shared recipe appearing at different family positions."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-workbook-references-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        self.shared = logger.save_recipe({"name": "Workbook shared reward", "sockets": 3,
            "combo": "Death + Power + Time", "level_band": "Fixture level", "category": "Item", "source": "Local fixture"})
        self.other = logger.save_recipe({"name": "Workbook other reward", "sockets": 4,
            "combo": "Rage + Life + Body + Mind", "category": "Currency", "source": "Local fixture"})
        logger.save_family({"family": 9998, "top_socket": 4,
                            "recipes": [self.other["name"], self.shared["name"]]})
        logger.save_family({"family": 9999, "top_socket": 4,
                            "recipes": [self.shared["name"], self.other["name"]], "valid": False})
        logger.save_seed_state({"family": 9998, "sockets": 3, "seed_slot": "P2", "seed_rune": "Power",
                                "rewards": [self.shared["name"]], "status": "local"})
        with logger._connect() as db:
            position = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM aliases").fetchone()[0]
            db.execute("INSERT INTO aliases VALUES(?,?,?)", (position, "workbook shared shortcut", self.shared["name"]))
            logger.rebuild_alias_keys(db)
        logger.start_map()

    def tearDown(self):
        """Restore the selected profile after closing every workbook and SQLite backup handle."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def column(self, address):
        """Decode one-based Excel column addresses without treating reference IDs as numbers."""
        number = 0
        for character in re.match(r"[A-Z]+", address).group():
            number = number * 26 + ord(character) - ord("A") + 1
        return number

    def workbook(self, data=None):
        """Read all worksheet names and sparse cell values while closing the ZIP archive immediately."""
        data = workbook_export.export_xlsx() if data is None else data
        namespace = {"s": workbook_export.NS}
        result = {}
        with ZipFile(io.BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            manifest = ET.fromstring(archive.read("xl/workbook.xml"))
            entries = manifest.findall("s:sheets/s:sheet", namespace)
            for number, entry in enumerate(entries, 1):
                self.assertEqual(entry.get("sheetId"), str(number))
                root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
                rows = []
                width = 0
                for row in root.findall("s:sheetData/s:row", namespace):
                    cells = {self.column(cell.get("r")): "".join(cell.itertext()) for cell in row}
                    if not rows:
                        width = max(cells)
                    rows.append(tuple(cells.get(column, "") for column in range(1, width + 1)))
                result[entry.get("name")] = rows
        return result

    def records(self, workbook, name):
        """Convert one reference worksheet's explicit headers and values into row dictionaries."""
        rows = workbook[name]
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        return [dict(zip(rows[0], values)) for values in rows[1:]]

    def source_rows(self):
        """Capture operational and reference tables independently of the new worksheet generator."""
        tables = ("meta", "maps", "expeditions", "map_unique_kills", "new_export", "legacy_export", "commits",
                  "currency_snapshots", "ritual_pages", "undo_actions", "chain_step_provenance", "chain_completions",
                  "families", "recipes", "recipe_ids", "seed_states", "aliases", "atlas_setups", "atlas_catalogs")
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in tables}

    def test_reference_sheets_append_after_existing_four_and_match_one_sql_snapshot(self):
        """Retain established worksheet indices and export every reference source on the same connection."""
        with logger._connect() as db:
            db.execute("BEGIN")
            reference_csvs = workbook_references.export_sheets(db)
            context_csv = logger.export_analysis_context_csv(_db=db)
            recipes = {row["name"]: dict(row) for row in db.execute("SELECT * FROM recipes")}
            identifiers = dict(db.execute("SELECT recipe_name,recipe_id FROM recipe_ids"))
            families = [dict(row) for row in db.execute("SELECT * FROM families ORDER BY id")]
            seeds = [tuple(row) for row in db.execute("SELECT family,sockets,seed_slot,seed_rune,rewards_json,status "
                                                    "FROM seed_states ORDER BY family,sockets")]
            aliases = [tuple(row) for row in db.execute("SELECT position,alias,target FROM aliases ORDER BY position")]
        workbook = self.workbook()
        self.assertEqual(list(workbook), ["Export", "Atlas Character Settings", "Scan History", "Analysis Context",
                                         "Family Database", "Recipe Database", "Seed Database", "Reference Aliases", "Kill Count Readings"])
        for name, raw in reference_csvs:
            expected = [tuple(row) for row in csv.reader(io.StringIO(raw.decode("utf-8-sig")))]
            self.assertEqual(workbook[name], expected)
        self.assertEqual(workbook["Analysis Context"],
                         [tuple(row) for row in csv.reader(io.StringIO(context_csv.decode("utf-8-sig")))])
        recipe_rows = self.records(workbook, "Recipe Database")
        self.assertEqual({row["Recipe"] for row in recipe_rows}, set(recipes) | set(identifiers))
        self.assertEqual(len(recipe_rows), len(set(recipes) | set(identifiers)))
        for row in recipe_rows:
            if row["Recipe"] in recipes:
                original = recipes[row["Recipe"]]
                self.assertEqual(row["Recipe ID"], identifiers.get(row["Recipe"], ""))
                self.assertEqual(row["Socket Count"], str(original["sockets"]))
                self.assertEqual(row["Exact Rune Combo"], original["combo"])
                for header, key in (("Level Band", "level_band"), ("Category", "category"), ("Reference Source", "source")):
                    self.assertEqual(row[header], "" if original[key] is None else original[key])
        expected_members = [(str(family["id"]), str(number), recipe)
                            for family in families for number, recipe in enumerate(json.loads(family["recipes_json"]), 1)]
        actual_members = [(row["Family ID"], row["Recipe # In Family"], row["Recipe"])
                          for row in self.records(workbook, "Family Database") if row["Recipe"]]
        self.assertEqual(actual_members, expected_members)
        seed_rows = self.records(workbook, "Seed Database")
        self.assertEqual([(int(row["Family ID"]), int(row["Socket Count"]), row["Visible Seed Slot"],
                           row["Visible Seed Rune"], json.loads(row["Rewards (JSON)"]), row["Mapping Status"])
                          for row in seed_rows],
                         [(family, sockets, slot, rune, json.loads(rewards), status)
                          for family, sockets, slot, rune, rewards, status in seeds])
        alias_rows = self.records(workbook, "Reference Aliases")
        self.assertEqual([(int(row["Alias Position"]), row["Alias"], row["Matched Recipe"], row["Recipe ID"])
                          for row in alias_rows],
                         [(position, alias, target, identifiers.get(target, "")) for position, alias, target in aliases])

    def test_shared_membership_order_validity_and_stable_recipe_identity_are_explicit(self):
        """One recipe ID joins both families while position and family-valid flags remain distinct."""
        original_id = self.shared["recipe_id"]
        revised = logger.save_recipe({"name": self.shared["name"].upper(), "sockets": 3,
                                      "combo": "Death + Power + Mind"})
        self.assertEqual(revised["recipe_id"], original_id)
        workbook = self.workbook()
        shared_rows = [row for row in self.records(workbook, "Family Database")
                       if row["Recipe"] == self.shared["name"]]
        self.assertEqual([(row["Family ID"], row["Recipe # In Family"], row["Family Valid"])
                          for row in shared_rows], [("9998", "2", "Yes"), ("9999", "1", "No")])
        self.assertTrue(all(row["Recipe ID"] == original_id and row["Top Socket Count"] == "4" and
                            row["Socket Count"] == "3" and row["Exact Rune Combo"] == revised["combo"]
                            for row in shared_rows))
        recipe_rows = [row for row in self.records(workbook, "Recipe Database") if row["Recipe ID"] == original_id]
        self.assertEqual(len(recipe_rows), 1)

    def test_retired_registry_identity_has_explicit_status_and_no_invented_combo(self):
        """Keep a retired ID discoverable without pretending its former catalog definition still exists."""
        retired = logger.save_recipe({"name": "Retired workbook reward", "sockets": 2, "combo": "Death + Time"})
        with logger._connect() as db:
            db.execute("DELETE FROM recipes WHERE name=?", (retired["name"],))
        rows = self.records(self.workbook(), "Recipe Database")
        row = next(row for row in rows if row["Recipe ID"] == retired["recipe_id"])
        self.assertEqual(row["Recipe"], retired["name"])
        self.assertEqual(row["Reference Status"], "Retired recipe ID")
        self.assertEqual([row[header] for header in ("Socket Count", "Exact Rune Combo", "Level Band",
                                                   "Category", "Reference Source")], [""] * 5)
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM recipes WHERE name=?", (retired["name"],)).fetchone())
            self.assertEqual(db.execute("SELECT recipe_id FROM recipe_ids WHERE recipe_name=?",
                                        (retired["name"],)).fetchone()[0], retired["recipe_id"])

    def test_incomplete_legacy_references_preserve_missing_and_empty_states_without_assigning_ids(self):
        """Describe legacy gaps faithfully rather than repairing catalogs or inventing definitions on export."""
        missing_id_name = "Legacy workbook recipe without identity"
        with logger._connect() as db:
            # These incomplete imported/legacy states are not created through the normal editors.
            db.execute("INSERT INTO families VALUES(?,?,?,?)", (9997, 4, 0, "[]"))
            db.execute("DELETE FROM recipes WHERE name=?", (self.shared["name"],))
            db.execute("INSERT INTO recipes VALUES(?,?,?,?,?,?)",
                       (missing_id_name, 3, "Rage + Power + Time", "", "Item", "Legacy fixture"))
            self.assertIsNone(db.execute("SELECT 1 FROM recipe_ids WHERE recipe_name=?", (missing_id_name,)).fetchone())
        before = self.source_rows()
        workbook = self.workbook()
        families = self.records(workbook, "Family Database")
        empty = [row for row in families if row["Family ID"] == "9997"]
        self.assertEqual(len(empty), 1)
        self.assertEqual((empty[0]["Top Socket Count"], empty[0]["Family Valid"], empty[0]["Reference Status"]),
                         ("4", "No", "Empty family"))
        self.assertEqual([empty[0][field] for field in ("Recipe # In Family", "Recipe ID", "Recipe",
                                                       "Socket Count", "Exact Rune Combo")], [""] * 5)
        missing = [row for row in families if row["Recipe"] == self.shared["name"]]
        self.assertEqual(len(missing), 2)
        self.assertTrue(all(row["Recipe ID"] == self.shared["recipe_id"] and row["Socket Count"] == "" and
                            row["Exact Rune Combo"] == "" and row["Reference Status"] == "Recipe missing from current catalog"
                            for row in missing))
        recipe = next(row for row in self.records(workbook, "Recipe Database") if row["Recipe"] == missing_id_name)
        self.assertEqual((recipe["Recipe ID"], recipe["Reference Status"]), ("", "Missing recipe ID"))
        self.assertEqual((recipe["Socket Count"], recipe["Exact Rune Combo"]), ("3", "Rage + Power + Time"))
        self.assertEqual(self.source_rows(), before)
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM recipe_ids WHERE recipe_name=?", (missing_id_name,)).fetchone())

    def test_sql_backup_and_exports_preserve_all_reference_and_operational_rows(self):
        """Add reference sheets without changing gameplay records, catalogs, counters or Undo."""
        logger.commit_remnant(self.shared["name"], family=9998)
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 3}])
        logger.save_counts(10, 2, 1, 5, unique=0)
        before = self.source_rows()
        self.workbook()
        path = Path(self.temporary.name) / "full-database.sqlite3"
        path.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(path)) as exported:
            exported.row_factory = sqlite3.Row
            self.assertEqual(exported.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            for table, rows in before.items():
                self.assertEqual([tuple(row) for row in exported.execute(f"SELECT * FROM {table} ORDER BY rowid")], rows, table)
            expected = workbook_references.export_sheets(exported)
        workbook = self.workbook()
        for name, raw in expected:
            self.assertEqual(workbook[name], [tuple(row) for row in csv.reader(io.StringIO(raw.decode("utf-8-sig")))])
        self.assertEqual(self.source_rows(), before)

    def test_concurrent_reference_and_context_writer_cannot_mix_a_new_snapshot(self):
        """Commit new catalog/seed/alias/context values after the first data read and retain old workbook values."""
        with logger._connect() as db:
            expected_context = logger.export_analysis_context_csv(_db=db)
        original = logger.export_primary_csv
        later_name = "Concurrent workbook reward"

        def read_then_edit(*, _db):
            """Change all related reference rows atomically after the workbook's snapshot starts."""
            self.assertTrue(_db.in_transaction)
            result = original(_db=_db)
            with logger._connect() as writer:
                writer.execute("UPDATE recipes SET combo='Death + Mind + Time' WHERE name=?", (self.shared["name"],))
                writer.execute("INSERT INTO recipes VALUES(?,?,?,?,?,?)", (later_name, 3, "Rage + Power + Time", "", "Item", "Concurrent"))
                logger._ensure_recipe_id(writer, later_name)
                writer.execute("UPDATE families SET recipes_json=? WHERE id=9998",
                               (logger._dump([self.shared["name"], later_name, self.other["name"]]),))
                writer.execute("UPDATE seed_states SET seed_rune='Mind' WHERE family=9998 AND sockets=3")
                position = writer.execute("SELECT COALESCE(MAX(position),0)+1 FROM aliases").fetchone()[0]
                writer.execute("INSERT INTO aliases VALUES(?,?,?)", (position, "concurrent shortcut", later_name))
                writer.execute("UPDATE analysis_context SET guidance='Concurrent context revision' WHERE key='purpose'")
            return result

        with patch.object(logger, "export_primary_csv", side_effect=read_then_edit):
            workbook = self.workbook()
        recipes = self.records(workbook, "Recipe Database")
        shared = next(row for row in recipes if row["Recipe"] == self.shared["name"])
        self.assertEqual(shared["Exact Rune Combo"], self.shared["combo"])
        self.assertFalse(any(row["Recipe"] == later_name for row in recipes))
        family = [row["Recipe"] for row in self.records(workbook, "Family Database") if row["Family ID"] == "9998"]
        self.assertEqual(family, [self.other["name"], self.shared["name"]])
        seed = next(row for row in self.records(workbook, "Seed Database") if row["Family ID"] == "9998")
        self.assertEqual(seed["Visible Seed Rune"], "Power")
        self.assertFalse(any(row["Alias"] == "concurrent shortcut" for row in self.records(workbook, "Reference Aliases")))
        self.assertEqual(workbook["Analysis Context"],
                         [tuple(row) for row in csv.reader(io.StringIO(expected_context.decode("utf-8-sig")))])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT combo FROM recipes WHERE name=?", (self.shared["name"],)).fetchone()[0],
                             "Death + Mind + Time")
            self.assertIsNotNone(db.execute("SELECT 1 FROM recipes WHERE name=?", (later_name,)).fetchone())

    def test_reference_names_and_aliases_stay_literal_text_not_excel_formulas(self):
        """Preserve exact unusual names while writing safe inline-string reference cells."""
        named = logger.save_recipe({"name": "=Literal reference reward", "sockets": 2, "combo": "Death + Time"})
        with logger._connect() as db:
            position = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM aliases").fetchone()[0]
            db.execute("INSERT INTO aliases VALUES(?,?,?)", (position, "+Literal alias", named["name"]))
        data = workbook_export.export_xlsx()
        workbook = self.workbook(data)
        self.assertTrue(any(row["Recipe"] == named["name"] for row in self.records(workbook, "Recipe Database")))
        self.assertTrue(any(row["Alias"] == "+Literal alias" and row["Matched Recipe"] == named["name"]
                            for row in self.records(workbook, "Reference Aliases")))
        with ZipFile(io.BytesIO(data)) as archive:
            for number in (5, 6, 7, 8):
                root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
                self.assertFalse(root.findall(f".//{{{workbook_export.NS}}}f"))


if __name__ == "__main__":
    unittest.main()
