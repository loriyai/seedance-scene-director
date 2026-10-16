"""Same-person masked assets retain source identity and event-time state."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

MODULE = Path(__file__).resolve().parent.parent / 'modules/screenplay-standardizer/scripts'
sys.path.insert(0, str(MODULE))
import asset_ledger as ledger
import asset_migrate as migration
import character_variants as variants


def fixture():
    paragraphs = ['阿明未戴头套站在门口。', '阿明戴上猪猪头套。', '阿明摘下猪猪头套。']
    cast = '阿明-白衬衫'
    data = {'schema_version': 3, 'source_digest': ledger.digest(paragraphs),
            'characters': [{'id': 'CHAR0001', 'name': cast, 'source_entry': cast,
                            'source_speaker': '阿明', 'aliases': [], 'visual_facts': [], 'personality_facts': []}],
            'scenes': [{'id': 'SC0001', 'number': 1, 'heading': '场景1-日-外-门口',
                        'source_range': [1, 3], 'location_id': 'LOC0001', 'scene_asset_id': 'SET0001',
                        'character_ids': ['CHAR0001'], 'prop_ids': [], 'visual_facts': [],
                        'time_basis': {'kind': '格式默认', 'source_paragraphs': []}}],
            'locations': [{'id': 'LOC0001', 'name': '门口', 'aliases': [], 'visual_facts': [],
                           'identity_status': 'confirmed', 'identity_basis': '原文明示门口'}],
            'scene_assets': [{'id': 'SET0001', 'name': '门口', 'location_id': 'LOC0001',
                              'variant': 'base', 'parent_id': None, 'visual_facts': []}],
            'props': [], 'retired_ids': [], 'inactive_assets': {'characters': []}, 'pending_reviews': []}
    track = {'scene_id': 'SC0001', 'base_asset_id': 'CHAR0001', 'initial_asset_id': 'CHAR0001',
             'initial_evidence': {'source_paragraphs': [1], 'evidence': paragraphs[0], 'basis': '未戴状态原文明示'},
             'transitions': [{'event_id': 'wear', 'from_asset_id': 'CHAR0001', 'to_asset_id': 'CHAR0002',
                              'source_paragraphs': [2], 'evidence': paragraphs[1], 'basis': '本次戴上'},
                             {'event_id': 'remove', 'from_asset_id': 'CHAR0002', 'to_asset_id': 'CHAR0001',
                              'source_paragraphs': [3], 'evidence': paragraphs[2], 'basis': '本次摘下'}]}
    review = {'policy': variants.POLICY, 'reviewed': True, 'source_digest': data['source_digest'],
              'variants': [{'id': 'CHAR0002', 'base_asset_id': 'CHAR0001', 'name': '阿明-白衬衫-戴猪猪头套',
                            'label': '戴猪猪头套', 'kind': 'headgear', 'design_basis': '遮住全头并连续跨镜，需稳定整体外观'}],
              'scene_appearances': [track]}
    script = '场景1-日-外-门口\n人物：' + cast + '；\n' + '\n'.join('△ ' + text for text in paragraphs) + '\n'
    return paragraphs, data, review, script


def storyboard(data, paragraphs):
    records, cursor = [], 0
    for number, text in enumerate(paragraphs, 1):
        count = len(variants.compact(text))
        records.append({'id': f'L{number:06d}', 'scene': 'SC0001', 'text': text,
                        'start': cursor, 'end': cursor + count, 'kind': 'action'})
        cursor += count
    initial = {'person': {'present': True, 'asset_id': 'CHAR0001', 'facts': {}}}
    unit = {'span': [0, cursor], 'scene_ids': ['SC0001'],
            'continuity': {'entities': {'person': {'asset_ids': ['CHAR0001', 'CHAR0002']}},
                           'initial': initial, 'final': copy.deepcopy(initial)}, 'shots': []}
    for number, target in enumerate((None, 'CHAR0002', 'CHAR0001')[:len(paragraphs)], 1):
        change = [] if target is None else [{'entity': 'person', 'set': {'asset_id': target},
                                             'evidence': [{'record': f'L{number:06d}', 'quote': paragraphs[number-1]}]}]
        unit['shots'].append({'scene_id': 'SC0001', 'continuity': {'changes': change}})
    return {'ledger': data, 'records': records}, unit


class CharacterVariantTests(unittest.TestCase):
    def setUp(self):
        self.paragraphs, self.base, self.review, self.script = fixture()
        self.data = variants.apply_variant_review(self.base, self.review, self.paragraphs)

    def test_source_cast_unchanged_and_catalog_has_both_appearances(self):
        self.assertEqual(self.data['scenes'][0]['character_ids'], ['CHAR0001'])
        self.assertEqual(variants.scene_character_ids(self.data['scenes'][0]), ['CHAR0001', 'CHAR0002'])
        self.assertEqual(variants.canonical_character_id(self.data, 'CHAR0002'), 'CHAR0001')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'script.txt').write_text(self.script, encoding='utf-8')
            (path / 'source.json').write_text(json.dumps({'paragraphs': [{'id': i, 'text': text}
                 for i, text in enumerate(self.paragraphs, 1)]}), encoding='utf-8')
            (path / 'ledger.json').write_text(json.dumps(self.data), encoding='utf-8')
            ledger.render(path / 'script.txt', path / 'source.json', path / 'ledger.json', path / 'catalog.txt')
            catalog = (path / 'catalog.txt').read_text(encoding='utf-8')
            self.assertIn('角色02 - 阿明-白衬衫-戴猪猪头套', catalog)
            self.assertEqual(catalog.count('出现的场景：场景1。'), 2)

    def test_repeat_review_preserves_ids_without_mutating_input(self):
        review = copy.deepcopy(self.review)
        review['variants'][0].pop('id')
        again = variants.apply_variant_review(self.data, review, self.paragraphs)
        self.assertEqual(again, self.data)
        self.assertEqual(len(self.base['characters']), 1)
        self.assertNotIn('character_variant_policy', self.base)

    def test_policy_cannot_be_silently_omitted(self):
        self.data.pop('character_variant_policy')
        with self.assertRaisesRegex(ValueError, '显式启用'):
            variants.validate_character_variants(self.data, self.paragraphs)

    def test_stale_review_and_duplicate_source_person_rejected(self):
        with self.subTest('stale'):
            stale = copy.deepcopy(self.data)
            stale['characters'][1]['appearance_variant']['source_digest'] = 'old'
            with self.assertRaisesRegex(ValueError, '已过期'):
                variants.validate_character_variants(stale, self.paragraphs)
        self.data['scenes'][0]['character_ids'].append('CHAR0002')
        with self.assertRaisesRegex(ValueError, '第二个人'):
            variants.validate_character_variants(self.data, self.paragraphs)

    def test_wrong_subject_alias_or_recursive_base_rejected(self):
        for field, value, message in [('source_speaker', '阿红', '原人物条目与声源'),
                                       ('source_entry', '阿红-基础形象', '原人物条目与声源')]:
            data = copy.deepcopy(self.data)
            data['characters'][1][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                variants.validate_character_variants(data, self.paragraphs)
        self.data['characters'][1]['appearance_variant']['base_asset_id'] = 'CHAR0002'
        with self.assertRaisesRegex(ValueError, '递归继承'):
            variants.validate_character_variants(self.data, self.paragraphs)

    def test_evidence_and_transition_chain_fail_independently(self):
        mutations = [('evidence', '凭空出现', '原文连续段落'),
                     ('source_paragraphs', [1, 3], '连续且不重复'),
                     ('from_asset_id', 'CHAR0002', '当前同一角色')]
        for field, value, message in mutations:
            data = copy.deepcopy(self.data)
            data['scenes'][0]['character_appearances'][0]['transitions'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                variants.validate_character_variants(data, self.paragraphs)

    def test_normalize_preserves_identity_and_track(self):
        normalized, _ = ledger.normalize_assets(self.data)
        self.assertEqual(normalized['characters'], self.data['characters'])
        self.assertEqual(normalized['scenes'][0]['character_appearances'], self.data['scenes'][0]['character_appearances'])
        self.assertEqual(normalized['scenes'][0]['character_ids'], ['CHAR0001'])

    def test_three_people_share_prop_design_without_collapsing_physical_count(self):
        paragraphs = ['阿明、阿红、阿蓝均未戴头套。', '三人戴上猪猪头套。', '三人摘下猪猪头套。']
        data = copy.deepcopy(self.base)
        data['source_digest'] = ledger.digest(paragraphs)
        data['props'] = [{'id': 'PROP0001', 'name': '猪猪头套', 'aliases': [], 'visual_facts': []}]
        scene = data['scenes'][0]
        scene['prop_ids'] = ['PROP0001']
        scene['prop_occurrences'] = [{'id': 'three-masks', 'asset_id': 'PROP0001', 'count': 3,
                                      'identity_status': '原文明示各戴一个'}]
        review = copy.deepcopy(self.review)
        review['source_digest'] = data['source_digest']
        review['variants'], review['scene_appearances'] = [], []
        for index, person in enumerate(('阿明', '阿红', '阿蓝')):
            base_id, masked_id = f'CHAR{index * 2 + 1:04d}', f'CHAR{index * 2 + 2:04d}'
            if index:
                base = copy.deepcopy(data['characters'][0])
                base.update(id=base_id, name=person + '-白衬衫', source_entry=person + '-白衬衫', source_speaker=person)
                data['characters'].append(base)
                scene['character_ids'].append(base_id)
            value = copy.deepcopy(self.review['variants'][0])
            value.update(id=masked_id, base_asset_id=base_id, name=person + '-白衬衫-戴猪猪头套', prop_asset_id='PROP0001')
            review['variants'].append(value)
            track = copy.deepcopy(self.review['scene_appearances'][0])
            track.update(base_asset_id=base_id, initial_asset_id=base_id)
            track['initial_evidence']['evidence'] = paragraphs[0]
            for event, before, after, quote in zip(track['transitions'], (base_id, masked_id),
                                                   (masked_id, base_id), paragraphs[1:]):
                event.update(from_asset_id=before, to_asset_id=after, evidence=quote)
            review['scene_appearances'].append(track)
        registered = variants.apply_variant_review(data, review, paragraphs)
        normalized, _ = ledger.normalize_assets(registered)
        self.assertEqual(len(normalized['characters']), 6)
        self.assertEqual(len(normalized['props']), 1)
        self.assertEqual(normalized['scenes'][0]['prop_occurrences'][0]['count'], 3)

    def test_variants_cli_writes_new_draft_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'script.txt').write_text(self.script, encoding='utf-8')
            values = {'source.json': {'paragraphs': [{'id': i, 'text': text}
                       for i, text in enumerate(self.paragraphs, 1)]}, 'ledger.json': self.base, 'review.json': self.review}
            for name, value in values.items():
                (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            command = [sys.executable, '-B', str(MODULE / 'asset_ledger.py'), 'variants', str(root / 'script.txt'),
                       str(root / 'source.json'), str(root / 'ledger.json'), str(root / 'review.json'),
                       '--output', str(root / 'draft.json')]
            before = (root / 'ledger.json').read_bytes()
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / 'draft.json').read_text(encoding='utf-8')), self.data)
            second = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('新文件', second.stderr)
            self.assertEqual((root / 'ledger.json').read_bytes(), before)

    def test_legacy_ledger_has_no_variant_gate(self):
        variants.validate_character_variants(self.base, self.paragraphs)
        variants.validate_storyboard({'ledger': self.base}, {})

    def test_storyboard_base_mask_base_switch(self):
        state, unit = storyboard(self.data, self.paragraphs)
        variants.validate_storyboard(state, unit)

    def test_cannot_use_mask_before_source_event(self):
        state, unit = storyboard(self.data, self.paragraphs)
        unit['continuity']['initial']['person']['asset_id'] = 'CHAR0002'
        with self.assertRaisesRegex(ValueError, '不能提前'):
            variants.validate_storyboard(state, unit)

    def test_partial_range_inherits_completed_wear(self):
        state, unit = storyboard(self.data, self.paragraphs)
        unit['span'][0] = state['records'][1]['end']
        unit['shots'] = unit['shots'][2:]
        unit['continuity']['initial']['person']['asset_id'] = 'CHAR0002'
        variants.validate_storyboard(state, unit)

    def test_same_person_cannot_be_two_entities(self):
        state, unit = storyboard(self.data, self.paragraphs)
        unit['continuity']['entities'] = {'unmasked': {'asset_ids': ['CHAR0001']},
                                         'masked': {'asset_ids': ['CHAR0002']}}
        with self.assertRaisesRegex(ValueError, '同一个连续性实体'):
            variants.validate_storyboard(state, unit)

    def add_explicit_clothing(self, *, same_identity=True, same_incarnation=True):
        appearance = {'identity': '阿明', 'incarnation': '当前世', 'variant': '白衬衫',
                      'active_from': 'SC0001', 'trigger': 'source_appearance',
                      'basis': self.paragraphs[0]}
        self.data['characters'][0]['appearance'] = copy.deepcopy(appearance)
        other = copy.deepcopy(self.data['characters'][0])
        other.update(id='CHAR0003', name='阿明-黑外套', source_entry='阿明-黑外套')
        other['appearance'].update(variant='黑外套')
        if not same_identity:
            other['appearance']['identity'] = '另一个人'
        if not same_incarnation:
            other['appearance']['incarnation'] = '另一世'
        self.data['characters'].append(other)

    def test_explicit_same_identity_clothing_alias_is_not_a_second_person(self):
        self.add_explicit_clothing()
        state, unit = storyboard(self.data, self.paragraphs)
        unit['continuity']['entities']['person']['asset_ids'].append('CHAR0003')
        variants.validate_storyboard(state, unit)

    def test_same_name_or_speaker_cannot_substitute_for_explicit_identity(self):
        for mode in ('different_identity', 'different_incarnation', 'no_identity'):
            with self.subTest(mode=mode):
                self.data = variants.apply_variant_review(self.base, self.review, self.paragraphs)
                self.add_explicit_clothing(same_identity=mode != 'different_identity',
                                           same_incarnation=mode != 'different_incarnation')
                if mode == 'no_identity':
                    self.data['characters'][-1].pop('appearance')
                state, unit = storyboard(self.data, self.paragraphs)
                unit['continuity']['entities']['person']['asset_ids'].append('CHAR0003')
                with self.assertRaisesRegex(ValueError, '另一个人物或道具'):
                    variants.validate_storyboard(state, unit)

    def test_cross_scene_clothing_change_uses_normal_source_evidence_in_director(self):
        import director as d
        self.add_explicit_clothing()
        flashback = '画面转入回忆，阿明穿黑外套站在走廊。'
        paragraphs = self.paragraphs[:2] + [flashback]
        self.data['source_digest'] = ledger.digest(paragraphs)
        self.data['characters'][1]['appearance_variant']['source_digest'] = self.data['source_digest']
        self.data['characters'][-1]['appearance'].update(active_from='SC0002', basis=flashback)
        first = self.data['scenes'][0]
        first['source_range'] = [1, 2]
        first['character_appearances'][0]['transitions'] = first['character_appearances'][0]['transitions'][:1]
        second = copy.deepcopy(first)
        second.update(id='SC0002', number=2, heading='场景2-日-内-走廊', source_range=[3, 3],
                      character_ids=['CHAR0003'], scene_asset_id='SET0002')
        second.pop('character_appearances')
        self.data['scenes'].append(second)
        self.data['scene_assets'].append({'id': 'SET0002', 'name': '走廊'})
        script_text = '场景1-日-外-门口\n人物：阿明-白衬衫；\n' + '\n'.join('△ ' + s for s in paragraphs[:2])
        script_text += '\n场景2-日-内-走廊\n人物：阿明-黑外套；\n△ ' + flashback + '\n'
        records, length = d.build_records(script_text, self.data)
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'script.txt'
            script.write_text(script_text, encoding='utf-8')
            state = {'ledger': self.data, 'records': records, 'length': length,
                     'files': {'script': {'path': str(script)}}, 'entry': {'min': 4, 'durations': []},
                     'style_hash': d.sha('国漫'), 'units': [], 'ranges': [], 'receipts': []}
            initial = {'person': {'present': True, 'asset_id': 'CHAR0001', 'facts': {}}}
            unit = {'span': [0, length], 'output_format': 'reference-v3', 'duration': 6,
                    'main_scene': 'SC0001', 'scene_ids': ['SC0001', 'SC0002'],
                    'character_ids': ['CHAR0001', 'CHAR0002', 'CHAR0003'], 'prop_ids': [],
                    'time': '白天', 'weather': '未明示', 'global': '@{CHAR0001} 站在门口。',
                    'start_state': '阿明未戴头套。', 'end_state': '回忆中的阿明穿黑外套站在走廊。',
                    'short_reason': 'natural_beat', 'short_basis': '戴头套动作引出回忆中的另一服装形象。',
                    'cross_scene_reason': '回忆', 'cross_scene_evidence': flashback,
                    'audio': [], 'shots': [], 'continuity': {'version': 1, 'boundary': {'kind': 'continuous'},
                        'entities': {'person': {'asset_ids': ['CHAR0001', 'CHAR0002', 'CHAR0003']}},
                        'initial': initial, 'final': {'person': {'present': True, 'asset_id': 'CHAR0003', 'facts': {}}}}}
            for index, (record, target) in enumerate(zip(records, ('CHAR0001', 'CHAR0002', 'CHAR0003'))):
                change = [] if index == 0 else [{'entity': 'person', 'set': {'asset_id': target},
                         'evidence': [{'record': record['id'], 'quote': record['text']}]}]
                unit['shots'].append({'start': index*2, 'end': index*2+2, 'scene_id': record['scene'],
                    'framing': '中景', 'angle': '斜侧', 'subjects': [target], 'focus': [target],
                    'label': '当前造型', 'transition': '硬切', 'purpose': '通过当前造型体现原文动作及回忆时空变化。',
                    'camera': '侧前方平视固定', 'composition': '人物与身后的门框分层可辨',
                    'depth_of_field': '人物清楚，门框略柔', 'lighting': '侧方自然日光照亮衣物轮廓',
                    'text': f'@{{{target}}} 完成当前原文动作。', 'dialogue_parts': [],
                    'continuity': {'changes': change, 'visibility': {'person': {'mode': 'visible',
                                             'basis': '当前形象在门框前完整入镜'}}}})
            request = {'mode': 'append', 'request_id': 'masked-clothing-flashback', 'span': [0, length], 'units': [unit]}
            # Module integrity is tested by the package suite. This integration
            # test exercises director, ordinary continuity and variant behavior.
            with mock.patch.object(d, 'dependency', return_value=None):
                d.validate(state, request)
                unit['shots'][2]['continuity']['changes'][0]['evidence'] = [
                    {'record': records[1]['id'], 'quote': records[1]['text']}]
                with self.assertRaisesRegex(ValueError, '进入场次的原文证据'):
                    d.validate(state, request)

    def test_new_scene_initial_state_does_not_invent_an_on_screen_remove_event(self):
        texts = ['阿明戴着猪猪头套站在门口。', '次日，阿明未戴头套站在门口。']
        state, unit = storyboard(self.data, texts)
        second = copy.deepcopy(self.data['scenes'][0])
        second.update(id='SC0002', number=2, character_ids=['CHAR0001'])
        second.pop('character_appearances')
        self.data['scenes'].append(second)
        track = self.data['scenes'][0]['character_appearances'][0]
        track.update(initial_asset_id='CHAR0002', transitions=[])
        state['records'][1]['scene'] = 'SC0002'
        unit['scene_ids'].append('SC0002')
        unit['continuity']['initial']['person']['asset_id'] = 'CHAR0002'
        unit['shots'] = [{'scene_id': 'SC0001', 'continuity': {'changes': []}},
                         {'scene_id': 'SC0002', 'continuity': {'changes': [
                             {'entity': 'person', 'set': {'asset_id': 'CHAR0001'},
                              'evidence': [{'record': 'L000002', 'quote': texts[1]}]}]}}]
        variants.validate_storyboard(state, unit)
        unit['shots'][1]['continuity']['changes'] = []
        with self.assertRaisesRegex(ValueError, '登记进入本场合法形象'):
            variants.validate_storyboard(state, unit)

    def test_switch_requires_exact_event_and_no_omission(self):
        state, unit = storyboard(self.data, self.paragraphs)
        unit['shots'][1]['continuity']['changes'][0]['evidence'][0]['quote'] = self.paragraphs[0]
        with self.assertRaisesRegex(ValueError, '完整已审动作证据'):
            variants.validate_storyboard(state, unit)
        state, unit = storyboard(self.data, self.paragraphs)
        unit['shots'] = unit['shots'][:2]
        with self.assertRaisesRegex(ValueError, '遗漏'):
            variants.validate_storyboard(state, unit)

    def test_transition_event_cannot_be_split_by_unit_boundary(self):
        state, unit = storyboard(self.data, self.paragraphs)
        unit['span'][1] = state['records'][1]['start'] + 1
        with self.assertRaisesRegex(ValueError, '边界不能拆开'):
            variants.validate_storyboard(state, unit)

    def test_unchanged_migration_retains_variant_id_and_changed_source_invalidates_review(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'scenes').mkdir()
            def save_source(paragraphs):
                (root / 'source.json').write_text(json.dumps({'paragraphs': [{'id': i, 'text': text}
                    for i, text in enumerate(paragraphs, 1)]}), encoding='utf-8')
            save_source(self.paragraphs)
            (root / 'old-source.json').write_bytes((root / 'source.json').read_bytes())
            (root / 'script.txt').write_text(self.script, encoding='utf-8')
            (root / 'scenes/scene_001_0001-0003.txt').write_text(self.script, encoding='utf-8')
            (root / 'ledger.json').write_text(json.dumps(self.data), encoding='utf-8')
            result, report = migration.migrate(root / 'ledger.json', root / 'old-source.json', root / 'script.txt', root, {})
            self.assertEqual([item['id'] for item in result['characters']], ['CHAR0001', 'CHAR0002'])
            self.assertEqual(result['characters'][1]['appearance_variant'], self.data['characters'][1]['appearance_variant'])
            self.assertEqual(result['scenes'][0]['character_appearances'], self.data['scenes'][0]['character_appearances'])
            self.assertFalse(report['pending_reviews'])
            revised = self.paragraphs[:]
            revised[1] = '阿明拿起猪猪头套。'
            save_source(revised)
            result, report = migration.migrate(root / 'ledger.json', root / 'old-source.json', root / 'script.txt', root,
                                               {'scenes': {'SC0001': [1]}})
            self.assertIn('CHAR0002', {item['id'] for item in result['characters']})
            self.assertTrue(any(item['kind'] == '佩戴形象事件证据待核' for item in report['pending_reviews']))
            self.assertTrue(any(item['kind'] == '佩戴形象审阅待更新' for item in report['pending_reviews']))


if __name__ == '__main__':
    unittest.main()
