from __future__ import annotations

import shutil
from pathlib import Path
import tempfile
import unittest

from check_sources import load_sources

DATA = Path(__file__).resolve().parents[1] / 'data'


class ExchangeTests(unittest.TestCase):
    def test_example_is_readable(self):
        tables = load_sources(DATA)
        self.assertTrue(all(tables.values()))
        for row in tables['counts']:
            self.assertEqual(sum(int(row[g]) for g in ('high','middle','low','unknown')), int(row['inspected']))
            self.assertLessEqual(int(row['inspected']), int(row['eligible']))
        for edition in {r['edition'] for r in tables['baskets']}:
            self.assertAlmostEqual(sum(float(r['weight']) for r in tables['baskets'] if r['edition'] == edition), 1)
        self.assertTrue(any(r['fixed'] == '1' for r in tables['targets']))

    def test_duplicate_identity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / 'copies'
            shutil.copytree(DATA, duplicate)
            path = duplicate / 'counts.csv'
            rows = path.read_text(encoding='utf-8').splitlines()
            path.write_text('\n'.join(rows + [rows[1]]) + '\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '标识为空或重复'):
                load_sources(duplicate)
