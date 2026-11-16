"""Synthetic regressions for final range files and receipt-backed content totals."""
import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import delivery_reports as reports
import script_partition as partition
import segment_naming
import storyboard_delivery_timing as timing
import test_script_partition as partition_tests


class DeliveryReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = partition_tests.PartitionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        self.state = self.fixture.fixture([['阿明站起。'], ['阿明坐下。'], ['阿明伸手。'], ['阿明收手。']])
        review = self.fixture.local_review(self.state, 2, [self.fixture.boundary(self.state, 8, 1, 'before', 2)])
        self.plan = partition.save_plan(self.project, self.state, partition.build_plan(self.state, 2, review=review))
        self.state['receipts'] = []

    def unit(self, key, label, span, lengths, index=1, round_number=0, version=1):
        shots, lines, cursor = [], [], 0
        for length in lengths:
            line = f'·{cursor:02d}秒-{cursor + length:02d}秒[合成测试镜头]'
            shots.append({'start': cursor, 'end': cursor + length, 'text': line, 'dialogue_parts': []})
            lines.append(line)
            cursor += length
        source_records = [r for r in self.state['records'] if r['start'] < span[1] and r['end'] > span[0]]
        unit = {'id': key, 'version': version, 'draft_key': key, 'span': span,
                'source_records': [r['id'] for r in source_records],
                'scene_ids': list(dict.fromkeys(r['scene'] for r in source_records)),
                'duration': cursor, 'shots': shots, 'source_text': '\n'.join(lines),
                'segment_label': label, 'segment_round': round_number, 'segment_index': index,
                'naming_policy': segment_naming.POLICY, 'segment_plan_id': self.plan['plan_id'],
                'segment_plan_version': self.plan['version']}
        return unit

    def commit(self, unit, current=True):
        final, frozen = timing.convert([unit])
        output = self.project / f"{unit['id']}-v{unit['version']}.txt"
        output.write_text('合成正式回执：' + repr(final), encoding='utf-8')
        self.state['receipts'].append({
            'policy': 'scene-text-v1', 'request_id': output.stem, 'units': copy.deepcopy(final),
            'source_units': [copy.deepcopy(unit)], 'delivery_timing': frozen,
            'output': str(output), 'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'final_review': {'reviewer': 'synthetic-test-only', 'scenes': [
                {'scene_id': sid, 'status': 'passed', 'issues': [], 'basis': '只验证合成测试数据绑定。'}
                for sid in unit['scene_ids']]}})
        if current:
            self.state['units'].append(final[0])
        return final[0]

    def complete(self):
        self.commit(self.unit('old-A', 'A', [0, 8], [4]))
        self.commit(self.unit('A1', 'A', [0, 4], [4], round_number=1))
        self.commit(self.unit('A2', 'A', [4, 8], [4, 4], index=2, round_number=1), current=False)
        self.commit(self.unit('A2', 'A', [4, 8], [2, 4], index=2, round_number=1, version=2))
        self.commit(self.unit('B1', 'B', [8, 16], [4]))

    def test_range_export_writes_complete_actual_file_without_overwriting(self):
        output = self.project / 'deliveries/v1/范围.txt'
        result = partition.export_plan(self.project, self.state, output)
        text = output.read_text(encoding='utf-8')
        self.assertEqual(result['segment_count'], 2)
        self.assertIn('总字数：20', text)
        self.assertIn('目标累计字数10', text)
        self.assertIn('连续性理由：', text)
        self.assertIn('标准正文范围：[0, 8)', text)
        with self.assertRaisesRegex(ValueError, '已存在'):
            partition.export_plan(self.project, self.state, output)
        original = (self.project / 'script.txt').read_bytes()
        with self.assertRaisesRegex(ValueError, '不能覆盖'):
            partition.export_plan(self.project, self.state, self.project / 'script.txt')
        self.assertEqual((self.project / 'script.txt').read_bytes(), original)

    def test_custom_ranges_share_plan_validation_and_export(self):
        ranges = {'policy': partition.CUSTOM_POLICY, 'sources': partition.source_snapshot(self.state),
                  'segments': [{key: copy.deepcopy(row[key]) for key in ('span', 'source_range')}
                               | {'reason': '用户已指定的合成测试范围。'} for row in self.plan['segments']]}
        custom = partition.build_custom_plan(self.state, ranges)
        self.assertEqual(custom['mode'], 'custom')
        saved = partition.save_plan(self.project, self.state, custom)
        self.assertEqual(saved['version'], 2)
        self.assertEqual(partition.load_plan(self.project, self.state)['segments'][1]['word_count'], 10)
        output = self.project / '范围-自定义.txt'
        partition.export_plan(self.project, self.state, output)
        self.assertIn('范围依据：用户已指定', output.read_text(encoding='utf-8'))
        self.assertNotIn('目标累计字数', output.read_text(encoding='utf-8'))
        ranges['segments'][0]['span'][1] = 6
        with self.assertRaises(ValueError):
            partition.build_custom_plan(self.state, ranges)

    def test_latest_round_current_revision_and_weighted_average(self):
        self.complete()
        report = reports.segment_report(self.project, self.state)
        self.assertEqual(report['totals'], {'word_count': 20, 'unit_count': 3, 'shot_count': 4,
                                          'total_seconds': 14, 'average_shot_seconds': 3.5})
        self.assertNotEqual(report['totals']['average_shot_seconds'],
                            sum(row['average_shot_seconds'] for row in report['segments']) / 2)
        self.assertEqual(report['segments'][0]['round'], 1)
        self.assertEqual(report['segments'][0]['units'][-1], {'id': 'A2', 'version': 2})
        self.assertNotIn('old-A-v1', report['receipt_ids'])
        self.assertNotIn('A2-v1', report['receipt_ids'])
        output = self.project / 'deliveries/v1/分段详情.txt'
        reports.export_segment_report(self.project, self.state, output)
        self.assertIn('合计｜—｜20｜3｜4｜0分14秒｜3.50秒', output.read_text(encoding='utf-8'))

    def test_real_commit_and_public_cli_export_share_actual_receipt(self):
        import director
        import test_storyboard_text_delivery as delivery_tests
        fixture = delivery_tests.TextDeliveryIntegration()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        state = director.load(fixture.project)
        # This fixture's review is a delivery stub, not a standardizer map. A
        # one-segment full-source plan has no interior boundaries to choose.
        # Only that unused mapping is supplied; the plan, source counts,
        # commit, frozen receipt and CLI export all use their real paths.
        with patch.object(partition, 'exact_source_map', return_value=list(range(state['length']))):
            plan = partition.save_plan(fixture.project, state, partition.build_plan(state, 1))
        committed = fixture.commit_fixture(request_id='real-segment-report-fixture')
        self.assertEqual(committed['status'], 'committed')
        frozen = director.load(fixture.project)['receipts'][-1]
        output = fixture.project / 'deliveries/report-v1/分段详情.txt'
        result = fixture.cli('storyboard_text_delivery.py', 'segment-report', '--project', fixture.project,
                             '--output', output)
        self.assertEqual(result['status'], 'exported')
        self.assertEqual(result['report']['plan_id'], plan['plan_id'])
        self.assertEqual(result['report']['receipt_ids'], [frozen['request_id']])
        self.assertEqual(result['report']['totals']['unit_count'], len(frozen['units']))
        self.assertEqual(result['report']['totals']['total_seconds'],
                         sum(unit['duration'] for unit in frozen['units']))
        self.assertEqual(result['report']['totals']['word_count'], plan['total_count'])
        self.assertEqual(result['output_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertIn('平均单镜时长', output.read_text(encoding='utf-8'))

    def test_no_previous_round_fallback_or_filling_current_gap(self):
        self.commit(self.unit('old-A', 'A', [0, 8], [4]))
        self.commit(self.unit('A1', 'A', [0, 4], [4], round_number=1))
        self.commit(self.unit('B1', 'B', [8, 16], [4]))
        with self.assertRaisesRegex(ValueError, '未完整覆盖'):
            reports.segment_report(self.project, self.state)

    def test_shared_boundary_action_counts_source_once_and_all_shots(self):
        self.commit(self.unit('A1', 'A', [0, 8], [4]))
        self.commit(self.unit('A2', 'A', [4, 8], [4], index=2))
        self.commit(self.unit('B1', 'B', [8, 16], [4]))
        report = reports.segment_report(self.project, self.state)
        self.assertEqual(report['totals']['word_count'], 20)
        self.assertEqual(report['totals']['shot_count'], 3)
        self.assertEqual(report['totals']['total_seconds'], 12)

    def test_nonboundary_overlap_rejected(self):
        self.commit(self.unit('A1', 'A', [0, 8], [4]))
        self.commit(self.unit('A2', 'A', [0, 8], [4], index=2))
        self.commit(self.unit('B1', 'B', [8, 16], [4]))
        with self.assertRaisesRegex(ValueError, '重复覆盖'):
            reports.segment_report(self.project, self.state)

    def test_missing_tampered_or_unreviewed_formal_delivery_rejected(self):
        self.complete()
        receipt = self.state['receipts'][-1]
        path = Path(receipt['output'])
        path.write_text('外部改动', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '正式原件缺失或变化'):
            reports.segment_report(self.project, self.state)
        receipt['output_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt['final_review']['scenes'][0]['status'] = 'needs_revision'
        with self.assertRaisesRegex(ValueError, '审阅闭环'):
            reports.segment_report(self.project, self.state)

    def test_unsaved_unit_and_corrupt_frozen_timing_rejected(self):
        self.complete()
        self.state['units'][-1]['duration'] += 1
        with self.assertRaisesRegex(ValueError, '冻结回执'):
            reports.segment_report(self.project, self.state)
        self.state['units'][-1]['duration'] -= 1
        self.state['receipts'][-1]['delivery_timing']['final_total_duration'] += 1
        with self.assertRaisesRegex(ValueError, '冻结回执'):
            reports.segment_report(self.project, self.state)


if __name__ == '__main__':
    unittest.main()
