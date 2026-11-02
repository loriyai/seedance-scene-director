"""Reuse only invocation-local checked work; preserve public quality gates."""
import copy
from contextlib import redirect_stdout
import io
import hashlib
import json
import unittest
from unittest.mock import patch

import director as d
import storyboard_authoring_gate as gate
import storyboard_metrics as metrics
import storyboard_text as core
import storyboard_text_delivery as delivery
import test_storyboard_authoring_v4 as fixtures


class SceneTextPerformanceTests(unittest.TestCase):
    def setUp(self):
        native = fixtures.TraceBindingIntegrationTest('runTest')
        native.setUp()
        self.addCleanup(native.doCleanups)
        self.fx = native.fixture
        # Fixed fictional rendering conditions required by the existing v4 gate.
        photography = [
            '桌侧低机位向右横移，杯底与桌面占下部；深景深保留手指和接触面；'
            '主光：左侧窗光照亮桌沿，辅光：白墙反射进入杯底暗部；',
            '门旁机位向说话者靠近，肩部和嘴唇在中央；浅景深使嘴角清楚、桌面虚化；'
            '主光：前方日光覆盖面颊，补光：后方墙壁反照托住耳侧阴影；',
            '走廊斜侧机位沿通路后退，脚下石板和尽头入口同框；景深覆盖双脚与落步处；'
            '主光：右侧日照落在地砖，环境反射：廊柱托住衣摆下方暗部；',
            '相机绕至廊柱旁转向上半身，脸部靠近画面右侧；焦点落在眼口、远处砖墙虚化；'
            '主光：后方逆光勾出发梢，反射光：地面散射轻托颈侧；',
        ]
        blocks = self.fx.draft.read_text(encoding='utf-8').strip().split('\n\n')
        text = '\n\n'.join(block.replace('相机向右横移，人物与门框保持清晰；', photo)
                            for block, photo in zip(blocks, photography)) + '\n'
        self.fx.draft.write_text(text, encoding='utf-8')
        delivery.bind_trace(self.fx.project, self.fx.draft, native.decisions)

    def test_prepare_parses_observes_and_hashes_once_with_identical_public_reports(self):
        with patch.object(core, 'parse', wraps=core.parse) as parse, \
                patch.object(core, 'observations', wraps=core.observations) as observations, \
                patch.object(delivery, 'context_hash', wraps=delivery.context_hash) as context:
            state, units, info = delivery.prepare(self.fx.project, self.fx.draft)
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(observations.call_count, 1)
        self.assertEqual(context.call_count, 1)
        public = core.validate(state, units)
        for key, value in public.items():
            if key not in ('warnings',):
                self.assertEqual(info[key], value, key)
        self.assertEqual(gate.validate(self.fx.project, state, self.fx.draft.read_bytes(),
                                       units, info['context_sha256'], draft_path=self.fx.draft),
                         info['authoring_gate'])
        self.assertNotEqual(info['observations']['preferred_target_summary']['policy'],
                            info['authoring_gate']['preferred_target_summary']['policy'])

    def test_batch_render_builds_context_once_and_matches_public_render_bytes(self):
        state, units, info = delivery.prepare(self.fx.project, self.fx.draft)
        for index, unit in enumerate(units, 1):
            unit['display_name'] = f'fictional-{index}'
        before = copy.deepcopy((state, units))
        with patch.object(core, '_context', wraps=core._context) as context:
            actual = delivery.render_units(state, units, authoring_gate=info['authoring_gate'])
        self.assertEqual(context.call_count, 1)
        expected = [d.render_unit(state, unit, authoring_gate=info['authoring_gate']) for unit in units]
        expected.append('本次共4个分镜单元，总时长 0分 16秒')
        self.assertEqual(actual.encode('utf-8'), ('\n\n'.join(expected) + '\n').encode('utf-8'))
        self.assertEqual((state, units), before)

    def test_public_validate_still_reparses_and_rejects_tampered_cache(self):
        state, units, _ = delivery.prepare(self.fx.project, self.fx.draft)
        with patch.object(core, 'parse', wraps=core.parse) as parse:
            core.validate(state, units)
        self.assertEqual(parse.call_count, 1)
        units[0]['shots'][0]['text'] = 'tampered derived cache'
        with self.assertRaisesRegex(ValueError, '派生字段'):
            core.validate(state, units)

    def test_public_render_and_batch_cannot_skip_authoring_gate(self):
        state, units, _ = delivery.prepare(self.fx.project, self.fx.draft)
        with self.assertRaisesRegex(ValueError, 'storyboard_authoring_gate'):
            core.render_unit(state, units[0])
        with self.assertRaisesRegex(ValueError, 'storyboard_authoring_gate'):
            delivery.render_units(state, units)

    def test_separate_prepare_does_not_reuse_drifted_draft_or_context(self):
        delivery.prepare(self.fx.project, self.fx.draft)
        original = self.fx.draft.read_bytes()
        self.fx.draft.write_bytes(original + b'\n')
        with self.assertRaises(gate.AuthoringGateError) as caught:
            delivery.prepare(self.fx.project, self.fx.draft)
        self.assertIn('AUTHORING_TRACE_DRAFT_BINDING',
                      {row['code'] for row in caught.exception.report['errors']})
        self.fx.draft.write_bytes(original)
        state = d.load(self.fx.project)
        state['entry']['min'] = 5
        d.write_json(self.fx.project / 'session.json', state)
        with self.assertRaises(gate.AuthoringGateError) as caught:
            delivery.prepare(self.fx.project, self.fx.draft)
        self.assertIn('AUTHORING_TRACE_CONTEXT_BINDING',
                      {row['code'] for row in caught.exception.report['errors']})

    def test_commit_keeps_locked_context_recheck(self):
        _, _, info = delivery.prepare(self.fx.project, self.fx.draft)
        authoring = info['authoring_gate']
        review = {key: info[key] for key in ('draft_sha256', 'context_sha256')}
        review.update(reviewer='synthetic-only', semantic_review='performed',
                      authoring_trace_sha256=authoring['trace_sha256'],
                      authoring_gate_digest=authoring['gate_digest'],
                      shot_count=authoring['shot_count'], reviewed_shots=authoring['shot_keys'],
                      scenes=[{'scene_id': sid, 'status': 'passed', 'issues': [],
                               'basis': 'Synthetic binding test only.'} for sid in info['scenes']])
        d.write_json(self.fx.review, review)
        with patch.object(delivery, 'context_hash', side_effect=[info['context_sha256'], 'drift']):
            with self.assertRaisesRegex(ValueError, '提交期间变化'):
                delivery.commit(self.fx.project, self.fx.draft, 'synthetic-drift',
                                self.fx.review, self.fx.output)
        self.assertFalse(self.fx.output.exists())
        self.assertEqual(d.load(self.fx.project)['receipts'], [])

    def test_multiscene_preview_verifies_artifact_twice_and_reuses_exact_events(self):
        with patch.object(metrics, 'validate_preview_artifact',
                          wraps=metrics.validate_preview_artifact) as artifact, \
                patch.object(metrics, 'read_events', wraps=metrics.read_events) as logs:
            first = delivery.preview(self.fx.project, self.fx.draft)
        self.assertEqual(artifact.call_count, 2)  # Before assembly and before append.
        self.assertEqual(logs.call_count, 2)  # Reuse lookup, then fresh append validation.
        self.assertEqual(len(first['preview_event_ids']), 2)
        second = delivery.preview(self.fx.project, self.fx.draft)
        self.assertTrue(second['preview_reused'])
        self.assertEqual(second['preview_event_ids'], first['preview_event_ids'])
        self.assertEqual(len(metrics.read_events(self.fx.project)), 2)

    def test_preview_batch_rejects_drifted_artifact_before_any_event(self):
        original = metrics._record_preview_events

        def changed(project, scenes, **kwargs):
            self.fx.draft.write_bytes(self.fx.draft.read_bytes() + b'\n')
            return original(project, scenes, **kwargs)

        with patch.object(metrics, '_record_preview_events', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '成功事件未全部记录'):
                delivery.preview(self.fx.project, self.fx.draft)
        self.assertEqual(metrics.read_events(self.fx.project), [])

    def test_preview_batch_rechecks_bytes_after_uuid_assembly_before_append(self):
        output = self.fx.project / 'assembled-preview.txt'
        original_draft = self.fx.draft.read_bytes()
        original_uuid = metrics.uuid.uuid4
        for changed_path in (output, self.fx.draft):
            with self.subTest(changed=changed_path.name):
                self.fx.draft.write_bytes(original_draft)
                output.write_bytes(b'fixed fictional preview fixture')
                artifact = {
                    'draft': str(self.fx.draft), 'output': str(output),
                    'draft_sha256': hashlib.sha256(original_draft).hexdigest(),
                    'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                    'context_sha256': 'a' * 64, 'span': [0, 8],
                    'scenes': ['SC0001', 'SC0002'],
                }
                mutated = []

                def mutate_after_first_uuid():
                    value = original_uuid()
                    if not mutated:
                        changed_path.write_bytes(b'changed during event assembly')
                        mutated.append(True)
                    return value

                with patch.object(metrics.uuid, 'uuid4', side_effect=mutate_after_first_uuid):
                    with self.assertRaisesRegex(ValueError, '预览产物不存在或摘要不匹配'):
                        metrics._record_preview_events(
                            self.fx.project, artifact['scenes'], at=delivery.now(),
                            activity_id=None, artifact=artifact)
                self.assertEqual(metrics.read_events(self.fx.project), [])
        self.fx.draft.write_bytes(original_draft)

    def test_preview_context_drift_is_rejected_before_write_or_event(self):
        context = delivery.context_hash(d.load(self.fx.project), self.fx.project)
        with patch.object(delivery, 'context_hash', side_effect=[context, 'changed-context']):
            with self.assertRaisesRegex(ValueError, '上下文在预览期间变化'):
                delivery.preview(self.fx.project, self.fx.draft)
        self.assertEqual(metrics.read_events(self.fx.project), [])

    def test_preview_reuse_also_rechecks_current_context(self):
        first = delivery.preview(self.fx.project, self.fx.draft)
        with patch.object(delivery, 'context_hash',
                          side_effect=[first['context_sha256'], 'changed-context']):
            with self.assertRaisesRegex(ValueError, '上下文在预览期间变化'):
                delivery.preview(self.fx.project, self.fx.draft)
        self.assertEqual(len(metrics.read_events(self.fx.project)), 2)

    def test_cli_preserves_message_and_all_structured_locations_beyond_eight(self):
        errors = [{'code': 'AUTHORING_TRACE_EVIDENCE', 'shot': f'SC0001/u01#{index}',
                   'field': 'viewing_decision', 'message': 'synthetic evidence problem'}
                  for index in range(1, 13)]
        report = {'enforced': True, 'status': 'blocked', 'errors': errors}
        with self.assertRaises(gate.AuthoringGateError) as caught:
            gate.require(report)
        message = str(caught.exception)
        self.assertEqual(message, '逐镜写作闸门：' + '; '.join([errors[0]['message']] * 8))
        out = io.StringIO()
        with patch.object(delivery, 'prepare', side_effect=caught.exception), \
                patch('sys.argv', ['storyboard_text_delivery.py', 'check', '--project',
                                   str(self.fx.project), '--draft', str(self.fx.draft)]), \
                redirect_stdout(out):
            self.assertEqual(delivery.main(), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result['message'], message)
        self.assertEqual(result['errors'], errors)
        self.assertEqual(result['errors'][-1]['shot'], 'SC0001/u01#12')
        report['errors'].clear()
        self.assertEqual(len(caught.exception.report['errors']), 12)


if __name__ == '__main__':
    unittest.main()
