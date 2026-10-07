import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.core.atlas_catalog import catalog


class MapSetupRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-map-setup-")
        self.previous_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous_data_dir
        logger._READY = False
        self.tmp.cleanup()

    def snapshot(self, map_id="M0001"):
        with logger._connect() as db:
            return json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()[0])

    def atlas(self, rarity):
        return {"catalog_version": catalog()["version"], "allocated": [], "choices": {},
                "gear_item_rarity": rarity}

    def inventory(self, phase="start", quantity=3):
        return logger.save_currency_snapshot(phase, [{"name": "Chaos Orb", "quantity": quantity}])

    def waystone(self, quantity=87):
        logger.save_settings({"tier": 16, "waystone": quantity, "map_mods": 5,
                              "waystone_name": "Storm Peak", "waystone_mods": ["Example map modifier"]})
        logger.record_commit("Map settings")

    def rows(self, exporter):
        return list(csv.DictReader(io.StringIO(exporter().decode("utf-8-sig"))))

    def test_first_waystone_after_start_inventory_completes_map_and_keeps_original_atlas(self):
        logger.save_atlas_settings(self.atlas(100))
        original = self.snapshot()
        self.inventory()
        with logger._connect() as db:
            start_commit = tuple(db.execute("SELECT snapshot_json,details_json FROM commits "
                                            "WHERE kind='Currency'").fetchone())
        logger.save_atlas_settings(self.atlas(200))
        self.waystone()
        saved = self.snapshot()
        self.assertEqual((saved["tier"], saved["waystone"], saved["base_map_mods"], saved["waystone_name"]),
                         (16, 87, 5, "Storm Peak"))
        self.assertTrue(saved["waystone_setup_saved"])
        self.assertEqual(saved["atlas_setup_id"], original["atlas_setup_id"])
        self.assertEqual(saved["gear_item_rarity"], 100)
        self.assertEqual(logger.get_state()["settings"]["atlas_settings"]["gear_item_rarity"], 200)
        with logger._connect() as db:
            self.assertEqual(tuple(db.execute("SELECT snapshot_json,details_json FROM commits "
                                             "WHERE kind='Currency'").fetchone()), start_commit)
        logger.finish_map(12, 2, 1, 1)
        exported_map = self.rows(logger.export_maps_csv)[0]
        self.assertEqual((exported_map["Tier"], exported_map["Waystone %"], exported_map["Waystone Name"]),
                         ("16", "87", "Storm Peak"))
        totals = next(row for row in self.rows(logger.export_all_csv) if row["Type"] == "Map totals")
        self.assertEqual(totals["Waystone %"], "87")
        self.assertEqual(totals["Atlas Setup ID"], original["atlas_setup_id"])

    def test_subsequent_edits_and_next_map_cannot_rewrite_saved_setup(self):
        self.inventory()
        self.waystone()
        original = self.snapshot()
        self.waystone(22)
        self.assertEqual(self.snapshot(), original)
        logger.finish_map(3, 2, 1, 1)
        closed = self.snapshot()
        logger.save_settings({"tier": 15, "waystone": 10, "map_mods": 2})
        self.assertEqual(self.snapshot(), closed)
        logger.start_map()
        self.inventory()
        self.waystone(55)
        self.assertEqual(self.snapshot(), closed)
        # The explicit next-map preparation was already complete before M0002 started.
        self.assertEqual(self.snapshot("M0002")["waystone"], 10)

    def test_first_setup_exception_requires_only_start_inventory(self):
        for activity in ("end", "remnant", "chain", "ritual", "counts"):
            with self.subTest(activity=activity):
                logger.clear_export_and_reset_ids()
                logger.start_map()
                self.inventory()
                if activity == "end":
                    self.inventory("end", 5)
                elif activity == "remnant":
                    logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
                elif activity == "chain":
                    logger.commit_chain("Rage")
                elif activity == "ritual":
                    logger.save_ritual_page([{"category": "Item", "name": "Test reward", "quantity": 1}])
                else:
                    logger.save_counts(3, 2, 1, 1)
                original = self.snapshot()
                self.waystone()
                self.assertEqual(self.snapshot(), original)

    def test_existing_map_without_marker_stays_frozen(self):
        self.inventory()
        with logger._connect() as db:
            previous = self.snapshot()
            previous.pop("waystone_setup_saved")
            db.execute("UPDATE maps SET snapshot_json=? WHERE map_id='M0001'", (json.dumps(previous),))
        self.waystone()
        self.assertEqual(self.snapshot(), previous)

    def test_repeated_inventory_phase_replaces_metadata_and_retains_commit_history(self):
        for phase in ("start", "end"):
            with self.subTest(phase=phase):
                logger.clear_export_and_reset_ids()
                logger.save_settings(dict(logger.WAYSTONE_DEFAULTS))
                logger.start_map()
                self.inventory(phase, 3)
                self.waystone()
                self.inventory(phase, 6)
                with logger._connect() as db:
                    snapshot = json.loads(db.execute("SELECT snapshot_json FROM currency_snapshots "
                                                    "WHERE phase=?", (phase,)).fetchone()[0])
                    commits = [json.loads(row[0]) for row in db.execute("SELECT snapshot_json FROM commits "
                                        "WHERE kind='Currency' ORDER BY number")]
                self.assertEqual([item["waystone"] for item in commits], [0, 87])
                self.assertEqual(snapshot, commits[-1])
                row = self.rows(logger.export_currency_csv)[0]
                self.assertEqual(row[f"{phase.title()} Waystone %"], "87")
                self.assertEqual(row[f"{phase.title()} Count"], "6")

    def test_same_ritual_page_replacement_matches_latest_commit_and_preserves_frozen_atlas(self):
        logger.save_atlas_settings(self.atlas(100))
        original_atlas = self.snapshot()["atlas_setup_id"]
        self.waystone()
        fingerprint = "a" * 64
        first = logger.save_ritual_page([{"category": "Item", "name": "Example reward", "quantity": 1}],
                                       "First capture", fingerprint)
        with logger._connect() as db:
            first_commit = tuple(db.execute("SELECT snapshot_json,details_json FROM commits WHERE number=?",
                                            (first["scan_commit_number"],)).fetchone())
        logger.save_atlas_settings(self.atlas(200))
        logger.save_settings({"tier": 15, "waystone": 22})
        saved = logger.save_ritual_page([{"category": "Item", "name": "Example reward", "quantity": 2}],
                                       "Corrected capture", fingerprint)
        self.assertTrue(saved["updated"])
        self.assertEqual(saved["page_number"], 1)
        with logger._connect() as db:
            self.assertEqual(tuple(db.execute("SELECT snapshot_json,details_json FROM commits WHERE number=?",
                                            (first["scan_commit_number"],)).fetchone()), first_commit)
            current_commit = json.loads(db.execute("SELECT snapshot_json FROM commits WHERE number=?",
                                                  (saved["scan_commit_number"],)).fetchone()[0])
            page_snapshot = json.loads(db.execute("SELECT snapshot_json FROM ritual_pages").fetchone()[0])
        self.assertEqual(page_snapshot, current_commit)
        self.assertEqual(page_snapshot["atlas_setup_id"], original_atlas)
        self.assertEqual(page_snapshot["gear_item_rarity"], 100)
        row = self.rows(logger.export_ritual_csv)[0]
        self.assertEqual((row["Ritual Page"], row["Quantity"], row["Waystone %"], row["Page OCR Text"]),
                         ("1", "2", "22", "Corrected capture"))
        self.assertEqual(row["Scan Commit #"], str(saved["scan_commit_number"]))

    def test_first_ever_prepared_waystone_is_marked_ready_and_cannot_be_overwritten_after_start(self):
        logger.clear_export_and_reset_ids()
        self.waystone()
        logger.start_map()
        prepared = self.snapshot()
        self.assertTrue(prepared["waystone_setup_saved"])
        self.assertEqual(prepared["waystone"], 87)
        self.inventory()
        self.waystone(22)
        self.assertEqual(self.snapshot(), prepared)

    def test_pending_next_waystone_survives_start_and_keeps_map_specific_atlas(self):
        logger.save_atlas_settings(self.atlas(100))
        self.waystone()
        self.inventory()
        logger.finish_map(12, 2, 1, 1)
        closed = self.snapshot()
        self.waystone(99)
        logger.save_atlas_settings(self.atlas(200))
        self.inventory()
        logger.save_atlas_settings(self.atlas(300))
        logger.start_map()
        upcoming = self.snapshot("M0002")
        self.assertEqual((upcoming["tier"], upcoming["waystone"], upcoming["base_map_mods"]), (16, 99, 5))
        self.assertEqual(upcoming["gear_item_rarity"], 200)
        self.assertTrue(upcoming["waystone_setup_saved"])
        self.waystone(22)
        self.assertEqual(self.snapshot("M0002"), upcoming)
        self.assertEqual(self.snapshot(), closed)
        with logger._connect() as db:
            self.assertIsNone(logger._meta(db, "prepared_waystone_setup"))

    def test_pending_next_waystone_survives_implicit_remnant_map_start(self):
        self.waystone()
        logger.finish_map(0, 0, 0, 0)
        closed = self.snapshot()
        self.waystone(99)
        saved = logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        self.assertEqual(saved["map_id"], "M0002")
        upcoming = self.snapshot("M0002")
        self.assertEqual(upcoming["waystone"], 99)
        self.assertTrue(upcoming["waystone_setup_saved"])
        self.assertEqual(self.snapshot(), closed)
        with logger._connect() as db:
            self.assertIsNone(logger._meta(db, "prepared_waystone_setup"))

    def test_pending_preparation_survives_app_restart_before_next_map_start(self):
        self.waystone()
        logger.finish_map(0, 0, 0, 0)
        self.waystone(99)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        upcoming = self.snapshot("M0002")
        self.assertEqual(upcoming["waystone"], 99)
        self.assertTrue(upcoming["waystone_setup_saved"])

    def test_tag_only_pending_commit_does_not_claim_prepared_waystone(self):
        self.waystone()
        logger.finish_map(0, 0, 0, 0)
        logger.save_settings({"biome": "Swamp"})
        logger.record_commit("Map settings")
        logger.start_map()
        upcoming = self.snapshot("M0002")
        self.assertEqual(upcoming["waystone"], 0)
        self.assertFalse(upcoming["waystone_setup_saved"])
        self.inventory()
        self.waystone(99)
        self.assertEqual(self.snapshot("M0002")["waystone"], 99)

    def test_partial_preparation_merges_defaults_and_does_not_claim_readiness(self):
        self.waystone()
        logger.finish_map(0, 0, 0, 0)
        logger.save_settings({"tier": 16})
        logger.record_commit("Map settings")
        logger.start_map()
        upcoming = self.snapshot("M0002")
        self.assertFalse(upcoming["waystone_setup_saved"])
        self.assertEqual((upcoming["tier"], upcoming["waystone"], upcoming["waystone_name"]), (16, 0, ""))
        self.assertEqual(upcoming["waystone_mods"], [])
        self.inventory()
        self.waystone(99)
        self.assertTrue(self.snapshot("M0002")["waystone_setup_saved"])
        self.assertEqual(self.snapshot("M0002")["waystone"], 99)

    def test_separate_partial_preparations_combine_before_start(self):
        logger.finish_map(0, 0, 0, 0)
        logger.save_settings({"tier": 16})
        logger.save_settings({"waystone": 99})
        logger.save_settings({"map_mods": 6})
        logger.start_map()
        upcoming = self.snapshot("M0002")
        self.assertEqual((upcoming["tier"], upcoming["waystone"], upcoming["base_map_mods"]), (16, 99, 6))
        self.assertTrue(upcoming["waystone_setup_saved"])

    def test_canceling_pending_marker_or_undo_invalidates_preparation_and_restores_old_values(self):
        for cancel in (lambda: logger.mark_next_map(False), logger.undo_empty_map):
            with self.subTest(cancel=cancel):
                logger.clear_export_and_reset_ids()
                logger.start_map()
                self.waystone()
                logger.finish_map(0, 0, 0, 0)
                closed = self.snapshot()
                logger.save_settings({"tier": 16, "waystone": 99, "map_mods": 6})
                cancel()
                self.assertEqual(logger.get_state()["settings"]["waystone"], 87)
                self.assertEqual(self.snapshot(), closed)
                with logger._connect() as db:
                    self.assertIsNone(logger._meta(db, "prepared_waystone_setup"))
                logger.mark_next_map(True)
                logger.start_map()
                self.assertEqual(self.snapshot("M0002")["waystone"], 0)

    def test_undo_prepared_empty_map_restores_previous_map_values_and_discards_preparation(self):
        self.waystone()
        logger.finish_map(0, 0, 0, 0)
        closed = self.snapshot()
        # No commit yet: this prepared empty map can still be undone.
        logger.save_settings({"tier": 16, "waystone": 99, "map_mods": 6})
        logger.start_map()
        logger.undo_empty_map()
        self.assertEqual(logger.get_state()["settings"]["waystone"], 87)
        self.assertEqual(self.snapshot(), closed)
        logger.mark_next_map(True)
        logger.start_map()
        self.assertEqual(self.snapshot("M0002")["waystone"], 0)

    def test_reset_invalidates_preparation_and_new_session_map_cannot_consume_it(self):
        logger.finish_map(0, 0, 0, 0)
        logger.save_settings({"tier": 16, "waystone": 99, "map_mods": 6})
        old_generation = logger.session_generation()
        logger.clear_export_and_reset_ids()
        self.assertGreater(logger.session_generation(), old_generation)
        with logger._connect() as db:
            self.assertIsNone(logger._meta(db, "prepared_waystone_setup"))
        logger.start_map()
        # Reset retains editable settings; preparation readiness itself does not survive.
        self.assertFalse(self.snapshot()["waystone_setup_saved"])
        logger.finish_map(0, 0, 0, 0)
        logger.start_map()
        self.assertEqual(self.snapshot("M0002")["waystone"], 0)

    def test_pending_records_use_clean_waystone_defaults_and_closed_end_keeps_old_setup(self):
        logger.save_atlas_settings(self.atlas(100))
        affixes = [{"affix": "Chance to Contain Essences", "value": 12}] + [
            {"affix": "", "value": None} for _ in range(15)]
        logger.save_settings({"atlas_master": "Jado", "tablet_affixes": affixes})
        self.waystone(85)
        logger.finish_map(0, 0, 0, 0)
        self.inventory()
        logger.save_settings({"biome": "Swamp"})
        logger.record_commit("Map settings")
        logger.save_atlas_settings(self.atlas(200))
        self.inventory("end", 6)
        with logger._connect() as db:
            for row in db.execute("SELECT kind,map_id,reference,snapshot_json FROM commits "
                                  "WHERE map_id IN ('M0002','M0003')"):
                context = json.loads(row["snapshot_json"])
                self.assertEqual((context["tier"], context["waystone"], context["base_map_mods"]), (15, 0, 0))
                self.assertEqual(context["waystone_name"], "")
                self.assertEqual(context["waystone_mods"], [])
                self.assertEqual(context["master"], "Jado")
                self.assertEqual(context["tablet_values"][:2], ["Chance to Contain Essences", 12])
            end = json.loads(db.execute("SELECT snapshot_json FROM commits WHERE map_id='M0001' "
                                        "AND kind='Currency' AND reference='End inventory'").fetchone()[0])
        self.assertEqual(end["waystone"], 85)
        self.assertEqual(end["gear_item_rarity"], 100)

    def test_pending_start_and_configuration_use_preparation_without_consuming_it(self):
        self.waystone(85)
        logger.finish_map(0, 0, 0, 0)
        self.waystone(99)
        with logger._connect() as db:
            prepared = logger._meta(db, "prepared_waystone_setup")
        self.inventory()
        logger.save_settings({"biome": "Swamp"})
        logger.record_commit("Map settings")
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "prepared_waystone_setup"), prepared)
            contexts = [json.loads(row[0]) for row in db.execute(
                "SELECT snapshot_json FROM commits WHERE map_id='M0002'")]
        self.assertTrue(contexts)
        self.assertEqual({context["waystone"] for context in contexts}, {99})
        logger.start_map()
        self.assertEqual(self.snapshot("M0002")["waystone"], 99)


if __name__ == "__main__":
    unittest.main()
