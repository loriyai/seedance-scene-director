"""Source-bound joint speech through the normal text and background queue path."""
import copy
import unittest

import storyboard_text as core
from test_storyboard_text import fixture, renderable


class JointVoiceTests(unittest.TestCase):
    def joint(self, count=2):
        state, draft = fixture()
        names = ['阿青', '路人-群像', '阿白'][:count]
        ids = ['CHAR0001', 'CHAR0002', 'CHAR0003'][:count]
        if count == 3:
            state['ledger']['scenes'][0]['character_ids'].append(ids[-1])
        next(r for r in state['records'] if r['kind'] == 'dialogue')['speaker'] = '、'.join(names)
        refs = '、'.join('@{' + cid + '}' for cid in ids)
        draft = draft.replace('人物：@{CHAR0001}；', '人物：' + refs + '；')
        draft = draft.replace(']：@{CHAR0001}说', ']：' + refs + '说')
        return state, draft, names, ids

    def test_two_and_three_speakers_render_one_verbatim_event(self):
        for count in (2, 3):
            with self.subTest(count=count):
                state, draft, names, ids = self.joint(count)
                units = core.parse(draft, state)
                part = units[0]['shots'][0]['dialogue_parts'][0]
                self.assertEqual(part['character_ids'], ids)
                output = core.render_unit(state, renderable(units[0]))
                self.assertIn('、'.join(names) + '说', output)
                self.assertEqual(output.count('：“别动，等我回来。”'), 1)
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0]['duration'], 5)

    def test_joint_mapping_rejects_omission_order_duplicates_and_unlisted(self):
        state, draft, _, _ = self.joint(3)
        source = '@{CHAR0001}、@{CHAR0002}、@{CHAR0003}说'
        for replacement in ('@{CHAR0001}、@{CHAR0002}说',
                            '@{CHAR0002}、@{CHAR0001}、@{CHAR0003}说',
                            '@{CHAR0001}、@{CHAR0001}、@{CHAR0003}说',
                            '@{CHAR0001}、@{CHAR0002}、@{CHAR9999}说'):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                core.parse(draft.replace(source, replacement), state)
        wrong_scene = copy.deepcopy(state)
        wrong_scene['ledger']['scenes'][0]['character_ids'].remove('CHAR0003')
        with self.assertRaises(ValueError):
            core.parse(draft, wrong_scene)

    def test_joint_never_relaxes_verbatim_voice_or_non_character_rules(self):
        state, draft, _, _ = self.joint()
        for wrong in (draft.replace('别动，等我回来。', '别动，等我回来！'),
                      draft.replace('}说', '}内心想法'),
                      draft.replace('@{CHAR0001}、@{CHAR0002}说', '阿青、路人-群像说')):
            with self.assertRaises(ValueError):
                core.parse(wrong, state)
        non_character = copy.deepcopy(state)
        record = next(r for r in non_character['records'] if r['kind'] == 'dialogue')
        record.update(source_kind='non_character', voice='非人物播报')
        with self.assertRaises(ValueError):
            core.parse(draft.replace('}说', '}非人物播报'), non_character)

    def test_single_speaker_schema_and_render_stay_compatible(self):
        state, draft = fixture()
        unit = core.parse(draft, state)[0]
        part = unit['shots'][0]['dialogue_parts'][0]
        self.assertEqual(part['character_id'], 'CHAR0001')
        self.assertNotIn('character_ids', part)
        output = core.render_unit(state, renderable(unit))
        self.assertIn('@阿青-基础形象 说', output)


if __name__ == '__main__':
    unittest.main()
