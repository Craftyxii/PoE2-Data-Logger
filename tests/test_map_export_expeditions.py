"""Export checks retaining map-linked expedition identities and ordered saved chain data."""

import csv
import io
from pathlib import Path
import tempfile
import unittest

from PoE2_Data_Logger.core import logger_store as logger, store


class MapExportExpeditionTests(unittest.TestCase):
    """Check map-export expedition columns retain positions, sparse IDs, and count semantics."""
    def setUp(self):
        """Initialize temporary logger storage, reset IDs, and start the first map."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-map-export-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Restore the original data directory and remove the isolated logger database."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def exported(self):
        """Validate map CSV structure and return headers plus rows keyed by map ID."""
        raw = list(csv.reader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig"))))
        self.assertEqual(len(raw[0]), len(set(raw[0])))
        self.assertTrue(all(len(row) == len(raw[0]) for row in raw))
        return raw[0], {row[0]: dict(zip(raw[0], row)) for row in raw[1:]}

    def test_later_expedition_columns_preserve_existing_positions_and_map_identity(self):
        """Verify later expedition columns preserve existing positions and map identity."""
        previous_headers, _ = self.exported()
        self.assertEqual(previous_headers[13:15], ["Expedition 1 Detonated", "Expedition 2 Detonated"])
        logger.save_detonated(5)
        logger.save_settings({"expedition": 2})
        logger.save_detonated(0)
        logger.save_settings({"expedition": 3})
        third = logger.save_detonated(31)
        logger.save_settings({"expedition": 4})
        logger.finish_map(1, 2, 3, 41)
        logger.start_map()
        logger.save_settings({"expedition": 3})
        logger.save_detonated(0)
        logger.save_settings({"expedition": 4})
        logger.finish_map(4, 5, 6, "")
        headers, rows = self.exported()
        original_headers = previous_headers[:-2]
        self.assertEqual(headers[:len(original_headers)], original_headers)
        self.assertEqual(headers[len(original_headers):],
                         ["Expedition 3 Detonated", "Expedition 4 Detonated", "Unique Kills", "Total Kills"])
        self.assertEqual(third["expedition_id"], "M0001-E03")
        for number, expected in ((1, "5"), (2, "0"), (3, "31"), (4, "41")):
            self.assertEqual(rows["M0001"][f"Expedition {number} Detonated"], expected)
        self.assertEqual(rows["M0002"]["Expedition 3 Detonated"], "0")
        self.assertEqual(rows["M0002"]["Expedition 4 Detonated"], "")

    def test_sparse_expedition_ids_only_add_the_stored_column(self):
        """Verify sparse expedition IDs only add the stored column."""
        previous_headers, _ = self.exported()
        logger.save_settings({"expedition": 100000})
        saved = logger.save_detonated(7)
        headers, rows = self.exported()
        original_headers = previous_headers[:-2]
        self.assertEqual(headers[:len(original_headers)], original_headers)
        self.assertEqual(headers[len(original_headers):],
                         ["Expedition 100000 Detonated", "Unique Kills", "Total Kills"])
        self.assertEqual(saved["expedition_id"], "M0001-E100000")
        self.assertEqual(rows["M0001"]["Expedition 100000 Detonated"], "7")


if __name__ == "__main__":
    unittest.main()
