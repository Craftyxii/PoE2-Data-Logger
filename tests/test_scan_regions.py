"""Qt region-page checks ensuring each scanner edits and saves its matching metadata key."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.region_select import REGIONS, ScanRegionsPage


class ScanRegionsTests(unittest.TestCase):
    """Check scanner tabs select and persist the matching region without changing other scanner crops."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication required by the Qt test fixtures."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Initialize isolated logger data and create a tracked scan-regions page."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-scan-regions-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.pages = []
        self.page = self.make_page()

    def tearDown(self):
        """Close tracked pages, process Qt events and restore the original data directory."""
        for page in self.pages:
            page.close()
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def make_page(self):
        """Create a scan-regions page and retain it for teardown cleanup."""
        page = ScanRegionsPage()
        self.pages.append(page)
        return page

    def test_every_scanner_has_a_matching_visible_tab(self):
        """Verify every scanner has a matching visible tab."""
        expected = (
            ("Opened remnant", "live_region"),
            ("Propagation · include the left cursor", "propagation_region"),
            ("Visible seeds", "seed_region"),
            ("Waystone tooltip", "waystone_region"),
            ("Tablet tooltip", "tablet_region"),
            ("Currency / items", "inventory_region"),
            ("Ritual rewards", "ritual_region"),
        )
        self.assertEqual(self.page.region_tabs.count(), len(expected))
        for index, (title, key) in enumerate(expected):
            with self.subTest(scanner=key):
                self.assertEqual(self.page.region_tabs.tabText(index), title)
                self.page.region_tabs.setCurrentIndex(index)
                self.assertEqual(self.page.canvas.active, key)
                self.assertFalse(self.page.canvas.picture.isNull())

    def test_currency_crop_saves_under_currency_key_and_preserves_other_regions(self):
        """Verify currency crop saves under currency key and preserves other regions."""
        saved_boxes = {key: list(definition[2]) for key, definition in REGIONS.items()}
        saved_boxes["tablet_region"] = [.2, .1, .3, .4]
        saved_boxes["ritual_region"] = [.1, .2, .4, .5]
        with logger._connect() as db:
            logger._set_meta(db, "scan_region_boxes", saved_boxes)
        page = self.make_page()
        currency_index = next(index for index in range(page.region_tabs.count())
                              if page.region_tabs.tabText(index) == "Currency / items")
        page.region_tabs.setCurrentIndex(currency_index)
        new_crop = [.4, .5, .5, .4]
        page.canvas.boxes[page.canvas.active] = new_crop
        saved = []
        page.saved.connect(lambda: saved.append(True))
        next(button for button in page.findChildren(QPushButton)
             if button.text() == "Save regions").click()
        self.assertEqual(saved, [True])
        expected = {**saved_boxes, "inventory_region": new_crop}
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "scan_region_boxes", {}), expected)
        reopened = self.make_page()
        self.assertEqual(reopened.canvas.boxes, expected)

    def test_programmatic_propagation_and_ritual_selection_targets_live_editor(self):
        """Verify programmatic propagation and ritual selection targets live editor."""
        selected = []
        self.page.select_in_game.connect(selected.append)
        button = next(button for button in self.page.findChildren(QPushButton)
                      if button.text() == "Select region in game")
        for key in ("propagation_region", "ritual_region"):
            with self.subTest(scanner=key):
                self.page.select_region(key)
                self.assertGreaterEqual(self.page.region_tabs.currentIndex(), 0)
                self.assertEqual(self.page.canvas.active, key)
                button.click()
                self.assertEqual(selected[-1], key)


if __name__ == "__main__":
    unittest.main()
