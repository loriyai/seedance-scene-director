"""Window capacity rejects impossible timings without approving performance."""
import copy
from decimal import Decimal
import json
import unittest

from speech_timing_audit import text_dialogue_capacity


def unit(start=0, end=2, parts=None):
    return {'draft_key': 'SC0001/u01', 'shots': [
        {'start': start, 'end': end, 'dialogue_parts': parts or []}]}


def fragment(text='甲乙丙丁戊己', start=.2, end=1.2, record='L000001'):
    return {'text': text, 'start': start, 'end': end, 'record': record}


class TextSpeechWindowTests(unittest.TestCase):
    def facts(self, data):
        return text_dialogue_capacity([data])['speech_window_diagnostics']

    def test_silent_shots_add_no_inferred_speech_or_pacing(self):
        result = text_dialogue_capacity([unit(end=6)])
        self.assertEqual(result['skipped_no_dialogue_shots'], 1)
        self.assertEqual(result['speech_window_diagnostics']['shots'], [])
        self.assertEqual(result['status'], 'checked')

    def test_tight_fragment_is_blocked_even_when_whole_shot_has_capacity(self):
        data = unit(parts=[fragment(start=.2, end=.3)])
        result = text_dialogue_capacity([data])
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('SPEECH_WINDOW_CAPACITY', {e['code'] for e in result['warnings']})
        self.assertIn('SPEECH_READING_CAPACITY', {e['code'] for e in result['errors']})
        self.assertNotIn('DIALOGUE_CAPACITY', {e['code'] for e in result['errors']})
        diagnostics = result['speech_window_diagnostics']
        self.assertIs(diagnostics['blocking'], True)
        row = diagnostics['shots'][0]
        self.assertAlmostEqual(row['before_first_speech'], .2)
        self.assertAlmostEqual(row['after_last_speech'], 1.7)
        part = row['fragments'][0]
        self.assertAlmostEqual(part['duration'], .1)
        self.assertEqual(part['dialogue_characters'], 6)
        self.assertEqual(part['mechanical_minimum'], .9375)
        self.assertIs(part['below_mechanical_minimum'], True)
        self.assertAlmostEqual(part['deficit'], .8375)

    def test_unit_relative_times_and_multiple_fragments_preserve_gaps(self):
        data = unit(start=10, end=16, parts=[
            fragment(start=10.5, end=11.5),
            fragment('庚辛壬癸子丑', start=13, end=14, record='L000002')])
        facts = self.facts(data)
        self.assertEqual(facts['time_basis'], 'authored_unit_relative_seconds')
        row = facts['shots'][0]
        self.assertEqual(row['before_first_speech'], .5)
        self.assertEqual(row['after_last_speech'], 2)
        self.assertEqual(row['between_fragments'], [1.5])
        self.assertEqual([p['start'] for p in row['fragments']], [10.5, 13])
        self.assertEqual([p['record'] for p in row['fragments']],
                         ['L000001', 'L000002'])
        self.assertFalse(any(p['below_mechanical_minimum'] for p in row['fragments']))

    def test_wide_window_in_legal_long_shot_has_no_slow_verdict(self):
        result = text_dialogue_capacity([unit(end=6, parts=[fragment(start=0, end=6)])])
        self.assertEqual(result['status'], 'checked')
        row = result['speech_window_diagnostics']['shots'][0]
        self.assertEqual(row['fragments'][0]['duration'], 6)
        self.assertFalse(row['fragments'][0]['below_mechanical_minimum'])
        self.assertEqual(row['fragments'][0]['deficit'], 0)
        self.assertNotIn('warnings', row)
        self.assertNotIn('rate', row['fragments'][0])
        self.assertEqual(result['semantic_review'], 'not_performed')

    def test_original_whole_shot_screening_is_preserved(self):
        result = text_dialogue_capacity([unit(end=1, parts=[
            fragment('甲乙丙丁戊己庚', start=0, end=1)])])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['warnings'][0]['code'], 'DIALOGUE_CAPACITY')
        self.assertAlmostEqual(result['warnings'][0]['minimum'], 7 / 6.4)
        self.assertEqual(result['errors'][0]['code'], 'SPEECH_READING_CAPACITY')

    def test_fragment_floor_is_exact_and_does_not_infer_punctuation_silence(self):
        row = self.facts(unit(end=2, parts=[fragment(
            '甲乙丙丁戊己，。', start=0,
            end=Decimal('0.937499999999999999999'))]))['shots'][0]
        part = row['fragments'][0]
        self.assertEqual(part['comma_or_pause_count'], 1)
        self.assertEqual(part['sentence_end_count'], 1)
        self.assertEqual(part['mechanical_minimum'], .9375)
        self.assertTrue(part['below_mechanical_minimum'])
        self.assertGreater(part['deficit'], 0)

    def test_explicit_internal_pause_changes_only_known_required_load(self):
        part = fragment('甲' * 16 + '。', start=0, end=2.6)
        part['speech_basis'] = {'pause_seconds': .1}
        data = unit(end=3, parts=[part])
        report = text_dialogue_capacity([data])
        self.assertEqual(report['status'], 'checked')
        fact = report['speech_window_diagnostics']['shots'][0]['fragments'][0]
        self.assertEqual(fact['phonetic_minimum'], 2.5)
        self.assertEqual(fact['authored_pause_seconds'], .1)
        self.assertEqual(fact['known_required_seconds'], 2.6)
        part['speech_basis']['pause_seconds'] = .11
        report = text_dialogue_capacity([data])
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['errors'][0]['code'], 'SPEECH_READING_CAPACITY')
        self.assertEqual(report['errors'][0]['minimum'], 2.61)

    def test_zero_pause_is_distinct_from_missing_pause_and_cannot_waive_reading(self):
        part = fragment('API，甲', start=0, end=2)
        part['speech_basis'] = {'pause_seconds': 0}
        report = text_dialogue_capacity([unit(parts=[part])])
        self.assertIn('SPEECH_READING_REQUIRED', {error['code'] for error in report['errors']})
        fact = report['speech_window_diagnostics']['shots'][0]['fragments'][0]
        self.assertEqual(fact['pause_status'], 'authored')
        self.assertEqual(fact['authored_pause_seconds'], 0)
        self.assertIsNone(fact['known_required_seconds'])

    def test_pause_values_must_be_finite_nonnegative_numbers_inside_window(self):
        for value in (None, True, False, '0.1', -.1, float('nan'), float('inf'), 1.01):
            with self.subTest(value=value):
                part = fragment(start=0, end=1)
                part['speech_basis'] = {'pause_seconds': value}
                report = text_dialogue_capacity([unit(parts=[part])])
                self.assertEqual(report['status'], 'blocked')
                self.assertIn('SPEECH_BASIS_INVALID', {error['code'] for error in report['errors']})

    def test_same_utterance_cluster_is_scanned_once_across_shots_and_units(self):
        first = fragment('甲？', start=0, end=.5)
        second = fragment('！乙', start=0, end=.5)
        first['speech_basis'] = {'pause_seconds': .1}
        second['speech_basis'] = {'pause_seconds': .1}
        data = [dict(unit(end=1, parts=[first]), draft_key='u1', duration=1),
                dict(unit(end=1, parts=[second]), draft_key='u2', duration=1)]
        report = text_dialogue_capacity(data)
        self.assertEqual(report['status'], 'checked')
        utterance = report['utterance_diagnostics']['records'][0]
        self.assertEqual(utterance['text'], '甲？！乙')
        self.assertEqual(utterance['speech_seconds'], 1)
        self.assertEqual(utterance['between_parts'], [.5])
        self.assertEqual(utterance['authored_pause_seconds'], .2)
        self.assertAlmostEqual(utterance['known_required_seconds'], 2 / 6.4 + .2)
        reference = utterance['punctuation_reference']
        self.assertEqual(reference['reference_seconds'], .175)
        self.assertEqual(len(reference['clusters']), 1)
        self.assertEqual(reference['clusters'][0]['text'], '？！')
        self.assertEqual([p['unit'] for p in reference['clusters'][0]['parts']], ['u1', 'u2'])
        second['record'] = 'L000002'
        separate = text_dialogue_capacity(data)['utterance_diagnostics']['records']
        self.assertEqual(len(separate), 2)
        self.assertEqual(sum(len(row['punctuation_reference']['clusters']) for row in separate), 2)

    def test_partial_pause_declarations_do_not_assume_other_fragments_are_silent(self):
        first = fragment('甲', start=0, end=.5)
        second = fragment('乙', start=1, end=1.5)
        first['speech_basis'] = {'pause_seconds': .1}
        report = text_dialogue_capacity([unit(parts=[first, second])])
        utterance = report['utterance_diagnostics']['records'][0]
        self.assertEqual(utterance['pause_status'], 'partial')
        self.assertEqual(utterance['authored_pause_seconds'], .1)
        self.assertAlmostEqual(utterance['known_required_seconds'], 2 / 6.4 + .1)
        self.assertEqual(utterance['between_parts'], [.5])
        self.assertEqual(report['semantic_review'], 'not_performed')

    def test_missing_or_invalid_windows_cannot_claim_capacity_checked(self):
        for changes in ({'start': None}, {'end': True}, {'end': float('nan')},
                        {'start': -.1}, {'end': 3}, {'start': 1.2, 'end': .2}):
            with self.subTest(changes=changes):
                part = fragment()
                part.update(changes)
                result = text_dialogue_capacity([unit(parts=[part])])
                self.assertEqual(result['status'], 'blocked')
                self.assertIn('SPEECH_WINDOW_INVALID', {e['code'] for e in result['errors']})
                self.assertEqual(result['speech_window_diagnostics']['shots'][0]
                                 ['timing_status'], 'unavailable')
        self.assertEqual(self.facts(unit(parts=[{'text': '甲'}]))['shots'][0]
                         ['timing_status'], 'unavailable')

    def test_overlapping_windows_are_not_reported_as_negative_gaps(self):
        row = self.facts(unit(end=4, parts=[fragment(start=0, end=2),
                                          fragment(start=1, end=3)]))['shots'][0]
        self.assertEqual(row['timing_status'], 'unavailable')
        self.assertNotIn('between_fragments', row)

    def test_no_input_mutation_and_diagnostics_are_json_serializable(self):
        data = unit(parts=[fragment()])
        original = copy.deepcopy(data)
        result = text_dialogue_capacity([data])
        self.assertEqual(data, original)
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_delivery_wrapper_forwards_nested_facts_and_keeps_gate(self):
        from storyboard_text_delivery import require_dialogue_capacity
        data = unit(parts=[fragment(start=.2, end=1.3)])
        self.assertEqual(require_dialogue_capacity([data]), text_dialogue_capacity([data]))
        with self.assertRaisesRegex(ValueError, 'SPEECH_READING_CAPACITY'):
            require_dialogue_capacity([unit(parts=[fragment(start=.2, end=.3)])])
        with self.assertRaisesRegex(ValueError, 'SPEECH_READING_CAPACITY'):
            require_dialogue_capacity([unit(end=1, parts=[fragment('甲乙丙丁戊己庚', start=0, end=1)])])

    def test_complete_and_incremental_reports_forward_actual_parsed_windows(self):
        import storyboard_text_delivery as delivery
        import test_storyboard_text_delivery as fixtures
        fx = fixtures.TextDeliveryIntegration()
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        before = fx.snapshot()
        _, _, complete = delivery.prepare(fx.project, fx.draft)
        rows = complete['dialogue_capacity']['speech_window_diagnostics']['shots']
        self.assertTrue(rows)
        self.assertTrue(all(row['timing_status'] == 'available' for row in rows))
        self.assertEqual(fx.snapshot(), before)
        draft = fx.write_draft('prefix.txt', fx.authored_text(fx.state['records'][:2]))
        before = fx.snapshot()
        incremental = delivery.check_incremental(fx.project, draft)
        rows = incremental['dialogue_capacity']['speech_window_diagnostics']['shots']
        self.assertEqual(rows[0]['fragments'][0]['start'], .2)
        self.assertEqual(rows[0]['fragments'][0]['end'], 2.8)
        self.assertEqual(fx.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
