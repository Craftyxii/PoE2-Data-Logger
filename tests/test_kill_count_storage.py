"""Independent observed kill stages never replace manual totals or require a chain."""

from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from PIL import Image

from PoE2_Data_Logger.core import kill_counts, logger_store as logger, store


class KillCountStorageTests(unittest.TestCase):
    """Exercise ownership, complete readings, provenance, retries and atomic lifecycle operations."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-kill-stage-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def reading(self, normal=100, magic=20, rare=3, unique=1, **extra):
        return {"normal": normal, "magic": magic, "rare": rare, "unique": unique,
                "complete": True, "raw_text": "100 20 3 1", "confidence": 99.5, **extra}

    def save(self, result=None, *, source_mode="", scope="map_stage", **options):
        context = options.pop("context", kill_counts.capture_context(source_mode))
        request_id = options.pop("request_id", str(uuid.uuid4()))
        return kill_counts.save_snapshot(self.reading() if result is None else result,
            context=context, request_id=request_id, source_mode=source_mode, scope=scope, **options)

    def rows(self, table):
        with logger._connect() as db:
            return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def records(self):
        return {table: self.rows(table) for table in (
            "maps", "map_unique_kills", "expeditions", "new_export", "commits", "meta",
            "kill_count_snapshots", "undo_actions")}

    def csv_rows(self):
        return list(csv.DictReader(io.StringIO(kill_counts.export_csv().decode("utf-8-sig"))))

    def png(self):
        output = io.BytesIO()
        Image.new("RGB", (16, 8), (73, 92, 111)).save(output, format="PNG")
        return output.getvalue()

    def test_stage_and_final_save_without_chain_and_preserve_manual_totals(self):
        logger.save_counts(800, 40, 8, 9, unique=2)
        before = {table: self.rows(table) for table in ("maps", "map_unique_kills", "expeditions", "new_export")}
        stage = self.save(scope="chain_stage")
        final = self.save(self.reading(120, 25, 4, 2), source_mode="kills", scope="map_final")
        self.assertEqual((stage["scope"], stage["expedition_id"]), ("map_stage", ""))
        self.assertEqual((final["scope"], final["expedition_id"]), ("map_final", ""))
        self.assertEqual({table: self.rows(table) for table in before}, before)
        state = logger.get_state()
        self.assertEqual((state["kills"], state["unique_kills"], state["detonated"], state["chain"]),
                         ([800, 40, 8], 2, 9, []))
        self.assertEqual(len(self.csv_rows()), 2)

    def test_two_chain_stages_and_final_keep_independent_observations(self):
        logger.commit_chain("Rage")
        first = self.save(self.reading(100, 20, 3, 1), source_mode="propagation", scope="chain_stage")
        logger.complete_chain(logger.scan_context())
        logger.commit_chain("Time")
        second = self.save(self.reading(150, 30, 5, 2), scope="chain_stage")
        final = self.save(self.reading(200, 45, 8, 3), source_mode="kills", scope="map_final")
        self.assertEqual([first["expedition_id"], second["expedition_id"], final["expedition_id"]],
                         ["M0001-E01", "M0001-E02", ""])
        self.assertEqual([row["Normal Kills (Observed)"] for row in self.csv_rows()], ["100", "150", "200"])
        self.assertEqual(logger.get_state()["kills"], [None, None, None])

    def test_default_expedition_is_not_chain_but_remnant_capture_is_associated(self):
        self.assertFalse(kill_counts.capture_context()["_capture_chain_associated"])
        before = self.rows("expeditions")
        associated = self.save(source_mode="remnant", scope="chain_stage")
        self.assertEqual(associated["expedition_id"], "M0001-E01")
        self.assertEqual(self.rows("expeditions"), before)
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertEqual(logger.get_state()["next_remnant_id"], "R0001")

    def test_zeros_are_saved_and_incomplete_missing_or_invalid_values_never_mutate(self):
        zero = self.save(self.reading(0, 0, 0, 0))
        self.assertEqual((zero["normal"], zero["unique"], zero["total"]), (0, 0, 0))
        before = self.records()
        invalid = [self.reading(complete=False), self.reading(complete=1)]
        for category in kill_counts.CATEGORIES:
            for value in (None, -1, True, "5", 1.5, 2**63):
                invalid.append(self.reading(**{category: value}))
            missing = self.reading()
            missing.pop(category)
            invalid.append(missing)
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.save(result)
            self.assertEqual(self.records(), before)

    def test_request_replay_is_exact_and_conflicting_payload_or_pixels_are_rejected(self):
        context = kill_counts.capture_context("kills")
        request_id = str(uuid.uuid4())
        raw = self.png()
        first = self.save(context=context, request_id=request_id, source_mode="kills", image_bytes=raw)
        before = self.records()
        replay = self.save(context=context, request_id=request_id, source_mode="kills", image_bytes=raw)
        self.assertTrue(replay["already_saved"])
        self.assertEqual({key: value for key, value in first.items() if key != "already_saved"},
                         {key: value for key, value in replay.items() if key != "already_saved"})
        self.assertEqual(self.records(), before)
        for changes in ({"result": self.reading(101)}, {"result": self.reading(raw_text="other pixels")},
                        {"image_bytes": None}, {"scope": "map_final"}):
            arguments = {"context": context, "request_id": request_id, "source_mode": "kills", "image_bytes": raw}
            arguments.update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "already used"):
                self.save(**arguments)
            self.assertEqual(self.records(), before)

    def test_parallel_callbacks_save_one_snapshot_commit_and_undo_action(self):
        context = kill_counts.capture_context()
        request_id = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.save(context=context, request_id=request_id), range(2)))
        self.assertEqual(sorted(row["already_saved"] for row in results), [False, True])
        self.assertEqual(len(self.rows("kill_count_snapshots")), 1)
        self.assertEqual(len(self.rows("commits")), 1)
        self.assertEqual(logger.undo_action(), "Kill count OCR")

    def test_no_active_map_and_changed_pending_marker_are_rejected_without_creating_activity(self):
        logger.clear_export_and_reset_ids()
        context = kill_counts.capture_context("kills")
        before = self.records()
        with self.assertRaisesRegex(ValueError, "Start a map"):
            self.save(context=context, source_mode="kills", scope="map_final")
        self.assertEqual(self.records(), before)
        logger.start_map()
        context = kill_counts.capture_context("kills")
        logger.finish_map(1, 2, 3, unique=4)
        before = self.records()
        with self.assertRaisesRegex(ValueError, "map or session changed"):
            self.save(context=context, source_mode="kills", scope="map_final")
        self.assertEqual(self.records(), before)

    def test_final_after_manual_finish_saves_same_map_without_changing_manual_state(self):
        logger.finish_map(11, 22, 33, unique=4)
        before = {table: self.rows(table) for table in ("maps", "map_unique_kills", "expeditions", "new_export")}
        context = kill_counts.capture_context("kills")
        self.assertTrue(context["_capture_map_pending"])
        saved = self.save(self.reading(100, 20, 3, 1), context=context, source_mode="kills", scope="map_final")
        self.assertEqual((saved["map_id"], saved["expedition_id"], saved["scope"]), ("M0001", "", "map_final"))
        self.assertEqual({table: self.rows(table) for table in before}, before)
        state = logger.get_state()
        self.assertEqual((state["kills"], state["unique_kills"], state["pending_new_map"], state["chain"]),
                         ([11, 22, 33], 4, True, []))
        for scope in ("map_stage", "chain_stage"):
            before_attempt = self.records()
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, "Start the next map"):
                self.save(context=context, source_mode="kills", scope=scope)
            self.assertEqual(self.records(), before_attempt)
        logger.start_map()
        before_attempt = self.records()
        with self.assertRaisesRegex(ValueError, "map or session changed"):
            self.save(context=context, source_mode="kills", scope="map_final")
        self.assertEqual(self.records(), before_attempt)

    def test_delayed_map_and_session_results_are_rejected(self):
        context = kill_counts.capture_context()
        logger.finish_map(1, 2, 3)
        logger.start_map()
        before = self.records()
        with self.assertRaisesRegex(ValueError, "map or session changed"):
            self.save(context=context)
        self.assertEqual(self.records(), before)
        context = kill_counts.capture_context()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        before = self.records()
        with self.assertRaisesRegex(ValueError, "map or session changed"):
            self.save(context=context)
        self.assertEqual(self.records(), before)

    def test_changed_selected_or_discarded_chain_rejects_new_callbacks(self):
        logger.commit_chain("Rage")
        context = kill_counts.capture_context("propagation")
        logger.save_settings({"expedition": 2})
        before = self.records()
        with self.assertRaisesRegex(ValueError, "captured chain changed"):
            self.save(context=context, source_mode="propagation", scope="chain_stage")
        self.assertEqual(self.records(), before)
        logger.save_settings({"expedition": 1})
        context = kill_counts.capture_context("propagation")
        logger.discard_chain(logger.scan_context())
        before = self.records()
        with self.assertRaisesRegex(ValueError, "captured chain changed"):
            self.save(context=context, source_mode="propagation", scope="chain_stage")
        self.assertEqual(self.records(), before)

    def test_replayed_saved_chain_callback_does_not_follow_new_expedition(self):
        context = kill_counts.capture_context("propagation")
        request_id = str(uuid.uuid4())
        first = self.save(context=context, request_id=request_id, source_mode="propagation", scope="chain_stage")
        logger.commit_chain("Rage")
        logger.complete_chain(logger.scan_context())
        replay = self.save(context=context, request_id=request_id, source_mode="propagation", scope="chain_stage")
        self.assertEqual((replay["snapshot_id"], replay["expedition_id"]), (first["snapshot_id"], "M0001-E01"))
        self.assertTrue(replay["already_saved"])
        self.assertEqual(len(self.rows("kill_count_snapshots")), 1)

    def test_evidence_raw_ocr_capture_time_and_backup_are_lossless(self):
        raw = self.png()
        context = kill_counts.capture_context("kills")
        result = self.reading(raw_text="skulls: 100 / 20 / 3 / 1", boxes=[[1, 2, 3, 4]])
        saved = self.save(result, context=context, source_mode="kills", scope="map_final", image_bytes=raw)
        row = self.rows("kill_count_snapshots")[0]
        self.assertEqual((row["image_png"], row["image_sha256"]), (raw, hashlib.sha256(raw).hexdigest()))
        self.assertEqual(json.loads(row["ocr_json"]), result)
        self.assertEqual(json.loads(row["context_json"]), context)
        self.assertEqual(row["captured_at"], context["captured_at"])
        backup_path = Path(self.tmp.name) / "kill-count-backup.sqlite3"
        backup_path.write_bytes(logger.backup_bytes())
        backup = sqlite3.connect(backup_path)
        try:
            self.assertEqual(backup.execute("SELECT image_png FROM kill_count_snapshots WHERE snapshot_id=?",
                                            (saved["snapshot_id"],)).fetchone()[0], raw)
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            backup.close()

    def test_restart_preserves_snapshot_and_idempotent_receipt(self):
        context = kill_counts.capture_context()
        request_id = str(uuid.uuid4())
        saved = self.save(context=context, request_id=request_id)
        before = kill_counts.export_csv()
        logger._READY = False
        logger.initialize()
        replay = self.save(context=context, request_id=request_id)
        self.assertEqual(replay["snapshot_id"], saved["snapshot_id"])
        self.assertTrue(replay["already_saved"])
        self.assertEqual(kill_counts.export_csv(), before)

    def test_undo_removes_snapshot_commit_and_evidence_without_replacing_manual_counts(self):
        logger.save_kills(9, 8, 7, unique=6)
        context = kill_counts.capture_context()
        saved = self.save(context=context, image_bytes=self.png())
        self.assertEqual(logger.undo_last_action()["action"], "Kill count OCR")
        self.assertEqual(self.rows("kill_count_snapshots"), [])
        self.assertNotIn(saved["scan_commit_number"], [row["number"] for row in self.rows("commits")])
        self.assertEqual((logger.get_state()["kills"], logger.get_state()["unique_kills"]), ([9, 8, 7], 6))
        with self.assertRaisesRegex(ValueError, "map or session changed"):
            self.save(context=context)

    def test_failure_after_commit_allocation_rolls_back_entire_snapshot_action(self):
        before = self.records()
        record = logger._record_commit
        def fail(*args, **kwargs):
            record(*args, **kwargs)
            raise RuntimeError("simulated commit failure")
        with patch.object(logger, "_record_commit", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "simulated commit failure"):
                self.save(image_bytes=self.png())
        self.assertEqual(self.records(), before)

    def test_map_discard_undo_and_full_reset_cover_snapshots(self):
        self.save(image_bytes=self.png())
        original = self.rows("kill_count_snapshots")
        logger.discard_current_map_logs(logger.scan_context())
        self.assertEqual(self.rows("kill_count_snapshots"), [])
        logger.undo_last_action()
        self.assertEqual(self.rows("kill_count_snapshots"), original)
        logger.clear_export_and_reset_ids()
        self.assertEqual(self.rows("kill_count_snapshots"), [])
        self.assertEqual(self.csv_rows(), [])

    def test_schema_upgrade_creates_empty_table_without_rewriting_existing_history(self):
        logger.save_kills(7, 6, 5, unique=4)
        before = {table: self.rows(table) for table in ("maps", "map_unique_kills", "commits", "new_export")}
        with logger._connect() as db:
            db.execute("DROP TABLE kill_count_snapshots")
        logger._READY = False
        logger.initialize()
        self.assertEqual(self.rows("kill_count_snapshots"), [])
        self.assertEqual({table: self.rows(table) for table in before}, before)

    def test_csv_labels_keep_stage_and_final_values_separate_from_manual_map_fields(self):
        self.save(self.reading(0, 2, 3, 4), source_mode="kills", scope="map_final")
        row = self.csv_rows()[0]
        self.assertEqual(set(row), set(kill_counts.CSV_HEADERS))
        self.assertEqual((row["Kill Count Scope"], row["Normal Kills (Observed)"], row["Total Kills (Observed)"]),
                         ("map_final", "0", "9"))
        self.assertEqual(row["Count Interpretation"], "Observed counter totals; not increments")
        self.assertEqual(row["Expedition ID"], "")
        self.assertNotIn("image_png", row)
        self.assertEqual(json.loads(row["OCR JSON"])["unique"], 4)

    def test_invalid_request_context_source_scope_or_evidence_never_mutate(self):
        before = self.records()
        for options in ({"request_id": "not-a-uuid"}, {"context": None}, {"scope": []},
                        {"context": {"source_mode": "kills"}}, {"image_bytes": b"not a PNG"},
                        {"context": kill_counts.capture_context("kills"), "source_mode": "ritual"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.save(**options)
            self.assertEqual(self.records(), before)

    def test_saved_observations_are_map_activity(self):
        self.save()
        with logger._connect() as db:
            self.assertTrue(logger._map_has_activity(db, "M0001"))

    def test_evidence_byte_and_dimension_limits_reject_before_any_database_write(self):
        before = self.records()
        with patch.object(kill_counts, "MAX_IMAGE_BYTES", 8):
            with self.assertRaisesRegex(ValueError, "16 MiB"):
                self.save(image_bytes=self.png())
        with patch.object(store, "MAX_IMAGE_PIXELS", 16):
            with self.assertRaisesRegex(ValueError, "pixel limit"):
                self.save(image_bytes=self.png())
        self.assertEqual(self.records(), before)


if __name__ == "__main__":
    unittest.main()
