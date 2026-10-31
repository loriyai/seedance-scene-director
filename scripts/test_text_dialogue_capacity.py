"""Pure text-dialogue lower-bound checks; no automatic authoring or approval."""
import copy
from decimal import Decimal
import json
import unittest

from speech_timing_audit import text_dialogue_capacity


def unit(texts, start=0, end=1, key='SC0001/u01'):
    return {'draft_key': key, 'shots': [
        {'start': start, 'end': end,
         'dialogue_parts': [{'text': text} for text in texts]}]}


class TextDialogueCapacityTests(unittest.TestCase):
    def test_exact_integer_threshold_is_checked_without_approval(self):
        result = text_dialogue_capacity([unit(['甲乙丙丁戊己'])])
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['semantic_review'], 'not_performed')
        self.assertEqual(result['checked_dialogue_shots'], 1)
        self.assertEqual(result['errors'], [])

    def test_below_threshold_has_precise_unit_shot_and_deficit(self):
        result = text_dialogue_capacity([unit(['甲乙丙丁戊己庚'])])
        self.assertEqual(result['status'], 'blocked')
        error = result['errors'][0]
        self.assertEqual((error['code'], error['unit'], error['shot']),
                         ('DIALOGUE_CAPACITY', 'SC0001/u01', 1))
        self.assertAlmostEqual(error['minimum'], 7 / 6)
        self.assertAlmostEqual(error['deficit'], 1 / 6)
        self.assertEqual(error['duration'], 1)

    def test_comma_pause_and_sentence_end_follow_exact_formula(self):
        text = '甲，乙、丙,丁。戊！己？!?'  # 6 letters, 3 commas, 5 sentence endings.
        result = text_dialogue_capacity([unit([text], end=Decimal('2.3'))])
        self.assertEqual(result['status'], 'checked')
        blocked = text_dialogue_capacity([unit([text], end=Decimal('2.29'))])
        error = blocked['errors'][0]
        self.assertEqual(error['dialogue_characters'], 6)
        self.assertEqual(error['comma_or_pause_count'], 3)
        self.assertEqual(error['sentence_end_count'], 5)
        self.assertAlmostEqual(error['minimum'], 2.3)
        self.assertAlmostEqual(error['deficit'], .01)

    def test_decimal_comparison_has_no_float_epsilon_exemption(self):
        result = text_dialogue_capacity([unit(['甲乙丙丁戊己，。'],
                                             end=Decimal('1.299999999999999999999'))])
        self.assertEqual(result['status'], 'blocked')
        self.assertGreater(result['errors'][0]['deficit'], 0)
        self.assertEqual(text_dialogue_capacity([
            unit(['甲乙丙丁戊己，。'], end=Decimal('1.3'))])['status'], 'checked')

    def test_every_fragment_in_one_shot_is_aggregated(self):
        result = text_dialogue_capacity([unit(['甲乙丙', '丁戊己庚'])])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['errors'][0]['dialogue_characters'], 7)

    def test_multiple_speakers_share_one_capacity_window(self):
        data = unit(['甲乙丙丁', '戊己庚辛'])
        for index, part in enumerate(data['shots'][0]['dialogue_parts'], 1):
            part['character_id'] = f'CHAR{index:04d}'
        result = text_dialogue_capacity([data])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['errors'][0]['dialogue_characters'], 8)

    def test_uses_entire_shot_not_fragment_subwindow(self):
        data = unit(['甲乙丙丁戊己'], end=2)
        data['shots'][0]['dialogue_parts'][0].update(start=.2, end=.3)
        self.assertEqual(text_dialogue_capacity([data])['status'], 'checked')

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
        self.assertEqual(result['errors'][0]['dialogue_characters'], 8)

    def test_nonzero_shot_start_is_subtracted_from_end(self):
        result = text_dialogue_capacity([unit(['甲乙丙丁戊己庚'], start=3, end=4)])
        self.assertEqual(result['errors'][0]['duration'], 1)

    def test_only_blocking_shot_reported_and_input_remains_byte_equivalent(self):
        data = unit(['甲乙丙丁戊己'], end=1)
        data['shots'].append({'start': 1, 'end': 2,
                             'dialogue_parts': [{'text': '甲乙丙丁戊己庚'}]})
        original = copy.deepcopy(data)
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)
        result = text_dialogue_capacity([data])
        self.assertEqual(data, original)
        self.assertEqual(json.dumps(data, ensure_ascii=False, sort_keys=True), encoded)
        self.assertEqual(result['checked_dialogue_shots'], 2)
        self.assertEqual([error['shot'] for error in result['errors']], [2])
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
