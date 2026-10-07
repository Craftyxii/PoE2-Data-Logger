import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store


def fixture_catalog():
    return {"version": "test-atlas-1", "nodes": {
        "root": {"name": "Atlas", "kind": "root", "activity": "Atlas", "allocatable": False},
        "rarity": {"name": "Item Rarity", "kind": "small", "activity": "Atlas", "allocatable": True,
                   "effects": ["5% increased Rarity of Items found in your Maps"], "stats": {"rarity": 5}},
        "choice": {"name": "Biome Choice", "kind": "notable", "activity": "Atlas", "allocatable": True,
                   "effects": ["Choose a biome effect"], "stats": {}, "choices": [
                       {"id": "swamp", "name": "Swamp", "effects": ["10% rarity in Swamp Maps"],
                        "stats": {"swamp_rarity": 10}},
                       {"id": "forest", "name": "Forest", "effects": ["20% rarity in Forest Maps"],
                        "stats": {"forest_rarity": 20}}]},
        "ritual": {"name": "Ritual Rewards", "kind": "small", "activity": "Ritual", "allocatable": True,
                   "effects": ["5% more Tribute"], "stats": {"tribute": 5}},
    }}


class AtlasStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-atlas-store-")
        self.previous_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        self.catalog = fixture_catalog()
        self.catalog_patch = patch.object(logger, "_atlas_catalog_data", side_effect=lambda: self.catalog)
        self.identity_patch = patch.object(logger, "_atlas_catalog_identity", side_effect=self.identity)
        self.catalog_patch.start()
        self.identity_patch.start()
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous_data_dir
        logger._READY = False
        self.identity_patch.stop()
        self.catalog_patch.stop()
        self.tmp.cleanup()

    def identity(self):
        encoded = json.dumps(self.catalog, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest(), encoded

    def settings(self, allocated=(), choices=None, rarity=None):
        return {"catalog_version": self.catalog["version"], "allocated": list(allocated),
                "choices": choices or {}, "gear_item_rarity": rarity}

    def setup_id(self):
        return logger._atlas_snapshot(logger.get_state()["settings"])["atlas_setup_id"]

    def rows(self, data):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def map_snapshot(self, map_id="M0001"):
        with logger._connect() as db:
            return json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()[0])

    def test_defaults_preserve_unknown_rarity_and_zero_is_distinct(self):
        config = logger.get_state()["settings"]["atlas_settings"]
        self.assertIsNone(config["gear_item_rarity"])
        self.assertEqual(config["allocated"], [])
        unknown_id = self.setup_id()
        logger.save_atlas_settings(self.settings(rarity=0))
        self.assertNotEqual(self.setup_id(), unknown_id)
        self.assertEqual(self.map_snapshot()["gear_item_rarity"], 0)
        self.assertEqual({row["Gear Item Rarity %"] for row in self.rows(logger.export_atlas_csv())}, {"0"})

    def test_settings_validation_and_unresolved_choices(self):
        for data in (self.settings(["root"]), self.settings(["missing"]), self.settings(["rarity", "rarity"]),
                     self.settings(choices={"choice": "invalid"}), self.settings(choices={"rarity": "swamp"}),
                     self.settings(rarity=True), self.settings(rarity=float("nan")),
                     self.settings(rarity=-1), {**self.settings(), "catalog_version": "old"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                logger.save_atlas_settings(data)
        # Autofill need not invent an effect for an unanswered dropdown.
        logger.save_atlas_settings(self.settings(["choice"]))
        row = next(row for row in self.rows(logger.export_atlas_csv()) if row["Node ID"] == "choice")
        self.assertEqual(row["Allocated"], "Yes")
        self.assertEqual(row["Selected Option"], "")
        self.assertEqual(row["Applied Effects"], "Choose a biome effect")

    def test_same_setup_has_stable_id_independent_of_input_order(self):
        logger.save_atlas_settings(self.settings(["ritual", "rarity"], {"choice": "swamp"}, 12.5))
        original = self.setup_id()
        logger.save_atlas_settings(self.settings(["rarity", "ritual"], {"choice": "swamp"}, 12.5))
        self.assertEqual(self.setup_id(), original)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM atlas_catalogs").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM atlas_setups").fetchone()[0], 2)

    def test_all_scan_types_keep_current_atlas_after_next_setup_is_saved(self):
        logger.save_atlas_settings(self.settings(["rarity", "choice"], {"choice": "swamp"}, 100))
        original = self.map_snapshot()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        state = logger.save_atlas_settings(self.settings(["ritual"], rarity=0))
        next_setup = self.setup_id()
        self.assertEqual(state["atlas_settings_target_map_id"], "M0002")
        self.assertEqual(self.map_snapshot(), original)
        logger.save_settings({"tier": 16, "waystone": 80})
        logger.record_commit("Map settings")
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        logger.commit_chain("Rage", "Time")
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 4}])
        logger.save_ritual_page([{"category": "Item", "name": "Example reward", "quantity": 1}])
        logger.finish_map(3, 2, 1, 1)
        with logger._connect() as db:
            for snapshot, in db.execute("SELECT snapshot_json FROM commits WHERE map_id='M0001'"):
                saved = json.loads(snapshot)
                self.assertEqual(saved["atlas_setup_id"], original["atlas_setup_id"])
                self.assertEqual(saved["gear_item_rarity"], 100)
            queued = db.execute("SELECT snapshot_json FROM commits WHERE kind='Atlas settings' "
                                "AND map_id='M0002'").fetchone()
            self.assertEqual(json.loads(queued[0])["atlas_setup_id"], next_setup)
        logger.start_map()
        self.assertEqual(self.map_snapshot("M0002")["atlas_setup_id"], next_setup)
        for exporter in (logger.export_csv, logger.export_maps_csv, logger.export_record_history_csv,
                         logger.export_all_csv, logger.export_ritual_csv):
            for row in self.rows(exporter()):
                if row["Map ID"] == "M0001":
                    self.assertEqual(row["Atlas Setup ID"], original["atlas_setup_id"])
                    self.assertEqual(row["Gear Item Rarity %"], "100")
        for row in self.rows(logger.export_currency_csv()):
            self.assertEqual(row["Start Atlas Setup ID"], original["atlas_setup_id"])
            self.assertEqual(row["End Atlas Setup ID"], original["atlas_setup_id"])

    def test_start_inventory_before_map_creation_freezes_atlas(self):
        logger.clear_export_and_reset_ids()
        logger.save_atlas_settings(self.settings(["rarity"], rarity=100))
        original = self.setup_id()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        state = logger.save_atlas_settings(self.settings(["ritual"], rarity=50))
        self.assertEqual(state["atlas_settings_target_map_id"], "M0002")
        logger.start_map()
        self.assertEqual(self.map_snapshot()["atlas_setup_id"], original)
        logger.commit_chain("Rage")
        latest = self.rows(logger.export_record_history_csv())[-1]
        self.assertEqual(latest["Atlas Setup ID"], original)

    def test_catalog_changes_keep_frozen_effects_and_old_live_profile(self):
        logger.save_atlas_settings(self.settings(["rarity"], rarity=25))
        original = self.setup_id()
        logger.commit_chain("Rage")
        self.catalog = copy.deepcopy(self.catalog)
        self.catalog["version"] = "test-atlas-2"
        self.catalog["nodes"]["rarity"]["effects"] = ["15% rarity, revised patch"]
        self.catalog["nodes"]["rarity"]["stats"] = {"rarity": 15}
        logger._READY = False
        logger.initialize()
        logger.save_settings({"waystone": 55})
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 5}])
        logger.save_atlas_settings(self.settings(["rarity"], rarity=25))
        revised = self.setup_id()
        self.assertNotEqual(original, revised)
        rows = self.rows(logger.export_atlas_csv())
        old = next(row for row in rows if row["Atlas Setup ID"] == original and row["Node ID"] == "rarity")
        new = next(row for row in rows if row["Atlas Setup ID"] == revised and row["Node ID"] == "rarity")
        self.assertIn("5% increased", old["Effects"])
        self.assertIn("15% rarity", new["Effects"])
        self.assertEqual(old["Atlas Data Version"], "test-atlas-1")
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM atlas_catalogs").fetchone()[0], 2)

    def test_atlas_rows_deduplicate_setups_link_maps_and_preserve_off_choices(self):
        logger.save_atlas_settings(self.settings(["rarity"], {"choice": "swamp"}, 0))
        setup_id = self.setup_id()
        logger.commit_chain("Rage")
        logger.finish_map(0, 0, 0, 0)
        logger.start_map()
        logger.commit_chain("Time")
        rows = self.rows(logger.export_atlas_csv())
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["Atlas Setup ID"] for row in rows}, {setup_id})
        self.assertEqual({row["Map IDs"] for row in rows}, {"M0001, M0002"})
        choice = next(row for row in rows if row["Node ID"] == "choice")
        self.assertEqual(choice["Allocated"], "No")
        self.assertEqual(choice["Points"], "0")
        self.assertEqual(choice["Selected Option ID"], "swamp")
        self.assertIn("Swamp Maps", choice["Effects"])
        self.assertEqual(choice["Applied Effects"], "")
        self.assertEqual(choice["Applied Stats"], "")

    def test_old_snapshots_remain_unknown_and_are_not_rewritten_on_upgrade(self):
        logger.commit_chain("Rage")
        with logger._connect() as db:
            for table in ("maps", "commits"):
                for row in db.execute(f"SELECT rowid AS record_id,snapshot_json FROM {table}").fetchall():
                    snapshot = json.loads(row["snapshot_json"])
                    for key in ("atlas_setup_id", "atlas_catalog_id", "atlas_settings", "gear_item_rarity"):
                        snapshot.pop(key, None)
                    db.execute(f"UPDATE {table} SET snapshot_json=? WHERE rowid=?",
                               (json.dumps(snapshot), row["record_id"]))
            config = logger._meta(db, "settings")
            config.pop("atlas_settings")
            logger._set_meta(db, "settings", config)
            before = [tuple(row) for row in db.execute("SELECT * FROM maps")]
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM maps")], before)
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 5}])
        for exporter in (logger.export_csv, logger.export_maps_csv, logger.export_record_history_csv,
                         logger.export_all_csv):
            for row in self.rows(exporter()):
                self.assertEqual(row["Atlas Setup ID"], "")
                self.assertEqual(row["Gear Item Rarity %"], "")

    def test_reset_preserves_current_settings_but_excludes_unused_historic_setups(self):
        logger.save_atlas_settings(self.settings(["rarity"], rarity=100))
        logger.commit_chain("Rage")
        logger.save_atlas_settings(self.settings(["ritual"], rarity=0))
        current_id = self.setup_id()
        logger.clear_export_and_reset_ids()
        rows = self.rows(logger.export_atlas_csv())
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["Atlas Setup ID"] for row in rows}, {current_id})
        self.assertEqual({row["Map IDs"] for row in rows}, {""})

    def test_csv_folder_exports_both_sheets_from_same_transaction(self):
        logger.save_atlas_settings(self.settings(["rarity"], rarity=0))
        logger.commit_chain("Rage")
        logger.save_export_folder(self.tmp.name)
        captured = {}
        with patch("PoE2_Data_Logger.core.export_files.write_export_files",
                   side_effect=lambda files: captured.update(files)):
            result = logger.save_export_file("csv")
        self.assertEqual({path.name for path in captured}, {"PoE2_Export.csv", "PoE2_Atlas.csv"})
        main_ids = {row["Atlas Setup ID"] for row in self.rows(captured[Path(result["path"])])}
        atlas_ids = {row["Atlas Setup ID"] for row in self.rows(captured[Path(result["atlas_path"])])}
        self.assertEqual(main_ids, atlas_ids)


if __name__ == "__main__":
    unittest.main()
