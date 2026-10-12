#!/usr/bin/env python3
"""Behavior regressions for the authorized standardizer audit repairs."""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / 'modules/screenplay-standardizer/scripts'))
import asset_ledger as ledger
import asset_migrate as migration
import screenplay_io as screenplay_io
import screenplay_review as review

TEST_ROOT = SKILL.parent / 'tests-standardizer'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(value, encoding='utf-8')


class StandardizerAuditRepairs(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='standardizer-', dir=TEST_ROOT)
        self.work = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def review_fixture(self, source, body):
        save(self.work / 'source.json', {'paragraphs': [{'id': 1, 'text': source}]})
        save(self.work / 'scenes/scene_001_0001-0001.txt',
             '场景1-日-内-房间\n人物：阿明-基础形象；阿红-基础形象；\n' + body + '\n')
        return review.make_report(self.work, {})

    def asset_fixture(self, subdir, paragraphs, character=True):
        work = self.work / subdir
        source = work / 'source.json'
        script = work / 'script.txt'
        save(source, {'paragraphs': [{'id': i, 'text': text}
                                    for i, text in enumerate(paragraphs, 1)]})
        cast = '人物：阿明-基础形象；' if character else '人物：无；'
        drafts = []
        data = {'schema_version': 3, 'source_digest': ledger.digest(paragraphs),
                'scenes': [], 'characters': [], 'props': [],
                'locations': [{'id': 'LOC0001', 'name': '房间', 'aliases': [],
                               'visual_facts': [], 'identity_status': 'confirmed',
                               'identity_basis': '原文均指向同一房间'}],
                'scene_assets': [{'id': 'SET0001', 'name': '房间', 'location_id': 'LOC0001',
                                  'variant': 'base', 'parent_id': None, 'visual_facts': []}],
                'retired_ids': [], 'inactive_assets': {'characters': []}, 'pending_reviews': []}
        if character:
            data['characters'].append({'id': 'CHAR0001', 'name': '阿明-基础形象',
                                       'source_entry': '阿明-基础形象', 'aliases': [],
                                       'visual_facts': [], 'personality_facts': []})
        for i, paragraph in enumerate(paragraphs, 1):
            heading = f'场景{i}-日-内-房间'
            draft = heading + '\n' + cast + '\n△ ' + paragraph + '\n'
            drafts.append(draft)
            save(work / f'scenes/scene_{i:03d}_{i:04d}-{i:04d}.txt', draft)
            data['scenes'].append({'id': f'SC{i:04d}', 'number': i, 'heading': heading,
                                   'source_range': [i, i], 'location_id': 'LOC0001',
                                   'scene_asset_id': 'SET0001',
                                   'character_ids': ['CHAR0001'] if character else [],
                                   'prop_ids': [], 'visual_facts': [],
                                   'time_basis': {'kind': '格式默认', 'source_paragraphs': []}})
        save(script, '\n'.join(drafts))
        return work, script, source, data

    def render(self, work, script, source, data):
        path, output = work / 'ledger.json', work / 'catalog.txt'
        save(path, data)
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.render(script, source, path, output)
        return output.read_text(encoding='utf-8')

    def test_action_subject_is_not_the_later_speaker(self):
        report = self.review_fixture('阿明看向阿红，阿红说「走吧。」',
                                     '△ 阿明看向阿红，阿红说\n阿明：走吧。')
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['counts']['missing'], 0)
        self.assertEqual(report['counts']['extra'], 0)
        self.assertEqual(report['counts']['speaker'], 1)
        self.assertIsNone(report['scenes'][0]['speaker_items'][0]['cue_hint'])

    def test_complex_cue_can_be_confirmed_after_correcting_speaker(self):
        report = self.review_fixture('阿明看向阿红，阿红说「走吧。」',
                                     '△ 阿明看向阿红，阿红说\n阿红：走吧。')
        item = report['scenes'][0]['speaker_items'][0]
        decision = dict(item['decision_template'], speaker='阿红')
        self.assertEqual(review.make_report(self.work, {item['decision_key']: decision})['status'], 'passed')

    def test_simple_direct_speech_still_passes(self):
        for source, action in [('阿红说「走吧。」', '阿红说'),
                               ('阿红低声说「走吧。」', '阿红低声说')]:
            with self.subTest(source=source):
                report = self.review_fixture(source, f'△ {action}\n阿红：走吧。')
                self.assertEqual(report['status'], 'passed')

    def test_multiple_quotes_do_not_reuse_first_speaker(self):
        report = self.review_fixture('阿明说「你好。」阿红说「再见。」',
                                     '△ 阿明说\n阿明：你好。\n△ 阿红说\n阿红：再见。')
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['counts']['speaker'], 2)

    def test_legacy_cached_wrong_speaker_is_rechecked(self):
        report = self.review_fixture('阿明看向阿红，阿红说「走吧。」',
                                     '△ 阿明看向阿红，阿红说\n阿明：走吧。')
        previous = copy.deepcopy(report)
        previous.update(review_version=3, status='passed')
        previous['counts']['speaker'] = 0
        item = previous['scenes'][0]['speaker_items'][0]
        item.update(cue_hint='阿明', issue=[])
        rechecked = review.make_report(self.work, {}, previous)
        self.assertEqual(rechecked['status'], 'needs_review')
        self.assertEqual(rechecked['counts']['speaker'], 1)
        self.assertEqual(rechecked['counts']['reused_scenes'], 0)

    def test_current_valid_review_cache_is_still_reused(self):
        report = self.review_fixture('阿红说「走吧。」', '△ 阿红说\n阿红：走吧。')
        reused = review.make_report(self.work, {}, report)
        self.assertEqual(reused['status'], 'passed')
        self.assertEqual(reused['counts']['reused_scenes'], 1)

    def test_render_rejects_evidence_borrowed_from_another_scene(self):
        work, script, source, data = self.asset_fixture('borrowed', ['他穿蓝衣。', '他穿红衣。'])
        data['characters'][0]['visual_facts'] = [
            {'text': '穿红衣', 'evidence': '穿红衣', 'source_paragraphs': [1, 2],
             'scene_ids': ['SC0001', 'SC0002'], 'attribution': '叙述'}]
        with self.assertRaisesRegex(ValueError, 'SC0001.*证据|证据.*SC0001'):
            self.render(work, script, source, data)

    def test_same_evidence_explicit_in_each_scene_is_valid(self):
        work, script, source, data = self.asset_fixture('shared', ['他穿红衣。', '她说他穿红衣。'])
        data['characters'][0]['visual_facts'] = [
            {'text': '穿红衣', 'evidence': '穿红衣', 'source_paragraphs': [1, 2],
             'scene_ids': ['SC0001', 'SC0002'], 'attribution': '叙述'}]
        self.assertIn('穿红衣', self.render(work, script, source, data))

    def test_nonadjacent_paragraphs_cannot_invent_continuous_evidence(self):
        fact = {'text': '穿红衣', 'evidence': '穿红衣', 'source_paragraphs': [1, 3],
                'scene_ids': ['SC0001'], 'attribution': '叙述'}
        scenes = {'SC0001': {'source_range': [1, 3]}}
        with self.assertRaisesRegex(ValueError, '连续原文'):
            ledger.fact_list([fact], ['他穿', '一件蓝衣。', '红衣'], scenes, 'CHAR0001')
        fact['source_paragraphs'] = [1, 2]
        ledger.fact_list([fact], ['他穿', '红衣'], {'SC0001': {'source_range': [1, 2]}}, 'CHAR0001')

    def test_source_rename_keeps_production_name_and_updates_voice(self):
        old_work, _, old_source, old = self.asset_fixture('old', ['阿明：我回来了。'])
        old['characters'][0].update(name='陈默-第二世-阿明-基础形象', source_speaker='阿明')
        old_path = old_work / 'old-ledger.json'
        save(old_path, old)
        new_work, new_script, new_source, _ = self.asset_fixture('new', ['阿红：我回来了。'])
        new_cast = new_script.read_text(encoding='utf-8').replace('人物：阿明-', '人物：阿红-')
        save(new_script, new_cast)
        save(new_work / 'scenes/scene_001_0001-0001.txt', new_cast)
        revised, report = migration.migrate(old_path, old_source, new_script, new_work,
                                            {'scenes': {'SC0001': 1},
                                             'characters': {'CHAR0001': '阿红-基础形象'}})
        character = revised['characters'][0]
        self.assertEqual(character['id'], 'CHAR0001')
        self.assertEqual(character['name'], '陈默-第二世-阿明-基础形象')
        self.assertEqual(character['source_entry'], '阿红-基础形象')
        self.assertEqual(character['source_speaker'], '阿红')
        self.assertEqual([item['kind'] for item in report['pending_reviews']], ['布景版本关联待核'])
        # The changed scene's existing set association has now been explicitly reviewed.
        revised['pending_reviews'] = []
        self.assertIn('陈默-第二世-阿明-基础形象', self.render(new_work, new_script, new_source, revised))

    def test_legacy_rename_creates_source_entry_without_changing_name(self):
        old_work, _, old_source, old = self.asset_fixture('legacy-old', ['阿明走进房间。'])
        old['characters'][0].pop('source_entry')
        old_path = old_work / 'old-ledger.json'
        save(old_path, old)
        new_work, new_script, _, _ = self.asset_fixture('legacy-new', ['阿红走进房间。'])
        content = new_script.read_text(encoding='utf-8').replace('人物：阿明-', '人物：阿红-')
        save(new_script, content)
        save(new_work / 'scenes/scene_001_0001-0001.txt', content)
        revised, _ = migration.migrate(old_path, old_source, new_script, new_work,
                                        {'scenes': {'SC0001': 1},
                                         'characters': {'CHAR0001': '阿红-基础形象'}})
        self.assertEqual(revised['characters'][0]['name'], '阿明-基础形象')
        self.assertEqual(revised['characters'][0]['source_entry'], '阿红-基础形象')

    def test_decor_catalog_inherits_common_base_elements(self):
        work, script, source, data = self.asset_fixture('decor', ['房间有木桌。', '房间挂满婚礼装饰。'], False)
        data['scene_assets'][0]['visual_facts'] = [
            {'text': '木桌', 'evidence': '木桌', 'source_paragraphs': [1],
             'scene_ids': ['SC0001'], 'attribution': '叙述'}]
        data['scene_assets'].append({'id': 'SET0002', 'name': '房间-婚礼布置', 'location_id': 'LOC0001',
                                    'variant': 'decor', 'parent_id': 'SET0001', 'visual_facts': [
                                        {'text': '婚礼装饰', 'evidence': '婚礼装饰', 'source_paragraphs': [2],
                                         'scene_ids': ['SC0002'], 'attribution': '叙述'}]})
        data['scenes'][1]['scene_asset_id'] = 'SET0002'
        self.assertIn('房间-婚礼布置｜关键元素：木桌、婚礼装饰。', self.render(work, script, source, data))

    def test_removed_element_is_scoped_to_versions_not_common_base(self):
        work, script, source, data = self.asset_fixture('removed', ['砖墙前有木桌。', '木桌已移走。'], False)
        data['scene_assets'][0]['visual_facts'] = [
            {'text': '砖墙', 'evidence': '砖墙', 'source_paragraphs': [1],
             'scene_ids': ['SC0001'], 'attribution': '叙述'}]
        for i, name, text in [(2, '有桌布置', '木桌'), (3, '移桌布置', '木桌已移走')]:
            source_id = i - 1
            data['scene_assets'].append({'id': f'SET{i:04d}', 'name': '房间-' + name,
                                        'location_id': 'LOC0001', 'variant': 'decor',
                                        'parent_id': 'SET0001', 'visual_facts': [
                                            {'text': text, 'evidence': text, 'source_paragraphs': [source_id],
                                             'scene_ids': [f'SC{source_id:04d}'], 'attribution': '叙述'}]})
            data['scenes'][source_id - 1]['scene_asset_id'] = f'SET{i:04d}'
        catalog = self.render(work, script, source, data)
        self.assertIn('房间-有桌布置｜关键元素：砖墙、木桌。', catalog)
        self.assertIn('房间-移桌布置｜关键元素：砖墙、木桌已移走。', catalog)
        self.assertNotIn('砖墙、木桌、木桌已移走', catalog)

    def test_punctuation_rewrite_requires_explicit_scene_mapping(self):
        old_work, _, old_source, old = self.asset_fixture('exact-old', ['不，是他。'])
        old_path = old_work / 'old-ledger.json'
        save(old_path, old)
        new_work, new_script, _, _ = self.asset_fixture('exact-new', ['不是他。'])
        revised, report = migration.migrate(old_path, old_source, new_script, new_work, {})
        self.assertIsNone(revised)
        self.assertEqual(report['status'], '需要场景对应')
        self.assertEqual(report['pending_reviews'][0]['exact_candidates'], [])
        revised, _ = migration.migrate(old_path, old_source, new_script, new_work,
                                        {'scenes': {'SC0001': 1}})
        self.assertEqual(revised['scenes'][0]['id'], 'SC0001')

    def test_unchanged_unique_source_still_reuses_scene_id(self):
        work, script, source, data = self.asset_fixture('same', ['他走进房间。'])
        path = work / 'old-ledger.json'
        save(path, data)
        revised, report = migration.migrate(path, source, script, work, {})
        self.assertEqual(revised['scenes'][0]['id'], 'SC0001')
        self.assertEqual(report['pending_reviews'], [])

    def test_paragraph_boundaries_and_duplicate_candidates_are_not_exact_unique(self):
        old_scene = [{'id': 'SC0001', 'source_range': [1, 2]}]
        mapping, pending = migration.normalize_mapping({}, old_scene, [('甲乙',)], ['甲', '乙'])
        self.assertEqual(mapping, {})
        self.assertEqual(len(pending), 1)
        old_scene[0]['source_range'] = [1, 1]
        mapping, pending = migration.normalize_mapping({}, old_scene, [('甲',), ('甲',)], ['甲'])
        self.assertEqual(mapping, {})
        self.assertEqual(pending[0]['exact_candidates'], [1, 2])

    def test_docx_textbox_text_is_extracted_once_with_paragraph_ownership(self):
        path = self.work / 'textbox.docx'
        xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:r><w:t>正文前。</w:t></w:r><w:r><w:drawing><w:txbxContent><w:p><w:r>
        <w:t>文本框台词。</w:t><w:tab/><w:t>后半句。</w:t><w:br/></w:r></w:p></w:txbxContent>
        </w:drawing></w:r><w:r><w:t>正文后。</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格段落。</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:p><w:r><w:t>末段。</w:t></w:r></w:p></w:body></w:document>'''
        with zipfile.ZipFile(path, 'w') as container:
            container.writestr('word/document.xml', xml)
        self.assertEqual(screenplay_io.read_source(path),
                         ['正文前。正文后。', '文本框台词。\t后半句。\n', '表格段落。', '末段。'])
        with contextlib.redirect_stdout(io.StringIO()):
            screenplay_io.prepare(path, self.work / 'prepared')
        paragraphs = ledger.source_paragraphs(self.work / 'prepared/source.json')
        self.assertEqual(sum(text.count('文本框台词。') for text in paragraphs), 1)

    def test_windows_device_names_are_rejected_before_asset_delivery(self):
        for name in ('CON', 'con.PNG', 'NUL', 'AUX', 'PRN', 'COM1', 'LPT9', 'COM¹', 'CON .jpg'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, '保留设备名'):
                ledger.reference_preflight({'characters': [{'id': 'CHAR0001', 'name': name}]})
        for name in ('CON-角色', 'COM10', 'COM1号', '房间', '阿明-基础形象'):
            with self.subTest(name=name):
                ledger.validate_image_name(name)


if __name__ == '__main__':
    unittest.main()
