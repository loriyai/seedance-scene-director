#!/usr/bin/env python3
"""Offline queue contracts: local preparation and injected workers only.

No test may construct the real CLI, start a subprocess, or reach a canvas.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import canvas as c
import director as d
import canvas_jobs as jobs


class CanvasJobChecks(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('SEEDANCE_AUDIT_TEST_DIR')
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.ledger = {
            'schema_version': 3, 'image_design_version': 'gpt-image-2-v1',
            'visual_style': {'text': '3D国漫'},
            'characters': [{'id': 'CHAR0001', 'name': '陈默-基础形象',
                            'image_prompt': '固定人物参考图提示词'}],
            'scene_assets': [], 'scenes': [], 'props': [],
        }
        self.unit = {'id': '场景1-分镜1', 'version': 1, 'duration': 4,
                     'display_name': 'A01-场景1-分镜1（时长：4秒）',
                     'character_ids': ['CHAR0001'], 'scene_ids': [], 'prop_ids': [],
                     'global': '@{CHAR0001} 静立', 'shots': [], 'test_prompt': '原提示词'}
        (self.project / 'script.txt').write_text('虚构测试剧本\n', encoding='utf-8')
        d.write_json(self.project / 'source.json', {'paragraphs': []})
        d.write_json(self.project / 'review.json', {'scenes': []})
        d.write_json(self.project / 'ledger.json', self.ledger)
        (self.project / 'video-style.txt').write_text('3D国漫\n', encoding='utf-8')
        self.session = {
            'schema_version': 1, 'revision': 0, 'phase': 'range',
            'style_hash': d.sha('3D国漫'), 'ledger': self.ledger,
            'units': [self.unit], 'ranges': [], 'receipts': [], 'drafts': {},
            'files': {key: {'path': str(self.project / filename),
                            'sha256': d.file_hash(self.project / filename)}
                      for key, filename in [('script', 'script.txt'), ('source', 'source.json'),
                                            ('review', 'review.json'), ('ledger', 'ledger.json')]},
        }
        self.save_session()
        self.state = c.blank_state()
        self.state['binding'] = {'canvas_id': 'JOB_TEST_ROOT', 'account_id': 'JOB_TEST_USER'}
        c.save(self.project, self.state)
        self.cli_guard = patch.object(c, 'CLI', side_effect=AssertionError('Real CLI is forbidden in queue tests'))
        self.cli_guard.start()
        self.addCleanup(self.cli_guard.stop)
        self.popen_guard = patch('subprocess.Popen', side_effect=AssertionError('Real subprocess is forbidden in queue tests'))
        self.popen_guard.start()
        self.addCleanup(self.popen_guard.stop)
        self.notification_guard = patch.object(jobs, 'notify_completion')
        self.notify = self.notification_guard.start()
        self.addCleanup(self.notification_guard.stop)
        attention = patch.object(jobs, 'notify_attention')
        self.attention = attention.start()
        self.addCleanup(attention.stop)

    def save_session(self):
        d.write_json(self.project / 'session.json', self.session)

    def enqueue(self, kind='assets', **kwargs):
        return jobs.enqueue(self.project, kind, start=False, **kwargs)

    def manifest_path(self, result):
        return self.project / 'canvas' / 'jobs' / (result['job_id'] + '.json')

    def read_manifest(self, result):
        return json.loads(self.manifest_path(result).read_text(encoding='utf-8'))

    def job_status(self, result):
        rows = jobs.status(self.project)['jobs']
        matches = [row for row in rows if row.get('job_id', row.get('id')) == result['job_id']]
        self.assertEqual(len(matches), 1)
        return matches[0]['status']

    @staticmethod
    def successful_runner(calls):
        def run(project, manifest, check_control, progress):
            calls.append(copy.deepcopy(manifest))
            return {'status': 'synced'}
        return run

    def test_enqueue_status_and_pause_are_local_without_starting_worker(self):
        result = self.enqueue(request_id='local-only')
        self.assertTrue(result['job_id'])
        self.assertFalse(result['reused'])
        self.assertTrue(self.manifest_path(result).is_file())
        self.assertFalse(jobs.status(self.project)['worker_active'])
        jobs.pause(self.project)
        self.assertTrue(jobs.status(self.project)['paused'])
        self.assertEqual(jobs.status(self.project)['events'], [])

    def test_same_request_and_payload_reuses_one_immutable_manifest(self):
        first = self.enqueue(request_id='same-request')
        original = self.manifest_path(first).read_bytes()
        second = self.enqueue(request_id='same-request')
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertTrue(second['reused'])
        self.assertEqual(self.manifest_path(first).read_bytes(), original)
        self.assertEqual(len(jobs.status(self.project)['jobs']), 1)

    def test_reused_request_id_cannot_silently_change_payload(self):
        first = self.enqueue('videos', request_id='frozen-request', ids=[self.unit['id']])
        original = self.manifest_path(first).read_bytes()
        self.session['units'][0]['test_prompt'] = '改后的提示词'
        self.session['units'][0]['version'] = 2
        self.save_session()
        with self.assertRaises(ValueError):
            self.enqueue('videos', request_id='frozen-request', ids=[self.unit['id']])
        self.assertEqual(self.manifest_path(first).read_bytes(), original)
        self.assertEqual(len(jobs.status(self.project)['jobs']), 1)

    def test_new_units_ranges_and_revision_do_not_invalidate_assets_job(self):
        queued = self.enqueue(request_id='assets-while-writing')
        frozen = self.manifest_path(queued).read_bytes()
        self.session['revision'] += 1
        self.session['units'].append(dict(self.unit, id='场景2-分镜1', version=1))
        self.session['ranges'].append({'span': [9, 18], 'batch': 'B'})
        self.save_session()
        calls = []
        jobs.run_worker(self.project, runner=self.successful_runner(calls))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['kind'], 'assets')
        self.assertEqual(self.job_status(queued), 'completed')
        self.assertEqual(self.manifest_path(queued).read_bytes(), frozen)

    def test_changed_bound_ledger_file_stops_before_dispatch(self):
        queued = self.enqueue(request_id='old-ledger')
        changed = copy.deepcopy(self.ledger)
        changed['characters'][0]['image_prompt'] = 'changed image prompt'
        d.write_json(self.project / 'ledger.json', changed)
        calls = []
        try:
            jobs.run_worker(self.project, runner=self.successful_runner(calls))
        except ValueError:
            pass
        self.assertEqual(calls, [])
        self.assertNotEqual(self.job_status(queued), 'completed')
        self.assertEqual([e['event'] for e in jobs.status(self.project)['events']], ['needs_attention'])

    def test_changed_canvas_binding_stops_before_dispatch(self):
        queued = self.enqueue(request_id='old-target')
        self.state['binding']['canvas_id'] = 'DIFFERENT_CANVAS'
        c.save(self.project, self.state)
        calls = []
        try:
            jobs.run_worker(self.project, runner=self.successful_runner(calls))
        except ValueError:
            pass
        self.assertEqual(calls, [])
        self.assertNotEqual(self.job_status(queued), 'completed')

    def test_changed_selected_video_unit_stops_before_dispatch(self):
        queued = self.enqueue('videos', request_id='old-video', ids=[self.unit['id']])
        self.session['units'][0]['duration'] = 8
        self.session['units'][0]['version'] += 1
        self.save_session()
        calls = []
        try:
            jobs.run_worker(self.project, runner=self.successful_runner(calls))
        except ValueError:
            pass
        self.assertEqual(calls, [])
        self.assertNotEqual(self.job_status(queued), 'completed')

    def test_same_canvas_lock_prevents_a_second_worker_in_another_project(self):
        queued = self.enqueue(request_id='lock-target')
        other_project = self.project / 'another-project'
        c.save(other_project, copy.deepcopy(self.state))
        calls = []
        with c.project_lock(other_project):
            try:
                jobs.run_worker(self.project, runner=self.successful_runner(calls))
            except c.CanvasError as exc:
                self.assertEqual(exc.code, 'BUSY')
        self.assertEqual(calls, [])
        self.assertNotEqual(self.job_status(queued), 'completed')

    def test_paused_queue_never_dispatches(self):
        queued = self.enqueue(request_id='pause-before-run')
        jobs.pause(self.project)
        calls = []
        jobs.run_worker(self.project, runner=self.successful_runner(calls))
        self.assertEqual(calls, [])
        self.assertNotEqual(self.job_status(queued), 'completed')
        self.assertEqual(jobs.status(self.project)['events'], [])

    def test_video_waits_for_asset_completion_and_partial_emits_no_success(self):
        assets = self.enqueue(request_id='asset-dependency')
        videos = self.enqueue('videos', request_id='dependent-video', ids=[self.unit['id']])
        self.assertIn(assets['job_id'], self.read_manifest(videos)['dependencies'])
        calls = []
        def partial(project, manifest, check_control, progress):
            calls.append(manifest['kind'])
            return {'status': 'partial', 'reason': 'missing references'}
        jobs.run_worker(self.project, runner=partial)
        self.assertEqual(calls, ['assets'])
        self.assertEqual(self.job_status(assets), 'needs_attention')
        self.assertNotEqual(self.job_status(videos), 'completed')
        self.assertEqual([e['event'] for e in jobs.status(self.project)['events']], ['needs_attention'])
        self.notify.assert_not_called()

    def test_success_dispatches_asset_then_video_and_is_not_repeated(self):
        assets = self.enqueue(request_id='complete-assets')
        videos = self.enqueue('videos', request_id='complete-video', ids=[self.unit['id']])
        session_before = (self.project / 'session.json').read_bytes()
        calls = []
        jobs.run_worker(self.project, runner=self.successful_runner(calls))
        self.assertEqual([m['kind'] for m in calls], ['assets', 'videos'])
        self.assertEqual(self.job_status(assets), 'completed')
        self.assertEqual(self.job_status(videos), 'completed')
        before = copy.deepcopy(jobs.status(self.project)['events'])
        self.assertEqual([event['job_id'] for event in before],
                         [assets['job_id'], videos['job_id']])
        self.assertEqual([event['event'] for event in before], ['completed', 'completed'])
        notifications_before = list(self.notify.call_args_list)
        self.assertEqual([call.args for call in notifications_before],
                         [(event['message'],) for event in before])
        self.assertEqual((self.project / 'session.json').read_bytes(), session_before)
        jobs.run_worker(self.project, runner=self.successful_runner(calls))
        self.assertEqual(len(calls), 2)
        self.assertEqual(jobs.status(self.project)['events'], before)
        self.assertEqual(self.notify.call_args_list, notifications_before)
        self.assertEqual((self.project / 'session.json').read_bytes(), session_before)

    def test_pause_requested_between_jobs_stops_next_dispatch(self):
        assets = self.enqueue(request_id='pause-after-assets')
        videos = self.enqueue('videos', request_id='paused-video', ids=[self.unit['id']])
        calls = []
        def pause_after_first(project, manifest, check_control, progress):
            calls.append(manifest['kind'])
            jobs.pause(project)
            return {'status': 'synced'}
        jobs.run_worker(self.project, runner=pause_after_first)
        self.assertEqual(calls, ['assets'])
        self.assertTrue(jobs.status(self.project)['paused'])
        self.assertNotEqual(self.job_status(videos), 'completed')

    def test_changed_upload_image_hash_stops_before_dispatch(self):
        folder = self.project / 'images'
        folder.mkdir()
        image = folder / '陈默-基础形象.png'
        image.write_bytes(b'fixture-before')
        queued = self.enqueue('images', request_id='frozen-images', folder=folder)
        image.write_bytes(b'fixture-after')
        calls = []
        jobs.run_worker(self.project, runner=self.successful_runner(calls))
        self.assertEqual(calls, [])
        self.assertEqual(self.job_status(queued), 'needs_attention')
        self.notify.assert_not_called()

    def test_completed_assets_are_not_ready_after_target_binding_changes(self):
        self.enqueue(request_id='assets-old-target')
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        self.assertTrue(jobs.status(self.project)['assets_ready'])
        self.state['binding']['canvas_id'] = 'NEW_TARGET'
        c.save(self.project, self.state)
        current = jobs.status(self.project)
        self.assertFalse(current['assets_ready'])
        self.assertFalse(current['image_choice_available'])
        with self.assertRaises(ValueError):
            jobs.image_choice(self.project, '1')

    def test_completed_assets_are_not_ready_after_ledger_rebind(self):
        self.enqueue(request_id='assets-old-ledger')
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        self.session['ledger']['characters'][0]['image_prompt'] = '新制作设计'
        d.write_json(self.project / 'ledger.json', self.session['ledger'])
        self.session['files']['ledger']['sha256'] = d.file_hash(self.project / 'ledger.json')
        self.save_session()
        self.assertFalse(jobs.status(self.project)['assets_ready'])
        self.assertFalse(jobs.status(self.project)['image_choice_available'])

    def test_uncertain_write_is_not_automatically_redispatched(self):
        queued = self.enqueue(request_id='unknown-write')
        calls = []
        def uncertain(project, manifest, check_control, progress):
            calls.append(manifest['job_id'])
            c.fail('UNCERTAIN_WRITE', 'fake unknown ACK')
        jobs.run_worker(self.project, runner=uncertain)
        self.assertEqual(self.job_status(queued), 'needs_attention')
        jobs.run_worker(self.project, runner=uncertain)
        self.assertEqual(calls, [queued['job_id']])
        self.assertEqual([e['event'] for e in jobs.status(self.project)['events']], ['needs_attention'])
        self.attention.assert_called_once()
        self.notify.assert_not_called()

    def test_active_worker_guard_rejects_a_second_worker_without_dispatch(self):
        queued = self.enqueue(request_id='first-worker')
        other_project = self.project / 'second-project'
        c.save(other_project, copy.deepcopy(self.state))
        calls = []
        def holding(project, manifest, check_control, progress):
            before = json.loads((project / 'canvas/jobs/worker.json').read_text(encoding='utf-8'))
            with self.assertRaises(c.CanvasError) as failure:
                jobs.run_worker(other_project, runner=self.successful_runner(calls))
            self.assertEqual(failure.exception.code, 'BUSY')
            after = json.loads((project / 'canvas/jobs/worker.json').read_text(encoding='utf-8'))
            self.assertEqual(before, after)
            return {'status': 'synced'}
        jobs.run_worker(self.project, runner=holding)
        self.assertEqual(calls, [])
        self.assertEqual(self.job_status(queued), 'completed')

    def test_start_worker_is_hidden_and_does_not_spawn_twice(self):
        self.enqueue(request_id='spawn-once')
        with patch.object(jobs.subprocess, 'Popen', return_value=SimpleNamespace(pid=os.getpid())) as spawn:
            first = jobs.start_worker(self.project)
            second = jobs.start_worker(self.project)
        self.assertEqual(first['status'], 'started')
        self.assertEqual(second['status'], 'already_running')
        spawn.assert_called_once()
        options = spawn.call_args.kwargs
        self.assertEqual(options['stdin'], jobs.subprocess.DEVNULL)
        if os.name == 'nt':
            self.assertTrue(options['creationflags'] & jobs.subprocess.CREATE_NO_WINDOW)
            self.assertTrue(options['creationflags'] & jobs.subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertTrue(options['start_new_session'])

    def test_resolved_asset_job_releases_video_dependency_on_superseded_failure(self):
        old_assets = self.enqueue(request_id='asset-choice')
        video = self.enqueue('videos', request_id='waiting-video', ids=[self.unit['id']])
        jobs.run_worker(self.project, runner=lambda *args: {'status': 'needs_decision'})
        self.assertEqual(self.job_status(old_assets), 'needs_attention')
        resolved = self.enqueue(request_id='asset-choice', decisions={'CHAR0001': {'reuse': 'EXISTING'}})
        self.assertNotEqual(resolved['job_id'], old_assets['job_id'])
        self.assertIn(old_assets['job_id'], self.read_manifest(resolved)['supersedes'])
        calls = []
        jobs.run_worker(self.project, runner=self.successful_runner(calls))
        self.assertEqual([m['job_id'] for m in calls], [resolved['job_id'], video['job_id']])
        self.assertEqual(self.job_status(old_assets), 'superseded')
        self.assertEqual(self.job_status(video), 'completed')

    def test_new_selected_video_version_supersedes_older_queued_content(self):
        old = self.enqueue('videos', request_id='old-selected-version', ids=[self.unit['id']])
        frozen = self.manifest_path(old).read_bytes()
        self.session['units'][0].update(version=2, duration=8, test_prompt='新审阅内容')
        self.save_session()
        new = self.enqueue('videos', request_id='new-selected-version', ids=[self.unit['id']])
        self.assertIn(old['job_id'], self.read_manifest(new)['supersedes'])
        calls = []
        jobs.run_worker(self.project, runner=self.successful_runner(calls))
        self.assertEqual([m['job_id'] for m in calls], [new['job_id']])
        self.assertEqual(calls[0]['session']['units'][0]['version'], 2)
        self.assertEqual(self.job_status(old), 'superseded')
        self.assertEqual(self.job_status(new), 'completed')
        self.assertEqual(self.manifest_path(old).read_bytes(), frozen)

    def test_rebinding_skips_old_queued_jobs_and_uses_only_current_dependencies(self):
        old_assets = self.enqueue(request_id='old-canvas-assets')
        old_video = self.enqueue('videos', request_id='old-canvas-video', ids=[self.unit['id']])
        self.state['binding']['canvas_id'] = 'NEW_CANVAS'
        c.save(self.project, self.state)
        current_assets = self.enqueue(request_id='new-canvas-assets')
        current_video = self.enqueue('videos', request_id='new-canvas-video', ids=[self.unit['id']])
        self.assertEqual(self.read_manifest(current_video)['dependencies'], [current_assets['job_id']])
        calls = []
        jobs.run_worker(self.project, runner=self.successful_runner(calls))
        self.assertEqual([m['job_id'] for m in calls], [current_assets['job_id'], current_video['job_id']])
        self.assertTrue(all(m['binding']['canvas_id'] == 'NEW_CANVAS' for m in calls))
        rows = {row['job_id']: row for row in jobs.status(self.project)['jobs']}
        for old in (old_assets, old_video):
            self.assertEqual(rows[old['job_id']]['status'], 'needs_attention')
            self.assertEqual(rows[old['job_id']]['error']['code'], 'CANVAS_BINDING_CHANGED')
        self.assertTrue(jobs.status(self.project)['assets_ready'])

    def test_binding_change_during_worker_requires_restart_for_new_canvas_jobs(self):
        original = self.enqueue(request_id='held-old-binding')
        dispatched = []
        created = {}
        def change_binding(project, manifest, check_control, progress):
            dispatched.append(manifest['job_id'])
            self.state['binding']['canvas_id'] = 'NEW_BINDING_DURING_WORK'
            c.save(project, self.state)
            created.update(self.enqueue(request_id='new-binding-waits-for-restart'))
            return {'status': 'synced'}
        result = jobs.run_worker(self.project, runner=change_binding)
        self.assertEqual(dispatched, [original['job_id']])
        self.assertEqual(result['worker_stop_reason'], 'CANVAS_BINDING_CHANGED')
        self.assertFalse(result['worker_active'])
        self.assertEqual(self.job_status(created), 'queued')
        next_calls = []
        jobs.run_worker(self.project, runner=self.successful_runner(next_calls))
        self.assertEqual([m['job_id'] for m in next_calls], [created['job_id']])
        self.assertEqual(next_calls[0]['binding']['canvas_id'], 'NEW_BINDING_DURING_WORK')

    def test_upload_choice_tracks_latest_assets_for_current_binding_only(self):
        first_binding = copy.deepcopy(self.state['binding'])
        first = self.enqueue(request_id='first-binding-completed')
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        self.state['binding']['canvas_id'] = 'SECOND_CANVAS'
        c.save(self.project, self.state)
        second = self.enqueue(request_id='second-binding-completed')
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        self.assertEqual(jobs.status(self.project)['asset_job_id'], second['job_id'])
        self.state['binding'] = first_binding
        c.save(self.project, self.state)
        current = jobs.status(self.project)
        self.assertTrue(current['assets_ready'])
        self.assertEqual(current['asset_job_id'], first['job_id'])
        jobs.image_choice(self.project, '2')
        control = json.loads((self.project / 'canvas/jobs/control.json').read_text(encoding='utf-8'))
        self.assertEqual(control['image_choice']['asset_job_id'], first['job_id'])
        self.assertFalse(jobs.status(self.project)['image_choice_available'])

    def test_asset_and_video_events_remain_pending_until_explicit_report(self):
        asset = self.enqueue(request_id='report-assets')
        video = self.enqueue('videos', request_id='report-videos', ids=[self.unit['id']])
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        event_file = self.project / 'canvas/jobs/events.json'
        original = event_file.read_bytes()
        first = jobs.status(self.project)
        self.assertEqual([event['job_id'] for event in first['pending_events']],
                         [asset['job_id'], video['job_id']])
        self.assertEqual(first, jobs.status(self.project))
        self.assertEqual(event_file.read_bytes(), original)
        identifier = first['pending_events'][0]['event_id']
        self.assertEqual(jobs.report_event(self.project, identifier)['status'], 'reported')
        self.assertEqual(jobs.report_event(self.project, identifier)['status'], 'already_reported')
        self.assertEqual([event['job_id'] for event in jobs.status(self.project)['pending_events']], [video['job_id']])
        self.assertEqual(event_file.read_bytes(), original)
        self.assertEqual(d.read_json(self.project / 'session.json'), self.session)

    def test_legacy_events_filter_current_canvas_without_rewriting_history(self):
        self.enqueue('videos', request_id='legacy-event', ids=[self.unit['id']])
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        event_file = self.project / 'canvas/jobs/events.json'
        legacy = d.read_json(event_file)
        for event in legacy:
            for key in ('event_id', 'binding', 'desktop_notification'):
                event.pop(key, None)
        d.write_json(event_file, legacy)
        original = event_file.read_bytes()
        identifier = jobs.status(self.project)['pending_events'][0]['event_id']
        self.state['binding']['canvas_id'] = 'OTHER_TARGET'
        c.save(self.project, self.state)
        current = jobs.status(self.project)
        self.assertEqual(current['events'], [])
        self.assertEqual(current['pending_events'], [])
        self.assertFalse(current['video_followup_available'])
        with self.assertRaises(ValueError):
            jobs.report_event(self.project, identifier)
        self.state['binding']['canvas_id'] = 'JOB_TEST_ROOT'
        c.save(self.project, self.state)
        self.assertEqual(jobs.status(self.project)['pending_events'][0]['event_id'], identifier)
        jobs.report_event(self.project, identifier)
        self.assertEqual(event_file.read_bytes(), original)

    def test_unknown_event_cannot_be_consumed(self):
        self.enqueue(request_id='not-completed')
        with self.assertRaises(ValueError):
            jobs.report_event(self.project, 'event-made-up')
        self.assertFalse((self.project / 'canvas/jobs/control.json').exists())

    def test_previous_canvas_failure_is_saved_without_notifying_current_canvas(self):
        self.enqueue(request_id='old-target-notification')
        self.state['binding']['canvas_id'] = 'NEW_TARGET_FOR_NOTIFICATIONS'
        c.save(self.project, self.state)
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        self.attention.assert_not_called()
        events = d.read_json(self.project / 'canvas/jobs/events.json')
        self.assertEqual(events[0]['event'], 'needs_attention')
        self.assertEqual(events[0]['desktop_notification'],
                         {'status': 'not_attempted', 'reason': 'non_current_canvas'})
        self.assertEqual(jobs.status(self.project)['pending_events'], [])

    def test_partial_result_never_offers_video_followup_even_when_count_is_full(self):
        video = self.enqueue('videos', request_id='partial-video', ids=[self.unit['id']])
        def partial(project, manifest, check_control, progress):
            progress({'completed': 1, 'total': 1})
            return {'status': 'partial'}
        jobs.run_worker(self.project, runner=partial)
        first = jobs.status(self.project)
        self.assertEqual(first['pending_events'][0]['event'], 'needs_attention')
        self.assertFalse(first['video_followup_available'])
        with self.assertRaises(ValueError):
            jobs.video_followup(self.project, video['job_id'], 'shown')
        jobs.report_event(self.project, first['pending_events'][0]['event_id'])
        jobs.resume(self.project, start=False)
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        recovered = jobs.status(self.project)
        self.assertEqual([event['event'] for event in recovered['pending_events']], ['completed'])
        self.assertTrue(recovered['video_followup_available'])
        self.assertEqual(len(recovered['events']), 2)

    def test_new_failure_replaces_unreported_old_warning_but_preserves_history(self):
        self.enqueue(request_id='changing-failure')
        jobs.run_worker(self.project, runner=lambda *args: {'status': 'partial'})
        first = jobs.status(self.project)['pending_events'][0]['event_id']
        jobs.resume(self.project, start=False)
        jobs.run_worker(self.project, runner=lambda *args: {'status': 'needs_decision'})
        current = jobs.status(self.project)
        self.assertEqual(len(current['events']), 2)
        self.assertEqual(len(current['pending_events']), 1)
        self.assertNotEqual(current['pending_events'][0]['event_id'], first)
        with self.assertRaises(ValueError):
            jobs.report_event(self.project, first)

    def test_video_followup_requires_report_then_records_shown_without_selecting(self):
        video = self.enqueue('videos', request_id='followup-once', ids=[self.unit['id']])
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        current = jobs.status(self.project)
        self.assertTrue(current['video_followup_available'])
        with self.assertRaises(ValueError):
            jobs.video_followup(self.project, video['job_id'], 'shown')
        jobs.report_event(self.project, current['pending_events'][0]['event_id'])
        jobs.pause(self.project)
        self.assertEqual(jobs.video_followup(self.project, video['job_id'], 'shown')['status'], 'shown')
        control = d.read_json(self.project / 'canvas/jobs/control.json')
        self.assertEqual(control['video_followups'][video['job_id']]['action'], 'shown')
        self.assertFalse(jobs.status(self.project)['video_followup_available'])
        self.assertEqual(jobs.video_followup(self.project, video['job_id'], 'shown')['status'], 'already_handled')
        self.assertEqual(jobs.video_followup(self.project, video['job_id'], 'modify')['status'], 'handled')
        self.assertEqual(jobs.video_followup(self.project, video['job_id'], 'modify')['status'], 'already_handled')
        self.assertTrue(jobs.status(self.project)['paused'])
        self.assertEqual(d.read_json(self.project / 'session.json'), self.session)
        self.assertEqual(len(jobs.status(self.project)['jobs']), 1)

    def test_user_direction_consumes_followup_without_showing_menu(self):
        video = self.enqueue('videos', request_id='already-directed', ids=[self.unit['id']])
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        jobs.report_event(self.project, jobs.status(self.project)['pending_events'][0]['event_id'])
        result = jobs.video_followup(self.project, video['job_id'], 'already-directed')
        self.assertEqual(result['status'], 'handled')
        self.assertFalse(jobs.status(self.project)['video_followup_available'])
        self.assertEqual(jobs.video_followup(self.project, video['job_id'], 'shown')['action'], 'already-directed')
        with self.assertRaises(ValueError):
            jobs.video_followup(self.project, video['job_id'], 'finish')

    def test_changed_video_input_does_not_offer_or_handle_old_followup(self):
        video = self.enqueue('videos', request_id='stale-followup', ids=[self.unit['id']])
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        jobs.report_event(self.project, jobs.status(self.project)['pending_events'][0]['event_id'])
        self.session['units'][0]['version'] += 1
        self.save_session()
        self.assertFalse(jobs.status(self.project)['video_followup_available'])
        with self.assertRaises(ValueError):
            jobs.video_followup(self.project, video['job_id'], 'already-directed')

    def test_desktop_launch_failure_is_durable_and_does_not_consume_chat_event(self):
        self.notify.return_value = {'status': 'start_failed', 'attempted_at': 123, 'error': 'test unavailable'}
        self.enqueue(request_id='desktop-failure')
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        current = jobs.status(self.project)
        self.assertEqual(current['jobs'][0]['status'], 'completed')
        self.assertEqual(current['pending_events'][0]['desktop_notification']['status'], 'start_failed')
        self.assertFalse(current['pending_events'][0]['reported'])

    def test_notification_exception_and_legacy_none_do_not_change_completion(self):
        self.notify.side_effect = RuntimeError('test notification implementation failed')
        self.enqueue(request_id='unknown-notification-outcome')
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        event = jobs.status(self.project)['pending_events'][0]
        self.assertEqual(event['desktop_notification']['status'], 'attempt_outcome_unknown')
        self.assertEqual(event['event'], 'completed')
        self.notify.side_effect = None
        self.notify.return_value = None
        self.enqueue('videos', request_id='legacy-none-notification', ids=[self.unit['id']])
        jobs.run_worker(self.project, runner=self.successful_runner([]))
        event = jobs.status(self.project)['pending_events'][-1]
        self.assertEqual(event['desktop_notification']['status'], 'attempt_outcome_unknown')
        self.assertEqual(event['event'], 'completed')

    def test_desktop_helper_reports_only_start_evidence_and_never_waits(self):
        from unittest.mock import Mock
        process = Mock(pid=456)
        with patch.object(jobs, 'os', SimpleNamespace(name='nt')), \
                patch.object(jobs.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                patch.object(jobs.subprocess, 'Popen', return_value=process) as spawn:
            result = jobs.notify_desktop('离线测试')
        self.assertEqual(result['status'], 'started_result_unknown')
        process.wait.assert_not_called()
        process.communicate.assert_not_called()
        self.assertEqual(spawn.call_args.kwargs['stderr'], jobs.subprocess.DEVNULL)
        with patch.object(jobs, 'os', SimpleNamespace(name='nt')), \
                patch.object(jobs.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                patch.object(jobs.subprocess, 'Popen', side_effect=OSError('test launch error')):
            failed = jobs.notify_desktop('离线测试')
        self.assertEqual(failed['status'], 'start_failed')
        with patch.object(jobs, 'os', SimpleNamespace(name='posix')):
            unsupported = jobs.notify_desktop('离线测试')
        self.assertEqual(unsupported, {'status': 'not_attempted', 'reason': 'unsupported_platform'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
