"""Offline regressions for range, speech, boundaries, and output protection."""
import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import director as d
import dialogue_layout as dl


def static_fixture_continuity(unit):
    """Register presence for fictional fixtures testing unrelated behavior.

    This test-only helper does not infer production continuity from subjects.
    Fixtures deliberately track no position, pose, or contact facts; tests for
    actual changes and identity transitions supply explicit contracts elsewhere.
    """
    asset_ids = sorted(set(unit.get('character_ids', []) + unit.get('prop_ids', [])))
    snapshot = {asset_id: {'present': True, 'asset_id': asset_id, 'facts': {}}
                for asset_id in asset_ids}
    unit['continuity'] = {
        'version': 1, 'boundary': {'kind': 'continuous'},
        'entities': {asset_id: {'asset_ids': [asset_id]} for asset_id in asset_ids},
        'initial': copy.deepcopy(snapshot), 'final': copy.deepcopy(snapshot)}
    for shot in unit['shots']:
        shot['continuity'] = {'changes': [], 'visibility': {
            asset_id: {'mode': 'visible' if asset_id in shot['subjects'] else 'offscreen',
                       'basis': '虚构测试实体位于本镜取景内' if asset_id in shot['subjects']
                                else '虚构测试实体仍在场，位于本镜取景范围外'}
            for asset_id in asset_ids}}
    return unit


class AuditDirectorChecks(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('SEEDANCE_AUDIT_TEST_DIR')
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.data = {
            'schema_version': 3,
            'characters': [{'id': 'CHAR0001', 'name': '陈默-基础形象'}], 'props': [],
            'scene_assets': [{'id': 'SET0001', 'name': '房间'}, {'id': 'SET0002', 'name': '走廊'}],
            'scenes': [{'id': 'SC0001', 'number': 1, 'heading': '场景1-日-内-房间', 'scene_asset_id': 'SET0001',
                        'character_ids': ['CHAR0001'], 'prop_ids': []},
                       {'id': 'SC0002', 'number': 2, 'heading': '场景2-日-内-走廊', 'scene_asset_id': 'SET0002',
                        'character_ids': ['CHAR0001'], 'prop_ids': []}]}
        self.script = '场景1-日-内-房间\n人物：陈默-基础形象；\n△ 陈默站起后看向窗外。\n△ 陈默站起后看向窗外。\n'
        (self.project / 'script.txt').write_text(self.script, encoding='utf-8')
        d.write_json(self.project / 'source.json', {'paragraphs': [{'id': 1, 'text': self.script}]})
        d.write_json(self.project / 'review.json', {'scenes': []})
        d.write_json(self.project / 'ledger.json', self.data)
        scene_dir = self.project / 'scenes'
        scene_dir.mkdir()
        draft = scene_dir / 'scene_001_0001-0001.txt'
        draft.write_text(self.script, encoding='utf-8')
        (self.project / 'video-style.txt').write_text('3D国漫\n', encoding='utf-8')
        records, length = d.build_records(self.script, self.data)
        self.state = {'schema_version': 1, 'phase': 'range', 'style_hash': d.sha('3D国漫'),
            'revision': 0, 'ranges': [], 'units': [], 'receipts': [], 'next_numbers': {},
            'length': length, 'entry': {'min': 4, 'durations': []}, 'ledger': self.data, 'records': records,
            'drafts': {draft.name: d.sha(self.script)},
            'files': {key: {'path': str(self.project / name), 'sha256': d.file_hash(self.project / name)}
                      for key, name in [('script', 'script.txt'), ('source', 'source.json'),
                                        ('review', 'review.json'), ('ledger', 'ledger.json')]}}
        d.write_json(self.project / 'session.json', self.state)

    def unit(self, span, scene='SC0001', framings=None):
        framings = framings or ['中景'] * 4
        unit = {'span': span, 'output_format': 'reference-v3', 'duration': 4,
            'main_scene': scene, 'scene_ids': [scene], 'character_ids': ['CHAR0001'], 'prop_ids': [],
            'time': '白天', 'weather': '未明示', 'global': '@{CHAR0001} 位于画面左侧。',
            'start_state': '站立', 'end_state': '站立', 'short_reason': 'natural_beat', 'short_basis': '完整动作落点',
            'audio': [], 'shots': [{'start': i, 'end': i+1, 'scene_id': scene, 'framing': framing,
                'subjects': ['CHAR0001'], 'focus': ['CHAR0001'], 'angle': ['斜侧', '正面', '侧拍', '客观'][i], 'label': '站起',
                'purpose': '看清人物站起与窗边的关系，固定机位保留动作连续性。',
                'camera': ['平视斜侧固定机位', '正面平视固定机位', '侧面平视固定机位', '客观平视固定机位'][i], 'composition': '人物在画面左侧',
                'depth_of_field': '中等景深，人物清晰', 'lighting': '既有室内柔光保持不变',
                'transition': '硬切', 'text': '@{CHAR0001} 完成原动作。', 'closeup_reason': '动作高光', 'dialogue_parts': []}
                for i, framing in enumerate(framings)]}
        return static_fixture_continuity(unit)

    def request(self, first=None, second=None):
        return {'mode': 'append', 'request_id': 'audit', 'span': [0, 18],
                'units': [first or self.unit([0, 9]), second or self.unit([9, 18])]}

    def commit(self, request, output):
        path = self.project / 'request.json'
        d.write_json(path, request)
        from test_review_support import approve_fixture
        args = approve_fixture(SimpleNamespace(project=self.project, request=path, output=output), self.state)
        return d.commit(args, self.state)

    def numeric_state(self, text):
        state = copy.deepcopy(self.state)
        state['records'] = [dict(state['records'][0], label='陈默', text=text, start=0, end=len(d.compact(text)))]
        state['length'] = len(d.compact(text))
        return state

    def test_numeric_anchor_cannot_change_decimal_sign_percentage_or_time(self):
        pairs = [('赔偿1.5万元。', '赔偿15万元'), ('气温-5度。', '气温5度'),
                 ('提升15%。', '提升15'), ('时间1:30。', '时间130'), ('分得1/5。', '分得15')]
        for text, quote in pairs:
            with self.subTest(text=text):
                self.assertEqual(d.anchors(self.numeric_state(text), quote), [])

    def test_numeric_anchor_is_not_a_partial_number(self):
        for text in ('金额15万元。', '金额1.5万元。', '金额-5万元。'):
            with self.subTest(text=text):
                self.assertEqual(d.anchors(self.numeric_state(text), '5万元'), [])

    def test_numeric_anchor_preserves_coordinates_and_format_punctuation(self):
        state = self.numeric_state('“赔偿 1.5万元！”')
        result = d.anchors(state, '赔偿1.5万元')
        self.assertEqual(result, [{'span': [0, 6], 'approximate': False}])
        self.assertEqual(d.anchors(self.numeric_state('你好，陈默！'), '你好 陈默'),
                         [{'span': [0, 4], 'approximate': False}])

    def test_numeric_symbol_width_and_unicode_minus_are_compatible(self):
        self.assertEqual(d.anchors(self.numeric_state('温度−5度。'), '温度-5度'),
                         [{'span': [0, 4], 'approximate': False}])
        self.assertEqual(d.anchors(self.numeric_state('比例1／5。'), '比例1/5'),
                         [{'span': [0, 4], 'approximate': False}])

    def test_semantic_math_operator_is_not_deleted(self):
        self.assertEqual(d.anchors(self.numeric_state('计算A+B。'), '计算AB'), [])
        self.assertEqual(d.anchors(self.numeric_state('计算A+B。'), '计算A+B'),
                         [{'span': [0, 4], 'approximate': False}])

    def test_original_source_fallback_does_not_reintroduce_wrong_number(self):
        state = self.numeric_state('标准化后的其他内容。')
        d.write_json(self.project / 'source.json', {'paragraphs': [{'id': 1, 'text': '赔偿1.5万元。'}]})
        self.assertEqual(d.anchors(state, '赔偿15万元'), [])

    def test_role_name_and_emotion_do_not_imply_os(self):
        for label in ('Ross', 'Josie', '陈默（内心忐忑）', '陈默（低声）'):
            with self.subTest(label=label):
                text = f'场景1-日-内-房间\n人物：陈默-基础形象；\n{label}：你好。\n'
                self.assertEqual(d.build_records(text, self.data)[0][0]['voice'], '说')

    def test_explicit_voice_tags_and_narrator_remain_supported(self):
        for label, voice in [('陈默(OS)', '内心想法'), ('陈默（内心想法）', '内心想法'),
                              ('陈默（内心OS）', '内心想法'), ('陈默（握紧拳头os）', '内心想法'),
                              ('陈默（画外音）', '画外音'), ('陈默（旁白）', '旁白'), ('旁白', '旁白')]:
            with self.subTest(label=label):
                text = f'场景1-日-内-房间\n人物：陈默-基础形象；\n{label}：你好。\n'
                self.assertEqual(d.build_records(text, self.data)[0][0]['voice'], voice)

    def cross_scene_unit(self):
        self.state['records'][1]['scene'] = 'SC0002'
        unit = self.unit([0, 18])
        unit.update(scene_ids=['SC0001', 'SC0002'], cross_scene_reason='回忆', cross_scene_evidence='陈默站起后看向窗外')
        for shot in unit['shots'][2:]:
            shot['scene_id'] = 'SC0002'
        return unit

    def test_connector_rejects_another_scene_set(self):
        unit = self.cross_scene_unit()
        unit['shots'][0]['connector'] = '@{SET0002} 窗边构图匹配。'
        with self.assertRaisesRegex(ValueError, '连接词引用了其他场景'):
            d.validate_unit(self.state, unit, [0, 18])
        unit['shots'][0]['connector'] = '@{SET0001} 窗边构图匹配。'
        self.assertEqual(d.validate_unit(self.state, unit, [0, 18]), [])

    def test_v3_requires_four_nonempty_single_line_photography_fields(self):
        cases = [('missing', None), ('null', None), ('empty', ''), ('whitespace', ' '),
                 ('boolean', False), ('newline', '摄影\n描述'), ('carriage_return', '摄影\r描述')]
        for field in d.PHOTOGRAPHY_FIELDS:
            for name, value in cases:
                with self.subTest(field=field, case=name):
                    unit = self.unit([0, 9])
                    if name == 'missing':
                        unit['shots'][0].pop(field)
                    else:
                        unit['shots'][0][field] = value
                    with self.assertRaisesRegex(ValueError, field):
                        d.validate_unit(self.state, unit, [0, 9])

    def test_v3_photography_rejects_bare_placeholders_but_accepts_specific_continuity(self):
        for field in d.PHOTOGRAPHY_FIELDS:
            for value in ('同前', '同上。', '无', '电影感', '；。'):
                with self.subTest(field=field, value=value):
                    unit = self.unit([0, 9])
                    unit['shots'][0][field] = value
                    with self.assertRaisesRegex(ValueError, field + '不能使用无信息占位值'):
                        d.validate_unit(self.state, unit, [0, 9])
        unit = self.unit([0, 9])
        unit['shots'][0]['lighting'] = '沿用前镜既有室内柔光，人物脸侧明暗方向保持一致'
        self.assertEqual(d.validate_unit(self.state, unit, [0, 9]), [])

    def test_v3_photography_references_reject_unknown_or_other_scene_assets(self):
        for field in d.PHOTOGRAPHY_FIELDS:
            unit = self.cross_scene_unit()
            unit['shots'][0][field] = '@{SET0002} 原有层次'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field + '引用了其他场景'):
                d.validate_unit(self.state, unit, [0, 18])
            unit['shots'][0][field] = '@{CHAR9999} 原有层次'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '资产引用'):
                d.validate_unit(self.state, unit, [0, 18])

    def test_v3_replace_upgrades_only_selected_legacy_unit(self):
        self.commit(self.request(), self.project / 'outputs' / 'new.txt')
        for unit in self.state['units']:
            unit['output_format'] = 'reference-v2'
            unit.pop('continuity')
            for shot in unit['shots']:
                shot.pop('continuity')
                shot.pop('purpose')
                for field in d.PHOTOGRAPHY_FIELDS:
                    shot.pop(field)
        old = copy.deepcopy(self.state['units'])
        selected = self.unit([0, 9])
        selected['id'] = old[0]['id']
        request = {'mode': 'replace', 'request_id': 'v3-selected', 'units': [selected]}
        self.assertEqual([warning['kind'] for warning in d.validate(self.state, request)],
                         ['旧单元连续性边界需人工核对'])
        self.assertEqual(self.state['units'], old)
        result = self.commit(request, self.project / 'outputs' / 'selected-v3.txt')
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(self.state['units'][0]['output_format'], 'reference-v3')
        self.assertEqual(self.state['units'][1], old[1])
        self.assertEqual(self.state['units'][0]['previous_versions'][-1]['output_format'], 'reference-v2')

    def test_partial_closeup_quota_warns_and_full_batch_passes(self):
        first = self.unit([0, 9], framings=['中景', '特写', '中景', '中景'])
        request = self.request(first=first)
        warnings = d.validate(self.state, dict(request, units=[first]), partial=True)
        self.assertEqual(warnings[0]['kind'], '累计特写比例较高需审查')
        self.assertEqual(warnings[0]['percent'], 25)
        self.assertEqual(d.validate(self.state, request), [])

    def test_complete_check_and_commit_allow_excess_closeups_with_review(self):
        first = self.unit([0, 9], framings=['中景', '特写', '中景', '中景'])
        request = {'mode': 'append', 'request_id': 'quota', 'span': [0, 9], 'units': [first]}
        warnings = d.validate(self.state, request)
        self.assertEqual(warnings[0]['kind'], '累计特写比例较高需审查')
        result = self.commit(request, self.project / 'outputs' / 'quota.txt')
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(len(self.state['units']), 1)

    def test_cross_scene_adjacent_close_shots_are_allowed(self):
        self.state['records'][1]['scene'] = 'SC0002'
        request = self.request(first=self.unit([0, 9], framings=['中景'] * 3 + ['近景']),
                               second=self.unit([9, 18], 'SC0002', ['近景'] + ['中景'] * 3))
        self.assertEqual(d.validate(self.state, request), [])

    def test_cross_batch_cross_scene_close_continuation_is_allowed(self):
        self.state['records'][1]['scene'] = 'SC0002'
        first = self.unit([0, 9], framings=['中景'] * 3 + ['近景'])
        first.update(id='场景1-分镜1', batch_id='old', predecessor=None)
        self.state['units'] = [first]
        self.state['ranges'] = [{'span': [0, 9], 'batch_id': 'old'}]
        second = self.unit([9, 18], 'SC0002', ['近景'] + ['中景'] * 3)
        self.assertEqual(d.validate(self.state, {'mode': 'append', 'request_id': 'next', 'span': [9, 18], 'units': [second]}), [])

    def test_legacy_cross_batch_replacement_recovers_cross_scene_boundary(self):
        self.state['records'][1]['scene'] = 'SC0002'
        first = self.unit([0, 9], framings=['中景'] * 3 + ['近景'])
        first.update(id='场景1-分镜1', batch_id='old-a', version=1, predecessor=None)
        second = self.unit([9, 18], 'SC0002')
        second.update(id='场景2-分镜1', batch_id='old-b', version=1, predecessor=None)
        for legacy in (first, second):
            legacy.pop('continuity')
            for shot in legacy['shots']:
                shot.pop('continuity')
        self.state['units'] = [first, second]
        self.state['ranges'] = [{'span': [0, 9], 'batch_id': 'old-a'}, {'span': [9, 18], 'batch_id': 'old-b'}]
        replacement = self.unit([9, 18], 'SC0002', ['近景'] + ['中景'] * 3)
        replacement['id'] = second['id']
        request = {'mode': 'replace', 'request_id': 'replace', 'units': [replacement]}
        self.assertEqual([warning['kind'] for warning in d.validate(self.state, request)],
                         ['旧单元连续性边界需人工核对'])
        replacement.update(batch_id='wrong', predecessor=replacement['id'])
        self.assertEqual([warning['kind'] for warning in d.validate(self.state, request)],
                         ['旧单元连续性边界需人工核对'])

    def test_output_protection_covers_scenes_canvas_and_bound_drafts(self):
        paths = [self.project / 'scenes' / 'new.txt', self.project / 'canvas' / 'new.json',
                 self.project / 'session.json', self.project / 'video-style.txt', d.ROOT / 'new.txt']
        before = copy.deepcopy(self.state)
        for output in paths:
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, '交付路径不能覆盖'):
                self.commit(self.request(), output)
            self.assertEqual(self.state, before)
            d.load(self.project)

    def test_external_bound_draft_directory_is_protected(self):
        external = self.project / 'standardizer'
        external.mkdir()
        self.state['files']['source']['path'] = str(external / 'source.json')
        with self.assertRaisesRegex(ValueError, '交付路径不能覆盖'):
            d.validate_output_path(self.state, self.project, external / 'scenes' / 'new.txt')

    def test_normal_outputs_commit_and_receipt_replay_are_allowed(self):
        output = self.project / 'outputs' / 'prompt.txt'
        request = self.request()
        self.assertEqual(self.commit(request, output)['status'], 'committed')
        self.assertEqual(self.commit(request, self.project / 'outputs' / 'ignored.txt')['status'], 'already_committed')
        self.assertTrue(output.is_file())
        d.load(self.project)

    def test_unsafe_old_receipt_cannot_be_rendered_into_state(self):
        receipt = {'output': str(self.project / 'canvas' / 'state.json'), 'units': []}
        with self.assertRaisesRegex(ValueError, '交付路径不能覆盖'):
            d.render_receipt(self.state, receipt, self.project)
        self.assertFalse((self.project / 'canvas' / 'state.json').exists())

    def speech_unit(self, end=2.0, pause=.2, measured=False):
        record = self.state['records'][0]
        record.update(kind='dialogue', speaker='陈默', voice='说')
        unit = self.unit([0, 9])
        unit['shots'] = [dict(unit['shots'][0], start=0, end=4)]
        unit['audio'] = [{'record': record['id'], 'character_id': 'CHAR0001', 'voice': '说',
                         'text': record['text'], 'start': 0, 'end': end, 'pause': pause,
                         'speed': '常速', 'units': 9}]
        if measured:
            unit['audio'][0].update(timing_basis='recording', recording_evidence='虚构录音测量样本')
        dl.attach(unit)
        return unit

    def test_text_timing_calls_estimate_with_band_limits(self):
        unit = self.speech_unit()
        with patch.object(dl, 'estimate', wraps=dl.estimate) as estimate:
            self.assertEqual([w['kind'] for w in d.validate_unit(self.state, unit, [0, 9])],
                             [])
        self.assertEqual([call.args[2] for call in estimate.call_args_list], [5.4, 4.8])

    def test_extra_emotional_pause_is_not_counted_twice(self):
        unit = self.speech_unit(end=2.9, pause=1.1)
        self.assertEqual([w['kind'] for w in d.validate_unit(self.state, unit, [0, 9])],
                         [])

    def test_flexible_camera_and_count_do_not_relax_voice_time_bounds(self):
        for update in ({'start': -.01}, {'end': 4.01}, {'start': 2.34},
                       {'pause': -.01}, {'pause': 2.34}):
            unit = self.speech_unit()
            unit['audio'][0].update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, '声音时间越界、倒序或停顿非法'):
                d.validate_unit(self.state, unit, [0, 9])

    def test_text_timing_warns_without_rejecting_and_reports_pause_basis(self):
        unit = self.speech_unit(end=1.0)
        warnings = d.validate_unit(self.state, unit, [0, 9])
        self.assertEqual(warnings[0]['kind'], '估计净语速超档')
        timing = warnings[0]['text_estimate']
        self.assertEqual(timing['source_pause_seconds'], .2)
        self.assertEqual(timing['registered_pause_seconds'], .2)
        self.assertEqual(timing['min_seconds'], 1.867)

    def test_recording_path_does_not_use_text_estimate(self):
        unit = self.speech_unit(measured=True)
        with patch.object(dl, 'estimate', side_effect=AssertionError('recording must not estimate')):
            self.assertEqual([w['kind'] for w in d.validate_unit(self.state, unit, [0, 9])],
                             [])


class AuditDialogueChecks(unittest.TestCase):
    def sample(self, basis=None):
        text = '我今天来到这里是为了告诉大家我们一定能成功'
        unit = {'output_format': 'reference-v3', 'audio': [{'record': 'L1', 'text': text, 'start': 0, 'end': 6,
                'units': len(dl.compact(text)), 'pause': .2, 'speed': '常速'}],
                'shots': [{'start': 0, 'end': 3}, {'start': 3, 'end': 6}]}
        first = {'text': '我今天来到这里', 'start': 0, 'end': 2.8}
        if basis is not None:
            first['breath_basis'] = basis
        return unit, {'L1': [first, {'text': '是为了告诉大家我们一定能成功', 'start': 3, 'end': 6}]}

    def test_mixed_contiguous_punctuation_counts_one_highest_pause(self):
        for text, expected in [('真的？！……', .2), ('等一下……？', .2), ('真的，！', .2),
                               ('等一下，……', .26), ('好，，', .15), ('等等……', .26), ('好！好！', .4)]:
            with self.subTest(text=text):
                self.assertEqual(dl.pause_seconds(text), expected)

    def test_unpunctuated_breath_boundary_needs_human_basis(self):
        unit, plans = self.sample()
        with self.assertRaisesRegex(ValueError, 'breath_basis'):
            dl.attach(unit, plans)
        unit, plans = self.sample('完整前置短语完成，依角色自然气口换镜')
        dl.attach(unit, plans)
        self.assertEqual(''.join(p['text'] for s in unit['shots'] for p in s['dialogue_parts']), unit['audio'][0]['text'])
        self.assertIn('breath_basis', unit['shots'][0]['dialogue_parts'][0])

    def test_invalid_breath_basis_cannot_bypass_boundary(self):
        for basis in (' ', 'natural pause', '气口\n解释', True):
            with self.subTest(basis=basis):
                unit, plans = self.sample(basis)
                with self.assertRaisesRegex(ValueError, '自然气口依据'):
                    dl.attach(unit, plans)

    def test_legacy_layout_does_not_accept_new_breath_override(self):
        unit, plans = self.sample('完整前置短语的自然气口')
        unit['output_format'] = 'reference-v1'
        with self.assertRaisesRegex(ValueError, '台词切点'):
            dl.attach(unit, plans)

    def test_tail_and_punctuation_rejection_survive_human_basis(self):
        cases = [('等等，', '了。', '禁止孤立句尾语气词'),
                 ('救命', '！', '禁止把纯标点分配成独立台词')]
        for first, second, expected in cases:
            text = first + second
            unit, _ = self.sample()
            unit['audio'][0].update(text=text, units=len(dl.compact(text)))
            plans = {'L1': [{'text': first, 'start': 0, 'end': 2.8, 'breath_basis': '人工气口依据'},
                            {'text': second, 'start': 3, 'end': 6}]}
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, expected):
                dl.attach(unit, plans)


if __name__ == '__main__':
    unittest.main()
