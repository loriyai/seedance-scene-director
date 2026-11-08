"""Source names, timed outfit states, and actual derived-image contracts."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import fixture, design, ledger, save
from test_character_variants import variants
from test_scene_character_names import script_io, review


class AppearanceStatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        _, _, _, self.data, self.payload = fixture(self.work, with_prop=False)
        self.paragraphs = ['陈默穿灰衣站在门口。', '陈默换好白衬衫。', '陈默戴上腕表。',
                           '陈默摘下腕表。', '陈默换好黑外套。']
        self.data['source_digest'] = ledger.digest(self.paragraphs)
        self.data['character_appearance_policy'] = variants.APPEARANCE_POLICY
        self.data['characters'][0].update(name='陈默-1', source_entry='陈默-1',
                                          source_speaker='陈默', visual_facts=[])
        self.data['scenes'] = [self.data['scenes'][0]]
        self.data['scenes'][0]['source_range'] = [1, len(self.paragraphs)]
        self.data['characters'][0].pop('appearance', None)
        self.payload['designs'][0]['styling'] = '灰色衣服'
        self.track = {'scene_id': 'SC0001', 'base_asset_id': 'CHAR0001', 'initial_asset_id': 'CHAR0001',
                      'initial_evidence': {'source_paragraphs': [1], 'evidence': self.paragraphs[0], 'basis': '本场起点'},
                      'transitions': []}
        current = 'CHAR0001'
        for index, target in enumerate(['CHAR0002', 'CHAR0003', 'CHAR0002', 'CHAR0004'], 2):
            self.track['transitions'].append({'event_id': f'变化{index}', 'from_asset_id': current,
                'to_asset_id': target, 'source_paragraphs': [index], 'evidence': self.paragraphs[index-1],
                'basis': '该动作明确完成，随后保持新状态'})
            current = target
        self.snapshot = {'policy': variants.STATE_POLICY, 'reviewed': True,
            'source_digest': self.data['source_digest'], 'scene_appearances': [self.track], 'variants': [
            {'id': 'CHAR0002', 'base_asset_id': 'CHAR0001', 'reference_asset_id': 'CHAR0001',
             'name': '陈默-白衬衫', 'label': '白衬衫', 'kind': 'clothing', 'design_basis': '原文换衣完成'},
            {'id': 'CHAR0003', 'base_asset_id': 'CHAR0001', 'reference_asset_id': 'CHAR0002',
             'name': '陈默-白衬衫-戴腕表', 'label': '白衬衫-戴腕表', 'kind': 'accessory', 'design_basis': '原文戴上腕表'},
            {'id': 'CHAR0004', 'base_asset_id': 'CHAR0001', 'reference_asset_id': 'CHAR0001',
             'name': '陈默-黑外套', 'label': '黑外套', 'kind': 'clothing', 'design_basis': '原文第二次换衣完成'}]}

    def states(self):
        return variants.apply_variant_review(self.data, self.snapshot, self.paragraphs)

    def designs(self):
        result = copy.deepcopy(self.payload)
        for aid, clothes in [('CHAR0002', '白衬衫'), ('CHAR0004', '黑外套')]:
            result['designs'].append({'asset_id': aid, 'attribution': '制作设计',
                'reviewed_source_facts': True, 'basis': '原文明示换衣后的状态', 'styling': clothes,
                'image_reference': {'base_asset_id': 'CHAR0001', 'changes': ['服装替换为'+clothes],
                                    'preserve': ['同一人物的面部身份与身体比例']}})
        result['designs'].append({'asset_id': 'CHAR0003', 'attribution': '制作设计',
            'reviewed_source_facts': True, 'basis': '白衬衫造型上增加腕表',
            'covering': '左腕佩戴一只银色腕表，表带贴合腕部', 'visible_identity': '腕表不遮挡人物面部'})
        # Submit reverse dependency order: the compiler must resolve parents.
        result['designs'].reverse()
        return result

    def test_change_completion_chain_and_remove_reuses_previous_asset(self):
        data = self.states()
        self.assertEqual(data['scenes'][0]['character_ids'], ['CHAR0001'])
        self.assertEqual(variants.scene_character_ids(data['scenes'][0]),
                         ['CHAR0001', 'CHAR0002', 'CHAR0003', 'CHAR0004'])
        self.assertEqual(data['scenes'][0]['character_appearances'][0]['transitions'][2]['to_asset_id'], 'CHAR0002')
        bad = copy.deepcopy(self.snapshot)
        bad['scene_appearances'][0]['transitions'][2]['from_asset_id'] = 'CHAR0001'
        with self.assertRaisesRegex(ValueError, '当前同一角色'):
            variants.apply_variant_review(self.data, bad, self.paragraphs)

    def test_outfit_then_wristwear_compiles_real_reference_without_redescribing_face(self):
        data = self.states()
        result = design.apply_design(data, self.designs(), '韩式写实3D动漫')
        by_id = {item['id']: item for item in result['characters']}
        watch = by_id['CHAR0003']
        self.assertEqual(design.image_reference_asset_ids(watch), ['CHAR0002'])
        self.assertIn('@陈默-白衬衫 ', watch['image_prompt'])
        self.assertIn('左腕佩戴', watch['image_prompt'])
        self.assertNotIn(self.payload['identity_anchors'][0]['face'], watch['image_prompt'])
        self.assertNotIn('遮挡物始终', watch['image_prompt'])
        self.assertEqual(watch['visual_design']['styling'], '白衬衫')
        self.assertEqual(design.image_reference_asset_ids(by_id['CHAR0004']), ['CHAR0001'])
        design.validate_visual_design(result)

    def test_preview_and_group_compilers_match_full_state_design(self):
        import design_preview
        import design_workspace
        data, payload = self.states(), self.designs()
        ids = [record['asset_id'] for record in payload['designs']]
        result = design.apply_design(data, payload, '写实')
        expected = {item['id']: item['image_prompt'] for category in design.ASSET_CATEGORIES
                    for item in result[category]}
        report = design_preview.preview_design(data, payload, '写实', ids)
        self.assertEqual({row['asset_id']: row['image_prompt'] for row in report['previews']}, expected)
        assignments = {item['id']: payload['identity_anchors'][0]['id'] for item in data['characters']}
        records = {row['asset_id']: {key: copy.deepcopy(value) for key, value in row.items()
                                   if key != 'asset_id'} for row in payload['designs']}
        _, previews = design_workspace._compile(data, records, design.identity_map(payload),
            assignments, design.DESIGN_VERSION, '写实')
        self.assertEqual({row['asset_id']: row['image_prompt'] for row in previews}, expected)

    def test_reference_cannot_change_identity_or_cycle(self):
        for parent in ('CHAR0003', 'CHAR9999'):
            bad = copy.deepcopy(self.snapshot)
            bad['variants'][0]['reference_asset_id'] = parent
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                variants.apply_variant_review(self.data, bad, self.paragraphs)

    def test_long_lived_scar_uses_core_continuity_not_two_scene_quota(self):
        self.paragraphs = ['陈默站在门口。', '陈默右颊留下长条伤痕，随后一路追逐到门口。']
        self.data['source_digest'] = ledger.digest(self.paragraphs)
        self.data['scenes'][0]['source_range'] = [1, 2]
        self.snapshot['source_digest'] = self.data['source_digest']
        state = self.snapshot['variants'][0]
        state.update(name='陈默-右颊伤痕', label='右颊伤痕', kind='persistent_state',
                     state_review={'selection_basis': 'core_continuity',
                        'consistency_reason': '追逐的多个镜头需保持右颊伤痕位置和长度',
                        'story_role': '受伤后的持续可见状态',
                        'visible_uses': [{'scene_id': 'SC0001', 'event_id': '受伤后追逐',
                                         'source_paragraphs': [2], 'evidence': self.paragraphs[1]}]})
        self.snapshot['variants'] = [state]
        self.track['initial_evidence']['evidence'] = self.paragraphs[0]
        self.track['transitions'] = [dict(self.track['transitions'][0], evidence=self.paragraphs[1])]
        self.states()
        state['state_review']['selection_basis'] = 'special_reusable'
        with self.assertRaisesRegex(ValueError, '使用证据不足'):
            self.states()

    def test_full_parenthesized_role_name_is_not_an_emotion_cue(self):
        original = self.work / 'original.txt'
        save(original, '人物：阿明-1（白衬衫）；\n阿明：“走吧。”')
        project = self.work / 'project'
        with contextlib.redirect_stdout(io.StringIO()):
            script_io.prepare(original, project)
        draft = project/'scenes/scene_001_0001-0002.txt'
        save(draft, '场景1-日-内-房间\n人物：阿明-1（白衬衫）；\n阿明-1（白衬衫）：走吧。')
        report = review.make_report(project, {})
        self.assertEqual(report['status'], 'passed', report)
        parts = review.draft_parts(draft)
        self.assertEqual(parts[-1][0]['speaker'], '阿明-1（白衬衫）')
        self.assertEqual(parts[-1][0]['cue'], '')
        import director
        records, _ = director.build_records(draft.read_text(encoding='utf-8'),
                                            {'scenes': [{'id': 'SC0001', 'number': 1}]})
        self.assertEqual(records[0]['speaker'], '阿明-1（白衬衫）')
        self.assertEqual(records[0]['voice'], '说')

    def test_numbered_description_and_uncertain_scenes_are_separate_assets(self):
        declarations = ['阿明-1', '阿明-1（红衣散发）', '阿明-场景3未明确', '阿明-场景4未明确']
        source = self.work/'numbered.txt'
        save(source, '\n'.join('人物：'+name+'；\n阿明走进房间。' for name in declarations))
        project = self.work/'numbered'
        with contextlib.redirect_stdout(io.StringIO()):
            script_io.prepare(source, project)
        all_text = []
        for n, name in enumerate(declarations, 1):
            body = f'场景{n}-日-内-房间\n人物：{name}；\n△ {name}走进房间。\n'
            save(project/f'scenes/scene_{n:03d}_{2*n-1:04d}-{2*n:04d}.txt', body)
            all_text.append(body)
        save(project/'script.txt', '\n'.join(all_text))
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.initialize(project/'script.txt', project, project/'ledger.json')
        data = json.loads((project/'ledger.json').read_text(encoding='utf-8'))
        self.assertEqual([item['name'] for item in data['characters']], declarations)
        self.assertEqual(set(variants.appearance_roots(data).values()), {'CHAR0001'})

    def test_first_numbered_appearance_is_root_and_age_retains_identity(self):
        data = copy.deepcopy(self.data)
        second = copy.deepcopy(data['characters'][0])
        second.update(id='CHAR0002', name='陈默-中年', source_entry='陈默-中年')
        data['characters'].append(second)
        scene = copy.deepcopy(data['scenes'][0])
        scene.update(id='SC0002', number=2, character_ids=['CHAR0002'])
        data['scenes'].append(scene)
        payload = copy.deepcopy(self.payload)
        age = copy.deepcopy(payload['designs'][0])
        age.update(asset_id='CHAR0002', change_kind='age', appearance_overrides={'age':'中年'},
                   variant_differences=['年龄进入中年，面部辨识特征不变'],
                   image_reference={'base_asset_id':'CHAR0001','changes':['同一人物进入中年'],
                                    'preserve':['面部辨识特征和身体比例']})
        payload['designs'].append(age)
        result = design.apply_design(data, payload, '写实')
        self.assertEqual(design.image_reference_asset_ids(result['characters'][1]), ['CHAR0001'])
        bad = copy.deepcopy(payload)
        bad['designs'][0]['image_reference'] = {'base_asset_id':'CHAR0002','changes':['换装'], 'preserve':['同一人']}
        bad['designs'][-1].pop('image_reference')
        with self.assertRaises(ValueError):
            design.apply_design(data, bad, '写实')

    def test_explicit_other_body_is_a_new_root(self):
        data = copy.deepcopy(self.data)
        data['characters'][0]['appearance'] = {'identity':'陈默', 'incarnation':'第一身体'}
        other = copy.deepcopy(data['characters'][0])
        other.update(id='CHAR0002', name='陈默-新身体')
        other['appearance']['incarnation'] = '第二身体'
        data['characters'].append(other)
        self.assertEqual(variants.appearance_roots(data), {'CHAR0001':'CHAR0001', 'CHAR0002':'CHAR0002'})


if __name__ == '__main__':
    unittest.main()
