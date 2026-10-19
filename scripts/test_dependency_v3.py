"""Opt-in typed dependencies; old protocols are never silently reinterpreted."""
import copy
import unittest
from unittest.mock import patch

import review_gate as gate
import scene_review as sr
import shot_dependencies as sd
import test_local4_upgrade as fine
import test_scene_review as legacy


def fixture():
    state, request = fine.fine_fixture()
    for unit in request['units']:
        unit['review_dependencies']['policy'] = sd.POLICY_V3
        unit['start_state'], unit['end_state'] = '车在路缘', '车仍在路缘'
    return state, request


def reviewed(state, request):
    report = legacy.reviewed(state, request)
    # Fictional scenes intentionally reset every tracked fact at scene cuts.
    for row in report.get('boundary_reviews', []):
        row.update(status='passed', reset_fields=copy.deepcopy(row['fields']),
                   basis='虚构原文转入独立停车场，首镜路缘重新建立且没有跨场持续事实。')
    return report


class TypedDependencyTests(unittest.TestCase):
    def setUp(self):
        self.rules = patch.object(sr, 'rules_digest', return_value='fictional-v3-rules')
        self.rules.start()
        self.addCleanup(self.rules.stop)
        self.state, self.request = fixture()

    def compare(self, request, original=None):
        old_request = original or self.request
        old = sr.freeze(self.state, old_request, validation_passed=True)
        report = reviewed(self.state, old_request)
        return sr.prepare_incremental(old, report, self.state, request,
            validation_passed=True, expected_report_sha256=sr.digest(report))

    def pending(self, proof):
        return {row['key'] for row in proof['shot_provenance'] if row['status'] == 'pending'}

    def link(self, kind, source='U3S1', target='U3S2'):
        self.request['units'][2]['review_dependencies']['links'].append(
            {'from': source, 'to': target, 'kind': kind, 'basis': '虚构已声明的具体承接关系'})

    def test_state_edge_does_not_read_camera_wording_but_own_shot_does(self):
        self.link('state')
        changed = copy.deepcopy(self.request)
        changed['units'][2]['shots'][0]['camera'] = '车头另一侧固定机位'
        _, proof = self.compare(changed)
        self.assertIn('3:1', self.pending(proof))
        self.assertNotIn('3:2', self.pending(proof))

    def test_state_edge_reads_replayed_exit_and_true_state_changes_propagate(self):
        self.link('state')
        changed = copy.deepcopy(self.request)
        unit = changed['units'][2]
        unit['shots'][0]['continuity']['changes'] = [
            {'entity': 'car', 'set': {'facts': {'position': 'road'}},
             'evidence': [{'record': 'L3', 'quote': '小车停稳'}]}]
        unit['continuity']['final']['car']['facts']['position'] = 'road'
        _, proof = self.compare(changed)
        self.assertTrue({'3:1', '3:2', '3:3'} <= self.pending(proof))

    def test_axis_and_occlusion_edges_keep_visual_dependencies_and_transitivity(self):
        self.link('axis', 'U3S1', 'U3S2')
        self.link('occlusion', 'U3S2', 'U3S3')
        changed = copy.deepcopy(self.request)
        changed['units'][2]['shots'][0]['camera'] = '相机越过车身轴线'
        _, proof = self.compare(changed)
        self.assertTrue({'3:1', '3:2', '3:3'} <= self.pending(proof))

    def test_actor_direction_in_text_invalidates_axis_consumer(self):
        self.link('axis')
        changed = copy.deepcopy(self.request)
        changed['units'][2]['shots'][0]['text'] = '人物转向车身另一侧，改变画面内朝向。'
        _, proof = self.compare(changed)
        self.assertIn('3:2', self.pending(proof))

    def test_typed_closure_does_not_turn_upstream_camera_into_downstream_state(self):
        self.request['units'] = self.request['units'][2:3]
        unit = self.request['units'][0]
        unit['review_dependencies']['links'] = [
            {'from': 'U3S1', 'to': 'U3S2', 'kind': 'axis', 'basis': '第二镜沿第一镜轴线'},
            {'from': 'U3S2', 'to': 'U3S3', 'kind': 'state', 'basis': '第三镜只继承第二镜退出状态'}]
        changed = copy.deepcopy(self.request)
        changed['units'][0]['shots'][0]['camera'] = '新轴线'
        _, proof = self.compare(changed)
        self.assertEqual(self.pending(proof), {'1:1', '1:2'})

    def test_exit_summary_correction_does_not_invalidate_earlier_shots(self):
        self.request['units'] = self.request['units'][2:3]
        changed = copy.deepcopy(self.request)
        changed['units'][0]['end_state'] = '修正末镜退出状态的文字'
        _, proof = self.compare(changed)
        self.assertEqual(self.pending(proof), {'1:3'})

    def test_changed_final_state_and_last_effect_do_not_backflow_to_earlier_shots(self):
        self.request['units'] = self.request['units'][2:3]
        changed = copy.deepcopy(self.request)
        unit = changed['units'][0]
        unit['shots'][-1]['continuity']['changes'] = [
            {'entity': 'car', 'set': {'facts': {'position': 'road'}},
             'evidence': [{'record': 'L3', 'quote': '小车停稳'}]}]
        unit['continuity']['final']['car']['facts']['position'] = 'road'
        unit['end_state'] = '车驶入路面'
        _, proof = self.compare(changed)
        self.assertEqual(self.pending(proof), {'1:3'})

    def test_entry_summary_is_owned_by_first_shot(self):
        self.request['units'] = self.request['units'][2:3]
        changed = copy.deepcopy(self.request)
        changed['units'][0]['start_state'] = '澄清进入时车头与路缘的关系'
        _, proof = self.compare(changed)
        self.assertEqual(self.pending(proof), {'1:1'})

    def test_unknown_kind_or_source_field_falls_back_to_full_comparison(self):
        for kind, extra in (('future_kind', False), ('state', True)):
            with self.subTest(kind=kind):
                self.request['units'][2]['review_dependencies']['links'] = []
                self.link(kind)
                if extra:
                    self.request['units'][2]['shots'][0]['future_semantic_field'] = '未分类输入'
                changed = copy.deepcopy(self.request)
                changed['units'][2]['shots'][0]['camera'] = '相机越过车身轴线'
                _, proof = self.compare(changed)
                self.assertIn('3:2', self.pending(proof))

    def test_unknown_unit_field_is_a_public_dependency(self):
        changed = copy.deepcopy(self.request)
        changed['units'][2]['future_condition'] = {'unclassified': True}
        _, proof = self.compare(changed)
        self.assertTrue({'3:1', '3:2', '3:3'} <= self.pending(proof))

    def test_v1_and_v2_keep_broad_state_edges_and_old_target_shape(self):
        for policy in (sd.POLICY, sd.POLICY_V2):
            with self.subTest(policy=policy):
                old = copy.deepcopy(self.request)
                for unit in old['units']:
                    unit['review_dependencies']['policy'] = policy
                old['units'][2]['review_dependencies']['links'] = [
                    {'from': 'U3S1', 'to': 'U3S2', 'kind': 'state',
                     'basis': '旧协议故意完整比较源镜全部摄影状态字段'}]
                snapshot = sr.freeze(self.state, old, validation_passed=True)
                self.assertNotIn('exiting_state', snapshot['targets'][0])
                self.assertIn('end_state', sd.common(old['units'][2]))
                changed = copy.deepcopy(old)
                changed['units'][2]['shots'][0]['camera'] = '旧协议机位变更'
                _, proof = self.compare(changed, old)
                self.assertIn('3:2', self.pending(proof))


if __name__ == '__main__':
    unittest.main()
