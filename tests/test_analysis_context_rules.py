"""Atlas raw-stat display conversion preserves source scope and excludes guesses."""

import copy
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core.analysis_context_rules import _rules_from_inputs, linear_stat_rules
from PoE2_Data_Logger.core.atlas_catalog import ATLAS_DIR, catalog
from PoE2_Data_Logger.core import analysis_context_rules


def translation(stat_id="sample", *, template="{0}% increased sample", handlers=(),
                condition=None, number_format="#", hidden=False, ids=None):
    return {"ids": [stat_id] if ids is None else ids, "hidden": hidden,
            "English": [{"condition": [condition if condition is not None else {"min": 1, "max": None}],
                         "format": [number_format], "index_handlers": [list(handlers)],
                         "string": template}]}


class AnalysisContextRuleTests(unittest.TestCase):
    hashes = {"pinned.json": {"sha256": "verified-input-hash"}}

    def rules(self, entries):
        return _rules_from_inputs({"sources": self.hashes, "translations": entries}, self.hashes)

    def actual(self, stat_id, raw):
        return next(rule for rule in linear_stat_rules() if rule["stat_id"] == stat_id
                    and (rule["min_raw"] is None or raw >= rule["min_raw"])
                    and (rule["max_raw"] is None or raw <= rule["max_raw"]))

    def test_pinned_input_identity_and_rule_keys_are_exact_and_stable(self):
        with gzip.open(Path(ATLAS_DIR) / "sources/inputs.json.gz", "rt", encoding="utf-8") as handle:
            inputs = json.load(handle)
        expected = catalog()["provenance"]["input_hashes"]
        self.assertEqual(inputs["sources"], expected)
        rules = linear_stat_rules()
        self.assertGreater(len(rules), 400)
        self.assertEqual(len({rule["rule_key"] for rule in rules}), len(rules))
        self.assertTrue(all(json.loads(rule["source_hashes_json"]) == expected for rule in rules))
        self.assertEqual(rules, linear_stat_rules())
        rules[0]["template"] = "Changed by caller"
        self.assertNotEqual(rules[0], linear_stat_rules()[0])

    def test_actual_rarity_and_effectiveness_keep_their_separate_meanings(self):
        rarity = self.actual("map_item_drop_rarity_+%", 10)
        effectiveness = self.actual("map_monster_potency_+%", 15)
        self.assertEqual(rarity["template"], "{0}% increased Rarity of Items found")
        self.assertEqual(effectiveness["template"], "{0}% increased Effectiveness of Monsters in your Maps")
        self.assertEqual((rarity["divisor"], effectiveness["divisor"]), (1, 1))
        self.assertEqual((rarity["unit"], effectiveness["unit"]), ("%", "%"))

    def test_actual_hundredths_and_permyriad_values_have_verified_percent_scale(self):
        examples = [("map_incursion_room_temple_bonuses_+%_for_1_room_per_type", 100, 1),
                    ("map_monster_dropped_item_rarity_permyriad_if_possessed", 2000, 20)]
        for stat, raw, displayed in examples:
            with self.subTest(stat=stat):
                rule = self.actual(stat, raw)
                self.assertEqual(rule["divisor"], 100)
                self.assertEqual(raw * rule["multiplier"] / rule["divisor"], displayed)
                self.assertEqual(rule["unit"], "%")
        self.assertIn("for each different Room", self.actual(examples[0][0], 100)["template"])
        self.assertIn("Possessed Monsters", self.actual(examples[1][0], 2000)["template"])

    def test_actual_seconds_and_cap_wording_are_preserved(self):
        timer = self.actual("map_azmeri_spirits_spawn_fractured_mirrors_in_fog_while_chased_every_X_ms", 10000)
        self.assertEqual((timer["divisor"], timer["unit"]), (1000, "seconds"))
        self.assertEqual(10000 / timer["divisor"], 10)
        cap = self.actual("map_abyss_monster_potency_+%_per_chasm_closed", 10)
        self.assertIn("for each closed Pit, up to 100%", cap["template"])
        self.assertEqual(cap["divisor"], 1)

    def test_actual_flags_offsets_and_coupled_effects_are_not_linear_rules(self):
        excluded = {"map_strongbox_guarded_by_rare_implicit_%", "map_rare_monster_modifier_min_+",
                    "map_desert_rare_in_rare_pack_chance_converted_to_effectiveness",
                    "map_city_ezomyte_additional_stone_circle_+",
                    "maps_with_powerful_bosses_additional_stone_circle_+",
                    "map_ritual_monster_tankiness_+%_for_each_time_revived",
                    "map_ritual_monster_damage_+%_for_each_time_revived"}
        self.assertFalse(excluded & {rule["stat_id"] for rule in linear_stat_rules()})

    def test_negative_branch_and_first_source_priority_remain_distinct(self):
        positive = translation(condition={"min": 1})
        negative = translation(template="{0}% reduced sample", handlers=("negate",), condition={"max": -1})
        duplicate = translation(template="{0}% different later wording", condition={"min": 1})
        rules = self.rules([positive, negative, duplicate])
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["template"], "{0}% increased sample")
        self.assertEqual((rules[1]["multiplier"], rules[1]["max_raw"]), (-1, -1))
        self.assertEqual(-20 * rules[1]["multiplier"] / rules[1]["divisor"], 20)

    def test_unsupported_handlers_branches_and_placeholders_are_skipped(self):
        invalid = [translation(handlers=("unknown_future_handler",)), translation(handlers=("subtract_one",)),
                   translation(handlers=("divide_by_one_hundred", "divide_by_one_hundred")),
                   translation(condition={"min": 4, "max": 2}), translation(condition={"min": True}),
                   translation(condition={"max": float("inf")}), translation(condition={"negated": True}),
                   translation(condition={"unknown_predicate": 1}), translation(number_format="ignore"),
                   translation(hidden=True), translation(template="20% fixed effect"),
                   translation(template="{0} and {0}"), translation(template="{0} and {1}"),
                   translation(template="{0:.2f}% modified field"), translation(template="{0} {broken"),
                   translation(ids=["one", "two"])]
        self.assertEqual(self.rules(invalid), [])

    def test_markup_count_unit_and_literal_percent_do_not_change_numeric_unit(self):
        rule = self.rules([translation(template="{0} to maximum [MonsterModifiers|Monster Modifiers]\n"
                                              "Each Modifier grants at least 100% increased [Rarity]",
                                       number_format="+#")])[0]
        self.assertEqual(rule["format"], "+#")
        self.assertEqual(rule["unit"], "count")
        self.assertIn("{0} to maximum Monster Modifiers\n", rule["template"])
        self.assertIn("100% increased Rarity", rule["template"])
        self.assertNotIn("[", rule["template"])
        self.assertEqual(self.rules([translation(template="Areas have {0} extra Rooms")])[0]["unit"], "count")
        self.assertEqual(self.rules([translation(template="{0} to Area Level")])[0]["unit"], "catalog display units")

    def test_mismatched_or_missing_source_identity_cannot_supply_display_rules(self):
        inputs = {"sources": copy.deepcopy(self.hashes), "translations": [translation()]}
        self.assertEqual(_rules_from_inputs(inputs, {"different-input": {"sha256": "other"}}), [])
        inputs["sources"] = {}
        self.assertEqual(_rules_from_inputs(inputs, {}), [])

    def test_missing_optional_translation_file_keeps_raw_context_available(self):
        """A missing display sidecar must not prevent normal logging from initializing."""
        analysis_context_rules._bundled_rules.cache_clear()
        try:
            with patch.object(analysis_context_rules.gzip, 'open', side_effect=FileNotFoundError('missing sidecar')), \
                    self.assertLogs(analysis_context_rules.__name__, level='WARNING'):
                self.assertEqual(linear_stat_rules(), [])
        finally:
            analysis_context_rules._bundled_rules.cache_clear()
        self.assertGreater(len(linear_stat_rules()), 400)


if __name__ == "__main__":
    unittest.main()
