import unittest

import prop_reference_review as review


class PropReferenceChecks(unittest.TestCase):
    def setUp(self):
        self.state = {'ledger': {'asset_policy': 'visual-continuity-v2',
            'props': [{'id': 'PROP1', 'name': '小车', 'source_name': '小车', 'aliases': []}],
            'prop_coverage_review': {'candidates': [{'candidate_id': 'PC1', 'name': '小车',
                'scene_ids': ['SC1'], 'requires_reference': True, 'disposition': 'asset',
                'target_id': 'PROP1', 'source_evidence': [{'scene_id': 'SC1', 'evidence': '小车向前驶去'}]}]}},
            'records': [{'id': 'L1', 'scene': 'SC1', 'text': '小车向前驶去', 'start': 0, 'end': 6}]}
        self.unit = {'scene_ids': ['SC1'], 'span': [0, 6], 'prop_ids': [],
                     'continuity': {'entities': {}}}

    def test_core_object_cannot_be_downgraded_to_temporary_entity(self):
        self.unit['continuity']['entities']['car'] = {'asset_ids': [], 'label': '小车', 'evidence': []}
        with self.assertRaisesRegex(ValueError, 'PROP1'):
            review.check(self.state, self.unit)

    def test_explicit_alias_catches_a_differently_labelled_temporary_object(self):
        self.state['ledger']['prop_coverage_review']['candidates'][0]['aliases'] = ['粉色车辆']
        self.unit['continuity']['entities']['car'] = {'asset_ids': [], 'label': '粉色车辆',
            'evidence': [{'record': 'L1', 'quote': '小车向前驶去'}]}
        with self.assertRaises(ValueError):
            review.check(self.state, self.unit)

    def test_production_name_also_preserves_asset_identity(self):
        self.state['ledger']['props'][0]['name'] = '小车-粉色基础形态'
        self.unit['continuity']['entities']['car'] = {
            'asset_ids': [], 'label': '小车-粉色基础形态', 'evidence': []}
        with self.assertRaisesRegex(ValueError, 'PROP1'):
            review.check(self.state, self.unit)

    def test_shared_action_quote_does_not_make_another_object_the_prop(self):
        self.unit['prop_ids'] = ['PROP1']
        self.unit['continuity']['entities']['road'] = {'asset_ids': [], 'label': '道路',
            'evidence': [{'record': 'L1', 'quote': '小车向前驶去'}]}
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_visible_action_without_reference_requires_semantic_review(self):
        self.assertEqual(review.check(self.state, self.unit)[0]['target_id'], 'PROP1')

    def test_referring_to_reviewed_asset_passes(self):
        self.unit['prop_ids'] = ['PROP1']
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_owner_name_and_partial_source_do_not_assert_visible_car(self):
        self.state['records'][0]['text'] = '小车车主说话'
        self.assertEqual(review.check(self.state, self.unit), [])
        self.state['records'][0]['text'] = '小车向前驶去'
        self.unit['span'] = [0, 2]
        self.assertEqual(review.check(self.state, self.unit), [])

    def test_other_scenes_and_legacy_receipts_are_unchanged(self):
        self.unit['scene_ids'] = ['SC2']
        self.assertEqual(review.check(self.state, self.unit), [])
        self.unit['scene_ids'] = ['SC1']
        self.state['ledger']['asset_policy'] = 'visible-reuse-v1'
        self.assertEqual(review.check(self.state, self.unit), [])


if __name__ == '__main__':
    unittest.main()
