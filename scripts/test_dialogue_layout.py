"""Behavioral regression tests for complete utterances and continuity."""
import copy
import unittest
import dialogue_layout as dl

class DialogueLayoutChecks(unittest.TestCase):
    def sample(self, text='妈妈妈妈！我看到猪猪侠了！！', end=3.2, units=11, durations=(3,3)):
        shots=[]; cursor=0
        for length in durations:
            shots.append({'start':cursor,'end':cursor+length,'dialogue_parts':[]})
            cursor+=length
        return {'output_format':'reference-v1','audio':[{'record':'L1','text':text,'start':1,'end':end,'units':units,
                         'pause':dl.pause_seconds(text),'speed':'常速'}], 'shots':shots}

    def plan(self,u,pieces):
        u['dialogue_layout']=dl.LEGACY_POLICY
        for i,(text,start,end) in enumerate(pieces):
            u['shots'][i]['dialogue_parts']=[{'record':'L1','text':text,'start':start,'end':end}]
        return u

    def test_mid_clause_cuts_rejected_without_word_loss(self):
        cases = [
            ('predicate', '妈妈妈妈！我看到猪猪侠了！！', 4, 11,
             [('妈妈妈妈！我看到', 1, 3), ('猪猪侠了！！', 3, 4)]),
            ('capital', '这就是资本的力量吗？解决事情快得让我来不及后悔。', 5, 22,
             [('这就是资本的力量吗？解决事情', 1, 3), ('快得让我来不及后悔。', 3, 5)]),
            ('bed', '那宝宝就是想在床上了。', 4, 10,
             [('那宝宝就是想在床上', 1, 3), ('了。', 3, 4)]),
        ]
        for name, text, end, units, pieces in cases:
            with self.subTest(case=name):
                u = self.plan(self.sample(text, end=end, units=units), pieces)
                with self.assertRaisesRegex(ValueError, '词句中间'):
                    dl.validate(u)

    def test_isolated_tail_rejected_even_after_punctuation(self):
        u=self.plan(self.sample('想睡觉，了。',end=4,units=4), [('想睡觉，',1,3),('了。',3,4)])
        with self.assertRaisesRegex(ValueError,'孤立句尾'):
            dl.validate(u)

    def test_punctuation_only_rejected(self):
        u=self.plan(self.sample('救命！',end=4,units=2), [('救命',1,3),('！',3,4)])
        u.update(output_format='reference-v2', dialogue_layout=dl.POLICY)
        u['shots'][0]['dialogue_parts'][0]['breath_basis'] = '呼救词完整说完后的自然气口'
        with self.assertRaisesRegex(ValueError, '禁止把纯标点分配成独立台词'):
            dl.validate(u)

    def test_short_whole_utterance_requires_replanned_cut(self):
        with self.assertRaisesRegex(ValueError,'短句必须完整'):
            dl.attach(self.sample())
        u=self.sample(durations=(4,2))
        dl.attach(u)
        self.assertEqual([p['text'] for s in u['shots'] for p in s['dialogue_parts']], [u['audio'][0]['text']])
        self.assertFalse(any(s.get('dialogue_continuations') for s in u['shots']))

    def test_natural_short_sentence_cannot_be_stretched_to_cross(self):
        u=self.sample('那宝宝就是想在床上了。',end=7,units=10,durations=(3,4))
        with self.assertRaisesRegex(ValueError,'拉长'):
            dl.attach(u)

    def test_original_one_word_reply_is_valid(self):
        dl.attach(self.sample('好。',end=1.7,units=1))

    def test_natural_full_clause_boundary_is_valid(self):
        u=self.sample('别担心，我会回来。',end=4,units=7)
        dl.attach(u,{'L1':[{'text':'别担心，','start':1,'end':2.9},
                          {'text':'我会回来。','start':3,'end':4}]})

    def test_repeated_punctuation_is_one_pause(self):
        self.assertEqual(dl.pause_seconds('好！'),dl.pause_seconds('好！！'))
        self.assertEqual(dl.pause_seconds('真的？'),dl.pause_seconds('真的？！'))
        self.assertAlmostEqual(dl.pause_seconds('妈妈妈妈！我看到猪猪侠了！！'),.68)

    def test_long_quote_once_and_exact_continuation_links(self):
        text='我会把事情的来龙去脉完整告诉你，请你先听我说完。'
        u=self.sample(text,end=8,units=23,durations=(3,3,3))
        dl.attach(u)
        self.assertEqual(sum(len(s['dialogue_parts']) for s in u['shots']),1)
        self.assertEqual([s.get('dialogue_continuations') for s in u['shots'][1:]],
                         [[{'record':'L1','segment':0}],[{'record':'L1','segment':0}]])
        bad=copy.deepcopy(u);bad['shots'][1]['dialogue_continuations']=[]
        with self.assertRaisesRegex(ValueError,'续声引用'):
            dl.validate(bad)

    def test_render_shows_actual_speech_and_never_requotes_continuation(self):
        import director as d
        text='我会把事情的来龙去脉完整告诉你，请你先听我说完。'
        u=self.sample(text,end=8,units=23,durations=(3,3,3))
        dl.attach(u)
        u.update(id='场景1-分镜1',duration=9,style='3D国漫',character_ids=['CHAR1'],
                 scene_ids=['SC1'],time='白天',weather='未明示',output_format='reference-v1')
        u['global']='陈默站在桌前。'
        u['prop_ids']=[]
        u['audio'][0].update(character_id='CHAR1',voice='说')
        for i,sh in enumerate(u['shots']):
            sh.update(text='人物说话。',connector='承接上一镜。',focus=['CHAR1'],
                      framing='中景',label='诉说',transition='硬切',scene_id='SC1')
        state={'ledger':{'schema_version':3,'characters':[{'id':'CHAR1','name':'陈默-基础形象'}],
                         'props':[],'scene_assets':[{'id':'SET1','name':'房间'}],
                         'scenes':[{'id':'SC1','heading':'场景1-日-内-房间','scene_asset_id':'SET1',
                                    'prop_ids':[],'character_ids':['CHAR1']}]}}
        rendered=d.render_unit(state,u)
        self.assertEqual(rendered.count('：“'+text+'”'),1)
        self.assertIn('发声估时1秒至8秒',rendered)
        self.assertEqual(rendered.count('续声至8秒'),2)
        u['audio'][0]['timing_basis']='recording'
        u['audio'][0]['recording_evidence']='虚构回归样本：已经测量的声音区间'
        measured=d.render_unit(state,u)
        self.assertIn('实配音1秒至8秒',measured)
        self.assertNotIn('发声估时',measured)

    def test_new_requests_cannot_reuse_legacy_word_splitter(self):
        import director as d
        unit=self.sample()
        unit['output_format']='reference-v3'
        with self.assertRaisesRegex(ValueError,'semantic-v2'):
            d.validate({'style_hash':'style'}, {'mode':'replace','request_id':'test','units':[unit]})

    def test_v2_long_speech_requires_explicit_per_shot_chunks(self):
        text = '爹！娘！我捡到一本绝世秘籍《玄清功》，给我十年，我一定提着仇人的头，来祭拜你们。'
        u = self.sample(text, end=13, units=len(dl.compact(text)), durations=(3, 3, 3, 4))
        u['output_format'] = 'reference-v2'
        with self.assertRaisesRegex(ValueError, '完整落在本镜'):
            dl.attach(u)
        pieces = ['爹！娘！', '我捡到一本绝世秘籍《玄清功》，', '给我十年，我一定提着仇人的头，', '来祭拜你们。']
        dl.attach(u, {'L1': [{'text': p, 'start': a, 'end': b}
                             for p, a, b in zip(pieces, (1, 3, 6, 9), (3, 6, 9, 13))]})
        self.assertEqual(u['dialogue_layout'], 'semantic-v2')
        self.assertEqual(''.join(p['text'] for s in u['shots'] for p in s['dialogue_parts']), text)
        self.assertFalse(any(s.get('dialogue_continuations') for s in u['shots']))
        u['shots'][1]['dialogue_continuations'] = [{'record':'L1','segment':0}]
        with self.assertRaisesRegex(ValueError, '逐镜写实际台词'):
            dl.validate(u)

    def test_v3_inherits_semantic_v2_chunks_and_rejects_legacy_continuations(self):
        u = self.sample('别担心，我会回来。', end=4, units=7)
        u['output_format'] = 'reference-v3'
        dl.attach(u, {'L1': [{'text': '别担心，', 'start': 1, 'end': 2.9},
                            {'text': '我会回来。', 'start': 3, 'end': 4}]})
        self.assertEqual(u['dialogue_layout'], 'semantic-v2')
        self.assertEqual(''.join(p['text'] for s in u['shots'] for p in s['dialogue_parts']), u['audio'][0]['text'])
        u['shots'][1]['dialogue_continuations'] = [{'record': 'L1', 'segment': 0}]
        with self.assertRaisesRegex(ValueError, '逐镜写实际台词'):
            dl.validate(u)

    def test_v2_render_quotes_only_the_words_spoken_in_each_shot(self):
        import director as d
        u = self.sample('别担心，我会回来。', end=4, units=7)
        u.update(output_format='reference-v2', id='场景1-分镜1', duration=6,
                 style='3D国漫', character_ids=['CHAR1'], prop_ids=[], scene_ids=['SC1'],
                 time='白天', weather='未明示')
        u['global'] = '@{CHAR1} 站在桌前。'
        u['audio'][0].update(character_id='CHAR1', voice='说')
        dl.attach(u, {'L1':[{'text':'别担心，','start':1,'end':2.9},
                           {'text':'我会回来。','start':3,'end':4}]})
        for shot in u['shots']:
            shot.update(focus=['CHAR1'], framing='中景', label='告别', transition='硬切',
                        scene_id='SC1', text='平视四十五度斜侧固定机位；人物看向桌前。')
        state={'ledger':{'schema_version':3,'characters':[{'id':'CHAR1','name':'陈默-第二世-林默-基础形象'}],
                         'props':[], 'scene_assets':[{'id':'SET1','name':'房间'}],
                         'scenes':[{'id':'SC1','heading':'场景1-日-内-房间','scene_asset_id':'SET1',
                                    'prop_ids':[],'character_ids':['CHAR1']}]}}
        rendered=d.render_unit(state,u)
        shots=[line for line in rendered.splitlines() if line.startswith('·')]
        self.assertIn('说：“别担心，”', shots[0])
        self.assertNotIn('我会回来', shots[0])
        self.assertIn('说：“我会回来。”', shots[1])
        self.assertNotIn('发声估时', rendered)
        self.assertNotIn('续声', rendered)
        self.assertIn('转场：硬切，平视四十五度斜侧固定机位', shots[0])
