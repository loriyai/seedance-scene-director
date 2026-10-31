"""Regression tests for the unified storyboard aspect-ratio registry."""
import copy
import unittest

import ratio_profiles


class RatioProfileTests(unittest.TestCase):
    def test_ratio_mapping_is_stable_and_distinct(self):
        landscape = ratio_profiles.profile_for_ratio('16:9')
        portrait = ratio_profiles.profile_for_ratio('9:16')
        self.assertEqual(landscape['profile_id'], 'landscape-v1')
        self.assertEqual(portrait['profile_id'], 'portrait-v1')
        self.assertEqual(landscape['orientation'], 'landscape')
        self.assertEqual(portrait['orientation'], 'portrait')
        self.assertNotEqual(landscape['profile_digest'], portrait['profile_digest'])

    def test_profile_snapshot_is_defensive_and_unit_freezes_it(self):
        profile = ratio_profiles.profile_for_ratio('9:16')
        profile['profile_id'] = 'changed'
        self.assertEqual(ratio_profiles.profile_id_for_ratio('9:16'), 'portrait-v1')
        unit = {'id': 'u01'}
        ratio_profiles.apply_to_unit(unit, '9:16')
        ratio_profiles.require_unit_profile(unit)
        self.assertEqual(unit['ratio_profile'], 'portrait-v1')
        self.assertEqual(unit['ratio_profile_version'], 1)

    def test_mismatch_is_rejected_but_historical_unit_remains_valid(self):
        unit = {'video_ratio': '9:16', 'ratio_profile': 'landscape-v1',
                'ratio_profile_version': 1,
                'ratio_profile_digest': ratio_profiles.profile_for_ratio('16:9')['profile_digest']}
        with self.assertRaisesRegex(ValueError, '不一致'):
            ratio_profiles.profile_for_unit(unit)
        historical = {'video_ratio': '16:9'}
        self.assertEqual(ratio_profiles.profile_for_unit(historical)['profile_id'], 'landscape-v1')

    def test_profile_summary_does_not_expose_mutable_registry(self):
        summary = ratio_profiles.profile_summary('16:9')
        clone = copy.deepcopy(summary)
        clone['rules_anchor'] = 'other'
        self.assertNotEqual(clone['rules_anchor'], ratio_profiles.profile_summary('16:9')['rules_anchor'])


if __name__ == '__main__':
    unittest.main()
