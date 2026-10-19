"""Early planning regressions using fictional sources, without model approvals."""
import copy
import contextlib
import io
import unittest
from unittest.mock import patch

import authoring_gates as ag
import storyboard_preflight as pf
import director as d
import review_gate as gate
import test_audit_director as director_fixture


def silent_fixture():
    text = '阿明推门后走进屋内'
    state = {'records': [{'id': 'L1', 'scene': 'SC1', 'kind': 'action',
                          'text': text, 'start': 0, 'end': len(text)}],
             'ledger': {'characters': []}, 'entry': {'min': 4, 'durations': []}}
    unit = {'workflow_policy': ag.POLICY, 'duration': 4, 'span': [0, len(text)],
            'audio': [], 'audio_plan': [], 'action_plan': [
                {'record': 'L1', 'quote': '推门', 'start': 0, 'end': 1}]}
    return state, unit


def assess(state, unit):
    return ag.audio_ready(state, unit, pf.check_audio_plan(unit))


def fixture_review(state, unit):
    """Synthetic evidence is confined to these tests, never production data."""
    unit['audio_preflight'] = copy.deepcopy(assess(state, unit)['semantic_template'])
    unit['audio_preflight'].update(
        reviewer='fictional regression writer',
        basis='虚构样例先推门再进屋，门扇开启占首秒，后续三秒留给过门动作。')


class AuthoringGateAuditTests(unittest.TestCase):
    def test_silent_action_needs_actual_writer_review(self):
        state, unit = silent_fixture()
        self.assertEqual(assess(state, unit)['status'], 'blocked')
        fixture_review(state, unit)
        self.assertEqual(assess(state, unit)['status'], 'ready')

    def test_silent_action_outside_source_or_time_is_rejected(self):
        for update in ({'quote': '跳上桌子'}, {'start': 3, 'end': 5},
                       {'record': 'missing'}, {'start': 2, 'end': 1}):
            state, unit = silent_fixture()
            unit['action_plan'][0].update(update)
            self.assertIn('AUDIO_ACTION_WINDOW', {e['code'] for e in assess(state, unit)['errors']})

    def test_empty_audio_cannot_hide_source_dialogue(self):
        state, unit = silent_fixture()
        state['records'][0]['kind'] = 'dialogue'
        self.assertIn('AUDIO_SOURCE_COVERAGE', {e['code'] for e in assess(state, unit)['errors']})

    def test_silent_plan_still_checks_entry_and_source(self):
        state, unit = silent_fixture()
        state['entry']['min'] = 6
        self.assertIn('AUDIO_ENTRY_DURATION', {e['code'] for e in assess(state, unit)['errors']})
        self.assertIn('AUDIO_SOURCE_UNVERIFIED', {e['code'] for e in assess({}, unit)['errors']})

    def test_silent_plan_changes_invalidate_review_but_camera_does_not(self):
        state, unit = silent_fixture()
        fixture_review(state, unit)
        unit['shots'] = [{'camera': '左侧固定机位', 'dialogue_parts': []}]
        self.assertEqual(assess(state, unit)['status'], 'ready')
        unit['action_plan'][0]['end'] = 2
        self.assertEqual(assess(state, unit)['status'], 'blocked')

    def test_missing_or_out_of_bounds_span_blocks_before_photography(self):
        for span in (None, [], [0, 0], [0, 999]):
            state, unit = silent_fixture()
            unit['span'] = span
            self.assertIn('AUDIO_SOURCE_SPAN', {e['code'] for e in assess(state, unit)['errors']})

    def test_silent_no_ordered_actions_still_binds_selected_source(self):
        state, unit = silent_fixture()
        unit['action_plan'] = []
        fixture_review(state, unit)
        self.assertEqual(assess(state, unit)['status'], 'ready')
        state['records'][0]['text'] = '阿明关门后走出屋外'
        self.assertEqual(assess(state, unit)['status'], 'blocked')

    def test_silent_v2_request_no_longer_bypasses_readiness(self):
        state, unit = silent_fixture()
        with self.assertRaisesRegex(ValueError, '声音尚不具备'):
            pf.validate(state, {'units': [unit]})

    def test_integer_cut_feasibility_allows_six_shot_long_unit(self):
        state, unit = silent_fixture()
        unit['duration'] = 30
        parts = [{'text': '甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉。',
                  'start': n * 5, 'end': (n + 1) * 5, 'pause': .4}
                 for n in range(6)]
        text = ''.join(p['text'] for p in parts)
        state['records'] = [{'id': 'D1', 'scene': 'SC1', 'kind': 'dialogue',
                             'text': text, 'start': 0, 'end': 120, 'speaker': '阿明', 'voice': '说'}]
        state['ledger']['characters'] = [{'id': 'CHAR1', 'name': '阿明-基础形象'}]
        unit.update(span=[0, 120], action_plan=[], audio_plan=[{'record': 'D1', 'parts': parts}],
                    audio=[{'record': 'D1', 'text': text, 'units': 120, 'start': 0, 'end': 30,
                            'pause': 2.4, 'speed': '常速', 'voice': '说', 'character_id': 'CHAR1'}])
        result = assess(state, unit)
        self.assertNotIn('AUDIO_CUT_COUNT', {e['code'] for e in result['errors']})
        self.assertNotIn('AUDIO_CUT_PARTITION', {e['code'] for e in result['errors']})

    def test_silent_long_plan_has_enough_legal_cuts(self):
        state, unit = silent_fixture()
        unit['duration'] = 30
        fixture_review(state, unit)
        self.assertEqual(assess(state, unit)['status'], 'ready')


class SingleWarningSourceTests(unittest.TestCase):
    def setUp(self):
        fixture = director_fixture.AuditDirectorChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.state = fixture.state
        self.state['records'] = [{'id': 'L1', 'scene': 'SC0001', 'kind': 'dialogue',
                                  'text': '请你稍等片刻。', 'start': 0, 'end': 6,
                                  'speaker': '陈默', 'voice': '说'}]
        self.state['length'] = 6
        u = fixture.unit([0, 6])
        part = {'text': '请你稍等片刻。', 'start': 0, 'end': 1, 'pause': .34}
        u.update(workflow_policy=ag.POLICY, style='3D国漫', action_plan=[],
                 dialogue_layout='semantic-v2', voice_direction='event-inheritance-v1',
                 audio_plan=[{'record': 'L1', 'parts': [part]}],
                 audio=[dict(part, record='L1', character_id='CHAR0001', voice='说', units=6, speed='常速')])
        u['shots'][0]['dialogue_parts'] = [dict(part, record='L1')]
        u['audio_preflight'] = copy.deepcopy(assess(self.state, u)['semantic_template'])
        u['audio_preflight'].update(reviewer='fictional writer', basis='虚构回归样例在一秒中急促表达请求，按该指定窗口核对片段发音与停顿。')
        for warning in u['audio_preflight']['warning_decisions']:
            warning.update(decision='accepted', basis='虚构回归样例急促说出六音节，片段窗口一秒且句末停顿零点三四秒。')
        u['staging_review'] = ag.staging_pending(self.state, u)
        u['staging_review'].update(status='reviewed', reviewer='fictional writer',
                                  render_basis='虚构样例中人物在窗边说出请求，其他镜头保持站位并有明确焦点。',
                                  timing_state_basis='四镜时间连续覆盖四秒，首镜一秒台词与后续无声保持没有交叉。')
        self.request = {'mode': 'append', 'request_id': 'fictional-warning', 'span': [0, 6], 'units': [u]}

    def test_v2_full_check_has_one_diagnostic_for_same_rate(self):
        warnings = d.validate(self.state, self.request)
        self.assertEqual(sum(w.get('code') == 'AUDIO_RATE' for w in warnings), 1)
        self.assertFalse(any(w.get('kind') in ('估计净语速超档', '片段估计净语速超档') for w in warnings))

    def test_exact_writer_decision_reused_without_passing_independent_review(self):
        warnings = d.validate(self.state, self.request)
        report = gate.pending(self.state, self.request, warnings)
        accepted = next(w for w in report['warning_decisions'] if w['warning'].get('code') == 'AUDIO_RATE')
        self.assertEqual(accepted['decision'], 'accepted')
        self.assertEqual(accepted['basis'], self.request['units'][0]['audio_preflight']['warning_decisions'][0]['basis'])
        self.assertEqual(report['status'], 'pending')
        self.assertTrue(all(r['status'] == 'pending' for r in report['shot_reviews']))

    def test_changed_warning_or_stale_plan_cannot_inherit_old_decision(self):
        warnings = d.validate(self.state, self.request)
        warning = next(w for w in warnings if w.get('code') == 'AUDIO_RATE')
        changed = dict(warning, rate=123)
        report = gate.pending(self.state, self.request, [changed])
        self.assertEqual(report['warning_decisions'][0]['decision'], 'pending')
        self.request['units'][0]['action_plan'] = [{'record': 'L1', 'quote': '请你', 'start': 1, 'end': 2}]
        report = gate.pending(self.state, self.request, [warning])
        self.assertEqual(report['warning_decisions'][0]['decision'], 'pending')


class StagingAliasTests(unittest.TestCase):
    def test_user_short_name_hashes_same_canonical_unit_as_commit(self):
        import staging_preflight
        fixture = director_fixture.AuditDirectorChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        unit = fixture.unit([0, 9])
        unit.update(id='场景1-分镜1', display_name='A02-场景1-分镜1', style='3D国漫')
        fixture.state['units'] = [unit]
        alias = copy.deepcopy(unit)
        alias['id'] = 'A02'
        request = {'mode': 'replace', 'request_id': 'fictional-alias', 'units': [alias]}
        state_path, request_path, output = [fixture.project / name for name in ('state.json', 'draft.json', 'pending.json')]
        d.write_json(state_path, fixture.state)
        d.write_json(request_path, request)
        with patch('sys.argv', ['staging_preflight.py', '--state', str(state_path),
                               '--request', str(request_path), '--output', str(output)]):
            with contextlib.redirect_stdout(io.StringIO()):
                staging_preflight.main()
        actual = d.read_json(output)['units'][0]
        self.assertEqual(actual['unit'], unit['id'])
        self.assertEqual(actual['staging_review'], ag.staging_pending(fixture.state, unit))
        self.assertEqual(d.read_json(request_path)['units'][0]['id'], 'A02')


if __name__ == '__main__':
    unittest.main()
