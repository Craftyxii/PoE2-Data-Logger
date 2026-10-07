from contextlib import contextmanager
import csv
import importlib.util
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


ATLAS_HEADERS = ["Atlas Setup ID", "Map IDs", "Atlas Data Version", "Gear Item Rarity %", "Activity",
                 "Node ID", "Node Name", "Allocated", "Points", "Selected Option ID",
                 "Selected Option", "Effects", "Stats", "Applied Effects", "Applied Stats"]


def csv_bytes(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


@contextmanager
def memory_database():
    database = sqlite3.connect(":memory:")
    try:
        yield database
    finally:
        database.close()


class AtlasWorkbookTests(unittest.TestCase):
    def sample_workbook(self):
        main_data = csv_bytes(["Map ID", "Atlas Setup ID", "Gear Item Rarity %"],
                              [["M0001", "ATLAS-1", 0], ["M0002", "ATLAS-2", 175.5],
                               ["M0000", "", ""]])
        atlas_data = csv_bytes(ATLAS_HEADERS, [
            ["ATLAS-1", "M0001, M0003", "patch-one", 0, "Main Atlas", "0007", "Inactive Node", "No", 0,
             "", "", "", "{}", "", ""],
            ["ATLAS-1", "M0001, M0003", "patch-one", 0, "Expedition", "0008", "Choice Node", "Yes", 1,
             "001", "Chosen bonus", "5% increased quantity", '{"quantity":5}',
             "5% increased quantity", '{"quantity":5}'],
            ["ATLAS-2", "M0002", "patch-one", 175.5, "Main Atlas", "0007", "Inactive Node", "No", 0,
             "", "", "", "{}", "", ""],
            ["ATLAS-unknown", "", "patch-one", "", "Ritual", "0009", "Unknown gear rarity", "No", 0,
             "", "", "", "{}", "", ""],
        ])
        connections = []

        def main_export(*, _db):
            self.assertTrue(_db.in_transaction)
            connections.append(_db)
            return main_data

        def atlas_export(*, _db):
            self.assertTrue(_db.in_transaction)
            connections.append(_db)
            return atlas_data

        with patch.object(logger, "_connect", memory_database), \
                patch.object(logger, "export_all_csv", side_effect=main_export), \
                patch.object(logger, "export_atlas_csv", side_effect=atlas_export, create=True):
            workbook = workbook_export.export_xlsx()
        self.assertIs(connections[0], connections[1])
        return workbook

    def test_companion_sheet_package_and_relationships(self):
        ns = {"s": workbook_export.NS, "r": workbook_export.DOC_REL,
              "p": workbook_export.PKG_REL, "c": workbook_export.TYPES}
        with ZipFile(io.BytesIO(self.sample_workbook())) as archive:
            self.assertIsNone(archive.testzip())
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            sheets = workbook.findall("s:sheets/s:sheet", ns)
            self.assertEqual([sheet.get("name") for sheet in sheets],
                             ["Export", "Atlas Character Settings"])
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {row.get("Id"): row.get("Target") for row in relationships}
            for number, sheet in enumerate(sheets, 1):
                self.assertEqual(targets[sheet.get(f"{{{workbook_export.DOC_REL}}}id")],
                                 f"worksheets/sheet{number}.xml")
            types = ET.fromstring(archive.read("[Content_Types].xml"))
            overrides = {row.get("PartName"): row.get("ContentType") for row in types}
            for number in (1, 2):
                self.assertTrue(overrides[f"/xl/worksheets/sheet{number}.xml"].endswith("worksheet+xml"))

            main = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            links = {link.get("ref"): link.get("location")
                     for link in main.findall("s:hyperlinks/s:hyperlink", ns)}
            self.assertEqual(links, {"B2": "'Atlas Character Settings'!A2",
                                     "B3": "'Atlas Character Settings'!A4"})
            linked_cell = main.find("s:sheetData/s:row/s:c[@r='B2']", ns)
            self.assertEqual(linked_cell.get("s"), "2")
            self.assertNotIn("B4", links)
            self.assertFalse(any(link.get(f"{{{workbook_export.DOC_REL}}}id")
                                 for link in main.findall("s:hyperlinks/s:hyperlink", ns)))

            atlas = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
            self.assertEqual(atlas.find("s:dimension", ns).get("ref"), "A1:O5")
            self.assertEqual(atlas.find("s:autoFilter", ns).get("ref"), "A1:O5")
            self.assertEqual(atlas.find("s:sheetViews/s:sheetView/s:pane", ns).get("state"), "frozen")
            rows = atlas.findall("s:sheetData/s:row", ns)
            self.assertEqual(["".join(cell.itertext()) for cell in rows[0]], ATLAS_HEADERS)
            cells = {cell.get("r"): cell for row in rows for cell in row}
            for address, value in (("D2", "0"), ("I2", "0"), ("I3", "1"), ("D4", "175.5")):
                self.assertIsNone(cells[address].get("t"))
                self.assertEqual(cells[address].findtext("s:v", namespaces=ns), value)
            self.assertNotIn("D5", cells)
            self.assertNotIn("J2", cells)
            self.assertEqual("".join(cells["F2"].itertext()), "0007")
            self.assertEqual("".join(cells["J3"].itertext()), "001")
            self.assertEqual("".join(cells["B2"].itertext()), "M0001, M0003")

    def test_setup_hyperlinks_follow_reordered_columns_and_escape_sheet_name(self):
        main_data = csv_bytes(["Map ID", "Detail", "Atlas Setup ID"],
                              [["M1", "first", "setup-two"], ["M2", "second", "setup-one"],
                               ["M3", "legacy", ""], ["M4", "unmatched", "missing"]])
        atlas_data = csv_bytes(["Node ID", "Atlas Setup ID", "Map IDs"],
                               [["n1", "setup-one", "M2"], ["n2", "setup-one", "M2"],
                                ["n1", "setup-two", "M1"]])
        links = workbook_export._atlas_setup_hyperlinks(main_data, atlas_data, "Atlas's Settings")
        self.assertEqual(links, {"C2": "'Atlas''s Settings'!B4", "C3": "'Atlas''s Settings'!B2"})
        root = ET.fromstring(workbook_export._data_sheet(main_data, hyperlinks=links))
        ns = {"s": workbook_export.NS}
        self.assertEqual({link.get("ref"): link.get("location")
                          for link in root.findall("s:hyperlinks/s:hyperlink", ns)}, links)

    def test_actual_export_links_each_map_to_its_matching_saved_setup(self):
        from PoE2_Data_Logger.core import atlas_catalog
        with tempfile.TemporaryDirectory(prefix="atlas-workbook-integration-") as directory:
            previous = store.DATA_DIR
            try:
                store.DATA_DIR = Path(directory)
                logger._READY = False
                logger.initialize()
                logger.start_map()
                catalog = atlas_catalog.catalog()
                nodes = [key for key, node in catalog["nodes"].items()
                         if node.get("allocatable") and not node.get("choices")]
                logger.save_atlas_settings({"catalog_version": catalog["version"],
                    "allocated": nodes[:1], "choices": {}, "gear_item_rarity": 0})
                logger.commit_chain("Rage")
                logger.finish_map(0, 0, 0, 0)
                logger.save_atlas_settings({"catalog_version": catalog["version"],
                    "allocated": nodes[:2], "choices": {}, "gear_item_rarity": 57.5})
                logger.start_map()
                logger.commit_chain("Time")
                main_rows = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
                atlas_rows = list(csv.DictReader(io.StringIO(logger.export_atlas_csv().decode("utf-8-sig"))))
                setup_by_map = {row["Map ID"]: row["Atlas Setup ID"] for row in main_rows}
                self.assertEqual(set(setup_by_map), {"M0001", "M0002"})
                self.assertNotEqual(setup_by_map["M0001"], setup_by_map["M0002"])
                for row in atlas_rows:
                    self.assertEqual(row["Atlas Setup ID"], setup_by_map[row["Map IDs"]])
                with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
                    ns = {"s": workbook_export.NS}
                    main = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
                    atlas = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
                    main_cells = {cell.get("r"): "".join(cell.itertext())
                                  for cell in main.findall("s:sheetData/s:row/s:c", ns)}
                    atlas_cells = {cell.get("r"): "".join(cell.itertext())
                                   for cell in atlas.findall("s:sheetData/s:row/s:c", ns)}
                    links = main.findall("s:hyperlinks/s:hyperlink", ns)
                    self.assertEqual(len(links), len(main_rows))
                    for link in links:
                        self.assertTrue(link.get("location").startswith("'Atlas Character Settings'!"))
                        target = link.get("location").rsplit("!", 1)[-1]
                        self.assertEqual(main_cells[link.get("ref")], atlas_cells[target])
            finally:
                store.DATA_DIR = previous
                logger._READY = False

    def test_invalid_atlas_row_width_rejected(self):
        with patch.object(logger, "_connect", memory_database), \
                patch.object(logger, "export_all_csv", return_value=csv_bytes(["Map ID"], [])), \
                patch.object(logger, "export_atlas_csv",
                             return_value=csv_bytes(ATLAS_HEADERS, [["too", "short"]]), create=True):
            with self.assertRaisesRegex(ValueError, "invalid width"):
                workbook_export.export_xlsx()

    def test_both_sheets_use_the_same_read_snapshot_during_a_concurrent_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.sqlite3"
            writer = sqlite3.connect(path)
            try:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("CREATE TABLE setup (rarity INTEGER)")
                writer.execute("INSERT INTO setup VALUES (25)")
                writer.commit()

                @contextmanager
                def reader_database():
                    reader = sqlite3.connect(path)
                    try:
                        yield reader
                    finally:
                        reader.close()

                def main_export(*, _db):
                    rarity = _db.execute("SELECT rarity FROM setup").fetchone()[0]
                    writer.execute("UPDATE setup SET rarity=75")
                    writer.commit()
                    return csv_bytes(["Gear Item Rarity %"], [[rarity]])

                def atlas_export(*, _db):
                    rarity = _db.execute("SELECT rarity FROM setup").fetchone()[0]
                    return csv_bytes(["Gear Item Rarity %"], [[rarity]])

                with patch.object(logger, "_connect", reader_database), \
                        patch.object(logger, "export_all_csv", side_effect=main_export), \
                        patch.object(logger, "export_atlas_csv", side_effect=atlas_export, create=True):
                    data = workbook_export.export_xlsx()
                self.assertEqual(writer.execute("SELECT rarity FROM setup").fetchone()[0], 75)
                with ZipFile(io.BytesIO(data)) as archive:
                    for number in (1, 2):
                        sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
                        cell = sheet.find(f".//{{{workbook_export.NS}}}c[@r='A2']")
                        self.assertEqual(cell.findtext(f"{{{workbook_export.NS}}}v"), "25")
            finally:
                writer.close()

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "optional Excel reader is not installed")
    def test_excel_reader_opens_both_sheets_with_numeric_and_blank_values(self):
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(self.sample_workbook()), data_only=True)
        try:
            self.assertEqual(workbook.sheetnames, ["Export", "Atlas Character Settings"])
            self.assertEqual(workbook["Export"]["C2"].value, 0)
            self.assertEqual(workbook["Export"]["B2"].hyperlink.location,
                             "'Atlas Character Settings'!A2")
            self.assertIsNone(workbook["Export"]["B4"].hyperlink)
            sheet = workbook["Atlas Character Settings"]
            self.assertEqual(sheet["D2"].value, 0)
            self.assertEqual(sheet["I2"].value, 0)
            self.assertEqual(sheet["D4"].value, 175.5)
            self.assertIsNone(sheet["D5"].value)
            self.assertIsNone(sheet["J2"].value)
            self.assertEqual(sheet["J3"].value, "001")
            self.assertEqual(sheet.freeze_panes, "A2")
            self.assertEqual(sheet.auto_filter.ref, "A1:O5")
        finally:
            workbook.close()


if __name__ == "__main__":
    unittest.main()
