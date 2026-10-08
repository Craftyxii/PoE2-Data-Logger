"""Keep review-labelled items in distinct numeric CSV and workbook columns."""
import csv
import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


class ReviewLearningExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-review-export-")
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

    def main_csv(self):
        raw = list(csv.reader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        self.assertEqual(len(raw[0]), len(set(raw[0])))
        self.assertTrue(all(len(row) == len(raw[0]) for row in raw))
        return raw[0], [dict(zip(raw[0], row)) for row in raw[1:]]

    def main_workbook(self):
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            root = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
        ns = {"s": workbook_export.NS}
        self.assertEqual(root.findall(".//s:f", ns), [])
        rows = root.findall("s:sheetData/s:row", ns)
        headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext()) for cell in rows[0]}
        self.assertEqual(len(headers), len(set(headers.values())))
        results = []
        for row in rows[1:]:
            values = {name: "" for name in headers.values()}
            types = {}
            for cell in row:
                name = headers[cell.get("r").rstrip("0123456789")]
                values[name] = "".join(cell.itertext())
                types[name] = cell.get("t")
            results.append((values, types))
        return list(headers.values()), results

    def assert_workbook_matches(self, headers, rows, numeric):
        workbook_headers, workbook = self.main_workbook()
        self.assertEqual(workbook_headers, headers)
        self.assertEqual([values for values, _ in workbook], rows)
        for values, types in workbook:
            for header in numeric:
                if values[header] != "":
                    self.assertIsNone(types[header], f"{header} counts must be numeric")

    def test_new_currency_omen_and_equipment_have_their_own_count_columns(self):
        logger.add_currency_item("Review Learned Currency")
        logger.add_item_name("Review Learned Armour")
        logger.add_ritual_name("Omen of Review Learning")
        logger.save_currency_snapshot("start", [
            {"name": "review learned currency", "quantity": 2},
            {"name": "REVIEW LEARNED CURRENCY", "quantity": 3},
            {"name": "review learned armour", "quantity": 1}])
        saved = logger.save_ritual_page([
            {"category": "Omen", "name": "Omen of Review Learning", "quantity": 4},
            {"category": "Omen", "name": "Omen of Review Learning", "quantity": 7, "deferred": True},
            {"category": "Item", "name": "Review Learned Armour", "quantity": 2}])
        logger.save_currency_snapshot("end", [{"name": "Review Learned Currency", "quantity": 8}])
        headers, rows = self.main_csv()
        columns = ("Currency: Review Learned Currency", "Omen: Omen of Review Learning",
                   "Item: Review Learned Armour")
        for column in columns:
            self.assertIn(column, headers)
        currency = [row for row in rows if row["Currency"] == "Review Learned Currency"]
        self.assertEqual([row[columns[0]] for row in currency], ["5", "8"])
        self.assertEqual(currency[-1]["Net Change"], "3")
        ritual = [row for row in rows if row["Scan Commit #"] == str(saved["scan_commit_number"])]
        self.assertEqual([row[columns[1]] for row in ritual], ["4", "0", ""])
        self.assertEqual([row[columns[2]] for row in ritual], ["", "", "2"])
        self.assertTrue(all(row["Map ID"] == "M0001" for row in rows))
        self.assert_workbook_matches(headers, rows, columns)

    def test_historical_item_columns_survive_reference_removal_and_keep_map_ids(self):
        logger.add_currency_item("First Map Currency")
        logger.add_item_name("First Map Equipment")
        logger.save_currency_snapshot("start", [{"name": "First Map Currency", "quantity": 3},
                                                 {"name": "First Map Equipment", "quantity": 2}])
        first = logger.save_ritual_page([{"category": "Omen", "name": "First Map Omen", "quantity": 4}])
        logger.finish_map(0, 0, 0)
        logger.start_map()
        logger.add_currency_item("Second Map Currency")
        logger.save_currency_snapshot("start", [{"name": "Second Map Currency", "quantity": 5}])
        with logger._connect() as db:
            db.execute("DELETE FROM currency_items WHERE name='First Map Currency'")
            db.execute("DELETE FROM item_names WHERE name='First Map Equipment'")
            db.execute("DELETE FROM ritual_names WHERE name='First Map Omen'")
        headers, rows = self.main_csv()
        columns = ("Currency: First Map Currency", "Item: First Map Equipment", "Omen: First Map Omen",
                   "Currency: Second Map Currency")
        self.assertTrue(all(column in headers for column in columns))
        first_rows = [row for row in rows if row["Map ID"] == "M0001"]
        second_rows = [row for row in rows if row["Map ID"] == "M0002"]
        self.assertEqual(sum(int(row[columns[0]] or 0) for row in first_rows), 3)
        self.assertEqual(sum(int(row[columns[1]] or 0) for row in first_rows), 2)
        self.assertEqual(sum(int(row[columns[2]] or 0) for row in first_rows), 4)
        self.assertEqual(sum(int(row[columns[3]] or 0) for row in second_rows), 5)
        self.assertTrue(all(not row[columns[3]] for row in first_rows))
        self.assertTrue(all(not row[column] for row in second_rows for column in columns[:3]))
        omen = next(row for row in first_rows if row["Scan Commit #"] == str(first["scan_commit_number"]))
        self.assertEqual((omen["Map ID"], omen["Ritual Page"]), ("M0001", "1"))
        self.assert_workbook_matches(headers, rows, columns)

    def test_reserved_and_formula_names_cannot_collide_or_create_formulas(self):
        names = ("Map ID", "Quantity", "=SUM(1,2)", "'=SUM(1,2)", "Currency: Map ID")
        for name in names:
            logger.add_currency_item(name)
        logger.save_currency_snapshot("start", [{"name": name, "quantity": index + 1}
                                                 for index, name in enumerate(names)])
        headers, rows = self.main_csv()
        columns = tuple(f"Currency: {name}" for name in names)
        self.assertTrue(all(column in headers for column in columns))
        self.assertEqual(len(rows), len(names))
        for index, column in enumerate(columns, 1):
            matches = [row for row in rows if row[column] != ""]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0][column], str(index))
            self.assertEqual(matches[0]["Map ID"], "M0001")
        self.assertEqual([row["Currency"] for row in rows].count("'=SUM(1,2)"), 2)
        self.assert_workbook_matches(headers, rows, columns)

    def test_literal_apostrophe_ritual_names_keep_distinct_counts_after_catalog_deletion(self):
        logger.save_ritual_page([
            {"category": "Item", "name": "=Review Name", "quantity": 2},
            {"category": "Item", "name": "'=Review Name", "quantity": 3},
            {"category": "Omen", "name": "@Review Omen", "quantity": 4}])
        with logger._connect() as db:
            db.execute("DELETE FROM item_names WHERE name IN (?,?)", ("=Review Name", "'=Review Name"))
            db.execute("DELETE FROM ritual_names WHERE name=?", ("@Review Omen",))
        headers, rows = self.main_csv()
        columns = ("Item: =Review Name", "Item: '=Review Name", "Omen: @Review Omen")
        for row, column, count in zip(rows, columns, (2, 3, 4)):
            self.assertEqual(row[column], str(count))
            self.assertTrue(all(not row[other] for other in columns if other != column))
        self.assert_workbook_matches(headers, rows, columns)

    def test_empty_inventory_commit_keeps_audit_metadata_without_an_item_column(self):
        logger.save_currency_snapshot("start", [])
        headers, rows = self.main_csv()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["Type"], rows[0]["Map ID"], rows[0]["Inventory Phase"]),
                         ("Currency", "M0001", "start"))
        self.assertFalse(any(header.startswith(("Currency: ", "Omen: ")) for header in headers))
        self.assertEqual(rows[0]["Quantity"], "")
        self.assert_workbook_matches(headers, rows, ())

    def test_approval_registers_fresh_names_and_unicode_case_variants_share_a_column(self):
        logger.save_currency_snapshot("start", [
            {"name": "Review Straße Currency", "quantity": 2},
            {"name": "REVIEW STRASSE CURRENCY", "quantity": 3}], register_names=True)
        saved = logger.save_ritual_page([
            {"category": "Item", "name": "Review Labelled Equipment", "quantity": 1},
            {"category": "Omen", "name": "Omen of Review Straße", "quantity": 2},
            {"category": "Omen", "name": "OMEN OF REVIEW STRASSE", "quantity": 3}], register_names=True)
        headers, rows = self.main_csv()
        columns = ("Currency: Review Straße Currency", "Item: Review Labelled Equipment",
                   "Omen: Omen of Review Straße")
        self.assertTrue(all(column in headers for column in columns))
        self.assertFalse(any("STRASSE" in header for header in headers))
        currency = next(row for row in rows if row["Type"] == "Currency")
        self.assertEqual(currency[columns[0]], "5")
        ritual = [row for row in rows if row["Scan Commit #"] == str(saved["scan_commit_number"])]
        self.assertEqual([row["Reward Name"] for row in ritual],
                         ["Review Labelled Equipment", "Omen of Review Straße", "Omen of Review Straße"])
        self.assertEqual([row[columns[2]] for row in ritual], ["", "2", "3"])
        self.assert_workbook_matches(headers, rows, columns)


if __name__ == "__main__":
    unittest.main()
