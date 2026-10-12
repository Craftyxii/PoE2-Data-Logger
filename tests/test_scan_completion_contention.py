"""Exercise real queued Qt scan completions against competing SQLite writers."""

from concurrent.futures import Future
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui import native_desktop as ui


class ScanCompletionContentionTests(unittest.TestCase):
    """Keep recognized drafts usable and late results harmless during database contention."""

    @classmethod
    def setUpClass(cls):
        """Use a real Qt event loop without a platform screenshot adapter."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated profile, normal window and deterministic capture evidence."""
        self.directory = tempfile.TemporaryDirectory(prefix="poe2-completion-contention-")
        self.previous_data, self.previous_mode = store.DATA_DIR, service.HOTKEY.mode
        store.DATA_DIR = Path(self.directory.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        settings = logger.get_state()["settings"]
        settings["ocr_auto_commit"] = True
        settings["auto_commit"] = False
        logger.save_settings(settings)
        self.window = ui.LoggerWindow()
        self.window._poll.stop()
        self.picture = Image.new("RGB", (600, 250), (40, 70, 110))
        self.picture_path = Path(self.directory.name) / "capture.png"
        self.picture.save(self.picture_path)
        self.grid_patch = patch.object(ui.item_ocr, "inventory_grid", side_effect=lambda image: image)
        self.grid_patch.start()
        self.writers = []
        self.release_timers = []
        self.timer_writers = {}
        self.delivery_threads = []

    def tearDown(self):
        """Release every real writer and worker before restoring the incoming profile."""
        for timer in self.release_timers:
            timer.cancel()
            timer.join()
        for writer in self.writers:
            writer.rollback()
            writer.close()
        for thread in self.delivery_threads:
            thread.join()
        self.grid_patch.stop()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        # Restore global mode without persisting it into the incoming profile.
        service.HOTKEY.mode = self.previous_mode
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.directory.cleanup()

    def pump(self, predicate=lambda: False, timeout=.5):
        """Process queued delivery and retries, returning the longest event-loop invocation."""
        deadline, longest = time.monotonic() + timeout, 0
        while time.monotonic() < deadline:
            started = time.monotonic()
            self.app.processEvents()
            longest = max(longest, time.monotonic() - started)
            if predicate():
                break
            time.sleep(.005)
        return longest

    def writer(self, release_after=5):
        """Hold BEGIN IMMEDIATE; a separate timer bounds failures in the old blocking implementation."""
        writer = sqlite3.connect(store.DATA_DIR / "scans.sqlite3", check_same_thread=False)
        writer.execute("BEGIN IMMEDIATE")
        self.writers.append(writer)
        timer = threading.Timer(release_after, writer.rollback)
        self.release_timers.append(timer)
        self.timer_writers[timer] = writer
        timer.start()
        return writer, timer

    def release_writer(self, timer):
        """Release contention as soon as admission assertions finish; retain a failure-only safety deadline."""
        timer.cancel()
        timer.join()
        self.timer_writers[timer].rollback()

    def deliver(self, future, result=None, error=None):
        """Complete the submitted Future on another thread so Tasks.completed uses queued Qt delivery."""
        thread = threading.Thread(target=lambda: future.set_exception(error) if error else future.set_result(result))
        self.delivery_threads.append(thread)
        thread.start()
        thread.join()

    def capture(self, mode, *, live=True, color=None):
        """Submit through the production capture and _submit_scan paths with a controlled OCR Future."""
        future = Future()
        picture = self.picture if color is None else Image.new("RGB", self.picture.size, color)
        with patch.object(ui.ocr_workers, "submit", return_value=future):
            if mode == "currency":
                ui.select(self.window.inventory_phase, "end")
                self.window._inventory_captured(picture, live=live)
            elif mode == "ritual":
                self.window._ritual_captured(picture, live=live)
            else:
                self.window.mode = mode
                ui.select(self.window.mode_select, mode)
                with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.picture_path), "")):
                    self.window.scan_file()
        self.app.processEvents()
        self.assertTrue(self.window._pending_tasks)
        return future

    def result(self, mode, quantity=17):
        """Return clear Currency/Ritual or reviewable remnant readings without running OCR."""
        if mode == "currency":
            return {"items": [{"slot": 1, "name": "Chaos Orb", "quantity": quantity, "score": 1.0}],
                    "unknown": []}
        if mode == "ritual":
            return {"items": [{"category": "Item", "name": "Chaos Orb", "quantity": quantity,
                               "tribute": 500, "source": "Chaos Orb", "score": 1.0,
                               "name_match": 1.0, "deferred": False}],
                    "raw_text": "Chaos Orb 500", "tribute_available": 1000,
                    "rerolls_remaining": 1, "reward_count": 1}
        if mode == "opened":
            return {"mode": "opened", "status": "Opened reward needs review", "can_use": False,
                    "first_recipe": None, "opened_recipes": [{"raw": "Unclear reward", "recipe": None}],
                    **logger.scan_context()}
        with logger._connect() as db:
            stage = dict(db.execute("SELECT * FROM seed_states WHERE family=3 LIMIT 1").fetchone())
        return {"mode": "seed", "status": "Visible seed needs review",
                "remnants": [{"sockets": stage["sockets"], "seed_slot": stage["seed_slot"],
                              "seed_rune": stage["seed_rune"], "candidates": [3], "can_commit": False}],
                **logger.scan_context()}

    def responsive_delivery(self, future, result=None, error=None):
        """Require real HUD rendering with zero-wait database admission and continued timer service."""
        ticks = []
        heartbeat = QTimer(self.window)
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
        heartbeat.start()
        timeouts, executions = [], []
        real_connect = sqlite3.connect

        class TimedConnection(sqlite3.Connection):
            """Observe real SQLite calls rather than interpreting unrelated Qt styling cost as lock waiting."""

            def execute(self, statement, parameters=()):
                started = time.monotonic()
                try:
                    return super().execute(statement, parameters)
                finally:
                    executions.append((statement, time.monotonic() - started))

        def connect(*args, **kwargs):
            """Preserve production arguments while recording the actual timeout and statement duration."""
            timeouts.append(kwargs.get("timeout", args[1] if len(args) > 1 else 5))
            kwargs.setdefault("factory", TimedConnection)
            return real_connect(*args, **kwargs)

        with patch.object(sqlite3, "connect", side_effect=connect):
            self.deliver(future, result, error)
            longest = self.pump(timeout=.3)
            # Widget restyling can take several hundred milliseconds under CI
            # load. Check timer delivery after that callback while the real
            # competing writer is still held, then inspect actual SQLite waits.
            self.pump(timeout=.12)
        heartbeat.stop()
        self.assertTrue(timeouts)
        self.assertEqual(set(timeouts), {0.0}, "GUI completion opened a connection that can wait on SQLite locks")
        slowest = max(executions, key=lambda entry: entry[1])
        self.assertLess(slowest[1], .5,
                        f"a real SQLite statement was slow during contention: {slowest!r}")
        self.assertLess(longest, 4, "scan completion blocked until the five-second writer release")
        self.assertGreaterEqual(len(ticks), 5, "the GUI heartbeat stalled while delivering the scan")

    def test_currency_auto_save_contention_retains_a_retryable_recognized_capture(self):
        """A busy auto-save preserves reviewed stacks, phase and image and eventually saves exactly once."""
        future = self.capture("currency")
        capture = self.window._inventory_capture_context
        _, timer = self.writer()
        self.responsive_delivery(future, self.result("currency"))
        self.assertFalse(self.window._failed_review)
        self.assertEqual(self.window.inventory_table.item(0, 1).text(), "Chaos Orb")
        self.assertEqual(self.window.inventory_table.item(0, 2).text(), "17")
        self.assertIs(self.window._inventory_capture_context, capture)
        self.assertEqual(self.window.preview.pixmap().toImage().pixelColor(0, 0).getRgb()[:3], (40, 70, 110))
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.release_writer(timer)
        self.pump(timeout=.5)
        if logger.get_state()["scan_commit_count"] == 0:
            self.assertTrue(self.window.approve_scan_button.isEnabled())
            self.window.approve_scan_button.click()
        self.pump(timeout=.3)
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Chaos Orb": 17})
        self.assertIsNone(self.window._inventory_reading)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertFalse(self.window._pending_tasks)

    def test_ritual_auto_save_contention_preserves_capture_hash_and_saves_one_page(self):
        """Ritual contention retains rows and capture fingerprint without stranding a failed review."""
        future = self.capture("ritual")
        capture = self.window._ritual_capture_context
        _, timer = self.writer()
        self.responsive_delivery(future, self.result("ritual", 2))
        self.assertFalse(self.window._failed_review)
        self.assertEqual(self.window.ritual_table.item(0, 1).text(), "Chaos Orb")
        self.assertEqual(self.window.ritual_table.item(0, 2).text(), "2")
        self.assertIs(self.window._ritual_capture_context, capture)
        self.assertEqual(self.window._ritual_hash, capture["scan_hash"])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.release_writer(timer)
        self.pump(timeout=.5)
        if logger.get_state()["scan_commit_count"] == 0:
            self.assertTrue(self.window.approve_scan_button.isEnabled())
            self.window.approve_scan_button.click()
        self.pump(timeout=.3)
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        pages = logger.ritual_pages_for_map("M0001")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["items"][0]["quantity"], 2)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT scan_hash FROM ritual_pages").fetchone()[0], capture["scan_hash"])
        self.assertIsNone(self.window._ritual_reading)
        self.assertIsNone(self.window.pending_review_kind)

    def test_seed_opened_and_both_complete_reservation_after_busy_writer(self):
        """Each remnant capture retains its original result and image while ID reservation is deferred."""
        for mode in ("seed", "opened", "both"):
            with self.subTest(mode=mode):
                future = self.capture(mode)
                result = self.result(mode)
                original = copy.deepcopy(result)
                _, timer = self.writer()
                self.responsive_delivery(future, result)
                self.release_writer(timer)
                self.pump(lambda: self.window._remnant_reading is None and
                          bool(self.window.results[original["mode"]]), timeout=3)
                self.assertFalse(self.window._failed_review)
                self.assertIsNone(self.window._remnant_reading)
                self.assertFalse(self.window._pending_tasks)
                displayed = self.window.results[original["mode"]]
                self.assertIsNotNone(displayed)
                self.assertEqual(displayed["status"], original["status"])
                self.assertEqual(self.window.images[original["mode"]], self.picture_path.read_bytes())
                pending = logger.get_state()["ocr_pending"]
                self.assertEqual((pending["map_id"], pending["remnant_id"]), ("M0001", "R0001"))
                self.assertEqual(logger.get_state()["scan_commit_count"], 0)
                self.window.reject_scan_button.click()
                self.pump(timeout=.05)
                self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_pre_reserved_clear_remnant_auto_save_contention_keeps_manual_approval_usable(self):
        """Ready remnant auto-saves fail promptly without replaying a presented seed/opened review."""
        logger.save_settings({"auto_commit": True})
        self.window.refresh()
        for mode in ("seed", "opened"):
            with self.subTest(mode=mode):
                before = logger.get_state()["scan_commit_count"]
                future = self.capture(mode)
                if mode == "seed":
                    result = self.result(mode)
                    result["remnants"][0]["can_commit"] = True
                else:
                    with logger._connect() as db:
                        names = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=1").fetchone()[0])
                        sockets = db.execute("SELECT sockets FROM recipes WHERE name=?", (names[0],)).fetchone()[0]
                    result = {"mode": "opened", "status": "Review the opened rewards before logging.",
                              "can_use": True, "family": "Family 1", "candidates": [1],
                              "sockets": sockets, "recipe_sockets": sockets, "socket_source": "opened icons",
                              "first_line_gap": 40, "first_recipe": names[0], "next_recipe": names[1],
                              "opened_recipes": [{"recipe": name, "raw": name, "ocr_score": .99,
                                                  "match_score": 1} for name in names[:2]],
                              **logger.scan_context()}
                result.update(logger.assign_ocr_id(mode))
                _, timer = self.writer()
                self.responsive_delivery(future, result)
                self.assertIsNone(self.window._remnant_reading)
                self.assertFalse(self.window._failed_review)
                self.assertTrue(self.window.approve_scan_button.isEnabled())
                self.assertEqual(self.window.results[mode]["remnant_id"], result["remnant_id"])
                self.assertEqual(logger.get_state()["scan_commit_count"], before)
                self.release_writer(timer)
                self.window.approve_scan_button.click()
                self.pump(timeout=.25)
                self.assertEqual(logger.get_state()["scan_commit_count"], before + 1)
                self.assertIsNone(logger.get_state()["ocr_pending"])
                self.assertIsNone(self.window.pending_review_kind)

    def test_deferred_older_remnant_result_cannot_replace_a_new_capture(self):
        """A reserved-ID retry belongs to its capture and cannot repaint a superseding result."""
        older = self.capture("opened")
        old_result = self.result("opened")
        _, timer = self.writer()
        self.responsive_delivery(older, old_result)
        newer = self.capture("opened")
        current_capture = self.window._remnant_reading
        current_result = self.result("opened")
        current_result["status"] = "New capture owns this review"
        self.release_writer(timer)
        self.deliver(newer, current_result)
        self.pump(lambda: bool(self.window.results["opened"]), timeout=3)
        self.pump(timeout=.4)
        self.assertEqual(self.window.results["opened"]["status"], current_result["status"])
        self.assertFalse(self.window._failed_review)
        self.assertIsNone(self.window._remnant_reading)
        self.assertIsNotNone(current_capture)
        self.assertFalse(self.window._pending_tasks)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_worker_failure_cleanup_is_responsive_and_releases_its_reservation(self):
        """A genuine OCR error leaves Reading promptly and Reject can finish busy reservation cleanup."""
        logger.assign_ocr_id("opened")
        future = self.capture("opened")
        _, timer = self.writer()
        self.responsive_delivery(future, error=RuntimeError("recognition worker failed"))
        self.assertIsNone(self.window._remnant_reading)
        self.assertTrue(self.window._failed_review)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertIn("recognition worker failed", self.window.review_summary.text())
        self.release_writer(timer)
        # Failed recognition is deliberately never replayed. The error review
        # retains its Reject action when the cleanup writer was unavailable.
        self.window.reject_scan_button.click()
        self.pump(timeout=.1)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertFalse(self.window._pending_tasks)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_persistent_reservation_contention_expires_without_stranded_reading(self):
        """The presentation deadline produces a rejectable error and the next scan works after release."""
        with patch.object(ui, "SCAN_PRESENTATION_WAIT_SECONDS", .15):
            future = self.capture("opened")
            _, timer = self.writer()
            self.responsive_delivery(future, self.result("opened"))
            self.pump(lambda: self.window._remnant_reading is None, timeout=.2)
            self.assertIsNone(self.window._remnant_reading)
            self.assertTrue(self.window._failed_review)
            self.assertFalse(self.window.approve_scan_button.isEnabled())
            self.assertIsNone(self.window.results["opened"])
            self.assertFalse(self.window._pending_tasks)
            self.assertFalse(self.window._scan_presentation_retries)
            self.assertNotIn("Reading", self.window.review_summary.text())
            self.release_writer(timer)
        self.window.reject_scan_button.click()
        fresh = self.capture("opened")
        self.deliver(fresh, self.result("opened"))
        self.pump(lambda: bool(self.window.results["opened"]), timeout=3)
        self.assertIsNotNone(self.window.results["opened"])
        self.assertFalse(self.window._failed_review)
        self.assertIsNone(self.window._remnant_reading)

    def test_deferred_results_are_retired_by_new_map_reset_and_close(self):
        """A delayed reservation must not revive rows or IDs after its map/session/window is retired."""
        for action in ("new_map", "reset", "close"):
            with self.subTest(action=action):
                future = self.capture("opened")
                result = self.result("opened")
                _, timer = self.writer()
                self.responsive_delivery(future, result)
                self.release_writer(timer)
                if action == "new_map":
                    self.window.finish_map()
                elif action == "reset":
                    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                        self.window.reset_logger()
                else:
                    self.window.close()
                commits = logger.get_state()["scan_commit_count"]
                self.pump(timeout=.4)
                self.assertEqual(logger.get_state()["scan_commit_count"], commits)
                self.assertIsNone(logger.get_state()["ocr_pending"])
                self.assertFalse(self.window._pending_tasks)
                if action != "close":
                    self.assertIsNone(self.window.results["opened"])
                    self.assertIsNone(self.window.pending_review_kind)

    def test_postcommit_refresh_contention_does_not_replay_an_already_saved_scan(self):
        """A busy refresh after the durable commit never reruns the scan or creates a duplicate page."""
        for mode in ("currency", "ritual"):
            with self.subTest(mode=mode):
                before = logger.get_state()["scan_commit_count"]
                future = self.capture(mode)
                real_refresh = self.window.refresh_currency_references
                calls = []

                def fail_first_refresh():
                    """Inject a BUSY at the UI refresh boundary after saving, rather than before BEGIN."""
                    calls.append(True)
                    if len(calls) == 1:
                        error = sqlite3.OperationalError("database is locked")
                        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                        raise error
                    return real_refresh()

                with patch.object(self.window, "refresh_currency_references", side_effect=fail_first_refresh):
                    self.deliver(future, self.result(mode, 3))
                    self.pump(timeout=.7)
                self.assertEqual(logger.get_state()["scan_commit_count"], before + 1)
                self.assertFalse(self.window._pending_tasks)
                self.assertIsNone(self.window.pending_review_kind)
                self.assertFalse(self.window.approve_scan_button.isEnabled())
                if mode == "ritual":
                    self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)

    def test_old_failure_cleanup_does_not_remove_a_new_maps_remnant_reservation(self):
        """Deferred cleanup is scoped to the failed capture even after another map reserves its own ID."""
        logger.assign_ocr_id("opened")
        failed = self.capture("opened")
        _, timer = self.writer()
        self.responsive_delivery(failed, error=RuntimeError("old reader failed"))
        self.release_writer(timer)
        self.window.finish_map()
        newer = self.capture("opened")
        current_result = self.result("opened")
        new_pending = logger.assign_ocr_id("opened")
        self.deliver(newer, current_result)
        self.pump(lambda: bool(self.window.results["opened"]), timeout=3)
        self.pump(timeout=.4)
        self.assertEqual(logger.get_state()["ocr_pending"], new_pending)
        self.assertEqual(new_pending["map_id"], "M0002")
        self.assertEqual(self.window.results["opened"]["map_id"], "M0002")
        self.assertFalse(self.window._failed_review)
        self.assertIsNone(self.window._remnant_reading)
        self.assertFalse(self.window._pending_tasks)

    def test_completed_old_expedition_failure_cannot_clear_new_reservation_on_same_map(self):
        """A permissively retained E1 context is insufficient authority to discard another expedition's token."""
        for initially_reserved in (False, True):
            with self.subTest(initially_reserved=initially_reserved):
                if initially_reserved:
                    logger.assign_ocr_id("opened")
                future = self.capture("opened")
                captured_context = logger.scan_context()
                old_expedition = logger.get_state()["current_expedition_id"]
                logger.commit_chain("Fire")
                logger.complete_chain(logger.scan_context())
                # Completing propagation deliberately retains an older
                # remnant's context. Simulate a newer accepted reservation
                # replacing it before the older recognition worker returns.
                logger.discard_ocr_id()
                new_pending = logger.assign_ocr_id("opened")
                self.assertNotEqual(new_pending["expedition_id"], old_expedition)
                self.assertEqual(new_pending["map_id"], "M0001")
                logger.validate_remnant_context(captured_context)
                before = logger.get_state()["scan_commit_count"]
                self.deliver(future, error=RuntimeError("older expedition recognition failed"))
                self.pump(lambda: self.window._remnant_reading is None, timeout=3)
                self.assertEqual(logger.get_state()["ocr_pending"], new_pending)
                self.assertEqual(logger.get_state()["scan_commit_count"], before)
                self.assertIsNone(self.window._remnant_reading)
                self.assertTrue(self.window._failed_review)
                self.assertFalse(self.window.approve_scan_button.isEnabled())
                self.assertIn("older expedition recognition failed", self.window.review_summary.text())
                logger.discard_ocr_id()

    def prepare_independent_propagation(self):
        """Show real recipe selectors, edit their rune slots and retain a distinct propagation screenshot."""
        self.window.show()
        self.app.processEvents()
        with logger._connect() as db:
            recipe = dict(db.execute("SELECT name,combo FROM recipes WHERE name='Divine Orb x2'").fetchone())
        names = [name.strip() for name in recipe["combo"].split("+")]
        image = io.BytesIO()
        Image.new("RGB", self.picture.size, (160, 55, 95)).save(image, format="PNG")
        result = {"mode": "propagation", "selected_recipe": recipe["name"],
                  "positions": [1], "runes": [names[0]], "can_use": True,
                  "status": "User is correcting these propagation selectors", **logger.scan_context()}
        self.window._propagation_read(result, image.getvalue())
        selectors = self.window._propagation_row_inputs[0]
        selectors[0].setCurrentIndex(2)
        selectors[1].setCurrentIndex(3)
        self.app.processEvents()
        approve = self.window.propagation_recipe_table.cellWidget(0, 2).findChild(
            QPushButton, "approvePropagationRecipe")
        self.assertTrue(approve.isEnabled())
        return {"summary": self.window.review_summary.text(), "context": copy.deepcopy(
                    self.window._manual_propagation_context), "selectors": tuple(selectors),
                "values": tuple(field.currentData() for field in selectors),
                "approve": approve, "preview": self.window.preview.pixmap().cacheKey(),
                "failed": self.window._failed_review}

    def assert_independent_propagation_preserved(self, expected):
        """Verify waiting or retiring a remnant cannot alter the user's independent correction."""
        self.assertEqual(self.window.review_kind.property("scanKind"), "propagation")
        self.assertEqual(self.window.review_summary.text(), expected["summary"])
        self.assertEqual(self.window._manual_propagation_context, expected["context"])
        self.assertEqual(tuple(self.window._propagation_row_inputs[0]), expected["selectors"])
        self.assertEqual(tuple(field.currentData() for field in expected["selectors"]), expected["values"])
        self.assertTrue(expected["approve"].isEnabled())
        self.assertFalse(self.window.chain_review_group.isHidden())
        self.assertTrue(self.window.scan_status.isHidden())
        self.assertTrue(self.window.remnant_log_group.isHidden())
        self.assertTrue(self.window.approve_scan_button.isHidden())
        self.assertEqual(self.window.preview.pixmap().cacheKey(), expected["preview"])
        self.assertEqual(self.window._failed_review, expected["failed"])

    def test_deferred_remnant_admission_preserves_visible_propagation_correction(self):
        """Storage-wait feedback and eventual remnant presentation preserve unrelated manual rune corrections."""
        future = self.capture("opened")
        expected = self.prepare_independent_propagation()
        _, timer = self.writer()
        self.responsive_delivery(future, self.result("opened"))
        self.assert_independent_propagation_preserved(expected)
        self.assertIsNotNone(self.window._remnant_reading)
        self.release_writer(timer)
        self.pump(lambda: bool(self.window.results["opened"]), timeout=3)
        self.assert_independent_propagation_preserved(expected)
        self.assertIsNone(self.window._remnant_reading)
        self.assertIsNotNone(self.window.results["opened"])

    def test_deferred_remnant_expiry_keeps_propagation_and_exposes_failed_held_review(self):
        """A remnant presentation deadline is attached to its held review without clearing visible propagation."""
        with patch.object(ui, "SCAN_PRESENTATION_WAIT_SECONDS", .15):
            future = self.capture("opened")
            expected = self.prepare_independent_propagation()
            _, timer = self.writer()
            self.responsive_delivery(future, self.result("opened"))
            self.pump(lambda: self.window._remnant_reading is None, timeout=.25)
            self.assert_independent_propagation_preserved(expected)
            self.assertIsNone(self.window._remnant_reading)
            self.assertTrue(self.window._held_remnant_review[3])
            self.assertFalse(self.window._scan_presentation_retries)
            self.release_writer(timer)
        self.window.manual_remnant_button.click()
        self.assertEqual(self.window.review_kind.property("scanKind"), "remnant")
        self.assertTrue(self.window._failed_review)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertFalse(self.window.reject_scan_button.isHidden())
        self.assertIn("Scan failed", self.window.review_summary.text())
        self.window.reject_scan_button.click()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])


if __name__ == "__main__":
    unittest.main()
