"""Offline current-authoring camera gates and historical render compatibility."""
import copy
import unittest
from unittest.mock import patch

import director as d
import storyboard_text as core
import storyboard_text_delivery as delivery
import test_storyboard_text_delivery as fixtures


class CameraConstraintChecks(unittest.TestCase):
    @staticmethod
    def unit(*shots, name='new'):
        cursor, result = 0, []
        for duration, camera in shots:
            result.append({'start': cursor, 'end': cursor + duration, 'camera': camera})
            cursor += duration
        return {'id': name, 'shots': result}

    def test_long_fixed_is_rejected_but_short_fixed_is_allowed(self):
        for duration in range(1, 7):
            report = core.camera_constraints([self.unit((duration, '相机固定，演员向右平移'))])
            self.assertEqual([e['code'] for e in report['errors']], ['LONG_SHOT_FIXED'] if duration >= 5 else [])
            self.assertEqual(report['semantic_review'], 'not_performed')

    def test_actor_motion_and_focus_do_not_satisfy_camera_motion(self):
        for camera in ('固定机位，演员奔跑', '相机固定并移焦', '相机固定，焦点由前景转向后景'):
            report = core.camera_constraints([self.unit((5, camera))])
            self.assertEqual([e['code'] for e in report['errors']], ['LONG_SHOT_FIXED'])

    def test_unknown_or_sequential_camera_prose_is_not_automatically_approved(self):
        for camera in ('门框清晰', '相机固定后推近', '相机固定，相机随后向右平移', '相机固定，随后向右平移', '镜头稳定'):
            report = core.camera_constraints([self.unit((6, camera))])
            self.assertFalse(report['errors'], camera)
            self.assertEqual(report['status'], 'needs_semantic_review', camera)
            self.assertTrue(report['warnings'])

    def test_adjacent_fixed_across_units_and_selected_boundaries(self):
        left = self.unit((4, '相机固定'), name='old-left')
        right = self.unit((6, '相机固定'), name='old-right')
        current = self.unit((2, '相机固定'))
        before = copy.deepcopy((left, right, current))
        report = core.camera_constraints([current], left, right)
        self.assertEqual([e['code'] for e in report['errors']], ['ADJACENT_FIXED', 'ADJACENT_FIXED'])
        self.assertEqual((left, right, current), before)
        report = core.camera_constraints([self.unit((2, '相机固定'), (2, '相机向右平移')), current])
        self.assertFalse(report['errors'])

    def fixture(self):
        test = fixtures.TextDeliveryIntegration()
        test.setUp()
        self.addCleanup(test.doCleanups)
        return test

    def test_preview_blocks_explicit_violation_without_writing_any_artifact(self):
        test = self.fixture()
        text = test.authored_text().replace('00秒-04秒', '00秒-05秒').replace('相机向右横移', '相机固定')
        draft = test.write_draft('bad-camera.txt', text)
        before = test.snapshot()
        with self.assertRaisesRegex(ValueError, '5～6秒镜头不得全程固定'):
            delivery.preview(test.project, draft)
        self.assertEqual(test.snapshot(), before)

    def test_text_receipt_uses_saved_v2_header_and_new_authoring_uses_project_header(self):
        test = self.fixture()
        state, units, _ = delivery.prepare(test.project, test.draft)
        current = dict(units[0], id='new')
        old = dict(current, header_policy='visual-only-v2')
        unmarked = {key: value for key, value in old.items() if key != 'header_policy'}
        old_render = core.render_unit(state, old)
        self.assertEqual(core.render_unit(state, unmarked), old_render)
        self.assertIn('Subtle micro-expressions', old_render)
        current_render = core.render_unit(state, current)
        self.assertIn('测试固定抬头第一行\n测试固定抬头第二行', current_render)
        self.assertEqual(current['header_policy'], 'custom-v1')

    def test_old_fixed_receipt_retry_preserves_saved_output_but_new_commit_is_rejected(self):
        test = self.fixture()
        old_draft = test.write_draft('historical.txt', test.authored_text().replace(
            '00秒-04秒', '00秒-05秒').replace('相机向右横移', '相机固定'))
        # Simulate a receipt committed under the former rules in a synthetic
        # fixture only. The current retry must not apply new creative gates.
        with patch.object(delivery, 'require_camera_constraints'):
            test.commit_fixture(draft=old_draft)
        state = d.load(test.project)
        for unit in state['units'] + state['receipts'][0]['units']:
            unit['header_policy'] = 'visual-only-v2'
        d.write_json(test.project / 'session.json', state)
        test.output.write_text(delivery.render_units(state, state['receipts'][0]['units']), encoding='utf-8')
        before = test.output.read_bytes()
        result = delivery.commit(test.project, old_draft, 'fictional-first', test.review, test.output)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(test.output.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, '5～6秒镜头不得全程固定'):
            delivery.commit(test.project, old_draft, 'new-request', test.review, test.project / 'new.txt')

    def test_replacement_does_not_treat_an_older_batch_as_adjacent(self):
        state = {'units': [dict(self.unit((6, '相机固定'), name='A-last'), span=[10, 20], batch_id='A'),
                           dict(self.unit((4, '相机向右横移'), name='B-first'), span=[0, 5], batch_id='B', predecessor=None),
                           dict(self.unit((4, '相机固定'), name='B-second'), span=[5, 10], batch_id='B', predecessor='B-first')]}
        before, after = core.camera_context(state, [state['units'][1]], ['B-first'])
        self.assertIsNone(before)
        self.assertEqual(after['id'], 'B-second')


if __name__ == '__main__':
    unittest.main()
