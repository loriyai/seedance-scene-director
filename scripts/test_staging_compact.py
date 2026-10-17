"""Lossless staging display snapshots; no synthetic semantic approval."""
import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch

import authoring_gates as ag
import director as d
import shot_review
import shot_dependencies
import staging_preflight as staging
import test_audit_director as director_fixture


class StagingCompactTests(unittest.TestCase):
    def setUp(self):
        fixture = director_fixture.AuditDirectorChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.state = fixture.project, fixture.state
        units = []
        for n in range(2):
            unit = fixture.unit([n * 9, (n + 1) * 9])
            unit['style'] = '3D国漫'
            entity = 'CHAR0001'
            unit['continuity']['initial'][entity]['facts'] = {'姿态': '坐着', '旧姿态': '待覆盖'}
            evidence = [{'record': self.state['records'][n]['id'], 'quote': '站起'}]
            unit['shots'][0]['continuity']['changes'] = [
                {'entity': entity, 'set': {'facts': {'姿态': '站立', '旧姿态': None}},
                 'evidence': evidence}]
            unit['shots'][1]['continuity']['changes'] = [
                {'entity': entity, 'set': {'facts': {'朝向': '窗外'}},
                 'evidence': [{'record': self.state['records'][n]['id'], 'quote': '看向窗外'}]}]
            unit['continuity']['final'][entity]['facts'] = {'姿态': '站立', '朝向': '窗外'}
            units.append(unit)
        self.request = {'mode': 'append', 'request_id': 'compact-fictional',
                        'span': [0, 18], 'units': units}
        self.raw = json.dumps(self.request, ensure_ascii=False, indent=3).encode('utf-8')
        self.path = self.root / 'compact.json'

    def write_view(self, view):
        self.path.write_text(json.dumps(view, ensure_ascii=False), encoding='utf-8')

    def build(self):
        view = staging.compact(self.state, self.request, self.raw, self.path)
        self.write_view(view)
        return view

    def test_full_reconstruction_and_old_staging_bindings_equal(self):
        before = copy.deepcopy(self.request)
        view = self.build()
        full = staging.expand(self.path)
        expected = staging.prepare(self.state, self.request)
        self.assertEqual(full, expected)
        self.assertEqual(self.request, before)
        for i, entry in enumerate(full['units']):
            self.assertEqual(entry['staging_review'], ag.staging_pending(self.state, self.request['units'][i]))
            self.assertEqual(view['units'][i]['actual']['rendered'], entry['actual']['rendered'])
            self.assertEqual(staging.entering_states(view['units'][i]),
                             [t['entering_state'] for t in entry['actual']['targets']])
        self.assertEqual(view['status'], 'pending')
        self.assertEqual(view['evidence_use'], 'display_only_not_approval')
        self.assertTrue(all(t['status'] == 'pending' for u in view['units'] for t in u['actual']['targets']))

    def test_snapshots_preserve_raw_request_once_and_are_not_overwritten(self):
        view = self.build()
        ref = view['sources']['request']
        snapshot = self.root / ref['path']
        self.assertEqual(snapshot.read_bytes(), self.raw)
        before = snapshot.stat().st_mtime_ns
        staging.compact(self.state, self.request, self.raw, self.root / 'another.json')
        self.assertEqual(snapshot.stat().st_mtime_ns, before)
        self.assertEqual(len(list((self.root / '.staging-inputs').glob('request-*.json'))), 1)
        # External edits cannot silently overwrite an existing addressed file.
        snapshot.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, '不得覆盖'):
            staging.compact(self.state, self.request, self.raw, self.root / 'third.json')

    def test_state_and_request_snapshot_tampering_is_detected(self):
        view = self.build()
        for kind in ('state', 'request'):
            path = self.root / view['sources'][kind]['path']
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, '快照摘要不符'):
                staging.expand(self.path)
            path.write_bytes(original)

    def test_display_tamper_and_rehashed_display_tamper_are_detected(self):
        original = self.build()
        for rehash in (False, True):
            view = copy.deepcopy(original)
            view['units'][0]['actual']['rendered'] += 'invented action'
            if rehash:
                view['projection_sha256'] = staging._view_digest(view)
            self.write_view(view)
            with self.subTest(rehash=rehash), self.assertRaisesRegex(ValueError, '视图|重建材料'):
                staging.expand(self.path)

    def test_display_cannot_become_review_evidence_by_setting_passed(self):
        view = self.build()
        view['status'] = 'passed'
        view['projection_sha256'] = staging._view_digest(view)
        self.write_view(view)
        with self.assertRaisesRegex(ValueError, '不能作为通过证据'):
            staging.expand(self.path)
        view = self.build()
        unit = copy.deepcopy(self.request['units'][0])
        unit['staging_review'] = view['units'][0]['staging_review']
        with self.assertRaisesRegex(ValueError, '具体依据'):
            ag.validate_staging(self.state, unit)

    def test_version_and_source_path_are_validated(self):
        original = self.build()
        for kind in ('policy', 'path'):
            view = copy.deepcopy(original)
            if kind == 'policy':
                view['sources']['state']['policy'] = 'unknown'
            else:
                view['sources']['state']['path'] = '../outside.json'
            view['projection_sha256'] = staging._view_digest(view)
            self.write_view(view)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                staging.expand(self.path)

    def test_unit_and_shot_expansion_keep_actual_entering_state_and_context(self):
        self.build()
        expected = staging.prepare(self.state, self.request)
        self.assertEqual(staging.expand(self.path, [2])['units'], [expected['units'][1]])
        shot = staging.expand(self.path, [1], 3)
        self.assertEqual(shot['units'][0]['actual']['target'], expected['units'][0]['actual']['targets'][2])
        self.assertEqual(shot['units'][0]['actual']['target']['entering_state']['CHAR0001']['facts'],
                         {'姿态': '站立', '朝向': '窗外'})
        for selected, index in (([0], None), ([1, 1], None), ([1, 2], 1), ([1], 99)):
            with self.subTest(selected=selected, shot=index), self.assertRaises(ValueError):
                staging.expand(self.path, selected, index)

    def test_target_version_specific_context_is_not_assumed_to_be_v1(self):
        original_targets = shot_review.targets

        def projected_targets(*args, **kwargs):
            result = original_targets(*args, **kwargs)
            for row in result:
                row['dependency_declaration'] = {'policy': 'future-test-projection', 'node': row['shot_index']}
                row['additional_context'] = {'read_only': True, 'version': 'test'}
            return result

        with patch('shot_review.targets', side_effect=projected_targets):
            view = self.build()
            full = staging.expand(self.path)
            for unit in view['units']:
                for row in unit['actual']['targets']:
                    declaration = unit['actual']['contexts'][row['dependency_declaration_ref']]
                    self.assertEqual(declaration['node'], row['shot_index'])
                    self.assertEqual(row['additional_context']['version'], 'test')
            self.assertEqual(full, staging.prepare(self.state, self.request))
        # The material producer is now different: old evidence is not refreshed.
        with self.assertRaisesRegex(ValueError, '当前规则已变化'):
            staging.expand(self.path)

    def test_cli_old_default_and_compact_expansion_roundtrip(self):
        state_path, request_path = self.root / 'state.json', self.root / 'request.json'
        d.write_json(state_path, self.state)
        # Historical read_json accepts a BOM; capture it byte-for-byte too.
        request_path.write_bytes(b'\xef\xbb\xbf' + self.raw)

        def call(*args):
            with patch('sys.argv', ['staging_preflight.py', *map(str, args)]), contextlib.redirect_stdout(io.StringIO()):
                staging.main()

        full_path, unit_path = self.root / 'full.json', self.root / 'unit.json'
        call('--state', state_path, '--request', request_path, '--output', full_path)
        self.assertEqual(d.read_json(full_path), staging.prepare(self.state, self.request))
        call('--state', state_path, '--request', request_path, '--output', self.path, '--format', 'compact')
        # Expanding captured sources is independent of later live request edits.
        request_path.write_text('{}', encoding='utf-8')
        call('--expand', self.path, '--unit', 2, '--output', unit_path)
        self.assertEqual(d.read_json(unit_path)['units'], [d.read_json(full_path)['units'][1]])
        with self.assertRaises(FileExistsError):
            call('--expand', self.path, '--output', unit_path)

    def v2_cross_request(self):
        # Two continuous, source-backed units, not mocked target dictionaries.
        first, second = self.request['units']
        second['continuity']['initial'] = copy.deepcopy(first['continuity']['final'])
        second['continuity']['final'] = copy.deepcopy(first['continuity']['final'])
        for s in second['shots']:
            s['continuity']['changes'] = []
        self.state['records'][1]['text'] = '陈默继续看向窗外。'
        end = 9 + len(d.compact(self.state['records'][1]['text']))
        self.state['records'][1]['end'] = end
        self.state['length'] = end
        self.request['span'][1] = end
        second['span'][1] = end
        for n, unit in enumerate((first, second), 1):
            for i, s in enumerate(unit['shots'], 1):
                s['review_id'] = f'U{n}S{i}'
            unit['review_dependencies'] = {
                'policy': shot_dependencies.POLICY_V2, 'complete': True,
                'basis': '同一站立姿态与窗外视线持续，明确声明镜间连接。',
                'links': [{'from': f'U{n}S{i}', 'to': f'U{n}S{i+1}', 'kind': 'state',
                           'basis': '同一窗边站位和视线连续。'} for i in range(1, 4)]}
        edge = {'from': 'U1S4', 'to': 'U2S1', 'kind': 'state',
                'basis': '第二单元承接第一单元已站起并看向窗外的姿态。'}
        first['review_dependencies']['links'].append(edge)
        self.assertEqual(shot_dependencies.request_diagnostics(self.request), [])
        # These are legal source/timing/continuity units under the real validator.
        d.validate(self.state, self.request)
        self.raw = json.dumps(self.request, ensure_ascii=False).encode('utf-8')
        return edge

    def test_real_v2_cross_unit_edge_is_bound_once_and_roundtrips(self):
        edge = self.v2_cross_request()
        view = self.build()
        full = staging.prepare(self.state, self.request)
        self.assertEqual(staging.expand(self.path), full)
        for form in (full, view):
            declarations = [u['actual']['timing_and_state']['review_dependencies'] for u in form['units']]
            self.assertEqual(sum(edge in g['links'] for g in declarations), 1)
            self.assertIn(edge, declarations[0]['links'])
        # The isolated target path still has no cross-unit incoming edge; the
        # owning-unit full declaration is the evidence that closes this gap.
        self.assertFalse(any(edge in t.get('dependency_declaration', {}).get('links', [])
                             for u in full['units'] for t in u['actual']['targets']))
        unit = self.request['units'][0]
        review = copy.deepcopy(full['units'][0]['staging_review'])
        review.update(status='reviewed', reviewer='fictional regression writer',
                      render_basis='虚构样例窗口侧固定机位清楚表现站起后视线保持。',
                      timing_state_basis='虚构样例两单元站位连续，原四秒窗口及站立变化一致。')
        for c in review['change_checks']:
            c.update(status='reviewed', basis='虚构样例站起更新姿态，后镜承接原站位和窗外方向。')
        unit['staging_review'] = review
        ag.validate_staging(self.state, unit)
        before = ag.staging_material(self.state, unit)
        unit['review_dependencies']['links'][-1]['basis'] = '跨单元依据改成另一条具体说明，须重新作者核对。'
        after = ag.staging_material(self.state, unit)
        self.assertEqual(before['targets'], after['targets'])
        self.assertEqual(before['rendered'], after['rendered'])
        self.assertNotEqual(ag.digest(before), ag.digest(after))
        with self.assertRaisesRegex(ValueError, '过期'):
            ag.validate_staging(self.state, unit)

    def test_v1_dependency_material_keeps_historical_shape_with_current_preview_header(self):
        unit = self.request['units'][0]
        for i, s in enumerate(unit['shots'], 1):
            s['review_id'] = f'LEGACY{i}'
        unit['review_dependencies'] = {
            'policy': shot_dependencies.POLICY, 'complete': True,
            'basis': '原单元的窗边站位与人物视线关系保持连续。',
            'links': [{'from': 'LEGACY1', 'to': 'LEGACY2', 'kind': 'state',
                       'basis': '第一镜站起后第二镜保持同一窗边站位。'}]}
        from continuity import validate_unit
        current = copy.deepcopy(unit)
        current.setdefault('id', '待编号')
        current['black_reference_mode'] = 'none-v1'
        # New authoring previews intentionally use the visual-only header;
        # v1 dependency material retains its prior fields and target shape.
        current['header_policy'] = d.HEADER_POLICY
        historical = {
            'rendered': d.render_unit(self.state, current),
            'timing_and_state': {k: v for k, v in unit.items() if k not in
                                 ('staging_review', 'audio_preflight', 'identity_decisions', 'review_dependencies')},
            'targets': shot_review.targets(self.state, {'units': [unit]}),
            'replay': validate_unit(self.state, unit, required=True)}
        material = ag.staging_material(self.state, unit)
        self.assertNotIn('review_dependencies', material['timing_and_state'])
        self.assertEqual(material, historical)
        self.assertEqual(ag.digest(material), ag.digest(historical))


if __name__ == '__main__':
    unittest.main()
