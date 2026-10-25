"""Offline user-flow regressions: no cloud, generation, uploads or actual popups."""
import base64
import copy
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import canvas as c
import canvas_job_backend as backend
import canvas_jobs as jobs
import director as d
import test_video_write_receipt as video_tests
import test_canvas_reference_workflow as reference_tests
import test_canvas_jobs as queue_tests


class VideoRatioFlowChecks(unittest.TestCase):
    setUp = video_tests.VideoWriteReceiptChecks.setUp
    sync = video_tests.VideoWriteReceiptChecks.sync
    target = video_tests.VideoWriteReceiptChecks.target
    target_writes = video_tests.VideoWriteReceiptChecks.target_writes

    def test_new_video_uses_frozen_ratio_and_preserves_other_settings(self):
        self.session['units'][0]['video_ratio'] = '16:9'
        result = self.sync()
        node = self.target()
        self.assertEqual(result['status'], 'synced')
        initial = self.target_writes(node)[0][1]['initialData']
        self.assertEqual(initial, {'generation': {'ratio': '16:9'}})
        generation = c.content(self.cli.doc, node)['generation']
        self.assertEqual(generation['ratio'], '16:9')
        self.assertEqual(generation['modelKey'], self.cli.default_generation['modelKey'])
        receipt = json.loads(Path(result['video_write_receipts'][0]).read_text(encoding='utf-8'))
        self.assertEqual(receipt['observed']['ratio'], '16:9')
        self.assertTrue(any(row['action'] == 'generation_settings' and row['matched'] for row in receipt['checks']))

    def test_vertical_video_does_not_change_asset_defaults(self):
        self.session['units'][0]['video_ratio'] = '9:16'
        self.sync()
        self.assertEqual(c.content(self.cli.doc, self.target())['generation']['ratio'], '9:16')
        self.assertEqual(c.ASSET_IMAGE_DEFAULTS['ratio'], '16:9')
        for kind in ('role', 'scene', 'image'):
            self.assertEqual(c.initial_data({'kind': kind, 'name': 'Asset'})['generation']['ratio'], '16:9')

    def test_ready_video_ratio_is_preserved_on_reuse(self):
        self.session['units'][0]['video_ratio'] = '16:9'
        self.sync()
        node = self.target()
        c.content(self.cli.doc, node)['generation']['ratio'] = '9:16'
        self.cli.writes.clear()
        result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(c.content(self.cli.doc, node)['generation']['ratio'], '9:16')
        self.assertFalse(any('ratio' in value.get('patch', {}).get('generation', {}) for _, value in self.cli.writes))

    def test_wrong_ratio_readback_never_counts_as_complete(self):
        self.session['units'][0]['video_ratio'] = '16:9'
        self.cli.final_fault = 'ratio'
        with patch.object(c.time, 'sleep'), self.assertRaises(c.CanvasError) as caught:
            self.sync()
        self.assertEqual(caught.exception.code, 'UNCERTAIN_WRITE')
        receipt = json.loads(Path(c.read_state(self.project)['mappings'][self.unit_id]['video_write_receipt']).read_text(encoding='utf-8'))
        self.assertFalse(receipt['core_verified'])

    def test_completed_asset_manual_prompt_and_inputs_are_not_revisited(self):
        self.state['mappings']['CHAR1']['image_input'] = {'status': 'pending', 'managed_references': []}
        self.state['mappings']['CHAR1']['prompt_hash'] = c.digest('Old prompt')
        c.save(self.project, self.state)
        c.content(self.cli.doc, 'ROLE').setdefault('generation', {})['prompt'] = 'User changed image prompt'
        before = copy.deepcopy(c.content(self.cli.doc, 'ROLE'))
        with patch.object(c, 'sync_asset_image_references', side_effect=AssertionError('No finished asset input refresh')):
            result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(c.content(self.cli.doc, 'ROLE'), before)

    def test_ordinary_video_never_checks_main_image_readiness_or_reports_missing_media(self):
        with patch.object(c, 'ready_image', side_effect=AssertionError('No image readiness preflight')), \
             patch.object(c, 'reference_ready', side_effect=AssertionError('No media readiness preflight')):
            result = self.sync()
        self.assertEqual(result['status'], 'synced')
        self.assertFalse(any(row['reason'] in ('missing_image', 'missing_video') for row in result['reference_issues']))
        record = d.read_json(self.project / 'canvas' / 'reference-check.json')
        self.assertFalse(record['media_readiness_checked'])
        self.assertIsNone(record['missing_image_count'])
        self.assertNotIn('缺少图片的资产节点', Path(result['reference_report']).read_text(encoding='utf-8'))


class MissingAssetFlowChecks(unittest.TestCase):
    setUp = reference_tests.ReferenceWorkflowChecks.setUp
    sync = reference_tests.ReferenceWorkflowChecks.sync

    def test_three_missing_assets_are_initialized_and_video_continues(self):
        result = self.sync()
        self.assertEqual(result['status'], 'synced')
        summary = result['asset_reconciliation']
        self.assertEqual(summary['missing_asset_count'], 3)
        self.assertEqual(len(summary['created']), 3)
        state = c.read_state(self.project)
        for key in ('CHAR1', 'SET1', 'PROP1'):
            self.assertEqual(c.content(self.cli.doc, state['mappings'][key]['node_id'])['generation']['ratio'], '16:9')

    def test_four_missing_assets_stop_before_every_remote_write(self):
        self.session['ledger']['props'].append({'id': 'PROP2', 'name': 'Extra Pen'})
        result = self.sync()
        self.assertEqual(result['status'], 'needs_decision')
        self.assertEqual(len(result['missing_assets']), 4)
        self.assertEqual({row['key'] for row in result['missing_assets']}, {'CHAR1', 'SET1', 'PROP1', 'PROP2'})
        self.assertEqual(self.cli.writes, [])
        self.assertEqual(self.cli.allocations, [])


class QueueFlowChecks(unittest.TestCase):
    setUp = queue_tests.CanvasJobChecks.setUp
    save_session = queue_tests.CanvasJobChecks.save_session
    enqueue = queue_tests.CanvasJobChecks.enqueue
    manifest_path = queue_tests.CanvasJobChecks.manifest_path
    read_manifest = queue_tests.CanvasJobChecks.read_manifest
    job_status = queue_tests.CanvasJobChecks.job_status
    successful_runner = staticmethod(queue_tests.CanvasJobChecks.successful_runner)

    def test_new_video_ratio_is_frozen_and_drift_is_detected(self):
        self.unit['video_ratio'] = '9:16'
        self.save_session()
        queued = self.enqueue('videos', ids=[self.unit['id']])
        manifest = self.read_manifest(queued)
        self.assertEqual(manifest['session']['units'][0]['video_ratio'], '9:16')
        self.unit['video_ratio'] = '16:9'
        self.save_session()
        with self.assertRaises(ValueError):
            jobs.validate_job(self.project, manifest)

    def test_completed_assets_never_offer_automatic_upload_menu(self):
        self.enqueue()
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        self.assertTrue(jobs.status(self.project)['assets_ready'])
        self.assertFalse(jobs.status(self.project)['image_choice_available'])

    def test_runtime_crash_creates_attention_event_without_losing_progress(self):
        queued = self.enqueue()
        def fail(project, manifest, control, progress):
            progress({'completed': 1, 'total': 2})
            raise RuntimeError('Injected worker crash')
        jobs.run_worker(self.project, runner=fail)
        self.assertEqual(self.job_status(queued), 'needs_attention')
        event = jobs.status(self.project)['pending_events'][0]
        self.assertEqual(event['count'], 1)
        self.assertIn('Injected worker crash', event['message'])

    def prepare_dead_worker(self, paused=False):
        queued = self.enqueue()
        state = jobs.job_state(self.project, self.read_manifest(queued))
        state.update(status='running', completed=1, total=2)
        d.write_json(jobs.state_file(self.project, queued['job_id']), state)
        d.write_json(jobs.directory(self.project) / 'worker.json', {'pid': 7654321, 'run_id': 'test-run', 'status': 'running'})
        if paused:
            jobs.pause(self.project)
        return queued

    def test_monitor_records_forced_exit_once_and_never_restarts_worker(self):
        queued = self.prepare_dead_worker()
        with patch.object(jobs, 'alive', return_value=False):
            result = jobs.settle_worker_exit(self.project, 7654321, 'test-run')
            again = jobs.settle_worker_exit(self.project, 7654321, 'test-run')
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(again['status'], 'stopped')
        self.assertEqual(self.job_status(queued), 'needs_attention')
        self.assertEqual(len(jobs.status(self.project)['events']), 1)
        self.attention.assert_called_once()

    def test_explicit_pause_is_not_reported_as_failure_after_exit(self):
        queued = self.prepare_dead_worker(paused=True)
        with patch.object(jobs, 'alive', return_value=False):
            result = jobs.settle_worker_exit(self.project, 7654321, 'test-run')
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.job_status(queued), 'paused')
        self.assertEqual(jobs.status(self.project)['events'], [])
        self.attention.assert_not_called()

    def test_launch_failure_is_durable_and_notified(self):
        queued = self.enqueue()
        with patch.object(jobs.subprocess, 'Popen', side_effect=OSError('Injected launch failure')):
            result = jobs.start_worker(self.project)
        self.assertEqual(result['status'], 'start_failed')
        self.assertEqual(self.job_status(queued), 'needs_attention')
        self.assertEqual(jobs.status(self.project)['events'][0]['error']['code'], 'WORKER_START_FAILED')

    def test_monitor_log_failure_keeps_started_worker_and_reports_monitor_failure(self):
        queued = self.enqueue()
        original_open = Path.open
        log_opens = 0
        def open_log(path, *args, **kwargs):
            nonlocal log_opens
            if path.name == 'worker.log' and args and args[0] == 'ab':
                log_opens += 1
                if log_opens == 2:
                    raise OSError('Injected monitor log failure')
            return original_open(path, *args, **kwargs)
        self.attention.return_value = {'status': 'started_result_unknown'}
        with patch.object(Path, 'open', new=open_log), \
             patch.object(jobs.subprocess, 'Popen', return_value=SimpleNamespace(pid=os.getpid())) as launch:
            result = jobs.start_worker(self.project)
        self.assertEqual(result['status'], 'started')
        self.assertEqual(result['monitor']['status'], 'start_failed')
        self.assertIn('Injected monitor log failure', result['monitor']['error'])
        self.assertEqual(launch.call_count, 1)
        current = jobs.status(self.project)
        self.assertTrue(current['worker_active'])
        self.assertEqual(current['worker']['monitor'], result['monitor'])
        self.assertEqual(self.job_status(queued), 'queued')
        self.attention.assert_called_once()

    def test_four_missing_assets_survive_worker_result_and_pending_event(self):
        self.ledger['props'] = [{'id': 'PROP000' + str(i), 'name': 'Missing Prop ' + str(i),
                                 'image_prompt': 'prop prompt'} for i in range(1, 4)]
        d.write_json(self.project / 'ledger.json', self.ledger)
        self.session['files']['ledger']['sha256'] = d.file_hash(self.project / 'ledger.json')
        self.save_session()
        self.state['binding'] = {'canvas_id': 'ROOT', 'account_id': 'USER'}
        c.save(self.project, self.state)
        doc = {'rootCanvasId': 'ROOT', 'assets': {'ROOT': {'type': 'canvas',
               'content': {'nodes': {}, 'edges': {}, 'settings': {'readOnly': False}}}}}
        cli = reference_tests.WorkflowCLI(doc)
        queued = self.enqueue('videos', ids=[self.unit['id']])
        with patch.object(backend, 'ControlledCLI', return_value=cli):
            jobs.run_worker(self.project)
        self.assertEqual(self.job_status(queued), 'needs_attention')
        saved = d.read_json(jobs.state_file(self.project, queued['job_id']))
        missing = saved['result']['missing_assets']
        self.assertEqual(len(missing), 4)
        self.assertEqual({row['key'] for row in missing}, {'CHAR0001', 'PROP0001', 'PROP0002', 'PROP0003'})
        self.assertTrue(all({'key', 'name', 'kind', 'previous_node_id'} <= row.keys() for row in missing))
        event = jobs.status(self.project)['pending_events'][0]
        self.assertEqual(event['conflicts'][0]['missing_assets'], missing)
        self.assertEqual(event['error']['code'], 'asset_rebuild_limit')
        self.assertEqual(event['count'], 0)
        self.assertEqual(cli.writes, [])
        self.assertEqual(cli.allocations, [])

    def test_success_popup_times_out_and_attention_popup_is_nonblocking(self):
        with patch.object(jobs.os, 'name', 'nt'), patch.object(jobs.subprocess, 'Popen', return_value=SimpleNamespace(pid=17)) as launch:
            jobs.notify_desktop('success')
            jobs.notify_desktop('failure', warning=True)
        scripts = [base64.b64decode(call.args[0][-1]).decode('utf-16-le') for call in launch.call_args_list]
        self.assertIn(",8,'小云雀同步',64)", scripts[0])
        self.assertIn(",0,'小云雀同步',48)", scripts[1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
