"""Qt region-editor checks for crop display and saved coordinates using controlled screen/native bounds."""

import gc
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.ui import region_select


class RegionRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_screenshot_colors_and_rows_survive_source_release(self):
        screenshot = Image.new("RGB", (321, 201), (17, 28, 49))
        colors = {(0, 0): (255, 0, 0), (320, 0): (0, 255, 0),
                  (0, 200): (0, 0, 255), (320, 200): (201, 53, 97),
                  (159, 100): (23, 199, 61)}
        for point, color in colors.items():
            screenshot.putpixel(point, color)
        editor = region_select.RegionEditor(screenshot=screenshot,
                                            screen_bounds=(0, 0, 321, 201))
        screenshot.close()
        del screenshot
        gc.collect()
        picture = editor.picture.toImage()
        self.assertEqual((picture.width(), picture.height()), (321, 201))
        for point, color in colors.items():
            self.assertEqual(picture.pixelColor(*point).getRgb(), (*color, 255))
        self.assertEqual(picture.pixelColor(1, 1).getRgb(), (17, 28, 49, 255))
        self.assertFalse(editor.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        editor.close()

    def test_4k_screenshot_needs_no_image_encoding(self):
        screenshot = Image.new("RGB", (3840, 2160), (83, 137, 219))
        with patch.object(Image.Image, "save", side_effect=AssertionError("Image encoding on GUI thread")):
            editor = region_select.RegionEditor(screenshot=screenshot,
                                                screen_bounds=(0, 0, 3840, 2160))
        self.assertEqual((editor.picture.width(), editor.picture.height()), (3840, 2160))
        self.assertEqual(editor.picture.toImage().pixelColor(3839, 2159).getRgb(), (83, 137, 219, 255))
        editor.close()

    def test_screenshot_preserves_scaled_native_region(self):
        screen = Mock()
        screen.devicePixelRatio.return_value = 2
        screen.geometry.return_value = QRect(-1920, 0, 960, 540)
        current = {"x": -1500, "y": 200, "w": 400, "h": 200}
        screenshot = Image.new("RGB", (1920, 1080), (10, 20, 30))
        with patch.object(region_select.QGuiApplication, "screenAt", return_value=screen):
            editor = region_select.RegionEditor(current, screenshot=screenshot,
                                                screen_bounds=(-1920, 0, 1920, 1080))
        self.assertEqual((editor.width(), editor.height()), (960, 540))
        self.assertEqual((editor.picture.width(), editor.picture.height()), (1920, 1080))
        self.assertEqual(editor.region(), current)
        editor.close()

    def test_alpha_and_palette_transparency_are_preserved(self):
        rgba = Image.new("RGBA", (321, 201), (83, 137, 219, 255))
        rgba.putpixel((320, 200), (0, 0, 0, 128))
        palette = Image.new("P", (321, 201), 1)
        palette.putpalette([0, 0, 0, 83, 137, 219] + [0] * 762)
        palette.info["transparency"] = 0
        palette.putpixel((320, 200), 0)
        for screenshot, expected in ((rgba, (0, 0, 0, 128)), (palette, (0, 0, 0, 0))):
            with self.subTest(mode=screenshot.mode):
                editor = region_select.RegionEditor(screenshot=screenshot,
                                                    screen_bounds=(0, 0, 321, 201))
                picture = editor.picture.toImage()
                self.assertTrue(picture.hasAlphaChannel())
                self.assertEqual(picture.pixelColor(320, 200).getRgb(), expected)
                self.assertEqual(picture.pixelColor(0, 0).getRgb(), (83, 137, 219, 255))
                editor.close()

    def test_no_screenshot_preserves_translucent_editor(self):
        editor = region_select.RegionEditor(screen_bounds=(0, 0, 1920, 1080))
        self.assertTrue(editor.picture.isNull())
        self.assertTrue(editor.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        editor.close()


if __name__ == "__main__":
    unittest.main()
