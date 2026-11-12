"""Synthetic regression checks for the reviewed draft's final timing conversion."""
import copy
import unittest

import storyboard_delivery_timing as timing


def make_unit(lengths, speech=None, key='SC0001/u01'):
    speech = speech or {}
    lines = ['## SC0001 / u01 / L000003-L000003\n', '人物站位：角色在桌边。\n']
    shots = []
    cursor = 0
    for number, length in enumerate(lengths, 1):
        text = (f'·{cursor:02d}秒-{cursor + length:02d}秒[镜头{number}-测试]：'
                '相机向右横移，角色抬手时同步说话。音效：衣料轻响。')
        parts = [dict(record='L000003', start=start, end=end, text=words,
                      character_id='CHAR0001', voice='说')
                 for start, end, words in speech.get(number, [])]
        shots.append({'start': cursor, 'end': cursor + length,
                      'text': text, 'dialogue_parts': parts})
        lines.append(text + '\n')
        for part in parts:
            lines.append(f'声音[{part["record"]},{part["start"]}-{part["end"]}]：'
                         f'@{{CHAR0001}}说：“{part["text"]}”\n')
        cursor += length
    return {'draft_key': key, 'duration': cursor, 'shots': shots,
            'source_text': ''.join(lines)}


class FinalDeliveryTimingTests(unittest.TestCase):
    def test_all_base_lengths_map_once_on_one_continuous_axis(self):
        units = [make_unit([1, 2, 3, 4, 5, 6, 7])]
        final, report = timing.convert(units)
        self.assertEqual([shot['end'] - shot['start'] for shot in final[0]['shots']],
                         [1, 2, 3, 3, 4, 5, 6])
        self.assertEqual([(shot['start'], shot['end']) for shot in final[0]['shots']],
                         [(0, 1), (1, 3), (3, 6), (6, 9), (9, 13), (13, 18), (18, 24)])
        self.assertEqual(final[0]['duration'], 24)
        self.assertEqual(report['base_total_duration'], 28)
        self.assertEqual(report['final_total_duration'], 24)
        self.assertEqual(final[0]['delivery_timing_policy'], timing.POLICY)
        self.assertFalse(report['units'][0]['four_second_unit_exempt'])
        self.assertIn('·18秒-24秒[镜头7-测试]', final[0]['source_text'])

    def test_entire_four_second_units_are_exempt(self):
        for lengths in ([4], [1, 3]):
            with self.subTest(lengths=lengths):
                base = make_unit(lengths)
                final, report = timing.convert([base])
                self.assertEqual(final[0]['duration'], 4)
                self.assertEqual(final[0]['shots'], base['shots'])
                self.assertEqual(final[0]['source_text'], base['source_text'])
                self.assertTrue(report['units'][0]['four_second_unit_exempt'])

    def test_four_second_shot_inside_longer_unit_loses_one_second(self):
        final, report = timing.convert([make_unit([4, 1])])
        self.assertEqual([(shot['start'], shot['end']) for shot in final[0]['shots']],
                         [(0, 3), (3, 4)])
        self.assertEqual(final[0]['duration'], 4)
        self.assertFalse(report['units'][0]['four_second_unit_exempt'])

    def test_voice_windows_use_shot_relative_scale_and_new_absolute_start(self):
        unit = make_unit([4, 5], speech={2: [(5, 8, '保持原话。')]})
        final, _ = timing.convert([unit])
        shot = final[0]['shots'][1]
        self.assertEqual((shot['start'], shot['end']), (3, 7))
        part = shot['dialogue_parts'][0]
        self.assertEqual((part['start'], part['end']), (3.8, 6.2))
        self.assertEqual(part['text'], '保持原话。')
        self.assertIn('声音[L000003,3.8-6.2]：@{CHAR0001}说：“保持原话。”',
                      final[0]['source_text'])
        self.assertEqual(shot['text'].split('秒[', 1)[1],
                         unit['shots'][1]['text'].split('秒[', 1)[1])

    def test_multiple_fragments_share_one_shot_conversion(self):
        unit = make_unit([5], speech={1: [(0, 2, '第一句。'), (2.5, 5, '第二句。')]})
        final, _ = timing.convert([unit])
        parts = final[0]['shots'][0]['dialogue_parts']
        self.assertEqual([(part['start'], part['end']) for part in parts],
                         [(0, 1.6), (2, 4)])
        self.assertAlmostEqual(sum(part['end'] - part['start'] for part in parts), 3.6)
        self.assertEqual(''.join(part['text'] for part in parts), '第一句。第二句。')
        self.assertEqual(final[0]['source_text'].count('第一句。'), 1)
        self.assertEqual(final[0]['source_text'].count('第二句。'), 1)

    def test_conversion_preserves_inputs_and_is_repeatable_from_base(self):
        units = [make_unit([5], speech={1: [(1, 4, '原话不改。')]})]
        constraints = {'SC0001/u01#1': [{'label': '接触后承重', 'start': 1, 'end': 3}]}
        before = copy.deepcopy((units, constraints))
        first = timing.convert(units, constraints)
        second = timing.convert(units, constraints)
        self.assertEqual(first, second)
        self.assertEqual((units, constraints), before)
        first[0][0]['shots'][0]['dialogue_parts'][0]['text'] = '测试独立副本'
        self.assertEqual((units, constraints), before)

    def test_final_units_cannot_be_converted_again(self):
        final, _ = timing.convert([make_unit([5])])
        before = copy.deepcopy(final)
        with self.assertRaisesRegex(ValueError, '不能重复减秒'):
            timing.convert(final)
        self.assertEqual(final, before)

    def test_local_constraints_scale_without_unit_start_offset(self):
        constraints = {'SC0001/u01#2': [
            {'label': '手部接触', 'start': 1, 'end': 2},
            {'label': '持续承重', 'start': 2, 'end': 5}]}
        _, report = timing.convert([make_unit([4, 5])], constraints)
        row = report['units'][0]['shots'][1]
        self.assertEqual(row['base_constraints'], constraints['SC0001/u01#2'])
        self.assertEqual(row['final_constraints'], [
            {'label': '手部接触', 'start': 0.8, 'end': 1.6},
            {'label': '持续承重', 'start': 1.6, 'end': 4}])

    def receipt(self):
        bases = [make_unit([5], speech={1: [(1, 4, '原话不改。')]})]
        constraints = {'SC0001/u01#1': [{'label': '关键接触', 'start': 1, 'end': 2}]}
        final, report = timing.convert(bases, constraints)
        return {'source_units': bases, 'units': final, 'delivery_timing': report}

    def test_frozen_receipt_verifies_without_mutation(self):
        receipt = self.receipt()
        before = copy.deepcopy(receipt)
        self.assertEqual(timing.verify_receipt(receipt), receipt['source_units'])
        self.assertEqual(receipt, before)

    def test_receipt_rejects_changed_base_final_mapping_and_constraints(self):
        for change in ('base', 'final', 'mapping', 'constraint', 'digest'):
            with self.subTest(change=change):
                receipt = self.receipt()
                if change == 'base':
                    receipt['source_units'][0]['shots'][0]['text'] += '新增正文'
                elif change == 'final':
                    receipt['units'][0]['source_text'] += '新增正文'
                elif change == 'mapping':
                    receipt['delivery_timing']['units'][0]['shots'][0]['final_end'] = 3
                elif change == 'constraint':
                    receipt['delivery_timing']['units'][0]['shots'][0]['final_constraints'][0]['end'] = 2
                else:
                    receipt['delivery_timing']['base_units_sha256'] = 'invalid'
                with self.assertRaisesRegex(ValueError, '冻结回执不一致'):
                    timing.verify_receipt(receipt)

    def test_legacy_receipt_is_readable_but_marked_units_need_policy_record(self):
        units = [make_unit([4])]
        self.assertEqual(timing.verify_receipt({'units': units}), units)
        final, _ = timing.convert(units)
        with self.assertRaisesRegex(ValueError, '缺少政策记录'):
            timing.verify_receipt({'units': final})

    def test_receipt_requires_known_policy_and_reviewed_base(self):
        receipt = self.receipt()
        receipt['delivery_timing']['policy'] = 'unknown'
        with self.assertRaisesRegex(ValueError, '未知的最终时长转换政策'):
            timing.verify_receipt(receipt)
        receipt = self.receipt()
        del receipt['source_units']
        with self.assertRaisesRegex(ValueError, '缺少已审基础单元'):
            timing.verify_receipt(receipt)

    def test_submillisecond_collapse_fails_instead_of_losing_speech(self):
        unit = make_unit([4, 1], speech={1: [(0.002, 0.003, '字')]})
        before = copy.deepcopy(unit)
        with self.assertRaisesRegex(ValueError, '不足毫秒精度'):
            timing.convert([unit])
        self.assertEqual(unit, before)

    def test_submillisecond_constraint_collapse_is_also_rejected(self):
        constraints = {'SC0001/u01#1': [{'label': '接触', 'start': 0.002, 'end': 0.003}]}
        with self.assertRaisesRegex(ValueError, '不足毫秒精度'):
            timing.convert([make_unit([4, 1])], constraints)


if __name__ == '__main__':
    unittest.main()
