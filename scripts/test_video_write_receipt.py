#!/usr/bin/env python3
"""Offline video write-order and final-receipt regressions; no real CLI or canvas."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import canvas as c
from test_canvas_reference_workflow import WorkflowCLI, add_node


class DefaultVideoCLI(WorkflowCLI):
    """Official commands with platform-created defaults and shallow content patches."""
    def __init__(self, doc, project):
        super().__init__(doc)
        self.project = project
        self.default_generation = {'modelKey': 'platform-default-v9', 'resolution': '1080p',
            'ratio': '9:16', 'seed': 413, 'parameters': {'strength': .6}, 'prompt': '', 'references': []}
        self.created_videos, self.named_videos, self.prompted_videos = set(), set(), set()
        self.hide_created_before_name = False
        self.hide_names_before_prompt = False
        self.final_fault = None
        self.unknown_ack_stage = None
        self.ack_before_next_command = []

    def native(self, *args, **kwargs):
        raise AssertionError('Video sync must not invoke model list/describe or another native command')

    @staticmethod
    def stage(name, value):
        if name == 'create_biz_node' and value['nodeKind'] == 'video':
            return 'create'
        if name == 'update_node_data' and {'name', 'title', 'caption'} <= set(value['patch']):
            return 'name'
        if name == 'update_node_data' and 'generation' in value['patch']:
            return 'duration'
        if name == 'xyq.generation.update_prompt':
            return 'prompt'
        return None

    def command(self, canvas_id, name, value=None):
        if name == 'get_snapshot':
            result = super().command(canvas_id, name, value)
            doc = result['document']
            for node_id in self.created_videos:
                if self.hide_created_before_name and node_id not in self.named_videos:
                    c.nodes(doc).pop(node_id, None)
                    doc['assets'].pop(node_id, None)
                    continue
                if self.hide_names_before_prompt and node_id not in self.prompted_videos:
                    for field in ('name', 'title', 'caption'):
                        c.content(doc, node_id).pop(field, None)
                if self.final_fault and node_id in self.prompted_videos:
                    data = c.content(doc, node_id)
                    if self.final_fault == 'missing_node':
                        c.nodes(doc).pop(node_id, None)
                        doc['assets'].pop(node_id, None)
                    elif self.final_fault == 'kind':
                        c.nodes(doc)[node_id]['type'] = 'biz/image'
                    elif self.final_fault in ('name', 'title', 'caption'):
                        data[self.final_fault] = 'Unexpected final field'
                    elif self.final_fault == 'duration':
                        data['generation']['durationMs'] = 999
                    elif self.final_fault == 'ratio':
                        data['generation']['ratio'] = '1:1'
                    elif self.final_fault == 'prompt':
                        data['generation']['prompt'] = 'Unexpected final prompt'
                    elif self.final_fault == 'references':
                        edges = doc['assets']['ROOT']['content']['edges']
                        for edge_id in list(edges):
                            if edges[edge_id].get('target') == node_id:
                                edges.pop(edge_id)
            return result
        stage = self.stage(name, value)
        if stage:
            previous = [entry for entry in c.read_state(self.project)['journal'] if entry['status'] != 'pending']
            if previous:
                self.ack_before_next_command.append((stage, previous[-1]['command'], previous[-1]['status']))
            if stage == self.unknown_ack_stage:
                self.calls.append(name)
                self.writes.append((name, copy.deepcopy(value)))
                c.fail('CLI_ERROR', 'fake command returned no confirmed ACK')
        result = super().command(canvas_id, name, value)
        if stage == 'create':
            node_id = value['id']
            self.created_videos.add(node_id)
            data = c.content(self.doc, node_id)
            data.update(name='Platform auto name', generation=copy.deepcopy(self.default_generation))
            data.pop('caption', None)
            data.pop('title', None)
        elif stage == 'name':
            self.named_videos.add(value['id'])
        elif stage == 'prompt':
            self.prompted_videos.add(value['nodeId'])
        return result


class VideoWriteReceiptChecks(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.project = Path(scratch.name)
        self.unit_id = '场景1-分镜1'
        self.session = {'ledger': {'schema_version': 3,
            'characters': [{'id': 'CHAR1', 'name': 'Actor'}],
            'scene_assets': [{'id': 'SET1', 'name': 'Room'}],
            'scenes': [{'id': 'SC1', 'scene_asset_id': 'SET1', 'prop_ids': ['PROP1']}],
            'props': [{'id': 'PROP1', 'name': 'Pen'}]}, 'receipts': [], 'units': [
                {'id': self.unit_id, 'version': 1, 'duration': 22, 'character_ids': ['CHAR1'],
                 'scene_ids': ['SC1'], 'prop_ids': ['PROP1'], 'global': 'standing', 'shots': [],
                 'test_prompt': '@Actor @Room @Pen action\n@黑幕'}]}
        doc = {'rootCanvasId': 'ROOT', 'assets': {'ROOT': {'type': 'canvas', 'content': {
            'nodes': {}, 'edges': {}, 'settings': {'readOnly': False}}}}}
        self.state = c.blank_state()
        self.state['binding'] = {'canvas_id': 'ROOT', 'account_id': 'USER'}
        for key, node_id, kind, name in [('CHAR1', 'ROLE', 'role', 'Actor'),
                ('SET1', 'SET', 'scene', 'Room'), ('PROP1', 'PROP', 'image', 'Pen')]:
            add_node(doc, node_id, kind, name)
            self.state['mappings'][key] = {'node_id': node_id, 'kind': kind, 'name': name, 'stage': 'ready'}
        add_node(doc, 'BLACK', 'video', '黑幕')
        self.state['black_node_id'] = 'BLACK'
        c.save(self.project, self.state)
        self.cli = DefaultVideoCLI(doc, self.project)

    def sync(self, **kwargs):
        def render(session, unit, reference_text=None):
            return reference_text(unit['test_prompt']) if reference_text else unit['test_prompt']
        with patch.object(c.d, 'load', return_value=self.session), \
             patch.object(c.d, 'render_unit', side_effect=render), \
             patch.object(c.media, 'model_settings', side_effect=AssertionError('No model lookup in canvas workflow')):
            return c.sync(self.project, self.cli, 'videos', ids=[self.unit_id], **kwargs)

    def target(self):
        return c.read_state(self.project)['mappings'][self.unit_id]['node_id']

    def target_writes(self, node_id):
        return [(name, value) for name, value in self.cli.writes
                if value.get('id', value.get('nodeId')) == node_id]

    def test_creation_uses_defaults_and_orders_name_duration_prompt_before_receipt(self):
        result = self.sync()
        self.assertEqual(result['status'], 'synced')
        node_id = self.target()
        writes = self.target_writes(node_id)
        self.assertEqual([name for name, _ in writes],
                         ['create_biz_node', 'update_node_data', 'update_node_data', 'xyq.generation.update_prompt'])
        self.assertEqual(writes[0][1]['initialData'], {})
        self.assertEqual(writes[1][1]['patch'], dict.fromkeys(('name', 'title', 'caption'), self.unit_id))
        generation = writes[2][1]['patch']['generation']
        self.assertEqual(generation['durationMs'], 22000)
        self.assertEqual({key: generation[key] for key in self.cli.default_generation}, self.cli.default_generation)
        data = c.content(self.cli.doc, node_id)
        self.assertEqual({key: data[key] for key in ('name', 'title', 'caption')},
                         dict.fromkeys(('name', 'title', 'caption'), self.unit_id))
        self.assertEqual(data['generation']['durationMs'], 22000)
        self.assertEqual(set(c.node_asset_ids(data['generation']['prompt'])), {'ROLE', 'SET', 'PROP', 'BLACK'})
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, node_id, data['generation']['prompt']))
        self.assertTrue(all(row['status'] == 'verified' for row in c.read_state(self.project)['journal']))
        self.assertEqual(len(result['video_write_receipts']), 1)
        receipt = json.loads(Path(result['video_write_receipts'][0]).read_text(encoding='utf-8'))
        self.assertEqual((receipt['key'], receipt['node_id'], receipt['status']), (self.unit_id, node_id, 'verified'))
        self.assertTrue(receipt['core_verified'])
        self.assertTrue(receipt['references_verified'])
        self.assertTrue(all(check['matched'] for check in receipt['checks']))

    def test_confirmed_previous_command_ack_is_saved_before_next_write(self):
        self.sync()
        self.assertEqual([(stage, command, status) for stage, command, status in self.cli.ack_before_next_command], [
            ('name', 'create_biz_node', 'acknowledged'),
            ('duration', 'update_node_data', 'acknowledged'),
            ('prompt', 'update_node_data', 'acknowledged')])

    def test_delayed_create_snapshot_does_not_block_name_and_prompt_writes(self):
        self.cli.hide_created_before_name = True
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(len(self.cli.allocations), 1)
        self.assertTrue(c.prompt_of(self.cli.doc, self.target()))

    def test_intermediate_missing_names_does_not_block_generation_and_prompt_writes(self):
        self.cli.hide_names_before_prompt = True
        self.assertEqual(self.sync()['status'], 'synced')
        node_id = self.target()
        self.assertEqual(c.content(self.cli.doc, node_id)['generation']['parameters'], {'strength': .6})
        self.assertTrue(c.prompt_of(self.cli.doc, node_id))

    def test_unknown_ack_stops_at_exact_stage_without_replay_or_delete(self):
        for stage, expected_count in [('create', 1), ('name', 2), ('duration', 3), ('prompt', 4)]:
            with self.subTest(stage=stage):
                self.setUp()
                self.cli.unknown_ack_stage = stage
                with self.assertRaises(c.CanvasError) as stopped:
                    self.sync()
                self.assertEqual(stopped.exception.code, 'UNCERTAIN_WRITE')
                node_id = self.target()
                self.assertEqual(len(self.target_writes(node_id)), expected_count)
                state = c.read_state(self.project)
                self.assertEqual(state['journal'][-1]['status'], 'uncertain')
                self.assertEqual(state['last_reference_failures'][-1]['node_id'], node_id)
                receipt = json.loads(Path(stopped.exception.details['video_receipt']).read_text(encoding='utf-8'))
                self.assertEqual((receipt['node_id'], receipt['status']), (node_id, 'uncertain'))
                self.assertEqual(receipt['steps'][-1], state['journal'][-1]['id'])
                self.assertEqual(DefaultVideoCLI.stage(state['journal'][-1]['command'], state['journal'][-1]['input']), stage)
                self.assertNotIn('delete_nodes', [name for name, _ in self.cli.writes])
                before = copy.deepcopy(self.cli.writes)
                with self.assertRaises(c.CanvasError):
                    self.sync()
                self.assertEqual(self.cli.writes, before)
                self.assertEqual(self.cli.allocations, [node_id])

    def test_final_metadata_identity_duration_and_prompt_failures_keep_id_and_ack_progress(self):
        for field in ('missing_node', 'kind', 'name', 'title', 'caption', 'duration', 'prompt'):
            with self.subTest(field=field):
                self.setUp()
                self.cli.final_fault = field
                with self.assertRaises(c.CanvasError) as stopped:
                    self.sync()
                self.assertEqual(stopped.exception.code, 'UNCERTAIN_WRITE')
                node_id = self.target()
                state = c.read_state(self.project)
                self.assertEqual(state['last_reference_failures'][-1]['node_id'], node_id)
                self.assertEqual([name for name, _ in self.target_writes(node_id)],
                                 ['create_biz_node', 'update_node_data', 'update_node_data', 'xyq.generation.update_prompt'])
                self.assertEqual([entry['status'] for entry in state['journal'][:-1]], ['acknowledged'] * 4)
                self.assertEqual((state['journal'][-1]['command'], state['journal'][-1]['status']),
                                 ('video_write_receipt', 'uncertain'))
                receipt = json.loads(Path(stopped.exception.details['video_receipt']).read_text(encoding='utf-8'))
                self.assertEqual((receipt['node_id'], receipt['status']), (node_id, 'uncertain'))
                self.assertFalse(receipt['core_verified'])
                self.assertEqual(receipt['attempts'], 3)
                self.assertLessEqual(self.cli.calls.count('get_snapshot'), 8)
                self.assertTrue(Path(stopped.exception.details['reference_report']).is_file())
                self.cli.final_fault = None
                before = copy.deepcopy(self.cli.writes)
                self.assertEqual(self.sync()['status'], 'synced')
                self.assertEqual(self.target(), node_id)
                self.assertEqual(self.cli.allocations, [node_id])
                self.assertFalse(any(name == 'create_biz_node' for name, _ in self.cli.writes[len(before):]))
                self.assertTrue(all(entry['status'] == 'verified' for entry in c.read_state(self.project)['journal']))

    def test_reference_failure_is_partial_with_exact_receipt_and_no_duplicate_creation(self):
        self.cli.final_fault = 'references'
        result = self.sync()
        self.assertEqual(result['status'], 'partial')
        node_id = self.target()
        state = c.read_state(self.project)
        self.assertIn(self.unit_id, state['pending_units'])
        self.assertFalse(state['mappings'][self.unit_id]['references_bound'])
        self.assertTrue(any(row['node_id'] == node_id and row['reason'] == 'reference_failed'
                            for row in result['failures']))
        receipt = json.loads(Path(result['video_write_receipts'][0]).read_text(encoding='utf-8'))
        self.assertEqual((receipt['node_id'], receipt['status']), (node_id, 'partial'))
        self.assertTrue(receipt['core_verified'])
        self.assertFalse(receipt['references_verified'])
        self.cli.final_fault = None
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(self.cli.allocations, [node_id])
        self.assertEqual(sum(name == 'create_biz_node' for name, _ in self.cli.writes), 1)

    def test_existing_allocated_auto_named_video_is_reused_without_create(self):
        add_node(self.cli.doc, 'OLD', 'video', 'Platform auto name', generation=copy.deepcopy(self.cli.default_generation))
        self.state['mappings'][self.unit_id] = {'node_id': 'OLD', 'kind': 'video',
            'name': self.unit_id, 'stage': 'allocated'}
        self.state['journal'].append({'id': 'stopped-old-create', 'command': 'create_biz_node',
            'status': 'uncertain', 'input': {'id': 'OLD', 'nodeKind': 'video'},
            'targets': [{'action': 'create', 'node_id': 'OLD', 'kind': 'video', 'name': self.unit_id}]})
        c.save(self.project, self.state)
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(self.target(), 'OLD')
        self.assertEqual(self.cli.allocations, [])
        self.assertFalse(any(name == 'create_biz_node' for name, _ in self.cli.writes))
        self.assertEqual(c.name_of(self.cli.doc, 'OLD'), self.unit_id)
        self.assertTrue(c.prompt_of(self.cli.doc, 'OLD'))

    def legacy_allocated_create(self, present_kind=None, mixed_targets=False):
        if present_kind is not None:
            add_node(self.cli.doc, 'OLD', present_kind, 'Platform auto name',
                     generation=copy.deepcopy(self.cli.default_generation) if present_kind == 'video' else None)
        self.state['mappings'][self.unit_id] = {'node_id': 'OLD', 'kind': 'video',
            'name': self.unit_id, 'stage': 'allocated'}
        entry = {'id': 'deleted-legacy-create', 'command': 'create_biz_node', 'status': 'uncertain',
            'input': {'id': 'OLD', 'nodeKind': 'video', 'initialData': {'name': self.unit_id}},
            'targets': [{'action': 'create', 'node_id': 'OLD', 'kind': 'video', 'name': self.unit_id}],
            'error': 'Previous create receipt was unconfirmed'}
        if mixed_targets:
            entry['targets'].append({'action': 'node_kind', 'node_id': 'ROLE', 'kind': 'role'})
        self.state['journal'].append(copy.deepcopy(entry))
        c.save(self.project, self.state)
        return entry

    def test_exact_recreate_deleted_legacy_allocated_video_preserves_superseded_entry_and_new_id(self):
        previous = self.legacy_allocated_create()
        result = self.sync(decisions={self.unit_id: {'recreate_missing': 'OLD'}})
        self.assertEqual(result['status'], 'synced')
        node_id = self.target()
        self.assertNotEqual(node_id, 'OLD')
        self.assertEqual(self.cli.allocations, [node_id])
        state = c.read_state(self.project)
        saved = next(entry for entry in state['journal'] if entry['id'] == previous['id'])
        self.assertEqual(saved['status'], 'superseded')
        self.assertEqual(saved['input'], previous['input'])
        self.assertEqual(saved['targets'], previous['targets'])
        self.assertEqual(saved['error'], previous['error'])
        creates = [value for name, value in self.cli.writes if name == 'create_biz_node']
        self.assertEqual([value['id'] for value in creates], [node_id])
        self.assertFalse(any(value.get('id', value.get('nodeId')) == 'OLD' for _, value in self.cli.writes))
        self.assertNotIn('delete_nodes', [name for name, _ in self.cli.writes])
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, node_id, c.prompt_of(self.cli.doc, node_id)))
        receipt = json.loads(Path(result['video_write_receipts'][0]).read_text(encoding='utf-8'))
        self.assertEqual((receipt['node_id'], receipt['status']), (node_id, 'verified'))

    def test_deleted_legacy_allocated_video_requires_exact_id_and_single_target_authorization(self):
        for decision, mixed in [(None, False), ({'recreate_missing': 'WRONG'}, False),
                                 ({'recreate_missing': 'OLD'}, True)]:
            with self.subTest(decision=decision, mixed_targets=mixed):
                self.setUp()
                previous = self.legacy_allocated_create(mixed_targets=mixed)
                with self.assertRaises(c.CanvasError) as stopped:
                    self.sync(decisions={self.unit_id: decision} if decision is not None else None)
                self.assertEqual(stopped.exception.code, 'UNCERTAIN_WRITE')
                self.assertEqual(self.target(), 'OLD')
                saved = next(entry for entry in c.read_state(self.project)['journal'] if entry['id'] == previous['id'])
                self.assertEqual(saved['status'], 'uncertain')
                self.assertEqual(self.cli.allocations, [])
                self.assertEqual(self.cli.writes, [])

    def test_recreate_missing_cannot_supersede_when_old_node_still_exists(self):
        for kind in ('video', 'image'):
            with self.subTest(kind=kind):
                self.setUp()
                previous = self.legacy_allocated_create(present_kind=kind)
                if kind == 'video':
                    self.assertEqual(self.sync(decisions={self.unit_id: {'recreate_missing': 'OLD'}})['status'], 'synced')
                    self.assertEqual(self.target(), 'OLD')
                else:
                    with self.assertRaises(c.CanvasError) as stopped:
                        self.sync(decisions={self.unit_id: {'recreate_missing': 'OLD'}})
                    self.assertEqual(stopped.exception.code, 'UNCERTAIN_WRITE')
                    self.assertEqual(self.cli.writes, [])
                saved = next(entry for entry in c.read_state(self.project)['journal'] if entry['id'] == previous['id'])
                self.assertNotEqual(saved['status'], 'superseded')
                self.assertEqual(self.cli.allocations, [])
                self.assertFalse(any(name in ('create_biz_node', 'delete_nodes') for name, _ in self.cli.writes))

    def test_ready_nodes_keep_manual_name_prompt_and_duration_conflict_protection(self):
        for field in ('name', 'prompt', 'duration'):
            with self.subTest(field=field):
                self.setUp()
                self.sync()
                node_id = self.target()
                data = c.content(self.cli.doc, node_id)
                if field == 'name':
                    data['name'] = 'Manually changed name'
                elif field == 'prompt':
                    data['generation']['prompt'] = 'Manually changed prompt'
                else:
                    data['generation']['durationMs'] = 27000
                self.cli.writes.clear()
                result = self.sync()
                self.assertEqual(result['status'], 'needs_decision')
                self.assertTrue(any(row['node_id'] == node_id for row in result['conflicts']))
                self.assertEqual(self.cli.writes, [])

    def test_interrupted_create_and_name_acks_do_not_absorb_manual_prompt_on_resume(self):
        with patch.object(c, 'video_snapshot', side_effect=KeyboardInterrupt('interrupt after create/name ACKs')):
            with self.assertRaises(KeyboardInterrupt):
                self.sync()
        node_id = self.target()
        self.assertEqual([name for name, _ in self.target_writes(node_id)],
                         ['create_biz_node', 'update_node_data'])
        self.assertEqual([entry['status'] for entry in c.read_state(self.project)['journal']],
                         ['acknowledged', 'acknowledged'])
        manual_prompt = 'User prompt supplied after the interrupted name write'
        c.content(self.cli.doc, node_id)['generation']['prompt'] = manual_prompt
        self.cli.writes.clear()
        result = self.sync()
        self.assertEqual(result['status'], 'needs_decision')
        self.assertTrue(any(row['node_id'] == node_id and row['reason'] == 'cloud_edited'
                            and 'prompt' in row['fields'] for row in result['conflicts']))
        self.assertEqual(c.prompt_of(self.cli.doc, node_id), manual_prompt)
        self.assertEqual(self.cli.writes, [])
        self.assertEqual(self.cli.allocations, [node_id])
        conflict = next(row for row in result['conflicts'] if row['node_id'] == node_id)
        approved = self.sync(decisions={self.unit_id: {'overwrite_fingerprint': conflict['fingerprint']}})
        self.assertEqual(approved['status'], 'synced')
        self.assertEqual(self.target(), node_id)
        self.assertEqual(self.cli.allocations, [node_id])
        self.assertFalse(any(name == 'create_biz_node' for name, _ in self.cli.writes))
        restored = c.prompt_of(self.cli.doc, node_id)
        self.assertNotEqual(restored, manual_prompt)
        self.assertIn('action', restored)
        self.assertEqual(set(c.node_asset_ids(restored)), {'ROLE', 'SET', 'PROP', 'BLACK'})
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, node_id, restored))

    def test_ready_title_or_caption_manual_edit_is_a_name_conflict_without_overwrite(self):
        for field, manual_value in [('title', 'User changed only title'),
                                    ('caption', 'User changed only caption'), ('title', None), ('caption', None)]:
            with self.subTest(field=field, value=manual_value):
                self.setUp()
                self.sync()
                node_id = self.target()
                data = c.content(self.cli.doc, node_id)
                if manual_value is None:
                    data.pop(field)
                else:
                    data[field] = manual_value
                self.assertEqual(data['name'], self.unit_id)
                self.cli.writes.clear()
                result = self.sync()
                self.assertEqual(result['status'], 'needs_decision')
                self.assertTrue(any(row['node_id'] == node_id and row['reason'] == 'cloud_edited'
                                    and 'name' in row['fields'] for row in result['conflicts']))
                self.assertEqual(data.get(field), manual_value)
                self.assertEqual(self.cli.writes, [])

    def test_ready_platform_model_and_resolution_changes_are_not_conflicts_or_overridden(self):
        self.sync()
        node_id = self.target()
        generation = c.content(self.cli.doc, node_id)['generation']
        generation.update(modelKey='another-platform-default', resolution='2160p')
        self.cli.writes.clear()
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual((generation['modelKey'], generation['resolution']), ('another-platform-default', '2160p'))
        self.assertEqual(self.cli.writes, [])


if __name__ == '__main__':
    unittest.main()
