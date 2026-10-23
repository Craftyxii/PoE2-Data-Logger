"""Persist explicit Rite membership and carry only completed, same-Rite observations."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, ritual_rite_store, store
from PoE2_Data_Logger.core.ritual_rites_catalog import affixes, normalize_affixes


class RitualRiteSettingsTests(unittest.TestCase):
    """Use isolated SQLite profiles for map boundaries, provenance and Undo checks."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-ritual-rite-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        # Rites apply only while Ritual is the selected Activity Type.
        logger.save_settings({"activity_type": "Ritual"})
        rows = affixes()
        self.local = next(row for row in rows if row["kind"] == "local")
        self.proliferation = next(row for row in rows if row["kind"] == "proliferation")
        self.bounty = next(row for row in rows if row["kind"] == "bounty")

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def map_snapshot(self, map_id="M0001"):
        with logger._connect() as db:
            row = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
            return json.loads(row[0])

    def completion_rows(self):
        with logger._connect() as db:
            return [tuple(row) for row in db.execute("SELECT * FROM ritual_rite_completions ORDER BY map_number")]

    def begin_rite(self, name="Declared Rite"):
        return logger.new_ritual_rite(name)["selected_rite_id"]

    def save_proliferation(self, count=1):
        return logger.save_ritual_rite_affixes([{"id": self.proliferation["id"], "count": count}])

    def test_unset_metadata_is_optional_and_does_not_change_existing_snapshots(self):
        """Blank Rite settings add no map context keys or completion and never gate map logging."""
        self.assertEqual(logger.ritual_rite_state(), logger.get_state()["ritual_rite"])
        self.assertIsNone(logger.ritual_rite_state()["selected_rite_id"])
        logger.start_map()
        logger.record_commit("Map settings")
        logger.finish_map(1, 2, 3, 4)
        self.assertFalse(set(ritual_rite_store.CONTEXT_FIELDS) & self.map_snapshot().keys())
        self.assertEqual(self.completion_rows(), [])
        with logger._connect() as db:
            for row in db.execute("SELECT snapshot_json FROM commits"):
                self.assertFalse(set(ritual_rite_store.CONTEXT_FIELDS) & json.loads(row[0]).keys())

    def test_only_completed_proliferation_carries_and_locals_reset_for_the_next_map(self):
        """Local/Bounty rows stay on their map; the exact proliferation origin survives separately."""
        rite_id = self.begin_rite()
        values = [self.local["id"], {"id": self.proliferation["id"], "count": 2}, self.bounty["id"]]
        saved = logger.save_ritual_rite_affixes(values)
        self.assertEqual(saved["map_id"], "M0001")
        self.assertEqual(saved["map_ordinal"], 1)
        self.assertEqual(saved["inherited_sources"], [])
        logger.start_map()
        logger.save_counts(2, 3, 4)
        self.assertEqual(logger.ritual_rite_state()["inherited_sources"], [])
        before = self.map_snapshot()
        logger.finish_map(2, 3, 4)
        state = logger.ritual_rite_state()
        self.assertEqual((state["selected_rite_id"], state["map_id"], state["map_ordinal"]),
                         (rite_id, "M0002", 2))
        self.assertEqual(state["current_affixes"], [])
        self.assertEqual(len(state["inherited_sources"]), 1)
        origin = state["inherited_sources"][0]
        self.assertEqual((origin["origin_map_id"], origin["origin_rite_id"], origin["origin_map_ordinal"],
                          origin["count"]), ("M0001", rite_id, 1, 2))
        self.assertEqual(origin["affix"], normalize_affixes([values[1]])[0])
        self.assertEqual(origin["affix"]["value"], self.proliferation["value"])
        self.assertEqual(self.map_snapshot()["ritual_rite_affixes"], normalize_affixes(values))
        for key in ("map_mods", "tablet_mods", "total_mods", "waystone", "area"):
            self.assertEqual(self.map_snapshot()[key], before[key])

    def test_inherited_sources_do_not_duplicate_and_new_origins_remain_separate(self):
        """Carry each origin once without multiplying its value or inferring stacked bonuses."""
        rite_id = self.begin_rite()
        self.save_proliferation(2)
        logger.start_map()
        logger.finish_map(0, 0, 0)
        logger.start_map()
        logger.finish_map(0, 0, 0)
        self.assertEqual(len(logger.ritual_rite_state()["inherited_sources"]), 1)
        logger.start_map()
        self.save_proliferation(3)
        logger.finish_map(0, 0, 0)
        sources = logger.ritual_rite_state()["inherited_sources"]
        self.assertEqual([(row["origin_map_id"], row["origin_rite_id"], row["count"]) for row in sources],
                         [("M0001", rite_id, 2), ("M0003", rite_id, 3)])
        self.assertEqual([row["affix"]["value"] for row in sources], [self.proliferation["value"]] * 2)

    def test_explicit_rite_switching_and_none_prevent_cross_rite_carry(self):
        """Returning to an earlier named Rite sees only that Rite's completed origins."""
        first = self.begin_rite("First Rite")
        self.save_proliferation(1)
        logger.start_map()
        logger.finish_map(0, 0, 0)
        second = self.begin_rite("Second Rite")
        self.assertNotEqual(first, second)
        self.assertEqual(logger.ritual_rite_state()["inherited_sources"], [])
        self.save_proliferation(2)
        logger.start_map()
        logger.finish_map(0, 0, 0)
        state = logger.select_ritual_rite(first)
        self.assertEqual(state["map_ordinal"], 2)
        self.assertEqual([row["origin_map_id"] for row in state["inherited_sources"]], ["M0001"])
        state = logger.select_ritual_rite(None)
        self.assertIsNone(state["selected_rite_id"])
        self.assertIsNone(state["map_ordinal"])
        self.assertEqual(state["inherited_sources"], [])
        # Typed local metadata remains available without declaring a multi-map Rite.
        logger.save_ritual_rite_affixes([self.proliferation["id"]])
        logger.start_map()
        logger.finish_map(0, 0, 0)
        self.assertEqual(logger.ritual_rite_state()["inherited_sources"], [])
        self.assertEqual(logger.select_ritual_rite(first)["map_ordinal"], 2)

    def test_upcoming_drafts_survive_map_creation_and_a_marker_is_not_a_completion(self):
        """Map IDs bind drafts across inventory/start paths; a marked next map supplies no donor."""
        self.begin_rite()
        self.save_proliferation()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 1}])
        logger.start_map()
        self.assertEqual(len(logger.ritual_rite_state()["current_affixes"]), 1)
        logger.mark_next_map(True)
        self.assertEqual(logger.ritual_rite_state()["current_affixes"], [])
        self.assertEqual(logger.ritual_rite_state()["inherited_sources"], [])
        logger.mark_next_map(False)
        self.assertEqual(len(logger.ritual_rite_state()["current_affixes"]), 1)
        logger.mark_next_map(True)
        logger.save_ritual_rite_affixes([self.local["id"]], expected_map_id="M0002")
        # Implicit map start uses the already prepared M0002 draft, never M0001's draft.
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        state = logger.ritual_rite_state()
        self.assertEqual(state["map_id"], "M0002")
        self.assertEqual([row["id"] for row in state["current_affixes"]], [self.local["id"]])
        self.assertEqual(state["inherited_sources"], [])

    def test_finalization_is_idempotent_and_retains_frozen_origin_after_selection_changes(self):
        """A second End map click cannot reinterpret a completed origin as the next Rite's map."""
        first = self.begin_rite()
        self.save_proliferation(2)
        logger.start_map()
        logger.finish_map(1, 2, 3)
        original = self.completion_rows()
        second = self.begin_rite("Different upcoming Rite")
        logger.save_ritual_rite_affixes([self.local["id"]])
        logger.finish_map(4, 5, 6)
        self.assertEqual(self.completion_rows(), original)
        self.assertEqual(self.map_snapshot()["ritual_rite_id"], first)
        self.assertEqual(logger.ritual_rite_state()["selected_rite_id"], second)
        self.assertEqual(logger.select_ritual_rite(first)["inherited_sources"][0]["origin_rite_id"], first)

    def test_new_observations_and_final_map_freeze_metadata_without_rewriting_earlier_commits(self):
        """Editing optional settings changes subsequent snapshots while prior observations stay exact."""
        self.begin_rite()
        self.save_proliferation()
        logger.start_map()
        first = logger.record_commit("Map settings")
        with logger._connect() as db:
            before = db.execute("SELECT snapshot_json FROM commits WHERE number=?", (first,)).fetchone()[0]
        custom = {"text": "User-described future Ritual modifier", "kind": "local", "count": 2}
        logger.save_ritual_rite_affixes([custom])
        logger.record_commit("Map settings")
        logger.finish_map(0, 0, 0)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT snapshot_json FROM commits WHERE number=?", (first,)).fetchone()[0], before)
            latest = json.loads(db.execute("SELECT snapshot_json FROM commits ORDER BY number DESC LIMIT 1").fetchone()[0])
        self.assertEqual(latest["ritual_rite_affixes"], normalize_affixes([custom]))
        self.assertEqual(self.map_snapshot()["ritual_rite_affixes"], normalize_affixes([custom]))
        self.assertEqual(logger.ritual_rite_state()["inherited_sources"], [])
        logger.record_commit("Tablet config")
        with logger._connect() as db:
            library = json.loads(db.execute("SELECT snapshot_json FROM commits ORDER BY number DESC LIMIT 1").fetchone()[0])
        self.assertFalse(set(ritual_rite_store.CONTEXT_FIELDS) & library.keys())

    def test_undo_finalization_revision_and_discard_restore_completion_sources_atomically(self):
        """Completion inverses restore map carry and discarded logs cannot keep acting as donors."""
        self.begin_rite()
        self.save_proliferation(2)
        logger.start_map()
        logger.finish_map(1, 2, 3)
        self.assertEqual(len(logger.ritual_rite_state()["inherited_sources"]), 1)
        logger.undo_last_action()
        self.assertEqual(self.completion_rows(), [])
        self.assertEqual(logger.ritual_rite_state()["map_id"], "M0001")
        self.assertEqual(logger.ritual_rite_state()["inherited_sources"], [])
        logger.finish_map(1, 2, 3)
        logger.start_map()
        logger.undo_last_action()  # Reopen the previous map's setup for an explicit revision.
        logger.save_ritual_rite_affixes([self.local["id"]])
        logger.finish_map(4, 5, 6)
        self.assertEqual(len(self.completion_rows()), 1)
        self.assertEqual(logger.ritual_rite_state()["inherited_sources"], [])
        logger.undo_last_action()
        logger.undo_last_action()  # Restore the previous map's proliferation selection.
        self.assertEqual(self.map_snapshot()["ritual_rite_affixes"], normalize_affixes([
            {"id": self.proliferation["id"], "count": 2}]))
        logger.discard_current_map_logs()
        self.assertEqual(self.completion_rows(), [])
        logger.mark_next_map(True)
        self.assertEqual(logger.ritual_rite_state()["inherited_sources"], [])
        logger.undo_last_action()  # Marker only.
        logger.undo_last_action()  # Restore discarded logs and the source completion.
        self.assertEqual(len(self.completion_rows()), 1)

    def test_restart_reset_and_sqlite_backup_preserve_scoped_provenance(self):
        """Backups retain complete origin rows; reset removes carry before Map IDs repeat."""
        selected = self.begin_rite()
        self.save_proliferation(2)
        logger.start_map()
        logger.finish_map(1, 2, 3)
        state = logger.ritual_rite_state()
        logger._READY = False
        logger.initialize()
        self.assertEqual(logger.ritual_rite_state(), state)
        path = Path(self.temporary.name) / "rite-backup.sqlite3"
        path.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT rite_id FROM ritual_rites").fetchone()[0], selected)
            snapshot = json.loads(db.execute("SELECT snapshot_json FROM ritual_rite_completions").fetchone()[0])
        self.assertEqual(snapshot["ritual_rite_affixes"], normalize_affixes([
            {"id": self.proliferation["id"], "count": 2}]))
        logger.clear_export_and_reset_ids()
        state = logger.ritual_rite_state()
        self.assertEqual(state["selected_rite_id"], selected)
        self.assertEqual((state["map_id"], state["map_ordinal"]), ("M0001", 1))
        self.assertEqual((state["current_affixes"], state["inherited_sources"]), ([], []))
        with self.assertRaisesRegex(ValueError, "session changed"):
            logger.save_ritual_rite_affixes([self.local["id"]], expected_map_id="M0001",
                                             expected_generation=state["session_generation"] - 1)

    def test_invalid_names_affixes_and_stale_map_writes_leave_optional_state_unchanged(self):
        """Catalog validation and map guards fail before writing local metadata or library rows."""
        self.assertEqual(logger.new_ritual_rite()["selected_rite_name"], "Rite 1")
        self.assertEqual(logger.new_ritual_rite()["selected_rite_name"], "Rite 2")
        before = logger.ritual_rite_state()
        for save in (lambda: logger.new_ritual_rite(" rite 1 "),
                     lambda: logger.new_ritual_rite(" "),
                     lambda: logger.select_ritual_rite("unknown"),
                     lambda: logger.save_ritual_rite_affixes(["unknown"]),
                     lambda: logger.save_ritual_rite_affixes([{"id": self.local["id"], "count": 0}]),
                     lambda: logger.save_ritual_rite_affixes([self.local["id"]], expected_map_id="M9999")):
            with self.assertRaises(ValueError):
                save()
        self.assertEqual(logger.ritual_rite_state(), before)
        logger.start_map()
        with patch.object(logger, "_record_commit", side_effect=ValueError("Final commit failed")):
            with self.assertRaisesRegex(ValueError, "Final commit failed"):
                logger.finish_map(1, 2, 3)
        self.assertEqual(self.completion_rows(), [])
        self.assertEqual(logger.ritual_rite_state(), before)


if __name__ == "__main__":
    unittest.main()
