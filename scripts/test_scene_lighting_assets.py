"""Lighting asset identity, spatial inheritance, and real canvas dependencies."""
import copy
import tempfile
import unittest
from pathlib import Path
import sys

MODULE = Path(__file__).resolve().parents[1] / 'modules/screenplay-standardizer/scripts'
sys.path.insert(0, str(MODULE))
import asset_ledger as ledger
import asset_visual_design as design
import scene_lighting as lighting
import design_workspace
import test_audit_temporal_assets as temporal


class SceneLightingAssets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        temporal.WORK = Path(self.tmp.name)

    def fixture(self, states):
        directory, paragraphs, data = temporal.fixture('project', ['人物穿过空间。'] * len(states))
        data['scene_lighting_policy'] = lighting.POLICY
        for scene, state in zip(data['scenes'], states):
            if state is not None:
                scene['scene_lighting'] = {'state': state, 'kind': '制作选择', 'basis': '已确认当前场的实际光照状态'}
        return directory, paragraphs, data

    def validate(self, directory, p, data):
        ledger.validate(ledger.parse_script(directory / 'standard.txt'), p, data)

    def test_day_night_reuse_and_night_first_dependency_order(self):
        directory, p, data = self.fixture(['night', 'day', 'night'])
        result = ledger.build_scene_assets(data, p)
        day, night = result['scene_assets']
        self.assertEqual([a['lighting_state'] for a in result['scene_assets']], ['day', 'night'])
        self.assertEqual(night['image_reference_asset_id'], day['id'])
        self.assertEqual(night['structure_asset_id'], day['id'])
        self.assertEqual(result['scenes'][0]['scene_asset_id'], result['scenes'][2]['scene_asset_id'])
        self.assertEqual(result, ledger.build_scene_assets(result, p))
        self.validate(directory, p, result)

    def test_night_only_does_not_create_day(self):
        directory, p, data = self.fixture(['night', 'night'])
        result = ledger.build_scene_assets(data, p)
        self.assertEqual(len(result['scene_assets']), 1)
        self.assertEqual(result['scene_assets'][0]['lighting_state'], 'night')
        self.assertNotIn('image_reference_asset_id', result['scene_assets'][0])
        self.validate(directory, p, result)

    def test_format_default_is_not_day_and_legacy_is_unchanged(self):
        directory, p, data = self.fixture([None, None])
        result = ledger.build_scene_assets(data, p)
        self.assertEqual(result['scene_assets'][0]['lighting_state'], 'unspecified')
        self.assertEqual(result['scene_assets'][0]['name'], '陈家老宅')
        self.validate(directory, p, result)
        data.pop('scene_lighting_policy')
        old = ledger.build_scene_assets(data, p)
        self.assertNotIn('lighting_state', old['scene_assets'][0])
        self.validate(directory, p, old)

    def test_source_time_cannot_be_overridden(self):
        _, _, data = self.fixture(['night'])
        scene = data['scenes'][0]
        scene['time_basis']['kind'] = '原文明示'
        with self.assertRaisesRegex(ValueError, '冲突'):
            lighting.scene_state(scene)

    def test_temporal_versions_have_separate_lighting_families(self):
        directory, p, data = temporal.fixture('temporal', ['旧日空间。', '夜晚停留。', '三年后，他回来。', '夜晚再次停留。'])
        data['scene_lighting_policy'] = lighting.POLICY
        temporal.layer(data, 'T1', '三年后', 3)
        for scene, state in zip(data['scenes'], ('day', 'night', 'day', 'night')):
            scene['scene_lighting'] = {'state': state, 'kind': '制作选择', 'basis': '已确认光照'}
        result = ledger.build_scene_assets(data, p)
        self.assertEqual(len(result['scene_assets']), 4)
        catalog = {a['id']: a for a in result['scene_assets']}
        for a in result['scene_assets']:
            if a['lighting_state'] == 'night':
                self.assertEqual(a['structure_asset_id'], a['image_reference_asset_id'])
                self.assertEqual(catalog[a['structure_asset_id']]['lighting_state'], 'day')
        self.validate(directory, p, result)

    def designed(self):
        directory, p, data = self.fixture(['day', 'night'])
        data = ledger.build_scene_assets(data, p)
        day, night = data['scene_assets']
        base = {'attribution': '制作设计', 'basis': '同一空间不同日夜光照', 'reviewed_source_facts': True,
                'space': '入口至后墙之间保留连续活动区', 'materials_palette': '灰色石材与暗色木材',
                'furnishings': '后墙沿墙固定木架', 'lighting': '日间自然光照亮活动区'}
        changed = {**base, 'lighting': '夜间环境光减弱，原有灯光照亮活动区',
                   'image_reference': {'base_asset_id': day['id'], 'changes': ['环境转为夜晚，减弱自然光'],
                                       'preserve': ['空间布局、固定陈设和材质本色']}}
        data['image_design_version'] = design.DESIGN_VERSION
        data['visual_style'] = {'text': '写实', 'digest': design.fingerprint('写实')}
        day['visual_design'], night['visual_design'] = base, changed
        return data

    def test_night_compiles_real_set_reference_and_binds_parent_design(self):
        data = self.designed()
        day, night = data['scene_assets']
        design.validate_image_references(data)
        prompt, form = design.compile_prompt(data, 'scene_assets', night, night['visual_design'], {})
        self.assertIn('@' + day['name'] + ' ', prompt)
        self.assertNotIn('纯白背景', prompt)
        self.assertEqual(form['reference_asset_ids'], [day['id']])
        night['image_format'] = form
        self.assertEqual(design.image_reference_asset_ids(night), [day['id']])
        before = design.source_context_digest(data, 'scene_assets', night)
        day['visual_design']['lighting'] += '；从已确认入口方向照入'
        self.assertNotEqual(before, design.source_context_digest(data, 'scene_assets', night))
        hard, _, _ = design_workspace._dependencies(data, {})
        self.assertIn(day['id'], hard[night['id']])

    def test_wrong_structure_missing_dependency_and_layout_drift_rejected(self):
        data = self.designed()
        day, night = data['scene_assets']
        night['visual_design']['space'] = '入口换到另一面墙'
        with self.assertRaisesRegex(ValueError, '继承日景space'):
            design.validate_image_references(data)
        night['visual_design']['space'] = day['visual_design']['space']
        night['structure_asset_id'] = 'SET9999'
        with self.assertRaisesRegex(ValueError, '同结构'):
            design.validate_image_references(data)
        night['structure_asset_id'] = day['id']
        night['visual_design'].pop('image_reference')
        with self.assertRaisesRegex(ValueError, 'image_reference'):
            design.validate_image_references(data)

    def test_undesignated_catalog_keeps_current_structure_facts(self):
        assets = {
            'SET0001': {'id': 'SET0001', 'location_id': 'LOC0001', 'variant': 'base', 'visual_facts': ['固定入口']},
            'SET0002': {'id': 'SET0002', 'location_id': 'LOC0001', 'variant': 'decor', 'visual_facts': ['修复后的屋顶']},
            'SET0003': {'id': 'SET0003', 'location_id': 'LOC0001', 'variant': 'lighting',
                        'structure_asset_id': 'SET0002', 'visual_facts': []}}
        locations = {'LOC0001': {'visual_facts': ['石制围墙']}}
        self.assertEqual(ledger.set_visual_facts(assets['SET0003'], locations, assets),
                         ['石制围墙', '固定入口', '修复后的屋顶'])

    def test_changed_frozen_mapping_rejected(self):
        _, p, data = self.fixture(['night'])
        result = ledger.build_scene_assets(data, p)
        result['scenes'][0]['scene_lighting']['state'] = 'day'
        with self.assertRaisesRegex(ValueError, '迁移'):
            ledger.build_scene_assets(result, p)


if __name__ == '__main__':
    unittest.main()
