"""Voice-event inheritance, explicit transitions, and legacy rendering contracts."""
import copy
import unittest

import dialogue_layout as dl


class VoiceInheritanceChecks(unittest.TestCase):
    def sample(self, *, modern=True, current=True, voice='说', speed='常速'):
        chunks = ['我先告诉你，', '这件事情很复杂，', '请听我说完。']
        event = dict(record='R1', character_id='CHAR0001', voice=voice,
                     text=''.join(chunks), start=0, end=6, pause=.62,
                     units=len(dl.compact(''.join(chunks))), speed=speed)
        unit = dict(output_format='reference-v3', dialogue_layout=dl.POLICY,
                    duration=6, audio=[event], shots=[])
        if modern:
            unit['voice_direction'] = dl.VOICE_POLICY
        if current:
            unit['speech_render_policy'] = dl.SPEECH_RENDER_POLICY
        for index, text in enumerate(chunks):
            unit['shots'].append(dict(start=index * 2, end=(index + 1) * 2,
                                     dialogue_parts=[dict(record='R1', text=text,
                                                          start=index * 2, end=(index + 1) * 2)]))
        return unit

    def parts(self, unit):
        return [part for shot in unit['shots'] for part in shot['dialogue_parts']]

    def rendered(self, unit):
        return [dl.delivery_text(part, unit['audio'][0], unit) for part in self.parts(unit)]

    def test_whole_event_basis_survives_all_shots_for_every_voice_kind(self):
        for voice in ('说', '内心想法', 'OS', '画外音', '旁白'):
            with self.subTest(voice=voice):
                unit = self.sample(voice=voice)
                unit['audio'][0]['delivery'] = {'articulation': '略显吞吐'}
                before = copy.deepcopy(unit)
                dl.validate(unit)
                self.assertEqual(self.rendered(unit), ['（咬字：略显吞吐）'] * 3)
                self.assertEqual(unit, before)
                self.assertEqual(''.join(part['text'] for part in self.parts(unit)), unit['audio'][0]['text'])
                self.assertTrue(all('嘴' not in text and '回响' not in text for text in self.rendered(unit)))

    def test_override_merges_and_persists_until_explicit_change(self):
        unit = self.sample()
        unit['audio'][0]['delivery'] = {'volume': '轻声', 'texture': '原有沙哑声线'}
        self.parts(unit)[1].update(delivery={'volume': '提高音量'},
                                  transition_basis='原文在第二分句明确要求提高音量')
        dl.validate(unit)
        output = self.rendered(unit)
        self.assertIn('音量：轻声', output[0])
        for text in output[1:]:
            self.assertIn('音量：提高音量', text)
            self.assertIn('发声质感：原有沙哑声线', text)
            self.assertNotIn('原文', text)
        self.assertNotIn('提高音量', output[0])

    def test_explicit_reset_clears_special_cues_and_persists(self):
        unit = self.sample()
        unit['audio'][0]['delivery'] = {'articulation': '略显吞吐'}
        self.parts(unit)[1].update(delivery_reset=True,
                                  transition_basis='原文在第二分句明确恢复流畅表达')
        dl.validate(unit)
        self.assertEqual(self.rendered(unit), ['（咬字：略显吞吐）', '', ''])

    def test_estimation_speed_never_adds_a_cue_before_or_after_reset(self):
        for speed, label in (('快节奏', '较快语速'), ('情绪慢速', '放缓语速')):
            with self.subTest(speed=speed):
                unit = self.sample(speed=speed)
                dl.validate(unit)
                self.assertEqual(self.rendered(unit), ['', '', ''])
                unit['audio'][0]['delivery'] = {'volume': '轻声'}
                self.parts(unit)[1].update(delivery_reset=True,
                                          transition_basis='原文在第二分句明确恢复普通音量')
                dl.validate(unit)
                self.assertEqual(self.rendered(unit), ['（音量：轻声）', '', ''])

    def test_neutral_speech_remains_unannotated_without_warning(self):
        unit = self.sample()
        dl.validate(unit)
        self.assertEqual(self.rendered(unit), ['', '', ''])
        self.assertEqual(dl.voice_warnings(unit), [])

    def test_legacy_v3_render_is_byte_identical_including_no_delivery_speed(self):
        unit = self.sample(modern=False, current=False, speed='快节奏')
        parts = self.parts(unit)
        parts[0]['delivery'] = {'volume': '轻声'}
        dl.validate(unit)
        expected = ['（音量：轻声；语速：较快语速）', '', '']
        self.assertEqual(self.rendered(unit), expected)
        self.assertEqual([dl.delivery_text(part, unit['audio'][0]) for part in parts], expected)
        self.assertEqual(dl.voice_warnings(unit)[0]['kind'], '跨镜声音表现可能漏续')
        self.assertEqual(self.rendered(unit), expected)

    def test_legacy_warnings_distinguish_neutral_consistent_and_changed(self):
        unit = self.sample(modern=False)
        self.assertEqual(dl.voice_warnings(unit), [])
        for part in self.parts(unit):
            part['delivery'] = {'volume': '轻声'}
        self.assertEqual(dl.voice_warnings(unit), [])
        self.parts(unit)[1]['delivery'] = {'volume': '提高音量'}
        self.assertEqual(dl.voice_warnings(unit)[0]['kind'], '跨镜声音变化待复核')

    def test_inheritance_does_not_leak_between_sound_events(self):
        unit = self.sample()
        unit['audio'][0]['delivery'] = {'volume': '轻声'}
        second = dict(unit['audio'][0], record='R2', text='我知道了。', start=6, end=8, units=4)
        second.pop('delivery')
        unit['audio'].append(second)
        part = dict(record='R2', text=second['text'], start=6, end=8)
        unit['shots'].append(dict(start=6, end=8, dialogue_parts=[part]))
        unit['duration'] = 8
        dl.validate(unit)
        self.assertEqual(dl.delivery_text(part, second, unit), '')

    def test_attach_retains_baseline_and_timing_without_adding_audio_dependency(self):
        unit = self.sample()
        event = unit['audio'][0]
        event['delivery'] = {'texture': '原有沙哑声线'}
        plans = {'R1': copy.deepcopy(self.parts(unit))}
        before = copy.deepcopy(event)
        dl.attach(unit, plans)
        self.assertEqual(unit['audio'][0], before)
        self.assertEqual(unit['voice_direction'], dl.VOICE_POLICY)
        self.assertEqual(self.rendered(unit), ['（发声质感：原有沙哑声线）'] * 3)
        self.assertNotIn('recording_evidence', event)

    def test_unknown_policy_and_new_fields_without_opt_in_are_rejected(self):
        unit = self.sample()
        unit['voice_direction'] = 'future-policy'
        with self.assertRaisesRegex(ValueError, 'voice_direction只支持'):
            dl.validate(unit)
        unit = self.sample(modern=False)
        unit['audio'][0]['delivery'] = {'volume': '轻声'}
        with self.assertRaisesRegex(ValueError, 'audio.delivery须显式'):
            dl.validate(unit)
        unit = self.sample(modern=False)
        self.parts(unit)[0]['delivery_reset'] = True
        with self.assertRaisesRegex(ValueError, '须启用'):
            dl.validate(unit)
        for output_format in ('reference-v1', 'reference-v2'):
            unit = self.sample()
            unit.pop('speech_render_policy')
            unit['output_format'] = output_format
            with self.assertRaisesRegex(ValueError, 'voice_direction须使用'):
                dl.validate(unit)

    def test_unversioned_inheritance_keeps_all_historical_speed_labels(self):
        for speed, label in dl.SPEED_LABELS.items():
            with self.subTest(speed=speed):
                unit = self.sample(current=False, speed=speed)
                unit['audio'][0]['delivery'] = {'volume': '轻声'}
                before = copy.deepcopy(unit)
                expected = [f'（音量：轻声；语速：{label}）'] * 3
                self.assertEqual(self.rendered(unit), expected)
                dl.validate(unit)
                self.assertEqual(self.rendered(unit), expected)
                self.assertEqual(unit, before)

    def test_preparing_explicit_revision_changes_only_rendering_policy(self):
        unit = self.sample(current=False, speed='情绪慢速')
        unit['audio'][0]['delivery'] = {'articulation': '略显吞吐'}
        before = copy.deepcopy(unit)
        self.assertEqual(self.rendered(unit), ['（语速：放缓语速；咬字：略显吞吐）'] * 3)
        self.assertIs(dl.prepare_render_policy(unit), unit)
        self.assertEqual(self.rendered(unit), ['（咬字：略显吞吐）'] * 3)
        self.assertEqual({k: v for k, v in unit.items() if k != 'speech_render_policy'}, before)
        self.assertEqual(self.rendered(before), ['（语速：放缓语速；咬字：略显吞吐）'] * 3)

    def test_unknown_render_policy_cannot_fall_back_or_be_overwritten(self):
        unit = self.sample()
        unit['speech_render_policy'] = 'future-policy'
        before = copy.deepcopy(unit)
        for operation in (lambda: dl.validate(unit), lambda: self.rendered(unit),
                          lambda: dl.prepare_render_policy(unit)):
            with self.assertRaisesRegex(ValueError, 'speech_render_policy'):
                operation()
        self.assertEqual(unit, before)

    def test_event_and_piece_delivery_share_strict_schema(self):
        invalid = (None, {}, [], '轻声', {'speed': '快节奏'}, {'mouth': '张嘴'},
                   {'volume': '轻声\n后续'}, {'volume': '同前'}, {'volume': 1},
                   {'volume': 'quiet'}, {'volume': '@人物轻声'}, {'texture': '轻声）'})
        for scope in ('event', 'part'):
            for value in invalid:
                with self.subTest(scope=scope, value=value):
                    unit = self.sample()
                    target = unit['audio'][0] if scope == 'event' else self.parts(unit)[1]
                    target['delivery'] = value
                    if scope == 'part':
                        target['transition_basis'] = '原文在第二分句明确压低了音量'
                    with self.assertRaisesRegex(ValueError, 'delivery'):
                        dl.validate(unit)

    def test_override_and_reset_require_concrete_basis_and_nonconflicting_fields(self):
        for change in ({'delivery': {'volume': '轻声'}}, {'delivery_reset': True}):
            for basis in (None, '', '同前', '自然变化', 'a source change', '原文转折\n轻声'):
                with self.subTest(change=change, basis=basis):
                    unit = self.sample()
                    self.parts(unit)[1].update(change, transition_basis=basis)
                    with self.assertRaisesRegex(ValueError, 'transition_basis'):
                        dl.validate(unit)
        for value in (False, 1, 'true'):
            unit = self.sample()
            self.parts(unit)[1].update(delivery_reset=value,
                                      transition_basis='原文在第二分句明确恢复普通音量')
            with self.assertRaisesRegex(ValueError, 'delivery_reset只能'):
                dl.validate(unit)
        unit = self.sample()
        self.parts(unit)[1].update(delivery_reset=True, delivery={'volume': '轻声'},
                                  transition_basis='原文在第二分句明确恢复普通音量')
        with self.assertRaisesRegex(ValueError, '不得同时'):
            dl.validate(unit)
        unit = self.sample()
        self.parts(unit)[1]['transition_basis'] = '原文在第二分句明确恢复普通音量'
        with self.assertRaisesRegex(ValueError, '须对应'):
            dl.validate(unit)

    def test_new_direction_cannot_mask_quote_or_semantic_cut_errors(self):
        unit = self.sample()
        unit['audio'][0]['delivery'] = {'volume': '轻声'}
        self.parts(unit)[1]['text'] = '省略这一句，'
        with self.assertRaisesRegex(ValueError, '漏字、重复或改词'):
            dl.validate(unit)
        unit = self.sample()
        unit['audio'][0]['delivery'] = {'volume': '轻声'}
        self.parts(unit)[0]['text'] = '我先告诉'
        self.parts(unit)[1]['text'] = '你，这件事情很复杂，'
        with self.assertRaisesRegex(ValueError, '词句中间'):
            dl.validate(unit)

    def test_director_render_consumes_inheritance_without_converting_quote_or_basis(self):
        import director as d
        state = {'ledger': {'schema_version': 3,
                 'characters': [{'id': 'CHAR0001', 'name': '陈默-基础形象'}], 'props': [],
                 'scene_assets': [{'id': 'SET0001', 'name': '房间'}],
                 'scenes': [{'id': 'SC0001', 'heading': '场景1-日-内-房间',
                             'scene_asset_id': 'SET0001', 'prop_ids': []}]}}
        unit = self.sample(voice='内心想法')
        unit.update(id='场景1-分镜1', style='3D国漫', time='日', weather='室内',
                    character_ids=['CHAR0001'], scene_ids=['SC0001'], prop_ids=[])
        unit['global'] = '@{CHAR0001} 站在房内。'
        unit['audio'][0]['delivery'] = {'articulation': '略显吞吐'}
        for shot in unit['shots']:
            shot.update(scene_id='SC0001', focus=['CHAR0001'], label='听清原有想法',
                        transition='直接切换', framing='中景', text='@{CHAR0001} 保持站姿。',
                        camera='机位固定', composition='人物在中央', depth_of_field='人物清晰',
                        lighting='室内原有光照')
        converted = []
        def convert(text):
            converted.append(text)
            return text.replace('@陈默-基础形象 ', '<ROLE:real-node>')
        before = copy.deepcopy(unit)
        rendered = d.render_unit(state, unit, reference_text=convert)
        self.assertEqual(rendered.count('内心想法（咬字：略显吞吐）'), 3)
        self.assertIn('<ROLE:real-node>内心想法', rendered)
        self.assertTrue(all('略显吞吐' not in text and '请听我说完' not in text for text in converted))
        self.assertEqual(unit, before)
        unit.pop('voice_direction')
        unit['audio'][0].pop('delivery')
        unit['audio'][0]['speed'] = '快节奏'
        legacy = d.render_unit(state, unit)
        self.assertNotIn('语速：', legacy)
        self.assertEqual(legacy, d.render_unit(state, copy.deepcopy(unit)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
