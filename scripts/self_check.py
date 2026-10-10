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
            'scene_ids': ['SC0001'], 'main_scene': 'SC0001', 'character_ids': ['CHAR0001'],
            'prop_ids': ['PROP0001'], 'time': '白天', 'weather': '未明示', 'style': '3D国风动漫',
            'global': '@{SET0001} 室内构图。', 'start_state': '站立', 'end_state': '放笔',
            'short_reason': 'range_end', 'audio': [],
            'shots': [{'start': 0, 'end': 4, 'framing': '中景', 'scene_id': 'SC0001',
                      'subjects': ['CHAR0001', 'PROP0001'], 'angle': '正面',
                      'text': '@{CHAR0001}的中景，将@{PROP0001} 放在桌上。', 'dialogue_parts': []}]}

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
