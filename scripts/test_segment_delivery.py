"""Offline numbering, ratio snapshots and delivery checks on synthetic scripts."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import director as d
import segment_naming as naming
import storyboard_context as context
import storyboard_text_delivery as delivery
import test_storyboard_text_delivery as fixture


class SegmentNumbering(unittest.TestCase):
    def setUp(self):
        self.state = {'ledger': {'scenes': [{'id': 'SC1', 'number': 1}, {'id': 'SC2', 'number': 2}]}, 'units': []}
        self.plan = {'policy': 'partition-v1', 'status': 'confirmed', 'plan_id': 'fixture-plan', 'version': 1,
                     'segments': [{'label': 'A', 'span': [0, 100]}, {'label': 'B', 'span': [100, 200]}]}

    def units(self, spans):
        return [{'span': span, 'main_scene': 'SC1' if span[0] < 100 else 'SC2'} for span in spans]

    def add(self, spans):
        units = self.units(spans)
        identities = naming.assignments(self.state, self.plan, units)
        self.state['units'].extend(dict(unit, **identity) for unit, identity in zip(units, identities))
        return identities

    def test_partial_continues_and_whole_restarts_one_shared_round_suffix(self):
        self.assertEqual(self.add([[0, 30]])[0]['display_name'], 'A01-场景1-分镜1')
        self.assertEqual(self.add([[30, 100]])[0]['display_name'], 'A02-场景1-分镜2')
        second = self.add([[0, 50], [50, 100]])
        self.assertEqual([row['display_name'] for row in second], ['A01-场景1-分镜1-1', 'A02-场景1-分镜2-1'])
        self.assertEqual(self.add([[30, 50]])[0]['display_name'], 'A03-场景1-分镜3-1')
        third = self.add([[0, 100]])
        self.assertEqual(third[0]['display_name'], 'A01-场景1-分镜1-2')
        self.assertEqual(len(self.state['units']), 6)

    def test_cross_segment_uses_each_letter_and_preview_is_pure(self):
        before = copy.deepcopy(self.state)
        result = naming.assignments(self.state, self.plan, self.units([[0, 100], [100, 200]]))
        self.assertEqual([row['display_name'] for row in result], ['A01-场景1-分镜1', 'B01-场景2-分镜1'])
        self.assertEqual(self.state, before)
        with self.assertRaisesRegex(ValueError, '跨分段'):
            naming.assignments(self.state, self.plan, self.units([[0, 200]]))

    def test_scene_counts_continue_independently_and_do_not_consume_other_segments(self):
        self.plan['segments'] = [{'label': 'A', 'span': [0, 200]}]
        self.add([[0, 50], [100, 150]])
        result = self.add([[150, 180]])[0]
        self.assertEqual(result['display_name'], 'A03-场景2-分镜2')

    def test_plan_change_and_legacy_name_collision_are_rejected(self):
        self.add([[0, 100]])
        self.plan['plan_id'] = 'another-plan'
        with self.assertRaisesRegex(ValueError, '其他拆分'):
            naming.assignments(self.state, self.plan, self.units([[0, 100]]))
        self.state['units'] = [{'batch_label': 'A'}]
        with self.assertRaisesRegex(ValueError, '历史批次'):
            naming.assignments(self.state, self.plan, self.units([[0, 100]]))


class SegmentDelivery(unittest.TestCase):
    # Reuse only fixture construction/helpers, not its complete inherited suite.
    setUp = fixture.TextDeliveryIntegration.setUp
    authored_text = fixture.TextDeliveryIntegration.authored_text
    write_draft = fixture.TextDeliveryIntegration.write_draft
    write_synthetic_review_fixture = fixture.TextDeliveryIntegration.write_synthetic_review_fixture
    commit_fixture = fixture.TextDeliveryIntegration.commit_fixture
    cli = fixture.TextDeliveryIntegration.cli

    def plan(self):
        records = self.state['records']
        middle = records[2]['start']
        return {'policy': 'partition-v1', 'status': 'confirmed', 'plan_id': 'fixture-plan', 'version': 1,
                'segments': [{'label': 'A', 'span': [0, middle]},
                             {'label': 'B', 'span': [middle, self.state['length']]}]}

    def confirmation(self, draft, span=None):
        _, _, info = delivery.prepare(self.project, draft, span)
        return info['history']['confirmation_token']

    def test_cross_segment_rerun_retry_and_targeted_edit(self):
        with patch('script_partition.load_plan', return_value=self.plan()):
            first = self.commit_fixture()
            self.assertEqual(first['names'], ['A01-场景1-分镜1', 'A02-场景1-分镜2',
                                              'B01-场景2-分镜1', 'B02-场景2-分镜2'])
            rerun_path = self.project / 'outputs' / 'round2.txt'
            token = self.confirmation(self.draft)
            second = self.commit_fixture(request_id='round2', output=rerun_path, range_confirmation=token)
            self.assertEqual(second['names'], [name + '-1' for name in first['names']])
            state = d.load(self.project)
            self.assertEqual(len(state['units']), 8)
            self.assertEqual(len({u['id'] for u in state['units']}), 8)
            self.assertEqual([u['display_name'] for u in state['units'][:4]], first['names'])
            again = delivery.commit(self.project, self.draft, 'round2', self.review, rerun_path)
            self.assertEqual(again['status'], 'already_committed')
            self.assertEqual(len(d.load(self.project)['units']), 8)
            old = state['units'][4]
            edit = self.write_draft('edit.txt', old['source_text'].replace('原作', '修改'))
            changed = self.commit_fixture(edit, request_id='edit', output=self.project / 'outputs/edit.txt',
                                          replace_ids=[old['id']])
            self.assertEqual(changed['names'], [old['display_name']])
            self.assertEqual(len(d.load(self.project)['units']), 8)

    def test_partial_ranges_continue_current_segment_numbers(self):
        with patch('script_partition.load_plan', return_value=self.plan()):
            first_record, second_record = self.state['records'][:2]
            first = self.write_draft('partial1.txt', self.authored_text([first_record]))
            second = self.write_draft('partial2.txt', self.authored_text([second_record]))
            self.assertEqual(self.commit_fixture(first, span=[first_record['start'], first_record['end']])['names'],
                             ['A01-场景1-分镜1'])
            result = self.commit_fixture(second, request_id='partial2', output=self.project / 'outputs/partial2.txt',
                                         span=[second_record['start'], second_record['end']])
            self.assertEqual(result['names'], ['A02-场景1-分镜2'])

    def test_ratio_snapshot_summary_export_and_legacy_render(self):
        ratio_file = self.project / 'video-ratio.txt'
        ratio_file.write_text('9:16\n', encoding='utf-8')
        before = context.context_hash(d.load(self.project), self.project)
        preview = delivery.preview(self.project, self.draft)
        self.assertIn('（时长：4秒）竖屏9:16', Path(preview['output']).read_text(encoding='utf-8'))
        self.commit_fixture()
        state = d.load(self.project)
        receipt = state['receipts'][0]
        result = self.output.read_text(encoding='utf-8')
        self.assertEqual(result.splitlines()[-1], '本次共4个分镜单元，总时长 0分 16秒')
        self.assertNotIn('本次共', d.render_unit(state, receipt['units'][0]))
        ratio_file.write_text('16:9\n', encoding='utf-8')
        self.assertNotEqual(before, context.context_hash(d.load(self.project), self.project))
        exported = self.project / 'outputs/export.txt'
        self.cli('storyboard_text_delivery.py', 'render', '--project', self.project,
                 '--request-id', 'fictional-first', '--output', exported)
        self.assertEqual(exported.read_text(encoding='utf-8'), result)
        legacy = copy.deepcopy(receipt['units'])
        for unit in legacy:
            unit.pop('video_ratio')
            unit.pop('export_format')
        old = delivery.render_units(state, legacy)
        self.assertNotIn('横屏16:9', old)
        self.assertNotIn('竖屏9:16', old)
        self.assertNotIn('本次共', old)


if __name__ == '__main__':
    unittest.main()
