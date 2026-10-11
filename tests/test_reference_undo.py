"""Verify reference corrections cannot strand earlier logging actions in the Undo stack."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store


class ReferenceUndoTests(unittest.TestCase):
    """Exercise destructive reference corrections and exact approval reversal in isolated profiles."""

    def setUp(self):
        """Create a clean active map without using the selected user's reference database."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-reference-undo-")
        self.previous = store.DATA_DIR
        self.profile("target")

    def tearDown(self):
        """Restore the selected profile and remove the temporary databases and captures."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def profile(self, name):
        """Select an independent initialized profile with one map and no sample logging rows."""
        store.DATA_DIR = Path(self.temporary.name) / name
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def test_deliberate_affix_edit_is_one_undoable_reference_action(self):
        """Reverse an explicit catalog addition without changing existing affix names."""
        before = logger.get_state()["affixes"]
        logger.add_affixes(["Manual Undo Audit Modifier"])
        self.assertEqual(logger.undo_action(), "Affix references")
        self.assertEqual(logger.undo_last_action()["action"], "Affix references")
        self.assertEqual(logger.get_state()["affixes"], before)

    def test_additive_scan_discovery_does_not_consume_the_previous_map_action(self):
        """Undo a saved map and its later implicit learning without replacing the user command label."""
        existing = logger.get_state()["affixes"][0]
        with logger.undo_group("New map"):
            logger.finish_map(0, 0, 0, 0)
            logger.start_map()
        self.assertEqual(logger.add_affixes([existing, "Automatic Undo Audit Modifier"], record_undo=False),
                         ["Automatic Undo Audit Modifier"])
        self.assertEqual(logger.undo_action(), "New map")
        self.assertEqual(logger.undo_last_action()["action"], "New map")
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assertNotIn("Automatic Undo Audit Modifier", logger.get_state()["affixes"])

    def test_reset_then_automatic_rediscovery_preserves_reset_and_prior_undo(self):
        """Restore a deleted custom affix despite implicit rediscovery, then undo its original addition."""
        name = "increased Local Release Audit Rewards in Map"
        logger.add_affixes([name])
        before = self.references()
        reference_pack.reset_to_defaults()
        self.assertNotIn(name, logger.get_state()["affixes"])
        self.assertEqual(logger.add_affixes([name], record_undo=False), [name])
        self.assertEqual(logger.undo_action(), "Reset OCR references")
        self.assertEqual(logger.undo_last_action()["action"], "Reset OCR references")
        self.assertEqual(self.references(), before)
        self.assertEqual(logger.undo_last_action()["action"], "Affix references")
        self.assertNotIn(name, logger.get_state()["affixes"])

    def test_repeated_implicit_discovery_keeps_one_previous_action(self):
        """Reverse multiple additive discovery transactions with the latest saved user action."""
        logger.add_affixes(["Deliberate Audit Modifier"])
        with logger._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0]
        for name in ("First Implicit Audit Modifier", "Second Implicit Audit Modifier"):
            logger.add_affixes([name], record_undo=False)
        logger.add_affixes(["First Implicit Audit Modifier"], record_undo=False)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0], count)
        self.assertEqual(logger.undo_last_action()["action"], "Affix references")
        self.assertFalse(any("Audit Modifier" in name for name in logger.get_state()["affixes"]))

    def test_implicit_discovery_without_saved_action_does_not_invent_undo(self):
        """Permit baseline discovery when there is no saved command to receive its inverse."""
        with logger._connect() as db:
            db.execute("DELETE FROM undo_actions")
        logger.add_affixes(["Bootstrap Implicit Audit Modifier"], record_undo=False)
        self.assertEqual(logger.undo_action(), "")
        self.assertIn("Bootstrap Implicit Audit Modifier", logger.get_state()["affixes"])

    def artwork(self):
        """Draw visible nonuniform artwork accepted by the real reviewed-icon validator."""
        image = Image.new("RGB", (96, 96), (15, 20, 45))
        draw = ImageDraw.Draw(image)
        draw.ellipse((10, 10, 86, 86), fill=(200, 80, 50))
        draw.line((0, 96, 96, 0), fill=(10, 210, 190), width=8)
        return image

    def approve(self):
        """Save an actual end inventory that teaches a new label and distinctive artwork."""
        logger.save_currency_snapshot("end", [{"name": "Reference Undo Token", "quantity": 7}],
            register_names=True, icon_examples=[{"name": "Reference Undo Token", "category": "Currency",
                "image": self.artwork(), "columns": 1, "rows": 1}])

    def references(self):
        """Read exact reference row contents independently of incidental SQLite row ordering."""
        with logger._connect() as db:
            return {table: sorted((tuple(row) for row in db.execute(f"SELECT * FROM {table}")), key=repr)
                    for table in (*reference_pack._DEFAULT_TABLES, *reference_pack._EXAMPLE_TABLES)}

    def pack(self, edit=None):
        """Create a valid exported ZIP with an optional supported manifest correction."""
        with ZipFile(io.BytesIO(reference_pack.export_pack())) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        if edit:
            manifest = json.loads(files["manifest.json"])
            edit(manifest["data"])
            files["manifest.json"] = json.dumps(manifest).encode()
        path = Path(self.temporary.name) / "references.zip"
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
        return path

    def test_remove_learned_example_undo_then_approval_undo(self):
        """Restore a removed learned icon before reversing the approval that originally taught it."""
        self.approve()
        before = logger.review_icons()
        logger.delete_review_icon(before[0]["id"])
        self.assertEqual(logger.undo_action(), "Remove reference example")
        self.assertEqual(logger.review_icons(), [])
        self.assertEqual(logger.undo_last_action()["action"], "Remove reference example")
        self.assertEqual(logger.review_icons(), before)
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Reference Undo Token": 7})
        self.assertEqual(logger.undo_last_action()["action"], "Currency approval")
        self.assertEqual(logger.currency_for_map("M0001")["end"], {})
        self.assertEqual(logger.review_icons(), [])

    def test_manual_example_removal_and_duplicate_save_have_durable_chronology(self):
        """Undo each supported icon deletion while duplicate saves and missing deletions stay no-ops."""
        for add, save, read, remove, name in (
                (logger.add_currency_item, logger.save_currency_icon, logger.currency_icons,
                 logger.delete_currency_icon, "Manual Undo currency"),
                (logger.add_item_name, logger.save_item_icon, logger.item_icons,
                 logger.delete_item_icon, "Manual Undo item"),
                (logger.add_ritual_name, logger.save_omen_icon, logger.omen_icons,
                 logger.delete_omen_icon, "Omen of Manual Undo")):
            with self.subTest(name=name):
                add(name)
                icon_id = save(name, self.artwork())
                before = read()
                action = logger.undo_action()
                with logger._connect() as db:
                    count = db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0]
                self.assertEqual(save(name, self.artwork()), icon_id)
                remove(icon_id + 10000)
                with logger._connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0], count)
                self.assertEqual(logger.undo_action(), action)
                remove(icon_id)
                logger._READY = False
                logger.initialize()
                self.assertEqual(logger.undo_last_action()["action"], "Remove reference example")
                self.assertEqual(read(), before)
                self.assertEqual(logger.undo_last_action()["action"], "Save reference example")
                self.assertEqual(read(), [])

    def test_import_relabel_undo_restores_previous_artwork_label_and_prior_approval(self):
        """Undo an imported correction of existing artwork without losing its earlier approval inverse."""
        self.approve()
        before = self.references()

        def relabel(data):
            """Give the existing learned artwork a different registered currency label."""
            data["currency_names"].append({"name": "Imported Undo Token"})
            data["review_icon_examples"][0]["name"] = "Imported Undo Token"

        reference_pack.import_pack(self.pack(relabel), replace_existing=True)
        self.assertEqual(logger.undo_action(), "Import OCR references")
        self.assertEqual(logger.review_icons()[0]["name"], "Imported Undo Token")
        self.assertEqual(logger.undo_last_action()["action"], "Import OCR references")
        self.assertEqual(self.references(), before)
        self.assertEqual(logger.undo_action(), "Currency approval")
        logger.undo_last_action()
        self.assertEqual(logger.currency_for_map("M0001")["end"], {})
        self.assertEqual(logger.review_icons(), [])

    def test_replace_import_undo_keeps_catalog_before_images_and_noop_order(self):
        """Capture REPLACE deletions and avoid new Undo entries for identical reference imports."""
        before = self.references()
        original_action = logger.undo_action()
        unchanged = self.pack()
        reference_pack.import_pack(unchanged, replace_existing=True)
        # The first import can canonicalize shipped rune-combo spacing. A
        # repeat of those same values must still leave chronology unchanged.
        with logger._connect() as db:
            initial_count = db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0]
        reference_pack.import_pack(unchanged, replace_existing=True)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0], initial_count)
        if self.references() != before:
            self.assertEqual(logger.undo_last_action()["action"], "Import OCR references")
        self.assertEqual(self.references(), before)
        self.assertEqual(logger.undo_action(), original_action)

        def edit(data):
            """Alter supported fields of existing same-name references without changing their keys."""
            data["recipes"][0]["source"] = "Imported Undo source"
            data["families"][0]["valid"] = not data["families"][0]["valid"]
            data["master_perks"][0]["effect"] = "Imported Undo effect"
            data["seed_states"][0]["seed_rune"] = "Imported Undo rune"

        changed = self.pack(edit)
        reference_pack.import_pack(changed, replace_existing=True)
        self.assertNotEqual(self.references(), before)
        with logger._connect() as db:
            action_count = db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0]
        reference_pack.import_pack(changed, replace_existing=True)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0], action_count)
        self.assertEqual(logger.undo_last_action()["action"], "Import OCR references")
        self.assertEqual(self.references(), before)
        self.assertEqual(logger.undo_action(), original_action)

    def test_reset_then_manual_readdition_of_same_name_undo_in_order(self):
        """Undo a re-added removed label before Undo restores its earlier pre-reset catalog row."""
        for add, names, name in ((logger.add_currency_item, logger.currency_names, "Reset manual currency"),
                                (logger.add_item_name, logger.item_names, "Reset manual item"),
                                (logger.add_ritual_name, logger.ritual_names, "Omen of Reset Manual")):
            with self.subTest(name=name):
                add(name)
                reference_pack.reset_to_defaults()
                self.assertNotIn(name, names())
                add(name)
                logger.undo_last_action()
                self.assertNotIn(name, names())
                self.assertEqual(logger.undo_last_action()["action"], "Reset OCR references")
                self.assertIn(name, names())
                logger.undo_last_action()
                self.assertNotIn(name, names())

    def test_imported_capture_undo_removes_metadata_and_glyph_but_retains_raw_file(self):
        """Reverse imported screenshot rows and learned vectors while preserving immutable capture bytes."""
        self.profile("source")
        raw = io.BytesIO()
        self.artwork().save(raw, format="PNG")
        with patch.object(store, "_reviewed_vector", return_value=bytes(1296 * 4)):
            store.save_scan(raw.getvalue(), "import-undo.png", 3, "P1", "Imported screenshot rune")
        path = self.pack()
        self.profile("target")
        before = self.references()
        result = reference_pack.import_pack(path)
        self.assertEqual(result["scans"], 1)
        self.assertEqual(result["glyphs"], 1)
        scan = store.list_scans()[0]
        capture = store.image_for(scan["id"])
        self.assertEqual(capture.read_bytes(), raw.getvalue())
        logger._READY = False
        logger.initialize()
        self.assertEqual(logger.undo_last_action()["action"], "Import OCR references")
        self.assertEqual(self.references(), before)
        self.assertEqual(store.list_scans(), [])
        self.assertEqual(capture.read_bytes(), raw.getvalue())


if __name__ == "__main__":
    unittest.main()
