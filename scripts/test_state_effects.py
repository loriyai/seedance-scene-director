"""Fictional behavior tests for explicit effects, not inferred Chinese events."""
import copy
import re
import unittest

import authoring_gates as ag
import continuity
import director
import state_effects as effects
import storyboard_assembly as assembly
import storyboard_preflight as preflight
import test_audit_director as fixtures


class ActionStateEffectsTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.AuditDirectorChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.state = fixture.state
        self.unit = fixture.unit([0, 9])
        self.record = self.state['records'][0]['id']
        self.unit['action_plan'] = [{
            'action_id': 'stand', 'record': self.record, 'quote': '站起', 'start': .2, 'end': 2.7,
            'milestones': {'upright': 1.5},
            'state_targets': [{'entity': 'CHAR0001', 'fields': ['facts.姿态']}]}]
        self.unit['continuity']['initial']['CHAR0001']['facts'] = {'姿态': '坐着'}
        self.unit['action_state_plan'] = {'policy': effects.POLICY, 'effects': [
            {'effect_id': 'upright', 'action_id': 'stand', 'at': 'upright',
             'entity': 'CHAR0001', 'set': {'facts': {'姿态': '站立'}}}]}
        self.unit = effects.clear_derived(self.unit)

    def compile(self):
        return effects.compile_unit(self.unit, self.state)

    def test_cross_shot_action_changes_once_at_milestone_and_not_again_at_end(self):
        before = copy.deepcopy(self.unit)
        compiled = self.compile()
        changes = [shot['continuity']['changes'] for shot in compiled['shots']]
        self.assertEqual([len(items) for items in changes], [0, 1, 0, 0])
        self.assertEqual(changes[1][0]['evidence'], [{'record': self.record, 'quote': '站起'}])
        self.assertEqual(compiled['continuity']['final']['CHAR0001']['facts'], {'姿态': '站立'})
        self.assertIn('姿态=坐着', compiled['start_state'])
        self.assertIn('姿态=站立', compiled['end_state'])
        self.assertEqual(self.unit, before)
        self.assertEqual(director.validate_unit(self.state, compiled, [0, 9]), [])

    def test_start_and_end_at_cut_have_defined_ownership(self):
        action = self.unit['action_plan'][0]
        action.update(start=1, end=2, milestones={})
        effect = self.unit['action_state_plan']['effects'][0]
        for anchor, shot_index in [('start', 1), ('end', 1)]:
            with self.subTest(anchor=anchor):
                effect['at'] = anchor
                compiled = self.compile()
                self.assertEqual([len(s['continuity']['changes']) for s in compiled['shots']],
                                 [int(i == shot_index) for i in range(4)])

    def test_milestone_at_cut_requires_author_side(self):
        self.unit['action_plan'][0]['milestones']['upright'] = 2
        with self.assertRaisesRegex(ValueError, 'cut_side'):
            self.compile()
        effect = self.unit['action_state_plan']['effects'][0]
        for side, index in [('before', 1), ('after', 2)]:
            effect['cut_side'] = side
            compiled = self.compile()
            self.assertEqual([len(s['continuity']['changes']) for s in compiled['shots']],
                             [int(i == index) for i in range(4)])

    def test_explicit_impact_commitment_cannot_omit_position_effect(self):
        action = self.unit['action_plan'][0]
        action['state_targets'][0]['fields'].append('facts.位置')
        with self.assertRaisesRegex(ValueError, '没有对应效果.*位置'):
            self.compile()
        # A stationary action may explicitly affect pose only. No motion word
        # creates a position effect: the declared commitment is authoritative.
        action['state_targets'][0]['fields'].remove('facts.位置')
        compiled = self.compile()
        self.assertNotIn('位置', compiled['continuity']['final']['CHAR0001']['facts'])

    def test_effects_cannot_silently_add_undeclared_impact_fields(self):
        self.unit['action_state_plan']['effects'][0]['set']['facts']['位置'] = '窗边'
        with self.assertRaisesRegex(ValueError, '未列入.*state_targets'):
            self.compile()

    def test_declared_position_noop_cannot_hide_beside_a_real_pose_change(self):
        self.unit['continuity']['initial']['CHAR0001']['facts']['位置'] = '原停车点'
        self.unit['action_plan'][0]['state_targets'][0]['fields'].append('facts.位置')
        self.unit['action_state_plan']['effects'][0]['set']['facts']['位置'] = '原停车点'
        with self.assertRaisesRegex(ValueError, 'facts.位置.*未改变状态'):
            self.compile()

    def test_source_anchor_and_action_time_fail_closed(self):
        action = self.unit['action_plan'][0]
        action['quote'] = '原文没有的跳跃动作'
        with self.assertRaisesRegex(ValueError, '证据不在'):
            self.compile()
        action['quote'] = '站起'
        self.unit['action_state_plan']['effects'][0]['at'] = 'unknown'
        with self.assertRaisesRegex(ValueError, '锚点不存在'):
            self.compile()
        self.unit['action_state_plan']['effects'][0]['at'] = 'upright'
        action['milestones']['upright'] = 3.5
        with self.assertRaisesRegex(ValueError, '里程点须在动作内部'):
            self.compile()

    def test_action_to_action_and_audio_part_order_use_exact_endpoints(self):
        self.unit['action_plan'].append({'action_id': 'look', 'record': self.record, 'quote': '看向窗外',
                                        'start': 2.7, 'end': 3.8, 'state_targets': []})
        self.unit['audio'] = [{'record': 'D1', 'text': '甲。乙。', 'start': 0, 'end': 4}]
        self.unit['audio_plan'] = [{'record': 'D1', 'parts': [
            {'text': '甲。', 'start': 0, 'end': 1}, {'text': '乙。', 'start': 3.8, 'end': 4}]}]
        self.unit['action_state_plan']['order'] = [
            {'before': {'action_id': 'stand', 'at': 'end'}, 'after': {'action_id': 'look', 'at': 'start'}},
            {'before': {'action_id': 'look', 'at': 'end'}, 'after': {'record': 'D1', 'part': 2, 'edge': 'start'}}]
        self.assertEqual(effects.validate_plan(self.state, self.unit)['policy'], effects.POLICY)
        self.unit['audio_plan'][0]['parts'][1]['start'] = 3.5
        with self.assertRaisesRegex(ValueError, '端点时序矛盾'):
            self.compile()

    def test_legacy_whole_audio_before_after_keep_their_meaning(self):
        self.unit['audio'] = [{'record': 'D1', 'start': 2.7, 'end': 4}]
        self.unit['action_plan'][0]['before'] = 'D1'
        self.compile()
        self.unit['audio'][0]['start'] = 2.6
        with self.assertRaisesRegex(ValueError, '整句声音先后约束冲突'):
            self.compile()

    def test_sustained_contact_survives_action_end_and_continuous_unit(self):
        self.unit['action_plan'][0].update(start=.2, end=1, milestones={},
                                          state_targets=[{'entity': 'CHAR0001', 'fields': ['facts.接触']}])
        effect = self.unit['action_state_plan']['effects'][0]
        effect.update(at='end', set={'facts': {'接触': '持续拥抱同伴'}})
        compiled = self.compile()
        self.assertEqual(compiled['continuity']['final']['CHAR0001']['facts']['接触'], '持续拥抱同伴')
        following = copy.deepcopy(compiled)
        following['span'] = [9, 18]
        following['action_plan'] = []
        following['action_state_plan']['effects'] = []
        following['continuity']['initial'] = copy.deepcopy(compiled['continuity']['final'])
        following = effects.recompile_unit(following, self.state)
        self.assertEqual(following['continuity']['final']['CHAR0001']['facts']['接触'], '持续拥抱同伴')
        continuity.validate_chain(self.state, [compiled, following], {0, 1})

    def test_release_is_an_explicit_effect_and_facts_null_still_works(self):
        self.unit['continuity']['initial']['CHAR0001']['facts']['接触'] = '持续拥抱同伴'
        self.unit['action_plan'][0]['state_targets'][0]['fields'].append('facts.接触')
        self.unit['action_state_plan']['effects'].append({
            'effect_id': 'release', 'action_id': 'stand', 'at': 'end', 'entity': 'CHAR0001',
            'set': {'facts': {'接触': None}}})
        compiled = self.compile()
        self.assertNotIn('接触', compiled['continuity']['final']['CHAR0001']['facts'])
        self.assertEqual(compiled['shots'][2]['continuity']['changes'][0]['set']['facts'], {'接触': None})

    def relative_scenario(self):
        contract = self.unit['continuity']
        contract['entities']['observer'] = {'asset_ids': ['CHAR0002']}
        contract['initial']['observer'] = {'present': True, 'asset_id': 'CHAR0002',
                                          'facts': {'位置': '陈默面前'}}
        contract['initial']['CHAR0001']['facts']['位置'] = '原处'
        self.state['ledger']['characters'].append({'id': 'CHAR0002', 'name': '旁观者'})
        for shot in self.unit['shots']:
            shot['continuity']['visibility']['observer'] = {'mode': 'offscreen', 'basis': '虚构旁观者仍在镜头外的门边'}
        self.unit['action_plan'][0]['state_targets'][0]['fields'] = ['facts.位置']
        effect = self.unit['action_state_plan']['effects'][0]
        effect.update(set={'facts': {'位置': '窗边'}})
        self.unit['action_state_plan']['relations'] = [{
            'relation_id': 'relative-position', 'entity': 'observer', 'fact': '位置',
            'depends_on': [{'entity': 'CHAR0001', 'fields': ['facts.位置']}]}]

    def test_relative_position_fails_when_reference_moves_until_relation_updated(self):
        self.relative_scenario()
        with self.assertRaisesRegex(ValueError, '参照状态变化后关系须同刻更新'):
            self.compile()
        self.unit['action_plan'][0]['state_targets'].append({'entity': 'observer', 'fields': ['facts.位置']})
        self.unit['action_state_plan']['effects'].append({
            'effect_id': 'observer-reference', 'action_id': 'stand', 'at': 'upright', 'entity': 'observer',
            'set': {'facts': {'位置': '原门边固定位置'}}})
        compiled = self.compile()
        self.assertEqual(compiled['continuity']['final']['observer']['facts']['位置'], '原门边固定位置')
        self.assertEqual(len(compiled['shots'][1]['continuity']['changes']), 2)

    def test_explicit_relation_retain_is_bound_to_exact_change_not_future_exemption(self):
        self.relative_scenario()
        try:
            self.compile()
        except ValueError as error:
            binding = re.search(r'trigger_sha256=([0-9a-f]{64})', str(error)).group(1)
        else:
            self.fail('relationship must require review')
        self.unit['action_state_plan']['relation_checks'] = [{
            'relation_id': 'relative-position', 'trigger_effect_ids': ['upright'], 'trigger_sha256': binding,
            'decision': 'retain', 'basis': '虚构测试中两人同向移动，原相对位置仍成立，实际正文另需作者核对。'}]
        self.compile()
        self.unit['action_state_plan']['effects'][0]['set']['facts']['位置'] = '窗外'
        with self.assertRaisesRegex(ValueError, '参照状态变化后关系须同刻更新'):
            self.compile()

    def test_unused_relation_check_is_rejected_and_removed_relation_does_not_linger(self):
        self.relative_scenario()
        self.unit['action_state_plan']['relation_checks'] = [{
            'relation_id': 'relative-position', 'trigger_effect_ids': ['upright'], 'trigger_sha256': 'old',
            'decision': 'retain', 'basis': '旧稿说明'}]
        self.unit['action_plan'][0]['state_targets'].append({'entity': 'observer', 'fields': ['facts.位置']})
        self.unit['action_state_plan']['effects'].append({
            'effect_id': 'end-relation', 'action_id': 'stand', 'at': 'upright', 'entity': 'observer',
            'set': {'facts': {'位置': None}}})
        with self.assertRaisesRegex(ValueError, '重复、过期或未被'):
            self.compile()
        del self.unit['action_state_plan']['relation_checks']
        self.assertNotIn('位置', self.compile()['continuity']['final']['observer']['facts'])

    def test_unchanged_relation_cannot_bypass_retain_with_an_unrelated_change(self):
        self.relative_scenario()
        before = self.unit['continuity']['initial']['observer']['facts']['位置']
        self.unit['action_plan'][0]['state_targets'].append({
            'entity': 'observer', 'fields': ['facts.位置', 'facts.姿态']})
        self.unit['action_state_plan']['effects'].append({
            'effect_id': 'fake-relation-update', 'action_id': 'stand', 'at': 'upright',
            'entity': 'observer', 'set': {'facts': {'位置': before, '姿态': '直立'}}})
        with self.assertRaisesRegex(ValueError, 'facts.位置.*未改变状态'):
            self.compile()

    def test_conflicting_derived_values_fail_and_recompile_preserves_input(self):
        compiled = self.compile()
        changed = copy.deepcopy(compiled)
        changed['action_state_plan']['effects'][0]['set']['facts']['姿态'] = '站直'
        before = copy.deepcopy(changed)
        with self.assertRaisesRegex(ValueError, 'continuity.changes'):
            effects.compile_unit(changed, self.state)
        updated = effects.recompile_unit(changed, self.state)
        self.assertEqual(changed, before)
        self.assertEqual(updated['continuity']['final']['CHAR0001']['facts']['姿态'], '站直')
        self.assertEqual(updated['shots'][0]['text'], compiled['shots'][0]['text'])
        self.assertEqual(updated['action_plan'], compiled['action_plan'])

    def test_missing_or_forged_compilation_fails_in_continuity_and_director(self):
        compiled = self.compile()
        compiled['shots'][1]['continuity']['changes'] = []
        compiled['continuity']['final'] = copy.deepcopy(compiled['continuity']['initial'])
        compiled['end_state'] = compiled['start_state']
        for function in (lambda: continuity.validate_unit(self.state, compiled),
                         lambda: director.validate_unit(self.state, compiled, [0, 9])):
            with self.assertRaisesRegex(ValueError, '权威action_state_plan矛盾'):
                function()
        compiled.pop('continuity')
        with self.assertRaisesRegex(ValueError, 'continuity.final'):
            continuity.validate_unit(self.state, compiled, required=False)

    def test_audio_readiness_validates_prephotography_plan_and_binds_effect_changes(self):
        planned = copy.deepcopy(self.unit)
        planned.pop('shots')
        planned['audio_plan'] = []
        first = ag.audio_ready(self.state, planned, preflight.check_audio_plan(planned))
        self.assertNotIn('ACTION_STATE_PLAN', {item['code'] for item in first['errors']})
        self.assertEqual(first['status'], 'blocked')  # no semantic approval invented
        planned['audio_preflight'] = copy.deepcopy(first['semantic_template'])
        planned['audio_preflight'].update(reviewer='fictional author', basis='虚构人物站起后保持站姿，动作窗口包含起身过程。')
        self.assertEqual(ag.audio_ready(self.state, planned, preflight.check_audio_plan(planned))['status'], 'ready')
        planned['action_state_plan']['effects'][0]['set']['facts']['姿态'] = '站直'
        changed = ag.audio_ready(self.state, planned, preflight.check_audio_plan(planned))
        self.assertNotEqual(changed['input_sha256'], first['input_sha256'])
        self.assertEqual(changed['status'], 'blocked')
        planned['action_plan'][0]['state_targets'][0]['fields'].append('facts.位置')
        invalid = ag.audio_ready(self.state, planned, preflight.check_audio_plan(planned))
        self.assertIn('ACTION_STATE_PLAN', {item['code'] for item in invalid['errors']})

    def test_assembly_builds_times_before_effects_and_summaries_before_text_checks(self):
        for shot in self.unit['shots']:
            shot['duration'] = shot.pop('end') - shot.pop('start')
        self.unit.pop('duration')
        assembled = assembly.assemble_unit(self.unit)
        self.assertEqual(assembled['duration'], 4)
        self.assertEqual(len(assembled['shots'][1]['continuity']['changes']), 1)
        self.assertIn('姿态=站立', assembled['end_state'])
        self.assertEqual(director.validate_unit(self.state, assembled, [0, 9]), [])

    def test_action_readiness_binds_entry_and_entities_but_not_camera(self):
        planned = copy.deepcopy(self.unit)
        planned['audio_plan'] = []
        first = ag.audio_ready(self.state, planned, preflight.check_audio_plan(planned))
        planned['audio_preflight'] = copy.deepcopy(first['semantic_template'])
        planned['audio_preflight'].update(reviewer='fictional author', basis='人物从坐姿起身并站稳，进入条件和动作时窗已逐项核对。')
        self.assertEqual(ag.audio_ready(self.state, planned, preflight.check_audio_plan(planned))['status'], 'ready')
        for field in ('initial', 'entities', 'camera'):
            changed = copy.deepcopy(planned)
            if field == 'initial':
                changed['continuity']['initial']['CHAR0001']['facts']['位置'] = '另一处起身位置'
            elif field == 'entities':
                changed['continuity']['entities']['CHAR0001']['asset_ids'].append('CHAR0002')
            else:
                changed['shots'][0]['camera'] = '从新侧面观察同一动作'
            with self.subTest(field=field):
                checked = ag.audio_ready(self.state, changed, preflight.check_audio_plan(changed))
                self.assertEqual(checked['input_sha256'] == first['input_sha256'], field == 'camera')
                self.assertEqual(checked['status'], 'ready' if field == 'camera' else 'blocked')

    def test_empty_effects_can_rebuild_initial_and_final_summaries(self):
        self.unit['action_plan'] = []
        self.unit['action_state_plan']['effects'] = []
        compiled = self.compile()
        self.assertEqual(compiled['continuity']['initial'], compiled['continuity']['final'])
        self.assertEqual(compiled['start_state'], compiled['end_state'])

    def test_duplicate_same_time_writes_and_unknown_policy_fail_closed(self):
        duplicate = copy.deepcopy(self.unit['action_state_plan']['effects'][0])
        duplicate['effect_id'] = 'second'
        self.unit['action_state_plan']['effects'].append(duplicate)
        with self.assertRaisesRegex(ValueError, '同一时点'):
            self.compile()
        self.unit['action_state_plan']['policy'] = 'unknown-version'
        with self.assertRaisesRegex(ValueError, '未知状态效果协议'):
            self.compile()

    def test_legacy_compilation_and_validation_are_unchanged(self):
        fixture = fixtures.AuditDirectorChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        legacy = fixture.unit([0, 9])
        before = copy.deepcopy(legacy)
        self.assertEqual(effects.compile_unit(legacy, fixture.state), before)
        self.assertEqual(effects.clear_derived(legacy), before)
        self.assertIsNone(effects.validate_plan(fixture.state, legacy))
        self.assertIsNone(effects.validate_compiled(fixture.state, legacy))
        self.assertEqual(director.validate_unit(fixture.state, legacy, [0, 9]), [])
        self.assertNotIn('action_state_plan', ag.audio_payload(fixture.state, legacy))
        self.assertEqual(legacy, before)


if __name__ == '__main__':
    unittest.main()
