#!/usr/bin/env python3
"""Offline canvas regressions; fake CLI only, with SDK-supported reference shapes."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import canvas as c


def document():
    return {'rootCanvasId': 'ROOT', 'assets': {
        'ROOT': {'type': 'canvas', 'content': {'nodes': {
            'R': {'type': 'biz/role'}, 'I': {'type': 'biz/image'},
            'V1': {'type': 'biz/video'}, 'V2': {'type': 'biz/video'}},
            'edges': {}, 'settings': {'readOnly': False}}},
        'R': {'content': {'nodeName': 'Actor', 'basicInfo': {'name': 'Actor', 'appearanceName': ''},
                          'appearanceInfo': {'pippitAssetId': 'ROLE_MEDIA'}}},
        'I': {'content': {'name': 'Pen', 'pippitAssetId': 'PEN_MEDIA'}},
        **{unit: {'content': {'name': unit, 'caption': unit, 'title': unit, 'generation': {
            'prompt': '<node-asset>R</node-asset> <node-asset>I</node-asset> action',
            'references': [{'id': 'R', 'type': 'image', 'sourceNodeId': 'R',
                            'nodeAssetId': 'R', 'pippitAssetId': 'ROLE_MEDIA'}]}}}
           for unit in ('V1', 'V2')}}}


class FakeCLI:
    """Emulate update_prompt's existing SDK edge/role-reference path, never its network."""
    def __init__(self, doc):
        self.doc = copy.deepcopy(doc)
        self.writes = []

    def auth(self):
        return {'identity': 'USER'}

    def command(self, canvas_id, name, value=None):
        if name == 'get_snapshot':
            return {'document': copy.deepcopy(self.doc)}
        if name == 'xyq.generation.update_prompt':
            self.writes.append((name, copy.deepcopy(value)))
            generation = c.content(self.doc, value['nodeId'])['generation']
            edges = self.doc['assets']['ROOT']['content']['edges']
            for source in c.node_asset_ids(value['prompt']):
                present = any(edge['source'] == source and edge['target'] == value['nodeId']
                              and edge['type'] == 'reference' for edge in edges.values())
                if present:
                    continue  # SDK tg returns noop for an existing edge.
                edge_id = source + '-' + value['nodeId']
                edges[edge_id] = {'id': edge_id, 'source': source, 'target': value['nodeId'], 'type': 'reference'}
                if c.kind_of(c.nodes(self.doc)[source]) == 'role' and c.role_reference_media(self.doc, source, value['nodeId']):
                    generation.setdefault('references', []).append({'id': source, 'sourceNodeId': source,
                        'nodeAssetId': source, 'type': 'image'})
            generation['prompt'] = value['prompt']
            return {}
        if name == 'delete_nodes':
            self.writes.append((name, copy.deepcopy(value)))
            for node_id in value['ids']:
                self.doc['assets']['ROOT']['content']['nodes'].pop(node_id)
                self.doc['assets'].pop(node_id)
            return {}
        if name == 'delete_edges':
            self.writes.append((name, copy.deepcopy(value)))
            edges = self.doc['assets']['ROOT']['content']['edges']
            for edge_id in value['ids']:
                edges.pop(edge_id, None)
            return {}
        raise AssertionError('Unexpected fake CLI call: ' + name)


class CanvasAuditChecks(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / 'tests-canvas'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.doc = document()
        self.cli = FakeCLI(self.doc)
        self.session = {'ledger': {'schema_version': 3,
            'characters': [{'id': 'C', 'name': 'Actor'}],
            'scene_assets': [], 'scenes': [], 'props': [{'id': 'P', 'name': 'Pen'}]},
            'units': [{'id': unit, 'version': 1, 'duration': 4, 'character_ids': ['C'],
                       'scene_ids': [], 'prop_ids': ['P'], 'global': 'standing', 'shots': [],
                       'test_prompt': '@Actor @Pen action'} for unit in ('V1', 'V2')],
            'receipts': []}
        self.state = c.blank_state()
        self.state.update(binding={'canvas_id': 'ROOT', 'account_id': 'USER'}, mappings={
            'C': {'node_id': 'R', 'kind': 'role', 'stage': 'ready', 'name': 'Actor'},
            'P': {'node_id': 'I', 'kind': 'image', 'stage': 'ready', 'name': 'Pen'},
            **{unit: {'node_id': unit, 'kind': 'video', 'stage': 'ready', 'name': unit,
                      'prompt_hash': c.digest(c.prompt_of(self.doc, unit)), 'version': 1,
                      'references_bound': True} for unit in ('V1', 'V2')}})
        self.store()

    def store(self):
        c.save(self.project, self.state)

    def edge(self, source, target):
        self.cli.doc['assets']['ROOT']['content']['edges'][source + '-' + target] = {
            'source': source, 'target': target, 'type': 'reference'}

    def complete_edges(self):
        for unit in ('V1', 'V2'):
            for source in ('R', 'I'):
                self.edge(source, unit)

    def sync(self, ids=('V1',), decisions=None):
        def render(session, unit, reference_text=None):
            return reference_text(unit['test_prompt']) if reference_text else unit['test_prompt']
        with patch.object(c.d, 'load', return_value=self.session), patch.object(c.d, 'render_unit', side_effect=render):
            return c.sync(self.project, self.cli, 'videos', ids=list(ids), decisions=decisions,
                          bind_references=True, existing_only=True, references_only=True)

    def test_identical_prompt_repairs_missing_edge(self):
        self.edge('R', 'V1')
        prompt = c.prompt_of(self.cli.doc, 'V1')
        self.assertFalse(c.prompt_references_satisfied(self.cli.doc, 'V1', prompt))
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual([command for command, _ in self.cli.writes], ['xyq.generation.update_prompt'])
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, 'V1', prompt))

    def test_new_video_node_names_use_the_committed_unit_id(self):
        unit = copy.deepcopy(self.session['units'][0])
        unit.update(id='场景3-分镜12', duration=27)
        with patch.object(c.d, 'render_unit', return_value='完整提示词'):
            wanted, pending = c.desired_videos(self.session, self.state, self.doc, [unit])
        self.assertEqual(pending, [])
        self.assertEqual(wanted[0]['key'], '场景3-分镜12')
        self.assertEqual(wanted[0]['name'], '场景3-分镜12')
        created = c.initial_data(wanted[0])
        self.assertEqual({key: created[key] for key in ('name', 'caption', 'title')},
                         dict.fromkeys(('name', 'caption', 'title'), '场景3-分镜12'))

    def test_photography_fields_include_assets_in_reference_requirements(self):
        self.session['ledger']['props'].append({'id': 'P2', 'name': 'Lamp'})
        self.session['ledger']['scene_assets'].append({'id': 'SET0001', 'name': 'Room'})
        self.session['ledger']['scenes'].append({'id': 'SC0001', 'scene_asset_id': 'SET0001',
                                                'prop_ids': ['P', 'P2']})
        unit = copy.deepcopy(self.session['units'][0])
        unit['scene_ids'] = ['SC0001']
        for field in ('camera', 'composition', 'depth_of_field', 'lighting'):
            with self.subTest(field=field):
                unit['shots'] = [{'text': '主体原动作', field: '@{P2} 位于前景'}]
                self.assertIn('P2', c.unit_asset_ids(self.session, unit))

    def test_role_missing_edge_and_draft_row_are_restored_together(self):
        c.content(self.cli.doc, 'V1')['generation']['references'] = []
        self.edge('I', 'V1')
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, 'V1', c.prompt_of(self.cli.doc, 'V1')))

    def test_existing_role_edge_with_missing_row_is_refreshed(self):
        self.complete_edges()
        c.content(self.cli.doc, 'V1')['generation']['references'] = []
        c.queue_image_references(self.session, self.state, ['C'])
        self.store()
        result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertEqual([name for name, _ in self.cli.writes], ['delete_edges', 'xyq.generation.update_prompt'])
        self.assertEqual(self.cli.writes[0][1], {'ids': ['R-V1']})
        self.assertIn('I-V1', self.cli.doc['assets']['ROOT']['content']['edges'])
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, 'V1', c.prompt_of(self.cli.doc, 'V1')))
        self.assertEqual(c.read_state(self.project)['journal'][-1]['status'], 'verified')
        self.assertEqual(c.read_state(self.project)['pending_image_reference_units']['C'], ['V2'])

    def test_ordinary_image_edge_valid_with_empty_draft_references(self):
        data = c.content(self.cli.doc, 'V1')['generation']
        data.update(prompt='<node-asset label="pen"> I </node-asset>', references=[])
        self.edge('I', 'V1')
        self.assertTrue(c.target_satisfied(self.cli.doc, {'action': 'prompt', 'node_id': 'V1', 'value': data['prompt']}))

    def test_saved_role_identity_aliases_and_selection_shape(self):
        self.complete_edges()
        draft = c.content(self.cli.doc, 'V1')['generation']
        for alias in ('id', 'sourceNodeId', 'nodeAssetId'):
            with self.subTest(alias=alias):
                draft['references'] = [{alias: 'R', 'type': 'image', 'dataReferenceSelection': ['ROLE_MEDIA']}]
                self.assertTrue(c.prompt_references_satisfied(self.cli.doc, 'V1', draft['prompt']))
        draft['references'] = [{'sourceNodeId': 'R', 'type': 'image', 'dataReferenceSelection': 'ROLE_MEDIA'}]
        self.assertFalse(c.prompt_references_satisfied(self.cli.doc, 'V1', draft['prompt']))
        draft['references'] = {'R': {'type': 'image'}}
        self.assertFalse(c.prompt_references_satisfied(self.cli.doc, 'V1', draft['prompt']))

    def test_attributes_and_whitespace_are_shared_by_delete_scan_and_readback(self):
        generation = c.content(self.cli.doc, 'V1')['generation']
        generation['prompt'] = '<node-asset label="角色" data-reference-selection="ROLE_MEDIA"> R </node-asset>'
        self.assertIn('V1', c.dependents(self.cli.doc, ['R']))
        self.edge('R', 'V1')
        self.assertTrue(c.target_satisfied(self.cli.doc, {'action': 'prompt', 'node_id': 'V1', 'value': generation['prompt']}))
        self.assertEqual(c.node_asset_ids('<node-asset-id>R</node-asset-id><node-asset-extra>R</node-asset-extra>'), [])

    def test_standalone_references_consumes_only_completed_units(self):
        self.complete_edges()
        c.queue_image_references(self.session, self.state, ['P'])
        self.store()
        self.assertEqual(self.sync()['status'], 'synced')
        first = c.read_state(self.project)
        self.assertEqual(first['pending_image_references'], ['P'])
        self.assertEqual(first['pending_image_reference_units']['P'], ['V2'])
        self.assertEqual(self.sync(ids=('V2',))['status'], 'synced')
        self.assertEqual(c.read_state(self.project)['pending_image_references'], [])

    def test_skipped_unit_and_missing_source_stay_pending(self):
        self.complete_edges()
        c.queue_image_references(self.session, self.state, ['C', 'P'])
        self.store()
        result = self.sync(ids=('V1', 'V2'), decisions={'V2': {'skip': True}})
        self.assertEqual(result['skipped'], ['V2'])
        state = c.read_state(self.project)
        self.assertEqual(state['pending_image_reference_units'], {'C': ['V2'], 'P': ['V2']})
        c.consume_image_references(self.session, state, {'V2'}, [{'key': 'C'}])
        self.assertEqual(state['pending_image_references'], ['C'])

    def test_existing_queue_without_unit_tracking_is_resumed(self):
        self.complete_edges()
        self.state['pending_image_references'] = ['P']
        self.store()
        self.sync(ids=('V1', 'V2'))
        self.assertEqual(c.read_state(self.project)['pending_image_references'], [])

    def test_source_node_type_changes_remain_text_and_are_reported(self):
        c.nodes(self.cli.doc)['R']['type'] = 'biz/image'
        c.content(self.cli.doc, 'R')['pippitAssetId'] = 'OTHER_MEDIA'
        resolved, pending = c.reference_map(self.session, self.state, self.cli.doc, {'C'})
        self.assertNotIn('Actor', resolved)
        conflict = next(item for item in pending if item['key'] == 'C')
        self.assertEqual((conflict['reason'], conflict['expected_kind'], conflict['actual_kind']),
                         ('node_type_changed', 'role', 'image'))
        self.assertNotIn('Pen', resolved)  # Unrelated sources are outside this requested lookup.

    def delete_document(self):
        for unit in ('V1', 'V2'):
            c.content(self.cli.doc, unit)['generation']['prompt'] = ''
            c.content(self.cli.doc, unit)['generation']['references'] = []
        c.nodes(self.cli.doc)['I2'] = {'type': 'biz/image'}
        self.cli.doc['assets']['I2'] = {'content': {'name': 'Second', 'pippitAssetId': 'SECOND_MEDIA'}}

    def test_delete_requires_exact_ids_and_complete_fingerprints(self):
        self.delete_document()
        with self.assertRaisesRegex(ValueError, '确切节点ID'):
            c.manage(self.project, self.cli, 'delete', {'targets': [{'kind': 'image'}]})
        with self.assertRaisesRegex(ValueError, '全部当前目标'):
            c.manage(self.project, self.cli, 'delete', {'targets': [{'id': 'I'}, {'id': 'I2'}],
                'fingerprints': {'I': c.fingerprint(self.cli.doc, 'I')}})
        self.assertEqual(self.cli.writes, [])
        preview = c.manage(self.project, self.cli, 'delete', {'targets': [{'kind': 'image'}]}, preview=True)
        self.assertEqual(set(preview['node_ids']), {'I', 'I2'})
        result = c.manage(self.project, self.cli, 'delete', {'targets': [{'id': node} for node in preview['node_ids']],
            'fingerprints': preview['fingerprints']})
        self.assertEqual(result['count'], 2)

    def test_delete_resumes_with_fingerprint_of_already_deleted_id(self):
        self.delete_document()
        fingerprints = {node: c.fingerprint(self.cli.doc, node) for node in ('I', 'I2')}
        c.manage(self.project, self.cli, 'delete', {'targets': [{'id': 'I'}], 'fingerprints': {'I': fingerprints['I']}})
        result = c.manage(self.project, self.cli, 'delete', {'targets': [{'id': 'I'}, {'id': 'I2'}], 'fingerprints': fingerprints})
        self.assertEqual(result['node_ids'], ['I2'])
        self.assertEqual(result['already_deleted'], ['I'])
        again = c.manage(self.project, self.cli, 'delete', {'targets': [{'id': 'I'}, {'id': 'I2'}], 'fingerprints': fingerprints})
        self.assertEqual(again['count'], 0)
        self.assertEqual(set(again['already_deleted']), {'I', 'I2'})


if __name__ == '__main__':
    unittest.main()
