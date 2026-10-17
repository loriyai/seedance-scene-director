"""Current authoring headers and byte-stable legacy render selection."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import authoring_gates as ag
import director as d
import test_audit_director as fixtures


class HeaderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.harness = fixtures.AuditDirectorChecks()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.state = self.harness.state
        self.unit = self.harness.unit([0, 9])
        self.unit.update(id='fictional-header-unit', style='3D国漫', black_reference_mode='none-v1')

    def test_legacy_unit_keeps_original_header_and_visual_version_changes_only_header(self):
        original = copy.deepcopy(self.unit)
        legacy = d.render_unit(self.state, self.unit)
        current = d.render_unit(self.state, dict(self.unit, header_policy=d.HEADER_POLICY))
        old_lines, new_lines = d.fixed_header(), d.fixed_header(d.HEADER_POLICY)
        self.assertEqual(d.sha('\n'.join(old_lines)), d.HEADER_SHA)
        self.assertEqual(d.sha('\n'.join(new_lines)), d.VISUAL_HEADER_SHA)
        self.assertEqual(old_lines[1], new_lines[1])
        self.assertEqual(current, legacy.replace(old_lines[0], new_lines[0], 1))
        self.assertEqual(self.unit, original)
        self.assertIn(self.unit['style'] + ';' + new_lines[0], current)
        self.assertIn('composed and graceful movements', legacy)
        for phrase in ('composed and graceful movements', 'natural blinking',
                       'Subtle micro-expressions', 'breathing chest movement',
                       'clothing fluttering gently in the wind'):
            self.assertNotIn(phrase, current)

    def test_authoring_preview_uses_current_header_without_mutating_draft(self):
        original = copy.deepcopy(self.unit)
        actual = ag.staging_material(self.state, self.unit)
        self.assertIn(d.fixed_header(d.HEADER_POLICY)[0], actual['rendered'])
        self.assertEqual(self.unit, original)
        pending = ag.staging_pending(self.state, self.unit)
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(pending['input_sha256'], ag.digest(actual))

    def test_unknown_header_policy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '抬头版本'):
            d.render_unit(self.state, dict(self.unit, header_policy='future-unknown'))

    def test_both_assets_are_independently_hash_checked(self):
        folder = self.harness.project / 'header-assets'
        (folder / 'assets').mkdir(parents=True)
        for policy, filename in ((None, 'fixed-header.txt'),
                                 (d.HEADER_POLICY, 'fixed-header-visual-v2.txt')):
            path = folder / 'assets' / filename
            path.write_text('\n'.join(d.fixed_header(policy)), encoding='utf-8')
            with patch.object(d, 'ROOT', folder):
                self.assertEqual(len(d.fixed_header(policy)), 2)
                path.write_text(path.read_text(encoding='utf-8') + ' altered', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, '对应版本常量'):
                    d.fixed_header(policy)

    def test_new_commit_persists_header_choice_and_idempotent_export_retains_it(self):
        request = self.harness.request()
        output = self.harness.project / 'current-header.txt'
        self.harness.commit(request, output)
        state = d.load(self.harness.project)
        self.assertTrue(all(u['header_policy'] == d.HEADER_POLICY for u in state['units']))
        self.assertTrue(all(u['header_policy'] == d.HEADER_POLICY for u in state['receipts'][0]['units']))
        before = output.read_bytes()
        d.render_receipt(state, state['receipts'][0], self.harness.project)
        self.assertEqual(output.read_bytes(), before)
        self.assertIn(d.fixed_header(d.HEADER_POLICY)[0].encode('utf-8'), before)


if __name__ == '__main__':
    unittest.main()
