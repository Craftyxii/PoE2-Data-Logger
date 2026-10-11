"""Saved-chain corrections discard only selected data and remain exactly undoable."""

import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store


class ChainDiscardTests(unittest.TestCase):
    """Protect saved counts, history and unrelated expeditions while deleting chain data."""

    TABLES = ("legacy_export", "new_export", "maps", "map_unique_kills", "expeditions",
              "scan_links", "currency_snapshots", "ritual_pages", "commits",
              "chain_completions", "chain_step_provenance")

    def setUp(self):
        """Create a fresh active map in an isolated database for each discard scenario."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-chain-discard-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Restore the incoming storage directory and remove the isolated profile."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def rows(self):
        """Read exact saved table contents so Undo cannot silently lose dependent data."""
        with logger._connect() as db:
            result = {}
            for table in self.TABLES:
                keys = [row["name"] for row in db.execute(f"PRAGMA table_info({table})") if row["pk"]]
                result[table] = [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY {','.join(keys)}")]
            return result

    def commits(self):
        """Read full audit records in their original scan commit order."""
        with logger._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]

    def add_part(self, rune, request_id):
        """Save one accepted propagation part with its count, origin and replay receipt."""
        return logger.accept_propagation_part(logger.scan_context(), runes=[rune],
            recipe="Greater Jeweller's Orb", request_id=request_id)

    def save_remnant(self, scan_id=None):
        """Save an independent remnant using two real recipes from the bundled catalog."""
        with logger._connect() as db:
            recipes = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=26").fetchone()[0])[1:3]
        return logger.commit_remnant(*recipes, 26, scan_id=scan_id)

    def assert_undo_restores(self, before):
        """Reopen storage and reverse one discard, recovering the exact removed records."""
        logger._READY = False
        logger.initialize()
        logger.undo_last_action()
        self.assertEqual(self.rows(), before)

    def test_discard_completed_chain_reopens_same_id_and_preserves_other_saved_activity(self):
        """Discard a completed selected chain without changing remnants, counts or another expedition."""
        saved = self.save_remnant()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 8}])
        logger.save_kills(12, 3, 1, unique=2)
        self.add_part("Death", "first")
        self.add_part("Power", "second")
        logger.complete_chain(logger.scan_context())
        self.add_part("Time", "other-expedition")
        logger.save_settings({"expedition": 1})
        before, original_commits = self.rows(), self.commits()
        state_before = logger.get_state()

        logger.discard_chain(logger.scan_context())

        state = logger.get_state()
        self.assertEqual((state["current_map_id"], state["current_expedition_id"]), ("M0001", "M0001-E01"))
        self.assertEqual(state["settings"], state_before["settings"])
        self.assertEqual((state["chain"], state["chain_provenance"], state["chain_completed"]), ([], [], False))
        self.assertEqual((state["detonated"], state["kills"], state["unique_kills"]), (2, [12, 3, 1], 2))
        self.assertEqual(state["map_remnant_id"], saved["remnant_id"])
        after = self.rows()
        for table in ("maps", "map_unique_kills", "expeditions", "currency_snapshots", "scan_links"):
            self.assertEqual(after[table], before[table], table)
        self.assertEqual(self.commits()[:-1], original_commits)
        audit = self.commits()[-1]
        self.assertEqual(audit["kind"], "Chain discard")
        self.assertEqual([step["step"] for step in json.loads(audit["details_json"])["removed_steps"]], [1, 2])
        with logger._connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM chain_completions WHERE expedition_id='M0001-E01'").fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM chain_step_provenance WHERE expedition_id='M0001-E01'").fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM new_export WHERE expedition_id='M0001-E02' "
                                        "AND chain_step>0").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM new_export WHERE remnant_id=?",
                                        (saved["remnant_id"],)).fetchone()[0],
                             sum(row[3] == saved["remnant_id"] for row in before["new_export"]))
        self.assert_undo_restores(before)

    def test_discard_middle_step_keeps_survivor_ids_and_completed_step_count(self):
        """Delete one completed part while retaining survivor IDs, origins and detonated totals."""
        for rune, token in (("Death", "a"), ("Power", "b"), ("Time", "c")):
            self.add_part(rune, token)
        logger.complete_chain(logger.scan_context())
        logger.save_settings({"expedition": 1})
        before, original_commits = self.rows(), self.commits()

        logger.discard_chain_step(2, logger.scan_context())

        state = logger.get_state()
        self.assertEqual([step["step"] for step in state["chain"]], [1, 3])
        self.assertEqual([origin["step"] for origin in state["chain_provenance"]], [1, 3])
        self.assertEqual((state["chain_completed"], state["detonated"], state["current_expedition_id"]),
                         (True, 3, "M0001-E01"))
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT step_count FROM chain_completions "
                                        "WHERE expedition_id='M0001-E01'").fetchone()[0], 2)
        self.assertEqual(self.commits()[:-1], original_commits)
        audit = self.commits()[-1]
        self.assertEqual(audit["kind"], "Chain step discard")
        self.assertEqual(json.loads(audit["details_json"])["removed_steps"],
                         [{"step": 2, "rune1": "Power", "rune2": ""}])
        self.assert_undo_restores(before)

    def test_discard_last_step_uses_numeric_maximum_after_gaps_and_preserves_survivors(self):
        """Remove the highest stable step ID after a gap, including two-digit step numbers."""
        logger.commit_chain_draft([{"rune1": "Death"} for _ in range(9)] + [{"rune1": "Time"}])
        logger.save_detonated(17)
        logger.discard_chain_step(2, logger.scan_context())
        before = self.rows()
        survivors = [step for step in logger.get_state()["chain"] if step["step"] != 10]

        result = logger.discard_last_chain_step(logger.scan_context())

        state = logger.get_state()
        self.assertEqual(result["steps"], [10])
        self.assertEqual(state["chain"], survivors)
        self.assertEqual([step["step"] for step in state["chain"]], [1, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual((state["current_expedition_id"], state["detonated"]), ("M0001-E01", 17))
        audit = self.commits()[-1]
        self.assertEqual(audit["kind"], "Chain step discard")
        self.assertEqual(json.loads(audit["details_json"])["removed_steps"],
                         [{"step": 10, "rune1": "Time", "rune2": ""}])
        self.assert_undo_restores(before)

    def test_repeated_last_step_discard_updates_completion_preserves_counts_and_undo_each_action(self):
        """Peel completed parts in reverse order, reopening only when empty and undoing each removal exactly."""
        for rune, token in (("Death", "last-a"), ("Power", "last-b"), ("Time", "last-c")):
            self.add_part(rune, token)
        logger.save_counts(12, 3, 1, unique=2)
        logger.complete_chain(logger.scan_context())
        logger.save_settings({"expedition": 1})
        original_commits = self.commits()
        snapshots = []

        for removed_step in (3, 2, 1):
            snapshots.append(self.rows())
            result = logger.discard_last_chain_step(logger.scan_context())
            state = logger.get_state()
            with self.subTest(removed_step=removed_step):
                self.assertEqual(result["steps"], [removed_step])
                self.assertEqual([step["step"] for step in state["chain"]], list(range(1, removed_step)))
                self.assertEqual([origin["step"] for origin in state["chain_provenance"]],
                                 list(range(1, removed_step)))
                self.assertEqual((state["detonated"], state["kills"], state["unique_kills"]),
                                 (3, [12, 3, 1], 2))
                self.assertEqual(state["current_expedition_id"], "M0001-E01")
                self.assertEqual(state["chain_completed"], removed_step > 1)
                with logger._connect() as db:
                    completion = db.execute("SELECT step_count FROM chain_completions "
                                            "WHERE expedition_id='M0001-E01'").fetchone()
                if removed_step > 1:
                    self.assertEqual(completion["step_count"], removed_step - 1)
                else:
                    self.assertIsNone(completion)
        self.assertEqual(self.commits()[:-3], original_commits)
        self.assertEqual([commit["kind"] for commit in self.commits()[-3:]], ["Chain step discard"] * 3)
        for before in reversed(snapshots):
            self.assert_undo_restores(before)
        self.assertTrue(logger.get_state()["chain_completed"])

    def test_last_step_discard_rejects_empty_chain_and_stale_revision_without_mutation(self):
        """Leave saved rows, state, capture tokens and the Undo action untouched on invalid last-step requests."""
        before_rows, before_state = self.rows(), logger.get_state()
        context, undo_action = logger.scan_context(), logger.undo_action()
        with self.assertRaises(ValueError):
            logger.discard_last_chain_step(context)
        self.assertEqual((self.rows(), logger.get_state(), logger.scan_context(), logger.undo_action()),
                         (before_rows, before_state, context, undo_action))
        self.add_part("Death", "stale-last-a")
        self.add_part("Power", "stale-last-b")
        stale = logger.scan_context()
        logger.discard_chain_step(1, logger.scan_context())
        before_rows, before_state = self.rows(), logger.get_state()
        context, undo_action = logger.scan_context(), logger.undo_action()

        with self.assertRaises(ValueError):
            logger.discard_last_chain_step(stale)

        self.assertEqual((self.rows(), logger.get_state(), logger.scan_context(), logger.undo_action()),
                         (before_rows, before_state, context, undo_action))
        self.assertEqual([step["step"] for step in logger.get_state()["chain"]], [2])

    def test_imported_mixed_row_keeps_remnant_and_stats_when_chain_fields_are_discarded(self):
        """Strip chain fields from an imported mixed row while preserving its unrelated remnant data."""
        self.save_remnant()
        logger.save_counts(9, 2, 1, 7, unique=1)
        with logger._connect() as db:
            row = db.execute("SELECT * FROM new_export ORDER BY position LIMIT 1").fetchone()
            values = json.loads(row["row_json"])
            values[25:28] = [5, "Death", "Power"]
            db.execute("INSERT INTO legacy_export VALUES(?,?,?,?,?,?)", (row["position"], row["map_id"],
                row["expedition_id"], row["remnant_id"], 5, json.dumps(values)))
            db.execute("DELETE FROM new_export WHERE position=?", (row["position"],))
        before = self.rows()

        logger.discard_chain_step(5, logger.scan_context())

        with logger._connect() as db:
            remaining = db.execute("SELECT * FROM legacy_export").fetchone()
        expected_values = list(values)
        expected_values[25:28] = ["", "", ""]
        self.assertEqual(json.loads(remaining["row_json"]), expected_values)
        self.assertIsNone(remaining["chain_step"])
        self.assertEqual(remaining["remnant_id"], row["remnant_id"])
        state = logger.get_state()
        self.assertEqual((state["chain"], state["detonated"], state["kills"], state["unique_kills"]),
                         ([], 7, [9, 2, 1], 1))
        self.assert_undo_restores(before)

    def test_completed_chain_correction_changes_only_runes_and_remains_completed(self):
        """Allow historical rune correction while preserving completion, count and original origin."""
        self.add_part("Death", "correct-later")
        logger.complete_chain(logger.scan_context())
        logger.save_settings({"expedition": 1})
        before, original_commits = self.rows(), self.commits()

        logger.update_chain_steps([{"step": 1, "rune1": "Time", "rune2": ""}], logger.scan_context())

        state = logger.get_state()
        self.assertEqual(state["chain"], [{"step": 1, "rune1": "Time", "rune2": ""}])
        self.assertEqual((state["chain_completed"], state["detonated"], state["current_expedition_id"]),
                         (True, 1, "M0001-E01"))
        for table in ("chain_completions", "chain_step_provenance", "expeditions"):
            self.assertEqual(self.rows()[table], before[table], table)
        self.assertEqual(self.commits()[:-1], original_commits)
        self.assertEqual(self.commits()[-1]["kind"], "Chain correction")
        self.assert_undo_restores(before)

    def test_discard_invalidates_old_scan_and_replayed_receipt_without_writes(self):
        """Reject delayed callbacks and original receipt replay after their saved chain is discarded."""
        context = logger.scan_context()
        logger.accept_propagation_part(context, runes=["Death"], request_id="old-reading")
        logger.discard_chain(logger.scan_context())
        self.assertNotEqual(logger.scan_context()["_capture_chain_revision"], context["_capture_chain_revision"])
        before = self.rows()
        current_context = logger.scan_context()
        actions = (
            lambda: logger.accept_propagation_part(context, runes=["Death"], request_id="old-reading"),
            lambda: logger.commit_chain_draft([{"rune1": "Power"}], context, request_id="late-draft"),
            lambda: logger.discard_chain(context),
            lambda: logger.accept_propagation_part(logger.scan_context(), runes=["Death"], request_id="old-reading"),
            lambda: logger.discard_chain_step(99, logger.scan_context()),
        )
        for action in actions:
            with self.subTest(action=action), self.assertRaises(ValueError):
                action()
            self.assertEqual(self.rows(), before)
            self.assertEqual(logger.scan_context(), current_context)

    def test_discard_history_exports_removed_pair_and_frozen_origin_when_step_number_is_reused(self):
        """Expose discard audit evidence separately from a later approval that reuses the same step number."""
        first = self.add_part("Death", "old-part")
        removed = logger.discard_chain_step(1, logger.scan_context())
        latest = self.add_part("Power", "replacement-part")
        rows = list(csv.DictReader(io.StringIO(logger.export_record_history_csv().decode("utf-8-sig"))))
        tombstone = next(row for row in rows if row["Type"] == "Chain step discard")
        self.assertEqual((tombstone["Scan Commit #"], tombstone["Chain Step #"],
                          tombstone["Propagation Rune 1"], tombstone["Chain Status"]),
                         (str(removed["scan_commit_number"]), "1", "Death", "Discarded"))
        self.assertEqual(tombstone["Propagation Recipe"], "Greater Jeweller's Orb")
        self.assertEqual(tombstone["Propagation Recipe ID"], first["provenance"][0]["recipe_id"])
        self.assertEqual(tombstone["Propagation Family Status"], first["provenance"][0]["family_status"])
        current = list(csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertEqual([(row["Chain Step #"], row["Propagation Rune 1"]) for row in current], [("1", "Power")])
        self.assertGreater(latest["scan_commit_number"], removed["scan_commit_number"])
        logger.discard_chain(logger.scan_context())
        rows = list(csv.DictReader(io.StringIO(logger.export_record_history_csv().decode("utf-8-sig"))))
        full = next(row for row in rows if row["Type"] == "Chain discard")
        self.assertEqual((full["Chain Step #"], full["Propagation Rune 1"], full["Chain Status"]),
                         ("1", "Power", "Discarded"))

    def test_service_discard_routes_remove_scoped_logs_and_reject_stale_map_request(self):
        """Dispatch last-step, explicit-step, full-chain and map removals with captured context protection."""
        from PoE2_Data_Logger.core import service
        self.add_part("Death", "service-first")
        self.add_part("Power", "service-second")
        self.add_part("Time", "service-last")
        result = service.dispatch("/api/chain/last/discard", {"expected_context": logger.scan_context()})
        self.assertEqual(result["steps"], [3])
        self.assertEqual([row["step"] for row in logger.get_state()["chain"]], [1, 2])
        context = logger.scan_context()
        service.dispatch("/api/chain/step/discard", {"step": 1, "expected_context": context})
        self.assertEqual([row["step"] for row in logger.get_state()["chain"]], [2])
        service.dispatch("/api/chain/discard", {"expected_context": logger.scan_context()})
        self.assertEqual(logger.get_state()["chain"], [])
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 3}])
        context = logger.scan_context()
        result = service.dispatch("/api/discard-map", {"expected_context": context})
        self.assertEqual((result["map_id"], result["state"]["detonated"]), ("M0001", None))
        self.assertEqual(logger.currency_for_map("M0001")["end"], {})
        before = self.rows()
        with self.assertRaises(ValueError):
            service.dispatch("/api/discard-map", {"expected_context": context})
        self.assertEqual(self.rows(), before)

    def test_map_discard_clears_current_activity_preserves_history_settings_and_raw_scan_then_undo(self):
        """Clear only the active map's logs and snapshots while retaining raw evidence and exact Undo."""
        self.add_part("Death", "past-map")
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 4}])
        logger.finish_map(10, 2, 1)
        logger.start_map()
        logger.save_settings({"tier": 16, "waystone": 87, "expedition": 2})
        raw = io.BytesIO()
        Image.new("RGB", (32, 32), "tan").save(raw, format="PNG")
        with logger._connect() as db:
            seed = db.execute("SELECT sockets,seed_slot,seed_rune FROM seed_states "
                              "WHERE family=26 AND seed_rune!='Unresolved' LIMIT 1").fetchone()
        scan = store.save_scan(raw.getvalue(), "map-discard.png", *tuple(seed), family=26)
        self.save_remnant(scan["id"])
        self.add_part("Power", "current-map")
        logger.complete_chain(logger.scan_context())
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 8}])
        logger.save_ritual_page([{"category": "Item", "name": "Chaos Orb", "quantity": 2}])
        logger.save_kills(20, 3, 2, unique=1)
        before, commits = self.rows(), self.commits()
        state_before = logger.get_state()

        logger.discard_current_map_logs(logger.scan_context())

        state = logger.get_state()
        self.assertEqual((state["current_map_id"], state["current_expedition_id"]),
                         (state_before["current_map_id"], state_before["current_expedition_id"]))
        self.assertEqual(state["settings"], state_before["settings"])
        self.assertEqual(state["next_remnant_id"], state_before["next_remnant_id"])
        self.assertEqual((state["kills"], state["unique_kills"], state["detonated"], state["chain"]),
                         ([None, None, None], None, None, []))
        self.assertGreater(state["scan_commit_count"], state_before["scan_commit_count"])
        self.assertEqual(len(store.list_scans()), 1)
        with logger._connect() as db:
            for table in ("legacy_export", "new_export", "map_unique_kills", "expeditions",
                          "currency_snapshots", "ritual_pages"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE map_id='M0002'").fetchone()[0], 0, table)
            for table in ("chain_completions", "chain_step_provenance"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE expedition_id LIKE 'M0002-%'").fetchone()[0], 0, table)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_links").fetchone()[0], 0)
        after_commits = self.commits()
        self.assertEqual(after_commits[:-1], [commit for commit in commits if commit["map_id"] != "M0002"])
        audit = after_commits[-1]
        self.assertEqual(audit["kind"], "Map logs discard")
        self.assertEqual(json.loads(audit["details_json"])["removed_commits"],
                         [commit for commit in commits if commit["map_id"] == "M0002"])
        after = self.rows()
        for table in ("legacy_export", "new_export", "expeditions", "currency_snapshots", "ritual_pages"):
            self.assertEqual([row for row in after[table] if "M0001" in row],
                             [row for row in before[table] if "M0001" in row], table)
        self.assert_undo_restores(before)


if __name__ == "__main__":
    unittest.main()
