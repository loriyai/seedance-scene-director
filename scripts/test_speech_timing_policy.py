"""Speech estimates share one policy without becoming rendering instructions."""
import copy
from decimal import Decimal
import unittest

import dialogue_layout as dl
import director
import speech_timing_audit as audit
import storyboard_preflight as preflight
import authoring_gates
import speech_timing_policy as timing_policy


class SpeechTimingPolicyChecks(unittest.TestCase):
    def unit(self, speed='常速', units=30, net=5.5, pause=.2):
        text = '甲' * units + '。'
        end = round(net + pause, 3)
        return {'audio': [{'record': 'L1', 'text': text, 'start': 0, 'end': end,
                           'units': units, 'pause': pause, 'speed': speed}],
                'shots': [{'dialogue_parts': [{'record': 'L1', 'text': text,
                                               'start': 0, 'end': end, 'pause': pause}]}]}

    def test_all_consumers_share_the_new_ranges(self):
        self.assertIs(dl.SPEEDS, timing_policy.SPEEDS)
        self.assertIs(dl.PAUSE_RANGES, timing_policy.PAUSE_RANGES)
        self.assertIs(dl.pause_seconds, timing_policy.pause_seconds)
        self.assertEqual(dl.SPEEDS, {'常速': (5.2, 5.8), '快节奏': (5.8, 6.4),
                                    '情绪慢速': (4.0, 4.8)})
        for module in (director, audit, preflight):
            self.assertIs(module.SPEEDS, dl.SPEEDS)
        self.assertEqual(dl.PAUSE_RANGES, {'comma': (.1, .2), 'sentence': (.1, .25)})
        self.assertEqual(timing_policy.DEFAULT_RATE, 5.5)
        self.assertEqual(timing_policy.CAPACITY_RATE, 6.4)
        self.assertAlmostEqual(dl.estimate('甲。', 55), 10.175)

    def test_decimal_capacity_floor_preserves_fractional_rate_exactly(self):
        self.assertEqual(timing_policy.capacity_seconds(32), Decimal('5'))
        self.assertEqual(timing_policy.capacity_seconds(16, .1), Decimal('2.6'))

    def test_overlapping_rate_is_valid_for_both_selected_bands(self):
        for speed in ('常速', '快节奏'):
            unit = self.unit(speed=speed, units=29, net=5)
            before = copy.deepcopy(unit)
            self.assertEqual(audit.audit(unit), [])
            self.assertEqual(unit, before)
        warnings = audit.audit(self.unit(speed='情绪慢速', units=29, net=5))
        self.assertEqual(warnings[0]['kind'], '片段估计净语速超档')
        self.assertEqual(warnings[0]['rate'], 5.8)

    def test_explicit_semantic_pause_replaces_punctuation_estimate_once(self):
        unit = self.unit(pause=1.7)
        self.assertEqual(audit.audit(unit), [])
        unit['audio'][0]['end'] = unit['shots'][0]['dialogue_parts'][0]['end'] = 6.7
        warnings = audit.audit(unit)
        self.assertEqual(warnings[0]['pause'], 1.7)
        self.assertEqual(warnings[0]['min_seconds'], round(30 / 5.8 + 1.7, 3))
        self.assertEqual(warnings[0]['rate'], 6.0)

    def test_mixed_punctuation_is_one_pause_and_special_marks_stay_distinct(self):
        self.assertEqual(dl.pause_seconds('甲，、乙？！'), .325)
        self.assertEqual(dl.pause_seconds('甲？！……'), .175)
        self.assertEqual(dl.pause_seconds('甲……'), dl.SEMANTIC_PAUSE_ESTIMATE)
        self.assertEqual(dl.pause_seconds('甲；乙：'), 2 * dl.SEMANTIC_PAUSE_ESTIMATE)
        self.assertEqual(dl.pause_seconds('甲。乙。'), .35)

    def test_complete_reading_tokens_own_internal_punctuation_for_every_consumer(self):
        text = "1,000，3.14；don't：R&D。"
        reference = timing_policy.punctuation_reference(text)
        self.assertEqual([row['text'] for row in reference['clusters']], ['，', '；', '：', '。'])
        self.assertEqual([text[row['start']:row['end']] for row in reference['clusters']],
                         ['，', '；', '：', '。'])
        self.assertEqual(dl.pause_seconds('1,000'), 0)
        self.assertEqual(dl.pause_seconds(text), .845)
        self.assertFalse(reference['blocking'])
        self.assertTrue(reference['clusters'][1]['requires_context'])

    def test_reference_clusters_do_not_duplicate_at_fragment_boundaries(self):
        fragments = ['甲？', '！……乙，', '、丙。']
        self.assertEqual(timing_policy.punctuation_weights(fragments), [.175, .15, .175])
        reference = timing_policy.punctuation_reference(''.join(fragments))
        self.assertEqual(len(reference['clusters']), 3)
        self.assertAlmostEqual(sum(timing_policy.punctuation_weights(fragments)),
                               reference['reference_seconds'])
        self.assertTrue(reference['clusters'][0]['requires_context'])

    def test_gap_thresholds_use_boundary_kind_without_reclassifying_ellipsis(self):
        for ending, gap, expect_warning in (('，', .21, True), ('。', .26, True),
                                             ('，', .2, False), ('。', .25, False),
                                             ('……', .6, False)):
            with self.subTest(ending=ending, gap=gap):
                unit = self.unit(units=10, net=2, pause=gap)
                event = unit['audio'][0]
                event['text'] = '甲' * 5 + ending + '乙' * 5 + '。'
                parts = [{'record': 'L1', 'text': '甲' * 5 + ending, 'start': 0, 'end': 1, 'pause': 0},
                         {'record': 'L1', 'text': '乙' * 5 + '。', 'start': 1 + gap,
                          'end': 2 + gap, 'pause': 0}]
                unit['shots'][0]['dialogue_parts'] = parts
                warnings = audit.audit(unit)
                self.assertEqual(any(w['kind'] == '分句间停顿偏长需复核' for w in warnings), expect_warning)
                self.assertFalse(any(w['kind'] == '片段停顿与整句总停顿不一致' for w in warnings))

    def test_render_policy_changes_the_audio_review_binding_without_rewriting_legacy(self):
        unit = self.unit()
        before = copy.deepcopy(unit)
        legacy_payload = authoring_gates.audio_payload({}, unit)
        self.assertNotIn('speech_render_policy', legacy_payload)
        unit['speech_render_policy'] = dl.SPEECH_RENDER_POLICY
        current_payload = authoring_gates.audio_payload({}, unit)
        self.assertNotEqual(authoring_gates.digest(legacy_payload), authoring_gates.digest(current_payload))
        self.assertEqual({k: v for k, v in current_payload.items() if k != 'speech_render_policy'}, legacy_payload)
        self.assertEqual(authoring_gates.audio_payload({}, before), legacy_payload)

    def test_assembly_versions_a_new_draft_before_render_and_does_not_mutate_source(self):
        import storyboard_assembly
        from test_audit_director import AuditDirectorChecks
        fixture = AuditDirectorChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        unit = fixture.unit([0, 9])
        before = copy.deepcopy(unit)
        self.assertNotIn('speech_render_policy', unit)
        assembled = storyboard_assembly.assemble_unit(unit)
        self.assertEqual(assembled['speech_render_policy'], dl.SPEECH_RENDER_POLICY)
        self.assertEqual(unit, before)
        self.assertEqual(storyboard_assembly.assemble_unit(assembled), assembled)


if __name__ == '__main__':
    unittest.main()
