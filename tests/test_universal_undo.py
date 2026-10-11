"""Exercise durable, atomic reversal of saved logger actions and their review learning."""

import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from PoE2_Data_Logger.core import logger_store as logger, store


class UniversalUndoTests(unittest.TestCase):
    """Keep history, counters, catalog learning and stale capture protection consistent after undo."""

    SESSION_TABLES = (
        "legacy_export", "new_export", "maps", "map_unique_kills", "expeditions",
        "scan_links", "currency_snapshots", "ritual_pages", "commits",
        "chain_completions", "chain_step_provenance",
    )

    def setUp(self):
        """Create a fresh WAL profile with one active map for every independent workflow."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-universal-undo-")
        self.previous_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Restore the incoming profile and remove the isolated database and journal files."""
        store.DATA_DIR = self.previous_data_dir
        logger._READY = False
        self.temporary.cleanup()

    def rows(self, tables=None):
        """Read exact ordered before-images of the session or explicitly selected catalog tables."""
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in (self.SESSION_TABLES if tables is None else tables)}

    def exports(self):
        """Read current and historical export projections to detect incomplete reversal."""
        return tuple(producer() for producer in (
            logger.export_csv, logger.export_all_csv, logger.export_currency_csv,
            logger.export_ritual_csv, logger.export_maps_csv,
        ))

    def undo(self):
        """Undo one logical action and verify its returned state matches durable storage."""
        before_generation = logger.session_generation()
        result = logger.undo_last_action()
        self.assertTrue(result["action"])
        self.assertEqual(result["state"], logger.get_state())
        self.assertGreater(logger.session_generation(), before_generation)
        return result["state"]

    def restart(self):
        """Reopen the database so undo must rely on durable storage rather than process memory."""
        logger._READY = False
        logger.initialize()
        return logger.get_state()

    def remnant_recipes(self):
        """Read two real recipes from the bundled family used by remnant approval checks."""
        with logger._connect() as db:
            return json.loads(db.execute("SELECT recipes_json FROM families WHERE id=26").fetchone()[0])[1:3]

    def approve_propagation(self, request_id="undo-propagation"):
        """Save a paired, explicitly approved part with its real catalog recipe origin."""
        return logger.accept_propagation_part(logger.scan_context(), runes=["Death", "Power"],
            recipe="Greater Jeweller's Orb", request_id=request_id)

    def example(self, name, category="Currency"):
        """Create one occupied, distinctive icon suitable for actual review-learning validation."""
        image = Image.new("RGB", (48, 48), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((5, 5, 22, 42), fill=(210, 150, 50))
        draw.ellipse((21, 7, 42, 37), fill=(70, 140, 220))
        return {"name": name, "category": category, "image": image, "columns": 1, "rows": 1}

    def test_remnant_approval_restores_rows_counters_and_scan_link(self):
        """Remove every row of one remnant while preserving its saved capture and earlier history."""
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), "tan").save(buffer, format="PNG")
        with logger._connect() as db:
            seed = db.execute("SELECT sockets,seed_slot,seed_rune FROM seed_states "
                              "WHERE family=26 AND seed_rune!='Unresolved' LIMIT 1").fetchone()
        scan = store.save_scan(buffer.getvalue(), "undo-remnant.png", *tuple(seed), family=26)
        pending = logger.assign_ocr_id("opened")
        capture = {**logger.scan_context(), **pending}
        before_rows, before_exports = self.rows(), self.exports()
        first, second = self.remnant_recipes()
        saved = logger.commit_remnant(first, second, 26, scan_id=scan["id"], expected_context=capture)
        self.assertEqual(saved["remnant_id"], "R0001")
        self.assertEqual(logger.get_state()["next_remnant_id"], "R0002")
        state = self.undo()
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual(self.exports(), before_exports)
        self.assertEqual(state["next_remnant_id"], "R0001")
        self.assertEqual(len(store.list_scans()), 1)
        with self.assertRaises(ValueError):
            logger.commit_remnant(first, second, 26, expected_context=capture)

    def test_propagation_approval_is_one_action_for_count_pair_origin_and_receipt(self):
        """Reverse both propagation commits and all dependent rows together without touching remnants."""
        pending = logger.assign_ocr_id("opened")
        before_rows, before_exports = self.rows(), self.exports()
        self.approve_propagation()
        state = logger.get_state()
        self.assertEqual((state["detonated"], len(state["chain"]), state["scan_commit_count"]), (1, 1, 2))
        self.assertTrue(state["chain_provenance"][0]["recipe_id"])
        state = self.undo()
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual(self.exports(), before_exports)
        self.assertEqual((state["detonated"], state["chain"], state["next_remnant_id"]), (None, [], "R0001"))
        self.assertIsNone(state["ocr_pending"])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_append_receipts").fetchone()[0], 0)

    def test_visible_seed_batch_undo_reverses_all_selected_remnants_atomically(self):
        """Undo one multi-remnant approval without leaving half the batch or consumed remnant numbers."""
        with logger._connect() as db:
            stage = dict(db.execute("SELECT family,sockets,seed_slot,seed_rune FROM seed_states "
                                   "WHERE family=26 AND seed_rune!='Unresolved' LIMIT 1").fetchone())
        pending = logger.assign_ocr_id("seed")
        result = {**logger.scan_context(), **pending, "remnants": [dict(stage), dict(stage)]}
        selections = [{"index": index, **stage} for index in range(2)]
        before_rows, before_exports = self.rows(), self.exports()
        saved = logger.commit_seed_batch(result, selections)
        self.assertEqual([item["remnant_id"] for item in saved["saved"]], ["R0001", "R0002"])
        self.assertEqual(logger.get_state()["next_remnant_id"], "R0003")
        state = self.undo()
        self.assertEqual((self.rows(), self.exports()), (before_rows, before_exports))
        self.assertEqual(state["next_remnant_id"], "R0001")
        self.assertIsNone(state["ocr_pending"])

    def test_count_only_propagation_undo_preserves_existing_saved_chain_parts(self):
        """Reverse a held scan's standalone count without treating earlier saved runes as its draft."""
        logger.commit_chain("Death")
        before_rows, before_exports = self.rows(), self.exports()
        logger.increment_propagation_detonated(logger.scan_context(), runes=["Power"],
                                               request_id="held-undo-count")
        self.assertEqual(logger.get_state()["detonated"], 1)
        state = self.undo()
        self.assertEqual((self.rows(), self.exports()), (before_rows, before_exports))
        self.assertEqual(state["chain"], [{"step": 1, "rune1": "Death", "rune2": ""}])
        self.assertIsNone(state["detonated"])

    def test_legacy_append_and_complete_are_one_undo_action(self):
        """Reverse an append-with-completion save as a unit, including expedition advancement."""
        before_rows, before_exports = self.rows(), self.exports()
        logger.commit_chain_steps([{"rune1": "Death", "rune2": "Power"}],
                                  advance_expedition=True, expected_context=logger.scan_context())
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        state = self.undo()
        self.assertEqual((self.rows(), self.exports()), (before_rows, before_exports))
        self.assertEqual(state["current_expedition_id"], "M0001-E01")

    def test_multiple_undo_survives_restart_and_reverses_replacements_in_order(self):
        """Restore prior inventory versions one at a time across process reinitialization."""
        snapshots = [(self.rows(), self.exports())]
        for quantity in (3, 7, 11):
            logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": quantity}])
            snapshots.append((self.rows(), self.exports()))
        self.restart()
        for expected_rows, expected_exports in reversed(snapshots[:-1]):
            inventories = expected_rows["currency_snapshots"]
            with self.subTest(expected_end=json.loads(inventories[0][2]) if inventories else {}):
                self.undo()
                self.assertEqual(self.rows(), expected_rows)
                self.assertEqual(self.exports(), expected_exports)
                self.restart()

    def test_chain_completion_undo_restores_selected_chain_without_remnant_reset(self):
        """Reopen the completed expedition with its parts, counts and remnant numbering intact."""
        first, second = self.remnant_recipes()
        logger.commit_remnant(first, second, 26)
        self.approve_propagation()
        before_rows, before_exports = self.rows(), self.exports()
        before = logger.get_state()
        logger.complete_chain(logger.scan_context())
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        state = self.undo()
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual(self.exports(), before_exports)
        for field in ("current_expedition_id", "chain", "chain_provenance", "detonated",
                      "next_remnant_id", "map_remnant_id"):
            self.assertEqual(state[field], before[field])
        self.assertFalse(state["chain_completed"])

    def test_chain_correction_and_append_undo_preserve_part_identity_and_origin(self):
        """Undo a saved rune correction and then the next append without rewriting the first origin."""
        self.approve_propagation()
        original_rows, original_exports = self.rows(), self.exports()
        logger.commit_chain("Time")
        appended_rows, appended_exports = self.rows(), self.exports()
        logger.update_chain_steps([{"step": 1, "rune1": "Power", "rune2": "Death"}], logger.scan_context())
        self.undo()
        self.assertEqual((self.rows(), self.exports()), (appended_rows, appended_exports))
        self.undo()
        self.assertEqual((self.rows(), self.exports()), (original_rows, original_exports))

    def test_repeated_propagation_and_completion_callbacks_do_not_consume_undo(self):
        """Keep no-op retry receipts outside history so one undo reverses the actual saved event."""
        capture = logger.scan_context()
        before = self.rows()
        self.approve_propagation()
        action = logger.get_state()["undo_action"]
        replay = logger.accept_propagation_part(capture, runes=["Death", "Power"],
            recipe="Greater Jeweller's Orb", request_id="undo-propagation")
        self.assertTrue(replay["reused"])
        self.assertEqual(logger.get_state()["undo_action"], action)
        self.undo()
        self.assertEqual(self.rows(), before)
        self.approve_propagation("undo-completion")
        before = self.rows()
        capture = logger.scan_context()
        logger.complete_chain(capture)
        action = logger.get_state()["undo_action"]
        self.assertTrue(logger.complete_chain(capture)["already_completed"])
        self.assertEqual(logger.get_state()["undo_action"], action)
        self.undo()
        self.assertEqual(self.rows(), before)

    def test_unchanged_chain_correction_and_reads_leave_last_action_available(self):
        """Do not create an undo action for unchanged values, export reads or state reads."""
        before = self.rows()
        self.approve_propagation()
        action = logger.get_state()["undo_action"]
        unchanged = logger.update_chain_steps([{"step": 1, "rune1": "Death", "rune2": "Power"}],
                                              logger.scan_context())
        self.assertFalse(unchanged["changed"])
        self.exports()
        self.assertEqual(logger.get_state()["undo_action"], action)
        self.undo()
        self.assertEqual(self.rows(), before)

    def test_failed_approval_rolls_back_learned_name_and_undo_history(self):
        """A failure after registering a label must leave neither partial rows nor another undo action."""
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 5}])
        before_rows, before_exports = self.rows(), self.exports()
        action = logger.get_state()["undo_action"]
        with self.assertRaises(ValueError):
            logger.save_currency_snapshot("end", [{"name": "Failed Undo Learning", "quantity": -1}],
                                          register_names=True)
        self.assertEqual((self.rows(), self.exports()), (before_rows, before_exports))
        self.assertNotIn("Failed Undo Learning", logger.currency_names())
        self.assertEqual(logger.get_state()["undo_action"], action)
        self.undo()
        self.assertEqual(logger.currency_for_map("M0001")["end"], {})

    def test_currency_learning_undo_restores_prior_label_for_same_icon(self):
        """Reverse a mistaken label, its learned name and its replacement inventory atomically."""
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 3}],
            icon_examples=[self.example("Chaos Orb")])
        tables = ("currency_items", "item_names", "ritual_names", "review_icon_examples")
        before_rows, before_catalog, before_exports = self.rows(), self.rows(tables), self.exports()
        logger.save_currency_snapshot("end", [{"name": "Mistaken Undo Currency", "quantity": 9}],
            register_names=True, icon_examples=[self.example("Mistaken Undo Currency")])
        self.assertEqual(logger.review_icons()[0]["name"], "Mistaken Undo Currency")
        self.undo()
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual(self.rows(tables), before_catalog)
        self.assertEqual(self.exports(), before_exports)
        self.assertEqual(logger.review_icons()[0]["name"], "Chaos Orb")

    def test_ritual_learning_and_same_capture_replacement_undo_restore_page_and_history(self):
        """Preserve page identity and metadata while removing a mistaken replacement and its learned Omen."""
        logger.save_ritual_page([{"category": "Item", "name": "Chaos Orb", "quantity": 2}],
            scan_hash="a" * 64, tribute_available=800, rerolls_remaining=2)
        tables = ("currency_items", "item_names", "ritual_names", "review_icon_examples")
        before_rows, before_catalog, before_exports = self.rows(), self.rows(tables), self.exports()
        logger.save_ritual_page([{"category": "Omen", "name": "Omen of Mistaken Undo", "quantity": 1,
                                  "tribute": 500, "deferred": True}],
            scan_hash="a" * 64, tribute_available=300, rerolls_remaining=1, register_names=True,
            icon_examples=[self.example("Omen of Mistaken Undo", "Omen")])
        self.undo()
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual(self.rows(tables), before_catalog)
        self.assertEqual(self.exports(), before_exports)

    def test_counts_undo_restores_first_row_totals_and_blank_versus_zero(self):
        """Restore map/expedition totals and every patched export cell without conflating unknown and zero."""
        first, second = self.remnant_recipes()
        logger.commit_remnant(first, second, 26)
        logger.save_counts(12, 0, "", 2, unique=0)
        before_rows, before_exports = self.rows(), self.exports()
        logger.save_counts(99, 8, 7, 6, unique=5)
        state = self.undo()
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual(self.exports(), before_exports)
        self.assertEqual((state["kills"], state["unique_kills"], state["detonated"]), ([12, 0, None], 0, 2))

    def test_separate_kill_and_detonated_actions_undo_independently(self):
        """Undo one changed statistic without discarding an earlier independently saved statistic."""
        logger.save_kills(12, 3, 1, unique=2)
        before_rows, before_exports = self.rows(), self.exports()
        logger.save_detonated(7)
        state = self.undo()
        self.assertEqual((self.rows(), self.exports()), (before_rows, before_exports))
        self.assertEqual((state["kills"], state["unique_kills"], state["detonated"]), ([12, 3, 1], 2, None))

    def test_new_map_undo_preserves_completed_stats_and_reopens_previous_map(self):
        """Remove the next empty map while reopening the previous logging target with saved totals intact."""
        logger.save_settings({"tier": 16, "waystone": 87, "map_mods": 5, "expedition": 3})
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 8}])
        logger.finish_map(12, 3, 1, 4, unique=2)
        before_rows = self.rows()
        logger.start_map()
        result = logger.undo_last_action()
        self.assertEqual(result["action"], "New map")
        state = result["state"]
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual((state["current_map_id"], state["current_expedition_id"], state["pending_new_map"]),
                         ("M0001", "M0001-E03", False))
        self.assertEqual((state["kills"], state["unique_kills"], state["detonated"]), ([12, 3, 1], 2, 4))
        self.assertEqual((state["settings"]["tier"], state["settings"]["waystone"]), (16, 87))
        logger.save_counts(13, 3, 1, unique=2)
        self.assertEqual(logger.get_state()["kills"], [13, 3, 1])
        logger.finish_map(13, 3, 1, unique=2)
        self.assertTrue(logger.get_state()["pending_new_map"])

    def test_undo_after_next_map_activity_reverses_activity_before_map_creation(self):
        """Never discard a map with activity as one accidental click on an older New map action."""
        logger.finish_map(12, 3, 1, 4)
        first_map_rows = self.rows()
        logger.start_map()
        second_map_rows = self.rows()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 7}])
        state = self.undo()
        self.assertEqual(state["current_map_id"], "M0002")
        self.assertEqual(self.rows(), second_map_rows)
        state = self.undo()
        self.assertEqual(state["current_map_id"], "M0001")
        self.assertEqual(self.rows(), first_map_rows)
        self.assertFalse(state["pending_new_map"])

    def test_map_and_atlas_settings_undo_restore_exact_saved_profiles(self):
        """Restore map setup fields and Atlas selections separately while preserving logged setup snapshots."""
        before_rows = self.rows()
        before_settings = logger.get_state()["settings"]
        logger.save_settings({"tier": 16, "waystone": 77, "biome": "Swamp", "deli": True})
        changed_rows = self.rows()
        changed_settings = logger.get_state()["settings"]
        atlas = dict(changed_settings["atlas_settings"])
        atlas["gear_item_rarity"] = 140
        logger.save_atlas_settings(atlas)
        state = self.undo()
        self.assertEqual(state["settings"], changed_settings)
        self.assertEqual(self.rows(), changed_rows)
        state = self.undo()
        self.assertEqual(state["settings"], before_settings)
        self.assertEqual(self.rows(), before_rows)

    def test_grouped_settings_and_audit_commit_are_one_undo_action(self):
        """Undo both settings and their separately recorded audit event from one UI command."""
        self.approve_propagation()
        before_rows, before_exports = self.rows(), self.exports()
        before_settings = logger.get_state()["settings"]
        previous_action = logger.get_state()["undo_action"]
        with logger.undo_group("Map settings"):
            logger.save_settings({"biome": "Swamp", "wisp": True, "waystone": 87})
            logger.record_commit("Map settings")
        state = self.undo()
        self.assertEqual((self.rows(), self.exports()), (before_rows, before_exports))
        self.assertEqual(state["settings"], before_settings)
        self.assertEqual(state["undo_action"], previous_action)

    def test_grouped_repeated_row_changes_restore_earliest_before_image(self):
        """Reverse repeated updates and twenty audit inserts in their true mutation order."""
        first, second = self.remnant_recipes()
        logger.commit_remnant(first, second, 26)
        before_rows, before_exports = self.rows(), self.exports()
        with logger.undo_group("Grouped map counts"):
            for count in range(20):
                logger.save_counts(count, 0, "", count, unique=count)
        self.assertEqual(logger.get_state()["kills"], [19, 0, None])
        self.undo()
        self.assertEqual((self.rows(), self.exports()), (before_rows, before_exports))

    def test_tablet_undo_restores_slot_sequence_and_configuration_together(self):
        """Restore the next scan slot and all tablet fields so a corrected rescan fills the same slot."""
        before_rows = self.rows()
        before_settings = logger.get_state()["settings"]
        with logger._connect() as db:
            affix = db.execute("SELECT name FROM affixes WHERE name LIKE '%additional random%' LIMIT 1").fetchone()[0]
        slot = logger.save_scanned_tablet([{"affix": affix, "value": 2, "unit": "count"}],
                                          ["Map has 2 additional random Modifiers"])
        self.assertEqual(slot, 1)
        self.assertEqual(logger.tablet_next_slot(), 2)
        state = self.undo()
        self.assertEqual(logger.tablet_next_slot(), 1)
        self.assertEqual(state["settings"], before_settings)
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual(logger.save_scanned_tablet([{"affix": affix, "value": 1, "unit": "count"}],
                                                   ["Map has 1 additional random Modifier"]), 1)

    def test_unsaved_reservation_and_discard_do_not_consume_saved_action_undo(self):
        """Keep review-only reservation changes separate from durable saved logging actions."""
        before_rows = self.rows()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 5}])
        action = logger.get_state()["undo_action"]
        pending = logger.assign_ocr_id("opened")
        capture = {**logger.scan_context(), **pending}
        logger.discard_ocr_id()
        self.assertEqual(logger.get_state()["undo_action"], action)
        state = self.undo()
        self.assertEqual(self.rows(), before_rows)
        self.assertIsNone(state["ocr_pending"])
        self.assertEqual(state["next_remnant_id"], "R0001")
        with self.assertRaises(ValueError):
            logger.validate_remnant_context(capture)

    def test_undo_invalidates_old_capture_even_when_map_and_expedition_are_unchanged(self):
        """Prevent a delayed approval from silently reinstating a deliberately undone chain part."""
        capture = logger.scan_context()
        self.approve_propagation()
        self.undo()
        self.assertEqual(logger.scan_context()["_capture_map_id"], capture["_capture_map_id"])
        self.assertEqual(logger.scan_context()["_capture_expedition"], capture["_capture_expedition"])
        before_rows = self.rows()
        with self.assertRaises(ValueError):
            logger.validate_scan_context(capture)
        with self.assertRaises(ValueError):
            logger.accept_propagation_part(capture, runes=["Death", "Power"],
                recipe="Greater Jeweller's Orb", request_id="undo-propagation")
        self.assertEqual(self.rows(), before_rows)
        self.assertFalse(self.approve_propagation("replacement-propagation")["reused"])

    def test_clear_session_clears_undo_history_and_cannot_resurrect_deleted_logs(self):
        """Treat an explicit session reset as a history boundary rather than a reversible logging click."""
        self.approve_propagation()
        logger.clear_export_and_reset_ids()
        state = self.restart()
        self.assertFalse(state["undo_action"])
        before_rows = self.rows()
        with self.assertRaises(ValueError):
            logger.undo_last_action()
        self.assertEqual(self.rows(), before_rows)

    def test_intervening_row_edit_rejects_undo_without_partially_reversing_history(self):
        """Roll back the entire attempted undo when its saved after-image no longer matches storage."""
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 5}])
        action = logger.get_state()["undo_action"]
        with logger._connect() as db:
            db.execute("UPDATE currency_snapshots SET items_json=? WHERE map_id='M0001' AND phase='end'",
                       (json.dumps({"Chaos Orb": 12}),))
        before_rows, before_exports = self.rows(), self.exports()
        generation = logger.session_generation()
        with self.assertRaises(ValueError):
            logger.undo_last_action()
        self.assertEqual((self.rows(), self.exports()), (before_rows, before_exports))
        self.assertEqual(logger.get_state()["undo_action"], action)
        self.assertEqual(logger.session_generation(), generation)


if __name__ == "__main__":
    unittest.main()
