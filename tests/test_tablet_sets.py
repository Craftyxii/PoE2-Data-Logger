"""Keep saved tablet libraries independent and attach only the final selected set."""

from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store


RANDOM = "Map has additional random Modifiers"
SECONDS = "Slaying Rare Monsters in Map pauses the Delirium Mirror Timer for seconds"
FLAG = "Abyss Pits in Map are twice as likely to have Rewards"


class TabletSetTests(unittest.TestCase):
    """Exercise migration, real persistence, final snapshots and atomic Undo."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-tablet-sets-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.clear_tablets()
        self.first = logger.get_state()["selected_tablet_set_id"]
        self.percent = next(name for name in logger.get_state()["affixes"] if "Pack Size" in name)

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def config(self, value, capacity=4):
        """Populate every editable pair and retain more raw lines than visible affix slots."""
        pairs = []
        raw = []
        for number in range(4):
            pairs.extend([{"affix": self.percent, "value": value + number},
                          {"affix": RANDOM, "value": number % 3, "unit": "count"},
                          {"affix": SECONDS, "value": 7 + number, "unit": "seconds"},
                          {"affix": FLAG, "value": 1, "unit": "flag"}])
            raw.append([f"{value + number}% increased Pack Size", f"Tablet {number + 1} raw line",
                        "Original line three", "Original line four", "Original line five"])
        return {"tablets_used": capacity, "tablet_affixes": pairs, "tablet_raw_mods": raw}

    def map_snapshot(self):
        with logger._connect() as db:
            return json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])

    def test_legacy_migration_keeps_existing_configuration_sequence_and_snapshots(self):
        """The initial Set 1 adopts the old editor rather than resetting populated tablets."""
        logger.save_settings(self.config(12, 3))
        logger.advance_tablet_slot(1)
        logger.advance_tablet_slot(2)
        logger.start_map()
        before = self.map_snapshot()
        old_settings = logger.get_state()["settings"]
        with logger._connect() as db:
            db.execute("DELETE FROM tablet_sets")
            db.execute("DELETE FROM meta WHERE key='selected_tablet_set_id'")
            old_settings.pop("tablet_set_id")
            old_settings.pop("tablet_set_name")
            logger._set_meta(db, "settings", old_settings)
        logger._READY = False
        logger.initialize()
        state = logger.get_state()
        self.assertEqual([(item["name"], item["next_slot"]) for item in state["tablet_sets"]], [("Set 1", 3)])
        self.assertEqual({key: state["settings"][key] for key in self.config(12, 3)}, self.config(12, 3))
        self.assertEqual(self.map_snapshot(), before)

    def test_sets_keep_all_values_units_raw_text_capacity_and_scan_position_after_restart(self):
        """Selecting or rescanning another set cannot erase the original sixteen saved pairs."""
        logger.save_settings(self.config(10, 2))
        logger.advance_tablet_slot(1)
        logger.advance_tablet_slot(2)
        second = logger.create_tablet_set("Bossing")["selected_tablet_set_id"]
        self.assertNotEqual(second, self.first)
        self.assertEqual(logger.tablet_next_slot(), 1)
        self.assertTrue(all(not pair["affix"] for pair in logger.get_state()["settings"]["tablet_affixes"]))
        logger.save_scanned_tablet([{"affix": self.percent, "value": 45}], ["45% increased Pack Size"])
        logger.save_settings(self.config(30, 4))
        logger._READY = False
        logger.initialize()
        self.assertEqual(logger.get_state()["selected_tablet_set_id"], second)
        self.assertEqual(logger.tablet_next_slot(), 2)
        for selected, value, capacity, next_slot in ((self.first, 10, 2, 3), (second, 30, 4, 2)):
            state = logger.select_tablet_set(selected)
            self.assertEqual(logger.tablet_next_slot(), next_slot)
            self.assertEqual({key: state["settings"][key] for key in self.config(value, capacity)},
                             self.config(value, capacity))

    def test_legacy_random_modifier_fallback_is_not_inherited_from_another_set(self):
        """The original manual +2 count keeps its legacy meaning when returning from a new set."""
        with logger._connect() as db:
            config = logger._meta(db, "settings")
            config.pop("tablet_random_mods_derived", None)
            config["plus_two_tablets"] = 2
            logger._set_meta(db, "settings", config)
            db.execute("DELETE FROM tablet_sets")
            db.execute("DELETE FROM meta WHERE key='selected_tablet_set_id'")
        logger._READY = False
        logger.initialize()
        first = logger.get_state()["selected_tablet_set_id"]
        self.assertEqual(logger._snapshot(logger.get_state()["settings"])["tablet_random_mods"], [2, 2, 0, 0])
        logger.create_tablet_set("Derived values")
        self.assertEqual(logger._snapshot(logger.get_state()["settings"])["tablet_random_mods"], [0, 0, 0, 0])
        logger.select_tablet_set(first)
        self.assertEqual(logger._snapshot(logger.get_state()["settings"])["tablet_random_mods"], [2, 2, 0, 0])

    def test_library_operations_do_not_change_active_map_or_create_map_activity(self):
        """Library audits retain configuration history without assigning an unused set to a map."""
        logger.start_map()
        before = self.map_snapshot()
        logger.save_settings(self.config(10))
        logger.record_commit("Tablet config")
        second = logger.create_tablet_set("Alternative")["selected_tablet_set_id"]
        logger.save_scanned_tablet([{"affix": self.percent, "value": 25}], ["25% increased Pack Size"])
        logger.rename_tablet_set(second, "Alternative renamed")
        logger.select_tablet_set(self.first)
        self.assertEqual(self.map_snapshot(), before)
        with logger._connect() as db:
            self.assertFalse(logger._map_has_activity(db, "M0001"))
            commits = list(db.execute("SELECT map_id,expedition_id,snapshot_json,details_json FROM commits"))
        self.assertEqual(len(commits), 2)
        for commit in commits:
            self.assertEqual((commit[0], commit[1]), ("", ""))
            self.assertEqual(json.loads(commit[3])["scope"], "tablet_library")
            self.assertIn(json.loads(commit[2])["tablet_set_id"], (self.first, second))

    def test_final_map_uses_only_selected_set_and_preserves_prior_history_and_manual_fields(self):
        """Final values and derived counts follow the selection while earlier events stay frozen."""
        logger.save_settings(self.config(10))
        logger.save_settings({"map_mods": 5, "biome": "Forest", "city_type": "Vaal", "deli": True})
        logger.start_map()
        logger.save_counts(1, 2, 3, 9, unique=4)
        before = self.map_snapshot()
        with logger._connect() as db:
            old_commits = [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]
        second = logger.create_tablet_set("Selected at end")["selected_tablet_set_id"]
        logger.save_settings(self.config(40, 2))
        expected = logger._snapshot(logger.get_state()["settings"])
        logger.save_settings({"map_mods": 27, "biome": "Swamp", "city_type": "Faridun"})
        logger.finish_map(100, 20, 5)
        final = self.map_snapshot()
        for key in ("tablet_set_id", "tablet_set_name", "tablet_affixes", "tablet_raw_mods",
                    "tablet_values", "tablets", "tablet_mods", "tablets_used", "tablet_capacity"):
            self.assertEqual(final[key], expected[key])
        self.assertEqual(final["tablet_set_id"], second)
        self.assertEqual(final["total_mods"], before["map_mods"] + expected["tablet_mods"])
        for key in ("map_mods", "biome", "city_type", "deli", "atlas_setup_id"):
            self.assertEqual(final[key], before[key])
        self.assertEqual(logger.get_state()["detonated"], 9)
        self.assertEqual(logger.get_state()["unique_kills"], 4)
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number LIMIT ?",
                                                               (len(old_commits),))], old_commits)
            last = json.loads(db.execute("SELECT snapshot_json FROM commits ORDER BY number DESC LIMIT 1").fetchone()[0])
        self.assertEqual(last["tablet_set_id"], second)
        rows = list(csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig"))))
        self.assertEqual(rows[0]["Tablet Set ID"], second)
        self.assertEqual(rows[0]["Tablet 1 Mod 1 Value"], "40")
        self.assertEqual(rows[0]["Tablet 3 Mod 1 Value"], "")
        logger.select_tablet_set(self.first)
        self.assertEqual(self.map_snapshot(), final)

    def test_repeated_final_counts_do_not_attach_the_upcoming_maps_selected_set(self):
        """Changing the next map's library selection cannot rewrite a completed map's setup."""
        logger.save_settings(self.config(10))
        logger.start_map()
        logger.finish_map(1, 2, 3, unique=4)
        completed = self.map_snapshot()
        second = logger.create_tablet_set("Upcoming map set")["selected_tablet_set_id"]
        logger.save_settings(self.config(40, 2))
        logger.finish_map(10, 20, 30, unique=40)
        self.assertEqual(self.map_snapshot(), completed)
        with logger._connect() as db:
            self.assertEqual(json.loads(db.execute("SELECT kills_json FROM maps").fetchone()[0]), [10, 20, 30])
            final = json.loads(db.execute("SELECT snapshot_json FROM commits ORDER BY number DESC LIMIT 1").fetchone()[0])
            self.assertEqual(final["tablet_set_id"], self.first)
        logger.start_map()
        logger.finish_map(5, 6, 7)
        with logger._connect() as db:
            next_map = json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0002'").fetchone()[0])
        self.assertEqual(next_map["tablet_set_id"], second)

    def test_pending_marker_without_completion_still_attaches_selected_set(self):
        """A next-map marker alone is not evidence that the active map was finalized."""
        logger.start_map()
        logger.mark_next_map(True)
        second = logger.create_tablet_set("Selected before actual finish")["selected_tablet_set_id"]
        logger.save_settings(self.config(30, 2))
        logger.finish_map(1, 2, 3)
        self.assertEqual(self.map_snapshot()["tablet_set_id"], second)

    def test_stale_capture_guards_and_failed_commit_leave_both_sets_unchanged(self):
        """Late OCR and failed audits cannot modify the replacement selection or scan position."""
        captured = logger.scan_context()["_capture_tablet_set_id"]
        logger.create_tablet_set("Second")
        before = logger.get_state()
        for save in (lambda: logger.save_settings(self.config(15), expected_tablet_set_id=captured),
                     lambda: logger.advance_tablet_slot(1, expected_tablet_set_id=captured),
                     lambda: logger.save_scanned_tablet([{"affix": self.percent, "value": 15}],
                         ["15% increased Pack Size"], expected_tablet_set_id=captured)):
            with self.assertRaisesRegex(ValueError, "selected tablet set changed"):
                save()
        self.assertEqual(logger.get_state(), before)
        with patch.object(logger, "_record_commit", side_effect=ValueError("Audit failed")):
            with self.assertRaisesRegex(ValueError, "Audit failed"):
                logger.save_scanned_tablet([{"affix": self.percent, "value": 15}], ["15% increased Pack Size"])
        self.assertEqual(logger.get_state(), before)

    def test_finalizing_an_imported_empty_context_preserves_its_remnant_setup(self):
        """A tablet projection must preserve the setup formerly recovered by Maps CSV."""
        logger.start_map()
        logger.save_settings({"tier": 16, "map_mods": 7, "waystone": 55, "irradiated": True})
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        with logger._connect() as db:
            db.execute("UPDATE maps SET snapshot_json='{}' WHERE map_id='M0001'")
        before = list(csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig"))))[0]
        logger.save_settings(self.config(20, 2))
        logger.finish_map(1, 2, 3)
        after = list(csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig"))))[0]
        for key in ("Tier", "Area Level", "Waystone %", "Map Mods", "Irradiated", "Atlas Master"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after["Tablet 1 Mod 1 Value"], "20")

    def test_undo_and_backup_preserve_library_rows_selection_and_next_scan(self):
        """Undo reverses each library mutation and backups contain every independently saved set."""
        logger.save_settings(self.config(10, 3))
        original = logger.get_state()["settings"]
        second = logger.create_tablet_set("Backup set")["selected_tablet_set_id"]
        logger.save_scanned_tablet([{"affix": self.percent, "value": 25}], ["25% increased Pack Size"])
        logger.clear_tablets()
        logger.undo_last_action()
        self.assertEqual(logger.tablet_next_slot(), 2)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][0]["value"], 25)
        logger.select_tablet_set(self.first)
        logger.undo_last_action()
        self.assertEqual(logger.get_state()["selected_tablet_set_id"], second)
        logger.select_tablet_set(self.first)
        logger.rename_tablet_set(second, "Renamed")
        logger.undo_last_action()
        self.assertEqual(logger.get_state()["tablet_sets"][1]["name"], "Backup set")
        logger.clear_export_and_reset_ids()
        self.assertEqual(logger.get_state()["settings"], original)
        backup = Path(self.temporary.name) / "backup.sqlite3"
        backup.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(backup)) as db:
            rows = list(db.execute("SELECT set_id,name,next_slot FROM tablet_sets ORDER BY rowid"))
        self.assertEqual(rows, [(self.first, "Set 1", 1), (second, "Backup set", 2)])

    def test_names_and_unknown_selection_fail_without_losing_saved_sets(self):
        """Names stay unique and invalid changes leave both the library and selection intact."""
        second = logger.create_tablet_set("Bossing")["selected_tablet_set_id"]
        before = logger.get_state()
        for save in (lambda: logger.create_tablet_set(" bossing "),
                     lambda: logger.create_tablet_set(" "),
                     lambda: logger.rename_tablet_set(self.first, "BOSSING"),
                     lambda: logger.rename_tablet_set("missing", "Valid"),
                     lambda: logger.select_tablet_set("missing")):
            with self.assertRaises(ValueError):
                save()
        self.assertEqual(logger.get_state(), before)
        self.assertEqual(logger.rename_tablet_set(second, "Boss rush")["tablet_sets"][1]["name"], "Boss rush")


if __name__ == "__main__":
    unittest.main()
