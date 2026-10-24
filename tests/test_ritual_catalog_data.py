"""Validate the offline Ritual eligibility evidence without altering scanner behavior."""

import hashlib
import json
from pathlib import Path
from runpy import run_path
import unittest
from urllib.parse import urlparse


PROJECT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT / "PoE2_Data_Logger" / "ritual_item_assets"
ARTWORK = PROJECT / "PoE2_Data_Logger" / "unique_item_assets" / "manifest.json"
ARTWORK_SHA256 = "4e71e5f8b74218dfcceab69592ffd7cceb3081fc4665b5d1bc7c7219f6de8a7e"
ABYSS_OMENS = {
    "Omen of Abyssal Echoes", "Omen of the Sovereign", "Omen of the Liege",
    "Omen of the Blackblooded", "Omen of Putrefaction", "Omen of Light",
    "Omen of Sinistral Necromancy", "Omen of Dextral Necromancy",
}


class RitualCatalogDataTests(unittest.TestCase):
    """Catch incomplete upstream payloads, unsourced exclusions and packaging omissions."""

    @classmethod
    def setUpClass(cls):
        """Read the same bundled evidence a future offline Ritual reader will consume."""
        cls.catalog = json.loads((ASSETS / "catalog.json").read_bytes())
        cls.provenance = json.loads((ASSETS / "provenance.json").read_bytes())
        cls.omens = json.loads((ASSETS / "repoe-omens.json").read_bytes())
        cls.uniques = json.loads((ASSETS / "exiledb-uniques.json").read_bytes())
        cls.artwork = json.loads(ARTWORK.read_bytes())

    def test_all_source_snapshots_are_complete_offline_and_hash_verified(self):
        """A provenance record must resolve to the entire named payload shipped in the package."""
        self.assertEqual(self.catalog["schema_version"], 1)
        self.assertEqual(self.catalog["scope"], "ritual_reward_identity")
        self.assertEqual(self.catalog["default_status"], "unknown")
        snapshots = self.catalog["source_snapshots"]
        self.assertTrue(snapshots)
        snapshot_paths = set()
        for snapshot in snapshots.values():
            with self.subTest(path=snapshot["path"]):
                relative = Path(snapshot["path"])
                self.assertFalse(relative.is_absolute())
                self.assertNotIn("..", relative.parts)
                raw = (ASSETS / relative).read_bytes()
                self.assertEqual(len(raw), snapshot["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), snapshot["sha256"])
                self.assertEqual(urlparse(snapshot["url"]).scheme, "https")
                self.assertTrue(urlparse(snapshot["url"]).netloc)
                snapshot_paths.add(relative.as_posix())
        self.assertTrue({"exiledb-uniques.json", "repoe-omens.json",
                         "repoe-version.txt", "repoe-exported-version.txt"} <= snapshot_paths)
        provenance_snapshots = {row["path"]: row for row in self.provenance["snapshots"]}
        self.assertEqual(set(provenance_snapshots), snapshot_paths)
        for snapshot in snapshots.values():
            self.assertEqual({key: provenance_snapshots[snapshot["path"]][key]
                              for key in ("path", "url", "sha256", "bytes")},
                             {key: snapshot[key] for key in ("path", "url", "sha256", "bytes")})
        self.assertEqual(self.provenance["catalog"]["path"], "catalog.json")
        self.assertEqual(self.provenance["catalog"]["sha256"],
                         hashlib.sha256((ASSETS / "catalog.json").read_bytes()).hexdigest())

    def test_every_omen_has_stable_identity_and_original_artwork_metadata(self):
        """Retain all fifty identities, including negative references and non-Ritual Sagas."""
        self.assertEqual(len(self.omens), 50)
        names = set()
        for metadata_id, row in self.omens.items():
            with self.subTest(metadata_id=metadata_id):
                self.assertTrue(metadata_id.startswith("Metadata/Items/"))
                self.assertEqual(row["item_class"], "Omen")
                self.assertEqual((row["inventory_width"], row["inventory_height"]), (1, 1))
                self.assertIsInstance(row["drop_level"], int)
                self.assertTrue(row["visual_identity"]["dds_file"].endswith(".dds"))
                self.assertTrue(row["name"])
                self.assertNotIn(row["name"], names)
                names.add(row["name"])
        self.assertTrue(ABYSS_OMENS <= names)
        self.assertTrue({"Aldur's Saga", "Medved's Saga", "Vorana's Saga",
                         "Uhtred's Saga", "Olroth's Saga", "Omen of Recombination"} <= names)
        self.assertEqual((ASSETS / "repoe-version.txt").read_text().strip(), "0.5.5.4.2")
        self.assertEqual((ASSETS / "repoe-exported-version.txt").read_text().strip(), "0.5.5.4.2")

    def test_unique_snapshot_keeps_full_acquisition_and_legacy_records(self):
        """Retain all upstream identities and uncertain sources, rather than only matched exclusions."""
        self.assertEqual((self.uniques["patch"], self.uniques["build"]), ("0.5.5", "0.5.5.4.2"))
        records = self.uniques["records"]
        self.assertEqual(self.uniques["count"], 446)
        self.assertEqual(len(records), self.uniques["count"])
        self.assertEqual(len({row["name"] for row in records}), 446)
        required = {"name", "base", "slot", "level", "implicits", "explicits", "choices",
                    "history", "legacy", "source", "league", "page"}
        for row in records:
            with self.subTest(name=row["name"]):
                self.assertTrue(required <= row.keys())
                self.assertTrue(row["name"])
                self.assertTrue(row["slot"])
                self.assertIs(type(row["legacy"]), bool)
                for key in ("implicits", "choices", "history"):
                    self.assertIsInstance(row[key], list)
                self.assertEqual(urlparse(row["page"]).netloc, "exiledb.com")
        self.assertTrue(any(row["source"] for row in records))
        self.assertTrue(any(row["source"] is None for row in records))
        self.assertTrue(any(row["legacy"] for row in records))

    def test_exclusions_have_independent_evidence_and_resolve_to_bundled_identities(self):
        """A forbidden name needs acquisition evidence and an exact catalog identity."""
        exclusions = self.catalog["exclusions"]
        excluded_names = {row["name"] for row in exclusions}
        self.assertEqual(len(excluded_names), len(exclusions))
        self.assertTrue(ABYSS_OMENS | {"Omen of Recombination"} <= excluded_names)
        omen_names = {row["name"]: metadata_id for metadata_id, row in self.omens.items()}
        manifest = {row["id"]: row for row in self.artwork["items"]}
        source_uniques = {row["name"]: row for row in self.uniques["records"]}
        expected_uniques = {name for name, row in source_uniques.items()
                            if row["source"] or row["legacy"]}
        unique_exclusions = {row["name"] for row in exclusions if row["item_kind"] == "unique"}
        self.assertEqual(len(expected_uniques), 57)
        self.assertEqual(unique_exclusions, expected_uniques)
        for row in exclusions:
            with self.subTest(name=row["name"]):
                self.assertTrue(row["reason"])
                self.assertTrue(row["evidence_urls"])
                self.assertIn(row["source_snapshot"], self.catalog["source_snapshots"])
                for url in row["evidence_urls"]:
                    self.assertEqual(urlparse(url).scheme, "https")
                    self.assertTrue(urlparse(url).netloc)
                if row["item_kind"] == "omen":
                    self.assertTrue(row["acquisition"])
                    self.assertEqual(row["metadata_id"], omen_names[row["name"]])
                else:
                    self.assertEqual(row["item_kind"], "unique")
                    source = source_uniques[row["name"]]
                    self.assertEqual(row["acquisition"], source["source"])
                    self.assertEqual(row["legacy"], source["legacy"])
                    self.assertTrue(row["acquisition"] or
                                    (row["legacy"] is True and row["reason"] == "legacy_only"))
                    self.assertTrue(row["manifest_ids"])
                    for manifest_id in row["manifest_ids"]:
                        self.assertEqual(manifest[manifest_id]["name"], row["name"])

    def test_missing_acquisition_sources_remain_unknown(self):
        """Absence of a source annotation cannot silently exclude or certify an item."""
        unknown = {row["name"]: row for row in self.catalog["unresolved"]}
        excluded = {row["name"] for row in self.catalog["exclusions"]}
        records = {row["name"]: row for row in self.uniques["records"]}
        for name in ("Grip of Kulemak", "Heart of the Well", "Loreweave"):
            with self.subTest(name=name):
                self.assertIsNone(records[name]["source"])
                self.assertFalse(records[name]["legacy"])
                self.assertNotIn(name, excluded)
                self.assertEqual(unknown[name]["status"], "unknown")

    def test_guaranteed_rite_rewards_are_not_contradicted_by_exclusions(self):
        """Guaranteed special rewards remain represented independently of the normal reward pool."""
        guaranteed = self.catalog["guaranteed_rewards"]
        names = {row["name"] for row in guaranteed}
        self.assertEqual(len(names), len(guaranteed))
        self.assertFalse(names & {row["name"] for row in self.catalog["exclusions"]})
        self.assertTrue({"Headhunter", "Mageblood", "Omen of Whittling"} <= names)
        for row in guaranteed:
            with self.subTest(name=row["name"]):
                self.assertEqual(row["kind"], "foretold_bounty")
                self.assertTrue(row["bounty_ids"])
                self.assertTrue(row["evidence_urls"])
                self.assertIn(row["source_snapshot"], self.catalog["source_snapshots"])

    def test_original_artwork_catalog_remains_intact_and_new_data_is_packaged(self):
        """Eligibility evidence must not replace the original art or disappear from frozen builds."""
        self.assertEqual(hashlib.sha256(ARTWORK.read_bytes()).hexdigest(), ARTWORK_SHA256)
        self.assertEqual(len(self.artwork["items"]), 449)
        self.assertEqual(len({row["image_sha256"] for row in self.artwork["items"]}), 446)
        sources = run_path(str(PROJECT / "packaging" / "sources.py"))
        self.assertIn("ritual_item_assets", sources["DIRECTORY_RESOURCES"])
        for required in ("catalog.json", "provenance.json", "README.md",
                         "exiledb-uniques.json", "repoe-omens.json"):
            self.assertTrue((ASSETS / required).is_file())


if __name__ == "__main__":
    unittest.main()
