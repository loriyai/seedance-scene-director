"""Pure text-dialogue lower-bound checks; no automatic authoring or approval."""
import copy
from decimal import Decimal
import json
import unittest

from speech_timing_audit import text_dialogue_capacity


def unit(texts, start=0, end=1, key='SC0001/u01'):
    parts = [{'text': text, 'record': f'L{i:06d}', 'start': start, 'end': end}
             for i, text in enumerate(texts, 1)]
    try:
        width = (Decimal(str(end)) - Decimal(str(start))) / max(1, len(parts))
        for i, part in enumerate(parts):
            part.update(start=Decimal(str(start)) + i * width,
                        end=Decimal(str(start)) + (i + 1) * width)
            if not isinstance(start, Decimal) and not isinstance(end, Decimal):
                part.update(start=float(part['start']), end=float(part['end']))
    except Exception:
        pass  # Invalid-shot tests exercise the public validator, not this helper.
    return {'draft_key': key, 'shots': [
        {'start': start, 'end': end,
         'dialogue_parts': parts}]}


class TextDialogueCapacityTests(unittest.TestCase):
    def test_exact_integer_threshold_is_checked_without_approval(self):
        result = text_dialogue_capacity([unit(['甲' * 32], end=5)])
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['semantic_review'], 'not_performed')
        self.assertEqual(result['checked_dialogue_shots'], 1)
        self.assertEqual(result['errors'], [])

    def test_below_threshold_has_precise_unit_shot_and_deficit(self):
        result = text_dialogue_capacity([unit(['甲乙丙丁戊己庚'])])
        self.assertEqual(result['status'], 'blocked')
        error = result['warnings'][0]
        self.assertEqual((error['code'], error['unit'], error['shot']),
                         ('DIALOGUE_CAPACITY', 'SC0001/u01', 1))
        self.assertAlmostEqual(error['minimum'], 7 / 6.4)
        self.assertAlmostEqual(error['deficit'], 7 / 6.4 - 1)
        self.assertEqual(error['duration'], 1)

    def test_punctuation_is_a_soft_cluster_reference_not_a_capacity_charge(self):
        text = '甲，乙、丙,丁。戊！己？!?'  # 6 letters, 3 commas, 5 sentence endings.
        result = text_dialogue_capacity([unit([text], end=Decimal('1'))])
        self.assertEqual(result['status'], 'checked')
        fact = result['speech_window_diagnostics']['shots'][0]['fragments'][0]
        self.assertEqual(fact['phonetic_minimum'], .9375)
        self.assertEqual(fact['known_required_seconds'], .9375)
        self.assertIsNone(fact['authored_pause_seconds'])
        self.assertEqual(fact['pause_status'], 'unspecified')
        reference = result['utterance_diagnostics']['records'][0]['punctuation_reference']
        self.assertFalse(reference['blocking'])
        self.assertEqual(len(reference['clusters']), 6)
        self.assertAlmostEqual(reference['reference_seconds'], .975)

    def test_decimal_comparison_has_no_float_epsilon_exemption(self):
        result = text_dialogue_capacity([unit(['甲乙丙丁戊己，。'],
                                             end=Decimal('0.937499999999999999999'))])
        self.assertEqual(result['status'], 'blocked')
        self.assertGreater(result['warnings'][0]['deficit'], 0)
        self.assertEqual(text_dialogue_capacity([
            unit(['甲乙丙丁戊己，。'], end=Decimal('0.9375'))])['status'], 'checked')

    def test_fractional_capacity_boundary_does_not_allow_faster_speech(self):
        accepted = text_dialogue_capacity([unit(['甲' * 32], end=Decimal('5'))])
        rejected = text_dialogue_capacity([unit(['甲' * 32], end=Decimal('4.999'))])
        self.assertEqual(accepted['policy'], 'scene-text-dialogue-capacity-v4')
        self.assertEqual(accepted['status'], 'checked')
        self.assertEqual(rejected['status'], 'blocked')
        self.assertIn('SPEECH_READING_CAPACITY', {e['code'] for e in rejected['errors']})

    def test_every_fragment_in_one_shot_is_aggregated(self):
        result = text_dialogue_capacity([unit(['甲乙丙', '丁戊己庚'])])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(len([e for e in result['warnings'] if e['code'] == 'DIALOGUE_CAPACITY']), 1)
        self.assertEqual(result['warnings'][0]['dialogue_characters'], 7)

    def test_multiple_speakers_share_one_capacity_window(self):
        data = unit(['甲乙丙丁', '戊己庚辛'])
        for index, part in enumerate(data['shots'][0]['dialogue_parts'], 1):
            part['character_id'] = f'CHAR{index:04d}'
        result = text_dialogue_capacity([data])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['warnings'][0]['dialogue_characters'], 8)

    def test_original_shot_check_is_retained_beside_stricter_window_check(self):
        data = unit(['甲乙丙丁戊己'], end=2)
        data['shots'][0]['dialogue_parts'][0].update(start=.2, end=.3)
        result = text_dialogue_capacity([data])
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('DIALOGUE_CAPACITY', {e['code'] for e in result['errors']})
        self.assertIn('SPEECH_WINDOW_CAPACITY', {e['code'] for e in result['warnings']})
        self.assertIn('SPEECH_READING_CAPACITY', {e['code'] for e in result['errors']})

    def test_silent_shots_are_skipped_without_inferring_duration_or_activity(self):
        data = {'draft_key': 'SC0001/u01', 'shots': [
            {'start': 0, 'end': 3, 'text': '作者已有动作', 'dialogue_parts': []},
            {'start': 3, 'end': 4, 'text': '作者已有保持'}]}
        result = text_dialogue_capacity([data])
        self.assertEqual(result['checked_dialogue_shots'], 0)
        self.assertEqual(result['skipped_no_dialogue_shots'], 2)
        self.assertEqual(result['status'], 'checked')

    def test_unicode_letters_numbers_count_but_symbols_and_spaces_do_not(self):
        result = text_dialogue_capacity([unit(['甲AéΩ١２Ⅳ²', ' ☃🙂 '])])
        self.assertEqual(result['warnings'][0]['dialogue_characters'], 8)

    def test_nonzero_shot_start_is_subtracted_from_end(self):
        result = text_dialogue_capacity([unit(['甲乙丙丁戊己庚'], start=3, end=4)])
        self.assertEqual(result['warnings'][0]['duration'], 1)

    def test_only_blocking_shot_reported_and_input_remains_byte_equivalent(self):
        data = unit(['甲乙丙丁戊己'], end=1)
        data['shots'].append({'start': 1, 'end': 2,
                             'dialogue_parts': [{'text': '甲乙丙丁戊己庚', 'record': 'L000002', 'start': 1, 'end': 2}]})
        original = copy.deepcopy(data)
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)
        result = text_dialogue_capacity([data])
        self.assertEqual(data, original)
        self.assertEqual(json.dumps(data, ensure_ascii=False, sort_keys=True), encoded)
        self.assertEqual(result['checked_dialogue_shots'], 2)
        self.assertEqual({error['shot'] for error in result['errors']}, {2})
        json.dumps(result, ensure_ascii=False)  # Public diagnostics can be emitted as JSON.

    def test_invalid_dialogue_text_is_rejected(self):
        data = unit(['甲乙'])
        data['shots'][0]['dialogue_parts'][0]['text'] = None
        with self.assertRaisesRegex(ValueError, '原文text'):
            text_dialogue_capacity([data])

    def test_invalid_dialogue_shot_times_are_rejected(self):
        for start, end in [(0, 0), (2, 1), (False, 1), (0, True),
                           ('bad', 1), (0, float('inf')), (0, Decimal('NaN'))]:
            with self.subTest(start=start, end=end), self.assertRaisesRegex(ValueError, '起止秒数'):
                text_dialogue_capacity([unit(['甲乙'], start=start, end=end)])


if __name__ == '__main__':
    unittest.main()
