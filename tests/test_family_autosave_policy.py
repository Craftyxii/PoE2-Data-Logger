"""User-selected family-only saving preserves OCR facts and transactional ownership."""

import copy
from pathlib import Path
import tempfile
import unittest

from PoE2_Data_Logger.core import auto_commit, logger_store as logger, store


class FamilyAutosavePolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='poe2-family-policy-')
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.directory.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        logger.save_settings({'auto_commit': True})
        self.reading = {
            'family': 'Family 50', 'candidates': [50], 'first_recipe': 'Perfect Regal Orb x3',
            'next_recipe': None, 'status': 'The opened socket count needs manual review.',
            'can_use': False, 'sockets': 3, 'recipe_sockets': 8,
            'socket_source': 'Recipe DB', 'first_line_gap': 0,
            'opened_recipes': [{'recipe': 'Perfect Regal Orb x3', 'ocr_score': .1, 'match_score': .1}],
            '_ocr_strictness': 100, **logger.scan_context(), **logger.assign_ocr_id('opened')}

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.directory.cleanup()

    def test_unique_family_saves_catalog_suffix_without_rewriting_ocr_evidence(self):
        before = copy.deepcopy(self.reading)
        self.assertFalse(auto_commit.candidate(self.reading)['ready'])
        self.assertTrue(auto_commit.family_candidate(self.reading)['ready'])
        saved = auto_commit.commit(self.reading)
        self.assertTrue(saved['committed'], saved)
        self.assertEqual(self.reading, before)
        self.assertEqual(logger.get_state()['counts']['new_rows'], 1)
        with logger._connect() as db:
            details = logger._load(db.execute("SELECT details_json FROM commits WHERE kind='Remnant'").fetchone()[0])
        self.assertEqual(details['recipes'][0]['sockets'], 8)
        self.assertEqual(details['recipes'][0]['recipe'], 'Perfect Regal Orb x3')

    def test_family_ambiguity_still_requires_selection(self):
        self.reading['candidates'] = [50, 55]
        self.assertFalse(auto_commit.commit(self.reading)['committed'])
        self.assertEqual(logger.get_state()['scan_commit_count'], 0)

    def test_first_recipe_must_identify_a_real_stage_in_the_family(self):
        self.reading['first_recipe'] = 'Not a catalog recipe'
        self.assertFalse(auto_commit.commit(self.reading)['committed'])
        self.assertEqual(logger.get_state()['scan_commit_count'], 0)

    def test_nonexistent_family_is_not_a_catalog_match(self):
        self.reading.update(family='Family 999999', candidates=[999999])
        self.assertFalse(auto_commit.commit(self.reading)['committed'])

    def test_conflicting_seed_family_remains_ambiguous(self):
        seed = {**self.reading, 'family': 'Family 55', 'candidates': [55]}
        self.assertFalse(auto_commit.commit(self.reading, seed)['committed'])

    def test_duplicate_attempt_and_auto_commit_off_still_require_approval(self):
        self.reading['_repeat_family_review'] = True
        self.assertFalse(auto_commit.commit(self.reading)['committed'])
        self.reading.pop('_repeat_family_review')
        logger.save_settings({'auto_commit': False})
        self.assertFalse(auto_commit.commit(self.reading)['committed'])

    def test_reset_does_not_rebind_unique_family_to_another_session(self):
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.save_settings({'auto_commit': True})
        logger.assign_ocr_id('opened')
        self.assertFalse(auto_commit.commit(self.reading)['committed'])
        self.assertEqual(logger.get_state()['scan_commit_count'], 0)
