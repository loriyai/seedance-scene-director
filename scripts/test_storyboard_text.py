"""Boundary tests for the authored-text source, independent of legacy gates."""
import copy
import unittest

import director as d
import storyboard_text as st


def fixture(dialogue='别动，等我回来。', voice='说'):
    ledger = {
        'schema_version': 3,
        'characters': [
            {'id': 'CHAR0001', 'name': '阿青-基础形象', 'source_entry': '阿青-基础形象'},
            {'id': 'CHAR0002', 'name': '路人-群像-常服', 'source_speaker': '路人-群像', 'visual_mode': 'prompt_only'},
            {'id': 'CHAR0003', 'name': '阿白-基础形象'}],
        'props': [{'id': 'PROP0001', 'name': '钥匙'}],
        'scene_assets': [{'id': 'SET0001', 'name': '房间'}, {'id': 'SET0002', 'name': '走廊'}],
        'scenes': [
            {'id': 'SC0001', 'number': 1, 'heading': '场景1-日-内-房间',
             'scene_asset_id': 'SET0001', 'character_ids': ['CHAR0001', 'CHAR0002'], 'prop_ids': ['PROP0001']},
            {'id': 'SC0002', 'number': 2, 'heading': '场景2-日-内-走廊',
             'scene_asset_id': 'SET0002', 'character_ids': ['CHAR0003'], 'prop_ids': []}],
    }
    source = f'场景1-日-内-房间\n人物：阿青-基础形象；路人-群像-常服；\n△ 阿青站在门边。\n阿青({voice})：{dialogue}\n△ 阿青看向窗外。\n'
    records, length = d.build_records(source, ledger)
    state = {'ledger': ledger, 'records': records, 'length': length}
    text = f'''## SC0001 / u01 / L000003-L000005
人物：@{{CHAR0001}}；
场景：@{{SET0001}}；时间：白天；天气：未明示；
人物站位：@{{CHAR0001}}站在门边朝向窗外。
·00秒-03秒[镜头1-交代]：（@{{CHAR0001}}的中景；转场：硬切）镜头向右平移，人物居左，门框在后景，人物与门框清晰，窗外柔光；@{{CHAR0001}}停在门边。音效：无。
声音[L000004,0.2-2.8]：@{{CHAR0001}}{voice}（压低音量）：“{dialogue}”
·03秒-05秒[镜头2-等待]：（@{{CHAR0001}}的近景；转场：硬切）侧后方固定机位保留视线方向，肩部在前景，窗框清晰，沿用窗外柔光；@{{CHAR0001}}看向窗外。音效：无。
'''
    return state, text


def renderable(unit):
    unit = copy.deepcopy(unit)
    unit.update(id='场景1-分镜1', display_name='A01-场景1-分镜1', style='3D国漫写实厚涂风格')
    return unit


class StoryboardTextTests(unittest.TestCase):
    def setUp(self):
        self.state, self.text = fixture()

    def action_draft(self, lengths, key='u01'):
        lines = self.text.splitlines()[:4]
        lines[0] = lines[0].replace('L000003-L000005', 'L000003-L000003').replace('/ u01 /', f'/ {key} /')
        body = self.text.splitlines()[4].split(']：', 1)[1]
        cursor = 0
        for index, length in enumerate(lengths, 1):
            lines.append(f'·{cursor:02d}秒-{cursor + length:02d}秒[镜头{index}-接续]：' + body)
            cursor += length
        return '\n'.join(lines) + '\n'

    def test_valid_text_derives_minimal_receipt_without_old_gates(self):
        original = copy.deepcopy(self.state)
        units = st.parse(self.text, self.state)
        unit = units[0]
        self.assertEqual(unit['draft_key'], 'SC0001/u01')
        self.assertEqual(unit['span'], [0, self.state['length']])
        self.assertEqual(unit['source_records'], ['L000003', 'L000004', 'L000005'])
        self.assertEqual(unit['asset_ids'], ['CHAR0001', 'SET0001'])
        self.assertEqual(unit['duration'], 5)
        for old in ('plans', 'audio', 'continuity', 'action_plan', 'shot_dependencies', 'performance_brief'):
            self.assertNotIn(old, unit)
        report = st.validate(self.state, units)
        self.assertEqual((report['unit_count'], report['shot_count'], report['total_duration']), (1, 2, 5))
        self.assertEqual(report['semantic_review'], 'not_performed')
        self.assertEqual(self.state, original)

    def test_render_keeps_product_layout_and_strips_author_markers(self):
        unit = renderable(st.parse(self.text, self.state)[0])
        output = st.render_unit(self.state, unit)
        self.assertTrue(output.startswith('A01-场景1-分镜1（时长：5秒）\n3D国漫写实厚涂风格;'))
        self.assertIn('人物：@阿青-基础形象 ；', output)
        self.assertIn('场景：@房间 ；时间：白天；天气：未明示；', output)
        self.assertIn('@阿青-基础形象 说（压低音量）：“别动，等我回来。”音效：无。', output)
        self.assertNotIn('声音[', output)
        self.assertNotIn('L000004', output)
        self.assertNotIn('SC0001 / u01', output)
        self.assertNotIn('@{', output)

    def test_literal_at_braces_and_markers_in_dialogue_never_reach_callback(self):
        spoken = '给我@阿白 #12 @{CHAR9999} 声音[L000001,1-2]：“原样”。'
        state, text = fixture(spoken)
        unit = renderable(st.parse(text, state)[0])
        calls = []
        output = st.render_unit(state, unit, lambda value: calls.append(value) or value.replace('@', '图:'))
        self.assertIn('：“' + spoken + '”', output)
        self.assertFalse(any(spoken in value or 'CHAR9999' in value for value in calls))
        self.assertTrue(all(value.startswith('@') for value in calls))

    def test_exact_dialogue_rejects_omission_extra_and_punctuation_change(self):
        for spoken in ('别动，等我。', '别动，等我回来！', '别动，等我回来。好。'):
            with self.subTest(spoken=spoken), self.assertRaisesRegex(ValueError, '逐字一致'):
                st.parse(self.text.replace('：“别动，等我回来。”', '：“' + spoken + '”'), self.state)

    def test_missing_voice_marker_fails(self):
        text = '\n'.join(line for line in self.text.splitlines() if not line.startswith('声音['))
        with self.assertRaisesRegex(ValueError, '台词遗漏'):
            st.parse(text, self.state)

    def test_bad_voice_and_speaker_fail(self):
        for text, message in (
            (self.text.replace('}说（', '}内心想法（'), '发声类型'),
            (self.text.replace('：@{CHAR0001}说', '：@{CHAR0002}说').replace('人物：@{CHAR0001}；', '人物：@{CHAR0001}，@{CHAR0002}；'), '说话人')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                st.parse(text, self.state)

    def test_inner_voice_survives_exactly(self):
        state, text = fixture(voice='内心想法')
        output = st.render_unit(state, renderable(st.parse(text, state)[0]))
        self.assertIn('@阿青-基础形象 内心想法（压低音量）', output)

    def test_invalid_ids_sc_refs_and_bare_names_rejected(self):
        for value in ('@{CHAR9999}', '@{SC0001}', '@阿青', '@{SET9999}', '@{PROP0002}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                st.parse(self.text.replace('人物站位：@{CHAR0001}', '人物站位：' + value), self.state)

    def test_unlisted_and_foreign_scene_character_fail(self):
        for text in (self.text.replace('人物站位：@{CHAR0001}', '人物站位：@{CHAR0002}'),
                     self.text.replace('人物：@{CHAR0001}；', '人物：@{CHAR0001}，@{CHAR0003}；')):
            with self.subTest(text=text[:90]), self.assertRaises(ValueError):
                st.parse(text, self.state)

    def test_prop_reference_is_derived_from_actual_text(self):
        text = self.text.replace('停在门边。', '握住@{PROP0001}停在门边。')
        unit = st.parse(text, self.state)[0]
        self.assertEqual(unit['prop_ids'], ['PROP0001'])
        self.assertEqual(unit['asset_ids'], ['CHAR0001', 'SET0001', 'PROP0001'])

    def test_prompt_only_character_renders_without_at_or_callback(self):
        text = self.text.replace('人物：@{CHAR0001}；', '人物：@{CHAR0001}，@{CHAR0002}；').replace('停在门边。', '停在门边，@{CHAR0002}继续等候。')
        unit = renderable(st.parse(text, self.state)[0])
        calls = []
        output = st.render_unit(self.state, unit, lambda value: calls.append(value) or value)
        self.assertIn('路人-群像-常服', output)
        self.assertNotIn('@路人-群像-常服', output)
        self.assertFalse(any('路人-群像-常服' in value for value in calls))

    def test_timing_integer_contiguous_limits_and_shot_numbers(self):
        mutations = [
            ('·00秒-03秒', '·01秒-03秒'), ('·03秒-05秒', '·04秒-05秒'),
            ('·00秒-03秒', '·00秒-07秒'), ('·03秒-05秒', '·03秒-03秒'),
            ('·00秒-03秒', '·00.0秒-03秒'), ('镜头2-等待', '镜头3-等待')]
        for before, after in mutations:
            with self.subTest(after=after), self.assertRaises(ValueError):
                st.parse(self.text.replace(before, after), self.state)
        text = '\n'.join(self.text.splitlines()[:-1])
        with self.assertRaisesRegex(ValueError, '4～30秒'):
            st.parse(text, self.state)

    def test_voice_window_and_overlap_fail(self):
        for timing in ('0.2-3.1', '2.8-0.2', '0.2-0.2', '0.2001-2.8', '-0.2-2.8'):
            with self.subTest(timing=timing), self.assertRaises(ValueError):
                st.parse(self.text.replace('0.2-2.8', timing), self.state)
        text = self.text.replace('声音[L000004,0.2-2.8]：@{CHAR0001}说（压低音量）：“别动，等我回来。”',
            '声音[L000004,0.2-1.8]：@{CHAR0001}说：“别动，”\n声音[L000004,1.7-2.8]：@{CHAR0001}说：“等我回来。”')
        with self.assertRaisesRegex(ValueError, '倒序或重叠'):
            st.parse(text, self.state)

    def test_long_unit_shot_count_boundaries(self):
        for total in range(26, 31):
            for lengths in ([6] * 4 + [total - 24], [1] * total):
                with self.subTest(total=total, shots=len(lengths)):
                    units = st.parse(self.action_draft(lengths), self.state)
                    report = st.validate(self.state, units)
                    self.assertEqual(report['total_duration'], total)
                    self.assertEqual(report['shot_count'], len(lengths))
                    self.assertEqual(report['status'], 'checked')
        for lengths in ([4, 4, 3, 3, 3, 3, 3, 3], [2] * 13, [4] * 6 + [3] * 2, [5] * 5):
            with self.subTest(valid=lengths):
                units = st.parse(self.action_draft(lengths), self.state)
                self.assertEqual(st.validate(self.state, units)['total_duration'], sum(lengths))
        for lengths, message in (([6] * 5 + [1], '4～30秒'), ([7, 6, 6, 6, 5], '1～6秒')):
            with self.subTest(invalid=lengths), self.assertRaisesRegex(ValueError, message):
                st.parse(self.action_draft(lengths), self.state)

    def test_observations_report_durations_and_review_candidates_without_mutation(self):
        text = self.action_draft([6, 6, 6, 6, 2]) + self.action_draft([4], key='u02')
        units = st.parse(text, self.state)
        before = copy.deepcopy(units)
        rendered = [st.render_unit(self.state, renderable(unit)) for unit in units]
        report = st.validate(self.state, units)
        stats = report['observations']
        self.assertEqual(stats['mode'], 'non_blocking')
        self.assertEqual(stats['unit_duration_seconds'], {
            'distribution': {'4': 1, '26': 1}, 'mean': 15, 'min': 4, 'max': 26})
        self.assertEqual(stats['same_scene_adjacent_candidates'], [{
            'units': ['SC0001/u01', 'SC0001/u02'], 'source_scene': 'SC0001',
            'combined_duration': 30, 'review': 'human_review_only'}])
        self.assertEqual(stats['camera_wording']['by_shot_duration_seconds'], {
            '2': {'fixed': 0, 'moving': 1, 'mixed_or_unclear': 0, 'unknown': 0},
            '4': {'fixed': 0, 'moving': 1, 'mixed_or_unclear': 0, 'unknown': 0},
            '6': {'fixed': 0, 'moving': 4, 'mixed_or_unclear': 0, 'unknown': 0}})
        self.assertEqual(report['semantic_review'], 'not_performed')
        self.assertEqual(units, before)
        self.assertEqual([st.render_unit(self.state, renderable(unit)) for unit in units], rendered)
        above = st.parse(self.action_draft([6] * 4 + [2]) + self.action_draft([5], key='u02'), self.state)
        self.assertEqual(st.validate(self.state, above)['observations']['same_scene_adjacent_candidates'], [])

    def test_camera_wording_uses_only_leading_camera_clause(self):
        visual = self.text.splitlines()[4]
        prefix = visual.split('）', 1)[0] + '）'
        cases = {
            '侧面固定机位，人物向右平移；@{CHAR0001}跟随走动。': 'fixed',
            '镜头向右平移；@{CHAR0001}保持固定姿势。': 'moving',
            '固定机位跟拍；@{CHAR0001}停下。': 'mixed_or_unclear',
            '镜头稳定；@{CHAR0001}转身。': 'mixed_or_unclear',
            '镜头不平移；@{CHAR0001}停下。': 'mixed_or_unclear',
            '固定机位或跟拍；@{CHAR0001}停下。': 'mixed_or_unclear',
            '镜头记录人物平移；@{CHAR0001}停下。': 'unknown',
            '镜头记录固定机位；@{CHAR0001}停下。': 'unknown',
            '@{CHAR0001}向右平移；镜头固定。': 'unknown',
            '人物保持固定姿势；镜头平移。': 'unknown',
            '门框清晰；镜头向右平移。': 'unknown',
            '车头一侧平视斜向固定机位，人物跟随走动。': 'fixed',
            '从前排中央位置向后取景的固定机位；@{CHAR0001}向右平移。': 'fixed',
            '人物连线侧面的固定机位，演员平移。': 'fixed',
            '人物侧前方固定机位；@{CHAR0001}转身。': 'fixed',
            '车头左前方腰高机位固定，人物跟拍设备入画。': 'fixed',
            '副驾前方偏中间机位向驾驶者小幅推进，人物固定站立。': 'moving',
            '人物侧方机位由邵向车主方向小幅横移，人物停下。': 'moving',
            '韩的侧前方机位随他向前移动一步，人物停下。': 'moving',
            '固定机位人物向右平移；@{CHAR0001}转身。': 'fixed',
            '镜头固定人物不平移；@{CHAR0001}转身。': 'fixed',
            '镜头固定后推近，人物停下。': 'mixed_or_unclear',
            '镜头推近后固定，人物停下。': 'mixed_or_unclear',
            '从前挡风玻璃外向车内小幅推进，人物停下。': 'unknown',
        }
        for body, expected in cases.items():
            with self.subTest(body=body):
                shot = {'text': prefix + body + '音效：跟拍设备轻响。'}
                self.assertEqual(st._camera_wording(shot), expected)

    def test_observation_helper_excludes_same_scene_units_with_a_source_gap(self):
        first = st.parse(self.action_draft([4]), self.state)[0]
        last = st.parse(self.action_draft([4], key='u03').replace(
            'L000003-L000003', 'L000005-L000005'), self.state)[0]
        self.assertEqual(first['scene_ids'], last['scene_ids'])
        self.assertEqual(st.observations(self.state, [first, last])['same_scene_adjacent_candidates'], [])

    def test_fixed_and_moving_are_both_accepted_at_every_shot_duration(self):
        for wording, label in (('固定机位', 'fixed'), ('镜头向右平移', 'moving'), ('窗框清晰', 'unknown')):
            with self.subTest(wording=wording):
                draft = self.action_draft([1, 2, 3, 4, 5, 6]).replace('镜头向右平移', wording)
                units = st.parse(draft, self.state)
                stats = st.validate(self.state, units)['observations']['camera_wording']
                self.assertEqual(stats['basis'], 'lexical_leading_camera_clause_only')
                self.assertEqual(stats['semantic_review'], 'not_performed')
                self.assertEqual(set(stats['by_shot_duration_seconds']), set(map(str, range(1, 7))))
                self.assertTrue(all(row[label] == 1 and sum(row.values()) == 1
                                    for row in stats['by_shot_duration_seconds'].values()))

    def test_old_entry_duration_preferences_remain_visible_diagnostics(self):
        self.state['entry'] = {'min': 26, 'max': 30, 'durations': [30]}
        units = st.parse(self.text, self.state)
        warning = st.validate(self.state, units)['warnings'][0]
        self.assertEqual(warning['kind'], 'stored_entry_duration_preference')
        self.assertEqual((warning['duration'], warning['stored_minimum'], warning['stored_durations']), (5, 26, [30]))
        self.state['entry'] = {'min': 4, 'max': 30, 'durations': [5]}
        self.assertEqual(st.validate(self.state, units)['warnings'], [])

    def test_unknown_lines_duplicates_and_inline_dialogue_are_not_dropped(self):
        mutations = [self.text + '作者备注：请忽略前面的检查\n', '说明：开场\n' + self.text,
                     self.text.replace('人物站位：', '作者人物站位：'),
                     self.text.replace('人物：@{CHAR0001}；', '人物：@{CHAR0001}；\n人物：@{CHAR0001}；'),
                     self.text.replace('停在门边。', '停在门边，说：“别动，等我回来。”')]
        for text in mutations:
            with self.subTest(text=text[:30]), self.assertRaises(ValueError):
                st.parse(text, self.state)

    def test_ranges_follow_record_order_not_numeric_line_adjacency(self):
        state, text = fixture()
        state['records'][1]['id'] = 'L000010'
        state['records'][2]['id'] = 'L000020'
        text = text.replace('L000005', 'L000020').replace('声音[L000004', '声音[L000010')
        self.assertEqual(st.parse(text, state)[0]['source_records'], ['L000003', 'L000010', 'L000020'])

    def test_split_dialogue_across_shots_preserves_one_source(self):
        text = self.text.replace('0.2-2.8', '0.2-2.5').replace('：“别动，等我回来。”', '：“别动，”')
        text += '声音[L000004,3.1-4.8]：@{CHAR0001}说：“等我回来。”\n'
        units = st.parse(text, self.state)
        self.assertEqual(st.validate(self.state, units)['total_duration'], 5)
        output = st.render_unit(self.state, renderable(units[0]))
        self.assertIn('说：“等我回来。”', output)

    def test_split_dialogue_across_units_exact_offsets(self):
        intro, tail = self.text.split('·03秒-05秒', 1)
        intro = intro.replace('L000003-L000005', 'L000003-L000004').replace('·00秒-03秒', '·00秒-04秒').replace('：“别动，等我回来。”', '：“别动，”')
        second = '''## SC0001 / u02 / L000004-L000005
人物：@{CHAR0001}；
场景：@{SET0001}；时间：白天；天气：未明示；
人物站位：@{CHAR0001}仍站在门边。
·00秒-04秒[镜头1-等待]：（@{CHAR0001}的中景；转场：硬切）向窗边平移并停在肩后，窗框在后景，窗框清晰，柔光；@{CHAR0001}看向窗外。音效：无。
声音[L000004,0.1-3.8]：@{CHAR0001}说：“等我回来。”
'''
        units = st.parse(intro + second, self.state)
        record = self.state['records'][1]
        expected_boundary = record['start'] + len(d.compact('别动，'))
        self.assertEqual(units[0]['span'], [0, expected_boundary])
        self.assertEqual(units[1]['span'], [expected_boundary, self.state['length']])
        st.validate(self.state, units)
        # Re-export of one receipt unit must not demand the other fragment again.
        self.assertIn('：“别动，”', st.render_unit(self.state, renderable(units[0])))
        self.assertIn('：“等我回来。”', st.render_unit(self.state, renderable(units[1])))
        original = copy.deepcopy(self.state)
        for half in units:
            selected = st.parse(half['source_text'], self.state, source_span=half['span'])
            self.assertEqual(selected[0]['span'], half['span'])
            self.assertEqual(selected[0]['source_text'], half['source_text'])
            st.validate(self.state, selected)
            with self.assertRaisesRegex(ValueError, '逐字一致'):
                st.parse(half['source_text'].replace('，”', '。”').replace('回来。”', '回来！”'),
                         self.state, source_span=half['span'])
        self.assertEqual(self.state, original)
        with self.assertRaisesRegex(ValueError, '逐字一致'):
            st.parse(units[0]['source_text'], self.state)
        # Moving boundary punctuation into the next unit would make existing
        # compact coordinates ambiguous on a later isolated replacement.
        with self.assertRaisesRegex(ValueError, '边界标点'):
            st.parse((intro + second).replace('：“别动，”', '：“别动”').replace('：“等我回来。”', '：“，等我回来。”'), self.state)

    def test_duplicate_keys_and_source_gaps_fail(self):
        state, text = fixture()
        simple = '\n'.join(line for line in text.splitlines() if not line.startswith('声音['))
        first = simple.replace('L000003-L000005', 'L000003-L000003')
        cases = [first + '\n' + first,
                 first + '\n' + simple.replace('L000003-L000005', 'L000005-L000005'),
                 first + '\n' + simple.replace('L000003-L000005', 'L000005-L000005').replace('/ u01 /', '/ u02 /')]
        for text in cases:
            with self.subTest(text=text[:50]), self.assertRaises(ValueError):
                st.parse(text, state)

    def test_long_action_can_continue_across_units_without_invented_character_splits(self):
        text = '''## SC0001 / u01 / L000003-L000003
人物：@{CHAR0001}；
场景：@{SET0001}；时间：白天；天气：未明示；
人物站位：@{CHAR0001}站在门边。
·00秒-06秒[镜头1-开始]：（@{CHAR0001}的中景；转场：硬切）向门边推近，人物居左，门边清晰，柔光；@{CHAR0001}从门侧转身。音效：无。
·06秒-12秒[镜头2-接续]：（@{CHAR0001}的中景；转场：硬切）沿人物方向跟移，人物居左，门边清晰，柔光；@{CHAR0001}接续转身。音效：无。
·12秒-16秒[镜头3-临界]：（@{CHAR0001}的中景；转场：硬切）向肩后移动，人物居左，门边清晰，柔光；@{CHAR0001}继续原动作。音效：无。
'''
        second = text.replace('/ u01 /', '/ u02 /').replace('从门侧转身', '接续上一单元的转身').replace('继续原动作', '完成原动作')
        units = st.parse(text + second, self.state)
        self.assertEqual([u['duration'] for u in units], [16, 16])
        record = self.state['records'][0]
        self.assertEqual(units[0]['span'], [record['start'], record['end']])
        self.assertEqual(units[1]['span'], units[0]['span'])
        report = st.validate(self.state, units)
        self.assertEqual(report['total_duration'], 32)
        self.assertEqual(report['source_records'], ['L000003'])
        self.assertEqual(report['semantic_review'], 'not_performed')
        st.validate(self.state, st.parse(second, self.state, source_span=units[1]['span']))
        # Skipping the next dialogue record is a real omission, even if both
        # action units would be valid individually.
        with self.assertRaisesRegex(ValueError, '不连续/重复'):
            st.parse(text + second.replace('L000003-L000003', 'L000005-L000005'), self.state)

    def test_multiple_overlapped_source_records_are_rejected(self):
        second = self.text.replace('/ u01 /', '/ u02 /')
        with self.assertRaisesRegex(ValueError, '不连续/重复'):
            st.parse(self.text + second, self.state)

    def test_derived_fields_cannot_silently_override_source_text(self):
        units = st.parse(self.text, self.state)
        for field, value in [('duration', 30), ('span', [1, 3]), ('character_ids', []), ('source_records', []), ('prop_ids', ['PROP0001'])]:
            broken = copy.deepcopy(units)
            broken[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                st.validate(self.state, broken)
        units[0].update(id='persisted-id', display_name='new title', style='已保存风格')
        st.validate(self.state, units)

    def test_non_character_announcement_preserves_source_without_char(self):
        state, text = fixture()
        record = state['records'][1]
        record.update(source_kind='non_character', speaker='门口提示音', voice='非人物播报')
        text = text.replace('：@{CHAR0001}说（压低音量）', '：门口提示音非人物播报')
        unit = renderable(st.parse(text, state)[0])
        calls = []
        output = st.render_unit(state, unit, lambda value: calls.append(value) or value)
        self.assertIn('门口提示音非人物播报：“别动，等我回来。”', output)
        self.assertNotIn('@门口提示音', output)
        self.assertFalse(any('门口提示音' in value for value in calls))
        with self.assertRaisesRegex(ValueError, '声源与原文不一致'):
            st.parse(text.replace('：门口提示音非人物播报', '：另一提示音非人物播报'), state)
        record['speaker'] = 'Service Center'
        text = text.replace('：门口提示音非人物播报', '：Service Center非人物播报')
        self.assertIn('Service Center非人物播报', st.render_unit(state, renderable(st.parse(text, state)[0])))

    def test_character_appearance_track_is_an_allowed_identity_not_new_speaker(self):
        state, text = fixture()
        state['ledger']['characters'].append({'id': 'CHAR0004', 'name': '阿青-戴面具', 'source_entry': '阿青-基础形象'})
        state['ledger']['scenes'][0]['character_appearances'] = [
            {'initial_asset_id': 'CHAR0001', 'transitions': [{'from_asset_id': 'CHAR0001', 'to_asset_id': 'CHAR0004'}]}]
        unit = st.parse(text.replace('CHAR0001', 'CHAR0004'), state)[0]
        self.assertEqual(unit['character_ids'], ['CHAR0004'])

    def test_bom_blank_lines_and_outer_whitespace_canonicalize_without_speech_edits(self):
        state, text = fixture(' 别动，等我回来。 ')
        state['records'][1]['text'] = ' 别动，等我回来。 '
        units = st.parse('\ufeff\n' + '\n\n'.join('  ' + line + '  ' for line in text.splitlines()), state)
        self.assertEqual(units[0]['shots'][0]['dialogue_parts'][0]['text'], ' 别动，等我回来。 ')
        st.validate(state, units)

    def test_malformed_empty_or_outside_source_fail(self):
        for text in ('', '\x00', self.text.replace('L000005', 'L999999'),
                     self.text.replace('L000003-L000005', 'L000005-L000003'),
                     self.text.replace('@{SET0001}；时间', '@{SET0002}；时间'),
                     self.text.replace('天气：未明示；', '天气：；')):
            with self.subTest(text=text[:25]), self.assertRaises(ValueError):
                st.parse(text, self.state)

    def test_source_span_is_bounded_and_must_be_fully_covered(self):
        for span in ([1], [-1, 5], [0, self.state['length'] + 1], [True, 5], [4, 4]):
            with self.subTest(span=span), self.assertRaises(ValueError):
                st.parse(self.text, self.state, source_span=span)
        # The header cannot silently omit the action at the requested start.
        text = self.text.replace('L000003-L000005', 'L000004-L000005')
        with self.assertRaisesRegex(ValueError, '完整覆盖指定source_span'):
            st.parse(text, self.state, source_span=[0, self.state['length']])


if __name__ == '__main__':
    unittest.main()
