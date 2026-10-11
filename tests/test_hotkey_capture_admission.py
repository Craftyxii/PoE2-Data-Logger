"""Keep cancelled adapter work bounded while releasing scan admission and reporting retries."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.platform import hotkey


class CaptureAdmissionTests(unittest.TestCase):
    """Exercise actual capture adapters rather than simulated OCR-only reservations."""

    def setUp(self):
        """Create disposable capture settings without changing the user's logger profile."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-capture-admission-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        with logger._connect() as db:
            logger._set_meta(db, "inventory_region", {"x": 0, "y": 0, "w": 400, "h": 300})
            logger._set_meta(db, "hud_overlay", True)
        self.image = Image.new("RGB", (400, 300), (20, 30, 40))
        self.manager = hotkey.HotkeyManager(supported=False,
            grabber=Mock(return_value=self.image), readers={"opened": Mock()})
        self.release = threading.Event()
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.patches = []
        self.threads = []

    def tearDown(self):
        """Release physical test adapters before restoring storage and deleting the profile."""
        self.manager.cancel_capture()
        self.release.set()
        for thread in self.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        for mocked in reversed(self.patches):
            mocked.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def blocked(self, *args, **kwargs):
        """Expose an uninterruptible adapter that returns only when the test releases it."""
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("Test did not release its capture adapter.")
        return self.image

    def start_blocked_screenshot(self):
        """Enter a real background screenshot adapter and observe the worker's eventual exit."""
        self.manager.grabber.side_effect = self.blocked
        original = self.manager._capture

        def capture(*args):
            """Track the physical worker so teardown never restores storage underneath it."""
            if threading.current_thread() is not threading.main_thread():
                self.threads.append(threading.current_thread())
            try:
                original(*args)
            finally:
                self.finished.set()

        mocked = patch.object(self.manager, "_capture", side_effect=capture)
        self.patches.append(mocked)
        mocked.start()
        self.manager.capture("remnant", background=True)
        self.assertTrue(self.entered.wait(2))
        return self.manager._capture_owner

    def assert_retired(self):
        """Ensure cancellation has cleared logical ownership, its watchdog and reading mode."""
        self.assertIsNone(self.manager._capture_owner)
        self.assertFalse(self.manager._capture_lock.locked())
        self.assertIsNone(self.manager._capture_watchdog)
        self.assertIsNone(self.manager._capture_deadline)
        self.assertIsNone(self.manager._active_capture_mode)

    def test_cancel_blocked_screenshot_releases_admission_and_currency_retry_recovers(self):
        """Cancel Remnant promptly, report a busy Currency retry, then accept Currency after return."""
        old = self.start_blocked_screenshot()
        self.manager.cancel_capture(modes=("opened",))
        self.assert_retired()
        self.assertEqual(self.manager._capture_adapter_owner, old)
        self.assertIsNone(self.manager.status()["latest"])
        notified = Mock()
        self.manager.on_event = notified
        self.manager.capture("currency", background=True)
        busy = self.manager.status()["latest"]
        self.assertEqual(busy["mode"], "currency")
        self.assertIsNone(busy["result"])
        self.assertIn("adapter is still busy", busy["error"])
        notified.assert_called_once()
        self.assert_retired()
        self.assertEqual(self.manager.grabber.call_count, 1)
        self.release.set()
        self.assertTrue(self.finished.wait(2))
        self.assertIsNone(self.manager._capture_adapter_owner)
        self.assertIs(self.manager.status()["latest"], busy)
        self.manager.readers["opened"].assert_not_called()
        self.manager.capture("currency")
        event = self.manager.status()["latest"]
        self.assertEqual(event["error"], "")
        self.assertEqual(event["result"]["map_id"], "M0001")
        self.assertEqual(self.manager.grabber.call_count, 2)
        self.assertIsNotNone(self.manager.image(event["id"]))
        self.assert_retired()

    def test_completed_capture_allows_next_scan_from_its_notification(self):
        """Release finished adapters before publication so an immediate next scan is admitted."""
        self.manager.readers["opened"].return_value = {"remnants": []}
        events = []

        def notified():
            """Consume the completed result and immediately request the next activity capture."""
            event = self.manager.status()["latest"]
            events.append(event)
            if event["mode"] == "opened":
                self.manager.capture("currency")

        self.manager.on_event = notified
        self.manager.capture("remnant")
        self.assertEqual([event["mode"] for event in events], ["opened", "currency"])
        self.assertTrue(all(not event["error"] for event in events))
        self.assertEqual(self.manager.grabber.call_count, 2)
        self.assertIsNone(self.manager._capture_adapter_owner)
        self.assert_retired()

    def test_repeated_cancel_and_retry_cannot_accumulate_blocked_threads(self):
        """Bound twenty retries to the original physical worker while leaving Overlay usable."""
        old = self.start_blocked_screenshot()
        for index in range(20):
            self.manager.cancel_capture()
            self.manager.capture("currency", background=True)
            self.assertIn("adapter is still busy", self.manager.status()["latest"]["error"])
            self.assertEqual(self.manager._capture_adapter_owner, old)
            self.assert_retired()
        self.assertEqual(len(self.threads), 1)
        self.manager.grabber.assert_called_once()
        self.manager.capture("overlay")
        self.assertEqual(self.manager.status()["overlay_sequence"], 1)
        self.release.set()
        self.assertTrue(self.finished.wait(2))
        self.assertIsNone(self.manager._capture_adapter_owner)

    def test_timeout_does_not_release_a_still_blocked_physical_adapter(self):
        """Expire the logical deadline without allowing unlimited screenshot retries afterward."""
        old = self.start_blocked_screenshot()
        self.manager._expire_capture("opened", old)
        self.assertIn("timed out", self.manager.status()["latest"]["error"])
        self.assert_retired()
        self.manager.capture("currency", background=True)
        busy = self.manager.status()["latest"]
        self.assertIn("adapter is still busy", busy["error"])
        self.assertEqual(self.manager._capture_adapter_owner, old)
        self.assertEqual(len(self.threads), 1)
        self.release.set()
        self.assertTrue(self.finished.wait(2))
        self.assertIs(self.manager.status()["latest"], busy)

    def test_unrelated_mode_cancellation_preserves_current_adapter_and_deadline(self):
        """Discarding propagation cannot retire an active Remnant screenshot or its watchdog."""
        old = self.start_blocked_screenshot()
        deadline = self.manager._capture_deadline
        watchdog = self.manager._capture_watchdog
        self.manager.cancel_capture(modes=("propagation",))
        self.assertEqual(self.manager._capture_owner, old)
        self.assertTrue(self.manager._capture_lock.locked())
        self.assertEqual(self.manager._capture_deadline, deadline)
        self.assertIs(self.manager._capture_watchdog, watchdog)
        self.assertEqual(self.manager._capture_adapter_owner, old)
        self.manager.cancel_capture()
        self.release.set()
        self.assertTrue(self.finished.wait(2))

    def test_cancellation_inside_database_adapter_remains_bounded(self):
        """Retain physical ownership when capture context's database adapter has not returned."""
        original = logger._connect

        def connect():
            """Block at context acquisition without using a real database lock or timeout."""
            self.blocked()
            return original()

        with patch.object(logger, "_connect", side_effect=connect):
            thread = threading.Thread(target=self.manager.capture, args=("remnant",), daemon=True)
            self.threads.append(thread)
            thread.start()
            self.assertTrue(self.entered.wait(2))
            self.manager.cancel_capture()
            self.assert_retired()
            self.manager.capture("currency", background=True)
            self.assertIn("adapter is still busy", self.manager.status()["latest"]["error"])
            self.release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.manager.grabber.assert_not_called()
        self.manager.readers["opened"].assert_not_called()
        self.assertIsNone(self.manager._capture_adapter_owner)

    def test_cancellation_during_strictness_read_never_schedules_a_cancelled_worker(self):
        """Freeze preferences under the same bounded adapter guard and stop before scheduling after cancel."""
        def saved_values():
            """Simulate a blocked settings database adapter at admission before worker start."""
            self.blocked()
            return {}

        with patch.object(hotkey.ocr_sensitivity, "saved_values", side_effect=saved_values), \
                patch.object(self.manager, "_capture") as capture:
            thread = threading.Thread(target=self.manager.capture, args=("remnant",), daemon=True)
            self.threads.append(thread)
            thread.start()
            self.assertTrue(self.entered.wait(2))
            self.manager.cancel_capture()
            self.assert_retired()
            self.manager.capture("currency", background=True)
            self.assertIn("adapter is still busy", self.manager.status()["latest"]["error"])
            self.release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            capture.assert_not_called()
        self.assertIsNone(self.manager._capture_adapter_owner)

    def test_cancellation_inside_clipboard_adapter_does_not_submit_ocr_or_accept_more_work(self):
        """Suppress the copied item and report a busy retry while the external clipboard call remains blocked."""
        self.manager.supported = True
        self.manager.tooltip_grabber = lambda: self.image
        self.manager.hover_reader = self.blocked
        with patch.object(hotkey.sys, "platform", "linux"), \
                patch.object(self.manager, "_read_capture") as recognize:
            thread = threading.Thread(target=self.manager.capture, args=("waystone",), daemon=True)
            self.threads.append(thread)
            thread.start()
            self.assertTrue(self.entered.wait(2))
            self.manager.cancel_capture(modes=("waystone",))
            self.assert_retired()
            self.manager.capture("currency", background=True)
            self.assertIn("adapter is still busy", self.manager.status()["latest"]["error"])
            self.release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            recognize.assert_not_called()
        self.assertIsNone(self.manager._capture_adapter_owner)


if __name__ == "__main__":
    unittest.main()
