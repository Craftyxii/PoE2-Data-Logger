"""Obsolete default listings retire without erasing accepted or learned data."""
import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


OMENS = (
    "Omen of Corruption", "Omen of Dextral Alchemy", "Omen of Dextral Coronation",
    "Omen of Recombination", "Omen of Sinistral Alchemy", "Omen of Sinistral Coronation",
)
RETIRED = (*OMENS, "Black Scythe Artifact", "Broken Circle Artifact", "Exotic Coinage",
           "Order Artifact", "Sun Artifact", "Aldur's Saga")


class CurrencyCatalogRetirementTests(unittest.TestCase):
    """Check retirement of defaults without losing accepted labels, references or history."""
    def setUp(self):
        """Initialize an isolated logger database for catalog retirement checks."""
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        """Restore the data directory and remove the temporary database."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def seed_previous_catalog(self):
        """Insert retired currency and Omen defaults to simulate an older catalog."""
        with logger._connect() as db:
            db.executemany("INSERT OR IGNORE INTO currency_items(name) VALUES(?)",
                           ((name,) for name in RETIRED))
            db.executemany("INSERT OR IGNORE INTO ritual_names(name) VALUES(?)",
                           ((name,) for name in OMENS))

    def restart(self):
        """Reinitialize the logger to exercise startup catalog retirement."""
        logger._READY = False
        logger.initialize()

    def test_new_database_omits_retired_defaults_and_keeps_current_names(self):
        """Verify fresh catalogs hide retired defaults while retaining current items."""
        self.assertFalse(set(RETIRED) & set(logger.currency_names()))
        self.assertFalse(set(OMENS) & set(logger.ritual_names()))
        with logger._connect() as db:
            self.assertFalse(set(RETIRED) & {row[0] for row in db.execute("SELECT name FROM currency_items")})
            self.assertFalse(set(OMENS) & {row[0] for row in db.execute("SELECT name FROM ritual_names")})
        self.assertIn("Orb of Alchemy", logger.currency_names())
        self.assertIn("Crystallised Corruption", logger.currency_names())
        self.assertIn("Omen of Homogenising Coronation", logger.ritual_names())
        self.restart()
        self.assertFalse(set(RETIRED) & set(logger.inventory_names()))

    def test_existing_database_hides_defaults_and_preserves_snapshots_and_history(self):
        """Verify retirement hides defaults without erasing stored counts or exportable names."""
        self.seed_previous_catalog()
        logger.start_map()
        logger.save_currency_snapshot("end", [
            {"name": "Sun Artifact", "quantity": 7},
            {"name": "Omen of Corruption", "quantity": 2}])
        logger.save_ritual_page([{"category": "Omen", "name": "Omen of Corruption", "quantity": 3}])
        with logger._connect() as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]
        self.restart()
        self.assertFalse(set(RETIRED) & set(logger.currency_names()))
        self.assertFalse(set(OMENS) & set(logger.ritual_names()))
        self.assertEqual(logger.currency_for_map("M0001")["end"],
                         {"Sun Artifact": 7, "Omen of Corruption": 2})
        self.assertEqual(logger.session_currency_totals()["items"], [
            {"name": "Omen of Corruption", "kind": "Omen", "quantity": 2},
            {"name": "Sun Artifact", "kind": "Currency", "quantity": 7}])
        self.assertEqual(logger.ritual_pages_for_map("M0001")[0]["items"][0]["quantity"], 3)
        with logger._connect() as db:
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")])
            self.assertTrue(set(RETIRED) <= {row[0] for row in db.execute("SELECT name FROM currency_items")})
        pack = reference_pack.export_pack()
        with ZipFile(io.BytesIO(pack)) as archive:
            names = json.loads(archive.read("manifest.json"))["data"]["currency_names"]
        self.assertTrue(set(RETIRED) <= {row["name"] for row in names})

    def test_explicit_local_labels_can_reuse_retired_names_after_restart(self):
        """Verify explicitly added retired labels remain selectable after restart."""
        self.seed_previous_catalog()
        logger.add_currency_item("sun artifact")
        logger.add_ritual_name("omen of corruption")
        logger.add_currency_item("Custom Expedition Token")
        logger.add_item_name("Aldur's Saga Armour")
        self.restart()
        self.assertIn("Sun Artifact", logger.currency_names())
        self.assertIn("Omen of Corruption", logger.currency_names())
        self.assertIn("Omen of Corruption", logger.ritual_names())
        self.assertIn("Custom Expedition Token", logger.currency_names())
        self.assertIn("Aldur's Saga Armour", logger.item_names())
        self.assertNotIn("Exotic Coinage", logger.currency_names())

    def test_existing_trained_references_remain_available_with_their_labels(self):
        """Verify learned icons preserve retired labels through catalog retirement."""
        self.seed_previous_catalog()
        artwork = Image.new("RGB", (40, 40), (26, 26, 40))
        ImageDraw.Draw(artwork).ellipse((10, 10, 35, 35), fill="gold")
        logger.save_currency_icon("Black Scythe Artifact", artwork)
        logger.save_omen_icon("Omen of Recombination", artwork)
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Aldur's Saga", "quantity": 1}],
                                      icon_examples=[{"name": "Aldur's Saga", "category": "Currency",
                                                      "image": artwork}])
        expected = logger.ritual_icons()
        self.restart()
        self.assertEqual(logger.ritual_icons(), expected)
        self.assertIn("Black Scythe Artifact", logger.currency_names())
        self.assertIn("Omen of Recombination", logger.ritual_names())
        self.assertIn("Aldur's Saga", logger.currency_names())
        self.assertNotIn("Sun Artifact", logger.currency_names())

    def test_dashboard_hides_retired_defaults_but_displays_accepted_history(self):
        """Verify dashboard cards show retired names only while accepted counts exist."""
        app = QApplication.instance() or QApplication([])
        self.seed_previous_catalog()
        window = LoggerWindow()
        window._poll.stop()
        try:
            self.assertFalse(set(RETIRED) & set(window.session_currency.cards))
            logger.save_currency_snapshot("end", [{"name": "Sun Artifact", "quantity": 7}])
            window.refresh_session_currency()
            self.assertEqual(window.session_currency.cards["Sun Artifact"].quantity, 7)
            self.assertEqual(window.session_currency._visible_groups[0][0], "Expedition")
            logger.save_currency_snapshot("end", [])
            window.refresh_session_currency()
            app.processEvents()
            self.assertNotIn("Sun Artifact", window.session_currency.cards)
            self.assertFalse(set(RETIRED) & set(window.session_currency.cards))
        finally:
            window.close()
            window.pool.shutdown(wait=True, cancel_futures=True)
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
