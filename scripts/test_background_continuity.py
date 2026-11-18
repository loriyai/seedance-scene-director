"""Offline synthetic scope/continuity tests, never production semantic approval."""
import copy
import unittest
from unittest.mock import patch

import canvas
import director as d
import source_background_slots as background
import source_participant_slots as participants
import storyboard_context as context
import storyboard_reference_display as display
import storyboard_text as core
import storyboard_text_delivery as delivery
import test_storyboard_text_delivery as fixtures


class BackgroundContinuityTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.TextDeliveryIntegration('runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.project = self.fx.project
        self.fx.script = self.fx.script.replace('陈默把杯子放在桌上。', '陈默把杯子放在桌上，宾客坐在后方宴席。')
        self.state = self.fx.state
        self.state['storyboard_authoring_policy'] = 'shot-by-shot-v1'
        self.state['records'], self.state['length'] = d.build_records(self.fx.script, self.fx.ledger)
        (self.project / 'script.txt').write_text(self.fx.script, encoding='utf-8')
        (self.project / 'scenes/fixture.txt').write_text(self.fx.script, encoding='utf-8')
        self.state['files']['script']['sha256'] = d.file_hash(self.project / 'script.txt')
        self.state['drafts']['fixture.txt'] = d.sha(self.fx.script)
        self.records = [r for r in self.state['records'] if r['scene'] == 'SC0001']
        self.anchor, self.later = self.records
        self.span = [self.anchor['start'], self.later['end']]
        self.auth = self.project / 'background-authorization.json'
        self.auth_data = {'status': 'user_explicitly_approved',
            'authorized_operations': [background.FIELD],
            'items': [{'scene_ids': ['SC0001'], 'source_records': [r['id'] for r in self.records],
                       'span': self.span}]}
        d.write_json(self.auth, self.auth_data)
        self.row = {'scene_id': 'SC0001', 'asset_id': 'CHAR9990', 'name': '宾客',
            'source_records': [r['id'] for r in self.records],
            'evidence': [{'record_id': r['id'], 'quote': r['text']} for r in self.records],
            'basis': 'Synthetic background identity; not production semantics.',
            'text_sufficiency_reason': 'Synthetic anonymous seated guests.',
            'authorization': {'path': str(self.auth), 'sha256': d.file_hash(self.auth)},
            'continuity': {'anchor': {'record_id': self.anchor['id'], 'quote': '宾客坐在后方宴席。'},
                           'basis': 'Synthetic continued seating through the dialogue.'}}
        self.trace = {'policy': 'shot-by-shot-v1', 'version': 4, background.FIELD: [self.row]}
        self.text = self.fx.authored_text(self.records).replace(
            '人物：@{CHAR0001}；', '人物：@{CHAR0001}、@{CHAR9990}；').replace(
            '人物站位：@{CHAR0001}在画面左侧。',
            '人物站位：@{CHAR0001}在画面左侧，@{CHAR9990}分坐后方宴席。')
        d.write_json(self.project / 'session.json', self.state)

    def parse(self, row=None, text=None, state=None, span=None):
        return core.parse(text or self.text, state or self.state,
                          source_span=span or self.span,
                          source_background_slots=[row or self.row])

    def freeze(self, units):
        for index, unit in enumerate(units, 1):
            unit.update(id=f'U{index:04d}', version=1, style='3D国漫')
        self.state['units'] = copy.deepcopy(units)
        return units

    def carried_row(self, first):
        row = background.project([self.row], {'scene_ids': ['SC0001'],
                                             'source_records': [self.later['id']]})[0]
        row['continuity']['anchor'].update(unit_id=first['id'], version=first['version'])
        return row

    def test_presence_survives_following_dialogue_unit_and_is_not_an_image(self):
        units = self.parse()
        self.assertEqual(len(units), 2)
        self.assertTrue(all('CHAR9990' in u['character_ids'] for u in units))
        self.assertEqual(units[1][background.FIELD][0]['source_records'], [self.later['id']])
        self.assertEqual(background.from_units(units), background.normalize([self.row]))
        self.assertEqual(core.validate(self.state, units)['unit_count'], 2)
        self.freeze(units)
        rendered = core.render_unit(self.state, self.state['units'][1])
        self.assertIn('宾客', rendered)
        self.assertNotIn('@宾客', rendered)
        self.assertNotIn('CHAR9990', canvas.unit_asset_ids(self.state, self.state['units'][1]))

    def test_legacy_projection_and_participant_scope_do_not_expand(self):
        legacy = copy.deepcopy(self.row)
        legacy.pop('continuity')
        legacy['source_records'] = [self.anchor['id']]
        legacy['evidence'] = [legacy['evidence'][0]]
        destination = {'scene_ids': ['SC0001'], 'source_records': [self.later['id']]}
        self.assertEqual(background.project([legacy], destination), [])
        self.assertEqual(background.validate(self.state, [legacy], [self.anchor['id']]), [legacy])
        with self.assertRaises(ValueError):
            self.parse(legacy)
        with self.assertRaisesRegex(ValueError, '字段'):
            participants.normalize([self.row])
        no_continuity = copy.deepcopy(self.row)
        no_continuity.pop('continuity')
        with self.assertRaisesRegex(ValueError, '无延续声明须为动作'):
            background.validate(self.state, [no_continuity], [r['id'] for r in self.records])

    def test_later_candidate_requires_current_frozen_anchor(self):
        units = self.freeze(self.parse())
        row = self.carried_row(units[0])
        state = copy.deepcopy(self.state)
        state['units'] = [state['units'][0]]
        later_text = units[1]['source_text']
        later_span = [self.later['start'], self.later['end']]
        parsed = self.parse(row, later_text, state, later_span)
        self.assertEqual(parsed[0][background.FIELD][0], row)
        missing = copy.deepcopy(row)
        missing['continuity']['anchor'].pop('unit_id')
        missing['continuity']['anchor'].pop('version')
        with self.assertRaises(ValueError):
            self.parse(missing, later_text, state, later_span)
        wrong_version = copy.deepcopy(row)
        wrong_version['continuity']['anchor']['version'] += 1
        with self.assertRaises(ValueError):
            self.parse(wrong_version, later_text, state, later_span)
        state['receipts'] = [{'units': state['units']}]
        state['units'] = []
        with self.assertRaises(ValueError):
            self.parse(row, later_text, state, later_span)

    def test_anchor_cannot_borrow_future_cross_scene_or_clipped_evidence(self):
        wrong_scene = copy.deepcopy(self.row)
        other = self.state['records'][2]
        wrong_scene['continuity']['anchor'] = {'record_id': other['id'], 'quote': other['text']}
        with self.assertRaises(ValueError):
            self.parse(wrong_scene)
        wrong_kind = copy.deepcopy(self.row)
        wrong_kind['continuity']['anchor'] = {'record_id': self.later['id'], 'quote': self.later['text']}
        with self.assertRaisesRegex(ValueError, '动作原句'):
            self.parse(wrong_kind)
        state = copy.deepcopy(self.state)
        state['records'][1].update(kind='action', speaker=None)
        with self.assertRaisesRegex(ValueError, '未来'):
            background.validate(state, [wrong_kind], [r['id'] for r in self.records])
        clipped = core._slice_state(self.state, [self.anchor['start'], self.anchor['start'] + 5])
        row = background.project([self.row], {'scene_ids': ['SC0001'],
                                             'source_records': [self.anchor['id']]})[0]
        with self.assertRaisesRegex(ValueError, '动作原句'):
            background.validate(clipped, [row], [self.anchor['id']], source_state=self.state)

    def test_authorization_covers_both_establishment_and_continued_use(self):
        for only in (self.anchor, self.later):
            with self.subTest(record=only['id']):
                auth = copy.deepcopy(self.auth_data)
                auth['items'][0]['source_records'] = [only['id']]
                d.write_json(self.auth, auth)
                row = copy.deepcopy(self.row)
                row['authorization']['sha256'] = d.file_hash(self.auth)
                with self.assertRaisesRegex(ValueError, '授权的来源范围'):
                    self.parse(row)

    def test_continuity_is_not_voice_permission_or_permission_outside_declared_records(self):
        units = self.parse()
        altered = copy.deepcopy(units)
        altered[1]['shots'][0]['dialogue_parts'][0]['character_id'] = 'CHAR9990'
        with self.assertRaisesRegex(ValueError, '不授予独立声音'):
            background.require_usage(altered, [self.row])
        after = {'scene_ids': ['SC0001'], 'source_records': ['L009999']}
        self.assertEqual(background.project([self.row], after), [])
        changed = copy.deepcopy(units)
        changed[1][background.FIELD][0]['continuity']['basis'] = 'Different unreviewed continuity.'
        with self.assertRaisesRegex(ValueError, '不一致'):
            background.from_units(changed)

    def test_context_binds_distant_anchor_unit_version_and_source(self):
        units = self.freeze(self.parse())
        row = self.carried_row(units[0])
        trace = {**self.trace, background.FIELD: [row]}
        # A nearer unrelated history item must not hide the explicit anchor.
        self.state['units'].append({'id': 'U9999', 'version': 1,
                                   'span': [self.anchor['end'], self.later['start']]})
        scope = context.context_scope(self.state, [self.later['start'], self.later['end']], trace)
        selected = context._scoped_history(self.state, None, scope)['selected']
        self.assertIn('U0001', [r['value']['id'] for r in selected])
        original = context.context_hash(self.state, scope=scope)
        changed = copy.deepcopy(self.state)
        changed['units'][0]['version'] += 1
        self.assertNotEqual(original, context.context_hash(changed, scope=scope))
        changed = copy.deepcopy(self.state)
        changed['records'][0]['text'] = 'Changed establishment.'
        self.assertNotEqual(original, context.context_hash(changed, scope=scope))

    def test_frozen_candidates_validate_together_without_merging_approvals(self):
        first, second = self.freeze(self.parse())
        row = self.carried_row(first)
        row['basis'] = 'Separate candidate approval for the same guests.'
        self.state['units'] = [first]
        later = self.parse(row, second['source_text'], self.state,
                           [self.later['start'], self.later['end']])[0]
        later.update(id='U0002', version=1, style='3D国漫')
        self.state['units'].append(later)
        frozen = copy.deepcopy(self.state['units'])
        self.state['receipts'] = [{'units': frozen}]
        with self.assertRaisesRegex(ValueError, '不一致'):
            background.from_units(frozen)
        self.assertEqual(core.validate(self.state, frozen)['unit_count'], 2)
        self.auth.unlink()
        self.state['units'][0]['version'] += 1
        self.assertEqual(core.validate(self.state, frozen)['unit_count'], 2)

    def test_same_action_record_can_continue_from_frozen_earlier_slice(self):
        anchor = self.anchor
        source = copy.deepcopy(self.state)
        # Put the group establishment in the first slice of the same action.
        record = source['records'][0]
        record['text'] = '宾客坐在宴席，陈默把杯子放在桌上。'
        record['end'] = record['start'] + sum(c.isalnum() for c in record['text'])
        split = record['start'] + 6
        first_row = copy.deepcopy(self.row)
        first_row['source_records'] = [record['id']]
        first_row['evidence'] = [{'record_id': record['id'], 'quote': '宾客坐在宴席，'}]
        first_row['continuity']['anchor']['quote'] = '宾客坐在宴席'
        earlier = {'id': 'U0001', 'version': 1, 'scene_ids': ['SC0001'],
                   'character_ids': ['CHAR9990'], 'source_records': [record['id']],
                   'span': [record['start'], split], background.FIELD: [first_row]}
        source['units'] = [earlier]
        row = copy.deepcopy(first_row)
        row['continuity']['anchor'].update(unit_id='U0001', version=1)
        row['evidence'] = [{'record_id': record['id'], 'quote': '陈默把杯子放在桌上。'}]
        clipped = core._slice_state(source, [split, record['end']])
        validated = background.validate(clipped, [row], [record['id']], source_state=source)
        self.assertEqual(validated, [row])
        unanchored = copy.deepcopy(row)
        unanchored['continuity']['anchor'].pop('unit_id')
        unanchored['continuity']['anchor'].pop('version')
        with self.assertRaisesRegex(ValueError, '动作原句'):
            background.validate(clipped, [unanchored], [record['id']], source_state=source)
        wrong_slice = copy.deepcopy(source)
        wrong_slice['units'][0]['span'] = [split, record['end']]
        with self.assertRaisesRegex(ValueError, '动作原句'):
            background.validate(clipped, [row], [record['id']], source_state=wrong_slice)

    def test_frozen_single_unit_remains_renderable_after_authorization_removed(self):
        units = self.freeze(self.parse())
        before = core.render_unit(self.state, self.state['units'][1])
        self.auth.unlink()
        self.state['units'][0]['version'] += 1
        core.validate(self.state, [self.state['units'][1]])
        self.assertEqual(before, core.render_unit(self.state, self.state['units'][1]))

    def test_real_context_export_exposes_only_declared_destination(self):
        units = self.freeze(self.parse())
        self.state['units'] = [self.state['units'][0]]
        row = self.carried_row(units[0])
        trace_path = self.project / 'continued-background.json'
        d.write_json(trace_path, {**self.trace, background.FIELD: [row]})
        d.write_json(self.project / 'session.json', self.state)
        result = context.scene_context(self.project, 'SC0001',
            [self.later['start'], self.later['end']], trace_path)
        self.assertIn('CHAR9990', result['allowed_asset_ids']['characters'])
        self.assertEqual(result[background.FIELD], [row])
        self.assertEqual(result['semantic_review'], 'not_performed')

    def test_display_name_requires_explicit_same_scene_prompt_only_slot(self):
        state = background.context(self.state, [self.row])
        names = display.Names(state, core._context(state)[3])
        self.assertEqual(names.character('CHAR9990', 'SC0001'), '宾客')
        with self.assertRaisesRegex(ValueError, '缺少唯一已审源人物名称'):
            names.character('CHAR9990', 'SC0002')
        for change in ('undeclared', 'different-name', 'image'):
            altered = copy.deepcopy(state)
            if change == 'undeclared':
                altered.pop('_' + background.FIELD)
            elif change == 'different-name':
                altered['_' + background.FIELD][0]['name'] = '另一个显示名'
            else:
                altered['ledger']['characters'][-1]['visual_mode'] = 'image_asset'
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, '缺少唯一已审源人物名称'):
                display.Names(altered, core._context(altered)[3]).character('CHAR9990', 'SC0001')

    def test_bind_preview_commit_and_frozen_retry_keep_later_unit_presence(self):
        # All prose and the synthetic review below belong only to this fixture.
        text = self.text.replace('音效：',
            '景深使主角清晰、后景宾客轻度虚化；主光：左侧日光照亮左颊，'
            '辅光：墙面反射托住右颊暗部。音效：')
        draft = self.project / 'background-draft.txt'
        draft.write_text(text, encoding='utf-8')
        decisions = {**self.trace, 'units': [
            {'draft_key': 'SC0001/u01', 'shots': [{
                'shot_key': 'SC0001/u01#1', 'source_records': [self.anchor['id']],
                'viewing_decision': '虚构夹具验证落杯与后景宾客共同存在。',
                'natural_timing': '虚构测试保留落杯镜窗，不批准实际制作节奏。'}]},
            {'draft_key': 'SC0001/u02', 'shots': [{
                'shot_key': 'SC0001/u02#1', 'source_records': [self.later['id']],
                'viewing_decision': '虚构夹具验证对白单元延续背景宾客。',
                'natural_timing': '虚构测试保留对白镜窗，不批准实际制作节奏。'}]}],
            'unit_organization': {'policy': 'prefer_26_30_at_unit_boundaries_v2', 'units': [
                {'draft_key': 'SC0001/u01', 'reason_code': 'semantic_cut',
                 'reason': '隔离测试保留两个生成单元以验证背景引用投影。'},
                {'draft_key': 'SC0001/u02', 'reason_code': 'selected_range_end',
                 'reason': '虚构测试选区到此结束。'}]}}
        decisions_path, trace = self.project / 'decisions.json', self.project / 'bound.json'
        d.write_json(decisions_path, decisions)
        delivery.bind_trace(self.project, draft, decisions_path, trace, self.span)
        self.assertEqual(delivery.check_incremental(self.project, draft, self.span, trace)['status'],
                         'incremental_checked')
        state, units, info = delivery.prepare(self.project, draft, self.span, authoring_trace=trace)
        self.assertEqual(info['authoring_gate']['status'], 'passed')
        self.assertEqual(info['authoring_gate']['semantic_review'], 'not_performed')
        delivery.preview(self.project, draft, span=self.span, authoring_trace=trace)
        gate = info['authoring_gate']
        review = {key: info[key] for key in ('draft_sha256', 'context_sha256')}
        review.update(reviewer='synthetic-test-only', semantic_review='performed',
            authoring_trace_sha256=gate['trace_sha256'], authoring_gate_digest=gate['gate_digest'],
            shot_count=gate['shot_count'], reviewed_shots=gate['shot_keys'],
            scenes=[{'scene_id': 'SC0001', 'status': 'passed', 'issues': [],
                     'basis': '仅测试冻结和重试绑定，不作实际创作审阅。'}])
        review_path, output = self.project / 'review-fixture.json', self.project / 'delivered.txt'
        d.write_json(review_path, review)
        result = delivery.commit(self.project, draft, 'background-continuity-test', review_path,
                                 output, self.span, authoring_trace=trace)
        self.assertEqual(result['status'], 'committed')
        saved = d.load(self.project)
        receipt = saved['receipts'][-1]
        self.assertEqual(len(receipt['units']), 2)
        later = receipt['units'][1]
        self.assertEqual(later[background.FIELD][0]['source_records'], [self.later['id']])
        core.validate(saved, [later])
        self.assertIn('宾客', core.render_unit(saved, later))
        original = output.read_bytes()
        self.auth.unlink()
        core.validate(saved, [later])
        self.assertEqual(delivery.render_units(saved, receipt['units'], historical_receipt=receipt).encode('utf-8'), original)
        output.unlink()
        with patch.object(delivery, 'prepare', side_effect=AssertionError('retry must use frozen receipt')):
            retried = delivery.commit(self.project, draft, 'background-continuity-test',
                self.project / 'missing-review.json', output, self.span, authoring_trace=trace)
        self.assertEqual(retried['status'], 'already_committed')
        self.assertEqual(output.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
