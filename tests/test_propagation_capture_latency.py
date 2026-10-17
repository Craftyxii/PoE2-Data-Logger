"""Retain lossless propagation captures through real queued OCR and explicit review."""

import csv
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, ocr_workers, service, store
from PoE2_Data_Logger.ocr.propagation_scan import scan_propagation
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class PropagationCaptureLatencyTests(unittest.TestCase):
    """Exercise capture encoding, spawned recognition, queued review and manual acceptance together."""

    @classmethod
    def setUpClass(cls):
        """Use Qt's real event loop without requiring a native game window."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated profile and route a real captured panel through the production hotkey."""
        ocr_workers.shutdown(wait=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-propagation-latency-")
        self.previous_data, self.previous_hotkey = store.DATA_DIR, service.HOTKEY
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        path = Path(__file__).parent / "fixtures/propagation_prefill/family52-opened.png"
        with Image.open(path) as source:
            self.picture = source.convert("RGB")
        self.manager = HotkeyManager(supported=False, readers={"propagation": scan_propagation},
                                    grabber=lambda **kwargs: self.picture.copy())
        service.HOTKEY = self.manager
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window._overlay_enabled = True
        self.window.show_overlay()
        self.app.processEvents()

    def tearDown(self):
        """Retire the capture and its real model children before restoring the caller's profile."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        ocr_workers.shutdown(wait=True)
        self.app.processEvents()
        service.HOTKEY, store.DATA_DIR = self.previous_hotkey, self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def test_fast_lossless_capture_reaches_queued_review_without_approving_or_erasing_draft(self):
        """Avoid costly default PNG compression while retaining evidence, ownership and explicit approval."""
        self.window.rune_inputs[0].setText("Life")
        before = logger.get_state()
        encodings = []
        original_save = Image.Image.save

        def save(picture, output, *args, **kwargs):
            """Observe actual capture settings while letting Pillow encode the real evidence."""
            if kwargs.get("format") == "PNG":
                encodings.append(dict(kwargs))
            return original_save(picture, output, *args, **kwargs)

        with patch.object(Image.Image, "save", save):
            self.manager.capture("propagation", background=True)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.app.processEvents()
                event = self.manager.status()["latest"]
                if event and event["error"]:
                    self.fail(event["error"])
                if (self.window._manual_propagation_context and self.window.isVisible()
                        and not self.window.isMinimized()):
                    break
                time.sleep(.005)
        self.assertIsNotNone(self.window._manual_propagation_context, "queued propagation review did not arrive")
        event = self.manager.status()["latest"]
        self.assertEqual(event["result"]["_capture_map_id"], before["current_map_id"])
        self.assertEqual(event["result"]["candidates"], [52])
        self.assertEqual(len(event["result"]["choices"]), 6)
        self.assertEqual([row["positions"] for row in event["result"]["choices"]], [[3]] * 6)
        raw = self.manager.image(event["id"])
        with Image.open(io.BytesIO(raw)) as frozen:
            self.assertEqual(frozen.format, "PNG")
            self.assertEqual(frozen.size, self.picture.size)
            self.assertEqual(frozen.convert("RGB").tobytes(), self.picture.tobytes())
        self.assertFalse(self.window.preview.pixmap().isNull())
        self.assertEqual(self.window.rune_inputs[0].text(), "Life")
        self.assertEqual(logger.get_state(), before)
        self.assertEqual(self.window.propagation_recipe_table.rowCount(), 6)
        selectors = self.window._propagation_row_inputs[0]
        self.assertEqual(selectors[0].currentData(), 2)
        self.assertIsNone(selectors[1].currentData())
        expected_rune = selectors[0].currentText()
        selected_recipe = event["result"]["choices"][0]["selected_recipe"]
        approve = self.window.propagation_recipe_table.cellWidget(0, 2).findChild(
            QPushButton, "approvePropagationRecipe")
        self.assertTrue(approve.isEnabled())
        approve.click()
        after = logger.get_state()
        self.assertEqual(after["chain"], [{"step": 1, "rune1": expected_rune, "rune2": ""}])
        self.assertEqual(after["detonated"], (before["detonated"] or 0) + 1)
        # One approval intentionally records the Propagation audit event and
        # its saved Chain part in the same transaction.
        self.assertEqual(after["scan_commit_count"], before["scan_commit_count"] + 2)
        with logger._connect() as db:
            self.assertEqual([row[0] for row in db.execute("SELECT kind FROM commits ORDER BY number")],
                             ["Propagation", "Chain"])
        exported = list(csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
        self.assertEqual(exported[-1]["Propagation Recipe"], selected_recipe)
        self.assertEqual(exported[-1]["Propagation Rune 1"], expected_rune)
        self.assertEqual(len(encodings), 1)
        self.assertEqual(encodings[0].get("compress_level"), 1,
                         "propagation still pays for default PNG compression before OCR dispatch")


if __name__ == "__main__":
    unittest.main()
