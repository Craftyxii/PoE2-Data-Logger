"""Use actual spawned readers to verify bounded admission, cancellation, reaping and recovery."""

from concurrent.futures import CancelledError, ThreadPoolExecutor, wait
from dataclasses import replace
import multiprocessing
import os
from pathlib import Path
import pickle
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PoE2_Data_Logger.core import ocr_runtime, ocr_workers, store


def _identity(delay=0):
    """Return the actual child and frozen profile preferences after an optional finite delay."""
    if delay:
        time.sleep(delay)
    return {"pid": os.getpid(), "data_dir": str(store.DATA_DIR),
            "cpu_threads": ocr_runtime.active_threads()}


def _blocked(pid_file):
    """Publish child ownership, then emulate a native reader that never returns until terminated."""
    Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    while True:
        time.sleep(.05)


def _broken(pid_file):
    """Publish ownership and exit abruptly so transport failure must release its admission."""
    Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    os._exit(7)


def _finite(pid_file, delay):
    """Publish child ownership, then finish a reading that takes a while."""
    Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    time.sleep(delay)
    return os.getpid()


def _validation_error():
    """Raise an ordinary reader validation error without making its healthy process unusable."""
    raise ValueError("Confirm the visible quantity.")


def _blocked_with_tempfile(evidence_file):
    """Expose a recognizer-created temporary screenshot before blocking so parent cleanup is measurable."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image:
        image.write(b"captured screenshot")
        temporary_path = image.name
    Path(evidence_file).write_text(temporary_path, encoding="utf-8")
    while True:
        time.sleep(.05)


class OCRWorkerRecoveryTests(unittest.TestCase):
    """Check real child lifecycle rather than replacing executor futures or hiding blocked readers."""

    def setUp(self):
        """Create a profile-bound transport fixture without loading any recognition models."""
        ocr_workers.shutdown(wait=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ocr-recovery-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        self.thread_patch = patch.object(ocr_runtime, "_ACTIVE_THREADS", 4)
        self.thread_patch.start()
        # A cancelled reading may finish in the background; a blocked one is stopped after this.
        self.grace_patch = patch.object(ocr_workers, "CANCELLED_GRACE_SECONDS", .3)
        self.grace_patch.start()

    def tearDown(self):
        """Terminate every test reader and restore the caller's profile and active OCR preference."""
        ocr_workers.shutdown(wait=True)
        self.grace_patch.stop()
        self.thread_patch.stop()
        store.DATA_DIR = self.previous_data
        self.tmp.cleanup()

    def work(self, function, *args, timeout=10):
        """Create an explicit importable test descriptor with one absolute admission deadline."""
        return ocr_workers.Request(function, tuple(args), {}, str(store.DATA_DIR), 4,
                                   time.monotonic() + timeout)

    def wait_until(self, predicate, timeout=4):
        """Bound polling for startup/reaping evidence without leaving an injected reader alive."""
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate(), "OCR child lifecycle did not finish within its bound.")

    def child_pids(self):
        """Read multiprocessing's live owned children without sending signals to Windows processes."""
        result = set()
        for child in multiprocessing.active_children():
            try:
                if child.name == "poe2-ocr-worker" and child.is_alive():
                    result.add(child.pid)
            except ValueError:
                # A supervisor may close a reaped handle during this snapshot.
                pass
        return result

    def start_blocked(self, name, timeout=10):
        """Wait until a real blocked reader has published a PID before exercising cancellation."""
        path = Path(self.tmp.name) / name
        future = ocr_workers.submit(self.work(_blocked, str(path), timeout=timeout))
        self.wait_until(lambda: path.exists() and bool(path.read_text(encoding="ascii")))
        return future, int(path.read_text(encoding="ascii"))

    def test_request_freezes_context_without_spawning_and_injected_readers_stay_inline(self):
        """Descriptor creation preserves profile/thread settings while test mocks require no serialization."""
        request = ocr_workers.request(ocr_runtime.active_threads)
        self.assertIsInstance(request, ocr_workers.Request)
        self.assertEqual(request.data_dir, self.tmp.name)
        self.assertEqual(request.cpu_threads, 4)
        self.assertIsNone(ocr_workers._BROKER)
        injected = Mock(return_value="injected")
        self.assertEqual(ocr_workers.run(injected, "input"), "injected")
        self.assertNotIsInstance(ocr_workers.request(injected), ocr_workers.Request)
        injected.assert_called_once_with("input")
        self.assertIsNone(ocr_workers._BROKER)
        store.DATA_DIR = Path(self.tmp.name) / "later-profile"
        request = replace(request, function=_identity)
        answer = request()
        self.assertEqual(answer["data_dir"], self.tmp.name)
        self.assertEqual(answer["cpu_threads"], 4)
        self.assertNotEqual(answer["pid"], os.getpid())

    def test_healthy_children_are_reused_and_bounded_to_two(self):
        """Concurrent scans warm two children and later requests reuse those processes and preferences."""
        initial = [ocr_workers.submit(self.work(_identity, .1)) for _ in range(2)]
        answers = [future.result(timeout=5) for future in initial]
        pids = {answer["pid"] for answer in answers}
        self.assertEqual(len(pids), 2)
        for _ in range(3):
            futures = [ocr_workers.submit(self.work(_identity, .05)) for _ in range(2)]
            answers = [future.result(timeout=5) for future in futures]
            self.assertLessEqual({answer["pid"] for answer in answers}, pids)
            self.assertTrue(all(answer["data_dir"] == self.tmp.name and answer["cpu_threads"] == 4
                                for answer in answers))
            self.assertEqual(self.child_pids(), pids)

    def test_native_timeout_reaps_child_and_next_scan_recovers(self):
        """A genuinely blocked reader reaches its deadline, disappears and cannot occupy later work."""
        future, pid = self.start_blocked("timeout.pid", timeout=2)
        with self.assertRaisesRegex(TimeoutError, "OCR timed out"):
            future.result(timeout=4)
        self.wait_until(lambda: pid not in self.child_pids())
        answer = ocr_workers.submit(self.work(_identity)).result(timeout=5)
        self.assertNotEqual(answer["pid"], pid)

    def test_cancelled_reading_finishes_and_keeps_its_loaded_child(self):
        """A cancelled reading is discarded but its child is not stopped, so nothing must reload."""
        self.grace_patch.stop()
        self.grace_patch = patch.object(ocr_workers, "CANCELLED_GRACE_SECONDS", 5)
        self.grace_patch.start()
        path = Path(self.tmp.name) / "finite.pid"
        future = ocr_workers.submit(self.work(_finite, str(path), .5))
        self.wait_until(lambda: path.exists() and bool(path.read_text(encoding="ascii")))
        pid = int(path.read_text(encoding="ascii"))
        self.assertTrue(future.cancel())
        with self.assertRaises(CancelledError):
            future.result(timeout=1)
        self.wait_until(lambda: not ocr_workers._BROKER.active)
        self.assertIn(pid, self.child_pids())
        answers = [ocr_workers.submit(self.work(_identity, .1)) for _ in range(2)]
        self.assertIn(pid, {answer.result(timeout=5)["pid"] for answer in answers})

    def test_active_cancellation_reaps_child_and_next_scan_recovers(self):
        """Cancelling active native work retires its result and frees a killable child for later scans."""
        future, pid = self.start_blocked("cancel.pid")
        self.assertTrue(future.running())
        self.assertTrue(future.cancel())
        with self.assertRaises(CancelledError):
            future.result(timeout=1)
        self.assertEqual(wait([future], timeout=.2).done, {future})
        self.wait_until(lambda: pid not in self.child_pids())
        answer = ocr_workers.submit(self.work(_identity)).result(timeout=5)
        self.assertNotEqual(answer["pid"], pid)

    def test_queue_deadline_overflow_cancellation_and_independent_export(self):
        """Two stuck scans cannot retain unlimited inputs, outlive queued deadlines or block normal I/O workers."""
        first, first_pid = self.start_blocked("first.pid")
        second, second_pid = self.start_blocked("second.pid")
        self.assertEqual(self.child_pids(), {first_pid, second_pid})
        short = ocr_workers.submit(self.work(_identity, timeout=.15))
        queued = ocr_workers.submit(self.work(_identity))
        overflow = ocr_workers.submit(self.work(_identity))
        self.assertTrue(overflow.done())
        with self.assertRaisesRegex(RuntimeError, "queue is full"):
            overflow.result()
        with self.assertRaisesRegex(TimeoutError, "before recognition"):
            short.result(timeout=1)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        self.assertTrue(queued.cancel())
        replacement = ocr_workers.submit(self.work(_identity))
        self.assertFalse(replacement.done())
        with ThreadPoolExecutor(max_workers=2) as exports:
            self.assertEqual(exports.submit(bytes, b"exported").result(timeout=.5), b"exported")
        first.cancel()
        second.cancel()
        answer = replacement.result(timeout=5)
        self.assertNotIn(answer["pid"], {first_pid, second_pid})

    def test_shutdown_is_nonblocking_then_bounded_and_new_broker_recovers(self):
        """GUI closure cancels results immediately; final shutdown reaps children and permits a clean reopening."""
        first, first_pid = self.start_blocked("close-first.pid")
        second, second_pid = self.start_blocked("close-second.pid")
        start = time.monotonic()
        ocr_workers.shutdown(wait=False)
        self.assertLess(time.monotonic() - start, .2)
        self.assertTrue(first.cancelled())
        self.assertTrue(second.cancelled())
        start = time.monotonic()
        ocr_workers.shutdown(wait=True)
        self.assertLess(time.monotonic() - start, ocr_workers.SHUTDOWN_JOIN_SECONDS + .5)
        self.assertNotIn(first_pid, self.child_pids())
        self.assertNotIn(second_pid, self.child_pids())
        self.assertEqual(ocr_workers._RETIRING, [])
        self.assertIsNone(ocr_workers._BROKER)
        answer = ocr_workers.submit(self.work(_identity)).result(timeout=5)
        self.assertNotIn(answer["pid"], {first_pid, second_pid})

    def test_startup_and_input_serialization_failures_leave_admission_reusable(self):
        """Startup closes both pipe endpoints; startup or serialization errors never strand an admission."""
        spawn = multiprocessing.get_context("spawn")
        unstarted = spawn.Process()
        parent_connection, child_connection = spawn.Pipe()
        context = Mock(wraps=spawn)
        context.Pipe.return_value = parent_connection, child_connection
        context.Process.return_value = unstarted
        with patch.object(ocr_workers.multiprocessing, "get_context", return_value=context), \
                patch.object(unstarted, "start", side_effect=OSError("Worker startup unavailable")):
            failed = ocr_workers.submit(self.work(_identity))
            with self.assertRaisesRegex(OSError, "Worker startup unavailable"):
                failed.result(timeout=3)
            self.wait_until(lambda: not ocr_workers._BROKER.active)
        self.assertTrue(unstarted._closed)
        self.assertTrue(parent_connection.closed)
        self.assertTrue(child_connection.closed)
        failed = ocr_workers.submit(self.work(lambda: None))
        with self.assertRaises((AttributeError, pickle.PicklingError)):
            failed.result(timeout=3)
        self.assertEqual(ocr_workers.submit(self.work(_identity)).result(timeout=5)["data_dir"], self.tmp.name)

    def test_broken_child_and_validation_error_do_not_poison_later_scans(self):
        """Abrupt child exit is visible, while a normal validation exception preserves healthy reusable workers."""
        path = Path(self.tmp.name) / "broken.pid"
        future = ocr_workers.submit(self.work(_broken, str(path)))
        with self.assertRaisesRegex(RuntimeError, "stopped unexpectedly"):
            future.result(timeout=5)
        self.wait_until(lambda: not self.child_pids())
        warmed = [ocr_workers.submit(self.work(_identity, .1)) for _ in range(2)]
        healthy = {future.result(timeout=5)["pid"] for future in warmed}
        validation = ocr_workers.submit(self.work(_validation_error))
        with self.assertRaisesRegex(ValueError, "Confirm the visible quantity"):
            validation.result(timeout=5)
        self.assertEqual(self.child_pids(), healthy)
        answer = ocr_workers.submit(self.work(_identity)).result(timeout=5)
        self.assertIn(answer["pid"], healthy)

    def test_cancelled_reader_created_screenshot_is_removed_by_parent(self):
        """Killing a reader removes its orphan-prone temporary screenshot as well as the request payload."""
        evidence = Path(self.tmp.name) / "screenshot-path.txt"
        future = ocr_workers.submit(self.work(_blocked_with_tempfile, str(evidence)))
        self.wait_until(lambda: evidence.exists() and bool(evidence.read_text(encoding="utf-8")))
        image = Path(evidence.read_text(encoding="utf-8"))
        self.assertTrue(image.exists())
        self.assertTrue(image.parent.name.startswith("poe2-ocr-job-"))
        future.cancel()
        self.wait_until(lambda: not image.parent.exists())


if __name__ == "__main__":
    unittest.main()
