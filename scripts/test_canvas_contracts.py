"""Official field/space contracts; all transport is injected, never live Canvas."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import canvas as c
import canvas_media as media
from test_canvas_reference_workflow import WorkflowCLI, add_node


PERSONAL = 'https://xyq.jianying.com/novel/detail/canvas?projectId=P&canvasId=ROOT&overviewPippitAssetId=OV'
TEAM = 'https://www.xiaoyunque.com/project?projectId=P&canvasId=ROOT&agent_thread_id=unneeded'
TEAM_XYQ = 'https://xyq.jianying.com/project?projectId=P&generate_mode=agent&canvasId=ROOT&agent_thread_id=unneeded'


def document():
    return {'rootCanvasId': 'ROOT', 'assets': {'ROOT': {'type': 'canvas', 'content': {
        'nodes': {}, 'edges': {}, 'settings': {'readOnly': False}}}}}


class SpaceCLI(WorkflowCLI):
    def __init__(self, teams=('12',), accepted='12'):
        super().__init__(document())
        self.teams, self.accepted, self.binding = teams, accepted, None
        self.selected = []

    use_binding = c.CLI.use_binding

    def native(self, *args):
        if args == ('space', 'list'):
            return {'spaces': [{'team_id': '0', 'name': 'Personal'}] +
                    [{'team_id': x, 'name': 'Team ' + x} for x in self.teams],
                    'current': {'team_id': '0'}}
        raise AssertionError(args)

    def command(self, canvas_id, name, value=None):
        self.selected.append(self.binding['team_id'])
        if self.binding['team_id'] != self.accepted:
            c.fail('CLI_ERROR', 'Wrong space')
        if name == 'get_permissions':
            return {'canvasId': canvas_id, 'readOnly': False}
        return super().command(canvas_id, name, value)


class CanvasContractChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def test_official_link_routes_and_host_validation(self):
        for url, hint in [(PERSONAL, 'personal'), (TEAM, 'team'), (TEAM_XYQ, 'team')]:
            parsed = c.parse_target(url, None)
            self.assertEqual(parsed['canvas_id'], 'ROOT')
            self.assertEqual(parsed['space_hint'], hint)
            self.assertNotIn('agent_thread_id', parsed['url'])
        for url in [TEAM.replace('www.xiaoyunque.com', 'www.xiaoyunque.com.evil.test'),
                    TEAM.replace('https://', 'https://user@'), TEAM.replace('/project?', '/wrong?'),
                    TEAM + '&canvasId=OTHER',
                    TEAM_XYQ.replace('xyq.jianying.com', 'xyq.jianying.com.evil.test'),
                    TEAM_XYQ.replace('/project?', '/wrong?'), TEAM_XYQ + '&canvasId=OTHER']:
            with self.assertRaises(ValueError): c.parse_target(url, None)

    def test_empty_duplicate_routing_ids_are_not_discarded(self):
        for url in [TEAM_XYQ + '&canvasId=',
                    TEAM_XYQ.replace('canvasId=ROOT', 'canvasId=&canvasId=ROOT'),
                    TEAM_XYQ + '&projectId=', PERSONAL + '&overviewPippitAssetId=']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                c.parse_target(url, None)

    def test_jianying_project_retains_context_and_selects_team_not_current_personal(self):
        cli = SpaceCLI()
        binding = c.bind(self.project, cli, TEAM_XYQ)['binding']
        self.assertEqual(binding['team_id'], '12')
        self.assertEqual(binding['project_id'], 'P')
        self.assertEqual(binding['canvas_id'], 'ROOT')
        self.assertEqual(binding['url'], 'https://xyq.jianying.com/project?projectId=P&canvasId=ROOT')
        self.assertEqual(binding['space_evidence'], 'url_and_single_team_candidate')
        self.assertTrue(cli.selected)
        self.assertEqual(set(cli.selected), {'12'})
        self.assertFalse(cli.writes)

    def test_jianying_project_multiple_teams_does_not_probe_personal_or_any_team(self):
        cli = SpaceCLI(('12', '34'))
        with self.assertRaises(c.CanvasError) as error:
            c.bind(self.project, cli, TEAM_XYQ)
        self.assertEqual(error.exception.code, 'SPACE_REQUIRED')
        self.assertEqual(cli.selected, [])
        self.assertFalse(cli.writes)

    def test_jianying_project_failed_team_verification_does_not_fallback(self):
        cli = SpaceCLI(accepted='34')
        with self.assertRaises(c.CanvasError) as error:
            c.bind(self.project, cli, TEAM_XYQ)
        self.assertEqual(error.exception.code, 'SPACE_LOOKUP_FAILED')
        self.assertEqual(cli.selected, ['12'])
        self.assertIsNone(c.read_state(self.project)['binding'])

    def test_team_id_strings_are_normalized_without_float_conversion(self):
        self.assertEqual(c.normalize_team_id('7690000000000000001'), '7690000000000000001')
        self.assertEqual(c.normalize_team_id('000'), '0')
        for bad in [-1, 0, '-1', '1.2', '9223372036854775808']:
            with self.assertRaises(ValueError): c.normalize_team_id(bad)

    def test_url_candidate_is_read_only_validated_and_saved(self):
        cli = SpaceCLI()
        result = c.bind(self.project, cli, TEAM)
        self.assertEqual(result['binding']['team_id'], '12')
        self.assertFalse(cli.writes)
        self.assertEqual(set(cli.selected), {'12'})

    def test_personal_candidate_does_not_use_team(self):
        result = c.bind(self.project, SpaceCLI(accepted='0'), PERSONAL)
        self.assertEqual(result['binding']['team_id'], '0')

    def test_multiple_teams_need_one_explicit_choice_without_probing(self):
        cli = SpaceCLI(('12', '34'))
        with self.assertRaises(c.CanvasError) as error: c.bind(self.project, cli, TEAM)
        self.assertEqual(error.exception.code, 'SPACE_REQUIRED')
        self.assertEqual(cli.selected, [])
        self.assertEqual(c.bind(self.project, cli, TEAM, team_id='12')['binding']['team_id'], '12')

    def test_saved_team_survives_rebind_and_changed_default(self):
        cli = SpaceCLI()
        c.bind(self.project, cli, TEAM)
        cli.teams = ('12', '34')
        with patch.object(cli, 'native', side_effect=AssertionError('No repeated team choice')):
            self.assertEqual(c.bind(self.project, cli, TEAM)['binding']['team_id'], '12')

    def test_failed_candidate_does_not_fallback_or_persist_binding(self):
        cli = SpaceCLI(accepted='34')
        with self.assertRaises(c.CanvasError) as error: c.bind(self.project, cli, TEAM)
        self.assertEqual(error.exception.code, 'SPACE_LOOKUP_FAILED')
        self.assertEqual(cli.selected, ['12'])
        self.assertIsNone(c.read_state(self.project)['binding'])

    def test_native_and_semantic_calls_pin_binding_after_process_restart(self):
        state = c.blank_state()
        state['binding'] = {'account_id': 'USER', 'canvas_id': 'ROOT', 'team_id': '12'}
        c.save(self.project, state)
        cli = c.CLI(self.project)
        cli.config = {k: __file__ for k in ('node_path', 'cli_path', 'canvas_entry')}
        cli.config['canvas_contract'] = c.ASSET_CONTRACT
        calls = []
        with patch.object(c, 'run_json', side_effect=lambda args, *a, **kw: calls.append(args) or {'ok': True, 'data': {}}):
            cli.native('canvas', 'get', '--asset-id', 'ROOT')
            cli.command('ROOT', 'get_snapshot')
        for args in calls:
            self.assertEqual(args[args.index('--team-id') + 1], '12')
        with self.assertRaises(ValueError): cli.command('OTHER', 'delete_nodes', {'ids': ['X']})

    def test_legacy_binding_does_not_silently_adopt_default(self):
        with self.assertRaises(c.CanvasError) as error:
            c.CLI(self.project).use_binding({'canvas_id': 'ROOT', 'account_id': 'USER'})
        self.assertEqual(error.exception.code, 'SPACE_REQUIRED')

    def test_description_alone_never_satisfies_prompt_contract(self):
        doc = document()
        add_node(doc, 'R', 'role', 'Actor')
        c.content(doc, 'R')['appearanceInfo'] = {'description': 'IMAGE PROMPT'}
        item = {'name': 'Actor', 'kind': 'role', 'image_prompt': 'IMAGE PROMPT'}
        self.assertFalse(all(c.target_satisfied(doc, t) for t in c.asset_targets(item, 'R')))

    def test_direct_assets_fix_empty_prompt_without_touching_description(self):
        doc = document()
        add_node(doc, 'R', 'role', 'Actor', generation={'prompt': '', 'ratio': '1:1'})
        c.content(doc, 'R')['appearanceInfo'] = {'description': 'INDEPENDENT DESCRIPTION'}
        cli = WorkflowCLI(doc)
        state = c.blank_state()
        state.update(binding={'canvas_id': 'ROOT', 'account_id': 'USER'}, mappings={
            'C': {'node_id': 'R', 'kind': 'role', 'name': 'Actor', 'stage': 'ready'}})
        c.save(self.project, state)
        session = {'ledger': {'schema_version': 3,
                   'characters': [{'id': 'C', 'name': 'Actor', 'image_prompt': 'NEW'}],
                   'scene_assets': [], 'props': [], 'scenes': []}, 'units': [], 'receipts': []}
        result = c.sync(self.project, cli, 'assets', session_override=session)
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(c.content(cli.doc, 'R')['generation'], {'prompt': 'NEW', 'ratio': '1:1'})
        self.assertEqual(c.content(cli.doc, 'R')['appearanceInfo']['description'], 'INDEPENDENT DESCRIPTION')
        self.assertEqual(c.read_state(self.project)['mappings']['C']['asset_contract'], c.ASSET_CONTRACT)

    def test_direct_assets_protect_manual_prompt_until_exact_override(self):
        doc = document()
        add_node(doc, 'R', 'role', 'Actor', generation={'prompt': 'MANUAL', 'modelKey': 'KEEP'})
        cli = WorkflowCLI(doc)
        state = c.blank_state()
        state.update(binding={'canvas_id': 'ROOT', 'account_id': 'USER'}, mappings={
            'C': {'node_id': 'R', 'kind': 'role', 'name': 'Actor', 'stage': 'ready'}})
        c.save(self.project, state)
        session = {'ledger': {'schema_version': 3,
                   'characters': [{'id': 'C', 'name': 'Actor', 'image_prompt': 'NEW'}],
                   'scene_assets': [], 'props': [], 'scenes': []}, 'units': [], 'receipts': []}
        self.assertEqual(c.sync(self.project, cli, 'assets', session_override=session)['status'], 'needs_decision')
        self.assertFalse(cli.writes)
        result = c.sync(self.project, cli, 'assets', session_override=session,
                        decisions={'C': {'overwrite_fingerprint': c.fingerprint(cli.doc, 'R')}})
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(c.content(cli.doc, 'R')['generation'], {'prompt': 'NEW', 'modelKey': 'KEEP'})

    def test_role_prompt_writer_preserves_description_and_all_other_settings(self):
        doc = document()
        add_node(doc, 'R', 'role', 'Actor', generation={'prompt': 'OLD', 'ratio': '1:1', 'modelKey': 'M', 'references': [{'id': 'KEEP'}]})
        c.content(doc, 'R')['appearanceInfo'] = {'description': 'KEEP DESCRIPTION'}
        cli = WorkflowCLI(doc)
        name, value, targets = c.asset_prompt_change(doc, 'R', 'role', 'NEW')
        self.assertEqual(name, 'xyq.role.updateAppearance')
        self.assertEqual(value['expected'], {'generationPrompt': 'OLD'})
        cli.command('ROOT', name, value)
        expected = copy.deepcopy(c.content(doc, 'R'))
        expected['generation']['prompt'] = 'NEW'
        self.assertEqual(c.content(cli.doc, 'R'), expected)
        self.assertTrue(all(c.target_satisfied(cli.doc, t) for t in targets))

    def test_scene_update_merges_complete_generation_and_handles_wrapped_content(self):
        for wrapped in (False, True):
            doc = document()
            add_node(doc, 'S', 'scene', 'Room', generation={'prompt': 'OLD', 'ratio': '4:3', 'resolution': '4K', 'references': [{'id': 'KEEP'}]})
            old = copy.deepcopy(c.content(doc, 'S'))
            if wrapped: doc['assets']['S']['content'] = {'data': old, 'outer': 'KEEP'}
            cli = WorkflowCLI(doc)
            name, value, targets = c.asset_prompt_change(doc, 'S', 'scene', 'NEW')
            cli.command('ROOT', name, value)
            self.assertEqual(c.content(cli.doc, 'S')['generation'], {**old['generation'], 'prompt': 'NEW'})
            self.assertTrue(all(c.target_satisfied(cli.doc, t) for t in targets))
            if wrapped: self.assertEqual(cli.doc['assets']['S']['content']['outer'], 'KEEP')

    def test_upload_uses_cloud_id_and_does_not_reupload_processing_material(self):
        state = c.blank_state()
        state['binding'] = {'canvas_id': 'ROOT', 'account_id': 'U', 'team_id': '12'}
        class UploadCLI:
            uploads = 0
            ready = False
            def native(self, *args, **kw):
                if args[1] == 'upload':
                    self.uploads += 1
                    return {'state': 'processing', 'pippit_asset_id': 'PID', 'asset_id': 'SOURCE', 'source_id': 'SOURCE'}
                return {'assets': [{'PippitAssetID': 'PID', 'SourceID': 'SOURCE', 'AssetType': 1,
                                    'Image': {'asset_id': 'CLOUD'} if self.ready else {}}]}
        cli = UploadCLI()
        item = {'file_hash': 'HASH', 'path': 'test.png'}
        self.assertIsNone(media.uploaded_asset(self.project, cli, state, item))
        cli.ready = True
        result = media.uploaded_asset(self.project, cli, state, item)
        self.assertEqual(result['media_asset_id'], 'CLOUD')
        self.assertEqual(result['source_id'], 'SOURCE')
        self.assertEqual(cli.uploads, 1)
        self.assertNotIn('asset_id', result)

    def test_upload_cache_scope_and_legacy_uncertainty(self):
        state = {'binding': {'account_id': 'U', 'team_id': '12'}, 'uploads': {'HASH': {'status': 'uncertain'}}}
        first = media.upload_key(state, 'HASH')
        state['binding']['team_id'] = '34'
        self.assertNotEqual(first, media.upload_key(state, 'HASH'))
        with self.assertRaises(c.CanvasError) as error:
            media.uploaded_asset(self.project, None, state, {'file_hash': 'HASH', 'path': 'x.png'})
        self.assertEqual(error.exception.code, 'UPLOAD_SCOPE_REQUIRED')

    def test_media_type_and_identity_are_checked(self):
        for asset in [{'PippitAssetID': 'WRONG', 'Image': {'asset_id': 'CLOUD'}},
                      {'PippitAssetID': 'PID', 'AssetType': 2, 'Image': {'asset_id': 'PREVIEW'}},
                      {'PippitAssetID': 'PID', 'Image': {'asset_id': 'PID'}},
                      {'PippitAssetID': 'PID', 'Image': {'asset_id': 'A', 'assetId': 'B'}}]:
            with self.assertRaises(c.CanvasError): media.queried_image_id(asset, 'PID')


if __name__ == '__main__':
    unittest.main()
