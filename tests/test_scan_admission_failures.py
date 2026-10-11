"""Keep failed scan preparation and screenshot rendering from leaving an unowned Reading review."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QFileDialog

from PoE2_Data_Logger.core import logger_store as logger, ocr_sensitivity, service, store
from PoE2_Data_Logger.ui import native_desktop
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ScanAdmissionFailureTests(unittest.TestCase):
    """Exercise real capture entry points while failing only preflight data or screenshot display."""

    @classmethod
    def setUpClass(cls):
        """Share the Qt application needed by the native review controls."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated logger and create a valid image for all file/capture modes."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-scan-admission-")
        self.previous_data = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.image = Image.new("RGB", (600, 400), (120, 130, 140))
        self.image_path = Path(self.tmp.name) / "capture.png"
        self.image.save(self.image_path)

    def tearDown(self):
        """Retire callbacks and restore the caller's profile and shortcut mode."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def capture(self, mode):
        """Invoke the actual capture entry point with fixed inventory geometry or file selection."""
        if mode == "currency":
            with patch.object(native_desktop.item_ocr, "inventory_grid", side_effect=lambda image: image):
                self.window._inventory_captured(self.image, live=False)
        elif mode == "ritual":
            self.window._ritual_captured(self.image, live=False)
        else:
            self.window.mode = mode
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.image_path), "")):
                self.window.scan_file()

    def held_review(self):
        """Stage a named inventory review whose editable rows must survive failed preflight."""
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 292}],
                                     "unknown": []}, live=False)
        return self.review_state()

    def review_state(self):
        """Capture the existing review, editable quantities, image binding and live reader identities."""
        window = self.window
        return {"kind": window.pending_review_kind, "summary": window.review_summary.text(),
                "failed": window._failed_review, "approve": window.approve_scan_button.isEnabled(),
                "rows": [[window.inventory_table.item(row, column).text()
                          if window.inventory_table.item(row, column) else None
                          for column in range(window.inventory_table.columnCount())]
                         for row in range(window.inventory_table.rowCount())],
                "readers": tuple(id(getattr(window, field)) for field in
                                 ("_inventory_reading", "_ritual_reading", "_remnant_reading")),
                "targets": (window._pending_currency_map, window._pending_currency_phase,
                            window._pending_ritual_map),
                "images": dict(window.images), "files": dict(window.current_file)}

    def test_reference_load_failures_preserve_the_existing_review_without_reading(self):
        """Reference queries fail before replacing a draft or admitting thread/native work."""
        before = self.held_review()
        for mode, reader in (("currency", "inventory_icons"), ("ritual", "ritual_names"),
                             ("ritual", "ritual_icons")):
            with self.subTest(mode=mode, reference=reader):
                with patch.object(logger, reader, side_effect=OSError("Reference profile unavailable")), \
                        patch.object(self.window.pool, "submit") as submit, \
                        patch.object(native_desktop.ocr_workers, "submit") as native_submit, \
                        patch.object(service.HOTKEY, "cancel_capture") as cancel:
                    with self.assertRaisesRegex(OSError, "Reference profile unavailable"):
                        self.capture(mode)
                self.assertEqual(self.review_state(), before)
                self.assertEqual(self.window._pending_tasks, {})
                self.assertEqual(self.window._scan_task_timers, {})
                submit.assert_not_called()
                native_submit.assert_not_called()
                cancel.assert_not_called()
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 292})

    def test_file_map_preparation_failure_preserves_the_existing_review(self):
        """Fail map preparation before replacing the held review or admitting any recognition work."""
        before = self.held_review()
        context, sensitivity = logger.scan_context(), ocr_sensitivity.saved_values()
        with patch.object(logger, "scan_context", return_value=context), \
                patch.object(ocr_sensitivity, "saved_values", return_value=sensitivity), \
                patch.object(logger, "_connect", side_effect=OSError("Map profile unavailable")), \
                patch.object(self.window.pool, "submit") as submit, \
                patch.object(native_desktop.ocr_workers, "submit") as native_submit, \
                patch.object(service.HOTKEY, "cancel_capture") as cancel:
            with self.assertRaisesRegex(OSError, "Map profile unavailable"):
                self.capture("opened")
        self.assertEqual(self.review_state(), before)
        self.assertEqual(self.window._pending_tasks, {})
        self.assertEqual(self.window._scan_task_timers, {})
        submit.assert_not_called()
        native_submit.assert_not_called()
        cancel.assert_not_called()

    def test_screenshot_render_failures_retire_reading_for_every_capture_path(self):
        """Preview exceptions retire reading and prevent admission to either thread or native workers."""
        failures = (("currency", "_show_review_capture"), ("currency", "_show_inventory_preview"),
                    ("ritual", "_show_review_capture"), ("opened", "_show_image"),
                    ("seed", "_show_image"), ("both", "_show_image"))
        for mode, display in failures:
            with self.subTest(mode=mode, display=display):
                with patch.object(self.window, display, side_effect=OSError("Preview unavailable")), \
                        patch.object(self.window.pool, "submit") as submit, \
                        patch.object(native_desktop.ocr_workers, "submit") as native_submit:
                    with self.assertRaisesRegex(OSError, "Preview unavailable"):
                        self.capture(mode)
                self.assertIsNone(self.window._inventory_reading)
                self.assertIsNone(self.window._ritual_reading)
                self.assertIsNone(self.window._remnant_reading)
                self.assertEqual(self.window._pending_tasks, {})
                self.assertEqual(self.window._scan_task_timers, {})
                self.assertTrue(self.window._failed_review)
                self.assertIn("Scan failed: Preview unavailable", self.window.review_summary.text())
                self.assertFalse(self.window.approve_scan_button.isEnabled())
                self.assertTrue(self.window.reject_scan_button.isEnabled())
                self.assertEqual(logger.get_state()["scan_commit_count"], 0)
                submit.assert_not_called()
                native_submit.assert_not_called()
                self.window.reject_scan_button.click()
                self.assertIsNone(self.window.pending_review_kind)
        self.held_review()
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 292})


if __name__ == "__main__":
    unittest.main()
