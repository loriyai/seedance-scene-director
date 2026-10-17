"""Regression tests for close-before-bind and honestly scoped handoff timing."""
import json
from pathlib import Path
import tempfile
import unittest

import agent_handoff as handoff
import pipeline_metrics as metrics
import test_stage_handoff as stages


class BindLifecycleTests(unittest.TestCase):
    setUp = stages.StageHandoffTests.setUp
    write_json = stages.StageHandoffTests.write_json
    save_config = stages.StageHandoffTests.save_config
    save_brief = stages.StageHandoffTests.save_brief
    prepare = stages.StageHandoffTests.prepare
    artifact = stages.StageHandoffTests.artifact

    def test_binding_blocked_until_verified_and_closed_with_artifact_identity(self):
        _, path, manifest = self.prepare('design')
        self.artifact(manifest)
        verified = handoff.verify(str(path))
        before = {p: p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, '绑定前须先verify'):
            handoff.assert_bind_ready(self.project)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertFalse((self.project / 'session.json').exists())
        closed = handoff.close(str(path), 'completed', '已核对实际草稿及业务审阅记录')
        self.assertEqual(closed['artifact_sha256'], verified['artifact_sha256'])
        self.assertEqual(handoff.assert_bind_ready(self.project)['status'], 'bind_ready')
        # After close root may bind. Repeating close is idempotent, and does not
        # re-verify a historical snapshot against the subsequently bound state.
        self.write_json(self.project / 'session.json', {'phase': 'asset_choice'})
        self.assertEqual(handoff.close(str(path), 'completed', '已关闭'), closed)
        # Direct verification still rejects changed immutable inputs.
        with self.assertRaisesRegex(ValueError, '存在性'):
            handoff.verify(str(path))

    def test_review_and_missing_state_also_block_binding(self):
        self.brief['review_stage'] = 'assets'
        self.save_brief()
        _, path, _ = self.prepare('review')
        (path.parent / 'handoff-state.json').unlink()
        with self.assertRaisesRegex(ValueError, '未关闭交接'):
            handoff.assert_bind_ready(self.project)
        handoff.close(str(path), 'cancelled', '宿主已确认写者退出，本次未采用')
        self.assertEqual(handoff.assert_bind_ready(self.project)['status'], 'bind_ready')

    def test_changed_source_cannot_be_completed_to_bypass_guard(self):
        _, path, manifest = self.prepare('assets')
        self.artifact(manifest)
        self.source.write_text('已变更的剧本', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '输入已改变'):
            handoff.close(str(path), 'completed', '尝试关闭')
        with self.assertRaisesRegex(ValueError, '未关闭交接'):
            handoff.assert_bind_ready(self.project)

    def test_invalid_state_is_not_treated_as_closed(self):
        _, path, _ = self.prepare('assets')
        self.write_json(path.parent / 'handoff-state.json', {'status': 'accepted'})
        with self.assertRaisesRegex(ValueError, '状态无效'):
            handoff.assert_bind_ready(self.project)

    def test_legacy_handoffs_remain_readable_without_new_reservations(self):
        self.config.pop('handoff_policy')
        self.save_config()
        _, path, manifest = self.prepare()
        self.artifact(manifest)
        before = path.read_bytes()
        self.assertEqual(handoff.assert_bind_ready(self.project)['status'], 'bind_ready')
        self.assertEqual(handoff.verify(str(path))['result_status'], 'draft_complete')
        self.assertEqual(path.read_bytes(), before)


class HandoffMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def time(self, minute):
        return f'2026-10-17T00:{minute:02d}:00+00:00'

    def add_handoff(self, identifier, start, end=None, status='completed', modern=True):
        folder = self.project / 'agents' / identifier
        folder.mkdir(parents=True)
        manifest = {'id': identifier, 'stage': 'design', 'brief': {'scope': {'task_key': 'same-task'}}}
        if modern:
            manifest.update(routing_policy='stage-handoff-v2', prepared_at=self.time(start))
        (folder / 'handoff.json').write_text(json.dumps(manifest), encoding='utf-8')
        if end is not None:
            (folder / 'handoff-state.json').write_text(json.dumps(
                {'status': status, 'closed_at': self.time(end), 'reason': '真实关闭'}), encoding='utf-8')

    def test_retry_intervals_and_manual_events_are_not_added_together(self):
        self.add_handoff('first', 0, 4, status='cancelled')
        self.add_handoff('retry', 6, 9)
        metrics.record_event(self.project, 'design', 'manual', 'started', occurred_at=self.time(1))
        metrics.record_event(self.project, 'design', 'manual', 'finished', occurred_at=self.time(8))
        result = metrics.summarize_project(self.project)
        observed = result['handoff_metrics']
        self.assertEqual(len(observed['tasks']), 2)
        self.assertEqual(observed['completed_task_seconds_sum'], 420)
        self.assertEqual(observed['completed_interval_union_seconds'], 420)
        self.assertEqual(observed['completed_window_seconds'], 540)
        self.assertEqual(observed['completed_window_uncovered_seconds'], 120)
        self.assertEqual(result['completed_task_seconds_sum'], 420)
        self.assertIsNone(result['total_pipeline_seconds'])
        self.assertIsNone(observed['total_pipeline_seconds'])

    def test_parallel_intervals_union_and_overlap_are_explicit(self):
        self.add_handoff('first', 0, 4)
        self.add_handoff('second', 1, 5)
        result = metrics.summarize_project(self.project)['handoff_metrics']
        self.assertEqual(result['completed_task_seconds_sum'], 480)
        self.assertEqual(result['completed_interval_union_seconds'], 300)
        self.assertEqual(result['completed_interval_overlap_seconds'], 180)
        self.assertEqual(result['completed_window_uncovered_seconds'], 0)

    def test_missing_old_and_active_times_are_not_invented_and_read_is_pure(self):
        self.add_handoff('legacy', 0, modern=False)
        self.add_handoff('active', 2)
        before = {p: p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        result = metrics.summarize_project(self.project)
        self.assertEqual(before, {p: p.read_bytes() for p in self.project.rglob('*') if p.is_file()})
        observed = result['handoff_metrics']
        self.assertEqual(len(observed['unmeasured_handoffs']), 1)
        self.assertEqual(observed['tasks'][0]['coverage'], 'missing_finish')
        self.assertIsNone(observed['tasks'][0]['elapsed_seconds'])
        self.assertIsNone(observed['completed_window_seconds'])
        self.assertIsNone(result['total_pipeline_seconds'])
        self.assertFalse((self.project / 'pipeline-events.jsonl').exists())

    def test_late_manual_start_is_flagged_without_backfill(self):
        events = [metrics.record_event(self.project, 'design', 'late', status,
                                      occurred_at=self.time(minute))
                  for status, minute in [('draft_saved', 0), ('started', 2), ('finished', 3)]]
        row = metrics.summarize(events)['tasks'][0]
        self.assertEqual(row['coverage'], 'late_start')
        self.assertEqual(row['elapsed_seconds'], 60)


if __name__ == '__main__':
    unittest.main()
