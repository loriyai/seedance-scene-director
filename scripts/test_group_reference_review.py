import copy
import unittest
from types import SimpleNamespace

import director as d
from core_test_support import setup_skill_fixture

import group_reference_review as review


class GroupReferenceTests(unittest.TestCase):
    def setUp(self):
        self.group = {'id': 'CHAR2', 'name': '迎宾学生-群像-学士服-礼堂',
                      'source_entry': '迎宾学生-群像-学士服', 'aliases': [],
                      'visual_mode': 'prompt_only',
                      'visible_uses': [{'scene_id': 'SC1', 'evidence': '学生捧花围成半圆'}]}
        self.state = {'ledger': {'group_policy': review.POLICY, 'characters': [self.group],
                                'scenes': [{'id': 'SC1', 'character_ids': ['CHAR2']}]},
                      'records': [{'scene': 'SC1', 'kind': 'action', 'text': '学生捧花围成半圆', 'start': 0, 'end': 8}]}
        self.unit = {'scene_ids': ['SC1'], 'span': [0, 8], 'character_ids': ['CHAR2'],
                     'shots': [{'subjects': ['CHAR2'], 'focus': []}],
                     'continuity': {'entities': {}}}

    def test_single_background_appearance_needs_no_automatic_image_warning(self):
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_multiple_shot_participation_warns_without_promoting_asset(self):
        self.unit['shots'].append({'subjects': ['CHAR2'], 'focus': []})
        before = copy.deepcopy((self.state, self.unit))
        result = review.check(self.state, self.unit)
        self.assertEqual(result[0]['kind'], '群像图片需求需复核')
        self.assertEqual(result[0]['shots'], [1, 2])
        self.assertEqual((self.state, self.unit), before)

    def test_single_focal_group_also_receives_semantic_review(self):
        self.unit['shots'][0]['focus'] = ['CHAR2']
        self.assertEqual(review.check(self.state, self.unit)[0]['focus_shots'], [1])

    def test_image_asset_group_can_participate_without_prompt_only_warning(self):
        self.group['visual_mode'] = 'image_asset'
        self.unit['shots'] *= 2
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_known_image_group_cannot_be_redeclared_as_temporary(self):
        self.group['visual_mode'] = 'image_asset'
        self.unit['continuity']['entities']['students'] = {
            'label': self.group['name'], 'asset_ids': []}
        with self.assertRaisesRegex(ValueError, 'CHAR2'):
            review.check(self.state, self.unit)
        self.unit['continuity']['entities']['students']['asset_ids'] = ['CHAR2']
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_shared_action_quote_does_not_turn_flowers_into_the_group(self):
        self.group['visual_mode'] = 'image_asset'
        self.unit['continuity']['entities']['flowers'] = {
            'label': '花束', 'asset_ids': [], 'evidence': [{'quote': '学生捧花围成半圆'}]}
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_ambiguous_alias_is_not_enough_to_force_group_identity(self):
        self.group['visual_mode'] = 'image_asset'
        self.group['aliases'] = ['迎宾队']
        other = copy.deepcopy(self.group)
        other.update(id='CHAR3', name='迎宾教师-群像-正装-礼堂')
        self.state['ledger']['characters'].append(other)
        self.state['ledger']['scenes'][0]['character_ids'].append('CHAR3')
        self.unit['continuity']['entities']['people'] = {'label': '迎宾队', 'asset_ids': []}
        result = review.check(self.state, self.unit)
        self.assertEqual([warning['kind'] for warning in result], ['群像画面覆盖需复核'])

    def test_missing_declared_group_with_complete_source_quote_warns(self):
        self.unit['character_ids'] = []
        self.unit['shots'][0]['subjects'] = []
        result = review.check(self.state, self.unit)
        self.assertEqual(result[0]['kind'], '群像画面覆盖需复核')
        self.unit['span'] = [0, 3]
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_unrelated_scene_and_legacy_policy_are_unchanged(self):
        self.unit['scene_ids'] = ['SC2']
        self.assertEqual(review.check(self.state, self.unit), [])
        self.unit['scene_ids'] = ['SC1']
        self.unit['shots'][0]['focus'] = ['CHAR2']
        for policy in (None, 'visible-reuse-v1'):
            self.state['ledger']['group_policy'] = policy
            self.assertEqual(review.check(self.state, self.unit), [])

    def test_same_words_in_another_scene_do_not_establish_group_coverage(self):
        self.unit.update(scene_ids=['SC1', 'SC2'], span=[8, 17], character_ids=[], shots=[])
        self.state['ledger']['scenes'].append({'id': 'SC2', 'character_ids': []})
        self.state['records'] = [
            {'scene': 'SC1', 'kind': 'action', 'text': '学生捧花围成半圆后', 'start': 0, 'end': 9},
            {'scene': 'SC2', 'kind': 'action', 'text': '学生捧花围成半圆', 'start': 9, 'end': 17}]
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_action_fragments_across_scenes_cannot_fabricate_coverage(self):
        self.unit.update(scene_ids=['SC1', 'SC2'], span=[8, 16], character_ids=[], shots=[])
        self.state['ledger']['scenes'].append({'id': 'SC2', 'character_ids': []})
        self.state['records'].extend([
            {'scene': 'SC1', 'kind': 'action', 'text': '学生捧花', 'start': 8, 'end': 12},
            {'scene': 'SC2', 'kind': 'action', 'text': '围成半圆', 'start': 12, 'end': 16}])
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_dialogue_repeating_an_earlier_action_is_not_visible_coverage(self):
        self.unit.update(span=[8, 16], character_ids=[], shots=[])
        self.state['records'].append(
            {'scene': 'SC1', 'kind': 'dialogue', 'text': '学生捧花围成半圆', 'start': 8, 'end': 16})
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_director_surfaces_review_and_keeps_prompt_only_rendering(self):
        case = SimpleNamespace(addCleanup=self.addCleanup)
        setup_skill_fixture(case)
        group = copy.deepcopy(self.group)
        group['id'] = 'CHAR0100'
        case.state['ledger']['group_policy'] = review.POLICY
        case.state['ledger']['characters'].append(group)
        case.state['ledger']['scenes'][0]['character_ids'].append(group['id'])
        case.unit['character_ids'].append(group['id'])
        case.unit['shots'][0]['subjects'].append(group['id'])
        case.unit['shots'][0]['focus'].append(group['id'])
        result = d.validate_unit(case.state, case.unit, case.unit['span'])
        self.assertTrue(any(warning['kind'] == '群像图片需求需复核' for warning in result))
        rendered, _ = d.asset_text(case.state, case.unit, '@{CHAR0100} 站在门旁。')
        self.assertEqual(rendered, group['name'] + ' 站在门旁。')


if __name__ == '__main__':
    unittest.main()
