"""Synthetic integration cases for review-first final timing conversion."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import director as d
import canvas
import storyboard_text_delivery as delivery
import storyboard_delivery_timing as timing
import test_storyboard_text_delivery as fixtures
import test_storyboard_authoring_v4 as authoring_fixtures


class FinalTimingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TextDeliveryIntegration('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fx = self.fixture
        self.fx.draft.write_text(self.fx.authored_text().replace('00秒-04秒', '00秒-07秒'),
                                 encoding='utf-8')

    def test_preview_keeps_base_and_commit_freezes_final_for_all_consumers(self):
        base_bytes = self.fx.draft.read_bytes()
        preview = delivery.preview(self.fx.project, self.fx.draft)
        self.assertEqual(preview['total_duration'], 28)
        self.assertEqual(preview['delivery_timing']['final_total_duration'], 24)
        self.assertIn('00秒-07秒', Path(preview['output']).read_text(encoding='utf-8'))
        result = self.fx.commit_fixture()
        self.assertEqual(result['total_seconds'], 24)
        state = d.load(self.fx.project)
        receipt = state['receipts'][0]
        self.assertEqual([u['duration'] for u in receipt['source_units']], [7] * 4)
        self.assertEqual([u['duration'] for u in state['units']], [6] * 4)
        self.assertEqual(self.fx.draft.read_bytes(), base_bytes)
        for unit in state['units']:
            self.assertIn('00秒-06秒', unit['source_text'])
            rendered = d.render_unit(state, unit)
            self.assertIn('时长：6秒', rendered)
            self.assertIn('00秒-06秒', rendered)
        part = state['units'][1]['shots'][0]['dialogue_parts'][0]
        self.assertEqual((part['start'], part['end']), (0.171, 2.4))
        self.assertEqual(timing.verify_receipt(receipt), receipt['source_units'])
        self.assertEqual(self.fx.output.read_text(encoding='utf-8'),
                         delivery.render_units(state, receipt['units'], historical_receipt=receipt))
        videos, _ = canvas.desired_videos(state, {'mappings': {}}, {}, state['units'])
        self.assertEqual([video['duration'] for video in videos], [6] * 4)
        self.assertTrue(all('时长：6秒' in video['prompt'] for video in videos))

    def test_failed_write_retry_and_cli_export_never_repeat_compression(self):
        self.fx.write_synthetic_review_fixture()
        atomic_text = d.atomic_text

        def interrupted(path, text):
            if Path(path).resolve() == self.fx.output.resolve():
                raise OSError('synthetic final-write interruption')
            return atomic_text(path, text)

        with patch.object(d, 'atomic_text', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interruption'):
                delivery.commit(self.fx.project, self.fx.draft, 'timed', self.fx.review, self.fx.output)
        frozen = (self.fx.project / 'session.json').read_bytes()
        for _ in range(2):
            result = delivery.commit(self.fx.project, self.fx.draft, 'timed', self.fx.review, self.fx.output)
            self.assertEqual(result['status'], 'already_committed')
            self.assertEqual(result['total_seconds'], 24)
            self.assertEqual((self.fx.project / 'session.json').read_bytes(), frozen)
        exported = self.fx.project / 'outputs/reexport.txt'
        self.fx.cli('storyboard_text_delivery.py', 'render', '--project', self.fx.project,
                    '--request-id', 'timed', '--output', exported)
        self.assertEqual(exported.read_bytes(), self.fx.output.read_bytes())
        self.fx.cli('director.py', 'render', '--project', self.fx.project, '--request-id', 'timed')
        self.assertEqual(exported.read_bytes(), self.fx.output.read_bytes())

    def test_unreviewed_or_stale_draft_cannot_create_final_state(self):
        self.fx.write_synthetic_review_fixture()
        report = d.read_json(self.fx.review)
        report['scenes'][0]['status'] = 'needs_rewrite'
        d.write_json(self.fx.review, report)
        before = (self.fx.project / 'session.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '未关闭'):
            delivery.commit(self.fx.project, self.fx.draft, 'unreviewed', self.fx.review, self.fx.output)
        self.assertEqual((self.fx.project / 'session.json').read_bytes(), before)
        self.assertFalse(self.fx.output.exists())

    def test_replacement_keeps_original_compressed_receipt_renderable(self):
        self.fx.commit_fixture()
        old_bytes = self.fx.output.read_bytes()
        old = d.load(self.fx.project)['receipts'][0]
        selected = old['source_units'][0]
        replacement = self.fx.write_draft('replacement.txt', selected['source_text'].replace(
            '00秒-07秒', '00秒-06秒'))
        revised_output = self.fx.project / 'outputs/revised.txt'
        self.fx.commit_fixture(replacement, 'revision', revised_output, replace_ids=[selected['id']])
        state = d.load(self.fx.project)
        self.assertEqual(state['units'][0]['duration'], 5)
        self.assertEqual(state['receipts'][0]['units'][0]['duration'], 6)
        d.render_receipt(state, state['receipts'][0], self.fx.project)
        self.assertEqual(self.fx.output.read_bytes(), old_bytes)
        retry = delivery.commit(self.fx.project, replacement, 'revision', self.fx.review,
                                 revised_output, replace_ids=[selected['id']])
        self.assertEqual(retry['status'], 'already_committed')
        self.assertEqual(retry['total_seconds'], 5)

    def test_speech_capacity_is_checked_on_base_before_authorized_final_conversion(self):
        from speech_timing_audit import text_dialogue_capacity
        authored = self.fx.authored_text().replace('00秒-04秒', '00秒-05秒').replace(
            ',0.2-2.8]', ',0.2-1.7]')
        self.fx.draft.write_text(authored, encoding='utf-8')
        _, _, info = delivery.prepare(self.fx.project, self.fx.draft)
        self.assertEqual(info['dialogue_capacity']['errors'], [])
        self.fx.commit_fixture()
        receipt = d.load(self.fx.project)['receipts'][0]
        self.assertTrue(text_dialogue_capacity(receipt['units'])['errors'])
        self.assertEqual(receipt['units'][1]['shots'][0]['dialogue_parts'][0]['text'],
                         receipt['source_units'][1]['shots'][0]['dialogue_parts'][0]['text'])

    def test_old_receipt_without_conversion_policy_reexports_unchanged(self):
        # Construct the pre-change frozen receipt shape from the same reviewed
        # base fixture, then exercise real retry and render entry points.
        self.fx.draft.write_text(self.fx.authored_text().replace('00秒-04秒', '00秒-06秒'), encoding='utf-8')
        self.fx.commit_fixture()
        state = d.load(self.fx.project)
        receipt = state['receipts'][0]
        receipt['units'] = receipt.pop('source_units')
        receipt.pop('delivery_timing')
        state['units'] = copy.deepcopy(receipt['units'])
        binding = {'policy': delivery.POLICY,
                   'draft_sha256': receipt['final_review']['draft_sha256'], 'mode': 'append',
                   'replace_ids': [], 'span': [receipt['units'][0]['span'][0], receipt['units'][-1]['span'][1]],
                   'header_sha256': d.sha(self.fx.header), 'video_ratios': receipt['video_ratios'],
                   'ratio_profiles': receipt['ratio_profiles']}
        receipt['request_hash'] = delivery.digest(binding)
        rendered = delivery.render_units(state, receipt['units'])
        receipt['output_sha256'] = d.sha(rendered)
        d.write_json(self.fx.project / 'session.json', state)
        self.fx.output.write_text(rendered, encoding='utf-8')
        result = delivery.commit(self.fx.project, self.fx.draft, 'fictional-first',
                                 self.fx.review, self.fx.output)
        self.assertEqual(result['total_seconds'], 24)
        self.assertIn('00秒-06秒', self.fx.output.read_text(encoding='utf-8'))

    def test_modern_trace_constraints_are_frozen_without_changing_reviewed_files(self):
        bound = authoring_fixtures.TraceBindingIntegrationTest('runTest')
        bound.setUp()
        self.addCleanup(bound.doCleanups)
        fx = bound.fixture
        authored = fx.authored_text().replace('00秒-04秒', '00秒-05秒').replace(
            '音效：', '浅景深使双手清晰、门框在焦外；主光：左侧窗光照亮手背，辅光：桌面反射托住杯底暗部。音效：')
        fx.draft.write_text(authored, encoding='utf-8')
        bound.decisions_data['units'][0]['shots'][0]['timing_constraints'] = [
            {'label': '接触过程', 'start': 1, 'end': 4}]
        d.write_json(bound.decisions, bound.decisions_data)
        binding = delivery.bind_trace(fx.project, fx.draft, bound.decisions)
        trace = Path(binding['authoring_trace'])
        trace_bytes = trace.read_bytes()
        _, _, info = delivery.prepare(fx.project, fx.draft)
        gate = info['authoring_gate']
        report = {key: info[key] for key in ('draft_sha256', 'context_sha256')}
        report.update(reviewer='synthetic-test-only', semantic_review='performed',
                      authoring_trace_sha256=gate['trace_sha256'], authoring_gate_digest=gate['gate_digest'],
                      shot_count=gate['shot_count'], reviewed_shots=gate['shot_keys'],
                      scenes=[{'scene_id': sid, 'status': 'passed', 'issues': [],
                               'basis': '测试夹具验证绑定，非生产语义审阅。'} for sid in info['scenes']])
        d.write_json(fx.review, report)
        delivery.commit(fx.project, fx.draft, 'bound-timing', fx.review, fx.output)
        receipt = d.load(fx.project)['receipts'][0]
        constraint = receipt['delivery_timing']['units'][0]['shots'][0]['final_constraints'][0]
        self.assertEqual((constraint['start'], constraint['end']), (0.8, 3.2))
        self.assertEqual(trace.read_bytes(), trace_bytes)
        self.assertEqual(fx.draft.read_text(encoding='utf-8'), authored)
        retry = delivery.commit(fx.project, fx.draft, 'bound-timing', fx.review, fx.output)
        self.assertEqual(retry['status'], 'already_committed')


if __name__ == '__main__':
    unittest.main()
