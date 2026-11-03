"""Shared source declarations use fictional fixtures and never attest reading."""
import copy
import json
import unittest
from unittest.mock import patch

import storyboard_authoring_gate as gate
import storyboard_text as core
import storyboard_text_delivery as delivery
import test_scene_text_performance as delivery_fixtures
import test_storyboard_authoring_gate as legacy_fixtures
import test_storyboard_authoring_v4 as compact_fixtures


class SourceMappingDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.state = {'records': [
            {'id': 'L000001', 'kind': 'action', 'label': '',
             'text': '非人角色跃过台阶。'},
            {'id': 'L000002', 'kind': 'action', 'label': '',
             'text': '机械门从关闭变为半开。'},
            {'id': 'L000003', 'kind': 'dialogue', 'label': '传讯装置（逐字停顿）',
             'text': '入口已开启。', 'source_kind': 'non_character'},
        ]}
        self.units = [{
            'draft_key': 'SC0001/u01',
            'source_records': ['L000001', 'L000002', 'L000003'],
            'shots': [
                {'dialogue_parts': []},
                {'dialogue_parts': [{'record': 'L000003', 'source_kind': 'non_character'}]},
            ],
        }]
        self.trace = {
            'policy': gate.POLICY, 'version': gate.VERSION,
            'units': [{'draft_key': 'SC0001/u01', 'shots': [
                {'shot_key': 'SC0001/u01#1', 'source_records': ['L000001']},
                {'shot_key': 'SC0001/u01#2', 'source_records': ['L000002', 'L000003'],
                 'source_cues': ['L000003:逐字停顿']},
            ]}],
        }

    def errors(self):
        return gate.source_mapping_errors(self.state, self.units, self.trace)

    def test_nonhuman_object_and_voice_sources_need_only_existing_mapping_fields(self):
        before = copy.deepcopy((self.state, self.units, self.trace))
        self.assertEqual(self.errors(), [])
        self.assertEqual((self.state, self.units, self.trace), before)

    def test_object_state_record_must_be_mapped_without_semantic_extraction(self):
        self.trace['units'][0]['shots'][1]['source_records'] = ['L000003']
        errors = self.errors()
        self.assertEqual([row['records'] for row in errors
                          if row['code'] == 'AUTHORING_TRACE_SOURCE_COVERAGE'], [['L000002']])

    def test_every_actual_voice_record_must_belong_to_its_shot_mapping(self):
        # Global record coverage cannot substitute for the actual voice's shot.
        self.trace['units'][0]['shots'][0]['source_records'].append('L000003')
        self.trace['units'][0]['shots'][1]['source_records'] = ['L000002']
        errors = self.errors()
        self.assertTrue(any(row['code'] == 'AUTHORING_TRACE_SOURCE'
                            and row.get('shot') == 'SC0001/u01#2' for row in errors))
        self.assertIn('AUTHORING_SOURCE_CUE_MAPPING', {row['code'] for row in errors})
        self.assertNotIn('AUTHORING_TRACE_SOURCE_COVERAGE', {row['code'] for row in errors})

    def test_missing_cue_cannot_be_replaced_by_whole_scene_claim(self):
        self.trace['units'][0]['shots'][1].pop('source_cues')
        self.trace['source_cue_coverage'] = {'status': '所有要求均已落实'}
        self.assertEqual([row['cues'] for row in self.errors()
                          if row['code'] == 'AUTHORING_SOURCE_CUE_COVERAGE'],
                         [[('L000003', '逐字停顿')]])

    def test_explicit_omission_requires_exact_cue_and_concrete_reason(self):
        self.trace['units'][0]['shots'][1].pop('source_cues')
        self.trace['source_cue_omissions'] = [{
            'source_record': 'L000003', 'cue': '逐字停顿',
            'reason': '来源要求的停顿与这次连续播报存在冲突，交由主审对照来源核实。',
        }]
        before = copy.deepcopy(self.trace)
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.trace, before)
        self.trace['source_cue_omissions'][0]['reason'] = ''
        self.assertEqual({row['code'] for row in self.errors()}, {
            'AUTHORING_SOURCE_CUE_MAPPING', 'AUTHORING_SOURCE_CUE_COVERAGE',
        })

    def test_ordinary_actions_and_voice_type_labels_do_not_become_inferred_cues(self):
        self.state['records'][0]['text'] = '非人角色跃过台阶，神色紧张，随后松开抓握。'
        self.state['records'][2]['label'] = '传讯装置（非人物播报）'
        self.trace['units'][0]['shots'][1].pop('source_cues')
        self.assertEqual(self.errors(), [])

    def test_malformed_trace_containers_and_keys_report_shape_errors(self):
        for malformed in (None, [], 7):
            with self.subTest(trace=malformed):
                self.assertEqual(gate.source_mapping_errors(self.state, self.units, malformed)[0]['code'],
                                 'AUTHORING_TRACE_INVALID')
        for malformed_units in (None, {}, [None], [{'shots': None}],
                                [{'draft_key': 'SC0001/u01', 'shots': [None]}],
                                [{'draft_key': 'SC0001/u01', 'shots': [{'shot_key': []}]}]):
            with self.subTest(units=malformed_units):
                trace = dict(self.trace, units=malformed_units)
                errors = gate.source_mapping_errors(self.state, self.units, trace)
                self.assertIn('AUTHORING_TRACE_SHOT_COVERAGE', {row['code'] for row in errors})

    def test_malformed_source_and_omission_values_report_errors_without_crashing(self):
        for source in (None, {}, [], [[]], [{}], [None]):
            with self.subTest(source=source):
                trace = copy.deepcopy(self.trace)
                trace['units'][0]['shots'][0]['source_records'] = source
                errors = gate.source_mapping_errors(self.state, self.units, trace)
                self.assertIn('AUTHORING_TRACE_SOURCE', {row['code'] for row in errors})
        for omissions in (None, {}, [None], [{'source_record': []}],
                          [{'source_record': 'L000003', 'cue': {}, 'reason': '待核实'}]):
            with self.subTest(omissions=omissions):
                trace = dict(self.trace, source_cue_omissions=omissions)
                errors = gate.source_mapping_errors(self.state, self.units, trace)
                self.assertIn('AUTHORING_SOURCE_CUE_MAPPING', {row['code'] for row in errors})

    def test_full_v4_gate_reuses_collector_without_duplicate_source_errors(self):
        fixture = compact_fixtures.CompactAuthoringTraceTest('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.set_cue()
        trace = fixture.trace()
        expected = gate.source_mapping_errors(fixture.state, fixture.units, trace)
        with patch.object(gate, 'source_mapping_errors', wraps=gate.source_mapping_errors) as collect:
            report = fixture.validate(trace)
        collect.assert_called_once()
        self.assertEqual(report['errors'], expected)
        self.assertEqual(report['semantic_review'], 'not_performed')

    def test_full_v4_gate_reports_malformed_shot_rows_safely(self):
        fixture = compact_fixtures.CompactAuthoringTraceTest('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        trace = fixture.trace()
        trace['units'][0]['shots'] = [{'shot_key': {'invalid': 'key'}}]
        report = fixture.validate(trace)
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('AUTHORING_TRACE_SHOT_COVERAGE', {row['code'] for row in report['errors']})

    def test_v3_keeps_its_existing_source_contract(self):
        fixture = legacy_fixtures.StoryboardAuthoringGateTest('runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        path = fixture.write_trace()
        with patch.object(gate, 'source_mapping_errors') as collect:
            report = gate.validate(fixture.project, fixture.state, fixture.draft,
                                   fixture.units, fixture.context, path)
        collect.assert_not_called()
        self.assertEqual(report['status'], 'passed')

    def test_basis_fields_are_preserved_and_do_not_suppress_statistical_warnings(self):
        for version in (gate.LEGACY_VERSION, gate.VERSION):
            with self.subTest(version=version):
                if version == gate.LEGACY_VERSION:
                    fixture = legacy_fixtures.StoryboardAuthoringGateTest('runTest')
                    fixture.setUp()
                    self.addCleanup(fixture.tearDown)
                    path = fixture.repeated_trace()
                    trace = json.loads(path.read_text(encoding='utf-8'))
                else:
                    fixture = compact_fixtures.CompactAuthoringTraceTest('runTest')
                    fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                    fixture.make_repeated_unit()
                    trace = fixture.trace()
                    path = fixture.project / 'draft.txt.authoring.json'
                for shot in fixture.units[0]['shots']:
                    shot['text'] = shot['text'].replace('转场：硬切', '转场：动作匹配切')
                trace['timing_distribution_basis'] = '来源包含六次等间隔变化，每次保留同样的观察时间。'
                trace['transition_distribution_basis'] = '每次变化都以相同接触点衔接，持续采用动作匹配切。'
                path.write_text(json.dumps(trace, ensure_ascii=False), encoding='utf-8')
                before = path.read_bytes()
                report = gate.validate(fixture.project, fixture.state, fixture.draft,
                                       fixture.units, fixture.context, path)
                self.assertEqual(report['status'], 'passed', report['errors'])
                self.assertTrue({'AUTHORING_UNIFORM_TIMING', 'AUTHORING_UNIFORM_TRANSITION'}
                                <= {row['code'] for row in report['warnings']})
                self.assertEqual(path.read_bytes(), before)


class PublicSourceMappingDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        fixture = delivery_fixtures.SceneTextPerformanceTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fx = fixture.fx
        self.state = delivery.d.load(self.fx.project)
        record = next(row for row in self.state['records'] if row['kind'] == 'dialogue')
        record['label'] = record['speaker'] + '（逐字停顿）'
        self.cue = (record['id'], '逐字停顿')
        text = self.fx.draft.read_text(encoding='utf-8').replace('@{CHAR0001}把', '陈默把')
        self.fx.draft.write_text(text, encoding='utf-8')
        raw = self.fx.draft.read_bytes()
        with self.assertRaises(core.TextValidationError) as caught:
            core.parse(raw.decode('utf-8'), self.state)
        authored = delivery.d.read_json(self.fx.project / 'authored-decisions.json')
        self.trace = gate.bind_metadata(authored, raw, caught.exception.units,
                                        delivery.context_hash(self.state, self.fx.project))
        self.path = gate.trace_path(self.fx.draft)
        self.path.write_text(json.dumps(self.trace, ensure_ascii=False), encoding='utf-8')

    def test_preview_reports_current_cue_and_reference_failures_together_without_writes(self):
        for explicit_path in (None, self.path):
            with self.subTest(explicit_path=explicit_path):
                before = self.fx.snapshot()
                with patch.object(delivery.d, 'load', return_value=self.state):
                    with self.assertRaises(core.TextValidationError) as caught:
                        delivery.preview(self.fx.project, self.fx.draft, authoring_trace=explicit_path)
                errors = caught.exception.errors
                codes = {row['code'] for row in errors}
                self.assertTrue({'BARE_CHARACTER_NAME', 'AUTHORING_SOURCE_CUE_COVERAGE'} <= codes)
                self.assertNotIn('AUTHORING_TRACE_BINDING', codes)
                self.assertEqual([row['cues'] for row in errors
                                  if row['code'] == 'AUTHORING_SOURCE_CUE_COVERAGE'], [[self.cue]])
                self.assertEqual(self.fx.snapshot(), before)

    def test_preview_stale_trace_reports_binding_without_reusing_its_source_mapping(self):
        for field in ('draft_sha256', 'context_sha256'):
            with self.subTest(field=field):
                trace = dict(self.trace, **{field: 'fictional-stale-binding'})
                self.path.write_text(json.dumps(trace, ensure_ascii=False), encoding='utf-8')
                before = self.fx.snapshot()
                with patch.object(delivery.d, 'load', return_value=self.state), \
                        patch.object(gate, 'source_mapping_errors') as collect:
                    with self.assertRaises(core.TextValidationError) as caught:
                        delivery.preview(self.fx.project, self.fx.draft, authoring_trace=self.path)
                collect.assert_not_called()
                codes = {row['code'] for row in caught.exception.errors}
                self.assertTrue({'BARE_CHARACTER_NAME', 'AUTHORING_TRACE_BINDING'} <= codes)
                self.assertNotIn('AUTHORING_SOURCE_CUE_COVERAGE', codes)
                self.assertEqual(self.fx.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
