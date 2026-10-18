"""Retain item evidence only while before-review affix discovery waits for SQLite."""

import copy
from unittest.mock import patch
import unittest

from PIL import Image

from tests import test_hotkey_presentation_retry as fixtures
from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.ocr import item_text
from PoE2_Data_Logger.ui.native_desktop import modifier_value


class ItemPresentationRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.HotkeyPresentationRetryTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.HotkeyPresentationRetryTests(
            "test_opened_result_and_png_stay_owned_until_writer_releases")
        self.fixture.setUp()
        self.window, self.manager = self.fixture.window, self.fixture.manager
        self.raw = self.fixture.raw

    def tearDown(self):
        self.fixture.tearDown()

    def reading(self, kind, *, novel=True, source="clipboard", score=.99):
        mod = (f"30% increased Local {kind.title()} Audit Rewards in Map" if novel
               else "10% increased Pack Size in Map")
        if kind == "waystone":
            lines = ["Waystone (Tier 15)", "Waystone Drop Chance: +10%", "Item Level: 82", mod]
            copied = ["Item Class: Waystones", *lines]
        else:
            lines = ["Precursor Tablet", "Uses Remaining: 10", mod]
            copied = ["Item Class: Tablets", *lines]
        if source == "screen OCR":
            result = item_text.read_screen_tooltip(Image.new("RGB", (600, 400)),
                self.window.state["affixes"], ocr_rows=[{"text": text, "score": score} for text in lines],
                strictness=0)
        else:
            result = item_text.parse_item_text("\n".join(copied), self.window.state["affixes"])
            result["source"] = source
        return {**result, **logger.scan_context()}, modifier_value(mod)["name"]

    def publish(self, reading):
        event = {"mode": "item", "result": reading, "error": ""}
        self.manager._publish_event(event, self.raw)
        return event

    def count_affix(self, name):
        with logger._connect() as db:
            return db.execute("SELECT count(*) FROM affixes WHERE name=?", (name,)).fetchone()[0]

    def recover_new_item(self, kind):
        reading, name = self.reading(kind)
        original = copy.deepcopy(reading)
        event = self.publish(reading)
        undo = logger.undo_action()
        with patch.object(logger, "add_affixes", wraps=logger.add_affixes) as add, \
                patch.object(self.window, "_deliver_hotkey_event", wraps=self.window._deliver_hotkey_event) as deliver:
            with logger._connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                self.window.poll()
                self.fixture.now += 60
                self.window.poll()
                self.assertIs(self.manager.next_event(), event)
                self.assertEqual(self.manager.image(event["id"]), self.raw)
                self.assertEqual(self.count_affix(name), 0)
                self.assertNotIn("_prepared_tablet_modifiers", event)
                deliver.assert_not_called()
            self.fixture.now += 1
            self.window.poll()
            self.window.poll()
        deliver.assert_called_once()
        successful_names = [entry for call in add.call_args_list for entry in call.args[0]]
        # Busy attempts are rolled back; the resulting catalog and UI each own one entry.
        self.assertTrue(successful_names)
        self.assertEqual(self.count_affix(name), 1)
        self.assertEqual(self.window.state["affixes"].count(name), 1)
        self.assertEqual(logger.undo_action(), undo)
        self.assertEqual(self.window.pending_review_kind, kind)
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(self.window._hotkey_presentation_retry)
        self.assertNotIn("_prepared_tablet_modifiers", event)
        self.assertEqual(reading, original)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        if kind == "waystone":
            self.assertEqual(self.window.waystone.text(), str(reading["fields"]["waystone"]))
            self.assertEqual(self.window.item_rarity.text(), "")
            self.assertEqual(self.window.monster_rarity.text(), "")
            self.assertEqual(self.window.waystone_mod_fields[0].text(), reading["mods"][0])
        else:
            self.assertEqual(self.window.tablet_values[0].text(), "30")
            self.assertEqual(self.window.tablet_raw_mods[0], reading["mods"])
            self.assertIn("Added 1 new affix name(s)", self.window.tablet_scan_status.text())
            self.assertEqual(logger.tablet_next_slot(), 1)

    def test_new_waystone_affix_waits_without_losing_fields_or_undo(self):
        self.recover_new_item("waystone")

    def test_new_tablet_affix_waits_then_preserves_original_added_name_feedback(self):
        self.recover_new_item("tablet")

    def test_tablet_preparation_runs_once_and_is_consumed_once(self):
        reading, name = self.reading("tablet")
        event = self.publish(reading)
        with patch.object(self.window, "_prepare_tablet_modifiers", wraps=self.window._prepare_tablet_modifiers) as prepare:
            self.window.poll()
            self.window.poll()
        prepare.assert_called_once_with(reading)
        self.assertEqual(self.count_affix(name), 1)
        self.assertIn("Added 1 new affix name(s)", self.window.tablet_scan_status.text())
        self.assertNotIn("_prepared_tablet_modifiers", event)

    def test_known_items_stage_while_writer_busy_without_learning_or_retry(self):
        for kind in ("waystone", "tablet"):
            with self.subTest(kind=kind):
                reading, _ = self.reading(kind, novel=False)
                event = self.publish(reading)
                with logger._connect() as writer:
                    writer.execute("BEGIN IMMEDIATE")
                    self.window.poll()
                    self.assertEqual(self.window.pending_review_kind, kind)
                    self.assertEqual(self.window._latest_hotkey, event["id"])
                    self.assertIsNone(self.window._hotkey_presentation_retry)
                self.assertIsNone(self.manager.next_event())
                self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_low_confidence_screen_modifiers_do_not_gain_discovery_on_retry(self):
        for kind in ("waystone", "tablet"):
            with self.subTest(kind=kind):
                reading, name = self.reading(kind, source="screen OCR", score=.45)
                self.publish(reading)
                with logger._connect() as writer:
                    writer.execute("BEGIN IMMEDIATE")
                    self.window.poll()
                self.assertEqual(self.count_affix(name), 0)
                self.assertEqual(self.window.pending_review_kind, kind)
                self.assertIsNone(self.window._hotkey_presentation_retry)
                self.assertIsNone(self.manager.next_event())

    def test_high_confidence_screen_tablet_keeps_source_rows_and_feedback(self):
        reading, name = self.reading("tablet", source="screen OCR")
        original = copy.deepcopy(reading)
        self.publish(reading)
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
        self.fixture.now += 1
        self.window.poll()
        self.assertEqual(self.count_affix(name), 1)
        self.assertEqual(reading, original)
        self.assertIn("Added 1 new affix name(s)", self.window.tablet_scan_status.text())

    def check_late_writer(self, kind):
        self.window.set_auto_all(True)
        reading, name = self.reading(kind)
        event = self.publish(reading)
        original = self.window._deliver_hotkey_event

        def present(event, raw):
            with logger._connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                original(event, raw)

        with patch.object(self.window, "_deliver_hotkey_event", side_effect=present) as deliver:
            self.window.poll()
            self.fixture.now += 30
            self.window.poll()
        deliver.assert_called_once()
        self.assertEqual(self.count_affix(name), 1)
        self.assertEqual(self.window.pending_review_kind, kind)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertIsNone(self.window._hotkey_presentation_retry)
        self.assertIsNone(self.manager.next_event())
        self.assertNotIn("_prepared_tablet_modifiers", event)
        self.window.approve_scan_button.click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(self.count_affix(name), 1)

    def test_late_waystone_save_contention_keeps_manual_review_without_replaying(self):
        self.check_late_writer("waystone")

    def test_late_tablet_save_contention_keeps_manual_review_without_replaying(self):
        self.check_late_writer("tablet")

    def test_full_tablet_capacity_prevents_discovery_and_writer_wait(self):
        known, _ = self.reading("tablet", novel=False)
        for _ in range(4):
            logger.save_scanned_tablet(known["matches"], known["mods"])
        self.window.refresh()
        reading, name = self.reading("tablet")
        event = self.publish(reading)
        with patch.object(self.window, "_prepare_tablet_modifiers", wraps=self.window._prepare_tablet_modifiers) as prepare:
            with logger._connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                self.window.poll()
        prepare.assert_not_called()
        self.assertEqual(self.count_affix(name), 0)
        self.assertEqual(self.window._latest_hotkey, event["id"])
        self.assertIn("Four tablets", self.window.tablet_scan_status.text())
        self.assertIsNone(self.window._hotkey_presentation_retry)
        self.assertFalse(self.window.approve_scan_button.isEnabled())

    def test_invalid_tablet_strictness_prevents_discovery(self):
        reading, name = self.reading("tablet")
        reading["_ocr_strictness"] = 101
        self.publish(reading)
        with patch.object(self.window, "_prepare_tablet_modifiers") as prepare, \
                patch.object(self.window, "error") as error:
            self.window.poll()
        prepare.assert_not_called()
        error.assert_called_once()
        self.assertEqual(self.count_affix(name), 0)
        self.assertIsNone(self.manager.next_event())

    def test_malformed_waystone_property_shape_never_teaches_affixes(self):
        for malformed in ("absent", "none", "missing_property"):
            with self.subTest(shape=malformed):
                reading, name = self.reading("waystone")
                if malformed == "absent":
                    del reading["fields"]
                elif malformed == "none":
                    reading["fields"] = None
                else:
                    del reading["fields"]["waystone"]
                self.publish(reading)
                with patch.object(self.window, "_discover_waystone_affixes") as discover, \
                        patch.object(self.window, "error") as error:
                    self.window.poll()
                discover.assert_not_called()
                error.assert_called_once()
                self.assertEqual(self.count_affix(name), 0)
                self.assertIsNone(self.manager.next_event())

    def test_waiting_item_rejects_changed_expedition_before_backoff_ends(self):
        reading, name = self.reading("tablet")
        self.publish(reading)
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
        logger.save_settings({"expedition": 2})
        self.fixture.now += .01
        with patch.object(self.window, "error") as error:
            self.window.poll()
        self.assertTrue(any("expedition" in str(call.args[0]) for call in error.call_args_list))
        self.assertEqual(self.count_affix(name), 0)
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(self.window._hotkey_presentation_retry)

    def test_new_map_cancels_waiting_item_before_learning(self):
        reading, name = self.reading("waystone")
        self.publish(reading)
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
        self.window.finish_map()
        self.fixture.now += 1
        self.window.poll()
        self.assertEqual(self.count_affix(name), 0)
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(self.window._hotkey_presentation_retry)

    def test_close_cancels_waiting_item_without_learning_or_callback(self):
        reading, name = self.reading("tablet")
        self.publish(reading)
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
        self.window.close()
        self.fixture.now += 1
        self.window.poll()
        self.assertEqual(self.count_affix(name), 0)
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(self.window._hotkey_presentation_retry)

    def test_waiting_item_restores_hidden_hud_before_replacing_review(self):
        reading, _ = self.reading("waystone")
        self.window._overlay_enabled = True
        self.window.show_overlay()
        self.fixture.app.processEvents()
        self.window._hide_overlay_capture(None)
        self.assertTrue(self.window.isMinimized())
        self.publish(reading)
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
            self.assertFalse(self.window.isMinimized())
            self.assertIsNone(self.window.pending_review_kind)
            self.assertIn("database is busy", self.window.statusBar().currentMessage())
        self.fixture.now += 1
        self.window.poll()
        self.assertEqual(self.window.pending_review_kind, "waystone")

    def test_pending_remnant_guard_precedes_item_learning(self):
        self.window.show_result("opened", self.fixture.opened(), self.raw)
        reading, name = self.reading("tablet")
        self.publish(reading)
        with patch.object(self.window, "error") as error:
            self.window.poll()
        self.assertTrue(any("pending remnant" in str(call.args[0]) for call in error.call_args_list))
        self.assertEqual(self.count_affix(name), 0)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertIsNone(self.manager.next_event())

    def test_item_capture_error_preserves_original_error_and_skips_discovery(self):
        event = {"mode": "item", "result": None, "error": "Captured tooltip unavailable"}
        self.manager._publish_event(event, None)
        with patch.object(self.window, "_discover_waystone_affixes") as discover, \
                patch.object(self.window, "_prepare_tablet_modifiers") as prepare, \
                patch.object(self.window, "error") as error:
            self.window.poll()
        discover.assert_not_called()
        prepare.assert_not_called()
        error.assert_called_once_with("Captured tooltip unavailable")

    def test_unsupported_item_kind_keeps_original_callback_call_shape(self):
        reading = {"kind": "other", **logger.scan_context()}
        self.publish(reading)
        with patch.object(self.window, "_hover_item_read") as present, \
                patch.object(self.window, "_discover_waystone_affixes") as discover, \
                patch.object(self.window, "_prepare_tablet_modifiers") as prepare:
            self.window.poll()
        present.assert_called_once_with(reading, self.raw)
        discover.assert_not_called()
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
