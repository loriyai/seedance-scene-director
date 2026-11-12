"""Speech estimates share one policy without becoming rendering instructions."""
import copy
import unittest

import dialogue_layout as dl
import director
import speech_timing_audit as audit
import storyboard_preflight as preflight
import authoring_gates


class SpeechTimingPolicyChecks(unittest.TestCase):
    def unit(self, speed='常速', units=30, net=6.0, pause=.2):
        text = '甲' * units + '。'
        end = round(net + pause, 3)
        return {'audio': [{'record': 'L1', 'text': text, 'start': 0, 'end': end,
                           'units': units, 'pause': pause, 'speed': speed}],
                'shots': [{'dialogue_parts': [{'record': 'L1', 'text': text,
                                               'start': 0, 'end': end, 'pause': pause}]}]}

    def test_all_consumers_share_the_new_ranges(self):
        self.assertEqual(dl.SPEEDS, {'常速': (4.8, 5.4), '快节奏': (4.8, 6.0),
                                    '情绪慢速': (4.0, 4.8)})
        for module in (director, audit, preflight):
            self.assertIs(module.SPEEDS, dl.SPEEDS)
        self.assertEqual(dl.PAUSE_RANGES, {'comma': (.1, .2), 'sentence': (.1, .25)})
        self.assertAlmostEqual(dl.estimate('甲。', 51), 10.175)

    def test_overlapping_rate_is_valid_for_both_selected_bands(self):
        for speed in ('常速', '快节奏'):
            unit = self.unit(speed=speed)
            before = copy.deepcopy(unit)
            self.assertEqual(audit.audit(unit), [])
            self.assertEqual(unit, before)
        warnings = audit.audit(self.unit(speed='情绪慢速'))
        self.assertEqual(warnings[0]['kind'], '片段估计净语速超档')
        self.assertEqual(warnings[0]['rate'], 5.0)

    def test_explicit_semantic_pause_replaces_punctuation_estimate_once(self):
        unit = self.unit(pause=1.7)
        self.assertEqual(audit.audit(unit), [])
        unit['audio'][0]['end'] = unit['shots'][0]['dialogue_parts'][0]['end'] = 6.7
        warnings = audit.audit(unit)
        self.assertEqual(warnings[0]['pause'], 1.7)
        self.assertEqual(warnings[0]['min_seconds'], round(30 / 5.4 + 1.7, 3))
        self.assertEqual(warnings[0]['rate'], 6.0)

    def test_mixed_punctuation_is_one_pause_and_special_marks_stay_distinct(self):
        self.assertEqual(dl.pause_seconds('甲，、乙？！'), .325)
        self.assertEqual(dl.pause_seconds('甲？！……'), .175)
        self.assertEqual(dl.pause_seconds('甲……'), dl.SEMANTIC_PAUSE_ESTIMATE)
        self.assertEqual(dl.pause_seconds('甲；乙：'), 2 * dl.SEMANTIC_PAUSE_ESTIMATE)
        self.assertEqual(dl.pause_seconds('甲。乙。'), .35)

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
