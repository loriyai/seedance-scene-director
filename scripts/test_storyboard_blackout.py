"""Black-frame acceptance cases exercised through the installed delivery command."""
import json
from pathlib import Path
import unittest

import director as d
import storyboard_text_delivery as delivery
import storyboard_delivery_timing as timing
import test_storyboard_text_delivery as fixtures


class BlackoutDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.TextDeliveryIntegration('runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def script_action(self, text):
        fx = self.fx
        script = '场景1-日-内-房间\n人物：陈默-基础形象；\n' + ''.join(
            '△ ' + line + '\n' for line in text.splitlines())
        (fx.project / 'script.txt').write_text(script, encoding='utf-8')
        (fx.project / 'scenes/fixture.txt').write_text(script, encoding='utf-8')
        d.write_json(fx.project / 'source.json', {'paragraphs': [{'id': 1, 'text': script}]})
        state = fx.state
        state['records'], state['length'] = d.build_records(script, fx.ledger)
        state['drafts'] = {'fixture.txt': d.sha(script)}
        for key in ('script', 'source'):
            state['files'][key]['sha256'] = d.file_hash(state['files'][key]['path'])
        d.write_json(fx.project / 'session.json', state)
        self.rid = state['records'][0]['id']

    def visible(self, transition='硬切', body=None):
        body = body or '相机向右横移，桌边的@{CHAR0001}抬起手，窗框留在焦外。'
        return f'（@{{CHAR0001}}的中景；转场：{transition}）{body}音效：衣料轻响。'

    def black(self):
        return f'（黑场[{self.rid}-{self.rid}]；转场：切黑）纯黑画面，无可见对象。音效：风声。'

    def partial(self, start, end):
        return self.visible(f'切黑[{self.rid}-{self.rid},{start}-{end}秒]')

    def unit(self, key, shots):
        rows = [f'## SC0001 / {key} / {self.rid}-{self.rid}', '人物：@{CHAR0001}；',
                '场景：@{SET0001}；时间：白天；天气：未明示；',
                '人物站位：@{CHAR0001}站在窗边。']
        start = 0
        for index, (duration, picture) in enumerate(shots, 1):
            end = start + duration
            rows.append(f'·{start:02d}秒-{end:02d}秒[镜头{index}-窗边动静]：{picture}')
            start = end
        return '\n'.join(rows) + '\n'

    def draft(self, *units):
        self.fx.draft.write_text('\n'.join(units), encoding='utf-8')

    def cli(self, command, *extra, ok=True):
        return self.fx.cli('storyboard_text_delivery.py', command, '--project', self.fx.project,
                           '--draft', self.fx.draft, *extra, success=ok)

    def test_no_cut_fainting_and_room_darkness_do_not_authorize_black(self):
        self.script_action('陈默晕倒，灯熄灭，窗框依稀可见。')
        for picture in (self.black(), self.partial(0, 0.5),
                        self.visible(body='画面全黑，无可见对象。')):
            self.draft(self.unit('u01', [(1, picture), (3, self.visible())]))
            with self.subTest(picture=picture):
                self.assertIn('黑', str(self.cli('check', ok=False)))
        self.draft(self.unit('u01', [(4, self.visible(body='灯熄灭，@{CHAR0001}的侧脸仍被窗光照亮。'))]))
        self.cli('check')

    def test_cross_unit_overage_rejected_by_all_new_draft_commands(self):
        self.script_action('【画面切黑】')
        self.draft(self.unit('u01', [(2, self.visible()), (2, self.black())]),
                   self.unit('u02', [(2, self.black()), (2, self.visible())]))
        before = self.fx.snapshot()
        for cmd, args in [('check', []), ('check', ['--incremental']), ('preview', []),
                          ('commit', ['--review', self.fx.review, '--output', self.fx.output,
                                      '--request-id', 'synthetic-rejected'])]:
            with self.subTest(cmd=cmd, args=args):
                result = self.cli(cmd, *args, ok=False)
                self.assertIn('BLACKOUT_DURATION', json.dumps(result))
                self.assertIn('4秒', str(result))
        self.assertEqual(before, self.fx.snapshot())

    def test_exact_limit_and_visible_reset(self):
        self.script_action('【画面切黑】')
        self.draft(self.unit('u01', [(2, self.visible()), (2, self.black())]),
                   self.unit('u02', [(1, self.black()), (3, self.visible())]))
        self.cli('check')
        self.script_action('【画面切黑】\n窗边的手再次抬起。切黑。')
        first = self.unit('u01', [(2, self.visible()), (2, self.black())])
        self.rid = self.fx.state['records'][1]['id']
        self.draft(first, self.unit('u02', [(1, self.visible()), (3, self.black())]))
        self.cli('check')

    def test_fractional_intervals_count_across_shots_and_units(self):
        self.script_action('切黑。')
        first = self.unit('u01', [(4, self.partial('2.75', 4))])
        self.draft(first, self.unit('u02', [(4, self.partial(0, '1.75'))]))
        self.cli('check')
        self.draft(first, self.unit('u02', [(4, self.partial(0, '1.751'))]))
        self.assertIn('BLACKOUT_DURATION', json.dumps(self.cli('check', ok=False)))

    def test_visual_prose_may_describe_the_declared_interval(self):
        self.script_action('切黑。')
        marker = f'切黑[{self.rid}-{self.rid},1-2秒]'
        for body, ok in [('画面切黑，随后回到清晰的窗边。', True),
                         ('画面切黑，亮起，随后再次切黑。', False)]:
            self.draft(self.unit('u01', [(4, self.visible(marker, body))]))
            result = self.cli('check', ok=ok)
            if not ok:
                self.assertIn('BLACKOUT_TIMING', json.dumps(result))

    def test_a_device_and_a_quoted_cut_are_not_script_cues(self):
        for source in ('手机屏幕全黑，窗边的人仍然可见。', '纸上写着“切黑”。'):
            with self.subTest(source=source):
                self.script_action(source)
                self.draft(self.unit('u01', [(1, self.black()), (3, self.visible())]))
                self.assertIn('BLACKOUT_SOURCE', json.dumps(self.cli('check', ok=False)))

    def test_new_timed_transition_survives_delivery_export_and_retry(self):
        self.script_action('陈默在桌边抬起手。切黑。')
        self.draft(self.unit('u01', [(6, self.partial(3, 6))]))
        self.cli('check')
        preview = self.cli('preview')
        self.assertNotIn(self.rid, Path(preview['output']).read_text(encoding='utf-8'))
        self.fx.commit_fixture(request_id='synthetic-cut')
        state = d.load(self.fx.project)
        receipt = state['receipts'][0]
        self.assertEqual(timing.verify_receipt(receipt), receipt['source_units'])
        final = receipt['units'][0]
        self.assertIn(',3-6秒]', final['shots'][0]['text'])
        self.assertIn(final['shots'][0]['text'], final['source_text'])
        delivered = self.fx.output.read_bytes()
        self.assertNotIn(self.rid.encode(), delivered)
        self.assertEqual(delivered.decode('utf-8'),
                         delivery.render_units(state, receipt['units'], historical_receipt=receipt))
        result = self.cli('commit', '--request-id', 'synthetic-cut',
                          '--review', self.fx.review, '--output', self.fx.output)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(delivered, self.fx.output.read_bytes())


if __name__ == '__main__':
    unittest.main()
