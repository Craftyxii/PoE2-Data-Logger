"""Validate the optional Family 19 pack with real import, resolution and Undo paths.

Reward detections are injected text, not a captured-image OCR accuracy measure.
"""

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store
from PoE2_Data_Logger.ocr import propagation_scan


UPDATE = Path(__file__).resolve().parents[1] / "docs/reference_updates/family19-chaos-x10"
PACK = UPDATE / "Family19_ChaosOrb_x10_reference_proposal.zip"
NAMES = ["Cadigan's Epiphany", "Astrid's Creativity", "Chaos Orb x10"]
OLD_NAMES = [*NAMES[:2], "Aldur's Saga"]
COMBO = "Wisdom + Vision + Rebirth + Rage + Power + Arcane + Adaptive"


def manifest():
    """Read the shipped human-reviewable proposal independently of the ZIP."""
    return json.loads((UPDATE / "manifest.json").read_text(encoding="utf-8"))


def write_pack(path, value):
    """Build a deliberately invalid test pack for real transactional rollback checks."""
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(value, ensure_ascii=False))


def restore_legacy_family19():
    """Retain the optional packet's documented .18 baseline after shipped defaults advance."""
    with logger._connect() as db:
        db.execute("UPDATE families SET recipes_json=? WHERE id=19", (logger._dump(OLD_NAMES),))
        db.execute("UPDATE seed_states SET rewards_json=?,status='calculator' WHERE family=19 AND sockets=7",
                   (logger._dump(list(reversed(OLD_NAMES))),))
        db.execute("DELETE FROM recipes WHERE name=?", (NAMES[-1],))
        db.execute("DELETE FROM recipe_ids WHERE recipe_name=?", (NAMES[-1],))


class Family19ReferenceUpdateTests(unittest.TestCase):
    """Validate controlled reference changes without using a personal profile."""

    def setUp(self):
        """Initialize a fresh baseline plus unrelated local references and recorded old reward activity."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-family19-proposal-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        restore_legacy_family19()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.save_recipe({"name": "Unrelated local reward", "sockets": 3,
                            "combo": "Power + Death + Time"})
        logger.save_family({"family": 9999, "top_socket": 3, "recipes": ["Unrelated local reward"]})
        logger.save_seed_state({"family": 9999, "sockets": 3, "seed_slot": "P1", "seed_rune": "Power",
                                "rewards": ["Unrelated local reward"]})
        logger.commit_remnant("Aldur's Saga", family=19)
        logger.accept_propagation_part(logger.scan_context(), runes=["Wisdom"],
            recipe="Aldur's Saga", family=19, request_id="old-family19-approval")
        image = io.BytesIO()
        Image.new("RGB", (200, 100), (170, 140, 80)).save(image, format="PNG")
        store.save_scan(image.getvalue(), "historical-retention-fixture.png", 7, "P1", "Wisdom", 19)

    def tearDown(self):
        """Release all temporary database and captured-file state."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def rows(self, table, *, by_key=False):
        """Read exact values ordered by recorded rows, or catalogs' declared primary keys.

        The existing Undo journal reinserts composite-key catalog records, so a
        physical SQLite rowid is not a stable catalog identity. Public seed
        scans and reference exports order these rows by family/socket instead.
        """
        with logger._connect() as db:
            order = "rowid"
            if by_key:
                columns = sorted((row["pk"], row["name"]) for row in db.execute(f"PRAGMA table_info({table})")
                                 if row["pk"])
                order = ",".join('"' + name + '"' for _, name in columns)
            return [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY {order}")]

    def catalog(self):
        """Read reference before-images excluding the deliberately persistent ID registry."""
        return {table: self.rows(table, by_key=True) for table in ("recipes", "families", "seed_states", "aliases",
            "alias_keys", "alias_conflicts", "currency_items", "item_names", "ritual_names",
            "reviewed_glyphs", "currency_icons", "item_icons", "omen_icons", "review_icon_examples")}

    def historical(self):
        """Read activity, snapshots, frozen attribution, and screenshot bytes exactly."""
        tables = ("legacy_export", "new_export", "maps", "map_unique_kills", "expeditions",
            "chain_completions", "chain_step_provenance", "scan_links", "commits",
            "currency_snapshots", "ritual_pages", "scans")
        return {"tables": {table: self.rows(table) for table in tables},
            "export_csv": logger.export_csv(), "history_csv": logger.export_record_history_csv(),
            "captures": {path.name: path.read_bytes() for path in (store.DATA_DIR / "images").iterdir()}}

    def family19(self):
        """Read the active Family 19 sequence from the real catalog search path."""
        return next(row for row in logger.search_catalog("", "families", 500) if row["family"] == 19)["recipes"]

    def apply(self):
        """Use the existing public importer with the same replacement flag as its checkbox."""
        return reference_pack.import_pack(PACK, replace_existing=True)

    def injected_reward_rows(self):
        """Pass exact supplied reward text through real matching, without mocking its lookup."""
        detections = [{"text": text, "x1": 150, "y1": 150 + 80 * index, "score": .99}
            for index, text in enumerate(("1x Cadigan's Epiphany", "1x Astrid's Creativity", "10x Chaos Orb"))]
        with logger._connect() as db:
            rows = propagation_scan._reward_rows(db, detections)
            families = propagation_scan._family_candidates(db, rows)
        return rows, families

    def test_exact_three_record_manifest(self):
        """Accept the candidate through real archive inspection with no screenshots or learned evidence."""
        self.assertEqual(len(PACK.read_bytes()), 607)
        self.assertEqual(hashlib.sha256(PACK.read_bytes()).hexdigest(),
                         "7699c2ceafa2cb0d930bb102fbf8d499bb64514e3a8a1168dccec3c5ff73b6ff")
        with ZipFile(PACK) as archive:
            self.assertEqual(archive.namelist(), ["manifest.json"])
            self.assertEqual(json.loads(archive.read("manifest.json")), manifest())
        data, assets = reference_pack._inspect(PACK)
        self.assertEqual(assets, {})
        self.assertEqual({key: len(rows) for key, rows in data.items() if rows},
                         {"recipes": 1, "families": 1, "seed_states": 1})
        self.assertEqual(data["recipes"][0]["combo"], COMBO)

    def test_injected_reward_text_resolves_before_and_after_without_approval(self):
        """Expose the old missing reward, then resolve all three real matcher rows to the confirmed family."""
        before, families = self.injected_reward_rows()
        self.assertEqual([row["recipe"] for row in before], [*NAMES[:2], None])
        self.assertEqual(families, [])
        chain = logger.get_state()["chain"]
        self.apply()
        after, families = self.injected_reward_rows()
        self.assertEqual([row["recipe"] for row in after], NAMES)
        self.assertEqual(families, [19])
        self.assertEqual(logger.get_state()["chain"], chain)
        self.assertEqual([row["recipe"] for row in logger.resolve(NAMES[0], family=19)["rows"]], NAMES)

    def test_only_the_three_supplied_reference_rows_change(self):
        """Preserve unrelated defaults/local edits, all histories, old meanings and existing identities."""
        before, history = self.catalog(), self.historical()
        identities = self.rows("recipe_ids")
        receipts = self.rows("chain_append_receipts")
        counts = self.apply()
        self.assertEqual({key: count for key, count in counts.items() if count},
                         {"recipes": 1, "families": 1, "seed_states": 1})
        after = self.catalog()
        self.assertEqual([row for row in after["recipes"] if row[0] != NAMES[-1]], before["recipes"])
        self.assertEqual([row for row in after["families"] if row[0] != 19],
                         [row for row in before["families"] if row[0] != 19])
        self.assertEqual([row for row in after["seed_states"] if row[:2] != (19, 7)],
                         [row for row in before["seed_states"] if row[:2] != (19, 7)])
        for table in before.keys() - {"recipes", "families", "seed_states"}:
            self.assertEqual(after[table], before[table], table)
        self.assertEqual(self.historical(), history)
        self.assertEqual(self.rows("chain_append_receipts"), receipts)
        self.assertEqual(self.rows("recipe_ids")[:len(identities)], identities)
        self.assertEqual(self.rows("recipe_ids")[len(identities):], [(NAMES[-1], logger._recipe_identifier(NAMES[-1]))])
        self.assertEqual(self.family19(), NAMES)
        self.assertNotEqual(logger._recipe_identifier("Aldur's Saga"), logger._recipe_identifier(NAMES[-1]))

    def test_single_undo_restores_reference_rows_and_keeps_history(self):
        """Reverse the whole import in one action while retaining the new ID tombstone as designed."""
        before, history = self.catalog(), self.historical()
        self.assertTrue(self.rows("chain_append_receipts"))
        self.apply()
        self.assertEqual(logger.undo_last_action()["action"], "Import OCR references")
        self.assertEqual(self.catalog(), before)
        self.assertEqual(self.historical(), history)
        # Undo intentionally retires retry receipts and capture generations;
        # original saved chain/provenance/log/export records remain exact.
        self.assertEqual(self.rows("chain_append_receipts"), [])
        self.assertEqual(self.family19(), OLD_NAMES)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT recipe_id FROM recipe_ids WHERE recipe_name=?", (NAMES[-1],)).fetchone()[0],
                             logger._recipe_identifier(NAMES[-1]))

    def test_reimport_is_idempotent_and_unchecked_import_does_not_replace_existing(self):
        """Require the existing replacement checkbox; repeated deliberate imports add no duplicate rows or Undo."""
        original = self.catalog()
        with self.assertRaisesRegex(ValueError, "disagree"):
            reference_pack.import_pack(PACK, replace_existing=False)
        self.assertEqual(self.catalog(), original)
        self.assertEqual(self.family19(), OLD_NAMES)
        self.apply()
        before, undo = self.catalog(), self.rows("undo_actions")
        self.apply()
        self.assertEqual(self.catalog(), before)
        self.assertEqual(self.rows("undo_actions"), undo)

    def test_invalid_seed_reference_rolls_back_recipe_family_and_ids(self):
        """Exercise transaction rollback after the new recipe and changed family have been written."""
        invalid = manifest()
        invalid["data"]["seed_states"][0]["rewards_json"] = '["Missing confirmed reward"]'
        path = Path(self.temporary.name) / "invalid.zip"
        write_pack(path, invalid)
        before, history = self.catalog(), self.historical()
        identities, undo = self.rows("recipe_ids"), self.rows("undo_actions")
        receipts = self.rows("chain_append_receipts")
        with self.assertRaisesRegex(ValueError, "unknown recipe"):
            reference_pack.import_pack(path, replace_existing=True)
        self.assertEqual(self.catalog(), before)
        self.assertEqual(self.historical(), history)
        self.assertEqual(self.rows("recipe_ids"), identities)
        self.assertEqual(self.rows("undo_actions"), undo)
        self.assertEqual(self.rows("chain_append_receipts"), receipts)


if __name__ == "__main__":
    unittest.main()
