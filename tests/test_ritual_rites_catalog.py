"""Source coverage and identity safety for the offline Rite modifier choices."""

from collections import Counter
from hashlib import sha256
import unittest

from PoE2_Data_Logger.core import ritual_rites_catalog as rites


PRIMARY_SOURCE = "https://poe2db.tw/us/Rite_of_the_Nameless"
PROLIFERATION_SOURCE = "https://www.vaal.tools/mechanics/foretold_proliferation/"


class RitualRitesCatalogTests(unittest.TestCase):
    def test_complete_published_table_with_traceable_internal_ids(self):
        data = rites.catalog()
        rows = data["affixes"]
        self.assertEqual(len(rows), 73)
        self.assertEqual(Counter(row["kind"] for row in rows),
                         {"bounty": 60, "proliferation": 6, "local": 7})
        self.assertEqual(len({row["id"] for row in rows}), 73)
        self.assertEqual([row["provenance"]["source_line"] for row in rows],
                         list(range(66, 139)))
        self.assertEqual(data["source_verified"], "2026-10-19")
        for row in rows:
            source = row["provenance"]
            digest = sha256(source["source_raw_description"].encode()).hexdigest()[:24]
            self.assertEqual(row["id"], "rite:" + row["kind"] + ":" + digest)
            self.assertTrue(row["stat_id"].startswith("logger:rite:"))
            self.assertIn(PRIMARY_SOURCE, source["source_urls"])
            self.assertEqual(source["source_verified"], "2026-10-19")
            self.assertFalse(row["custom"])
            self.assertNotIn("weight", row)
            self.assertNotIn("density", row)
        self.assertIn("not game IDs", data["id_namespace_notice"])

    def test_every_published_bounty_item_and_currency_amount_is_present(self):
        quantities = {
            "Exalted Orbs": (2, 4, 8), "Divine Orbs": (1, 2, 5),
            "Chaos Orbs": (2, 4, 8), "Greater Orbs of Augmentation": (2, 4, 8),
            "Greater Chaos Orbs": (1, 2, 5), "Greater Exalted Orbs": (1, 2, 5),
            "Greater Regal Orbs": (1, 2, 5),
            "Greater Orbs of Transmutation": (2, 4, 8),
            "Orbs of Annulment": (1, 2), "Orbs of Chance": (1, 2),
            "Perfect Orbs of Augmentation": (1, 2), "Perfect Chaos Orbs": (1,),
            "Perfect Exalted Orbs": (1,), "Perfect Regal Orbs": (1,),
            "Perfect Orbs of Transmutation": (1, 2),
        }
        items = {
            "Alpha's Howl", "Astramentis", "Defiance of Destiny", "Dream Fragments",
            "Headhunter", "Kalandra's Touch", "Mageblood", "Original Sin",
            "Queen of the Forest", "Yoke of Suffering",
        }
        omens = {
            "Amelioration", "the Blessed", "Chance", "Corruption", "Dextral Annulment",
            "Dextral Crystallisation", "Dextral Erasure", "Dextral Exaltation",
            "Greater Annulment", "Sanctification", "Sinistral Annulment",
            "Sinistral Crystallisation", "Sinistral Erasure", "Sinistral Exaltation",
            "Whittling",
        }
        expected = {(amount, item) for item, amounts in quantities.items()
                    for amount in amounts}
        expected.update((1, item) for item in items)
        expected.update((1, "Omen of " + name) for name in omens)
        bounty = [row for row in rites.affixes() if row["kind"] == "bounty"]
        self.assertEqual({(row["value"], row["unit"]) for row in bounty}, expected)
        self.assertEqual(len(expected), 60)
        self.assertTrue(all(not row["inherited_text"] for row in bounty))

    def test_six_proliferation_templates_have_exact_values_and_effect_text(self):
        expected = {
            "additional_wildwood_packs": (1, "packs", "Wildwood Monsters"),
            "additional_rerolls": (1, "rerolls", "1 additional time"),
            "first_free_rerolls": (1, "free_rerolls", "no Cost the first time"),
            "tribute_increased": (25, "percent_increased", "25% increased Tribute"),
            "reroll_cost_reduced": (20, "percent_reduced", "20% reduced Tribute"),
            "favour_count_increased": (20, "percent_increased", "20% increased number of Favours"),
        }
        rows = [row for row in rites.affixes() if row["kind"] == "proliferation"]
        self.assertEqual(len(rows), len(expected))
        for row in rows:
            key = row["stat_id"].removeprefix("logger:rite:")
            amount, unit, phrase = expected[key]
            self.assertEqual((row["value"], row["unit"]), (amount, unit))
            self.assertIn(phrase, row["inherited_text"])
            self.assertNotIn("Foretold Proliferation:", row["inherited_text"])
            self.assertIn(PROLIFERATION_SOURCE, row["provenance"]["source_urls"])
        self.assertFalse(any("convert" in row["text"].lower() for row in rites.affixes()))

    def test_untranslated_raw_rows_remain_honest_and_do_not_gain_inheritance(self):
        raw = [row for row in rites.affixes()
               if row["translation_status"] == "raw_untranslated"]
        self.assertEqual({row["text"] for row in raw}, {
            "map ritual additional wildwood packs [1]",
            "map ritual contains very rare unique [1]",
            "map ritual offered rewards amount +% [20]",
        })
        for row in raw:
            self.assertEqual(row["kind"], "local")
            self.assertEqual(row["inherited_text"], "")
            self.assertTrue(row["stat_id"].startswith("logger:rite:raw:"))
            self.assertTrue(row["provenance"]["source_raw_stat"])
            self.assertEqual(row["text"], row["provenance"]["source_raw_description"])

    def test_catalog_lookups_and_normalization_do_not_mutate_bundled_data(self):
        original = rites.catalog()
        copied = rites.catalog()
        copied["affixes"][0]["provenance"]["source_urls"].clear()
        copied["sources"][0]["url"] = "changed"
        rows = rites.affixes()
        rows[0]["text"] = "changed"
        selected = rites.affix(original["affixes"][0]["id"])
        selected["provenance"]["source_urls"].clear()
        normalized = rites.normalize_affixes([original["affixes"][0]["id"]])
        normalized[0]["provenance"]["source_urls"].clear()
        self.assertEqual(rites.catalog(), original)
        self.assertIsNone(rites.affix("missing"))

    def test_known_choices_canonicalize_metadata_but_keep_occurrence_counts(self):
        choice = next(row for row in rites.affixes() if row["kind"] == "local")
        rows = rites.normalize_affixes([
            {"id": choice["id"], "count": 3, "text": "tampered",
             "kind": "proliferation", "value": 999},
            choice["id"],
        ])
        self.assertEqual(rows[0], {**choice, "count": 3})
        self.assertEqual(rows[1], {**choice, "count": 1})
        self.assertEqual(rites.normalize_affixes(rows), rows)
        self.assertEqual(rites.normalize_affixes(None), [])

    def test_typed_future_choices_are_unverified_without_inferred_numeric_effects(self):
        values = [
            {"text": "Foretold Proliferation: A future Ritual effect", "kind": "proliferation", "count": 2},
            {"description": "A future local modifier", "kind": "local"},
            {"text": "Foretold Bounty: A future item", "kind": "bounty", "inherited_text": "ignored"},
        ]
        rows = rites.normalize_affixes(values)
        self.assertEqual(rows[0]["inherited_text"], "A future Ritual effect")
        self.assertEqual(rows[0]["count"], 2)
        for row in rows:
            self.assertTrue(row["id"].startswith("custom:rite:"))
            self.assertTrue(row["custom"])
            self.assertIsNone(row["value"])
            self.assertIsNone(row["stat_id"])
            self.assertEqual(row["provenance"]["source"], "user_entered")
            self.assertEqual(row["provenance"]["source_urls"], [])
            self.assertIsNone(row["provenance"]["source_verified"])
        self.assertFalse(rows[1]["inherited_text"])
        self.assertFalse(rows[2]["inherited_text"])
        self.assertEqual(rites.normalize_affixes(rows), rows)

    def test_invalid_selection_shapes_and_counts_are_rejected(self):
        choice = rites.affixes()[0]["id"]
        invalid = [
            "not-a-list", [5], [None], [{"id": "rite:missing"}],
            [{"id": []}], [{"id": choice, "count": 0}],
            [{"id": choice, "count": -1}], [{"id": choice, "count": True}],
            [{"id": choice, "count": 1.5}], [{"id": choice, "count": "1.0"}],
            [{"text": ""}], [{"text": False}], [{"text": "effect", "kind": []}],
            [{"text": "effect", "kind": "ordinary"}],
            [{"text": "effect", "inherited_text": 2}],
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                rites.normalize_affixes(values)


if __name__ == "__main__":
    unittest.main()
