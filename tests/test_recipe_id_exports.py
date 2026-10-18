"""Recipe IDs accompany saved propagation origins without moving existing export columns."""

import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


class RecipeIdExportTests(unittest.TestCase):
    """Exercise recipe references across CSV and XLSX with known, ambiguous and manual families."""

    def setUp(self):
        """Create an isolated session with one recipe shared by two existing family records."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-recipe-id-export-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.recipe = "Recipe ID Export Shared Test"
        logger.save_recipe({"name": self.recipe, "sockets": 3, "combo": "Rage + Time + Bond"})
        for family in (8801, 8802):
            logger.save_family({"family": family, "top_socket": 3, "recipes": [self.recipe]})
        self.context = logger.scan_context()

    def tearDown(self):
        """Restore the original profile and discard the temporary session."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def accept(self, **arguments):
        """Approve one paired propagation part using the recipe's canonical catalog name."""
        return logger.accept_propagation_part(self.context, runes=["Rage", "Time"],
                                             recipe=self.recipe, **arguments)

    def csv_rows(self, producer):
        """Require even rows and the ID at the end of the established layout before map metadata."""
        rows = list(csv.reader(io.StringIO(producer().decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        headers = rows[0]
        without_stages = (headers[:-len(logger.CURRENCY_STAGE_HEADERS)]
                          if headers[-len(logger.CURRENCY_STAGE_HEADERS):] == list(logger.CURRENCY_STAGE_HEADERS)
                          else headers)
        previous_headers = (without_stages[:-len(logger.MAP_CONTEXT_HEADERS)]
                            if without_stages[-len(logger.MAP_CONTEXT_HEADERS):] == list(logger.MAP_CONTEXT_HEADERS)
                            else without_stages)
        self.assertEqual(previous_headers[-1], "Propagation Recipe ID")
        return rows[0], [dict(zip(rows[0], row)) for row in rows[1:]]

    def recipe_id(self):
        """Read the exact catalog reference used to identify the approved recipe."""
        with logger._connect() as db:
            return db.execute("SELECT recipe_id FROM recipe_ids WHERE recipe_name=?",
                              (self.recipe,)).fetchone()[0]

    def test_csv_ids_preserve_existing_family_fields_and_column_positions(self):
        """Keep family attribution unchanged while appending the same ID to every recipe-bearing chain row."""
        self.accept(family=8801)
        self.accept()
        logger.commit_chain("Bond")
        recipe_id = self.recipe_id()
        self.assertTrue(recipe_id)
        for producer in (logger.export_csv, logger.export_record_history_csv,
                         logger.export_all_csv, logger.export_primary_csv):
            with self.subTest(producer=producer.__name__):
                headers, rows = self.csv_rows(producer)
                chains = [row for row in rows if row.get("Chain Step #")]
                self.assertEqual([row["Propagation Recipe ID"] for row in chains],
                                 [recipe_id, recipe_id, ""])
                self.assertEqual([row["Propagation Family ID"] for row in chains],
                                 ["8801", "Unknown", "Unknown"])
                self.assertEqual([row["Propagation Family Status"] for row in chains],
                                 ["known", "ambiguous", "unknown"])
                self.assertEqual(chains[1]["Propagation Family Candidates"], "8801;8802")
                if producer == logger.export_csv:
                    self.assertEqual(headers.index("Scan Commit #"), 90)
                    self.assertEqual([headers.index(name) for name in ("Deli", "Wisp")], [129, 130])
                    self.assertEqual(headers[-2], "Propagation Family Source")
                elif producer in (logger.export_record_history_csv, logger.export_all_csv):
                    suffix = [*logger.MAP_CONTEXT_HEADERS, *logger.CURRENCY_STAGE_HEADERS]
                    self.assertEqual(headers[-len(suffix):], suffix)
                    previous_headers = headers[:-len(suffix)]
                    self.assertEqual(previous_headers[-2], "Previous Rune 2")
                    self.assertEqual(previous_headers[-1], "Propagation Recipe ID")
                if producer == logger.export_all_csv:
                    self.assertEqual(headers.index("Type"), 127)

    def test_old_explicit_history_origins_resolve_exact_catalog_ids_without_guessing_manual_rows(self):
        """Export old origin JSON through canonical IDs, keeping records without origins unidentified."""
        self.accept(family=8801)
        logger.commit_chain("Bond")
        with logger._connect() as db:
            for commit in db.execute("SELECT number,details_json FROM commits").fetchall():
                details = json.loads(commit["details_json"])
                for origin in [details.get("origin", {}), *details.get("origins", [])]:
                    origin.pop("recipe_id", None)
                db.execute("UPDATE commits SET details_json=? WHERE number=?",
                           (json.dumps(details), commit["number"]))
        _, rows = self.csv_rows(logger.export_record_history_csv)
        known = [row for row in rows if row["Propagation Recipe"] == self.recipe]
        self.assertEqual(len(known), 2)
        self.assertTrue(all(row["Propagation Recipe ID"] == self.recipe_id() for row in known))
        manual = next(row for row in rows if row.get("Chain Step #") == "2")
        self.assertEqual(manual["Propagation Recipe ID"], "")
        self.assertEqual(manual["Propagation Family ID"], "Unknown")

    def test_correction_completion_and_catalog_edits_retain_saved_id(self):
        """Keep recipe references and the two recorded rune slots stable through later corrections and edits."""
        self.accept(family=8802)
        recipe_id = self.recipe_id()
        logger.update_chain_steps([{"step": 1, "rune1": "Time", "rune2": "Bond"}], self.context)
        logger.complete_chain(self.context)
        logger.save_recipe({"name": self.recipe, "sockets": 3, "combo": "Bond + Rage + Time"})
        _, rows = self.csv_rows(logger.export_record_history_csv)
        events = [row for row in rows if row["Type"] in
                  ("Propagation", "Chain", "Chain correction", "Chain completion")]
        self.assertEqual(len(events), 4)
        self.assertTrue(all(row["Propagation Recipe ID"] == recipe_id for row in events))
        self.assertTrue(all(row["Propagation Family ID"] == "8802" for row in events))
        correction = next(row for row in events if row["Type"] == "Chain correction")
        self.assertEqual((correction["Previous Rune 1"], correction["Previous Rune 2"]), ("Rage", "Time"))
        self.assertEqual(self.recipe_id(), recipe_id)

    def test_unknown_manual_origin_stays_unidentified_after_catalog_addition(self):
        """Avoid assigning a later catalog recipe to an originally unknown manual label."""
        name = "Recipe ID Export Previously Unknown"
        logger.accept_propagation_part(self.context, runes=["Rage", "Time"], recipe=name)
        logger.save_recipe({"name": name, "sockets": 3, "combo": "Rage + Time + Bond"})
        with logger._connect() as db:
            for commit in db.execute("SELECT number,details_json FROM commits").fetchall():
                details = json.loads(commit["details_json"])
                for origin in [details.get("origin", {}), *details.get("origins", [])]:
                    origin.pop("recipe_id", None)
                db.execute("UPDATE commits SET details_json=? WHERE number=?",
                           (json.dumps(details), commit["number"]))
        for producer in (logger.export_csv, logger.export_record_history_csv,
                         logger.export_all_csv, logger.export_primary_csv):
            with self.subTest(producer=producer.__name__):
                _, rows = self.csv_rows(producer)
                origins = [row for row in rows if row["Propagation Recipe"] == name]
                self.assertTrue(origins)
                self.assertTrue(all(row["Propagation Recipe ID"] == "" for row in origins))

    def test_xlsx_recipe_ids_are_text_and_follow_existing_columns(self):
        """Keep the ID as text after established columns, before appended history map metadata."""
        self.accept(family=8801)
        logger.add_item_name("Recipe ID Export Test Item")
        primary_headers, _ = self.csv_rows(logger.export_primary_csv)
        self.assertEqual(primary_headers[-2], "Recipe ID Export Test Item")
        recipe_id = self.recipe_id()
        namespace = {"s": workbook_export.NS}
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            for path in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet3.xml"):
                with self.subTest(sheet=path):
                    rows = ET.fromstring(archive.read(path)).findall("s:sheetData/s:row", namespace)
                    headings = ["".join(cell.itertext()) for cell in rows[0]]
                    appended = [*logger.MAP_CONTEXT_HEADERS, *logger.CURRENCY_STAGE_HEADERS]
                    suffix = len(appended) if path.endswith("sheet3.xml") else 0
                    if suffix:
                        self.assertEqual(headings[-suffix:], appended)
                    recipe_cell = rows[0][-suffix - 1]
                    self.assertEqual("".join(recipe_cell.itertext()), "Propagation Recipe ID")
                    column = recipe_cell.get("r").rstrip("0123456789")
                    cells = [cell for row in rows[1:] for cell in row
                             if cell.get("r").rstrip("0123456789") == column]
                    self.assertTrue(cells)
                    self.assertTrue(all(cell.get("t") == "inlineStr" and
                                        "".join(cell.itertext()) == recipe_id for cell in cells))


if __name__ == "__main__":
    unittest.main()
