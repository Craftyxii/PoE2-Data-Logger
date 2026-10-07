import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication, QMessageBox

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class SessionCurrencyUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def review(self, phase, amounts):
        select(self.window.inventory_phase, phase)
        self.window._inventory_read({"items": [
            {"slot": index, "name": name, "quantity": quantity}
            for index, (name, quantity) in enumerate(amounts.items(), 1)], "unknown": []}, live=False)

    def approve(self, phase, **amounts):
        self.review(phase, amounts)
        self.window.approve_review()

    def quantities(self):
        return {name: card.quantity for name, card in self.window.session_currency.cards.items()}

    def test_reference_tools_live_in_debug_and_are_hidden_by_default(self):
        page = self.window.tabs.widget(4).widget()
        debug = self.window.tabs.widget(5).widget()
        self.assertFalse(page.isAncestorOf(self.window.inventory_reference_group))
        self.assertTrue(debug.isAncestorOf(self.window.inventory_reference_group))
        self.assertTrue(self.window.inventory_reference_group.isHidden())
        self.window.developer_mode.setChecked(True)
        self.assertFalse(self.window.inventory_reference_group.isHidden())
        self.assertTrue(page.isAncestorOf(self.window.session_currency))
        self.assertTrue(page.isAncestorOf(self.window.normal))

    def test_only_approved_end_scans_update_totals_and_repeats_replace_them(self):
        self.approve("start", **{"Chaos Orb": 10, "Divine Orb": 2})
        self.assertEqual(self.quantities(), {})
        self.review("end", {"Chaos Orb": 18, "Divine Orb": 3})
        self.assertEqual(self.quantities(), {})
        self.window.approve_review()
        self.assertEqual(self.quantities(), {"Chaos Orb": 8, "Divine Orb": 1})
        self.approve("end", **{"Chaos Orb": 18, "Divine Orb": 3})
        self.assertEqual(self.quantities(), {"Chaos Orb": 8, "Divine Orb": 1})
        self.approve("end", **{"Chaos Orb": 16, "Divine Orb": 4})
        self.assertEqual(self.quantities(), {"Chaos Orb": 6, "Divine Orb": 2})
        for name, card in self.window.session_currency.cards.items():
            self.assertFalse(card.icon.pixmap().isNull(), name)
            self.assertEqual(card.name_label.toolTip(), name)

    def test_rejected_scan_does_not_change_counter(self):
        self.approve("start")
        self.approve("end", **{"Chaos Orb": 8})
        self.review("end", {"Chaos Orb": 99})
        self.window.reject_review()
        self.assertEqual(self.quantities(), {"Chaos Orb": 8})

    def test_new_map_retains_previous_gains_then_adds_its_approved_gain(self):
        self.approve("start", **{"Chaos Orb": 10})
        self.approve("end", **{"Chaos Orb": 18})
        self.window.finish_map()
        self.assertEqual(self.quantities(), {"Chaos Orb": 8})
        self.approve("start", **{"Chaos Orb": 18})
        self.approve("end", **{"Chaos Orb": 23, "Divine Orb": 1})
        self.assertEqual(self.quantities(), {"Chaos Orb": 13, "Divine Orb": 1})

    def test_session_reset_clears_cards_and_retains_reference_tools(self):
        self.approve("start")
        self.approve("end", **{"Chaos Orb": 8})
        self.window.session_currency.search.setText("Chaos")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.reset_logger()
        self.assertEqual(self.quantities(), {})
        self.assertEqual(self.window.session_currency.grid.count(), 0)
        self.assertFalse(self.window.session_currency.empty.isHidden())
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assertEqual(self.window.session_currency.search.text(), "")
        self.assertIsNotNone(self.window.icon_name)

    def test_cancelled_reset_keeps_session_totals(self):
        self.approve("start")
        self.approve("end", **{"Chaos Orb": 8})
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.window.reset_logger()
        self.assertEqual(self.quantities(), {"Chaos Orb": 8})

    def test_missing_baseline_is_visible_and_later_start_scan_updates_totals(self):
        self.approve("end", **{"Chaos Orb": 100})
        self.assertEqual(self.quantities(), {})
        self.assertIn("needs a start scan", self.window.session_currency.summary.text())
        self.approve("start", **{"Chaos Orb": 97})
        self.assertEqual(self.quantities(), {"Chaos Orb": 3})

    def test_custom_learned_icon_and_name_display_with_session_quantity(self):
        name = "My newly labelled currency"
        icon = Image.new("RGB", (96, 96), (12, 12, 20))
        ImageDraw.Draw(icon).ellipse((16, 16, 80, 80), fill=(170, 80, 220), outline=(220, 180, 80), width=4)
        logger.save_currency_snapshot("start", [])
        logger.save_currency_snapshot("end", [{"name": name, "quantity": 12}], register_names=True,
                                      icon_examples=[{"name": name, "category": "Currency", "columns": 1,
                                                      "rows": 1, "image": icon}])
        self.window.refresh()
        card = self.window.session_currency.cards[name]
        self.assertEqual(card.quantity, 12)
        self.assertFalse(card.icon.pixmap().isNull())
        self.assertEqual(card.name_label.toolTip(), name)

    def test_filter_and_responsive_grid_preserve_all_totals(self):
        self.approve("start")
        self.approve("end", **{"Chaos Orb": 1000, "Divine Orb": 2, "Exalted Orb": 5})
        counter = self.window.session_currency
        counter.search.setText("DIVINE")
        self.assertEqual(counter._visible, ("Divine Orb",))
        self.assertEqual(counter.grid.count(), 1)
        counter.search.setText("Nothing matches")
        self.assertEqual(counter.grid.count(), 0)
        self.assertIn("No currency matches", counter.empty.text())
        counter.search.clear()
        self.assertEqual(counter._visible, ("Chaos Orb", "Divine Orb", "Exalted Orb"))
        self.assertEqual(counter.cards["Chaos Orb"].total_label.text(), "1,000")
        self.assertEqual(self.quantities(), {"Chaos Orb": 1000, "Divine Orb": 2, "Exalted Orb": 5})
        counter.resize(500, 320)
        counter._relayout()
        self.assertEqual(counter._columns, 2)
        counter.resize(1050, 320)
        counter._relayout()
        self.assertEqual(counter._columns, 4)
        self.assertEqual(counter.grid.count(), 3)

    def test_latest_correction_to_zero_removes_its_card_without_other_items(self):
        self.approve("start")
        self.approve("end", **{"Chaos Orb": 8, "Divine Orb": 1})
        self.approve("end", **{"Divine Orb": 1})
        self.assertEqual(self.quantities(), {"Divine Orb": 1})
        self.assertEqual(self.window.session_currency.grid.count(), 1)


if __name__ == "__main__":
    unittest.main()
