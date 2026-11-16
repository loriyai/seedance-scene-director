import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import canvas as c
import canvas_space_defaults as defaults
from test_canvas_contracts import SpaceCLI, TEAM


class SpaceDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.path = self.project / 'config/defaults.json'
        self.patch = patch.object(defaults, 'defaults_path', return_value=self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_first_team_use_returns_options_even_with_one_team(self):
        cli = SpaceCLI()
        with self.assertRaises(c.CanvasError) as error:
            c.bind(self.project, cli, TEAM)
        self.assertEqual(error.exception.code, 'SPACE_REQUIRED')
        self.assertEqual([x['team_id'] for x in error.exception.details['spaces']], ['0', '12'])
        self.assertFalse(cli.selected)

    def test_selected_default_is_reused_by_a_new_project_and_process(self):
        first = SpaceCLI(('12', '34'))
        c.select_space(first, '34')
        self.assertEqual(defaults.selection('USER')['team_id'], '34')
        second = SpaceCLI(('12', '34'), accepted='34')
        with patch.object(second, 'native', side_effect=AssertionError('Saved choice must not ask again')):
            binding = c.bind(self.project / 'new-project', second, TEAM)['binding']
        self.assertEqual(binding['team_id'], '34')
        self.assertEqual(binding['space_evidence'], 'user_space_default')

    def test_defaults_are_isolated_by_identity_and_failed_choice_keeps_old(self):
        defaults.save_selection('USER-A', '12', 'A')
        defaults.save_selection('USER-B', '34', 'B')
        self.assertEqual(defaults.selection('USER-A')['team_id'], '12')
        self.assertIsNone(defaults.selection('NEW-KEY'))
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            c.select_space(SpaceCLI(), '999')
        self.assertEqual(self.path.read_bytes(), before)

    def test_changed_default_does_not_switch_existing_project(self):
        cli = SpaceCLI(('12', '34'))
        c.bind(self.project, cli, TEAM, team_id='12')
        c.select_space(cli, '34')
        self.assertEqual(c.bind(self.project, cli, TEAM)['binding']['team_id'], '12')
        self.assertEqual(defaults.selection('USER')['team_id'], '34')

    def test_preflight_selection_reused_only_for_same_credential_and_target(self):
        cli = SpaceCLI(('12', '34'))
        cli.project = self.project
        c.preflight(self.project, cli, TEAM, team_id='12')
        with patch.object(cli, 'native', side_effect=AssertionError('No repeated choice')):
            self.assertEqual(c.bind(self.project, cli, TEAM)['binding']['team_id'], '12')
        cached = json.loads((self.project / 'canvas/preflight.json').read_text())
        cached['account_id'] = 'ANOTHER-KEY'
        c.d.write_json(self.project / 'canvas/preflight.json', cached)
        target = c.parse_target(TEAM, None)
        with self.assertRaises(c.CanvasError):
            c.binding_space(cli, target, None, 'USER')

    def test_corrupted_file_is_not_overwritten(self):
        self.path.parent.mkdir()
        self.path.write_text('{broken')
        with self.assertRaises(ValueError):
            defaults.save_selection('USER', '12', 'Team')
        self.assertEqual(self.path.read_text(), '{broken')
        self.assertFalse(self.path.with_suffix('.lock').exists())

    def test_overview_preflight_scopes_resolution_before_query_without_saving_binding(self):
        class OverviewCLI(SpaceCLI):
            def native(self, *args):
                if args == ('canvas', 'get', '--asset-id', 'OV'):
                    self.space_args()
                    return {'canvasAssetId': 'ROOT'}
                return super().native(*args)

            space_args = c.CLI.space_args

        cli = OverviewCLI()
        cli.project = self.project
        result = c.preflight(self.project, cli,
                             'https://www.xiaoyunque.com/project?projectId=P&overviewPippitAssetId=OV',
                             team_id='12')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['root_canvas_id'], 'ROOT')
        self.assertIsNone(c.read_state(self.project)['binding'])


if __name__ == '__main__':
    unittest.main()
