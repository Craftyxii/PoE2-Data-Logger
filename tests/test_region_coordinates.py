"""Coordinate checks using controlled native/Qt monitor geometry and screenshots, including DPI and negative origins."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui import region_select


class RegionCoordinateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def legacy(self, key="inventory_region", region=None):
        region = region or {"x": 600, "y": 300, "w": 1000, "h": 600}
        with logger._connect() as db:
            logger._set_meta(db, key, region)
        return region

    def test_default_and_saved_normalized_regions_use_physical_4k_bounds(self):
        bounds = (1920, -2160, 5760, 0)
        self.assertEqual(region_select.region_for("inventory_region", bounds),
                         {"x": 4228, "y": -845, "w": 1302, "h": 560})
        with logger._connect() as db:
            logger._set_meta(db, "scan_region_boxes", {"inventory_region": [.25, .5, .5, .25]})
        self.assertEqual(region_select.region_for("inventory_region", bounds),
                         {"x": 2880, "y": -1080, "w": 1920, "h": 540})

    def test_legacy_only_captures_at_original_hd_origin(self):
        old = self.legacy()
        self.assertEqual(region_select.region_for("inventory_region", (0, 0, 1920, 1080)), old)
        for bounds in ((0, 0, 3840, 2160), (1920, 0, 3840, 1080),
                       (-1920, 0, 0, 1080), (100, 200, 2020, 1280)):
            with self.subTest(bounds=bounds), self.assertRaisesRegex(ValueError, "Select.*again"):
                region_select.region_for("inventory_region", bounds)

    def test_reselect_can_open_after_legacy_capture_is_held(self):
        self.legacy()
        bounds = (0, 0, 3840, 2160)
        self.assertEqual(region_select.region_for("inventory_region", bounds, for_selection=True),
                         {"x": 2308, "y": 1315, "w": 1302, "h": 560})
        self.legacy(region={"x": 0, "y": 0, "w": 10, "h": 10})
        self.assertEqual(region_select.region_for("inventory_region", bounds, for_selection=True),
                         {"x": 2308, "y": 1315, "w": 1302, "h": 560})

    def test_all_unresolved_regions_do_not_report_a_successful_save(self):
        for key in region_select.REGIONS:
            self.legacy(key)
        page = region_select.ScanRegionsPage()
        self.addCleanup(page.close)
        saved = []
        page.saved.connect(lambda: saved.append(True))
        with patch.object(region_select.QMessageBox, "warning") as warning:
            page.save_regions()
        warning.assert_called_once()
        self.assertEqual(saved, [])
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "scan_region_boxes", {}), {})

    def test_legacy_page_save_does_not_guess_reference_resolution(self):
        old = self.legacy()
        page = region_select.ScanRegionsPage()
        self.addCleanup(page.close)
        self.assertIn("Select", page.status.text())
        with patch.object(region_select.QMessageBox, "warning") as warning:
            page.save_regions()
        warning.assert_not_called()
        self.assertIn("Select these older regions", page.status.text())
        with logger._connect() as db:
            self.assertNotIn("inventory_region", logger._meta(db, "scan_region_boxes", {}))
            self.assertEqual(logger._meta(db, "inventory_region", None), old)

    def test_one_calibrated_region_saves_while_other_legacy_region_stays_held(self):
        self.legacy()
        ritual = self.legacy("ritual_region", {"x": 200, "y": 100, "w": 800, "h": 600})
        page = region_select.ScanRegionsPage()
        self.addCleanup(page.close)
        saved = []
        page.saved.connect(lambda: saved.append(True))
        page.calibrate_region("inventory_region", [.25, .5, .5, .25])
        page.save_regions()
        self.assertEqual(saved, [True])
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "ritual_region", None), ritual)
            self.assertNotIn("ritual_region", logger._meta(db, "scan_region_boxes", {}))
        self.assertEqual(region_select.region_for("inventory_region", (0, 0, 3840, 2160)),
                         {"x": 960, "y": 1080, "w": 1920, "h": 540})
        with self.assertRaisesRegex(ValueError, "Select.*again"):
            region_select.region_for("ritual_region", (0, 0, 3840, 2160))
        self.assertIn("Ritual rewards", page.status.text())

    def test_explicit_calibration_replaces_legacy_region(self):
        self.legacy()
        page = region_select.ScanRegionsPage()
        self.addCleanup(page.close)
        page.calibrate_region("inventory_region", [.25, .5, .5, .25])
        page.save_regions()
        self.assertEqual(region_select.region_for("inventory_region", (0, 0, 3840, 2160)),
                         {"x": 960, "y": 1080, "w": 1920, "h": 540})

    def test_reset_explicitly_accepts_default_for_legacy_region(self):
        self.legacy()
        page = region_select.ScanRegionsPage()
        self.addCleanup(page.close)
        page.select_region("inventory_region")
        page.reset_region()
        page.save_regions()
        self.assertEqual(region_select.region_for("inventory_region", (0, 0, 3840, 2160)),
                         {"x": 2308, "y": 1315, "w": 1302, "h": 560})

    def test_native_monitor_query_uses_exclusive_physical_edges_and_device_name(self):
        user32 = Mock()
        user32.MonitorFromRect.return_value = 77
        def monitor_info(handle, pointer):
            info = pointer._obj
            info.rcMonitor.left, info.rcMonitor.top = -3840, -2160
            info.rcMonitor.right, info.rcMonitor.bottom = 0, 0
            info.szDevice = "\\\\.\\DISPLAY2"
            return 1
        user32.GetMonitorInfoW.side_effect = monitor_info
        with patch("ctypes.WinDLL", create=True, return_value=user32):
            name, bounds = region_select._native_monitor_bounds(QRect(-3590, -1860, 1280, 720))
        self.assertEqual(name, "\\\\.\\DISPLAY2")
        self.assertEqual(bounds, QRect(-3840, -2160, 3840, 2160))
        rect = user32.MonitorFromRect.call_args.args[0]._obj
        self.assertEqual((rect.left, rect.top, rect.right, rect.bottom), (-3590, -1860, -2310, -1140))

    def editor(self, capture, native, ratio, logical_origin=None, name="\\\\.\\DISPLAY2", native_name=None):
        origin = logical_origin or (native[0], native[1])
        screen = Mock()
        screen.name.return_value = name
        screen.devicePixelRatio.return_value = ratio
        screen.geometry.return_value = QRect(*origin, round(native[2] / ratio), round(native[3] / ratio))
        primary = Mock()
        primary.name.return_value = "\\\\.\\DISPLAY1"
        primary.devicePixelRatio.return_value = 1
        primary.geometry.return_value = QRect(0, 0, 1920, 1080)
        selected = {"x": capture[0] + 100, "y": capture[1] + 100, "w": 400, "h": 200}
        with patch.object(region_select.sys, "platform", "win32"), patch.object(
                region_select, "_native_monitor_bounds", create=True, return_value=(native_name or name, QRect(*native))), patch.object(
                region_select.QGuiApplication, "screens", return_value=[primary, screen]), patch.object(
                region_select.QGuiApplication, "screenAt", return_value=primary), patch.object(
                region_select.QGuiApplication, "primaryScreen", return_value=primary):
            editor = region_select.RegionEditor(selected, screen_bounds=capture)
        self.addCleanup(editor.close)
        return editor, selected

    def test_native_monitor_anchor_preserves_all_dpi_scales(self):
        for ratio in (1, 1.25, 1.5, 2):
            for origin in ((0, 0), (1920, 0), (-3840, -2160)):
                native = (*origin, 3840, 2160)
                capture = (origin[0] + 250, origin[1] + 300, 1280, 720)
                with self.subTest(ratio=ratio, origin=origin):
                    editor, selected = self.editor(capture, native, ratio)
                    self.assertEqual((editor.x(), editor.y(), editor.width(), editor.height()),
                                     (round(origin[0] + 250 / ratio), round(origin[1] + 300 / ratio),
                                      round(1280 / ratio), round(720 / ratio)))
                    # Integer Qt coordinates may differ by one native pixel.
                    for key, value in selected.items():
                        self.assertLessEqual(abs(editor.region()[key] - value), 1)

    def test_monitor_lookup_must_match_a_qt_screen(self):
        with self.assertRaisesRegex(ValueError, "display"):
            self.editor((1920, 0, 3840, 2160), (1920, 0, 3840, 2160), 2,
                        name="missing", native_name="\\\\.\\DISPLAY2")

    def test_capture_cannot_span_monitors_or_missing_pixels(self):
        with self.assertRaisesRegex(ValueError, "one display"):
            self.editor((1800, 0, 3840, 2160), (1920, 0, 3840, 2160), 2)
        with self.assertRaisesRegex(ValueError, "screenshot.*bounds"):
            region_select.RegionEditor(screenshot=Image.new("RGB", (1920, 1080)),
                                       screen_bounds=(0, 0, 3840, 2160))


if __name__ == "__main__":
    unittest.main()
