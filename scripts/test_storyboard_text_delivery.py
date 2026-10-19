"""Offline integration checks for plain-text storyboard delivery.

All screenplay, assets and review records below are synthetic test fixtures.
The review helper exercises binding and history; it does not approve real art,
performance, screenplay fidelity or production work.
"""
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import authoring_gates
import batch_naming
import canvas
import director as d
import review_gate
import storyboard_text as core
import storyboard_text_delivery as delivery


class TextDeliveryIntegration(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('SEEDANCE_AUDIT_TEST_DIR')
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.script = (
            '场景1-日-内-房间\n人物：陈默-基础形象；\n'
            '△ 陈默把杯子放在桌上。\n陈默：把消息发给@群里。\n'
            '场景2-日-内-走廊\n人物：陈默-基础形象；\n'
            '△ 陈默走到走廊尽头。\n陈默：我去看看。\n')
        self.ledger = {
            'schema_version': 3,
            'characters': [{'id': 'CHAR0001', 'name': '陈默-基础形象'}],
            'props': [{'id': 'PROP0001', 'name': '白色杯子'},
                      {'id': 'PROP0002', 'name': '未使用旧道具'}],
            'scene_assets': [{'id': 'SET0001', 'name': '房间'},
                             {'id': 'SET0002', 'name': '走廊'}],
            'scenes': [
                {'id': 'SC0001', 'number': 1, 'heading': '场景1-日-内-房间',
                 'scene_asset_id': 'SET0001', 'character_ids': ['CHAR0001'],
                 'prop_ids': ['PROP0001', 'PROP0002']},
                {'id': 'SC0002', 'number': 2, 'heading': '场景2-日-内-走廊',
                 'scene_asset_id': 'SET0002', 'character_ids': ['CHAR0001'],
                 'prop_ids': []}]}
        (self.project / 'script.txt').write_text(self.script, encoding='utf-8')
        (self.project / 'video-style.txt').write_text('3D国漫\n', encoding='utf-8')
        d.write_json(self.project / 'source.json', {'paragraphs': [{'id': 1, 'text': self.script}]})
        d.write_json(self.project / 'review.json', {'fixture_only': True, 'scenes': []})
        d.write_json(self.project / 'ledger.json', self.ledger)
        (self.project / 'scenes').mkdir()
        (self.project / 'scenes' / 'fixture.txt').write_text(self.script, encoding='utf-8')
        records, length = d.build_records(self.script, self.ledger)
        self.state = {
            'schema_version': 1, 'phase': 'range', 'style_hash': d.sha('3D国漫'),
            'revision': 0, 'ranges': [], 'units': [], 'receipts': [], 'next_numbers': {},
            'length': length, 'entry': {'min': 4, 'durations': []},
            'ledger': copy.deepcopy(self.ledger), 'records': records,
            'drafts': {'fixture.txt': d.sha(self.script)},
            'files': {key: {'path': str(self.project / name), 'sha256': d.file_hash(self.project / name)}
                      for key, name in [('script', 'script.txt'), ('source', 'source.json'),
                                        ('review', 'review.json'), ('ledger', 'ledger.json')]}}
        d.write_json(self.project / 'session.json', self.state)
        self.draft = self.write_draft('draft.txt', self.authored_text())
        self.review = self.project / 'scene-review-fixture.json'
        self.output = self.project / 'outputs' / 'delivery.txt'

    def authored_text(self, records=None, label='原作'):
        rows = []
        for i, record in enumerate(records or self.state['records'], 1):
            sid = record['scene']
            set_id = 'SET0001' if sid == 'SC0001' else 'SET0002'
            action = ('@{CHAR0001}把@{PROP0001}放在桌上。' if sid == 'SC0001'
                      else '@{CHAR0001}走到走廊尽头。')
            rows.extend([
                f'## {sid} / u{i:02d} / {record["id"]}-{record["id"]}',
                '人物：@{CHAR0001}；',
                f'场景：@{{{set_id}}}；时间：白天；天气：未明示；',
                '人物站位：@{CHAR0001}在画面左侧。',
                f'·00秒-04秒[镜头1-{label}]：（@{{CHAR0001}}的中景；转场：硬切）'
                f'侧面固定机位，人物与门框保持清晰；{action}音效：衣料轻响。'])
            if record['kind'] == 'dialogue':
                rows.append(f'声音[{record["id"]},0.2-2.8]：@{{CHAR0001}}说：“{record["text"]}”')
            rows.append('')
        return '\n'.join(rows)

    def write_draft(self, name, text):
        path = self.project / name
        path.write_text(text, encoding='utf-8')
        return path

    def write_synthetic_review_fixture(self, draft=None, span=None, replace_ids=None):
        """Generate fake review data exclusively for isolated fictional tests."""
        _, _, info = delivery.prepare(self.project, draft or self.draft, span, replace_ids)
        report = {key: info[key] for key in ('draft_sha256', 'context_sha256')}
        report.update(reviewer='synthetic-test-fixture-only', scenes=[
            {'scene_id': sid, 'status': 'passed', 'issues': [],
             'basis': '仅用于虚构测试数据的绑定与交付回归，不是生产审阅。'}
            for sid in info['scenes']])
        d.write_json(self.review, report)
        return report

    def commit_fixture(self, draft=None, request_id='fictional-first', output=None, **kwargs):
        draft = draft or self.draft
        self.write_synthetic_review_fixture(draft, kwargs.get('span'), kwargs.get('replace_ids'))
        return delivery.commit(self.project, draft, request_id, self.review, output or self.output, **kwargs)

    def snapshot(self):
        return {str(p.relative_to(self.project)): p.read_bytes()
                for p in self.project.rglob('*') if p.is_file()}

    def cli(self, script, *args, success=True):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name(script)),
                                 *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                                timeout=30)
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
        return json.loads(result.stdout if result.stdout.strip() else result.stderr)

    def test_check_and_preview_are_read_only_for_session_and_history(self):
        before = self.snapshot()
        result = self.cli('storyboard_text_delivery.py', 'check', '--project', self.project,
                          '--draft', self.draft)
        self.assertEqual(result['semantic_review'], 'not_performed')
        self.assertEqual(result['unit_count'], 4)
        stats = result['observations']
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(stats['unit_duration_seconds'], {
            'distribution': {'4': 4}, 'mean': 4, 'min': 4, 'max': 4})
        self.assertEqual([row['source_scene'] for row in stats['same_scene_adjacent_candidates']],
                         ['SC0001', 'SC0002'])
        self.assertEqual([row['units'] for row in stats['same_scene_adjacent_candidates']],
                         [['SC0001/u01', 'SC0001/u02'], ['SC0002/u03', 'SC0002/u04']])
        self.assertEqual(stats['camera_wording']['by_shot_duration_seconds']['4']['fixed'], 4)
        self.assertEqual(self.snapshot(), before)
        preview = self.project / 'preview.txt'
        result = self.cli('storyboard_text_delivery.py', 'preview', '--project', self.project,
                          '--draft', self.draft, '--output', preview)
        after = self.snapshot()
        after.pop('preview.txt')
        self.assertEqual(after, before)
        self.assertIn('预览01-场景1-分镜1', preview.read_text(encoding='utf-8'))
        self.assertEqual(result['semantic_review'], 'not_performed')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['observations'], stats)
        self.assertEqual(preview.read_text(encoding='utf-8'),
                         delivery.render_units(*delivery.prepare(self.project, self.draft)[:2], preview=True))
        self.assertFalse((self.project / batch_naming.REGISTRY).exists())

    def test_merge_candidates_exclude_units_spanning_multiple_source_scenes(self):
        records = self.state['records']
        cross = '\n'.join([
            f'## SC0001 / cross / {records[0]["id"]}-{records[2]["id"]}',
            '人物：@{CHAR0001}；',
            '场景：@{SET0001}，@{SET0002}；时间：白天；天气：未明示；',
            '人物站位：@{CHAR0001}从@{SET0001}走到@{SET0002}。',
            '·00秒-04秒[镜头1-房间]：（@{CHAR0001}的中景；转场：硬切）'
            '固定机位；@{CHAR0001}把@{PROP0001}放在桌上。音效：杯底轻响。',
            f'声音[{records[1]["id"]},0.2-2.8]：@{{CHAR0001}}说：“{records[1]["text"]}”',
            '·04秒-08秒[镜头2-走廊]：（@{CHAR0001}的中景；转场：硬切）'
            '镜头平移；@{CHAR0001}走到@{SET0002}尽头。音效：脚步声。'])
        draft = self.write_draft('cross-adjacent.txt', cross + '\n' + self.authored_text([records[3]]))
        _, units, info = delivery.prepare(self.project, draft)
        self.assertEqual(info['status'], 'checked')
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]['scene_ids'], ['SC0001', 'SC0002'])
        self.assertEqual(info['observations']['same_scene_adjacent_candidates'], [])
        self.assertEqual(info['observations']['unit_duration_seconds'], {
            'distribution': {'4': 1, '8': 1}, 'mean': 6, 'min': 4, 'max': 8})

    def test_incomplete_scene_requires_exact_explicit_span(self):
        record = self.state['records'][0]
        partial = self.write_draft('partial.txt', self.authored_text([record]))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '完整场景'):
            delivery.prepare(self.project, partial)
        span = [record['start'], record['end']]
        _, units, info = delivery.prepare(self.project, partial, span)
        self.assertEqual(info['span'], span)
        self.assertEqual(len(units), 1)
        with self.assertRaisesRegex(ValueError, '指定.*(来源范围|source_span)'):
            delivery.prepare(self.project, partial, [span[0], span[1] + 1])
        self.assertEqual(self.snapshot(), before)

    def test_review_rejects_stale_draft_or_bound_context(self):
        self.write_synthetic_review_fixture()
        original = self.draft.read_text(encoding='utf-8')
        self.draft.write_text(original.replace('镜头1-原作', '镜头1-修改'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'draft_sha256'):
            delivery.commit(self.project, self.draft, 'stale-draft', self.review, self.output)
        self.draft.write_text(original, encoding='utf-8')
        changed = d.load(self.project)
        changed['entry']['min'] = 5
        d.write_json(self.project / 'session.json', changed)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'context_sha256'):
            delivery.commit(self.project, self.draft, 'stale-context', self.review, self.output)
        self.assertEqual(self.snapshot(), before)

    def test_review_rejects_missing_scene_open_issue_and_blank_evidence(self):
        original = self.write_synthetic_review_fixture()
        reports = []
        report = copy.deepcopy(original)
        report['scenes'].pop()
        reports.append(report)
        report = copy.deepcopy(original)
        report['scenes'][0]['issues'] = ['unclosed fictional issue']
        reports.append(report)
        report = copy.deepcopy(original)
        report['scenes'][0]['status'] = 'pending'
        reports.append(report)
        report = copy.deepcopy(original)
        report['scenes'][0]['basis'] = ' '
        reports.append(report)
        report = copy.deepcopy(original)
        report['reviewer'] = ''
        reports.append(report)
        for report in reports:
            with self.subTest(report=report):
                d.write_json(self.review, report)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    delivery.commit(self.project, self.draft, 'invalid-review', self.review, self.output)
                self.assertEqual(self.snapshot(), before)

    def test_cross_scene_unit_review_covers_every_source_scene(self):
        records = self.state['records']
        text = '\n'.join([
            f'## SC0001 / cross_scene / {records[0]["id"]}-{records[-1]["id"]}',
            '人物：@{CHAR0001}；',
            '场景：@{SET0001}，@{SET0002}；时间：白天；天气：未明示；',
            '人物站位：@{CHAR0001}从@{SET0001}走到@{SET0002}。',
            '·00秒-04秒[镜头1-房间]：（@{CHAR0001}的中景；转场：硬切）'
            '侧面固定机位；@{CHAR0001}把@{PROP0001}放在桌上。音效：杯底轻响。',
            f'声音[{records[1]["id"]},0.2-2.8]：@{{CHAR0001}}说：“{records[1]["text"]}”',
            '·04秒-08秒[镜头2-走廊]：（@{CHAR0001}的中景；转场：硬切）'
            '侧面固定机位；@{CHAR0001}走到@{SET0002}尽头。音效：脚步声。',
            f'声音[{records[3]["id"]},4.2-6.8]：@{{CHAR0001}}说：“{records[3]["text"]}”'])
        draft = self.write_draft('cross-scene.txt', text)
        span = [0, self.state['length']]
        _, _, info = delivery.prepare(self.project, draft, span=span)
        self.assertEqual(info['scenes'], ['SC0001', 'SC0002'])
        report = self.write_synthetic_review_fixture(draft, span=span)
        report['scenes'].pop()
        d.write_json(self.review, report)
        with self.assertRaisesRegex(ValueError, '审阅场景'):
            delivery.commit(self.project, draft, 'cross-scene', self.review, self.output, span=span)

    def test_commit_and_retry_do_not_consume_second_number_or_approval(self):
        result = self.commit_fixture()
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(result['ids'], ['场景1-分镜1', '场景1-分镜2', '场景2-分镜1', '场景2-分镜2'])
        self.assertEqual(result['names'], ['A01-场景1-分镜1', 'A02-场景1-分镜2',
                                           'A03-场景2-分镜1', 'A04-场景2-分镜2'])
        committed = d.load(self.project)
        self.assertEqual(committed['next_numbers'], {'SC0001': 3, 'SC0002': 3})
        self.assertEqual(committed['revision'], 1)
        self.assertEqual(result['canvas_sync'], 'not_requested')
        self.review.unlink()
        before = self.snapshot()
        result = delivery.commit(self.project, self.draft, 'fictional-first', self.review, self.output)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(d.load(self.project)['receipts']), 1)
        self.assertFalse((self.project / 'canvas').exists())

    def test_retry_restores_missing_output_and_refuses_modified_output(self):
        self.commit_fixture()
        expected = self.output.read_bytes()
        state_before = (self.project / 'session.json').read_bytes()
        registry_before = (self.project / batch_naming.REGISTRY).read_bytes()
        self.output.unlink()
        result = delivery.commit(self.project, self.draft, 'fictional-first', self.review, self.output)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(self.output.read_bytes(), expected)
        self.assertEqual((self.project / 'session.json').read_bytes(), state_before)
        self.assertEqual((self.project / batch_naming.REGISTRY).read_bytes(), registry_before)
        self.output.write_text('externally changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '外部修改'):
            delivery.commit(self.project, self.draft, 'fictional-first', self.review, self.output)
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'externally changed')

    def test_failed_delivery_write_can_recover_from_saved_receipt(self):
        self.write_synthetic_review_fixture()
        atomic_text = d.atomic_text
        def fail_only_delivery(path, text):
            if Path(path).resolve() == self.output.resolve():
                raise OSError('synthetic output failure')
            return atomic_text(path, text)
        with patch.object(d, 'atomic_text', side_effect=fail_only_delivery):
            with self.assertRaisesRegex(OSError, 'synthetic output failure'):
                delivery.commit(self.project, self.draft, 'recover-write', self.review, self.output)
        state = d.load(self.project)
        self.assertEqual(len(state['receipts']), 1)
        self.assertEqual(state['revision'], 1)
        self.assertFalse(self.output.exists())
        state_before = (self.project / 'session.json').read_bytes()
        result = delivery.commit(self.project, self.draft, 'recover-write', self.review, self.output)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(self.output.read_text(encoding='utf-8'), delivery.render_units(state, state['receipts'][0]['units']))
        self.assertEqual((self.project / 'session.json').read_bytes(), state_before)

    def test_overlapping_append_requires_explicit_history_confirmation(self):
        self.commit_fixture()
        self.write_synthetic_review_fixture()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '范围重叠|本次用户授权'):
            delivery.commit(self.project, self.draft, 'overlapping-fixture', self.review,
                            self.project / 'outputs' / 'overlap.txt')
        self.assertEqual(self.snapshot(), before)

    def test_same_request_id_cannot_change_content_or_output(self):
        self.commit_fixture()
        before = (self.project / 'session.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '沿用原交付路径'):
            delivery.commit(self.project, self.draft, 'fictional-first', self.review,
                            self.project / 'outputs' / 'other.txt')
        self.draft.write_text(self.authored_text(label='新动作'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'request_id已用于其他内容'):
            delivery.commit(self.project, self.draft, 'fictional-first', self.review, self.output)
        self.assertEqual((self.project / 'session.json').read_bytes(), before)

    def test_selected_replacement_preserves_ids_history_and_original_receipt(self):
        self.commit_fixture()
        old = d.load(self.project)
        original_bytes = self.output.read_bytes()
        selected = old['units'][0]['id']
        replacement = self.write_draft('replacement.txt', self.authored_text([old['records'][0]], label='手部动作'))
        result = self.commit_fixture(replacement, 'fictional-replace', self.project / 'outputs' / 'revised.txt',
                                     replace_ids=[selected])
        current = d.load(self.project)
        self.assertEqual(result['ids'], [selected])
        self.assertEqual(current['units'][0]['version'], 2)
        self.assertEqual(current['units'][0]['display_name'], old['units'][0]['display_name'])
        expected_version = {k: v for k, v in old['units'][0].items() if k != 'previous_versions'}
        self.assertEqual(current['units'][0]['previous_versions'], [expected_version])
        self.assertEqual(current['units'][1:], old['units'][1:])
        self.assertEqual(current['receipts'][0], old['receipts'][0])
        self.assertEqual(current['ranges'], old['ranges'])
        self.assertEqual(current['next_numbers'], old['next_numbers'])
        self.assertEqual(self.output.read_bytes(), original_bytes)
        export = self.project / 'outputs' / 'original-again.txt'
        self.cli('storyboard_text_delivery.py', 'render', '--project', self.project,
                 '--request-id', 'fictional-first', '--output', export)
        self.assertEqual(export.read_bytes(), original_bytes)

    def test_single_split_dialogue_unit_can_be_replaced_using_original_span(self):
        blocks = self.draft.read_text(encoding='utf-8').strip().split('\n\n')
        first = blocks[1].replace('把消息发给@群里。', '把消息')
        second = blocks[1].replace('/ u02 /', '/ u02b /').replace('把消息发给@群里。', '发给@群里。')
        self.draft.write_text('\n\n'.join([blocks[0], first, second, *blocks[2:]]) + '\n', encoding='utf-8')
        self.commit_fixture()
        old = d.load(self.project)
        selected = old['units'][2]
        replacement = self.write_draft('split-replacement.txt', second.replace('镜头1-原作', '镜头1-反应') + '\n')
        self.commit_fixture(replacement, 'split-replacement', self.project / 'outputs' / 'split-revised.txt',
                            replace_ids=[selected['id']])
        current = d.load(self.project)
        self.assertEqual(current['units'][2]['span'], selected['span'])
        self.assertEqual(current['units'][2]['source_records'], selected['source_records'])
        self.assertEqual(current['units'][2]['version'], 2)
        self.assertEqual(current['units'][:2], old['units'][:2])
        self.assertEqual(current['units'][3:], old['units'][3:])
        self.assertIn('：“发给@群里。”', (self.project / 'outputs' / 'split-revised.txt').read_text(encoding='utf-8'))

    def test_output_cannot_overwrite_draft_review_sources_state_or_skill(self):
        self.write_synthetic_review_fixture()
        protected = [self.draft, self.review, self.project / 'source.json', self.project / 'script.txt',
                     self.project / 'session.json', self.project / 'video-style.txt',
                     self.project / 'scenes' / 'new.txt', self.project / 'canvas' / 'new.txt',
                     d.ROOT / 'must-not-be-written-by-test.txt']
        before = self.snapshot()
        for output in protected:
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, '输出|交付路径'):
                delivery.commit(self.project, self.draft, 'protected', self.review, output)
            self.assertEqual(self.snapshot(), before)

    def test_separate_process_commit_render_and_status_keep_exact_bytes(self):
        self.write_synthetic_review_fixture()
        args = ('commit', '--project', self.project, '--draft', self.draft, '--request-id', 'cli-fixture',
                '--review', self.review, '--output', self.output)
        first = self.cli('storyboard_text_delivery.py', *args)
        second = self.cli('storyboard_text_delivery.py', *args)
        self.assertEqual(first['status'], 'committed')
        self.assertEqual(second['status'], 'already_committed')
        export = self.project / 'outputs' / 'export.txt'
        self.cli('storyboard_text_delivery.py', 'render', '--project', self.project,
                 '--request-id', 'cli-fixture', '--output', export)
        self.assertEqual(export.read_bytes(), self.output.read_bytes())
        status = self.cli('director.py', 'status', '--project', self.project)
        self.assertEqual(len(status['units']), 4)
        self.assertTrue(all(u['output_format'] == 'scene-text-v1' for u in status['units']))
        self.assertTrue(all('start_state' not in u and 'end_state' not in u for u in status['units']))

    def test_new_route_never_calls_legacy_validation_or_gates(self):
        with ExitStack() as stack:
            for name in ('validate', 'validate_unit'):
                stack.enter_context(patch.object(d, name, side_effect=AssertionError('legacy validation called')))
            for module in (review_gate, authoring_gates):
                for name, value in vars(module).items():
                    if callable(value) and getattr(value, '__module__', None) == module.__name__:
                        stack.enter_context(patch.object(module, name, side_effect=AssertionError('legacy gate called')))
            state, units, info = delivery.prepare(self.project, self.draft)
            self.assertEqual(info['semantic_review'], 'not_performed')
            self.assertIn('预览01', delivery.render_units(state, units, preview=True))
            self.commit_fixture()
            self.assertEqual(delivery.commit(self.project, self.draft, 'fictional-first', self.review,
                                             self.output)['status'], 'already_committed')

    def test_canvas_projection_and_direct_render_need_no_old_performance_states(self):
        self.commit_fixture()
        state = d.load(self.project)
        unit = state['units'][0]
        self.assertTrue(all(key not in unit for key in ('continuity', 'start_state', 'end_state', 'audio')))
        self.assertEqual(canvas.unit_asset_ids(state, unit), {'CHAR0001', 'SET0001', 'PROP0001'})
        self.assertNotIn('PROP0002', canvas.unit_asset_ids(state, unit))
        self.assertEqual(d.render_unit(state, unit), core.render_unit(state, unit))
        seen = []
        def bind_reference(value):
            seen.append(value)
            return '[bound:' + value + ']'
        speech = d.render_unit(state, state['units'][1], bind_reference)
        self.assertIn('：“把消息发给@群里。”', speech)
        self.assertTrue(all('@群里' not in value for value in seen))
        self.assertIn('[bound:@陈默-基础形象 ]', speech)
        state['ledger']['characters'][0]['visual_mode'] = 'prompt_only'
        self.assertEqual(canvas.unit_asset_ids(state, unit), {'SET0001', 'PROP0001'})
        self.assertNotIn('@陈默-基础形象', d.render_unit(state, unit))

    def test_mixed_project_keeps_old_receipt_renderable_and_legacy_check_explicit(self):
        self.commit_fixture()
        state = d.load(self.project)
        legacy = {
            'id': 'legacy-fixture', 'version': 1, 'span': [0, state['length']],
            'output_format': 'reference-v1', 'style': '3D国漫', 'duration': 4,
            'header_policy': d.HEADER_POLICY, 'black_reference_mode': d.BLACK_REFERENCE_MODE,
            'character_ids': ['CHAR0001'], 'scene_ids': ['SC0001'], 'prop_ids': [],
            'main_scene': 'SC0001', 'time': '白天', 'weather': '未明示',
            'global': '@{CHAR0001}站在门边。', 'audio': [],
            'shots': [{'start': 0, 'end': 4, 'scene_id': 'SC0001', 'label': '历史测试',
                       'framing': '中景', 'focus': ['CHAR0001'], 'transition': '硬切',
                       'text': '@{CHAR0001}转头。', 'dialogue_parts': []}]}
        legacy_path = self.project / 'outputs' / 'legacy.txt'
        receipt = {'request_id': 'legacy-receipt-fixture', 'request_hash': 'fixture-only',
                   'mode': 'append', 'display_format': d.RECEIPT_DISPLAY_FORMAT,
                   'output': str(legacy_path), 'units': [legacy]}
        expected = (d.render_unit(state, legacy) + '\n').encode('utf-8')
        state['units'].append(legacy)
        state['receipts'].append(receipt)
        d.write_json(self.project / 'session.json', state)
        result = self.cli('director.py', 'render', '--project', self.project,
                          '--request-id', 'legacy-receipt-fixture')
        self.assertEqual(result['status'], 'rendered')
        self.assertEqual(legacy_path.read_bytes(), expected)
        with self.assertRaisesRegex(ValueError, '文本|scene-text|storyboard_text'):
            d.validate(state, {'mode': 'replace', 'request_id': 'legacy-edit', 'units': [legacy]})

    def test_timing_records_latest_run_and_requires_explicit_completion(self):
        before = (self.project / 'session.json').read_bytes()
        empty = delivery.timing(self.project)
        self.assertFalse(empty['complete'])
        self.assertIsNone(empty['total_wall_seconds'])
        events = [('2026-10-19T00:00:00+00:00', 'start'),
                  ('2026-10-19T00:01:00+00:00', 'complete'),
                  ('2026-10-19T01:00:00+00:00', 'start'),
                  ('2026-10-19T01:00:20+00:00', 'delivered')]
        for timestamp, name in events:
            with patch.object(delivery, 'now', return_value=timestamp):
                delivery.event(self.project, name)
        active = delivery.timing(self.project)
        self.assertFalse(active['complete'])
        self.assertIsNone(active['total_wall_seconds'])
        self.assertEqual(len(active['current_run_events']), 2)
        with patch.object(delivery, 'now', return_value='2026-10-19T01:01:30+00:00'):
            delivery.event(self.project, 'complete')
        done = delivery.timing(self.project)
        self.assertTrue(done['complete'])
        self.assertEqual(done['total_wall_seconds'], 90)
        self.assertEqual(len(done['events']), 5)
        self.assertIsNone(done['model_seconds'])
        self.assertEqual((self.project / 'session.json').read_bytes(), before)

    def test_complete_event_without_start_does_not_invent_elapsed_time(self):
        delivery.event(self.project, 'complete')
        result = delivery.timing(self.project)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['total_wall_seconds'])


if __name__ == '__main__':
    unittest.main()
