"""Offline behavior checks for purposeful shots and soft photography review."""
import copy
import unittest

import director as d
import test_audit_director as fixtures


class CameraPolicyChecks(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AuditDirectorChecks()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = self.fixture.state

    def unit(self, framings=None):
        return self.fixture.unit([0, 9], framings=framings)

    def request(self, unit):
        return {'mode': 'append', 'request_id': 'camera-policy', 'span': [0, 9], 'units': [unit]}

    def add_prop(self, unit):
        self.state['ledger']['props'].append({'id': 'PROP0001', 'name': '书'})
        self.state['ledger']['scenes'][0]['prop_ids'].append('PROP0001')
        unit['prop_ids'] = ['PROP0001']
        for shot in unit['shots']:
            shot.update(subjects=['CHAR0001', 'PROP0001'], focus=['PROP0001'],
                        text='@{CHAR0001} 递出@{PROP0001}。')

    def face_shots(self, unit, framing='近景'):
        for shot in unit['shots']:
            shot.update(framing=framing, angle='正面', face_closeup=True,
                        camera='正面平视固定机位', composition='面部位于画面左侧')

    def test_new_submission_requires_concrete_single_line_purpose(self):
        for value in (None, '', ' ', False, '同前', '同上。', '无', '电影感',
                      '推进剧情', '体现情绪', '展示人物', '；。', '观看\n动作'):
            with self.subTest(value=value):
                unit = self.unit()
                if value is None:
                    unit['shots'][0].pop('purpose')
                else:
                    unit['shots'][0]['purpose'] = value
                with self.assertRaisesRegex(ValueError, 'purpose'):
                    d.validate(self.state, self.request(unit))
                self.assertEqual(self.state['units'], [])
        unit = self.unit()
        unit['shots'][0]['purpose'] = '展示人物站起后与窗边的距离，采用全景让动作方向可见。'
        self.assertEqual(d.validate(self.state, self.request(unit)), [])

    def test_closeup_numbers_and_adjacent_pairs_are_soft_review(self):
        unit = self.unit(['特写'] * 4)
        for shot in unit['shots']:
            shot.pop('closeup_reason')
        warnings = d.validate(self.state, self.request(unit))
        kinds = {warning['kind'] for warning in warnings}
        self.assertIn('单元特写较集中需审查', kinds)
        self.assertIn('累计特写比例较高需审查', kinds)
        self.assertFalse(any('面部' in kind for kind in kinds))
        result = self.fixture.commit(self.request(unit), self.fixture.project / 'outputs' / 'close.txt')
        self.assertEqual(result['status'], 'committed')
        replay = self.fixture.commit(self.request(unit), self.fixture.project / 'outputs' / 'unused.txt')
        self.assertEqual(replay['status'], 'already_committed')

    def test_three_identical_photography_choices_warn_but_different_focus_does_not(self):
        unit = self.unit()
        for shot in unit['shots']:
            shot.update(angle='斜侧', camera='平视斜侧固定机位')
        warnings = d.validate_unit(self.state, unit, [0, 9])
        self.assertTrue(any(w['kind'] == '连续三镜摄影重复需审查' for w in warnings))
        self.add_prop(unit)
        unit['shots'][1]['focus'] = ['CHAR0001']
        self.assertFalse(any(w['kind'] == '连续三镜摄影重复需审查'
                             for w in d.validate_unit(self.state, unit, [0, 9])))

    def test_explicit_face_runs_warn_without_banning_first_shot_or_reactions(self):
        unit = self.unit()
        self.face_shots(unit)
        warnings = d.validate_unit(self.state, unit, [0, 9])
        kinds = {warning['kind'] for warning in warnings}
        self.assertIn('首镜面部近景需审查', kinds)
        self.assertIn('连续正面面部近景需审查', kinds)
        self.assertIn('同人物连续正面面部镜需审查', kinds)
        d.validate(self.state, self.request(unit))

    def test_same_character_frontal_medium_close_faces_are_included(self):
        unit = self.unit()
        self.face_shots(unit, '中近景')
        kinds = {w['kind'] for w in d.validate_unit(self.state, unit, [0, 9])}
        self.assertIn('同人物连续正面面部镜需审查', kinds)
        self.assertNotIn('连续正面面部近景需审查', kinds)

    def test_existing_explicit_face_words_are_supported_without_new_flag(self):
        unit = self.unit()
        self.face_shots(unit)
        for shot in unit['shots']:
            shot.pop('face_closeup')
        self.assertTrue(any(w['kind'] == '连续正面面部近景需审查'
                            for w in d.validate_unit(self.state, unit, [0, 9])))

    def test_frontal_character_closeup_alone_does_not_prove_face_emphasis(self):
        unit = self.unit(['近景'] * 4)
        for shot in unit['shots']:
            shot.update(angle='正面', camera='正面平视固定机位',
                        composition='手部位于画面下沿，面部不入画', text='@{CHAR0001} 双手抬起，露出微笑。')
        warnings = d.validate_unit(self.state, unit, [0, 9])
        self.assertFalse(any('面部' in w['kind'] for w in warnings))

    def test_prop_focus_is_not_treated_as_a_face_even_with_stale_face_flag(self):
        unit = self.unit(['近景'] * 4)
        self.add_prop(unit)
        for shot in unit['shots']:
            shot.update(angle='正面', face_closeup=True, composition='书的页面占画面中央，人物面部位于后景')
        self.assertFalse(any('面部' in w['kind'] for w in d.validate_unit(self.state, unit, [0, 9])))

    def test_two_different_people_frontal_close_faces_warn_but_are_allowed(self):
        unit = self.unit()
        self.face_shots(unit)
        self.state['ledger']['characters'].append({'id': 'CHAR0002', 'name': '对手-基础形象'})
        self.state['ledger']['scenes'][0]['character_ids'].append('CHAR0002')
        unit['character_ids'].append('CHAR0002')
        for shot in unit['shots'][1::2]:
            shot.update(subjects=['CHAR0002'], focus=['CHAR0002'], text='@{CHAR0002} 完成原动作。')
        warnings = d.validate_unit(self.state, unit, [0, 9])
        self.assertTrue(any(w['kind'] == '连续正面面部近景需审查' for w in warnings))
        self.assertFalse(any(w['kind'] == '同人物连续正面面部镜需审查' for w in warnings))

    def test_review_crosses_unit_and_batch_boundaries_and_preserves_rendition_links(self):
        first = self.fixture.unit([0, 9])
        second = self.fixture.unit([9, 18])
        first.update(id='场景1-分镜1', batch_id='a')
        second.update(id='场景1-分镜2', batch_id='b', predecessor=first['id'])
        self.face_shots(first, '中近景')
        self.face_shots(second, '中近景')
        warnings = d.boundary_photography_warnings(self.state, [first, second])
        self.assertTrue(any(w['kind'] == '同人物连续正面面部镜需审查' for w in warnings))
        self.assertTrue(all(len({s['unit'] for s in w['shots']}) > 1 for w in warnings))
        repeat = copy.deepcopy(first)
        repeat['id'] = '场景1-分镜3'
        repeat['batch_id'] = 'repeat'
        self.assertEqual(d.boundary_photography_warnings(self.state, [first, repeat]), [])

    def test_three_shot_review_spans_three_single_shot_units(self):
        units = []
        for index in range(3):
            unit = self.unit()
            self.face_shots(unit, '中近景')
            unit.update(id=f'场景1-分镜{index + 1}', span=[index * 9, (index + 1) * 9])
            unit['shots'] = [unit['shots'][0]]
            units.append(unit)
        self.assertTrue(any(w['kind'] == '同人物连续正面面部镜需审查'
                            for w in d.boundary_photography_warnings(self.state, units)))

    def test_old_v2_receipt_without_purpose_or_photography_fields_still_renders(self):
        unit = self.unit()
        unit['output_format'] = 'reference-v2'
        unit.update(id='场景1-分镜1', style='3D国漫')
        for shot in unit['shots']:
            shot.pop('purpose')
            for field in d.PHOTOGRAPHY_FIELDS:
                shot.pop(field)
        text = d.render_unit(self.state, unit)
        self.assertIn('场景：场景1-日-内-房间', text)
        self.assertIn('景别：', text)
        self.assertNotIn('摄影选择依据', text)
        self.assertFalse(d.photography_warnings(self.state, [('旧单元', i + 1, s) for i, s in enumerate(unit['shots'])]))

    def test_purpose_is_not_rendered_as_product_prompt_content(self):
        unit = self.unit()
        unit.update(id='场景1-分镜1', style='3D国漫')
        unit['shots'][0]['purpose'] = '仅用于后台复核的摄影依据，保留人物动作关系。'
        self.assertNotIn('仅用于后台复核', d.render_unit(self.state, unit))


if __name__ == '__main__':
    unittest.main(verbosity=2)
