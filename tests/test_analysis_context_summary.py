"""Context-only expansion retains data sheets and explains real catalog totals and perks."""

from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PoE2_Data_Logger.core import analysis_context, atlas_catalog, logger_store as logger, store, workbook_export


class AnalysisContextSummaryTests(unittest.TestCase):
    """Use actual bundled references and saved maps, rather than invented game-effect formulas."""

    def setUp(self):
        """Create a disposable profile and preserve the selected storage root."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-context-summary-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        """Close all per-operation handles before restoring the selected profile."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def facts(self, section):
        """Read ordinary SQLite Context facts without application-only database functions."""
        with logger._connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT guidance FROM analysis_context_complete WHERE section=? ORDER BY key", (section,))]

    def allocate(self, nodes, choices=None):
        """Save a real user-selected Atlas configuration through the public validation path."""
        logger.save_atlas_settings({"catalog_version": atlas_catalog.catalog()["version"],
            "allocated": list(nodes), "choices": choices or {}, "gear_item_rarity": 75})

    def test_all_master_perk_functions_are_present_and_master_is_not_part_of_atlas_id(self):
        """Describe every actual perk while linking active selections to maps rather than the Atlas hash."""
        with logger._connect() as db:
            expected = {(r['master'], r['name']): (r['tier'], r['effect'])
                        for r in db.execute("SELECT * FROM master_perks")}
        found = {(r['master'], r['name']): (r['tier'], r['effect']) for r in self.facts('Atlas master perk')}
        self.assertEqual(found, expected)
        self.assertEqual(len(found), 36)
        config = logger.get_state()['settings']
        selections = dict(config['master_selections'])
        selections['Jado'] = ['Unexpected Missions', 'None', 'None', 'None']
        logger.save_settings({'atlas_master': 'Jado', 'master_selections': selections, 'map_mods': 3})
        logger.start_map()
        logger.finish_map(0, 0, 0, 0, unique=0)
        logger.save_settings({'atlas_master': 'Hilda'})
        logger.start_map()
        maps = {r['map_id']: r for r in self.facts('Map settings')}
        self.assertEqual(maps['M0001']['atlas_setup_id'], maps['M0002']['atlas_setup_id'])
        self.assertEqual(maps['M0001']['master'], 'Jado')
        self.assertIn('Unexpected Missions', maps['M0001']['perks'])
        self.assertEqual((maps['M0001']['base_map_mods'], maps['M0001']['master_adds_mod'],
                          maps['M0001']['map_mods']), (3, 1, 4))
        self.assertEqual(maps['M0002']['master'], 'Hilda')
        self.assertEqual(maps['M0002']['master_adds_mod'], 0)

    def test_real_atlas_coefficients_have_verified_units_and_preserved_conditions(self):
        """Render exact-source sums with their true scales, without evaluating conditional game effects."""
        nodes = atlas_catalog.catalog()['nodes']
        stats = ('map_item_drop_rarity_+%', 'map_monster_potency_+%',
                 'map_monster_dropped_item_rarity_permyriad_if_possessed',
                 'map_incursion_room_temple_bonuses_+%_for_1_room_per_type')
        selected = set()
        choices = {}
        for stat in stats:
            matches = [(key, node) for key, node in nodes.items()
                       if node.get('allocatable') and stat in node.get('stats', {})]
            if matches:
                selected.update(key for key, _ in matches[:2])
            else:
                key, option = next((key, option) for key, node in nodes.items()
                                   for option in node['choices'] if stat in option['stats'])
                selected.add(key)
                choices[key] = option['id']
        cap_node, cap_choice = next((key, option) for key, node in nodes.items() for option in node['choices']
            if 'map_abyss_monster_potency_+%_per_chasm_closed' in option['stats'])
        selected.add(cap_node)
        choices[cap_node] = cap_choice['id']
        self.allocate(selected, choices)
        totals = {r['stat_id']: r for r in self.facts('Atlas stat total')}
        for stat in stats:
            expected = sum(nodes[key]['stats'].get(stat, 0) for key in selected)
            expected += sum(option['stats'].get(stat, 0) for key, option_id in choices.items()
                            for option in nodes[key]['choices'] if option['id'] == option_id)
            with self.subTest(stat=stat):
                self.assertEqual(totals[stat]['raw_total'], expected)
                self.assertEqual(totals[stat]['unit'], '%')
                scale = 100 if stat in stats[2:] else 1
                self.assertEqual(totals[stat]['display_total'], expected / scale)
                self.assertIn('%', totals[stat]['effect'])
        self.assertIn('Possessed Monsters', totals[stats[2]]['effect'])
        self.assertIn('for each different Room', totals[stats[3]]['effect'])
        cap = totals['map_abyss_monster_potency_+%_per_chasm_closed']
        self.assertEqual(cap['display_total'], cap_choice['stats'][cap['stat_id']])
        self.assertIn('for each closed Pit, up to 100%', cap['effect'])
        self.assertIn('not an applied game total', cap['scope'])

    def test_the_other_three_workbook_sheets_are_byte_identical(self):
        """Expanding Context changes none of the existing data, Atlas or Scan History worksheets."""
        logger.save_settings({'effectiveness': 20})
        logger.start_map()
        logger.save_currency_snapshot('end', [{'name': 'Chaos Orb', 'quantity': 3}])

        def static_context(*, _db):
            """Reproduce the former static-only Context projection on the same snapshot."""
            output = io.StringIO(newline='')
            writer = csv.writer(output)
            writer.writerow(analysis_context.HEADERS)
            writer.writerows(_db.execute('SELECT section,topic,guidance,context_version '
                                         'FROM analysis_context ORDER BY position,key'))
            return output.getvalue().encode('utf-8-sig')

        with patch.object(logger, 'export_analysis_context_csv', side_effect=static_context):
            before = workbook_export.export_xlsx()
        after = workbook_export.export_xlsx()
        with ZipFile(io.BytesIO(before)) as old, ZipFile(io.BytesIO(after)) as new:
            for index in (1, 2, 3):
                path = f'xl/worksheets/sheet{index}.xml'
                self.assertEqual(new.read(path), old.read(path), path)
            self.assertNotEqual(new.read('xl/worksheets/sheet4.xml'), old.read('xl/worksheets/sheet4.xml'))

    def test_live_totals_follow_undo_without_context_cache_or_extra_logging(self):
        """Undo changes summary facts immediately, while the guide and definitions remain present."""
        logger.start_map()
        logger.save_counts(10, 2, 3, 4, unique=1)
        first = {r['metric']: r for r in self.facts('Session stat total')}
        self.assertEqual(first['known_kills_total']['total'], 16)
        logger.undo_last_action()
        after = {r['metric']: r for r in self.facts('Session stat total')}
        self.assertIsNone(after['known_kills_total']['total'])
        self.assertEqual(after['normal_kills']['missing_maps'], 1)
        self.assertEqual(len(self.facts('Atlas master perk')), 36)
        # A serialized WAL-mode profile needs a filesystem-backed connection;
        # SQLite cannot open its WAL journal after deserializing into :memory:.
        backup = Path(self.temporary.name) / 'independent-context.sqlite3'
        backup.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(backup)) as independent:
            self.assertEqual(independent.execute('SELECT COUNT(*) FROM analysis_context_complete '
                                                 "WHERE section='Atlas master perk'").fetchone()[0], 36)


if __name__ == '__main__':
    unittest.main()
