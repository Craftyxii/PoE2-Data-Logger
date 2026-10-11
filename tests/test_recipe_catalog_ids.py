"""Recipe IDs link propagation entries to catalogs without changing their logging workflow."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store


class RecipeCatalogIdTests(unittest.TestCase):
    """Exercise identity stability, upgrade safety and reference-pack compatibility."""

    def setUp(self):
        """Create an isolated profile with one custom recipe and an active map."""
        self.temp = tempfile.TemporaryDirectory(prefix="poe2-recipe-ids-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.recipe = {"name": "Identity test reward", "sockets": 3, "combo": "Death + Power + Time"}
        self.saved = logger.save_recipe(self.recipe)
        logger.save_family({"family": 9999, "top_socket": 3, "recipes": [self.recipe["name"]]})

    def tearDown(self):
        """Restore the incoming profile and release all temporary catalog data."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temp.cleanup()

    def approve(self):
        """Save one explicitly reviewed propagated rune with the existing family ID."""
        return logger.accept_propagation_part(logger.scan_context(), runes=["Death"],
            recipe=self.recipe["name"], family=9999, request_id="identity-approval")

    def manifest(self):
        """Read the exported reference manifest containing recipe identity links."""
        with ZipFile(io.BytesIO(reference_pack.export_pack())) as pack:
            return json.loads(pack.read("manifest.json"))

    def pack(self, recipes):
        """Create a minimal compatible reference archive for import validation."""
        data = {key: [] for key in ("families", "recipes", "aliases", "seed_states", "affixes",
                "master_perks", "currency_names", "omen_names", "item_names", "glyphs", "icons", "scans")}
        data["recipes"] = recipes
        path = Path(self.temp.name) / "references.zip"
        with ZipFile(path, "w") as pack:
            pack.writestr("manifest.json", json.dumps({"format": reference_pack.FORMAT, "version": 2, "data": data}))
        return path

    def test_every_catalog_recipe_has_a_unique_id_preserved_by_edits_and_restart(self):
        """Assign every current recipe an ID while combo edits and case normalization retain identity."""
        with logger._connect() as db:
            recipes = db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
            linked = db.execute("SELECT COUNT(*),COUNT(DISTINCT recipe_id) FROM recipes "
                                "JOIN recipe_ids ON recipe_name=name").fetchone()
        self.assertEqual(tuple(linked), (recipes, recipes))
        revised = logger.save_recipe({**self.recipe, "name": self.recipe["name"].upper(),
                                      "combo": "Power + Death + Time"})
        self.assertEqual(revised["recipe_id"], self.saved["recipe_id"])
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT recipe_id FROM recipe_ids WHERE recipe_name=?",
                                       (self.recipe["name"],)).fetchone()[0], self.saved["recipe_id"])

    def test_approval_adds_id_without_full_combo_or_remnant_mutations(self):
        """Keep pending remnant ownership and existing counts while recording only the recipe link."""
        pending = logger.assign_ocr_id("opened")
        result = self.approve()
        state = logger.get_state()
        origin = result["provenance"][0]
        self.assertEqual((origin["recipe_id"], origin["family"]), (self.saved["recipe_id"], 9999))
        self.assertEqual(state["ocr_pending"], pending)
        self.assertEqual(state["chain"], [{"step": 1, "rune1": "Death", "rune2": ""}])
        self.assertEqual((state["detonated"], state["scan_commit_count"]), (1, 2))
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM new_export WHERE remnant_id IS NOT NULL").fetchone()[0], 0)
            linked = db.execute("SELECT r.combo,f.id FROM chain_step_provenance p "
                                "JOIN recipe_ids i ON i.recipe_id=p.recipe_id "
                                "JOIN recipes r ON r.name=i.recipe_name "
                                "JOIN families f ON f.id=p.family_id").fetchone()
            self.assertEqual(tuple(linked), (self.recipe["combo"], 9999))
            details = [json.loads(row[0]) for row in db.execute("SELECT details_json FROM commits")]
        self.assertTrue(all("recipe_runes" not in item and "combo" not in item for item in details))
        self.assertEqual(self.approve()["provenance"], [origin])
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)

    def test_reference_pack_ids_round_trip_and_old_packs_remain_supported(self):
        """Share stable IDs, import legacy names and reject mismatched identities without partial writes."""
        row = next(row for row in self.manifest()["data"]["recipes"] if row["name"] == self.recipe["name"])
        self.assertEqual(row["recipe_id"], self.saved["recipe_id"])
        reference_pack.import_pack(self.pack([row]), replace_existing=True)
        old = {"name": "Legacy identity reward", "sockets": 2, "combo": "Death + Time"}
        reference_pack.import_pack(self.pack([old]))
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT recipe_id FROM recipe_ids WHERE recipe_name=?",
                                       (old["name"],)).fetchone()[0], logger._recipe_identifier(old["name"]))
        good = {**old, "name": "Rollback identity reward"}
        with self.assertRaisesRegex(ValueError, "recipe ID"):
            reference_pack.import_pack(self.pack([good, {**row, "recipe_id": "RCP-invalid"}]), replace_existing=True)
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM recipes WHERE name=?", (good["name"],)).fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM recipe_ids WHERE recipe_name=?", (good["name"],)).fetchone())

    def test_old_provenance_schema_upgrades_without_changing_saved_steps(self):
        """Add IDs to explicit old origins without replaying approval or guessing unidentified manual entries."""
        self.approve()
        logger.commit_chain("Time")
        before = logger.get_state()
        with logger._connect() as db:
            db.execute("DROP VIEW chain_rune_provenance")
            db.execute("ALTER TABLE chain_step_provenance DROP COLUMN recipe_id")
        logger._READY = False
        logger.initialize()
        after = logger.get_state()
        for key in ("chain", "detonated", "scan_commit_count", "ocr_pending"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after["chain_provenance"][0]["recipe_id"], self.saved["recipe_id"])
        self.assertIsNone(after["chain_provenance"][1]["recipe_id"])
        with logger._connect() as db:
            rows = list(db.execute("SELECT recipe_id FROM chain_rune_provenance ORDER BY chain_step"))
        self.assertEqual([row[0] for row in rows], [self.saved["recipe_id"], None])

    def test_reference_reset_preserves_logged_identity_and_baseline_ids(self):
        """Reset active references while retaining logged custom links and stable restored default identities."""
        with logger._connect() as db:
            baseline = dict(db.execute("SELECT recipe_name,recipe_id FROM recipe_ids WHERE recipe_name<>?",
                                       (self.recipe["name"],)))
        self.approve()
        reference_pack.reset_to_defaults()
        with logger._connect() as db:
            restored = dict(db.execute("SELECT name,recipe_id FROM recipes JOIN recipe_ids ON recipe_name=name"))
            self.assertIsNone(db.execute("SELECT 1 FROM recipes WHERE name=?", (self.recipe["name"],)).fetchone())
        self.assertEqual(restored, baseline)
        self.assertEqual(logger.get_state()["chain_provenance"][0]["recipe_id"], self.saved["recipe_id"])


if __name__ == "__main__":
    unittest.main()
