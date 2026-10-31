"""Read-only author-prefix checks never create a preview or delivery approval."""
import unittest

import storyboard_text_delivery as delivery
import test_storyboard_text_delivery as fixtures


class IncrementalAuthorChecks(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.TextDeliveryIntegration()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def draft(self, text):
        return self.fx.write_draft('in-progress.txt', text)

    def test_completed_unit_prefix_checks_without_any_file_mutation(self):
        draft = self.draft(self.fx.authored_text(self.fx.state['records'][:1]))
        before = self.fx.snapshot()
        result = delivery.check_incremental(self.fx.project, draft)
        self.assertEqual(result['status'], 'incremental_checked')
        self.assertEqual(result['validation_scope'], 'authored_prefix_only')
        self.assertEqual(result['semantic_review'], 'not_performed')
        self.assertFalse(result['delivery_eligible'])
        self.assertEqual(result['span'], [0, self.fx.state['records'][0]['end']])
        self.assertEqual(self.fx.snapshot(), before)
        with self.assertRaisesRegex(ValueError, '完整场景'):
            delivery.prepare(self.fx.project, draft)

    def test_one_finished_short_shot_is_checked_but_cannot_be_delivered(self):
        text = self.fx.authored_text(self.fx.state['records'][:1]).replace('00秒-04秒', '00秒-02秒')
        draft = self.draft(text)
        result = delivery.check_incremental(self.fx.project, draft)
        self.assertEqual(result['total_duration'], 2)
        self.assertEqual(result['shot_count'], 1)
        self.assertFalse(result['delivery_eligible'])
        with self.assertRaisesRegex(ValueError, '4～30'):
            delivery.prepare(self.fx.project, draft)

    def test_final_dialogue_may_be_exact_source_prefix_only(self):
        text = self.fx.authored_text(self.fx.state['records'][:2])
        text = text.replace('：“把消息发给@群里。”', '：“把消息”')
        draft = self.draft(text)
        before = self.fx.snapshot()
        result = delivery.check_incremental(self.fx.project, draft)
        self.assertLess(result['span'][1], self.fx.state['records'][1]['end'])
        self.assertEqual(self.fx.snapshot(), before)
        with self.assertRaisesRegex(ValueError, '逐字一致'):
            delivery.prepare(self.fx.project, draft)

    def test_unfinished_tail_does_not_add_unwritten_boundary_punctuation(self):
        text = self.fx.authored_text(self.fx.state['records'][:2])
        for fragment in ('把消息', '把消息发给@群里'):
            with self.subTest(fragment=fragment):
                draft = self.draft(text.replace('：“把消息发给@群里。”', '：“' + fragment + '”'))
                before = self.fx.snapshot()
                result = delivery.check_incremental(self.fx.project, draft)
                self.assertEqual(result['status'], 'incremental_checked')
                self.assertEqual(self.fx.snapshot(), before)
                with self.assertRaisesRegex(ValueError, '逐字一致'):
                    delivery.prepare(self.fx.project, draft)

    def test_prefix_cannot_skip_scene_start_or_change_source_words(self):
        draft = self.draft(self.fx.authored_text(self.fx.state['records'][1:2]))
        with self.assertRaisesRegex(ValueError, '起点|场首|首记录'):
            delivery.check_incremental(self.fx.project, draft)
        text = self.fx.authored_text(self.fx.state['records'][:2]).replace(
            '：“把消息发给@群里。”', '：“把错误消息”')
        draft.write_text(text, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '前缀|逐字'):
            delivery.check_incremental(self.fx.project, draft)

    def test_explicit_source_start_allows_authorized_local_prefix(self):
        record = self.fx.state['records'][1]
        draft = self.draft(self.fx.authored_text([record]))
        result = delivery.check_incremental(self.fx.project, draft, [record['start'], record['end']])
        self.assertEqual(result['span'], [record['start'], record['end']])
        self.assertFalse(result['delivery_eligible'])

    def test_only_last_unit_can_be_shorter_than_four_seconds(self):
        text = self.fx.authored_text(self.fx.state['records'][:2]).replace(
            '00秒-04秒', '00秒-02秒', 1)
        with self.assertRaisesRegex(ValueError, '4～30'):
            delivery.check_incremental(self.fx.project, self.draft(text))

    def test_incremental_cli_is_read_only_and_not_a_preview_event(self):
        draft = self.draft(self.fx.authored_text(self.fx.state['records'][:1]))
        before = self.fx.snapshot()
        result = self.fx.cli('storyboard_text_delivery.py', 'check', '--incremental',
                             '--project', self.fx.project, '--draft', draft)
        self.assertEqual(result['status'], 'incremental_checked')
        self.assertEqual(before, self.fx.snapshot())

    def test_incremental_report_cannot_be_used_as_scene_review_input(self):
        with self.assertRaisesRegex(ValueError, '增量检查'):
            delivery.verify_review({}, {'validation_scope': 'authored_prefix_only'})

    def test_incremental_never_bypasses_missing_refs_or_impossible_dialogue(self):
        text = self.fx.authored_text(self.fx.state['records'][:1]).replace(
            '人物站位：@{CHAR0001}在画面左侧。', '人物站位：陈默在画面左侧。')
        with self.assertRaisesRegex(ValueError, 'REFERENCE|引用'):
            delivery.check_incremental(self.fx.project, self.draft(text))
        blocks = self.fx.authored_text(self.fx.state['records'][:2]).strip().split('\n\n')
        blocks[1] = blocks[1].replace('00秒-04秒', '00秒-01秒').replace('0.2-2.8', '0.1-0.9')
        with self.assertRaisesRegex(ValueError, 'DIALOGUE_CAPACITY'):
            delivery.check_incremental(self.fx.project, self.draft('\n\n'.join(blocks)))

    def test_multiscene_prefix_rejected_without_changing_default_complete_route(self):
        with self.assertRaisesRegex(ValueError, '一场|单场'):
            delivery.check_incremental(self.fx.project, self.fx.draft)
        _, _, result = delivery.prepare(self.fx.project, self.fx.draft)
        self.assertEqual(result['status'], 'checked')
        self.assertNotIn('validation_scope', result)


if __name__ == '__main__':
    unittest.main()
