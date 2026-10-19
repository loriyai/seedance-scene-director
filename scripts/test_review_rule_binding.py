"""Rule binding across fresh/reused reviews; fictional evidence only."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import director as d
import creative_rules
import review_gate as gate
import scene_review as sr
import test_audit_director as director_fixture
import test_dependency_v2 as dependency_fixture
import test_scene_review as fixture


def complete_pending(report):
    """Controlled fictional approval, never used by production code."""
    report.update(status='passed', reviewer='fictional rule-binding reviewer')
    for item in report['checks'].values():
        item.update(status='passed', basis='虚构停车场景的车头、路缘和停止线已经逐项核对。')
    for item in report['shot_reviews']:
        item.update(status='passed', basis='虚构车头与停止线同框，路缘关系支持本镜停车状态。')
    for item in report['warning_decisions']:
        item.update(decision='accepted', basis='此警告对应虚构停车场景的预留停顿。')
    return report


class ReviewRuleBindingTests(unittest.TestCase):
    def setUp(self):
        self.state, self.request = fixture.fixture()
        self.rule_patch = patch.object(sr, 'rules_digest', return_value='fictional-rules-A')
        self.rules = self.rule_patch.start()
        self.addCleanup(self.rule_patch.stop)

    def test_fresh_report_binds_rules_and_unchanged_rules_verify(self):
        report = fixture.reviewed(self.state, self.request)
        self.assertEqual(report['policy'], gate.POLICY)
        self.assertEqual(report['rules_sha256'], 'fictional-rules-A')
        result = gate.verify(self.state, self.request, report, [])
        self.assertEqual(result['rules_sha256'], report['rules_sha256'])
        self.assertEqual(result['status'], 'passed')

    def test_fresh_report_rejects_changed_or_missing_rules(self):
        report = fixture.reviewed(self.state, self.request)
        self.rules.return_value = 'fictional-rules-B'
        with self.assertRaisesRegex(ValueError, '创作规则'):
            gate.verify(self.state, self.request, report, [])
        self.rules.return_value = 'fictional-rules-A'
        report.pop('rules_sha256')
        with self.assertRaisesRegex(ValueError, '规则绑定'):
            gate.verify(self.state, self.request, report, [])

    def test_incremental_rules_binding_matches_fresh_and_rejects_later_changes(self):
        state, request = dependency_fixture.fixture()
        snapshot = sr.freeze(state, request, validation_passed=True)
        previous = fixture.reviewed(state, request)
        report, proof = sr.prepare_incremental(
            snapshot, previous, state, request, validation_passed=True,
            expected_report_sha256=sr.digest(previous))
        self.assertGreater(proof['stats']['reused_shots'], 0)
        self.assertIn('reuse_evidence', report)
        self.assertEqual(report['rules_sha256'], gate.pending(state, request, [])['rules_sha256'])
        complete_pending(report)
        # Reused judgments retain the original reviewer's basis, not our new
        # fixture approval. Final checks are the newly performed judgments.
        old = {item['key']: item for item in previous['shot_reviews']}
        for item in report['shot_reviews']:
            if item['key'] in report['reuse_evidence']['reused_keys']:
                item['basis'] = old[item['key']]['basis']
        self.assertEqual(gate.verify(state, request, report, [])['status'], 'passed')
        self.rules.return_value = 'fictional-rules-B'
        with self.assertRaisesRegex(ValueError, '创作规则'):
            gate.verify(state, request, report, [])

    def test_rule_changes_make_incremental_judgments_pending(self):
        snapshot = sr.freeze(self.state, self.request)
        previous = fixture.reviewed(self.state, self.request)
        self.rules.return_value = 'fictional-rules-B'
        report, proof = sr.prepare_incremental(snapshot, previous, self.state, self.request)
        self.assertEqual(report['rules_sha256'], 'fictional-rules-B')
        self.assertEqual(proof['stats']['reused_shots'], 0)
        self.assertTrue(all(item['status'] == 'pending' for item in report['shot_reviews']))

    def test_legacy_report_can_be_read_but_cannot_submit_or_reuse(self):
        warnings = [{'kind': 'fictional warning'}]
        snapshot = sr.freeze(self.state, self.request, warnings)
        previous = fixture.reviewed(self.state, self.request, warnings)
        previous['policy'] = gate.LEGACY_POLICY
        previous.pop('rules_sha256')
        before = copy.deepcopy((snapshot, previous))
        with self.assertRaisesRegex(ValueError, '旧版.*重新准备'):
            gate.verify(self.state, self.request, previous, warnings)
        report, proof = sr.prepare_incremental(snapshot, previous, self.state, self.request, warnings)
        self.assertEqual(proof['stats']['reused_shots'], 0)
        self.assertEqual(proof['stats']['reused_warnings'], 0)
        self.assertEqual(proof['stats']['reasons']['old_review_rules_unbound'], len(snapshot['targets']))
        self.assertEqual(report['warning_decisions'][0]['decision'], 'pending')
        self.assertEqual((snapshot, previous), before)

    def test_old_v3_report_must_match_its_own_snapshot_rules(self):
        snapshot = sr.freeze(self.state, self.request)
        previous = fixture.reviewed(self.state, self.request)
        previous['rules_sha256'] = 'unrelated-rules'
        with self.assertRaisesRegex(ValueError, '旧审阅规则绑定.*不匹配'):
            sr.prepare_incremental(snapshot, previous, self.state, self.request)

    def test_rule_change_during_incremental_preparation_is_rejected(self):
        snapshot = sr.freeze(self.state, self.request)
        previous = fixture.reviewed(self.state, self.request)
        self.rules.side_effect = ['fictional-rules-A', 'fictional-rules-B']
        with self.assertRaisesRegex(ValueError, '准备期间创作规则发生变化'):
            sr.prepare_incremental(snapshot, previous, self.state, self.request)

    def test_actual_rule_file_mutation_invalidates_fresh_report(self):
        self.rule_patch.stop()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            rule = root / 'rule.md'
            rule.write_text('original fictional rule', encoding='utf-8')
            with patch.object(sr, '__file__', str(root / 'scripts' / 'scene_review.py')), \
                    patch.object(creative_rules, 'REQUIRED_FILES', ('rule.md',)):
                snapshot = sr.freeze(self.state, self.request)
                report = fixture.reviewed(self.state, self.request)
                self.assertEqual(gate.verify(self.state, self.request, report, [])['status'], 'passed')
                rule.write_text('revised fictional rule', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, '创作规则已变化'):
                    gate.verify(self.state, self.request, report, [])
                # Historical evidence stays readable with its recorded digest.
                self.assertEqual(sr.verify_snapshot(snapshot), snapshot)

    def test_delivered_legacy_receipt_replays_without_current_rule_approval(self):
        harness = director_fixture.AuditDirectorChecks()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        output = harness.project / 'outputs' / 'delivered.txt'
        request = harness.request()
        harness.commit(request, output)
        receipt = harness.state['receipts'][0]
        receipt['final_review']['policy'] = gate.LEGACY_POLICY
        receipt['final_review'].pop('rules_sha256')
        original_bytes, original_receipt = output.read_bytes(), copy.deepcopy(receipt)
        with patch.object(sr, 'rules_digest', side_effect=AssertionError('replay must not approve anew')):
            result = harness.commit(request, output)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(output.read_bytes(), original_bytes)
        self.assertEqual(receipt, original_receipt)


if __name__ == '__main__':
    unittest.main()
