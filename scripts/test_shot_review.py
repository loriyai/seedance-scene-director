"""Offline review gates for authored shots, not assertions of artistic quality."""
import copy
import unittest

import review_gate
import shot_review


class ShotReviewChecks(unittest.TestCase):
    def setUp(self):
        self.state = {'records': [{'id': 'L1', 'scene': 'SC1', 'kind': 'action',
                                  'text': '小车停住。男孩挥手。', 'start': 0, 'end': 8}]}
        self.unit = {'span': [0, 8], 'global': '小车靠近停止线，母子在旁边等候。',
                     'continuity': {'initial': {'car': {'facts': {'状态': '接近停止线'}}}},
                     'shots': [
                         {'camera': '路缘侧固定机位', 'label': '停稳',
                          'purpose': '以停止线为参照看清小车停稳，并交代与母子的相邻关系。',
                          'composition': '车头和停止线同框，母子在画面右侧。',
                          'depth_of_field': '车头、停止线与旁边母子均清楚。',
                          'continuity': {'changes': [{'entity': 'car', 'set': {'facts': {'状态': '停稳'}}}],
                                         'visibility': {'car': {'mode': 'visible', 'basis': '车头位于画面左侧'}}}},
                         {'camera': '从车头向相邻电动车小幅横移，停在男孩上身',
                          'label': '挥手', 'purpose': '沿已建立的相邻关系接向男孩挥手。',
                          'composition': '男孩在画内，车在左边画外。',
                          'depth_of_field': '男孩的手与面部清楚，后方路口稍虚。',
                          'continuity': {'changes': [], 'visibility': {
                              'car': {'mode': 'offscreen', 'basis': '横移后取景收在电动车后座，左侧小车不入画'}}}}]}
        self.request = {'units': [self.unit]}

    def approve(self):
        report = review_gate.pending(self.state, self.request, [])
        report.update(status='passed', reviewer='offline shot reviewer')
        for check in report['checks'].values():
            check.update(status='passed', basis='已核对虚构停车与挥手动作的来源和顺序。')
        for shot in report['shot_reviews']:
            shot.update(status='passed', basis='依据停止线和相邻电动车的位置，核对停车后的空间接续。')
        return report

    def test_prepare_never_approves_and_carries_source_and_entering_state(self):
        before = copy.deepcopy(self.request)
        report = review_gate.pending(self.state, self.request, [])
        self.assertEqual([s['status'] for s in report['shot_reviews']], ['pending', 'pending'])
        self.assertEqual(report['shot_reviews'][1]['entering_state']['car']['facts']['状态'], '停稳')
        self.assertEqual(report['shot_reviews'][0]['source'][0]['text'], '小车停住。男孩挥手。')
        self.assertEqual(self.request, before)

    def test_fixed_and_moving_shots_can_both_pass_without_rewriting(self):
        before = copy.deepcopy(self.request)
        self.assertEqual(shot_review.diagnostics(self.unit), [])
        result = review_gate.verify(self.state, self.request, self.approve(), [])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(self.request, before)

    def test_all_v2_shot_lengths_review_choice_even_without_camera_keywords(self):
        self.unit['workflow_policy'] = 'scene-review-b-v2'
        for duration in (1, 2, 3, 4, 5, 6):
            for camera in ('固定机位', '沿动作方向横移', '', '保持角色在画面中部'):
                shot = self.unit['shots'][0]
                shot.update(start=0, end=duration, camera=camera)
                before = copy.deepcopy(self.request)
                target = shot_review.targets(self.state, self.request)[0]
                with self.subTest(duration=duration, camera=camera):
                    self.assertTrue(any('1至6秒摄影选择' in cue for cue in target['review_cues']))
                    self.assertTrue(any('5至6秒不得全程固定' in cue for cue in target['review_cues']))
                    self.assertEqual(target['status'], 'pending')
                    self.assertEqual(target['basis'], '')
                    self.assertEqual(target['shot']['camera'], camera)
                    self.assertEqual(self.request, before)

    def test_legacy_cue_policy_preserves_historical_fixed_shot_snapshot(self):
        self.unit['workflow_policy'] = 'scene-review-b-v2'
        self.unit['review_dependencies'] = {'policy': 'shot-dependencies-v2', 'links': []}
        self.unit['shots'][0].update(start=0, end=6)
        current = shot_review.targets(self.state, self.request)[0]['review_cues']
        legacy = shot_review.targets(self.state, self.request,
                                     cue_policy=shot_review.LEGACY_CUE_POLICY)[0]['review_cues']
        self.assertTrue(any('1至6秒摄影选择' in cue for cue in current))
        self.assertTrue(any('5至6秒不得全程固定' in cue for cue in current))
        self.assertEqual(legacy, ['4至6秒镜头含固定描述：核对是否仅为实际运镜后的短暂稳定落点；整镜固定不合格，须有服务观看重点的相机位移或转向，移焦与主体动作不算。'])

    def test_global_pass_cannot_replace_missing_or_pending_shot_review(self):
        for defect in ('missing', 'pending', 'generic'):
            report = self.approve()
            if defect == 'missing':
                report['shot_reviews'].pop()
            elif defect == 'pending':
                report['shot_reviews'][0]['status'] = 'pending'
            else:
                report['shot_reviews'][0]['basis'] = '字段完整'
            with self.subTest(defect=defect), self.assertRaisesRegex(ValueError, '逐镜复核'):
                review_gate.verify(self.state, self.request, report, [])

    def test_same_count_duplicate_or_reordered_shot_reviews_are_rejected(self):
        for defect in ('duplicate', 'reordered'):
            report = self.approve()
            if defect == 'duplicate':
                report['shot_reviews'][1] = copy.deepcopy(report['shot_reviews'][0])
            else:
                report['shot_reviews'].reverse()
            with self.subTest(defect=defect), self.assertRaisesRegex(ValueError, '快照'):
                review_gate.verify(self.state, self.request, report, [])

    def test_snapshot_cannot_be_edited_without_reviewing_actual_request(self):
        report = self.approve()
        report['shot_reviews'][0]['shot']['camera'] = '自动加横移'
        with self.assertRaisesRegex(ValueError, '快照'):
            review_gate.verify(self.state, self.request, report, [])

    def test_new_commit_does_not_accept_old_batch_only_approval(self):
        report = self.approve()
        report['policy'] = 'final-request-review-v1'
        with self.assertRaisesRegex(ValueError, '复核记录'):
            review_gate.verify(self.state, self.request, report, [])

    def test_placeholder_findings_request_review_instead_of_forcing_motion(self):
        shot = self.unit['shots'][0]
        shot['purpose'] = shot['label'] + '的原有信息通过该取景完整可读；' + shot['camera']
        shot['depth_of_field'] = '中等景深，车与本镜动作接触点清楚，前后方已有结构略柔而可辨'
        before = copy.deepcopy(self.unit)
        issues = shot_review.diagnostics(self.unit)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['fields'], ['purpose', 'depth_of_field'])
        self.assertEqual(self.unit, before)

    def test_same_concrete_focus_across_shots_is_not_itself_an_error(self):
        self.unit['shots'][1]['depth_of_field'] = self.unit['shots'][0]['depth_of_field']
        self.assertEqual(shot_review.diagnostics(self.unit), [])

    def test_stale_mask_reference_warns_without_reassigning_voice_or_body(self):
        state = {'ledger': {'character_variant_policy': 'source-masked-v1', 'characters': [
            {'id': 'CHAR0001'}, {'id': 'CHAR0002', 'appearance_variant': {'base_asset_id': 'CHAR0001'}}]}}
        unit = {'continuity': {'initial': {'person': {'present': True, 'asset_id': 'CHAR0002'}}},
                'audio': [{'record': 'L1', 'character_id': 'CHAR0001', 'voice': 'OS'}],
                'shots': [{'text': '@{CHAR0001}站立', 'dialogue_parts': [{'record': 'L1'}],
                           'continuity': {'changes': []}}]}
        before = copy.deepcopy(unit)
        warnings = shot_review.appearance_reference_warnings(state, unit)
        self.assertEqual(warnings[0]['reached_appearances'], ['CHAR0002'])
        self.assertEqual(warnings[0]['fields'], ['audio.OS', 'text'])
        self.assertEqual(unit, before)
        unit['shots'][0]['continuity']['changes'] = [{'entity': 'person', 'set': {'asset_id': 'CHAR0001'}}]
        self.assertEqual(shot_review.appearance_reference_warnings(state, unit), [])

    def test_absent_person_or_legacy_policy_does_not_infer_visible_appearance(self):
        state = {'ledger': {'characters': [{'id': 'CHAR0001'},
                 {'id': 'CHAR0002', 'appearance_variant': {'base_asset_id': 'CHAR0001'}}]}}
        unit = {'continuity': {'initial': {'person': {'present': True, 'asset_id': 'CHAR0002'}}},
                'shots': [{'text': '@{CHAR0001}的声音', 'continuity': {'changes': []}}]}
        self.assertEqual(shot_review.appearance_reference_warnings(state, unit), [])
        state['ledger']['character_variant_policy'] = 'source-masked-v1'
        unit['continuity']['initial']['person']['present'] = False
        self.assertEqual(shot_review.appearance_reference_warnings(state, unit), [])


if __name__ == '__main__':
    unittest.main()
