"""Keep optional OCR observations separate from existing manual map controls."""

import base64
import copy
import csv
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from PoE2_Data_Logger.core import kill_counts, logger_store as logger, service, store
from PoE2_Data_Logger.ui import native_desktop
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow
from PoE2_Data_Logger.ui.region_select import REGIONS, ScanRegionsPage, region_for


class KillCountRegionsUITests(unittest.TestCase):
    """Exercise the actual region page's draft, opt-in and native-pixel mapping."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-kill-region-ui-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.pages = []
        self.page = self.make_page()

    def tearDown(self):
        for page in self.pages:
            page.close()
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def make_page(self):
        page = ScanRegionsPage()
        self.pages.append(page)
        return page

    def enabled(self):
        with logger._connect() as db:
            return logger._meta(db, "kill_count_scan_enabled", False)

    def test_separate_region_uses_own_real_example_and_starts_disabled(self):
        title, group, box, _ = REGIONS["kill_count_region"]
        self.assertIn("four skulls", title)
        self.assertEqual(group, "kills")
        self.assertEqual(box, (.85, .24, .15, .12))
        self.page.select_region("kill_count_region")
        self.assertEqual(self.page.canvas.active, "kill_count_region")
        self.assertFalse(self.page.canvas.picture.isNull())
        with Image.open(self.page.root / "kills.jpg") as example:
            self.assertEqual((self.page.canvas.picture.width(), self.page.canvas.picture.height()), example.size)
        self.assertNotEqual(group, REGIONS["live_region"][1])
        self.assertFalse(self.page.kill_count_capture.isChecked())
        self.assertFalse(self.enabled())

    def test_saving_unrelated_region_does_not_opt_in_to_capture(self):
        self.page.calibrate_region("ritual_region", [.1, .15, .4, .7])
        self.page.save_regions()
        self.assertFalse(self.enabled())
        self.assertFalse(self.make_page().kill_count_capture.isChecked())

    def test_calibration_enables_only_draft_until_explicit_save_and_can_be_disabled(self):
        calibrated = [.82, .21, .18, .15]
        self.page.calibrate_region("kill_count_region", calibrated)
        self.assertTrue(self.page.kill_count_capture.isChecked())
        self.assertFalse(self.enabled())
        self.assertFalse(self.make_page().kill_count_capture.isChecked())
        self.page.save_regions()
        reopened = self.make_page()
        self.assertTrue(self.enabled())
        self.assertTrue(reopened.kill_count_capture.isChecked())
        self.assertEqual(reopened.canvas.boxes["kill_count_region"], calibrated)
        reopened.kill_count_capture.setChecked(False)
        reopened.save_regions()
        self.assertFalse(self.enabled())

    def test_calibrated_box_maps_to_native_resolutions_and_offset_displays(self):
        box = [.81, .22, .18, .14]
        self.page.calibrate_region("kill_count_region", box)
        self.page.save_regions()
        for width, height in ((1920, 1080), (2560, 1440), (3840, 2160)):
            for left, top in ((0, 0), (-2560, 137), (321, -120)):
                with self.subTest(size=(width, height), origin=(left, top)):
                    actual = region_for("kill_count_region", (left, top, left + width, top + height))
                    self.assertEqual(actual, {
                        "x": left + round(box[0] * width),
                        "y": top + round(box[1] * height),
                        "w": round((box[0] + box[2]) * width) - round(box[0] * width),
                        "h": round((box[1] + box[3]) * height) - round(box[1] * height),
                    })

    def test_loading_kill_example_leaves_other_scanner_examples_and_boxes_unchanged(self):
        original_boxes = copy.deepcopy(self.page.canvas.boxes)
        self.page.select_region("live_region")
        opened_image = self.page.canvas.picture.toImage()
        screenshot = Path(self.temporary.name) / "full-game.png"
        Image.new("RGB", (1920, 1080), (11, 22, 33)).save(screenshot)
        self.page.select_region("kill_count_region")
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(screenshot), "")):
            self.page.load_screenshot()
        self.assertTrue((self.page.custom / "kills.jpg").is_file())
        self.assertFalse((self.page.custom / "opened.jpg").exists())
        self.assertEqual(self.page.canvas.boxes, original_boxes)
        self.assertFalse(self.enabled())
        self.page.select_region("live_region")
        self.assertEqual(self.page.canvas.picture.toImage(), opened_image)


class KillCountSnapshotUITests(unittest.TestCase):
    """Persist real auxiliary observations through Qt without replacing manual drafts."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-kill-snapshot-ui-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        image = io.BytesIO()
        Image.new("RGB", (290, 130), (11, 22, 33)).save(image, format="PNG")
        self.evidence = image.getvalue()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def payload(self, *, source="kills", scope="map_final"):
        return {
            "result": {"normal": 984, "magic": 719, "rare": 656, "unique": 4,
                       "complete": True, "confidence": .99, "status": "All four counters read"},
            "context": kill_counts.capture_context(source), "request_id": str(uuid.uuid4()),
            "source_mode": source, "scope": scope,
            "image_base64": base64.b64encode(self.evidence).decode("ascii"),
        }

    def rows(self):
        with logger._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM kill_count_snapshots ORDER BY snapshot_id")]

    def manual_fields(self):
        return self.window.normal, self.window.magic, self.window.rare, self.window.unique

    def draft(self, values=("11", "22", "33", "44")):
        for field, text in zip(self.manual_fields(), values):
            field.setText(text)
        return values

    def assert_draft(self, values):
        self.assertEqual(tuple(field.text() for field in self.manual_fields()), values)

    def map_rows(self):
        return list(csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig"))))

    def poll_event(self, event):
        status = service.HOTKEY.status()
        with patch.object(service.HOTKEY, "status", return_value={**status, "latest": event}):
            self.window.poll()

    def propagation_review(self):
        reading = {"mode": "propagation", "runes": ["Rage"], "positions": [1],
                   "selected_recipe": "Medved's Saga", "can_use": True,
                   **logger.scan_context()}
        self.window._propagation_read(reading, self.evidence)

    def test_final_without_chain_saves_evidence_but_preserves_manual_fields_and_totals(self):
        logger.save_counts(1, 2, 3, unique=4)
        draft = self.draft(("101", "", "not yet entered", "7"))
        before = self.map_rows()[0]
        self.window._queue_kill_count_reading(self.payload())
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scope"], "map_final")
        self.assertEqual(rows[0]["map_id"], "M0001")
        self.assertIsNone(rows[0]["expedition_id"])
        self.assertEqual([rows[0][key] for key in ("normal", "magic", "rare", "unique_kills")], [984, 719, 656, 4])
        self.assertEqual(rows[0]["image_png"], self.evidence)
        self.assert_draft(draft)
        after = self.map_rows()[0]
        for key in ("Normal Kills", "Magic Kills", "Rare Kills", "Unique Kills", "Total Kills"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(logger.get_state()["chain"], [])

    def test_snapshot_leaves_pending_propagation_dropdowns_and_screenshot_untouched(self):
        self.propagation_review()
        draft = self.draft()
        before = logger.get_state()
        reading = copy.deepcopy(self.window._propagation_reading)
        table = self.window.propagation_recipe_table
        selection = table.currentRow()
        dropdowns = [(box, [box.itemText(i) for i in range(box.count())], box.currentText())
                     for box in table.findChildren(native_desktop.QComboBox)]
        self.assertTrue(dropdowns)
        picture = self.window.preview.pixmap().toImage()
        self.assertFalse(picture.isNull())
        self.window._queue_kill_count_reading(self.payload(source="propagation", scope="chain_stage"))
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.assertEqual(self.window._propagation_reading, reading)
        self.assertEqual(table.currentRow(), selection)
        for box, options, chosen in dropdowns:
            self.assertEqual([box.itemText(i) for i in range(box.count())], options)
            self.assertEqual(box.currentText(), chosen)
        self.assertEqual(self.window.preview.pixmap().toImage(), picture)
        self.assert_draft(draft)
        after = logger.get_state()
        for key in ("chain", "detonated", "current_expedition_id"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(self.rows()[0]["scope"], "chain_stage")
        self.assertEqual(self.rows()[0]["expedition_id"], "M0001-E01")

    def test_dedicated_final_event_does_not_route_to_or_clear_other_reviews(self):
        self.propagation_review()
        draft = self.draft()
        event = {"id": 10001, "mode": "kills", "error": "", "result": None,
                 "kill_counts": self.payload()}
        with patch.object(self.window, "show_result", side_effect=AssertionError("primary routing")), \
             patch.object(service.HOTKEY, "image", side_effect=AssertionError("primary evidence")):
            self.poll_event(event)
            self.poll_event(event)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.assert_draft(draft)

    def test_primary_capture_error_does_not_discard_complete_auxiliary_reading(self):
        draft = self.draft()
        event = {"id": 10002, "mode": "currency", "error": "Primary inventory failed", "result": None,
                 "kill_counts": self.payload(source="currency", scope="map_stage")}
        self.poll_event(event)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["scope"], "map_stage")
        self.assertIn("Primary inventory failed", self.window.statusBar().currentMessage())
        self.assert_draft(draft)

    def test_uncertain_or_malformed_auxiliary_reading_does_not_block_propagation_review(self):
        detonated = logger.get_state()["detonated"]
        for index, auxiliary in enumerate(("invalid", {"result": {"complete": False, "normal": None,
                                                                 "status": "Rare skull unclear"}}), 1):
            with self.subTest(auxiliary=auxiliary):
                event = {"id": 11000 + index, "mode": "propagation", "error": "",
                         "result": {"mode": "propagation", "runes": ["Rage"], "positions": [1],
                                    "selected_recipe": "Medved's Saga", "can_use": True,
                                    **logger.scan_context()}, "kill_counts": auxiliary}
                with patch.object(service.HOTKEY, "image", return_value=self.evidence):
                    self.poll_event(event)
                self.assertEqual(self.window.pending_review_kind, "propagation")
                self.assertEqual(self.rows(), [])
                self.assertEqual(logger.get_state()["chain"], [])
                self.assertEqual(logger.get_state()["detonated"], detonated)

    def test_invalid_counts_or_evidence_are_nonfatal_and_never_saved_as_zero(self):
        draft = self.draft()
        for changed in ({"result": {"complete": False}}, {"image_base64": "%%%"},
                        {"request_id": "not-a-uuid"},
                        {"result": {"normal": 1, "magic": None, "rare": 2, "unique": 0, "complete": True}}):
            with self.subTest(changed=changed):
                self.window._queue_kill_count_reading({**self.payload(), **changed})
                self.assertEqual(self.rows(), [])
                self.assertEqual(self.window._pending_kill_count_saves, {})
                self.assert_draft(draft)

    def test_duplicate_callbacks_save_exactly_one_snapshot_and_one_commit(self):
        payload = self.payload()
        commits = logger.get_state()["scan_commit_count"]
        self.window._queue_kill_count_reading(payload)
        self.window._queue_kill_count_reading(copy.deepcopy(payload))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(logger.get_state()["scan_commit_count"], commits + 1)

    def test_delayed_callback_after_map_transition_is_rejected(self):
        payload = self.payload()
        self.window.finish_map()
        draft = self.draft(("9", "8", "7", "6"))
        commits = logger.get_state()["scan_commit_count"]
        self.window._queue_kill_count_reading(payload)
        self.assertEqual(self.rows(), [])
        self.assertEqual(logger.get_state()["scan_commit_count"], commits)
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertIn("changed", self.window.statusBar().currentMessage())
        self.assert_draft(draft)

    def test_temporary_writer_lock_retains_original_request_then_retries_once(self):
        payload = self.payload()
        draft = self.draft()
        callbacks = []
        blocker = sqlite3.connect(store.DATA_DIR / "scans.sqlite3")
        try:
            blocker.execute("BEGIN IMMEDIATE")
            with patch.object(native_desktop.QTimer, "singleShot", side_effect=lambda _delay, callback: callbacks.append(callback)):
                self.window._queue_kill_count_reading(payload)
            self.assertEqual(len(callbacks), 1)
            self.assertIn(payload["request_id"], self.window._pending_kill_count_saves)
            self.assert_draft(draft)
            blocker.rollback()
        finally:
            blocker.close()
        callbacks[0]()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["request_id"], payload["request_id"])
        self.assertEqual(self.window._pending_kill_count_saves, {})
        self.assert_draft(draft)

    def test_manual_save_and_finish_map_still_use_exact_entered_values(self):
        self.window._queue_kill_count_reading(self.payload())
        original_parent = self.window.normal.parentWidget()
        self.draft(("11", "22", "33", "44"))
        self.window.save_counts()
        saved = self.map_rows()[0]
        self.assertEqual([saved[key] for key in ("Normal Kills", "Magic Kills", "Rare Kills", "Unique Kills", "Total Kills")],
                         ["11", "22", "33", "44", "110"])
        self.draft(("15", "25", "35", "45"))
        self.window.finish_map()
        finished = self.map_rows()[0]
        self.assertEqual([finished[key] for key in ("Normal Kills", "Magic Kills", "Rare Kills", "Unique Kills", "Total Kills")],
                         ["15", "25", "35", "45", "120"])
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assert_draft(("", "", "", ""))
        self.assertIs(self.window.normal.parentWidget(), original_parent)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["normal"], 984)

    def test_existing_invalid_manual_entry_still_blocks_new_map_and_keeps_draft(self):
        self.window._queue_kill_count_reading(self.payload())
        draft = self.draft(("10", "20", "30", "not a number"))
        commits = logger.get_state()["scan_commit_count"]
        with self.assertRaises(ValueError):
            self.window.finish_map()
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assertEqual(logger.get_state()["scan_commit_count"], commits)
        self.assert_draft(draft)
        self.assertEqual(len(self.rows()), 1)

    def test_new_direct_key_is_after_existing_overlay_and_preserves_original_order(self):
        self.assertEqual(tuple(self.window.direct_hotkey_labels),
                         ("remnant", "waystone", "tablet", "currency", "ritual", "propagation", "overlay", "kills"))
        self.assertEqual(self.window.direct_hotkey_labels["kills"].text(), "Off")
        self.assertTrue(self.window.kill_counts_group.isAncestorOf(self.window.normal))
        self.assertEqual(tuple(field.placeholderText() for field in self.manual_fields()),
                         ("Normal kills", "Magic kills", "Rare kills", "Unique kills"))

    def test_kills_only_shortcut_indicator_requires_registration_and_game_focus(self):
        status = service.HOTKEY.status()
        for focused, registered, expected in ((True, True, "ACTIVE"),
                                               (False, True, "INACTIVE"),
                                               (True, False, "INACTIVE")):
            with self.subTest(focused=focused, registered=registered):
                kills_only = {**status, "combo": "", "combos": {"kills": "Ctrl+Shift+F10"},
                              "registered": registered}
                with patch.object(service.HOTKEY, "status", return_value=kills_only), \
                     patch.object(service.HOTKEY, "focused", new=lambda: focused):
                    self.window._set_ocr_indicator()
                self.assertEqual(self.window.stat_values[1].text(), expected)

    def test_save_as_confirms_kill_companion_without_touching_files_on_decline(self):
        destination = Path(self.temporary.name) / "data.csv"
        companion = destination.with_name("data_Kill_Counts.csv")
        companion.write_bytes(b"previous kill readings")
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "CSV (*.csv)")), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question, \
             patch.object(self.window, "_submit") as submit:
            self.window.save_as("csv")
        self.assertEqual(companion.read_bytes(), b"previous kill readings")
        self.assertFalse(destination.exists())
        self.assertIn(str(companion), question.call_args.args[2])
        submit.assert_not_called()

    def test_save_as_writes_all_four_csvs_from_one_database_snapshot(self):
        self.window._queue_kill_count_reading(self.payload())
        destination = Path(self.temporary.name) / "data.csv"
        saved = []
        def submit(_label, work, _done):
            saved.append(work())
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "CSV (*.csv)")), \
             patch.object(self.window, "_submit", side_effect=submit), \
             patch.object(logger, "export_primary_csv", wraps=logger.export_primary_csv) as primary, \
             patch.object(logger, "export_atlas_csv", wraps=logger.export_atlas_csv) as atlas, \
             patch.object(logger, "export_all_csv", wraps=logger.export_all_csv) as history, \
             patch.object(kill_counts, "export_csv", wraps=kill_counts.export_csv) as kills:
            self.window.save_as("csv")
        self.assertEqual(len(saved), 1)
        for suffix in ("", "_Atlas", "_Scan_History", "_Kill_Counts"):
            self.assertTrue(destination.with_name("data" + suffix + ".csv").is_file())
        connections = [export.call_args.kwargs["_db"] for export in (primary, atlas, history, kills)]
        self.assertTrue(all(connection is connections[0] for connection in connections))
        with destination.with_name("data_Kill_Counts.csv").open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Normal Kills (Observed)"], "984")
        self.assertEqual(saved[0]["kill_counts_path"], str(destination.with_name("data_Kill_Counts.csv")))


if __name__ == "__main__":
    unittest.main()
