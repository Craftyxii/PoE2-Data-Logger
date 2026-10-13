"""Applicable Atlas area bonuses form verified totals for one map context."""
import copy
import json
import unittest

from PoE2_Data_Logger.core.atlas_effect_snapshot import build_snapshot


QUANTITY = "map_item_drop_quantity_+%"
DESERT_QUANTITY = "map_desert_item_drop_quantity_+%"
PACK_SIZE = "map_pack_size_+%"
DESERT_PACK_SIZE = "map_desert_pack_size_+%"
CITY_PACK_SIZE = "map_city_pack_size_+%"
SOURCE_HASHES = {"fixture-source": "combined-totals-v1"}


def node(stats, activity="Main Atlas", choices=()):
    """Keep fixtures independent of the user's saved Atlas setup."""
    return {"name": "fixture node", "activity": activity, "allocatable": True,
            "stats": stats, "effects": [], "choices": list(choices)}


def catalog(nodes):
    return {"version": "combined-totals-v1", "nodes": nodes,
            "provenance": {"input_hashes": SOURCE_HASHES}}


def rule(stat, **changes):
    """Give synthetic source amounts an explicitly pinned linear translation."""
    result = {"stat_id": stat, "source_hashes_json": json.dumps(SOURCE_HASHES, sort_keys=True,
              separators=(",", ":")), "min_raw": None, "max_raw": None,
              "multiplier": 1, "divisor": 1, "unit": "%", "format": "#",
              "template": "{0}% fixture bonus"}
    result.update(changes)
    return result


def context(data, allocated=None, choices=None, **changes):
    result = {"atlas_setup_id": "fixture-setup", "atlas_catalog_id": "fixture-catalog",
              "activity_type": "Expedition", "biome": "Desert", "city_type": "Faridun",
              "item_rarity": 91, "monster_rarity": 82, "pack_size": 73, "effectiveness": 64,
              "atlas_settings": {"catalog_version": data["version"], "catalog_id": "fixture-catalog",
                  "allocated": list(data["nodes"]) if allocated is None else allocated,
                  "choices": choices or {}}}
    result.update(changes)
    return result


def totals(snapshot):
    """Use a list for each ID so accidental duplicate totals remain visible."""
    result = {}
    for row in snapshot["totals"]:
        result.setdefault(row["stat_id"], []).append(row)
    return result


class CombinedAtlasTotalsTests(unittest.TestCase):
    def only_total(self, snapshot, stat):
        rows = totals(snapshot).get(stat, [])
        self.assertEqual(len(rows), 1, (stat, rows))
        return rows[0]

    def test_generic_desert_minors_and_selected_choice_sum_once(self):
        data = catalog({
            "general": node({QUANTITY: 5}),
            "minor-a": node({DESERT_QUANTITY: 2}),
            "minor-b": node({DESERT_QUANTITY: 2}),
            "mastery": node({}, choices=[
                {"id": "selected", "stats": {DESERT_QUANTITY: 3}},
                {"id": "inactive", "stats": {DESERT_QUANTITY: 100}}]),
        })
        selected = context(data, allocated=["general", "minor-a", "minor-b", "minor-a", "mastery"],
                           choices={"mastery": "selected"})
        original = copy.deepcopy(selected)
        snapshot = build_snapshot(selected, data, [rule(QUANTITY), rule(DESERT_QUANTITY)])
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["totals"], [{"stat_id": QUANTITY, "raw_total": 12,
            "display_total": 12, "unit": "%", "source_stat_ids": sorted([QUANTITY, DESERT_QUANTITY])}])
        self.assertEqual(selected, original)

    def test_city_biome_and_general_pack_size_form_one_total(self):
        data = catalog({"general": node({PACK_SIZE: 3}), "desert": node({DESERT_PACK_SIZE: 4}),
                        "city": node({CITY_PACK_SIZE: 6})})
        snapshot = build_snapshot(context(data), data,
                                  [rule(PACK_SIZE), rule(DESERT_PACK_SIZE), rule(CITY_PACK_SIZE)])
        self.assertEqual(snapshot["totals"], [{"stat_id": PACK_SIZE, "raw_total": 13,
            "display_total": 13, "unit": "%",
            "source_stat_ids": sorted([PACK_SIZE, DESERT_PACK_SIZE, CITY_PACK_SIZE])}])

    def test_inapplicable_and_unallocated_effects_do_not_inflate_total(self):
        forest = "map_forest_item_drop_quantity_+%"
        ezomyte = "map_city_ezomyte_item_drop_quantity_+%"
        data = catalog({
            "general": node({QUANTITY: 5}), "desert": node({DESERT_QUANTITY: 2}),
            "forest": node({forest: 100}), "wrong-city": node({ezomyte: 200}),
            "other-activity": node({QUANTITY: 300}, activity="Breach"),
            "unallocated": node({DESERT_QUANTITY: 400}),
            "unallocated-choice": node({}, choices=[{"id": "saved", "stats": {DESERT_QUANTITY: 500}}]),
        })
        selected = context(data, allocated=["general", "desert", "forest", "wrong-city", "other-activity"],
                           choices={"unallocated-choice": "saved"})
        snapshot = build_snapshot(selected, data, [rule(stat) for stat in (QUANTITY, DESERT_QUANTITY, forest, ezomyte)])
        self.assertEqual(snapshot["totals"], [{"stat_id": QUANTITY, "raw_total": 7,
            "display_total": 7, "unit": "%", "source_stat_ids": sorted([QUANTITY, DESERT_QUANTITY])}])

    def test_missing_choice_does_not_add_its_bonus(self):
        data = catalog({"general": node({QUANTITY: 5}), "desert": node({DESERT_QUANTITY: 2}),
            "mastery": node({}, choices=[{"id": "bonus", "stats": {DESERT_QUANTITY: 100}}])})
        snapshot = build_snapshot(context(data), data, [rule(QUANTITY), rule(DESERT_QUANTITY)])
        self.assertEqual(self.only_total(snapshot, QUANTITY)["display_total"], 7)
        self.assertEqual(snapshot["unresolved"], [{"node_id": "mastery", "choice_id": "", "reason": "choice_unresolved"}])

    def test_map_magic_rare_breach_and_per_unit_targets_remain_distinct(self):
        magic, desert_magic = "map_magic_pack_size_+%", "map_desert_magic_pack_size_+%"
        rare, desert_rare = "map_number_of_rare_packs_+%", "map_desert_number_of_rare_packs_+%"
        breach = "map_breach_pack_size_+%"
        per_unit = "map_pack_size_+%_per_tablet"
        desert_per_unit = "map_desert_pack_size_+%_per_tablet"
        data = catalog({"general": node({PACK_SIZE: 3, magic: 4, rare: 5, per_unit: 6}),
            "desert": node({DESERT_PACK_SIZE: 7, desert_magic: 8, desert_rare: 9, desert_per_unit: 10}),
            "breach": node({breach: 11}, activity="Breach")})
        stats = (PACK_SIZE, DESERT_PACK_SIZE, magic, desert_magic, rare, desert_rare, breach, per_unit, desert_per_unit)
        snapshot = build_snapshot(context(data, activity_type="Breach"), data, [rule(stat) for stat in stats])
        self.assertEqual(set(totals(snapshot)), {PACK_SIZE, magic, rare, breach, per_unit, desert_per_unit})
        for stat, amount in ((PACK_SIZE, 10), (magic, 12), (rare, 14), (breach, 11), (per_unit, 6), (desert_per_unit, 10)):
            with self.subTest(stat=stat):
                self.assertEqual(self.only_total(snapshot, stat)["display_total"], amount)
        self.assertEqual(self.only_total(snapshot, magic)["conditions"], ["catalog_target"])
        self.assertEqual(self.only_total(snapshot, rare)["conditions"], ["catalog_target"])
        self.assertEqual(self.only_total(snapshot, per_unit)["conditions"], ["runtime_count"])

    def test_unselected_biome_is_retained_as_a_separate_conditional_total(self):
        data = catalog({"general": node({QUANTITY: 5}), "desert": node({DESERT_QUANTITY: 2})})
        snapshot = build_snapshot(context(data, biome="None"), data, [rule(QUANTITY), rule(DESERT_QUANTITY)])
        self.assertEqual(set(totals(snapshot)), {QUANTITY, DESERT_QUANTITY})
        self.assertEqual(self.only_total(snapshot, QUANTITY)["display_total"], 5)
        desert = self.only_total(snapshot, DESERT_QUANTITY)
        self.assertEqual(desert["display_total"], 2)
        self.assertEqual(desert["conditions"], ["biome_unselected:desert"])
        self.assertNotIn("source_stat_ids", desert)

    def test_unverified_translation_preserves_exact_id_sums(self):
        data = catalog({"general": node({QUANTITY: 5}), "desert-a": node({DESERT_QUANTITY: 2}),
                        "desert-b": node({DESERT_QUANTITY: 2})})
        snapshot = build_snapshot(context(data), data)
        self.assertEqual(snapshot["totals"], [
            {"stat_id": DESERT_QUANTITY, "raw_total": 4}, {"stat_id": QUANTITY, "raw_total": 5}])

    def test_mismatched_units_scales_or_source_hashes_prevent_merge(self):
        data = catalog({"general": node({QUANTITY: 5}), "desert": node({DESERT_QUANTITY: 2})})
        mismatches = {"unit": {"unit": "count"}, "divisor": {"divisor": 100},
                      "signed scale": {"multiplier": -1},
                      "source hash": {"source_hashes_json": '{"fixture-source":"other"}'}}
        for label, changes in mismatches.items():
            with self.subTest(mismatch=label):
                snapshot = build_snapshot(context(data), data, [rule(QUANTITY), rule(DESERT_QUANTITY, **changes)])
                self.assertEqual(set(totals(snapshot)), {QUANTITY, DESERT_QUANTITY})
                self.assertEqual(self.only_total(snapshot, QUANTITY)["display_total"], 5)
                desert = self.only_total(snapshot, DESERT_QUANTITY)
                self.assertEqual(desert["raw_total"], 2)
                self.assertNotIn("source_stat_ids", desert)
                if label == "source hash":
                    self.assertNotIn("display_total", desert)

    def test_translation_bounds_apply_to_each_source_total(self):
        data = catalog({"general": node({QUANTITY: 5}), "desert-a": node({DESERT_QUANTITY: 2}),
                        "desert-b": node({DESERT_QUANTITY: 2})})
        for changes in ({"max_raw": 3}, {"min_raw": 5}):
            with self.subTest(bounds=changes):
                snapshot = build_snapshot(context(data), data, [rule(QUANTITY), rule(DESERT_QUANTITY, **changes)])
                self.assertEqual(set(totals(snapshot)), {QUANTITY, DESERT_QUANTITY})
                self.assertEqual(self.only_total(snapshot, DESERT_QUANTITY), {"stat_id": DESERT_QUANTITY, "raw_total": 4})
        snapshot = build_snapshot(context(data), data,
                                  [rule(QUANTITY), rule(DESERT_QUANTITY, min_raw=4, max_raw=4)])
        self.assertEqual(self.only_total(snapshot, QUANTITY)["display_total"], 9)

    def test_zero_and_negative_verified_values_are_preserved(self):
        for general, desert, expected in ((0, 0, 0), (5, -5, 0), (-3, -2, -5)):
            with self.subTest(general=general, desert=desert):
                data = catalog({"general": node({QUANTITY: general}), "desert": node({DESERT_QUANTITY: desert})})
                snapshot = build_snapshot(context(data), data, [rule(QUANTITY), rule(DESERT_QUANTITY)])
                row = self.only_total(snapshot, QUANTITY)
                self.assertEqual((row["raw_total"], row["display_total"]), (expected, expected))
                self.assertEqual(row["source_stat_ids"], sorted([QUANTITY, DESERT_QUANTITY]))

    def test_verified_source_displays_combine_without_a_canonical_rule(self):
        water = "map_water_item_drop_quantity_+%"
        data = catalog({"desert": node({DESERT_QUANTITY: 150}), "water": node({water: 250}),
                        "classification": node({"map_city_faridun_also_count_as_water": 1})})
        snapshot = build_snapshot(context(data), data,
                                  [rule(DESERT_QUANTITY, divisor=100), rule(water, divisor=100)])
        row = self.only_total(snapshot, QUANTITY)
        self.assertEqual((row["raw_total"], row["display_total"], row["unit"]), (400, 4, "%"))
        self.assertEqual(row["source_stat_ids"], sorted([DESERT_QUANTITY, water]))

    def test_overlapping_classifications_and_duplicate_allocations_apply_once(self):
        water = "map_water_item_drop_quantity_+%"
        data = catalog({"general": node({QUANTITY: 5}), "desert": node({DESERT_QUANTITY: 2}),
            "water": node({water: 3}),
            "city-desert": node({"map_city_faridun_also_count_as_desert": 1}),
            "city-water": node({"map_city_faridun_also_count_as_water": 1}),
            "ocean-water": node({"map_expedition_oceans_count_as_water": 1}, activity="Expedition")})
        selected = context(data, allocated=list(data["nodes"]) + ["desert", "water"], ocean="Yes")
        snapshot = build_snapshot(selected, data, [rule(stat) for stat in (QUANTITY, DESERT_QUANTITY, water)])
        self.assertEqual(snapshot["effective_biomes"], ["Desert", "Ocean", "Water"])
        row = self.only_total(snapshot, QUANTITY)
        self.assertEqual((row["raw_total"], row["display_total"]), (10, 10))
        self.assertEqual(row["source_stat_ids"], sorted([QUANTITY, DESERT_QUANTITY, water]))

    def test_real_catalog_general_biome_and_city_pack_size_sum_to_fifteen(self):
        from PoE2_Data_Logger.core.atlas_catalog import catalog as bundled_catalog
        from PoE2_Data_Logger.core.analysis_context_rules import linear_stat_rules

        data = bundled_catalog()
        selected = context(data, allocated=["AtlasGenericSmall38", "AtlasGenericSmall39_",
                           "AtlasBiomeGenericNotable3", "AtlasBiomeCityNotable5_"])
        snapshot = build_snapshot(selected, data, linear_stat_rules())
        row = self.only_total(snapshot, PACK_SIZE)
        self.assertEqual((row["raw_total"], row["display_total"], row["unit"]), (15, 15, "%"))
        self.assertEqual(row["source_stat_ids"], sorted([PACK_SIZE, DESERT_PACK_SIZE, CITY_PACK_SIZE]))
        self.assertNotIn("map_water_pack_size_+%", totals(snapshot))

    def test_real_catalog_summoning_circle_alias_combines_city_grass_and_general(self):
        from PoE2_Data_Logger.core.atlas_catalog import catalog as bundled_catalog
        from PoE2_Data_Logger.core.analysis_context_rules import linear_stat_rules

        data = bundled_catalog()
        selected = context(data, allocated=["AtlasBiomeCity4_", "AtlasBiomeGrass3", "AtlasStoneCircle1"],
                           biome="Grass", city_type="Faridun")
        snapshot = build_snapshot(selected, data, linear_stat_rules())
        stat = "map_stone_circle_chance_+%"
        row = self.only_total(snapshot, stat)
        self.assertEqual((row["raw_total"], row["display_total"], row["unit"]), (140, 140, "%"))
        self.assertEqual(row["source_stat_ids"], sorted([stat, "map_city_stone_circle_chance_%",
                                                       "map_grass_stone_circle_chance_%"]))
        self.assertEqual(row["conditions"], ["catalog_target"])
        self.assertEqual(len(snapshot["totals"]), 1)

    def test_summoning_circle_alias_keeps_other_targets_and_chance_spellings_distinct(self):
        circle = "map_stone_circle_chance_+%"
        desert_circle = "map_desert_stone_circle_chance_%"
        boss = "map_stone_circle_city_citadel_boss_chance_%"
        per_unit = "map_desert_stone_circle_chance_%_per_tablet"
        strongbox = "map_strongbox_chance_%"
        desert_strongbox = "map_desert_strongbox_chance_+%"
        data = catalog({"general": node({circle: 40, boss: 7, strongbox: 9}),
                        "desert": node({desert_circle: 50, per_unit: 8, desert_strongbox: 10})})
        stats = (circle, desert_circle, boss, per_unit, strongbox, desert_strongbox)
        snapshot = build_snapshot(context(data), data, [rule(stat) for stat in stats])
        self.assertEqual(set(totals(snapshot)), {circle, boss, per_unit, strongbox, desert_strongbox})
        row = self.only_total(snapshot, circle)
        self.assertEqual(row["display_total"], 90)
        self.assertEqual(row["source_stat_ids"], sorted([circle, desert_circle]))
        for stat, amount in ((boss, 7), (per_unit, 8), (strongbox, 9), (desert_strongbox, 10)):
            with self.subTest(stat=stat):
                row = self.only_total(snapshot, stat)
                self.assertEqual(row["display_total"], amount)
                self.assertNotIn("source_stat_ids", row)


if __name__ == "__main__":
    unittest.main()
