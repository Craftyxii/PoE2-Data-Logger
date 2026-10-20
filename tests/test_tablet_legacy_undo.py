"""Keep pre-library Undo journals from resurrecting migrated tablet settings."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from PoE2_Data_Logger.core import logger_store as logger, store, undo_store


class TabletLegacyUndoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-tablet-legacy-undo-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        self.affix = next(name for name in logger.get_state()["affixes"] if "Pack Size" in name)

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def legacy_journal(self, *, settings_change):
        """Install the exact legacy meta-row inverse shape before library migration.

        Old settings contain no set ID/name and a Tablet slot action changes
        only tablet_auto_next. Historical observations are independent rows.
        """
        original = logger.get_state()["settings"]
        original.pop("tablet_set_id")
        original.pop("tablet_set_name")
        modified = copy.deepcopy(original)
        modified["tablet_affixes"][0] = {"affix": self.affix, "value": 37}
        modified.setdefault("tablet_raw_mods", [[] for _ in range(4)])[0] = ["37% increased Pack Size"]
        entries = []
        if settings_change:
            entries.append(["meta", ["settings", logger._dump(original)],
                            ["settings", logger._dump(modified)]])
        entries.append(["meta", ["tablet_auto_next", "1"], ["tablet_auto_next", "2"]])
        with logger._connect() as db:
            db.execute("DELETE FROM undo_actions")
            db.execute("DROP TABLE tablet_sets")
            db.execute("DELETE FROM meta WHERE key='selected_tablet_set_id'")
            logger._set_meta(db, "settings", modified)
            logger._set_meta(db, "tablet_auto_next", 2)
            db.execute("INSERT INTO undo_actions(token,label,changes) VALUES(?,?,?)",
                       ("legacy-tablet-action", "Tablet scan" if settings_change else "Tablet slot",
                        undo_store._pack(entries)))
            logger._record_commit(db, "Map settings", "M0001", context={"legacy_evidence": True})
            observations = [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]
        logger._READY = False
        logger.initialize()
        selected = logger.get_state()["selected_tablet_set_id"]
        return selected, original if settings_change else modified, observations

    def assert_mirror(self, selected, expected, next_slot):
        settings = logger.get_state()["settings"]
        self.assertEqual(settings["tablet_set_id"], selected)
        self.assertEqual(settings["tablet_set_name"], "Set 1")
        self.assertEqual(settings["tablet_affixes"], expected["tablet_affixes"])
        self.assertEqual(settings.get("tablet_raw_mods"), expected.get("tablet_raw_mods"))
        self.assertEqual(logger.tablet_next_slot(), next_slot)
        with logger._connect() as db:
            row = db.execute("SELECT * FROM tablet_sets WHERE set_id=?", (selected,)).fetchone()
            self.assertEqual(json.loads(row["config_json"]), logger._tablet_set_settings(settings))
            self.assertEqual(row["next_slot"], next_slot)

    def test_legacy_settings_undo_remains_undone_after_selection_and_restart(self):
        selected, expected, observations = self.legacy_journal(settings_change=True)
        self.assertEqual(logger.undo_last_action()["action"], "Tablet scan")
        self.assert_mirror(selected, expected, 1)
        logger.select_tablet_set(selected)
        self.assert_mirror(selected, expected, 1)
        logger._READY = False
        logger.initialize()
        self.assert_mirror(selected, expected, 1)
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")],
                             observations)

    def test_legacy_slot_only_undo_updates_the_migrated_library_position(self):
        selected, expected, _ = self.legacy_journal(settings_change=False)
        self.assertEqual(logger.undo_last_action()["action"], "Tablet slot")
        self.assert_mirror(selected, expected, 1)
        logger.select_tablet_set(selected)
        self.assert_mirror(selected, expected, 1)

    @staticmethod
    def modern_mirrors():
        with logger._connect() as db:
            return ([tuple(row) for row in db.execute("SELECT * FROM tablet_sets ORDER BY rowid")],
                    [tuple(row) for row in db.execute("SELECT * FROM meta WHERE key IN "
                     "('settings','selected_tablet_set_id','tablet_auto_next') ORDER BY key")])

    def test_modern_create_save_rename_and_switch_undo_keep_exact_library_row_images(self):
        states = [self.modern_mirrors()]
        first = logger.get_state()["selected_tablet_set_id"]
        second = logger.create_tablet_set("Second")["selected_tablet_set_id"]
        states.append(self.modern_mirrors())
        pairs = logger.get_state()["settings"]["tablet_affixes"]
        pairs[0] = {"affix": self.affix, "value": 29}
        logger.save_settings({"tablet_affixes": pairs})
        states.append(self.modern_mirrors())
        logger.rename_tablet_set(second, "Renamed second")
        states.append(self.modern_mirrors())
        logger.select_tablet_set(first)
        for before in reversed(states):
            logger.undo_last_action()
            self.assertEqual(self.modern_mirrors(), before)

    def test_missing_selected_library_row_rolls_back_the_complete_legacy_undo(self):
        selected, _, _ = self.legacy_journal(settings_change=True)
        with logger._connect() as db:
            db.execute("DELETE FROM tablet_sets WHERE set_id=?", (selected,))
            before = [tuple(row) for row in db.execute("SELECT * FROM meta ORDER BY key")]
            journal = [tuple(row) for row in db.execute("SELECT * FROM undo_actions ORDER BY id")]
        with self.assertRaisesRegex(ValueError, "selected tablet set is missing"):
            logger.undo_last_action()
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM meta ORDER BY key")], before)
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM undo_actions ORDER BY id")], journal)


if __name__ == "__main__":
    unittest.main()
