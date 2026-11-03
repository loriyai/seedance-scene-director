"""Public-entry diagnostics on fictional local fixtures, with no approval claims."""
from contextlib import redirect_stdout
import io
import json
import re
import unittest
from unittest.mock import patch

import storyboard_authoring_gate as gate
import storyboard_text as core
import storyboard_text_delivery as delivery
import test_scene_text_performance as fixtures


class TextDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.SceneTextPerformanceTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fx = fixture.fx

    def broken_references_and_lighting(self):
        text = self.fx.draft.read_text(encoding='utf-8')
        text = text.replace('@{CHAR0001}把', '陈默把').replace('@{CHAR0001}走到', '陈默走到')
        text = re.sub(r'主光：[^；\r\n]+；', '', text)
        self.fx.draft.write_text(text, encoding='utf-8')

    def test_preview_reports_multiple_references_and_independent_body_errors_without_writes(self):
        self.broken_references_and_lighting()
        before = self.fx.snapshot()
        with self.assertRaises(core.TextValidationError) as caught:
            delivery.preview(self.fx.project, self.fx.draft)
        errors = caught.exception.errors
        refs = [e for e in errors if e['code'] == 'BARE_CHARACTER_NAME']
        self.assertGreaterEqual(len(refs), 2)
        self.assertGreaterEqual(len({e['unit'] for e in refs}), 2)
        self.assertIn('AUTHORING_LIGHTING_UNDER_SPECIFIED', {e['code'] for e in errors})
        self.assertEqual(before, self.fx.snapshot())

    def test_bind_reports_diagnostics_before_writing_trace(self):
        self.broken_references_and_lighting()
        before = self.fx.snapshot()
        with self.assertRaises(core.TextValidationError) as caught:
            delivery.bind_trace(self.fx.project, self.fx.draft,
                                self.fx.project / 'authored-decisions.json',
                                self.fx.project / 'new-trace.json')
        self.assertIn('AUTHORING_LIGHTING_UNDER_SPECIFIED', {e['code'] for e in caught.exception.errors})
        self.assertEqual(before, self.fx.snapshot())

    def test_bind_collects_references_and_source_mapping_in_one_attempt(self):
        self.broken_references_and_lighting()
        path = self.fx.project / 'authored-decisions.json'
        decisions = json.loads(path.read_text('utf-8'))
        decisions['units'][0]['shots'][0]['source_records'] = []
        path.write_text(json.dumps(decisions, ensure_ascii=False), encoding='utf-8')
        before = self.fx.snapshot()
        with self.assertRaises(core.TextValidationError) as caught:
            delivery.bind_trace(self.fx.project, self.fx.draft, path, self.fx.project / 'new.json')
        self.assertTrue({'BARE_CHARACTER_NAME', 'AUTHORING_TRACE_SOURCE',
                         'AUTHORING_LIGHTING_UNDER_SPECIFIED'} <= {e['code'] for e in caught.exception.errors})
        self.assertEqual(before, self.fx.snapshot())

    def test_cli_returns_structured_diagnostics(self):
        self.broken_references_and_lighting()
        output = io.StringIO()
        with patch('sys.argv', ['storyboard_text_delivery.py', 'check', '--project',
                               str(self.fx.project), '--draft', str(self.fx.draft)]), redirect_stdout(output):
            self.assertEqual(delivery.main(), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report['status'], 'error')
        self.assertGreater(len(report['errors']), 2)

    def test_invalid_syntax_stops_dependent_diagnostics(self):
        self.fx.draft.write_text('malformed authored fixture', encoding='utf-8')
        before = self.fx.snapshot()
        with patch.object(gate, 'body_errors', wraps=gate.body_errors) as body:
            with self.assertRaises(ValueError):
                delivery.preview(self.fx.project, self.fx.draft)
        body.assert_not_called()
        self.assertEqual(before, self.fx.snapshot())

    def test_replacement_parser_errors_include_real_successor_camera_boundary(self):
        state = delivery.d.load(self.fx.project)
        text = self.fx.draft.read_text(encoding='utf-8')
        text = re.sub(r'(；转场：硬切）)[^；]+；', r'\1摄影机固定在桌侧观察；', text)
        old, successor = core.parse(text, state)[:2]
        old.update(id='fictional-old', predecessor=None, batch_id='fictional-batch')
        successor.update(id='fictional-successor', predecessor=old['id'], batch_id='fictional-batch')
        state['units'] = [old, successor]
        self.fx.draft.write_text(old['source_text'].replace('@{CHAR0001}把', '陈默把'), encoding='utf-8')
        before = self.fx.snapshot()
        for check_camera in (True, False):
            with self.subTest(check_camera=check_camera), patch.object(delivery.d, 'load', return_value=state):
                with self.assertRaises(core.TextValidationError) as caught:
                    delivery.prepare(self.fx.project, self.fx.draft,
                                     replace_ids=[old['id']], check_camera=check_camera)
                errors = caught.exception.errors
                self.assertIn('BARE_CHARACTER_NAME', {row['code'] for row in errors})
                boundaries = [row for row in errors if row['code'] == 'CAMERA_CONSTRAINT']
                self.assertEqual(len(boundaries), int(check_camera))
                if check_camera:
                    self.assertEqual(boundaries[0]['unit'], successor['id'])
                    self.assertEqual(boundaries[0]['previous_unit'], old['draft_key'])
        self.assertEqual(before, self.fx.snapshot())

    def test_statistical_warning_reaches_preview_and_still_requires_review(self):
        blocks = self.fx.draft.read_text(encoding='utf-8').strip().split('\n\n')
        first = next(line for line in blocks[0].splitlines() if line.startswith('·'))
        second = next(line for line in blocks[1].splitlines() if line.startswith('·'))
        blocks[1] = blocks[1].replace(second, first)
        self.fx.draft.write_text('\n\n'.join(blocks) + '\n', encoding='utf-8')
        trace = self.fx.project / 'warning-trace.json'
        delivery.bind_trace(self.fx.project, self.fx.draft,
                            self.fx.project / 'authored-decisions.json', trace)
        result = delivery.preview(self.fx.project, self.fx.draft, authoring_trace=trace)
        self.assertEqual(result['authoring_gate']['status'], 'passed')
        self.assertEqual(result['semantic_review'], 'not_performed')
        self.assertIn('AUTHORING_TEMPLATE_REUSE', {w.get('code') for w in result['warnings']})
        with self.assertRaises(ValueError):
            delivery.verify_review({}, result)

    def test_gate_camera_and_speech_errors_are_returned_together(self):
        text = self.fx.draft.read_text(encoding='utf-8')
        text = re.sub(r'(；转场：硬切）)[^；]+；', r'\1摄影机固定在桌侧观察；', text)
        text = re.sub(r'主光：[^；\r\n]+；', '', text)
        text = re.sub(r'声音\[([^,\]]+),[^\]]+\]', r'声音[\1,0.2-0.3]', text)
        blocks = text.strip().split('\n\n')
        shot = next(line for line in blocks[1].splitlines() if line.startswith('·'))
        blocks[1] = blocks[1].replace(shot, shot.replace('00秒-04秒', '00秒-01秒'))
        blocks[1] += '\n' + shot.replace('00秒-04秒', '01秒-04秒').replace('[镜头1-', '[镜头2-')
        text = '\n\n'.join(blocks) + '\n'
        self.fx.draft.write_text(text, encoding='utf-8')
        decisions_path = self.fx.project / 'authored-decisions.json'
        decisions = json.loads(decisions_path.read_text('utf-8'))
        mapping = dict(decisions['units'][1]['shots'][0])
        mapping['shot_key'] = decisions['units'][1]['draft_key'] + '#2'
        decisions['units'][1]['shots'].append(mapping)
        decisions_path.write_text(json.dumps(decisions, ensure_ascii=False), encoding='utf-8')
        trace = self.fx.project / 'diagnostic-trace.json'
        delivery.bind_trace(self.fx.project, self.fx.draft,
                            self.fx.project / 'authored-decisions.json', trace)
        before = self.fx.snapshot()
        with self.assertRaises(gate.AuthoringGateError) as caught:
            delivery.preview(self.fx.project, self.fx.draft, authoring_trace=trace)
        codes = {e['code'] for e in caught.exception.errors}
        self.assertTrue({'AUTHORING_LIGHTING_UNDER_SPECIFIED', 'CAMERA_CONSTRAINT',
                         'DIALOGUE_CAPACITY'} <= codes, codes)
        self.assertEqual(before, self.fx.snapshot())


if __name__ == '__main__':
    unittest.main()
