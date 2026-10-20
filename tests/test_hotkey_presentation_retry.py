"""Retain recognized hotkey remnants during writer contention without replaying saves."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui import native_desktop
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class HotkeyPresentationRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="poe2-hotkey-presentation-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.directory.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        self.manager = HotkeyManager(supported=False)
        self.hotkey = patch.object(service, "HOTKEY", self.manager)
        self.hotkey.start()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.set_auto_all(False)
        raw = io.BytesIO()
        Image.new("RGB", (600, 400), "tan").save(raw, format="PNG")
        self.raw = raw.getvalue()
        self.now = 100.0
        self.clock = patch.object(native_desktop.time, "monotonic", side_effect=lambda: self.now)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.hotkey.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.directory.cleanup()

    def opened(self, family=50):
        with logger._connect() as db:
            names = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=?", (family,)).fetchone()[0])
        resolved = logger.resolve(names[0], names[1] if len(names) > 1 else None, family)
        sockets = resolved["rows"][0]["sockets"]
        return {"mode": "opened", "status": "Review the opened rewards before logging.",
                "can_use": True, "family": f"Family {family}", "candidates": [family],
                "sockets": sockets, "recipe_sockets": sockets, "socket_source": "opened icons",
                "first_line_gap": 60, "list_complete": False,
                "first_recipe": names[0], "next_recipe": names[1] if len(names) > 1 else None,
                "opened_recipes": [{"recipe": name, "raw": name, "ocr_score": .99, "match_score": 1}
                                   for name in names[:2]], **logger.scan_context()}

    def seed(self):
        with logger._connect() as db:
            stage = dict(db.execute("SELECT * FROM seed_states WHERE family=3 LIMIT 1").fetchone())
        return {"mode": "seed", "remnants": [{"sockets": stage["sockets"],
                "seed_slot": stage["seed_slot"], "seed_rune": stage["seed_rune"],
                "family": "Family 3", "candidates": [3], "can_commit": True}], **logger.scan_context()}

    def propagation(self):
        with logger._connect() as db:
            combo = db.execute("SELECT combo FROM recipes WHERE name=?", ("Medved's Saga",)).fetchone()[0]
        return {"mode": "propagation", "can_use": True, "runes": [combo.split("+")[0].strip()],
                "positions": [1], "selected_recipe": "Medved's Saga", **logger.scan_context()}

    def publish(self, result, *, paired=False):
        event = {"mode": result["mode"], "result": result, "error": ""}
        self.manager._publish_event(event, self.raw, paired=paired)
        return event

    def hold_for_writer(self, result=None):
        event = self.publish(result or self.opened())
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
        self.assertIs(self.manager.next_event(), event)
        self.assertEqual(self.window._latest_hotkey, 0)
        return event

    def test_opened_result_and_png_stay_owned_until_writer_releases(self):
        result = self.opened()
        event = self.publish(result)
        with patch.object(self.window, "_deliver_hotkey_event", wraps=self.window._deliver_hotkey_event) as deliver:
            with logger._connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                self.window.poll()
                self.assertIs(self.manager.next_event(), event)
                self.assertEqual(self.manager.image(event["id"]), self.raw)
                self.assertEqual(self.window._latest_hotkey, 0)
                self.assertIsNone(self.window.pending_review_kind)
                deliver.assert_not_called()
            self.now += 1
            self.window.poll()
            self.window.poll()
        deliver.assert_called_once()
        self.assertIsNone(self.manager.next_event())
        self.assertIs(self.window.results["opened"], result)
        self.assertEqual(self.window.images["opened"], self.raw)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertIsNone(self.window._hotkey_presentation_retry)

    def test_visible_seed_admission_recovers_without_losing_reading(self):
        reading = self.seed()
        self.hold_for_writer(reading)
        self.now += 1
        self.window.poll()
        self.assertIs(self.window.results["seed"], reading)
        self.assertEqual(self.window.pending_review_kind, "seed")
        self.assertEqual(self.window.seed_table.rowCount(), 1)
        self.assertIsNotNone(logger.get_state()["ocr_pending"])
        self.assertIsNone(self.manager.next_event())

    def test_waiting_more_than_ten_seconds_never_discards_recognized_result(self):
        event = self.publish(self.opened())
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
            self.now += 60
            self.window.poll()
            self.assertIs(self.manager.next_event(), event)
            self.assertEqual(self.manager.image(event["id"]), self.raw)
            self.assertEqual(self.window._latest_hotkey, 0)
            self.assertIsNotNone(self.window._hotkey_presentation_retry)
            self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.now += 1
        self.window.poll()
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertIsNone(self.manager.next_event())

    def test_poll_backoff_is_nonblocking_and_caps_at_one_second(self):
        self.publish(self.opened())
        with patch.object(self.window, "_prepare_remnant_result", wraps=self.window._prepare_remnant_result) as admit:
            with logger._connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                self.window.poll()
                self.assertEqual(admit.call_count, 1)
                self.now += .05
                self.window.poll()
                self.assertEqual(admit.call_count, 1)
                self.now += .06
                self.window.poll()
                self.assertEqual(admit.call_count, 2)
                for _ in range(6):
                    self.now += 1.1
                    self.window.poll()
                pending = self.window._hotkey_presentation_retry
                self.assertGreater(pending["next_attempt"], self.now)
                self.assertLessEqual(pending["next_attempt"] - self.now, 1)
        self.assertIsNotNone(self.manager.next_event())

    def test_writer_recovery_autosaves_once_and_does_not_repeat_family_scan(self):
        self.window.set_auto_all(True)
        reading = self.opened()
        self.hold_for_writer(reading)
        self.now += 1
        with patch.object(self.window, "_deliver_hotkey_event", wraps=self.window._deliver_hotkey_event) as deliver:
            self.window.poll()
            self.window.poll()
            self.window.poll()
        deliver.assert_called_once()
        self.assertFalse(reading["_repeat_family_review"])
        state = logger.get_state()
        self.assertEqual(state["scan_commit_count"], 1)
        self.assertIsNone(state["ocr_pending"])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM commits WHERE kind='Remnant'").fetchone()[0], 1)

    def test_newer_recognized_remnant_replaces_old_result_without_a_fifo(self):
        self.window.set_auto_all(True)
        first, second = self.opened(50), self.opened(55)
        event1 = self.publish(first)
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
            self.assertIs(self.window._hotkey_presentation_retry["event"], event1)
            event2 = self.publish(second)
            self.now += 30
            self.window.poll()
            self.assertIs(self.window._hotkey_presentation_retry["event"], event2)
            self.assertEqual(self.manager._events, {})
            self.assertIsNone(self.manager.image(event1["id"]))
        self.now += 1
        self.window.poll()
        self.assertEqual(self.window._latest_hotkey, event2["id"])
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.window.poll()
        self.window.poll()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertIsNone(self.manager.next_event())
        self.assertNotIn("remnant_id", first)
        self.assertEqual(second["remnant_id"], "R0001")

    def test_writer_arriving_after_admission_keeps_review_without_replaying_autosave(self):
        self.window.set_auto_all(True)
        reading = self.opened()
        self.publish(reading)
        original = self.window._deliver_hotkey_event

        def present_with_competing_writer(event, raw):
            # The reservation succeeded before this writer arrived. Presentation
            # must complete once; a subsequent busy save remains a manual review.
            with logger._connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                original(event, raw)

        with patch.object(self.window, "_deliver_hotkey_event", side_effect=present_with_competing_writer) as deliver:
            self.window.poll()
            self.now += 30
            self.window.poll()
        deliver.assert_called_once()
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertIs(self.window.results["opened"], reading)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertIsNone(self.window._hotkey_presentation_retry)
        self.assertIsNone(self.manager.next_event())
        self.window.approve_remnant_scan()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_queued_propagation_remains_manually_approved_after_remnant_retry(self):
        opened, prop = self.opened(), self.propagation()
        self.publish(opened, paired=True)
        self.publish(prop, paired=True)
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
        self.now += 1
        self.window.poll()
        reservation = logger.get_state()["ocr_pending"]
        self.window.poll()
        self.assertEqual(self.window.review_kind.property("scanKind"), "propagation")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertIsNotNone(self.window._held_remnant_review)
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.window.approve_propagation_recipe(0)
        self.assertEqual(len(logger.get_state()["chain"]), 1)
        self.assertEqual(logger.get_state()["ocr_pending"], reservation)

    def test_stale_context_is_rejected_during_backoff_without_reservation(self):
        event = self.hold_for_writer()
        with logger._connect() as db:
            logger._set_meta(db, "session_generation", logger.session_generation() + 1)
        self.now += .01
        with patch.object(self.window, "error") as error:
            self.window.poll()
        self.assertTrue(any("previous session" in str(call.args[0]) for call in error.call_args_list))
        self.assertEqual(self.window._latest_hotkey, event["id"])
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertIsNone(self.window.results["opened"])

    def test_changed_expedition_retires_stale_result_before_backoff_ends(self):
        self.hold_for_writer()
        logger.save_settings({"expedition": 2})
        self.now += .01
        with patch.object(self.window, "error") as error:
            self.window.poll()
        self.assertTrue(any("expedition" in str(call.args[0]) for call in error.call_args_list))
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_new_map_cancels_waiting_result_instead_of_rebinding_it(self):
        self.hold_for_writer()
        self.window.finish_map()
        self.now += 1
        self.window.poll()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(self.window._hotkey_presentation_retry)
        self.assertIsNone(self.window.results["opened"])
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_undo_cancels_waiting_result_without_a_late_reservation(self):
        self.hold_for_writer()
        self.window.undo_map()
        self.now += 1
        self.window.poll()
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(self.window._hotkey_presentation_retry)
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_session_reset_cancels_waiting_result_even_when_map_id_is_reused(self):
        event = self.hold_for_writer()
        generation = event["result"]["_scan_generation"]
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.reset_logger()
        self.now += 1
        self.window.poll()
        self.assertNotEqual(logger.session_generation(), generation)
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_rejecting_held_remnant_discards_waiting_remnant_event(self):
        self.window.show_result("opened", self.opened(), self.raw)
        self.hold_for_writer(self.opened(55))
        self.window.reject_review()
        self.now += 1
        self.window.poll()
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(self.window._hotkey_presentation_retry)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertIsNone(self.window.pending_review_kind)

    def test_close_retires_waiting_result_and_has_no_retry_timer(self):
        self.hold_for_writer()
        self.window.close()
        self.now += 1
        self.window.poll()
        self.assertTrue(self.window._closed)
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(self.window._hotkey_presentation_retry)
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_busy_wait_restores_hidden_hud_without_changing_existing_review(self):
        prior = self.opened(55)
        self.window.show_result("opened", prior, self.raw)
        self.window._overlay_enabled = True
        self.window.show_overlay()
        self.app.processEvents()
        self.window._hide_overlay_capture(None)
        self.assertTrue(self.window.isMinimized())
        self.assertIsNotNone(self.window._overlay_capture_restore)
        event = self.publish(self.opened(50))
        with logger._connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.window.poll()
            self.assertFalse(self.window.isMinimized())
            self.assertIs(self.window.results["opened"], prior)
            self.assertIs(self.manager.next_event(), event)
            self.assertIn("database is busy", self.window.statusBar().currentMessage())
        self.now += 1
        self.window.poll()
        self.assertIs(self.window.results["opened"], event["result"])

    def test_nonbusy_admission_error_is_reported_and_not_retried(self):
        event = self.publish(self.opened())
        with patch.object(self.window, "_prepare_remnant_result", side_effect=ValueError("Invalid capture context")), \
                patch.object(self.window, "error") as error:
            self.window.poll()
            self.window.poll()
        error.assert_called_once_with("Invalid capture context")
        self.assertEqual(self.window._latest_hotkey, event["id"])
        self.assertIsNone(self.manager.next_event())
        self.assertIsNone(logger.get_state()["ocr_pending"])


if __name__ == "__main__":
    unittest.main()
