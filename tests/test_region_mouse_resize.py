"""Exercise overlay resize gestures in logical coordinates with native capture bounds."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from PoE2_Data_Logger.ui import region_select


class RegionMouseResizeTests(unittest.TestCase):
    """Keep mouse-resized overlay crops within native minimums and display bounds."""

    @classmethod
    def setUpClass(cls):
        """Create the Qt application used by shown overlay widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def editor(self, current, logical):
        """Show an overlay with controlled display scaling and register its cleanup."""
        with patch.object(region_select, "_logical_capture_bounds", return_value=logical):
            editor = region_select.RegionEditor(current, screen_bounds=(0, 0, 1920, 1080))
        editor.show()
        self.app.processEvents()
        self.addCleanup(editor.close)
        return editor

    def resize_to(self, editor, target):
        """Drag the visible selection handle to a logical widget position."""
        QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=editor.selection.bottomRight())
        QTest.mouseMove(editor, target)
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=target)
        self.app.processEvents()

    def test_resize_minimum_uses_native_pixels_at_each_display_scale(self):
        """Allow a 200×100 native crop at 100%, 125%, 150%, and 200% display scales."""
        for width, height in ((1920, 1080), (1536, 864), (1280, 720), (960, 540)):
            with self.subTest(logical=(width, height)):
                editor = self.editor({"x": 100, "y": 100, "w": 600, "h": 300},
                                     QRect(0, 0, width, height))
                self.resize_to(editor, editor.selection.topLeft() + QPoint(5, 5))
                region = editor.region()
                self.assertGreaterEqual(region["w"], 200)
                self.assertGreaterEqual(region["h"], 100)
                self.assertLessEqual(region["w"], 202)
                self.assertLessEqual(region["h"], 102)
                QTest.keyClick(editor, Qt.Key.Key_Return)
                self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)
                editor.close()

    def test_scaled_resize_at_capture_edge_stays_inside_before_confirmation(self):
        """Keep a minimum native crop at the bottom-right edge while dragging outside."""
        editor = self.editor({"x": 1720, "y": 980, "w": 200, "h": 100},
                             QRect(0, 0, 960, 540))
        self.resize_to(editor, QPoint(editor.width() + 100, editor.height() + 100))
        self.assertTrue(editor.rect().contains(editor.selection))
        self.assertEqual(editor.region(), {"x": 1720, "y": 980, "w": 200, "h": 100})
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)

    def test_unscaled_resize_retains_native_minimum_and_escape_cancels(self):
        """Preserve the manual desktop fallback's minimum and Escape cancellation."""
        editor = region_select.RegionEditor({"x": 20, "y": 20, "w": 400, "h": 200})
        editor.show()
        self.app.processEvents()
        self.addCleanup(editor.close)
        self.resize_to(editor, editor.selection.topLeft() + QPoint(5, 5))
        self.assertEqual((editor.region()["w"], editor.region()["h"]), (200, 100))
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        self.assertEqual(editor.result(), QDialog.DialogCode.Rejected)


if __name__ == "__main__":
    unittest.main()
