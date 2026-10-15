"""Atlas aggregates retain their source, target and historical map context."""
import copy
import hashlib
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.core.atlas_effect_snapshot import build_snapshot


def fixture_catalog():
    """Provide distinct biome/city/activity stats and one conditional coefficient."""
    nodes = {}
    def node(key, stats, activity="Main Atlas", choices=None):
        nodes[key] = {"name": key, "activity": activity, "allocatable": True,
                      "stats": stats, "effects": [key + " source effect"], "choices": choices or []}
    node("quantity1", {"map_desert_item_drop_quantity_+%": 2})
    node("quantity2", {"map_desert_item_drop_quantity_+%": 2})
    node("generic", {"map_desert_number_of_rare_packs_+%": 20, "map_forest_number_of_rare_packs_+%": 20})
    node("city", {"map_city_pack_size_+%": 6, "map_city_faridun_additional_shrine_+": 1,
                  "map_city_ezomyte_additional_stone_circle_+": 1})
    node("classification", {"dummy_display_faridun_city_biome_selector": 1}, choices=[
        {"id": "desert", "stats": {"map_city_faridun_also_count_as_desert": 1}, "effects": ["Faridun Cities are also considered Desert Areas"]}])
    node("desert-choice", {"dummy_display_desert_mastery_choice": 1}, choices=[
        {"id": "power", "stats": {"map_desert_monster_potency_+%": 15}, "effects": ["15% increased Effectiveness in Desert Areas"]},
        {"id": "alternate", "stats": {"map_desert_monster_potency_+%": 100}, "effects": ["100% alternate"]}])
    node("breach", {"map_breach_monster_potency_+%": 20}, "Breach")
    node("ritual", {"map_ritual_tribute_+%": 5}, "Ritual")
    node("delirium", {"map_delirium_giga_mirror_chance_+%_at_max_depth": 5}, "Delirium")
    node("expedition", {"expedition_monsters_logbook_chance_+%": 5}, "Expedition")
    node("grand", {"map_expedition_logbook_level_+": 1, "dummy_expedition_logbooks_have_up_to_3_modifiers": 1}, "Expedition")
    node("conditional", {"map_expedition_runic_monsters_logbook_chance_+%_per_runic_modifier": 50}, "Expedition")
    node("unknown-state", {"map_corrupted_rare_monster_modifiers_chance_%": 10})
    node("scaled-display", {"map_ezomyte_anomaly_%_chance_extra_deadly_boss_lineage": 1500})
    return {"version": "effects-test-1", "nodes": nodes, "provenance": {"input_hashes": {"source": "test"}}}


def snapshot_context(catalog=None):
    """Select the fixture's nodes and choices for one Faridun desert Expedition."""
    data = catalog or fixture_catalog()
    return {"atlas_setup_id": "fixture-setup", "atlas_catalog_id": "fixture-catalog",
            "activity_type": "Expedition", "biome": "Desert", "city_type": "Faridun",
            "item_rarity": 91, "monster_rarity": 82, "pack_size": 73, "effectiveness": 64,
            "atlas_settings": {"catalog_version": data["version"], "catalog_id": "fixture-catalog",
                "allocated": list(data["nodes"]), "choices": {"classification": "desert", "desert-choice": "power"}}}


class AtlasEffectSnapshotTests(unittest.TestCase):
    """Evaluate source coefficients, context filtering and unresolved evidence."""
    def effects(self, context=None, data=None, rules=()):
        """Index one pure aggregate by exact source stat identity."""
        return {row["stat_id"]: row for row in build_snapshot(context or snapshot_context(), data or fixture_catalog(), rules)["totals"]}

    def test_exact_stats_sum_once_and_leave_observations_unchanged(self):
        """Repeated minors sum without merging different targets or adding to observed stats."""
        context = snapshot_context()
        original = copy.deepcopy(context)
        stats = self.effects(context)
        self.assertEqual(stats["map_desert_item_drop_quantity_+%"]["raw_total"], 4)
        self.assertNotIn("contribution_count", stats["map_desert_item_drop_quantity_+%"])
        self.assertEqual(stats["map_desert_number_of_rare_packs_+%"]["raw_total"], 20)
        self.assertNotIn("map_forest_number_of_rare_packs_+%", stats)
        self.assertNotIn("map_city_ezomyte_additional_stone_circle_+", stats)
        self.assertEqual(context, original)

    def test_additional_city_biome_applies_without_erasing_manual_classification(self):
        """Faridun's allocated Desert flag adds classification while retaining the entered biome."""
        context = snapshot_context()
        context["biome"] = "Forest"
        result = build_snapshot(context, fixture_catalog())
        self.assertEqual(result["biome"], "Forest")
        self.assertEqual(result["effective_biomes"], ["Desert", "Forest"])
        totals = {row["stat_id"]: row for row in result["totals"]}
        self.assertEqual(totals["map_desert_number_of_rare_packs_+%"]["raw_total"], 20)
        self.assertEqual(totals["map_forest_number_of_rare_packs_+%"]["raw_total"], 20)

    def test_unallocated_or_unselected_choices_never_add_bonus(self):
        """A saved dropdown choice alone cannot activate an unallocated parent."""
        context = snapshot_context()
        context["atlas_settings"]["allocated"].remove("desert-choice")
        self.assertNotIn("map_desert_monster_potency_+%", self.effects(context))
        context["atlas_settings"]["allocated"].append("desert-choice")
        del context["atlas_settings"]["choices"]["desert-choice"]
        result = build_snapshot(context, fixture_catalog())
        self.assertNotIn("map_desert_monster_potency_+%", {row["stat_id"] for row in result["totals"]})
        self.assertTrue(any(row["node_id"] == "desert-choice" for row in result["unresolved"]))

    def test_one_activity_and_explicit_grand_only_effects(self):
        """Expedition shares its tree with Grand, without giving normal maps Grand-only levels."""
        context = snapshot_context()
        totals = self.effects(context)
        self.assertNotIn("map_breach_monster_potency_+%", totals)
        self.assertNotIn("map_ritual_tribute_+%", totals)
        self.assertNotIn("map_expedition_logbook_level_+", totals)
        context["activity_type"] = "Grand Expedition"
        self.assertEqual(self.effects(context)["map_expedition_logbook_level_+"]["raw_total"], 1)
        context["activity_type"] = "Ritual"
        self.assertIn("map_ritual_tribute_+%", self.effects(context))
        self.assertNotIn("expedition_monsters_logbook_chance_+%", self.effects(context))

    def test_runtime_coefficients_and_unknown_units_stay_explicit(self):
        """A 50% per-modifier coefficient is not a fabricated encounter total."""
        totals = self.effects()
        conditional = totals["map_expedition_runic_monsters_logbook_chance_+%_per_runic_modifier"]
        self.assertEqual(conditional["raw_total"], 50)
        self.assertIsNone(conditional.get("display_total"))
        self.assertTrue(conditional["conditions"])
        self.assertTrue(totals["map_corrupted_rare_monster_modifiers_chance_%"]["conditions"])

    def test_deli_flag_includes_delirium_independently_of_primary_activity(self):
        """Each primary encounter keeps its own tree while Deli adds its allocated coefficient once."""
        stat = "map_delirium_giga_mirror_chance_+%_at_max_depth"
        for activity in logger.ACTIVITY_TYPES:
            for deli in (True, "Yes", False, "No", None):
                with self.subTest(activity=activity, deli=deli):
                    context = snapshot_context()
                    context.update({"activity_type": activity, "deli": deli})
                    context["atlas_settings"]["allocated"].append("delirium")
                    original = copy.deepcopy(context)
                    result = build_snapshot(context, fixture_catalog())
                    totals = {row["stat_id"]: row for row in result["totals"]}
                    if deli is True or deli == "Yes":
                        self.assertEqual(totals[stat]["raw_total"], 5)
                        self.assertEqual(totals[stat]["conditions"], ["catalog_trigger"])
                    else:
                        self.assertNotIn(stat, totals)
                    self.assertEqual("map_breach_monster_potency_+%" in totals, activity == "Breach")
                    self.assertEqual("map_ritual_tribute_+%" in totals, activity == "Ritual")
                    self.assertEqual("expedition_monsters_logbook_chance_+%" in totals,
                                     activity in ("Expedition", "Grand Expedition"))
                    self.assertEqual(result["deli"], deli)
                    self.assertEqual(context, original)

    def test_deli_flag_does_not_activate_unallocated_delirium_nodes(self):
        """The map option only filters saved allocations; it cannot allocate the Delirium tree."""
        context = snapshot_context()
        context["deli"] = "Yes"
        context["atlas_settings"]["allocated"].remove("delirium")
        self.assertNotIn("map_delirium_giga_mirror_chance_+%_at_max_depth", self.effects(context))

    def test_display_conversion_requires_matching_frozen_source(self):
        """Only the pinned translation turns 1500 raw into its displayed 15%, without guessing units."""
        stat = "map_ezomyte_anomaly_%_chance_extra_deadly_boss_lineage"
        rule = {"stat_id": stat, "source_hashes_json": '{"source":"test"}', "min_raw": None, "max_raw": None,
                "multiplier": 1, "divisor": 100, "unit": "%", "format": "#", "template": "{0}% extra support chance"}
        row = self.effects(rules=[rule])[stat]
        self.assertEqual((row["display_total"], row["unit"]), (15, "%"))
        self.assertIsNone(self.effects(rules=[{**rule, "source_hashes_json": '{"source":"different"}'}])[stat].get("display_total"))

    def test_unknown_biome_preserves_condition_and_unknown_nodes(self):
        """Missing tags or retired catalog nodes are unresolved facts, not successful bonus inference."""
        context = snapshot_context()
        context["biome"] = "None"
        context["city_type"] = "None"
        context["atlas_settings"]["allocated"].append("retired-node")
        result = build_snapshot(context, fixture_catalog())
        self.assertTrue(any(row["node_id"] == "retired-node" for row in result["unresolved"]))
        self.assertTrue(self.effects(context)["map_desert_item_drop_quantity_+%"]["conditions"])
        self.assertNotIn("map_city_pack_size_+%", self.effects(context))

    def test_ocean_flag_applies_classification_independently_of_manual_biome(self):
        """An Ocean map with Grass selected still receives its allocated Ocean-to-Water flag."""
        data = fixture_catalog()
        data["nodes"]["ocean-choice"] = {"name": "ocean choice", "activity": "Expedition", "allocatable": True,
            "stats": {}, "effects": [], "choices": [{"id": "water", "stats": {"map_expedition_oceans_count_as_water": 1}}]}
        data["nodes"]["water"] = {"name": "water", "activity": "Main Atlas", "allocatable": True,
            "stats": {"map_water_item_drop_rarity_+%": 3}, "effects": []}
        context = snapshot_context(data)
        context["atlas_settings"]["choices"]["ocean-choice"] = "water"
        context.update({"biome": "Grass", "city_type": "None", "ocean": "Yes"})
        result = build_snapshot(context, data)
        self.assertEqual(result["effective_biomes"], ["Grass", "Ocean", "Water"])
        self.assertEqual({row["stat_id"]: row for row in result["totals"]}["map_water_item_drop_rarity_+%"]["raw_total"], 3)
        context["ocean"] = "No"
        self.assertNotIn("map_water_item_drop_rarity_+%", self.effects(context, data))
        context.update({"ocean": "Yes", "biome": "None", "city_type": "Faridun"})
        result = self.effects(context, data)
        self.assertNotIn("biome_unselected:desert", result["map_desert_item_drop_quantity_+%"].get("conditions", []))
        self.assertIn("biome_unselected:forest", result["map_forest_number_of_rare_packs_+%"]["conditions"])
        self.assertNotIn("biome_unselected:water", result["map_water_item_drop_rarity_+%"].get("conditions", []))

    def test_full_valid_catalog_allocation_fits_losslessly_in_excel(self):
        """All allocated nodes with valid first choices fit even with unselected area tags."""
        from PoE2_Data_Logger.core.atlas_catalog import catalog
        from PoE2_Data_Logger.core.analysis_context_rules import linear_stat_rules
        data = catalog()
        rules = linear_stat_rules()
        settings = {"catalog_version": data["version"], "catalog_id": "c" * 64,
                    "allocated": [key for key, node in data["nodes"].items() if node.get("allocatable")],
                    "choices": {key: node["choices"][0]["id"] for key, node in data["nodes"].items()
                                if node.get("allocatable") and node.get("choices")}}
        for activity in logger.ACTIVITY_TYPES:
            for city in logger.CITY_TYPES:
                for biome in logger.BIOMES:
                    for ocean, deli in product(("No", "Yes"), repeat=2):
                        with self.subTest(activity=activity, city=city, biome=biome, ocean=ocean, deli=deli):
                            result = build_snapshot({"atlas_settings": settings, "atlas_setup_id": "A" + "a" * 64,
                                "atlas_catalog_id": "c" * 64, "activity_type": activity,
                                "city_type": city, "biome": biome, "ocean": ocean, "deli": deli}, data, rules)
                            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                            self.assertLess(len(encoded), 32767)
                            self.assertEqual(json.loads(encoded), result)


class FrozenMapAtlasEffectTests(unittest.TestCase):
    """Use the persistence API to prove future Atlas edits cannot change old map aggregates."""
    def setUp(self):
        """Create an isolated database with deterministic source catalog IDs."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-map-atlas-effects-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        self.data = fixture_catalog()
        self.patches = [patch.object(logger, "_atlas_catalog_data", side_effect=lambda: self.data),
                        patch.object(logger, "_atlas_catalog_identity", side_effect=self.identity)]
        for item in self.patches:
            item.start()
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        """Restore catalog providers and data directory without touching user profiles."""
        for item in reversed(self.patches):
            item.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def identity(self):
        """Hash the precise fixture catalog to exercise historical catalog lookup."""
        encoded = json.dumps(self.data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest(), encoded

    def map_snapshot(self, map_id="M0001"):
        """Read the actual stored map context, rather than an editable HUD draft."""
        with logger._connect() as db:
            return json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()[0])

    def test_current_and_next_maps_keep_distinct_setup_totals(self):
        """A frozen two-minor Desert aggregate survives selecting a single minor for the next map."""
        logger.save_settings({"biome": "Desert", "activity_type": "Expedition"})
        logger.save_atlas_settings({"allocated": ["quantity1", "quantity2"]})
        logger.start_map()
        original = self.map_snapshot()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 1}])
        logger.save_atlas_settings({"allocated": ["quantity1"]})
        self.assertEqual(self.map_snapshot(), original)
        logger.save_settings({"activity_type": "Grand Expedition"})
        updated = self.map_snapshot()
        self.assertEqual(updated["atlas_setup_id"], original["atlas_setup_id"])
        self.assertEqual(updated["atlas_effect_snapshot"]["totals"][0]["raw_total"], 4)
        logger.finish_map(1, 0, 0, 0)
        logger.start_map()
        next_map = self.map_snapshot("M0002")
        self.assertEqual(next_map["atlas_effect_snapshot"]["totals"][0]["raw_total"], 2)
        self.assertNotEqual(next_map["atlas_setup_id"], original["atlas_setup_id"])

    def test_late_tag_uses_historical_catalog_not_new_bundled_effect(self):
        """After catalog turnover a map still reads its own frozen 2% node, not the new 99% definition."""
        logger.save_settings({"biome": "Desert"})
        logger.save_atlas_settings({"allocated": ["quantity1"]})
        logger.start_map()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 1}])
        frozen_id = self.map_snapshot()["atlas_catalog_id"]
        self.data = copy.deepcopy(self.data)
        self.data["version"] = "effects-test-2"
        self.data["nodes"]["quantity1"]["stats"]["map_desert_item_drop_quantity_+%"] = 99
        logger.save_atlas_settings({"allocated": ["quantity1"]})
        logger.save_settings({"activity_type": "Ritual"})
        saved = self.map_snapshot()
        self.assertEqual(saved["atlas_catalog_id"], frozen_id)
        self.assertEqual(saved["atlas_effect_snapshot"]["totals"][0]["raw_total"], 2)

    def test_ocean_flag_invalidates_existing_context_cache(self):
        """The same setup and biome with a changed independent Ocean tag recomputes its scope."""
        self.data["nodes"]["ocean"] = {"name": "ocean", "activity": "Expedition", "allocatable": True,
            "stats": {"map_expedition_oceans_count_as_water": 1}, "effects": []}
        self.data["nodes"]["water"] = {"name": "water", "activity": "Main Atlas", "allocatable": True,
            "stats": {"map_water_item_drop_rarity_+%": 3}, "effects": []}
        logger.save_settings({"biome": "Grass", "ocean": False, "activity_type": "Expedition"})
        logger.save_atlas_settings({"allocated": ["ocean", "water"]})
        logger.start_map()
        original = self.map_snapshot()
        self.assertEqual(original["atlas_effect_snapshot"]["totals"], [])
        with logger._connect() as db:
            changed = logger._with_atlas_effect_snapshot(db, {**original, "ocean": "Yes"})
        self.assertIn("map_water_item_drop_rarity_+%", {row["stat_id"] for row in changed["atlas_effect_snapshot"]["totals"]})
        self.assertEqual(original["atlas_effect_snapshot"]["totals"], [])

    def test_previous_schema_rebuild_preserves_stored_history(self):
        """A stale aggregate cache is rebuilt from its frozen setup without rewriting its map."""
        logger.save_settings({"biome": "Desert"})
        logger.save_atlas_settings({"allocated": ["quantity1", "quantity2"]})
        logger.start_map()
        original = self.map_snapshot()
        stale = copy.deepcopy(original)
        stale["atlas_effect_snapshot"] = {**stale["atlas_effect_snapshot"], "schema_version": 1,
                                           "totals": [{"stat_id": "wrong-cache", "raw_total": 999}]}
        with logger._connect() as db:
            db.execute("UPDATE maps SET snapshot_json=? WHERE map_id='M0001'", (json.dumps(stale),))
        with logger._connect() as db:
            updated = logger._with_atlas_effect_snapshot(db, stale)
        self.assertEqual(updated["atlas_effect_snapshot"]["schema_version"], 2)
        self.assertEqual(updated["atlas_effect_snapshot"]["totals"][0]["raw_total"], 4)
        self.assertEqual(updated["atlas_setup_id"], original["atlas_setup_id"])
        self.assertEqual(self.map_snapshot(), stale)
        self.assertEqual(stale["atlas_effect_snapshot"]["schema_version"], 1)

    def test_deli_flag_invalidates_cache_in_both_directions_and_old_missing_flag(self):
        """A Deli toggle or pre-fix cache cannot reuse totals calculated for different map context."""
        logger.save_settings({"deli": False, "activity_type": "Expedition"})
        logger.save_atlas_settings({"allocated": ["delirium", "expedition"]})
        logger.start_map()
        original = self.map_snapshot()
        stat = "map_delirium_giga_mirror_chance_+%_at_max_depth"
        with logger._connect() as db:
            enabled = logger._with_atlas_effect_snapshot(db, {**original, "deli": "Yes"})
            disabled = logger._with_atlas_effect_snapshot(db, {**enabled, "deli": "No"})
            legacy = copy.deepcopy(enabled)
            legacy["atlas_effect_snapshot"].pop("deli")
            legacy["atlas_effect_snapshot"]["totals"] = []
            rebuilt = logger._with_atlas_effect_snapshot(db, legacy)
        enabled_totals = {row["stat_id"]: row for row in enabled["atlas_effect_snapshot"]["totals"]}
        self.assertEqual(enabled_totals[stat]["raw_total"], 5)
        self.assertIn("expedition_monsters_logbook_chance_+%", enabled_totals)
        self.assertEqual(rebuilt["atlas_effect_snapshot"], enabled["atlas_effect_snapshot"])
        self.assertEqual(disabled["atlas_effect_snapshot"], original["atlas_effect_snapshot"])
        self.assertEqual(self.map_snapshot(), original)
        self.assertNotIn(stat, {row["stat_id"] for row in original["atlas_effect_snapshot"]["totals"]})


if __name__ == "__main__":
    unittest.main()
