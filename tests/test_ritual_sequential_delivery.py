"""Save sequential Ritual pages while ordinary captures keep latest-only delivery.

Recognition is injected through Futures; these tests do not measure OCR accuracy.
"""
from concurrent.futures import Future
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform import hotkey
from PoE2_Data_Logger.ui import native_desktop as ui


class RetainedFuture(Future):
    """Represent a native completion racing an already requested cancellation."""
    def cancel(self):
        return False


class RitualSequentialDeliveryTests(unittest.TestCase):
    """Keep accepted history while later captures replace unsaved ordinary reviews."""
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-ritual-delivery-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.save_settings({"ocr_auto_commit": True})
        self.manager = hotkey.HotkeyManager(supported=False)
        self.hotkey_patch = patch.object(service, "HOTKEY", self.manager)
        self.hotkey_patch.start()
        self.window = ui.LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()
        self.futures = []
        self.requests = []
        self.submit_patch = patch.object(ui.ocr_workers, "submit", side_effect=self.submit)
        self.submit_patch.start()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.submit_patch.stop()
        self.hotkey_patch.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def submit(self, request):
        future = RetainedFuture()
        self.futures.append(future)
        self.requests.append(request)
        return future

    @staticmethod
    def png(index):
        raw = io.BytesIO()
        Image.new("RGB", (600, 250), (index % 256, (index * 17) % 256, 71)).save(raw, format="PNG")
        return raw.getvalue()

    def publish(self, index, *, mode="ritual", raw=None, strictness=20):
        result = {"captured": True, "map_id": logger.get_state()["current_map_id"],
                  **logger.scan_context(), "_ocr_strictness_values": {mode: strictness}}
        if mode == "currency":
            result["phase"] = "end"
        event = {"mode": mode, "result": result, "error": ""}
        with self.manager._lock:
            self.manager._publish_event(event, self.png(index) if raw is None else raw)
        return event

    @staticmethod
    def reading(quantity, *, needs_review=False):
        return {"items": [{"category": "Item", "name": "Ritual Delivery Reward", "quantity": quantity,
                           "tribute": 100, "source": "text OCR", "deferred": False,
                           "score": .99, "name_match": 1, "needs_review": needs_review}],
                "raw_text": f"Injected page {quantity}", "reward_count": 1,
                "needs_review": needs_review, "coverage_uncertain": False}

    def complete(self, index, quantity, *, needs_review=False):
        self.futures[index].set_result(self.reading(quantity, needs_review=needs_review))
        self.app.processEvents()

    @staticmethod
    def csv_rows(data):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        assert len(rows[0]) == len(set(rows[0]))
        assert all(len(row) == len(rows[0]) for row in rows)
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def ritual_commits(self):
        with logger._connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT details_json FROM commits WHERE kind='Ritual' ORDER BY number")]

    def click_approve(self):
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        QTest.mouseClick(self.window.approve_scan_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def test_fifty_five_sequential_pages_auto_save_and_export(self):
        for field, text in zip((self.window.normal, self.window.magic, self.window.rare, self.window.unique),
                               ("11", "22", "33", "44")):
            field.setText(text)
        for index in range(1, 56):
            event = self.publish(index)
            self.window.poll()
            self.assertEqual(len(self.futures), index)
            self.assertEqual(self.window._latest_hotkey, event["id"])
            self.assertEqual(self.window._ritual_capture_context["scan_hash"],
                             hashlib.sha256(Image.open(io.BytesIO(self.png(index))).tobytes()).hexdigest())
            self.window.poll()
            self.assertEqual(len(self.futures), index)
            self.complete(index - 1, index)
            self.assertIsNone(self.window.pending_review_kind)
        pages = logger.ritual_pages_for_map("M0001")
        self.assertEqual([page["page_number"] for page in pages], list(range(1, 56)))
        self.assertEqual([page["items"][0]["quantity"] for page in pages], list(range(1, 56)))
        self.assertEqual([commit["items"][0]["quantity"] for commit in self.ritual_commits()], list(range(1, 56)))
        history = [row for row in self.csv_rows(logger.export_all_csv()) if row["Type"] == "Ritual"]
        self.assertEqual([int(row["Quantity"]) for row in history], list(range(1, 56)))
        self.assertEqual([int(row["Ritual Page"]) for row in history], list(range(1, 56)))
        self.assertEqual(logger.session_currency_totals()["items"], [])
        self.assertEqual(tuple(field.text() for field in
                               (self.window.normal, self.window.magic, self.window.rare, self.window.unique)),
                         ("11", "22", "33", "44"))
        self.assertIsNone(self.manager.next_event(self.window._latest_hotkey))

    def test_burst_before_poll_delivers_only_latest_ritual(self):
        first, second, latest = self.publish(1), self.publish(2), self.publish(3)
        self.assertIsNone(self.manager.image(first["id"]))
        self.assertIsNone(self.manager.image(second["id"]))
        self.assertEqual(self.manager.next_event()["id"], latest["id"])
        self.window.poll()
        self.assertEqual(len(self.futures), 1)
        self.assertEqual(self.window._latest_hotkey, latest["id"])
        self.assertEqual(self.window._ritual_capture_context["scan_hash"],
                         hashlib.sha256(Image.open(io.BytesIO(self.png(3))).tobytes()).hexdigest())
        self.complete(0, 3)
        self.window.poll()
        self.assertEqual(len(self.futures), 1)
        self.assertEqual([p["items"][0]["quantity"] for p in logger.ritual_pages_for_map("M0001")], [3])
        self.assertIsNone(self.manager.next_event(self.window._latest_hotkey))

    def test_later_ritual_replaces_held_page_before_real_approve(self):
        self.publish(1)
        self.window.poll()
        self.complete(0, 1, needs_review=True)
        self.window.ritual_table.item(0, 2).setText("7")
        latest = self.publish(2)
        self.window.poll()
        self.assertEqual(len(self.futures), 2)
        self.assertEqual(self.window._latest_hotkey, latest["id"])
        self.assertEqual(self.window.ritual_table.rowCount(), 0)
        self.complete(1, 2, needs_review=True)
        for _ in range(3):
            self.window.poll()
        self.assertEqual(len(self.futures), 2)
        self.assertEqual(self.window.ritual_table.item(0, 2).text(), "2")
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.click_approve()
        self.window.poll()
        self.assertEqual([p["items"][0]["quantity"] for p in logger.ritual_pages_for_map("M0001")], [2])
        self.assertEqual(len(self.ritual_commits()), 1)

    def test_later_ritual_replaces_inflight_page_and_ignores_late_future(self):
        self.publish(1)
        self.window.poll()
        original = self.futures[0]
        latest = self.publish(2)
        self.window.poll()
        self.assertEqual(len(self.futures), 2)
        self.assertEqual(self.window._latest_hotkey, latest["id"])
        self.assertFalse(original.done())
        self.complete(1, 2)
        self.complete(0, 999, needs_review=True)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(self.window.ritual_table.item(0, 2).text(), "2")
        self.assertEqual([p["items"][0]["quantity"] for p in logger.ritual_pages_for_map("M0001")], [2])
        self.assertEqual([c["items"][0]["quantity"] for c in self.ritual_commits()], [2])

    def test_reject_latest_page_discards_unpolled_burst(self):
        self.publish(1)
        self.publish(2)
        latest = self.publish(3)
        self.window.poll()
        self.assertEqual(self.window._latest_hotkey, latest["id"])
        self.complete(0, 3, needs_review=True)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.publish(4)
        self.publish(5)
        QTest.mouseClick(self.window.reject_scan_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        for _ in range(3):
            self.window.poll()
        self.assertEqual(len(self.futures), 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(self.manager.next_event(self.window._latest_hotkey))
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.assertEqual(self.ritual_commits(), [])

    def test_same_image_updates_one_page_but_retains_both_approval_commits(self):
        raw = self.png(1)
        self.publish(1, raw=raw)
        self.window.poll()
        self.complete(0, 1)
        self.publish(2, raw=raw)
        self.window.poll()
        self.complete(1, 2)
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)
        self.assertEqual(logger.ritual_pages_for_map("M0001")[0]["items"][0]["quantity"], 2)
        self.assertEqual([c["page"] for c in self.ritual_commits()], [1, 1])
        self.assertEqual([c["items"][0]["quantity"] for c in self.ritual_commits()], [1, 2])

    def test_currency_burst_is_also_latest_only(self):
        ui.select(self.window.inventory_phase, "end")
        self.publish(1, mode="currency")
        self.publish(2, mode="currency")
        self.window.poll()
        self.assertEqual(len(self.futures), 1)
        self.assertIsNone(self.manager.next_event(self.window._latest_hotkey))
        self.futures[0].set_result({"items": [{"slot": 1, "name": "Divine Orb", "quantity": 3}], "unknown": []})
        self.app.processEvents()
        self.window.poll()
        self.assertEqual(len(self.futures), 1)
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Divine Orb": 3})

    def test_other_mode_replaces_held_ritual_without_waiting_on_ritual_burst(self):
        self.publish(1)
        self.window.poll()
        self.complete(0, 1, needs_review=True)
        self.window.ritual_table.item(0, 2).setText("7")
        self.publish(2)
        ui.select(self.window.inventory_phase, "end")
        latest = self.publish(3, mode="currency")
        self.window.poll()
        self.assertEqual(len(self.futures), 2)
        self.assertEqual(self.window._latest_hotkey, latest["id"])
        self.assertEqual(self.window.pending_review_kind, "currency")
        self.futures[1].set_result({"items": [{"slot": 1, "name": "Divine Orb", "quantity": 3}], "unknown": []})
        self.app.processEvents()
        for _ in range(3):
            self.window.poll()
        self.assertEqual(len(self.futures), 2)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Divine Orb": 3})
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.assertEqual(self.ritual_commits(), [])
        self.assertIsNone(self.manager.next_event(self.window._latest_hotkey))

    def test_frozen_context_and_strictness_survive_later_preferences(self):
        event = self.publish(1, strictness=37)
        with logger._connect() as db:
            logger._set_meta(db, "ocr_strictness", {"ritual": 81})
        self.window.poll()
        capture = self.window._ritual_capture_context
        self.assertEqual(capture["strictness"], 37)
        for token in ("_scan_generation", "_capture_map_id", "_capture_map_pending", "_capture_expedition"):
            self.assertEqual(capture["context"][token], event["result"][token])
        self.complete(0, 1)
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)

    def test_new_map_retires_latest_capture_and_late_inflight_result(self):
        self.publish(1)
        self.publish(2)
        self.window.poll()
        self.window.finish_map()
        self.complete(0, 999)
        self.window.poll()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.assertEqual(logger.ritual_pages_for_map("M0002"), [])
        self.assertEqual(self.ritual_commits(), [])
        self.assertIsNone(self.window.pending_review_kind)

    def test_busy_physical_ritual_capture_does_not_queue_another_hotkey(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def block(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Blocked test capture was not released")
            return Image.new("RGB", (600, 250), "tan")
        manager = hotkey.HotkeyManager(grabber=Mock(side_effect=block), supported=False)
        with logger._connect() as db:
            logger._set_meta(db, "ritual_region", {"x": 0, "y": 0, "w": 600, "h": 250})
        original = manager._capture
        def tracked(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            finally:
                finished.set()
        with patch.object(manager, "_capture", side_effect=tracked):
            try:
                manager.capture("ritual", background=True)
                self.assertTrue(entered.wait(2))
                manager.capture("ritual", background=True)
                self.assertEqual(manager.grabber.call_count, 1)
                self.assertIsNone(manager._deferred_propagation)
            finally:
                release.set()
                self.assertTrue(finished.wait(2))
                manager.cancel_capture()


if __name__ == "__main__":
    unittest.main()
