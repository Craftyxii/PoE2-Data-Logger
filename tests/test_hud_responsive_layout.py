"""Check visible navigation and header controls at small logical desktop sizes."""

import os
import json
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ResponsiveHUDTests(unittest.TestCase):
    """Exercise actual page navigation and shared map controls after responsive reflow."""

    @classmethod
    def setUpClass(cls):
        """Create or reuse the application needed by shown HUD widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open a fresh logger profile and disable background polling."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-responsive-hud-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        """Close UI workers and restore storage after each geometry scenario."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def expose(self, widget):
        """Scroll each containing viewport and require an unobstructed mouse target."""
        self.app.processEvents()
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 8, 8)
                bounds = QRect(widget.mapTo(parent.widget(), QPoint()), widget.size())
                parent.ensureVisible(bounds.right(), bounds.bottom(), 8, 8)
                parent.ensureVisible(bounds.left(), bounds.top(), 8, 8)
                self.app.processEvents()
            parent = parent.parentWidget()
        rect = QRect(widget.mapTo(self.window, QPoint()), widget.size())
        self.assertTrue(widget.isVisible())
        self.assertTrue(self.window.rect().contains(rect), (widget.objectName(), rect))
        hit = self.window.childAt(widget.mapTo(self.window, widget.rect().center()))
        self.assertTrue(hit is widget or widget.isAncestorOf(hit), widget.objectName())

    def test_every_page_and_header_remain_reachable_at_small_window_sizes(self):
        """Use every sidebar page at 900/1024/1366 widths and expose its shared fields."""
        for width, height in ((900, 650), (1024, 650), (1366, 720)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertEqual(self.window.size().toTuple(), (width, height))
                for button in self.window.nav_buttons:
                    self.expose(button)
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    self.app.processEvents()
                    self.assertEqual(button.property("active"), "true")
                    for control in (self.window.normal, self.window.unique,
                                    self.window.header_expedition, self.window.header_biome):
                        self.expose(control)
                self.expose(self.window.atlas_settings_page.gear_rarity)
                self.expose(self.window.atlas_settings_page.save_button)

    def test_header_reflow_retains_typed_kills_and_commits_once(self):
        """Resize through both layouts, then save and clear typed kills with New map."""
        self.window.resize(900, 650)
        self.app.processEvents()
        self.expose(self.window.normal)
        QTest.mouseClick(self.window.normal, Qt.MouseButton.LeftButton)
        QTest.keyClicks(self.window.normal, "123")
        for width in (1600, 1024, 1920, 900):
            self.window.resize(width, 720)
            self.app.processEvents()
            self.assertEqual(self.window.normal.text(), "123")
            self.expose(self.window.normal)
        from PySide6.QtWidgets import QPushButton
        new_map = next(button for button in self.window.findChildren(QPushButton)
                       if button.text() == "+ New map")
        self.expose(new_map)
        QTest.mouseClick(new_map, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        state = logger.get_state()
        self.assertEqual(state["current_map_id"], "M0002")
        self.assertEqual(state["scan_commit_count"], 1)
        self.assertEqual(self.window.normal.text(), "")
        with logger._connect() as db:
            normal = json.loads(db.execute("SELECT kills_json FROM maps WHERE map_id='M0001'").fetchone()[0])[0]
        self.assertEqual(normal, 123)

    def test_large_counters_and_global_chain_completion_fit_both_header_layouts(self):
        """Keep compact counters together with Complete beside them and retain the wide header."""
        for width, height, size, caption_size, expedition_caption_size in (
                (900, 650, 41, 14, 12), (1024, 768, 41, 14, 12),
                (1366, 720, 41, 14, 12), (1920, 1080, 54, 17, 14)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertEqual(self.window.size().toTuple(), (width, height))
                for counter in (self.window.header_map_id, self.window.header_remnant_id):
                    self.assertIn(f"font-size:{size}px;", counter.styleSheet())
                    self.assertEqual(counter.alignment(), Qt.AlignmentFlag.AlignCenter)
                    self.expose(counter)
                for caption, expected_size in zip(self.window._header_identifier_captions,
                                                   (caption_size, caption_size, expedition_caption_size)):
                    self.assertIn(f"font-size:{expected_size}px;", caption.styleSheet())
                for page in (0, 1):
                    self.window.tabs.setCurrentIndex(page)
                    self.app.processEvents()
                    button = self.window.header_complete_chain_button
                    bounds = QRect(button.mapTo(self.window, QPoint()), button.size())
                    self.assertTrue(self.window.rect().contains(bounds), (width, bounds))
                    self.expose(self.window.header_complete_chain_button)
                    if width < 1600:
                        counters = (self.window.header_map_id, self.window.header_remnant_id)
                        map_bounds, remnant_bounds = [
                            QRect(counter.mapTo(self.window, QPoint()), counter.size()) for counter in counters]
                        gap = remnant_bounds.left() - map_bounds.right() - 1
                        self.assertGreaterEqual(gap, 0)
                        self.assertLessEqual(gap, 20)
                        self.assertEqual(map_bounds.center().y(), remnant_bounds.center().y())
                        bounds = QRect(button.mapTo(self.window, QPoint()), button.size())
                        self.assertLess(bounds.right(), map_bounds.left())
                        self.assertLessEqual(abs(bounds.center().y() - map_bounds.center().y()), 1)
                        for caption, counter in zip(self.window._header_identifier_captions[:2], counters):
                            caption_bounds = QRect(caption.mapTo(self.window, QPoint()), caption.size())
                            counter_bounds = QRect(counter.mapTo(self.window, QPoint()), counter.size())
                            vertical_gap = counter_bounds.top() - caption_bounds.bottom() - 1
                            self.assertGreaterEqual(vertical_gap, 0)
                            self.assertLessEqual(vertical_gap, 4)
                            self.expose(caption)
                    self.expose(self.window.header_expedition)

    def test_growing_live_counter_ids_fit_compact_header_without_another_resize(self):
        """Reflow live ID growth at 900/1024 widths while keeping the counter row readable."""
        for width in (900, 1024):
            self.window.resize(width, 768)
            self.app.processEvents()
            for map_number, remnant_number in ((4, 69), (100, 100), (9999, 9999)):
                with self.subTest(width=width, map=map_number, remnant=remnant_number):
                    state = logger.get_state()
                    state.update(current_map_id=f"M{map_number:04}", current_remnant_id=f"R{remnant_number:04}")
                    self.window._sync_header_ids(state)
                    QTest.qWait(30)
                    self.assertEqual(self.window.size().toTuple(), (width, 768))
                    counters = (self.window.header_map_id, self.window.header_remnant_id)
                    for counter, number in zip(counters, (map_number, remnant_number)):
                        self.assertEqual(counter.text(), f"#{number}")
                        self.assertEqual(counter.font().pixelSize(), 41)
                        metrics = counter.fontMetrics()
                        text_width = max(metrics.horizontalAdvance(counter.text()),
                                         metrics.boundingRect(counter.text()).width())
                        self.assertLessEqual(text_width, counter.contentsRect().width())
                        bounds = QRect(counter.mapTo(self.window, QPoint()), counter.size())
                        self.assertTrue(self.window.rect().contains(bounds), (width, counter.text(), bounds))
                        self.expose(counter)
                    map_bounds, remnant_bounds = [
                        QRect(counter.mapTo(self.window, QPoint()), counter.size()) for counter in counters]
                    gap = remnant_bounds.left() - map_bounds.right() - 1
                    self.assertGreaterEqual(gap, 0)
                    self.assertLessEqual(gap, 20)
                    self.assertEqual(map_bounds.center().y(), remnant_bounds.center().y())
                    button = self.window.header_complete_chain_button
                    bounds = QRect(button.mapTo(self.window, QPoint()), button.size())
                    self.assertLess(bounds.right(), map_bounds.left())
                    self.assertLessEqual(abs(bounds.center().y() - map_bounds.center().y()), 1)
                    self.expose(button)


if __name__ == "__main__":
    unittest.main()
