"""Settings edits change only the map being played; finished maps change only after Undo reopens them."""

import csv
import io
import unittest

from PoE2_Data_Logger.core import logger_store as logger, ocr_sensitivity, ritual_rites_catalog
from tests.test_atlas_biome_sync import AtlasBiomeProfile


MAP_EXPORTS = ("export_maps_csv", "export_map_summary_csv", "export_csv", "export_record_history_csv",
               "export_all_csv", "export_primary_csv", "export_currency_csv", "export_ritual_csv",
               "export_commits_csv")


class SettingsHistoryLockTests(AtlasBiomeProfile):
    """Log a complete Ritual map, then edit every kind of setting on the next one."""

    def setUp(self):
        """Finish M0001 with Atlas, waystone, master, tablet, Rite and logged activity, then start M0002."""
        super().setUp()
        rows = ritual_rites_catalog.affixes()
        self.local = next(row for row in rows if row["kind"] == "local")
        self.proliferation = next(row for row in rows if row["kind"] == "proliferation")
        logger.save_settings({"activity_type": "Ritual"})
        logger.new_ritual_rite("History Rite")
        logger.save_ritual_rite_affixes([self.local["id"], {"id": self.proliferation["id"], "count": 1}])
        logger.save_atlas_settings(self.atlas())
        logger.save_settings({"tier": 16, "waystone": 87, "map_mods": 5, "biome": "Island", "deli": True,
                              "atlas_master": "Jado", "tablets_used": 2})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 12}])
        logger.save_ritual_page([{"category": "Item", "name": "Chaos Orb", "quantity": 2}])
        logger.save_kills(30, 6, 2, unique=1)
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 16}])
        logger.finish_map(30, 6, 2)
        logger.start_map()
        self.history = self.saved_history()

    def saved_history(self):
        """Read M0001's database rows and its rows in every map-level export."""
        with logger._connect() as db:
            tables = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} WHERE map_id='M0001'")]
                      for table in ("maps", "expeditions", "currency_snapshots", "ritual_pages", "commits",
                                    "map_unique_kills", "kill_count_snapshots", "new_export",
                                    "ritual_rite_map_settings", "ritual_rite_completions")}
        exports = {}
        for name in MAP_EXPORTS:
            reader = csv.DictReader(io.StringIO(getattr(logger, name)().decode("utf-8-sig")))
            exports[name] = [row for row in reader if row.get("Map ID") == "M0001"]
        return tables, exports

    def edit_everything(self):
        """Change Atlas, map, waystone, master, tablet, activity, Rite and OCR settings on the current map."""
        logger.save_ritual_rite_affixes([{"id": self.proliferation["id"], "count": 3}])
        logger.save_atlas_settings(self.atlas(allocated=False))
        logger.save_settings({"tier": 15, "waystone": 12, "map_mods": 1, "biome": "Forest", "deli": False,
                              "wisp": True, "atlas_master": "Hilda", "tablets_used": 4,
                              "city_type": "Faridun", "waystone_mods": ["Next map modifier"]})
        logger.save_settings({"activity_type": "None"})
        ocr_sensitivity.save_values({kind: 30 if kind == "ritual" else ocr_sensitivity.DEFAULT
                                     for kind, _label in ocr_sensitivity.SCAN_TYPES})

    def test_next_map_edits_never_change_the_finished_map_or_its_exports(self):
        """Every setting saved on M0002 leaves M0001's rows and export rows identical."""
        tables, exports = self.history
        self.assertTrue(all(exports.values()), "every map export should list M0001")
        self.assertTrue(tables["ritual_rite_completions"])
        self.edit_everything()
        logger.save_kills(1, 1, 1)
        logger.finish_map(1, 1, 1)
        logger.start_map()
        self.edit_everything()
        self.assertEqual(self.saved_history(), self.history)
        self.assertEqual(logger.get_state()["current_map_id"], "M0003")

    def test_reset_of_the_next_map_never_changes_the_finished_map(self):
        """Reset map clears only the map being played."""
        logger.save_kills(1, 1, 1)
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        logger.discard_current_map_logs(logger.scan_context())
        self.assertEqual(self.saved_history(), self.history)

    def test_only_undo_reopens_the_finished_map_for_correction(self):
        """Undo of New map unlocks M0001's setup; the correction changes that field and nothing else."""
        self.edit_everything()
        while logger.undo_last_action()["action"] != "New map":
            pass
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        tables, _ = self.history
        self.assertEqual(self.saved_history()[0]["maps"], tables["maps"])
        logger.save_settings({"tier": 15})
        corrected = logger.get_state()
        self.assertEqual(corrected["current_map_id"], "M0001")
        with logger._connect() as db:
            snapshot = logger._load(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])
            before = logger._load(tables["maps"][0][[column[1] for column in db.execute(
                "PRAGMA table_info(maps)")].index("snapshot_json")])
        self.assertEqual((before["tier"], snapshot["tier"]), (16, 15))
        changed = {key for key in set(before) | set(snapshot) if before.get(key) != snapshot.get(key)}
        self.assertLessEqual(changed, {"tier", "area", "map_setup_corrected_fields"})
        for table in ("currency_snapshots", "ritual_pages", "map_unique_kills", "ritual_rite_completions"):
            self.assertEqual(self.saved_history()[0][table], tables[table], table)


if __name__ == "__main__":
    unittest.main()
