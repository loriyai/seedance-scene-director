"""Operation-local validation reuse; source, ownership and close gates stay live."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_handoff as handoff
import test_design_workspace as existing

workspace = existing.workspace
design = existing.design
save = existing.save


class WorkspaceValidationReuseTests(unittest.TestCase):
    shared = existing.DesignWorkspaceTests.shared
    freeze = existing.DesignWorkspaceTests.freeze
    simple = existing.DesignWorkspaceTests.simple
    submission = existing.DesignWorkspaceTests.submission

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.script, self.source, self.paragraphs, self.data, self.payload = existing.fixture(self.work)
        self.style = (self.work / 'video-style.txt').read_text(encoding='utf-8').strip()

    def ready(self):
        baseline, plan = self.simple()
        submissions = [self.submission(baseline, plan, gid) for gid in ('prop', 'set')]
        return baseline, plan, submissions

    def test_every_public_operation_validates_once_without_cross_call_cache(self):
        baseline, plan, submissions = self.ready()
        groups = [{'group_id': row['group_id'], 'owned_asset_ids': row['owned_asset_ids']}
                  for row in plan['groups']]
        operations = [lambda: workspace.plan(baseline, groups),
                      lambda: workspace.validate_plan(baseline, plan),
                      lambda: workspace.group_spec(baseline, plan, 'prop'),
                      lambda: workspace.verify_group(baseline, plan, 'prop', submissions[0]),
                      lambda: workspace.merge(baseline, plan, submissions)]
        with patch.object(workspace, 'validate_baseline', wraps=workspace.validate_baseline) as check:
            for operation in operations:
                check.reset_mock()
                operation()
                self.assertEqual(check.call_count, 1)
        # A previous successful check never authorizes a later changed input.
        self.source.write_text(self.source.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        for operation in operations:
            with self.assertRaisesRegex(ValueError, '冻结输入已变化'):
                operation()

    def test_cli_does_not_repeat_full_baseline_validation(self):
        baseline, plan, submissions = self.ready()
        for name, value in [('baseline', baseline), ('plan', plan), ('submission', submissions[0])]:
            save(self.work / (name + '.json'), value)
        with patch.object(workspace, 'validate_baseline', wraps=workspace.validate_baseline) as check:
            with contextlib.redirect_stdout(io.StringIO()):
                code = workspace.main(['verify-group', str(self.work / 'baseline.json'),
                                       str(self.work / 'plan.json'), str(self.work / 'submission.json'),
                                       '--group-id', 'prop'])
        self.assertEqual(code, 0)
        self.assertEqual(check.call_count, 1)

    def test_resealed_plan_or_baseline_drift_and_ownership_are_rejected(self):
        baseline, plan, submissions = self.ready()
        bad = copy.deepcopy(plan)
        bad['groups'][0]['read_only_dependency_sha256'] = {'anchor:fake': 'fake'}
        bad = workspace._seal({k: v for k, v in bad.items() if k != 'plan_sha256'}, 'plan_sha256')
        with self.assertRaisesRegex(ValueError, '分组依赖或计划字段'):
            workspace.merge(baseline, bad, submissions)
        bad_baseline = copy.deepcopy(baseline)
        bad_baseline['style'] = '另一风格'
        bad_baseline = workspace._seal({k: v for k, v in bad_baseline.items() if k != 'baseline_sha256'},
                                       'baseline_sha256')
        with self.assertRaisesRegex(ValueError, '基线风格'):
            workspace.verify_group(bad_baseline, plan, 'prop', submissions[0])
        wrong = copy.deepcopy(submissions)
        wrong[0]['designs'].append(copy.deepcopy(wrong[1]['designs'][0]))
        with self.assertRaisesRegex(ValueError, '精确匹配owned_asset_ids'):
            workspace.merge(baseline, plan, wrong)
        with self.assertRaisesRegex(ValueError, '重复'):
            workspace.merge(baseline, plan, [submissions[0], submissions[0]])

    def test_plan_checks_input_drift_after_dependency_work(self):
        baseline, plan, _ = self.ready()
        original = workspace._group_dependencies
        def drift(*args):
            result = original(*args)
            (self.work / 'video-style.txt').write_text('已漂移', encoding='utf-8')
            return result
        with patch.object(workspace, '_group_dependencies', side_effect=drift):
            with self.assertRaisesRegex(ValueError, '冻结输入已变化'):
                workspace.validate_plan(baseline, plan)

    def test_group_and_merge_keep_final_source_drift_checks(self):
        for merging in (False, True):
            baseline, plan, submissions = self.ready()
            original = workspace._compile
            def drift(*args, **kwargs):
                result = original(*args, **kwargs)
                if 'PROP0001' in args[1]:
                    self.source.write_text(self.source.read_text(encoding='utf-8') + '\n', encoding='utf-8')
                return result
            with self.subTest(merge=merging), patch.object(workspace, '_compile', side_effect=drift):
                with self.assertRaisesRegex(ValueError, '冻结输入已变化'):
                    if merging:
                        workspace.merge(baseline, plan, submissions)
                    else:
                        workspace.verify_group(baseline, plan, 'prop', submissions[0])

    def test_merge_compiled_result_proof_cannot_hide_postcompile_changes(self):
        baseline, plan, submissions = self.ready()
        original = design._apply_design_compiled
        for field in ('image_prompt', 'name'):
            def drift(*args, **kwargs):
                complete, proof = original(*args, **kwargs)
                complete['characters'][0][field] = '编译后替换'
                return complete, proof
            with self.subTest(field=field), patch.object(design, '_apply_design_compiled', side_effect=drift):
                with self.assertRaises(ValueError):
                    workspace.merge(baseline, plan, submissions)

    def test_merge_rejects_inmemory_baseline_or_plan_drift_between_groups(self):
        for target in ('baseline', 'plan'):
            for reseal in (False, True):
                baseline, plan, submissions = self.ready()
                original = workspace._verify_group
                def drift(*args, **kwargs):
                    result = original(*args, **kwargs)
                    # Mutate after the final group compiled, before merge closes.
                    if args[2] == 'set':
                        value = baseline if target == 'baseline' else plan
                        value['changed_during_operation'] = True
                        if reseal:
                            field = 'baseline_sha256' if target == 'baseline' else 'plan_sha256'
                            value[field] = design.fingerprint({k: v for k, v in value.items() if k != field})
                    return result
                with self.subTest(target=target, reseal=reseal), patch.object(workspace, '_verify_group', side_effect=drift):
                    with self.assertRaisesRegex(ValueError, 'sha256|操作期间'):
                        workspace.merge(baseline, plan, submissions)

    def test_verify_group_rejects_submission_mutation_during_compilation(self):
        baseline, plan, submissions = self.ready()
        submission = submissions[0]
        original = workspace._compile
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            if 'PROP0001' in args[1]:
                submission['designs'][0]['basis'] = '编译后并发修改'
            return result
        with patch.object(workspace, '_compile', side_effect=drift):
            with self.assertRaisesRegex(ValueError, '分组提交已变化'):
                workspace.verify_group(baseline, plan, 'prop', submission)

    def test_merge_rejects_mutating_prior_submission_or_submission_list(self):
        for change_list in (False, True):
            baseline, plan, submissions = self.ready()
            original = workspace._verify_group
            def drift(*args, **kwargs):
                report = original(*args, **kwargs)
                if args[2] == 'set':
                    if change_list:
                        submissions.reverse()
                    else:
                        submissions[0]['designs'][0]['basis'] = '另一组处理期间改旧提交'
                return report
            with self.subTest(list_change=change_list), patch.object(workspace, '_verify_group', side_effect=drift):
                with self.assertRaisesRegex(ValueError, '分组提交已变化'):
                    workspace.merge(baseline, plan, submissions)

    def test_submission_snapshot_already_bound_during_baseline_validation(self):
        for merging in (False, True):
            baseline, plan, submissions = self.ready()
            original = workspace.validate_baseline
            def drift(*args, **kwargs):
                result = original(*args, **kwargs)
                submissions[0]['designs'][0]['basis'] = '基础校验期间并发改提交'
                return result
            with self.subTest(merge=merging), patch.object(workspace, 'validate_baseline', side_effect=drift):
                with self.assertRaisesRegex(ValueError, '分组提交已变化'):
                    if merging:
                        workspace.merge(baseline, plan, submissions)
                    else:
                        workspace.verify_group(baseline, plan, 'prop', submissions[0])

    def prepare_group(self, ready=None):
        baseline, plan, submissions = ready if ready is not None else self.ready()
        save(self.work / 'baseline.json', baseline)
        save(self.work / 'plan.json', plan)
        brief = {'objective': '已有道具设计候选', 'authorization': '仅本地测试',
                 'scope': {'task_key': 'test-group-prop'}, 'inputs': [str(self.script)],
                 'expected_outputs': ['result.json', 'submission.json'],
                 'design_group': {'baseline_path': str(self.work / 'baseline.json'),
                                  'plan_path': str(self.work / 'plan.json'),
                                  'group_id': 'prop', 'submission': 'submission.json'}}
        save(self.work / 'brief.json', brief)
        result = handoff.prepare(str(self.work), 'design', str(self.work / 'brief.json'))
        path = Path(result['handoff'])
        save(path.parent / 'output/submission.json', submissions[0])
        save(path.parent / 'output/result.json', {'status': 'draft_complete', 'scope': brief['scope'],
                                                'artifacts': brief['expected_outputs']})
        return path

    def test_prepare_verify_and_close_each_do_one_independent_full_validation(self):
        ready = self.ready()
        with patch.object(workspace, 'validate_baseline', wraps=workspace.validate_baseline) as check:
            path = self.prepare_group(ready)
            self.assertEqual(check.call_count, 1)
            check.reset_mock()
            brief = handoff.read_json(self.work / 'brief.json')
            handoff.group_inputs(brief, 'design')
            self.assertEqual(check.call_count, 1)
            check.reset_mock()
            report = handoff.verify(str(path))
            self.assertEqual(check.call_count, 1)
            self.assertEqual(report['design_group_check']['semantic_review'], 'not_performed')
            check.reset_mock()
            closed = handoff.close(str(path), 'completed', '已核本地测试产物')
            self.assertEqual(check.call_count, 1)
            self.assertEqual(closed['artifact_sha256'], report['artifact_sha256'])

    def test_close_reverifies_mutated_submission_and_leaves_reservation_active(self):
        path = self.prepare_group()
        handoff.verify(str(path))
        output = path.parent / 'output/submission.json'
        bad = handoff.read_json(output)
        bad['owned_asset_ids'] = ['SET0001']
        save(output, bad)
        with self.assertRaisesRegex(ValueError, '拥有范围'):
            handoff.close(str(path), 'completed', '之前verify不授权当前坏稿')
        self.assertEqual(handoff.read_json(path.parent / 'handoff-state.json')['status'], 'active')

    def test_close_rejects_inflight_source_drift_before_terminal_write(self):
        path = self.prepare_group()
        original = workspace._compile
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            if 'PROP0001' in args[1]:
                self.source.write_text(self.source.read_text(encoding='utf-8') + '\n', encoding='utf-8')
            return result
        with patch.object(workspace, '_compile', side_effect=drift):
            with self.assertRaisesRegex(ValueError, '冻结输入已变化'):
                handoff.close(str(path), 'completed', '不能关闭漂移上下文')
        self.assertEqual(handoff.read_json(path.parent / 'handoff-state.json')['status'], 'active')


if __name__ == '__main__':
    unittest.main()
