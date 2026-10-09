"""Keep selected propagation rune names readable on compact desktop windows."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QStyle, QStyleOptionComboBox

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class CompactRuneSelectionTests(unittest.TestCase):
    """Check the visible result of selecting a long recipe rune with real Qt controls."""

    @classmethod
    def setUpClass(cls):
        """Reuse or create the application's Qt event loop."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Show a logger window backed by an isolated profile without polling."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-rune-selection-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        """Close this window and restore the caller's profile."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def expose(self, widget):
        """Scroll a recipe cell's selector into an unobstructed mouse target."""
        self.app.processEvents()
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                point = widget.mapTo(parent.widget(), widget.rect().center())
                parent.ensureVisible(point.x(), point.y(), 20, 20)
                self.app.processEvents()
            parent = parent.parentWidget()
        self.assertTrue(widget.isVisible())
        hit = self.window.childAt(widget.mapTo(self.window, widget.rect().center()))
        self.assertTrue(hit is widget or widget.isAncestorOf(hit))

    def test_long_selected_rune_is_readable_in_compact_recipe_review(self):
        """Read the closed selector after choosing Electrocuting at both compact widths."""
        for width, height in ((900, 650), (1024, 768)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.window._propagation_read({
                    "mode": "propagation", "runes": [], "can_use": False,
                    "status": "Choose the marked runes.",
                    "choices": [{"selected_recipe": "Mystic Alloy", "runes": [],
                                 "can_use": False}], **logger.scan_context()})
                self.app.processEvents()
                self.assertEqual(self.window.size().toTuple(), (width, height))
                field = self.window._propagation_row_inputs[0][0]
                self.expose(field)
                index = field.findText("Electrocuting")
                self.assertGreaterEqual(index, 0)
                QTest.mouseClick(field, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                QTest.keyClick(field, Qt.Key.Key_Escape)
                QTest.keyClick(field, Qt.Key.Key_Home)
                for _ in range(index):
                    QTest.keyClick(field, Qt.Key.Key_Down)
                QTest.keyClick(field, Qt.Key.Key_Return)
                self.app.processEvents()
                self.assertEqual(field.currentText(), "Electrocuting")
                option = QStyleOptionComboBox()
                field.initStyleOption(option)
                text_area = field.style().subControlRect(
                    QStyle.ComplexControl.CC_ComboBox, option,
                    QStyle.SubControl.SC_ComboBoxEditField, field)
                icon_width = option.iconSize.width() + 4 if not option.currentIcon.isNull() else 0
                visible_text_width = text_area.width() - icon_width
                self.assertGreaterEqual(
                    visible_text_width, field.fontMetrics().horizontalAdvance(field.currentText()),
                    "The chosen rune must be readable before approving the chain part.")
                self.window.reject_review()


if __name__ == "__main__":
    unittest.main()
