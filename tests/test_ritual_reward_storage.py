"""Check manually resolved Ritual rewards through storage and spreadsheet exports."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


class RitualRewardStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ritual-storage-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def records(self):
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("meta", "maps", "ritual_pages", "commits")}

    def rows(self, exporter):
        rows = list(csv.reader(io.StringIO(exporter().decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def test_unresolved_blank_reward_blocks_whole_page_then_manual_edits_save(self):
        rewards = [{"category": "Omen", "name": "Omen of Whittling", "quantity": 1, "tribute": 250},
                   {"category": "Item", "name": "", "quantity": None, "tribute": None,
                    "needs_review": True, "source": "Occupied reward; name not resolved"}]
        before = self.records()
        with self.assertRaisesRegex(ValueError, "type and a name"):
            logger.save_ritual_page(rewards, scan_hash="a" * 64)
        self.assertEqual(self.records(), before)
        rewards[1].update(name="Unregistered rare helmet", quantity="1", tribute="750", deferred=False)
        saved = logger.save_ritual_page(rewards, scan_hash="a" * 64)
        self.assertEqual((saved["page_number"], saved["count"]), (1, 2))
        stored = logger.ritual_pages_for_map("M0001")[0]["items"]
        self.assertEqual(stored[1], {"category": "Item", "name": "Unregistered rare helmet", "quantity": 1,
                                     "tribute": 750, "deferred": False,
                                     "source": "Occupied reward; name not resolved"})

    def test_currency_equipment_omen_and_deferred_metrics_preserve_order(self):
        rewards = [
            {"category": "Item", "name": "Chaos Orb", "quantity": 5, "tribute": 0},
            {"category": "Item", "name": "Unregistered rare helmet", "quantity": 1, "tribute": None},
            {"category": "Omen", "name": "Omen of Whittling", "quantity": 2, "tribute": 400,
             "deferred": True},
            {"category": "Item", "name": "Unregistered rare belt", "quantity": 1, "tribute": 800,
             "deferred": True}]
        saved = logger.save_ritual_page(rewards, tribute_available=0, rerolls_remaining=0)
        separate = self.rows(logger.export_ritual_csv)
        main = [row for row in self.rows(logger.export_all_csv)
                if row["Scan Commit #"] == str(saved["scan_commit_number"])]
        self.assertEqual([row["Name"] for row in separate], [item["name"] for item in rewards])
        self.assertEqual([row["Reward Name"] for row in main], [item["name"] for item in rewards])
        for rows in (separate, main):
            self.assertEqual([row["New Find Quantity"] for row in rows], ["5", "1", "0", "0"])
            self.assertEqual([row["Tribute"] for row in rows], ["0", "", "400", "800"])
            self.assertEqual([row["Deferred"] for row in rows], ["False", "False", "True", "True"])
            self.assertTrue(all(row["Ritual Tribute Available"] == "0" and
                                row["Ritual Rerolls Remaining"] == "0" for row in rows))
        self.assertEqual([row["Item Name"] for row in main],
                         ["Chaos Orb", "Unregistered rare helmet", "", "Unregistered rare belt"])

    def test_invalid_manual_rows_fail_without_creating_partial_pages(self):
        known = {"category": "Item", "name": "Chaos Orb", "quantity": 1}
        before = self.records()
        for edit in ({"name": " "}, {"category": "Currency"}, {"quantity": ""},
                     {"quantity": 0}, {"quantity": 1.5}, {"tribute": -1}, {"deferred": "True"}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                logger.save_ritual_page([known, {**known, **edit}])
            self.assertEqual(self.records(), before)

    def test_full_120_slot_page_saves_all_rewards_and_121_is_rejected_atomically(self):
        items = [{"category": "Item", "name": f"Reward {index + 1}", "quantity": 1,
                  "tribute": index, "deferred": index % 2 == 0} for index in range(120)]
        saved = logger.save_ritual_page(items, scan_hash="a" * 64)
        self.assertEqual(saved["count"], 120)
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")[0]["items"]), 120)
        separate = self.rows(logger.export_ritual_csv)
        self.assertEqual(len(separate), 120)
        self.assertEqual([row["Name"] for row in separate], [item["name"] for item in items])
        self.assertEqual(sum(int(row["New Find Quantity"]) for row in separate), 60)
        before = self.records()
        with self.assertRaisesRegex(ValueError, "up to 120"):
            logger.save_ritual_page([*items, {"name": "Overflow reward"}], scan_hash="a" * 64)
        self.assertEqual(self.records(), before)

    def test_same_capture_correction_keeps_page_identity_and_original_history(self):
        atlas = dict(logger.get_state()["settings"]["atlas_settings"])
        atlas["gear_item_rarity"] = 100
        logger.save_atlas_settings(atlas)
        first = logger.save_ritual_page([{"name": "Unregistered rare helmet", "quantity": 1}],
                                       scan_hash="a" * 64, tribute_available=10, rerolls_remaining=2)
        with logger._connect() as db:
            original_commit = tuple(db.execute("SELECT * FROM commits WHERE number=?",
                                              (first["scan_commit_number"],)).fetchone())
            original_page_id = db.execute("SELECT id FROM ritual_pages").fetchone()[0]
        atlas["gear_item_rarity"] = 200
        logger.save_atlas_settings(atlas)
        second = logger.save_ritual_page([{"name": "Corrected unique helmet", "quantity": 2, "deferred": True}],
            scan_hash="a" * 64, expected_map_id="M0001", tribute_available=20, rerolls_remaining=0)
        self.assertTrue(second["updated"])
        self.assertEqual(second["page_number"], first["page_number"])
        with logger._connect() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM commits WHERE number=?",
                                             (first["scan_commit_number"],)).fetchone()), original_commit)
            page = db.execute("SELECT * FROM ritual_pages").fetchone()
            self.assertEqual(page["id"], original_page_id)
            snapshot = db.execute("SELECT snapshot_json FROM commits WHERE number=?",
                                  (second["scan_commit_number"],)).fetchone()[0]
            self.assertEqual(page["snapshot_json"], snapshot)
            self.assertEqual(json.loads(snapshot)["gear_item_rarity"], 100)
        exported = self.rows(logger.export_ritual_csv)
        self.assertEqual(len(exported), 1)
        self.assertEqual((exported[0]["Name"], exported[0]["Quantity"], exported[0]["New Find Quantity"],
                          exported[0]["Ritual Rerolls Remaining"]), ("Corrected unique helmet", "2", "0", "0"))
        self.assertEqual(exported[0]["Scan Commit #"], str(second["scan_commit_number"]))

    def test_capture_identity_is_map_scoped_and_old_map_cannot_be_overwritten(self):
        logger.save_ritual_page([{"name": "First map reward", "quantity": 1}], scan_hash="a" * 64)
        logger.finish_map(0, 0, 0)
        logger.start_map()
        before = self.records()
        with self.assertRaisesRegex(ValueError, "belongs to M0001"):
            logger.save_ritual_page([{"name": "Wrong map reward"}], scan_hash="a" * 64,
                                   expected_map_id="M0001")
        self.assertEqual(self.records(), before)
        second = logger.save_ritual_page([{"name": "Second map reward", "quantity": 2}], scan_hash="a" * 64,
                                        expected_map_id="M0002")
        self.assertFalse(second["updated"])
        self.assertEqual(second["page_number"], 1)
        self.assertEqual([row["Name"] for row in self.rows(logger.export_ritual_csv)],
                         ["First map reward", "Second map reward"])

    def test_manual_names_and_sources_export_as_text_not_spreadsheet_formulas(self):
        logger.save_ritual_page([{"name": "=SUM(1,2)", "quantity": 1, "source": "@unexpected source"}],
                               raw_text="+captured text")
        row = self.rows(logger.export_ritual_csv)[0]
        self.assertEqual(row["Name"], "'=SUM(1,2)")
        self.assertEqual(row["OCR Source"], "'@unexpected source")
        self.assertEqual(row["Page OCR Text"], "'+captured text")
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            ns = {"s": workbook_export.NS}
            self.assertEqual(sheet.findall(".//s:f", ns), [])
            texts = [node.text or "" for node in sheet.findall(".//s:t", ns)]
        self.assertIn("'=SUM(1,2)", texts)


if __name__ == "__main__":
    unittest.main()
