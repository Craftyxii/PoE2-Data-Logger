"""Map-only cumulative kill snapshots select the latest reading without adding history."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from PoE2_Data_Logger.core import kill_counts, logger_store as logger, store


class MapKillReadingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='poe2-map-kills-')
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.directory.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.directory.cleanup()

    def save(self, counts, context=None):
        return kill_counts.save_snapshot(
            {**dict(zip(kill_counts.CATEGORIES, counts)), 'complete': True},
            context=context or kill_counts.capture_context('kills'), request_id=str(uuid.uuid4()),
            source_mode='kills', scope='map_final')

    def rows(self):
        return list(csv.DictReader(io.StringIO(kill_counts.export_csv().decode('utf-8-sig'))))

    def current(self):
        return [row for row in self.rows() if row['Current Map Kill Reading'] == 'True']

    def test_ten_then_twenty_selects_twenty_and_retains_both_snapshots(self):
        logger.save_kills(1, 2, 3, unique=4)
        with logger._connect() as db:
            manual = [tuple(row) for row in db.execute('SELECT * FROM maps')]
        first = self.save((10, 0, 0, 0))
        second = self.save((20, 0, 0, 0))
        self.assertNotEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertEqual([row['Total Kills (Observed)'] for row in self.rows()], ['10', '20'])
        self.assertEqual([row['Total Kills (Observed)'] for row in self.current()], ['20'])
        self.assertTrue(all(row['Expedition ID'] == '' for row in self.rows()))
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM maps')], manual)
        self.assertEqual((logger.get_state()['kills'], logger.get_state()['unique_kills']), ([1, 2, 3], 4))

    def test_all_four_categories_use_latest_values_not_sums_or_maxima(self):
        self.save((10, 20, 30, 4))
        self.save((15, 8, 12, 2))
        row = self.current()[0]
        self.assertEqual([row[f'{name} Kills (Observed)'] for name in ('Normal', 'Magic', 'Rare', 'Unique')],
                         ['15', '8', '12', '2'])
        self.assertEqual(row['Total Kills (Observed)'], '37')

    def test_capture_and_save_have_no_expedition_or_chain_revision_dependency(self):
        logger.commit_chain('Death')
        with patch.object(logger, '_chain_revision', side_effect=AssertionError('chain dependency')), \
             patch.object(logger, '_saved_chain_rows', side_effect=AssertionError('chain dependency')):
            context = kill_counts.capture_context('kills')
        self.assertFalse(any('expedition' in key or 'chain' in key for key in context))
        logger.complete_chain(logger.scan_context())
        saved = self.save((20, 1, 2, 0), context)
        self.assertEqual(saved['expedition_id'], '')
        self.assertEqual(logger.get_state()['settings']['expedition'], 2)
        self.assertEqual(json.loads(self.current()[0]['Capture Context JSON']), context)

    def test_zero_is_latest_and_undo_selects_previous_surviving_reading(self):
        self.save((20, 0, 0, 0))
        self.save((0, 0, 0, 0))
        self.assertEqual(self.current()[0]['Total Kills (Observed)'], '0')
        logger.undo_last_action()
        self.assertEqual(self.current()[0]['Total Kills (Observed)'], '20')
        self.assertEqual(len(self.rows()), 1)

    def test_selection_is_independent_for_each_map(self):
        self.save((10, 0, 0, 0))
        logger.finish_map(1, 2, 3, unique=4)
        logger.start_map()
        self.save((20, 0, 0, 0))
        self.assertEqual({row['Map ID']: row['Total Kills (Observed)'] for row in self.current()},
                         {'M0001': '10', 'M0002': '20'})

    def test_old_session_cannot_rebind_to_reused_map_id(self):
        context = kill_counts.capture_context('kills')
        logger.clear_export_and_reset_ids()
        logger.start_map()
        with self.assertRaisesRegex(ValueError, 'map or session changed'):
            self.save((20, 0, 0, 0), context)
        self.assertEqual(self.rows(), [])
