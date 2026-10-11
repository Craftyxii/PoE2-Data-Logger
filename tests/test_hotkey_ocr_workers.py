"""Verify shortcut capture ownership against real killable recognition children."""

from concurrent.futures import Future
from dataclasses import replace
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, ocr_runtime, ocr_workers, store
from PoE2_Data_Logger.platform import hotkey


def _blocked_reader(pid_file):
    """Expose ownership and emulate a native recognition call that cannot finish by itself."""
    Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    while True:
        time.sleep(.05)


def _finished_reader():
    """Return a usable result from the actual child without loading OCR models."""
    return {"positions": [2], "runes": ["Ward"], "can_use": True, "pid": os.getpid()}


class HotkeyOCRWorkerTests(unittest.TestCase):
    """Exercise timeout, selective cancellation and fallback deadlines at their actual capture boundary."""

    def setUp(self):
        """Create an isolated profile and reserve captures without invoking screenshot or game adapters."""
        ocr_workers.shutdown(wait=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-hotkey-worker-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.manager = hotkey.HotkeyManager(supported=False)
        self.image = Image.new("RGB", (100, 120), (20, 30, 40))
        self.threads = []

    def tearDown(self):
        """Cancel owned work, reap test readers and restore the caller's profile."""
        self.manager.cancel_capture()
        ocr_workers.shutdown(wait=True)
        for thread in self.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        if self.manager._capture_owner is not None:
            self.manager._finish_capture(None, None, self.manager._capture_owner)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def reserve(self, mode="propagation"):
        """Reserve the production capture revision and original deadline while holding its screenshot adapter."""
        with patch.object(self.manager, "_capture"):
            self.manager.capture(mode)
        return self.manager._capture_owner

    def work(self, function, *args):
        """Create an importable test recognizer that uses the production process transport."""
        return ocr_workers.Request(function, args, {}, str(store.DATA_DIR),
                                   ocr_runtime.active_threads(), time.monotonic() + 90)

    def wait_until(self, predicate):
        """Bound waiting for child startup or reaping without leaving abandoned native work."""
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def start_blocked(self, revision):
        """Run the real capture completion path while only replacing recognition with a blocked child."""
        path = Path(self.tmp.name) / "blocked.pid"
        job = self.work(_blocked_reader, str(path))
        with patch.object(ocr_workers, "request", return_value=job):
            thread = threading.Thread(target=self.manager._read_capture,
                                      args=("propagation", "propagation", None, self.image, None),
                                      kwargs={"revision": revision, "strictness_values": {}}, daemon=True)
            self.threads.append(thread)
            thread.start()
            self.wait_until(lambda: path.exists() and bool(path.read_text(encoding="ascii")))
        return thread, int(path.read_text(encoding="ascii")), self.manager._ocr_capture_future

    def test_expiration_kills_exact_reader_and_late_completion_cannot_release_retry(self):
        """A shortcut timeout kills native recognition, preserves its error and cannot release the next capture."""
        old = self.reserve()
        thread, pid, future = self.start_blocked(old)
        self.assertTrue(future.running())
        self.manager._expire_capture("propagation", old)
        timeout = self.manager.status()["latest"]
        self.assertIn("timed out", timeout["error"])
        current = self.reserve()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(future.cancelled())
        self.assertEqual(self.manager._capture_owner, current)
        self.assertTrue(self.manager._capture_lock.locked())
        self.assertEqual(self.manager.status()["latest"], timeout)
        self.wait_until(lambda: pid not in {p.pid for p in multiprocessing.active_children()})
        with patch.object(ocr_workers, "request", return_value=self.work(_finished_reader)):
            self.manager._read_capture("propagation", "propagation", None, self.image, None,
                                       revision=current, strictness_values={})
        latest = self.manager.status()["latest"]
        self.assertEqual(latest["error"], "")
        self.assertEqual(latest["result"]["positions"], [2])
        self.assertNotEqual(latest["result"]["pid"], pid)
        self.assertFalse(self.manager._capture_lock.locked())

    def test_selective_cancellation_preserves_unrelated_active_reader(self):
        """Rejecting a remnant does not terminate the active propagation child, but matching cancellation does."""
        revision = self.reserve()
        thread, pid, future = self.start_blocked(revision)
        self.manager.cancel_capture(modes=("opened", "seed", "both"))
        self.assertEqual(self.manager._capture_revision, revision)
        self.assertFalse(future.done())
        self.assertIn(pid, {p.pid for p in multiprocessing.active_children()})
        self.manager.cancel_capture(modes=("propagation",))
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(future.cancelled())
        self.assertIsNone(self.manager.status()["latest"])
        self.assertFalse(self.manager._capture_lock.locked())
        self.wait_until(lambda: pid not in {p.pid for p in multiprocessing.active_children()})

    def test_all_recognition_stages_share_original_admission_deadline(self):
        """Fallback stages inherit the first capture deadline rather than receiving a fresh timeout each time."""
        revision = self.reserve()
        deadline = self.manager._capture_deadline
        descriptor = replace(self.work(_finished_reader), deadline=deadline + 600)
        completed = Future()
        completed.set_result({"recognized": True})
        with patch.object(ocr_workers, "request", return_value=descriptor), \
                patch.object(ocr_workers, "submit", return_value=completed) as submit:
            for _ in range(3):
                self.assertEqual(self.manager._ocr_capture_call(revision, _finished_reader), {"recognized": True})
        self.assertEqual(len(submit.call_args_list), 3)
        self.assertTrue(all(call.args[0].deadline == deadline for call in submit.call_args_list))
        self.assertIsNone(self.manager._ocr_capture_future)
        self.assertIsNone(self.manager._ocr_capture_revision)
        self.assertEqual(self.manager._capture_owner, revision)
        self.manager._finish_capture(None, None, revision)


if __name__ == "__main__":
    unittest.main()
