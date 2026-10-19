"""Exact revisions preserve unrelated text, inputs and review boundaries."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

import authoring_gates as gates
import draft_revision as revision
import storyboard_assembly as assembly
import test_audit_director as fixtures


class DraftRevisionTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.AuditDirectorChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.document = assembly.assemble_batch(fixture.request())
        self.unit = self.document['units'][0]
        self.unit.update({'audio_preflight': {'fixture': 'unchanged-audio'},
                          'staging_review': {'fixture': 'must-invalidate'}})

    def plan(self, path='/camera', shot=True, value='平视斜侧缓慢推进'):
        p = revision.inspect_edit(self.document, self.unit['draft_unit_id'], path,
                                  self.unit['shots'][0]['review_id'] if shot else None)
        p['edits'][0]['value'] = value
        return p

    def test_precise_camera_edit_keeps_same_text_elsewhere_and_voice_review(self):
        original = copy.deepcopy(self.document)
        result, report = revision.apply_revision(self.document, self.plan())
        self.assertEqual(self.document, original)
        self.assertEqual(result['units'][1], original['units'][1])
        self.assertEqual(result['units'][0]['shots'][1:], original['units'][0]['shots'][1:])
        self.assertNotIn('staging_review', result['units'][0])
        self.assertEqual(result['units'][0]['audio_preflight'], self.unit['audio_preflight'])
        self.assertNotEqual(result['request_id'], original['request_id'])
        self.assertEqual(report['status'], 'revised_unreviewed')

    def test_changed_input_or_old_value_rejected_before_mutation(self):
        for wrong in ('hash', 'old'):
            with self.subTest(wrong=wrong):
                plan = self.plan()
                plan['input_sha256' if wrong == 'hash' else 'edits'] = ('wrong' if wrong == 'hash' else
                    [dict(plan['edits'][0], old='别的旧稿')])
                original = copy.deepcopy(self.document)
                with self.assertRaises(ValueError):
                    revision.apply_revision(self.document, plan)
                self.assertEqual(self.document, original)

    def test_exact_identity_after_reorder(self):
        self.document['units'].reverse()
        plan = self.plan()
        result, _ = revision.apply_revision(self.document, plan)
        self.assertEqual(result['units'][1]['shots'][0]['camera'], '平视斜侧缓慢推进')
        self.assertEqual(result['units'][0], self.document['units'][0])

    def test_missing_and_duplicate_identity_rejected(self):
        for invalid in ('missing', 'duplicate'):
            with self.subTest(invalid=invalid):
                doc = copy.deepcopy(self.document)
                if invalid == 'missing':
                    del doc['units'][0]['draft_unit_id']
                else:
                    doc['units'][1]['draft_unit_id'] = doc['units'][0]['draft_unit_id']
                with self.assertRaisesRegex(ValueError, '身份缺失或重复'):
                    revision.inspect_edit(doc, self.unit['draft_unit_id'], '/global')

    def test_cannot_inject_review_or_change_identity_or_derived_audio(self):
        for path, shot in (('/staging_review', False), ('/draft_unit_id', False),
                           ('/shots', False), ('/review_id', True), ('/dialogue_parts', True)):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    revision.apply_revision(self.document, self.plan(path, shot, '无效替换'))

    def test_late_failure_leaves_entire_input_unchanged(self):
        plan = self.plan()
        second = copy.deepcopy(plan['edits'][0])
        second.update({'path': '/composition', 'old': '不是实际旧值'})
        plan['edits'].append(second)
        original = copy.deepcopy(self.document)
        with self.assertRaisesRegex(ValueError, '旧值不匹配'):
            revision.apply_revision(self.document, plan)
        self.assertEqual(self.document, original)

    def test_overlapping_changes_and_noop_rejected(self):
        plan = self.plan()
        plan['edits'].append(copy.deepcopy(plan['edits'][0]))
        with self.assertRaisesRegex(ValueError, '重复或互相覆盖'):
            revision.apply_revision(self.document, plan)
        plan = self.plan()
        plan['edits'][0]['value'] = plan['edits'][0]['old']
        with self.assertRaisesRegex(ValueError, '没有实际变化'):
            revision.apply_revision(self.document, plan)

    def test_action_change_invalidates_voice_review(self):
        plan = self.plan('/performance_brief', False, '观众先看到人物停顿，再看到目光移向窗边。')
        result, summary = revision.apply_revision(self.document, plan)
        self.assertNotIn('audio_preflight', result['units'][0])
        self.assertIn('audio_preflight', summary['changes'][0]['removed_reviews'])

    def test_json_pointer_escapes_and_absent_add(self):
        plan = self.plan('/continuity/initial/CHAR0001/facts/位置~1朝向', False, '窗边朝外')
        result, _ = revision.apply_revision(self.document, plan)
        self.assertEqual(result['units'][0]['continuity']['final']['CHAR0001']['facts']['位置/朝向'], '窗边朝外')

    def test_cli_preserves_input_and_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, plan, output = (root / name for name in ('draft.json', 'plan.json', 'new.json'))
            source.write_text(json.dumps(self.document, ensure_ascii=False), encoding='utf-8')
            plan.write_text(json.dumps(self.plan(), ensure_ascii=False), encoding='utf-8')
            source_bytes = source.read_bytes()
            args = ['apply', '--input', str(source), '--plan', str(plan), '--output', str(output)]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(revision.main(args), 0)
                output_bytes = output.read_bytes()
                self.assertEqual(revision.main(args), 2)
                self.assertEqual(revision.main(args[:-1] + [str(source)]), 2)
            self.assertEqual(output.read_bytes(), output_bytes)
            self.assertEqual(source.read_bytes(), source_bytes)

    def action_document(self):
        import test_state_effects
        fixture = test_state_effects.ActionStateEffectsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        unit = fixture.compile()
        self.document = assembly.assemble_batch({'request_id': 'effects', 'mode': 'append',
                                                 'span': unit['span'], 'units': [unit]})
        self.unit = self.document['units'][0]
        self.unit.update({'audio_preflight': {'fixture': 'voice'}, 'staging_review': {'fixture': 'staging'}})

    def test_state_effect_revision_rebuilds_final_summary_and_removes_stale_reviews(self):
        self.action_document()
        plan = self.plan('/action_state_plan/effects/0/set/facts/姿态', False, '直立挺身')
        result, _ = revision.apply_revision(self.document, plan)
        unit = result['units'][0]
        self.assertEqual(unit['continuity']['final']['CHAR0001']['facts']['姿态'], '直立挺身')
        self.assertIn('姿态=直立挺身', unit['end_state'])
        self.assertNotIn('audio_preflight', unit)
        self.assertNotIn('staging_review', unit)
        self.assertEqual(self.unit['continuity']['final']['CHAR0001']['facts']['姿态'], '站立')

    def test_state_effect_revision_cannot_edit_derived_state(self):
        self.action_document()
        for path, shot in (('/end_state', False), ('/continuity/final', False),
                           ('/continuity/changes', True)):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, '权威动作或效果'):
                    revision.apply_revision(self.document, self.plan(path, shot, '伪造状态'))

    def test_retimed_action_moves_effect_to_correct_shot_once(self):
        self.action_document()
        plan = self.plan('/action_plan/0/milestones/upright', False, 2.3)
        result, _ = revision.apply_revision(self.document, plan)
        self.assertEqual([len(s['continuity']['changes']) for s in result['units'][0]['shots']], [0, 0, 1, 0])

    def test_initial_action_condition_revision_invalidates_old_action_readiness(self):
        self.action_document()
        plan = self.plan('/continuity/initial/CHAR0001/facts/位置', False, '新的起身位置')
        result, _ = revision.apply_revision(self.document, plan)
        self.assertNotIn('audio_preflight', result['units'][0])
        self.assertIn('位置=新的起身位置', result['units'][0]['start_state'])


if __name__ == '__main__':
    unittest.main()
