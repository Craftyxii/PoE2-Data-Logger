"""Round-trip every sourced unique label through approved inventory and Ritual records.

This exercises persistence and export projection, not screenshot recognition. The
bounded API batches intentionally include duplicate approved item rows so saved
quantities and latest-inventory subtraction are checked for every catalog label.
"""

import csv
import io
from itertools import zip_longest
import json
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export
from PoE2_Data_Logger.ocr import unique_items


class UniqueCatalogExportTests(unittest.TestCase):
    """Keep all unique names as distinct Items in SQL, history, totals and XLSX."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-all-unique-exports-")
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

    def csv_rows(self, data):
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        headers = reader.fieldnames
        self.assertEqual(len(headers), len(set(headers)))
        rows = list(reader)
        self.assertTrue(all(None not in row for row in rows))
        return headers, rows

    def test_every_catalog_name_round_trips_with_latest_inventory_and_ritual_offers(self):
        manifest = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger/unique_item_assets/manifest.json"
        source = json.loads(manifest.read_text(encoding="utf-8"))
        names = tuple(sorted({item["name"] for item in source["items"]}, key=str.casefold))
        self.assertEqual(len(source["items"]), 449)
        self.assertEqual(len(names), 441)
        self.assertEqual(unique_items.names(), names)
        # Loading artwork metadata does not register unapproved user data.
        self.assertFalse(set(names).intersection(logger.item_names()))
        self.assertFalse(set(names).intersection(logger.currency_names()))

        maps = {}
        for offset in range(0, len(names), 20):
            batch = names[offset:offset + 20]
            map_id = logger.get_state()["current_map_id"]
            maps[map_id] = batch

            def copies(count):
                return [{"name": name, "quantity": 1, "category": "Item"}
                        for name in batch for _ in range(count)]

            logger.save_currency_snapshot("start", copies(1), register_names=True)
            interim = logger.save_currency_snapshot("end", copies(2), register_names=True)
            self.assertEqual(interim["end"], dict.fromkeys(batch, 2))
            final = logger.save_currency_snapshot("end", copies(3), register_names=True)
            self.assertEqual(final["start"], dict.fromkeys(batch, 1))
            self.assertEqual(final["end"], dict.fromkeys(batch, 3))
            self.assertEqual(final["net"], dict.fromkeys(batch, 2))
            logger.save_ritual_page([
                {"name": name, "quantity": 1, "category": "Item", "deferred": deferred,
                 "source": "approved sourced unique catalog proposal"}
                for name in batch for deferred in (False, True)], register_names=True,
                tribute_available=1000, rerolls_remaining=2)
            logger.finish_map(0, 0, 0)
            if offset + len(batch) < len(names):
                logger.start_map()

        self.assertEqual(set(logger.item_names()), set(names))
        self.assertFalse(set(names).intersection(logger.currency_names()))
        self.assertFalse(set(names).intersection(logger.ritual_names()))
        with logger._connect() as db:
            snapshots = list(db.execute("SELECT map_id,phase,items_json FROM currency_snapshots"))
            self.assertEqual(len(snapshots), len(maps) * 2)
            for saved in snapshots:
                amount = 1 if saved["phase"] == "start" else 3
                self.assertEqual(json.loads(saved["items_json"]), dict.fromkeys(maps[saved["map_id"]], amount))
            for saved in db.execute("SELECT kind,map_id,details_json FROM commits WHERE kind IN ('Currency','Ritual')"):
                details = json.loads(saved["details_json"])
                if saved["kind"] == "Currency":
                    self.assertEqual(details["item_kinds"], dict.fromkeys(maps[saved["map_id"]], "Item"))
                else:
                    self.assertEqual(len(details["items"]), len(maps[saved["map_id"]]) * 2)
                    self.assertTrue(all(item["category"] == "Item" and item["quantity"] == 1
                                        for item in details["items"]))
            pages = list(db.execute("SELECT map_id,items_json FROM ritual_pages"))
            self.assertEqual(len(pages), len(maps))
            for saved in pages:
                offers = json.loads(saved["items_json"])
                self.assertEqual({item["name"] for item in offers}, set(maps[saved["map_id"]]))
                self.assertTrue(all(item["category"] == "Item" for item in offers))

        totals = logger.session_currency_totals()
        self.assertEqual(totals["maps_counted"], len(maps))
        self.assertEqual({row["name"]: (row["kind"], row["quantity"]) for row in totals["items"]},
                         {name: ("Item", 2) for name in names})

        _, inventory = self.csv_rows(logger.export_currency_csv())
        self.assertEqual(len(inventory), len(names))
        self.assertEqual({row["Currency"] for row in inventory}, set(names))
        for row in inventory:
            self.assertIn(row["Currency"], maps[row["Map ID"]])
            self.assertEqual([row[key] for key in ("Start Count", "End Count", "Net Change", "Session Found Quantity")],
                             ["1", "3", "2", "2"])
            self.assertEqual(row["End Currency Scan #"], "3")

        _, ritual = self.csv_rows(logger.export_ritual_csv())
        self.assertEqual(len(ritual), len(names) * 2)
        self.assertEqual({row["Name"] for row in ritual}, set(names))
        for row in ritual:
            self.assertIn(row["Name"], maps[row["Map ID"]])
            self.assertEqual((row["Type"], row["Quantity"]), ("Item", "1"))
            self.assertEqual(row["New Find Quantity"], "0" if row["Deferred"] == "True" else "1")

        headers, history = self.csv_rows(logger.export_all_csv())
        for name in names:
            self.assertIn("Item: " + name, headers)
            self.assertNotIn("Currency: " + name, headers)
            self.assertNotIn("Omen: " + name, headers)
        found = {name: {"inventory": [], "offers": []} for name in names}
        for row in history:
            if row["Type"] == "Item":
                name = row["Item Name"]
                self.assertIn(name, maps[row["Map ID"]])
                self.assertEqual(row["Currency"], "")
                self.assertEqual(row["Item: " + name], row["Quantity"])
                found[name]["inventory"].append(row)
            elif row["Type"] == "Ritual":
                name = row["Reward Name"]
                self.assertEqual(row["Reward Type"], "Item")
                self.assertEqual(row["Item: " + name], row["New Find Quantity"])
                found[name]["offers"].append(row)
        for name, readings in found.items():
            self.assertEqual([row["Quantity"] for row in readings["inventory"]], ["1", "2", "3"], name)
            self.assertEqual([row["Final Currency Scan"] for row in readings["inventory"]],
                             ["False", "False", "True"], name)
            self.assertEqual([row["Session Found Quantity"] for row in readings["inventory"]],
                             ["0", "0", "2"], name)
            self.assertEqual([row["Item: " + name] for row in readings["offers"]], ["1", "0"], name)

        primary_headers, primary = self.csv_rows(logger.export_primary_csv())
        self.assertEqual(len(primary), len(maps))
        for row in primary:
            for name in maps[row["Map ID"]]:
                self.assertIn(name, primary_headers)
                self.assertEqual(row[name], "2")

        book = load_workbook(io.BytesIO(workbook_export.export_xlsx()), read_only=True, data_only=True)
        try:
            for sheet_name, csv_headers, expected_rows in (("Scan History", headers, history),
                                                          ("Export", primary_headers, primary)):
                rows = book[sheet_name].iter_rows(values_only=True)
                self.assertEqual(list(next(rows)), csv_headers)
                type_index = csv_headers.index("Type")
                name_index = csv_headers.index("Item Name" if sheet_name == "Scan History" else "Map ID")
                offer_index = csv_headers.index("Reward Name") if sheet_name == "Scan History" else None
                item_indices = {name: csv_headers.index("Item: " + name if sheet_name == "Scan History" else name)
                                for name in names}
                for expected, values in zip_longest(expected_rows, rows):
                    self.assertIsNotNone(expected)
                    self.assertIsNotNone(values)
                    self.assertEqual(["" if value is None else str(value) for value in values],
                                     [expected[header] for header in csv_headers])
                    if sheet_name == "Scan History" and values[type_index] in ("Item", "Ritual"):
                        name = values[name_index] if values[type_index] == "Item" else values[offer_index]
                        self.assertIs(type(values[item_indices[name]]), int)
                    elif sheet_name == "Export":
                        for name in maps[values[name_index]]:
                            self.assertIs(type(values[item_indices[name]]), int)
                            self.assertEqual(values[item_indices[name]], 2)
        finally:
            book.close()


if __name__ == "__main__":
    unittest.main()
