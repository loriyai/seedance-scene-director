"""Synthetic timing windows, gaps, issues and incomplete evidence."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest

import storyboard_metrics as metrics


def row(seconds, name, scene='SC0001', **fields):
    return {'at': (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat(),
            'event': name, 'scene': scene, **fields}


class StoryboardMetricsTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
