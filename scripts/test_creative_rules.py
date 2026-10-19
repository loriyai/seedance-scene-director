"""Real isolated rule mutations distinguish operations from creative changes."""
import tempfile
import unittest
import os
from pathlib import Path

import creative_rules as rules


DIRECTOR = '''import json
VALUE = 1
def save(project, state):
    state['revision'] += 1
    write_json(project, state)
def sync_canvas_replacements(project, receipt):
    return {'status': 'not_bound'}
def set_style(args, state=None):
    style = args.file.read_text(encoding='utf-8').strip()
    validate_style(style)
    if state is not None:
        state['style_hash'] = sha(style)
        if state.get('phase') == 'style':
            state['phase'] = ('complete' if state.get('delivery_contract', {}).get('scope')
                              in {'assets', 'screenplay'} else 'range')
        save(args.project, state)
    return style
def validate_unit(unit):
    return unit['duration'] <= 30
def render_unit(unit):
    return unit['text']
'''


class CreativeRuleDomainsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # Explicit fixture inventory: no discovery of tests or fixtures can
        # accidentally become the rules under test.
        self.put('scripts/director.py', DIRECTOR)
        self.put('scripts/creative_rules.py', Path(rules.__file__).read_text(encoding='utf-8'))
        self.put('references/storyboard-contract.md', '# Contract\nKeep source wording.\n')
        self.put('references/runtime.md', '# Runtime\n'
                 '<!-- rule-domain:operations:start dependencies -->\n'
                 'Locate the Python interpreter.\n'
                 '<!-- rule-domain:operations:end dependencies -->\n')
        self.put('SKILL.md', '# Entry\n[Contract](references/storyboard-contract.md)\n')

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def digest(self):
        return rules.rules_digest(self.root, required_files=())

    def test_creative_reference_and_actual_validator_changes_invalidate(self):
        before = self.digest()
        self.put('references/storyboard-contract.md', '# Contract\nKeep all source words.\n')
        changed = self.digest()
        self.assertNotEqual(before, changed)
        self.put('scripts/director.py', DIRECTOR.replace("<= 30", "<= 25"))
        self.assertNotEqual(changed, self.digest())

    def test_known_save_and_canvas_operation_body_changes_do_not_invalidate(self):
        before = self.digest()
        self.put('scripts/director.py', DIRECTOR.replace("state['revision'] += 1", "state['revision'] += 2")
                 .replace("{'status': 'not_bound'}", "{'status': 'queued'}"))
        self.assertEqual(before, self.digest())

    def test_pure_phase_transition_does_not_invalidate_but_style_and_render_do(self):
        before = self.digest()
        self.put('scripts/director.py', DIRECTOR.replace("else 'range'", "else 'followup'"))
        self.assertEqual(before, self.digest())
        self.put('scripts/director.py', DIRECTOR.replace('.strip()', '.rstrip()'))
        self.assertNotEqual(before, self.digest())
        self.put('scripts/director.py', DIRECTOR.replace("return unit['text']", "return unit['text'] + '!'"))
        self.assertNotEqual(before, self.digest())

    def test_phase_side_effects_and_style_hash_are_not_hidden(self):
        before = self.digest()
        self.put('scripts/director.py', DIRECTOR.replace("else 'range'", "else choose_phase(state)"))
        self.assertNotEqual(before, self.digest())
        self.put('scripts/director.py', DIRECTOR.replace("sha(style)", "sha(style.upper())"))
        self.assertNotEqual(before, self.digest())

    def test_operation_signature_and_unknown_code_remain_bound(self):
        before = self.digest()
        for changed in (
            DIRECTOR.replace('def save(project, state):', 'def save(project, state, strict=True):'),
            DIRECTOR + '\ndef new_validation(value):\n    return value > 0\n',
            DIRECTOR + '\nNEW_RULE = 7\n',
            DIRECTOR.replace('import json', 'import json\nimport math'),
        ):
            with self.subTest(changed=changed[-70:]):
                self.put('scripts/director.py', changed)
                self.assertNotEqual(before, self.digest())

    def test_new_helpers_and_unknown_files_are_included_without_registration(self):
        before = self.digest()
        for name in ('scripts/state_effects.py', 'scripts/storyboard_assembly.py',
                     'scripts/draft_revision.py', 'scripts/future_validator.py',
                     'references/future-rule.md', 'assets/future-template.json'):
            with self.subTest(name=name):
                path = self.put(name, 'SOURCE = 1\n')
                added = self.digest()
                self.assertNotEqual(before, added)
                path.write_text('SOURCE = 2\n', encoding='utf-8')
                self.assertNotEqual(added, self.digest())
                path.unlink()
                self.assertEqual(before, self.digest())

    def test_test_code_caches_and_explicit_operation_logs_do_not_leak_into_digest(self):
        before = self.digest()
        for name in ('scripts/test_new_case.py', 'scripts/test_new_cli.js',
                     'scripts/__pycache__/helper.cpython.pyc', 'logs/worker.jsonl',
                     'logs/worker.log', 'logs/metrics.txt'):
            self.put(name, 'first fixture content')
        self.assertEqual(before, self.digest())
        self.put('scripts/test_new_case.py', 'modified fixture content')
        self.put('logs/worker.jsonl', 'another operational event')
        self.assertEqual(before, self.digest())
        self.put('logs/unknown_code.py', 'RULE = 1')
        self.assertNotEqual(before, self.digest())

    def test_named_metrics_operation_changes_do_not_hide_new_functions(self):
        self.put('scripts/pipeline_metrics.py', 'def record_event(event):\n    return str(event)\n')
        before = self.digest()
        self.put('scripts/pipeline_metrics.py', 'def record_event(event):\n    return repr(event)\n')
        self.assertEqual(before, self.digest())
        self.put('scripts/pipeline_metrics.py', 'def record_event(event):\n    return repr(event)\n'
                 'def unknown_rule(value):\n    return value > 2\n')
        self.assertNotEqual(before, self.digest())

    def test_known_operational_paragraph_changes_do_not_hide_outside_rules(self):
        before = self.digest()
        path = self.root / 'references/runtime.md'
        original = path.read_text(encoding='utf-8')
        path.write_text(original.replace('Locate the Python interpreter.', 'Locate the bundled interpreter.'), encoding='utf-8')
        self.assertEqual(before, self.digest())
        path.write_text(original + '\nNew creative requirement.\n', encoding='utf-8')
        self.assertNotEqual(before, self.digest())

    def test_unknown_blocks_and_new_sections_are_conservative(self):
        before = self.digest()
        path = self.root / 'references/runtime.md'
        original = path.read_text(encoding='utf-8')
        path.write_text(original + '<!-- rule-domain:operations:start unknown -->\n'
                        'New requirement.\n<!-- rule-domain:operations:end unknown -->\n', encoding='utf-8')
        unknown = self.digest()
        self.assertNotEqual(before, unknown)
        path.write_text(path.read_text(encoding='utf-8').replace('New requirement.', 'Different requirement.'), encoding='utf-8')
        self.assertNotEqual(unknown, self.digest())
        path.write_text(original.replace('Locate the Python interpreter.', '## New section\nNew rule.'), encoding='utf-8')
        section = self.digest()
        self.assertNotEqual(before, section)
        path.write_text(path.read_text(encoding='utf-8').replace('New rule.', 'Changed rule.'), encoding='utf-8')
        self.assertNotEqual(section, self.digest())

    def test_malformed_or_nested_operation_blocks_fail_closed(self):
        for text in (
            '<!-- rule-domain:operations:start dependencies -->\nunclosed',
            '<!-- rule-domain:operations:end dependencies -->',
            '<!-- rule-domain:operations:start dependencies -->\n'
            '<!-- rule-domain:operations:start recovery -->\n',
            '<!-- rule-domain:operations:start dependencies -->\n'
            '<!-- rule-domain:operations:start unknown -->\n',
        ):
            with self.subTest(text=text):
                self.put('references/runtime.md', text)
                with self.assertRaises(ValueError):
                    self.digest()

    def test_classifier_itself_and_required_files_cannot_be_bypassed(self):
        before = self.digest()
        path = self.root / 'scripts/creative_rules.py'
        path.write_text(path.read_text(encoding='utf-8') + '\n# Audited classification change\n', encoding='utf-8')
        self.assertNotEqual(before, self.digest())
        with self.assertRaisesRegex(ValueError, '缺失'):
            rules.rules_digest(self.root, required_files=('references/missing-rule.md',))
        self.put('scripts/director.py', 'def invalid(')
        with self.assertRaises(SyntaxError):
            self.digest()

    def test_canonical_asset_rules_remain_required_and_content_bound(self):
        for relative in rules.REQUIRED_FILES:
            if not (self.root / relative).exists():
                self.put(relative, '# Required fixture entry\n')
        baseline = rules.rules_digest(self.root)
        for relative in (
            'references/asset-workflow.md',
            'modules/screenplay-standardizer/INSTRUCTIONS.md',
            'modules/screenplay-standardizer/references/asset-ledger.md',
            'references/asset-image-design.md',
        ):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'\nA revised source-bound requirement.\n')
                    self.assertNotEqual(baseline, rules.rules_digest(self.root))
                    path.unlink()
                    with self.assertRaisesRegex(ValueError, '缺失'):
                        rules.rules_digest(self.root)
                finally:
                    path.write_bytes(original)
                self.assertEqual(baseline, rules.rules_digest(self.root))

    def test_warm_projection_cache_rereads_same_path_same_mtime_content(self):
        before = self.digest()
        self.assertEqual(before, self.digest())
        path = self.root / 'scripts/director.py'
        stamp = path.stat()
        updated = DIRECTOR.replace("<= 30", "<= 29")
        self.assertEqual(len(updated), len(DIRECTOR))
        path.write_text(updated, encoding='utf-8')
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(path.stat().st_mtime_ns, stamp.st_mtime_ns)
        self.assertEqual(path.stat().st_size, stamp.st_size)
        self.assertNotEqual(before, self.digest())
        path.write_text(DIRECTOR, encoding='utf-8')
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(before, self.digest())


if __name__ == '__main__':
    unittest.main()
