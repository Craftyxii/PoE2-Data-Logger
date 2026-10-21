"""Exercise startup affinity and replacement preparation in actual bounded OCR children."""

from dataclasses import replace
from functools import partial
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import ocr_runtime, ocr_workers, store


def _preparation_count():
    """Inspect only the current child's lightweight preparation evidence."""
    path = Path(store.DATA_DIR) / f"prepared-{os.getpid()}.txt"
    return int(path.read_text(encoding="ascii")) if path.exists() else 0


def _prepare_once():
    """Stand in for artwork preparation without importing images, matching banks or OCR models."""
    root = Path(store.DATA_DIR)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"prepared-{os.getpid()}.txt").write_text(
        str(_preparation_count() + 1), encoding="ascii")
    delay = root / "preparation-delay.txt"
    if delay.exists():
        time.sleep(float(delay.read_text(encoding="ascii")))
    while (root / "hold-preparation").exists() and not (root / "release-preparation").exists():
        time.sleep(.01)
    if (root / "fail-preparation").exists():
        raise ValueError("Artwork preparation unavailable.")
    return os.getpid()


def _identity():
    """Return worker identity and preparation ownership without doing any recognition."""
    return {"pid": os.getpid(), "data_dir": str(store.DATA_DIR),
            "cpu_threads": ocr_runtime.active_threads(),
            "gpu_labels": ocr_runtime.active_gpu_labels(),
            "prepared": _preparation_count()}


def _blocked(pid_file):
    """Expose the exact owned process before emulating a blocked native recognizer."""
    Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    while True:
        time.sleep(.05)


def _recognized(marker):
    """Record that recognition actually started after any automatic preparation."""
    Path(marker).write_text(str(os.getpid()), encoding="ascii")
    return _identity()


class OCRStartupPreloadTests(unittest.TestCase):
    """Keep startup work in the normal process, deadline and cancellation lifecycle."""

    def setUp(self):
        """Start with an isolated profile and no surviving broker or model process."""
        ocr_workers.shutdown(wait=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-startup-preload-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        self.thread_patch = patch.object(ocr_runtime, "_ACTIVE_THREADS", 4)
        self.gpu_patch = patch.object(ocr_runtime, "_ACTIVE_GPU_LABELS", False)
        self.thread_patch.start()
        self.gpu_patch.start()

    def tearDown(self):
        """Reap every test child before deleting its profile and restore frozen preferences."""
        ocr_workers.shutdown(wait=True)
        self.gpu_patch.stop()
        self.thread_patch.stop()
        store.DATA_DIR = self.previous_data
        self.tmp.cleanup()

    def work(self, function, *args, timeout=10, prepare=False):
        """Build one importable admission with an original absolute deadline."""
        return ocr_workers.Request(function, tuple(args), {}, str(store.DATA_DIR), 4,
                                   time.monotonic() + timeout, gpu_labels=False,
                                   prepare_artwork=prepare)

    def broker(self):
        """Use the global lifecycle so production shutdown also owns direct affinity fixtures."""
        with ocr_workers._BROKER_LOCK:
            if ocr_workers._BROKER is None:
                ocr_workers._BROKER = ocr_workers._Broker()
            return ocr_workers._BROKER

    def wait_until(self, predicate, timeout=5):
        """Bound observation of startup, cancellation and process reaping."""
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate(), "OCR startup lifecycle did not finish within its bound.")

    def child_pids(self):
        """Snapshot live OCR children while supervisors may concurrently close reaped handles."""
        pids = set()
        for child in multiprocessing.active_children():
            try:
                if child.name == "poe2-ocr-worker" and child.is_alive():
                    pids.add(child.pid)
            except ValueError:
                pass
        return pids

    def start_blocked(self, broker, affinity, name):
        """Occupy a known supervisor and return its cancellable admission and exact process."""
        path = Path(self.tmp.name) / name
        future = broker.submit(self.work(_blocked, str(path)), affinity=affinity)
        self.wait_until(lambda: path.exists() and bool(path.read_text(encoding="ascii")))
        return future, int(path.read_text(encoding="ascii"))

    def test_public_preload_uses_two_distinct_children_once_without_scheduling_delays(self):
        """Even an immediate preparer warms both physical workers and repeats add no admissions."""
        with patch.object(ocr_workers, "_prepare_artwork", _prepare_once):
            startup = ocr_workers.preload_artwork()
            self.assertIs(ocr_workers.preload_artwork(), startup)
            pids = [future.result(timeout=5) for future in startup]
            self.assertIs(ocr_workers.preload_artwork(), startup)
        self.assertEqual(len(set(pids)), 2)
        broker = self.broker()
        self.assertEqual(broker.sequence, 2)
        for index, pid in enumerate(pids):
            answer = broker.submit(self.work(_identity, prepare=True), affinity=index).result(timeout=5)
            self.assertEqual((answer["pid"], answer["prepared"]), (pid, 1))
        self.assertEqual(self.child_pids(), set(pids))

    def test_request_marks_only_inventory_and_ritual_artwork_consumers(self):
        """Request creation stays lazy and unrelated recognizers retain their original transport."""
        from PoE2_Data_Logger.ocr import item_ocr

        for function in (item_ocr.scan_inventory_grid, item_ocr.scan_ritual_page,
                         partial(item_ocr.scan_inventory_grid, strictness=50)):
            descriptor = ocr_workers.request(function, "capture")
            self.assertTrue(descriptor.prepare_artwork)
            self.assertEqual(descriptor.data_dir, self.tmp.name)
            self.assertEqual(descriptor.cpu_threads, 4)
        self.assertFalse(ocr_workers.request(ocr_runtime.active_threads).prepare_artwork)
        self.assertFalse(self.work(_identity).prepare_artwork)
        self.assertIsNone(ocr_workers._BROKER)

    def test_new_frozen_profile_thread_or_gpu_context_gets_its_own_startup_pair(self):
        """Changed contexts prepare replacement children while repeated current contexts reuse futures."""
        broker = self.broker()
        work = self.work(_prepare_once)
        previous = None
        for changes in ({}, {"cpu_threads": 6}, {"gpu_labels": True},
                        {"data_dir": str(Path(self.tmp.name) / "other-profile")}):
            work = replace(work, **changes)
            startup = broker.preload(work)
            self.assertIsNot(startup, previous)
            self.assertIs(broker.preload(replace(work, deadline=work.deadline + 100)), startup)
            pids = {future.result(timeout=5) for future in startup}
            self.assertEqual(len(pids), 2)
            for index in range(2):
                answer = broker.submit(replace(work, function=_identity), affinity=index).result(timeout=5)
                self.assertIn(answer["pid"], pids)
                self.assertEqual((answer["data_dir"], answer["cpu_threads"], answer["gpu_labels"]),
                                 (work.data_dir, work.cpu_threads, work.gpu_labels))
                self.assertEqual(answer["prepared"], 1)
            previous = startup

    def test_startup_admissions_share_queue_bounds_and_selective_cancellation(self):
        """Two active startup tasks leave only two queue slots and cancelling one preserves the other."""
        root = Path(self.tmp.name)
        (root / "hold-preparation").touch()
        broker = self.broker()
        work = self.work(_prepare_once)
        startup = broker.preload(work)
        self.wait_until(lambda: len(list(root.glob("prepared-*.txt"))) == 2)
        self.assertTrue(all(future.running() for future in startup))
        queued = [broker.submit(self.work(_identity)) for _ in range(2)]
        overflow = broker.submit(self.work(_identity))
        self.assertTrue(overflow.done())
        with self.assertRaisesRegex(RuntimeError, "queue is full"):
            overflow.result()
        for future in queued:
            self.assertTrue(future.cancel())
        self.assertTrue(startup[0].cancel())
        self.wait_until(lambda: len(self.child_pids()) == 1)
        self.assertFalse(startup[1].done())
        self.assertIs(broker.preload(work), startup)
        self.assertEqual(broker.sequence, 4)
        (root / "release-preparation").touch()
        survivor = startup[1].result(timeout=5)
        self.assertEqual(self.child_pids(), {survivor})

    def test_startup_pair_is_rejected_atomically_when_only_one_queue_slot_remains(self):
        """Insufficient capacity cannot retain half a preload pair or replace its configured context."""
        broker = self.broker()
        first, _ = self.start_blocked(broker, 0, "first.pid")
        second, _ = self.start_blocked(broker, 1, "second.pid")
        queued = broker.submit(self.work(_identity))
        sequence = broker.sequence
        startup = broker.preload(self.work(_prepare_once))
        for future in startup:
            with self.assertRaisesRegex(RuntimeError, "could not be admitted"):
                future.result(timeout=1)
        self.assertEqual(broker.sequence, sequence)
        self.assertIsNone(broker.preload_work)
        self.assertFalse(list(Path(self.tmp.name).glob("prepared-*.txt")))
        first.cancel()
        second.cancel()
        queued.cancel()

    def test_queued_startup_pair_expires_at_its_original_admission_deadline(self):
        """Affinity tasks waiting behind active recognition receive no fresh timeout when a slot frees."""
        broker = self.broker()
        first, first_pid = self.start_blocked(broker, 0, "first.pid")
        second, second_pid = self.start_blocked(broker, 1, "second.pid")
        startup = broker.preload(self.work(_prepare_once, timeout=.15))
        for future in startup:
            with self.assertRaisesRegex(TimeoutError, "before recognition"):
                future.result(timeout=2)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        self.assertEqual(self.child_pids(), {first_pid, second_pid})
        self.assertFalse(list(Path(self.tmp.name).glob("prepared-*.txt")))

    def test_cancelled_worker_replacement_prepares_once_before_its_next_flagged_scan(self):
        """A replacement uses the configured preparer while the healthy worker retains its existing bank."""
        broker = self.broker()
        startup = broker.preload(self.work(_prepare_once))
        original = [future.result(timeout=5) for future in startup]
        blocked, pid = self.start_blocked(broker, 0, "cancel.pid")
        self.assertEqual(pid, original[0])
        blocked.cancel()
        self.wait_until(lambda: pid not in self.child_pids())
        for _ in range(2):
            answers = [broker.submit(self.work(_identity, prepare=True), affinity=index).result(timeout=5)
                       for index in range(2)]
            self.assertEqual(answers[1]["pid"], original[1])
            self.assertNotEqual(answers[0]["pid"], pid)
            self.assertTrue(all(answer["prepared"] == 1 for answer in answers))
        self.assertEqual(len(list(Path(self.tmp.name).glob("prepared-*.txt"))), 3)

    def test_replacement_preparation_consumes_scan_deadline_and_never_starts_expired_recognizer(self):
        """Slow automatic preparation is killed within the scan's original absolute deadline."""
        broker = self.broker()
        for future in broker.preload(self.work(_prepare_once)):
            future.result(timeout=5)
        blocked, pid = self.start_blocked(broker, 0, "replace.pid")
        blocked.cancel()
        self.wait_until(lambda: pid not in self.child_pids())
        (Path(self.tmp.name) / "preparation-delay.txt").write_text("3", encoding="ascii")
        marker = Path(self.tmp.name) / "recognition-started"
        work = self.work(_recognized, str(marker), timeout=1, prepare=True)
        started = time.monotonic()
        future = broker.submit(work, affinity=0)
        self.wait_until(lambda: len(list(Path(self.tmp.name).glob("prepared-*.txt"))) == 3)
        with self.assertRaisesRegex(TimeoutError, "OCR timed out"):
            future.result(timeout=3)
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(marker.exists())
        self.wait_until(lambda: len(self.child_pids()) == 1)

    def test_unrelated_context_does_not_run_configured_artwork_preparer(self):
        """Non-Currency recognition remains lightweight when it replaces a worker for another profile."""
        broker = self.broker()
        for future in broker.preload(self.work(_prepare_once)):
            future.result(timeout=5)
        other = Path(self.tmp.name) / "unrelated-profile"
        other.mkdir()
        unrelated = replace(self.work(_identity), data_dir=str(other))
        answer = broker.submit(unrelated, affinity=0).result(timeout=5)
        self.assertEqual(answer["data_dir"], str(other))
        self.assertEqual(answer["prepared"], 0)
        self.assertEqual(list(other.iterdir()), [])

    def test_failed_bulk_preparation_preserves_scans_and_is_not_repeated_in_healthy_children(self):
        """Startup errors remain visible while normal scans retain their existing on-demand fallback."""
        (Path(self.tmp.name) / "fail-preparation").touch()
        broker = self.broker()
        startup = broker.preload(self.work(_prepare_once))
        for future in startup:
            with self.assertRaisesRegex(ValueError, "Artwork preparation unavailable"):
                future.result(timeout=5)
        pids = set(self.child_pids())
        for index in range(2):
            for attempt in range(2):
                marker = Path(self.tmp.name) / f"recognized-{index}-{attempt}"
                answer = broker.submit(self.work(_recognized, str(marker), prepare=True),
                                       affinity=index).result(timeout=5)
                self.assertTrue(marker.exists())
                self.assertIn(answer["pid"], pids)
                self.assertEqual(answer["prepared"], 1)
        self.assertIs(broker.preload(self.work(_prepare_once)), startup)

    def test_scan_ahead_of_startup_affinity_attempts_preparation_once_and_retains_startup_error(self):
        """A scan may overtake its startup job without swallowing that job's eventual preparation error."""
        root = Path(self.tmp.name)
        (root / "fail-preparation").touch()
        broker = self.broker()
        blocked, old_pid = self.start_blocked(broker, 0, "before-startup.pid")
        marker = root / "recognition-started"
        scan = broker.submit(self.work(_recognized, str(marker), prepare=True), affinity=0)
        startup = broker.preload(self.work(_prepare_once))
        blocked.cancel()
        answer = scan.result(timeout=5)
        self.assertNotEqual(answer["pid"], old_pid)
        self.assertTrue(marker.exists())
        self.assertEqual(answer["prepared"], 1)
        for future in startup:
            with self.assertRaisesRegex(ValueError, "Artwork preparation unavailable"):
                future.result(timeout=5)
        self.assertEqual(len(list(root.glob("prepared-*.txt"))), 2)
        self.assertTrue(all(path.read_text(encoding="ascii") == "1"
                            for path in root.glob("prepared-*.txt")))

    def test_shutdown_cancels_startup_and_queued_work_without_leaving_children(self):
        """Closing during preparation stays nonblocking and final cleanup remains bounded."""
        (Path(self.tmp.name) / "hold-preparation").touch()
        broker = self.broker()
        startup = broker.preload(self.work(_prepare_once))
        self.wait_until(lambda: len(list(Path(self.tmp.name).glob("prepared-*.txt"))) == 2)
        queued = [broker.submit(self.work(_identity)) for _ in range(2)]
        started = time.monotonic()
        ocr_workers.shutdown(wait=False)
        self.assertLess(time.monotonic() - started, .2)
        self.assertTrue(all(future.cancelled() for future in (*startup, *queued)))
        ocr_workers.shutdown(wait=True)
        self.assertEqual(self.child_pids(), set())
        self.assertEqual(ocr_workers._RETIRING, [])


if __name__ == "__main__":
    unittest.main()
