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

    def test_workbook_numeric_research_fields_and_single_sheet(self):
        logger.save_ritual_page([{"category": "Omen", "name": "Omen of Whittling", "quantity": 3,
                                 "deferred": True, "tribute": 4000}], tribute_available=1234, rerolls_remaining=2)
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            ns = {"s": workbook_export.NS}
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            self.assertEqual([row.get("name") for row in workbook.findall("s:sheets/s:sheet", ns)], ["Export"])
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
                              "biome": "Forest", "irradiated": True})
        logger.finish_map(0, 0, 0, 0)
        state = logger.start_map()
        self.assertEqual(state["settings"]["waystone"], 0)
        self.assertEqual(state["settings"]["waystone_name"], "")
        self.assertEqual(state["settings"]["waystone_mods"], [])
        self.assertEqual(state["settings"]["biome"], "Forest")
        self.assertTrue(state["settings"]["irradiated"])

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
