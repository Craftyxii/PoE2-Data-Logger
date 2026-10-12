"""City-biome autofill checks for saved Atlas choices, frozen maps and unchanged HUD controls."""

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.core.atlas_catalog import catalog
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, value


RULES = (
    ("Faridun", "AtlasBiomeCityNotable10", "AtlasBiomeCityNotable10a", "Desert"),
    ("Faridun", "AtlasBiomeCityNotable10", "AtlasBiomeCityNotable10b", "Mountain"),
    ("Ezomyte", "AtlasBiomeCityNotable11", "AtlasBiomeCityNotable11a", "Grass"),
    ("Ezomyte", "AtlasBiomeCityNotable11", "AtlasBiomeCityNotable11b", "Swamp"),
    ("Vaal", "AtlasBiomeCityNotable12", "AtlasBiomeCityNotable12a", "Forest"),
    ("Vaal", "AtlasBiomeCityNotable12", "AtlasBiomeCityNotable12b", "Water"),
)


def fixture_catalog():
    """Retain real city-choice definitions in a small scene plus an unrelated chance bonus."""
    pinned = catalog()
    nodes = {node_id: copy.deepcopy(pinned["nodes"][node_id])
             for node_id in {rule[1] for rule in RULES}}
    for index, node in enumerate(nodes.values()):
        node.update(x=index * 180, y=0)
    nodes["chance"] = {"id": "chance", "name": "Desert likelihood", "activity": "Main Atlas",
                       "kind": "small", "allocatable": True, "x": 0, "y": 180,
                       "effects": ["Faridun Cities have increased chance to contain Desert Maps"],
                       "stats": {"map_city_faridun_desert_chance_+%": 100}, "choices": []}
    return {"version": pinned["version"], "nodes": nodes,
            "activities": ["Main Atlas"], "edges": []}


class AtlasBiomeProfile(unittest.TestCase):
    """Provide a disposable profile and real city-choice references for backend and desktop cases."""

    def setUp(self):
        """Use an isolated profile and controlled catalog while preserving the process configuration."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-atlas-biome-")
        self.previous_dir, self.previous_ready = store.DATA_DIR, logger._READY
        self.catalog = fixture_catalog()
        store.DATA_DIR = Path(self.tmp.name)
        self.catalog_patch = patch.object(logger, "_atlas_catalog_data", side_effect=lambda: self.catalog)
        self.identity_patch = patch.object(logger, "_atlas_catalog_identity", side_effect=self.identity)
        self.catalog_patch.start()
        self.identity_patch.start()
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Restore initialization, storage and catalog providers before removing the disposable profile."""
        self.catalog_patch.stop()
        self.identity_patch.stop()
        store.DATA_DIR, logger._READY = self.previous_dir, self.previous_ready
        self.tmp.cleanup()

    def identity(self):
        """Fingerprint each controlled catalog so frozen setups retain their correct definitions."""
        encoded = json.dumps(self.catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest(), encoded

    def atlas(self, node="AtlasBiomeCityNotable10", choice="AtlasBiomeCityNotable10a", *, allocated=True):
        """Build the selected city effect with allocation and remembered choice independently editable."""
        return {"catalog_version": self.catalog["version"], "allocated": [node] if allocated else [],
                "choices": {node: choice} if choice else {}, "gear_item_rarity": None}

    def snapshot(self, map_id="M0001"):
        """Read the saved map context without holding a SQLite handle across fixture cleanup."""
        with logger._connect() as db:
            return json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()[0])


class AtlasBiomeSyncTests(AtlasBiomeProfile):
    """Verify automatic classification respects allocation, current-map ownership and Undo."""

    def test_all_six_real_city_effects_work_in_either_selection_order(self):
        """Save city before Atlas and Atlas before city; each exact active rule fills its matching biome."""
        for city, node, choice, expected in RULES:
            with self.subTest(city=city, biome=expected):
                logger.save_settings({"city_type": "None", "biome": "Island"})
                logger.save_atlas_settings(self.atlas(allocated=False))
                logger.save_settings({"city_type": city})
                state = logger.save_atlas_settings(self.atlas(node, choice))
                self.assertEqual(state["settings"]["biome"], expected)
                self.assertEqual(self.snapshot()["biome"], expected)
                logger.save_settings({"city_type": "None", "biome": "Island"})
                logger.save_atlas_settings(self.atlas(node, choice))
                state = logger.save_settings({"city_type": city})
                self.assertEqual(state["settings"]["biome"], expected)

    def test_inactive_unanswered_unrelated_or_chance_rules_preserve_manual_biome(self):
        """Remembered choices, dummy selector flags and chance bonuses cannot establish a city biome."""
        for setup, city in ((self.atlas(allocated=False), "Faridun"),
                            (self.atlas(choice=None), "Faridun"),
                            (self.atlas("chance", None), "Faridun"),
                            (self.atlas(), "Ezomyte"), (self.atlas(), "None")):
            with self.subTest(setup=setup, city=city):
                logger.save_settings({"city_type": "None", "biome": "Island"})
                logger.save_atlas_settings(setup)
                state = logger.save_settings({"city_type": city})
                self.assertEqual(state["settings"]["biome"], "Island")

    def test_conflicting_explicit_effects_do_not_choose_a_biome(self):
        """An impossible or future conflicting catalog retains the user's selection instead of guessing."""
        choice = self.catalog["nodes"]["AtlasBiomeCityNotable10"]["choices"][0]
        choice["stats"]["map_city_faridun_also_count_as_mountain"] = 1
        logger.save_settings({"city_type": "Faridun", "biome": "Island"})
        state = logger.save_atlas_settings(self.atlas())
        self.assertEqual(state["settings"]["biome"], "Island")

    def test_manual_biome_and_full_settings_save_remain_available(self):
        """Saving an unchanged City Type cannot overwrite an intentional manual Biome selection."""
        logger.save_atlas_settings(self.atlas())
        logger.save_settings({"city_type": "Faridun"})
        state = logger.save_settings({"city_type": "Faridun", "biome": "Island", "waystone": 55})
        self.assertEqual(state["settings"]["biome"], "Island")
        self.assertEqual(self.snapshot()["biome"], "Island")
        logger.save_settings({"expedition": 2})
        self.assertEqual(logger.get_state()["settings"]["biome"], "Island")
        logger.save_atlas_settings(self.atlas())
        self.assertEqual(logger.get_state()["settings"]["biome"], "Island")
        with logger._connect() as db:
            saved = json.loads(db.execute("SELECT snapshot_json FROM commits WHERE kind='Atlas settings' "
                                          "ORDER BY number DESC LIMIT 1").fetchone()[0])
        self.assertEqual(saved["biome"], "Island")

    def test_city_change_and_automatic_biome_are_one_undo_action(self):
        """Undo one saved city edit restores both tags and its map snapshot without undoing the Atlas."""
        logger.save_atlas_settings(self.atlas())
        original = logger.get_state()["settings"]
        original_snapshot = self.snapshot()
        logger.save_settings({"city_type": "Faridun"})
        state = logger.undo_last_action()["state"]
        self.assertEqual((state["settings"]["city_type"], state["settings"]["biome"]),
                         (original["city_type"], original["biome"]))
        self.assertEqual(state["settings"]["atlas_settings"], original["atlas_settings"])
        self.assertEqual(self.snapshot(), original_snapshot)

    def test_empty_map_undo_restores_prior_manual_biome_after_restart(self):
        """The legacy backend cancellation restores tags changed by map creation without rewriting history."""
        logger.save_atlas_settings(self.atlas())
        logger.save_settings({"city_type": "Faridun"})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        logger.save_settings({"biome": "Island"})
        frozen = self.snapshot()
        logger.finish_map(1, 0, 0, 0)
        self.assertEqual(logger.start_map()["settings"]["biome"], "Desert")
        logger._READY = False
        logger.initialize()
        restored = logger.undo_empty_map()
        self.assertEqual(restored["current_map_id"], "M0001")
        self.assertEqual(restored["settings"]["biome"], "Island")
        self.assertEqual(self.snapshot(), frozen)

    def test_empty_first_map_undo_restores_the_pre_map_manual_biome(self):
        """Cancelling an empty first map reverses its automatic classification as well."""
        logger.clear_export_and_reset_ids()
        logger.save_settings({"atlas_settings": self.atlas(), "city_type": "Faridun"})
        logger.save_settings({"biome": "Island"})
        self.assertEqual(logger.start_map()["settings"]["biome"], "Desert")
        restored = logger.undo_empty_map()
        self.assertEqual(restored["current_map_id"], "")
        self.assertEqual(restored["settings"]["biome"], "Island")

    def test_empty_map_undo_without_new_metadata_keeps_legacy_biome(self):
        """An older profile without the new restoration key cannot invent a previous selection."""
        logger.save_settings({"biome": "Island"})
        logger.finish_map(1, 0, 0, 0)
        logger.start_map()
        with logger._connect() as db:
            db.execute("DELETE FROM meta WHERE key='previous_biome'")
        self.assertEqual(logger.undo_empty_map()["settings"]["biome"], "Island")

    def test_atlas_and_automatic_biome_are_one_undo_action(self):
        """Undo a saved Atlas effect returns the editable tags and map snapshot to their previous state."""
        logger.save_settings({"city_type": "Faridun", "biome": "Island"})
        before = self.snapshot()
        logger.save_atlas_settings(self.atlas())
        state = logger.undo_last_action()["state"]
        self.assertEqual(state["settings"]["biome"], "Island")
        self.assertEqual(self.snapshot(), before)

    def test_future_atlas_change_waits_for_next_map_and_never_rewrites_recorded_context(self):
        """A frozen Desert map stays Desert while its next map receives the edited Mountain effect."""
        logger.save_atlas_settings(self.atlas())
        logger.save_settings({"city_type": "Faridun"})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        before = self.snapshot()
        with logger._connect() as db:
            start = tuple(db.execute("SELECT * FROM currency_snapshots").fetchone())
        state = logger.save_atlas_settings(self.atlas(choice="AtlasBiomeCityNotable10b"))
        self.assertEqual(state["atlas_settings_target_map_id"], "M0002")
        self.assertEqual(state["settings"]["biome"], "Desert")
        self.assertEqual(self.snapshot(), before)
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 4}])
        with logger._connect() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM currency_snapshots WHERE phase='start'").fetchone()), start)
            queued = json.loads(db.execute("SELECT snapshot_json FROM commits WHERE kind='Atlas settings' "
                                           "AND map_id='M0002'").fetchone()[0])
            end = json.loads(db.execute("SELECT snapshot_json FROM currency_snapshots WHERE phase='end'").fetchone()[0])
        self.assertEqual(queued["biome"], "Mountain")
        self.assertEqual(end["biome"], "Desert")
        self.assertEqual(end["atlas_setup_id"], before["atlas_setup_id"])
        logger.finish_map(1, 0, 0, 0)
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 4}])
        self.assertEqual(logger.get_state()["settings"]["biome"], "Mountain")
        with logger._connect() as db:
            upcoming = json.loads(db.execute("SELECT snapshot_json FROM currency_snapshots "
                                             "WHERE map_id='M0002' AND phase='start'").fetchone()[0])
        self.assertEqual(upcoming["biome"], "Mountain")
        restored = logger.undo_last_action()["state"]
        self.assertEqual(restored["settings"]["biome"], "Desert")
        self.assertEqual(self.snapshot("M0001"), before)
        state = logger.start_map()
        self.assertEqual(state["settings"]["biome"], "Mountain")
        self.assertEqual(self.snapshot("M0002")["biome"], "Mountain")
        self.assertEqual(self.snapshot("M0001"), before)
        restored = logger.undo_last_action()["state"]
        self.assertEqual(restored["settings"]["biome"], "Desert")
        self.assertEqual(restored["current_map_id"], "M0001")

    def test_future_atlas_edit_preserves_the_active_maps_manual_biome(self):
        """A queued effect must not replace a manual tag on the already frozen current map."""
        logger.save_atlas_settings(self.atlas())
        logger.save_settings({"city_type": "Faridun"})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        frozen = self.snapshot()
        logger.save_settings({"biome": "Island"})
        state = logger.save_atlas_settings(self.atlas(choice="AtlasBiomeCityNotable10b"))
        self.assertEqual(state["atlas_settings_target_map_id"], "M0002")
        self.assertEqual(state["settings"]["biome"], "Island")
        self.assertEqual(self.snapshot(), frozen)
        logger.finish_map(1, 0, 0, 0)
        self.assertEqual(logger.start_map()["settings"]["biome"], "Mountain")

    def test_pre_map_inventory_retains_its_frozen_city_effect(self):
        """Creating a map after Start inventory uses its frozen Atlas despite an intervening edit."""
        logger.clear_export_and_reset_ids()
        logger.save_atlas_settings(self.atlas())
        logger.save_settings({"city_type": "Faridun"})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        logger.save_atlas_settings(self.atlas(choice="AtlasBiomeCityNotable10b"))
        state = logger.start_map()
        self.assertEqual(state["settings"]["biome"], "Desert")
        self.assertEqual(self.snapshot()["biome"], "Desert")

    def test_implicit_remnant_creates_next_map_with_new_city_effect(self):
        """The normal Remnant path applies queued Atlas classification when it creates the next map."""
        logger.save_atlas_settings(self.atlas())
        logger.save_settings({"city_type": "Faridun"})
        logger.commit_chain("Rage")
        logger.save_atlas_settings(self.atlas(choice="AtlasBiomeCityNotable10b"))
        logger.finish_map(1, 0, 0, 0)
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        self.assertEqual(logger.get_state()["settings"]["biome"], "Mountain")
        self.assertEqual(self.snapshot("M0002")["biome"], "Mountain")
        self.assertEqual(self.snapshot("M0001")["biome"], "Desert")

    def test_frozen_city_effect_uses_its_own_catalog_after_catalog_update(self):
        """Later catalog changes cannot reinterpret an active map's recorded city effect."""
        logger.save_atlas_settings(self.atlas())
        logger.save_settings({"city_type": "Faridun"})
        logger.commit_chain("Rage")
        self.catalog = copy.deepcopy(self.catalog)
        choice = self.catalog["nodes"]["AtlasBiomeCityNotable10"]["choices"][0]
        choice["stats"] = {"map_city_faridun_also_count_as_water": 1}
        logger.save_atlas_settings(self.atlas())
        logger.save_settings({"city_type": "None", "biome": "Island"})
        state = logger.save_settings({"city_type": "Faridun"})
        self.assertEqual(state["settings"]["biome"], "Desert")


class AtlasBiomeSyncUITests(AtlasBiomeProfile):
    """Exercise existing City, Biome and Atlas Save controls without changing their layout or choices."""

    @classmethod
    def setUpClass(cls):
        """Reuse the QApplication required for real desktop widget events."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open the unchanged desktop over the disposable profile and small Atlas scene."""
        super().setUp()
        self.ui_catalog_patch = patch("PoE2_Data_Logger.ui.atlas_settings.catalog", side_effect=lambda: self.catalog)
        self.ui_catalog_patch.start()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1280, 720)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        """Dispose of the desktop and workers before removing its database."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.ui_catalog_patch.stop()
        super().tearDown()

    def save_atlas(self, setup):
        """Click the existing Atlas Save button after editing its normal settings data."""
        self.window.atlas_settings_page.set_settings(setup, force=True)
        self.window.atlas_settings_page.save_button.click()
        self.app.processEvents()

    def choose(self, control, selected):
        """Change a real combo's current item, delivering its existing save signal."""
        control.setCurrentIndex(control.findData(selected))
        self.app.processEvents()

    def assert_biome(self, expected):
        """Check both existing Biome controls and persisted state agree."""
        self.assertEqual(value(self.window.header_biome), expected)
        self.assertEqual(value(self.window.biome), expected)
        self.assertEqual(logger.get_state()["settings"]["biome"], expected)

    def test_header_city_and_atlas_save_autofill_both_existing_biome_controls(self):
        """A City change and real Atlas Save refresh the two existing Biome combos in either order."""
        buttons = [button.text() for button in self.window.findChildren(QPushButton)]
        combos = len(self.window.findChildren(QComboBox))
        self.choose(self.window.header_city_type, "Faridun")
        self.save_atlas(self.atlas())
        self.assert_biome("Desert")
        self.save_atlas(self.atlas(choice="AtlasBiomeCityNotable10b"))
        self.assert_biome("Mountain")
        self.choose(self.window.header_city_type, "Ezomyte")
        self.save_atlas(self.atlas("AtlasBiomeCityNotable11", "AtlasBiomeCityNotable11b"))
        self.assert_biome("Swamp")
        self.assertEqual([button.text() for button in self.window.findChildren(QPushButton)], buttons)
        self.assertEqual(len(self.window.findChildren(QComboBox)), combos)
        self.assertEqual([self.window.header_biome.itemData(index)
                          for index in range(self.window.header_biome.count())], list(logger.BIOMES))

    def test_manual_biome_and_full_settings_save_preserve_explicit_selection(self):
        """The existing manual Biome dropdown and full Map Settings Save remain editable with an active rule."""
        self.save_atlas(self.atlas())
        self.choose(self.window.city_type, "Faridun")
        self.assert_biome("Desert")
        self.choose(self.window.biome, "Island")
        self.assert_biome("Island")
        self.window.save_map_settings()
        self.app.processEvents()
        self.assert_biome("Island")

    def test_existing_undo_button_restores_city_and_biome_together(self):
        """One click on existing Undo reverses an automatic city classification and restores both controls."""
        self.save_atlas(self.atlas())
        self.choose(self.window.header_city_type, "Faridun")
        self.assert_biome("Desert")
        self.window.undo_button.click()
        self.app.processEvents()
        self.assertEqual(value(self.window.header_city_type), "None")
        self.assert_biome("None")


if __name__ == "__main__":
    unittest.main()
