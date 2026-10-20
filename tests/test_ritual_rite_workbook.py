"""Keep the new, source-rich Rite JSON lossless in Excel without changing existing CSV fields."""

import csv
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export as workbook
from PoE2_Data_Logger.core.ritual_rites_catalog import affixes


FIELDS = ("Ritual Rite Affixes JSON", "Ritual Rite Inherited Sources JSON")


def csv_data(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def sheet_data(xml):
    root = ET.fromstring(xml)
    result = []
    for row in root.findall(f"{{{workbook.NS}}}sheetData/{{{workbook.NS}}}row"):
        values = {}
        for cell in row:
            values[workbook._col_number(cell.get("r")) - 1] = "".join(cell.itertext())
            if cell.get("t") == "inlineStr":
                text = cell.find(f"{{{workbook.NS}}}is/{{{workbook.NS}}}t")
                if text is not None:
                    assert len(text.text or "") <= 32767
        result.append(values)
    headers = [result[0].get(index, "") for index in range(max(result[0]) + 1)]
    assert len(headers) == len(set(headers))
    rows = [{name: row.get(index, "") for index, name in enumerate(headers)} for row in result[1:]]
    return root, headers, rows


def reconstructed(row, field, extra_headers):
    suffix = re.compile(re.escape(field) + r" continuation (\d+)$")
    parts = [(int(found.group(1)), key) for key in extra_headers
             if (found := suffix.search(key))]
    return row[field] + "".join(row[key] for _, key in sorted(parts))


class RiteJsonCellTests(unittest.TestCase):
    def test_only_oversized_new_fields_add_columns_and_keep_original_order_and_values(self):
        for length, count in ((0, 0), (32767, 0), (32768, 1), (65534, 1), (65535, 2)):
            with self.subTest(length=length):
                headers = ["Map ID", FIELDS[0], FIELDS[1], "Quantity"]
                original = "x" * length
                data = csv_data(headers, [["M0001", original, "[]", "3"], ["M0002", "[]", "[]", "4"]])
                root, actual_headers, rows = sheet_data(workbook._data_sheet(data))
                self.assertEqual(actual_headers[:len(headers)], headers)
                self.assertEqual(len(actual_headers), len(headers) + count)
                self.assertEqual(reconstructed(rows[0], FIELDS[0], actual_headers[len(headers):]), original)
                self.assertEqual(rows[0][FIELDS[1]], "[]")
                self.assertEqual(rows[0]["Quantity"], "3")
                self.assertEqual(reconstructed(rows[1], FIELDS[0], actual_headers[len(headers):]), "[]")
                bounds = f"A1:{workbook._column(len(actual_headers))}3"
                self.assertEqual(root.find(f"{{{workbook.NS}}}dimension").get("ref"), bounds)
                self.assertEqual(root.find(f"{{{workbook.NS}}}autoFilter").get("ref"), bounds)

    def test_item_header_collisions_cannot_replace_item_columns_or_duplicate_generated_labels(self):
        collision = FIELDS[0] + " continuation 1"
        headers = [FIELDS[0], FIELDS[1], collision, "Rite metadata | " + collision]
        original = "{\"evidence\":\"" + "x" * 66000 + "\"}"
        data = csv_data(headers, [[original, original, "7", "8"]])
        _, actual_headers, rows = sheet_data(workbook._data_sheet(data))
        self.assertEqual(actual_headers[:len(headers)], headers)
        generated = actual_headers[len(headers):]
        self.assertTrue(all(name.startswith("Rite metadata | Rite metadata | ") for name in generated))
        self.assertEqual((rows[0][collision], rows[0]["Rite metadata | " + collision]), ("7", "8"))
        for field in FIELDS:
            self.assertEqual(reconstructed(rows[0], field, generated), original)

    def test_old_unrelated_columns_and_malformed_row_guards_keep_existing_behavior(self):
        data = csv_data(["Name", "Other JSON"], [["Unrelated", "x" * 32768]])
        _, headers, rows = sheet_data(workbook._data_sheet(data))
        self.assertEqual(headers, ["Name", "Other JSON"])
        self.assertEqual(len(rows[0]["Other JSON"]), 32767)
        for headers in ([FIELDS[0], "Name"], ["Name", "Other JSON"]):
            with self.subTest(headers=headers), self.assertRaisesRegex(ValueError, "invalid width"):
                workbook._data_sheet(csv_data(headers, [["only one value"]]))


class RiteWorkbookStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-rite-workbook-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def test_real_catalog_and_many_origin_maps_round_trip_export_history_and_sql(self):
        catalog = list(affixes())
        proliferation = [row["id"] for row in catalog if row["kind"] == "proliferation"]
        logger.new_ritual_rite("Source-rich Rite")
        for number in range(1, 9):
            logger.save_ritual_rite_affixes([row["id"] for row in catalog] if number == 1 else proliferation)
            logger.start_map()
            logger.record_commit("Map settings")
            logger.finish_map(number, number + 1, number + 2)
        with logger._connect() as db:
            before_maps = [tuple(row) for row in db.execute("SELECT * FROM maps ORDER BY map_id")]
            before_commits = [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]
            first = json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])
            last = json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0008'").fetchone()[0])
        self.assertEqual(len(first["ritual_rite_affixes"]), 73)
        self.assertEqual(len(last["ritual_rite_inherited_sources"]), 7 * len(proliferation))
        sources = ((1, logger.export_primary_csv()), (3, logger.export_all_csv()))
        package = workbook.export_xlsx()
        with ZipFile(io.BytesIO(package)) as archive:
            for number, data in sources:
                expected = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
                original_headers = expected.fieldnames
                expected = list(expected)
                _, headers, actual = sheet_data(archive.read(f"xl/worksheets/sheet{number}.xml"))
                self.assertEqual(headers[:len(original_headers)], original_headers)
                self.assertEqual(len(actual), len(expected))
                extras = headers[len(original_headers):]
                for field in FIELDS:
                    self.assertTrue(any(len(row[field]) > 32767 for row in expected))
                    for source, saved in zip(expected, actual):
                        self.assertEqual(reconstructed(saved, field, extras), source[field])
                if number == 1:
                    first_row = next(row for row in actual if row["Map ID"] == "M0001")
                    last_row = next(row for row in actual if row["Map ID"] == "M0008")
                    self.assertEqual(json.loads(reconstructed(first_row, FIELDS[0], extras)),
                                     first["ritual_rite_affixes"])
                    self.assertEqual(json.loads(reconstructed(last_row, FIELDS[1], extras)),
                                     last["ritual_rite_inherited_sources"])
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM maps ORDER BY map_id")], before_maps)
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")], before_commits)


if __name__ == "__main__":
    unittest.main()
