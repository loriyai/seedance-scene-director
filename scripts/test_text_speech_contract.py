"""Source-bound readings and overlaps: isolated fixtures, no media generation."""
import copy
import json
import unittest
from unittest.mock import patch

import director as d
import storyboard_text as core
import storyboard_text_delivery as delivery
from speech_timing_audit import text_dialogue_capacity
from test_storyboard_text import fixture, renderable


def annotation(value):
    return '声音依据：' + json.dumps(value, ensure_ascii=False)


def reading(text, units, occurrence=1):
    return {'text': text, 'occurrence': occurrence, 'units': units}


class ReadingContractTests(unittest.TestCase):
    def candidate(self, words, readings=None, window='0-3'):
        state, draft = fixture(words)
        draft = draft.replace('0.2-2.8', window)
        if readings is not None:
            lines = draft.splitlines()
            index = next(i for i, line in enumerate(lines) if line.startswith('声音['))
            lines.insert(index + 1, annotation({'readings': readings}))
            draft = '\n'.join(lines) + '\n'
        return state, draft

    def test_mixed_readings_keep_dialogue_and_render_without_internal_annotations(self):
        words = '看API，收10000元。'
        state, draft = self.candidate(words, [reading('API', ['诶', '批', '爱']), reading('10000', ['一', '万'])])
        before = copy.deepcopy(state), draft
        units = core.parse(draft, state)
        report = text_dialogue_capacity(units)
        self.assertEqual(report['status'], 'checked', report)
        fact = report['speech_window_diagnostics']['shots'][0]['fragments'][0]
        self.assertEqual(fact['phonetic_units'], 8)
        rendered = core.render_unit(state, renderable(units[0]))
        self.assertIn('：“' + words + '”', rendered)
        self.assertNotIn('声音依据', rendered)
        self.assertNotIn('readings', rendered)
        self.assertEqual((state, draft), before)
        core.validate(state, units)

    def test_missing_reading_cannot_be_replaced_by_raw_character_count(self):
        state, draft = self.candidate('收到API。')
        report = text_dialogue_capacity(core.parse(draft, state))
        self.assertIn('SPEECH_READING_REQUIRED', {e['code'] for e in report['errors']})
        self.assertEqual(report['semantic_review'], 'not_performed')

    def test_longer_pronunciation_fails_its_actual_window(self):
        state, draft = self.candidate('W', [reading('W', ['达', '不', '溜'])], '0-0.2')
        report = text_dialogue_capacity(core.parse(draft, state))
        codes = {e['code'] for e in report['errors']}
        self.assertNotIn('DIALOGUE_CAPACITY', codes)
        self.assertNotIn('SPEECH_WINDOW_CAPACITY', codes)
        self.assertIn('SPEECH_READING_CAPACITY', codes)

    def test_shorter_reading_retains_raw_screening_without_raw_rejection(self):
        state, draft = self.candidate('10000', [reading('10000', ['一', '万'])], '0-0.4')
        report = text_dialogue_capacity(core.parse(draft, state))
        self.assertIn('SPEECH_WINDOW_CAPACITY', {e['code'] for e in report['warnings']})
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['status'], 'checked')
        self.assertEqual(report['semantic_review'], 'not_performed')

    def test_whole_shot_screening_alone_cannot_reject_declared_reading(self):
        from storyboard_text_delivery import require_dialogue_capacity
        state, draft = self.candidate('thoroughly', [reading('thoroughly', ['thur', 'uh', 'lee'])], '0-1')
        units = core.parse(draft, state)
        units[0]['shots'][0]['end'] = 1  # Pure capacity fixture; no rendered shot is modified.
        report = require_dialogue_capacity(units)
        self.assertIn('DIALOGUE_CAPACITY', {e['code'] for e in report['warnings']})
        self.assertEqual(report['errors'], [])
        self.assertFalse(report['mechanical_screening']['blocking'])
        self.assertFalse(report['mechanical_screening']['acceptance_standard'])
        self.assertEqual(report['semantic_review'], 'not_performed')

    def test_punctuation_inside_complete_reading_does_not_split_its_target(self):
        examples = [('1,000', ['一', '千']), ("don't", ['dont']),
                    ('R&D', ['ar', 'and', 'dee']), ('C++', ['see', 'plus', 'plus'])]
        for word, syllables in examples:
            with self.subTest(word=word):
                state, draft = self.candidate(word, [reading(word, syllables)], '0-1')
                units = core.parse(draft, state)
                report = text_dialogue_capacity(units)
                self.assertEqual(report['errors'], [])
                fact = report['speech_window_diagnostics']['shots'][0]['fragments'][0]
                self.assertAlmostEqual(fact['phonetic_minimum'], len(syllables) / 6)
                self.assertEqual(report['utterance_diagnostics']['records'][0]['phonetic_minimum'],
                                 fact['phonetic_minimum'])
                self.assertIn('：“' + word + '”', core.render_unit(state, renderable(units[0])))

    def test_reading_targets_and_units_are_checked(self):
        for rows in ([reading('API', ['一'])], [reading('10000', ['一万'])],
                     [reading('10000', ['一', '万'], occurrence=2)],
                     [reading('10000', ['一', '万']), reading('10000', ['一', '万'])]):
            state, draft = self.candidate('10000', rows)
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                core.parse(draft, state)

    def test_repeated_tokens_require_their_actual_occurrences(self):
        state, draft = self.candidate('A和A', [reading('A', ['诶'])])
        report = text_dialogue_capacity(core.parse(draft, state))
        missing = next(e['missing'] for e in report['errors'] if e['code'] == 'SPEECH_READING_REQUIRED')
        self.assertEqual(missing, [{'text': 'A', 'occurrence': 2}])

    def test_basis_must_immediately_follow_its_voice_and_is_not_repeatable(self):
        state, draft = self.candidate('A', [reading('A', ['诶'])])
        line = next(line for line in draft.splitlines() if line.startswith('声音依据'))
        for changed in (draft.replace(line, line + '\n' + line), line + '\n' + draft):
            with self.assertRaises(ValueError):
                core.parse(changed, state)

    def test_cross_shot_utterance_keeps_exact_text_and_reports_real_gap(self):
        state, draft = fixture('别动，等我回来。')
        draft = draft.replace('声音[L000004,0.2-2.8]：@{CHAR0001}说（压低音量）：“别动，等我回来。”',
                              '声音[L000004,0-0.5]：@{CHAR0001}说：“别动，”')
        draft += '声音[L000004,3-4]：@{CHAR0001}说：“等我回来。”\n'
        report = text_dialogue_capacity(core.parse(draft, state))
        self.assertEqual(report['status'], 'checked', report)
        utterance = report['utterance_diagnostics']['records'][0]
        self.assertEqual(utterance['text'], '别动，等我回来。')
        self.assertEqual(utterance['speech_seconds'], 1.5)
        self.assertEqual(utterance['between_parts'], [2.5])
        self.assertEqual(utterance['mechanical_minimum'], 1.3)


class OverlapContractTests(unittest.TestCase):
    def candidate(self):
        state, original = fixture()
        state['ledger']['scenes'][0]['character_ids'].append('CHAR0003')
        source = ('场景1-日-内-房间\n人物：阿青-基础形象；阿白-基础形象；\n'
                  '△ 两人同时说话。\n阿青：我已经说过这件事了。\n阿白：你先听我把话说完吧。\n')
        state['records'], state['length'] = d.build_records(source, state['ledger'])
        lines = original.splitlines()[:5]
        lines[1] = '人物：@{CHAR0001}、@{CHAR0003}；'
        lines[4] = lines[4].replace('00秒-03秒', '00秒-04秒')
        lines.extend([
            '声音[L000004,0-2]：@{CHAR0001}说：“我已经说过这件事了。”',
            '声音[L000005,0.2-2.2]：@{CHAR0003}说（叠在对方话中接入，自己的声音较低）：“你先听我把话说完吧。”',
            annotation({'overlap': {'with': ['L000004'], 'source_record': 'L000003', 'quote': '两人同时说话'}}),
        ])
        return state, '\n'.join(lines) + '\n'

    def test_source_bound_overlap_survives_parse_capacity_validation_and_render(self):
        state, draft = self.candidate()
        units = core.parse(draft, state)
        report = text_dialogue_capacity(units)
        self.assertEqual(report['status'], 'checked', report)
        core.validate(state, units)
        text = core.render_unit(state, renderable(units[0]))
        self.assertIn('叠在对方话中接入', text)
        self.assertEqual(text.count('：“我已经说过这件事了。”'), 1)
        self.assertEqual(text.count('：“你先听我把话说完吧。”'), 1)
        self.assertNotIn('声音依据', text)

    def test_short_shot_checks_concurrent_sources_separately(self):
        state, draft = self.candidate()
        units = core.parse(draft, state)
        shot = units[0]['shots'][0]
        shot['end'] = 2.2  # Pure capacity fixture; parser still requires integer production shots.
        report = text_dialogue_capacity(units)
        self.assertEqual(report['status'], 'checked', report)
        shot['dialogue_parts'][1]['start'] = 2
        shot['dialogue_parts'][1]['end'] = 4
        shot['dialogue_parts'][1].pop('speech_basis')
        shot['end'] = 3
        report = text_dialogue_capacity(units)
        self.assertIn('DIALOGUE_CAPACITY', {e['code'] for e in report['warnings']})

    def test_undeclared_unbound_and_unused_overlap_are_rejected(self):
        state, draft = self.candidate()
        basis = next(line for line in draft.splitlines() if line.startswith('声音依据'))
        for changed in (draft.replace(basis, ''), draft.replace('两人同时说话"', '无人说话"'),
                        draft.replace('"with": ["L000004"]', '"with": ["L000099"]'),
                        draft.replace('L000005,0.2-2.2', 'L000005,2-4')):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                core.parse(changed, state)

    def test_same_character_different_appearance_cannot_overlap_itself(self):
        state, draft = self.candidate()
        state['ledger']['characters'][2]['source_speaker'] = '阿青'
        state['records'][-1]['speaker'] = '阿青'
        with self.assertRaisesRegex(ValueError, '同一声源'):
            core.parse(draft, state)

    def test_overlap_does_not_authorize_missing_dialogue_or_changed_words(self):
        state, draft = self.candidate()
        with self.assertRaisesRegex(ValueError, '逐字一致'):
            core.parse(draft.replace('你先听我把话说完吧。', '你听我说。'), state)

    def test_cross_shot_interleaving_requires_real_overlap_evidence(self):
        state, draft = self.candidate()
        first = '我已经说过这件事了。'
        draft = draft.replace('00秒-04秒', '00秒-02秒')
        draft = draft.replace('0-2]：', '0-1.8]：').replace(first, '我已经说过')
        draft = draft.replace('L000005,0.2-2.2', 'L000005,0.2-2')
        _, original = fixture()
        shot = original.splitlines()[-1].replace('03秒-05秒', '02秒-04秒')
        draft += shot + '\n声音[L000004,2-3]：@{CHAR0001}说：“这件事了。”\n'
        units = core.parse(draft, state)
        self.assertEqual(text_dialogue_capacity(units)['status'], 'checked')
        self.assertEqual(''.join(p['text'] for s in units[0]['shots'] for p in s['dialogue_parts']
                                 if p['record'] == 'L000004'), first)


class SpeechDeliveryTests(unittest.TestCase):
    def test_narrow_window_blocks_preview_and_incremental_without_writing_success(self):
        from test_storyboard_text_delivery import TextDeliveryIntegration
        fx = TextDeliveryIntegration()
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        draft = fx.write_draft('narrow.txt', fx.authored_text().replace('0.2-2.8', '2.7-2.8'))
        before = fx.snapshot()
        with self.assertRaises(ValueError):
            delivery.preview(fx.project, draft)
        self.assertEqual(fx.snapshot(), before)
        prefix = fx.write_draft('narrow-prefix.txt', fx.authored_text(fx.state['records'][:2]).replace('0.2-2.8', '2.7-2.8'))
        with self.assertRaises(ValueError):
            delivery.check_incremental(fx.project, prefix)

    def test_cloud_pause_does_not_block_local_commit(self):
        from test_storyboard_text_delivery import TextDeliveryIntegration
        import canvas_jobs
        fx = TextDeliveryIntegration()
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        (fx.project / 'canvas').mkdir()
        d.write_json(fx.project / 'canvas' / 'control.json', {'paused': True})
        original = (fx.project / 'canvas' / 'control.json').read_bytes()
        with patch.object(canvas_jobs, 'enqueue', side_effect=AssertionError('Local commit must not queue')):
            fx.commit_fixture()
        self.assertTrue(fx.output.is_file())
        self.assertEqual((fx.project / 'canvas' / 'control.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
