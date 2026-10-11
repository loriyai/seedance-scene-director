#!/usr/bin/env python3
"""Offline regression checks using temporary fictional scripts; no CLI/network calls."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import director as d
import canvas as c
import canvas_media as media
from test_dialogue_layout import DialogueLayoutChecks

_, ledger, _ = d.dependency()
import asset_migrate


class SkillChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script = self.work / 'script.txt'
        self.source = self.work / 'source.json'
        cast = '人物：陈默-基础形象；'
        self.paragraphs = [
            '场景1-日-内-陈默卧室', cast, '陈默把笔和笔记本电脑放在桌上。',
            '场景2-夜-内-陈默卧室', cast, '陈默坐下。',
            '场景3-日-内-陈默卧室', cast, '房间挂满婚礼装饰，陈默站着。',
        ]
        self.source.write_text(json.dumps({'paragraphs': [{'id': i, 'text': p}
            for i, p in enumerate(self.paragraphs, 1)]}, ensure_ascii=False), encoding='utf-8')
        self.script.write_text('\n'.join(p if i % 3 != 2 else '△ ' + p
            for i, p in enumerate(self.paragraphs)) + '\n', encoding='utf-8')
        scenes_dir = self.work / 'scenes'
        scenes_dir.mkdir()
        for i in range(3):
            (scenes_dir / f'scene_{i+1:03d}_{i*3+1:04d}-{i*3+3:04d}.txt').write_text(
                '\n'.join(self.script.read_text(encoding='utf-8').splitlines()[i*3:i*3+3]) + '\n', encoding='utf-8')
        path = self.work / 'draft.json'
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.initialize(self.script, self.work, path)
        draft = d.read_json(path)
        self.draft = copy.deepcopy(draft)
        draft['locations'] = [draft['locations'][0]]
        draft['locations'][0].update(identity_status='confirmed', identity_basis='原文明示陈默所属同一卧室')
        draft['retired_ids'] = ['LOC0002', 'LOC0003']
        for scene in draft['scenes']:
            scene['location_id'] = 'LOC0001'
            scene['time_basis'] = {'kind': '原文明示', 'source_paragraphs': [scene['source_range'][0]]}
        draft['props'] = [{'id': 'PROP0001', 'name': '笔', 'aliases': [], 'visual_facts': []},
                          {'id': 'PROP0002', 'name': '笔记本电脑', 'aliases': [], 'visual_facts': []}]
        draft['scenes'][0]['prop_ids'] = ['PROP0001', 'PROP0002']
        self.data = ledger.build_scene_assets(draft)
        self.state = {'ledger': self.data, 'length': 8, 'entry': {'min': 4, 'durations': []},
            'files': {'script': {'path': str(self.script)}},
            'records': [{'id': 'L000001', 'scene': 'SC0001', 'start': 0, 'end': 8,
                         'kind': 'action', 'text': '陈默将笔放在桌上'}]}
        self.unit = {'id': '场景1-分镜1', 'version': 1, 'span': [0, 8], 'duration': 4,
            'output_format': 'reference-v1',
            'scene_ids': ['SC0001'], 'main_scene': 'SC0001', 'character_ids': ['CHAR0001'],
            'prop_ids': ['PROP0001'], 'time': '白天', 'weather': '未明示', 'style': '3D国风动漫',
            'global': '@{CHAR0001} 站在桌前。', 'start_state': '站立', 'end_state': '放笔',
            'short_reason': 'range_end', 'audio': [],
            'shots': [{'start': 0, 'end': 4, 'framing': '中景', 'scene_id': 'SC0001',
                      'subjects': ['CHAR0001', 'PROP0001'], 'angle': '正面',
                      'focus': ['CHAR0001'], 'label': '放笔', 'transition': '硬切',
                      'text': '将@{PROP0001} 放在桌上。', 'dialogue_parts': []}]}

    def validate(self, data=None):
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, data or self.data)

    def add_decor(self):
        self.data['scene_assets'].append({'id': 'SET0002', 'name': '陈默卧室-婚礼布置',
            'location_id': 'LOC0001', 'variant': 'decor', 'parent_id': 'SET0001',
            'visual_facts': [{'text': '挂满婚礼装饰', 'evidence': '挂满婚礼装饰',
                'source_paragraphs': [9], 'scene_ids': ['SC0003'], 'attribution': '叙述'}]})
        self.data['scenes'][2]['scene_asset_id'] = 'SET0002'

    def test_day_night_share_one_set_and_all_assets_in_plan(self):
        self.validate()
        plan = c.desired_assets(self.state)
        self.assertEqual([a['key'] for a in plan if a['kind'] == 'scene'], ['SET0001'])
        self.assertEqual(len(plan), 4)  # Includes unused-in-this-unit PROP0002.
        self.assertEqual([s['scene_asset_id'] for s in self.data['scenes']], ['SET0001'] * 3)
        self.assertEqual(ledger.build_scene_assets(self.data), self.data)

    def test_unknown_identity_is_blocked(self):
        with self.assertRaisesRegex(ValueError, '身份尚未核实'):
            ledger.build_scene_assets(self.draft)

    def test_same_name_does_not_merge_distinct_locations(self):
        draft = copy.deepcopy(self.draft)
        for item in draft['locations']:
            item.update(identity_status='confirmed', identity_basis='虚构样本：三处不同空间')
        for scene in draft['scenes']:
            scene['time_basis'] = {'kind': '原文明示', 'source_paragraphs': [scene['source_range'][0]]}
        result = ledger.build_scene_assets(draft)
        self.assertEqual(len(result['scene_assets']), 3)
        with self.assertRaisesRegex(ValueError, '重复'):
            self.validate(result)  # Require source-backed distinguishing names.

    def test_real_decor_version_evidence_and_mapping(self):
        self.add_decor()
        self.validate()
        self.assertEqual(d.scene_asset_ids(self.state, ['SC0001', 'SC0002', 'SC0003']), ['SET0001', 'SET0002'])
        self.assertEqual(len([a for a in c.desired_assets(self.state) if a['kind'] == 'scene']), 2)
        self.data['scene_assets'][1]['visual_facts'] = []
        with self.assertRaisesRegex(ValueError, '证据'):
            self.validate()

    def test_duplicate_base_is_blocked(self):
        item = copy.deepcopy(self.data['scene_assets'][0])
        item.update(id='SET0002', name='陈默卧室-重复')
        self.data['scene_assets'].append(item)
        with self.assertRaisesRegex(ValueError, '一个基础布景'):
            self.validate()

    def test_reference_boundary_and_id_space(self):
        text, used = d.asset_text(self.state, self.unit, '@笔记本电脑 放在桌上，@{PROP0001}的特写。')
        self.assertEqual(text, '@笔记本电脑 放在桌上，@笔 的特写。')
        self.assertEqual(used, {'PROP0001', 'PROP0002'})
        for bad in ['@笔记本电脑放在桌上', '@笔记本电脑', '@笔记本电脑；', '@笔记本电脑\t放下', '@笔记本电源 放下']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                d.asset_text(self.state, self.unit, bad)
        short_state = copy.deepcopy(self.state)
        short_state['ledger']['props'] = short_state['ledger']['props'][:1]
        with self.assertRaises(ValueError):
            d.asset_text(short_state, self.unit, '@笔记本电脑 放下')

    def test_scene_occurrence_not_a_visual_asset_id(self):
        with self.assertRaises(ValueError):
            d.asset_text(self.state, self.unit, '@{SC0001} 室内。')
        self.add_decor()
        with self.assertRaises(ValueError):
            d.asset_text(self.state, self.unit, '@{SET0002} 室内。')
        d.validate_unit(self.state, self.unit, self.unit['span'])

    def test_header_black_order_and_dialogue_preservation(self):
        self.assertEqual(d.sha('\n'.join(d.fixed_header())), '8001822afdf324cbbfa3eb284b2a03ccbde084b2e70fa66763a26b5c738e5962')
        original = '不要动@笔记本电脑 放在桌上。'
        self.unit['audio'] = [{'record': 'Lx', 'character_id': 'CHAR0001', 'voice': '说', 'text': original}]
        self.unit['shots'][0]['dialogue_parts'] = [{'record': 'Lx', 'text': original}]
        convert = c.reference_renderer({'陈默卧室': '<SET>', '陈默-基础形象': '<CHAR>', '笔': '<PROP>', '黑幕': '<BLACK>'},
                                      [*d.reference_catalog(self.state).values(), '黑幕'])
        plain = d.render_unit(self.state, self.unit)
        cloud = d.render_unit(self.state, self.unit, convert)
        self.assertEqual(plain.splitlines()[1], '@黑幕')
        self.assertEqual(cloud.splitlines()[1], '<BLACK>')
        self.assertIn('场景1-日-内-陈默卧室@陈默卧室 ；', plain)
        self.assertIn('@陈默-基础形象 说：“' + original + '”', plain)
        self.assertIn('<CHAR> 说：“' + original + '”', cloud)
        self.assertEqual(cloud.splitlines()[2:4], plain.splitlines()[2:4])

    def test_cloud_renderer_does_not_match_name_prefix(self):
        convert = c.reference_renderer({'笔': '<P>', '黑幕': '<B>'})
        self.assertEqual(convert('@笔记本电脑 放下'), '@笔记本电脑 放下')
        self.assertEqual(convert('@笔 放下'), '<P> 放下')
        self.assertEqual(convert('@黑幕'), '<B>')
        self.assertEqual(convert('@黑幕布 放下'), '@黑幕布 放下')

    def test_catalog_and_image_scan_use_set_names(self):
        self.add_decor()
        lp = self.work / 'ledger.json'
        out = self.work / 'catalog.txt'
        d.write_json(lp, self.data)
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.render(self.script, self.source, lp, out)
        text = out.read_text(encoding='utf-8')
        self.assertIn('使用的场次：场景1，场景2。', text)
        self.assertNotIn('- 场景1-日-内-', text)
        folder = self.work / 'images'
        folder.mkdir()
        (folder / '陈默卧室.png').write_bytes(b'\x89PNG\r\n\x1a\nTEST')
        scan = media.scan_images(self.state, folder)
        self.assertEqual(scan['matched'][0]['key'], 'SET0001')

    def test_video_missing_reference_report_uses_set(self):
        # No canvas calls: replace the pure lookup with a fictional pending list.
        old = c.reference_map
        c.reference_map = lambda *args: ({}, [{'key': 'SET0001'}, {'key': 'SC0001'}, {'key': 'PROP0002'}, {'key': 'BLACK'}])
        try:
            _, pending = c.desired_videos(self.state, {'mappings': {}}, {}, [self.unit], True)
        finally:
            c.reference_map = old
        self.assertEqual([p['key'] for p in pending], ['SET0001', 'BLACK'])

    def test_migration_preserves_set_and_reviews_changed_source(self):
        self.add_decor()
        path = self.work / 'ledger.json'
        d.write_json(path, self.data)
        migrated, report = asset_migrate.migrate(path, self.source, self.script, self.work, {})
        self.assertEqual(migrated['schema_version'], 3)
        self.assertEqual(migrated['scene_assets'], self.data['scene_assets'])
        self.assertEqual(report['pending_reviews'], [])
        self.validate(migrated)
        revised = copy.deepcopy(d.read_json(self.source))
        revised['paragraphs'][2]['text'] += '陈默离开。'
        d.write_json(self.work / 'new-source.json', revised)
        old_source = self.work / 'old-source.json'
        old_source.write_bytes(self.source.read_bytes())
        self.source.write_bytes((self.work / 'new-source.json').read_bytes())
        _, report = asset_migrate.migrate(path, old_source, self.script, self.work, {'scenes': {'SC0001': 1}})
        self.assertTrue(any(p['kind'] == '布景版本关联待核' for p in report['pending_reviews']))

    def test_legacy_canvas_plan_requires_reviewed_upgrade(self):
        legacy = copy.deepcopy(self.state)
        legacy['ledger']['schema_version'] = 2
        self.assertIn('SC0001', d.reference_catalog(legacy))
        with self.assertRaisesRegex(ValueError, '旧台账'):
            c.desired_assets(legacy)
        with self.assertRaisesRegex(ValueError, '旧项目'):
            c.desired_videos(legacy, {'mappings': {}}, {}, [], False)
        with patch.object(d, 'load', return_value=legacy), patch.object(c, 'connected') as connect:
            with self.assertRaisesRegex(ValueError, '旧项目'):
                c.sync(self.work, None, 'assets')
            connect.assert_not_called()

    def paired_shots(self):
        first = copy.deepcopy(self.unit['shots'][0])
        first['end'] = 2
        first.update(focus=['CHAR0001'], purpose='观看人物放笔的动作')
        second = copy.deepcopy(first)
        second.update(start=2, end=4, focus=['PROP0001'], purpose='看清笔与桌面的接触',
                      text='@{CHAR0001} 将笔放在桌上。')
        self.unit['shots'] = [first, second]

    def test_same_framing_and_optional_connector(self):
        self.paired_shots()
        d.validate_unit(self.state, self.unit, self.unit['span'])
        rendered = d.render_unit(self.state, self.unit)
        self.assertEqual(rendered.count('的中景'), 2)
        self.assertIn('·02秒-04秒[镜头2-放笔]', rendered)

    def test_focus_does_not_remove_supporting_assets_or_leak_intent(self):
        self.unit['shots'][0].update(focus=['CHAR0001'], purpose='后台意图：突出放笔动作')
        d.validate_unit(self.state, self.unit, self.unit['span'])
        rendered = d.render_unit(self.state, self.unit)
        self.assertIn('@笔 ', rendered)
        self.assertNotIn('后台意图', rendered)

    def test_reference_template_has_dynamic_heading_and_inline_connector(self):
        self.paired_shots()
        self.unit['shots'][1].update(label='落点', transition='动作匹配剪辑',
                                    connector='承接放笔动作。', sound_foreground='落桌轻响',
                                    sound_environment='室内底噪')
        rendered = d.render_unit(self.state, self.unit)
        lines = rendered.splitlines()
        self.assertEqual(lines[4], '人物：@陈默-基础形象 ；')
        self.assertEqual(len(lines), 8)
        self.assertNotIn(self.unit['global'], rendered)
        self.assertIn('·00秒-02秒[镜头1-放笔]：（景别：@陈默-基础形象 的中景；转场：硬切）', lines[6])
        self.assertIn('·02秒-04秒[镜头2-落点]：（景别：@笔 的中景；转场：动作匹配剪辑）', lines[7])
        self.assertIn('承接放笔动作。音效：落桌轻响+室内底噪。', lines[7])
        self.assertTrue(lines[6].endswith('音效：无。'))

    def test_reference_focal_field_counts_as_subject_reference(self):
        # The focus is referenced by the generated framing field; other participants remain explicit.
        d.validate_unit(self.state, self.unit, self.unit['span'])
        self.unit['shots'][0]['text'] = '将物品放在桌上。'
        with self.assertRaisesRegex(ValueError, '正文参与者'):
            d.validate_unit(self.state, self.unit, self.unit['span'])

    def test_reference_environment_shot_uses_scene_focus_and_source_evidence(self):
        self.state['records'][0]['text'] = '窗外细雨滴落屋檐'
        self.unit.update(character_ids=[], prop_ids=[])
        self.unit['global'] = '空间无人；窗框位于画面左侧。'
        shot = self.unit['shots'][0]
        shot.update(subjects=[], framing='远景', label='雨景', text='窗外细雨滴落屋檐。',
                    environment_evidence='窗外细雨滴落屋檐')
        shot.pop('focus')
        self.assertEqual(d.validate_unit(self.state, self.unit, self.unit['span']), [])
        rendered = d.render_unit(self.state, self.unit)
        self.assertIn('人物：无；', rendered)
        self.assertIn('（景别：@陈默卧室 的远景；转场：硬切）', rendered)
        shot.pop('environment_evidence')
        with self.assertRaisesRegex(ValueError, '环境证据'):
            d.validate_unit(self.state, self.unit, self.unit['span'])

    def test_reference_template_accepts_8_and_13_shots_without_30_second_suffix(self):
        for lengths in ((3, 3, 3, 3, 3, 3, 4, 4), (2,) * 9 + (3,) * 4):
            with self.subTest(lengths=lengths):
                unit = copy.deepcopy(self.unit)
                unit.update(duration=sum(lengths), count_reason='虚构样本：逐动作安排镜数')
                unit.pop('short_reason')
                unit['shots'] = []
                cursor = 0
                for i, length in enumerate(lengths):
                    shot = copy.deepcopy(self.unit['shots'][0])
                    shot.update(start=cursor, end=cursor + length, label=f'动作{i + 1}')
                    unit['shots'].append(shot)
                    cursor += length
                self.assertEqual(d.validate_unit(self.state, unit, unit['span']), [])
                rendered = d.render_unit(self.state, unit)
                self.assertEqual(sum(line.startswith('·') for line in rendered.splitlines()), len(lengths))
                self.assertIn(f'秒-{sum(lengths):02d}秒[镜头{len(lengths)}-动作{len(lengths)}]', rendered.splitlines()[-1])
                if sum(lengths) != 30:
                    self.assertNotIn('-30秒[', rendered)

    def test_reference_template_missing_new_fields_are_rejected(self):
        for field in ('label', 'transition', 'focus'):
            with self.subTest(field=field):
                unit = copy.deepcopy(self.unit)
                unit['shots'][0].pop(field)
                with self.assertRaisesRegex(ValueError, field):
                    d.validate_unit(self.state, unit, unit['span'])
        state = dict(self.state, style_hash='style')
        for version in (None, 'legacy'):
            with self.subTest(version=version):
                unit = copy.deepcopy(self.unit)
                if version is None:
                    unit.pop('output_format')
                else:
                    unit['output_format'] = version
                with self.assertRaisesRegex(ValueError, 'reference-v1'):
                    d.validate(state, {'mode': 'append', 'request_id': 'new', 'span': [0, 8], 'units': [unit]})

    def test_sound_fields_validate_references_and_convert_separately(self):
        self.unit['shots'][0]['sound_foreground'] = '@{PROP0001} 落桌轻响'
        self.unit['shots'][0]['sound_environment'] = ''
        d.validate_unit(self.state, self.unit, self.unit['span'])
        convert = c.reference_renderer({'笔': '<PROP>'})
        rendered = d.render_unit(self.state, self.unit, convert)
        self.assertIn('音效：<PROP> 落桌轻响。', rendered)
        self.assertNotIn('轻响+。', rendered)
        self.unit['shots'][0]['sound_environment'] = '无'
        rendered = d.render_unit(self.state, self.unit, convert)
        self.assertIn('音效：<PROP> 落桌轻响。', rendered)
        self.assertNotIn('轻响+无', rendered)
        self.unit['shots'][0]['sound_foreground'] = '无'
        rendered = d.render_unit(self.state, self.unit, convert)
        self.assertIn('音效：无。', rendered)
        self.assertNotIn('音效：无+无。', rendered)
        self.unit['shots'][0]['sound_environment'] = '@{CHAR9999} 喘息'
        with self.assertRaisesRegex(ValueError, '资产引用'):
            d.validate_unit(self.state, self.unit, self.unit['span'])

    def test_legacy_renderer_and_idempotent_receipt_keep_old_layout(self):
        unit = copy.deepcopy(self.unit)
        unit.pop('output_format')
        unit['shots'][0]['text'] = '@{CHAR0001} 的中景，将@{PROP0001} 放在桌上。'
        rendered = d.render_unit(self.state, unit)
        self.assertIn('人物：陈默-基础形象@陈默-基础形象 ；', rendered)
        self.assertIn('@陈默-基础形象 站在桌前。\n[0秒-4秒]', rendered)
        self.assertNotIn('[镜头1-', rendered)
        self.assertNotIn('音效：', rendered)
        request = {'mode': 'append', 'request_id': 'legacy', 'units': [unit]}
        request_path = self.work / 'request.json'
        output_path = self.work / 'legacy.txt'
        d.write_json(request_path, request)
        receipt = {'request_id': 'legacy', 'request_hash': d.sha(d.packed(request)),
                   'mode': 'append', 'output': str(output_path), 'units': [unit]}
        state = dict(self.state, receipts=[receipt])
        args = d.argparse.Namespace(request=request_path, project=self.work)
        with patch.object(d, 'validate') as validate:
            result = d.commit(args, state)
        validate.assert_not_called()
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(output_path.read_text(encoding='utf-8'), rendered + '\n')

    def test_measured_speech_requires_recording_basis(self):
        record = self.state['records'][0]
        record.update(kind='dialogue', speaker='陈默', voice='说')
        self.unit['audio'] = [{'record': record['id'], 'character_id': 'CHAR0001', 'voice': '说',
                              'text': record['text'], 'start': 0, 'end': 2.34, 'pause': .34,
                              'speed': '常速', 'units': 8, 'timing_basis': 'recording'}]
        d.dialogue_layout.attach(self.unit)
        with self.assertRaisesRegex(ValueError, '录音测量依据'):
            d.validate_unit(self.state, self.unit, self.unit['span'])
        self.unit['audio'][0]['recording_evidence'] = '虚构测试录音：测量区间0至2.34秒'
        self.assertEqual(d.validate_unit(self.state, self.unit, self.unit['span']), [])
        self.assertIn('实配音0秒至2.34秒', d.render_unit(self.state, self.unit))

    def test_unknown_or_empty_focus_rejected(self):
        for focus in (['CHAR9999'], [], ['CHAR0001', 'CHAR0001']):
            with self.subTest(focus=focus):
                self.unit['shots'][0]['focus'] = focus
                with self.assertRaisesRegex(ValueError, '观看焦点'):
                    d.validate_unit(self.state, self.unit, self.unit['span'])

    def test_optional_connector_still_checks_references(self):
        self.paired_shots()
        self.unit['shots'][1]['connector'] = '@{CHAR9999} 看向桌面。'
        with self.assertRaisesRegex(ValueError, '资产引用'):
            d.validate_unit(self.state, self.unit, self.unit['span'])

    def test_consecutive_close_shots_still_rejected(self):
        self.paired_shots()
        for shot in self.unit['shots']:
            shot.update(framing='近景', closeup_reason='动作高光')
            shot['text'] = shot['text'].replace('中景', '近景')
        with self.assertRaisesRegex(ValueError, '连续近景'):
            d.validate_unit(self.state, self.unit, self.unit['span'])

    def test_same_framing_across_units_allowed_but_close_pair_rejected(self):
        first = copy.deepcopy(self.unit)
        first.update(duration=26, count_reason='虚构回归样本：八镜均为中景关系切换', batch_id='batch')
        first.pop('short_reason')
        first['shots'] = []
        cursor = 0
        for length in (3, 3, 3, 3, 3, 3, 4, 4):
            shot = copy.deepcopy(self.unit['shots'][0])
            shot.update(start=cursor, end=cursor + length)
            first['shots'].append(shot)
            cursor += length
        second = copy.deepcopy(first)
        second.update(id='场景1-分镜2', span=[8, 16])
        state = copy.deepcopy(self.state)
        state.update(length=16, style_hash='style', units=[first, second],
                     ranges=[{'span': [0, 16], 'batch_id': 'batch'}])
        record = copy.deepcopy(state['records'][0])
        record.update(id='L000002', start=8, end=16)
        state['records'].append(record)
        request = {'mode': 'replace', 'request_id': 'camera-boundary', 'units': [copy.deepcopy(first)]}
        self.assertEqual(d.validate(state, request), [])
        for shot in (request['units'][0]['shots'][-1], state['units'][1]['shots'][0]):
            shot.update(framing='近景', closeup_reason='动作高光')
            shot['text'] = shot['text'].replace('中景', '近景')
        with self.assertRaisesRegex(ValueError, '边界禁止连续近景'):
            d.validate(state, request)


if __name__ == '__main__':
    unittest.main(verbosity=2)
