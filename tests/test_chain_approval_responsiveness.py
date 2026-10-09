"""Native chain approval stays responsive under SQLite contention and preserves other drafts."""

from contextlib import closing, contextmanager
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
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ChainApprovalResponsivenessTests(unittest.TestCase):
    """Use real SQLite writers and the normal propagation approval buttons."""

    @classmethod
    def setUpClass(cls):
        """Share the Qt application across isolated logger windows."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create a fresh database and a recipe with known ordered rune slots."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-chain-responsiveness-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            db.execute("UPDATE recipes SET sockets=3,combo=? WHERE name=?",
                       ("Death + Power + Opulent", "Medved's Saga"))
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1366, 1000)
        self.window.show()
        self.app.processEvents()
        image = io.BytesIO()
        Image.new("RGB", (575, 720), "tan").save(image, format="PNG")
        self.raw = image.getvalue()

    def tearDown(self):
        """Retire pending callbacks before restoring the caller's database."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        QTest.qWait(60)
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def wait_until(self, condition, timeout=3):
        """Pump Qt while awaiting a writer or scheduled acceptance callback."""
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition(), "The pending chain operation did not finish.")

    @contextmanager
    def writer_lock(self, duration, metadata=None):
        """Hold a competing writer lock and close its handle before Windows fixture cleanup."""
        path = store.DATA_DIR / "scans.sqlite3"
        ready = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        errors = []
        released_at = []

        def write():
            try:
                with closing(sqlite3.connect(path, timeout=2)) as db:
                    db.execute("BEGIN IMMEDIATE")
                    ready.set()
                    release.wait(duration)
                    for key, value in (metadata or {}).items():
                        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) "
                                   "DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
                    if metadata:
                        db.commit()
                    else:
                        db.rollback()
                    released_at.append(time.monotonic())
            except Exception as error:
                errors.append(error)
                ready.set()
            finally:
                finished.set()

        thread = threading.Thread(target=write, name="chain-test-writer", daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(2), "The background writer did not acquire SQLite.")
            self.assertEqual(errors, [])
            yield finished, released_at
        finally:
            release.set()
            thread.join(3)
            self.assertFalse(thread.is_alive(), "The background writer did not stop.")
            self.assertEqual(errors, [])

    def stage(self, runes=("Death", "Power")):
        """Deliver a propagation capture and return its real row approval control."""
        slots = {"Death": 1, "Power": 2, "Opulent": 3}
        reading = {"mode": "propagation", "can_use": True, "runes": list(runes),
                   "positions": [slots[rune] for rune in runes],
                   "selected_recipe": "Medved's Saga", **logger.scan_context()}
        self.window._propagation_read(reading, self.raw)
        self.app.processEvents()
        row = self.window.propagation_recipe_table.currentRow()
        approve = self.window.propagation_recipe_table.cellWidget(row, 2).findChild(
            QPushButton, "approvePropagationRecipe")
        self.assertTrue(approve.isEnabled())
        return reading, approve

    def assert_unsaved(self):
        """Confirm rejected, failed or stale requests leave both counters untouched."""
        state = logger.get_state()
        self.assertEqual(state["chain"], [])
        self.assertIsNone(state["detonated"])
        self.assertEqual(state["scan_commit_count"], 0)

    def test_writer_contention_keeps_heartbeat_and_saves_one_frozen_part(self):
        """A contended approval returns promptly, keeps Qt running and accepts each pair once."""
        for duration in (.2, 1):
            with self.subTest(writer_seconds=duration):
                before = logger.get_state()
                reading, approve = self.stage()
                fields = tuple(self.window._propagation_row_inputs[0])
                ticks = []
                timer = QTimer(self.window)
                timer.setInterval(10)
                timer.timeout.connect(lambda: ticks.append(time.monotonic()))
                try:
                    with self.writer_lock(duration) as (finished, released_at):
                        timer.start()
                        start = time.monotonic()
                        approve.click()
                        elapsed = time.monotonic() - start
                        if duration == 1:
                            self.assertLess(elapsed, .2, "Approve blocked on the SQLite writer.")
                        self.assertIsNotNone(self.window._chain_save_pending)
                        self.assertEqual(self.window.pending_review_kind, "propagation")
                        self.assertTrue(all(not field.isEnabled() for field in fields))
                        self.assertFalse(approve.isEnabled())
                        approve.click()
                        approve.click()
                        locked_state = logger.get_state()
                        self.assertEqual(locked_state["chain"], before["chain"])
                        self.assertEqual(locked_state["detonated"], before["detonated"])
                        self.wait_until(lambda: finished.is_set() and
                                        self.window._chain_save_pending is None)
                        self.assertGreater(sum(tick < released_at[0] for tick in ticks), 5)
                finally:
                    timer.stop()
                state = logger.get_state()
                self.assertEqual(len(state["chain"]), len(before["chain"]) + 1)
                self.assertEqual(state["chain"][-1]["rune1"], "Death")
                self.assertEqual(state["chain"][-1]["rune2"], "Power")
                self.assertEqual(state["detonated"], (before["detonated"] or 0) + 1)
                self.assertEqual(state["scan_commit_count"], before["scan_commit_count"] + 2)
                self.assertIsNone(self.window.pending_review_kind)
                self.window._propagation_read(reading, self.raw)
                QTest.qWait(100)
                self.assertEqual(logger.get_state()["scan_commit_count"], state["scan_commit_count"])
                self.assertIsNone(self.window.pending_review_kind)

    def test_reject_cancels_a_contended_approval_before_any_count_is_saved(self):
        """Reject remains available while waiting and retires the queued acceptance."""
        _reading, approve = self.stage()
        with self.writer_lock(.2) as (finished, _released_at):
            approve.click()
            self.assertIsNotNone(self.window._chain_save_pending)
            self.assertTrue(self.window.reject_scan_button.isEnabled())
            self.window.reject_scan_button.click()
            self.assertIsNone(self.window._chain_save_pending)
            self.assertIsNone(self.window.pending_review_kind)
            self.wait_until(finished.is_set)
            QTest.qWait(100)
        self.assert_unsaved()

    def test_changed_map_or_session_invalidates_the_waiting_capture(self):
        """A competing context change cannot redirect approval to another map or session."""
        for key in ("current_map_number", "session_generation"):
            with self.subTest(changed=key):
                _reading, approve = self.stage()
                with logger._connect() as db:
                    original = logger._meta(db, key, 0)
                with self.writer_lock(.2, {key: original + 1}) as (finished, _released_at):
                    approve.click()
                    self.assertIsNotNone(self.window._chain_save_pending)
                    self.wait_until(lambda: finished.is_set() and
                                    self.window._chain_save_pending is None)
                    QTest.qWait(100)
                self.assert_unsaved()
                self.window.reject_review()
                with logger._connect() as db:
                    logger._set_meta(db, key, original)
                self.window.refresh()

    def test_retry_error_restores_recipe_controls_and_allows_a_later_approval(self):
        """An unsuccessful retry retains its review and reenables its frozen inputs."""
        _reading, approve = self.stage()
        fields = tuple(self.window._propagation_row_inputs[0])
        with self.writer_lock(.2):
            approve.click()
            with patch.object(logger, "accept_propagation_part",
                              side_effect=ValueError("Injected chain save failure")):
                self.wait_until(lambda: self.window._chain_save_pending is None)
            self.assertEqual(self.window.pending_review_kind, "propagation")
            self.assertTrue(approve.isEnabled())
            self.assertTrue(all(field.isEnabled() for field in fields))
            self.assertIn("Injected chain save failure", self.window.statusBar().currentMessage())
            self.assert_unsaved()
        approve.click()
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(len(logger.get_state()["chain"]), 1)

    def test_expired_retry_retains_review_and_allows_a_later_approval(self):
        """An expired contention deadline restores the review without counting a failed save."""
        _reading, approve = self.stage()
        fields = tuple(self.window._propagation_row_inputs[0])
        with self.writer_lock(.2):
            approve.click()
            self.assertIsNotNone(self.window._chain_save_pending)
            self.window._chain_save_pending["deadline"] = time.monotonic() - 1
            self.wait_until(lambda: self.window._chain_save_pending is None)
            self.assertEqual(self.window.pending_review_kind, "propagation")
            self.assertTrue(approve.isEnabled())
            self.assertTrue(all(field.isEnabled() for field in fields))
            self.assertIn("not saved", self.window.statusBar().currentMessage())
            self.assert_unsaved()
        approve.click()
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(len(logger.get_state()["chain"]), 1)

    def test_replacement_currency_review_survives_the_old_approval_callback(self):
        """An older chain request cannot clear or freeze a newer inventory review."""
        self.assert_replacement_review_survives("currency")

    def test_replacement_ritual_review_survives_the_old_approval_callback(self):
        """An older chain request cannot clear or freeze newer Ritual corrections."""
        self.assert_replacement_review_survives("ritual")

    def assert_replacement_review_survives(self, kind):
        """Replace a waiting recipe review with a fully editable activity result."""
        _reading, approve = self.stage()
        with self.writer_lock(.2) as (finished, _released_at):
            approve.click()
            if kind == "currency":
                self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb",
                    "quantity": 7}], "unknown": []}, live=False)
                table = self.window.inventory_table
                add_row = self.window.inventory_add_row_button
            else:
                self.window._ritual_read({"items": [{"category": "Item", "name": "Chaos Orb",
                    "quantity": 7, "tribute": 100, "deferred": False}],
                    "raw_text": "Chaos Orb", "tribute_available": 1000}, live=False)
                table = self.window.ritual_table
                add_row = self.window.ritual_add_row_button
            table.item(0, 2).setText("123")
            summary = self.window.review_summary.text()
            self.wait_until(lambda: finished.is_set() and self.window._chain_save_pending is None)
            QTest.qWait(100)
            self.assertEqual(self.window.pending_review_kind, kind)
            self.assertEqual(self.window.review_kind.property("scanKind"), kind)
            self.assertEqual(self.window.review_summary.text(), summary)
            self.assertEqual(table.item(0, 2).text(), "123")
            self.assertTrue(table.editTriggers() & QAbstractItemView.EditTrigger.DoubleClicked)
            self.assertTrue(add_row.isEnabled())
            self.assertTrue(self.window.approve_scan_button.isEnabled())
            state = logger.get_state()
            self.assertEqual(state["detonated"], 1)
            self.assertEqual(state["chain"], [{"step": 1, "rune1": "Death", "rune2": "Power"}])
            self.assertEqual(state["scan_commit_count"], 2)

    def test_appending_preserves_dropdown_objects_corrections_and_typed_kills(self):
        """Growing the saved chain retains edits and skips unrelated currency rebuilding."""
        _reading, approve = self.stage(("Death",))
        approve.click()
        table = self.window.expedition_chain_table
        first = table.cellWidget(0, 1)
        second = table.cellWidget(0, 2)
        first.setCurrentIndex(first.findText("Opulent"))
        self.window.normal.clear()
        QTest.keyClicks(self.window.normal, "123")
        with patch.object(self.window, "refresh_currency_summary") as currency_refresh:
            for runes in (("Power",), ("Opulent",), ("Death", "Power")):
                _reading, approve = self.stage(runes)
                approve.click()
                self.assertIs(table.cellWidget(0, 1), first)
                self.assertIs(table.cellWidget(0, 2), second)
                self.assertEqual(first.currentText(), "Opulent")
                self.assertEqual(self.window.normal.text(), "123")
            currency_refresh.assert_not_called()
        state = logger.get_state()
        self.assertEqual(len(state["chain"]), 4)
        self.assertEqual(state["detonated"], 4)
        self.assertEqual(state["chain"][0]["rune1"], "Death")
        self.assertIsNone(state["kills"][0])
        self.assertTrue(self.window.expedition_save_chain_button.isEnabled())
        self.assertFalse(self.window.expedition_complete_chain_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
