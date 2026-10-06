import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication, QFileDialog

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ocr.item_text import parse_item_text
from PoE2_Data_Logger.ui import native_desktop
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class UIRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ui-regression-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        self.grid_patch = patch.object(native_desktop.item_ocr, "inventory_grid", side_effect=lambda image: image)
        self.grid_patch.start()

    def tearDown(self):
        self.grid_patch.stop()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def image_bytes(self, color=(120, 130, 140)):
        raw = io.BytesIO()
        Image.new("RGB", (600, 400), color).save(raw, format="PNG")
        return raw.getvalue()

    def opened_result(self):
        return {"mode": "opened", "status": "Review opened rewards.", "can_use": True,
                "family": "Family 3", "candidates": [3], "sockets": 10, "recipe_sockets": 10,
                "socket_source": "opened icons", "first_line_gap": 60, "list_complete": False,
                "first_recipe": "Perfect Chaos Orb x3", "next_recipe": "Perfect Exalted Orb x3",
                "opened_recipes": [{"recipe": name, "ocr_score": .99, "match_score": 1}
                                   for name in ("Perfect Chaos Orb x3", "Perfect Exalted Orb x3")],
                "_target_map_id": logger.get_state()["current_map_id"], **logger.scan_context()}

    def capture_file(self, mode="opened"):
        self.window.mode = mode
        select(self.window.mode_select, mode)
        path = Path(self.tmp.name) / "remnant.png"
        path.write_bytes(self.image_bytes())
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            self.window.scan_file()
        return self.jobs[-1][1]

    def capture_inventory(self, color=(120, 130, 140), live=False):
        self.window._inventory_captured(Image.new("RGB", (480, 200), color), live=live)
        return self.jobs[-1][1]

    def inventory_result(self, quantity=7):
        return {"items": [{"slot": 1, "name": "Chaos Orb", "quantity": quantity}], "unknown": []}

    def capture_ritual(self):
        self.window._ritual_captured(Image.new("RGB", (200, 100)), live=False)
        return self.jobs[-1][1]

    def task_error(self, callback, text="Recognition unavailable"):
        self.window._pending_tasks["failed"] = (callback, logger.session_generation())
        self.window._task_done("failed", None, RuntimeError(text))

    def seed_result(self, color=(120, 130, 140)):
        with logger._connect() as db:
            stage = dict(db.execute("SELECT * FROM seed_states WHERE family=3 LIMIT 1").fetchone())
        result = {"mode": "seed", "remnants": [{"sockets": stage["sockets"],
                  "seed_slot": stage["seed_slot"], "seed_rune": stage["seed_rune"],
                  "family": "Family 3", "candidates": [3], "can_commit": True}], **logger.scan_context()}
        self.window.show_result("seed", result, self.image_bytes(color))

    def reference_job(self):
        self.seed_result()
        self.window.save_seed_scan()
        return self.jobs[-1]

    def two_seed_reference_job(self):
        self.seed_result()
        reading = dict(self.window._seed_readings[0])
        result = {**self.window.results["seed"], "remnants": [
            {**reading, "scan_index": index + 1, "bar_bounds": bounds}
            for index, bounds in enumerate((
                {"x": 80, "y": 60, "width": 180, "height": 20},
                {"x": 320, "y": 200, "width": 180, "height": 20}))]}
        image = Image.new("RGB", (600, 400), (120, 130, 140))
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 60, 259, 79), fill=(180, 50, 40))
        draw.rectangle((320, 200, 499, 219), fill=(40, 50, 180))
        raw = io.BytesIO()
        image.save(raw, format="PNG")
        self.window.show_result("seed", result, raw.getvalue())
        self.window.seed_table.setCurrentCell(0, 2)
        self.window.save_seed_scan()
        return self.jobs[-1]

    def persist_reference(self, work):
        with patch.object(store, "_reviewed_vector", return_value=None):
            return work()

    def test_discard_cancels_delayed_remnant_and_autosave(self):
        self.window.set_auto_commit(True)
        self.window.show_result("opened", self.opened_result())
        callback = self.capture_file()
        result = self.opened_result()
        self.window.discard_scan()
        callback(result)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertFalse(self.window.approve_scan_button.isEnabled())

    def test_undo_clears_waystone_draft_and_preserves_saved_history(self):
        self.window.waystone.setText("50")
        self.window.save_map_settings()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 7}])
        logger.save_ritual_page([{"category": "Item", "name": "Reward", "quantity": 2}])
        self.window.finish_map()
        before = logger.export_all_csv()
        with logger._connect() as db:
            history = [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]
        result = parse_item_text("Item Class: Waystones\nRarity: Rare\nStorm Peak\nWaystone (Tier 16)\n--------\nWaystone Drop Chance: +87%\n--------\nItem Level: 82\n--------\n30% increased Rarity of Items found in this Area", self.window.state["affixes"])
        result.update(logger.scan_context())
        self.window._hover_item_read(result)
        self.window.undo_map()
        self.assertEqual(self.window.state["current_map_id"], "M0001")
        self.assertEqual(self.window.waystone.text(), "50")
        self.assertIsNone(self.window.pending_review_kind)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)
        self.assertEqual(logger.export_all_csv(), before)
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")], history)

    def test_failed_undo_preserves_pending_draft(self):
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 7}])
        self.window._review_pending("waystone", "Review waystone")
        self.window.waystone.setText("87")
        with self.assertRaisesRegex(ValueError, "saved activity"):
            self.window.undo_map()
        self.assertEqual(self.window.pending_review_kind, "waystone")
        self.assertEqual(self.window.waystone.text(), "87")

    def test_reference_save_finishes_without_linking_to_later_map(self):
        work, callback = self.reference_job()
        self.window.finish_map()
        self.window.show_result("opened", self.opened_result())
        saved = self.persist_reference(work)
        callback(saved)
        self.assertIsNone(self.window.saved_scan)
        self.window.approve_remnant_scan()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scans WHERE id=?", (saved["id"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_links").fetchone()[0], 0)

    def test_reference_save_does_not_attach_to_replacement_capture(self):
        work, callback = self.reference_job()
        self.seed_result((140, 130, 120))
        callback(self.persist_reference(work))
        self.assertIsNone(self.window.saved_scan)

    def test_reference_callback_distinguishes_identical_seed_labels(self):
        work, callback = self.two_seed_reference_job()
        original = self.window._seed_readings[0]
        self.window.seed_table.setCurrentCell(1, 2)
        saved = self.persist_reference(work)
        callback(saved)
        self.assertIsNot(self.window._seed_readings[self.window.seed_table.currentRow()], original)
        self.assertIsNone(self.window.saved_scan)
        with Image.open(store.image_for(saved["id"])) as screenshot:
            self.assertEqual(screenshot.getpixel((75, 70)), (180, 50, 40))

    def test_selecting_another_identical_seed_releases_saved_reference(self):
        work, callback = self.two_seed_reference_job()
        saved = self.persist_reference(work)
        callback(saved)
        self.assertEqual(self.window.saved_scan["id"], saved["id"])
        self.window.seed_table.setCurrentCell(1, 2)
        self.assertIsNone(self.window.saved_scan)
        self.window.show_result("opened", self.opened_result())
        self.window.approve_remnant_scan()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_links").fetchone()[0], 0)

    def test_current_seed_reference_remains_associated(self):
        work, callback = self.reference_job()
        saved = self.persist_reference(work)
        callback(saved)
        self.assertEqual(self.window.saved_scan["id"], saved["id"])

    def test_reference_completion_can_link_original_seed_and_opened_view(self):
        work, callback = self.reference_job()
        self.window.show_result("opened", self.opened_result())
        saved = self.persist_reference(work)
        callback(saved)
        self.assertEqual(self.window.saved_scan["id"], saved["id"])
        self.window.approve_remnant_scan()
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT remnant_id,scan_id FROM scan_links")], [("R0001", saved["id"])])

    def test_clear_tablets_drops_inflight_autosave_result(self):
        self.window.set_auto_tablets(True)
        revision = service.HOTKEY._capture_revision
        event = {"mode": "item", "error": "", "result": {"kind": "tablet",
                 "mods": ["30% increased Pack Size"], "matches": [], "uncertain": [],
                 "source": "clipboard", **logger.scan_context()}}
        service.HOTKEY._capture_lock.acquire()
        try:
            self.window.clear_tablets()
        finally:
            service.HOTKEY._finish_capture(event, None, revision)
        self.window.poll()
        self.assertIsNone(service.HOTKEY.status()["latest"])
        self.assertEqual(logger.tablet_next_slot(), 1)
        self.assertFalse(any(row["affix"] for row in logger.get_state()["settings"]["tablet_affixes"]))
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_scan_failures_release_reading_state_and_allow_rejection(self):
        for kind, capture, field, save in (
                ("currency", self.capture_inventory, "_inventory_reading", self.window.save_inventory),
                ("ritual", self.capture_ritual, "_ritual_reading", self.window.save_ritual),
                ("remnant", self.capture_file, "_remnant_reading", self.window.commit_remnant),
                ("seed", lambda: self.capture_file("seed"), "_remnant_reading", self.window.commit_review)):
            with self.subTest(kind=kind):
                callback = capture()
                self.task_error(callback)
                self.assertIsNone(getattr(self.window, field))
                self.assertFalse(self.window.approve_scan_button.isEnabled())
                self.assertIn("Scan failed", self.window.review_summary.text())
                with self.assertRaisesRegex(ValueError, "scan failed"):
                    save()
                self.window.reject_review()
                self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_executor_failure_reaches_failed_review(self):
        self.window._submit = LoggerWindow._submit.__get__(self.window, LoggerWindow)
        with patch.object(native_desktop.item_ocr, "scan_inventory_grid", side_effect=RuntimeError("Recognition unavailable")):
            self.window._inventory_captured(Image.new("RGB", (480, 200)), live=False)
            deadline = time.monotonic() + 3
            while self.window._pending_tasks and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertFalse(self.window._pending_tasks)
        self.assertIsNone(self.window._inventory_reading)
        self.assertIn("Recognition unavailable", self.window.review_summary.text())
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_callback_context_failure_reports_finished_scan(self):
        callback = self.capture_file()
        result = self.opened_result()
        logger.save_settings({"expedition": 2})
        self.window._pending_tasks["result"] = (callback, logger.session_generation())
        self.window._task_done("result", result, None)
        self.assertIsNone(self.window._remnant_reading)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertIn("expedition changed", self.window.review_summary.text())
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_failed_inventory_retry_preserves_clear_autosave(self):
        self.window.set_auto_all(True)
        self.task_error(self.capture_inventory(live=True))
        callback = self.capture_inventory((140, 130, 120), live=True)
        callback(self.inventory_result(9))
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 9})
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_stale_failure_cannot_clear_or_replace_newer_review(self):
        older = self.capture_inventory()
        latest = self.capture_inventory((140, 130, 120))
        self.task_error(older, "Old recognition failed")
        self.assertIsNotNone(self.window._inventory_reading)
        latest(self.inventory_result(9))
        summary = self.window.review_summary.text()
        self.task_error(older, "Old recognition failed again")
        self.assertEqual(self.window.review_summary.text(), summary)
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.assertEqual(self.window.save_inventory()["start"], {"Chaos Orb": 9})


if __name__ == "__main__":
    unittest.main()
