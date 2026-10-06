from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class CoreRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-core-regressions-")
        self.previous_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous_data_dir
        logger._READY = False
        self.tmp.cleanup()

    def stage(self):
        with logger._connect() as db:
            return dict(db.execute("SELECT * FROM seed_states WHERE family=3 "
                                   "ORDER BY sockets DESC LIMIT 1").fetchone())

    def seed_result(self, stage, count=1):
        reading = {"sockets": stage["sockets"], "seed_slot": stage["seed_slot"],
                   "seed_rune": stage["seed_rune"], "family": "Family 3", "candidates": [3],
                   "can_commit": True, "rewards": json.loads(stage["rewards_json"])}
        return {"remnants": [dict(reading) for _ in range(count)],
                **logger.scan_context(), **logger.assign_ocr_id("seed")}

    def selection(self, stage, index=0):
        return {"index": index, "family": 3, "sockets": stage["sockets"],
                "seed_slot": stage["seed_slot"], "seed_rune": stage["seed_rune"]}

    def commit_recipes(self, number):
        with logger._connect() as db:
            details = json.loads(db.execute("SELECT details_json FROM commits WHERE number=?",
                                            (number,)).fetchone()[0])
        return [row["recipe"] for row in details["recipes"]]

    def test_local_stage_review_and_commit_include_only_mapped_rewards(self):
        stage = self.stage()
        rewards = ["Chaos Orb", "Sovereign Alloy"]
        logger.save_seed_state({**stage, "rewards": rewards})
        stage = self.stage()
        window = LoggerWindow()
        window._poll.stop()
        try:
            window.seed_sockets.setText(str(stage["sockets"]))
            window.seed_slot.setText(stage["seed_slot"])
            window.seed_rune.setText(stage["seed_rune"])
            window.update_seed_candidates(3)
            self.assertEqual(set(window.seed_rewards.text().split(" · ")), set(rewards))
            logger.save_settings({"auto_commit": True})
            for automatic in (False, True):
                with self.subTest(automatic=automatic):
                    saved = logger.commit_seed_batch(self.seed_result(stage), [self.selection(stage)],
                                                     automatic=automatic)
                    self.assertEqual(saved["saved"][0]["recipes"], 2)
                    self.assertEqual(self.commit_recipes(saved["scan_commit_number"]),
                                     ["Sovereign Alloy", "Chaos Orb"])
                    exported = list(csv.DictReader(io.StringIO(logger.export_csv().decode("utf-8-sig"))))
                    rows = [row for row in exported if row["Scan Commit #"] == str(saved["scan_commit_number"])]
                    self.assertEqual([row["Matched Recipe"] for row in rows], ["Sovereign Alloy", "Chaos Orb"])
        finally:
            window.close()
            window.pool.shutdown(wait=True, cancel_futures=True)
            self.app.processEvents()

    def test_builtin_seed_stage_rewards_keep_the_full_family_order(self):
        with logger._connect() as db:
            for stage in db.execute("SELECT s.* FROM seed_states s JOIN families f ON f.id=s.family WHERE f.valid=1"):
                rewards = json.loads(stage["rewards_json"])
                if not rewards:
                    continue
                with self.subTest(family=stage["family"], sockets=stage["sockets"]):
                    family = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=?",
                                                   (stage["family"],)).fetchone()[0])
                    expected = [name for name in family if db.execute("SELECT sockets FROM recipes WHERE name=?",
                                                                      (name,)).fetchone()[0] <= stage["sockets"]]
                    rows = logger._seed_recipes(db, stage["family"], stage["sockets"], rewards)
                    self.assertEqual([row["recipe"] for row in rows], expected)
        stage = self.stage()
        saved = logger.commit_seed_batch(self.seed_result(stage), [self.selection(stage)])
        self.assertEqual(set(self.commit_recipes(saved["scan_commit_number"])),
                         set(json.loads(stage["rewards_json"])))

    def test_partial_seed_approval_updates_context_after_implicit_map_start(self):
        logger.finish_map(12, 3, 1, 2)
        stage = self.stage()
        result = self.seed_result(stage, count=2)
        first = logger.commit_seed_batch(result, [self.selection(stage)])
        self.assertEqual(first["context"], logger.scan_context())
        self.assertFalse(first["context"]["_capture_map_pending"])
        result["remnants"][0]["saved"] = first["saved"][0]
        result.update(first["pending"])
        result.update(first["context"])
        second = logger.commit_seed_batch(result, [self.selection(stage, index=1)])
        self.assertEqual([first["saved"][0]["map_id"], second["saved"][0]["map_id"]], ["M0002", "M0002"])
        self.assertEqual(second["saved"][0]["remnant_id"], "R0002")
        self.assertIsNone(second["pending"])

    def test_new_map_and_reference_edits_preserve_prior_records_and_settings(self):
        logger.save_settings({"tier": 16, "waystone": 87})
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 10}])
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 15}])
        logger.save_ritual_page([{"name": "Saved reward", "quantity": 2}])
        stage = self.stage()
        logger.commit_seed_batch(self.seed_result(stage), [self.selection(stage)])
        logger.finish_map(12, 3, 1, 2)
        with logger._connect() as db:
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} WHERE map_id='M0001'")]
                      for table in ("maps", "new_export", "currency_snapshots", "ritual_pages", "commits")}
        logger.start_map()
        logger.save_settings({"waystone": 20})
        logger.save_seed_state({**stage, "rewards": ["Chaos Orb"]})
        with logger._connect() as db:
            after = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} WHERE map_id='M0001'")]
                     for table in before}
        self.assertEqual(after, before)
        self.assertEqual(logger.currency_for_map("M0001")["net"], {"Chaos Orb": 5})
        self.assertEqual(logger.currency_for_map("M0002")["start"], {})
        self.assertEqual(logger.currency_for_map("M0002")["end"], {})
        self.assertEqual(logger.ritual_pages_for_map("M0001")[0]["items"][0]["name"], "Saved reward")
        self.assertTrue(all(json.loads(row[-2])["waystone"] == 87 for row in before["commits"]))

    def test_concurrent_partial_settings_updates_both_survive(self):
        first_read = threading.Event()
        second_saved = threading.Event()
        original_validate = logger._validate_settings

        def pause_first(db, data):
            settings = original_validate(db, data)
            if data == {"waystone": 87}:
                first_read.set()
                second_saved.wait(1)
            return settings

        def save_second():
            result = logger.save_settings({"biome": "Forest"})
            second_saved.set()
            return result

        with patch.object(logger, "_validate_settings", side_effect=pause_first), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(logger.save_settings, {"waystone": 87})
            self.assertTrue(first_read.wait(5))
            second = pool.submit(save_second)
            first.result(timeout=10)
            second.result(timeout=10)
        settings = logger.get_state()["settings"]
        self.assertEqual((settings["waystone"], settings["biome"]), (87, "Forest"))

    def test_concurrent_tablet_scans_receive_distinct_slots_and_history(self):
        first_read = threading.Event()
        second_saved = threading.Event()
        original_validate = logger._validate_settings

        def pause_first(db, data):
            settings = original_validate(db, data)
            if data["tablet_affixes"][0]["value"] == 10 and not first_read.is_set():
                first_read.set()
                second_saved.wait(1)
            return settings

        def save_tablet(amount):
            return logger.save_scanned_tablet([{"affix": "Chance to Contain Essences", "value": amount}],
                                             [f"{amount}% chance to contain Essences"])

        def save_second():
            number = save_tablet(20)
            second_saved.set()
            return number

        with patch.object(logger, "_validate_settings", side_effect=pause_first), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(save_tablet, 10)
            self.assertTrue(first_read.wait(5))
            second = pool.submit(save_second)
            self.assertEqual((first.result(timeout=10), second.result(timeout=10)), (1, 2))
        self.assertEqual(logger.tablet_next_slot(), 3)
        self.assertEqual([pair["value"] for pair in logger.get_state()["settings"]["tablet_affixes"][::4]],
                         [10, 20, None, None])
        with logger._connect() as db:
            commits = list(db.execute("SELECT reference,snapshot_json FROM commits WHERE kind='Tablet config' ORDER BY number"))
        self.assertEqual([row[0] for row in commits], ["Tablet 1", "Tablet 2"])
        self.assertIsNone(json.loads(commits[0][1])["tablet_affixes"][4]["value"])
        self.assertEqual(json.loads(commits[1][1])["tablet_affixes"][4]["value"], 20)

    def test_tablet_commit_failure_rolls_back_settings_and_slot(self):
        before = logger.get_state()["settings"]
        with patch.object(logger, "_record_commit", side_effect=ValueError("Commit failed")):
            with self.assertRaisesRegex(ValueError, "Commit failed"):
                logger.save_scanned_tablet([{"affix": "Chance to Contain Essences", "value": 10}],
                                          ["10% chance to contain Essences"])
        self.assertEqual(logger.get_state()["settings"], before)
        self.assertEqual(logger.tablet_next_slot(), 1)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)


if __name__ == "__main__":
    unittest.main()
