"""Export first/final approved Ritual counters once per map without summing scans."""

import csv
import io
import json
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from tests import test_ritual_counters as fixtures
from PoE2_Data_Logger.core import logger_store as logger, workbook_export


class RitualCounterExportTests(unittest.TestCase):
    setUp = fixtures.RitualCounterSummaryTests.setUp
    tearDown = fixtures.RitualCounterSummaryTests.tearDown
    save = staticmethod(fixtures.RitualCounterSummaryTests.save)

    @staticmethod
    def rows(producer):
        return list(csv.DictReader(io.StringIO(producer().decode("utf-8-sig"))))

    def test_maps_summary_primary_and_workbook_keep_first_and_latest_approved_readings(self):
        logger.start_map()
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        first = self.save(0, 3)
        self.save(900, 2, fingerprint="b")
        final = self.save(250, 0)
        expected = ["3", "0", "3", "250", "0", str(first["scan_commit_number"]), str(final["scan_commit_number"])]
        with logger._connect() as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]
        for producer in (logger.export_maps_csv, logger.export_map_summary_csv, logger.export_primary_csv):
            rows = self.rows(producer)
            map_rows = [row for row in rows if row["Map ID"] == "M0001"]
            self.assertEqual([map_rows[0][key] for key in logger.RITUAL_COUNTER_HEADERS], expected)
            self.assertTrue(all(row[key] == "" for row in map_rows[1:] for key in logger.RITUAL_COUNTER_HEADERS))
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = sheet.findall(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row")
        headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext()) for cell in rows[0]}
        values = {headers[cell.get("r").rstrip("0123456789")]: cell for cell in rows[1]}
        for key, expected_value in zip(logger.RITUAL_COUNTER_HEADERS, expected):
            self.assertNotEqual(values[key].get("t"), "inlineStr")
            self.assertEqual("".join(values[key].itertext()), expected_value)
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")], before)

    def test_unknown_boundaries_and_unscanned_maps_stay_separate(self):
        logger.start_map()
        self.save(None, 4)
        self.save(1000, 3, fingerprint="b")
        self.save(0, None, fingerprint="c")
        logger.finish_map(1, 2, 3)
        logger.start_map()
        maps = {row["Map ID"]: row for row in self.rows(logger.export_maps_csv)}
        self.assertEqual([maps["M0001"][key] for key in logger.RITUAL_COUNTER_HEADERS[:5]],
                         ["3", "", "4", "0", ""])
        self.assertEqual([maps["M0002"][key] for key in logger.RITUAL_COUNTER_HEADERS],
                         ["0", "", "", "", "", "", ""])
        history = [row for row in self.rows(logger.export_record_history_csv) if row["Type"] == "Ritual"]
        self.assertEqual(len(history), 3)
        self.assertEqual([row["Ritual Tribute Available"] for row in history], ["", "1000", "0"])
        with logger._connect() as db:
            details = [json.loads(row[0]) for row in db.execute("SELECT details_json FROM commits WHERE kind='Ritual'")]
        self.assertEqual([row["rerolls_remaining"] for row in details], [4, 3, None])


if __name__ == "__main__":
    unittest.main()
