"""Optional performance notes bind existing review; they never grant approval."""
import copy
import json
import unittest

import authoring_gates as ag
import staging_preflight as staging
import test_audit_director as director_fixture
from test_authoring_gate_audit import assess, fixture_review, silent_fixture


class PerformancePlanAudioTests(unittest.TestCase):
    def test_absent_notes_preserve_legacy_payload_shape_and_readiness(self):
        state, unit = silent_fixture()
        payload = ag.audio_payload(state, unit)
        self.assertEqual(set(payload), {
            'policy', 'rules', 'audio', 'parts', 'duration', 'span', 'actions',
            'source', 'characters', 'voice_direction', 'dialogue_layout'})
        self.assertEqual(payload['actions'], unit['action_plan'])
        fixture_review(state, unit)
        self.assertEqual(assess(state, unit)['status'], 'ready')

    def test_supplied_notes_do_not_grant_semantic_approval(self):
        state, unit = silent_fixture()
        unit['performance_brief'] = '观众看清阿明推门进入，注意力从门口转向屋内。'
        unit['action_plan'][0]['performance'] = '伸手推门至足以通行，跨过门槛后放开。'
        result = assess(state, unit)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('AUDIO_SEMANTIC_PENDING', {e['code'] for e in result['errors']})
        self.assertEqual(result['semantic_template']['reviewer'], '')
        self.assertEqual(result['semantic_template']['basis'], '')

    def test_brief_changes_invalidate_same_audio_review_and_removal_restores_input(self):
        state, unit = silent_fixture()
        fixture_review(state, unit)
        original = assess(state, unit)['input_sha256']
        unit['performance_brief'] = '观众首先看见门扇打开，再跟随阿明进入。'
        added = assess(state, unit)
        self.assertNotEqual(added['input_sha256'], original)
        self.assertEqual(added['status'], 'blocked')
        fixture_review(state, unit)
        self.assertEqual(assess(state, unit)['status'], 'ready')
        unit['performance_brief'] = '观众注意阿明推门时的迟疑，进屋后停步观察。'
        changed = assess(state, unit)
        self.assertNotEqual(changed['input_sha256'], added['input_sha256'])
        self.assertEqual(changed['status'], 'blocked')
        del unit['performance_brief']
        self.assertEqual(assess(state, unit)['input_sha256'], original)

    def test_action_performance_uses_existing_action_binding_and_camera_is_independent(self):
        state, unit = silent_fixture()
        unit['performance_brief'] = '阿明打开门并进入屋内，动作转为停步观察。'
        unit['action_plan'][0]['performance'] = '推开门后松手，身体随即跨过门槛。'
        payload = ag.audio_payload(state, unit)
        self.assertEqual(payload['actions'], unit['action_plan'])
        self.assertNotIn('performance', payload)
        fixture_review(state, unit)
        before = assess(state, unit)['input_sha256']
        unit['shots'] = [{'camera': '侧面固定机位', 'dialogue_parts': []}]
        self.assertEqual(assess(state, unit)['input_sha256'], before)
        self.assertEqual(assess(state, unit)['status'], 'ready')
        unit['action_plan'][0]['performance'] = '推开门后仍扶住门扇，等待同行者通过再松手。'
        result = assess(state, unit)
        self.assertNotEqual(result['input_sha256'], before)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('AUDIO_SEMANTIC_PENDING', {e['code'] for e in result['errors']})

    def test_invalid_note_types_and_empty_values_have_exact_field_diagnostics(self):
        for invalid in (None, False, 0, {}, [], '', ' \n\t'):
            for field, code in (('performance_brief', 'PERFORMANCE_BRIEF_SCHEMA'),
                                ('action_plan[0].performance', 'ACTION_PERFORMANCE_SCHEMA')):
                with self.subTest(value=invalid, field=field):
                    state, unit = silent_fixture()
                    if field == 'performance_brief':
                        unit[field] = invalid
                    else:
                        unit['action_plan'][0]['performance'] = invalid
                    result = assess(state, unit)
                    matching = [e for e in result['errors'] if e['code'] == code]
                    self.assertEqual(len(matching), 1)
                    self.assertEqual(matching[0]['field'], field)
                    self.assertEqual(result['status'], 'blocked')


class PerformancePlanStagingTests(unittest.TestCase):
    def setUp(self):
        fixture = director_fixture.AuditDirectorChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.state = fixture.project, fixture.state
        self.unit = fixture.unit([0, 9])
        self.unit['style'] = '3D国漫'
        self.unit['action_plan'] = [{'record': self.state['records'][0]['id'],
                                    'quote': '站起', 'start': 0, 'end': 1}]
        self.request = {'mode': 'append', 'request_id': 'fictional-performance',
                        'span': [0, 9], 'units': [self.unit]}

    def add_notes(self):
        self.unit['performance_brief'] = '观众看见陈默起身把注意力移到窗外，屋内活动暂止。'
        self.unit['action_plan'][0]['performance'] = '从坐姿起身，重心稳定后转向窗外并保持站立。'

    def test_compact_preserves_notes_and_roundtrips_without_auto_rendering(self):
        before = ag.staging_material(self.state, self.unit)
        self.add_notes()
        raw = json.dumps(self.request, ensure_ascii=False).encode('utf-8')
        path = self.root / 'performance-compact.json'
        view = staging.compact(self.state, self.request, raw, path)
        path.write_text(json.dumps(view, ensure_ascii=False), encoding='utf-8')
        fields = view['units'][0]['actual']['timing_and_state']
        self.assertEqual(fields['performance_brief'], self.unit['performance_brief'])
        self.assertEqual(fields['action_plan'], self.unit['action_plan'])
        self.assertEqual(view['units'][0]['actual']['rendered'], before['rendered'])
        self.assertEqual(staging.expand(path), staging.prepare(self.state, self.request))
        self.assertEqual(view['status'], 'pending')
        self.assertEqual(view['units'][0]['staging_review']['status'], 'pending')

    def test_note_changes_invalidate_staging_binding_without_altering_rendered_prose(self):
        self.add_notes()
        before = ag.staging_material(self.state, self.unit)
        review = copy.deepcopy(ag.staging_pending(self.state, self.unit))
        review.update(status='reviewed', reviewer='fictional regression writer',
                      render_basis='虚构测试的人物站在室内，原四镜正文与摄影未修改。',
                      timing_state_basis='虚构测试四镜仍连续覆盖四秒，动作计划保留原首秒。')
        self.unit['staging_review'] = review
        ag.validate_staging(self.state, self.unit)
        self.unit['performance_brief'] = '观众主要察觉陈默转向窗外后停止室内活动。'
        after = ag.staging_material(self.state, self.unit)
        self.assertEqual(before['rendered'], after['rendered'])
        self.assertNotEqual(ag.digest(before), ag.digest(after))
        with self.assertRaises(ValueError):
            ag.validate_staging(self.state, self.unit)


if __name__ == '__main__':
    unittest.main()
