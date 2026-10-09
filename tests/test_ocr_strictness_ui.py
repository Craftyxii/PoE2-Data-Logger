"""Exercise per-scan strictness settings and approval through the visible desktop controls."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, ocr_sensitivity, store
from PoE2_Data_Logger.ocr import item_ocr, item_text
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, clear_currency_read, clear_ritual_read, clear_waystone_read


class OCRStrictnessUITests(unittest.TestCase):
    """Check defaults, persistence, in-flight settings and manual approval at maximum strictness."""

    @classmethod
    def setUpClass(cls):
        """Reuse the Qt application for keyboard and mouse interactions."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create isolated saved data and hold queued scanner jobs for capture-policy checks."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-strictness-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))

    def tearDown(self):
        """Close UI workers and restore storage without retaining test preferences."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def test_visible_tab_keyboard_preferences_reset_and_help_navigation(self):
        """Operate all seven independent sliders and retain the existing help and Atlas pages."""
        self.window.show()
        nav = next(button for button in self.window.nav_buttons if button.text() == "OCR Sensitivity")
        QTest.mouseClick(nav, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.page_title.text(), "OCR Sensitivity")
        self.assertEqual(self.window.tabs.currentIndex(), 13)
        self.assertEqual(len(self.window.ocr_sensitivity_sliders), 7)
        defaults = {kind: 50 for kind, _ in ocr_sensitivity.SCAN_TYPES}
        for kind, slider in self.window.ocr_sensitivity_sliders.items():
            page = self.window.tabs.currentWidget()
            page.ensureWidgetVisible(slider)
            QTest.keyClick(slider, Qt.Key.Key_End)
            self.assertEqual(ocr_sensitivity.saved_values()[kind], 100)
            self.assertEqual(self.window.ocr_sensitivity_labels[kind].text(), "100")
        self.window.ocr_sensitivity_reset.click()
        self.assertEqual(ocr_sensitivity.saved_values(), defaults)
        for action, title in zip(self.window.help_menu.actions(), ("README", "Licenses", "Disclaimer")):
            action.trigger()
            self.assertEqual(self.window.page_title.text(), title)
        self.window.nav_buttons[-1].click()
        self.assertEqual(self.window.tabs.currentIndex(), 12)

    def test_atomic_validation_corrupt_preferences_and_no_research_commits(self):
        """Invalid changes cannot partially overwrite preferences or enter map/export data."""
        defaults = ocr_sensitivity.saved_values()
        before = logger.get_state()["scan_commit_count"]
        for invalid in (-1, 101, True, 1.5, "50", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ocr_sensitivity.save_values({**defaults, "currency": 0, "ritual": invalid})
            self.assertEqual(ocr_sensitivity.saved_values(), defaults)
        with logger._connect() as db:
            logger._set_meta(db, "ritual_ocr_strictness", "invalid")
        self.assertEqual(ocr_sensitivity.saved_values(), defaults)
        ocr_sensitivity.save_values({**defaults, "currency": 0})
        self.assertEqual(logger.get_state()["scan_commit_count"], before)
        self.assertNotIn("currency_ocr_strictness", logger.get_state()["settings"])
        self.assertNotIn(b"ocr_strictness", logger.export_all_csv())
        with patch.object(ocr_sensitivity, "save_values", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.window.save_ocr_sensitivity("currency", 100)
        self.assertEqual(self.window.ocr_sensitivity_sliders["currency"].value(), 50)

    def test_currency_and_ritual_workers_freeze_their_own_capture_values(self):
        """Changing preferences after queuing work must not reinterpret that capture."""
        values = ocr_sensitivity.saved_values()
        ocr_sensitivity.save_values({**values, "currency": 0, "ritual": 100})
        image = Image.new("RGB", (480, 200), "navy")
        self.window._inventory_captured(image, live=False)
        currency_work, _ = self.jobs[-1]
        self.window.discard_scan()
        self.window._ritual_captured(image, live=False, expected_strictness=25)
        ritual_work, _ = self.jobs[-1]
        ocr_sensitivity.save_values(values)
        # Each worker's cancellation guard needs its own current capture restored.
        inventory = self.window._inventory_capture_context
        self.window._inventory_reading = inventory
        with patch.object(item_ocr, "scan_inventory_grid", return_value={}) as scan:
            currency_work()
            self.assertEqual(scan.call_args.kwargs["strictness"], 0)
        with patch.object(item_ocr, "scan_ritual_page", return_value={}) as scan:
            ritual_work()
            self.assertEqual(scan.call_args.kwargs["strictness"], 25)

    def test_loose_ocr_can_accept_tentative_evidence_but_missing_data_still_holds(self):
        """Different evidence floors cannot bypass missing counts, modifiers or Ritual prices."""
        waystone = {"source": "screen OCR", "fields": {"tier": 15, "waystone": 100, "map_mods": 1},
                    "mods": ["20% increased Quantity"], "ocr_rows": [{"text": "modifier", "score": .85}]}
        self.assertFalse(clear_waystone_read(waystone))
        self.assertTrue(clear_waystone_read({**waystone, "_ocr_strictness": 0}))
        self.assertFalse(clear_waystone_read({**waystone, "_ocr_strictness": 100}))
        self.assertFalse(clear_waystone_read({**waystone, "fields": {}, "_ocr_strictness": 0}))
        item = {"name": "Omen of Light", "category": "Omen", "name_match": .86,
                "score": .85, "quantity": 1, "tribute": 100, "source": "Omen of Light 100"}
        ritual = {"items": [item]}
        self.assertFalse(clear_ritual_read(ritual, [item["name"]]))
        self.assertTrue(clear_ritual_read({**ritual, "_ocr_strictness": 0}, [item["name"]]))
        self.assertFalse(clear_ritual_read({**ritual, "_ocr_strictness": 100}, [item["name"]]))
        item["tribute"] = None
        self.assertFalse(clear_ritual_read({**ritual, "_ocr_strictness": 0}, [item["name"]]))

    def test_thresholds_increase_preserve_default_and_bound_confidence(self):
        """Zero is looser than fifty and one hundred in every supported score scale."""
        for base, spread in ((.94, .14), (.9, .1), (.8, .2), (.035, .025), (-3000, 800), (80, 20)):
            floors = [ocr_sensitivity.clear_threshold(base, level, spread) for level in (0, 50, 100)]
            self.assertEqual(floors[1], base)
            self.assertLess(floors[0], floors[1])
            self.assertLess(floors[1], floors[2])
            if 0 <= base <= 1:
                self.assertGreaterEqual(floors[0], 0)
                self.assertLessEqual(floors[2], 1)

    def test_maximum_currency_requires_row_confirmation_then_saves_once(self):
        """An exact named read stays held at 100; explicit row approval saves the correct count."""
        reader = Mock()
        reader.icon.side_effect = [{"family": "chaos", "members": ["Chaos Orb"], "score": 1}] + [{"empty": True}] * 59
        reader.count.return_value = 23
        image = Image.fromarray(np.random.default_rng(4).integers(0, 256, (200, 480, 3), dtype=np.uint8))
        image.info["poe2_inventory_aligned"] = True
        with patch.object(item_ocr.currency_ocr, "get_reader", return_value=reader), patch.object(item_ocr, "_inventory_equipment_slots", return_value=set()):
            result = item_ocr.scan_inventory_grid(image, read=lambda _: [], strictness=100)
        self.assertTrue(result["items"][0]["name_needs_review"])
        self.assertFalse(clear_currency_read(result))
        self.window.inventory_phase.setCurrentIndex(self.window.inventory_phase.findData("end"))
        self.window._inventory_read(result, live=True)
        controls = self.window.inventory_table.cellWidget(0, 3)
        self.assertEqual(controls.property("reviewStatus"), "pending")
        controls.findChild(QPushButton, "approveCurrency").click()
        self.window.approve_scan_button.click()
        self.window.approve_scan_button.click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(next(row["quantity"] for row in logger.session_currency_totals()["items"] if row["name"] == "Chaos Orb"), 23)

    def test_header_numbers_follow_live_map_remnant_and_undo(self):
        """Large presentation counters track actual saved IDs through a map transition and undo."""
        self.assertEqual(self.window.header_map_id.text(), "#1")
        self.window.finish_map()
        self.assertEqual(self.window.header_map_id.text(), "#2")
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.window.undo_map()
        self.assertEqual(self.window.header_map_id.text(), "#1")
        self.assertEqual(self.window.header_map_id.toolTip(), "M0001")

    def test_tablet_ocr_uses_its_own_policy_and_manual_approval_remains_available(self):
        """Read real tablet parser output at three policies and operate the held approval control."""
        image = Image.new("RGB", (480, 200), "black")
        self.window.auto_tablet_checkbox.setChecked(True)
        for level, score, saved in ((100, 1, False), (0, .85, True), (50, .85, False)):
            with self.subTest(strictness=level):
                rows = [{"text": "Tablet", "score": 1},
                        {"text": "Uses Remaining: 5", "score": 1},
                        {"text": "30% increased Pack Size", "score": score}]
                result = item_text.read_screen_tooltip(image, self.window.state["affixes"],
                                                       ocr_rows=rows, strictness={"tablet": level, "waystone": 100})
                before = logger.get_state()["scan_commit_count"]
                result.update(logger.scan_context())
                number = self.window._tablet_read(1, result)
                self.assertEqual(bool(number), saved)
                self.assertEqual(logger.get_state()["scan_commit_count"], before + int(saved))
                if not saved:
                    self.assertEqual(self.window.pending_review_kind, "tablet")
                    self.window.approve_scan_button.click()
                    self.assertEqual(logger.get_state()["scan_commit_count"], before + 1)


if __name__ == "__main__":
    unittest.main()
