"""Check saved map activity tags and setup readiness without changing recorded observations."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store


def metadata_catalog():
    """Supply two distinct valid Atlas selections for current and upcoming map checks."""
    return {"version": "map-metadata-test-1", "nodes": {
        "root": {"name": "Atlas", "kind": "root", "activity": "Atlas", "allocatable": False},
        "rarity": {"name": "Item Rarity", "kind": "small", "activity": "Atlas", "allocatable": True,
                   "effects": ["5% increased Rarity of Items found in your Maps"],
                   "stats": {"map_item_drop_rarity_+%": 5}},
        "ritual": {"name": "Ritual Rewards", "kind": "small", "activity": "Ritual", "allocatable": True,
                   "effects": ["5% increased Tribute"], "stats": {"ritual_tribute_+%": 5}},
    }}


class MapActivityMetadataTests(unittest.TestCase):
    """Exercise activity tagging, historical isolation and durable readiness on real SQLite transactions."""

    HISTORY_TABLES = ("commits", "currency_snapshots", "ritual_pages", "legacy_export", "new_export",
                      "atlas_setups", "atlas_catalogs")

    def setUp(self):
        """Create an independent profile and controlled Atlas catalog for every workflow."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-map-activity-")
        self.previous_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        self.catalog = metadata_catalog()
        encoded = json.dumps(self.catalog, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.catalog_patch = patch.object(logger, "_atlas_catalog_data", return_value=self.catalog)
        self.identity_patch = patch.object(logger, "_atlas_catalog_identity",
                                           return_value=(hashlib.sha256(encoded.encode()).hexdigest(), encoded))
        self.catalog_patch.start()
        self.identity_patch.start()
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        """Restore the caller's profile, reference functions and initialization state."""
        self.identity_patch.stop()
        self.catalog_patch.stop()
        store.DATA_DIR = self.previous_data_dir
        logger._READY = False
        self.temporary.cleanup()

    def restart(self):
        """Reopen the profile to require database persistence rather than process-only metadata."""
        logger._READY = False
        logger.initialize()
        return logger.get_state()

    def map_snapshot(self, map_id="M0001"):
        """Decode exactly the saved map context used by exports and current setup readiness."""
        with logger._connect() as db:
            row = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
        self.assertIsNotNone(row)
        return json.loads(row[0])

    def history(self):
        """Capture exact saved observations and Atlas references for non-rewrite assertions."""
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in self.HISTORY_TABLES}

    def atlas_settings(self, allocated, rarity):
        """Build a validated reference setup distinguishable from the next map's setup."""
        return {"catalog_version": self.catalog["version"], "allocated": allocated,
                "choices": {}, "gear_item_rarity": rarity}

    def test_activity_options_round_trip_and_default_does_not_guess_from_logs(self):
        """Keep the exact supported activity names and store deliberate selections independently of scan kind."""
        self.assertEqual(logger.ACTIVITY_TYPES,
                         ("None", "Breach", "Expedition", "Grand Expedition", "Abyss", "Ritual"))
        state = logger.get_state()
        self.assertEqual(state["settings"]["activity_type"], "None")
        logger.start_map()
        logger.save_ritual_page([{"category": "Item", "name": "Reviewed reward", "quantity": 1}])
        self.assertEqual(logger.get_state()["settings"]["activity_type"], "None")
        for activity in logger.ACTIVITY_TYPES:
            with self.subTest(activity=activity):
                state = logger.save_settings({"activity_type": activity})
                self.assertEqual(state["settings"]["activity_type"], activity)
                self.assertEqual(self.map_snapshot()["activity_type"], activity)
                self.assertEqual(self.restart()["settings"]["activity_type"], activity)

    def test_invalid_activity_values_reject_entire_settings_transaction(self):
        """Reject misspelled labels, booleans and non-string input without partially saving companion edits."""
        logger.start_map()
        logger.save_settings({"activity_type": "Breach", "biome": "Forest"})
        expected_settings = logger.get_state()["settings"]
        expected_map = self.map_snapshot()
        expected_history = self.history()
        expected_undo = logger.undo_action()
        for invalid in ("breach", "GrandExpedition", "Expedition ", "Unknown", "", None,
                        True, False, 1, ["Breach"], {"name": "Breach"}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    logger.save_settings({"activity_type": invalid, "biome": "Desert"})
                self.assertEqual(logger.get_state()["settings"], expected_settings)
                self.assertEqual(self.map_snapshot(), expected_map)
                self.assertEqual(self.history(), expected_history)
                self.assertEqual(logger.undo_action(), expected_undo)

    def test_readiness_requires_saved_fields_and_resets_for_the_new_map(self):
        """Require complete reviewed/manual setup evidence rather than a nonzero global value or active activity."""
        self.assertIs(logger.get_state()["current_map_setup_saved"], False)
        state = logger.start_map()
        self.assertIs(state["current_map_setup_saved"], False)
        logger.save_settings({"activity_type": "Grand Expedition", "biome": "Desert"})
        self.assertIs(logger.get_state()["current_map_setup_saved"], False)
        logger.save_settings({"tier": 16, "waystone": 0})
        self.assertIs(logger.get_state()["current_map_setup_saved"], False)
        state = logger.save_settings({"map_mods": 0})
        self.assertIs(state["current_map_setup_saved"], True)
        self.assertIs(self.map_snapshot()["waystone_setup_saved"], True)
        self.assertIs(self.restart()["current_map_setup_saved"], True)
        logger.finish_map(12, 3, 1)
        state = logger.start_map()
        self.assertIs(state["current_map_setup_saved"], False)
        self.assertEqual(state["settings"]["activity_type"], "Grand Expedition")
        self.assertIs(self.map_snapshot("M0001")["waystone_setup_saved"], True)
        self.assertIs(self.map_snapshot("M0002")["waystone_setup_saved"], False)

    def test_complete_manual_setup_readiness_reaches_commits_currency_and_ritual(self):
        """Transport actual saved setup proof into observations; a new unconfigured map must carry False."""
        logger.start_map()
        logger.save_settings({"tier": 16, "waystone": 55, "map_mods": 5})
        logger.record_commit("Map settings")
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        logger.save_ritual_page([{"category": "Item", "name": "Reviewed reward", "quantity": 1}])
        with logger._connect() as db:
            contexts = [json.loads(row[0]) for row in db.execute(
                "SELECT snapshot_json FROM commits WHERE map_id='M0001'")]
            contexts.extend(json.loads(row[0]) for row in db.execute(
                "SELECT snapshot_json FROM currency_snapshots WHERE map_id='M0001'"))
            contexts.extend(json.loads(row[0]) for row in db.execute(
                "SELECT snapshot_json FROM ritual_pages WHERE map_id='M0001'"))
        self.assertEqual(len(contexts), 5)
        for context in contexts:
            with self.subTest(context=context):
                self.assertIs(context["waystone_setup_saved"], True)
                self.assertEqual(set(context["waystone_setup_fields"]), {"tier", "waystone", "map_mods"})
        logger.finish_map(12, 3, 1)
        logger.start_map()
        logger.record_commit("Map settings")
        with logger._connect() as db:
            fresh = json.loads(db.execute("SELECT snapshot_json FROM commits WHERE kind='Map settings' "
                                          "AND map_id='M0002' ORDER BY number DESC LIMIT 1").fetchone()[0])
        self.assertIs(fresh["waystone_setup_saved"], False)
        self.assertEqual(fresh["waystone_setup_fields"], [])

    def test_complete_queued_setup_proof_reaches_commit_before_new_map(self):
        """Keep prepared next-map readiness separate from the completed map and retain it through New Map."""
        logger.start_map()
        logger.save_settings({"tier": 15, "waystone": 35, "map_mods": 4})
        logger.finish_map(20, 3, 1)
        completed = self.map_snapshot()
        logger.save_settings({"tier": 16, "waystone": 80, "map_mods": 6})
        logger.record_commit("Map settings")
        with logger._connect() as db:
            queued = json.loads(db.execute("SELECT snapshot_json FROM commits WHERE kind='Map settings' "
                                           "AND map_id='M0002' ORDER BY number DESC LIMIT 1").fetchone()[0])
        self.assertIs(queued["waystone_setup_saved"], True)
        self.assertEqual(set(queued["waystone_setup_fields"]), {"tier", "waystone", "map_mods"})
        self.assertEqual(self.map_snapshot(), completed)
        state = logger.start_map()
        self.assertIs(state["current_map_setup_saved"], True)
        self.assertIs(self.map_snapshot("M0002")["waystone_setup_saved"], True)

    def test_prepared_waystone_and_activity_apply_to_upcoming_map_only(self):
        """Keep the completed map unchanged when activity and a complete next-map setup are prepared."""
        logger.start_map()
        logger.save_settings({"activity_type": "Breach", "tier": 15, "waystone": 35, "map_mods": 4})
        logger.finish_map(20, 3, 1)
        completed = self.map_snapshot()
        logger.save_settings({"activity_type": "Expedition", "tier": 16, "waystone": 80, "map_mods": 6})
        self.assertEqual(self.map_snapshot(), completed)
        state = logger.start_map()
        self.assertEqual(state["settings"]["activity_type"], "Expedition")
        self.assertIs(state["current_map_setup_saved"], True)
        self.assertEqual(self.map_snapshot("M0002")["activity_type"], "Expedition")
        self.assertEqual(self.map_snapshot("M0001")["activity_type"], "Breach")

    def test_late_activity_tag_preserves_observed_stats_history_and_current_atlas_binding(self):
        """Tag a used map without rewriting captured numbers, saved approvals or its historical Atlas selection."""
        logger.start_map()
        logger.save_atlas_settings(self.atlas_settings(["rarity"], 100))
        captured = {"tier": 16, "waystone": 75, "map_mods": 5, "waystone_name": "Reviewed map",
                    "waystone_mods": ["Example captured modifier"], "item_rarity": 41,
                    "monster_rarity": 27, "pack_size": 18, "effectiveness": 82,
                    "biome": "Desert", "city_type": "Faridun", "activity_type": "Breach"}
        logger.save_settings(captured)
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        logger.save_ritual_page([{"category": "Item", "name": "Reviewed reward", "quantity": 1}])
        original = self.map_snapshot()
        state = logger.save_atlas_settings(self.atlas_settings(["ritual"], 50))
        self.assertEqual(state["atlas_settings_target_map_id"], "M0002")
        history = self.history()
        state = logger.save_settings({"activity_type": "Grand Expedition"})
        updated = self.map_snapshot()
        self.assertEqual(state["settings"]["activity_type"], "Grand Expedition")
        self.assertEqual(updated["activity_type"], "Grand Expedition")
        allowed = {"activity_type", "atlas_effect_snapshot"}
        self.assertEqual({key: value for key, value in updated.items() if key not in allowed},
                         {key: value for key, value in original.items() if key not in allowed})
        self.assertEqual(self.history(), history)
        self.assertEqual(self.restart()["settings"]["activity_type"], "Grand Expedition")
        self.assertEqual(self.map_snapshot(), updated)
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 4}])
        with logger._connect() as db:
            latest = json.loads(db.execute("SELECT snapshot_json FROM commits WHERE kind='Currency' "
                                           "AND map_id='M0001' ORDER BY number DESC LIMIT 1").fetchone()[0])
        self.assertEqual(latest["activity_type"], "Grand Expedition")
        for key in ("atlas_setup_id", "atlas_catalog_id", "atlas_settings", "gear_item_rarity"):
            self.assertEqual(latest[key], original[key])

    def test_activity_changes_on_second_map_do_not_retag_first_map(self):
        """Preserve independent activity copies even though the selected activity carries into New Map."""
        logger.start_map()
        logger.save_settings({"activity_type": "Abyss"})
        logger.finish_map(1, 1, 1)
        first = self.map_snapshot()
        logger.start_map()
        self.assertEqual(self.map_snapshot("M0002")["activity_type"], "Abyss")
        logger.save_settings({"activity_type": "Ritual"})
        self.assertEqual(self.map_snapshot("M0001"), first)
        self.assertEqual(self.map_snapshot("M0002")["activity_type"], "Ritual")
        self.restart()
        self.assertEqual(self.map_snapshot("M0001"), first)

    def test_activity_undo_survives_restart_and_restores_current_map_tag(self):
        """Use the universal durable undo history to reverse a late activity edit together with its map metadata."""
        logger.start_map()
        logger.save_settings({"activity_type": "Expedition"})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        original_settings = logger.get_state()["settings"]
        original_map = self.map_snapshot()
        original_history = self.history()
        logger.save_settings({"activity_type": "Grand Expedition"})
        self.restart()
        result = logger.undo_last_action()
        self.assertEqual(result["action"], "Settings")
        self.assertEqual(result["state"]["settings"], original_settings)
        self.assertEqual(self.map_snapshot(), original_map)
        self.assertEqual(self.history(), original_history)
        self.assertEqual(self.restart()["settings"]["activity_type"], "Expedition")

    def test_undo_new_map_restores_previous_activity_and_readiness(self):
        """Reopen the preceding map with its own complete setup instead of the fresh map's readiness."""
        logger.start_map()
        logger.save_settings({"activity_type": "Breach", "tier": 16, "waystone": 55, "map_mods": 5})
        logger.finish_map(12, 3, 1)
        first = self.map_snapshot()
        logger.start_map()
        self.assertIs(logger.get_state()["current_map_setup_saved"], False)
        self.restart()
        result = logger.undo_last_action()
        self.assertEqual(result["action"], "New map")
        self.assertEqual(result["state"]["current_map_id"], "M0001")
        self.assertEqual(result["state"]["settings"]["activity_type"], "Breach")
        self.assertIs(result["state"]["current_map_setup_saved"], True)
        self.assertEqual(self.map_snapshot(), first)

    def test_legacy_readiness_and_activity_remain_unknown_in_saved_history(self):
        """Supply a safe current No while retaining absent historical evidence instead of backfilling a fabricated Yes."""
        logger.start_map()
        logger.save_settings({"tier": 16, "waystone": 80, "map_mods": 6, "activity_type": "Breach"})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        with logger._connect() as db:
            for table in ("maps", "commits", "currency_snapshots"):
                for row in db.execute(f"SELECT rowid AS record_id,snapshot_json FROM {table}").fetchall():
                    snapshot = json.loads(row["snapshot_json"])
                    for key in ("activity_type", "waystone_setup_saved", "waystone_setup_fields",
                                "atlas_effect_snapshot"):
                        snapshot.pop(key, None)
                    db.execute(f"UPDATE {table} SET snapshot_json=? WHERE rowid=?",
                               (json.dumps(snapshot), row["record_id"]))
            config = logger._meta(db, "settings")
            config.pop("activity_type", None)
            logger._set_meta(db, "settings", config)
        original_map = self.map_snapshot()
        original_history = self.history()
        state = self.restart()
        self.assertEqual(state["settings"]["activity_type"], "None")
        self.assertIs(state["current_map_setup_saved"], False)
        self.assertEqual(self.map_snapshot(), original_map)
        self.assertEqual(self.history(), original_history)
        logger.save_settings({"activity_type": "Ritual"})
        updated = self.map_snapshot()
        self.assertEqual(updated["activity_type"], "Ritual")
        self.assertNotIn("waystone_setup_saved", updated)
        self.assertIs(logger.get_state()["current_map_setup_saved"], False)
        self.assertEqual(self.history(), original_history)


if __name__ == "__main__":
    unittest.main()
