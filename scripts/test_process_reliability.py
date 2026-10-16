"""Offline behavioral regressions for final review, recovery and node progress."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import director as d
import canvas as c
import canvas_jobs as jobs
import canvas_job_backend as backend
import recovery_status
import review_gate
import speech_timing_audit as speech
from test_review_support import approve_fixture
import test_audit_director as director_fixture
import test_canvas_jobs as queue_fixture
import test_video_write_receipt as video_fixture


class FragmentTimingChecks(unittest.TestCase):
    def unit(self):
        return {'audio': [{'record': 'L1', 'text': '我们继续，现在出发。', 'units': 8,
                           'start': 0, 'end': 2.58, 'pause': .58, 'speed': '常速'}],
                'shots': [{'dialogue_parts': [
                    {'record': 'L1', 'text': '我们继续，', 'start': 0, 'end': 1.14, 'pause': .14},
                    {'record': 'L1', 'text': '现在出发。', 'start': 1.24, 'end': 2.58, 'pause': .34}]}]}

    def test_natural_nonzero_gap_is_allowed_without_rewriting(self):
        unit = self.unit()
        before = copy.deepcopy(unit)
        self.assertEqual(speech.audit(unit), [])
        self.assertEqual(unit, before)

    def test_whole_event_rate_cannot_hide_an_impossible_last_fragment(self):
        unit = self.unit()
        # Same whole-event duration/units, but almost all time went to part one.
        unit['shots'][0]['dialogue_parts'][0]['end'] = 1.77
        unit['shots'][0]['dialogue_parts'][1]['start'] = 1.87
        issues = speech.audit(unit)
        self.assertTrue(any(w['kind'] == '片段估计净语速超档' and w['part'] == 2 for w in issues))

    def test_numbers_require_reading_units_and_expose_short_countdown(self):
        unit = self.unit()
        part = unit['shots'][0]['dialogue_parts'][1]
        part.update(text='倒计时19分59秒。', start=1.87)
        unit['audio'][0].update(text='我们继续，倒计时19分59秒。', units=14)
        self.assertTrue(any(w['kind'] == '片段发音单位待核' for w in speech.audit(unit)))
        part.update(spoken_units=10, spoken_basis='倒计时十九分五十九秒，十个发音单位')
        self.assertTrue(any(w['kind'] == '片段估计净语速超档' and w['part'] == 2 for w in speech.audit(unit)))
        self.assertFalse(any(w['kind'] == '片段发音单位待核' for w in speech.audit(unit)))

    def test_double_counted_pause_and_long_gap_are_visible(self):
        unit = self.unit()
        parts = unit['shots'][0]['dialogue_parts']
        parts[1]['start'] = 1.94
        issues = speech.audit(unit)
        self.assertTrue(any(w['kind'] == '片段停顿与整句总停顿不一致' for w in issues))
        self.assertTrue(any(w['kind'] == '分句间停顿偏长需复核' for w in issues))

    def test_recording_compatibility_does_not_require_new_text_metadata(self):
        unit = self.unit()
        unit['audio'][0]['timing_basis'] = 'recording'
        self.assertEqual(speech.audit(unit), [])


class FinalReviewChecks(unittest.TestCase):
    def setUp(self):
        self.fixture = director_fixture.AuditDirectorChecks()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state, self.project = self.fixture.state, self.fixture.project
        self.request = self.fixture.request()
        self.path = self.project / 'final.json'
        d.write_json(self.path, self.request)
        self.args = SimpleNamespace(project=self.project, request=self.path,
                                    output=self.project / 'outputs' / 'new.txt')

    def report(self):
        approve_fixture(self.args, self.state)
        return d.read_json(self.args.review)

    def test_missing_review_blocks_new_commit_without_consuming_numbers(self):
        before = (self.project / 'session.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '最终稿复核记录'):
            d.commit(self.args, self.state)
        self.assertEqual((self.project / 'session.json').read_bytes(), before)
        self.assertEqual(self.state['next_numbers'], {})
        self.assertFalse(self.args.output.exists())

    def test_pending_and_incomplete_checklists_cannot_be_submitted(self):
        warnings = d.validate(self.state, self.request)
        report = review_gate.pending(self.state, self.request, warnings)
        with self.assertRaisesRegex(ValueError, '尚未通过'):
            review_gate.verify(self.state, self.request, report, warnings)
        report = self.report()
        report['checks']['continuity']['status'] = 'pending'
        with self.assertRaisesRegex(ValueError, 'continuity'):
            review_gate.verify(self.state, self.request, report, warnings)

    def test_post_review_composition_edit_invalidates_review(self):
        self.report()
        self.request['units'][0]['shots'][0]['composition'] = '人物居右，留出视线方向空间'
        d.write_json(self.path, self.request)
        with self.assertRaisesRegex(ValueError, '草稿已改变'):
            d.commit(self.args, self.state)
        self.assertFalse(self.args.output.exists())

    def test_neighbor_change_invalidates_review(self):
        report = self.report()
        altered = copy.deepcopy(self.state)
        altered['units'].append({'id': 'new-neighbor', 'version': 2})
        with self.assertRaisesRegex(ValueError, '上下文已变化'):
            review_gate.verify(altered, self.request, report, d.validate(self.state, self.request))

    def test_warning_dispositions_must_match_current_diagnostics(self):
        report = self.report()
        warnings = d.validate(self.state, self.request)
        warnings.append({'kind': 'new diagnostic'})
        with self.assertRaisesRegex(ValueError, '警告清单'):
            review_gate.verify(self.state, self.request, report, warnings)

    def test_committed_review_is_version_bound_and_legacy_replay_still_works(self):
        self.report()
        result = d.commit(self.args, self.state)
        self.assertEqual(result['status'], 'committed')
        receipt = self.state['receipts'][-1]
        self.assertEqual(receipt['final_review']['request_sha256'], review_gate.digest(self.request))
        self.assertEqual(receipt['final_review']['status'], 'passed')
        original = self.args.output.read_bytes()
        del self.args.review
        self.assertEqual(d.commit(self.args, self.state)['status'], 'already_committed')
        self.assertEqual(self.args.output.read_bytes(), original)


class RecoveryViewChecks(unittest.TestCase):
    def setUp(self):
        self.fixture = queue_fixture.CanvasJobChecks()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project, self.session = self.fixture.project, self.fixture.session

    def test_queue_knows_total_before_first_node_and_old_state_is_not_rewritten(self):
        result = self.fixture.enqueue('videos', ids=[self.fixture.unit['id']])
        job = self.fixture.read_manifest(result)
        path = jobs.state_file(self.project, result['job_id'])
        self.assertEqual(d.read_json(path)['total'], 1)
        d.write_json(path, {'job_id': result['job_id'], 'status': 'queued', 'completed': 0})
        before = path.read_bytes()
        self.assertEqual(jobs.job_state(self.project, job)['total'], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_stale_intake_cannot_override_current_session_and_queries_write_nothing(self):
        self.fixture.enqueue()
        old = self.project / 'intake-state.json'
        d.write_json(old, {'phase': 'nodes_choice', 'asset_transfer_verified_count': 270,
                           'next_step': 'retry obsolete operation'})
        before = {str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        view = recovery_status.summarize(self.project, self.session)
        self.assertEqual(view['phase'], 'range')
        self.assertFalse(view['legacy_intake']['authoritative'])
        self.assertEqual(view['asset_progress']['total'], 1)
        self.assertTrue(view['legacy_intake']['differences'])
        self.assertNotIn('next_step', view)
        after = {str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_obsolete_input_or_binding_is_reported_as_history(self):
        result = self.fixture.enqueue()
        self.session['ledger']['characters'][0]['image_prompt'] = 'changed'
        view = recovery_status.summarize(self.project, self.session)
        self.assertIn(result['job_id'], view['historical_job_ids'])
        self.assertIsNone(view['asset_progress'])

    def image_folder(self):
        folder = self.project / 'images'
        folder.mkdir()
        (folder / '陈默-基础形象.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        (folder / 'unmatched.png').write_bytes(b'\x89PNG\r\n\x1a\nother')
        (folder / 'ignored.gif').write_bytes(b'GIF89a')
        return folder

    def test_image_total_counts_eligible_matches_not_all_inventory_files(self):
        folder = self.image_folder()
        result = self.fixture.enqueue('images', folder=folder)
        manifest = self.fixture.read_manifest(result)
        self.assertEqual(len(manifest['options']['images']), 3)
        self.assertEqual(jobs.job_state(self.project, manifest)['total'], 1)
        updates = []
        with patch.object(backend, 'ControlledCLI', return_value=object()), \
             patch.object(c.media, 'images', return_value={'status': 'images_imported', 'imported': ['CHAR0001']}):
            outcome = backend.run_job(self.project, manifest, lambda: None, updates.append)
        self.assertEqual(outcome['status'], 'synced')
        self.assertEqual(updates[-1]['completed'], updates[-1]['total'])

    def test_changed_or_missing_image_folder_invalidates_completed_snapshot(self):
        folder = self.image_folder()
        result = self.fixture.enqueue('images', folder=folder)
        d.write_json(jobs.state_file(self.project, result['job_id']),
                     {'job_id': result['job_id'], 'status': 'completed', 'completed': 1, 'total': 1})
        self.assertEqual(len(recovery_status.summarize(self.project, self.session)['jobs']), 1)
        (folder / '陈默-基础形象.png').write_bytes(b'\x89PNG\r\n\x1a\nchanged')
        view = recovery_status.summarize(self.project, self.session)
        self.assertIn(result['job_id'], view['historical_job_ids'])
        self.assertEqual(view['jobs'], [])

    def test_old_image_total_is_unknown_without_rewriting_or_scanning_history(self):
        folder = self.image_folder()
        result = self.fixture.enqueue('images', folder=folder)
        manifest = self.fixture.read_manifest(result)
        manifest['options'].pop('image_target_count')
        path = jobs.state_file(self.project, result['job_id'])
        d.write_json(path, {'job_id': result['job_id'], 'status': 'completed', 'completed': 1})
        before = path.read_bytes()
        self.assertIsNone(jobs.job_state(self.project, manifest)['total'])
        self.assertEqual(path.read_bytes(), before)

    def test_tampered_manifest_cannot_report_a_current_completion(self):
        result = self.fixture.enqueue()
        path = self.fixture.manifest_path(result)
        manifest = self.fixture.read_manifest(result)
        manifest['options']['decisions'] = {'fabricated': True}
        d.write_json(path, manifest)
        view = recovery_status.summarize(self.project, self.session)
        self.assertIn(result['job_id'], view['historical_job_ids'])


class VerifiedVideoProgressChecks(unittest.TestCase):
    def setUp(self):
        self.fixture = video_fixture.VideoWriteReceiptChecks()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_backend(self):
        fixture = self.fixture
        manifest = {'kind': 'videos', 'session': fixture.session,
                    'options': {'request_id': None, 'ids': [fixture.unit_id], 'decisions': {},
                                'existing_only': False, 'references_only': False}}
        self.progress = []
        def render(session, unit, reference_text=None):
            return reference_text(unit['test_prompt']) if reference_text else unit['test_prompt']
        with patch.object(backend, 'ControlledCLI', return_value=fixture.cli), \
             patch.object(c.d, 'render_unit', side_effect=render):
            return backend.run_job(fixture.project, manifest, lambda: None,
                                   lambda value: self.progress.append(copy.deepcopy(value)))

    def test_callback_reports_full_node_before_whole_batch_result(self):
        self.assertEqual(self.run_backend()['status'], 'synced')
        self.assertEqual(self.progress[0]['completed'], 0)
        events = [p for p in self.progress if p.get('stage') == 'node_verified']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['completed'], 1)
        self.assertEqual(events[0]['total'], 1)
        self.assertEqual(events[0]['node_ids'][self.fixture.unit_id], self.fixture.target())

    def test_unknown_step_ack_does_not_advance_count(self):
        self.fixture.cli.unknown_ack_stage = 'duration'
        with self.assertRaises(c.CanvasError):
            self.run_backend()
        self.assertFalse(any(p.get('completed', 0) for p in self.progress))

    def test_reference_failure_does_not_advance_count(self):
        self.fixture.cli.final_fault = 'references'
        result = self.run_backend()
        self.assertNotEqual(result['status'], 'synced')
        self.assertFalse(any(p.get('completed', 0) for p in self.progress))


if __name__ == '__main__':
    unittest.main()
