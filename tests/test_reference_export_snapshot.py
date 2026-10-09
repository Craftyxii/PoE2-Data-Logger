"""Reference export checks taking related database records from one consistent snapshot."""

from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store


class ReferenceExportSnapshotTests(unittest.TestCase):
    """Exercise consistent family and recipe export snapshots during concurrent database edits."""
    def test_family_and_recipe_export_use_one_snapshot_during_concurrent_edit(self):
        """Verify an intervening writer cannot mix new recipe data with old family data in an exported pack."""
        with tempfile.TemporaryDirectory(prefix="poe2-reference-snapshot-") as directory:
            previous = store.DATA_DIR
            try:
                store.DATA_DIR = Path(directory) / "source"
                logger._READY = False
                logger.initialize()
                logger.save_recipe({"name": "Reference export race example", "sockets": 6,
                                    "combo": " + ".join(["Rage"] * 6)})
                logger.save_family({"family": 9999, "top_socket": 6,
                                    "recipes": ["Reference export race example"]})
                original_connect = logger._connect
                changed = False

                class ExportConnection:
                    """Wrap an export connection to inject a committed edit between related table reads."""
                    def __init__(self, database):
                        """Retain the underlying connection whose read snapshot is being exercised."""
                        self.database = database

                    def execute(self, query, *arguments):
                        """Commit a concurrent family and recipe update once before forwarding the recipe query."""
                        nonlocal changed
                        if query.startswith("SELECT name,sockets,combo") and not changed:
                            changed = True
                            # A valid update commits after the export already read
                            # families, but before it starts reading recipes.
                            with original_connect() as writer:
                                writer.execute("UPDATE families SET top_socket=7 WHERE id=9999")
                                writer.execute("UPDATE recipes SET sockets=7,combo=? WHERE name=?",
                                               (" + ".join(["Rage"] * 7), "Reference export race example"))
                        return self.database.execute(query, *arguments)

                @contextmanager
                def concurrent_export_connection():
                    """Yield the wrapped export connection while retaining the logger connection's normal lifecycle."""
                    with original_connect() as database:
                        yield ExportConnection(database)

                with patch.object(logger, "_connect", concurrent_export_connection):
                    contents = reference_pack.export_pack()
                self.assertTrue(changed)
                with original_connect() as db:
                    self.assertEqual(db.execute("SELECT sockets FROM recipes WHERE name=?",
                                                ("Reference export race example",)).fetchone()[0], 7)
                with ZipFile(io.BytesIO(contents)) as archive:
                    self.assertIsNone(archive.testzip())
                    data = json.loads(archive.read("manifest.json"))["data"]
                family = next(row for row in data["families"] if row["id"] == 9999)
                recipe = next(row for row in data["recipes"] if row["name"] == "Reference export race example")
                self.assertEqual(family["top_socket"], 6)
                self.assertEqual(recipe["sockets"], 6)

                path = Path(directory) / "references.zip"
                path.write_bytes(contents)
                store.DATA_DIR = Path(directory) / "restored"
                logger._READY = False
                reference_pack.import_pack(path, replace_existing=True)
                with original_connect() as db:
                    self.assertEqual(db.execute("SELECT top_socket FROM families WHERE id=9999").fetchone()[0], 6)
                    self.assertEqual(db.execute("SELECT sockets FROM recipes WHERE name=?",
                                                ("Reference export race example",)).fetchone()[0], 6)
            finally:
                store.DATA_DIR = previous
                logger._READY = False


if __name__ == "__main__":
    unittest.main()
