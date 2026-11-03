"""Fictional reference-permission fixtures, never production semantic approval."""
import copy
import unittest
from unittest.mock import patch

import continuity
import director as d
import storyboard_text as core
import storyboard_text_delivery as delivery
from storyboard_context import scene_context
import test_storyboard_authoring_v4 as fixtures


class CarryReferenceTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.TraceBindingIntegrationTest('runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.project = self.fx.project
        self.draft = self.fx.fixture.draft
        state = d.load(self.project)
        records = [r for r in state['records'] if r['scene'] == 'SC0002']
        self.span = [records[0]['start'], records[-1]['end']]
        text = self.draft.read_text(encoding='utf-8')
        text = '## SC0002' + text.split('## SC0002', 1)[1]
        text = text.replace('音效：', '浅景深使人物清晰、门框在焦外；主光：左侧窗光照亮手背，辅光：桌面反射托住下颌暗部。音效：')
        self.draft.write_text(text, encoding='utf-8')
        decisions = self.fx.decisions_data
        decisions['units'] = [u for u in decisions['units'] if u['draft_key'].startswith('SC0002/')]
        decisions['unit_organization']['units'] = [u for u in decisions['unit_organization']['units']
                                                  if u['draft_key'].startswith('SC0002/')]
        self.row = {'scene_id': 'SC0002', 'asset_id': 'PROP0001', 'holder_id': 'CHAR0001',
                    'anchor': {'record_id': 'L000003', 'evidence': '陈默把杯子放在桌上。'},
                    'carry_state': '测试声明的低位持物状态。',
                    'basis': '仅验证声明传递，测试不证明人物是否重新取杯或剧情连续性。'}
        decisions['continuity_props'] = [self.row]
        self.trace = None

    def bind(self, name='bound.json'):
        d.write_json(self.fx.decisions, self.fx.decisions_data)
        self.trace = self.project / name
        return delivery.bind_trace(self.project, self.draft, self.fx.decisions, self.trace, self.span)

    def prepare(self):
        return delivery.prepare(self.project, self.draft, self.span, authoring_trace=self.trace)

    def commit_fixture(self, request='carry-test'):
        _, _, info = self.prepare()
        gate = info['authoring_gate']
        review = {key: info[key] for key in ('draft_sha256', 'context_sha256')}
        review.update(reviewer='synthetic-fixture', semantic_review='performed',
                      authoring_trace_sha256=gate['trace_sha256'], authoring_gate_digest=gate['gate_digest'],
                      shot_count=gate['shot_count'], reviewed_shots=gate['shot_keys'],
                      scenes=[{'scene_id': 'SC0002', 'status': 'passed', 'issues': [],
                               'basis': '虚构测试数据，仅验证正常提交入口。'}])
        path = self.project / 'review-fixture.json'
        d.write_json(path, review)
        return delivery.commit(self.project, self.draft, request, path,
                               self.project/'delivered.txt', self.span, authoring_trace=self.trace)

    def test_passive_presence_does_not_add_visible_prop_references(self):
        self.bind()
        state, units, info = self.prepare()
        self.assertEqual(info['authoring_gate']['status'], 'passed')
        self.assertTrue(all(u['prop_ids'] == [] for u in units))
        self.assertTrue(all(u['continuity_props'] == [self.row] for u in units))
        self.assertEqual(state['ledger']['scenes'][1]['prop_ids'], [])
        core.validate(state, units)

    def test_visible_reference_works_through_check_preview_commit_and_historical_render(self):
        text = self.draft.read_text('utf-8').replace('@{CHAR0001}走到', '@{CHAR0001}手边带着@{PROP0001}走到')
        self.draft.write_text(text, encoding='utf-8')
        self.bind()
        incremental = delivery.check_incremental(self.project, self.draft, self.span, self.trace)
        self.assertEqual(incremental['status'], 'incremental_checked')
        result = delivery.preview(self.project, self.draft, span=self.span, authoring_trace=self.trace)
        self.assertEqual(result['semantic_review'], 'not_performed')
        self.assertEqual(self.commit_fixture()['status'], 'committed')
        state = d.load(self.project)
        receipt = state['receipts'][-1]
        self.assertEqual(receipt['units'][0]['prop_ids'], ['PROP0001'])
        before = (self.project/'delivered.txt').read_text('utf-8')
        self.assertEqual(delivery.render_units(state, receipt['units'], historical_receipt=receipt), before)
        core.validate(state, receipt['units'])

    def test_without_declaration_existing_scope_check_still_blocks_reference(self):
        self.draft.write_text(self.draft.read_text('utf-8').replace('@{CHAR0001}走到', '@{CHAR0001}带着@{PROP0001}走到'), encoding='utf-8')
        with self.assertRaises(core.TextValidationError):
            core.parse(self.draft.read_text('utf-8'), d.load(self.project), self.span)

    def test_invalid_identity_scope_anchor_and_legacy_trace_are_rejected(self):
        state = d.load(self.project)
        changes = [('asset_id', 'PROP9999'), ('holder_id', 'CHAR9999'), ('scene_id', 'SC0001'),
                   ('anchor', {'record_id': 'L000007', 'evidence': '陈默走到走廊尽头。'}),
                   ('anchor', {'record_id': 'L000003', 'evidence': '未发生的动作'})]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                row = copy.deepcopy(self.row)
                row[key] = value
                with self.assertRaises(ValueError):
                    continuity.validate_carry_props(state, [row], {'SC0002'})
        with self.assertRaises(ValueError):
            continuity.carry_declarations({'version': 3, 'continuity_props': [self.row]})
        with self.assertRaises(ValueError):
            continuity.validate_carry_props(state, [self.row, self.row], {'SC0002'})

    def test_context_is_read_only_and_scopes_a_multiscene_declaration_file(self):
        self.fx.decisions_data['continuity_props'].append({**self.row, 'scene_id': 'SC9999'})
        d.write_json(self.fx.decisions, self.fx.decisions_data)
        before = self.fx.fixture.snapshot()
        context = scene_context(self.project, 'SC0002', authoring_trace=self.fx.decisions)
        self.assertEqual(context['allowed_asset_ids']['props'], ['PROP0001'])
        self.assertEqual(context['continuity_props'], [self.row])
        self.assertEqual(before, self.fx.fixture.snapshot())
        ordinary = scene_context(self.project, 'SC0002')
        self.assertEqual(ordinary['allowed_asset_ids']['props'], [])

    def test_unit_anchor_version_drift_blocks_new_candidate_but_preserves_saved_validation(self):
        state = d.load(self.project)
        previous = core.parse(self.fx.fixture.authored_text(), state)[0]
        previous.update(id='old-unit', version=1)
        state['units'] = [previous]
        d.write_json(self.project/'session.json', state)
        self.row['anchor'] = {'unit_id': 'old-unit', 'version': 1,
                              'evidence': '@{CHAR0001}把@{PROP0001}放在桌上。'}
        self.bind()
        _, units, _ = self.prepare()
        frozen = copy.deepcopy(units)
        for i, unit in enumerate(frozen):
            unit.update(id=f'accepted-{i}', version=1)
        state['units'].extend(frozen)
        state['units'][0]['version'] = 2
        core.validate(state, frozen)
        self.assertTrue(scene_context(self.project, 'SC0002'))
        with self.assertRaises(ValueError):
            core.parse(self.draft.read_text('utf-8'), state, self.span, continuity_props=[self.row])

    def test_altered_declaration_changes_gate_and_request_identity(self):
        self.bind()
        self.commit_fixture()
        self.row['carry_state'] = '另一个经过作者改变的低干扰携带状态。'
        self.bind('changed.json')
        with self.assertRaisesRegex(ValueError, 'request_id已用于其他内容'):
            self.commit_fixture()

    def test_trace_change_between_parse_and_gate_is_rejected(self):
        self.bind()
        read = delivery.storyboard_authoring_gate.candidate_carry_props

        def change_after_read(*args):
            rows = read(*args)
            trace = d.read_json(self.trace)
            trace['continuity_props'][0]['carry_state'] = '解析之后改变的携带状态。'
            d.write_json(self.trace, trace)
            return rows

        with patch.object(delivery.storyboard_authoring_gate, 'candidate_carry_props', change_after_read):
            with self.assertRaisesRegex(ValueError, '跨场道具声明不一致'):
                self.prepare()

    def test_declaration_order_has_no_effect_on_reference_projection(self):
        other = copy.deepcopy(self.row)
        other['asset_id'] = 'PROP0002'
        self.fx.decisions_data['continuity_props'] = [other, self.row]
        self.bind()
        _, units, info = self.prepare()
        self.assertEqual([r['asset_id'] for r in units[0]['continuity_props']], ['PROP0001', 'PROP0002'])
        self.assertEqual(info['authoring_gate']['status'], 'passed')

    def clothing_fixture(self, anchor_kind):
        state = d.load(self.project)
        old = state['ledger']['characters'][0]
        old['appearance'] = {'identity': '陈默', 'incarnation': '当前世', 'variant': '旧服装'}
        new = copy.deepcopy(old)
        new.update(id='CHAR0002', name='陈默-新服装')
        new['appearance']['variant'] = '新服装'
        state['ledger']['characters'].append(new)
        state['ledger']['scenes'][1]['character_ids'] = ['CHAR0002']
        row = copy.deepcopy(self.row)
        row['holder_id'] = 'CHAR0002'
        if anchor_kind != 'source':
            previous = {'id': 'before-clothing', 'version': 1, 'span': [0, self.span[0]],
                        'source_text': '测试前序@{CHAR0001}携带@{PROP0001}。',
                        'character_ids': ['CHAR0001'], 'prop_ids': ['PROP0001']}
            evidence = previous['source_text']
            if anchor_kind == 'inherited':
                previous['prop_ids'] = []
                previous['source_text'] = '测试前序镜头未显示物件。'
                previous['continuity_props'] = [{**self.row, 'holder_id': 'CHAR0001'}]
                evidence = self.row['carry_state']
            state['units'] = [previous]
            row['anchor'] = {'unit_id': previous['id'], 'version': 1, 'evidence': evidence}
        return state, row

    def test_explicit_same_person_clothing_versions_preserve_carry_identity(self):
        for anchor_kind in ('source', 'unit', 'inherited'):
            with self.subTest(anchor_kind=anchor_kind):
                state, row = self.clothing_fixture(anchor_kind)
                self.assertEqual(continuity.validate_carry_props(state, [row], {'SC0002'}), [row])

    def test_name_only_missing_identity_and_other_incarnation_cannot_join_holders(self):
        for anchor_kind in ('source', 'unit', 'inherited'):
            for changed in ('identity', 'incarnation', 'missing_identity', 'missing_incarnation'):
                with self.subTest(anchor_kind=anchor_kind, changed=changed):
                    state, row = self.clothing_fixture(anchor_kind)
                    appearance = state['ledger']['characters'][1]['appearance']
                    if changed.startswith('missing_'):
                        appearance.pop(changed.removeprefix('missing_'))
                    else:
                        appearance[changed] = '同名异人或另一世'
                    with self.assertRaises(ValueError):
                        continuity.validate_carry_props(state, [row], {'SC0002'})

    def test_same_holder_id_remains_compatible_without_explicit_identity(self):
        state = d.load(self.project)
        self.assertNotIn('appearance', state['ledger']['characters'][0])
        self.assertEqual(continuity.validate_carry_props(state, [self.row], {'SC0002'}), [self.row])


if __name__ == '__main__':
    unittest.main()
