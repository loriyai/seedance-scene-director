"""Active task and first-review diagnostics, using fictional isolated data."""
import copy
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import director as d
import scene_review as sr
from test_dependency_v2 import fixture
from test_scene_review import reviewed


class StylePhaseTests(unittest.TestCase):
    def test_followup_style_preserves_activity_and_history(self):
        with tempfile.TemporaryDirectory() as name:
            for scope in ('assets', 'workflow', 'screenplay'):
                for phase in ('range', 'storyboard', 'asset_choice', 'nodes_choice', 'followup', 'complete'):
                    state = {'revision': 1, 'phase': phase, 'delivery_contract': {'scope': scope},
                             'units': [{'id': 'old-unit'}], 'receipts': [{'request_id': 'old'}]}
                    old = copy.deepcopy(state)
                    d.set_style(Namespace(project=Path(name), choice='1', file=None), state)
                    self.assertEqual(state['phase'], phase)
                    for key in ('delivery_contract', 'units', 'receipts'):
                        self.assertEqual(state[key], old[key])
                    self.assertEqual(state['revision'], 2)
                    self.assertEqual(state['style_hash'], d.sha(d.STYLES['1']))

    def test_explicit_style_gate_advances_only_its_own_task(self):
        with tempfile.TemporaryDirectory() as name:
            for scope, expected in (('assets', 'complete'), ('workflow', 'range')):
                state = {'revision': 0, 'phase': 'style', 'delivery_contract': {'scope': scope}}
                d.set_style(Namespace(project=Path(name), choice='2', file=None), state)
                self.assertEqual(state['phase'], expected)


class FirstReviewDiagnosticsTests(unittest.TestCase):
    def test_appended_legal_unit_is_first_review_not_a_broken_graph(self):
        state, full = fixture()
        prefix = copy.deepcopy(full)
        prefix['units'] = prefix['units'][:3]
        with patch.object(sr, 'rules_digest', return_value='isolated-rules'):
            snapshot = sr.freeze(state, prefix, validation_passed=True)
            report = reviewed(state, prefix)
            _, proof = sr.prepare_incremental(snapshot, report, state, full,
                validation_passed=True, expected_report_sha256=sr.digest(report))
        new = [row for row in proof['shot_provenance'] if int(row['key'].split(':')[0]) > 3]
        self.assertTrue(new)
        for row in new:
            self.assertEqual(row['status'], 'pending')
            self.assertIn('new_target_without_prior_review', row['reasons'])
            self.assertNotIn('shot_dependency_declaration_missing', row['reasons'])
            self.assertNotIn('shot_identity_order_changed_conservative', row['reasons'])


if __name__ == '__main__':
    unittest.main()
