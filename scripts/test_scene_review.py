"""Fictional evidence fixtures for conservative review reuse, never production approvals."""
import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import review_gate
import scene_review as sr


def fixture():
    records, units = [], []
    for number in range(1, 7):
        scene = 'SC1' if number <= 5 else 'SC2'
        record = {'id': 'L' + str(number), 'scene': scene, 'kind': 'action',
                  'text': '小车停稳', 'start': (number - 1) * 4, 'end': number * 4}
        records.append(record)
        initial = {'car': {'present': True, 'asset_id': 'PROP1', 'facts': {'position': 'curb'}}}
        boundary = {'kind': 'continuous'}
        if number in (1, 6):
            boundary = {'kind': 'reset', 'reason': '原文转入独立场景',
                        'evidence': [{'scene': scene, 'quote': '新场景'}]}
        shots = []
        for shot_number in range(2):
            shots.append({'scene_id': scene, 'camera': '路缘固定机位',
                          'composition': '车头与停止线同框' + str(shot_number),
                          'subjects': ['PROP1'], 'dialogue_parts': [],
                          'continuity': {'changes': [], 'visibility': {}}})
        units.append({'id': 'u' + str(number), 'span': [record['start'], record['end']],
                      'scene_ids': [scene], 'character_ids': [], 'global': '车在路缘停稳',
                      'audio': [{'record': record['id'], 'units': 4, 'pause': 0.2, 'voice': '说'}],
                      'voice_policy': {'delivery': '轻声'}, 'shots': shots,
                      'continuity': {'version': 1, 'boundary': boundary,
                                     'entities': {'car': {'asset_ids': ['PROP1']}},
                                     'initial': copy.deepcopy(initial), 'final': copy.deepcopy(initial)}})
    state = {'records': records, 'style_hash': 'style-a', 'entry': {'min': 4}, 'units': [],
             'files': {'source': {'path': 'fiction.txt', 'sha256': 'source-a'}},
             'ledger': {'schema_version': 3,
                        'scenes': [{'id': sid, 'heading': '新场景', 'prop_ids': ['PROP1']} for sid in ('SC1', 'SC2')],
                        'characters': [], 'props': [{'id': 'PROP1', 'design': '红色小车'},
                                                   {'id': 'PROP2', 'design': '未使用的茶杯'}]}}
    return state, {'request_id': 'fiction-only', 'units': units}


def reviewed(state, request, warnings=None):
    report = review_gate.pending(state, request, warnings or [])
    report.update(status='passed', reviewer='fictional fixture reviewer')
    for check in report['checks'].values():
        check.update(status='passed', basis='核对虚构场景中的停止线与车头接续依据。')
    for target in report['shot_reviews']:
        target.update(status='passed', basis='车头和停止线同框，镜内路缘位置支持停车状态。')
    for warning in report['warning_decisions']:
        warning.update(decision='accepted', basis='核对虚构停车场景和预留停顿。')
    return report


class SceneReviewTests(unittest.TestCase):
    def setUp(self):
        self.rules = patch.object(sr, 'rules_digest', return_value='rules-fixture')
        self.rules_mock = self.rules.start()
        self.addCleanup(self.rules.stop)
        self.state, self.request = fixture()
        self.snapshot = sr.freeze(self.state, self.request)
        self.report = reviewed(self.state, self.request)

    def prepare(self, request=None, state=None, report=None, snapshot=None, warnings=None):
        return sr.prepare_incremental(snapshot or self.snapshot, report or self.report,
                                      state or self.state, request or self.request, warnings)

    def statuses(self, report):
        return {item['key']: item['status'] for item in report['shot_reviews']}

    def test_identical_inputs_reuse_actual_reviews_but_never_global_approval(self):
        report, proof = self.prepare()
        self.assertEqual(proof['stats']['reused_shots'], 12)
        self.assertEqual(report['status'], 'pending')
        self.assertEqual(report['reviewer'], '')
        self.assertTrue(all(c['status'] == 'pending' for c in report['checks'].values()))
        self.assertEqual(set(report), set(review_gate.pending(self.state, self.request, [])))
        with self.assertRaisesRegex(ValueError, '最终稿复核'):
            review_gate.verify(self.state, self.request, report, [])
        self.assertEqual(proof['shot_provenance'][0]['previous_reviewer'], self.report['reviewer'])

    def test_speech_only_and_voice_policy_changes_invalidate_whole_unit(self):
        for field in ('audio', 'voice_policy'):
            request = copy.deepcopy(self.request)
            if field == 'audio':
                request['units'][2]['audio'][0]['pause'] = 0.5
            else:
                request['units'][2]['voice_policy']['delivery'] = '急声'
            with self.subTest(field=field):
                report, _ = self.prepare(request=request)
                states = self.statuses(report)
                self.assertEqual(states['3:1'], 'pending')
                self.assertEqual(states['3:2'], 'pending')
                self.assertEqual(states['6:1'], 'passed')

    def test_composition_rechecks_actual_connections_not_unrelated_scene(self):
        request = copy.deepcopy(self.request)
        request['units'][2]['shots'][0]['composition'] = '停止线被裁出画面'
        report, proof = self.prepare(request=request)
        states = self.statuses(report)
        for key in ('2:1', '3:1', '3:2', '4:1'):
            self.assertEqual(states[key], 'pending')
        self.assertEqual(states['1:1'], 'passed')
        self.assertEqual(states['5:1'], 'passed')
        self.assertEqual(states['6:1'], 'passed')
        self.assertIn('dependency_changed:previous', proof['stats']['reasons'])

    def test_plain_name_change_is_not_semantically_normalized(self):
        request = copy.deepcopy(self.request)
        request['units'][0]['shots'][0]['composition'] += '，小车改称轿车'
        report, _ = self.prepare(request=request)
        self.assertEqual(self.statuses(report)['1:1'], 'pending')

    def test_continuity_change_propagates_beyond_two_neighbours_to_reviewed_reset(self):
        request = copy.deepcopy(self.request)
        request['units'][0]['continuity']['final']['car']['facts']['position'] = 'road'
        report, proof = self.prepare(request=request)
        states = self.statuses(report)
        for index in range(1, 6):
            self.assertEqual(states[f'{index}:1'], 'pending')
        self.assertEqual(states['6:1'], 'passed')
        self.assertGreaterEqual(proof['stats']['reasons']['continuity_change_propagation'], 8)

    def test_matching_inheritance_and_restored_final_stops_propagation(self):
        request = copy.deepcopy(self.request)
        request['units'][0]['continuity']['final']['car']['facts']['position'] = 'road'
        request['units'][1]['continuity']['initial']['car']['facts']['position'] = 'road'
        # Unit two explicitly returns to the original final state. Full
        # validation/review must still verify the authored action evidence.
        report, proof = self.prepare(request=request)
        states = self.statuses(report)
        self.assertEqual(states['2:1'], 'pending')
        self.assertEqual(states['3:1'], 'pending')  # changed previous connection
        self.assertEqual(states['5:1'], 'passed')
        self.assertEqual(states['6:1'], 'passed')

    def test_unreviewed_reset_does_not_stop_propagation(self):
        old_report = copy.deepcopy(self.report)
        old_report['checks']['continuity']['status'] = 'pending'
        request = copy.deepcopy(self.request)
        request['units'][0]['continuity']['final']['car']['facts']['position'] = 'road'
        report, _ = self.prepare(request=request, report=old_report)
        self.assertEqual(self.statuses(report)['6:1'], 'pending')

    def test_pending_reset_warning_does_not_stop_propagation(self):
        warnings = [{'kind': '连续性重置需人工核实', 'unit': 'u6'}]
        snapshot = sr.freeze(self.state, self.request, warnings)
        previous = reviewed(self.state, self.request, warnings)
        previous['warning_decisions'][0].update(decision='pending', basis='')
        request = copy.deepcopy(self.request)
        request['units'][0]['continuity']['final']['car']['facts']['position'] = 'road'
        report, _ = self.prepare(request=request, report=previous, snapshot=snapshot, warnings=warnings)
        self.assertEqual(self.statuses(report)['6:1'], 'pending')

    def test_unknown_dependency_expands_connected_component(self):
        state, request = copy.deepcopy(self.state), copy.deepcopy(self.request)
        del request['units'][2]['continuity']['version']
        old = sr.freeze(state, request)
        previous = reviewed(state, request)
        request['units'][0]['shots'][0]['composition'] = '新的构图'
        report, proof = self.prepare(state=state, request=request, snapshot=old, report=previous)
        self.assertEqual(self.statuses(report)['5:1'], 'pending')
        self.assertEqual(self.statuses(report)['6:1'], 'passed')
        self.assertIn('unknown_dependency_connected_component', proof['stats']['reasons'])

    def test_source_style_rules_and_relevant_design_changes_invalidate(self):
        for change in ('source', 'style', 'design', 'rules'):
            state = copy.deepcopy(self.state)
            if change == 'source':
                state['files']['source']['sha256'] = 'source-b'
            elif change == 'style':
                state['style_hash'] = 'style-b'
            elif change == 'design':
                state['ledger']['props'][0]['design'] = '蓝色小车'
            else:
                self.rules_mock.return_value = 'rules-changed'
            with self.subTest(change=change):
                report, proof = self.prepare(state=state)
                self.assertEqual(proof['stats']['reused_shots'], 0)
                self.assertTrue(all(s == 'pending' for s in self.statuses(report).values()))

    def test_unrelated_asset_edit_keeps_actual_reviews(self):
        state = copy.deepcopy(self.state)
        state['ledger']['props'][1]['design'] = '未使用的白色茶杯'
        _, proof = self.prepare(state=state)
        self.assertEqual(proof['stats']['reused_shots'], 12)

    def test_linked_nonstandard_design_id_is_a_dependency(self):
        state = copy.deepcopy(self.state)
        state['ledger']['props'][0]['design_group'] = 'group-red-car'
        state['ledger']['design_groups'] = [{'id': 'group-red-car', 'surface': 'red paint'}]
        snapshot = sr.freeze(state, self.request)
        previous = reviewed(state, self.request)
        state['ledger']['design_groups'][0]['surface'] = 'blue paint'
        _, proof = self.prepare(state=state, snapshot=snapshot, report=previous)
        self.assertEqual(proof['stats']['reused_shots'], 0)

    def test_insert_delete_reorder_never_reassigns_positional_approval(self):
        for change in ('insert', 'delete', 'reorder', 'insert_shot'):
            request = copy.deepcopy(self.request)
            if change == 'insert':
                request['units'].insert(2, copy.deepcopy(request['units'][2]))
            elif change == 'delete':
                del request['units'][2]
            elif change == 'reorder':
                request['units'][2], request['units'][3] = request['units'][3], request['units'][2]
            else:
                request['units'][2]['shots'].insert(0, copy.deepcopy(request['units'][2]['shots'][0]))
            with self.subTest(change=change):
                report, proof = self.prepare(request=request)
                self.assertEqual(len(report['shot_reviews']), sum(len(u['shots']) for u in request['units']))
                self.assertEqual(self.statuses(report)['3:1'], 'pending')
                self.assertIn('positional_structure_changed', proof['stats']['reasons'])

    def test_external_connection_change_invalidates_range_edge(self):
        state = copy.deepcopy(self.state)
        predecessor = copy.deepcopy(self.request['units'][0])
        predecessor.update(id='old', span=[-4, 0])
        state['units'] = [predecessor]
        snapshot = sr.freeze(state, self.request)
        report = reviewed(state, self.request)
        state['units'][0]['shots'][0]['composition'] = '新前镜'
        current, proof = self.prepare(state=state, snapshot=snapshot, report=report)
        self.assertEqual(self.statuses(current)['1:1'], 'pending')
        self.assertEqual(self.statuses(current)['6:1'], 'passed')

    def test_partial_old_report_reuses_only_concrete_actual_passed_records(self):
        previous = copy.deepcopy(self.report)
        previous['status'] = 'pending'
        previous['shot_reviews'][0]['status'] = 'pending'
        previous['shot_reviews'][1]['basis'] = '通过'
        previous['shot_reviews'][2]['basis'] = ''
        report, proof = self.prepare(report=previous)
        self.assertEqual(proof['stats']['pending_shots'], 3)
        self.assertEqual(self.statuses(report)['3:1'], 'passed')

    def test_missing_or_mismatched_old_snapshot_evidence_is_rejected(self):
        for change in ('missing', 'duplicate', 'edited', 'binding'):
            report = copy.deepcopy(self.report)
            if change == 'missing':
                report['shot_reviews'].pop()
            elif change == 'duplicate':
                report['shot_reviews'][1] = copy.deepcopy(report['shot_reviews'][0])
            elif change == 'edited':
                report['shot_reviews'][0]['shot']['composition'] = '自称审过另一构图'
            else:
                report['request_sha256'] = 'wrong'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(report=report)

    def test_warning_requires_exact_object_hash_and_full_dependencies(self):
        warnings = [{'kind': '语速提示', 'unit': 'u3', 'message': '停顿较长'}]
        snapshot = sr.freeze(self.state, self.request, warnings)
        previous = reviewed(self.state, self.request, warnings)
        report, proof = self.prepare(snapshot=snapshot, report=previous, warnings=warnings)
        self.assertEqual(proof['stats']['reused_warnings'], 1)
        request = copy.deepcopy(self.request)
        request['units'][2]['audio'][0]['pause'] = 0.7
        report, proof = self.prepare(request=request, snapshot=snapshot, report=previous, warnings=warnings)
        self.assertEqual(proof['stats']['pending_warnings'], 1)
        self.assertEqual(report['warning_decisions'][0]['decision'], 'pending')
        previous['warning_decisions'][0]['warning']['message'] = '篡改对象'
        with self.assertRaisesRegex(ValueError, '警告对象'):
            self.prepare(snapshot=snapshot, report=previous, warnings=warnings)

    def test_immutable_freeze_and_exclusive_output(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['request']['units'][0]['global'] = '改动'
        with self.assertRaisesRegex(ValueError, '快照已改变'):
            sr.verify_snapshot(snapshot)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            sr._write_new(path, self.snapshot)
            with self.assertRaises(FileExistsError):
                sr._write_new(path, self.snapshot)

    def issue(self, target='1:1', change='说明车头与停止线的位置'):
        frozen_target = next(item for item in self.snapshot['targets'] if item['key'] == target)
        return {'root_cause': '停车空间关系不清', 'target': target, 'field': 'shot.composition',
                'basis': '原文小车已经停稳，应可核对车头相对停止线的位置。',
                'expected_sha256': sr.digest(frozen_target['shot']['composition']),
                'requested_change': change, 'closure_condition': '构图与动作、景深依据一致。'}

    def test_root_cause_merge_preserves_all_hits_and_never_closes(self):
        issues = [[self.issue(), self.issue('1:2')], [self.issue()]]
        merged = sr.merge_issues(self.snapshot, issues)
        self.assertEqual(len(merged['groups']), 1)
        self.assertEqual(merged['finding_count'], 3)
        self.assertEqual(merged['conflicts'], [])
        plan = sr.revise_plan(self.snapshot, merged)
        self.assertEqual(plan['status'], 'pending_revision')
        self.assertTrue(all(c['status'] == 'pending' and c['closure_basis'] == '' for c in plan['changes']))
        self.assertEqual(plan['changes'][0]['expected_sha256'], sr.digest('车头与停止线同框0'))

    def test_conflicts_block_revision_instead_of_last_write_wins(self):
        merged = sr.merge_issues(self.snapshot, [[self.issue()], [self.issue(change='去掉停止线')]])
        self.assertEqual(len(merged['conflicts']), 1)
        self.assertEqual(merged['finding_count'], 2)
        self.assertEqual(sr.revise_plan(self.snapshot, merged)['status'], 'blocked_conflicts')

    def test_stale_issue_field_hash_and_unknown_field_rejected(self):
        for change in ('hash', 'field'):
            issue = self.issue()
            issue['expected_sha256' if change == 'hash' else 'field'] = 'wrong'
            with self.subTest(change=change), self.assertRaises(ValueError):
                sr.merge_issues(self.snapshot, [[issue]])

    def test_issue_requires_snapshot_bundle_or_field_hash(self):
        issue = self.issue()
        del issue['expected_sha256']
        for bundle in ([issue], {'issues': [issue]}):
            with self.subTest(bundle=type(bundle).__name__), self.assertRaisesRegex(ValueError, '反馈未绑定'):
                sr.merge_issues(self.snapshot, [bundle])
        merged = sr.merge_issues(self.snapshot, [{'snapshot_sha256': self.snapshot['snapshot_sha256'],
                                                 'issues': [issue]}])
        self.assertEqual(sr.revise_plan(self.snapshot, merged)['status'], 'pending_revision')
        self.assertEqual(merged['groups'][0]['hits'][0]['feedback_binding']['mode'], 'snapshot')

    def test_stale_bundle_cannot_create_revision_plan_even_with_matching_field(self):
        bundle = {'snapshot_sha256': 'a-different-frozen-scene', 'issues': [self.issue()]}
        with self.assertRaisesRegex(ValueError, '不同冻结快照'):
            sr.merge_issues(self.snapshot, [bundle])
        merged = sr.merge_issues(self.snapshot, [[self.issue()]])
        merged['snapshot_sha256'] = 'a-different-frozen-scene'
        with self.assertRaisesRegex(ValueError, '问题单与冻结版本不匹配'):
            sr.revise_plan(self.snapshot, merged)

    def test_tampered_field_hash_in_merged_plan_still_rejected(self):
        merged = sr.merge_issues(self.snapshot, [[self.issue()]])
        merged['groups'][0]['hits'][0]['expected_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, '原字段已改变'):
            sr.revise_plan(self.snapshot, merged)

    def test_partial_scope_is_explicit_and_not_automatically_validated(self):
        request = copy.deepcopy(self.request)
        request['span'] = [0, 24]
        request['units'] = request['units'][:2]
        snapshot = sr.freeze(self.state, request, partial=True)
        self.assertEqual(snapshot['validation']['status'], 'not_run')
        self.assertTrue(snapshot['validation']['partial'])
        self.assertEqual(snapshot['validation']['request_span'], [0, 24])
        self.assertEqual(snapshot['validation']['covered_span'], [0, 8])
        self.assertEqual(snapshot['validation']['unit_spans'], [[0, 4], [4, 8]])
        self.assertTrue(snapshot['validation']['final_full_validation_required'])
        sr.verify_snapshot(snapshot)
        snapshot['validation']['covered_span'] = [0, 24]
        snapshot['snapshot_sha256'] = sr.digest(sr._snapshot_payload(snapshot))
        with self.assertRaisesRegex(ValueError, '验证范围与请求不一致'):
            sr.verify_snapshot(snapshot)

    def test_cli_freeze_and_prepare_partial_call_validator_and_workflow_gate(self):
        import director as d
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {'state.json': self.state, 'request.json': self.request,
                      'snapshot.json': self.snapshot, 'report.json': self.report}
            for name, value in inputs.items():
                (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            for command in ('freeze', 'prepare-incremental'):
                output = root / (command + '.json')
                args = [command, '--state', str(root / 'state.json'), '--request', str(root / 'request.json'),
                        '--out', str(output), '--partial', '--require-workflow']
                if command == 'prepare-incremental':
                    args += ['--snapshot', str(root / 'snapshot.json'), '--old-report', str(root / 'report.json'),
                             '--provenance', str(root / 'proof.json')]
                with self.subTest(command=command), patch.object(d, 'validate', return_value=[]) as validate, \
                        patch.object(d, 'require_workflow') as workflow, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(sr.main(args), 0)
                    validate.assert_called_once_with(self.state, self.request, partial=True)
                    workflow.assert_called_once_with(self.request)
                    document = json.loads(output.read_text(encoding='utf-8'))
                    if command == 'freeze':
                        evidence = document['validation']
                    else:
                        self.assertEqual(document['status'], 'pending')
                        evidence = json.loads((root / 'proof.json').read_text(encoding='utf-8'))['validation']
                    self.assertEqual(evidence['status'], 'passed')
                    self.assertTrue(evidence['partial'])
                    self.assertTrue(evidence['require_workflow'])
                    self.assertTrue(evidence['final_full_validation_required'])


class ReviewCueCompatibilityTests(unittest.TestCase):
    """Historical cue payloads remain verifiable, never upgraded approvals."""

    LEGACY_FIXED = '固定镜头：核对原有动作或保持的用途、信息读清所需时长和切点；不要求补运镜。'
    LEGACY_MOVING = '运动镜头：核对起止取景、途中可见性、遮挡和落点；不只检查起始构图。'

    def historical(self, workflow=None):
        state, request = fixture()
        for unit in request['units']:
            if workflow:
                unit['workflow_policy'] = workflow
            unit['shots'][0].update(start=0, end=2)
            unit['shots'][1].update(start=2, end=4, camera='从车头横移至停止线')
        snapshot = sr.freeze(state, request)
        # These exact strings were emitted by local.1. Do not use the current
        # cue producer to manufacture the historical expected payload.
        snapshot.pop('cue_policy')
        snapshot['rules_sha256'] = 'historical-local.1-rules'
        for target in snapshot['targets']:
            target['review_cues'] = [self.LEGACY_FIXED if target['shot_index'] == 1
                                     else self.LEGACY_MOVING]
        snapshot['snapshot_sha256'] = sr.digest(sr._snapshot_payload(snapshot))
        report = reviewed(state, request)
        report['policy'] = review_gate.LEGACY_POLICY
        report.pop('rules_sha256')
        report['shot_reviews'] = copy.deepcopy(snapshot['targets'])
        for target in report['shot_reviews']:
            target.update(status='passed', basis='车头与停止线的实际位置支持固定观察和随后横移。')
        return state, request, snapshot, report

    def test_unmarked_legacy_fixed_and_moving_snapshots_are_readable(self):
        for workflow in (None, 'scene-review-b-v2'):
            state, request, snapshot, _ = self.historical(workflow)
            before = copy.deepcopy(snapshot)
            with self.subTest(workflow=workflow):
                self.assertEqual(sr.verify_snapshot(snapshot), before)
                self.assertEqual(snapshot, before)
                current = sr.freeze(state, request)
                self.assertEqual(current['cue_policy'], sr.shot_review.CUE_POLICY)
                if workflow:
                    self.assertNotEqual(current['targets'][0]['review_cues'],
                                        snapshot['targets'][0]['review_cues'])
                else:
                    self.assertEqual(current['targets'], snapshot['targets'])

    def test_legacy_cue_or_shot_tampering_still_fails(self):
        for field in ('review_cues', 'shot'):
            _, _, snapshot, _ = self.historical('scene-review-b-v2')
            if field == 'review_cues':
                snapshot['targets'][0]['review_cues'] = []
            else:
                snapshot['targets'][0]['shot']['camera'] = '自动补入推进'
            # Even recomputing the outer checksum cannot hide a target that
            # disagrees with the exact historical producer and bound request.
            snapshot['snapshot_sha256'] = sr.digest(sr._snapshot_payload(snapshot))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '快照镜头不一致'):
                sr.verify_snapshot(snapshot)

    def test_unknown_cue_policy_is_rejected(self):
        _, _, snapshot, _ = self.historical()
        snapshot['cue_policy'] = 'unknown-cues'
        snapshot['snapshot_sha256'] = sr.digest(sr._snapshot_payload(snapshot))
        with self.assertRaisesRegex(ValueError, '提示策略不兼容'):
            sr.verify_snapshot(snapshot)

    def test_changed_rules_leave_all_legacy_judgments_pending(self):
        for workflow in (None, 'scene-review-b-v2'):
            state, request, snapshot, previous = self.historical(workflow)
            before = copy.deepcopy((snapshot, previous))
            report, provenance = sr.prepare_incremental(snapshot, previous, state, request)
            with self.subTest(workflow=workflow):
                self.assertEqual(provenance['stats']['reused_shots'], 0)
                self.assertTrue(all(row['status'] == 'pending' for row in report['shot_reviews']))
                self.assertEqual(report['status'], 'pending')
                self.assertTrue(all(row['status'] == 'pending' for row in report['checks'].values()))
                self.assertEqual((snapshot, previous), before)
                with self.assertRaises(ValueError):
                    review_gate.verify(state, request, report, [])
                if workflow:
                    with self.assertRaisesRegex(ValueError, '旧版.*重新准备'):
                        review_gate.verify(state, request, previous, [])


if __name__ == '__main__':
    unittest.main()
