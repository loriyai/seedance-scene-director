#!/usr/bin/env python3
"""Offline grouped asset writes: complete fields, conflicts, and interruptions."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import canvas as c
import canvas_job_backend as backend
import canvas_jobs as jobs
from canvas_jobs import PauseRequested
from test_canvas_reference_workflow import WorkflowCLI, add_node


class AssetJobCLI(WorkflowCLI):
    def __init__(self, doc):
        super().__init__(doc)
        self.check_control = lambda: None
        self.allocation_batches = []
        self.final_fault = None
        self.unknown_command = None
        self.unknown_field = None
        self.factory_name = None
        self.pause_after_create = False
        self.pause_after_allocate = False
        self.paused = False
        self.check_control = self.control

    def control(self):
        if self.paused:
            raise PauseRequested()

    def allocate_many(self, count):
        self.allocation_batches.append(count)
        result = [self.allocate() for _ in range(count)]
        if self.pause_after_allocate:
            self.paused = True
        return result

    def command(self, canvas_id, name, value=None):
        if name == self.unknown_command and (self.unknown_field is None or value.get('field') == self.unknown_field):
            self.calls.append(name)
            self.writes.append((name, copy.deepcopy(value)))
            c.fail('CLI_ERROR', 'fake unknown command ACK')
        if name in ('xyq.role.updateDescription', 'xyq.scene.updateDescription'):
            self.calls.append(name)
            self.writes.append((name, copy.deepcopy(value)))
            data = c.content(self.doc, value['nodeId'])
            field = value['field']
            if field in ('characterName', 'sceneName'):
                data['nodeName'] = value['value']
                data.setdefault('basicInfo', {})['name'] = value['value']
                if c.nodes(self.doc)[value['nodeId']].get('data'):
                    c.nodes(self.doc)[value['nodeId']]['data']['name'] = value['value']
            elif field in ('appearanceName', 'sceneViewName'):
                data.setdefault('basicInfo', {})['appearanceName'] = value['value']
            elif field in ('appearance', 'description'):
                data.setdefault('appearanceInfo', {})['description'] = value['value']
            else:
                raise AssertionError('Unexpected asset field: ' + field)
            return {}
        result = super().command(canvas_id, name, value)
        if name == 'create_biz_node':
            if self.factory_name and value['nodeKind'] in ('role', 'scene'):
                c.nodes(self.doc)[value['id']]['data'] = {'name': self.factory_name}
            if self.pause_after_create:
                self.paused = True
        if (name == 'get_snapshot' and self.final_fault
                and any(n in ('xyq.generation.update_prompt', 'xyq.role.updateAppearance') or v.get('field') in ('characterName', 'sceneName')
                        for n, v in self.writes)):
            self.final_fault(result['document'])
        return result


class AssetJobBackendChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.session = {'ledger': {'schema_version': 3,
            'characters': [{'id': 'CHAR1', 'name': 'Actor', 'image_prompt': 'Actor full reference'}],
            'scene_assets': [{'id': 'SET1', 'name': 'Room', 'image_prompt': 'Room complete set'}],
            'props': [{'id': 'PROP1', 'name': 'Pen', 'image_prompt': 'Pen complete object'}],
            'scenes': []}, 'units': [], 'receipts': []}
        doc = {'rootCanvasId': 'ROOT', 'assets': {'ROOT': {'type': 'canvas', 'content': {
            'nodes': {}, 'edges': {}, 'settings': {'readOnly': False}}}}}
        self.cli = AssetJobCLI(doc)
        self.state = c.blank_state()
        self.state['binding'] = {'canvas_id': 'ROOT', 'account_id': 'USER'}
        c.save(self.project, self.state)
        self.progress = []
        attention = patch.object(jobs, 'notify_attention')
        attention.start()
        self.addCleanup(attention.stop)

    def sync(self, **kwargs):
        return backend.sync_assets(self.project, self.cli, self.session,
                                   lambda value: self.progress.append(copy.deepcopy(value)), **kwargs)

    def only_role(self):
        self.session['ledger']['scene_assets'] = []
        self.session['ledger']['props'] = []

    def existing_role(self, prompt='', view=''):
        self.only_role()
        add_node(self.cli.doc, 'ROLE', 'role', 'Actor')
        data = c.content(self.cli.doc, 'ROLE')
        data['appearanceInfo'] = {'description': 'Independent appearance description'}
        data['generation']['prompt'] = prompt
        data['basicInfo']['appearanceName'] = view
        self.state['mappings']['CHAR1'] = {'node_id': 'ROLE', 'kind': 'role', 'name': 'Actor',
                                         'stage': 'ready', 'prompt_hash': c.digest(prompt)}
        c.save(self.project, self.state)

    def test_all_fields_are_written_and_group_readback_is_bounded(self):
        with patch.object(c, 'checkpoint', wraps=c.checkpoint) as checkpoint:
            result = self.sync(batch_size=2)
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.cli.allocation_batches, [2, 1])
        self.assertEqual(self.cli.calls.count('get_snapshot'), 7)  # Plus business readiness for role/scene.
        checkpoint.assert_called_once()
        state = c.read_state(self.project)
        for item in backend.asset_items(self.session):
            mapping = state['mappings'][item['key']]
            self.assertEqual(mapping['stage'], 'ready')
            self.assertTrue(all(c.target_satisfied(self.cli.doc, target)
                                for target in backend.asset_targets(item, mapping['node_id'])))
            self.assertEqual(mapping['prompt_hash'], c.digest(item['image_prompt']))
        self.assertTrue(all(e['status'] == 'verified' for e in state['journal']))
        self.assertEqual(self.progress[-1]['completed'], 3)

    def test_new_assets_receive_image_defaults_and_their_own_complete_prompts(self):
        self.sync()
        state = c.read_state(self.project)
        for key, prompt in (('CHAR1', 'Actor full reference'), ('SET1', 'Room complete set'),
                            ('PROP1', 'Pen complete object')):
            with self.subTest(asset=key):
                data = c.content(self.cli.doc, state['mappings'][key]['node_id'])
                self.assertEqual({k: data['generation'][k] for k in ('resolution', 'effort', 'ratio')},
                                 {'resolution': '2K', 'effort': 'high', 'ratio': '16:9'})
                self.assertEqual(data['generation']['prompt'], prompt)
        self.assertFalse(any('generate' in command for command, value in self.cli.writes))

    def test_missing_or_wrong_image_defaults_cannot_complete_any_asset_kind(self):
        for category in ('characters', 'scene_assets', 'props'):
            for field in ('resolution', 'effort', 'ratio'):
                with self.subTest(category=category, field=field):
                    self.setUp()
                    for other in ('characters', 'scene_assets', 'props'):
                        if other != category:
                            self.session['ledger'][other] = []
                    def corrupt(doc, key=field):
                        for node_id in c.nodes(doc):
                            c.content(doc, node_id)['generation'].pop(key, None)
                    self.cli.final_fault = corrupt
                    with self.assertRaises(c.CanvasError):
                        self.sync()
                    self.assertFalse(any(m.get('stage') == 'ready'
                                         for m in c.read_state(self.project)['mappings'].values()))

    def test_ready_asset_custom_image_parameters_survive_prompt_update(self):
        self.existing_role()
        generation = {'resolution': '4K', 'effort': 'low', 'ratio': '1:1',
                      'modelKey': 'user-selected', 'references': [{'id': 'ref'}]}
        c.content(self.cli.doc, 'ROLE')['generation'] = copy.deepcopy(generation)
        self.sync()
        self.assertEqual(c.content(self.cli.doc, 'ROLE')['generation'], {**generation, 'prompt': 'Actor full reference'})

    def test_video_preparation_creates_assets_with_defaults_and_prompts(self):
        state, doc = c.connected(self.project, self.cli)
        doc, summary = c.reconcile_asset_nodes(self.project, self.cli, self.session, state, doc)
        self.assertEqual(len(summary['created']), 3)
        for key, prompt in (('CHAR1', 'Actor full reference'), ('SET1', 'Room complete set'),
                            ('PROP1', 'Pen complete object')):
            data = c.content(doc, state['mappings'][key]['node_id'])
            self.assertEqual({k: data['generation'][k] for k in ('resolution', 'effort', 'ratio')},
                             {'resolution': '2K', 'effort': 'high', 'ratio': '16:9'})
            self.assertEqual(data['generation']['prompt'], prompt)
        writes = len(self.cli.writes)
        c.content(self.cli.doc, state['mappings']['CHAR1']['node_id'])['generation']['ratio'] = '1:1'
        doc = c.document(self.cli, state['binding'])
        doc, summary = c.reconcile_asset_nodes(self.project, self.cli, self.session, state, doc)
        self.assertEqual(len(self.cli.writes), writes)

    def test_direct_assets_creation_writes_defaults_and_prompts(self):
        result = c.sync(self.project, self.cli, 'assets', session_override=self.session)
        self.assertEqual(result['status'], 'synced')
        state = c.read_state(self.project)
        for key in ('CHAR1', 'SET1', 'PROP1'):
            data = c.content(self.cli.doc, state['mappings'][key]['node_id'])
            self.assertEqual(data['generation']['resolution'], '2K')
            self.assertEqual(data['generation']['effort'], 'high')
            self.assertEqual(data['generation']['ratio'], '16:9')
            self.assertTrue(data['generation'].get('prompt'))

    def test_every_role_field_is_required_for_final_completion(self):
        def change(doc, field):
            nid = next(iter(c.nodes(doc)))
            data = c.content(doc, nid)
            if field == 'kind':
                c.nodes(doc)[nid]['type'] = 'biz/image'
            elif field == 'displayName':
                c.nodes(doc)[nid]['data'] = {'name': 'Wrong display name'}
            elif field == 'basicName':
                data['basicInfo']['name'] = 'Wrong basic name'
            elif field == 'view':
                data['basicInfo']['appearanceName'] = 'Unexpected view'
            else:
                data['generation']['prompt'] = 'Wrong complete prompt'
        for field in ('kind', 'displayName', 'basicName', 'view', 'prompt'):
            with self.subTest(field=field):
                self.setUp()
                self.only_role()
                self.cli.final_fault = lambda doc, f=field: change(doc, f)
                with self.assertRaises(c.CanvasError):
                    self.sync()
                self.assertNotEqual(c.read_state(self.project)['mappings']['CHAR1']['stage'], 'ready')
                self.assertFalse(any(p.get('completed') == 1 for p in self.progress))

    def test_different_nonempty_prompt_is_not_overwritten_even_with_saved_hash(self):
        self.existing_role(prompt='Human edited description')
        self.state['mappings']['CHAR1']['prompt_hash'] = c.digest('Actor full reference')
        c.save(self.project, self.state)
        with self.assertRaises(c.CanvasError) as failure:
            self.sync()
        self.assertEqual(failure.exception.code, 'CONCURRENT_EDIT')
        self.assertEqual(self.cli.writes, [])
        self.assertEqual(c.content(self.cli.doc, 'ROLE')['generation']['prompt'], 'Human edited description')

    def test_every_image_field_is_required_for_final_completion(self):
        for field in ('name', 'caption', 'prompt'):
            with self.subTest(field=field):
                self.setUp()
                self.session['ledger']['characters'] = []
                self.session['ledger']['scene_assets'] = []
                def change(doc, field=field):
                    nid = next(iter(c.nodes(doc)))
                    data = c.content(doc, nid)
                    if field == 'prompt':
                        data['generation']['prompt'] = 'Incorrect prompt'
                    else:
                        data[field] = 'Incorrect ' + field
                self.cli.final_fault = change
                with self.assertRaises(c.CanvasError):
                    self.sync()
                self.assertNotEqual(c.read_state(self.project)['mappings']['PROP1']['stage'], 'ready')
                self.assertFalse(any(p.get('completed') == 1 for p in self.progress))

    def test_exact_explicit_fingerprint_allows_prompt_change(self):
        self.existing_role(prompt='Previous approved description')
        fingerprint = c.fingerprint(self.cli.doc, 'ROLE')
        result = self.sync(decisions={'CHAR1': {'overwrite_fingerprint': fingerprint}})
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.cli.allocations, [])
        self.assertEqual(c.content(self.cli.doc, 'ROLE')['generation']['prompt'], 'Actor full reference')

    def test_exact_complete_nodes_are_read_back_without_rewriting(self):
        self.existing_role(prompt='Actor full reference')
        result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.cli.writes, [])
        self.assertEqual(self.cli.allocations, [])
        self.assertEqual(self.cli.calls.count('get_snapshot'), 1)
        self.assertEqual(self.progress[-1]['completed'], 1)

    def test_unknown_ack_stops_next_node_and_resume_never_replays(self):
        self.cli.unknown_command = 'xyq.role.updateAppearance'
        self.cli.unknown_field = None
        with self.assertRaises(c.CanvasError) as first:
            self.sync()
        self.assertEqual(first.exception.code, 'UNCERTAIN_WRITE')
        before = copy.deepcopy(self.cli.writes)
        state = c.read_state(self.project)
        self.assertEqual(state['journal'][-1]['status'], 'uncertain')
        self.cli.unknown_command = None
        with self.assertRaises(c.CanvasError) as resumed:
            self.sync()
        self.assertEqual(resumed.exception.code, 'UNCERTAIN_WRITE')
        self.assertEqual(self.cli.writes, before)

    def test_pause_before_dispatch_does_not_create_pending_journal(self):
        self.cli.paused = True
        with self.assertRaises(PauseRequested):
            self.sync()
        self.assertEqual(self.cli.writes, [])
        self.assertEqual(c.read_state(self.project)['journal'], [])

    def test_pause_after_id_reservation_reuses_the_reserved_id(self):
        self.only_role()
        self.cli.pause_after_allocate = True
        with self.assertRaises(PauseRequested):
            self.sync()
        state = c.read_state(self.project)
        reserved = state['mappings']['CHAR1']['node_id']
        self.assertEqual(state['mappings']['CHAR1']['stage'], 'allocated')
        self.assertEqual(state['journal'], [])
        self.cli.pause_after_allocate = self.cli.paused = False
        result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.cli.allocations, [reserved])
        self.assertEqual(c.read_state(self.project)['mappings']['CHAR1']['node_id'], reserved)

    def test_pause_after_create_ack_with_factory_name_resumes_same_id(self):
        self.only_role()
        self.cli.factory_name = '未命名角色'
        self.cli.pause_after_create = True
        with self.assertRaises(PauseRequested):
            self.sync()
        reserved = self.cli.allocations[0]
        self.cli.pause_after_create = self.cli.paused = False
        result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.cli.allocations, [reserved])
        self.assertEqual(sum(name == 'create_biz_node' for name, _ in self.cli.writes), 1)
        self.assertEqual(c.name_of(self.cli.doc, reserved), 'Actor')

    def test_explicit_queue_resume_still_requires_reconcile_before_unknown_write(self):
        self.only_role()
        self.cli.unknown_command = 'xyq.role.updateAppearance'
        self.cli.unknown_field = None
        def runner(project, manifest, check_control, progress):
            return backend.sync_assets(project, self.cli, manifest['session'], progress)
        with patch.object(jobs.d, 'load', return_value=self.session), \
             patch.object(jobs, 'notify_completion') as notify:
            queued = jobs.enqueue(self.project, 'assets', request_id='unknown-resume', start=False)
            jobs.run_worker(self.project, runner=runner)
            before = copy.deepcopy(self.cli.writes)
            self.cli.unknown_command = None
            jobs.resume(self.project, start=False)
            jobs.run_worker(self.project, runner=runner)
            current = jobs.status(self.project)
        row = next(j for j in current['jobs'] if j['job_id'] == queued['job_id'])
        self.assertEqual(row['status'], 'needs_attention')
        self.assertEqual(row['error']['code'], 'UNCERTAIN_WRITE')
        self.assertEqual(self.cli.writes, before)
        self.assertTrue(all(e['event'] == 'needs_attention' for e in current['events']))
        notify.assert_not_called()

    def test_replacement_job_does_not_bypass_unknown_canvas_journal(self):
        self.only_role()
        self.cli.unknown_command = 'xyq.role.updateAppearance'
        self.cli.unknown_field = None
        def runner(project, manifest, check_control, progress):
            return backend.sync_assets(project, self.cli, manifest['session'], progress,
                                       decisions=manifest['options']['decisions'])
        with patch.object(jobs.d, 'load', return_value=self.session), \
             patch.object(jobs, 'notify_completion') as notify:
            original = jobs.enqueue(self.project, 'assets', request_id='uncertain-replaced', start=False)
            jobs.run_worker(self.project, runner=runner)
            before = copy.deepcopy(self.cli.writes)
            self.cli.unknown_command = None
            replacement = jobs.enqueue(self.project, 'assets', request_id='uncertain-replaced', start=False,
                decisions={'CHAR1': {'overwrite_fingerprint': c.fingerprint(self.cli.doc, self.cli.allocations[0])}})
            jobs.run_worker(self.project, runner=runner)
            rows = {j['job_id']: j for j in jobs.status(self.project)['jobs']}
        self.assertEqual(rows[original['job_id']]['status'], 'superseded')
        self.assertEqual(rows[replacement['job_id']]['status'], 'needs_attention')
        self.assertEqual(rows[replacement['job_id']]['error']['code'], 'UNCERTAIN_WRITE')
        self.assertEqual(self.cli.writes, before)
        notify.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
