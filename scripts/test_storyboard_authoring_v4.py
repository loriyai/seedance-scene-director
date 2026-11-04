"""Offline invariants for the compact authoring trace.

These fixtures test mechanical bindings and explicit timing constraints.  A
passing gate is deliberately not treated as approval of fictional creative
content; semantic reading remains a separate operation.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import storyboard_authoring_gate as gate
import storyboard_text as text_core
import storyboard_text_delivery as delivery
import director as director
import test_storyboard_text_delivery as delivery_test_fixtures


class CompactAuthoringTraceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.draft = b'fictional authored text for trace-boundary tests\n'
        self.context = 'fictional-context-sha'
        self.state = {
            'storyboard_authoring_policy': gate.POLICY,
            'records': [{
                'id': 'L000001', 'scene': 'SC0001', 'kind': 'action',
                'label': '', 'text': '阿青缓慢提起杯子。', 'start': 0, 'end': 10,
            }],
        }
        self.units = [{
            'draft_key': 'SC0001/u01', 'source_records': ['L000001'],
            'span': [0, 10], 'duration': 4,
            'shots': [{
                'start': 0, 'end': 4,
                'text': '·00秒-04秒[镜头1-提杯]：（@{CHAR0001}的中景；转场：硬切）'
                        '桌侧相机缓慢向右移，双手与杯身在画面中部；浅景深使手指与杯沿清晰、门框在焦外；'
                        '主光：左侧窗光照亮手背，辅光：桌面反射托住杯底暗部；'
                        '@{CHAR0001}双手缓慢把杯子从桌面提到胸前，杯到胸前即切出。音效：杯底离桌的轻响。',
                'dialogue_parts': [],
            }],
        }]

    def trace(self):
        return {
            'policy': gate.POLICY,
            'version': 4,
            'draft_sha256': hashlib.sha256(self.draft).hexdigest(),
            'context_sha256': self.context,
            'source_span': [self.units[0]['span'][0], self.units[-1]['span'][1]],
            'units': [{
                'draft_key': unit['draft_key'],
                'shots': [{
                    'shot_key': f'{unit["draft_key"]}#{index}',
                    'source_records': list(unit['source_records']),
                    'viewing_decision': f'第{index}个取景读清杯身提起及双手受力的实际变化。',
                    'natural_timing': '原文缓慢提杯，双手受力与杯身上升在同一过程中读清，杯到胸前切出。',
                } for index, shot in enumerate(unit['shots'], 1)],
            } for unit in self.units],
            'unit_organization': {
                'policy': 'prefer_26_30_at_unit_boundaries_v2',
                'units': [{
                    'draft_key': unit['draft_key'],
                    **({'reason_code': 'selected_range_end',
                        'reason': '选定来源在提杯完成处结束，没有后续来源可用于组合。'}
                       if unit['duration'] < 26 else {}),
                } for unit in self.units],
            },
        }

    def validate(self, trace=None, **kwargs):
        trace = self.trace() if trace is None else trace
        path = self.project / 'draft.txt.authoring.json'
        path.write_text(json.dumps(trace, ensure_ascii=False), encoding='utf-8')
        return gate.validate(self.project, self.state, self.draft, self.units,
                             self.context, path, **kwargs)

    @staticmethod
    def codes(report):
        return {row['code'] for row in report.get('errors', [])}

    def assertPasses(self, report):
        self.assertEqual(report['status'], 'passed', report.get('errors'))

    def set_cue(self):
        record = self.state['records'][0]
        record.update(kind='dialogue', speaker='阿青', label='阿青（笑了笑）',
                      voice='说', text='你来了。')
        self.units[0]['shots'][0]['dialogue_parts'] = [{
            'record': 'L000001', 'voice': '说', 'character_id': 'CHAR0001',
            'source_kind': 'character', 'source_label': None,
            'text': '你来了。', 'start': 0.5, 'end': 1.5,
        }]

    def make_target_unit(self):
        # Six legal integer windows sum to 28 seconds.  Distinct source events
        # keep this an organization check rather than a repeated-shell fixture.
        descriptions = [
            '相机从桌沿推向双手，杯底离桌时指腹受力清楚；@{CHAR0001}缓慢提起杯子至胸前，杯沿稳定即切出。',
            '相机绕到门旁朝室内拍，门扇与脚下木槛同框；@{CHAR0001}把门拉开至通路足够宽，门缝露出室外来路。',
            '低机位沿砖地侧移，裙摆与跨槛的脚保留在下半画幅；@{CHAR0001}跨过门槛并把后脚落在院中石板。',
            '相机随肩转向院墙，墙角与脸部呈对角关系；@{CHAR0001}辨清墙边新出现的来客，眉间由紧绷转为舒展。',
            '相机从来客肩后朝双手靠近，两只手掌与杯柄均在清晰区域；@{CHAR0001}将杯子递至对方张开的手中。',
            '侧面机位向后退露出院中通路，前景留空给退出路径；@{CHAR0001}松开杯柄并转身走向门洞，背影跨入门内即切出。',
        ]
        lengths = [3, 4, 5, 5, 5, 6]
        transitions = ['硬切', '动作匹配切', '硬切，按视线接续',
                       '硬切', '动作匹配切', '硬切，按出口方向接续']
        sounds = ['杯底离桌轻响', '门轴轻响', '鞋底踏石声',
                  '衣料轻摩擦', '杯柄与指腹轻擦', '脚步渐远']
        cursor = 0
        records, shots = [], []
        for index, (length, action, transition, sound) in enumerate(
                zip(lengths, descriptions, transitions, sounds), 1):
            record = {'id': f'L{index:06d}', 'scene': 'SC0001', 'kind': 'action',
                      'label': '', 'text': action, 'start': (index - 1) * 10,
                      'end': index * 10}
            records.append(record)
            shots.append({
                'start': cursor, 'end': cursor + length,
                'text': f'·{cursor:02d}秒-{cursor + length:02d}秒[镜头{index}-事件{index}]：'
                        f'（@{{CHAR0001}}的中景；转场：{transition}）' + action +
                        '浅景深使当前接触清晰、远处焦外；主光：左侧日光落在左颊，'
                        f'辅光：院墙反射托住右侧暗部。音效：{sound}。',
                'dialogue_parts': [],
            })
            cursor += length
        self.state['records'] = records
        self.units[0].update(source_records=[r['id'] for r in records],
                             span=[0, 60], duration=cursor, shots=shots)

    def test_minimal_trace_does_not_require_duplicate_action_fields(self):
        trace = self.trace()
        row = trace['units'][0]['shots'][0]
        self.assertEqual(set(row), {'shot_key', 'source_records',
                                    'viewing_decision', 'natural_timing'})
        self.assertPasses(self.validate(trace))

    def test_gate_does_not_claim_semantic_reading(self):
        report = self.validate()
        self.assertPasses(report)
        self.assertEqual(report['semantic_review'], 'not_performed')
        self.assertTrue(report['limits'])
        self.assertEqual(report['warnings'], [])

    def test_body_meta_check_distinguishes_targets_from_ordinary_chinese(self):
        original = self.units[0]['shots'][0]['text']
        cases = [
            ('看不得孩子挨饿', False), ('顾不得衣袖沾水', False),
            ('舍不得放下杯子', False), ('不得不转身', False),
            ('门边贴着“禁止吸烟”', False), ('逐页审核账本', False),
            ('不得生成字幕', True), ('禁止添加额外人物', True),
            ('不得擅自改写台词', True), ('禁止新增人物', True),
            ('不要改变人物身份', True), ('“不得显示系统面板”', True),
            ('后台审核要求', True),
        ]
        for prose, blocked in cases:
            with self.subTest(prose=prose):
                self.units[0]['shots'][0]['text'] = original.replace('桌侧相机', prose + '；桌侧相机')
                report = self.validate()
                self.assertEqual('AUTHORING_META_PROMPT' in self.codes(report), blocked)
                self.assertEqual(report['status'], 'blocked' if blocked else 'passed', report['errors'])

    def make_repeated_unit(self, lengths=(4, 4, 4, 4, 4, 4)):
        original = self.units[0]['shots'][0]
        shots, cursor = [], 0
        for index, length in enumerate(lengths, 1):
            shot = copy.deepcopy(original)
            shot.update(start=cursor, end=cursor + length)
            shot['text'] = shot['text'].replace(
                '·00秒-04秒[镜头1-', f'·{cursor:02d}秒-{cursor + length:02d}秒[镜头{index}-')
            shots.append(shot)
            cursor += length
        self.units[0].update(shots=shots, duration=cursor)

    def test_all_statistical_risks_are_review_warnings_for_v4(self):
        self.make_repeated_unit()
        report = self.validate()
        self.assertPasses(report)
        self.assertEqual(report['errors'], [])
        self.assertEqual({item['code'] for item in report['warnings']}, {
            'AUTHORING_UNIFORM_TIMING', 'AUTHORING_UNIFORM_TRANSITION',
            'AUTHORING_UNIFORM_SOUND', 'AUTHORING_TEMPLATE_REUSE',
        })
        gate.require(report)
        self.assertEqual(report['semantic_review'], 'not_performed')

    def test_uniform_timing_warning_keeps_threshold_and_basis_contract(self):
        original = copy.deepcopy(self.units)
        for lengths, warned in [((4, 4, 4, 4, 5, 6), True),
                                ((4, 4, 4, 3, 5, 6), False),
                                ((1,) * 13 + (2,) * 7, True),
                                ((1,) * 12 + (2,) * 8, False),
                                ((4, 4, 4, 4, 4), False)]:
            with self.subTest(lengths=lengths):
                self.units = copy.deepcopy(original)
                self.make_repeated_unit(lengths)
                report = self.validate()
                self.assertPasses(report)
                self.assertEqual('AUTHORING_UNIFORM_TIMING' in {
                    item['code'] for item in report['warnings']}, warned)
        self.units = original
        self.make_repeated_unit()
        trace = self.trace()
        trace['timing_distribution_basis'] = '测试来源为六次等间隔滴水，逐次观察相同自然周期。'
        original_trace = copy.deepcopy(trace)
        report = self.validate(trace)
        self.assertPasses(report)
        self.assertIn('AUTHORING_UNIFORM_TIMING', {item['code'] for item in report['warnings']})
        self.assertEqual(trace, original_trace)

    def test_statistical_warnings_do_not_override_true_placeholder_errors(self):
        self.make_repeated_unit()
        shot = self.units[0]['shots'][0]
        shot['text'] = shot['text'].replace('桌侧相机', '镜头平滑跟随后在动作结果处停稳；桌侧相机')
        self.units[0]['global'] = '不同景层保持可见关系'
        report = self.validate()
        self.assertEqual(report['status'], 'blocked')
        self.assertTrue({'AUTHORING_GENERIC_CAMERA', 'AUTHORING_GENERIC_STATION'} <= self.codes(report))
        self.assertIn('AUTHORING_UNIFORM_TIMING', {item['code'] for item in report['warnings']})
        with self.assertRaises(gate.AuthoringGateError):
            gate.require(report)

    def test_body_collector_reports_independent_hard_errors_without_mutation(self):
        self.units[0]['shots'][0]['text'] = (
            '·00秒-04秒[镜头1-提杯]：（@{CHAR0001}的中景；转场：硬切）'
            '不得生成字幕；说话者在当前关系中完成一次清楚的发声与回应；'
            '镜头平滑跟随后在动作结果处停稳。音效：杯底离桌的轻响。')
        self.units[0]['global'] = '不同景层保持可见关系'
        before = copy.deepcopy(self.units)
        errors = gate.body_errors(self.units)
        self.assertEqual({item['code'] for item in errors}, {
            'AUTHORING_META_PROMPT', 'AUTHORING_TEMPLATE_PHRASE',
            'AUTHORING_GENERIC_CAMERA', 'AUTHORING_LIGHTING_UNDER_SPECIFIED',
            'AUTHORING_DEPTH_MISSING', 'AUTHORING_GENERIC_STATION',
        })
        self.assertTrue(all(item.get('shot') == 'SC0001/u01#1'
                            for item in errors if item['code'] != 'AUTHORING_GENERIC_STATION'))
        self.assertEqual(self.units, before)

    def test_missing_natural_timing_is_not_replaced_by_duration(self):
        trace = self.trace()
        row = trace['units'][0]['shots'][0]
        row.pop('natural_timing')
        row['duration_seconds'] = 4
        self.assertEqual(self.validate(trace)['status'], 'blocked')

    def test_missing_viewing_decision_is_not_replaced_by_generic_status(self):
        trace = self.trace()
        trace['units'][0]['shots'][0].pop('viewing_decision')
        trace['status'] = '全部镜头已通过'
        self.assertEqual(self.validate(trace)['status'], 'blocked')

    def test_changed_draft_invalidates_prior_trace(self):
        trace = self.trace()
        self.draft += b'changed body\n'
        self.assertIn('AUTHORING_TRACE_DRAFT_BINDING', self.codes(self.validate(trace)))

    def test_changed_context_invalidates_prior_trace(self):
        trace = self.trace()
        self.context = 'changed-context-sha'
        self.assertIn('AUTHORING_TRACE_CONTEXT_BINDING', self.codes(self.validate(trace)))

    def test_missing_shot_cannot_be_hidden_in_whole_scene_statement(self):
        trace = self.trace()
        trace['units'][0]['shots'] = []
        trace['source_cue_coverage'] = {'status': '全部已逐镜阅读并落实'}
        self.assertEqual(self.validate(trace)['status'], 'blocked')

    def test_unknown_source_record_is_rejected(self):
        trace = self.trace()
        trace['units'][0]['shots'][0]['source_records'] = ['L999999']
        self.assertIn('AUTHORING_TRACE_SOURCE', self.codes(self.validate(trace)))

    def test_target_unit_needs_no_duration_decision_or_redundant_reason(self):
        self.make_target_unit()
        trace = self.trace()
        self.assertEqual(self.units[0]['duration'], 28)
        self.assertEqual(trace['unit_organization']['units'], [{'draft_key': 'SC0001/u01'}])
        self.assertPasses(self.validate(trace))

    def test_short_unit_requires_concrete_organization_reason(self):
        trace = self.trace()
        trace['unit_organization']['units'][0].pop('reason')
        self.assertEqual(self.validate(trace)['status'], 'blocked')

    def test_source_cue_claim_without_mapping_is_rejected(self):
        self.set_cue()
        trace = self.trace()
        trace['source_cue_coverage'] = {'status': '笑了笑已经落实，全部无遗漏'}
        self.assertEqual(self.validate(trace)['status'], 'blocked')

    def test_source_cue_mapping_can_locate_actual_shot(self):
        self.set_cue()
        trace = self.trace()
        trace['units'][0]['shots'][0]['source_cues'] = ['L000001:笑了笑']
        self.assertPasses(self.validate(trace))

    def test_cue_mapping_cannot_substitute_different_source_cue(self):
        self.set_cue()
        trace = self.trace()
        trace['units'][0]['shots'][0]['source_cues'] = ['L000001:皱眉']
        self.assertEqual(self.validate(trace)['status'], 'blocked')

    def test_explicit_omission_is_recorded_not_declared_semantically_approved(self):
        self.set_cue()
        trace = self.trace()
        trace['source_cue_omissions'] = [{
            'source_record': 'L000001', 'cue': '笑了笑',
            'reason': '本镜只取双手与杯柄，笑意不作为画面重点；声音仍按原有态度发出。',
        }]
        report = self.validate(trace)
        self.assertPasses(report)
        self.assertEqual(report['semantic_review'], 'not_performed')

    def test_empty_cue_omission_reason_is_rejected(self):
        self.set_cue()
        trace = self.trace()
        trace['source_cue_omissions'] = [{
            'source_record': 'L000001', 'cue': '笑了笑', 'reason': '',
        }]
        self.assertEqual(self.validate(trace)['status'], 'blocked')

    def test_partial_overlap_uses_authored_extent_not_maximum_duration(self):
        trace = self.trace()
        row = trace['units'][0]['shots'][0]
        row['natural_timing'] = '动作占0到2秒，发言从1.5秒开始并持续3秒，总跨度4.5秒。'
        row['timing_constraints'] = [
            {'label': '提杯', 'start': 0, 'end': 2},
            {'label': '随后起句的发言', 'start': 1.5, 'end': 4.5},
        ]
        report = self.validate(trace)
        self.assertIn('AUTHORING_TRACE_TIMING_CONSTRAINT', self.codes(report))

    def test_partial_overlap_inside_window_is_accepted_without_scalar_loads(self):
        trace = self.trace()
        trace['units'][0]['shots'][0]['timing_constraints'] = [
            {'label': '提杯', 'start': 0, 'end': 2},
            {'label': '发言', 'start': 1, 'end': 4},
        ]
        self.assertPasses(self.validate(trace))

    def test_invalid_local_constraints_are_rejected(self):
        for start, end in [(-0.1, 2), (2, 1), (True, 2), (0, float('nan')),
                           (0, float('inf'))]:
            with self.subTest(start=start, end=end):
                trace = self.trace()
                trace['units'][0]['shots'][0]['timing_constraints'] = [
                    {'label': '实际接触', 'start': start, 'end': end},
                ]
                self.assertIn('AUTHORING_TRACE_TIMING_CONSTRAINT',
                              self.codes(self.validate(trace)))

    def test_validation_leaves_author_prose_timings_and_trace_bytes_unchanged(self):
        before_state, before_units = copy.deepcopy(self.state), copy.deepcopy(self.units)
        trace = self.trace()
        path = self.project / 'draft.txt.authoring.json'
        path.write_text(json.dumps(trace, ensure_ascii=False), encoding='utf-8')
        before_bytes = path.read_bytes()
        report = gate.validate(self.project, self.state, self.draft, self.units,
                               self.context, path)
        self.assertPasses(report)
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertEqual(self.state, before_state)
        self.assertEqual(self.units, before_units)

    def test_metadata_binding_is_pure_and_preserves_authored_decisions(self):
        decisions = self.trace()
        for field in ('draft_sha256', 'context_sha256', 'source_span'):
            decisions.pop(field)
        before = copy.deepcopy(decisions)
        bound = gate.bind_metadata(decisions, self.draft, self.units, self.context)
        self.assertEqual(decisions, before)
        self.assertEqual(bound['units'], before['units'])
        self.assertEqual(bound['unit_organization'], before['unit_organization'])
        self.assertEqual(bound['draft_sha256'], hashlib.sha256(self.draft).hexdigest())
        self.assertEqual(bound['context_sha256'], self.context)
        self.assertEqual(bound['source_span'], [0, 10])
        self.assertPasses(self.validate(bound))

    def test_fixed_template_shell_remains_blocked(self):
        self.units[0]['shots'][0]['text'] = self.units[0]['shots'][0]['text'].replace(
            '双手缓慢把杯子', '说话者在当前关系中完成一次清楚的发声与回应；双手缓慢把杯子')
        report = self.validate()
        self.assertIn('AUTHORING_TEMPLATE_PHRASE', self.codes(report))
        self.assertEqual(report['status'], 'blocked')

    def test_identity_rotated_similarity_requires_review_without_blocking(self):
        second = copy.deepcopy(self.units[0]['shots'][0])
        second.update(start=4, end=8,
                      text=second['text'].replace('CHAR0001', 'CHAR0002').replace(
                          '·00秒-04秒[镜头1-', '·04秒-08秒[镜头2-'))
        self.units[0]['shots'].append(second)
        self.units[0]['duration'] = 8
        trace = self.trace()
        trace['units'][0]['shots'][1]['viewing_decision'] = '另一人物的持杯动作作为第二个观看决定。'
        trace['units'][0]['shots'][1]['natural_timing'] = '另一人物在本镜演出持杯，不借用前镜作者依据。'
        report = self.validate(trace)
        self.assertPasses(report)
        reuse = [item for item in report['warnings'] if item['code'] == 'AUTHORING_TEMPLATE_REUSE']
        self.assertEqual(len(reuse), 1)
        self.assertEqual(reuse[0]['shots'], ['SC0001/u01#1', 'SC0001/u01#2'])

    def test_metadata_binding_does_not_fill_absent_semantic_decisions(self):
        decisions = self.trace()
        row = decisions['units'][0]['shots'][0]
        row.pop('viewing_decision')
        row.pop('natural_timing')
        before = copy.deepcopy(decisions)
        bound = gate.bind_metadata(decisions, self.draft, self.units, self.context)
        self.assertEqual(decisions, before)
        self.assertEqual(bound['units'], before['units'])
        self.assertNotIn('viewing_decision', bound['units'][0]['shots'][0])
        self.assertNotIn('natural_timing', bound['units'][0]['shots'][0])
        report = self.validate(bound)
        self.assertEqual(report['status'], 'blocked')
        missing = {error.get('field') for error in report['errors']
                   if error['code'] == 'AUTHORING_TRACE_EVIDENCE'}
        self.assertEqual(missing, {'viewing_decision', 'natural_timing'})

    def test_review_still_binds_exact_trace_gate_and_read_shot_list(self):
        # This is a fake review record used only to exercise the binding API.
        # Its performed flag is not evidence of an actual creative reading.
        authoring = self.validate()
        self.assertPasses(authoring)
        info = {'draft_sha256': hashlib.sha256(self.draft).hexdigest(),
                'context_sha256': self.context, 'scenes': ['SC0001'],
                'authoring_gate': authoring}
        review = {
            'draft_sha256': info['draft_sha256'], 'context_sha256': self.context,
            'reviewer': 'fictional-test-record-only', 'semantic_review': 'performed',
            'authoring_trace_sha256': authoring['trace_sha256'],
            'authoring_gate_digest': authoring['gate_digest'],
            'shot_count': 1, 'reviewed_shots': ['SC0001/u01#1'],
            'scenes': [{'scene_id': 'SC0001', 'status': 'passed', 'issues': [],
                        'basis': '仅验证虚构审阅记录的精确绑定，不批准生产创作。'}],
        }
        before_info, before_review = copy.deepcopy(info), copy.deepcopy(review)
        self.assertEqual(delivery.verify_review(review, info), review)
        mutations = [
            ('draft_sha256', 'stale-draft'), ('context_sha256', 'stale-context'),
            ('authoring_trace_sha256', 'stale-trace'),
            ('authoring_gate_digest', 'stale-gate'), ('shot_count', 0),
            ('reviewed_shots', []), ('reviewed_shots', ['SC0001/u01#2']),
            ('reviewed_shots', ['SC0001/u01#1', 'SC0001/u01#1']),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(review)
                changed[field] = value
                with self.assertRaises(ValueError):
                    delivery.verify_review(changed, info)
        self.assertEqual(info, before_info)
        self.assertEqual(review, before_review)

    def test_twenty_plus_twenty_exposes_twenty_eight_second_boundary_without_rewrite(self):
        # The authored units each last 20 seconds, so direct whole-unit merge
        # cannot be used.  Existing 4-second shot boundaries permit 28+12.
        self.units = []
        self.state['records'] = []
        for index in range(2):
            rid = f'L{index + 1:06d}'
            self.state['records'].append({
                'id': rid, 'scene': 'SC0001', 'kind': 'action', 'label': '',
                'text': '用于边界枚举的原始连续动作。',
                'start': index * 10, 'end': (index + 1) * 10,
            })
            self.units.append({
                'draft_key': f'SC0001/u{index + 1:02d}',
                'source_records': [rid], 'span': [index * 10, (index + 1) * 10],
                'duration': 20,
                'shots': [{'start': start, 'end': start + 4,
                           'text': '原有摄影与动作正文。', 'dialogue_parts': []}
                          for start in range(0, 20, 4)],
            })
        before = copy.deepcopy(self.units)
        report = text_core.observations(self.state, self.units)
        candidates = report['same_scene_boundary_candidates']
        self.assertTrue(any(candidate['start_shot'] == 'SC0001/u01#1'
                            and candidate['end_shot'] == 'SC0001/u02#2'
                            and candidate['combined_duration'] == 28
                            for candidate in candidates), candidates)
        self.assertTrue(all(candidate['review'] == 'human_review_only'
                            for candidate in candidates))
        self.assertEqual(report['same_scene_adjacent_candidates'], [])
        self.assertEqual(self.units, before)


class TraceBindingIntegrationTest(unittest.TestCase):
    """Use the existing isolated screenplay fixture, with authored decisions."""

    def setUp(self):
        fixture = delivery_test_fixtures.TextDeliveryIntegration('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.project = fixture.project
        state = director.load(self.project)
        state['storyboard_authoring_policy'] = gate.POLICY
        director.write_json(self.project / 'session.json', state)
        # These four prose decisions are fixture text authored here, not a
        # conversion or creative completion generated from parsed screenplay.
        self.decisions_data = {
            'policy': gate.POLICY, 'version': 4,
            'units': [
                {'draft_key': 'SC0001/u01', 'shots': [{
                    'shot_key': 'SC0001/u01#1', 'source_records': ['L000003'],
                    'viewing_decision': '杯子落到桌面，接触结果在本镜读清。',
                    'natural_timing': '提杯到落桌依原动作演绎，接触后切出。',
                }]},
                {'draft_key': 'SC0001/u02', 'shots': [{
                    'shot_key': 'SC0001/u02#1', 'source_records': ['L000004'],
                    'viewing_decision': '交代把消息发到群里的要求。',
                    'natural_timing': '整句连贯说出，杯子已经在桌上。',
                }]},
                {'draft_key': 'SC0002/u03', 'shots': [{
                    'shot_key': 'SC0002/u03#1', 'source_records': ['L000007'],
                    'viewing_decision': '走到走廊尽头的来源动作完成。',
                    'natural_timing': '走到尽头后落步，不添加等待。',
                }]},
                {'draft_key': 'SC0002/u04', 'shots': [{
                    'shot_key': 'SC0002/u04#1', 'source_records': ['L000008'],
                    'viewing_decision': '陈默说出去查看的打算。',
                    'natural_timing': '短句自然发出，不新增动身事件。',
                }]},
            ],
            'unit_organization': {
                'policy': 'prefer_26_30_at_unit_boundaries_v2',
                'units': [
                    {'draft_key': 'SC0001/u01', 'reason_code': 'semantic_cut',
                     'reason': '隔离的绑定测试保留夹具原单元，不对其作生产语义批准。'},
                    {'draft_key': 'SC0001/u02', 'reason_code': 'scene_boundary',
                     'reason': '下一条来源已切换到走廊场景。'},
                    {'draft_key': 'SC0002/u03', 'reason_code': 'semantic_cut',
                     'reason': '隔离的绑定测试保留夹具原单元，不对其作生产语义批准。'},
                    {'draft_key': 'SC0002/u04', 'reason_code': 'selected_range_end',
                     'reason': '夹具选定来源在这句话结束。'},
                ],
            },
        }
        self.decisions = self.project / 'authored-decisions.json'
        director.write_json(self.decisions, self.decisions_data)

    def test_source_bound_spoken_prohibitions_pass_parser_and_authoring_gate(self):
        before = self.fixture.snapshot()
        for spoken in ('不得进入后院。', '不要动我的杯子。', '禁止靠近窗户。'):
            with self.subTest(spoken=spoken):
                state = director.load(self.project)
                state['records'], state['length'] = director.build_records(
                    self.fixture.script.replace('把消息发给@群里。', spoken), state['ledger'])
                authored = self.fixture.authored_text().replace('把消息发给@群里。', spoken)
                authored = authored.replace('音效：',
                    '浅景深使双手清晰、门框在焦外；主光：左侧窗光照亮手背，'
                    '辅光：桌面反射托住杯底暗部。音效：')
                raw = authored.encode('utf-8')
                units = text_core.parse(authored, state)
                parts = [part for unit in units for shot in unit['shots'] for part in shot['dialogue_parts']]
                self.assertIn(spoken, [part['text'] for part in parts])
                context = 'synthetic-parser-context'
                trace = gate.bind_metadata(self.decisions_data, raw, units, context)
                path = self.project / 'spoken-prohibition.authoring.json'
                director.write_json(path, trace)
                report = gate.validate(self.project, state, raw, units, context, path)
                self.assertEqual(report['status'], 'passed', report['errors'])
                self.assertEqual(report['semantic_review'], 'not_performed')
                # A source line containing a prohibition does not exempt body
                # prose from its independent rendering-instruction check.
                units[0]['shots'][0]['text'] = units[0]['shots'][0]['text'].replace(
                    '浅景深', '“不得显示系统面板”；浅景深')
                report = gate.validate(self.project, state, raw, units, context, path)
                self.assertIn('AUTHORING_META_PROMPT', {item['code'] for item in report['errors']})
        after = self.fixture.snapshot()
        self.assertEqual({key: after[key] for key in before}, before)

    def test_bind_trace_writes_only_new_metadata_envelope(self):
        before = self.fixture.snapshot()
        target = self.project / 'bound-trace.json'
        result = delivery.bind_trace(self.project, self.fixture.draft, self.decisions,
                                     output=target)
        after = self.fixture.snapshot()
        self.assertEqual(set(after) - set(before), {'bound-trace.json'})
        for relative, content in before.items():
            self.assertEqual(after[relative], content, relative)
        self.assertEqual(result['status'], 'bound')
        self.assertEqual(result['semantic_review'], 'not_performed')
        bound = director.read_json(target)
        self.assertEqual(bound['units'], self.decisions_data['units'])
        self.assertEqual(bound['unit_organization'], self.decisions_data['unit_organization'])
        self.assertEqual(bound['draft_sha256'], hashlib.sha256(self.fixture.draft.read_bytes()).hexdigest())
        state = director.load(self.project)
        self.assertEqual(bound['context_sha256'], delivery.context_hash(state, self.project,
            scope=delivery.context_scope(state, bound['source_span'], bound)))
        units = text_core.parse(self.fixture.draft.read_text(encoding='utf-8'), director.load(self.project))
        self.assertEqual(bound['source_span'], [units[0]['span'][0], units[-1]['span'][1]])

    def test_bind_trace_rejects_input_or_existing_output_overwrite(self):
        occupied = self.project / 'occupied.json'
        occupied.write_text('existing output must survive', encoding='utf-8')
        for target in (self.fixture.draft, self.decisions,
                       self.project / 'session.json', occupied):
            with self.subTest(target=target):
                before = self.fixture.snapshot()
                with self.assertRaises(ValueError):
                    delivery.bind_trace(self.project, self.fixture.draft,
                                        self.decisions, output=target)
                self.assertEqual(self.fixture.snapshot(), before)

    def test_binding_incomplete_decisions_does_not_create_semantic_approval(self):
        incomplete = copy.deepcopy(self.decisions_data)
        incomplete['units'][0]['shots'][0].pop('natural_timing')
        director.write_json(self.decisions, incomplete)
        target = self.project / 'incomplete-bound-trace.json'
        result = delivery.bind_trace(self.project, self.fixture.draft, self.decisions,
                                     output=target)
        self.assertEqual(result['semantic_review'], 'not_performed')
        state = director.load(self.project)
        raw = self.fixture.draft.read_bytes()
        units = text_core.parse(raw.decode('utf-8'), state)
        report = gate.validate(self.project, state, raw, units,
                               delivery.context_hash(state, self.project), target)
        self.assertEqual(report['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
