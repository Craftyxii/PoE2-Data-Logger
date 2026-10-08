"""Exercise chain correction and currency accounting together through Review."""
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class ChainCurrencyWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-chain-currency-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        image = io.BytesIO()
        Image.new("RGB", (575, 720), "tan").save(image, format="PNG")
        self.raw = image.getvalue()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def currency(self, phase, amounts, approve=True):
        select(self.window.inventory_phase, phase)
        self.window._inventory_read({"items": [
            {"slot": index, "name": name, "quantity": quantity}
            for index, (name, quantity) in enumerate(amounts.items(), 1)], "unknown": []}, live=False)
        if approve:
            self.window.approve_review()

    def totals(self):
        return {name: card.quantity for name, card in self.window.session_currency.cards.items()
                if card.quantity > 0}

    def rows(self, data):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def assert_workbook_matches_csv(self):
        ns = {"s": workbook_export.NS}
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            for index, csv_data in enumerate((logger.export_all_csv(), logger.export_atlas_csv()), 1):
                root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
                rows = root.findall("s:sheetData/s:row", ns)
                headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext()) for cell in rows[0]}
                actual = []
                for row in rows[1:]:
                    values = dict.fromkeys(headers.values(), "")
                    for cell in row:
                        self.assertIsNone(cell.find("s:f", ns))
                        values[headers[cell.get("r").rstrip("0123456789")]] = "".join(cell.itertext())
                    actual.append(values)
                self.assertEqual(actual, self.rows(csv_data))

    def test_review_choice_and_manual_pair_commit_without_crossing_currency_map_ids(self):
        result = {"mode": "propagation", "can_use": False, "runes": [],
                  "status": "Confirm the propagated recipe", "choices": [
                      {"selected_recipe": "Greater Jeweller's Orb", "runes": ["Death", "Power"], "can_use": True},
                      {"selected_recipe": "Chaos Orb", "runes": ["Rage"], "can_use": True}],
                  **logger.scan_context()}
        self.window._propagation_read(result, self.raw)
        self.window.deny_propagation_recipe(1)
        self.window.approve_propagation_recipe(0)
        for field, rune in zip(self.window.propagation_rune_inputs, ("Rage", "Time")):
            field.setEditText(rune)
        self.window.propagation_add_button.click()
        self.assertEqual(logger.get_state()["detonated"], 2)
        self.assertEqual(self.window._chain_steps(), [
            {"rune1": "Death", "rune2": "Power"}, {"rune1": "Rage", "rune2": "Time"}])
        self.window.review_commit_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

        # The currency snapshot belongs to the map, independently of the
        # expedition number that advanced when its chain was committed.
        self.currency("end", {"Chaos Orb": 8, "Divine Orb": 2})
        self.assertEqual(self.totals(), {"Chaos Orb": 8, "Divine Orb": 2})
        self.currency("end", {"Chaos Orb": 7, "Divine Orb": 1})
        self.assertEqual(self.totals(), {"Chaos Orb": 7, "Divine Orb": 1})
        self.window.finish_map()
        self.currency("start", {"Chaos Orb": 20, "Divine Orb": 2})
        self.currency("end", {"Chaos Orb": 23, "Divine Orb": 1, "Exalted Orb": 4})
        self.assertEqual(self.totals(), {"Chaos Orb": 10, "Divine Orb": 1, "Exalted Orb": 4})

        chains = [row for row in self.rows(logger.export_csv()) if row["Chain Step #"]]
        self.assertEqual([(row["Map ID"], row["Expedition ID"], row["Propagation Rune 1"],
                           row["Propagation Rune 2"]) for row in chains],
                         [("M0001", "M0001-E01", "Death", "Power"),
                          ("M0001", "M0001-E01", "Rage", "Time")])
        self.assertEqual(chains[0]["Remnants Detonated (Expedition)"], "2")
        latest = self.rows(logger.export_currency_csv())
        self.assertEqual([(row["Map ID"], row["Currency"], row["Start Count"], row["End Count"],
                           row["Net Change"], row["Start Baseline"]) for row in latest],
                         [("M0001", "Chaos Orb", "0", "7", "7", "Assumed empty"),
                          ("M0001", "Divine Orb", "0", "1", "1", "Assumed empty"),
                          ("M0002", "Chaos Orb", "20", "23", "3", "Scanned"),
                          ("M0002", "Divine Orb", "2", "1", "-1", "Scanned"),
                          ("M0002", "Exalted Orb", "0", "4", "4", "Scanned")])
        maps = self.rows(logger.export_maps_csv())
        self.assertEqual(maps[0]["Expedition 1 Detonated"], "2")
        history = self.rows(logger.export_record_history_csv())
        propagation = [row for row in history if row["Type"] == "Propagation"]
        self.assertEqual([(row["Map ID"], row["Expedition ID"], row["Propagation Rune 1"],
                           row["Propagation Rune 2"]) for row in propagation],
                         [("M0001", "M0001-E01", "Death", "Power"),
                          ("M0001", "M0001-E01", "Rage", "Time")])
        for data in (logger.export_record_history_csv(), logger.export_all_csv()):
            found = {}
            for row in self.rows(data):
                if row["Type"] == "Currency" and row["Session Found Quantity"]:
                    found[row["Currency"]] = found.get(row["Currency"], 0) + int(row["Session Found Quantity"])
            self.assertEqual({name: quantity for name, quantity in found.items() if quantity}, self.totals())
        with logger._connect() as db:
            saved = {(row["map_id"], row["phase"]): json.loads(row["items_json"])
                     for row in db.execute("SELECT map_id,phase,items_json FROM currency_snapshots")}
        self.assertEqual(saved, {("M0001", "end"): {"Chaos Orb": 7, "Divine Orb": 1},
                                 ("M0002", "start"): {"Chaos Orb": 20, "Divine Orb": 2},
                                 ("M0002", "end"): {"Chaos Orb": 23, "Divine Orb": 1, "Exalted Orb": 4}})
        self.assert_workbook_matches_csv()

    def test_live_auto_commit_end_only_and_uncertain_rejection_do_not_double_credit(self):
        self.window.set_auto_all(True)
        select(self.window.inventory_phase, "end")
        clear = {"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 6}], "unknown": []}
        self.window._inventory_read(clear, live=True)
        self.assertEqual(self.totals(), {"Chaos Orb": 6})
        self.assertIsNone(self.window.pending_review_kind)
        self.window._inventory_read(clear, live=True)
        self.assertEqual(self.totals(), {"Chaos Orb": 6})
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 99,
                                                "count_needs_review": True}], "unknown": []}, live=True)
        self.assertEqual(self.window.pending_review_kind, "currency")
        self.assertEqual(self.totals(), {"Chaos Orb": 6})
        self.window.reject_review()
        self.assertEqual(self.rows(logger.export_currency_csv())[0]["Net Change"], "6")
        self.assert_workbook_matches_csv()

    def test_reject_restart_and_id_reset_preserve_then_clear_only_approved_data(self):
        self.currency("end", {"Chaos Orb": 4})
        self.currency("end", {"Chaos Orb": 999}, approve=False)
        self.window.reject_review()
        self.assertEqual(self.totals(), {"Chaos Orb": 4})
        stale = logger.scan_context()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.assertEqual(self.totals(), {"Chaos Orb": 4})
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.reset_logger()
        self.assertEqual(self.totals(), {})
        with self.assertRaisesRegex(ValueError, "previous session"):
            self.window._propagation_read({**stale, "can_use": True, "runes": ["Death"]}, self.raw)
        for export in (logger.export_currency_csv, logger.export_record_history_csv,
                       logger.export_commits_csv, logger.export_csv, logger.export_all_csv):
            self.assertEqual(self.rows(export()), [])
        self.currency("end", {"Exalted Orb": 3})
        self.assertEqual(self.totals(), {"Exalted Orb": 3})
        self.assertEqual(self.rows(logger.export_currency_csv())[0]["Map ID"], "M0001")
        self.assert_workbook_matches_csv()

    def test_uncertain_recipe_accepts_manual_runes_and_keeps_recipe_in_saved_audit(self):
        result = {"mode": "propagation", "can_use": False, "runes": [],
                  "status": "Confirm marked runes", "choices": [
                      {"selected_recipe": "Greater Jeweller's Orb", "runes": [], "can_use": False,
                       "status": "Marks unclear"},
                      {"selected_recipe": "Chaos Orb", "runes": ["Rage"], "can_use": True}],
                  **logger.scan_context()}
        self.window._propagation_read(result, self.raw)
        self.window.propagation_recipe_table.setCurrentCell(0, 0)
        for field, rune in zip(self.window.propagation_rune_inputs, ("Death", "Rebirth")):
            field.setEditText(rune)
        self.window.approve_propagation_recipe(0)
        self.assertEqual(self.window._chain_steps(), [{"rune1": "Death", "rune2": "Rebirth"}])
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(self.window.chain_review_table.item(0, 2).text(), "Greater Jeweller's Orb")
        self.window.commit_chain()
        rows = [row for row in self.rows(logger.export_record_history_csv()) if row["Type"] == "Propagation"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["Map ID"], rows[0]["Expedition ID"], rows[0]["Recipe"],
                          rows[0]["Propagation Rune 1"], rows[0]["Propagation Rune 2"]),
                         ("M0001", "M0001-E01", "Greater Jeweller's Orb", "Death", "Rebirth"))
        self.assert_workbook_matches_csv()


if __name__ == "__main__":
    unittest.main()
