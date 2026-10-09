"""Resolution choices must guard full game captures without rewriting regions."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.platform import hotkey
from PoE2_Data_Logger.ui import region_select


class ScanResolutionTests(unittest.TestCase):
    """Check saved resolution guards and native region coordinate mapping."""
    @classmethod
    def setUpClass(cls):
        """Create the shared Qt application for scan-resolution page tests."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Initialize temporary logger data and track pages for cleanup."""
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.pages = []

    def tearDown(self):
        """Close created pages and restore the original data directory."""
        for page in self.pages:
            page.close()
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def page(self):
        """Create and track a scan-region settings page."""
        page = region_select.ScanRegionsPage()
        self.pages.append(page)
        return page

    def save(self, page):
        """Click the page's Save regions button."""
        next(button for button in page.findChildren(QPushButton)
             if button.text() == "Save regions").click()

    def test_choice_is_saved_and_restored_without_changing_boxes(self):
        """Verify resolution selection persists only on save and leaves region boxes unchanged."""
        page = self.page()
        self.assertEqual(page.game_resolution.currentData(), "auto")
        original = dict(page.canvas.boxes)
        page.game_resolution.setCurrentIndex(page.game_resolution.findData("3840x2160"))
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "scan_region_resolution", "auto"), "auto")
        self.save(page)
        reopened = self.page()
        self.assertEqual(reopened.game_resolution.currentData(), "3840x2160")
        self.assertEqual(reopened.canvas.boxes, original)
        self.assertFalse(reopened.canvas.picture.isNull())  # HD sample is still usable.

    def test_auto_maps_boxes_to_all_actual_sizes_and_monitor_origins(self):
        """Verify automatic resolution maps normalized boxes to varied native bounds."""
        with logger._connect() as db:
            logger._set_meta(db, "scan_region_boxes", {"inventory_region": [.25, .5, .5, .25]})
        for width, height in ((1920, 1080), (2560, 1440), (3840, 2160), (3440, 1440), (1280, 720)):
            with self.subTest(size=(width, height)):
                bounds = (-width, 150, 0, 150 + height)
                self.assertEqual(region_select.region_for("inventory_region", bounds),
                                 {"x": -width + round(width * .25), "y": 150 + round(height * .5),
                                  "w": round(width * .75) - round(width * .25),
                                  "h": round(height * .75) - round(height * .5)})

    def test_matching_manual_resolution_uses_native_pixels(self):
        """Verify matching manual resolutions keep mapped regions within native monitor bounds."""
        page = self.page()
        for mode, width, height in (("1920x1080", 1920, 1080), ("2560x1440", 2560, 1440),
                                    ("3840x2160", 3840, 2160), ("3440x1440", 3440, 1440)):
            with self.subTest(mode=mode):
                page.game_resolution.setCurrentIndex(page.game_resolution.findData(mode))
                self.save(page)
                bounds = (1920, -height, 1920 + width, 0)
                actual = region_select.region_for("inventory_region", bounds)
                self.assertGreaterEqual(actual["x"], bounds[0])
                self.assertLessEqual(actual["x"] + actual["w"], bounds[2])

    def test_mismatch_holds_even_selection_and_legacy_hd_before_return(self):
        """Verify resolution mismatches reject selection and legacy region coordinates."""
        with logger._connect() as db:
            logger._set_meta(db, "scan_region_resolution", "3840x2160")
            logger._set_meta(db, "inventory_region", {"x": 600, "y": 300, "w": 1000, "h": 600})
        for selection in (False, True):
            with self.subTest(selection=selection), self.assertRaisesRegex(ValueError, "Auto.*1920.*1080"):
                region_select.region_for("inventory_region", (0, 0, 1920, 1080), for_selection=selection)

    def test_picker_can_validate_unsaved_resolution_without_persisting_it(self):
        """Verify region selection validates a draft resolution without storing it."""
        page = self.page()
        page.game_resolution.setCurrentIndex(page.game_resolution.findData("3840x2160"))
        with self.assertRaisesRegex(ValueError, "Auto"):
            region_select.region_for("inventory_region", (0, 0, 1920, 1080),
                                     for_selection=True, resolution=page.game_resolution.currentData())
        page.game_resolution.setCurrentIndex(page.game_resolution.findData("auto"))
        self.assertEqual(region_select.region_for("inventory_region", (0, 0, 1920, 1080),
                                                 for_selection=True, resolution=page.game_resolution.currentData())["w"], 651)
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "scan_region_resolution", "auto"), "auto")

    def test_default_capture_mismatch_stops_before_screenshot_or_reader(self):
        """Verify mismatched resolution stops capture before grabbing pixels or invoking OCR."""
        with logger._connect() as db:
            logger._set_meta(db, "scan_region_resolution", "3840x2160")
        grabber, reader = Mock(return_value=Image.new("RGB", (400, 300))), Mock()
        manager = hotkey.HotkeyManager(supported=True, grabber=grabber,
                    readers={"opened": reader}, tooltip_grabber=grabber, focused=lambda: True)
        for kind in ("default", "waystone"):
            # A provided tooltip grabber bypasses the waystone region_for call.
            with self.subTest(kind=kind), patch.object(hotkey.sys, "platform", "win32"), patch(
                    "PoE2_Data_Logger.platform.hover_copy._tooltip_bounds", return_value=(0, 0, 1920, 1080)):
                manager.capture(kind)
            self.assertIn("Auto", manager.status()["latest"]["error"])
        grabber.assert_not_called()
        reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
