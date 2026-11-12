"""Source-bound blackouts touching a new submission or a selected replacement."""
import unittest

import director as d
import storyboard_text as text_core
import storyboard_text_delivery as delivery
import test_storyboard_text_delivery as fixtures


class BlackoutNeighbors(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TextDeliveryIntegration()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        self.script = ('场景1-日-内-房间\n人物：陈默-基础形象；\n'
                       '△ 陈默抬手。\n△ 【画面切黑】\n△ 【画面切黑】\n'
                       '△ 陈默放下手。\n△ 陈默走向桌边。\n'
                       '△ 【画面切黑】\n陈默（画外音）：谁？\n△ 陈默走到门边。\n')
        (self.project / 'script.txt').write_text(self.script, encoding='utf-8')
        (self.project / 'scenes' / 'fixture.txt').write_text(self.script, encoding='utf-8')
        d.write_json(self.project / 'source.json', {'paragraphs': [{'id': 1, 'text': self.script}]})
        state = self.fixture.state
        ledger = self.fixture.ledger
        ledger['scenes'] = ledger['scenes'][:1]
        d.write_json(self.project / 'ledger.json', ledger)
        state['ledger'] = ledger
        state['records'], state['length'] = d.build_records(self.script, ledger)
        state['drafts'] = {'fixture.txt': d.sha(self.script)}
        for key in ('script', 'source', 'ledger'):
            state['files'][key]['sha256'] = d.file_hash(state['files'][key]['path'])
        d.write_json(self.project / 'session.json', state)
        self.records = state['records']

    def unit(self, first, last, shots, key='u01'):
        first_id, last_id = self.records[first]['id'], self.records[last]['id']
        rows = [f'## SC0001 / {key} / {first_id}-{last_id}',
                '人物：@{CHAR0001}；', '场景：@{SET0001}；时间：白天；天气：未明示；',
                '人物站位：@{CHAR0001}在画面左侧。']
        start = 0
        for number, (black_record, duration) in enumerate(shots, 1):
            if black_record is None:
                content = '（@{CHAR0001}的中景；转场：硬切）相机向右横移，@{CHAR0001}放下手，袖口缓缓垂落。'
            else:
                marker = self.records[black_record]['id']
                content = f'（黑场[{marker}-{marker}]；转场：切黑）纯黑画面，无可见对象。'
            rows.append(f'·{start:02d}秒-{start + duration:02d}秒[镜头{number}-测试]：{content}音效：衣料轻响。')
            start += duration
        return '\n'.join(rows) + '\n'

    def span(self, first, last):
        return [self.records[first]['start'], self.records[last]['end']]

    def assert_rejected(self, call):
        snapshot = self.fixture.snapshot()
        with self.assertRaises(text_core.TextValidationError) as raised:
            call()
        self.assertIn('BLACKOUT_DURATION', {e['code'] for e in raised.exception.errors})
        self.assertEqual(self.fixture.snapshot(), snapshot)

    def test_previous_commit_and_repeated_draft_key(self):
        first = self.fixture.write_draft('first.txt', self.unit(0, 1, [(None, 2), (1, 2)]))
        self.fixture.commit_fixture(first, 'first-take', span=self.span(0, 1))
        second = self.fixture.write_draft('second.txt', self.unit(2, 3, [(2, 2), (None, 2)]))
        self.assert_rejected(lambda: delivery.prepare(self.project, second, self.span(2, 3)))
        self.assert_rejected(lambda: delivery.check_incremental(self.project, second, self.span(2, 3)))

    def test_committed_successor_when_submitted_out_of_order(self):
        second = self.fixture.write_draft('second.txt', self.unit(2, 3, [(2, 2), (None, 2)]))
        self.fixture.commit_fixture(second, 'second-take', span=self.span(2, 3))
        first = self.fixture.write_draft('first.txt', self.unit(0, 1, [(None, 2), (1, 2)]))
        self.assert_rejected(lambda: delivery.preview(self.project, first, span=self.span(0, 1)))

    def test_two_separate_drafts_share_the_same_boundary_action(self):
        first = self.fixture.write_draft('first.txt', self.unit(0, 1, [(None, 2), (1, 2)]))
        self.fixture.commit_fixture(first, 'first-take', span=self.span(0, 1))
        second = self.fixture.write_draft('second.txt', self.unit(1, 3, [(1, 2), (None, 2)]))
        self.assert_rejected(lambda: delivery.prepare(self.project, second, self.span(1, 3)))

    def test_replacing_one_unit_respects_unchanged_successor(self):
        first = self.unit(0, 1, [(None, 3), (1, 1)])
        second = self.unit(2, 3, [(2, 2), (None, 2)], 'u02')
        pair = self.fixture.write_draft('pair.txt', first + '\n' + second)
        result = self.fixture.commit_fixture(pair, 'pair-take', span=self.span(0, 3))
        revised = self.fixture.write_draft('revision.txt', self.unit(0, 1, [(None, 2), (1, 2)]))
        self.assert_rejected(lambda: delivery.prepare(self.project, revised, replace_ids=[result['ids'][0]]))

    def test_retry_of_vo_continuation_keeps_source_cue_before_saved_span(self):
        voice_record = self.records[6]['id']
        unit = self.unit(6, 7, [(6, 2), (None, 2)]).replace(
            '\n·02秒', f'\n声音[{voice_record},0.1-1.8]：@{{CHAR0001}}画外音：“谁？”\n·02秒')
        draft = self.fixture.write_draft('vo.txt', unit)
        self.fixture.commit_fixture(draft, 'vo-take', span=self.span(6, 7))
        snapshot = self.fixture.snapshot()
        retry = delivery.commit(self.project, draft, 'vo-take', self.fixture.review,
                                self.fixture.output, self.span(6, 7))
        self.assertEqual(retry['status'], 'already_committed')
        self.assertEqual(self.fixture.snapshot(), snapshot)

    def test_unrelated_historical_blackout_does_not_block_new_visible_shots(self):
        # Frozen history from a project that predates these checks.
        old = self.fixture.write_draft('old.txt', self.unit(0, 1, [(None, 2), (1, 2)]))
        self.fixture.commit_fixture(old, 'historical', span=self.span(0, 1))
        state = d.read_json(self.project / 'session.json')
        old_shot = state['units'][0]['shots'][-1]
        old_shot.update(end=7, text=old_shot['text'].replace('02秒-04秒', '02秒-07秒'))
        state['units'][0]['duration'] = 7
        d.write_json(self.project / 'session.json', state)
        for first, last in ((2, 3), (4, 4)):
            with self.subTest(first=first):
                next_draft = self.fixture.write_draft('next.txt', self.unit(first, last, [(None, 4)]))
                snapshot = self.fixture.snapshot()
                _, units, _ = delivery.prepare(self.project, next_draft, self.span(first, last))
                self.assertEqual(len(units), 1)
                self.assertEqual(self.fixture.snapshot(), snapshot)

    def test_legacy_boundary_without_parsed_shots_preserves_new_validation(self):
        state = d.read_json(self.project / 'session.json')
        state['units'].append({'id': 'legacy', 'span': self.span(0, 1), 'main_scene': 'SC0001',
                               'output_format': 'reference-v1', 'duration': 4})
        d.write_json(self.project / 'session.json', state)
        draft = self.fixture.write_draft('later.txt', self.unit(2, 3, [(2, 2), (None, 2)]))
        snapshot = self.fixture.snapshot()
        _, units, _ = delivery.prepare(self.project, draft, self.span(2, 3))
        self.assertEqual(len(units), 1)
        self.assertEqual(self.fixture.snapshot(), snapshot)


if __name__ == '__main__':
    unittest.main()
