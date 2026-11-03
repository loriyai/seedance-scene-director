"""Synthetic timing windows, gaps, issues and incomplete evidence."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import copy
import json
import tempfile
import unittest

import storyboard_metrics as metrics


def row(seconds, name, scene='SC0001', **fields):
    return {'at': (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat(),
            'event': name, 'scene': scene, **fields}


def native_preview(seconds, scene='SC0001', **fields):
    # Historical artifact metadata only. The summary must not require these
    # synthetic files to exist or upgrade producer-only legacy milestones.
    artifact = {'draft_sha256': 'a' * 64, 'context_sha256': 'b' * 64,
                'output_sha256': 'c' * 64, 'scenes': [scene], 'span': [0, 10],
                'draft': str(Path.cwd() / 'fictional-draft.txt'),
                'output': str(Path.cwd() / 'fictional-preview.txt')}
    return row(seconds, 'previewed', scene, producer=metrics.PREVIEW_PRODUCER,
               artifact=artifact, **fields)


def observation(result, phase, attempt=1, activity=None):
    return next(window for window in result['milestone_windows']
                if window['phase'] == phase and window['attempt_index'] == attempt
                and window['activity_id'] == activity)


class StoryboardMetricsTests(unittest.TestCase):
    def test_partial_save_does_not_close_full_manuscript_window(self):
        result = metrics.summarize([
            row(0, 'source_read_started', activity_id='draft1', coverage='full'),
            row(2, 'manuscript_partial', activity_id='draft1', note='仅保存前部'),
            row(8, 'manuscript_ready', activity_id='draft1'),
            row(9, 'authoring_ready', activity_id='draft1'),
            native_preview(12, activity_id='draft1'),
        ])
        self.assertEqual(observation(result, 'source_preparation_to_manuscript', activity='draft1')['elapsed_seconds'], 8)
        self.assertEqual(observation(result, 'manuscript_to_authoring', activity='draft1')['elapsed_seconds'], 1)
        self.assertEqual(observation(result, 'authoring_to_first_preview', activity='draft1')['elapsed_seconds'], 3)
        self.assertEqual(result['milestone_observations'][0]['events'][1]['event'], 'manuscript_partial')

    def test_authoring_boundary_is_not_inferred_from_preview_or_another_activity(self):
        result = metrics.summarize([
            row(0, 'manuscript_ready', activity_id='draft1'),
            row(4, 'authoring_ready', activity_id='draft2'),
            native_preview(7, activity_id='draft1'),
        ])
        self.assertIsNone(observation(result, 'manuscript_to_authoring', activity='draft1')['elapsed_seconds'])
        self.assertIsNone(observation(result, 'authoring_to_first_preview', activity='draft1')['elapsed_seconds'])
        self.assertIsNone(observation(result, 'manuscript_to_authoring', activity='draft2')['elapsed_seconds'])

    def test_legacy_partial_save_is_not_promoted_to_complete_manuscript(self):
        events = [row(0, 'draft_saved', note='旧记录，仅部分正文已保存')]
        result = metrics.summarize(events + [row(6, 'authoring_ready')])
        self.assertIsNone(observation(result, 'manuscript_to_authoring')['started_at'])
        self.assertIsNone(observation(result, 'manuscript_to_authoring')['elapsed_seconds'])
        result = metrics.summarize(events + [row(4, 'manuscript_ready'), row(6, 'authoring_ready')])
        self.assertEqual(observation(result, 'manuscript_to_authoring')['elapsed_seconds'], 2)

    def test_explicit_partial_at_either_endpoint_overrides_full_start_declaration(self):
        for start_coverage, end_coverage, expected in (
                ('full', 'partial', 'partial'), ('partial', 'full', 'partial'),
                ('full', None, 'full')):
            with self.subTest(start=start_coverage, end=end_coverage):
                end_fields = {'coverage': end_coverage} if end_coverage else {}
                result = metrics.summarize([
                    row(0, 'source_read_started', coverage=start_coverage),
                    row(4, 'source_read_finished', note='阅读与初步构思合并观察', **end_fields),
                ])
                window = observation(result, 'source_read')
                self.assertEqual(window['measurement_coverage'], expected)
                self.assertEqual(window['start_event']['coverage'], start_coverage)
                self.assertEqual(window['end_event']['note'], '阅读与初步构思合并观察')

    def test_first_review_then_two_rechecks_keep_submission_and_report_boundaries(self):
        events = [row(0, 'review_submitted'), row(1, 'review_started', coverage='full'),
                  row(3, 'review_report_finished'), row(4, 'root_accepted'), row(5, 'review_finished')]
        for offset in (10, 20):
            events.extend([
                row(offset, 'revision_started', coverage='full'), row(offset + 1, 'revision_finished'),
                row(offset + 2, 'review_submitted', note='实际请求本轮复核'),
                row(offset + 3, 'recheck_started', coverage='full', note='实际开始读取本轮影响'),
                row(offset + 5, 'review_report_finished', note='本轮实际报告已完成'),
                row(offset + 6, 'root_accepted'), row(offset + 7, 'recheck_finished'),
            ])
        result = metrics.summarize(events)
        self.assertEqual(len(result['milestone_observations']), 3)
        for attempt in (1, 2, 3):
            self.assertEqual(observation(result, 'submission_to_review_start', attempt)['elapsed_seconds'], 1)
            self.assertEqual(observation(result, 'review_to_report', attempt)['elapsed_seconds'], 2)
            self.assertEqual(observation(result, 'report_to_root_acceptance', attempt)['elapsed_seconds'], 1)
        for attempt in (2, 3):
            reading = observation(result, 'review_to_report', attempt)['start_event']
            self.assertEqual(reading['event'], 'recheck_started')
            self.assertEqual(reading['note'], '实际开始读取本轮影响')
        self.assertEqual([window['phase'] for window in result['phase_windows']],
                         ['review', 'revision', 'recheck', 'revision', 'recheck'])
        self.assertFalse(result['coverage_warnings'])
        self.assertTrue(all(window['elapsed_seconds'] == 1 for window in result['handoff_reason_windows']))

    def test_new_dispatch_after_closed_report_keeps_recheck_submission(self):
        result = metrics.summarize([
            row(0, 'review_report_finished'), row(1, 'root_accepted'),
            row(2, 'review_submitted'), row(4, 'recheck_started'), row(8, 'review_report_finished'),
        ])
        self.assertEqual(len(result['milestone_observations']), 2)
        window = observation(result, 'submission_to_review_start', attempt=2)
        self.assertEqual(window['elapsed_seconds'], 2)
        self.assertEqual(window['end_event']['event'], 'recheck_started')
        self.assertEqual(result['phase_windows'][-1]['phase'], 'recheck')
        self.assertEqual(result['phase_windows'][-1]['elapsed_seconds'], 4)

    def test_legacy_recheck_closure_is_not_promoted_to_report_completion(self):
        result = metrics.summarize([row(0, 'recheck_started'), row(5, 'recheck_finished')])
        self.assertEqual(result['phase_windows'][0]['elapsed_seconds'], 5)
        self.assertIn('历史recheck_finished', result['phase_windows'][0]['meaning'])
        self.assertIsNone(observation(result, 'review_to_report')['finished_at'])

    def test_report_closes_pending_review_before_overlapping_recheck(self):
        result = metrics.summarize([
            row(0, 'review_started'), row(1, 'recheck_started'),
            row(3, 'review_report_finished'), row(4, 'review_report_finished'),
            row(5, 'recheck_finished'),
        ])
        self.assertEqual([window['phase'] for window in result['phase_windows']], ['review', 'recheck'])
        self.assertEqual([window['elapsed_seconds'] for window in result['phase_windows']], [3, 3])

    def test_source_preparation_and_late_manuscript_save_are_distinct(self):
        result = metrics.summarize([
            row(0, 'source_read_started', note='阅读原文并开始构思机位', coverage='partial'),
            row(100, 'source_read_finished', note='含初步构思，无法分离纯阅读'),
            row(101, 'draft_started', coverage='full', note='开始保存已构思正文'),
            row(102, 'manuscript_ready', note='首次实际落盘'), native_preview(110),
        ])
        self.assertEqual(observation(result, 'source_preparation_to_manuscript')['elapsed_seconds'], 102)
        save = observation(result, 'draft_to_manuscript')
        self.assertEqual(save['elapsed_seconds'], 1)
        self.assertEqual(save['start_event']['note'], '开始保存已构思正文')
        self.assertIn('event_id', save['end_event'])
        self.assertEqual(observation(result, 'source_read')['measurement_coverage'], 'partial')
        self.assertIn('可同时包含构思', result['milestone_meanings']['source_read'])
        self.assertIn('不能证明', result['phase_windows'][0]['meaning'])

    def test_legacy_draft_finished_is_candidate_ready_not_first_preview(self):
        result = metrics.summarize([
            row(1, 'draft_started'), row(2, 'draft_saved'), native_preview(3),
            row(6, 'draft_finished', note='候选稿及trace全部准备完成', coverage='partial'),
            row(10, 'review_started'),
        ])
        candidate = observation(result, 'candidate_to_review_start')
        self.assertEqual(candidate['elapsed_seconds'], 4)
        self.assertEqual(candidate['start_event']['event'], 'draft_finished')
        self.assertEqual(observation(result, 'manuscript_to_first_preview')['elapsed_seconds'], 1)
        self.assertEqual(observation(result, 'draft_to_manuscript')['end_event']['event'], 'draft_saved')

    def test_optional_phase_endpoints_are_not_inferred_from_later_milestones(self):
        result = metrics.summarize([
            row(0, 'source_read_started'), row(2, 'conception_started'),
            row(4, 'manuscript_ready'), row(6, 'root_accepted'),
        ])
        for name in ('source_read', 'conception'):
            window = observation(result, name)
            self.assertEqual(window['bounds'], 'missing_finish')
            self.assertIsNone(window['elapsed_seconds'])
        self.assertIsNone(observation(result, 'draft_to_manuscript')['started_at'])
        self.assertIsNone(observation(result, 'report_to_root_acceptance')['started_at'])
        self.assertIsNone(result['milestone_observations'][0]['milestones']['review_report_finished'])

    def test_report_completion_closes_review_before_distinct_root_acceptance(self):
        result = metrics.summarize([
            row(1, 'review_submitted', note='实际请求派审'),
            row(5, 'review_started', coverage='full'), row(11, 'review_report_finished', note='实际报告已保存'),
            row(20, 'root_accepted'), row(22, 'review_finished'),
        ])
        self.assertEqual(observation(result, 'review_to_report')['elapsed_seconds'], 6)
        self.assertEqual(observation(result, 'report_to_root_acceptance')['elapsed_seconds'], 9)
        self.assertEqual(len(result['phase_windows']), 1)
        self.assertEqual(result['phase_windows'][0]['elapsed_seconds'], 6)
        self.assertEqual(result['phase_windows'][0]['end_event']['event'], 'review_report_finished')
        self.assertFalse(result['coverage_warnings'])

    def test_legacy_review_finish_does_not_become_report_or_root_event(self):
        result = metrics.summarize([row(2, 'review_started'),
                                    row(10, 'review_finished', note='主代理报告绑定验收')])
        self.assertEqual(result['phase_windows'][0]['elapsed_seconds'], 8)
        self.assertIn('主代理验收闭环', result['phase_windows'][0]['meaning'])
        self.assertIsNone(observation(result, 'review_to_report')['elapsed_seconds'])
        points = result['milestone_observations'][0]['milestones']
        self.assertIsNone(points['review_report_finished'])
        self.assertIsNone(points['root_accepted'])

    def test_wait_reasons_apply_only_forward_until_actual_transition(self):
        result = metrics.summarize([
            native_preview(0),
            row(3, 'review_waiting', reason='preparing_handoff', note='当前整理交接材料'),
            row(5, 'review_submitted', coverage='partial', note='现在观察到已派出，早前请求时刻未知'),
            row(7, 'review_waiting', reason='reviewer_busy', note='当前审阅者仍被占用'),
            row(11, 'review_waiting', reason='user_paused', note='当前用户要求暂停'),
            row(15, 'review_started'),
        ])
        windows = result['handoff_reason_windows']
        self.assertEqual([w['reason'] for w in windows],
                         ['unknown', 'preparing_handoff', 'unknown', 'reviewer_busy', 'user_paused'])
        self.assertEqual([w['elapsed_seconds'] for w in windows], [3, 2, 2, 4, 4])
        self.assertEqual(windows[2]['start_event']['coverage'], 'partial')
        self.assertEqual(observation(result, 'submission_to_review_start')['elapsed_seconds'], 10)

    def test_missing_queue_end_and_unobserved_prior_reason_remain_unknown(self):
        result = metrics.summarize([
            row(5, 'review_waiting', reason='dependencies_unready', note='当前缺本次依赖'),
        ])
        before, after = result['handoff_reason_windows']
        self.assertEqual(before['reason'], 'unknown')
        self.assertEqual(before['bounds'], 'missing_start')
        self.assertEqual(after['reason'], 'dependencies_unready')
        self.assertEqual(after['bounds'], 'missing_finish')
        self.assertIsNone(before['elapsed_seconds'])
        self.assertIsNone(after['elapsed_seconds'])

    def test_repeated_preview_and_revision_are_isolated_per_attempt(self):
        result = metrics.summarize([
            row(0, 'draft_started'), row(1, 'manuscript_ready'), native_preview(2), native_preview(3),
            row(4, 'review_submitted'), row(5, 'review_started'), row(6, 'review_report_finished'),
            row(7, 'revision_started'), row(8, 'manuscript_ready'), native_preview(9), native_preview(10),
            row(11, 'review_submitted'), row(12, 'review_started'), row(13, 'review_report_finished'),
        ])
        self.assertEqual(len(result['milestone_observations']), 2)
        for attempt in (1, 2):
            self.assertEqual(observation(result, 'manuscript_to_first_preview', attempt)['elapsed_seconds'], 1)
            self.assertEqual(observation(result, 'preview_to_review_submission', attempt)['elapsed_seconds'], 2)
        self.assertEqual([w['elapsed_seconds'] for w in result['handoff_reason_windows']], [2, 1, 2, 1])

    def test_interleaved_activity_ids_do_not_share_first_preview_or_queue_reason(self):
        result = metrics.summarize([
            native_preview(0, activity_id='a'), native_preview(1, activity_id='b'),
            row(2, 'review_waiting', activity_id='a', reason='reviewer_busy', note='a审阅者忙'),
            row(3, 'review_started', activity_id='b'), row(5, 'review_started', activity_id='a'),
        ])
        waits_a = [w for w in result['handoff_reason_windows'] if w['activity_id'] == 'a']
        waits_b = [w for w in result['handoff_reason_windows'] if w['activity_id'] == 'b']
        self.assertEqual([w['reason'] for w in waits_a], ['unknown', 'reviewer_busy'])
        self.assertEqual([w['elapsed_seconds'] for w in waits_b], [2])
        self.assertEqual(waits_b[0]['reason'], 'unknown')

    def test_native_first_success_uses_effective_artifact_events_only(self):
        rows = metrics.identified([
            row(0, 'manuscript_ready'), row(1, 'previewed'),
            row(2, 'previewed', producer=metrics.PREVIEW_PRODUCER),
            native_preview(4), native_preview(6),
        ])
        rows.append(row(7, 'event_invalidated', invalidates=rows[3]['event_id'], note='实际产物事件已撤销'))
        result = metrics.summarize(rows)
        first = observation(result, 'manuscript_to_first_preview')
        self.assertEqual(first['elapsed_seconds'], 6)
        self.assertEqual(first['end_event']['event_id'], rows[4]['event_id'])
        legacy = metrics.summarize(rows[:3])
        self.assertEqual(observation(legacy, 'manuscript_to_first_preview')['bounds'], 'missing_finish')

    def test_review_waiting_append_requires_reason_and_actual_note(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / 'session.json').write_text('{}', encoding='utf-8')
            for fields in ({}, {'reason': 'reviewer_busy'}, {'reason': 'invalid', 'note': '观察'},
                           {'reason': ['unknown'], 'note': '观察'}, {'reason': 'unknown', 'note': ' '}):
                with self.subTest(fields=fields), self.assertRaises(ValueError):
                    metrics.record_event(project, 'review_waiting', at=row(1, 'x')['at'], **fields)
            for reason in sorted(metrics.WAIT_REASONS):
                event = metrics.record_event(project, 'review_waiting', at=row(2, 'x')['at'],
                                             scene='SC0001', reason=reason, note='当前实际观察')
                self.assertEqual(event['reason'], reason)
            before = (project / 'text-storyboards/events.jsonl').read_bytes()
            with self.assertRaises(ValueError):
                metrics.record_event(project, 'review_started', at=row(3, 'x')['at'],
                                     reason='reviewer_busy', note='错误阶段字段')
            self.assertEqual((project / 'text-storyboards/events.jsonl').read_bytes(), before)

    def test_correction_after_completion_excludes_bad_preview_without_extending_wall(self):
        rows = metrics.identified([row(0, 'start'), row(1, 'draft_started', coverage='full'),
            row(3, 'previewed'), row(7, 'previewed', producer=metrics.PREVIEW_PRODUCER),
            row(9, 'review_started', coverage='full'), row(10, 'review_finished'), row(12, 'complete')])
        wrong = rows[2]['event_id']
        rows.append(row(20, 'event_invalidated', invalidates=wrong, note='actual rejected output'))
        result = metrics.summarize(rows)
        self.assertEqual(result['total_wall_seconds'], 12)
        self.assertEqual(result['phase_windows'][0]['elapsed_seconds'], 6)
        self.assertEqual([g['elapsed_seconds'] for g in result['handoff_gaps']], [2])
        self.assertFalse(result['coverage_warnings'])
        self.assertEqual(result['invalidated_events'][0]['event']['event_id'], wrong)
        self.assertEqual(len(result['events']), 8)

    def test_revision_and_assembly_previews_do_not_invent_extra_draft_windows(self):
        result = metrics.summarize([row(0, 'start'), row(1, 'draft_started', coverage='full'),
            row(5, 'previewed', producer=metrics.PREVIEW_PRODUCER),
            row(6, 'revision_started', coverage='full'), row(7, 'revision_finished'),
            row(8, 'previewed', producer=metrics.PREVIEW_PRODUCER),
            row(9, 'previewed', producer=metrics.PREVIEW_PRODUCER), row(12, 'complete')])
        self.assertEqual(len(result['phase_windows']), 2)
        self.assertFalse(result['coverage_warnings'])
        self.assertEqual(result['by_phase']['draft']['interval_union_seconds'], 4)

    def test_legacy_ids_stable_and_correction_does_not_rewrite_original_log(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project/'session.json').write_text('{}', encoding='utf-8')
            log = project/'text-storyboards/events.jsonl'
            log.parent.mkdir()
            rows = [row(0, 'start'), row(1, 'draft_started'), row(3, 'previewed'), row(10, 'complete')]
            original = ''.join(json.dumps(r)+'\n' for r in rows).encode('utf-8')
            log.write_bytes(original)
            key = metrics.read_events(project)[2]['event_id']
            self.assertTrue(key.startswith('legacy-'))
            metrics.invalidate_event(project, key, at=row(12, 'issue')['at'], note='actual erroneous end')
            self.assertTrue(log.read_bytes().startswith(original))
            self.assertEqual(metrics.read_events(project)[2]['event_id'], key)
            summary = metrics.timing(project)
            self.assertEqual(summary['total_wall_seconds'], 10)
            self.assertEqual(summary['phase_windows'][0]['bounds'], 'missing_finish')
            self.assertIsNone(summary['phase_windows'][0]['elapsed_seconds'])
            before = log.read_bytes()
            for target in (key, 'nonexistent', metrics.read_events(project)[0]['event_id']):
                with self.assertRaises(ValueError):
                    metrics.invalidate_event(project, target, at=row(13, 'issue')['at'], note='invalid target')
                self.assertEqual(log.read_bytes(), before)

    def test_preview_success_requires_verified_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project/'session.json').write_text('{}', encoding='utf-8')
            for fields in ({}, {'producer':metrics.PREVIEW_PRODUCER, 'artifact':{}},
                           {'producer':metrics.PREVIEW_PRODUCER, 'artifact':{'output_sha256':'bad'}}):
                with self.assertRaises(ValueError):
                    metrics.record_event(project, 'previewed', at=row(1, 'previewed')['at'], **fields)
            self.assertFalse(metrics.read_events(project))

    def test_nested_repair_is_not_added_to_revision_wall_time(self):
        result = metrics.summarize([
            row(0, 'start'), row(10, 'revision_started', coverage='full', note='实际退回问题'),
            row(20, 'tool_repair_started', note='实际工具报错'),
            row(30, 'tool_repair_finished'), row(50, 'revision_finished'), row(60, 'complete')])
        self.assertEqual(result['total_wall_seconds'], 60)
        self.assertEqual(result['by_phase']['revision']['interval_union_seconds'], 40)
        self.assertEqual(result['by_phase']['tool_repair']['interval_union_seconds'], 10)
        self.assertEqual(result['measured_windows']['seconds_sum'], 50)
        self.assertEqual(result['measured_windows']['interval_union_seconds'], 40)
        self.assertEqual(result['measured_windows']['interval_overlap_seconds'], 10)
        self.assertEqual(result['measured_windows']['uncovered_wall_seconds'], 20)
        self.assertEqual(len(result['issues']), 2)
        self.assertTrue(all(i['classification_source'] == 'legacy_event_name' for i in result['issues']))
        self.assertIsNone(result['model_seconds'])

    def test_legacy_late_draft_start_cannot_claim_full_creation_coverage(self):
        result = metrics.summarize([row(0, 'start'), row(50, 'draft_started'),
                                    row(51, 'draft_saved'), row(63, 'previewed'), row(80, 'complete')])
        window = result['phase_windows'][0]
        self.assertEqual(window['elapsed_seconds'], 13)
        self.assertEqual(window['measurement_coverage'], 'unspecified')
        self.assertEqual(result['coverage_warnings'], [window])

    def test_partial_and_missing_bounds_remain_explicit(self):
        result = metrics.summarize([row(0, 'start'), row(1, 'draft_started', coverage='partial'),
                                    row(4, 'previewed'), row(5, 'review_finished'),
                                    row(6, 'revision_started'), row(9, 'complete')])
        self.assertEqual([w['bounds'] for w in result['phase_windows']],
                         ['bounded', 'missing_start', 'missing_finish'])
        self.assertEqual(len(result['coverage_warnings']), 3)
        self.assertIsNone(result['phase_windows'][1]['elapsed_seconds'])
        self.assertIsNone(result['phase_windows'][2]['elapsed_seconds'])

    def test_interleaved_attempts_pair_by_activity_not_arbitrary_order(self):
        result = metrics.summarize([row(0, 'start'),
            row(1, 'review_started', activity_id='a'), row(2, 'review_started', activity_id='b'),
            row(4, 'review_finished', activity_id='a'), row(8, 'review_finished', activity_id='b'),
            row(10, 'complete')])
        self.assertEqual([w['elapsed_seconds'] for w in result['phase_windows']], [3, 6])
        self.assertEqual(result['by_phase']['review']['interval_union_seconds'], 7)
        self.assertEqual(result['by_phase']['review']['interval_overlap_seconds'], 2)

    def test_unfinished_attempt_is_not_bridged_to_the_next_attempt(self):
        result = metrics.summarize([row(0, 'draft_started'), row(4, 'draft_started'), row(8, 'previewed')])
        self.assertEqual(result['phase_windows'][0]['bounds'], 'missing_finish')
        self.assertEqual(result['phase_windows'][1]['elapsed_seconds'], 4)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['total_wall_seconds'])

    def test_latest_run_and_first_completion_bound_the_measurement(self):
        result = metrics.summarize([row(0, 'start'), row(10, 'complete'),
            row(20, 'start'), row(30, 'review_started'), row(40, 'review_finished'),
            row(50, 'complete'), row(80, 'complete'), row(90, 'review_started')])
        self.assertEqual(result['total_wall_seconds'], 30)
        self.assertEqual(len(result['phase_windows']), 1)
        self.assertEqual(len(result['current_run_events']), 6)

    def test_gaps_are_milestone_intervals_not_idle_or_extra_wall_time(self):
        result = metrics.summarize([row(0, 'start'), row(3, 'previewed'),
            row(8, 'review_started'), row(12, 'review_finished'), row(14, 'revision_finished'),
            row(16, 'recheck_started'), row(18, 'recheck_finished'), row(20, 'complete')])
        self.assertEqual([g['elapsed_seconds'] for g in result['handoff_gaps']], [5, 2])
        self.assertEqual(result['total_wall_seconds'], 20)
        self.assertEqual([g['from'] for g in result['handoff_gaps']], ['previewed', 'revision_finished'])

    def test_explicit_issue_is_not_automatically_marked_closed(self):
        result = metrics.summarize([row(0, 'start'),
            row(2, 'issue', issue_kind='author_revision', note='实际作者自修'), row(3, 'complete')])
        self.assertEqual(result['issues'][0]['classification_source'], 'explicit')
        self.assertNotIn('resolved', result['issues'][0])

    def test_read_only_summary_and_invalid_append_leave_existing_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / 'session.json').write_text('{}', encoding='utf-8')
            metrics.record_event(project, 'start', at=row(10, 'start')['at'])
            path = project / 'text-storyboards/events.jsonl'
            before = path.read_bytes()
            metrics.timing(project)
            self.assertEqual(path.read_bytes(), before)
            for fields in ({'at': row(5, 'issue')['at']},
                           {'at': row(20, 'issue')['at'], 'issue_kind': 'tool_repair'},
                           {'at': '2026-01-01T00:00:30'}):
                with self.assertRaises(ValueError):
                    metrics.record_event(project, 'issue', **fields)
                self.assertEqual(path.read_bytes(), before)


class CommandTimingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-command-timing-')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.log = self.project / 'timing-preview.json'
        self.command = {
            'phase': 'preview', 'command': ['python', 'fictional-preview.py'],
            'measurement_kind': metrics.COMMAND_MEASUREMENT_KIND,
            'started_at': row(2, 'x')['at'], 'finished_at': row(4, 'x')['at'],
            'elapsed_seconds': 2.0001, 'returncode': 0,
        }

    def write_command(self, value=None):
        self.log.write_text(json.dumps(self.command if value is None else value), encoding='utf-8')

    def test_explicit_command_measurements_are_read_only_and_separate_from_phases(self):
        self.write_command()
        events = [row(0, 'start'), row(1, 'draft_started'), row(5, 'previewed'), row(7, 'complete')]
        event_file = self.project / 'text-storyboards/events.jsonl'
        event_file.parent.mkdir()
        event_file.write_text(''.join(json.dumps(item) + '\n' for item in events), encoding='utf-8')
        before = {path: path.read_bytes() for path in (self.log, event_file)}
        ordinary = metrics.timing(self.project)
        result = metrics.timing(self.project, command_logs=[self.log, self.log])
        self.assertEqual(ordinary['command_measurements'], [])
        self.assertEqual(len(result['command_measurements']), 1)
        command = result['command_measurements'][0]
        self.assertEqual(command['elapsed_seconds'], 2.0001)
        self.assertEqual(command['timestamp_elapsed_seconds'], 2)
        self.assertEqual(result['measured_windows'], ordinary['measured_windows'])
        self.assertEqual(result['total_wall_seconds'], ordinary['total_wall_seconds'])
        self.assertIsNone(result['model_seconds'])
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_failed_actual_command_is_observed_with_original_return_code(self):
        self.command['returncode'] = 127
        self.command['error'] = 'synthetic executable missing'
        self.write_command()
        result = metrics.timing(self.project, command_logs=[self.log])
        self.assertEqual(result['command_measurements'][0]['returncode'], 127)
        self.assertIsNone(result['total_wall_seconds'])

    def test_actual_start_only_command_log_has_unknown_finish_duration_and_return_code(self):
        running = {key: value for key, value in self.command.items()
                   if key not in {'finished_at', 'elapsed_seconds', 'returncode'}}
        for explicit_nulls in (False, True):
            with self.subTest(explicit_nulls=explicit_nulls):
                value = dict(running)
                if explicit_nulls:
                    value.update(finished_at=None, elapsed_seconds=None, returncode=None)
                self.write_command(value)
                before = self.log.read_bytes()
                measured = metrics.timing(self.project, command_logs=[self.log])['command_measurements'][0]
                self.assertEqual(measured['started_at'], running['started_at'])
                self.assertEqual(measured['bounds'], 'missing_finish')
                for key in ('finished_at', 'elapsed_seconds', 'returncode', 'timestamp_elapsed_seconds'):
                    self.assertIsNone(measured[key])
                self.assertEqual(self.log.read_bytes(), before)

    def test_start_only_log_does_not_hide_invalid_partial_completion_fields(self):
        running = {key: value for key, value in self.command.items()
                   if key not in {'finished_at', 'elapsed_seconds', 'returncode'}}
        for value in (-1, float('nan'), float('inf'), True, '1', 1):
            with self.subTest(elapsed=value):
                self.write_command({**running, 'elapsed_seconds': value})
                with self.assertRaises(ValueError):
                    metrics.timing(self.project, command_logs=[self.log])

    def test_incomplete_or_invalid_command_schema_is_rejected(self):
        variants = [
            ('elapsed_seconds', -1), ('elapsed_seconds', True),
            ('elapsed_seconds', float('nan')), ('elapsed_seconds', float('inf')),
            ('finished_at', row(1, 'x')['at']), ('started_at', '2026-01-01T00:00:00'),
            ('returncode', False), ('returncode', '0'), ('command', []),
            ('command', 'python fictional.py'), ('phase', 'model_generation'),
            ('measurement_kind', 'estimated_model_time'),
        ]
        for key, value in variants:
            with self.subTest(key=key, value=value):
                invalid = copy.deepcopy(self.command)
                invalid[key] = value
                self.write_command(invalid)
                with self.assertRaises(ValueError):
                    metrics.timing(self.project, command_logs=[self.log])
        for key in ('started_at', 'finished_at', 'returncode', 'elapsed_seconds'):
            with self.subTest(missing=key):
                invalid = copy.deepcopy(self.command)
                invalid.pop(key)
                self.write_command(invalid)
                with self.assertRaises(ValueError):
                    metrics.timing(self.project, command_logs=[self.log])

    def test_only_explicit_existing_measurements_are_loaded(self):
        self.log.write_text('malformed unrequested file', encoding='utf-8')
        self.assertEqual(metrics.timing(self.project)['command_measurements'], [])
        missing = self.project / 'missing-command.json'
        with self.assertRaises(FileNotFoundError):
            metrics.timing(self.project, command_logs=[missing])
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
