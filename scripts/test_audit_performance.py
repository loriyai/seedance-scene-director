"""Behavioral checks for optional speech direction and unchanged script identity."""
import copy
import re
import unittest

import director as d
import dialogue_layout as dl
import test_audit_director as fixtures


class PerformanceDirectionChecks(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AuditDirectorChecks()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = self.fixture.state

    def sample(self, text='我知道。', voice='说'):
        label = '陈默（低声）' if voice == '说' else f'陈默（{voice}）'
        script = f'场景1-日-内-房间\n人物：陈默-基础形象；\n{label}：{text}\n'
        self.fixture.script = script
        path = self.fixture.project / 'script.txt'
        path.write_text(script, encoding='utf-8')
        self.state['files']['script']['sha256'] = d.file_hash(path)
        records, length = d.build_records(script, self.state['ledger'])
        self.state.update(records=records, length=length)
        unit = self.fixture.unit([0, length])
        unit['short_reason'] = 'script_end'
        unit['shots'] = [unit['shots'][0]]
        unit['shots'][0].update(end=4, label='答复', purpose='听清原文答复，保持原有观察关系。',
                               text='@{CHAR0001} 保持原有朝向。')
        record = records[0]
        unit['audio'] = [dict(record=record['id'], character_id='CHAR0001', voice=voice, text=text,
                              start=0, end=1, pause=.34, speed='常速', units=len(dl.compact(text)))]
        dl.attach(unit)
        return unit

    def request(self, unit, rid='performance'):
        return dict(mode='append', request_id=rid, span=unit['span'], units=[unit])

    def validate(self, unit):
        return d.validate(self.state, self.request(unit))

    def render(self, unit, convert=None):
        value = copy.deepcopy(unit)
        value.update(id='场景1-分镜1', style='3D国漫')
        return d.render_unit(self.state, value, reference_text=convert)

    def test_plain_v3_has_no_added_direction_or_empty_brackets(self):
        unit = self.sample()
        self.validate(unit)
        rendered = self.render(unit)
        self.assertIn('说：“我知道。”', rendered)
        self.assertNotIn('语速：', rendered)
        self.assertNotIn('（）', rendered)

    def test_direction_is_outside_original_quote_and_preserves_timing(self):
        unit = self.sample()
        original = copy.deepcopy(unit)
        unit['shots'][0]['dialogue_parts'][0]['delivery'] = {
            'volume': '压低音量', 'ending': '平稳收住', 'articulation': '清楚连贯'}
        self.assertEqual(self.validate(unit), self.validate(original))
        rendered = self.render(unit)
        self.assertIn('说（音量：压低音量；尾音：平稳收住；咬字：清楚连贯）：“我知道。”', rendered)
        self.assertNotIn('语速：', rendered)
        self.assertEqual(re.findall('：“(.*?)”', rendered), ['我知道。'])
        self.assertEqual(unit['audio'], original['audio'])
        self.assertEqual(unit['duration'], original['duration'])

    def test_attach_carries_direction_without_adding_pause_or_rewriting_text(self):
        unit = self.sample()
        event = unit['audio'][0]
        audio = copy.deepcopy(event)
        plan = {event['record']: [dict(text=event['text'], start=0, end=1,
                                      delivery={'texture': '清晰自然'})]}
        dl.attach(unit, plan)
        self.validate(unit)
        self.assertEqual(event, audio)
        self.assertEqual(unit['shots'][0]['dialogue_parts'][0]['delivery'], {'texture': '清晰自然'})
        self.assertIn('发声质感：清晰自然', self.render(unit))

    def test_speed_changes_use_event_source_and_still_trigger_timing_review(self):
        unit = self.sample()
        unit['shots'][0]['dialogue_parts'][0]['delivery'] = {'volume': '轻声'}
        unit['audio'][0]['speed'] = '情绪慢速'
        rendered = self.render(unit)
        self.assertIn('音量：轻声', rendered)
        self.assertNotIn('语速：', rendered)
        self.assertTrue(any(w['kind'] == '估计净语速超档' for w in self.validate(unit)))

    def test_duplicate_pace_pause_and_visual_fields_are_rejected(self):
        for field in ('speed', 'pace', 'rate', 'pause', 'mouth', 'text', 'character_id'):
            with self.subTest(field=field):
                unit = self.sample()
                unit['shots'][0]['dialogue_parts'][0]['delivery'] = {field: '正常'}
                with self.assertRaisesRegex(ValueError, 'delivery只允许'):
                    self.validate(unit)

    def test_malformed_cues_cannot_change_quote_or_reference_structure(self):
        for value in (None, {}, [], '低声', {'volume': ''}, {'volume': 3}, {'volume': '同前'},
                      {'volume': '低声\n新增一行'}, {'volume': '@{CHAR0002}低声'},
                      {'volume': '低声：“新增台词”'}, {'volume': '低声）'}, {'volume': 'quiet'}):
            with self.subTest(value=value):
                unit = self.sample()
                unit['shots'][0]['dialogue_parts'][0]['delivery'] = value
                with self.assertRaisesRegex(ValueError, 'delivery'):
                    self.validate(unit)

    def test_voice_types_remain_distinct_and_no_mouth_instruction_is_generated(self):
        for voice in ('内心想法', '画外音', '旁白'):
            with self.subTest(voice=voice):
                unit = self.sample(voice=voice)
                unit['shots'][0]['dialogue_parts'][0]['delivery'] = {'texture': '清晰自然'}
                self.validate(unit)
                rendered = self.render(unit)
                self.assertIn(voice + '（发声质感：清晰自然）：“我知道。”', rendered)
                shot_text = rendered.split('·00秒-', 1)[1]
                self.assertNotIn('张嘴', shot_text)
                self.assertNotIn('嘴部', shot_text)
                self.assertNotIn('说（', shot_text)

    def test_cloud_reference_conversion_leaves_quote_and_direction_untouched(self):
        unit = self.sample(text='@陈默，我知道。')
        part = unit['shots'][0]['dialogue_parts'][0]
        part['delivery'] = {'volume': '轻声'}
        unit['audio'][0]['end'] = part['end'] = 2.0
        self.validate(unit)
        converted = []
        def convert(value):
            converted.append(value)
            return value.replace('@陈默-基础形象 ', '<ROLE:actual-node>')
        rendered = self.render(unit, convert)
        self.assertIn('<ROLE:actual-node>说（音量：轻声）', rendered)
        self.assertIn('：“@陈默，我知道。”', rendered)
        self.assertFalse(any('我知道' in value or '轻声' in value for value in converted))

    def test_long_speech_has_local_cues_and_original_semantic_chunks(self):
        text = '我会把事情的来龙去脉完整告诉你，请你先听我说完。'
        unit = self.sample(text)
        unit['duration'] = 8
        first = unit['shots'][0]
        first['end'] = 5
        unit['shots'].append(dict(copy.deepcopy(first), start=5, end=8, label='承诺'))
        event = unit['audio'][0]
        event.update(end=6, pause=.48)
        plans = {event['record']: [
            {'text': '我会把事情的来龙去脉完整告诉你，', 'start': 0, 'end': 5,
             'delivery': {'volume': '自然音量'}},
            {'text': '请你先听我说完。', 'start': 5, 'end': 6,
             'delivery': {'volume': '自然音量', 'ending': '平稳收住'}}]}
        dl.attach(unit, plans)
        self.validate(unit)
        rendered = self.render(unit)
        self.assertEqual(''.join(re.findall('：“(.*?)”', rendered)), text)
        self.assertNotIn('语速：', rendered)
        self.assertEqual(rendered.count('音量：自然音量'), 2)
        self.assertEqual(unit['audio'][0]['pause'], .48)
        self.assertNotIn('继续说', rendered)

    def test_direction_cannot_bypass_original_quote_or_semantic_cut_validation(self):
        unit = self.sample()
        part = unit['shots'][0]['dialogue_parts'][0]
        part.update(delivery={'ending': '平稳收住'}, text='我都知道。')
        with self.assertRaisesRegex(ValueError, '改词|差异'):
            self.validate(unit)
        unit = self.sample('那宝宝就是想在床上了。')
        unit['audio'][0].update(end=4, pause=.34)
        unit['shots'][0]['end'] = 2
        unit['shots'].append(dict(copy.deepcopy(unit['shots'][0]), start=2, end=4))
        with self.assertRaisesRegex(ValueError, '词句中间'):
            dl.attach(unit, {unit['audio'][0]['record']: [
                dict(text='那宝宝就是想在床上', start=0, end=2, delivery={'volume': '自然音量'}),
                dict(text='了。', start=2, end=4, delivery={'volume': '自然音量'})]})

    def test_legacy_renders_ignore_new_cues_and_new_validation_rejects_old_format(self):
        unit = self.sample()
        unit.pop('continuity')
        unit.pop('speech_render_policy')
        for shot in unit['shots']:
            shot.pop('continuity')
        for version in ('reference-v1', 'reference-v2'):
            with self.subTest(version=version):
                unit['output_format'] = version
                baseline = self.render(unit)
                unit['shots'][0]['dialogue_parts'][0]['delivery'] = {'volume': '轻声'}
                self.assertEqual(self.render(unit), baseline)
                with self.assertRaisesRegex(ValueError, 'delivery仅用于'):
                    dl.validate(unit)
                unit['shots'][0]['dialogue_parts'][0].pop('delivery')

    def test_commit_replay_and_receipt_render_preserve_direction(self):
        unit = self.sample()
        unit['shots'][0]['dialogue_parts'][0]['delivery'] = {'volume': '轻声'}
        request = self.request(unit)
        output = self.fixture.project / 'outputs' / 'directed.txt'
        result = self.fixture.commit(request, output)
        self.assertEqual(result['status'], 'committed')
        initial = output.read_bytes()
        second = self.fixture.commit(request, self.fixture.project / 'outputs' / 'unused.txt')
        self.assertEqual(second['status'], 'already_committed')
        self.assertEqual(output.read_bytes(), initial)
        self.assertIn('音量：轻声', initial.decode('utf-8'))
        self.assertNotIn('语速：', initial.decode('utf-8'))
        self.assertEqual(self.state['receipts'][0]['units'][0]['speech_render_policy'], dl.SPEECH_RENDER_POLICY)

    def test_new_request_requires_policy_before_preview_but_old_receipt_renders(self):
        unit = self.sample()
        unit['shots'][0]['dialogue_parts'][0]['delivery'] = {'volume': '轻声'}
        current_text = self.render(unit)
        unit.pop('speech_render_policy')
        before = copy.deepcopy(unit)
        with self.assertRaisesRegex(ValueError, '预览前登记speech_render_policy'):
            self.validate(unit)
        self.assertEqual(unit, before)
        expected = current_text.replace('说（音量：轻声）', '说（音量：轻声；语速：自然语速）')
        historical = copy.deepcopy(unit)
        historical.update(id='场景1-分镜1', style='3D国漫')
        output = self.fixture.project / 'outputs' / 'historical.txt'
        receipt = {'output': str(output), 'units': [historical]}
        d.render_receipt(self.state, receipt, self.fixture.project)
        self.assertEqual(output.read_text(encoding='utf-8'), expected + '\n')
        original = output.read_bytes()
        d.render_receipt(self.state, receipt, self.fixture.project)
        self.assertEqual(output.read_bytes(), original)
        self.assertNotIn('speech_render_policy', historical)


if __name__ == '__main__':
    unittest.main(verbosity=2)
