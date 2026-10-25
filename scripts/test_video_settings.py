"""Intake gates, ratio isolation and planned-range behavior, using temporary projects."""
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import director as d
import video_settings as settings


class VideoSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.chat = Path(self.temp.name) / 'chat'
        self.project = self.chat / 'project-a'
        self.project.mkdir(parents=True)

    def style(self, project=None):
        project = project or self.project
        project.mkdir(parents=True, exist_ok=True)
        return d.set_style(SimpleNamespace(project=project, choice='1', file=None))

    def test_each_intake_gate_only_saves_its_setting(self):
        self.assertEqual(self.style()['next_step'], 'ratio')
        self.assertEqual({p.name for p in self.project.iterdir()}, {'video-style.txt'})
        settings.save_ratio(self.project, '9:16')
        self.assertEqual({p.name for p in self.project.iterdir()}, {'video-style.txt', 'video-ratio.txt'})
        self.assertFalse((self.project / 'session.json').exists())
        self.assertFalse((self.project / 'project-contract.json').exists())
        self.assertEqual(settings.require_intake(self.project), '9:16')

    def test_ratio_cannot_precede_style_or_be_guessed(self):
        with self.assertRaisesRegex(ValueError, '风格'):
            settings.save_ratio(self.project, '16:9')
        self.style()
        with self.assertRaisesRegex(ValueError, '比例'):
            settings.require_intake(self.project)
        with self.assertRaisesRegex(ValueError, '尚未确认'):
            settings.save_ratio(self.project, inherit=True)
        with self.assertRaises(ValueError):
            settings.save_ratio(self.project, '4:3')

    def test_chat_default_is_reused_but_old_project_keeps_its_ratio(self):
        self.style()
        settings.save_ratio(self.project, '9:16')
        second = self.chat / 'project-b'
        self.style(second)
        self.assertEqual(settings.save_ratio(second, inherit=True)['video_ratio'], '9:16')
        settings.save_ratio(second, '16:9')
        self.assertEqual(settings.get_ratio(self.project), '9:16')
        self.assertEqual(settings.read_default(self.project), '16:9')
        elsewhere = Path(self.temp.name) / 'other-chat' / 'project-c'
        self.style(elsewhere)
        self.assertIsNone(settings.read_default(elsewhere))

    def test_ratio_change_does_not_modify_receipt_or_assets(self):
        self.style()
        settings.save_ratio(self.project, '16:9')
        state = {'revision': 1, 'phase': 'range', 'video_ratio': '16:9',
                 'video_ratio_hash': settings.digest('16:9'), 'ledger': {'asset_ratio': '16:9'},
                 'units': [{'video_ratio': '16:9'}], 'receipts': [{'units': [{'video_ratio': '16:9'}]}]}
        old = copy.deepcopy(state)
        d.set_ratio(SimpleNamespace(project=self.project, choice='2', inherit=False), state)
        self.assertEqual(settings.get_ratio(self.project, state), '9:16')
        for key in ('ledger', 'units', 'receipts'):
            self.assertEqual(state[key], old[key])

    def test_outside_edits_and_deleted_confirmed_ratio_are_detected(self):
        self.style()
        settings.save_ratio(self.project, '16:9')
        state = {'video_ratio_hash': settings.digest('16:9')}
        (self.project / settings.RATIO_FILE).write_text('9:16\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '外部更改'):
            settings.get_ratio(self.project, state)
        (self.project / settings.RATIO_FILE).unlink()
        with self.assertRaisesRegex(ValueError, '尚未保存'):
            settings.get_ratio(self.project, state)

    def test_legacy_default_is_read_only_but_new_workflow_requires_ratio(self):
        self.assertEqual(settings.get_ratio(self.project), '16:9')
        self.assertEqual(list(self.project.iterdir()), [])
        self.style()
        settings.save_ratio(self.project, '16:9')
        settings.mark_workflow(self.project)
        (self.project / settings.RATIO_FILE).unlink()
        with self.assertRaises(ValueError):
            settings.get_ratio(self.project)


class PartitionRangeReviewTests(unittest.TestCase):
    def setUp(self):
        self.state = {'length': 30, 'revision': 1, 'ranges': [{'span': [0, 10]}],
                      'records': [{'start': 0, 'end': 30, 'text': '甲' * 30}]}
        self.plan = {'status': 'confirmed', 'segments': [
            {'label': 'A', 'span': [0, 10]}, {'label': 'B', 'span': [10, 20]},
            {'label': 'C', 'span': [20, 30]}]}

    def test_unmade_whole_segment_can_skip_other_planned_work(self):
        with patch('script_partition.load_plan', return_value=self.plan):
            self.assertEqual(d.range_review(self.state, [20, 30], Path('.'))['issues'], [])
        self.assertTrue(d.range_review(self.state, [20, 30])['issues'])

    def test_partial_gap_and_duplicate_coverage_still_require_choice(self):
        with patch('script_partition.load_plan', return_value=self.plan):
            self.assertTrue(d.range_review(self.state, [22, 30], Path('.'))['issues'])
            report = d.range_review(self.state, [0, 10], Path('.'))
            self.assertEqual(report['issues'][0]['kind'], '重叠')
            self.assertIsNotNone(report['confirmation_token'])

    def test_backward_whole_unmade_segment_is_not_accidental_gap(self):
        self.state['ranges'] = [{'span': [20, 30]}]
        with patch('script_partition.load_plan', return_value=self.plan):
            self.assertEqual(d.range_review(self.state, [0, 10], Path('.'))['issues'], [])

    def test_repeat_round_alias_does_not_select_previous_generation(self):
        state = {'units': [
            {'id': '场景1-分镜1', 'display_name': 'A01-场景1-分镜1', 'segment_round': 0},
            {'id': '场景1-分镜2', 'display_name': 'A01-场景1-分镜1-1', 'segment_round': 1}]}
        self.assertEqual(d.resolve_unit_id(state, 'A01-1'), '场景1-分镜2')
        self.assertEqual(d.resolve_unit_id(state, 'A01-场景1-分镜1'), '场景1-分镜1')
        self.assertEqual(d.resolve_unit_id(state, '场景1-分镜2'), '场景1-分镜2')
        with self.assertRaisesRegex(ValueError, '无法唯一定位'):
            d.resolve_unit_id(state, 'A01')


if __name__ == '__main__':
    unittest.main()
