"""Offline checks of user reports: empty images are not failed node references."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
import canvas as c
import canvas_reference_report as report


class ReferenceReportChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        self.delivery = Path(self.temp.name) / 'outputs' / '分镜.txt'
        self.delivery.parent.mkdir()
        self.delivery.write_text('@Actor @Room @Pen 原始提示词\n', encoding='utf-8')
        self.unit = {'id': 'V', 'character_ids': ['C'], 'scene_ids': ['SC'], 'prop_ids': ['P'],
                     'global': '', 'shots': []}
        self.session = {'ledger': {'schema_version': 3, 'characters': [{'id': 'C', 'name': 'Actor'}],
            'scene_assets': [{'id': 'SET', 'name': 'Room'}], 'props': [{'id': 'P', 'name': 'Pen'}],
            'scenes': [{'id': 'SC', 'scene_asset_id': 'SET', 'prop_ids': ['P']}]},
            'units': [self.unit], 'receipts': [{'output': str(self.delivery)}]}
        self.doc = {'rootCanvasId': 'ROOT', 'assets': {
            'ROOT': {'type': 'canvas', 'content': {'nodes': {
                'R': {'type': 'biz/role'}, 'S': {'type': 'biz/scene'}, 'I': {'type': 'biz/image'},
                'B': {'type': 'biz/image'}, 'V': {'type': 'biz/video'}},
                'edges': {source+'-V': {'source': source, 'target': 'V', 'type': 'reference'} for source in ('R','S','I','B')},
                'settings': {'readOnly': False}}},
            'R': {'content': {'basicInfo': {'name': 'Actor'}, 'appearanceInfo': {}}},
            'S': {'content': {'basicInfo': {'name': 'Room'}, 'appearanceInfo': {}}},
            'I': {'content': {'name': 'Pen'}}, 'B': {'content': {'name': '黑幕', 'pippitAssetId': 'BLACK_IMAGE'}},
            'V': {'content': {'name': 'V', 'generation': {'prompt': ' '.join('<node-asset>'+s+'</node-asset>' for s in ('R','S','I','B')),
                                                        'references': []}}}}}
        self.state = c.blank_state()
        self.state.update(binding={'canvas_id': 'ROOT'}, black_node_id='B', mappings={
            'C': {'node_id': 'R', 'kind': 'role', 'name': 'Actor'},
            'SET': {'node_id': 'S', 'kind': 'scene', 'name': 'Room'},
            'P': {'node_id': 'I', 'kind': 'image', 'name': 'Pen'},
            'V': {'node_id': 'V', 'kind': 'video', 'name': 'V'}})

    def issues(self):
        return report.collect_issues(self.session, self.state, self.doc, [self.unit])

    def test_empty_images_are_reported_without_failing_valid_node_edges(self):
        issues = self.issues()
        self.assertEqual({(i['key'], i['reason']) for i in issues},
                         {('C', 'missing_image'), ('SET', 'missing_image'), ('P', 'missing_image')})

    def test_media_added_to_role_requires_its_draft_reference_row(self):
        self.doc['assets']['R']['content']['appearanceInfo'] = {'pippitAssetId': 'ACTOR_IMAGE'}
        issues = self.issues()
        self.assertIn(('C', 'role_media_reference_missing'), {(i['key'], i['reason']) for i in issues})
        self.assertNotIn(('C', 'missing_image'), {(i['key'], i['reason']) for i in issues})
        self.doc['assets']['V']['content']['generation']['references'] = [{'sourceNodeId': 'R', 'type': 'image'}]
        self.assertNotIn('role_media_reference_missing', {i['reason'] for i in self.issues()})

    def test_unmapped_asset_and_missing_edge_have_distinct_causes(self):
        del self.state['mappings']['SET']
        del self.doc['assets']['ROOT']['content']['edges']['I-V']
        issues = {(i['key'], i['reason']) for i in self.issues()}
        self.assertIn(('SET', 'missing_node'), issues)
        self.assertIn(('P', 'reference_edge_missing'), issues)

    def test_report_and_backend_record_preserve_original_prompt_and_allow_repair(self):
        original = self.delivery.read_bytes()
        result = report.write_reference_report(self.project, self.session, self.state, self.doc, [self.unit],
                    failures=[{'key': 'V', 'reason': 'reference_failed', 'message': '官方接口拒绝引用'}])
        self.assertEqual(self.delivery.read_bytes(), original)
        self.assertEqual(Path(result['report']).parent, self.delivery.parent)
        text = Path(result['report']).read_text(encoding='utf-8')
        self.assertIn('缺少图片的资产节点：3项', text)
        self.assertIn('角色节点', text)
        self.assertIn('场景节点', text)
        self.assertIn('道具图片节点', text)
        self.assertIn('官方接口拒绝引用', text)
        saved = json.loads((self.project/'canvas'/'reference-check.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['report'], result['report'])
        self.assertEqual(saved['missing_image_count'], 3)
        self.doc['assets']['R']['content']['appearanceInfo'] = {'pippitAssetId': 'ACTOR_IMAGE'}
        self.doc['assets']['S']['content']['appearanceInfo'] = {'pippitAssetId': 'ROOM_IMAGE'}
        self.doc['assets']['I']['content']['pippitAssetId'] = 'PEN_IMAGE'
        self.doc['assets']['V']['content']['generation']['references'] = [{'sourceNodeId': 'R', 'type': 'image'}]
        repaired = report.write_reference_report(self.project, self.session, self.state, self.doc, [self.unit])
        self.assertEqual(repaired['issues'], [])
        self.assertEqual(repaired['missing_image_count'], 0)

    def test_explicit_report_target_cannot_overwrite_committed_prompt(self):
        with self.assertRaisesRegex(ValueError, '不能覆盖'):
            report.write_reference_report(self.project, self.session, self.state, self.doc, [self.unit], output=self.delivery)


if __name__ == '__main__':
    unittest.main()
