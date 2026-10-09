"""Verify the distinct Farrul Hunt rune without weakening family matching."""
import csv
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import auto_commit, catalog_repairs, logger_store as logger, store
from PoE2_Data_Logger.ocr import opened_scan


GRACE = catalog_repairs.FARRUL_GRACE
HUNT = catalog_repairs.FARRUL_HUNT
GENERIC = catalog_repairs.GENERIC_HUNT


class FarrulFamilyRepairTests(unittest.TestCase):
    """Check Farrul recipe repair while preserving custom references and logged history."""
    def setUp(self):
        """Initialize a temporary logger database with one active map."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-farrul-repair-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Restore the data directory and remove the temporary logger data."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def legacy_defaults(self):
        """Seed the obsolete generic Hunt reward and four-socket Farrul stage."""
        with logger._connect() as db:
            db.execute("UPDATE families SET recipes_json=? WHERE id=47", (logger._dump([GRACE, GENERIC]),))
            db.execute("UPDATE seed_states SET rewards_json=? WHERE family=47 AND sockets=5",
                       (logger._dump([GRACE, GENERIC]),))
            db.execute("INSERT OR REPLACE INTO seed_states VALUES(47,4,'P2','Unresolved',?,'calculator')",
                       (logger._dump([GENERIC]),))

    def reference_rows(self):
        """Read the Farrul family and ordered seed-state rows for comparison."""
        with logger._connect() as db:
            family = dict(db.execute("SELECT * FROM families WHERE id=47").fetchone())
            seeds = [dict(row) for row in db.execute("SELECT * FROM seed_states WHERE family=47 ORDER BY sockets")]
        return family, seeds

    def restart(self):
        """Reinitialize the logger to run reference repairs."""
        logger._READY = False
        logger.initialize()

    def test_bundled_sources_use_the_named_five_socket_hunt_and_no_four_socket_stage(self):
        """Verify bundled Farrul data uses its named Hunt reward only at five sockets."""
        base = Path(logger.__file__).resolve().parent.parent
        with gzip.open(base / "bootstrap.json.gz", "rt", encoding="utf-8") as handle:
            bootstrap = json.load(handle)
        family = next(row for row in bootstrap["families"] if row["id"] == 47)
        self.assertEqual(family["recipes"], [GRACE, HUNT])
        generic_family = next(row for row in bootstrap["families"] if row["id"] == 61)
        self.assertIn(GENERIC, generic_family["recipes"])
        catalog = json.loads((base / "catalog.json").read_text(encoding="utf-8"))
        stages = [row for row in catalog["states"] if row["family"] == 47]
        self.assertEqual([(stage["sockets"], stage["rewards"]) for stage in stages], [(5, [GRACE, HUNT])])

    def test_fresh_database_resolves_both_ascii_and_curly_apostrophes_in_exact_order(self):
        """Verify fresh Farrul resolution normalizes apostrophes and preserves reward order."""
        for first, second in ((GRACE, HUNT), (GRACE.replace("'", "’"), HUNT.replace("'", "’"))):
            with self.subTest(first=first):
                result = logger.resolve(first, second, 47)
                self.assertEqual((result["status"], result["family"]), ("ready", 47))
                self.assertEqual([row["recipe"] for row in result["rows"]], [GRACE, HUNT])
                self.assertEqual([row["sockets"] for row in result["rows"]], [5, 5])
        family, seeds = self.reference_rows()
        self.assertEqual(json.loads(family["recipes_json"]), [GRACE, HUNT])
        self.assertEqual([seed["sockets"] for seed in seeds], [5])

    def test_real_family_mismatches_still_fail_instead_of_accepting_the_generic_hunt(self):
        """Verify repair does not accept generic Hunt or reversed Farrul rewards."""
        for first, second in ((GRACE, GENERIC), (HUNT, GRACE)):
            with self.subTest(first=first, second=second):
                self.assertEqual(logger.resolve(first, second, 47)["status"], "no matching family")
        self.assertEqual(logger.resolve(GENERIC, family=61)["status"], "ready")

    def test_opened_family_and_auto_commit_checks_accept_only_the_correct_two_rewards(self):
        """Verify opened recognition and automatic commit require the correct Farrul pair."""
        lines = [{"recipe": name, "raw": "1x " + name, "ocr_score": .99, "match_score": 1}
                 for name in (GRACE, HUNT)]
        with logger._connect() as db:
            self.assertEqual(opened_scan._families(db, lines, list_complete=True), (47, [47], True))
        opened = {"family": "Family 47", "candidates": [47],
                  "status": "Review the opened rewards before logging.", "can_use": True,
                  "sockets": 5, "recipe_sockets": 5, "socket_source": "opened icons", "first_line_gap": 60,
                  "opened_recipes": lines, "first_recipe": GRACE, "next_recipe": HUNT}
        self.assertTrue(auto_commit.candidate(opened)["ready"])
        wrong = {**opened, "next_recipe": GENERIC,
                 "opened_recipes": [lines[0], {**lines[1], "recipe": GENERIC}]}
        self.assertFalse(auto_commit.candidate(wrong)["ready"])

    def test_existing_default_database_repairs_references_but_preserves_history_and_samples(self):
        """Verify legacy repair changes references without altering scans, maps or commits."""
        logger.commit_remnant(GRACE, HUNT, 47)
        self.legacy_defaults()
        pixels = io.BytesIO()
        Image.new("RGB", (30, 30), (10, 20, 30)).save(pixels, format="PNG")
        with patch.object(store, "_reviewed_vector", return_value=None):
            store.save_scan(pixels.getvalue(), "old-remnant.png", 5, "P2", "Unresolved", 47)
        tables = ("scans", "new_export", "commits", "maps")
        with logger._connect() as db:
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                      for table in tables}
        self.restart()
        family, seeds = self.reference_rows()
        self.assertEqual(json.loads(family["recipes_json"]), [GRACE, HUNT])
        self.assertEqual([(seed["sockets"], json.loads(seed["rewards_json"])) for seed in seeds], [(5, [GRACE, HUNT])])
        with logger._connect() as db:
            after = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                     for table in tables}
        self.assertEqual(after, before)
        self.assertEqual(logger.resolve(GRACE, HUNT, 47)["status"], "ready")
        export = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        self.assertEqual([row["Matched Recipe"] for row in export if row["Type"] == "Remnant"], [GRACE, HUNT])
        once = self.reference_rows()
        self.restart()
        self.assertEqual(self.reference_rows(), once)

    def test_custom_family_edits_or_recipe_signatures_are_left_untouched(self):
        """Verify customized family metadata or recipe signatures prevent default repair."""
        for changed in ("family", "top_socket", "disabled", "recipe"):
            with self.subTest(changed=changed):
                with logger._connect() as db:
                    db.execute("UPDATE families SET top_socket=5,valid=1,recipes_json=? WHERE id=47",
                               (logger._dump([GRACE, GENERIC]),))
                    db.execute("UPDATE recipes SET sockets=5,combo=? WHERE name=?",
                               ("Vision + Bloodletting + Bond + Time + Rage", HUNT))
                    if changed == "family":
                        db.execute("UPDATE families SET recipes_json=? WHERE id=47", (logger._dump([GRACE]),))
                    elif changed == "top_socket":
                        db.execute("UPDATE families SET top_socket=6 WHERE id=47")
                    elif changed == "disabled":
                        db.execute("UPDATE families SET valid=0 WHERE id=47")
                    else:
                        db.execute("UPDATE recipes SET combo='Power + Power + Power + Power + Power' WHERE name=?", (HUNT,))
                before = self.reference_rows()
                self.restart()
                self.assertEqual(self.reference_rows(), before)

    def test_custom_seed_reviews_survive_repair_and_already_correct_family_is_idempotent(self):
        """Verify reviewed seeds survive repair and repeated initialization is idempotent."""
        self.legacy_defaults()
        with logger._connect() as db:
            db.execute("UPDATE seed_states SET seed_rune='Power',status='reviewed' WHERE family=47")
        original_seeds = self.reference_rows()[1]
        self.restart()
        family, seeds = self.reference_rows()
        self.assertEqual(json.loads(family["recipes_json"]), [GRACE, HUNT])
        self.assertEqual(seeds, original_seeds)
        original = self.reference_rows()
        self.restart()
        self.assertEqual(self.reference_rows(), original)


if __name__ == "__main__":
    unittest.main()
