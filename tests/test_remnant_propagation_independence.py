"""Captured remnants and propagation chains keep separate review/ID bindings."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, service, store


class RemnantPropagationIndependenceTests(unittest.TestCase):
    """Exercise independent remnant reservations and propagation counts across expedition advancement."""
    def setUp(self):
        """Create a fresh isolated map database for capture-context checks."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-remnant-propagation-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Restore the logger data directory and remove temporary storage."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def recipes(self):
        """Read the family-26 recipe names used for remnant commits."""
        with logger._connect() as db:
            return json.loads(db.execute("SELECT recipes_json FROM families WHERE id=26").fetchone()[0])[1:]

    def opened(self):
        """Reserve an opened-remnant ID and combine it with the current capture context."""
        return {**logger.scan_context(), **logger.assign_ocr_id("opened")}

    def records(self):
        """Snapshot context and history tables to detect writes from rejected captures."""
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("meta", "maps", "expeditions", "new_export", "commits")}

    def accept_prop(self, runes=None):
        """Increment propagation count using the supplied runes and current capture context."""
        return logger.increment_propagation_detonated(logger.scan_context(),
            runes=["Death", "Rebirth"] if runes is None else runes)

    def advance(self):
        """Save a chain part and complete the chain to advance the active expedition."""
        logger.commit_chain_draft([{"rune1": "Death", "rune2": "Rebirth"}], logger.scan_context())
        return logger.complete_chain(logger.scan_context())

    def test_pending_remnant_logs_original_expedition_after_chain_advance(self):
        """Verify a reserved remnant saves to its original expedition after independent propagation advances."""
        result = self.opened()
        pending = dict(logger.get_state()["ocr_pending"])
        self.assertEqual(self.accept_prop()["detonated"], 1)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.advance()["next_expedition_id"], "M0001-E02")
        self.assertEqual(self.accept_prop(["Power"])["expedition_id"], "M0001-E02")
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        logger.validate_remnant_context(result)
        recipes = self.recipes()
        saved = logger.commit_remnant(recipes[0], recipes[1], 26)
        self.assertEqual((saved["remnant_id"], saved["expedition_id"]), ("R0001", "M0001-E01"))
        state = logger.get_state()
        self.assertEqual((state["current_expedition_id"], state["detonated"]), ("M0001-E02", 1))
        self.assertIsNone(state["ocr_pending"])
        with logger._connect() as db:
            remnant = db.execute("SELECT * FROM commits WHERE kind='Remnant'").fetchone()
            self.assertEqual((remnant["map_id"], remnant["expedition_id"]), ("M0001", "M0001-E01"))
            self.assertEqual(json.loads(remnant["snapshot_json"])["expedition"], 1)
            exports = [json.loads(row[0]) for row in db.execute("SELECT row_json FROM new_export WHERE remnant_id='R0001'")]
            self.assertTrue(exports)
            self.assertTrue(all(row[22] == "M0001" and row[31:33] == [1, "M0001-E01"] for row in exports))
            counts = dict(db.execute("SELECT expedition_id,detonated FROM expeditions"))
        self.assertEqual(counts, {"M0001-E01": 1, "M0001-E02": 1})

    def test_delayed_remnant_result_can_bind_to_a_committed_original_chain(self):
        """Verify a delayed remnant can reserve its original expedition after that chain is completed."""
        capture = logger.scan_context()
        self.advance()
        logger.validate_remnant_context(capture)
        pending = logger.assign_ocr_id("opened", "M0001", capture["_scan_generation"],
                                       capture["_capture_expedition"])
        self.assertEqual(pending["expedition_id"], "M0001-E01")
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_plain_expedition_setting_change_cannot_rebind_an_unreserved_delayed_result(self):
        """Verify an ordinary expedition settings change cannot rebind an unreserved delayed remnant."""
        capture = logger.scan_context()
        logger.save_settings({"expedition": 2})
        before = self.records()
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            logger.validate_remnant_context(capture)
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            logger.assign_ocr_id("opened", "M0001", capture["_scan_generation"], 1)
        self.assertEqual(self.records(), before)

    def test_generic_scan_context_remains_strict_after_chain_advance(self):
        """Verify generic scan context rejects chain advancement while remnant context permits its original binding."""
        currency_capture = logger.scan_context()
        self.advance()
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            logger.validate_scan_context(currency_capture)
        logger.validate_remnant_context(currency_capture)

    def test_new_expedition_capture_cannot_consume_the_older_pending_token(self):
        """Verify a new expedition capture cannot consume the older remnant reservation."""
        result = self.opened()
        self.advance()
        current = logger.scan_context()
        before = self.records()
        with self.assertRaisesRegex(ValueError, "different expedition"):
            logger.assign_ocr_id("opened", "M0001", current["_scan_generation"], 2)
        self.assertEqual(self.records(), before)
        logger.validate_remnant_context(result)

    def test_discarding_remnant_does_not_discard_the_independent_propagation_count(self):
        """Verify discarding a remnant leaves independent propagation counts intact."""
        result = self.opened()
        self.accept_prop()
        self.advance()
        self.accept_prop(["Power"])
        logger.discard_ocr_id()
        self.assertEqual(logger.get_state()["detonated"], 1)
        with self.assertRaisesRegex(ValueError, "saved or discarded"):
            logger.validate_remnant_context(result)
        self.assertEqual(self.accept_prop(["Rage"])["detonated"], 2)

    def test_reset_or_map_change_invalidates_original_remnant_context(self):
        """Verify map end, map change and session reset invalidate the original remnant capture."""
        result = self.opened()
        self.advance()
        logger.finish_map(0, 0, 0)
        with self.assertRaisesRegex(ValueError, "map ended"):
            logger.validate_remnant_context(result)
        logger.start_map()
        with self.assertRaisesRegex(ValueError, "map changed"):
            logger.validate_remnant_context(result)
        logger.clear_export_and_reset_ids()
        logger.start_map()
        before = self.records()
        with self.assertRaisesRegex(ValueError, "previous session"):
            logger.validate_remnant_context(result)
        with self.assertRaisesRegex(ValueError, "previous session"):
            logger.assign_ocr_id("opened", "M0001", result["_scan_generation"], 1)
        self.assertEqual(self.records(), before)

    def stage(self):
        """Read the highest-socket family-3 seed stage for batch fixtures."""
        with logger._connect() as db:
            return dict(db.execute("SELECT * FROM seed_states WHERE family=3 ORDER BY sockets DESC LIMIT 1").fetchone())

    def seed_result(self, stage, count=2):
        """Build a reserved seed capture with the requested number of identical recognized remnants."""
        reading = {"sockets": stage["sockets"], "seed_slot": stage["seed_slot"],
                   "seed_rune": stage["seed_rune"], "family": "Family 3", "candidates": [3],
                   "can_commit": True, "rewards": json.loads(stage["rewards_json"])}
        return {"remnants": [dict(reading) for _ in range(count)],
                **logger.scan_context(), **logger.assign_ocr_id("seed")}

    def selection(self, stage, index=0):
        """Build an explicit family-3 seed selection for the requested capture row."""
        return {"index": index, "family": 3, "sockets": stage["sockets"],
                "seed_slot": stage["seed_slot"], "seed_rune": stage["seed_rune"]}

    def test_partial_seed_batch_retains_original_expedition_across_chain_advance(self):
        """Verify a partially saved seed batch retains its original expedition after chain advancement."""
        stage = self.stage()
        result = self.seed_result(stage)
        first = logger.commit_seed_batch(result, [self.selection(stage)])
        result["remnants"][0]["saved"] = first["saved"][0]
        result.update(first["pending"])
        result.update(first["context"])
        waiting = dict(logger.get_state()["ocr_pending"])
        self.advance()
        self.accept_prop(["Rage"])
        self.assertEqual(logger.get_state()["ocr_pending"], waiting)
        second = logger.commit_seed_batch(result, [self.selection(stage, 1)])
        self.assertEqual([first["saved"][0]["expedition_id"], second["saved"][0]["expedition_id"]],
                         ["M0001-E01", "M0001-E01"])
        self.assertEqual(second["saved"][0]["remnant_id"], "R0002")
        self.assertEqual(second["context"]["_capture_expedition"], 1)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_seed_batch_whole_capture_after_advance_does_not_switch_later_rows_to_active_chain(self):
        """Verify saving an entire earlier seed capture never switches later rows to the active expedition."""
        stage = self.stage()
        result = self.seed_result(stage)
        self.advance()
        saved = logger.commit_seed_batch(result, [self.selection(stage), self.selection(stage, 1)])
        self.assertEqual([row["expedition_id"] for row in saved["saved"]], ["M0001-E01", "M0001-E01"])
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_seed_batch_requires_complete_capture_context_even_when_token_matches(self):
        """Verify seed batches reject missing capture-context fields even when the reserved token matches."""
        stage = self.stage()
        result = self.seed_result(stage)
        before = self.records()
        for key in ("_scan_generation", "_capture_map_id", "_capture_map_pending", "_capture_expedition"):
            incomplete = {field: value for field, value in result.items() if field != key}
            with self.subTest(missing=key), self.assertRaises(ValueError):
                logger.commit_seed_batch(incomplete, [self.selection(stage)])
            self.assertEqual(self.records(), before)

    def test_scan_service_preserves_capture_when_chain_is_committed_during_ocr(self):
        """Verify scan service retains the original capture expedition when a chain completes during OCR."""
        capture = logger.scan_context()
        output = io.BytesIO()
        Image.new("RGB", (300, 200), (70, 80, 90)).save(output, format="PNG")

        def finish_scan(path):
            """Advance the chain during the mocked opened scan and return a reviewable original recipe."""
            self.advance()
            return {"opened_recipes": [{"recipe": self.recipes()[0]}], "status": "review"}

        with patch.object(service, "scan_opened", side_effect=finish_scan):
            result = service.dispatch("/api/scan?mode=opened", {
                "image": base64.b64encode(output.getvalue()).decode("ascii"),
                "map_id": "M0001", "scan_context": capture,
                "scan_generation": capture["_scan_generation"]})
        self.assertEqual((result["_capture_expedition"], result["expedition_id"]), (1, "M0001-E01"))
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")


if __name__ == "__main__":
    unittest.main()
