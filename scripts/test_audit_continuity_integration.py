"""Public request/commit checks on a fictional period-drama scene, offline."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import director as d


class ContinuityRequestChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.script = self.project / 'script.txt'
        self.script.write_text(
            '场景1-日-内-宫殿\n人物：沈砚-基础形象；侍女-基础形象；\n'
            '△ 沈砚站在屏风旁，侍女立在门边。\n'
            '△ 沈砚看向侍女。\n'
            '△ 侍女转身离开。\n', encoding='utf-8')
        ledger = {
            'characters': [{'id': 'CHAR0001', 'name': '沈砚-基础形象'},
                           {'id': 'CHAR0002', 'name': '侍女-基础形象'}],
            'props': [], 'scene_assets': [{'id': 'SET0001', 'name': '宫殿'}],
            'scenes': [{'id': 'SC0001', 'number': 1, 'heading': '场景1-日-内-宫殿',
                        'scene_asset_id': 'SET0001', 'character_ids': ['CHAR0001', 'CHAR0002'],
                        'prop_ids': []}]}
        records, length = d.build_records(self.script.read_text(encoding='utf-8'), ledger)
        self.state = {'ledger': ledger, 'records': records, 'length': length,
                      'entry': {'min': 4, 'durations': []}, 'style_hash': d.sha('国漫'),
                      'files': {'script': {'path': str(self.script)}},
                      'units': [], 'ranges': [], 'receipts': [], 'next_numbers': {}, 'revision': 0}
        (self.project / 'video-style.txt').write_text('国漫\n', encoding='utf-8')

    def unit(self, record_index):
        record = self.state['records'][record_index]
        subjects = [['CHAR0001', 'CHAR0002'], ['CHAR0001'], ['CHAR0002']][record_index]
        unit = {'span': [record['start'], record['end']], 'output_format': 'reference-v3',
                'duration': 4, 'main_scene': 'SC0001', 'scene_ids': ['SC0001'],
                'character_ids': subjects.copy(), 'prop_ids': [], 'time': '白天',
                'weather': '未明示', 'global': f'@{{{subjects[0]}}} 保持原文起始位置。',
                'start_state': '沈砚在屏风旁，侍女在门边。', 'end_state': '承接当前原文动作。',
                'short_reason': 'natural_beat', 'short_basis': '当前原文动作形成完整叙事落点。',
                'style': '国漫', 'audio': [], 'shots': [
                    {'start': 0, 'end': 4, 'scene_id': 'SC0001', 'framing': '中景',
                     'angle': '斜侧', 'subjects': subjects.copy(), 'focus': [subjects[0]],
                     'label': '既有场面', 'transition': '硬切',
                     'purpose': '中景说明当前人物位置与原动作结果。',
                     'camera': '沿已建立关系轴斜侧平视固定',
                     'composition': '屏风与门口保持既有空间关系',
                     'depth_of_field': '中等景深保留必要的空间参照',
                     'lighting': '宫殿内既有日光方向保持不变',
                     'text': '、'.join(f'@{{{a}}}' for a in subjects) + ' 完成原文动作。',
                     'dialogue_parts': []}]}
        initial = {
            'scholar': {'present': True, 'asset_id': 'CHAR0001', 'facts': {'position': '屏风旁'}},
            'attendant': {'present': True, 'asset_id': 'CHAR0002', 'facts': {'position': '门边'}}}
        unit['continuity'] = {
            'version': 1, 'boundary': {'kind': 'continuous'},
            'entities': {'scholar': {'asset_ids': ['CHAR0001']},
                         'attendant': {'asset_ids': ['CHAR0002']}},
            'initial': initial, 'final': copy.deepcopy(initial)}
        unit['shots'][0]['continuity'] = {
            'changes': [], 'visibility': {
                entity: {'mode': 'visible' if value['asset_id'] in subjects else 'offscreen',
                         'basis': '本镜取景内可见' if value['asset_id'] in subjects else '当前取景未覆盖其既有位置'}
                for entity, value in initial.items()}}
        if record_index == 2:
            unit['shots'][0]['continuity']['changes'] = [
                {'entity': 'attendant', 'set': {'present': False},
                 'evidence': [{'record': record['id'], 'quote': '侍女转身离开'}]}]
            unit['continuity']['final']['attendant']['present'] = False
        return unit

    def request(self, units):
        return {'mode': 'append', 'request_id': 'fictional-continuity',
                'span': [units[0]['span'][0], units[-1]['span'][1]], 'units': units}

    def test_new_request_requires_contract_without_modifying_input(self):
        unit = self.unit(0)
        unit.pop('continuity')
        request = self.request([unit])
        before = copy.deepcopy(request)
        with self.assertRaisesRegex(ValueError, 'continuity'):
            d.validate(self.state, request)
        self.assertEqual(request, before)

    def test_period_scene_allows_offscreen_presence_and_visible_exit(self):
        request = self.request([self.unit(i) for i in range(3)])
        before = copy.deepcopy(request)
        d.validate(self.state, request)
        self.assertEqual(request, before)
        # A voice/appearance roster is not a census of all offscreen people.
        self.assertNotIn('CHAR0002', request['units'][1]['character_ids'])
        self.assertTrue(request['units'][1]['continuity']['final']['attendant']['present'])

    def test_partial_checks_only_finished_units_then_accepts_complete_chain(self):
        units = [self.unit(i) for i in range(3)]
        request = self.request(units)
        partial = dict(request, units=units[:1])
        d.validate(self.state, partial, partial=True)
        d.validate(self.state, request)

    def test_camera_cut_cannot_turn_existing_person_into_absent(self):
        unit = self.unit(0)
        first = unit['shots'][0]
        second = copy.deepcopy(first)
        first['end'] = 2
        second.update(start=2, end=4, subjects=['CHAR0001'])
        second['continuity']['visibility']['attendant']['mode'] = 'absent'
        unit['shots'].append(second)
        with self.assertRaisesRegex(ValueError, '在场实体不能标为absent'):
            d.validate(self.state, self.request([unit]))

    def test_replace_validates_unchanged_successor_boundary(self):
        old_units = [self.unit(i) for i in range(2)]
        for i, unit in enumerate(old_units, 1):
            unit.update(id=f'场景1-分镜{i}', batch_id='old')
        self.state.update(units=old_units, ranges=[{'span': [0, old_units[-1]['span'][1]], 'batch_id': 'old'}])
        changed = copy.deepcopy(old_units[0])
        for field in ('initial', 'final'):
            changed['continuity'][field]['attendant']['facts']['position'] = '屏风后'
        request = {'mode': 'replace', 'request_id': 'replace-first', 'units': [changed]}
        with self.assertRaisesRegex(ValueError, '承接状态不一致'):
            d.validate(self.state, request)

    def test_legacy_neighbour_is_preserved_with_explicit_review_warning(self):
        old = self.unit(0)
        old.update(id='场景1-分镜1', batch_id='old')
        old.pop('continuity')
        old['shots'][0].pop('continuity')
        self.state.update(units=[old], ranges=[{'span': old['span'], 'batch_id': 'old'}])
        before = copy.deepcopy(old)
        warnings = d.validate(self.state, self.request([self.unit(1)]))
        self.assertTrue(any(w['kind'] == '旧单元连续性边界需人工核对' for w in warnings))
        self.assertEqual(old, before)

    def test_metadata_never_changes_rendered_prompt_or_canvas_reference_text(self):
        unit = self.unit(0)
        unit['id'] = '场景1-分镜1'
        old = copy.deepcopy(unit)
        old.pop('continuity')
        for shot in old['shots']:
            shot.pop('continuity')
        self.assertEqual(d.render_unit(self.state, unit), d.render_unit(self.state, old))
        convert = lambda value: value.replace('@沈砚-基础形象 ', '<same-node>')
        self.assertEqual(d.render_unit(self.state, unit, convert), d.render_unit(self.state, old, convert))

    def test_rejected_commit_writes_no_session_or_output(self):
        unit = self.unit(0)
        unit['continuity']['final']['attendant']['present'] = False
        request_path, output = self.project / 'request.json', self.project / 'output.txt'
        d.write_json(request_path, self.request([unit]))
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, 'final'):
            d.commit(SimpleNamespace(project=self.project, request=request_path, output=output), self.state)
        self.assertEqual(self.state, before)
        self.assertFalse(output.exists())
        self.assertFalse((self.project / 'session.json').exists())

    def test_successful_commit_persists_contract_and_retries_without_duplicate_units(self):
        request_path, output = self.project / 'request.json', self.project / 'output.txt'
        request = self.request([self.unit(i) for i in range(3)])
        d.write_json(request_path, request)
        args = SimpleNamespace(project=self.project, request=request_path, output=output)
        result = d.commit(args, self.state)
        self.assertEqual(result['status'], 'committed')
        saved = d.read_json(self.project / 'session.json')
        self.assertEqual(len(saved['units']), 3)
        self.assertFalse(saved['units'][-1]['continuity']['final']['attendant']['present'])
        original = output.read_bytes()
        self.assertNotIn(b'continuity', original)
        self.assertEqual(d.commit(args, saved)['status'], 'already_committed')
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(len(saved['units']), 3)


if __name__ == '__main__':
    unittest.main()
