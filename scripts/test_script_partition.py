"""Synthetic-only regressions for source-exact work assignment."""
import copy
import itertools
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest

import script_partition as partition

MODULE = Path(__file__).resolve().parents[1] / 'modules' / 'screenplay-standardizer' / 'scripts'
sys.path.insert(0, str(MODULE))
import screenplay_review as review
import screenplay_io
import source_compat


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')


class PartitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def fixture(self, groups):
        paragraphs, records, scenes, texts = [], [], [], []
        cursor = 0
        for number, rows in enumerate(groups, 1):
            start = len(paragraphs) + 1
            paragraphs.extend(rows)
            body = []
            for index, value in enumerate(rows, 3):
                label = source_compat.explicit_label(value, ['阿明'])
                text = label['text'] if label else value
                body.append(value if label else '△ ' + value)
                size = len(partition.compact(text))
                records.append({'id': f'L{number:03d}{index:03d}', 'scene': f'SC{number:04d}',
                                'local_line': index, 'kind': 'dialogue' if label else 'action',
                                'speaker': '阿明' if label else None, 'text': text,
                                'start': cursor, 'end': cursor + size})
                cursor += size
            content = f'场景{number}-日-内-房间\n人物：阿明-基础形象；\n' + '\n'.join(body) + '\n'
            save(self.project / 'scenes' / f'scene_{number:03d}_{start:04d}-{len(paragraphs):04d}.txt', content)
            texts.append(content.strip())
            scenes.append({'id': f'SC{number:04d}', 'number': number, 'source_range': [start, len(paragraphs)]})
        save(self.project / 'source.json', {'paragraphs': [{'id': n, 'text': row} for n, row in enumerate(paragraphs, 1)]})
        save(self.project / 'script.txt', '\n\n'.join(texts) + '\n')
        report = review.make_report(self.project, {})
        decisions = {}
        for scene in report['scenes']:
            for item in scene['speaker_items']:
                decisions[item['decision_key']] = {**item['decision_template'], 'speaker': '阿明'}
        report = review.make_report(self.project, decisions)
        self.assertEqual(report['status'], 'passed', report)
        save(self.project / 'review.json', report)
        self.state = {'records': records, 'length': cursor, 'ledger': {'scenes': scenes}, 'units': [],
                      'files': {key: {'path': str(self.project / filename), 'sha256': partition.file_digest(self.project / filename)}
                                for key, filename in [('source', 'source.json'), ('script', 'script.txt'), ('review', 'review.json')]}}
        return self.state

    def test_scene_plan_covers_source_counts_original_punctuation(self):
        state = self.fixture([['阿明站起。'], ['阿明坐下。'], ['阿明伸手。'], ['阿明收手。']])
        proposal = partition.build_plan(state, 2, 'scene')
        self.assertEqual([s['word_count'] for s in proposal['segments']], [10, 10])
        self.assertEqual(proposal['total_count'], 20)
        self.assertEqual([s['span'] for s in proposal['segments']], [[0, 8], [8, 16]])
        plan = partition.save_plan(self.project, state, proposal)
        self.assertEqual(partition.load_plan(self.project, state), plan)
        self.assertEqual(partition.resolve_segment(self.project, state, [1, 7])['label'], 'A')
        self.assertIsNone(partition.resolve_segment(self.project, state, [7, 9]))
        self.assertEqual(partition.resolve_segment(self.project, state, 'B分段')['span'], [8, 16])

    def test_scene_gap_threshold_and_confirmation_are_exact_and_idempotent(self):
        state = self.fixture([['甲' * 2001 + '。'], ['乙。']])
        proposal = partition.build_plan(state, 2, 'scene')
        self.assertEqual(proposal['difference'], 2000)
        self.assertEqual(proposal['status'], 'confirmed')
        state = self.fixture([['甲' * 2002 + '。'], ['乙。']])
        proposal = partition.build_plan(state, 2, 'scene')
        plan = partition.save_plan(self.project, state, proposal)
        self.assertEqual(plan['status'], 'needs_confirmation')
        with self.assertRaisesRegex(ValueError, '尚未'):
            partition.resolve_segment(self.project, state, 'A')
        with self.assertRaisesRegex(ValueError, '过期'):
            partition.confirm_plan(self.project, state, 'wrong')
        confirmed = partition.confirm_plan(self.project, state, plan['proposal_hash'])
        self.assertEqual(confirmed, partition.confirm_plan(self.project, state, plan['proposal_hash']))
        self.assertEqual(confirmed, partition.save_plan(self.project, state, proposal))

    def test_balanced_requires_reviewed_exact_boundary_and_keeps_dialogue_whole(self):
        state = self.fixture([['阿明站起。', '阿明：我已经知道了。', '阿明走开。']])
        review_data = partition.boundary_template(state)
        review_data['boundaries'] = [{'offset': 4, 'source_position': [2, 0], 'safe': True,
                                     'reason': '站起已结束，后续发言可以另起单元。'},
                                    {'offset': 10, 'source_position': [3, 0], 'safe': True,
                                     'reason': '发言完整结束，无急促回应依赖。'}]
        proposal = partition.build_plan(state, 2, 'balanced', review_data)
        self.assertEqual(len(proposal['segments']), 2)
        bad = copy.deepcopy(review_data)
        bad['boundaries'][0]['source_position'] = [1, 1]
        with self.assertRaisesRegex(ValueError, '精确对应'):
            partition.build_plan(state, 2, 'balanced', bad)
        bad = copy.deepcopy(review_data)
        bad['boundaries'] = [{'offset': 6, 'source_position': [2, 5], 'safe': True, 'reason': '错误样本'}]
        with self.assertRaisesRegex(ValueError, '台词'):
            partition.build_plan(state, 2, 'balanced', bad)
        bad = copy.deepcopy(review_data)
        bad['boundaries'][0]['safe'] = False
        with self.assertRaisesRegex(ValueError, '实读'):
            partition.build_plan(state, 2, 'balanced', bad)

    def test_participant_limits_one_person_and_labels(self):
        state = self.fixture([['阿明站起。'], ['阿明坐下。']])
        for count in (0, -1, True, 2.5, 3):
            with self.assertRaises(ValueError):
                partition.build_plan(state, count, 'scene')
        self.assertEqual(partition.build_plan(state, 1, 'balanced')['segments'][0]['span'], [0, 8])
        self.assertEqual([partition.label_for(n) for n in (1, 26, 27, 28)], ['A', 'Z', 'AA', 'AB'])

    def test_stale_source_and_changed_plan_after_generation_rejected(self):
        state = self.fixture([['阿明站起。'], ['阿明坐下。'], ['阿明走开。']])
        plan = partition.save_plan(self.project, state, partition.build_plan(state, 2, 'scene'))
        state['units'] = [{'id': 'existing'}]
        self.assertEqual(plan, partition.save_plan(self.project, state, partition.build_plan(state, 2, 'scene')))
        with self.assertRaisesRegex(ValueError, '已有分镜'):
            partition.save_plan(self.project, state, partition.build_plan(state, 3, 'scene'))
        with (self.project / 'script.txt').open('a', encoding='utf-8') as stream:
            stream.write('变更')
        with self.assertRaisesRegex(ValueError, '已变化'):
            partition.load_plan(self.project, state)

    def test_global_balance_matches_exhaustive_small_cases(self):
        rng = random.Random(7)
        for size in range(2, 9):
            for _ in range(10):
                weights = [rng.randrange(1, 20) for _ in range(size)]
                counts = [0]
                for weight in weights:
                    counts.append(counts[-1] + weight)
                for participants in range(1, size + 1):
                    chosen = partition.balanced_indices(counts, participants)
                    score = sum((counts[b] - counts[a]) ** 2 for a, b in zip(chosen, chosen[1:]))
                    expected = min(sum((counts[b] - counts[a]) ** 2 for a, b in zip([0, *cuts], [*cuts, size]))
                                   for cuts in itertools.combinations(range(1, size), participants - 1))
                    self.assertEqual(score, expected)

    def test_explicit_fragment_mapping_is_exact_for_action_and_os(self):
        from test_source_fragment_mapping import SourceFragmentMappingTests
        fixture = SourceFragmentMappingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        decisions = fixture.cue_fixture()
        report = fixture.checked(decisions)
        self.assertEqual(report['status'], 'passed')
        save(fixture.work / 'review.json', report)
        import director
        ledger = {'scenes': [{'id': 'SC0001', 'number': 1, 'source_range': [1, 2]}]}
        records, length = director.build_records(fixture.path.read_text(encoding='utf-8'), ledger)
        state = {'records': records, 'length': length, 'ledger': ledger, 'units': [],
                 'files': {key: {'path': str(path), 'sha256': partition.file_digest(path)}
                           for key, path in [('source', fixture.source), ('script', fixture.path),
                                             ('review', fixture.work / 'review.json')]}}
        mapped = partition.exact_source_map(state)
        raw = '\n'.join(fixture.paragraphs)
        self.assertEqual(''.join(raw[i] for i in mapped), ''.join(partition.compact(r['text']) for r in records))

    def test_reviewed_transition_prefix_maps_dialogue_voice_and_retained_action(self):
        from test_source_fragment_mapping import SourceFragmentMappingTests
        import director
        cases = [
            ('转场 阿明：我知道。', '阿明：我知道。'),
            ('转场 OS 阿明：“我知道。”', '阿明(内心OS)：我知道。'),
            ('转场 OS “我知道。”', '阿明(内心OS)：我知道。'),
            ('遮挡转场 阿明 O.S.：我知道。', '阿明(内心OS)：我知道。'),
            ('转场 阿明走过来。', '△ 转场 阿明走过来。'),
        ]
        for original, body in cases:
            with self.subTest(original=original):
                fixture = SourceFragmentMappingTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                report = fixture.fixture([original], body)
                decisions = {item['decision_key']: {**item['decision_template'], 'speaker': '阿明'}
                             for scene in report['scenes'] for item in scene['speaker_items']}
                report = fixture.checked(decisions)
                self.assertEqual(report['status'], 'passed', report)
                self.assertTrue(all(report['counts'][key] == 0 for key in ('missing', 'extra', 'speaker', 'scene_boundary')))
                save(fixture.work / 'review.json', report)
                ledger = {'scenes': [{'id': 'SC0001', 'number': 1, 'source_range': [1, 1]}]}
                records, length = director.build_records(fixture.path.read_text(encoding='utf-8'), ledger)
                state = {'records': records, 'length': length, 'ledger': ledger, 'units': [],
                         'files': {key: {'path': str(path), 'sha256': partition.file_digest(path)}
                                   for key, path in [('source', fixture.source), ('script', fixture.path),
                                                     ('review', fixture.work / 'review.json')]}}
                plan = partition.build_plan(state, 1, 'scene')
                self.assertEqual(plan['segments'][0]['span'], [0, length])
                self.assertEqual(plan['total_count'], sum(not c.isspace() for c in original))
                mapped = partition.exact_source_map(state)
                self.assertEqual(''.join(original[i] for i in mapped),
                                 ''.join(partition.compact(r['text']) for r in records))


class ActionMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.path = self.work / 'scenes/scene_001_0001-0001.txt'

    def test_registered_prefix_is_shared_by_validation_and_review(self):
        save(self.work / 'source.json', {'paragraphs': [{'id': 1, 'text': '◆阿明站起。'}]})
        text = '场景1-日-内-房间\n人物：阿明-基础形象；\n◆阿明站起。\n'
        save(self.path, text)
        self.assertTrue(screenplay_io.validate_scene(text, 1, self.path))
        source_compat.register_action_markers(self.work, ['◆'], '实读确认原稿◆仅用于动作行首，与角色台词分开。')
        self.assertFalse(screenplay_io.validate_scene(text, 1, self.path))
        self.assertEqual(review.make_report(self.work, {})['status'], 'passed')
        self.assertEqual(source_compat.action_content('◆阿明站起。', ('△', '◆')), '阿明站起。')
        self.assertIsNone(source_compat.action_content('阿明：◆是符号。', ('△', '◆')))
        self.assertIn('action_markers', review.fingerprint_workdir(self.work))
        report = review.make_report(self.work, {})
        self.assertEqual(report['fingerprint'], review.fingerprint_workdir(self.work))
        source_compat.register_action_markers(self.work, ['◆'], '重新实读该原符号，正文保持。')
        current = review.make_report(self.work, {}, previous=report)
        self.assertEqual(current['counts']['reused_scenes'], 0)

    def test_marker_must_be_source_bound_and_not_a_dialogue_quote(self):
        save(self.work / 'source.json', {'paragraphs': [{'id': 1, 'text': '◆阿明站起。'}]})
        for marker in ['▲', '“', '：', '动作']:
            with self.assertRaises(ValueError):
                source_compat.register_action_markers(self.work, [marker], '错误样本')
        source_compat.register_action_markers(self.work, ['◆'], '实读确认')
        save(self.work / 'source.json', {'paragraphs': [{'id': 1, 'text': '◆阿明坐下。'}]})
        with self.assertRaisesRegex(ValueError, '失配'):
            source_compat.load_action_markers(self.work)

    def test_new_heading_policy_and_legacy_read_compatibility(self):
        source = {'paragraphs': [{'id': 1, 'text': '阿明站起。'}]}
        text = '场景1：日·内·房间\n人物：阿明-基础形象；\n△ 阿明站起。\n'
        save(self.work / 'source.json', source)
        self.assertFalse(screenplay_io.validate_scene(text, 1, self.path))
        source['format_policy'] = 'standard-screenplay-v1'
        save(self.work / 'source.json', source)
        self.assertTrue(screenplay_io.validate_scene(text, 1, self.path))


if __name__ == '__main__':
    unittest.main()
