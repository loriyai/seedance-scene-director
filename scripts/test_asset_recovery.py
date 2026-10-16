#!/usr/bin/env python3
"""Offline reproduction of create ACK -> scene refusal -> same-ID recovery."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import canvas as c
import canvas_job_backend as backend
import canvas_jobs as jobs
import test_canvas_job_backend as asset_tests


REFUSAL = {'ok': False, 'code': 'INVARIANT_FAILED', 'message': 'scene asset not found', 'revision': 1}


class ReadinessCLI(asset_tests.AssetJobCLI):
    def __init__(self, doc):
        super().__init__(doc)
        self.refusals = 0
        self.refusal_field = 'sceneName'
        self.refusal = REFUSAL
        self.edit_on_refusal = False
        self.apply_then_refuse = False
        self.delay_reads = 0
        self.delayed = None
        self.create_count = 0

    def command(self, canvas_id, name, value=None):
        if name == 'get_snapshot' and self.delayed:
            node_id, asset = self.delayed
            if self.delay_reads <= 0:
                self.doc['assets'][node_id] = asset
                self.delayed = None
            else:
                self.delay_reads -= 1
        if name == 'xyq.scene.updateDescription' and value['field'] == self.refusal_field and self.refusals:
            self.refusals -= 1
            if self.apply_then_refuse:
                super().command(canvas_id, name, value)
            else:
                self.calls.append(name)
                self.writes.append((name, copy.deepcopy(value)))
            if self.edit_on_refusal:
                c.content(self.doc, value['nodeId'])['appearanceInfo'] = {'description': 'Human edit'}
            c.fail('COMMAND_REJECTED', json.dumps(self.refusal), rejection=self.refusal)
        result = super().command(canvas_id, name, value)
        if name == 'create_biz_node':
            self.create_count += 1
            if self.delay_reads:
                self.delayed = (value['id'], self.doc['assets'].pop(value['id']))
        return result


class AssetRecoveryChecks(unittest.TestCase):
    setUp = asset_tests.AssetJobBackendChecks.setUp
    sync = asset_tests.AssetJobBackendChecks.sync

    def scene_only(self):
        self.session['ledger']['characters'] = []
        self.session['ledger']['props'] = []
        self.cli = ReadinessCLI(self.cli.doc)
        self.cli.factory_name = '未命名场景'

    def legacy_failure(self):
        self.scene_only()
        self.cli.unknown_command = 'xyq.scene.updateDescription'
        self.cli.unknown_field = 'sceneName'
        with self.assertRaises(c.CanvasError):
            self.sync()
        self.cli.unknown_command = None
        state = c.read_state(self.project)
        state['journal'][-1]['error'] = json.dumps(REFUSAL)
        state['journal'][-1].pop('baseline', None)  # Old versions did not store this.
        c.save(self.project, state)
        return state['journal'][-1]['id'], self.cli.allocations[0]

    def test_structured_negative_is_classified_before_nonzero_exit(self):
        result = SimpleNamespace(stdout=json.dumps(REFUSAL), stderr='', returncode=1)
        with patch.object(c.subprocess, 'run', return_value=result):
            with self.assertRaises(c.CanvasError) as failure:
                c.run_json(['fake'], self.project)
        self.assertEqual(failure.exception.code, 'COMMAND_REJECTED')
        self.assertEqual(failure.exception.details['rejection'], REFUSAL)

    def test_rejection_metadata_does_not_retain_credentials(self):
        evidence = c.rejection_details({**REFUSAL, 'authorization': 'secret', 'message': 'token=private'})
        self.assertNotIn('authorization', evidence)
        self.assertNotIn('private', evidence['message'])

    def test_ready_wait_precedes_business_write_and_is_bounded(self):
        self.scene_only()
        self.cli.delay_reads = 2
        with patch.object(backend.time, 'sleep') as sleep:
            result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(self.cli.create_count, 1)

    def test_ready_timeout_then_resume_reuses_original_node(self):
        self.scene_only()
        self.cli.delay_reads = 20
        with patch.object(backend.time, 'sleep') as sleep:
            with self.assertRaises(c.CanvasError) as failure:
                self.sync()
        self.assertEqual(failure.exception.code, 'ASSET_NOT_READY')
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual([name for name, _ in self.cli.writes], ['create_biz_node'])
        node_id = self.cli.allocations[0]
        self.cli.delay_reads = 1
        with patch.object(backend.time, 'sleep'):
            result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.cli.allocations, [node_id])
        self.assertEqual(self.cli.create_count, 1)

    def test_known_scene_refusal_retries_same_id_then_full_completion(self):
        self.scene_only()
        self.cli.refusals = 2
        with patch.object(backend.time, 'sleep'):
            result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.cli.create_count, 1)
        names = [value for name, value in self.cli.writes if value.get('field') == 'sceneName']
        self.assertEqual(len(names), 3)
        self.assertEqual(len({v['nodeId'] for v in names}), 1)
        rejected = [e for e in c.read_state(self.project)['journal'] if e['status'] == 'rejected']
        self.assertEqual(len(rejected), 2)
        self.assertTrue(all(e.get('resolved_by') for e in rejected))
        before = copy.deepcopy(self.cli.writes)
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(self.cli.writes, before)

    def test_retry_exhaustion_is_rejected_and_explicit_resume_does_not_recreate(self):
        self.scene_only()
        self.cli.refusals = 10
        with patch.object(backend.time, 'sleep'):
            with self.assertRaises(c.CanvasError) as failure:
                self.sync()
        self.assertEqual(failure.exception.code, 'COMMAND_REJECTED')
        self.assertEqual(self.cli.refusals, 7)
        self.assertEqual(c.read_state(self.project)['journal'][-1]['status'], 'rejected')
        self.cli.refusals = 0
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(self.cli.create_count, 1)
        self.assertEqual(len(self.cli.allocations), 1)

    def test_rejection_on_description_accounts_for_prior_name_ack(self):
        self.scene_only()
        self.cli.refusal_field = 'description'
        self.cli.refusals = 1
        with patch.object(backend.time, 'sleep'):
            self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(sum(v.get('field') == 'sceneName' for _, v in self.cli.writes), 1)

    def test_refusal_with_target_applied_is_read_back_without_replay(self):
        self.scene_only()
        self.cli.refusals = 1
        self.cli.apply_then_refuse = True
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(sum(v.get('field') == 'sceneName' for _, v in self.cli.writes), 1)
        self.assertTrue(any(e.get('resolution') == 'readback_after_rejection'
                            for e in c.read_state(self.project)['journal']))

    def test_unrelated_negative_response_is_not_retried(self):
        self.scene_only()
        self.cli.refusals = 4
        self.cli.refusal = {'ok': False, 'code': 'FORBIDDEN', 'message': 'permission denied'}
        with self.assertRaises(c.CanvasError) as failure:
            self.sync()
        self.assertEqual(failure.exception.code, 'COMMAND_REJECTED')
        self.assertEqual(self.cli.refusals, 3)

    def test_human_change_during_refusal_stops_retry_and_resume(self):
        self.scene_only()
        self.cli.refusals = 2
        self.cli.edit_on_refusal = True
        with self.assertRaises(c.CanvasError) as failure:
            self.sync()
        self.assertEqual(failure.exception.code, 'CONCURRENT_EDIT')
        before = copy.deepcopy(self.cli.writes)
        with self.assertRaises(c.CanvasError):
            self.sync()
        self.assertEqual(self.cli.writes, before)

    def test_pause_during_retry_wait_keeps_rejected_log_and_sends_nothing_more(self):
        self.scene_only()
        self.cli.refusals = 2
        def pause(_):
            self.cli.paused = True
        with patch.object(backend.time, 'sleep', side_effect=pause):
            with self.assertRaises(jobs.PauseRequested):
                self.sync()
        self.assertEqual(self.cli.refusals, 1)
        self.assertEqual(c.read_state(self.project)['journal'][-1]['status'], 'rejected')

    def test_legacy_preview_apply_preserves_pause_and_resumes_on_same_id(self):
        operation_id, node_id = self.legacy_failure()
        jobs.pause(self.project)
        before_state = c.state_path(self.project).read_bytes()
        before_writes = copy.deepcopy(self.cli.writes)
        preview = jobs.recover_assets(self.project, operation_id, cli=self.cli)
        self.assertEqual(c.state_path(self.project).read_bytes(), before_state)
        self.assertEqual(self.cli.writes, before_writes)
        applied = jobs.recover_assets(self.project, operation_id, True, preview['fingerprint'], cli=self.cli)
        self.assertEqual(applied['status'], 'recovered_local_only')
        self.assertTrue(jobs.status(self.project)['paused'])
        self.assertEqual(self.cli.writes, before_writes)
        state = c.read_state(self.project)
        self.assertEqual(state['journal'][-1]['recovery']['previous_status'], 'uncertain')
        jobs.resume(self.project, start=False)
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(self.cli.allocations, [node_id])
        self.assertEqual(self.cli.create_count, 1)

    def test_legacy_recovery_rejects_changed_fingerprint_and_human_content(self):
        operation_id, node_id = self.legacy_failure()
        preview = jobs.recover_assets(self.project, operation_id, cli=self.cli)
        c.nodes(self.cli.doc)[node_id]['x'] = 999
        with self.assertRaises(ValueError):
            jobs.recover_assets(self.project, operation_id, True, preview['fingerprint'], cli=self.cli)
        self.assertEqual(c.read_state(self.project)['journal'][-1]['status'], 'uncertain')
        c.content(self.cli.doc, node_id)['appearanceInfo'] = {'description': 'Human edit'}
        with self.assertRaises(ValueError):
            jobs.recover_assets(self.project, operation_id, cli=self.cli)

    def test_legacy_name_already_applied_is_verified_without_replaying(self):
        operation_id, node_id = self.legacy_failure()
        c.nodes(self.cli.doc)[node_id]['data']['name'] = 'Room'
        c.content(self.cli.doc, node_id)['basicInfo']['name'] = 'Room'
        preview = jobs.recover_assets(self.project, operation_id, cli=self.cli)
        result = jobs.recover_assets(self.project, operation_id, True, preview['fingerprint'], cli=self.cli)
        self.assertEqual(result['journal_status'], 'verified')
        before = sum(v.get('field') == 'sceneName' for _, v in self.cli.writes)
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(sum(v.get('field') == 'sceneName' for _, v in self.cli.writes), before)

    def test_new_asset_changed_during_pause_is_not_initialized(self):
        self.scene_only()
        self.cli.pause_after_create = True
        with self.assertRaises(jobs.PauseRequested):
            self.sync()
        node_id = self.cli.allocations[0]
        c.content(self.cli.doc, node_id)['basicInfo']['appearanceName'] = 'Human view'
        self.cli.pause_after_create = self.cli.paused = False
        before = copy.deepcopy(self.cli.writes)
        with self.assertRaises(c.CanvasError) as failure:
            self.sync()
        self.assertEqual(failure.exception.code, 'CONCURRENT_EDIT')
        self.assertEqual(self.cli.writes, before)

    def test_true_unknown_or_missing_creation_proof_cannot_be_reclassified(self):
        operation_id, _ = self.legacy_failure()
        state = c.read_state(self.project)
        state['journal'][-1]['error'] = 'CLI timeout'
        c.save(self.project, state)
        with self.assertRaises(ValueError):
            jobs.recover_assets(self.project, operation_id, cli=self.cli)
        state['journal'][-1]['error'] = json.dumps(REFUSAL)
        state['journal'][0]['status'] = 'uncertain'
        c.save(self.project, state)
        with self.assertRaises(ValueError):
            jobs.recover_assets(self.project, operation_id, cli=self.cli)

    def test_recovery_cannot_run_alongside_another_canvas_writer(self):
        operation_id, _ = self.legacy_failure()
        binding = c.read_state(self.project)['binding']
        guard = jobs.FileLock(Path(jobs.tempfile.gettempdir()) / ('seedance-worker-' + c.digest([binding['account_id'], binding['canvas_id']]) + '.lock'))
        guard.acquire()
        try:
            with self.assertRaises(c.CanvasError) as failure:
                jobs.recover_assets(self.project, operation_id, cli=self.cli)
            self.assertEqual(failure.exception.code, 'BUSY')
        finally:
            guard.release()

    def test_attention_deduplicates_and_success_still_emits_completion(self):
        job = {'job_id': 'job-notification', 'kind': 'assets'}
        state = {'status': 'needs_attention', 'completed': 270, 'total': 349,
                 'error': {'code': 'COMMAND_REJECTED', 'message': 'scene asset not found'}}
        with patch.object(jobs, 'notify_attention') as attention, patch.object(jobs, 'notify_completion') as complete:
            jobs.publish_event(self.project, job, state)
            jobs.publish_event(self.project, job, state)
            attention.assert_called_once()
            complete.assert_not_called()
            state.update(status='completed', completed=349)
            jobs.publish_event(self.project, job, state)
            jobs.publish_event(self.project, job, state)
            complete.assert_called_once()
        self.assertEqual([e['event'] for e in jobs.status(self.project)['events']], ['needs_attention', 'completed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
