"""Independent focused audit; injected OCR results are explicitly identified."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class StableRitualCurrencyAudit(unittest.TestCase):
    """Audit real Ritual captures and guarded currency review with isolated saved data."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the Qt application for review widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open isolated storage and retain queued OCR jobs for controlled completion."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-stable-ritual-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))

    def tearDown(self):
        """Close workers and restore the caller's storage after each review scenario."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def click(self, table, row, name):
        """Invoke a named enabled row action on the specified captured currency row."""
        action = table.cellWidget(row, 3).findChild(QPushButton, name)
        self.assertTrue(action.isEnabled())
        action.click()

    def test_real_reward_pixels_preserve_duplicates_deferred_raw_and_page_identity(self):
        """No OCR injection: scan captured 08.png, correct unknowns, and scan it again."""
        folder = Path(__file__).parent / "fixtures/ritual_rewards"
        expected = next(case for case in json.loads((folder / "expected.json").read_text())
                        if case["file"] == "08.png")
        with Image.open(folder / "08.png") as source:
            image = source.convert("RGB")
        raw_evidence = None
        for scan in range(2):
            self.window._ritual_captured(image, live=False)
            work, callback = self.jobs[-1]
            result = work()
            self.assertEqual([item["grid_slots"] for item in result["items"]], expected["footprints"])
            callback(result)
            table = self.window.ritual_table
            self.assertEqual(table.rowCount(), len(expected["footprints"]))
            self.assertEqual([table.item(row, 5).checkState() == Qt.CheckState.Checked
                              for row in range(table.rowCount())],
                             [slots[0] in expected["deferred_anchors"] for slots in expected["footprints"]])
            for row in range(table.rowCount()):
                if not table.item(row, 1).text():
                    table.item(row, 1).setText(f"Audit equipment {row}")
                if not table.item(row, 2).text():
                    table.item(row, 2).setText("1")
            if scan == 0:
                raw_evidence = self.window.ritual_raw.toPlainText()
            self.assertEqual(self.window.ritual_raw.toPlainText(), raw_evidence)
            self.window.approve_scan_button.click()
            self.assertIsNone(self.window.pending_review_kind)
            pages = logger.ritual_pages_for_map("M0001")
            self.assertEqual(len(pages), 1)
            self.assertEqual(len(pages[0]["items"]), len(expected["footprints"]))
            self.assertEqual(sum(item["name"] == "Omen of Resurgence" for item in pages[0]["items"]), 2)
            export = list(csv.DictReader(io.StringIO(logger.export_ritual_csv().decode("utf-8-sig"))))
            self.assertTrue(all(row["Page OCR Text"] == raw_evidence for row in export))
            self.assertEqual(logger.get_state()["scan_commit_count"], scan + 1)

    def test_injected_currency_approval_filters_edits_and_duplicate_stacks(self):
        """Injected OCR; real edit signals, row Approve/Reject, and final Approve controls."""
        self.window._inventory_read({"items": [
            {"slot": 1, "name": "Chaos Orb", "quantity": 4},
            {"slot": 2, "name": "Chaos Orb", "quantity": 7},
            {"slot": 3, "name": "Regal Orb", "quantity": 3}],
            "unknown": [{"slot": 4, "candidate": "Divine Orb"}]}, live=False)
        table = self.window.inventory_table
        table.item(0, 2).setText("8")
        self.assertEqual(table.cellWidget(0, 3).property("reviewStatus"), "pending")
        self.click(table, 0, "approveCurrency")
        table.item(1, 2).setText("9")
        self.click(table, 1, "approveCurrency")
        self.click(table, 2, "rejectCurrency")
        table.item(3, 1).setText("Unapproved custom audit label")
        table.item(3, 2).setText("999")
        self.window.approve_scan_button.click()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 17})
        self.assertNotIn("Unapproved custom audit label", logger.inventory_names())
        self.assertEqual([table.cellWidget(row, 3).property("reviewStatus") for row in range(4)],
                         ["approved", "approved", "rejected", "rejected"])

    def test_real_context_pixels_save_nonempty_raw_headers_and_repeated_labels(self):
        """No OCR injection: full 11_context.webp retains header text and repeated rewards."""
        path = Path(__file__).parent / "fixtures/ritual_rewards/11_context.webp"
        with Image.open(path) as source:
            image = source.convert("RGB")
        self.window._ritual_captured(image, live=False)
        work, callback = self.jobs[-1]
        result = work()
        self.assertEqual(result["raw_text"], "FAVOURS\n41,220 TRIBUTE")
        self.assertEqual((result["tribute_available"], result["rerolls_remaining"]), (41220, 6))
        callback(result)
        table = self.window.ritual_table
        repeated = 0
        for row in range(table.rowCount()):
            if not table.item(row, 1).text():
                table.item(row, 1).setText("Audit repeated ring" if repeated < 2 else f"Audit equipment {row}")
                repeated += 1
            if not table.item(row, 2).text():
                table.item(row, 2).setText("1")
        self.window.approve_scan_button.click()
        self.assertIsNone(self.window.pending_review_kind)
        items = logger.ritual_pages_for_map("M0001")[0]["items"]
        self.assertEqual(sum(item["name"] == "Omen of Refreshment" for item in items), 3)
        self.assertEqual(sum(item["name"] == "Audit repeated ring" for item in items), 2)
        rows = list(csv.DictReader(io.StringIO(logger.export_ritual_csv().decode("utf-8-sig"))))
        self.assertTrue(rows)
        self.assertEqual(rows[0]["Page OCR Text"], result["raw_text"])
        self.assertTrue(all(row["Page OCR Text"] == "" for row in rows[1:]))
        self.assertTrue(all(row["Ritual Tribute Available"] == "41220" and
                            row["Ritual Rerolls Remaining"] == "6" for row in rows))

    def test_injected_currency_phase_controls_bind_same_map_and_ignore_old_callback(self):
        """Injected OCR; actual capture binding, combo signals and + New map button."""
        image = Image.new("RGB", (480, 200), (26, 26, 40))
        image.info["poe2_inventory_aligned"] = True
        self.window._inventory_captured(image, live=False)
        callback = self.jobs[-1][1]
        phase = self.window.inventory_phase
        self.assertFalse(phase.isEnabled())
        phase.setCurrentIndex(phase.findData("end"))
        self.assertEqual(phase.currentData(), "start")
        result = {"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 7}], "unknown": []}
        callback(result)
        self.assertTrue(phase.isEnabled())
        phase.setCurrentIndex(phase.findData("end"))
        self.assertEqual(self.window._pending_currency_map, "M0001")
        self.assertEqual(self.window._pending_currency_phase, "end")
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Chaos Orb": 7})
        self.window._inventory_captured(image, live=False)
        callback = self.jobs[-1][1]
        new_map = next(button for button in self.window.findChildren(QPushButton)
                       if button.text() == "+ New map")
        new_map.click()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        callback(result)
        self.assertEqual(self.window.inventory_table.rowCount(), 0)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.currency_for_map("M0002")["end"], {})

    def test_injected_uncertain_deferred_state_cannot_commit_until_confirmed(self):
        """Injected OCR; real partially checked Deferred row and final approval."""
        self.window._ritual_read({"items": [{"category": "Item", "name": "Chaos Orb",
            "quantity": 1, "tribute": 0, "source": "Injected audit evidence", "deferred": None}],
            "raw_text": "Injected audit raw", "unmatched": [],
            "tribute_available": 0, "rerolls_remaining": 0}, live=False)
        deferred = self.window.ritual_table.item(0, 5)
        self.assertEqual(deferred.checkState(), Qt.CheckState.PartiallyChecked)
        with self.assertRaisesRegex(ValueError, "Deferred"):
            self.window.save_ritual()
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        deferred.setCheckState(Qt.CheckState.Checked)
        self.window.approve_scan_button.click()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertTrue(logger.ritual_pages_for_map("M0001")[0]["items"][0]["deferred"])
        self.assertFalse(deferred.flags() & Qt.ItemFlag.ItemIsUserCheckable)


if __name__ == "__main__":
    unittest.main()
