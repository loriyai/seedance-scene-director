"""Regression tests for close-before-bind and honestly scoped handoff timing."""
import json
from datetime import timedelta
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

    def test_linked_milestones_share_lifecycle_and_keep_versions_and_check_kinds(self):
        prepared, path, manifest = self.prepare('design')
        self.assertIn('pipeline_metrics.py record --handoff', prepared['task'])
        self.artifact(manifest)
        revision = Path(manifest['output_dir']) / 'result.json'
        first = metrics.record_handoff_event(path, 'draft_saved', revision_file=revision)
        structure = metrics.record_handoff_event(path, 'checked', revision_file=revision, check_kind='structure')
        metrics.record_handoff_event(path, 'needs_review', revision_file=revision, check_kind='semantic')
        # A new draft is a new check input while immutable source identity stays.
        revision.write_text(revision.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        second = metrics.record_handoff_event(path, 'checked', revision_file=revision, check_kind='semantic')
        self.assertEqual(first['input_digest'], second['input_digest'])
        self.assertNotEqual(first['revision_digest'], second['revision_digest'])
        self.assertEqual(first['revision_digest'], structure['revision_digest'])
        handoff.close(str(path), 'completed', '已实际验收最新稿')
        result = metrics.summarize_project(self.project)
        self.assertEqual(result['tasks'], [])
        self.assertEqual(result['completed_task_seconds_sum'], 0)
        observed = result['handoff_metrics']
        self.assertEqual(len(observed['tasks']), 1)
        self.assertEqual(observed['completed_interval_count'], 1)
        self.assertEqual(observed['first_checks_by_kind'], {
            'semantic': {'passed': 1, 'total': 2}, 'structure': {'passed': 1, 'total': 1}})
        self.assertEqual(observed['tasks'][0]['milestones']['first_draft_at'], first['occurred_at'])
        self.assertEqual(result['unmatched_handoff_event_ids'], [])

    def test_linked_events_refuse_history_backfill_and_unbound_checks(self):
        _, path, manifest = self.prepare('assets')
        with self.assertRaisesRegex(ValueError, '起止'):
            metrics.record_handoff_event(path, 'started')
        with self.assertRaisesRegex(ValueError, 'check_kind'):
            metrics.record_handoff_event(path, 'checked')
        with self.assertRaisesRegex(ValueError, 'revision_file'):
            metrics.record_handoff_event(path, 'checked', check_kind='structure')
        with self.assertRaisesRegex(ValueError, 'output'):
            metrics.record_handoff_event(path, 'draft_saved', revision_file=self.source)
        handoff.close(str(path), 'cancelled', '未继续')
        with self.assertRaisesRegex(ValueError, '已关闭'):
            metrics.record_handoff_event(path, 'compiled')
        self.assertFalse((self.project / 'pipeline-events.jsonl').exists())

    def test_close_and_verify_do_not_create_semantic_success_events(self):
        _, path, manifest = self.prepare('assets')
        self.artifact(manifest)
        handoff.verify(str(path))
        handoff.close(str(path), 'completed', '主代理说明')
        result = metrics.summarize_project(self.project)['handoff_metrics']
        self.assertEqual(result['first_check_total'], 0)
        self.assertEqual(result['tasks'][0]['milestones']['first_checks_at'], {})
        self.assertFalse((self.project / 'pipeline-events.jsonl').exists())

    def test_linked_events_outside_lifecycle_are_rejected(self):
        _, path, manifest = self.prepare('design')
        before_prepare = (metrics.stamp(manifest['prepared_at']) - timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, '早于'):
            metrics.record_handoff_event(path, 'compiled', occurred_at=before_prepare)
        self.assertFalse((self.project / 'pipeline-events.jsonl').exists())
        metrics.record_handoff_event(path, 'compiled')
        closed = handoff.close(str(path), 'cancelled', '未采用')
        event_path = self.project / 'pipeline-events.jsonl'
        event = json.loads(event_path.read_text(encoding='utf-8'))
        event['occurred_at'] = (metrics.stamp(closed['closed_at']) + timedelta(seconds=1)).isoformat()
        event_path.write_text(json.dumps(event) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '超出交接生命周期'):
            metrics.summarize_project(self.project)


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

    def work(self, identifier, category, status, minute, parent=None):
        return metrics.record_event(self.project, 'design', 'work', status,
                                    work_id=identifier, category=category, parent_work_id=parent,
                                    reason='真实问题或确认事项', occurred_at=self.time(minute))

    def test_nested_and_overlapping_rework_is_not_added_as_net_time(self):
        events = [self.work('review-1', 'review_revision', 'work_started', 0),
                  self.work('proof-1', 'confirmation', 'work_started', 1),
                  self.work('tool-1', 'tool_repair', 'work_started', 2, 'review-1'),
                  self.work('tool-1', 'tool_repair', 'work_finished', 3, 'review-1'),
                  self.work('review-1', 'review_revision', 'work_finished', 4),
                  self.work('proof-1', 'confirmation', 'work_finished', 5)]
        work = metrics.summarize(events)['tasks'][0]['work']
        self.assertEqual(work['seconds_sum'], 540)
        self.assertEqual(work['interval_union_seconds'], 300)
        self.assertEqual(work['interval_overlap_seconds'], 240)
        self.assertEqual(work['by_category']['tool_repair']['seconds_sum'], 60)
        self.assertEqual(next(r for r in work['windows'] if r['work_id'] == 'tool-1')['parent_work_id'], 'review-1')

    def test_unknown_work_endpoints_and_failed_checks_do_not_invent_rework(self):
        events = [self.work('unknown-start', 'author_revision', 'work_finished', 2),
                  self.work('unknown-end', 'confirmation', 'work_started', 3),
                  metrics.record_event(self.project, 'design', 'work', 'needs_review',
                                       input_digest='old', occurred_at=self.time(4))]
        work = metrics.summarize(events)['tasks'][0]['work']
        self.assertEqual(len(work['windows']), 2)
        self.assertTrue(all(row['elapsed_seconds'] is None for row in work['windows']))
        self.assertEqual(work['interval_count'], 0)

    def test_work_id_cannot_silently_join_two_repair_rounds(self):
        self.work('repair', 'author_revision', 'work_started', 0)
        self.work('repair', 'author_revision', 'work_finished', 1)
        path = self.project / 'pipeline-events.jsonl'
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '新一轮'):
            self.work('repair', 'author_revision', 'work_started', 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(metrics.summarize_project(self.project)['tasks'][0]['work']['interval_count'], 1)

    def test_category_conflict_and_time_reversal_are_rejected_before_append(self):
        self.work('repair', 'author_revision', 'work_started', 2)
        path = self.project / 'pipeline-events.jsonl'
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '类别'):
            self.work('repair', 'confirmation', 'work_finished', 3)
        with self.assertRaisesRegex(ValueError, '倒序'):
            self.work('repair', 'author_revision', 'work_finished', 1)
        self.assertEqual(path.read_bytes(), before)

    def test_unlinked_legacy_attempts_stay_in_manual_metrics(self):
        for status, minute in [('started', 0), ('finished', 2)]:
            metrics.record_event(self.project, 'design', 'old', status,
                                 attempt_id='legacy-attempt', occurred_at=self.time(minute))
        result = metrics.summarize_project(self.project)
        self.assertEqual(result['tasks'][0]['attempt_id'], 'legacy-attempt')
        self.assertEqual(result['tasks'][0]['elapsed_seconds'], 120)
        self.assertEqual(result['unmatched_handoff_event_ids'], [])

    def test_same_draft_under_changed_inputs_has_independent_first_check(self):
        events = [metrics.record_event(self.project, 'assets', 'new-input', status,
                                       input_digest=source, revision_digest='same-draft', check_kind='semantic',
                                       occurred_at=self.time(index))
                  for index, (source, status) in enumerate([('v1', 'needs_review'), ('v2', 'checked')])]
        result = metrics.summarize(events)
        self.assertEqual(result['first_checks_by_kind']['semantic'], {'passed': 1, 'total': 2})

    def test_read_does_not_backfill_or_rewrite_old_event_schema(self):
        old = {'event_id': 'old', 'stage': 'design', 'task_key': 'old', 'status': 'checked',
               'object_ids': [], 'input_digest': 'input-v1', 'reason': None, 'occurred_at': self.time(0)}
        path = self.project / 'pipeline-events.jsonl'
        path.write_text(json.dumps(old) + '\n', encoding='utf-8')
        before = path.read_bytes()
        result = metrics.summarize_project(self.project)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result['first_checks_by_kind'], {'unspecified': {'passed': 1, 'total': 1}})
        self.assertEqual(result['tasks'][0]['first_checks'], {'input-v1': 'checked'})
        self.assertEqual(result['tasks'][0]['work']['windows'], [])


if __name__ == '__main__':
    unittest.main()
