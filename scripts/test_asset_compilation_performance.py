"""Operation-local reuse keeps public validation and all quality gates intact."""
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_asset_visual_design import fixture, save, ledger, design
import design_inputs


class AssetCompilationPerformance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)
        self.style = self.work / 'video-style.txt'

    def cli(self):
        arguments = ['asset_ledger.py', 'design', str(self.script), str(self.source),
                     str(self.work / 'ledger.json'), str(self.work / 'design.json'),
                     '--style-file', str(self.style), '--output', str(self.work / 'final.json'),
                     '--catalog-output', str(self.work / 'catalog.txt')]
        with patch.object(sys, 'argv', arguments), contextlib.redirect_stdout(io.StringIO()):
            ledger.main()

    def test_normal_design_catalog_compiles_each_asset_once(self):
        with patch.object(design, 'compile_prompt', wraps=design.compile_prompt) as compile_call:
            self.cli()
        self.assertEqual(compile_call.call_count, 3)
        result = json.loads((self.work / 'final.json').read_text(encoding='utf-8'))
        expected = design.apply_design(self.data, self.payload, self.style.read_text().strip(), paragraphs=self.paragraphs)
        self.assertEqual(result, expected)
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.render(self.script, self.source, self.work / 'final.json', self.work / 'independent.txt')
        self.assertEqual((self.work / 'catalog.txt').read_bytes(), (self.work / 'independent.txt').read_bytes())

    def test_public_validate_recompiles_on_every_call_and_rejects_tampering(self):
        result = design.apply_design(self.data, self.payload, self.style.read_text().strip(), paragraphs=self.paragraphs)
        script = ledger.parse_script(self.script)
        with patch.object(design, 'compile_prompt', wraps=design.compile_prompt) as compile_call:
            ledger.validate(script, self.paragraphs, result, require_designed=True)
            ledger.validate(script, self.paragraphs, result, require_designed=True)
        self.assertEqual(compile_call.call_count, 6)
        result['characters'][0]['image_prompt'] += '伪造'
        with self.assertRaisesRegex(ValueError, '提示词或版式'):
            ledger.validate(script, self.paragraphs, result, require_designed=True)

    def test_internal_evidence_is_bound_to_entire_result_and_not_a_flag(self):
        result, proof = design._apply_design_compiled(self.data, self.payload, self.style.read_text().strip(), paragraphs=self.paragraphs)
        script = ledger.parse_script(self.script)
        for field in ('image_prompt', 'image_format', 'visual_design'):
            bad = copy.deepcopy(result)
            bad['characters'][0][field] = '伪造'
            with self.subTest(field=field), self.assertRaises(ValueError):
                ledger._validate_compiled(script, self.paragraphs, bad, proof, require_designed=True)
        with self.assertRaisesRegex(ValueError, '实际编译'):
            ledger._validate_compiled(script, self.paragraphs, result, True, require_designed=True)

    def test_input_drift_during_compilation_writes_no_final_outputs(self):
        original = design._apply_design_compiled
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.source.write_bytes(self.source.read_bytes() + b' ')
            return result
        with patch.object(design, '_apply_design_compiled', side_effect=changed), self.assertRaises(SystemExit):
            self.cli()
        self.assertFalse((self.work / 'final.json').exists())
        self.assertFalse((self.work / 'catalog.txt').exists())

    def test_invalid_source_still_rejected_before_any_output(self):
        self.data['source_digest'] = '0' * 64
        save(self.work / 'ledger.json', self.data)
        with self.assertRaises(SystemExit):
            self.cli()
        self.assertFalse((self.work / 'final.json').exists())

    def test_source_drift_after_json_write_blocks_catalog(self):
        original = Path.write_text
        def changed(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == self.work / 'final.json':
                source = json.loads(self.source.read_text(encoding='utf-8'))
                source['paragraphs'][0]['text'] += '新增'
                original(self.source, json.dumps(source, ensure_ascii=False), encoding='utf-8')
            return result
        with patch.object(Path, 'write_text', changed), self.assertRaises(SystemExit):
            self.cli()
        self.assertTrue((self.work / 'final.json').exists())
        self.assertFalse((self.work / 'catalog.txt').exists())

    def test_check_rebuilds_index_once_and_hashes_rules_twice(self):
        index = design_inputs.prepare(self.script, self.source, self.work / 'ledger.json', self.style)
        with patch.object(design_inputs, 'rule_hashes', wraps=design_inputs.rule_hashes) as rules, \
             patch.object(ledger, 'validate', wraps=ledger.validate) as validations:
            self.assertEqual(design_inputs.check(index)['status'], 'current')
        self.assertEqual(rules.call_count, 2)
        self.assertEqual(validations.call_count, 1)

    def test_index_checks_actual_snapshot_not_caller_hashes(self):
        index = design_inputs.prepare(self.script, self.source, self.work / 'ledger.json', self.style)
        original = design_inputs.project_inputs
        def changed(*args, **kwargs):
            projected = original(*args, **kwargs)
            self.style.write_bytes(self.style.read_bytes() + b' ')
            return projected
        with patch.object(design_inputs, 'project_inputs', side_effect=changed), self.assertRaisesRegex(ValueError, '期间输入或规则变化'):
            design_inputs.check(index)


if __name__ == '__main__':
    unittest.main()
