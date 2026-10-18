"""Preserve remnant/propagation requests, evidence and frozen ownership across busy capture."""

import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.platform import hotkey


class DeferredPropagationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-deferred-propagation-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        self.source = Image.new("RGB", (580, 730), (20, 40, 60))
        with logger._connect() as db:
            logger._set_meta(db, "live_region", {"x": 0, "y": 100, "w": 580, "h": 730})
            logger._set_meta(db, "propagation_region", {"x": 0, "y": 0, "w": 600, "h": 800})
        self.entered, self.release = threading.Event(), threading.Event()
        self.threads = []
        self.manager = hotkey.HotkeyManager(supported=False,
            grabber=Mock(side_effect=lambda **_: self.source.copy()),
            readers={"opened": Mock(return_value={"mode": "opened", "family": 50}),
                     "propagation": Mock(return_value={"runes": ["Ward"], "positions": [2], "can_use": True})})
        self.manager.before_capture = Mock()
        original = self.manager._capture
        def capture(*args):
            if threading.current_thread() is not threading.main_thread():
                self.threads.append(threading.current_thread())
            return original(*args)
        self.capture_patch = patch.object(self.manager, "_capture", side_effect=capture)
        self.capture_patch.start()

    def tearDown(self):
        self.manager.cancel_capture()
        self.release.set()
        for _ in range(3):
            for thread in list(self.threads):
                thread.join(2)
        self.assertTrue(all(not thread.is_alive() for thread in self.threads))
        self.capture_patch.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def block_opened(self, picture):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("Test reader was not released.")
        # Injected legacy readers receive a temporary JPEG path; native readers
        # receive PIL. Neither may own the queued lossless image.
        if isinstance(picture, Image.Image):
            picture.putpixel((0, 0), (255, 0, 0))
        else:
            with Image.open(picture) as decoded:
                decoded.load()
                decoded.putpixel((0, 0), (255, 0, 0))
        return {"mode": "opened", "family": 50}

    def start_remnant(self, block_grab=False):
        if block_grab:
            def grab(**_):
                self.entered.set()
                if not self.release.wait(5):
                    raise RuntimeError("Test screenshot was not released.")
                return self.source.copy()
            self.manager.grabber.side_effect = grab
        else:
            self.manager.readers["opened"].side_effect = self.block_opened
        self.manager.capture("remnant", background=True)
        self.assertTrue(self.entered.wait(2))

    def wait_finished(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self.manager._lock:
                done = (self.manager._capture_owner is None and
                        self.manager._capture_adapter_owner is None and
                        self.manager._deferred_propagation is None)
            if done:
                return
            self.release.wait(.005)
            time.sleep(.005)
        self.fail("Capture did not finish.")

    def test_following_prop_key_preserves_both_readings_and_each_screenshot(self):
        self.manager.grabber.side_effect = None
        self.manager.grabber.return_value = self.source
        self.start_remnant()
        self.source.putpixel((0, 0), (255, 0, 0))
        self.manager.capture("propagation", background=True)
        self.assertIsNotNone(self.manager._deferred_propagation)
        self.manager.readers["propagation"].assert_not_called()
        self.release.set()
        self.wait_finished()
        first = self.manager.next_event()
        second = self.manager.next_event(first["id"])
        self.assertEqual([first["mode"], second["mode"]], ["opened", "propagation"])
        self.assertEqual([first["error"], second["error"]], ["", ""])
        self.assertEqual(first["result"]["family"], 50)
        self.assertTrue(second["result"]["can_use"])
        self.assertNotIn("approved", second["result"])
        self.assertEqual(second["result"]["_capture_map_id"], "M0001")
        self.assertEqual(second["result"]["_capture_expedition"], 1)
        self.assertEqual(second["result"]["_scan_generation"], logger.session_generation())
        frozen = Image.open(io.BytesIO(self.manager.image(second["id"])))
        self.assertEqual(frozen.format, "PNG")
        self.assertEqual(frozen.getpixel((0, 0)), (20, 40, 60))
        self.assertIsNotNone(self.manager.image(first["id"]))
        self.manager.grabber.assert_called_once()
        self.manager.before_capture.assert_called_once()
        self.manager.ack_event(first["id"])
        self.assertIsNone(self.manager.image(first["id"]))
        self.assertIs(self.manager.next_event(), second)
        self.manager.ack_event(second["id"])
        self.assertIsNone(self.manager.next_event())
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM new_export").fetchone()[0], 0)
            self.assertIsNone(logger._meta(db, "ocr_pending"))

    def test_key_pressed_before_grab_returns_waits_for_same_frozen_image(self):
        self.start_remnant(block_grab=True)
        self.manager.capture("propagation", background=True)
        self.release.set()
        self.wait_finished()
        self.manager.grabber.assert_called_once()
        self.manager.readers["propagation"].assert_called_once()
        self.assertEqual(self.manager.next_event()["mode"], "opened")
        self.assertEqual(self.manager.status()["latest"]["mode"], "propagation")

    def test_repeated_pending_prop_key_is_bounded_to_one_request(self):
        self.start_remnant()
        for _ in range(20):
            self.manager.capture("propagation", background=True)
        self.release.set()
        self.wait_finished()
        self.assertEqual(self.manager._sequence, 2)
        self.manager.readers["propagation"].assert_called_once()
        self.manager.grabber.assert_called_once()

    def test_completed_remnant_cache_is_one_shot_then_prop_captures_independently(self):
        self.manager.capture("remnant")
        self.manager.capture("propagation")
        self.assertEqual(self.manager.grabber.call_count, 1)
        self.manager.capture("propagation")
        self.assertEqual(self.manager.grabber.call_count, 2)
        self.assertEqual(self.manager.readers["propagation"].call_count, 2)
        self.assertEqual(self.manager.before_capture.call_count, 2)

    def test_standalone_prop_needs_no_remnant(self):
        self.manager.capture("propagation")
        self.manager.readers["opened"].assert_not_called()
        self.manager.grabber.assert_called_once()
        self.assertEqual(self.manager.status()["latest"]["error"], "")
        self.assertIsNone(self.manager._remnant_capture)

    def test_another_capture_invalidates_completed_remnant_image(self):
        self.manager.capture("remnant")
        with logger._connect() as db:
            logger._set_meta(db, "inventory_region", {"x": 0, "y": 0, "w": 400, "h": 300})
        self.manager.capture("currency")
        self.manager.capture("propagation")
        self.assertEqual(self.manager.grabber.call_count, 3)

    def test_changed_expedition_invalidates_completed_cache_and_captures_current_pixels(self):
        self.manager.capture("remnant")
        logger.save_settings({"expedition": 2})
        self.source = Image.new("RGB", (580, 730), (70, 80, 90))
        self.manager.capture("propagation")
        event = self.manager.status()["latest"]
        self.assertEqual(event["error"], "")
        self.assertEqual(event["result"]["_capture_expedition"], 2)
        self.assertEqual(self.manager.grabber.call_count, 2)
        self.assertEqual(self.manager.readers["propagation"].call_args.args[0].getpixel((0, 0)), (70, 80, 90))

    def test_changed_context_cannot_rebind_queued_old_image(self):
        for change in ("expedition", "generation", "map", "finished"):
            with self.subTest(change=change):
                # Each subtest uses a separate bounded capture pair and acknowledges its evidence.
                self.release.clear()
                self.entered.clear()
                self.start_remnant()
                self.manager.capture("propagation", background=True)
                if change == "expedition":
                    logger.save_settings({"expedition": logger.get_state()["settings"]["expedition"] + 1})
                elif change == "generation":
                    with logger._connect() as db:
                        logger._set_meta(db, "session_generation", logger._meta(db, "session_generation", 0) + 1)
                elif change == "map":
                    logger.finish_map(0, 0, 0, 0)
                    logger.start_map()
                else:
                    logger.finish_map(0, 0, 0, 0)
                self.release.set()
                self.wait_finished()
                event = self.manager.status()["latest"]
                self.assertEqual(event["mode"], "propagation")
                self.assertTrue(event["error"])
                self.assertIsNone(event["result"])
                self.manager.readers["propagation"].assert_not_called()
                for ident in list(self.manager._events):
                    self.manager.ack_event(ident)

    def test_prop_only_cancel_preserves_remnant_without_launching_queued_prop(self):
        self.start_remnant()
        self.manager.capture("propagation", background=True)
        revision = self.manager._capture_revision
        self.manager.cancel_capture(modes=("propagation",))
        self.assertEqual(self.manager._capture_revision, revision)
        self.release.set()
        self.wait_finished()
        self.assertEqual(self.manager.status()["latest"]["mode"], "opened")
        self.manager.readers["propagation"].assert_not_called()

    def test_global_cancel_discards_queued_prop_and_late_grab(self):
        self.start_remnant(block_grab=True)
        self.manager.capture("propagation", background=True)
        self.manager.cancel_capture()
        self.release.set()
        self.wait_finished()
        self.assertIsNone(self.manager.next_event())
        self.manager.readers["propagation"].assert_not_called()
        self.assertIsNone(self.manager._remnant_capture)

    def test_remnant_only_cancel_preserves_queued_prop_from_already_frozen_image(self):
        self.start_remnant()
        self.manager.capture("propagation", background=True)
        self.manager.cancel_capture(modes=("opened", "seed", "both"))
        self.assertIsNotNone(self.manager._capture_adapter_owner)
        self.manager.readers["propagation"].assert_not_called()
        self.release.set()
        self.wait_finished()
        self.assertEqual(self.manager.next_event()["mode"], "propagation")
        self.assertEqual(self.manager.next_event()["error"], "")
        self.manager.grabber.assert_called_once()

    def test_remnant_cancel_before_grab_returns_takes_fresh_prop_after_adapter_exits(self):
        self.start_remnant(block_grab=True)
        self.manager.capture("propagation", background=True)
        self.manager.cancel_capture(modes=("opened",))
        self.manager.readers["propagation"].assert_not_called()
        # The old adapter returns the old frame; the subsequent capture gets fresh pixels.
        self.manager.grabber.side_effect = lambda **kw: Image.new("RGB", (580, 730), (70, 80, 90))
        self.release.set()
        self.wait_finished()
        self.assertEqual(self.manager.grabber.call_count, 2)
        self.assertEqual(self.manager.readers["propagation"].call_args.args[0].getpixel((0, 0)), (70, 80, 90))
        self.assertEqual(self.manager.next_event()["mode"], "propagation")
        self.assertEqual(self.manager.next_event()["error"], "")

    def test_retained_prop_has_a_deadline_even_if_cancelled_adapter_never_returns(self):
        self.start_remnant(block_grab=True)
        self.manager.capture("propagation", background=True)
        pending = self.manager._deferred_propagation
        self.manager.cancel_capture(modes=("opened",))
        self.manager._expire_deferred_propagation(pending)
        self.assertIn("timed out", self.manager.next_event()["error"])
        self.release.set()
        self.wait_finished()
        self.manager.readers["propagation"].assert_not_called()

    def test_queued_fresh_capture_reports_focus_loss_after_overlay_preparation(self):
        active = [True]
        self.manager.focused = lambda: active[0]
        self.start_remnant(block_grab=True)
        self.manager.capture("propagation", background=True)
        self.manager.cancel_capture(modes=("opened",))
        self.manager.before_capture.side_effect = lambda: active.__setitem__(0, False)
        self.release.set()
        self.wait_finished()
        event = self.manager.next_event()
        self.assertEqual(event["mode"], "propagation")
        self.assertIn("lost focus before capture", event["error"])
        self.manager.grabber.assert_called_once()
        self.manager.readers["propagation"].assert_not_called()

    def test_deferred_context_read_failure_preserves_active_remnant_and_reports_prop(self):
        self.start_remnant()
        with patch.object(logger, "scan_context", side_effect=RuntimeError("context unavailable")):
            self.manager.capture("propagation", background=True)
        failed = self.manager.next_event()
        self.assertEqual(failed["mode"], "propagation")
        self.assertIn("context unavailable", failed["error"])
        self.assertEqual(self.manager._active_capture_mode, "opened")
        self.release.set()
        self.wait_finished()
        self.assertEqual(self.manager.status()["latest"]["mode"], "opened")
        self.manager.readers["propagation"].assert_not_called()

    def test_completed_cache_validation_failure_releases_admission(self):
        self.manager.capture("remnant")
        with patch.object(logger, "validate_scan_context", side_effect=RuntimeError("context unavailable")):
            self.manager.capture("propagation")
        self.assertIn("context unavailable", self.manager.status()["latest"]["error"])
        self.assertFalse(self.manager._capture_lock.locked())
        self.manager.capture("propagation")
        self.assertEqual(self.manager.status()["latest"]["error"], "")
        self.assertEqual(self.manager.grabber.call_count, 2)

    def test_repeated_deferred_errors_reserve_capacity_for_the_active_result(self):
        self.start_remnant()
        with patch.object(logger, "scan_context", side_effect=RuntimeError("context unavailable")):
            for _ in range(20):
                self.manager.capture("propagation", background=True)
        self.assertLessEqual(len(self.manager._events), hotkey.MAX_PENDING_SCAN_EVENTS - 2)
        self.release.set()
        self.wait_finished()
        self.assertLessEqual(len(self.manager._events), hotkey.MAX_PENDING_SCAN_EVENTS)
        self.assertEqual(self.manager.status()["latest"]["mode"], "opened")

    def test_legacy_latest_consumer_does_not_fill_the_ordered_queue(self):
        manager = hotkey.HotkeyManager(supported=False, ordered_delivery=False,
            grabber=lambda **_: self.source.copy(), readers={"propagation": Mock(
                return_value={"can_use": True, "runes": ["Ward"], "positions": [2]})})
        for _ in range(12):
            manager.capture("propagation")
        self.assertEqual(len(manager._events), 0)
        latest = manager.status()["latest"]
        self.assertTrue(manager.image(latest["id"]))
        manager.enable_ordered_delivery()
        self.assertEqual(manager.next_event(), latest)
        manager.ack_event(latest["id"])
        self.assertIsNone(manager.next_event())

    def test_queued_request_keeps_frozen_strictness_after_preferences_change(self):
        with patch.object(hotkey.ocr_sensitivity, "saved_values", return_value={"propagation": 74}):
            self.start_remnant()
        self.manager.capture("propagation", background=True)
        self.release.set()
        self.wait_finished()
        result = self.manager.status()["latest"]["result"]
        self.assertEqual(result["_ocr_strictness_values"]["propagation"], 74)
        self.assertEqual(result["_ocr_strictness"], 74)

    def test_pending_result_capacity_rejects_new_work_without_dropping_old_events(self):
        self.manager.capture("remnant")
        first = self.manager.next_event()
        image = self.manager.image(first["id"])
        with patch.object(hotkey, "MAX_PENDING_SCAN_EVENTS", 2):
            self.manager.capture("remnant")
        self.manager.grabber.assert_called_once()
        self.assertIn("Pending scan results are full", self.manager.status()["error"])
        self.assertIs(self.manager.next_event(), first)
        self.assertEqual(self.manager.image(first["id"]), image)

    def test_pending_evidence_byte_budget_rejects_before_capture_without_eviction(self):
        self.manager.capture("remnant")
        first = self.manager.next_event()
        with patch.object(hotkey, "MAX_PENDING_SCAN_BYTES", hotkey.MAX_SCAN_EVIDENCE_BYTES):
            self.manager.capture("currency")
        self.manager.grabber.assert_called_once()
        self.assertIs(self.manager.next_event(), first)
        self.assertIn("Pending scan results are full", self.manager.status()["error"])

    def test_general_scan_never_uses_currency_inventory_fallback(self):
        self.manager.supported = True
        self.manager._capture_lock.acquire()
        self.manager._capture_revision = self.manager._capture_owner = 1
        picture = self.source.copy()
        aligned = picture.copy()
        aligned.info["poe2_inventory_aligned"] = True
        with patch("PoE2_Data_Logger.ocr.item_ocr.ocr_lines", return_value=[]), \
                patch("PoE2_Data_Logger.ocr.item_ocr.is_ritual_page", return_value=False), \
                patch("PoE2_Data_Logger.ocr.item_text.read_screen_tooltip", return_value=None), \
                patch("PoE2_Data_Logger.ocr.item_ocr.inventory_grid", return_value=aligned) as inventory:
            self.manager._read_capture("default", "opened", None, picture, picture,
                activity_crops={"currency": (0, 0, 580, 730)}, revision=1, strictness_values={})
        event = self.manager.status()["latest"]
        self.assertEqual(event["mode"], "opened")
        self.assertEqual(event["error"], "")
        inventory.assert_not_called()
        self.manager.readers["opened"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
