"""Fictional dependency fixtures; no production reports or external writes."""
import copy
import unittest
from unittest.mock import patch

import director as d
import review_gate as gate
import scene_review as sr
import shot_dependencies as sd
import shot_review
import test_audit_director as director_fixture
import test_local4_upgrade as fine
import test_scene_review as legacy
import test_authoring_gate_audit as authoring_fixture
import authoring_gates as ag
import storyboard_preflight as pf


def fixture():
    state, request = fine.fine_fixture()
    for unit in request['units']:
        unit['review_dependencies'].update(policy=sd.POLICY_V2, basis='独立观察')
    return state, request


def link(source='U3S1', target='U3S2', basis='同轴'):
    return {'from': source, 'to': target, 'kind': 'axis', 'basis': basis}


class DependencyV2Tests(unittest.TestCase):
    def setUp(self):
        self.rules = patch.object(sr, 'rules_digest', return_value='fixture-dependency-rules')
        self.rules.start()
        self.addCleanup(self.rules.stop)
        self.state, self.request = fixture()

    def freeze(self, request=None, warnings=None):
        request = request or self.request
        snapshot = sr.freeze(self.state, request, warnings, validation_passed=True)
        report = legacy.reviewed(self.state, request, warnings)
        return snapshot, report

    def prepare(self, changed, warnings=None, original=None):
        snapshot, report = self.freeze(original, warnings)
        return sr.prepare_incremental(snapshot, report, self.state, changed, warnings,
                                      validation_passed=True, expected_report_sha256=sr.digest(report))

    def pending(self, proof):
        return [x['key'] for x in proof['shot_provenance'] if x['status'] == 'pending']

    def test_v2_short_evidence_is_structural_only_and_v1_keeps_length_rule(self):
        unit = self.request['units'][2]
        unit['review_dependencies']['links'] = [link(basis='轴')]
        sd.validate_unit(unit)
        self.assertIsNotNone(sd.graph(unit))
        unit['review_dependencies']['policy'] = sd.POLICY
        self.assertIsNone(sd.graph(unit))
        errors = sd.diagnostics(unit, 'units[2]')
        self.assertTrue(any(e['path'] == 'units[2].review_dependencies.links[0].basis'
                            and e['code'] == 'legacy_basis_length' for e in errors))

    def test_v2_blank_evidence_and_unknown_fields_are_rejected_explicitly(self):
        for mutation, expected in (
                (lambda g: g.update(basis='  '), '.basis'),
                (lambda g: g.update(complete=False), '.complete'),
                (lambda g: g.update(extra=True), 'declaration_fields'),
                (lambda g: g.update(links=[dict(link(), fields=['camera'])]), 'link_fields'),
                (lambda g: g.update(links=[dict(link(), kind=[])]), '.kind')):
            request = copy.deepcopy(self.request)
            mutation(request['units'][2]['review_dependencies'])
            errors = sd.request_diagnostics(request)
            self.assertTrue(any(expected in str(e) for e in errors), errors)
            with self.assertRaises(ValueError):
                sr.freeze(self.state, request, validation_passed=True)

    def test_unknown_policy_cannot_fall_back_to_legacy(self):
        self.request['units'][2]['review_dependencies']['policy'] = 'unrecognized'
        with self.assertRaisesRegex(ValueError, r'units\[2\].review_dependencies.policy'):
            self.freeze()

    def test_unit_allows_cross_unit_edges_request_resolves_them(self):
        self.request['units'][2]['review_dependencies']['links'] = [link('U3S2', 'U4S2')]
        sd.validate_unit(self.request['units'][2])
        sd.validate_request(self.request)
        self.request['units'][2]['review_dependencies']['links'][0]['to'] = 'missing'
        sd.validate_unit(self.request['units'][2])
        with self.assertRaisesRegex(ValueError, r'units\[2\].review_dependencies.links\[0\].to'):
            sd.validate_request(self.request)

    def test_request_duplicate_ids_and_invalid_id_types_are_diagnostic(self):
        self.request['units'][3]['shots'][0]['review_id'] = 'U3S1'
        self.assertTrue(any(e['code'] == 'duplicate_review_id'
                            for e in sd.request_diagnostics(self.request)))
        self.request['units'][2]['shots'][0]['review_id'] = []
        self.assertTrue(any(e['code'] == 'review_id_required'
                            for e in sd.request_diagnostics(self.request)))

    def test_undeclared_legacy_inputs_remain_supported(self):
        for unit in self.request['units']:
            unit.pop('review_dependencies')
            for shot in unit['shots']:
                shot.pop('review_id')
        sd.validate_request(self.request)
        snapshot, _ = self.freeze()
        sr.verify_snapshot(snapshot)

    def test_v1_target_and_dependency_golden_fingerprints_are_unchanged(self):
        # Captured from the pre-upgrade implementation using this fictional
        # fixture. This guards both target shape and the exact v1 hash domain.
        state, request = fine.fine_fixture()
        snapshot = sr.freeze(state, request, validation_passed=True)
        self.assertEqual(sr.digest(shot_review.targets(state, request)),
                         'ac2da046208dc5637ed542f17fc8fbb971bc33afba72f257b45ce267ebf083e9')
        self.assertEqual(sr.digest(sd.dependencies(snapshot, {0, 5})),
                         'e19aa9af82f60852f6da99357373c22c0ab3390ab6344b1064963d06d95f4ebb')
        sr.verify_snapshot(snapshot)

    def test_v1_report_is_not_implicitly_migrated_to_v2(self):
        _, original = fine.fine_fixture()
        changed = copy.deepcopy(original)
        changed['units'][2]['review_dependencies']['policy'] = sd.POLICY_V2
        _, proof = self.prepare(changed, original=original)
        self.assertTrue({'3:1', '3:2', '3:3'} <= set(self.pending(proof)))

    def test_full_graph_stays_in_snapshot_local_edge_basis_changes_only_target(self):
        self.request['units'][2]['review_dependencies']['links'] = [link()]
        changed = copy.deepcopy(self.request)
        changed['units'][2]['review_dependencies']['links'][0]['basis'] = '以车头为轴'
        snapshot, _ = self.freeze()
        self.assertEqual(snapshot['request']['units'][2]['review_dependencies']['links'], [link()])
        self.assertEqual(snapshot['targets'][6]['dependency_declaration']['links'], [])
        self.assertEqual(snapshot['targets'][7]['dependency_declaration']['links'], [link()])
        report, proof = self.prepare(changed)
        self.assertEqual(self.pending(proof), ['3:2'])
        self.assertEqual(report['status'], 'pending')
        self.assertTrue(all(check['status'] == 'pending' for check in report['checks'].values()))

    def test_edge_basis_propagates_through_declared_closure_and_boundary(self):
        self.request['units'][2]['review_dependencies']['links'] = [link(), link('U3S2', 'U3S3')]
        changed = copy.deepcopy(self.request)
        changed['units'][2]['review_dependencies']['links'][0]['basis'] = '以车头为轴'
        _, proof = self.prepare(changed)
        self.assertEqual(self.pending(proof), ['3:2', '3:3', '4:1'])

    def test_public_premise_changes_keep_whole_unit_dependencies(self):
        changed = copy.deepcopy(self.request)
        changed['units'][2]['review_dependencies']['basis'] = '公共朝向已变化'
        _, proof = self.prepare(changed)
        self.assertTrue({'3:1', '3:2', '3:3'} <= set(self.pending(proof)))

    def test_cross_unit_edge_basis_is_not_lost_from_remote_target(self):
        self.request['units'][2]['review_dependencies']['links'] = [link('U3S2', 'U4S2')]
        changed = copy.deepcopy(self.request)
        changed['units'][2]['review_dependencies']['links'][0]['basis'] = '承接前单元车头方向'
        _, proof = self.prepare(changed)
        self.assertEqual(self.pending(proof), ['4:2'])

    def test_explicit_edge_to_unit_entry_closes_its_implicit_incoming_boundary(self):
        self.request['units'][4]['review_dependencies']['links'] = [link('U4S1', 'U5S2')]
        changed = copy.deepcopy(self.request)
        changed['units'][2]['shots'][2]['camera'] = '改变真实前接镜的摄影轴线'
        _, proof = self.prepare(changed)
        self.assertEqual(self.pending(proof), ['3:3', '4:1', '5:2'])
        target = next(p for p in proof['shot_provenance'] if p['key'] == '5:2')
        self.assertEqual(set(target['verified_dependencies']), {'U3S3', 'U4S1', 'U5S2'})

    def test_explicit_edge_to_unit_exit_closes_its_implicit_outgoing_boundary(self):
        self.request['units'][4]['review_dependencies']['links'] = [link('U3S3', 'U5S2')]
        changed = copy.deepcopy(self.request)
        changed['units'][3]['shots'][0]['camera'] = '改变真实后接镜的摄影轴线'
        _, proof = self.prepare(changed)
        self.assertEqual(self.pending(proof), ['3:3', '4:1', '5:2'])
        target = next(p for p in proof['shot_provenance'] if p['key'] == '5:2')
        self.assertEqual(set(target['verified_dependencies']), {'U3S3', 'U4S1', 'U5S2'})

    def test_reached_trusted_reset_blocks_boundary_but_unreviewed_reset_does_not(self):
        # U6 is the fixture's actual second scene, with source-backed reset.
        self.request['units'][4]['review_dependencies']['links'] = [link('U6S1', 'U5S2')]
        changed = copy.deepcopy(self.request)
        changed['units'][4]['shots'][2]['camera'] = '改变重置前的镜头'
        for trusted in (True, False):
            snapshot, report = self.freeze()
            if not trusted:
                report['checks']['continuity']['status'] = 'failed'
            _, proof = sr.prepare_incremental(
                snapshot, report, self.state, changed, validation_passed=True,
                expected_report_sha256=sr.digest(report))
            target = next(p for p in proof['shot_provenance'] if p['key'] == '5:2')
            self.assertEqual(target['status'], 'reused' if trusted else 'pending')
            self.assertEqual('U5S3' in target['verified_dependencies'], not trusted)

    def test_reached_entry_does_not_use_nonadjacent_array_predecessor(self):
        self.request['units'][4]['review_dependencies']['links'] = [link('U4S1', 'U5S2')]
        self.request['units'] = [self.request['units'][i] for i in (1, 3, 4)]
        changed = copy.deepcopy(self.request)
        changed['units'][0]['shots'][2]['camera'] = '改变隔着来源缺口的数组前项'
        _, proof = self.prepare(changed)
        target = next(p for p in proof['shot_provenance'] if p['key'] == '3:2')
        self.assertEqual(target['status'], 'reused')
        self.assertNotIn('U2S3', target['verified_dependencies'])

    def test_reached_entry_retains_exact_external_neighbour_evidence(self):
        self.request['units'][4]['review_dependencies']['links'] = [link('U4S1', 'U5S2')]
        self.state['units'] = copy.deepcopy(self.request['units'])
        self.request.update(mode='replace', units=self.request['units'][3:5])
        snapshot, report = self.freeze()
        state = copy.deepcopy(self.state)
        state['units'][2]['shots'][2]['camera'] = '改变真实未选前接镜'
        _, proof = sr.prepare_incremental(
            snapshot, report, state, self.request, validation_passed=True,
            expected_report_sha256=sr.digest(report))
        target = next(p for p in proof['shot_provenance'] if p['key'] == '2:2')
        self.assertEqual(target['status'], 'pending')
        self.assertIn('dependency:U4S1.external', target['reasons'])

    def test_edge_removal_and_camera_change_still_require_real_dependencies(self):
        self.request['units'][2]['review_dependencies']['links'] = [link()]
        for remove in (True, False):
            changed = copy.deepcopy(self.request)
            if remove:
                changed['units'][2]['review_dependencies']['links'] = []
                expected = ['3:2']
            else:
                changed['units'][2]['shots'][0]['camera'] = '反向机位'
                expected = ['2:3', '3:1', '3:2']
            _, proof = self.prepare(changed)
            self.assertEqual(self.pending(proof), expected)

    def test_entire_audio_and_all_fragments_remain_public_dependencies(self):
        for fragment in (False, True):
            changed = copy.deepcopy(self.request)
            if fragment:
                changed['units'][2]['shots'][1]['dialogue_parts'] = [{'record': 'L3', 'pause': 0.5}]
            else:
                changed['units'][2]['audio'][0]['pause'] = 0.5
            _, proof = self.prepare(changed)
            self.assertTrue({'3:1', '3:2', '3:3'} <= set(self.pending(proof)))

    def test_actual_state_propagation_stops_after_restoring_shot(self):
        self.request['units'] = self.request['units'][2:3]
        changed = copy.deepcopy(self.request)
        unit = changed['units'][0]
        unit['continuity']['initial']['car']['facts']['position'] = 'road'
        unit['shots'][1]['continuity']['changes'] = [
            {'entity': 'car', 'set': {'facts': {'position': 'curb'}},
             'evidence': [{'record': 'L3', 'quote': '小车停稳'}]}]
        _, proof = self.prepare(changed)
        self.assertEqual(self.pending(proof), ['1:1', '1:2'])

    def test_invalid_legacy_fallback_now_explains_the_exact_field(self):
        state, request = fine.fine_fixture()
        request['units'][2]['review_dependencies']['basis'] = '短'
        snapshot = sr.freeze(state, request, validation_passed=True)
        report = legacy.reviewed(state, request)
        changed = copy.deepcopy(request)
        changed['units'][2]['shots'][1]['camera'] = '改变的局部机位'
        _, proof = sr.prepare_incremental(snapshot, report, state, changed,
                                          validation_passed=True, expected_report_sha256=sr.digest(report))
        self.assertTrue(any(e['path'] == 'units[2].review_dependencies.basis'
                            and e['code'] == 'legacy_basis_length'
                            for e in proof['dependency_diagnostics']['current']))
        self.assertTrue({'3:1', '3:2', '3:3'} <= set(self.pending(proof)))

    def test_new_director_unit_entry_reports_dependency_field_before_later_checks(self):
        unit = self.request['units'][0]
        unit['review_dependencies']['basis'] = ''
        with self.assertRaisesRegex(ValueError, r'unit.review_dependencies.basis'):
            d.validate_unit({}, unit, [0, 4])

    def test_director_request_entry_checks_cross_ids_before_preflight(self):
        unit = {'output_format': d.OUTPUT_FORMAT, 'audio': [],
                'shots': [{'review_id': 'a'}],
                'review_dependencies': {'policy': sd.POLICY_V2, 'complete': True,
                                        'basis': '轴', 'links': [link('a', 'missing')]}}
        request = {'mode': 'append', 'request_id': 'fixture', 'units': [unit]}
        with patch.object(d, 'fixed_header'), patch.object(d, 'resolved_request', side_effect=lambda state, r: r):
            with self.assertRaisesRegex(ValueError, r'units\[0\].review_dependencies.links\[0\].to'):
                d.validate({'style_hash': 'fixture'}, request)

    def test_v2_gate_recomputes_bound_evidence_and_never_forges_new_passes(self):
        self.request['units'][2]['review_dependencies']['links'] = [link()]
        report, proof = self.prepare(self.request)
        self.assertEqual(proof['stats']['reused_shots'], 18)
        snapshot, reviewed = self.freeze()
        report.update(status='passed', reviewer='fictional final reviewer', checks=reviewed['checks'])
        gate.verify(self.state, self.request, report, [])
        report['shot_reviews'][7]['basis'] = '篡改虚构判断，不是旧审阅证据。'
        with self.assertRaisesRegex(ValueError, '复用判断'):
            gate.verify(self.state, self.request, report, [])


class WarningScopeV2Tests(unittest.TestCase):
    setUp = DependencyV2Tests.setUp
    freeze = DependencyV2Tests.freeze
    prepare = DependencyV2Tests.prepare

    def warning(self, shot=1):
        return sd.scope_warning({'kind': 'fixture scope warning', 'unit': 'u3', 'shot': shot},
                                self.request['units'])

    def test_local_and_boundary_warning_scopes_bind_stable_source_and_shot_ids(self):
        warning = self.warning()
        self.assertEqual(warning['review_scope'], {'policy': sd.WARNING_SCOPE_POLICY,
            'units': [{'unit_id': 'u3', 'span': [8, 12], 'scene_ids': ['SC1'], 'review_ids': ['U3S1']}]})
        unnumbered = copy.deepcopy(self.request['units'])
        for unit in unnumbered:
            unit.pop('id')
        local = sd.scope_warning({'kind': 'group', 'shots': [1, 3]}, [unnumbered[2]], local_index=0)
        self.assertEqual(local['review_scope']['units'][0]['span'], [8, 12])
        self.assertEqual(local['review_scope']['units'][0]['review_ids'], ['U3S1', 'U3S3'])
        boundary = sd.scope_warning({'kind': 'boundary', 'shots': [
            {'unit': '待提交单元3', 'shot': 3}, {'unit': '待提交单元4', 'shot': 1}]}, unnumbered)
        self.assertEqual(sr._warning_units(boundary, unnumbered), [2, 3])

    def test_legacy_warning_hashes_and_unknown_scopes_are_preserved(self):
        _, v1 = fine.fine_fixture()
        warning = {'kind': 'fixture', 'unit': 'u3', 'shot': 1}
        self.assertEqual(sd.scope_warning(warning, v1['units']), warning)
        unknown = sd.scope_warning(dict(warning, unit='ambiguous'), self.request['units'])
        self.assertNotIn('review_scope', unknown)
        self.assertEqual(sr._warning_units(unknown, self.request['units']), list(range(6)))

    def test_malformed_explicit_scope_cannot_fall_back_to_narrow_legacy_name(self):
        for broken in ({'policy': 'unknown', 'units': []},
                       {'policy': sd.WARNING_SCOPE_POLICY, 'units': [{'span': [99, 100], 'scene_ids': ['SC1']}]}):
            warning = {'kind': 'fixture', 'unit': 'u3', 'review_scope': broken}
            self.assertEqual(sr._warning_units(warning, self.request['units']), list(range(6)))

    def test_warning_reuses_unaffected_shot_but_not_changed_target_or_unit_audio(self):
        for changed_field, shot, reused in [('camera', 1, 1), ('camera', 2, 0), ('audio', 1, 0)]:
            changed = copy.deepcopy(self.request)
            if changed_field == 'camera':
                changed['units'][2]['shots'][1]['camera'] = '降低的固定机位'
            else:
                changed['units'][2]['audio'][0]['pause'] = 0.5
            _, proof = self.prepare(changed, [self.warning(shot)])
            self.assertEqual(proof['stats']['reused_warnings'], reused)

    def test_unknown_shot_scope_requires_pending_decision(self):
        warning = self.warning()
        warning['review_scope']['units'][0]['review_ids'] = ['missing']
        _, proof = self.prepare(self.request, [warning])
        self.assertEqual(proof['stats']['pending_warnings'], 1)
        self.assertIn('warning_scope_dependencies_unknown', proof['warning_provenance'][0]['reasons'])

    def test_warning_reuse_evidence_is_recomputed(self):
        warning = self.warning()
        report, proof = self.prepare(self.request, [warning])
        self.assertEqual(proof['stats']['reused_warnings'], 1)
        sr.verify_reuse_evidence(self.state, self.request, report, [warning])
        report['warning_decisions'][0]['basis'] = '替换后的虚构依据。'
        with self.assertRaisesRegex(ValueError, '复用警告'):
            sr.verify_reuse_evidence(self.state, self.request, report, [warning])

    def test_director_attaches_scope_at_the_actual_local_emission_point(self):
        harness = director_fixture.AuditDirectorChecks()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        unit = harness.unit([0, 9])
        unit['review_dependencies'] = {'policy': sd.POLICY_V2, 'complete': True, 'basis': '独立', 'links': []}
        for n, shot in enumerate(unit['shots']):
            shot['review_id'] = f'local-{n}'
        emitted = {'kind': 'fixture local diagnostic', 'shots': [2, 3]}
        with patch.object(d, 'photography_warnings', return_value=[emitted]):
            warnings = d.validate_unit(harness.state, unit, [0, 9])
        warning = next(w for w in warnings if w['kind'] == emitted['kind'])
        self.assertEqual(warning['review_scope']['units'][0],
                         {'span': [0, 9], 'scene_ids': ['SC0001'], 'review_ids': ['local-1', 'local-2']})

    def test_scoped_audio_warning_reuses_current_readiness_but_changed_plan_does_not(self):
        harness = authoring_fixture.SingleWarningSourceTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        state, request = harness.state, harness.request
        unit = request['units'][0]
        unit['review_dependencies'] = {'policy': sd.POLICY_V2, 'complete': True,
                                       'basis': '原音与原位', 'links': []}
        for n, shot in enumerate(unit['shots']):
            shot['review_id'] = f'audio-{n}'
        unit['staging_review']['input_sha256'] = ag.staging_pending(state, unit)['input_sha256']
        self.assertEqual(ag.audio_ready(state, unit, pf.check_audio_plan(unit))['status'], 'ready')
        warnings = d.validate(state, request)
        original = next(w for w in warnings if w.get('code') == 'AUDIO_RATE')
        self.assertIn('review_scope', original)
        report = gate.pending(state, request, warnings)
        accepted = next(w for w in report['warning_decisions'] if w['warning'].get('code') == 'AUDIO_RATE')
        self.assertEqual(accepted['decision'], 'accepted')
        self.assertEqual(accepted['warning'], original)
        self.assertEqual(report['status'], 'pending')
        self.assertTrue(all(x['status'] == 'pending' for x in report['shot_reviews']))
        self.assertTrue(all(x['status'] == 'pending' for x in report['checks'].values()))
        _, proof = sr.prepare_incremental(
            sr.freeze(state, request, warnings, validation_passed=True),
            legacy.reviewed(state, request, warnings), state, request, warnings,
            validation_passed=True)  # No old report digest: current readiness is separate evidence.
        self.assertEqual(proof['stats']['authoring_reused_warnings'], 1)
        self.assertEqual(proof['warning_provenance'][0]['decision_source'], 'current_audio_preflight')
        # A real timing edit makes the writer's exact input hash stale, even if
        # a caller supplies the prior warning object to pending().
        unit['audio'][0]['pause'] = 0.3
        unit['audio_plan'][0]['parts'][0]['pause'] = 0.3
        unit['shots'][0]['dialogue_parts'][0]['pause'] = 0.3
        self.assertEqual(ag.audio_ready(state, unit, pf.check_audio_plan(unit))['status'], 'blocked')
        changed = gate.pending(state, request, [original])
        self.assertEqual(changed['warning_decisions'][0]['decision'], 'pending')


if __name__ == '__main__':
    unittest.main()
