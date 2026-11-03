import copy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest

import storyboard_authoring_gate as gate


class StoryboardAuthoringGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.state = {
            'storyboard_authoring_policy': gate.POLICY,
            'records': [{'id': 'L000001', 'scene': 'SC0001', 'kind': 'action',
                         'start': 0, 'end': 4}],
        }
        self.draft = b'authored draft\n'
        self.units = [{
            'draft_key': 'SC0001/u01',
            'source_records': ['L000001'],
            'span': [0, 4],
            'duration': 4,
            'shots': [{
                'start': 0, 'end': 4,
                'text': '·00秒-04秒[镜头1-窗外]：（@{CHAR0001}的中景；转场：硬切）@{CHAR0001}抬头，视线落向窗外，肩膀随呼吸轻微起伏；浅景深让@{CHAR0001}清晰、窗外焦外；主光：左侧窗外漫射日光落在左脸，辅光：院墙反射托住右脸暗部。音效：无。',
                'dialogue_parts': [],
            }],
        }]
        self.context = 'context-sha'

    def tearDown(self):
        self.temp.cleanup()

    def write_trace(self, **updates):
        draft_sha = hashlib.sha256(self.draft).hexdigest()
        trace = {
            'policy': gate.POLICY,
            'version': gate.LEGACY_VERSION,
            'draft_sha256': draft_sha,
            'context_sha256': self.context,
            'source_span': [0, 4],
            'timing_distribution_basis': '本镜持续观察窗外变化，动作和观察均在镜内完成。',
            'unit_organization': {
                'policy': gate.UNIT_ORGANIZATION_POLICY,
                'units': [{
                    'draft_key': 'SC0001/u01',
                    'duration_seconds': 4,
                    'decision': 'short_exception',
                    'reason_code': 'selected_range_end',
                    'reason': '本测试选定范围在该单元结束，范围内没有可承接的后续来源。',
                }],
            },
            'units': [{
                'draft_key': 'SC0001/u01',
                'shots': [{
                    'shot_key': 'SC0001/u01#1',
                    'source_records': ['L000001'],
                    'actor_ids': ['CHAR0001'],
                    'focus_note': '主焦点是抬头并看向窗外的CHAR0001，观众先读清视线变化。',
                    'beat': '观众读清人物从抬头到看向窗外的变化。',
                    'visual_change': '头部抬起，视线落到窗外，肩膀保持呼吸起伏。',
                    'gaze_target': '窗外',
                    'hold_reason': '窗外变化尚未揭示，保留观察到切出。',
                    'timing_basis': {
                        'duration_seconds': 4,
                        'speech_seconds': 0,
                        'action_seconds': 2,
                        'observation_seconds': 2,
                        'action_phases': ['抬头', '视线落向窗外'],
                        'entry_phase': '人物从低头状态抬起头',
                        'completion_point_seconds': 2,
                        'parallel_with': ['呼吸与窗外观察同期'],
                        'tail_load': '窗外变化尚未揭示，观察持续到切出。',
                        'parallel': False,
                        'cut_reason': '视线落点与观察信息完成后切出。',
                    },
                }],
            }],
            'source_cue_coverage': {'status': '无源文括号动作；本镜动作与观察已逐镜落实。'},
        }
        for key, value in updates.items():
            trace[key] = value
        path = self.project / 'draft.txt.authoring.json'
        path.write_text(json.dumps(trace, ensure_ascii=False), encoding='utf-8')
        return path

    def test_legacy_project_is_readable_without_trace(self):
        report = gate.validate(self.project, {}, self.draft, self.units, self.context)
        self.assertFalse(report['enforced'])

    def test_new_project_requires_trace(self):
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['errors'][0]['code'], 'AUTHORING_TRACE_MISSING')

    def test_valid_trace_passes_and_binds_one_shot(self):
        path = self.write_trace()
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['shot_count'], 1)
        self.assertEqual(report['shot_keys'], ['SC0001/u01#1'])
        self.assertEqual(report['warnings'], [])

    def test_ordinary_prose_is_not_a_meta_instruction(self):
        original = self.units[0]['shots'][0]['text']
        for prose in ('看不得孩子挨饿', '顾不得衣袖沾水', '舍不得放下杯子',
                      '不得不转身', '门边贴着“禁止吸烟”', '逐页审核账本'):
            with self.subTest(prose=prose):
                self.units[0]['shots'][0]['text'] = original.replace('抬头，', prose + '；抬头，')
                path = self.write_trace()
                report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
                self.assertEqual(report['status'], 'passed', report['errors'])

    def test_rendering_prohibitions_still_block_including_quoted_meta(self):
        original = self.units[0]['shots'][0]['text']
        for prose in ('不得生成字幕', '禁止添加额外人物', '不要改变人物身份',
                      '不得擅自改写台词', '禁止新增人物',
                      '“不得显示系统面板”', '后台审核要求'):
            with self.subTest(prose=prose):
                self.units[0]['shots'][0]['text'] = original.replace('抬头，', prose + '；抬头，')
                path = self.write_trace()
                report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
                self.assertEqual(report['status'], 'blocked')
                self.assertIn('AUTHORING_META_PROMPT', {item['code'] for item in report['errors']})

    def repeated_trace(self):
        path = self.write_trace()
        trace = json.loads(path.read_text(encoding='utf-8'))
        shot = self.units[0]['shots'][0]
        self.units[0]['shots'] = [dict(shot, start=index * 4, end=(index + 1) * 4)
                                   for index in range(6)]
        self.units[0]['duration'] = 24
        row = trace['units'][0]['shots'][0]
        trace['units'][0]['shots'] = [dict(copy.deepcopy(row), shot_key=f'SC0001/u01#{index}')
                                     for index in range(1, 7)]
        trace['unit_organization']['units'][0]['duration_seconds'] = 24
        trace.pop('timing_distribution_basis')
        path.write_text(json.dumps(trace, ensure_ascii=False), encoding='utf-8')
        return path

    def test_all_statistical_risks_are_review_warnings_for_v3(self):
        path = self.repeated_trace()
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertEqual(report['errors'], [])
        self.assertEqual({item['code'] for item in report['warnings']}, {
            'AUTHORING_UNIFORM_TIMING', 'AUTHORING_UNIFORM_TRANSITION',
            'AUTHORING_UNIFORM_SOUND', 'AUTHORING_TEMPLATE_REUSE',
        })
        gate.require(report)

    def test_statistical_warnings_do_not_override_hard_camera_error_for_v3(self):
        path = self.repeated_trace()
        shot = self.units[0]['shots'][0]
        shot['text'] = shot['text'].replace('抬头，', '镜头平滑跟随后在动作结果处停稳；抬头，')
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('AUTHORING_GENERIC_CAMERA', {item['code'] for item in report['errors']})
        self.assertIn('AUTHORING_UNIFORM_TIMING', {item['code'] for item in report['warnings']})
        with self.assertRaises(gate.AuthoringGateError):
            gate.require(report)

    def test_meta_prompt_is_blocked(self):
        self.units[0]['shots'][0]['text'] = self.units[0]['shots'][0]['text'].replace(
            '抬头，', '不显示系统文字或面板；抬头，')
        path = self.write_trace()
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
        self.assertIn('AUTHORING_META_PROMPT', {item['code'] for item in report['errors']})

    def test_stale_trace_coverage_still_blocks(self):
        self.units[0]['shots'] = [dict(self.units[0]['shots'][0], start=0, end=4),
                                  dict(self.units[0]['shots'][0], start=4, end=8)]
        self.units[0]['span'] = [0, 8]
        path = self.write_trace()
        # Coverage is intentionally stale; the gate must fail closed before
        # any uniform-timing claim can be trusted.
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
        self.assertEqual(report['status'], 'blocked')

    def test_short_unit_requires_organization_proof(self):
        path = self.write_trace()
        trace = json.loads(path.read_text(encoding='utf-8'))
        trace.pop('unit_organization')
        path.write_text(json.dumps(trace, ensure_ascii=False), encoding='utf-8')
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
        self.assertIn('AUTHORING_UNIT_ORGANIZATION', {item['code'] for item in report['errors']})

    def test_candidate_short_unit_requires_incompatibility_reason(self):
        second = dict(self.units[0])
        second['draft_key'] = 'SC0001/u02'
        second['span'] = [4, 8]
        second['duration'] = 4
        second['shots'] = [dict(second['shots'][0], start=0, end=4)]
        self.units.append(second)
        path = self.write_trace()
        trace = json.loads(path.read_text(encoding='utf-8'))
        trace['unit_organization']['units'].append({
            'draft_key': 'SC0001/u02',
            'duration_seconds': 4,
            'decision': 'short_exception',
            'reason_code': 'selected_range_end',
            'reason': '测试范围结束。',
        })
        path.write_text(json.dumps(trace, ensure_ascii=False), encoding='utf-8')
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
        codes = {item['code'] for item in report['errors']}
        self.assertIn('AUTHORING_UNIT_CANDIDATE_REVIEW', codes)


if __name__ == '__main__':
    unittest.main()
