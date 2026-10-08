"""The session dashboard shows its entire catalog, with related items together."""
import io
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QLabel

from PoE2_Data_Logger.ui.currency_counter import CurrencyCard, SessionCurrencyCounter


class CurrencyCounterGroupsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.counter = SessionCurrencyCounter()
        self.counter.resize(1050, 500)
        self.catalog = [{"name": name, "kind": "Currency"} for name in (
            "Perfect Exalted Orb", "Greater Exalted Orb", "Exalted Orb", "Chaos Orb", "Greater Chaos Orb",
            "Perfect Chaos Orb", "Divine Orb", "Regal Orb", "Greater Regal Orb", "Perfect Regal Orb",
            "Lesser Essence of Haste", "Essence of Haste", "Greater Essence of Haste", "Perfect Essence of Haste",
            "Essence of Ice", "Omen of Refreshment", "Omen of Greater Exaltation", "Lesser Ward Rune",
            "Soul Core of Tacati", "Adaptive Catalyst", "Refined Adaptive Catalyst", "The Runebinder's Alloy",
            "Liquid Despair", "Sun Artifact", "Custom Token")]
        self.catalog.append({"name": "User-labelled Armour", "kind": "Item"})

    def tearDown(self):
        self.counter.close()
        self.counter.deleteLater()
        self.app.processEvents()

    def data(self, **quantities):
        return {"items": [{"name": name, "kind": "Currency", "quantity": quantity}
                          for name, quantity in quantities.items()],
                "maps_counted": int(bool(quantities)), "maps_pending_end": 0,
                "maps_pending_baseline": 0, "maps_assumed_empty": int(bool(quantities))}

    def test_every_catalog_item_is_visible_at_zero_in_family_order(self):
        self.counter.set_totals(self.data(), catalog=self.catalog)
        self.assertEqual(set(self.counter.cards), {item["name"] for item in self.catalog})
        self.assertTrue(all(card.quantity == 0 and not card.isHidden() for card in self.counter.cards.values()))
        self.assertEqual(self.counter.groups["Exalted orbs"],
                         ("Exalted Orb", "Greater Exalted Orb", "Perfect Exalted Orb"))
        self.assertEqual(self.counter.groups["Essences"],
                         ("Lesser Essence of Haste", "Essence of Haste", "Greater Essence of Haste",
                          "Perfect Essence of Haste", "Essence of Ice"))
        self.assertEqual(self.counter.groups["Omens"], ("Omen of Greater Exaltation", "Omen of Refreshment"))
        self.assertEqual(self.counter.groups["Runes and soul cores"], ("Soul Core of Tacati", "Lesser Ward Rune"))
        self.assertEqual(self.counter.groups["Alloys"], ("The Runebinder's Alloy",))
        self.assertTrue(self.counter.empty.isHidden())
        self.assertIn(f"0 of {len(self.catalog)} item types found", self.counter.summary.text())

    def test_found_quantities_update_existing_cards_and_reset_keeps_zero_catalog(self):
        self.counter.set_totals(self.data(), catalog=self.catalog)
        cards = dict(self.counter.cards)
        for amount in (2, 5, 5, 3):
            self.counter.set_totals(self.data(**{"Exalted Orb": amount, "Chaos Orb": 1}), catalog=self.catalog)
            self.assertEqual(self.counter.cards["Exalted Orb"].quantity, amount)
            self.assertTrue(all(self.counter.cards[name] is card for name, card in cards.items()))
        self.counter.set_totals(self.data(), catalog=self.catalog)
        self.assertEqual(len(self.counter.cards), len(self.catalog))
        self.assertTrue(all(card.quantity == 0 for card in self.counter.cards.values()))
        self.assertEqual(set(self.counter._visible), set(cards))
        self.assertNotIn("assumed", self.counter.summary.text())

    def test_catalog_spelling_merges_totals_and_preserves_unknown_learned_item(self):
        self.counter.set_totals({**self.data(), "items": [
            {"name": "exalted orb", "kind": "Currency", "quantity": 2},
            {"name": "EXALTED ORB", "kind": "Currency", "quantity": 3},
            {"name": "Newly learned item", "kind": "Item", "quantity": 1}]}, catalog=self.catalog)
        self.assertEqual(self.counter.cards["Exalted Orb"].quantity, 5)
        self.assertNotIn("exalted orb", self.counter.cards)
        self.assertEqual(self.counter.cards["Newly learned item"].quantity, 1)
        self.assertIn("Newly learned item", self.counter.groups["Items"])

    def test_filter_includes_zero_items_and_matching_group_then_restores_groups(self):
        self.counter.set_totals(self.data(), catalog=self.catalog)
        self.counter.search.setText("EXALTED")
        self.assertEqual(self.counter._visible, ("Exalted Orb", "Greater Exalted Orb", "Perfect Exalted Orb"))
        self.counter.search.setText("Omens")
        self.assertEqual(self.counter._visible, self.counter.groups["Omens"])
        self.assertEqual(self.counter.grid.count(), 3)
        self.counter.search.setText("Nothing matches")
        self.assertEqual(self.counter.grid.count(), 0)
        self.assertIn("No currency matches", self.counter.empty.text())
        self.counter.search.clear()
        self.assertEqual(set(self.counter._visible), set(self.counter.cards))
        self.assertEqual(self.counter.grid.count(), len(self.counter.cards) + len(self.counter.groups))

    def test_responsive_layout_has_group_headers_on_separate_rows_without_overlap(self):
        self.counter.set_totals(self.data(), catalog=self.catalog)
        for width, columns in ((500, 2), (1050, 4), (1600, 6), (240, 1)):
            self.counter.resize(width, 500)
            self.counter._relayout()
            self.assertEqual(self.counter._columns, columns)
            occupied = set()
            for index in range(self.counter.grid.count()):
                row, column, rows, span = self.counter.grid.getItemPosition(index)
                cells = {(r, c) for r in range(row, row + rows) for c in range(column, column + span)}
                self.assertFalse(occupied & cells)
                occupied.update(cells)
                if isinstance(self.counter.grid.itemAt(index).widget(), QLabel):
                    self.assertEqual((column, span), (0, columns))
                else:
                    self.assertLess(column, columns)

    def test_repeated_refresh_and_search_do_not_accumulate_cards_or_headers(self):
        self.counter.set_totals(self.data(), catalog=self.catalog)
        labels = dict(self.counter.group_labels)
        for index in range(30):
            self.counter.set_totals(self.data(**{"Chaos Orb": index}), catalog=self.catalog)
            self.counter.search.setText("Omens" if index % 2 else "")
            self.app.processEvents()
        self.assertEqual(len(self.counter.findChildren(CurrencyCard)), len(self.catalog))
        self.assertTrue(all(self.counter.group_labels[group] is label for group, label in labels.items()))
        self.counter.set_totals(self.data(), catalog=[])
        self.counter.search.clear()
        self.app.processEvents()
        self.assertEqual(self.counter.cards, {})
        self.assertEqual(self.counter.group_labels, {})
        self.assertEqual(self.counter.grid.count(), 0)

    def test_explicit_groups_natural_levels_and_late_icon_updates(self):
        self.counter.set_totals(self.data(), catalog=[
            {"name": "Flux (Level 10)", "group": "Fluxes"},
            {"name": "Flux (Level 9)", "group": "Fluxes"}])
        self.assertEqual(self.counter.groups["Fluxes"], ("Flux (Level 9)", "Flux (Level 10)"))
        name = "Flux (Level 9)"
        card = self.counter.cards[name]
        self.assertTrue(card.icon.pixmap().isNull())
        png = io.BytesIO()
        Image.new("RGB", (40, 40), "gold").save(png, format="PNG")
        self.counter.set_totals(self.data(**{name: 6}), icons={name: png.getvalue()})
        self.assertIs(card, self.counter.cards[name])
        self.assertFalse(card.icon.pixmap().isNull())
        self.assertEqual(card.quantity, 6)


if __name__ == "__main__":
    unittest.main()
