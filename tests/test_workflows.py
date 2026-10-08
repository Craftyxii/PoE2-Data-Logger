from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store, workbook_export


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def test_repeated_currency_scans_replace_totals_and_keep_history(self):
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 10}])
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 15}])
        saved = logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 12},
                                                     {"name": "Chaos Orb", "quantity": 2}])
        self.assertEqual(saved["end"], {"Chaos Orb": 14})
        self.assertEqual(saved["net"], {"Chaos Orb": 4})
        with logger._connect() as db:
            commits = [json.loads(row[0]) for row in db.execute("SELECT details_json FROM commits WHERE kind='Currency' ORDER BY number")]
        self.assertEqual([row["items"]["Chaos Orb"] for row in commits], [10, 15, 14])
        logger.finish_map(0, 0, 0, 0)
        logger.start_map()
        self.assertEqual(logger.currency_for_map("M0002")["start"], {})
        self.assertEqual(logger.currency_for_map("M0002")["end"], {})
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Chaos Orb": 14})

    def test_configuration_changes_preserve_committed_settings(self):
        logger.save_settings({"tier": 16, "waystone": 87})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        logger.save_ritual_page([{"category": "Item", "name": "Example reward", "quantity": 1}])
        with logger._connect() as db:
            before = list(db.execute("SELECT number,snapshot_json,details_json FROM commits ORDER BY number"))
        logger.save_settings({"tier": 15, "waystone": 20})
        with logger._connect() as db:
            after = list(db.execute("SELECT number,snapshot_json,details_json FROM commits ORDER BY number"))
        self.assertEqual([tuple(row) for row in before], [tuple(row) for row in after])
        self.assertEqual(json.loads(before[0][1])["waystone"], 87)

    def test_deferred_rewards_have_zero_new_find_quantity(self):
        logger.save_ritual_page([{"category": "Omen", "name": "Omen of Whittling", "quantity": 3,
                                 "deferred": True, "tribute": 4000}], tribute_available=1234, rerolls_remaining=2)
        rows = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        row = next(row for row in rows if row.get("New Find Quantity") == "0")
        self.assertEqual(row["Quantity"], "3")
        self.assertEqual(row["Deferred"], "True")

    def test_workbook_numeric_research_fields_and_atlas_sheet(self):
        logger.save_ritual_page([{"category": "Omen", "name": "Omen of Whittling", "quantity": 3,
                                 "deferred": True, "tribute": 4000}], tribute_available=1234, rerolls_remaining=2)
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            ns = {"s": workbook_export.NS}
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            self.assertEqual([row.get("name") for row in workbook.findall("s:sheets/s:sheet", ns)],
                             ["Export", "Atlas Character Settings"])
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            rows = sheet.findall("s:sheetData/s:row", ns)
            headers = {cell.get("r").rstrip("1"): "".join(cell.itertext()) for cell in rows[0]}
            found = {}
            for row in rows[1:]:
                for cell in row:
                    key = headers[cell.get("r").rstrip("0123456789")]
                    if key in ("New Find Quantity", "Ritual Tribute Available", "Ritual Rerolls Remaining"):
                        found[key] = (cell.get("t"), cell.findtext("s:v", namespaces=ns))
            self.assertEqual(found, {"New Find Quantity": (None, "0"),
                                     "Ritual Tribute Available": (None, "1234"),
                                     "Ritual Rerolls Remaining": (None, "2")})
        self.assertTrue(workbook_export._numeric_column("Visible Seed Sockets"))

    def test_concurrent_commits_have_unique_numbers(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            numbers = list(pool.map(lambda _: logger.record_commit("Map settings"), range(24)))
        self.assertEqual(sorted(numbers), list(range(1, 25)))
        with logger._connect() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_finished_map_rejects_stale_context_and_chain(self):
        context = logger.scan_context()
        logger.finish_map(12, 3, 1, 2)
        with self.assertRaisesRegex(ValueError, "map ended"):
            logger.validate_scan_context(context)
        with self.assertRaisesRegex(ValueError, "next map"):
            logger.commit_chain_runes(["Sun", "Moon"])
        logger.start_map()
        self.assertEqual(logger.commit_chain_runes(["Sun", "Moon"])["map_id"], "M0002")

    def test_map_transition_carries_tablets_and_clears_waystone_fields(self):
        logger.save_settings({"tier": 16, "waystone": 87, "map_mods": 4,
                              "waystone_name": "Storm Peak", "waystone_mods": ["Example modifier"],
                              "biome": "Forest", "irradiated": True, "deli": True, "wisp": True})
        logger.finish_map(0, 0, 0, 0)
        state = logger.start_map()
        self.assertEqual(state["settings"]["waystone"], 0)
        self.assertEqual(state["settings"]["waystone_name"], "")
        self.assertEqual(state["settings"]["waystone_mods"], [])
        self.assertEqual(state["settings"]["biome"], "Forest")
        self.assertTrue(state["settings"]["irradiated"])
        self.assertTrue(state["settings"]["deli"])
        self.assertTrue(state["settings"]["wisp"])

    def export_rows(self, data):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows[1:]))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def save_map_flag_records(self):
        for kind in ("Map settings", "Tablet config", "Atlas Master", "Master perks"):
            logger.record_commit(kind)
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        logger.commit_chain("Rage", "Time")
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 5}])
        logger.save_ritual_page([{"category": "Item", "name": "Example reward", "quantity": 1}])

    def test_map_flags_default_validate_and_do_not_change_area_level(self):
        state = logger.get_state()
        self.assertIs(state["settings"]["deli"], False)
        self.assertIs(state["settings"]["wisp"], False)
        area = state["area_level"]
        state = logger.save_settings({"deli": 1, "wisp": 1})
        self.assertIs(state["settings"]["deli"], True)
        self.assertIs(state["settings"]["wisp"], True)
        self.assertEqual(state["area_level"], area)
        state = logger.save_settings({"deli": 0})
        self.assertIs(state["settings"]["deli"], False)
        self.assertIs(state["settings"]["wisp"], True)
        self.assertEqual(state["area_level"], area)

    def test_map_flags_snapshot_export_and_preserve_previous_maps(self):
        logger.save_settings({"deli": True, "wisp": True})
        self.save_map_flag_records()
        logger.finish_map(12, 3, 1, 2)
        tables = ("maps", "new_export", "currency_snapshots", "ritual_pages", "commits")
        with logger._connect() as db:
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} WHERE map_id='M0001'")]
                      for table in tables}
            for table in ("maps", "currency_snapshots", "ritual_pages", "commits"):
                for snapshot, in db.execute(f"SELECT snapshot_json FROM {table} WHERE map_id='M0001'"):
                    self.assertEqual((json.loads(snapshot)["deli"], json.loads(snapshot)["wisp"]), ("Yes", "Yes"))
        logger.start_map()
        logger.save_settings({"deli": False, "wisp": True})
        self.save_map_flag_records()
        with logger._connect() as db:
            after = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} WHERE map_id='M0001'")]
                     for table in tables}
        self.assertEqual(after, before)
        for exporter in (logger.export_csv, logger.export_maps_csv, logger.export_ritual_csv,
                         logger.export_record_history_csv, logger.export_all_csv):
            with self.subTest(exporter=exporter.__name__):
                rows = self.export_rows(exporter())
                self.assertTrue(rows)
                for row in rows:
                    self.assertEqual((row["Deli"], row["Wisp"]),
                                     ("Yes" if row["Map ID"] == "M0001" else "No", "Yes"))
        for row in self.export_rows(logger.export_currency_csv()):
            expected = ("Yes" if row["Map ID"] == "M0001" else "No", "Yes")
            self.assertEqual((row["Start Deli"], row["Start Wisp"]), expected)
            self.assertEqual((row["End Deli"], row["End Wisp"]), expected)
        export = list(csv.reader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertEqual(export[0].index("Scan Commit #"), 90)
        self.assertEqual([export[0].index(name) for name in ("Deli", "Wisp")], [129, 130])
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            ns = {"s": workbook_export.NS}
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            rows = sheet.findall("s:sheetData/s:row", ns)
            headers = {cell.get("r").rstrip("1"): "".join(cell.itertext()) for cell in rows[0]}
            fields = [{headers[cell.get("r").rstrip("0123456789")]: "".join(cell.itertext())
                       for cell in row} for row in rows[1:]]
            self.assertTrue(fields)
            for row in fields:
                self.assertEqual((row["Deli"], row["Wisp"]),
                                 ("Yes" if row["Map ID"] == "M0001" else "No", "Yes"))

    def test_map_flag_upgrade_preserves_legacy_records_and_exports_blank(self):
        logger.save_settings({"deli": True, "wisp": True})
        self.save_map_flag_records()
        tables = ("maps", "new_export", "legacy_export", "currency_snapshots", "ritual_pages", "commits")
        with logger._connect() as db:
            config = logger._meta(db, "settings")
            config.pop("wisp")
            logger._set_meta(db, "settings", config)
            for table in ("maps", "currency_snapshots", "ritual_pages", "commits"):
                for row in list(db.execute(f"SELECT rowid,snapshot_json FROM {table}")):
                    snapshot = json.loads(row[1])
                    snapshot.pop("deli", None)
                    snapshot.pop("wisp", None)
                    db.execute(f"UPDATE {table} SET snapshot_json=? WHERE rowid=?", (logger._dump(snapshot), row[0]))
            for row in list(db.execute("SELECT position,row_json FROM new_export")):
                db.execute("UPDATE new_export SET row_json=? WHERE position=?",
                           (logger._dump(json.loads(row[1])[:-2]), row[0]))
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables}
        logger._READY = False
        logger.initialize()
        state = logger.get_state()
        self.assertIs(state["settings"]["deli"], True)
        self.assertIs(state["settings"]["wisp"], False)
        with logger._connect() as db:
            after = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables}
        self.assertEqual(after, before)
        for exporter in (logger.export_csv, logger.export_maps_csv, logger.export_ritual_csv,
                         logger.export_record_history_csv, logger.export_all_csv):
            with self.subTest(exporter=exporter.__name__):
                for row in self.export_rows(exporter()):
                    self.assertEqual((row["Deli"], row["Wisp"]), ("", ""))
        for row in self.export_rows(logger.export_currency_csv()):
            self.assertEqual([row[name] for name in ("Start Deli", "Start Wisp", "End Deli", "End Wisp")], [""] * 4)
        logger.record_commit("Map settings")
        history = self.export_rows(logger.export_record_history_csv())
        self.assertEqual((history[-1]["Deli"], history[-1]["Wisp"]), ("Yes", "No"))

    def test_new_map_flag_columns_preserve_existing_export_positions(self):
        logger.add_item_name("Example reward")
        positions = {
            logger.export_currency_csv: {"End Tier": 117},
            logger.export_record_history_csv: {"Tablet Slot Capacity": 149, "Jado Configured Perk 1": 150,
                                               "Hilda Configured Perk 4": 161, "Deli": 164, "Wisp": 165},
            logger.export_all_csv: {"Type": 127, "Tablet Slot Capacity": 156, "Jado Configured Perk 1": 157,
                                    "Hilda Configured Perk 4": 168, "Item: Example reward": 169,
                                    "Deli": 172, "Wisp": 173},
        }
        for exporter, fields in positions.items():
            with self.subTest(exporter=exporter.__name__):
                headers = next(csv.reader(io.StringIO(exporter().decode("utf-8-sig"))))
                for name, index in fields.items():
                    self.assertEqual(headers.index(name), index)
                if exporter == logger.export_currency_csv:
                    self.assertEqual(headers[-6:-2], ["Start Deli", "Start Wisp", "End Deli", "End Wisp"])
                    self.assertEqual(headers[-2:], ["Start Baseline", "Session Found Quantity"])

    def test_reset_preserves_settings_references_and_backup(self):
        logger.save_settings({"tier": 16, "waystone": 87})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        old_context = logger.scan_context()
        backup = Path(self.tmp.name) / "backup.sqlite3"
        backup.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM commits").fetchone()[0], 1)
        before = logger.currency_names()
        state = logger.clear_export_and_reset_ids()
        self.assertEqual(state["settings"]["waystone"], 87)
        self.assertEqual(state["scan_commit_count"], 0)
        self.assertEqual(logger.currency_names(), before)
        with self.assertRaisesRegex(ValueError, "previous session"):
            logger.validate_scan_context(old_context)

    def pack(self, mutate=None):
        raw = reference_pack.export_pack()
        if mutate:
            with ZipFile(io.BytesIO(raw)) as source:
                contents = {name: source.read(name) for name in source.namelist()}
            manifest = json.loads(contents["manifest.json"])
            mutate(manifest, contents)
            contents["manifest.json"] = json.dumps(manifest).encode()
            output = io.BytesIO()
            with ZipFile(output, "w") as destination:
                for name, value in contents.items():
                    destination.writestr(name, value)
            raw = output.getvalue()
        path = Path(self.tmp.name) / "references.zip"
        path.write_bytes(raw)
        return path

    def add_scan(self):
        image = io.BytesIO()
        Image.new("RGB", (40, 40), (30, 80, 120)).save(image, format="PNG")
        raw = image.getvalue()
        sha = hashlib.sha256(raw).hexdigest()
        images = store.DATA_DIR / "images"
        images.mkdir(exist_ok=True)
        destination = images / (sha + ".png")
        destination.write_bytes(raw)
        with logger._connect() as db:
            db.execute("INSERT INTO scans VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (1, logger._now(), "reference.png", destination.name, sha, 3, "P1", "Sun", "", '["Example reward"]', "local"))
        return destination, raw

    def test_reference_pack_excludes_gameplay_and_settings(self):
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        with ZipFile(self.pack()) as archive:
            data = json.loads(archive.read("manifest.json"))["data"]
        self.assertFalse(set(data) & {"settings", "maps", "commits", "currency_snapshots", "ritual_pages"})
        self.assertTrue(all(value == 0 for value in reference_pack.import_pack(self.pack()).values()))

    def test_reference_pack_repairs_existing_damaged_image(self):
        destination, raw = self.add_scan()
        path = self.pack()
        with logger._connect() as db:
            db.execute("DELETE FROM scans")
        destination.write_bytes(b"damaged")
        self.assertEqual(reference_pack.import_pack(path)["scans"], 1)
        self.assertEqual(destination.read_bytes(), raw)
        self.assertTrue(reference_pack.export_pack())

    def test_reference_pack_repairs_damage_even_when_record_already_exists(self):
        destination, raw = self.add_scan()
        path = self.pack()
        destination.write_bytes(b"damaged")
        self.assertEqual(reference_pack.import_pack(path)["scans"], 0)
        self.assertEqual(destination.read_bytes(), raw)

    def test_implicit_map_start_clears_previous_waystone_settings(self):
        logger.save_settings({"tier": 16, "waystone": 87, "map_mods": 4})
        logger.finish_map(0, 0, 0, 0)
        saved = logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        self.assertEqual(saved["map_id"], "M0002")
        self.assertEqual(logger.get_state()["settings"]["waystone"], 0)
        with logger._connect() as db:
            snapshot = json.loads(db.execute("SELECT snapshot_json FROM commits WHERE kind='Remnant'").fetchone()[0])
        self.assertEqual(snapshot["waystone"], 0)

    def test_tablet_sequence_uses_four_slots_and_clear_restarts_it(self):
        with logger._connect() as db:
            affix = db.execute("SELECT name FROM affixes WHERE name LIKE '%Pack Size%' LIMIT 1").fetchone()[0]
        for number in range(1, 5):
            self.assertEqual(logger.save_scanned_tablet([{"affix": affix, "value": 10 * number, "unit": "%"}],
                                                      [f"{10 * number}% increased Pack Size"]), number)
        state = logger.get_state()["settings"]
        self.assertEqual([state["tablet_affixes"][i * 4]["value"] for i in range(4)], [10, 20, 30, 40])
        with self.assertRaisesRegex(ValueError, "Four tablets"):
            logger.save_scanned_tablet([{"affix": affix, "value": 50, "unit": "%"}], ["50% increased Pack Size"])
        logger.clear_tablets()
        self.assertEqual(logger.tablet_next_slot(), 1)

    def test_two_expeditions_keep_separate_chains_and_counts(self):
        first = logger.commit_chain_runes(["Sun", "Moon"])
        logger.save_counts(100, 20, 5, 2)
        logger.start_next_chain()
        second = logger.commit_chain_runes(["Sky", "Tide"])
        logger.save_detonated(3)
        self.assertEqual(first["steps"], [1, 2])
        self.assertEqual(second["steps"], [1, 2])
        self.assertNotEqual(first["expedition_id"], second["expedition_id"])
        with logger._connect() as db:
            self.assertEqual([row[0] for row in db.execute("SELECT detonated FROM expeditions WHERE map_id='M0001' ORDER BY expedition_id")], [2, 3])

    def test_current_remnant_id_tracks_pending_saved_and_active_map(self):
        self.assertEqual(logger.get_state()["current_remnant_id"], "")
        pending = logger.assign_ocr_id("opened")
        self.assertEqual(logger.get_state()["current_remnant_id"], pending["remnant_id"])
        saved = logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        self.assertEqual(saved["remnant_id"], pending["remnant_id"])
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["current_remnant_id"], saved["remnant_id"])
        logger.finish_map(0, 0, 0, 0)
        future = logger.assign_ocr_id("opened")
        self.assertEqual(future["map_id"], "M0002")
        state = logger.get_state()
        self.assertEqual(state["current_map_id"], "M0001")
        self.assertEqual(state["current_remnant_id"], saved["remnant_id"])
        logger.discard_ocr_id()
        state = logger.start_map()
        self.assertEqual(state["current_map_id"], "M0002")
        self.assertEqual(state["current_remnant_id"], "")

    def test_current_remnant_id_filters_expedition_beyond_recent_list(self):
        first = logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        logger.start_next_chain()
        for _ in range(11):
            second = logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        self.assertEqual(logger.get_state()["current_remnant_id"], second["remnant_id"])
        self.assertNotIn(first["remnant_id"], [row["remnant_id"] for row in logger.get_state()["recent"]])
        with logger._connect() as db:
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
                      for table in ("maps", "expeditions", "new_export", "commits")}
        logger.save_settings({"expedition": 1})
        self.assertEqual(logger.get_state()["current_remnant_id"], first["remnant_id"])
        pending = logger.assign_ocr_id("opened")
        self.assertEqual(logger.get_state()["current_remnant_id"], pending["remnant_id"])
        logger.discard_ocr_id()
        self.assertEqual(logger.get_state()["current_remnant_id"], first["remnant_id"])
        logger.save_settings({"expedition": 2})
        self.assertEqual(logger.get_state()["current_remnant_id"], second["remnant_id"])
        with logger._connect() as db:
            after = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in before}
        self.assertEqual(after, before)

    def test_current_remnant_id_falls_back_to_imported_records(self):
        saved = logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        with logger._connect() as db:
            db.execute("INSERT INTO legacy_export SELECT * FROM new_export")
            db.execute("DELETE FROM new_export")
        self.assertEqual(logger.get_state()["current_remnant_id"], saved["remnant_id"])
        newest = logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        self.assertEqual(logger.get_state()["current_remnant_id"], newest["remnant_id"])

    def test_reference_pack_rejects_invalid_reward_values(self):
        self.add_scan()
        path = self.pack(lambda manifest, contents: manifest["data"]["scans"][0].update(rewards_json="[42]"))
        with logger._connect() as db:
            db.execute("DELETE FROM scans")
        with self.assertRaisesRegex(ValueError, "invalid name"):
            reference_pack.import_pack(path)
        self.assertEqual(store.list_scans(), [])

    def test_reference_pack_rejects_mislabeled_screenshot_path(self):
        self.add_scan()
        def mutate(manifest, contents):
            row = manifest["data"]["scans"][0]
            original = row["file"]
            row["file"] = "screens/" + "0" * 64 + ".png"
            contents[row["file"]] = contents.pop(original)
        with self.assertRaisesRegex(ValueError, "filename"):
            reference_pack.import_pack(self.pack(mutate))

    def test_reference_export_checks_import_entry_limit(self):
        image = io.BytesIO()
        Image.new("RGB", (20, 20)).save(image, format="PNG")
        with logger._connect() as db:
            db.executemany("INSERT INTO currency_icons(name,image_png,recorded_at) VALUES(?,?,?)",
                           [("Chaos Orb", image.getvalue(), logger._now())] * 2000)
        with self.assertRaisesRegex(ValueError, "too many images"):
            reference_pack.export_pack()


if __name__ == "__main__":
    unittest.main()
