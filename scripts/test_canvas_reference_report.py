"""Offline checks of user reports: empty images are not failed node references."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

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

    def test_black_video_media_is_separate_from_images_and_reference_failures(self):
        c.nodes(self.doc)['B']['type'] = 'biz/video'
        c.content(self.doc, 'B').pop('pippitAssetId')
        self.state['black_node_kind'] = 'video'
        result = report.write_reference_report(self.project, self.session, self.state, self.doc, [self.unit])
        self.assertEqual(result['missing_image_count'], 3)
        self.assertEqual(result['missing_video_count'], 1)
        self.assertEqual(result['reference_issue_count'], 0)
        text = Path(result['report']).read_text(encoding='utf-8')
        self.assertIn('黑幕视频素材待补：1项', text)
        self.assertNotIn('黑幕图片', text)
        c.content(self.doc, 'B')['pippitAssetId'] = 'BLACK_VIDEO'
        self.assertNotIn('missing_video', {i['reason'] for i in self.issues()})

    def test_legacy_unit_image_pin_is_preserved_after_project_default_changes(self):
        self.state['mappings']['V'].update(black_node_id='B', black_node_kind='image')
        self.state.update(black_node_id='NEW', black_node_kind='video')
        c.nodes(self.doc)['NEW'] = {'type': 'biz/video'}
        self.doc['assets']['NEW'] = {'content': {'name': '黑幕'}}
        self.assertNotIn('BLACK', {i['key'] for i in self.issues()})

    def test_shared_black_media_is_counted_once_with_all_affected_units(self):
        c.nodes(self.doc)['B']['type'] = 'biz/video'
        c.content(self.doc, 'B').pop('pippitAssetId')
        second = copy.deepcopy(self.unit)
        second['id'] = 'V2'
        self.state['mappings']['V2'] = {'node_id': 'V2', 'kind': 'video', 'name': 'V2'}
        c.nodes(self.doc)['V2'] = {'type': 'biz/video'}
        self.doc['assets']['V2'] = copy.deepcopy(self.doc['assets']['V'])
        edges = self.doc['assets']['ROOT']['content']['edges']
        for source in ('R', 'S', 'I', 'B'):
            edges[source+'-V2'] = {'source': source, 'target': 'V2', 'type': 'reference'}
        result = report.write_reference_report(self.project, self.session, self.state, self.doc, [self.unit, second])
        self.assertEqual(result['missing_video_count'], 1)
        black = next(i for i in result['issues'] if i['reason'] == 'missing_video')
        self.assertEqual(black['unit_ids'], ['V', 'V2'])

    def test_unqueried_asset_is_unverified_instead_of_missing_media(self):
        del self.doc['assets']['I']
        result = report.write_reference_report(self.project, self.session, self.state, self.doc, [self.unit])
        issues = {(issue['key'], issue['reason']) for issue in result['issues']}
        self.assertIn(('P', 'readback_unverified'), issues)
        self.assertNotIn(('P', 'missing_image'), issues)
        self.assertEqual(result['unverified_node_ids'], ['I'])
        self.assertFalse(result['media_readiness_checked'])
        self.assertIn('尚未核验', Path(result['report']).read_text(encoding='utf-8'))

    def test_unqueried_video_and_black_do_not_become_missing_references(self):
        del self.doc['assets']['V']
        del self.doc['assets']['B']
        issues = self.issues()
        self.assertEqual({(row['key'], row['reason']) for row in issues if row['key'] in {'V', 'BLACK'}},
                         {('V', 'readback_unverified'), ('BLACK', 'readback_unverified')})

    def test_success_report_reads_all_required_inputs_once_and_replaces_partial_document(self):
        fresh = copy.deepcopy(self.doc)
        fresh['assets']['I']['content']['pippitAssetId'] = 'REAL_IMAGE'
        del self.doc['assets']['I']
        self.doc['assets']['V']['content']['old_cached_field'] = 'do not carry forward'
        context = {'session': self.session, 'state': self.state, 'doc': self.doc, 'units': [self.unit], 'failures': []}
        cli = Mock()
        cli.batch_snapshot.return_value = {'document': fresh, 'complete': True}
        result = c.reference_report(self.project, context, cli=cli)
        cli.batch_snapshot.assert_called_once_with(self.state['binding'], node_ids=['B', 'I', 'R', 'S', 'V'])
        self.assertIs(context['doc'], fresh)
        self.assertNotIn('old_cached_field', c.content(context['doc'], 'V'))
        self.assertNotIn('P', {issue['key'] for issue in result['issues']})

    def test_report_read_failure_still_writes_unverified_result_without_false_missing_image(self):
        del self.doc['assets']['I']
        context = {'session': self.session, 'state': self.state, 'doc': self.doc, 'units': [self.unit], 'failures': []}
        cli = Mock()
        cli.batch_snapshot.side_effect = c.CanvasError('READBACK_PENDING', 'network read budget exhausted')
        result = c.reference_report(self.project, context, cli=cli)
        self.assertEqual(cli.batch_snapshot.call_count, 1)
        self.assertIn('report_readback_error', context)
        self.assertIn(('P', 'readback_unverified'), {(issue['key'], issue['reason']) for issue in result['issues']})
        self.assertNotIn(('P', 'missing_image'), {(issue['key'], issue['reason']) for issue in result['issues']})

    def test_complete_report_does_not_start_additional_read(self):
        context = {'session': self.session, 'state': self.state, 'doc': self.doc, 'units': [self.unit], 'failures': []}
        cli = Mock()
        c.reference_report(self.project, context, cli=cli)
        cli.batch_snapshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
