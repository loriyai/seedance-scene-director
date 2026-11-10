"""Offline unit-lighting contracts; fixture prose is not production approval."""
import copy
import re
import unittest
from unittest.mock import patch

import canvas_transfer
import director as d
import source_background_slots as background
import source_references
import source_text_voices
import storyboard_authoring_gate as gate
import storyboard_lighting as lighting
import storyboard_text as core
import storyboard_text_delivery as delivery
from test_storyboard_text import fixture, renderable
import test_storyboard_text_delivery as delivery_fixtures


BASELINE = '光照：房间既有窗侧漫射光从门框对面的窗向室内进入，照亮窗边桌沿，背窗区域保留深暗。'


def with_baseline(text, line=BASELINE):
    lines = text.splitlines()
    index = next(i for i, value in enumerate(lines) if value.startswith('人物站位：'))
    lines.insert(index + 1, line)
    return '\n'.join(lines) + '\n'


class UnitLightingSyntaxTests(unittest.TestCase):
    def setUp(self):
        self.state, self.legacy = fixture()
        self.text = with_baseline(self.legacy)

    def parse(self, text=None):
        return core.parse(text or self.text, self.state, _current_lighting=True)

    def test_baseline_is_source_bound_and_rendered_once(self):
        units = self.parse()
        self.assertEqual(units[0]['lighting'], BASELINE.removeprefix('光照：'))
        self.assertEqual(units[0]['lighting_policy'], lighting.POLICY)
        before = copy.deepcopy(units)
        result = core.validate(self.state, units)
        self.assertEqual(result['semantic_review'], 'not_performed')
        output = core.render_unit(self.state, renderable(units[0]))
        self.assertEqual(output.count(BASELINE), 1)
        self.assertLess(output.index('人物站位：'), output.index(BASELINE))
        self.assertLess(output.index(BASELINE), output.index('·00秒'))
        self.assertEqual(units, before)

    def test_missing_line_cannot_fall_back_even_with_old_session(self):
        self.assertNotIn(lighting.SESSION_FIELD, self.state)
        with self.assertRaises(core.TextValidationError) as caught:
            self.parse(self.legacy)
        self.assertIn('AUTHORING_LIGHTING_UNDER_SPECIFIED', {e['code'] for e in caught.exception.errors})

    def test_new_binding_policy_also_enforces_low_level_parse(self):
        self.state[lighting.SESSION_FIELD] = lighting.POLICY
        with self.assertRaises(core.TextValidationError):
            core.parse(self.legacy, self.state)

    def test_empty_duplicate_and_misplaced_lines_are_rejected(self):
        variants = [self.text.replace(BASELINE, '光照：'),
                    self.text.replace(BASELINE, BASELINE + '\n' + BASELINE),
                    self.legacy + BASELINE + '\n',
                    BASELINE + '\n' + self.legacy]
        for text in variants:
            with self.subTest(text=text[-80:]), self.assertRaises(ValueError):
                self.parse(text)

    def test_metadata_cannot_override_or_remove_authored_baseline(self):
        units = self.parse()
        for field, value in [('lighting', '另一处新光源'), ('lighting_policy', None)]:
            changed = copy.deepcopy(units)
            changed[0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '派生字段'):
                core.validate(self.state, changed)

    def test_refs_in_baseline_are_checked_collected_and_encoded(self):
        text = self.text.replace('照亮窗边桌沿', '照亮@{PROP0001}边缘')
        unit = self.parse(text)[0]
        self.assertIn('PROP0001', unit['asset_ids'])
        output = core.render_unit(self.state, renderable(unit), reference_text=lambda s: '<' + s + '>')
        self.assertIn('<@钥匙 >边缘', output)
        for target in ('PROP9999', 'CHAR0003'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.parse(text.replace('PROP0001', target))

    def test_bare_character_in_baseline_does_not_escape_identity_checks(self):
        with self.assertRaises(core.TextValidationError) as caught:
            self.parse(self.text.replace('照亮窗边桌沿', '照亮阿青的面颊'))
        self.assertIn('BARE_CHARACTER_NAME', {e['code'] for e in caught.exception.errors})

    def test_cross_reference_prohibitions_cover_all_visible_fields(self):
        variants = [self.text.replace(BASELINE, '光照：同前。'),
                    self.text.replace('人物站位：', '人物站位：光照沿用上镜；'),
                    self.text.replace('时间：白天', '时间：光照同上'),
                    self.text.replace('天气：未明示', '天气：光线如前'),
                    self.text.replace('转场：硬切', '转场：硬切，光照同前', 1),
                    self.text.replace('音效：无。', '音效：光照照旧。', 1),
                    self.text.replace('镜头向右平移', '主光承接前一镜；镜头向右平移')]
        variants.extend(self.text.replace('镜头向右平移', word + '同前；镜头向右平移')
                        for word in ('灯光', '光影', '光源', '色温', '亮度'))
        for text in variants:
            with self.subTest(text=text), self.assertRaises(core.TextValidationError) as caught:
                self.parse(text)
            self.assertIn('LIGHTING_BACK_REFERENCE', {e['code'] for e in caught.exception.errors})

    def test_source_dialogue_and_nonlighting_phase_references_are_not_flagged(self):
        state, legacy = fixture(dialogue='光照同前。')
        text = with_baseline(legacy).replace('镜头向右平移', '动作承接前一镜，镜头向右平移')
        units = core.parse(text, state, _current_lighting=True)
        self.assertEqual(units[0]['shots'][0]['dialogue_parts'][0]['text'], '光照同前。')
        self.assertEqual(lighting.errors(units, current=True), [])

    def test_diffuse_silhouette_dark_and_stylized_are_not_forced_to_invent_fill(self):
        baselines = ['光照：既有均匀漫射照明使桌面与人物轮廓清楚。',
                     '光照：亮背景前人物呈暗色剪影，面部细节处于深暗。',
                     '光照：室内完全黑暗，画面保持黑色。',
                     '光照：既定平涂风格以平整色块呈现，人物与背景通过色块明度分离。']
        for line in baselines:
            with self.subTest(line=line):
                units = self.parse(self.text.replace(BASELINE, line))
                errors = gate.body_errors(units)
                self.assertNotIn('AUTHORING_LIGHTING_UNDER_SPECIFIED', {e['code'] for e in errors})
                self.assertEqual(lighting.errors(units, current=True), [])
        # These checks accept the syntax only, not the fixture's visibility or
        # fidelity. In particular darkness cannot prove that visible action works.

    def test_new_baseline_does_not_remove_other_photography_checks(self):
        errors = gate.body_errors(self.parse())
        self.assertIn('AUTHORING_DEPTH_MISSING', {e['code'] for e in errors})
        self.assertNotIn('AUTHORING_LIGHTING_UNDER_SPECIFIED', {e['code'] for e in errors})

    def test_voice_only_and_background_permissions_include_baseline(self):
        unit = self.parse()[0]
        unit['lighting'] += '@{CHAR0003}面部受光。'
        with self.assertRaisesRegex(ValueError, '声音专用发送者'):
            source_text_voices.require_visual_scope(unit, {'CHAR0003'})
        row = {'asset_id': 'CHAR0003', 'name': '静默人群'}
        with self.assertRaisesRegex(ValueError, '静默背景槽'):
            background.require_usage([unit], [row])

    def test_photo_only_character_cannot_become_real_body_through_baseline(self):
        unit = self.parse()[0]
        unit['source_references'] = [{'mode': source_references.PHOTO_BODY_PART,
                                     'asset_id': 'CHAR0003', 'carrier_asset_id': 'PROP0001'}]
        unit['lighting'] += '@{CHAR0003}面部受光。'
        errors = source_references.photo_reference_issues(unit)
        self.assertTrue(any(e['code'] == 'EMBEDDED_CHARACTER_CARRIER' and e.get('field') == 'lighting'
                            for e in errors))

    def test_saved_legacy_unit_remains_renderable_and_validatable(self):
        units = core.parse(self.legacy, self.state)
        expected = core.render_unit(self.state, renderable(units[0]))
        state = dict(self.state, units=copy.deepcopy(units), storyboard_lighting_policy=lighting.POLICY)
        core.validate(state, units)
        actual = core.render_unit(state, renderable(units[0]))
        self.assertEqual(actual, expected)
        self.assertNotIn('光照：', actual)


class UnitLightingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f = delivery_fixtures.TextDeliveryIntegration('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_public_candidate_commands_require_baseline_without_mutating_session(self):
        f = self.f
        before = (f.project / 'session.json').read_bytes()
        old = re.sub(r'^光照：.*\n', '', f.draft.read_text(encoding='utf-8'), flags=re.M)
        f.draft.write_text(old, encoding='utf-8')
        incremental = f.write_draft('missing-incremental.txt', old.split('## SC0002', 1)[0])
        decisions = f.project / 'empty-decisions.json'
        d.write_json(decisions, {'policy': gate.POLICY, 'version': 4, 'units': []})
        for command in [lambda: delivery.prepare(f.project, f.draft),
                        lambda: delivery.check_incremental(f.project, incremental),
                        lambda: delivery.bind_trace(f.project, f.draft, decisions)]:
            with self.subTest(command=command), self.assertRaises(core.TextValidationError) as caught:
                command()
            self.assertIn('AUTHORING_LIGHTING_UNDER_SPECIFIED', {e['code'] for e in caught.exception.errors})
        self.assertEqual((f.project / 'session.json').read_bytes(), before)

    def test_preview_commit_retry_and_canvas_share_the_authored_baseline(self):
        f = self.f
        raw = f.draft.read_bytes()
        state, units, info = delivery.prepare(f.project, f.draft)
        preview = delivery.render_units(state, units, preview=True, authoring_gate=info.get('authoring_gate'))
        self.assertEqual(preview.count('\n光照：'), 4)
        result = f.commit_fixture(request_id='lighting-first')
        self.assertEqual(result['status'], 'committed')
        original = f.output.read_bytes()
        saved = d.load(f.project)
        for unit in saved['units']:
            self.assertEqual(unit['lighting_policy'], lighting.POLICY)
            native = core.render_unit(saved, unit)
            canvas = canvas_transfer.render_unit(saved, unit)
            self.assertEqual(canvas, native)
            self.assertIn('光照：' + unit['lighting'], canvas)
        retried = delivery.commit(f.project, f.draft, 'lighting-first', f.review, f.output)
        self.assertEqual(retried['status'], 'already_committed')
        self.assertEqual(f.output.read_bytes(), original)
        self.assertEqual(f.draft.read_bytes(), raw)

    def test_delivery_rejects_lighting_back_reference_in_voice_direction(self):
        f = self.f
        original = f.draft.read_text(encoding='utf-8')
        for direction in ('光照同前', '灯光沿用上镜'):
            with self.subTest(direction=direction):
                f.draft.write_text(original.replace('说：“', f'说（{direction}）：“', 1), encoding='utf-8')
                with self.assertRaises(core.TextValidationError) as caught:
                    delivery.prepare(f.project, f.draft)
                self.assertTrue(any(e['code'] == 'LIGHTING_BACK_REFERENCE'
                                    and e.get('field') == 'voice_direction'
                                    for e in caught.exception.errors))
        f.draft.write_text(original.replace('说：“', '说（平静）：“', 1), encoding='utf-8')
        _, units, _ = delivery.prepare(f.project, f.draft)
        parts = [part for unit in units for shot in unit['shots'] for part in shot['dialogue_parts']]
        self.assertEqual(parts[0]['direction'], '（平静）')
        self.assertEqual(parts[0]['text'], '把消息发给@群里。')

    def test_replacing_a_saved_unit_cannot_drop_its_baseline(self):
        f = self.f
        result = f.commit_fixture(request_id='lighting-original')
        state = d.load(f.project)
        old = state['units'][0]
        replacement = f.write_draft('without-lighting.txt', re.sub(r'^光照：.*\n', '', old['source_text'], flags=re.M))
        with self.assertRaises(core.TextValidationError):
            delivery.prepare(f.project, replacement, replace_ids=[result['ids'][0]])

    def test_historical_receipt_reexport_and_retry_are_byte_identical(self):
        f = self.f
        old = re.sub(r'^光照：.*\n', '', f.draft.read_text(encoding='utf-8'), flags=re.M)
        f.draft.write_text(old, encoding='utf-8')
        # Construct an isolated pre-policy receipt by temporarily using the old
        # lighting checker. All retry/render assertions run the current code.
        with patch.object(lighting, 'errors', return_value=[]):
            f.commit_fixture(request_id='historical-lighting')
        original = f.output.read_bytes()
        state = d.load(f.project)
        state[lighting.SESSION_FIELD] = lighting.POLICY
        d.write_json(f.project / 'session.json', state)
        receipt = state['receipts'][-1]
        exported = delivery.render_units(state, receipt['units'], historical_receipt=receipt)
        self.assertEqual(exported.encode('utf-8'), original)
        retried = delivery.commit(f.project, f.draft, 'historical-lighting', f.review, f.output)
        self.assertEqual(retried['status'], 'already_committed')
        self.assertEqual(f.output.read_bytes(), original)
        self.assertNotIn(b'lighting_policy', d.packed(receipt).encode('utf-8'))


if __name__ == '__main__':
    unittest.main()
