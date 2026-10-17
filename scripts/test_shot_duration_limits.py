"""Offline boundary checks; synthetic fixtures never approve production shots."""
import copy
import unittest

import authoring_gates as ag
import director as d
import review_gate as gate
import shot_review
import storyboard_preflight as pf
import test_audit_director as fixture


MOTION_WARNING = '4至6秒实际运镜需审查'


def speech_plan(windows, duration):
    chunks = ['核对当前信号。' for _ in windows]
    text = ''.join(chunks)
    state = {'ledger': {'characters': [{'id': 'C1', 'name': '测试人物', 'source_speaker': '测试人物'}]},
             'records': [{'id': 'L1', 'scene': 'SC1', 'kind': 'dialogue', 'speaker': '测试人物',
                          'voice': '说', 'text': text, 'start': 0, 'end': len(d.compact(text))}]}
    unit = {'duration': duration, 'span': [0, len(d.compact(text))], 'action_plan': [],
            'audio': [{'record': 'L1', 'character_id': 'C1', 'voice': '说', 'text': text,
                       'units': len(d.compact(text)), 'speed': '常速', 'pause': 0,
                       'start': windows[0][0], 'end': windows[-1][1]}],
            'audio_plan': [{'record': 'L1', 'parts': [
                {'text': text, 'start': start, 'end': end, 'pause': 0}
                for text, (start, end) in zip(chunks, windows)]}]}
    return state, unit


def cut_errors(windows, duration):
    state, unit = speech_plan(windows, duration)
    checked = pf.check_audio_plan(unit)
    if checked['errors']:
        raise AssertionError(checked['errors'])
    result = ag.audio_ready(state, unit, checked)
    # The fixture intentionally leaves semantic review pending; only exercise
    # numeric feasibility, without turning a structural pass into approval.
    assert any(e['code'] == 'AUDIO_SEMANTIC_PENDING' for e in result['errors'])
    return [e for e in result['errors'] if e['code'].startswith('AUDIO_CUT_')]


class ShotDurationTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.AuditDirectorChecks()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def unit(self, lengths, camera='相机沿动作路径向前推进，终点短暂稳定'):
        unit = self.fx.unit([0, 9])
        base = unit['shots'][0]
        unit['shots'] = []
        start = 0
        for number, length in enumerate(lengths, 1):
            shot = copy.deepcopy(base)
            shot.update(start=start, end=start + length, camera=camera,
                        review_id=f'S{number}')
            unit['shots'].append(shot)
            start += length
        unit.update(duration=start, count_reason='虚构边界测试按既定动作窗口排列镜头。')
        return unit

    def test_six_seconds_accepted_and_seven_rejected(self):
        unit = self.unit([6])
        warnings = d.validate_unit(self.fx.state, unit, [0, 9])
        self.assertEqual(len([w for w in warnings if w['kind'] == MOTION_WARNING]), 1)
        with self.assertRaisesRegex(ValueError, '单镜不在1～6秒'):
            d.validate_unit(self.fx.state, self.unit([7]), [0, 9])

    def test_integer_axis_and_unit_duration_remain_hard_constraints(self):
        unit = self.unit([2, 2])
        unit['shots'][0]['end'] = 2.5
        with self.assertRaisesRegex(ValueError, '单镜不在1～6秒'):
            d.validate_unit(self.fx.state, unit, [0, 9])
        for lengths in ([3], [6] * 5 + [1]):
            with self.subTest(lengths=lengths), self.assertRaisesRegex(ValueError, '单元时长不合法'):
                d.validate_unit(self.fx.state, self.unit(lengths), [0, 9])

    def test_long_unit_still_requires_eight_to_thirteen_shots(self):
        with self.assertRaisesRegex(ValueError, '8～13镜'):
            d.validate_unit(self.fx.state, self.unit([6] * 5), [0, 9])
        with self.assertRaisesRegex(ValueError, '8～13镜'):
            d.validate_unit(self.fx.state, self.unit([2] * 14), [0, 9])
        warnings = d.validate_unit(self.fx.state, self.unit([6, 4, 4, 4, 3, 3, 3, 3]), [0, 9])
        motion = [w for w in warnings if w['kind'] == MOTION_WARNING]
        self.assertEqual(len(motion), 1)
        self.assertEqual([s['shot'] for s in motion[0]['shots']], [1, 2, 3, 4])

    def test_short_fixed_shots_have_no_long_motion_diagnostic(self):
        for lengths in ([1, 3], [2, 2], [3, 3]):
            with self.subTest(lengths=lengths):
                warnings = d.validate_unit(self.fx.state, self.unit(lengths, '固定机位'), [0, 9])
                self.assertNotIn(MOTION_WARNING, {w['kind'] for w in warnings})

    def test_keyword_variants_never_automatically_approve_long_shots(self):
        for camera in ('全程固定机位', '固定机位，仅移焦', '固定机位，人物走动',
                       '运镜丝滑', '先推近再固定于落点'):
            with self.subTest(camera=camera):
                unit = self.unit([4, 5, 6], camera)
                warnings = d.validate_unit(self.fx.state, unit, [0, 9])
                motion = [w for w in warnings if w['kind'] == MOTION_WARNING]
                self.assertEqual(len(motion), 1)
                self.assertEqual([s['shot'] for s in motion[0]['shots']], [1, 2, 3])
                report = gate.pending(self.fx.state, {'units': [unit]}, motion)
                self.assertEqual(report['warning_decisions'][0]['decision'], 'pending')
                self.assertTrue(all(r['status'] == 'pending' for r in report['shot_reviews']))

    def test_boundary_scan_does_not_add_motion_rules_to_historical_neighbor(self):
        unit = self.unit([6])
        entries = [('OLD', 1, unit['shots'][0]), ('NEW', 1, unit['shots'][0])]
        warnings = d.photography_warnings(self.fx.state, entries, boundary_only=True)
        self.assertNotIn(MOTION_WARNING, {w['kind'] for w in warnings})

    def test_v2_long_fixed_cue_and_legacy_cue_are_distinct(self):
        unit = self.unit([4], '先推近再固定于落点')
        unit['review_dependencies'] = {'policy': 'shot-dependencies-v2', 'complete': True,
                                       'basis': '虚构单镜无跨镜空间依赖。', 'links': []}
        cues = shot_review.targets(self.fx.state, {'units': [unit]})[0]['review_cues']
        self.assertTrue(any('整镜固定不合格' in cue for cue in cues))
        self.assertFalse(any('不要求补运镜' in cue for cue in cues))
        unit['review_dependencies']['policy'] = 'shot-dependencies-v1'
        cues = shot_review.targets(self.fx.state, {'units': [unit]})[0]['review_cues']
        self.assertEqual(cues[0], '固定镜头：核对原有动作或保持的用途、信息读清所需时长和切点；不要求补运镜。')
        unit['review_dependencies']['policy'] = 'shot-dependencies-v2'
        unit['shots'][0]['end'] = 3
        cues = shot_review.targets(self.fx.state, {'units': [unit]})[0]['review_cues']
        self.assertIn('不要求补运镜', cues[0])


class AudioCutLimitTests(unittest.TestCase):
    def test_six_second_window_accepted_seven_rejected(self):
        self.assertEqual(cut_errors([(0, 6)], 6), [])
        self.assertIn('AUDIO_CUT_WINDOW', {e['code'] for e in cut_errors([(0, 7)], 7)})

    def test_fractional_windows_still_need_legal_integer_cuts(self):
        self.assertEqual(cut_errors([(.25, 5.75)], 6), [])
        self.assertIn('AUDIO_CUT_WINDOW', {e['code'] for e in cut_errors([(.25, 6.25)], 7)})

    def test_joint_partition_can_use_six_but_not_seven_seconds(self):
        self.assertEqual(cut_errors([(0, 2.9), (2.9, 5.8)], 6), [])
        errors = cut_errors([(0, 3.3), (3.3, 6.6)], 8)
        self.assertNotIn('AUDIO_CUT_WINDOW', {e['code'] for e in errors})
        self.assertIn('AUDIO_CUT_PARTITION', {e['code'] for e in errors})

    def test_feasible_audio_partition_preserves_long_unit_shot_count(self):
        errors = cut_errors([(n, n + 6) for n in range(0, 30, 6)], 30)
        self.assertEqual(errors[0]['code'], 'AUDIO_CUT_COUNT')
        self.assertEqual(errors[0]['feasible_counts'], [5])
        lengths = [6, 4, 4, 4, 3, 3, 3, 3]
        windows, start = [], 0
        for length in lengths:
            windows.append((start, start + length))
            start += length
        self.assertEqual(cut_errors(windows, 30), [])


if __name__ == '__main__':
    unittest.main()
