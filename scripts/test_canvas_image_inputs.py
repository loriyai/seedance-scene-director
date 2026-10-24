"""Derived role images: real input identities, missing media, and safe recovery."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import canvas as c
import canvas_job_backend as backend
from test_canvas_job_backend import AssetJobCLI
from test_canvas_reference_workflow import add_node


class DerivedImageInputs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.session = {'ledger': {'schema_version': 3, 'scenes': [], 'scene_assets': [],
            'characters': [
                {'id': 'CHAR0002', 'name': 'Actor-Masked', 'image_prompt': '以 @Actor 为基础，佩戴 @Mask 。',
                 'visual_design': {'image_reference': {'base_asset_id': 'CHAR0001',
                    'changes': ['佩戴头罩'], 'preserve': ['未变化衣装'], 'prop_asset_ids': ['PROP0001']}},
                 'image_format': {'reference_policy': 'asset-image-reference-v1',
                                  'reference_asset_ids': ['CHAR0001', 'PROP0001']}},
                {'id': 'CHAR0001', 'name': 'Actor', 'image_prompt': 'base appearance'}],
            'props': [{'id': 'PROP0001', 'name': 'Mask', 'image_prompt': 'mask reference'}]},
            'units': [], 'receipts': []}
        self.cli = AssetJobCLI({'rootCanvasId': 'ROOT', 'assets': {'ROOT': {'type': 'canvas',
            'content': {'nodes': {}, 'edges': {}, 'settings': {'readOnly': False}}}}})
        state = c.blank_state()
        state['binding'] = {'canvas_id': 'ROOT', 'account_id': 'USER'}
        c.save(self.project, state)

    def sync(self):
        return backend.sync_assets(self.project, self.cli, self.session, lambda value: None)

    def mapping(self, key='CHAR0002'):
        return c.read_state(self.project)['mappings'][key]

    def generation(self):
        return c.content(self.cli.doc, self.mapping()['node_id'])['generation']

    def add_media(self):
        for key, pid, aid in [('CHAR0001', 'MATERIAL-A', 'MEDIA-A'), ('PROP0001', 'MATERIAL-P', 'MEDIA-P')]:
            data = c.content(self.cli.doc, self.mapping(key)['node_id'])
            info = data.setdefault('appearanceInfo', {}) if key.startswith('CHAR') else data
            info.update(pippitAssetId=pid, assetId=aid)

    def test_dependencies_precede_derived_node(self):
        self.assertEqual([i['key'] for i in c.desired_assets(self.session)], ['CHAR0001', 'PROP0001', 'CHAR0002'])

    def test_missing_compiled_contract_is_not_silently_treated_as_legacy(self):
        self.session['ledger']['characters'][0]['image_format'].pop('reference_policy')
        with self.assertRaisesRegex(ValueError, '图片引用编译合同缺失'):
            c.desired_assets(self.session)

    def test_three_entrypoints_create_empty_inputs_without_claiming_media_ready(self):
        for entry in ('queue', 'direct', 'video_prepare'):
            with self.subTest(entry=entry):
                self.setUp()
                if entry == 'queue':
                    self.sync()
                elif entry == 'direct':
                    c.sync(self.project, self.cli, 'assets', session_override=self.session)
                else:
                    state, doc = c.connected(self.project, self.cli)
                    c.reconcile_asset_nodes(self.project, self.cli, self.session, state, doc)
                mapping = self.mapping()
                self.assertEqual(mapping['image_input']['status'], 'pending')
                self.assertEqual(len(mapping['image_input']['pending']), 2)
                self.assertEqual(mapping['image_input']['managed_references'], [])
                self.assertEqual(set(c.node_asset_ids(self.generation()['prompt'])),
                                 {self.mapping('CHAR0001')['node_id'], self.mapping('PROP0001')['node_id']})
                self.assertEqual(len(self.cli.allocations), 3)

    def test_exact_media_inputs_preserve_configuration_and_unrelated_references(self):
        self.sync()
        self.add_media()
        generation = self.generation()
        generation.update(modelKey='custom-model', resolution='4K', ratio='1:1', effort='low', seed=123)
        generation['references'] = [{'id': 'unrelated', 'type': 'image', 'pippitAssetId': 'USER-MATERIAL'}]
        self.sync()
        result = self.generation()
        self.assertEqual({k: result[k] for k in ('modelKey', 'resolution', 'ratio', 'effort', 'seed')},
                         {'modelKey': 'custom-model', 'resolution': '4K', 'ratio': '1:1', 'effort': 'low', 'seed': 123})
        self.assertEqual(result['references'][0]['id'], 'unrelated')
        self.assertEqual([(r['pippitAssetId'], r['assetId']) for r in result['references'][1:]],
                         [('MATERIAL-A', 'MEDIA-A'), ('MATERIAL-P', 'MEDIA-P')])
        self.assertEqual(result['references'][1]['dataReferenceSelection'], ['MATERIAL-A'])
        self.assertEqual(self.mapping()['image_input']['status'], 'ready')
        role_id = self.mapping()['node_id']
        self.assertFalse(any(n == 'xyq.generation.update_prompt' and v['nodeId'] == role_id for n, v in self.cli.writes))

    def test_repeat_is_idempotent_and_main_image_change_refreshes_owned_input(self):
        self.sync()
        self.add_media()
        self.sync()
        writes = len(self.cli.writes)
        self.sync()
        self.assertEqual(len(self.cli.writes), writes)
        base = c.content(self.cli.doc, self.mapping('CHAR0001')['node_id'])['appearanceInfo']
        base.update(pippitAssetId='MATERIAL-B', assetId='MEDIA-B')
        self.sync()
        refs = self.mapping()['image_input']['managed_references']
        self.assertEqual(refs[0]['pippitAssetId'], 'MATERIAL-B')
        self.assertEqual(len(self.cli.allocations), 3)

    def test_manual_input_edits_are_not_overwritten(self):
        self.sync()
        self.add_media()
        self.sync()
        self.generation()['references'][0]['pippitAssetId'] = 'MANUAL'
        before = copy.deepcopy(self.generation())
        with self.assertRaises(c.CanvasError) as error:
            self.sync()
        self.assertEqual(error.exception.code, 'CONCURRENT_EDIT')
        self.assertEqual(self.generation(), before)

    def test_pippit_id_alone_is_not_ready_and_no_media_id_is_fabricated(self):
        self.sync()
        base = c.content(self.cli.doc, self.mapping('CHAR0001')['node_id'])
        base['appearanceInfo'] = {'pippitAssetId': 'MATERIAL-ONLY'}
        self.sync()
        self.assertEqual(self.mapping()['image_input']['status'], 'pending')
        self.assertEqual(self.generation()['references'], [])

    def test_manual_deletion_and_alias_only_edits_are_protected(self):
        for edit in ('delete', 'alias'):
            with self.subTest(edit=edit):
                self.setUp()
                self.sync()
                self.add_media()
                self.sync()
                if edit == 'delete':
                    self.generation()['references'] = []
                else:
                    row = self.generation()['references'][0]
                    row.pop('sourceNodeId')
                    row.pop('nodeAssetId')
                    row['pippitAssetId'] = 'USER-CHOICE'
                before = copy.deepcopy(self.generation())
                with self.assertRaises(c.CanvasError):
                    self.sync()
                self.assertEqual(self.generation(), before)

    def test_lost_ack_after_input_write_recovers_same_ids(self):
        self.sync()
        self.add_media()
        command = self.cli.command
        failed = False
        def lose_ack(canvas_id, name, value=None):
            nonlocal failed
            if failed and name == 'get_snapshot':
                c.fail('CLI_ERROR', 'readback unavailable after lost ACK')
            result = command(canvas_id, name, value)
            if name == 'xyq.role.updateAppearance' and 'generation' in value.get('patch', {}) and not failed:
                failed = True
                c.fail('CLI_ERROR', 'lost ACK after server accepted write')
            return result
        with patch.object(self.cli, 'command', side_effect=lose_ack):
            with self.assertRaises(c.CanvasError):
                self.sync()
        self.assertEqual(self.mapping()['image_input']['status'], 'pending')
        self.sync()
        self.assertEqual(len(self.cli.allocations), 3)
        self.assertEqual(self.mapping()['image_input']['status'], 'ready')

    def test_rebound_source_removes_only_owned_old_edge_even_without_images(self):
        self.sync()
        state = c.read_state(self.project)
        target = state['mappings']['CHAR0002']['node_id']
        old_source = state['mappings']['CHAR0001']['node_id']
        add_node(self.cli.doc, 'NEW-BASE', 'role', 'Actor', generation={'prompt': 'base appearance'})
        state['mappings']['CHAR0001']['node_id'] = 'NEW-BASE'
        add_node(self.cli.doc, 'USER-SOURCE', 'image', 'User reference')
        edges = self.cli.doc['assets']['ROOT']['content']['edges']
        edges['USER-EDGE'] = {'id': 'USER-EDGE', 'type': 'reference', 'source': 'USER-SOURCE', 'target': target}
        c.sync_asset_image_references(self.project, self.cli, self.session, state, self.cli.doc)
        self.assertIsNone(c.reference_edge(self.cli.doc, old_source, target))
        self.assertIsNotNone(c.reference_edge(self.cli.doc, 'NEW-BASE', target))
        self.assertIn('USER-EDGE', self.cli.doc['assets']['ROOT']['content']['edges'])
        self.assertEqual(self.mapping()['image_input']['status'], 'pending')

    def test_target_rename_during_fresh_read_cannot_be_accepted(self):
        self.sync()
        state = c.read_state(self.project)
        nid = self.mapping()['node_id']
        prior = copy.deepcopy(self.cli.doc)
        c.nodes(self.cli.doc)[nid].setdefault('data', {})['name'] = 'User renamed'
        with self.assertRaises(c.CanvasError) as error:
            c.sync_asset_image_references(self.project, self.cli, self.session, state, prior)
        self.assertEqual(error.exception.code, 'CONCURRENT_EDIT')

    def test_missing_mapped_target_cannot_keep_successful_input_status(self):
        self.sync()
        self.add_media()
        self.sync()
        self.assertEqual(self.mapping()['image_input']['status'], 'ready')
        state = c.read_state(self.project)
        nid = self.mapping()['node_id']
        self.cli.doc['assets']['ROOT']['content']['nodes'].pop(nid)
        self.cli.doc['assets'].pop(nid)
        with self.assertRaises(c.CanvasError) as error:
            c.sync_asset_image_references(self.project, self.cli, self.session, state, self.cli.doc)
        self.assertEqual(error.exception.code, 'CONCURRENT_EDIT')
        self.assertEqual(self.mapping()['image_input']['status'], 'pending')

    def test_missing_edge_ack_does_not_complete_inputs(self):
        self.sync()
        nid = self.mapping()['node_id']
        self.cli.doc['assets']['ROOT']['content']['edges'].clear()
        self.cli.omit_edges_for.add(nid)
        with self.assertRaises(c.CanvasError):
            self.sync()
        self.assertEqual(self.mapping()['image_input']['status'], 'pending')

    def test_legacy_ledger_gets_no_reference_contract_or_extra_writes(self):
        self.session['ledger']['characters'] = [self.session['ledger']['characters'][1]]
        result = self.sync()
        self.assertEqual(result['image_inputs'], [])
        self.assertFalse(any(n == 'create_edge' for n, v in self.cli.writes))


if __name__ == '__main__':
    unittest.main()
