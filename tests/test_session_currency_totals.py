import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store


class SessionCurrencyTotalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-session-currency-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def snapshot(self, phase, **items):
        return logger.save_currency_snapshot(phase, [{"name": name, "quantity": quantity}
                                                     for name, quantity in items.items()])

    def quantities(self):
        return {item["name"]: item["quantity"] for item in logger.session_currency_totals()["items"]}

    def next_map(self):
        logger.finish_map(0, 0, 0)
        logger.start_map()

    def test_counts_positive_gains_per_map_without_netting_against_later_spending(self):
        self.snapshot("start", **{"Chaos Orb": 10, "Exalted Orb": 7})
        self.snapshot("end", **{"Chaos Orb": 15, "Exalted Orb": 5, "Regal Orb": 2})
        self.assertEqual(self.quantities(), {"Chaos Orb": 5, "Regal Orb": 2})
        self.next_map()
        self.snapshot("start", **{"Chaos Orb": 15, "Regal Orb": 2})
        self.snapshot("end", **{"Chaos Orb": 11, "Regal Orb": 5})
        result = logger.session_currency_totals()
        self.assertEqual(self.quantities(), {"Chaos Orb": 5, "Regal Orb": 5})
        self.assertEqual(result["maps_counted"], 2)
        self.assertEqual((result["maps_pending_baseline"], result["maps_pending_end"]), (0, 0))

    def test_repeat_approved_snapshots_replace_totals_instead_of_counting_history(self):
        self.snapshot("start", **{"Chaos Orb": 10})
        for _ in range(3):
            self.snapshot("end", **{"Chaos Orb": 15})
            self.assertEqual(self.quantities(), {"Chaos Orb": 5})
        self.snapshot("end", **{"Chaos Orb": 13})
        self.assertEqual(self.quantities(), {"Chaos Orb": 3})
        self.snapshot("start", **{"Chaos Orb": 12})
        self.assertEqual(self.quantities(), {"Chaos Orb": 1})
        self.snapshot("end", **{"Chaos Orb": 11})
        self.assertEqual(self.quantities(), {})
        self.assertEqual(logger.session_currency_totals()["maps_counted"], 1)

    def test_end_only_inventory_counts_empty_start_then_scanned_start_takes_precedence(self):
        self.snapshot("end", **{"Chaos Orb": 100})
        self.assertEqual(logger.session_currency_totals(), {
            "items": [{"name": "Chaos Orb", "kind": "Currency", "quantity": 100}],
            "maps_counted": 1, "maps_pending_baseline": 0, "maps_pending_end": 0,
            "maps_assumed_empty": 1})
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Chaos Orb": 100})
        self.assertEqual(logger.currency_for_map("M0001")["start_baseline"], "Assumed empty")
        self.snapshot("start", **{"Chaos Orb": 97})
        self.assertEqual(self.quantities(), {"Chaos Orb": 3})
        self.assertEqual(logger.session_currency_totals()["maps_assumed_empty"], 0)
        self.assertEqual(logger.currency_for_map("M0001")["start_baseline"], "Scanned")
        self.snapshot("start")
        self.assertEqual(self.quantities(), {"Chaos Orb": 100})

    def test_mixed_end_only_and_paired_maps_keep_latest_gain_without_duplicate_scans(self):
        for _ in range(3):
            self.snapshot("end", **{"Chaos Orb": 10, "Divine Orb": 2})
        self.snapshot("end", **{"Chaos Orb": 7, "Divine Orb": 1})
        self.next_map()
        self.snapshot("start", **{"Chaos Orb": 7, "Divine Orb": 1})
        self.snapshot("end", **{"Chaos Orb": 11, "Divine Orb": 1, "Exalted Orb": 3})
        self.next_map()
        self.snapshot("end", **{"Chaos Orb": 2, "Exalted Orb": 4})
        result = logger.session_currency_totals()
        self.assertEqual(self.quantities(), {"Chaos Orb": 13, "Divine Orb": 1, "Exalted Orb": 7})
        self.assertEqual((result["maps_counted"], result["maps_assumed_empty"],
                          result["maps_pending_baseline"], result["maps_pending_end"]), (3, 2, 0, 0))

    def test_start_only_and_prepared_next_map_do_not_add_unfinished_counts(self):
        self.snapshot("start", **{"Chaos Orb": 20})
        self.assertEqual(logger.session_currency_totals()["maps_pending_end"], 1)
        self.assertEqual(self.quantities(), {})
        self.snapshot("end", **{"Chaos Orb": 23})
        logger.finish_map(0, 0, 0)
        self.snapshot("start", **{"Chaos Orb": 23})
        result = logger.session_currency_totals()
        self.assertEqual(result["items"], [{"name": "Chaos Orb", "kind": "Currency", "quantity": 3}])
        self.assertEqual((result["maps_counted"], result["maps_pending_end"]), (1, 1))

    def test_ritual_ordinary_and_deferred_offers_are_excluded(self):
        self.snapshot("start")
        self.snapshot("end", **{"Chaos Orb": 2})
        logger.save_ritual_page([
            {"category": "Item", "name": "Chaos Orb", "quantity": 1000},
            {"category": "Omen", "name": "Omen of Whittling", "quantity": 3},
            {"category": "Omen", "name": "Omen of Whittling", "quantity": 5, "deferred": True}])
        self.assertEqual(self.quantities(), {"Chaos Orb": 2})

    def test_custom_learned_currency_equipment_and_omens_have_individual_counters(self):
        logger.add_item_name("Learned Armour")
        logger.add_ritual_name("Omen of Session Testing")
        logger.save_currency_snapshot("start", [], register_names=True)
        art = Image.fromarray(np.random.default_rng(91).integers(40, 220, (50, 50, 3), dtype=np.uint8))
        logger.save_currency_snapshot("end", [
            {"name": "Learned Currency", "quantity": 4},
            {"name": "Learned Armour", "quantity": 1},
            {"name": "Omen of Session Testing", "quantity": 2}], register_names=True,
            icon_examples=[{"name": "Learned Currency", "category": "Currency", "image": art}])
        self.assertEqual(logger.session_currency_totals()["items"], [
            {"name": "Learned Armour", "kind": "Item", "quantity": 1},
            {"name": "Learned Currency", "kind": "Currency", "quantity": 4},
            {"name": "Omen of Session Testing", "kind": "Omen", "quantity": 2}])
        self.assertEqual(len(logger.review_icons()), 1)
        # The returned counters can also be served through a JSON API.
        json.dumps(logger.session_currency_totals())

    def test_unicode_canonical_names_merge_across_maps_and_older_snapshot_spellings(self):
        logger.save_currency_snapshot("start", [{"name": "Straße Token", "quantity": 1}], register_names=True)
        logger.save_currency_snapshot("end", [{"name": "STRASSE TOKEN", "quantity": 4}], register_names=True)
        self.next_map()
        logger.save_currency_snapshot("start", [{"name": "strasse token", "quantity": 2}], register_names=True)
        logger.save_currency_snapshot("end", [{"name": "Straße Token", "quantity": 7}], register_names=True)
        # Old imported snapshots can predate canonicalisation of stack keys.
        with logger._connect() as db:
            db.execute("UPDATE currency_snapshots SET items_json=? WHERE map_id='M0002' AND phase='end'",
                       (json.dumps({"Straße Token": 4, "STRASSE TOKEN": 3}),))
        self.assertEqual(logger.session_currency_totals()["items"], [
            {"name": "Straße Token", "kind": "Currency", "quantity": 8}])

    def test_local_reference_deletion_does_not_erase_logged_equipment_gain(self):
        logger.add_item_name("Tracked Equipment")
        self.snapshot("start")
        self.snapshot("end", **{"Tracked Equipment": 1})
        with logger._connect() as db:
            db.execute("DELETE FROM item_names WHERE name='Tracked Equipment'")
        self.assertEqual(logger.session_currency_totals()["items"], [
            {"name": "Tracked Equipment", "kind": "Item", "quantity": 1}])

    def test_app_reopen_preserves_session_and_reset_ids_clears_it(self):
        self.snapshot("start", **{"Chaos Orb": 1})
        self.snapshot("end", **{"Chaos Orb": 9})
        logger._READY = False
        logger.initialize()
        self.assertEqual(self.quantities(), {"Chaos Orb": 8})
        logger.clear_export_and_reset_ids()
        self.assertEqual(logger.session_currency_totals(), {
            "items": [], "maps_counted": 0, "maps_pending_baseline": 0, "maps_pending_end": 0,
            "maps_assumed_empty": 0})
        logger.start_map()
        self.snapshot("start")
        self.snapshot("end", **{"Chaos Orb": 2})
        self.assertEqual(self.quantities(), {"Chaos Orb": 2})

    def test_failed_or_unapproved_replacement_cannot_change_existing_session_total(self):
        self.snapshot("start", **{"Chaos Orb": 10})
        self.snapshot("end", **{"Chaos Orb": 13})
        with patch.object(logger, "_record_commit", side_effect=RuntimeError("disk write failed")):
            with self.assertRaises(RuntimeError):
                self.snapshot("end", **{"Chaos Orb": 999})
        self.assertEqual(self.quantities(), {"Chaos Orb": 3})


if __name__ == "__main__":
    unittest.main()
