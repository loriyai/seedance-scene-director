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
        self.state = {'storyboard_authoring_policy': gate.POLICY}
        self.draft = b'authored draft\n'
        self.units = [{
            'draft_key': 'SC0001/u01',
            'source_records': ['L000001'],
            'span': [0, 4],
            'shots': [{
                'start': 0, 'end': 4,
                'text': '·00秒-04秒[镜头1-窗外]：（@{CHAR0001}的中景；转场：硬切）@{CHAR0001}抬头，视线落向窗外，肩膀随呼吸轻微起伏。音效：无。',
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
            'version': gate.VERSION,
            'draft_sha256': draft_sha,
            'context_sha256': self.context,
            'source_span': [0, 4],
            'timing_distribution_basis': '本镜持续观察窗外变化，动作和观察均在镜内完成。',
            'units': [{
                'draft_key': 'SC0001/u01',
                'shots': [{
                    'shot_key': 'SC0001/u01#1',
                    'source_records': ['L000001'],
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

    def test_meta_prompt_is_blocked(self):
        self.units[0]['shots'][0]['text'] = self.units[0]['shots'][0]['text'].replace(
            '抬头，', '不显示系统文字或面板；抬头，')
        path = self.write_trace()
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
        self.assertIn('AUTHORING_META_PROMPT', {item['code'] for item in report['errors']})

    def test_uniform_duration_requires_explicit_basis(self):
        self.units[0]['shots'] = [dict(self.units[0]['shots'][0], start=0, end=4),
                                  dict(self.units[0]['shots'][0], start=4, end=8)]
        self.units[0]['span'] = [0, 8]
        path = self.write_trace()
        # Coverage is intentionally stale; the gate must fail closed before
        # any uniform-timing claim can be trusted.
        report = gate.validate(self.project, self.state, self.draft, self.units, self.context, path)
        self.assertEqual(report['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
