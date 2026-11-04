"""Synthetic source-reference contracts; these tests give no semantic approval."""
import copy
import unittest
from unittest.mock import patch

import director as d
import source_references as refs
import storyboard_text as core
import storyboard_text_delivery as delivery
from storyboard_context import scene_context
import test_continuity_props as carry_fixture


class SourceReferenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = carry_fixture.CarryReferenceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project, self.draft = self.fixture.project, self.fixture.draft
        self.span = self.fixture.span
        self.decisions = self.fixture.fx.decisions_data
        self.decisions.pop('continuity_props')
        self.row = {'scene_id': 'SC0002', 'asset_id': 'SET0001', 'source_records': ['L000007'],
                    'evidence': [{'record_id': 'L000007', 'quote': '陈默走到走廊尽头。'}],
                    'basis': '虚构测试只验证当前来源的既有参考资格，不判断地点同一性。'}
        self.prop = {**copy.deepcopy(self.row), 'asset_id': 'PROP0001'}
        self.decisions['source_references'] = [self.prop, self.row]
        text = self.draft.read_text('utf-8')
        text = text.replace('场景：@{SET0002}', '场景：@{SET0001}', 1)
        text = text.replace('@{CHAR0001}走到', '@{CHAR0001}带着@{PROP0001}走到', 1)
        self.draft.write_text(text, encoding='utf-8')

    def bind(self, name='source-bound.json'):
        return self.fixture.bind(name)

    def prepare(self):
        return self.fixture.prepare()

    def commit(self):
        return self.fixture.commit_fixture('source-test')

    def test_normal_context_bind_check_preview_commit_validate_render_retry(self):
        before = (self.project/'session.json').read_bytes()
        d.write_json(self.fixture.fx.decisions, self.decisions)
        context = scene_context(self.project, 'SC0002', authoring_trace=self.fixture.fx.decisions)
        self.assertEqual(context['allowed_asset_ids']['scene_assets'], ['SET0002', 'SET0001'])
        self.assertEqual(context['allowed_asset_ids']['props'], ['PROP0001'])
        self.assertEqual(context['semantic_review'], 'not_performed')
        self.bind()
        state, units, info = self.prepare()
        self.assertEqual([u['set_ids'] for u in units], [['SET0001'], ['SET0002']])
        self.assertEqual(units[0]['prop_ids'], ['PROP0001'])
        self.assertNotIn('source_references', units[1])
        self.assertEqual(units[0]['asset_ids'], ['CHAR0001', 'SET0001', 'PROP0001'])
        self.assertEqual(info['authoring_gate']['source_references'], refs.normalize([self.row, self.prop]))
        self.assertEqual((self.project/'session.json').read_bytes(), before)
        self.assertEqual(delivery.check_incremental(self.project, self.draft, self.span,
                         self.fixture.trace)['status'], 'incremental_checked')
        core.validate(state, units)
        preview = delivery.preview(self.project, self.draft, span=self.span, authoring_trace=self.fixture.trace)
        self.assertFalse(preview['preview_reused'])
        self.assertTrue(delivery.preview(self.project, self.draft, span=self.span,
                        authoring_trace=self.fixture.trace)['preview_reused'])
        self.assertEqual(self.commit()['status'], 'committed')
        saved = d.load(self.project)
        receipt = saved['receipts'][-1]
        self.assertEqual(saved['ledger'], state['ledger'])
        core.validate(saved, receipt['units'])
        original = (self.project/'delivered.txt').read_bytes()
        self.assertEqual(delivery.render_units(saved, receipt['units'], historical_receipt=receipt).encode(), original)
        (self.project/'delivered.txt').unlink()
        with patch.object(delivery, 'prepare', side_effect=AssertionError('frozen receipt only')):
            with patch.object(refs, 'validate', side_effect=AssertionError('do not reapprove old declarations')):
                result = delivery.commit(self.project, self.draft, 'source-test', self.project/'missing-review.json',
                                         self.project/'delivered.txt', self.span, authoring_trace=self.fixture.trace)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual((self.project/'delivered.txt').read_bytes(), original)

    def test_without_declaration_and_record_scope_abuse_are_rejected(self):
        state = d.load(self.project)
        with self.assertRaises(core.TextValidationError):
            core.parse(self.draft.read_text('utf-8'), state, self.span)
        text = self.draft.read_text('utf-8').replace('场景：@{SET0002}', '场景：@{SET0001}')
        with self.assertRaises(core.TextValidationError):
            core.parse(text, state, self.span, source_references=[self.row, self.prop])

    def test_bad_types_assets_scenes_records_quotes_and_v3_declarations_rejected(self):
        state = d.load(self.project)
        changes = [('asset_id', 'SET9999'), ('asset_id', 'CHAR0001'), ('scene_id', 'SC0001'),
                   ('source_records', ['L000003']), ('source_records', []),
                   ('evidence', [{'record_id': 'L000007', 'quote': '未发生的路线'}]),
                   ('evidence', []), ('basis', '')]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                row = {**copy.deepcopy(self.row), key: value}
                with self.assertRaises(ValueError):
                    refs.validate(state, [row], ['L000007'])
        with self.assertRaises(ValueError):
            refs.validate(state, [self.row, self.row], ['L000007'])
        with self.assertRaises(ValueError):
            refs.declarations({'policy': 'shot-by-shot-v1', 'version': 3, 'source_references': [self.row]})
        outside = {**self.row, 'source_records': ['L000003'],
                   'evidence': [{'record_id': 'L000003', 'quote': '陈默把杯子放在桌上。'}]}
        with self.assertRaises(ValueError):
            refs.validate(state, [outside], ['L000007'])
        with self.assertRaises(ValueError):
            refs.validate(state, [outside], ['L000003'])

    def test_actual_selected_span_cannot_borrow_quote_from_unselected_record_tail(self):
        d.write_json(self.fixture.fx.decisions, self.decisions)
        state = d.load(self.project)
        record = next(r for r in state['records'] if r['id'] == 'L000007')
        span = [record['start'], record['end'] - 2]
        with self.assertRaisesRegex(ValueError, '实际选区'):
            scene_context(self.project, 'SC0002', span, self.fixture.fx.decisions)
        with self.assertRaisesRegex(ValueError, '实际选区'):
            refs.validate(core._slice_state(state, span), [self.row], ['L000007'])

    def test_shared_action_record_projects_without_expanding_later_units(self):
        unit = {'scene_ids': ['SC0002'], 'source_records': ['L000007']}
        other = {'scene_ids': ['SC0002'], 'source_records': ['L000007', 'L000008']}
        rows = refs.validate(d.load(self.project), [self.row], ['L000007', 'L000008'])
        projected = [{**u, 'source_references': refs.project(rows, u)} for u in (unit, other)]
        self.assertEqual(refs.from_units(projected), rows)
        self.assertEqual(refs.project(rows, {'scene_ids': ['SC0002'], 'source_records': ['L000008']}), [])

    def test_parser_accepts_shared_action_record_and_preserves_projection_order(self):
        text = self.draft.read_text('utf-8')
        first, second = text.split('## SC0002 / u04', 1)
        shared = first.replace('/ u03 /', '/ shared /')
        units = core.parse(first + shared + '## SC0002 / u04' + second,
                           d.load(self.project), self.span, source_references=[self.row, self.prop])
        self.assertEqual(len(units), 3)
        self.assertEqual(units[0]['source_references'], units[1]['source_references'])
        self.assertNotIn('source_references', units[2])
        self.assertEqual(refs.from_units(units), refs.normalize([self.prop, self.row]))
        core.validate(d.load(self.project), units)

    def test_prop_only_declaration_cannot_relax_default_set_list(self):
        with self.assertRaises(core.TextValidationError):
            core.parse(self.draft.read_text('utf-8'), d.load(self.project), self.span,
                       source_references=[self.prop])

    def test_multiple_actual_sets_and_carry_can_coexist(self):
        self.decisions['continuity_props'] = [self.fixture.row]
        self.draft.write_text(self.draft.read_text('utf-8').replace('场景：@{SET0001}',
                             '场景：@{SET0001}；@{SET0002}'), encoding='utf-8')
        self.bind()
        state, units, info = self.prepare()
        self.assertEqual(units[0]['set_ids'], ['SET0001', 'SET0002'])
        self.assertEqual(units[0]['continuity_props'], [self.fixture.row])
        self.assertEqual(info['authoring_gate']['status'], 'passed')
        core.validate(state, units)

    def test_changed_basis_requires_new_preview_and_review_and_changes_request(self):
        self.bind()
        original = delivery.preview(self.project, self.draft, span=self.span, authoring_trace=self.fixture.trace)
        old_gate = original['authoring_gate']
        self.row['basis'] = '虚构测试改变了来源关系判断，需要新匹配审阅。'
        self.bind('source-changed.json')
        current = delivery.preview(self.project, self.draft, span=self.span, authoring_trace=self.fixture.trace)
        self.assertEqual(original['output_sha256'], current['output_sha256'])
        self.assertFalse(current['preview_reused'])
        self.assertNotEqual(old_gate['gate_digest'], current['authoring_gate']['gate_digest'])
        with self.assertRaises(ValueError):
            delivery.verify_review({'draft_sha256': current['draft_sha256'],
                                    'context_sha256': current['context_sha256'],
                                    'authoring_gate_digest': old_gate['gate_digest']}, current)
        self.commit()
        self.row['basis'] = '再次改变的虚构测试判断。'
        self.bind('source-changed-again.json')
        with self.assertRaisesRegex(ValueError, 'request_id已用于其他内容'):
            self.commit()

    def test_trace_race_between_parse_and_gate_is_rejected(self):
        self.bind()
        read = delivery.storyboard_authoring_gate.candidate_source_references
        def race(*args):
            rows = read(*args)
            data = d.read_json(self.fixture.trace)
            data['source_references'][0]['basis'] = '绑定之后发生的测试变化。'
            d.write_json(self.fixture.trace, data)
            return rows
        with patch.object(delivery.storyboard_authoring_gate, 'candidate_source_references', race):
            with self.assertRaisesRegex(ValueError, '来源引用声明不一致'):
                self.prepare()

    def test_forged_new_unit_cannot_use_historical_render_authority(self):
        self.bind()
        self.commit()
        state = d.load(self.project)
        unit = copy.deepcopy(state['units'][0])
        unit['source_references'][0]['basis'] = '冒充历史的未审新判断。'
        with self.assertRaisesRegex(ValueError, 'authoring_gate'):
            core.render_unit(state, unit)


if __name__ == '__main__':
    unittest.main()
