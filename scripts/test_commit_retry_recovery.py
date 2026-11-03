"""Exact receipt recovery on synthetic v3/v4 and legacy local fixtures."""
import copy
import hashlib
import unittest
from unittest.mock import patch

import director as d
import storyboard_authoring_gate as gate
import storyboard_text as core
import storyboard_text_delivery as delivery
import test_scene_text_performance as fixtures
import test_storyboard_authoring_gate as legacy_gate_fixtures
import test_continuity_props as carry_fixtures
import test_storyboard_text_delivery as delivery_fixtures


class CommitRetryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SceneTextPerformanceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fx = self.fixture.fx
        self.original = self.fx.draft.read_text('utf-8')
        self.decisions = d.read_json(self.fx.project / 'authored-decisions.json')

    def select(self, name, indexes):
        blocks = self.original.strip().split('\n\n')
        draft = self.fx.project / (name + '.txt')
        draft.write_text('\n\n'.join(blocks[i] for i in indexes) + '\n', encoding='utf-8')
        decisions = copy.deepcopy(self.decisions)
        decisions['units'] = [decisions['units'][i] for i in indexes]
        decisions['unit_organization']['units'] = [decisions['unit_organization']['units'][i] for i in indexes]
        path = self.fx.project / (name + '-decisions.json')
        d.write_json(path, decisions)
        units = core.parse(draft.read_text('utf-8'), d.load(self.fx.project))
        span = [units[0]['span'][0], units[-1]['span'][1]]
        delivery.bind_trace(self.fx.project, draft, path, span=span)
        return draft, span

    def commit(self, request, draft=None, span=None):
        draft = draft or self.fx.draft
        _, _, info = delivery.prepare(self.fx.project, draft, span)
        authoring = info['authoring_gate']
        review = {k: info[k] for k in ('draft_sha256', 'context_sha256')}
        review.update(reviewer='synthetic-only', semantic_review='performed',
                      authoring_trace_sha256=authoring['trace_sha256'],
                      authoring_gate_digest=authoring['gate_digest'],
                      shot_count=authoring['shot_count'], reviewed_shots=authoring['shot_keys'],
                      scenes=[{'scene_id': sid, 'status': 'passed', 'issues': [],
                               'basis': 'Synthetic binding test only.'} for sid in info['scenes']])
        review_path = self.fx.project / (request + '-review.json')
        output = self.fx.project / (request + '-output.txt')
        d.write_json(review_path, review)
        delivery.commit(self.fx.project, draft, request, review_path, output, span)
        return draft, request, review_path, output, span

    def retry(self, args):
        draft, request, review, output, span = args
        return delivery.commit(self.fx.project, draft, request, review, output, span)

    def test_v4_retry_uses_saved_review_without_prepare_or_new_events(self):
        args = self.commit('v4')
        args[2].unlink()
        before = self.fx.snapshot()
        with patch.object(delivery, 'prepare', side_effect=AssertionError('must not rebind accepted history')):
            self.assertEqual(self.retry(args)['status'], 'already_committed')
        self.assertEqual(self.fx.snapshot(), before)

    def test_later_scene_commit_does_not_require_rebinding_earlier_receipt(self):
        first = self.select('first-scene', [0, 1])
        args = self.commit('first', *first)
        second = self.select('second-scene', [2, 3])
        self.commit('second', *second)
        before = self.fx.snapshot()
        self.assertEqual(self.retry(args)['status'], 'already_committed')
        self.assertEqual(self.fx.snapshot(), before)
        self.assertEqual(len(d.load(self.fx.project)['receipts']), 2)

    def test_missing_output_restores_exact_bytes_without_state_changes(self):
        args = self.commit('restore')
        expected = args[3].read_bytes()
        args[3].unlink()
        before = self.fx.snapshot()
        self.assertEqual(self.retry(args)['status'], 'already_committed')
        self.assertEqual(args[3].read_bytes(), expected)
        after = self.fx.snapshot()
        for name, value in before.items():
            self.assertEqual(after[name], value, name)

    def test_draft_trace_scope_and_output_changes_are_rejected_without_writes(self):
        args = self.commit('identity')
        draft, request, review, output, span = args
        original = draft.read_bytes()
        trace = gate.trace_path(draft)
        trace_original = trace.read_bytes()
        for target, value in ((draft, original + b'\n'), (trace, trace_original + b'\n')):
            with self.subTest(target=target.name):
                target.write_bytes(value)
                before = self.fx.snapshot()
                with self.assertRaisesRegex(ValueError, '其他内容'):
                    self.retry(args)
                self.assertEqual(self.fx.snapshot(), before)
                target.write_bytes(original if target == draft else trace_original)
        before = self.fx.snapshot()
        with self.assertRaisesRegex(ValueError, '原交付路径'):
            self.retry((draft, request, review, self.fx.project / 'other.txt', span))
        with self.assertRaisesRegex(ValueError, '来源范围'):
            self.retry((draft, request, review, output, [0, 1]))
        self.assertEqual(self.fx.snapshot(), before)

    def test_altered_history_and_external_output_are_rejected(self):
        args = self.commit('tamper')
        session_path = self.fx.project / 'session.json'
        original = session_path.read_bytes()
        for change in ('shot', 'review', 'style'):
            with self.subTest(change=change):
                state = d.load(self.fx.project)
                receipt = state['receipts'][0]
                if change == 'shot':
                    receipt['units'][0]['shots'][0]['label'] = 'changed'
                elif change == 'review':
                    receipt['final_review']['scenes'][0]['status'] = 'needs_rewrite'
                else:
                    receipt['units'][0]['style'] = 'changed style'
                d.write_json(session_path, state)
                before = self.fx.snapshot()
                with self.assertRaises(ValueError):
                    self.retry(args)
                self.assertEqual(self.fx.snapshot(), before)
                session_path.write_bytes(original)
        args[3].write_text('externally edited', encoding='utf-8')
        before = self.fx.snapshot()
        with self.assertRaisesRegex(ValueError, '外部修改'):
            self.retry(args)
        self.assertEqual(self.fx.snapshot(), before)

    def test_original_receipt_without_new_output_digest_remains_recoverable(self):
        args = self.commit('old-v4')
        state = d.load(self.fx.project)
        state['receipts'][0].pop('output_sha256')
        d.write_json(self.fx.project / 'session.json', state)
        before = self.fx.snapshot()
        self.assertEqual(self.retry(args)['status'], 'already_committed')
        self.assertEqual(self.fx.snapshot(), before)

    def test_v3_receipt_retry_preserves_its_original_gate_payload(self):
        draft, span = self.select('v3-scene', [0])
        native = legacy_gate_fixtures.StoryboardAuthoringGateTest('runTest')
        native.setUp()
        self.addCleanup(native.tearDown)
        trace = d.read_json(native.write_trace())
        trace.update(draft_sha256=hashlib.sha256(draft.read_bytes()).hexdigest(),
                     context_sha256=delivery.context_hash(d.load(self.fx.project), self.fx.project),
                     source_span=span)
        trace['units'][0]['shots'][0]['source_records'] = ['L000003']
        d.write_json(gate.trace_path(draft), trace)
        args = self.commit('v3', draft, span)
        self.assertEqual(d.load(self.fx.project)['receipts'][0]['authoring_gate']['version'], 3)
        before = self.fx.snapshot()
        self.assertEqual(self.retry(args)['status'], 'already_committed')
        self.assertEqual(self.fx.snapshot(), before)

    def test_uncommitted_draft_still_requires_complete_current_gate(self):
        self.fx.draft.write_text(self.original + '\n', encoding='utf-8')
        before = self.fx.snapshot()
        with self.assertRaises(gate.AuthoringGateError):
            delivery.commit(self.fx.project, self.fx.draft, 'not-committed',
                            self.fx.review, self.fx.output)
        self.assertEqual(self.fx.snapshot(), before)

    def test_committed_carry_receipt_survives_prior_anchor_version_change(self):
        case = carry_fixtures.CarryReferenceTests('runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        state = d.load(case.project)
        previous = core.parse(case.fx.fixture.authored_text(), state)[0]
        previous.update(id='prior-unit', version=1)
        state['units'] = [previous]
        d.write_json(case.project / 'session.json', state)
        case.row['anchor'] = {'unit_id': 'prior-unit', 'version': 1,
                              'evidence': '@{CHAR0001}把@{PROP0001}放在桌上。'}
        case.bind()
        case.commit_fixture()
        state = d.load(case.project)
        state['units'][0]['version'] = 2
        d.write_json(case.project / 'session.json', state)
        before = case.fx.fixture.snapshot()
        result = delivery.commit(case.project, case.draft, 'carry-test',
            case.project / 'review-fixture.json', case.project / 'delivered.txt',
            case.span, authoring_trace=case.trace)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(case.fx.fixture.snapshot(), before)
        with self.assertRaisesRegex(ValueError, '前序单元'):
            case.prepare()

    def test_legacy_receipt_without_custom_header_or_authoring_gate_recovers(self):
        # Construct the older persisted shape from a synthetic accepted receipt.
        # This is compatibility data, not a source/project migration operation.
        case = delivery_fixtures.TextDeliveryIntegration('runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.commit_fixture()
        state = d.load(case.project)
        receipt = state['receipts'][0]
        state.pop('header_hash')
        state.pop('header_policy')
        for unit in state['units'] + receipt['units']:
            unit.pop('header_text')
            unit['header_policy'] = 'visual-only-v2'
        receipt.pop('output_sha256')
        request = {'policy': delivery.POLICY, 'draft_sha256': d.file_hash(case.draft),
                   'mode': 'append', 'replace_ids': [],
                   'span': [receipt['units'][0]['span'][0], receipt['units'][-1]['span'][1]],
                   'header_sha256': None, 'video_ratios': receipt['video_ratios'],
                   'ratio_profiles': receipt['ratio_profiles']}
        receipt['request_hash'] = delivery.digest(request)
        d.write_json(case.project / 'session.json', state)
        (case.project / 'video-header.txt').unlink()
        case.output.unlink()
        expected = delivery.render_units(d.load(case.project), receipt['units'], historical_receipt=receipt)
        result = delivery.commit(case.project, case.draft, 'fictional-first', case.review, case.output)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(case.output.read_text('utf-8'), expected)


if __name__ == '__main__':
    unittest.main()
