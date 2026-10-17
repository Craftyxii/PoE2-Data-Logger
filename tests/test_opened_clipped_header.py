"""Real-pixel clipped-header recovery retains independent geometry and save guards."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from PoE2_Data_Logger.core import auto_commit, logger_store as logger, store
from PoE2_Data_Logger.ocr import opened_scan


CAPTURE = Path(__file__).resolve().parent / 'fixtures/propagation_prefill/family57-opened.jpg'


class OpenedClippedHeaderTests(unittest.TestCase):
    """Use real fixture pixels and disposable profiles for the reported review hold."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='poe2-clipped-header-')
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.save_settings({'auto_commit': True})

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def clipped_panel(self, delta=107):
        """Pin the original heading while scrolling only existing reward pixels.

        These controlled derivatives retain five readable Level 20 rows; the
        original's clipped Pile label is never reconstructed or invented.
        """
        with Image.open(CAPTURE) as source:
            source = source.convert('RGB')
        image = Image.new('RGB', (575, 643 - delta))
        image.paste(source.crop((0, 160, 575, 263)), (0, 0))
        image.paste(source.crop((0, 263 + delta, 575, 803)), (0, 103))
        return image

    def captured(self, reading):
        """Bind a real scanner reading to its current disposable map reservation."""
        return {**copy.deepcopy(reading), **logger.scan_context(), **logger.assign_ocr_id('opened')}

    def assert_held_without_remnant(self, reading):
        """Exercise the production automatic writer, not only its preliminary gate."""
        self.assertFalse(auto_commit.candidate(reading)['ready'], reading)
        saved = auto_commit.commit(self.captured(reading))
        self.assertFalse(saved['committed'], saved)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM commits WHERE kind='Remnant'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM new_export WHERE remnant_id IS NOT NULL").fetchone()[0], 0)

    def erase(self, image, box, background_x=300):
        """Replace a tested icon region with real blank parchment from its own row."""
        x1, y1, x2, y2 = box
        result = image.copy()
        result.paste(image.crop((background_x, y1, background_x + x2 - x1, y2)), (x1, y1))
        return result

    def test_real_clipped_header_uses_visible_rows_and_saves_required_family_suffix(self):
        reading = opened_scan.scan_opened(self.clipped_panel())
        self.assertEqual(reading['first_recipe'], 'Verisium Manifestations (Level 20)', reading)
        self.assertEqual(reading['next_recipe'], 'Powered by Verisium (Level 20)', reading)
        self.assertEqual((reading['family'], reading['candidates']), ('Family 57', [57]), reading)
        self.assertEqual(len(reading['opened_recipes']), 5, reading)
        self.assertEqual((reading['sockets'], reading['recipe_sockets'], reading['socket_source']),
                         (5, 5, 'Recipe DB'), reading)
        self.assertEqual(reading['first_line_gap'], 19, reading)
        self.assertIsInstance(reading.get('clipped_first_row'), dict, reading)
        proof = reading['clipped_first_row']
        self.assertEqual((proof['sockets'], proof['first_line_gap']), (5, 19))
        self.assertEqual(proof['supporting_rows'], [
            {'index': 1, 'recipe': 'Powered by Verisium (Level 20)', 'sockets': 5},
            {'index': 2, 'recipe': 'Remnants of Kalguur (Level 20)', 'sockets': 5},
        ])
        self.assertTrue(auto_commit.candidate(reading)['ready'], reading)
        saved = auto_commit.commit(self.captured(reading))
        self.assertTrue(saved['committed'], saved)
        self.assertEqual((saved['family'], saved['recipes']), (57, 12), saved)
        with logger._connect() as db:
            stored = json.loads(db.execute("SELECT details_json FROM commits WHERE kind='Remnant'").fetchone()[0])
            exported = db.execute("SELECT count(*) FROM new_export WHERE remnant_id=?", (saved['remnant_id'],)).fetchone()[0]
            family = json.loads(db.execute('SELECT recipes_json FROM families WHERE id=57').fetchone()[0])
        self.assertEqual([row['recipe'] for row in stored['recipes']], family[1:])
        self.assertEqual(exported, 12)

    def test_erased_visible_first_bar_cannot_be_invented_from_later_rows(self):
        image = self.erase(self.clipped_panel(), (40, 103, 253, 120))
        reading = opened_scan.scan_opened(image)
        self.assertEqual(reading['first_recipe'], 'Verisium Manifestations (Level 20)', reading)
        self.assertNotIn('clipped_first_row', reading)
        self.assert_held_without_remnant(reading)

    def test_extra_first_bar_socket_cannot_be_replaced_by_the_catalog_count(self):
        image = self.clipped_panel()
        # A real occupied strip copied onto socket six leaves the text intact.
        image.paste(image.crop((206, 103, 232, 120)), (247, 103))
        reading = opened_scan.scan_opened(image)
        self.assertEqual(reading['first_recipe'], 'Verisium Manifestations (Level 20)', reading)
        self.assertNotIn('clipped_first_row', reading)
        self.assert_held_without_remnant(reading)

    def test_one_visible_witness_does_not_authorize_clipped_first_row(self):
        image = self.clipped_panel().crop((0, 0, 575, 238))
        reading = opened_scan.scan_opened(image)
        self.assertEqual(reading['first_recipe'], 'Verisium Manifestations (Level 20)', reading)
        self.assertEqual(len(reading['opened_recipes']), 2, reading)
        self.assertNotIn('clipped_first_row', reading)
        self.assert_held_without_remnant(reading)

    def test_missing_captured_heading_does_not_authorize_recovery(self):
        image = self.clipped_panel()
        reading = opened_scan.scan_opened(image.crop((0, 103, 575, image.height)))
        self.assertFalse(reading.get('header_verified'), reading)
        self.assertNotIn('clipped_first_row', reading)
        self.assert_held_without_remnant(reading)

    def test_skipped_recipe_row_does_not_authorize_recovery(self):
        image = self.clipped_panel()
        # Remove the whole Powered row without changing the following labels.
        skipped = Image.new('RGB', (image.width, image.height - 81))
        skipped.paste(image.crop((0, 0, 575, 157)), (0, 0))
        skipped.paste(image.crop((0, 238, 575, image.height)), (0, 157))
        reading = opened_scan.scan_opened(skipped)
        self.assertEqual(reading['first_recipe'], 'Verisium Manifestations (Level 20)', reading)
        self.assertEqual(reading['next_recipe'], 'Remnants of Kalguur (Level 20)', reading)
        self.assertNotIn('clipped_first_row', reading)
        self.assert_held_without_remnant(reading)

    def test_visible_witness_socket_conflict_stays_held(self):
        image = self.erase(self.clipped_panel(), (213, 158, 253, 201))
        reading = opened_scan.scan_opened(image)
        self.assertEqual(reading['first_recipe'], 'Verisium Manifestations (Level 20)', reading)
        self.assertEqual(reading['next_recipe'], 'Powered by Verisium (Level 20)', reading)
        self.assertNotIn('clipped_first_row', reading)
        self.assert_held_without_remnant(reading)

    def test_observed_first_bar_conflicts_keep_the_original_hold(self):
        for delta, observed in ((87, 4), (91, 7), (97, 10), (101, 9)):
            with self.subTest(scroll_pixels=delta):
                reading = opened_scan.scan_opened(self.clipped_panel(delta))
                self.assertEqual((reading['sockets'], reading['recipe_sockets']), (observed, 5), reading)
                self.assertEqual(reading['socket_source'], 'opened icons', reading)
                self.assertNotIn('clipped_first_row', reading)
                self.assertIn(f'Opened icons show {observed} sockets', reading['status'])
                self.assert_held_without_remnant(reading)

    def test_maximum_strictness_remains_manual(self):
        reading = opened_scan.scan_opened(self.clipped_panel(), strictness=100)
        self.assertEqual(reading['_ocr_strictness'], 100, reading)
        self.assert_held_without_remnant(reading)

    def test_weak_observed_confidence_cannot_be_bypassed_by_valid_evidence(self):
        original = opened_scan.scan_opened(self.clipped_panel())
        self.assertIn('clipped_first_row', original)
        for key, score in (('ocr_score', .79), ('match_score', .94)):
            with self.subTest(confidence=key):
                reading = copy.deepcopy(original)
                reading['opened_recipes'][2][key] = score
                self.assert_held_without_remnant(reading)

    def test_missing_or_corrupted_evidence_stays_held(self):
        original = opened_scan.scan_opened(self.clipped_panel())
        self.assertIn('clipped_first_row', original)
        cases = []
        missing = copy.deepcopy(original)
        missing.pop('clipped_first_row')
        cases.append(('missing', missing))
        for key, value in (('sockets', True), ('first_line_gap', 29), ('header_gap', 40),
                           ('header_gap', float('nan')), ('row_pitch', 101)):
            reading = copy.deepcopy(original)
            reading['clipped_first_row'][key] = value
            cases.append((f'{key}={value}', reading))
        wrong_recipe = copy.deepcopy(original)
        wrong_recipe['clipped_first_row']['supporting_rows'][0]['recipe'] = 'Grim Pillars (Level 20)'
        cases.append(('wrong witness recipe', wrong_recipe))
        duplicate = copy.deepcopy(original)
        duplicate['clipped_first_row']['supporting_rows'][1] = copy.deepcopy(
            duplicate['clipped_first_row']['supporting_rows'][0])
        cases.append(('same witness twice', duplicate))
        for name, reading in cases:
            with self.subTest(evidence=name):
                self.assert_held_without_remnant(reading)

    def test_current_catalog_drift_invalidates_saved_witness_evidence(self):
        reading = opened_scan.scan_opened(self.clipped_panel())
        self.assertTrue(auto_commit.candidate(reading)['ready'], reading)
        logger.save_recipe({'name': 'Powered by Verisium (Level 20)', 'sockets': 4,
                            'combo': 'Arcane + Earth + Ward + Vision'})
        self.assert_held_without_remnant(reading)

    def test_witness_with_different_recipe_stage_does_not_supply_first_count(self):
        logger.save_recipe({'name': 'Powered by Verisium (Level 20)', 'sockets': 4,
                            'combo': 'Arcane + Earth + Ward + Vision'})
        reading = opened_scan.scan_opened(self.clipped_panel())
        self.assertNotIn('clipped_first_row', reading)
        self.assert_held_without_remnant(reading)

    def test_recovery_keeps_auto_commit_toggle_and_capture_context_guards(self):
        reading = opened_scan.scan_opened(self.clipped_panel())
        self.assertTrue(auto_commit.candidate(reading)['ready'], reading)
        captured = self.captured(reading)
        logger.save_settings({'auto_commit': False})
        result = auto_commit.commit(captured)
        self.assertEqual(result, {'committed': False, 'reason': 'Auto-commit is off.'})
        logger.save_settings({'auto_commit': True})
        captured['_scan_generation'] -= 1
        result = auto_commit.commit(captured)
        self.assertFalse(result['committed'], result)
        self.assertIn('previous session', result['reason'])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM commits WHERE kind='Remnant'").fetchone()[0], 0)

    def test_unclipped_reading_preserves_existing_socket_provenance_without_recovery(self):
        reading = opened_scan.scan_opened(CAPTURE)
        self.assertNotIn('clipped_first_row', reading)
        self.assertEqual((reading['first_recipe'], reading['next_recipe']),
                         ('Thaumaturgic Flux (Level 19)', 'Verisium Manifestations (Level 20)'), reading)
        self.assertEqual((reading['sockets'], reading['recipe_sockets'], reading['socket_source']),
                         (5, 5, 'opened icons'), reading)
        self.assertEqual(reading['first_line_gap'], 49, reading)
        self.assertEqual(len(reading['opened_recipes']), 6, reading)
        self.assertTrue(auto_commit.candidate(reading)['ready'], reading)


if __name__ == '__main__':
    unittest.main()
