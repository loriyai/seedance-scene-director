"""B workflow integration boundaries, using fictional sources only."""
import copy
import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import director as d
import review_gate
import test_audit_director as fixtures


class WorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AuditDirectorChecks()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = self.fixture.state
        self.request = self.fixture.request()

    def test_explicit_new_workflow_rejects_unmarked_request(self):
        with self.assertRaisesRegex(ValueError, 'workflow_policy'):
            d.require_workflow(self.request)
        for unit in self.request['units']:
            unit['workflow_policy'] = 'scene-review-b-v2'
        d.require_workflow(self.request)

    def test_main_validator_consumes_preflight_warnings(self):
        marker = {'kind': 'controlled identity candidate', 'unit': 'fixture'}
        with patch('storyboard_preflight.validate', return_value=[marker]) as check:
            warnings = d.validate(self.state, self.request)
        self.assertIn(marker, warnings)
        check.assert_called_once()

    def test_preflight_error_blocks_full_validation(self):
        with patch('storyboard_preflight.validate', side_effect=ValueError('controlled pause mismatch')):
            with self.assertRaisesRegex(ValueError, 'pause mismatch'):
                d.validate(self.state, self.request)

    def test_new_commit_requires_marker_before_allocating_output(self):
        path = self.fixture.project / 'new-workflow.json'
        d.write_json(path, self.request)
        output = self.fixture.project / 'not-created.txt'
        args = SimpleNamespace(project=self.fixture.project, request=path,
                               output=output, require_workflow=True)
        with self.assertRaisesRegex(ValueError, 'workflow_policy'):
            d.commit(args, self.state)
        self.assertFalse(output.exists())

    def test_warning_body_cannot_be_changed_under_valid_digest(self):
        warnings = [{'kind': 'fixture warning', 'record': 'L1', 'rate': 5.3}]
        report = review_gate.pending(self.state, self.request, warnings)
        report.update(status='passed', reviewer='fictional regression reviewer')
        for item in report['checks'].values():
            item.update(status='passed', basis='Fictional controlled source and boundary checked.')
        for item in report['shot_reviews']:
            item.update(status='passed', basis='Fictional subject and source-specific framing checked.')
        for item in report['warning_decisions']:
            item.update(decision='accepted', basis='Controlled warning examined in this fixture.')
        review_gate.verify(self.state, self.request, report, warnings)
        tampered = copy.deepcopy(report)
        tampered['warning_decisions'][0]['warning']['rate'] = 1.0
        with self.assertRaisesRegex(ValueError, '警告原对象'):
            review_gate.verify(self.state, self.request, tampered, warnings)

    def test_partial_review_can_be_prepared_but_not_verified(self):
        request_path = self.fixture.project / 'draft.json'
        report_path = self.fixture.project / 'partial-review.json'
        d.write_json(request_path, self.request)
        args = ['review_gate.py', 'prepare', '--project', str(self.fixture.project),
                '--request', str(request_path), '--report', str(report_path), '--partial']
        with patch('sys.argv', args), patch.object(d, 'validate', return_value=[]) as check:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(review_gate.main(), 0)
            self.assertTrue(check.call_args.kwargs['partial'])
        args[1] = 'verify'
        with patch('sys.argv', args), patch.object(d, 'validate') as check:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(review_gate.main(), 1)
            check.assert_not_called()


if __name__ == '__main__':
    unittest.main()
