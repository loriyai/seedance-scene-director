"""Source-bound spoken announcements retain text/timing without a CHAR asset."""
import copy
import tempfile
import unittest
from pathlib import Path

import director as d
import audio_sources
import storyboard_preflight as pf
from test_audit_director import static_fixture_continuity


class NonCharacterAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'script.txt'
        text = '场景1-日-内-房间\n人物：无；\n△ 房间空无一人。\n电话提示音(非人物播报)：您拨打的号码暂时无法接通。\n'
        self.path.write_text(text, encoding='utf-8')
        self.ledger = {'schema_version': 3, 'characters': [], 'props': [],
                       'scene_assets': [{'id': 'SET0001', 'name': '房间'}],
                       'scenes': [{'id': 'SC0001', 'number': 1, 'heading': '场景1-日-内-房间',
                                   'scene_asset_id': 'SET0001', 'character_ids': [], 'prop_ids': []}]}
        records, length = d.build_records(text, self.ledger)
        self.state = {'ledger': self.ledger, 'records': records, 'length': length,
                      'entry': {'min': 4, 'durations': []}, 'files': {'script': {'path': str(self.path)}}}
        event = {'record': records[-1]['id'], 'source_kind': 'non_character', 'source_label': '电话提示音',
                 'voice': '非人物播报', 'text': records[-1]['text'], 'start': 0, 'end': 3,
                 'pause': .4, 'units': 12, 'speed': '常速'}
        self.unit = {'id': '场景1-分镜1', 'span': [0, length], 'duration': 4,
                     'output_format': 'reference-v3', 'black_reference_mode': 'none-v1',
                     'dialogue_layout': 'semantic-v2', 'voice_direction': 'event-inheritance-v1',
                     'scene_ids': ['SC0001'], 'main_scene': 'SC0001', 'character_ids': [], 'prop_ids': [],
                     'time': '日', 'weather': '未明示', 'style': '3D国漫', 'global': '无人。',
                     'start_state': '空房间', 'end_state': '空房间', 'short_reason': 'range_end',
                     'audio': [event], 'shots': [{'start': 0, 'end': 4, 'scene_id': 'SC0001',
                         'subjects': [], 'focus': [], 'framing': '全景', 'angle': '客观',
                         'purpose': '在无人房间中听清电话播报，固定全景保持空间关系。',
                         'label': '电话播报', 'transition': '直接切换', 'camera': '平视固定机位',
                         'composition': '房门和桌面完整入画', 'depth_of_field': '桌面和房门清晰',
                         'lighting': '保持室内柔光', 'text': '房间保持无人状态。',
                         'environment_evidence': '房间空无一人。',
                         'dialogue_parts': [{'record': event['record'], 'text': event['text'], 'start': 0, 'end': 3}]}]}
        static_fixture_continuity(self.unit)

    def test_record_and_full_unit_accept_announcement_without_character(self):
        self.assertEqual(self.state['records'][-1]['source_kind'], 'non_character')
        before = copy.deepcopy(self.unit)
        d.validate_unit(self.state, self.unit, self.unit['span'])
        self.assertEqual(pf._source_audio(self.state, self.unit), [])
        self.assertEqual(self.unit, before)
        converted = []
        output = d.render_unit(self.state, self.unit, reference_text=lambda value: converted.append(value) or value)
        self.assertIn('人物：无；', output)
        self.assertIn('电话提示音非人物播报：“您拨打的号码暂时无法接通。”', output)
        self.assertNotIn('@电话提示音', output)
        self.assertNotIn('电话提示音', converted)

    def test_announcement_still_requires_complete_text_fragments_and_timing(self):
        for mutation in ('text', 'part', 'missing', 'timing'):
            with self.subTest(mutation=mutation):
                unit = copy.deepcopy(self.unit)
                if mutation == 'text': unit['audio'][0]['text'] = '已省略。'
                if mutation == 'part': unit['shots'][0]['dialogue_parts'][0]['text'] = '已省略。'
                if mutation == 'missing': unit['audio'] = []
                if mutation == 'timing': unit['audio'][0]['end'] = 5
                with self.assertRaises(ValueError):
                    d.validate_unit(self.state, unit, unit['span'])

    def test_invalid_source_identity_rejected_by_early_and_final_checks(self):
        for patch in ({'character_id': None}, {'character_ids': []}, {'character_id': 'CHAR0001'},
                      {'source_label': '另一台设备'}, {'source_kind': 'character'}):
            with self.subTest(patch=patch):
                unit = copy.deepcopy(self.unit)
                unit['audio'][0].update(patch)
                self.assertTrue(pf._source_audio(self.state, unit))
                with self.assertRaises(ValueError):
                    d.validate_unit(self.state, unit, unit['span'])

    def test_broadcast_is_not_inferred_from_a_name_or_normal_voice_cue(self):
        for label in ('电话提示音', '电话提示音(画外音)', '机器人(低声)'):
            records, _ = d.build_records('场景1-日-内-房间\n人物：无；\n' + label + '：你好。\n', self.ledger)
            self.assertNotIn('source_kind', records[0])
            with self.assertRaisesRegex(ValueError, '人物对白不得'):
                audio_sources.validate_source(self.unit['audio'][0], records[0])

    def test_legacy_records_and_character_rendering_are_unchanged(self):
        records, _ = d.build_records('场景1-日-内-房间\n人物：小林-基础形象；\n小林：你好。\n', self.ledger)
        self.assertEqual(set(records[0]), {'id', 'line', 'local_line', 'scene', 'kind', 'speaker', 'label', 'voice', 'text', 'start', 'end'})
        event = {'character_id': 'CHAR0001', 'voice': '说'}
        self.assertFalse(audio_sources.validate_source(event, records[0]))
        self.assertEqual(audio_sources.render_source(event, lambda cid: '@小林-基础形象 '), '@小林-基础形象 ')


if __name__ == '__main__':
    unittest.main()
