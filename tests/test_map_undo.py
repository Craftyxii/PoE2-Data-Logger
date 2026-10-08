"""Storage checks restricting new-map undo and preserving prior recorded activity and counters."""

import json
from pathlib import Path
import tempfile
import unittest

from PoE2_Data_Logger.core import logger_store as logger, store


class MapUndoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-map-undo-")
        self.previous_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous_data_dir
        logger._READY = False
        self.tmp.cleanup()

    def history(self):
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} WHERE map_id='M0001'")]
                    for table in ("maps", "expeditions", "new_export", "currency_snapshots", "commits")}

    def reopen(self):
        logger._READY = False
        logger.initialize()
        return logger.get_state()

    def test_untouched_waystone_new_map_undo_and_reopen_keep_valid_defaults(self):
        logger.finish_map(0, 0, 0, 0)
        before = self.history()
        self.assertEqual(logger.start_map()["current_map_id"], "M0002")
        state = logger.undo_empty_map()
        self.assertEqual(state["current_map_id"], "M0001")
        for key, value in logger.WAYSTONE_DEFAULTS.items():
            self.assertEqual(state["settings"][key], value)
        self.assertEqual(self.history(), before)
        self.assertEqual(self.reopen()["settings"]["waystone_mods"], [])
        self.assertEqual(logger.save_settings({"biome": "Forest"})["settings"]["biome"], "Forest")

    def test_undo_restores_saved_waystone_fields_and_preserves_history(self):
        previous = {"tier": 16, "waystone": 87, "map_mods": 7, "waystone_name": "Storm Peak",
                    "waystone_mods": ["Example modifier"], "item_rarity": 0, "monster_rarity": 25,
                    "pack_size": 0, "effectiveness": None}
        logger.save_settings({**previous, "expedition": 3, "biome": "Forest", "deli": True})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 10}])
        logger.finish_map(12, 3, 1, 2)
        before = self.history()
        logger.start_map()
        logger.save_settings({"tier": 15, "waystone": 42, "waystone_mods": ["Next map modifier"]})
        state = logger.undo_empty_map()
        self.assertEqual(state["current_map_id"], "M0001")
        self.assertEqual(state["settings"]["expedition"], 3)
        self.assertEqual(state["settings"]["biome"], "Forest")
        self.assertTrue(state["settings"]["deli"])
        self.assertEqual({key: state["settings"][key] for key in previous}, previous)
        self.assertEqual(self.history(), before)
        reopened = self.reopen()
        self.assertEqual({key: reopened["settings"][key] for key in previous}, previous)
        self.assertEqual(self.history(), before)

    def test_undo_repairs_nullable_previous_settings_from_older_beta(self):
        logger.save_settings({"tier": 16, "waystone": 87, "map_mods": 4})
        logger.finish_map(0, 0, 0, 0)
        before = self.history()
        logger.start_map()
        with logger._connect() as db:
            previous = logger._meta(db, "previous_waystone_settings")
            previous["waystone_mods"] = None
            previous["waystone_name"] = None
            previous["item_rarity"] = 0
            previous.pop("effectiveness")
            logger._set_meta(db, "previous_waystone_settings", previous)
        settings = logger.undo_empty_map()["settings"]
        self.assertEqual((settings["tier"], settings["waystone"], settings["map_mods"]), (16, 87, 4))
        self.assertEqual(settings["waystone_mods"], [])
        self.assertEqual(settings["waystone_name"], "")
        self.assertEqual(settings["item_rarity"], 0)
        self.assertIsNone(settings["effectiveness"])
        self.assertEqual(self.history(), before)
        self.assertEqual(self.reopen()["settings"]["waystone_mods"], [])

    def test_initialize_repairs_existing_bad_settings_without_rewriting_saved_history(self):
        logger.save_settings({"tier": 16, "waystone": 87, "map_mods": 4, "item_rarity": 37})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 10}])
        before = self.history()
        with logger._connect() as db:
            config = logger._meta(db, "settings")
            config["waystone_mods"] = None
            config["waystone_name"] = None
            config.pop("pack_size")
            logger._set_meta(db, "settings", config)
        settings = self.reopen()["settings"]
        self.assertEqual((settings["tier"], settings["waystone"], settings["map_mods"]), (16, 87, 4))
        self.assertEqual(settings["waystone_mods"], [])
        self.assertEqual(settings["waystone_name"], "")
        self.assertEqual(settings["item_rarity"], 37)
        self.assertIsNone(settings["pack_size"])
        self.assertEqual(self.history(), before)
        with logger._connect() as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0]),
                             settings)
        self.assertEqual(logger.save_settings({"wisp": True})["settings"]["waystone_mods"], [])


if __name__ == "__main__":
    unittest.main()
