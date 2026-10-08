import csv
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui import native_desktop
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class ScanContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-scan-context-")
        self.original_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.callbacks = []
        self.window._submit = lambda label, work, done=None: self.callbacks.append(done)
        self.grid_patch = patch.object(native_desktop.item_ocr, "inventory_grid", side_effect=lambda image: image)
        self.grid_patch.start()

    def tearDown(self):
        self.grid_patch.stop()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.original_data_dir
        logger._READY = False
        self.tmp.cleanup()

    def inventory_result(self, quantity=7):
        return {"items": [{"slot": 1, "name": "Chaos Orb", "quantity": quantity}], "unknown": []}

    def ritual_result(self, name="Reward"):
        return {"items": [{"category": "Item", "name": name, "quantity": 1,
                           "tribute": 10, "source": name, "score": 1}], "raw_text": name}

    def capture_inventory(self, phase="start", live=False, color=(10, 20, 30)):
        select(self.window.inventory_phase, phase)
        self.window._inventory_captured(Image.new("RGB", (480, 200), color), live=live)
        return self.callbacks[-1]

    def choose_inventory_phase(self, phase):
        # Emit the actual combo-box signal; select() deliberately blocks it.
        self.window.inventory_phase.setCurrentIndex(self.window.inventory_phase.findData(phase))

    def capture_ritual(self, color=(10, 20, 30), live=False):
        image = Image.new("RGB", (200, 100), color)
        self.window._ritual_captured(image, live=live)
        return self.callbacks[-1], hashlib.sha256(image.tobytes()).hexdigest()

    def ritual_fingerprints(self):
        with logger._connect() as db:
            return [row[0] for row in db.execute("SELECT scan_hash FROM ritual_pages ORDER BY page_number")]

    def test_inventory_preserves_phase_changed_during_ocr(self):
        callback = self.capture_inventory()
        self.assertFalse(self.window.inventory_phase.isEnabled())
        self.choose_inventory_phase("end")
        self.assertEqual(self.window.inventory_phase.currentData(), "start")
        callback(self.inventory_result())
        self.assertTrue(self.window.inventory_phase.isEnabled())
        self.assertEqual(self.window.inventory_phase.currentData(), "start")
        saved = self.window.save_inventory()
        self.assertEqual(saved["start"], {"Chaos Orb": 7})
        self.assertEqual(saved["end"], {})

    def test_completed_review_explicit_phase_change_updates_visible_and_saved_phase(self):
        callback = self.capture_inventory("end")
        callback(self.inventory_result())
        self.choose_inventory_phase("start")
        self.assertEqual(self.window._pending_currency_phase, "start")
        self.assertEqual(self.window._inventory_capture_context["phase"], "start")
        self.assertTrue(self.window.review_summary.text().startswith("Start inventory"))
        self.assertIn("start inventory", self.window.approve_scan_button.toolTip())
        saved = self.window.save_inventory()
        self.assertEqual(saved["start"], {"Chaos Orb": 7})
        self.assertEqual(saved["end"], {})

    def test_end_selection_and_bottom_approval_feed_counter_and_export_without_start_baseline(self):
        callback = self.capture_inventory("start")
        result = self.inventory_result()
        result["items"][0]["count_needs_review"] = True
        callback(result)
        self.choose_inventory_phase("end")
        self.assertTrue(self.window.review_summary.text().startswith("End inventory"))
        row = self.window.inventory_table.cellWidget(0, 3)
        row.findChild(QPushButton, "approveCurrency").click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.session_currency_totals()["items"], [])
        self.window.approve_scan_button.click()
        saved = logger.currency_for_map("M0001")
        self.assertEqual((saved["start"], saved["end"], saved["net"]),
                         ({}, {"Chaos Orb": 7}, {"Chaos Orb": 7}))
        self.assertEqual(saved["start_baseline"], "Assumed empty")
        self.assertEqual(self.window.session_currency.cards["Chaos Orb"].quantity, 7)
        exported = list(csv.DictReader(io.StringIO(logger.export_currency_csv().decode("utf-8-sig"))))
        self.assertEqual(len(exported), 1)
        self.assertEqual((exported[0]["Map ID"], exported[0]["Start Count"], exported[0]["End Count"],
                          exported[0]["Session Found Quantity"]), ("M0001", "0", "7", "7"))
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_pending_next_map_snapshot_cannot_change_phase_to_previous_map(self):
        logger.finish_map(0, 0, 0, 0)
        callback = self.capture_inventory("start")
        callback(self.inventory_result())
        self.assertEqual(self.window._pending_currency_map, "M0002")
        self.choose_inventory_phase("end")
        self.assertEqual(self.window.inventory_phase.currentData(), "start")
        self.assertEqual(self.window._pending_currency_phase, "start")
        self.assertIn("another map", self.window.statusBar().currentMessage())
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0002")["start"], {"Chaos Orb": 7})
        self.assertEqual(logger.currency_for_map("M0001")["end"], {})

    def test_silent_phase_changes_cannot_save_a_mislabelled_snapshot(self):
        self.capture_inventory("start")(self.inventory_result())
        # A blocked programmatic change must not bypass the explicit phase policy.
        select(self.window.inventory_phase, "end")
        with self.assertRaisesRegex(ValueError, "selection no longer matches"):
            self.window.save_inventory()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})

    def test_inventory_auto_commit_preserves_phase(self):
        self.window.state["settings"]["ocr_auto_commit"] = True
        callback = self.capture_inventory(live=True)
        self.choose_inventory_phase("end")
        callback(self.inventory_result())
        saved = logger.currency_for_map("M0001")
        self.assertEqual(saved["start"], {"Chaos Orb": 7})
        self.assertEqual(saved["end"], {})
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_manual_inventory_save_uses_selected_phase(self):
        self.window.add_inventory_row({"name": "Chaos Orb", "quantity": 4})
        self.choose_inventory_phase("end")
        saved = self.window.save_inventory()
        self.assertEqual(saved["end"], {"Chaos Orb": 4})
        self.assertEqual(saved["start"], {})

    def test_saved_inventory_releases_capture_phase_for_manual_save(self):
        self.capture_inventory()(self.inventory_result())
        self.window.save_inventory()
        self.choose_inventory_phase("end")
        saved = self.window.save_inventory()
        self.assertEqual(saved["start"], {"Chaos Orb": 7})
        self.assertEqual(saved["end"], {"Chaos Orb": 7})

    def test_inventory_overlapping_callbacks_keep_latest_capture(self):
        older = self.capture_inventory("start")
        newer = self.capture_inventory("end", color=(30, 20, 10))
        older(self.inventory_result(99))
        self.assertEqual(self.window.inventory_table.rowCount(), 0)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        newer(self.inventory_result(3))
        older(self.inventory_result(99))
        saved = self.window.save_inventory()
        self.assertEqual(saved["end"], {"Chaos Orb": 3})
        self.assertEqual(saved["start"], {})
        self.assertEqual(self.window._inventory_capture.getpixel((0, 0)), (30, 20, 10))

    def test_ritual_out_of_order_callbacks_keep_latest_fingerprint(self):
        older, _ = self.capture_ritual()
        newer, fingerprint = self.capture_ritual((30, 20, 10))
        newer(self.ritual_result("Latest reward"))
        older(self.ritual_result("Old reward"))
        self.window.save_ritual()
        pages = logger.ritual_pages_for_map("M0001")
        self.assertEqual([page["items"][0]["name"] for page in pages], ["Latest reward"])
        self.assertEqual(self.ritual_fingerprints(), [fingerprint])
        self.assertEqual(self.window.preview.pixmap().toImage().pixelColor(0, 0).getRgb()[:3], (30, 20, 10))

    def test_ritual_stale_callback_cannot_enable_approval(self):
        older, _ = self.capture_ritual()
        newer, fingerprint = self.capture_ritual((30, 20, 10))
        older(self.ritual_result("Old reward"))
        self.assertEqual(self.window.ritual_table.rowCount(), 0)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        newer(self.ritual_result("Latest reward"))
        self.window.save_ritual()
        self.assertEqual(self.ritual_fingerprints(), [fingerprint])

    def test_ritual_sequential_captures_save_distinct_pages(self):
        first, first_hash = self.capture_ritual()
        first(self.ritual_result("Reward A"))
        self.window.save_ritual()
        second, second_hash = self.capture_ritual((30, 20, 10))
        second(self.ritual_result("Reward B"))
        self.window.save_ritual()
        pages = logger.ritual_pages_for_map("M0001")
        self.assertEqual([page["items"][0]["name"] for page in pages], ["Reward A", "Reward B"])
        self.assertEqual(self.ritual_fingerprints(), [first_hash, second_hash])

    def test_ritual_identical_capture_updates_same_page(self):
        first, fingerprint = self.capture_ritual()
        first(self.ritual_result("Reward"))
        self.assertFalse(self.window.save_ritual()["updated"])
        second, _ = self.capture_ritual()
        second(self.ritual_result("Corrected reward"))
        self.assertTrue(self.window.save_ritual()["updated"])
        self.assertEqual(self.ritual_fingerprints(), [fingerprint])

    def test_ritual_auto_commit_ignores_overlapping_stale_result(self):
        self.window.state["settings"]["ocr_auto_commit"] = True
        older, _ = self.capture_ritual(live=True)
        newer, fingerprint = self.capture_ritual((30, 20, 10), live=True)
        older(self.ritual_result("Old reward"))
        newer(self.ritual_result("Latest reward"))
        older(self.ritual_result("Old reward"))
        self.assertEqual(self.ritual_fingerprints(), [fingerprint])
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_pending_captures_cannot_be_saved_or_revived_after_rejection(self):
        inventory = self.capture_inventory()
        with self.assertRaisesRegex(ValueError, "finish"):
            self.window.save_inventory()
        self.window.reject_review()
        inventory(self.inventory_result())
        ritual, _ = self.capture_ritual()
        with self.assertRaisesRegex(ValueError, "finish"):
            self.window.save_ritual()
        self.window.reject_review()
        ritual(self.ritual_result())
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_inventory_map_guard_uses_capture_phase(self):
        callback = self.capture_inventory()
        logger.finish_map(0, 0, 0, 0)
        self.choose_inventory_phase("end")
        with self.assertRaisesRegex(ValueError, "map ended"):
            callback(self.inventory_result())
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})
        self.assertEqual(logger.currency_for_map("M0002")["start"], {})

    def test_new_map_cancels_pending_capture(self):
        callback, _ = self.capture_ritual()
        self.window.finish_map()
        callback(self.ritual_result())
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.ritual_pages_for_map("M0002"), [])

    def test_reset_cancels_capture_even_when_map_id_is_reused(self):
        callback = self.capture_inventory()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.reset_logger()
        callback(self.inventory_result())
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})

    def test_new_review_cancels_capture_of_other_kind(self):
        inventory = self.capture_inventory()
        ritual, fingerprint = self.capture_ritual()
        inventory(self.inventory_result())
        ritual(self.ritual_result())
        self.window.save_ritual()
        self.assertEqual(self.ritual_fingerprints(), [fingerprint])
        self.assertEqual(logger.currency_for_map("M0001")["start"], {})

    def test_icon_reference_rescan_keeps_explicitly_corrected_review_phase(self):
        self.capture_inventory()(self.inventory_result())
        self.choose_inventory_phase("end")
        select(self.window.icon_name, "Chaos Orb")
        with patch.object(logger, "save_currency_icon", return_value=1):
            self.window.save_icon_example()
        self.callbacks[-1](self.inventory_result(9))
        saved = self.window.save_inventory()
        self.assertEqual(saved["end"], {"Chaos Orb": 9})
        self.assertEqual(saved["start"], {})

    def test_direct_read_helpers_remain_supported(self):
        self.window._inventory_read(self.inventory_result(), live=False, expected_map_id="M0001")
        self.window.save_inventory()
        self.window._ritual_read(self.ritual_result(), live=False, expected_map_id="M0001")
        self.window.save_ritual()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)

    def test_waystone_review_survives_unrelated_refresh(self):
        from PoE2_Data_Logger.ocr.item_text import parse_item_text
        result = parse_item_text("Item Class: Waystones\nRarity: Rare\nStorm Peak\nWaystone (Tier 16)\n--------\nWaystone Drop Chance: +87%\n--------\nItem Level: 82\n--------\n30% increased Rarity of Items found in this Area", self.window.state["affixes"])
        self.window._hover_item_read(result)
        self.assertEqual(self.window.waystone.text(), "87")
        self.window.waystone.setText("90")
        self.window.refresh()
        self.assertEqual(self.window.waystone.text(), "90")
        self.assertEqual(self.window.tier.currentData(), 16)
        self.assertEqual(self.window.waystone_name.text(), "Storm Peak")
        self.window.save_map_settings()
        self.assertEqual(logger.get_state()["settings"]["waystone"], 90)

    def test_starting_map_clears_unapproved_waystone(self):
        self.window.pending_review_kind = "waystone"
        self.window.waystone.setText("87")
        self.window.finish_map()
        self.assertEqual(self.window.waystone.text(), "0")
        self.assertIsNone(self.window.pending_review_kind)

    def test_new_map_clears_old_currency_and_ritual_review_rows(self):
        self.capture_inventory()(self.inventory_result())
        self.window.save_inventory()
        callback, _ = self.capture_ritual()
        callback(self.ritual_result())
        self.window.save_ritual()
        self.window.finish_map()
        self.assertEqual(self.window.inventory_table.rowCount(), 0)
        self.assertEqual(self.window.ritual_table.rowCount(), 0)
        self.assertIsNone(self.window._ritual_hash)
        self.assertIsNone(self.window._inventory_capture_context)

    def test_clear_auto_scan_does_not_open_overlay_while_reading(self):
        logger.save_settings({"ocr_auto_commit": True})
        self.window.refresh()
        self.window._overlay_enabled = True
        with patch.object(self.window, "show_overlay") as show:
            callback = self.capture_inventory(live=True)
            self.app.processEvents()
            show.assert_not_called()
            callback(self.inventory_result())
            self.app.processEvents()
            show.assert_not_called()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_uncertain_scan_still_opens_review_overlay(self):
        logger.save_settings({"ocr_auto_commit": True})
        self.window.refresh()
        self.window._overlay_enabled = True
        with patch.object(self.window, "show_overlay") as show:
            callback = self.capture_inventory(live=True)
            self.app.processEvents()
            result = self.inventory_result()
            result["items"][0]["count_needs_review"] = True
            callback(result)
            self.app.processEvents()
            show.assert_called_once_with(automatic=True)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_hotkey_delivery_keeps_original_inventory_phase(self):
        from PoE2_Data_Logger.core import service
        buffer = __import__("io").BytesIO()
        Image.new("RGB", (480, 200)).save(buffer, format="PNG")
        event = {"id": 100, "error": "", "mode": "currency", "result":
                 {"map_id": "M0001", "phase": "start", **logger.scan_context()}}
        select(self.window.inventory_phase, "end")
        status = {**service.HOTKEY.status(), "latest": event}
        with patch.object(service.HOTKEY, "status", return_value=status), patch.object(
                service.HOTKEY, "image", return_value=buffer.getvalue()):
            self.window.poll()
        self.callbacks[-1](self.inventory_result())
        self.assertEqual(self.window.save_inventory()["start"], {"Chaos Orb": 7})

    def file_capture(self):
        import io
        from PySide6.QtWidgets import QFileDialog
        raw = io.BytesIO()
        Image.new("RGB", (600, 400), (190, 190, 190)).save(raw, format="PNG")
        path = Path(self.tmp.name) / "remnant.png"
        path.write_bytes(raw.getvalue())
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            self.window.scan_file()
        return self.callbacks[-1]

    def opened_result(self):
        return {"mode": "opened", "status": "Review the opened rewards before logging.",
                "can_use": True, "family": "Family 3", "candidates": [3], "sockets": 10,
                "recipe_sockets": 10, "socket_source": "opened icons", "first_line_gap": 60,
                "list_complete": False, "first_recipe": "Perfect Chaos Orb x3", "next_recipe": "Perfect Exalted Orb x3",
                "opened_recipes": [{"recipe": name, "ocr_score": .99, "match_score": 1}
                                   for name in ("Perfect Chaos Orb x3", "Perfect Exalted Orb x3")],
                "_target_map_id": "M0001", **logger.scan_context()}

    def test_rejected_file_scan_cannot_create_pending_remnant(self):
        callback = self.file_capture()
        self.window.reject_review()
        callback(self.opened_result())
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_only_latest_file_scan_creates_pending_remnant(self):
        old = self.file_capture()
        latest = self.file_capture()
        old(self.opened_result())
        self.assertIsNone(logger.get_state()["ocr_pending"])
        latest(self.opened_result())
        self.assertEqual(logger.get_state()["ocr_pending"]["remnant_id"], "R0001")

    def test_review_approval_rejects_expedition_changed_after_scan(self):
        self.capture_inventory()(self.inventory_result())
        logger.save_settings({"expedition": 2})
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            self.window.save_inventory()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_both_mode_links_partial_opened_list_and_logs_once(self):
        with logger._connect() as db:
            sockets = db.execute("SELECT sockets FROM recipes WHERE name='Perfect Chaos Orb x3'").fetchone()[0]
            stage = dict(db.execute("SELECT * FROM seed_states WHERE family=3 AND sockets=? LIMIT 1", (sockets,)).fetchone())
        select(self.window.mode_select, "both")
        self.window.mode = "both"
        seed = {"mode": "seed", "remnants": [{"sockets": sockets, "seed_slot": stage["seed_slot"],
                 "seed_rune": stage["seed_rune"], "family": "Family 3", "candidates": [3], "can_commit": True}],
                **logger.scan_context()}
        self.window.show_result("seed", seed)
        self.window.approve_remnant_scan()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        opened = self.opened_result()
        opened["sockets"] = opened["recipe_sockets"] = sockets
        self.window.show_result("opened", opened)
        self.assertEqual(self.window._both_link, 0)
        self.window.approve_remnant_scan()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertTrue(self.window._seed_readings[0]["saved"])
        with self.assertRaises(ValueError):
            self.window.show_result("opened", opened)
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)


if __name__ == "__main__":
    unittest.main()
