"""Focused regressions for the explicitly requested scan review and chain-gap behavior."""

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class DiscussedScanUITests(unittest.TestCase):
    """Keep request ownership, explicit approval and existing saved chain semantics intact."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="poe2-discussed-ui-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.directory.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        self.manager = HotkeyManager(supported=False)
        self.hotkey = patch.object(service, "HOTKEY", self.manager)
        self.hotkey.start()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.set_auto_commit(True)
        raw = io.BytesIO()
        Image.new("RGB", (600, 400), "tan").save(raw, format="PNG")
        self.raw = raw.getvalue()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.hotkey.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.directory.cleanup()

    def opened(self, family=50):
        """Make one confident delivered reading using actual ordered catalog rewards."""
        with logger._connect() as db:
            names = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=?", (family,)).fetchone()[0])
        resolved = logger.resolve(names[0], names[1] if len(names) > 1 else None, family)
        return {"mode": "opened", "status": "Review the opened rewards before logging.",
                "can_use": True, "family": f"Family {family}", "candidates": [family],
                "sockets": resolved["rows"][0]["sockets"], "recipe_sockets": resolved["rows"][0]["sockets"],
                "socket_source": "opened icons", "first_line_gap": 60, "list_complete": False,
                "first_recipe": names[0], "next_recipe": names[1] if len(names) > 1 else None,
                "opened_recipes": [{"recipe": name, "raw": name, "ocr_score": .99, "match_score": 1}
                                   for name in names[:2]], **logger.scan_context()}

    def propagation(self):
        """Keep a valid screenshot reading separate from the user's approval action."""
        with logger._connect() as db:
            combo = db.execute("SELECT combo FROM recipes WHERE name=?", ("Medved's Saga",)).fetchone()[0]
        return {"mode": "propagation", "can_use": True, "runes": [combo.split("+")[0].strip()],
                "positions": [1], "selected_recipe": "Medved's Saga", **logger.scan_context()}

    def older_chain(self):
        """Persist a real accepted entry old enough to require the same-chain question."""
        old = datetime.now(timezone.utc) - timedelta(seconds=65)
        with patch.object(logger, "_now", return_value=old.isoformat()):
            logger.commit_chain("Death")
        self.window.refresh()

    def test_consecutive_family_requires_approval_without_losing_suffix_fill(self):
        first = self.opened()
        self.window.show_result("opened", first, self.raw)
        self.window.maybe_auto_commit(first)
        second = self.opened()
        self.window.show_result("opened", second, self.raw)
        self.window.maybe_auto_commit(second)
        self.assertTrue(second["_repeat_family_review"])
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertIn("same family", self.window.review_summary.text())
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(self.window.recipe_table.rowCount(), len(self.window.resolved["rows"]))
        self.window.approve_remnant_scan()
        self.assertEqual(logger.get_state()["scan_commit_count"], 2)

    def test_redisplay_is_not_a_new_scan_and_intervening_held_family_counts(self):
        first = self.opened()
        self.window.show_result("opened", first, self.raw)
        self.window.show_result("opened", first, self.raw)
        self.assertFalse(first["_repeat_family_review"])
        other = self.opened(55)
        self.window.show_result("opened", other, self.raw)
        again = self.opened()
        self.window.show_result("opened", again, self.raw)
        self.assertFalse(again["_repeat_family_review"])
        repeated = self.opened()
        self.window.show_result("opened", repeated, self.raw)
        self.assertTrue(repeated["_repeat_family_review"])

    def test_same_chain_yes_preserves_commit_and_does_not_autoapprove(self):
        self.older_chain()
        reading = self.propagation()
        before = logger.get_state()["scan_commit_count"]
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as question:
            self.window._propagation_read(reading, self.raw)
            self.window._propagation_read(reading, self.raw)
        question.assert_called_once()
        self.assertEqual(logger.get_state()["scan_commit_count"], before)
        self.assertEqual(logger.get_state()["settings"]["expedition"], 1)
        self.window.approve_propagation_recipe(0)
        self.assertEqual(len(logger.get_state()["chain"]), 2)

    def test_same_chain_no_completes_and_retains_new_reading_for_manual_approval(self):
        self.older_chain()
        held = self.opened()
        self.window.show_result("opened", held, self.raw)
        reservation = logger.get_state()["ocr_pending"]
        reading = self.propagation()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window._propagation_read(reading, self.raw)
        state = logger.get_state()
        self.assertEqual(state["settings"]["expedition"], 2)
        self.assertEqual(state["chain"], [])
        self.assertEqual(state["ocr_pending"], reservation)
        self.assertEqual(reading["_capture_expedition"], 2)
        self.assertEqual(self.window._manual_propagation_context["_capture_expedition"], 2)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.window.approve_propagation_recipe(0)
        self.assertEqual(len(logger.get_state()["chain"]), 1)
        self.assertEqual(logger.get_state()["ocr_pending"], reservation)

    def test_timeout_no_discards_older_unapproved_review_and_retains_new_scan(self):
        logger.commit_chain("Death")
        self.window.refresh()
        previous = self.propagation()
        self.window._propagation_read(previous, self.raw)
        old = (datetime.now(timezone.utc) - timedelta(seconds=65)).isoformat()
        with logger._connect() as db:
            db.execute("UPDATE commits SET recorded_at=? WHERE kind='Chain'", (old,))
        incoming = self.propagation()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window._propagation_read(incoming, self.raw)
        self.assertEqual(logger.get_state()["settings"]["expedition"], 2)
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertNotEqual(previous["_chain_accept_request"],
                            self.window._manual_propagation_context["_chain_accept_request"])
        self.assertEqual(incoming["_capture_expedition"], 2)
        self.assertFalse(self.window.preview.pixmap().isNull())
        self.window.approve_propagation_recipe(0)
        self.assertEqual(len(logger.get_state()["chain"]), 1)

    def test_timeout_no_keeps_held_remnant_while_replacing_older_propagation(self):
        logger.commit_chain("Death")
        self.window.refresh()
        self.window.show_result("opened", self.opened(), self.raw)
        pending = logger.get_state()["ocr_pending"]
        self.window._propagation_read(self.propagation(), self.raw)
        old = (datetime.now(timezone.utc) - timedelta(seconds=65)).isoformat()
        with logger._connect() as db:
            db.execute("UPDATE commits SET recorded_at=? WHERE kind='Chain'", (old,))
        incoming = self.propagation()
        buffer = io.BytesIO()
        Image.new("RGB", (600, 400), (12, 140, 50)).save(buffer, format="PNG")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window._propagation_read(incoming, buffer.getvalue())
        self.assertEqual(logger.get_state()["settings"]["expedition"], 2)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.window.preview.pixmap().toImage().pixelColor(0, 0).getRgb()[:3], (12, 140, 50))
        self.assertEqual(self.window._manual_propagation_context["_chain_accept_request"],
                         incoming["_chain_accept_request"])
        self.assertEqual(logger.get_state()["chain"], [])

    def test_approval_time_no_preserves_denied_recipe_choices(self):
        logger.commit_chain("Death")
        self.window.refresh()
        reading = self.propagation()
        reading["choices"] = [dict(reading), dict(reading)]
        self.window._propagation_read(reading, self.raw)
        self.window.deny_propagation_recipe(1)
        old = (datetime.now(timezone.utc) - timedelta(seconds=65)).isoformat()
        with logger._connect() as db:
            db.execute("UPDATE commits SET recorded_at=? WHERE kind='Chain'", (old,))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window.approve_propagation_recipe(0)
        self.assertEqual(logger.get_state()["settings"]["expedition"], 2)
        self.assertTrue(self.window._propagation_choices[1]["denied"])
        with self.assertRaises(ValueError):
            self.window.approve_propagation_recipe(1)
        self.assertEqual(logger.get_state()["chain"], [])

    def test_unique_family_ignores_socket_and_confidence_holds_but_keeps_duplicate_review(self):
        reading = self.opened(38)
        reading.update(can_use=False, status="The opened socket count needs manual review.",
                       sockets=3, socket_source="Recipe DB", first_line_gap=0)
        reading["opened_recipes"][0].update(ocr_score=0, match_score=0)
        self.window.show_result("opened", reading, self.raw)
        self.window.maybe_auto_commit(reading)
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(len(logger.resolve(reading["first_recipe"], None, 38)["rows"]),
                         logger.get_state()["counts"]["new_rows"])
        repeat = self.opened(38)
        repeat.update(can_use=False, sockets=3)
        self.window.show_result("opened", repeat, self.raw)
        self.window.maybe_auto_commit(repeat)
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertTrue(repeat["_repeat_family_review"])

    def test_typed_chain_no_retains_draft_and_requires_another_explicit_commit(self):
        self.older_chain()
        self.window.rune_inputs[0].setText("Power")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
            self.window.commit_chain()
        question.assert_called_once()
        self.assertEqual(logger.get_state()["settings"]["expedition"], 2)
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertEqual(self.window.rune_inputs[0].text(), "Power")
        self.window.commit_chain()
        self.assertEqual(logger.get_state()["chain"], [{"step": 1, "rune1": "Power", "rune2": ""}])

    def test_typed_chain_yes_preserves_the_normal_commit(self):
        self.older_chain()
        self.window.rune_inputs[0].setText("Power")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as question:
            self.window.commit_chain()
        question.assert_called_once()
        self.assertEqual(logger.get_state()["settings"]["expedition"], 1)
        self.assertEqual([row["rune1"] for row in logger.get_state()["chain"]], ["Death", "Power"])

    def check_approval_time_no(self, held=False):
        """A gap growing while a review is open preserves the current edited evidence on No."""
        logger.commit_chain("Death")
        self.window.refresh()
        reservation = None
        if held:
            self.window.show_result("opened", self.opened(), self.raw)
            reservation = logger.get_state()["ocr_pending"]
        self.window._propagation_read(self.propagation(), self.raw)
        fields = self.window._propagation_row_inputs[0]
        fields[0].setCurrentIndex(2)
        expected_rune = fields[0].currentText()
        preview = self.window.preview.pixmap().toImage()
        old = (datetime.now(timezone.utc) - timedelta(seconds=65)).isoformat()
        with logger._connect() as db:
            db.execute("UPDATE commits SET recorded_at=? WHERE kind='Chain'", (old,))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window.approve_propagation_recipe(0)
        self.assertEqual(logger.get_state()["settings"]["expedition"], 2)
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertEqual(self.window.preview.pixmap().toImage(), preview)
        self.assertEqual(self.window._propagation_row_inputs[0][0].currentText(), expected_rune)
        self.assertEqual(logger.get_state()["ocr_pending"], reservation)
        self.window.approve_propagation_recipe(0)
        self.assertEqual(logger.get_state()["chain"][0]["rune1"], expected_rune)

    def test_approval_time_no_keeps_screenshot_and_edited_selectors(self):
        self.check_approval_time_no()

    def test_approval_time_no_keeps_screenshot_and_independent_remnant(self):
        self.check_approval_time_no(held=True)

    def test_currency_review_keeps_original_map_context_across_expedition_selection(self):
        """Currency owns the original map generation, with no expedition dependency."""
        context = logger.scan_context()
        jobs = []
        self.window._submit = lambda label, work, done: jobs.append((work, done))
        with patch.object(service.HOTKEY, "cancel_capture"), patch(
                "PoE2_Data_Logger.ui.native_desktop.item_ocr.inventory_grid", side_effect=lambda image: image):
            self.window._inventory_captured(Image.new("RGB", (600, 300)), live=False,
                                            expected_phase="end", expected_context=context)
        capture = self.window._inventory_reading
        self.assertEqual(capture["context"], context)
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(logger.get_state()["settings"]["expedition"], 2)
        self.window._inventory_read({"items": [{"slot": 1, "name": "Divine Orb", "quantity": 3}],
                                     "unknown": []}, live=False, expected_map_id="M0001", capture=capture)
        saved = self.window.save_inventory()
        self.assertEqual(saved["map_id"], "M0001")
        self.assertEqual(capture["context"], context)

    def test_kill_error_does_not_keep_an_old_currency_heading(self):
        self.window.review_kind.setText("Currency inventory")
        self.window.review_kind.setProperty("scanKind", "currency")
        self.manager._publish_event({"mode": "kills", "result": None,
                                     "error": "The kill region was not verified."}, None)
        self.window.poll()
        self.assertEqual(self.window.review_kind.text(), "Map kill counts")
        self.assertEqual(self.window.review_kind.property("scanKind"), "kills")
        self.assertIsNone(self.window.pending_review_kind)

    def test_same_family_scope_resets_for_a_new_map(self):
        first = self.opened()
        self.window.show_result("opened", first, self.raw)
        self.window.maybe_auto_commit(first)
        logger.mark_next_map(True)
        next_map = self.opened()
        self.window.show_result("opened", next_map, self.raw)
        self.assertEqual(next_map["map_id"], "M0002")
        self.assertFalse(next_map["_repeat_family_review"])

    def test_ordered_hotkey_delivery_keeps_remnant_and_propagation_images(self):
        self.window.set_auto_commit(False)
        opened, prop = self.opened(), self.propagation()
        for mode, result in (("opened", opened), ("propagation", prop)):
            self.manager._publish_event({"mode": mode, "result": result, "error": ""}, self.raw)
        self.window.poll()
        self.assertIs(self.window.results["opened"], opened)
        self.window.poll()
        self.assertEqual(self.window.review_kind.property("scanKind"), "propagation")
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertIsNotNone(self.window._held_remnant_review)
        self.assertTrue(self.window.preview.pixmap())
        self.assertIsNone(self.manager.next_event(self.window._latest_hotkey))

    def test_ordered_delivery_retains_propagation_after_remnant_auto_save(self):
        opened, prop = self.opened(), self.propagation()
        for mode, result in (("opened", opened), ("propagation", prop)):
            self.manager._publish_event({"mode": mode, "result": result, "error": ""}, self.raw)
        self.window.poll()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.window.poll()
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.assertTrue(self.window.preview.pixmap())
        self.assertEqual(self.window._manual_propagation_context["_chain_accept_request"],
                         prop["_chain_accept_request"])
        self.window.approve_propagation_recipe(0)
        with logger._connect() as db:
            kinds = [row[0] for row in db.execute("SELECT kind FROM commits ORDER BY number")]
        self.assertEqual(kinds, ["Remnant", "Propagation", "Chain"])
        self.assertIsNone(self.manager.next_event(self.window._latest_hotkey))

    def test_ready_propagation_reveals_while_older_file_remnant_is_reading(self):
        self.window._overlay_enabled = True
        capture = object()
        self.window._remnant_reading = capture
        self.window._review_pending("remnant", "Reading remnant image…", False)
        self.window._propagation_read(self.propagation(), self.raw)
        with patch.object(self.window, "show_overlay") as reveal:
            self.window._reveal_review_overlay(self.window._overlay_review_token)
        reveal.assert_called_once_with(automatic=True)
        self.assertIs(self.window._remnant_reading, capture)

    def test_reveal_retries_when_older_reader_finishes_and_honors_escape_token(self):
        self.window._review_pending("remnant", "Reading remnant image…", False)
        self.app.processEvents()
        self.window._overlay_enabled = True
        self.window._remnant_reading = object()
        token = self.window._overlay_review_token
        with patch.object(self.window, "show_overlay") as reveal:
            self.window._reveal_review_overlay(token)
            self.window._remnant_reading = None
            QTest.qWait(130)
        reveal.assert_called_once_with(automatic=True)
        self.window._remnant_reading = object()
        with patch.object(self.window, "show_overlay") as reveal:
            self.window._reveal_review_overlay(token)
            self.window._overlay_review_token += 1
            self.window._remnant_reading = None
            QTest.qWait(130)
        reveal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
