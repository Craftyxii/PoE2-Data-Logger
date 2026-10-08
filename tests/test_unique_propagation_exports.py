"""Export checks projecting unique/total kills and one-per-scan propagation counts without inflating paired runes."""

import csv
import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


class UniquePropagationExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-unique-prop-export-")
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

    def rows(self, producer):
        raw = list(csv.reader(io.StringIO(producer().decode("utf-8-sig"))))
        self.assertEqual(len(raw[0]), len(set(raw[0])))
        self.assertTrue(all(len(row) == len(raw[0]) for row in raw))
        return [dict(zip(raw[0], row)) for row in raw[1:]]

    def test_unique_and_total_kills_keep_unknown_zero_partial_and_old_commit_values(self):
        unknown = logger.save_kills("", "", "", unique="")["scan_commit_number"]
        zero = logger.save_kills(0, 0, 0, unique=0)["scan_commit_number"]
        partial = logger.save_kills(5, None, 0, unique=None)["scan_commit_number"]
        complete = logger.save_kills(11, 22, 33, unique=4)["scan_commit_number"]
        for producer in (logger.export_record_history_csv, logger.export_all_csv):
            rows = {int(row["Scan Commit #"]): row for row in self.rows(producer)}
            for number, expected in ((unknown, ("", "")), (zero, ("0", "0")),
                                     (partial, ("", "5")), (complete, ("4", "70"))):
                self.assertEqual((rows[number]["Unique Kills (Map)"], rows[number]["Total Kills"]), expected)
        logger.finish_map(11, 22, 33)
        logger.start_map()
        rows = {row["Map ID"]: row for row in self.rows(logger.export_maps_csv)}
        self.assertEqual((rows["M0001"]["Unique Kills"], rows["M0001"]["Total Kills"]), ("4", "70"))
        self.assertEqual((rows["M0002"]["Unique Kills"], rows["M0002"]["Total Kills"]), ("", ""))

    def test_low_level_remnant_export_projects_unique_once_without_changing_original_columns(self):
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        logger.save_kills(1, 2, 3, unique=4)
        raw = list(csv.reader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        with logger._connect() as db:
            original = logger._meta(db, "export_headers")
        self.assertEqual(raw[0][:67], original)
        rows = self.rows(logger.export_csv)
        self.assertEqual((rows[0]["Unique Kills (Map)"], rows[0]["Total Kills"]), ("4", "10"))
        self.assertTrue(all(row["Unique Kills (Map)"] == "" for row in rows[1:]))
        main_headers = next(csv.reader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        self.assertEqual(main_headers.index("Type"), 127)
        self.assertEqual(main_headers[-len(logger.HISTORY_APPEND_HEADERS):], list(logger.HISTORY_APPEND_HEADERS))

    def test_propagation_scan_rows_preserve_pair_order_ids_and_one_per_scan_delta(self):
        first = logger.increment_propagation_detonated(logger.scan_context(),
                    runes=["Rage", "Time"], recipe="3x Greater Exalted Orb")
        second = logger.increment_propagation_detonated(logger.scan_context(),
                    runes=["Bond"], recipe="1x Regal Orb")
        logger.commit_chain_draft([{"rune1": "Rage", "rune2": "Time"},
                                   {"rune1": "Bond", "rune2": ""}])
        logger.complete_chain(logger.scan_context())
        third = logger.increment_propagation_detonated(logger.scan_context(),
                    runes=["Death", "Power"], recipe="1x Chaos Orb")
        logger.commit_chain_draft([{"rune1": "Death", "rune2": "Power"}])
        for producer in (logger.export_record_history_csv, logger.export_all_csv):
            rows = self.rows(producer)
            scans = [row for row in rows if row["Type"] == "Propagation"]
            self.assertEqual(len(scans), 3)
            self.assertEqual([row["Propagation Rune 1"] for row in scans], ["Rage", "Bond", "Death"])
            self.assertEqual([row["Propagation Rune 2"] for row in scans], ["Time", "", "Power"])
            self.assertEqual([row["Expedition ID"] for row in scans],
                             ["M0001-E01", "M0001-E01", "M0001-E02"])
            self.assertEqual([row["Remnants Detonated (Expedition)"] for row in scans], ["1", "2", "1"])
            self.assertEqual(sum(int(row["Remnants Detonated (Scan)"] or 0) for row in rows), 3)
            self.assertTrue(all(row["Remnants Detonated (Scan)"] == "" for row in rows if row["Type"] == "Chain"))
            self.assertEqual([int(row["Scan Commit #"]) for row in scans],
                             [first["scan_commit_number"], second["scan_commit_number"], third["scan_commit_number"]])
            recipe_field = "Recipe" if producer == logger.export_record_history_csv else "Matched Recipe"
            self.assertEqual([row[recipe_field] for row in scans],
                             ["3x Greater Exalted Orb", "1x Regal Orb", "1x Chaos Orb"])
            self.assertTrue(all(not row.get("Remnant ID") and not row.get("Family ID") for row in scans))
        maps = self.rows(logger.export_maps_csv)
        self.assertEqual((maps[0]["Expedition 1 Detonated"], maps[0]["Expedition 2 Detonated"]), ("2", "1"))

    def test_xlsx_new_metrics_are_numeric_and_unknown_remains_blank(self):
        logger.save_kills(0, 0, 0, unique=0)
        logger.increment_propagation_detonated(logger.scan_context(), runes=["Rage", "Time"], recipe="Example")
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            ns = {"s": workbook_export.NS}
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
            rows = sheet.findall("s:sheetData/s:row", ns)
            headers = {cell.get("r").rstrip("1"): "".join(cell.itertext()) for cell in rows[0]}
            found = {}
            for row in rows[1:]:
                for cell in row:
                    field = headers[cell.get("r").rstrip("0123456789")]
                    if field in ("Unique Kills (Map)", "Total Kills", "Remnants Detonated (Scan)"):
                        found[field] = (cell.get("t"), cell.findtext("s:v", namespaces=ns))
            self.assertEqual(found, {"Unique Kills (Map)": (None, "0"), "Total Kills": (None, "0"),
                                     "Remnants Detonated (Scan)": (None, "1")})
        self.assertTrue(workbook_export._numeric_column("Stat: some_effect"))
        self.assertTrue(workbook_export._numeric_column("Applied Stat: some_effect"))


if __name__ == "__main__":
    unittest.main()
