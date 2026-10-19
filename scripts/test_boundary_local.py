"""Local boundary evidence is an ordinary review item, never reset-label trust."""
import copy
import unittest
from unittest.mock import patch

import review_gate as gate
import scene_review as sr
import director as d
import shot_dependencies as sd
import test_audit_director as director_fixture
import test_dependency_v3 as fixture


class LocalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.rules = patch.object(sr, 'rules_digest', return_value='fictional-local-boundary-rules')
        self.rules.start()
        self.addCleanup(self.rules.stop)
        self.state, self.request = fixture.fixture()
        self.request['units'] = self.request['units'][4:]
        self.old = sr.freeze(self.state, self.request, validation_passed=True)
        self.report = fixture.reviewed(self.state, self.request)

    def compare(self, request, report=None):
        previous = report or self.report
        return sr.prepare_incremental(self.old, previous, self.state, request,
            validation_passed=True, expected_report_sha256=sr.digest(previous))

    def changed_previous(self):
        changed = copy.deepcopy(self.request)
        unit = changed['units'][0]
        unit['shots'][-1]['continuity']['changes'] = [
            {'entity': 'car', 'set': {'facts': {'position': 'road'}},
             'evidence': [{'record': 'L5', 'quote': '小车停稳'}]}]
        unit['continuity']['final']['car']['facts']['position'] = 'road'
        return changed

    def test_prepare_is_pending_and_reset_label_cannot_approve_boundary(self):
        report = gate.pending(self.state, self.request, [])
        self.assertEqual(report['boundary_reviews'][0]['status'], 'pending')
        self.assertEqual(report['boundary_reviews'][0]['reset_fields'], [])
        with self.assertRaisesRegex(ValueError, '局部边界尚未'):
            sr.verify_boundary_reviews(self.state, self.request, report['boundary_reviews'])

    def test_unrelated_last_shot_failure_does_not_invalidate_passed_local_boundary(self):
        previous = copy.deepcopy(self.report)
        previous['checks']['continuity'].update(status='pending', basis='后面的动作仍待修')
        previous['shot_reviews'][-1].update(status='pending', basis='末镜问题尚待修')
        report, proof = self.compare(self.changed_previous(), previous)
        self.assertEqual(proof['boundary_provenance'][0]['status'], 'reused')
        statuses = {row['key']: row['status'] for row in report['shot_reviews']}
        self.assertEqual(statuses['2:1'], 'passed')
        self.assertEqual(statuses['2:2'], 'passed')
        self.assertEqual(statuses['2:3'], 'pending')

    def test_changed_boundary_or_first_shot_invalidates_local_judgment(self):
        for field in ('boundary', 'initial', 'first'):
            changed = copy.deepcopy(self.request)
            unit = changed['units'][1]
            if field == 'boundary':
                unit['continuity']['boundary']['reason'] = '新的转场解释'
            elif field == 'initial':
                unit['continuity']['initial']['car']['facts']['position'] = 'another curb'
            else:
                unit['shots'][0]['camera'] = '新的首镜取景'
            with self.subTest(field=field):
                _, proof = self.compare(changed)
                self.assertEqual(proof['boundary_provenance'][0]['status'], 'pending')

    def test_carried_fact_change_invalidates_even_with_unchanged_reset_label(self):
        previous = copy.deepcopy(self.report)
        row = previous['boundary_reviews'][0]
        position = {'entity': 'car', 'path': ['facts', 'position']}
        row['reset_fields'].remove(position)
        row['carried_fields'] = [position]
        row['basis'] = '虚构转场保持车辆在路缘的事实，其他状态按新场重建。'
        sr.verify_boundary_reviews(self.state, self.request, [row])
        report, proof = self.compare(self.changed_previous(), previous)
        self.assertEqual(proof['boundary_provenance'][0]['status'], 'pending')
        self.assertEqual(report['shot_reviews'][3]['status'], 'pending')

    def test_partition_cannot_omit_facts_or_claim_changed_fact_as_carried(self):
        for mutation in ('missing', 'false_carried'):
            report = copy.deepcopy(self.report)
            row = report['boundary_reviews'][0]
            row['reset_fields'].pop()
            if mutation == 'false_carried':
                value = row['fields'][-1]
                row['carried_fields'] = [value]
                # A changed binding is independently rejected before a claim
                # can make mismatched prior and current values appear equal.
                row['binding']['predecessors'][0]['final']['car']['present'] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                sr.verify_boundary_reviews(self.state, self.request, report['boundary_reviews'])

    def test_final_gate_recomputes_current_boundary_and_reused_judgment(self):
        report, proof = self.compare(self.request)
        self.assertEqual(proof['boundary_provenance'][0]['status'], 'reused')
        report.update(status='passed', reviewer='fictional final reviewer')
        report['checks'] = copy.deepcopy(self.report['checks'])
        self.assertEqual(gate.verify(self.state, self.request, report, [])['status'], 'passed')
        report['boundary_reviews'][0]['basis'] = '替换旧局部判断的具体依据'
        with self.assertRaisesRegex(ValueError, '复用局部边界'):
            gate.verify(self.state, self.request, report, [])

    def test_missing_historical_local_evidence_does_not_get_synthesized(self):
        previous = copy.deepcopy(self.report)
        previous.pop('boundary_reviews')
        report, proof = self.compare(self.changed_previous(), previous)
        self.assertEqual(proof['boundary_provenance'][0]['status'], 'pending')
        self.assertEqual(report['boundary_reviews'][0]['status'], 'pending')

    def test_unknown_upstream_conditions_cannot_disappear_at_a_reset(self):
        for location in ('unit', 'shot'):
            changed = copy.deepcopy(self.request)
            owner = changed['units'][0] if location == 'unit' else changed['units'][0]['shots'][-1]
            owner['future_state_condition'] = '尚未归类的持续关系'
            with self.subTest(location=location):
                _, proof = self.compare(changed)
                self.assertEqual(proof['boundary_provenance'][0]['status'], 'pending')

    def test_missing_old_report_digest_or_validation_does_not_reuse_boundary(self):
        report, proof = sr.prepare_incremental(self.old, self.report, self.state, self.request,
                                              validation_passed=True)
        self.assertEqual(proof['boundary_provenance'][0]['status'], 'pending')
        self.assertEqual(report['boundary_reviews'][0]['status'], 'pending')
        report, proof = sr.prepare_incremental(self.old, self.report, self.state, self.request,
            expected_report_sha256=sr.digest(self.report))
        self.assertEqual(proof['boundary_provenance'][0]['status'], 'pending')

    def test_changed_local_basis_cannot_be_reused_via_a_forged_bound_target(self):
        previous = copy.deepcopy(self.report)
        previous['boundary_reviews'][0]['binding']['initial']['car']['facts']['position'] = 'invented'
        with self.assertRaisesRegex(ValueError, '旧局部边界绑定'):
            self.compare(self.request, previous)

    def test_public_full_validation_prepare_incremental_and_final_gate(self):
        harness = director_fixture.AuditDirectorChecks()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        state = copy.deepcopy(harness.state)
        script = ('场景1-日-内-房间\n人物：陈默-基础形象；\n△ 陈默站起后看向窗外。\n'
                  '场景2-日-内-走廊\n人物：陈默-基础形象；\n△ 陈默站起后看向窗外。\n')
        state['records'], state['length'] = d.build_records(script, state['ledger'])
        units = []
        for index, record in enumerate(state['records']):
            unit = harness.unit([record['start'], record['end']], scene=record['scene'])
            unit['review_dependencies'] = {'policy': sd.POLICY_V3, 'complete': True,
                'basis': '虚构分场各自建立窗边位置，镜内固定观察原有动作。', 'links': []}
            for n, shot in enumerate(unit['shots']):
                shot['review_id'] = f'VALID-U{index + 1}-S{n + 1}'
            for field in ('initial', 'final'):
                unit['continuity'][field]['CHAR0001']['facts']['位置'] = '房间窗边' if index == 0 else '走廊窗边'
            if index:
                unit['continuity']['boundary'] = {'kind': 'reset', 'reason': '原文明确进入走廊',
                    'evidence': [{'scene': 'SC0002', 'quote': '场景2-日-内-走廊'}]}
            units.append(unit)
        request = {'mode': 'append', 'request_id': 'fictional-public-boundary',
                   'span': [0, state['length']], 'units': units}
        warnings = d.validate(state, request)
        snapshot = sr.freeze(state, request, warnings, validation_passed=True)
        previous = gate.pending(state, request, warnings)
        previous.update(status='passed', reviewer='fictional public-api reviewer')
        for row in previous['checks'].values():
            row.update(status='passed', basis='虚构房间与走廊的原文、时长和人物状态已对照核实。')
        for row in previous['shot_reviews']:
            row.update(status='passed', basis='人物位于当前场景窗边，镜头保留其站起和转头动作。')
        for row in previous['warning_decisions']:
            row.update(decision='accepted', basis='本条诊断的虚构转场或固定观察已有原文和实际镜头依据。')
        boundary = previous['boundary_reviews'][0]
        boundary.update(status='passed', basis='原文进入走廊，位置重建，人物身份及在场状态持续。',
            reset_fields=[field for field in boundary['fields'] if field['path'][0] == 'facts'],
            carried_fields=[field for field in boundary['fields'] if field['path'][0] != 'facts'])
        gate.verify(state, request, previous, warnings)
        changed = copy.deepcopy(request)
        changed['units'][0]['shots'][0]['camera'] = '房间窗边斜侧低机位固定'
        current_warnings = d.validate(state, changed)
        report, proof = sr.prepare_incremental(snapshot, previous, state, changed, current_warnings,
            validation_passed=True, expected_report_sha256=sr.digest(previous))
        self.assertEqual(proof['boundary_provenance'][0]['status'], 'reused')
        self.assertGreater(proof['stats']['reused_shots'], 0)
        report.update(status='passed', reviewer='fictional current-version reviewer')
        for row in report['checks'].values():
            row.update(status='passed', basis='已对当前虚构低机位版本、来源覆盖和局部转场重新确认。')
        for row in report['shot_reviews']:
            if row['status'] == 'pending':
                row.update(status='passed', basis='修改后的低机位保留窗边站位与既有动作的完整可见范围。')
        for row in report['warning_decisions']:
            if row['decision'] == 'pending':
                row.update(decision='accepted', basis='已复核当前虚构镜头与转场，诊断条件有具体依据。')
        self.assertEqual(gate.verify(state, changed, report, current_warnings)['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
