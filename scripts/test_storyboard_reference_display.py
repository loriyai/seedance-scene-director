"""User-visible reference placement and source-bound appearance timing."""
import copy
import json
import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import storyboard_text as st
from test_storyboard_text import fixture, renderable


def modern(unit):
    unit = renderable(unit)
    unit['reference_display_policy'] = st.REFERENCE_DISPLAY_POLICY
    return unit


class ReferenceDisplayTests(unittest.TestCase):
    def test_contextual_names_references_and_literal_dialogue(self):
        state, text = fixture('阿青@{CHAR0001}：别动。')
        state['ledger']['locations'] = [{'id': 'LOC0001', 'name': '房间'}]
        state['ledger']['scene_assets'][0].update(name='房间-夜晚', location_id='LOC0001')
        text = text.replace('@{CHAR0001}停在门边。', '@{CHAR0001}握住@{PROP0001}。')
        unit = modern(st.parse(text, state)[0])
        calls = []
        actual = st.render_unit(state, unit, lambda value: calls.append(value) or value)
        self.assertIn('人物：阿青@阿青-基础形象 ；', actual)
        self.assertIn('场景：房间@房间-夜晚 ；', actual)
        self.assertIn('人物站位：阿青站在门边', actual)
        self.assertIn('（阿青的中景', actual)
        self.assertIn('阿青握住@钥匙 ', actual)
        self.assertIn('阿青@阿青-基础形象 说', actual)
        self.assertIn('：“阿青@{CHAR0001}：别动。”', actual)
        self.assertEqual(calls.count('@阿青-基础形象 '), 2)
        self.assertFalse(any('{CHAR' in x for x in calls))

    def test_legacy_render_unchanged_without_frozen_policy(self):
        state, text = fixture()
        output = st.render_unit(state, renderable(st.parse(text, state)[0]))
        self.assertIn('人物：@阿青-基础形象 ；', output)
        self.assertIn('人物站位：@阿青-基础形象 ', output)

    def test_legacy_silent_name_uses_verified_bound_source_cast(self):
        from storyboard_reference_display import Names
        state, _ = fixture()
        state['ledger']['characters'][0].pop('source_entry')
        for record in state['records']:
            record['speaker'] = None
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'standard.txt'
            raw = '场景1-日-内-房间\n人物：阿青-基础形象；\n△ 阿青站在门边。\n'.encode('utf-8')
            source.write_bytes(raw)
            state['files'] = {'script': {'path': str(source), 'sha256': hashlib.sha256(raw).hexdigest()}}
            names = Names(state, st._context(state)[3])
            self.assertEqual(names.character('CHAR0001', 'SC0001'), '阿青')
            source.write_text('场景1-日-内-房间\n人物：错误-基础形象；\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '绑定标准剧本已变化'):
                names.character('CHAR0001', 'SC0001')
            wrong_scene = '场景2-日-内-走廊\n人物：阿青-基础形象；\n'.encode('utf-8')
            source.write_bytes(wrong_scene)
            state['files']['script']['sha256'] = hashlib.sha256(wrong_scene).hexdigest()
            with self.assertRaisesRegex(ValueError, '缺少唯一已审源人物名称'):
                names.character('CHAR0001', 'SC0001')

    def test_source_name_is_not_production_filename_or_first_hyphen(self):
        state, text = fixture()
        state['ledger']['characters'][0].update(name='制作文件-77-新版', source_speaker='阿青')
        output = st.render_unit(state, modern(st.parse(text, state)[0]))
        self.assertIn('阿青@制作文件-77-新版 ', output)
        self.assertNotIn('人物站位：制作文件', output)

    def test_prompt_only_and_non_character_keep_no_image(self):
        state, text = fixture()
        text = text.replace('人物：@{CHAR0001}；', '人物：@{CHAR0001}、@{CHAR0002}；')
        text = text.replace('人物站位：', '人物站位：@{CHAR0002}站在远处，')
        output = st.render_unit(state, modern(st.parse(text, state)[0]))
        self.assertIn('路人-群像；', output)
        self.assertNotIn('@路人', output)
        record = state['records'][1]
        record.update(speaker='电话提示音', source_kind='non_character', voice='非人物播报')
        text = text.replace('@{CHAR0001}说', '电话提示音非人物播报')
        output = st.render_unit(state, modern(st.parse(text, state)[0]))
        self.assertIn('电话提示音 非人物播报', output)
        self.assertNotIn('@电话提示音', output)

    def test_joint_each_member_has_own_reference_and_one_verbatim_quote(self):
        from test_storyboard_joint_voice import JointVoiceTests
        state, text, _, _ = JointVoiceTests().joint(3)
        output = st.render_unit(state, modern(st.parse(text, state)[0]))
        self.assertIn('阿青@阿青-基础形象 、路人-群像、阿白@阿白-基础形象 说', output)
        self.assertEqual(output.count('：“别动，等我回来。”'), 1)

    def test_sv_sender_pure_voice_does_not_create_image_reference(self):
        state, text = fixture()
        text = text.replace('声音[L000004,', '声音[SV000004,').replace('}说（', '}画外音（')
        unit = modern(st._parse_units(text, state)[0])
        unit['source_text_voices'] = [{'event_id': 'SV000004', 'speaker': 'CHAR0001'}]
        with patch('source_text_voices.voice_only_ids', return_value={'CHAR0001'}):
            output = st.render_unit(state, unit)
        self.assertIn('阿青 画外音', output)
        self.assertNotIn('@阿青', output)

    def test_sv_announcer_stays_non_character(self):
        state, text = fixture()
        text = text.replace('声音[L000004,', '声音[SV000004,').replace('@{CHAR0001}说（', '女播音腔画外音（')
        unit = modern(st._parse_units(text, state)[0])
        unit['source_text_voices'] = [{'event_id': 'SV000004', 'speaker': 'female_announcer'}]
        with patch('source_text_voices.voice_only_ids', return_value=set()):
            output = st.render_unit(state, unit)
        self.assertIn('女播音腔 画外音', output)
        self.assertNotIn('@女播音腔', output)

    def changed(self, at=1.5):
        state, text = fixture()
        state['ledger']['characters'].append({'id': 'CHAR0004', 'name': '阿青-戴面具',
            'source_entry': '阿青-基础形象', 'source_speaker': '阿青',
            'appearance_variant': {'base_asset_id': 'CHAR0001'}})
        scene = state['ledger']['scenes'][0]
        scene['character_appearances'] = [{'base_asset_id': 'CHAR0001', 'initial_asset_id': 'CHAR0001',
            'transitions': [{'event_id': 'wear', 'from_asset_id': 'CHAR0001', 'to_asset_id': 'CHAR0004',
                             'evidence': state['records'][0]['text']}]}]
        declaration = {'scene_id': 'SC0001', 'base_asset_id': 'CHAR0001', 'events': [
            {'event_id': 'wear', 'record_id': state['records'][0]['id'],
             'quote': state['records'][0]['text'], 'at': at}]}
        text = text.replace('人物：@{CHAR0001}；', '人物：@{CHAR0001}、@{CHAR0004}；')
        lines = text.splitlines()
        lines.insert(4, '形象状态：' + json.dumps(declaration, ensure_ascii=False))
        lines[-1] = lines[-1].replace('CHAR0001', 'CHAR0004')
        return state, '\n'.join(lines) + '\n'

    def test_same_person_groups_and_cross_change_voice_stays_initial(self):
        state, text = self.changed()
        unit = modern(st.parse(text, state)[0])
        st.check_reference_policy(state, [unit])
        output = st.render_unit(state, unit)
        self.assertIn('阿青@阿青-基础形象 （起始） → 阿青@阿青-戴面具 （1.5秒起）', output)
        self.assertIn('阿青@阿青-基础形象 说', output)
        self.assertNotIn('形象状态', output)
        self.assertNotIn('CHAR0004', output)
        self.assertIn('（阿青的近景', output)

    def test_future_image_at_speech_start_rejected(self):
        state, text = self.changed()
        text = text.replace(']：@{CHAR0001}说', ']：@{CHAR0004}说')
        with self.assertRaisesRegex(ValueError, '片段开始时已生效'):
            st.check_reference_policy(state, [modern(st.parse(text, state)[0])])

    def test_old_image_after_speech_start_rejected(self):
        state, text = self.changed(at=0.1)
        with self.assertRaisesRegex(ValueError, '片段开始时已生效'):
            st.check_reference_policy(state, [modern(st.parse(text, state)[0])])

    def test_unrelated_action_cannot_be_bound_as_change(self):
        state, text = self.changed()
        text = text.replace('"record_id": "L000003", "quote": "阿青站在门边。"',
                            '"record_id": "L000005", "quote": "阿青看向窗外。"')
        with self.assertRaisesRegex(ValueError, '已审事件evidence'):
            st.check_reference_policy(state, [modern(st.parse(text, state)[0])])

    def test_missing_completion_anchor_rejected_for_current_change(self):
        state, text = self.changed()
        text = '\n'.join(line for line in text.splitlines() if not line.startswith('形象状态：'))
        with self.assertRaisesRegex(ValueError, '实际完成秒数'):
            st.check_reference_policy(state, [modern(st.parse(text, state)[0])])

    def test_parallel_scene_tracks_use_explicit_shot_scopes(self):
        state, text = self.changed()
        next_start = state['records'][-1]['end']
        state['records'].append({'id': 'L000008', 'line': 8, 'local_line': 3, 'scene': 'SC0002',
                                'kind': 'action', 'speaker': None, 'text': '阿青在另一叙事层站在走廊。',
                                'start': next_start, 'end': next_start + 15})
        state['length'] = next_start + 15
        state['ledger']['scenes'][1]['character_ids'] = ['CHAR0001']
        state['ledger']['scenes'][1]['character_appearances'] = [
            {'base_asset_id': 'CHAR0001', 'initial_asset_id': 'CHAR0004', 'transitions': []}]
        text = text.replace('L000003-L000005', 'L000003-L000008').replace(
            '场景：@{SET0001}；', '场景：@{SET0001}、@{SET0002}；')
        lines = text.splitlines()
        declaration = json.loads(lines[4][len('形象状态：'):])
        declaration['shots'] = [1]
        lines[4] = '形象状态：' + json.dumps(declaration, ensure_ascii=False)
        lines.insert(5, '形象状态：' + json.dumps({'scene_id': 'SC0002', 'base_asset_id': 'CHAR0001',
                    'events': [], 'shots': [2]}, ensure_ascii=False))
        unit = modern(st.parse('\n'.join(lines), state)[0])
        output = st.render_unit(state, unit)
        self.assertIn('（镜头1）', output)
        self.assertIn('（镜头2）', output)
        self.assertEqual(output.count('：“别动，等我回来。”'), 1)
        bad = copy.deepcopy(unit)
        bad['appearance_states'][0].pop('shots')
        with self.assertRaisesRegex(ValueError, '并行叙事层'):
            st.check_reference_policy(state, [bad])

    def test_same_identity_static_appearances_group_by_actual_shots(self):
        state, text = self.changed()
        state['ledger']['scenes'][0].pop('character_appearances')
        state['ledger']['scenes'][0]['character_ids'].append('CHAR0004')
        text = '\n'.join(line for line in text.splitlines() if not line.startswith('形象状态：'))
        output = st.render_unit(state, modern(st.parse(text, state)[0]))
        self.assertIn('〔阿青@阿青-基础形象 （镜头1） ／ 阿青@阿青-戴面具 （镜头2）〕', output)

    def test_declared_state_cache_cannot_be_edited(self):
        state, text = self.changed()
        unit = modern(st.parse(text, state)[0])
        unit['appearance_states'][0]['events'][0]['at'] = 2
        with self.assertRaisesRegex(ValueError, 'appearance_states'):
            st.validate(state, [unit])

    def test_next_unit_without_change_table_resolves_prior_completed_state(self):
        state, text = self.changed()
        # A later range uses source order to recover the completed reviewed
        # change; no repeated per-shot or per-unit state table is needed.
        later = text.splitlines()
        later = [later[0].replace('L000003-L000005', 'L000004-L000005')] + [
            line for line in later[1:] if not line.startswith('形象状态：')]
        later = '\n'.join(later).replace('@{CHAR0001}、@{CHAR0004}', '@{CHAR0004}').replace('CHAR0001', 'CHAR0004')
        unit = modern(st.parse(later, state)[0])
        st.check_reference_policy(state, [unit])
        self.assertIn('阿青@阿青-戴面具 （起始）', st.render_unit(state, unit))


if __name__ == '__main__':
    unittest.main()
