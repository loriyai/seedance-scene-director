"""Synthetic, read-only scene-context integration tests."""
import unittest

import director as d
import storyboard_context as context
import storyboard_text as core
import test_storyboard_text_delivery as fixtures


class SceneContextTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TextDeliveryIntegration('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project

    def test_cli_exports_only_one_scene_and_leaves_project_byte_identical(self):
        before = self.fixture.snapshot()
        result = self.fixture.cli('storyboard_text_delivery.py', 'context',
                                  '--project', self.project, '--scene', 'SC0001')
        self.assertEqual(before, self.fixture.snapshot())
        expected = [r for r in self.fixture.state['records'] if r['scene'] == 'SC0001']
        self.assertEqual([r['text'] for r in result['records']], [r['text'] for r in expected])
        self.assertEqual(result['allowed_asset_ids'], {
            'characters': ['CHAR0001'], 'scene_assets': ['SET0001'],
            'props': ['PROP0001', 'PROP0002']})
        self.assertNotIn('SET0002', [a['id'] for a in result['assets']])
        self.assertIn('陈默：把消息发给@群里。', result['source_lines']['text'])
        self.assertEqual(result['versions']['context_sha256'], context.context_hash(d.load(self.project)))
        self.assertEqual(result['semantic_review'], 'not_performed')
        self.assertEqual(result['style'], '3D国漫')

    def test_partial_context_preserves_original_dialogue_and_exact_slice(self):
        record = self.fixture.state['records'][1]
        span = [record['start'] + 1, record['end'] - 1]
        result = context.scene_context(self.project, 'SC0001', span)
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['text'], record['text'])
        self.assertEqual(result['records'][0]['selected_text'], d.portion(record, *span))
        self.assertEqual(result['records'][0]['selected_span'], span)
        self.assertEqual(result['span'], span)
        with self.assertRaisesRegex(ValueError, '指定场次'):
            context.scene_context(self.project, 'SC0001', [0, self.fixture.state['length']])

    def test_appearance_transitions_and_prompt_only_use_parser_legal_references(self):
        state = d.load(self.project)
        state['ledger']['characters'].extend([
            {'id': 'CHAR0002', 'name': '陈默-戴帽'},
            {'id': 'CHAR0003', 'name': '人群', 'visual_mode': 'prompt_only'}])
        scene = state['ledger']['scenes'][0]
        scene['character_ids'].append('CHAR0003')
        scene['character_appearances'] = [{'initial_asset_id': 'CHAR0002', 'transitions': [
            {'from_asset_id': 'CHAR0002', 'to_asset_id': 'CHAR0001'}]}]
        d.write_json(self.project / 'session.json', state)
        result = context.scene_context(self.project, 'SC0001')
        exported = set(result['allowed_asset_ids']['characters'])
        self.assertEqual(exported, core._scene_characters(scene))
        self.assertEqual(exported, {'CHAR0001', 'CHAR0002', 'CHAR0003'})
        self.assertEqual(next(a for a in result['assets'] if a['id'] == 'CHAR0003')['visual_mode'], 'prompt_only')
        # The parser accepts exactly the exported appearance variant, not an
        # independently maintained list in the context code.
        draft = self.fixture.authored_text(self.fixture.state['records'][:2]).replace('CHAR0001', 'CHAR0002')
        self.assertEqual(core.parse(draft, state)[0]['character_ids'], ['CHAR0002'])

    def test_neighbour_comes_from_real_delivered_text_and_reports_adjacency(self):
        self.fixture.commit_fixture()
        before = self.fixture.snapshot()
        result = context.scene_context(self.project, 'SC0002')
        previous = result['neighbours']['previous']
        self.assertEqual(len(previous), 1)
        self.assertTrue(result['neighbours']['previous_is_adjacent'])
        self.assertEqual(previous[0]['span'][1], result['span'][0])
        self.assertIn('source_text', previous[0])
        self.assertEqual(result['neighbours']['next'], [])
        self.assertEqual(before, self.fixture.snapshot())

    def test_stale_input_and_missing_scene_fail_without_writes(self):
        before = self.fixture.snapshot()
        with self.assertRaisesRegex(ValueError, '场次不存在'):
            context.scene_context(self.project, 'SC9999')
        self.assertEqual(before, self.fixture.snapshot())
        (self.project / 'script.txt').write_text('changed', encoding='utf-8')
        before = self.fixture.snapshot()
        with self.assertRaisesRegex(ValueError, '已变化'):
            context.scene_context(self.project, 'SC0001')
        self.assertEqual(before, self.fixture.snapshot())


if __name__ == '__main__':
    unittest.main()
