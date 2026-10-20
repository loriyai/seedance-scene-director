"""Review-scope regressions use fictional snapshots, never production approvals."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scene_review as sr
from test_local4_upgrade import fine_fixture
from test_scene_review import reviewed, fixture as conservative_fixture


class IncrementalScopeAuditTests(unittest.TestCase):
    def setUp(self):
        self.rules = patch.object(sr, 'rules_digest', return_value='scope-audit-rules')
        self.rules.start()
        self.addCleanup(self.rules.stop)
        self.state, self.request = fine_fixture()

    def compare(self, before, after, new_state=None, warnings=None):
        snapshot = sr.freeze(self.state, before, warnings, partial=True, validation_passed=True)
        report = reviewed(self.state, before, warnings)
        result, proof = sr.prepare_incremental(
            snapshot, report, new_state or self.state, after, warnings, partial=True,
            validation_passed=True, expected_report_sha256=sr.digest(report))
        return result, {p['key']: p for p in proof['shot_provenance']}

    def prefix(self, count):
        request = copy.deepcopy(self.request)
        request['units'] = request['units'][:count]
        return request

    def test_appending_unit_reuses_prefix_except_its_new_outgoing_boundary(self):
        result, proof = self.compare(self.prefix(2), self.prefix(3))
        pending = {key for key, value in proof.items() if value['status'] == 'pending'}
        self.assertEqual(pending, {'2:3', '3:1', '3:2', '3:3'})
        self.assertEqual(sum(p['status'] == 'reused' for p in proof.values()), 5)
        self.assertEqual(result['status'], 'pending')
        self.assertIn('connection_changed:U3S1', proof['2:3']['reasons'])
        self.assertTrue(all(not proof[key]['reasons'] for key in ('1:1', '1:2', '1:3', '2:1', '2:2')))

    def test_removing_tail_rechecks_retained_outgoing_boundary_only(self):
        _, proof = self.compare(self.prefix(3), self.prefix(2))
        self.assertEqual({key for key, p in proof.items() if p['status'] == 'pending'}, {'2:3'})
        self.assertIn('connection_changed:U3S1', proof['2:3']['reasons'])

    def test_local_insert_delete_reorder_invalidates_its_unit_without_reassigning_reviews(self):
        for operation in ('insert', 'delete', 'reorder'):
            with self.subTest(operation=operation):
                after = copy.deepcopy(self.request)
                shots = after['units'][2]['shots']
                if operation == 'insert':
                    inserted = copy.deepcopy(shots[1])
                    inserted['review_id'] = 'new-interior-shot'
                    shots.insert(1, inserted)
                elif operation == 'delete':
                    shots.pop(1)
                else:
                    shots[0], shots[1] = shots[1], shots[0]
                result, proof = self.compare(self.request, after)
                for number in range(1, len(shots) + 1):
                    key = f'3:{number}'
                    self.assertEqual(proof[key]['status'], 'pending')
                    self.assertIn('shot_identity_order_changed_conservative', proof[key]['reasons'])
                    self.assertEqual(next(t for t in result['shot_reviews'] if t['key'] == key)['basis'], '')
                self.assertEqual(proof['2:3']['status'], 'pending')
                if operation != 'reorder':
                    self.assertEqual(proof['4:1']['status'], 'pending')
                for key in ('1:1', '2:1', '5:2', '6:1'):
                    self.assertEqual(proof[key]['status'], 'reused')

    def test_unit_insert_does_not_match_shifted_targets_by_review_id(self):
        after = copy.deepcopy(self.request)
        inserted = copy.deepcopy(after['units'][2])
        inserted['id'] = 'inserted-unit'
        for number, shot in enumerate(inserted['shots'], 1):
            shot['review_id'] = f'inserted-{number}'
        after['units'].insert(2, inserted)
        result, proof = self.compare(self.request, after)
        self.assertEqual(proof['1:1']['status'], 'reused')
        self.assertEqual(proof['2:3']['status'], 'pending')
        self.assertTrue(all(p['status'] == 'pending' for key, p in proof.items() if int(key.split(':')[0]) >= 3))
        self.assertTrue(all(t['basis'] == '' for t in result['shot_reviews'] if t['unit_index'] >= 3))

    def test_reordering_units_keeps_unmoved_unconnected_unit_evidence(self):
        after = copy.deepcopy(self.request)
        after['units'][2], after['units'][3] = after['units'][3], after['units'][2]
        _, proof = self.compare(self.request, after)
        self.assertTrue(all(proof[f'{unit}:{shot}']['status'] == 'pending'
                            for unit in (3, 4) for shot in (1, 2, 3)))
        self.assertEqual(proof['1:2']['status'], 'reused')
        self.assertEqual(proof['6:2']['status'], 'reused')

    def test_append_with_unknown_graph_keeps_boundary_conservative(self):
        after = self.prefix(3)
        del after['units'][2]['review_dependencies']
        _, proof = self.compare(self.prefix(2), after)
        self.assertEqual(proof['1:2']['status'], 'reused')
        self.assertEqual(proof['2:2']['status'], 'reused')
        self.assertEqual(proof['2:3']['status'], 'pending')
        self.assertIn('shot_dependency_declaration_missing', proof['2:3']['reasons'])
        self.assertTrue(all(proof[f'3:{n}']['status'] == 'pending' for n in (1, 2, 3)))

    def test_append_cannot_hide_duplicate_id_in_reviewed_prefix(self):
        after = self.prefix(3)
        after['units'][2]['shots'][0]['review_id'] = 'U1S1'
        _, proof = self.compare(self.prefix(2), after)
        self.assertTrue(all(proof[f'1:{n}']['status'] == 'pending' for n in (1, 2, 3)))
        self.assertIn('shot_dependency_declaration_missing', proof['1:1']['reasons'])

    def replacements(self, *indices):
        self.state['units'] = copy.deepcopy(self.request['units'])
        request = copy.deepcopy(self.request)
        request['mode'] = 'replace'
        request['units'] = [request['units'][i] for i in indices]
        return request

    def test_disjoint_replacements_include_actual_external_next_and_previous(self):
        request = self.replacements(1, 4)
        for changed, invalidated, retained in ((2, 1, 2), (3, 2, 1)):
            with self.subTest(changed=changed):
                current = copy.deepcopy(self.state)
                current['units'][changed]['shots'][0]['camera'] = '实际未选邻接单元改变观看轴线'
                _, proof = self.compare(request, request, new_state=current)
                self.assertTrue(all(proof[f'{invalidated}:{n}']['status'] == 'pending' for n in (1, 2, 3)))
                self.assertTrue(all(proof[f'{retained}:{n}']['status'] == 'reused' for n in (1, 2, 3)))

    def test_disjoint_replacements_do_not_create_an_artificial_boundary(self):
        request = self.replacements(1, 4)
        after = copy.deepcopy(request)
        after['units'][0]['shots'][-1]['camera'] = '原范围末镜略降低机位'
        _, proof = self.compare(request, after)
        self.assertEqual(proof['1:3']['status'], 'pending')
        self.assertTrue(all(proof[f'2:{n}']['status'] == 'reused' for n in (1, 2, 3)))

    def test_unordered_replacements_resolve_boundary_by_source(self):
        request = self.replacements(2, 1)
        after = copy.deepcopy(request)
        after['units'][1]['shots'][-1]['camera'] = '实际前序末镜改变机位'
        _, proof = self.compare(request, after)
        self.assertEqual(proof['1:1']['status'], 'pending')
        self.assertEqual(proof['2:3']['status'], 'pending')
        self.assertEqual(proof['1:2']['status'], 'reused')

    def test_stored_predecessor_selects_exact_rendition_not_all_same_source_versions(self):
        request = self.replacements(1)
        self.state['units'][1]['predecessor'] = 'u1'
        duplicate = copy.deepcopy(self.state['units'][0])
        duplicate['id'] = 'alternate-u1'
        self.state['units'].append(duplicate)
        current = copy.deepcopy(self.state)
        current['units'][-1]['shots'][0]['camera'] = '未被真实前驱绑定的另一版镜头'
        _, proof = self.compare(request, request, new_state=current)
        self.assertTrue(all(p['status'] == 'reused' for p in proof.values()))
        current['units'][0]['shots'][-1]['camera'] = '已绑定真实前驱的末镜变化'
        _, proof = self.compare(request, request, new_state=current)
        self.assertTrue(all(p['status'] == 'pending' for p in proof.values()))

    def test_ambiguous_source_neighbours_cannot_reuse_old_judgments(self):
        request = self.replacements(1)
        duplicate = copy.deepcopy(self.state['units'][0])
        duplicate['id'] = 'alternate-u1'
        self.state['units'].append(duplicate)
        _, proof = self.compare(request, request)
        self.assertTrue(all(p['status'] == 'pending' for p in proof.values()))
        self.assertTrue(all('shot_dependency_declaration_missing' in p['reasons'] for p in proof.values()))

    def test_replacement_display_name_resolves_stable_id_and_saved_predecessor(self):
        request = self.replacements(1)
        self.state['units'][1].update(display_name='A02-场景1-分镜2', predecessor='u1')
        request['units'][0]['id'] = 'A02-场景1-分镜2'
        actual = sr._connections(self.state, request)[0]
        self.assertEqual([u['id'] for u in actual['previous_external']], ['u1'])
        self.assertEqual([u['id'] for u in actual['next_external']], ['u3'])

    def test_writer_review_notes_do_not_invalidate_shots_or_exact_warnings(self):
        for fine in (False, True):
            with self.subTest(fine=fine):
                self.state, request = fine_fixture() if fine else conservative_fixture()
                for field in ('staging_review', 'audio_preflight'):
                    request['units'][2][field] = {'reviewer': 'fictional writer', 'basis': '实际内容与已核对状态不变。'}
                after = copy.deepcopy(request)
                for field in ('staging_review', 'audio_preflight'):
                    # Even asset-like prose in review notes is not an image dependency.
                    after['units'][2][field]['basis'] = '只补充审阅记录；不改变画面或声音，未使用@{PROP2}。'
                warnings = [{'kind': '原声音时间待核', 'unit': 'u3'}]
                report, proof = self.compare(request, after, warnings=warnings)
                self.assertTrue(all(p['status'] == 'reused' for p in proof.values()))
                self.assertEqual(report['warning_decisions'][0]['decision'], 'accepted')

    def test_external_review_notes_do_not_invalidate_selected_content(self):
        request = self.replacements(1)
        current = copy.deepcopy(self.state)
        current['units'][0]['staging_review'] = {'reviewer': 'fictional writer', 'basis': '补充说明但未改实际镜头内容。'}
        _, proof = self.compare(request, request, new_state=current)
        self.assertTrue(all(p['status'] == 'reused' for p in proof.values()))

    def test_conservative_prefix_append_keeps_unchanged_nonadjacent_unit(self):
        self.state, self.request = conservative_fixture()
        _, proof = self.compare(self.prefix(2), self.prefix(3))
        self.assertTrue(all(proof[f'1:{n}']['status'] == 'reused' for n in (1, 2)))
        self.assertTrue(all(proof[f'{u}:{n}']['status'] == 'pending' for u in (2, 3) for n in (1, 2)))

    def test_current_authoring_warning_reuse_is_not_reported_as_pending_or_old_review(self):
        from test_local4_upgrade import speech_fixture, approve_fictional_audio
        from storyboard_preflight import check_audio_plan
        state, unit = speech_fixture()
        approve_fictional_audio(state, unit)
        unit['shots'] = [{'start': 0, 'end': 4, 'camera': '固定机位',
                          'dialogue_parts': [dict(p, record='L1') for p in unit['audio_plan'][0]['parts']]}]
        before = {'units': [unit]}
        warnings = [dict(w, unit=unit['id']) for w in check_audio_plan(unit)['warnings']]
        old = sr.freeze(state, before, warnings, validation_passed=True)
        review = reviewed(state, before, warnings)
        after = copy.deepcopy(before)
        after['units'][0]['shots'][0]['camera'] = '降低固定机位，声音保持原计划'
        report, proof = sr.prepare_incremental(old, review, state, after, warnings,
                                               validation_passed=True, expected_report_sha256=sr.digest(review))
        self.assertEqual(report['status'], 'pending')
        self.assertEqual(report['shot_reviews'][0]['status'], 'pending')
        self.assertEqual(proof['stats']['authoring_reused_warnings'], len(warnings))
        self.assertEqual(proof['stats']['pending_warnings'], 0)
        self.assertEqual(proof['stats']['reused_warnings'], 0)
        self.assertTrue(all(p['decision_source'] == 'current_audio_preflight' for p in proof['warning_provenance']))
        self.assertTrue(all(p['previous_reviewer'] is None for p in proof['warning_provenance']))
        # Current sound changes without a new writer decision cannot inherit it.
        after['units'][0]['audio'][0]['delivery'] = '虚构声音变化使作者旧绑定失效'
        report, proof = sr.prepare_incremental(old, review, state, after, warnings,
                                               validation_passed=True, expected_report_sha256=sr.digest(review))
        self.assertEqual(proof['stats']['authoring_reused_warnings'], 0)
        self.assertEqual(proof['stats']['pending_warnings'], len(warnings))
        self.assertTrue(all(w['decision'] == 'pending' for w in report['warning_decisions']))


class RuleDomainAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        import creative_rules
        package = Path(sr.__file__).resolve().parent.parent
        for name in creative_rules.REQUIRED_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((package / name).read_bytes())
        self.location = patch.object(sr, '__file__', str(self.root / 'scripts' / 'scene_review.py'))
        self.location.start()
        self.addCleanup(self.location.stop)

    def test_audited_runtime_function_changes_do_not_invalidate_storyboard_rules(self):
        # Current domains ignore audited operation bodies, never unknown code
        # or arbitrary new files merely placed beside queue/recovery scripts.
        for name, function in (('scripts/canvas.py', 'sync'), ('scripts/canvas_jobs.py', 'status'),
                               ('scripts/recovery_status.py', 'summarize')):
            (self.root / name).write_text(f'def {function}():\n    return 1\n', encoding='utf-8')
        before = sr.rules_digest()
        for name in ('scripts/canvas.py', 'scripts/canvas_jobs.py', 'scripts/recovery_status.py'):
            path = self.root / name
            path.write_text(path.read_text(encoding='utf-8').replace('return 1', 'return 2'), encoding='utf-8')
        self.assertEqual(sr.rules_digest(), before)

    def test_unknown_runtime_code_or_reference_is_conservatively_bound(self):
        for name in ('scripts/agent_handoff.py', 'references/future-runtime.md'):
            before = sr.rules_digest()
            path = self.root / name
            path.write_text('UNKNOWN_RULE = 1\n', encoding='utf-8')
            self.assertNotEqual(sr.rules_digest(), before)

    def test_photography_sound_render_and_character_rules_are_bound(self):
        for name in ('references/storyboard-rules.md', 'references/storyboard-text-format.md',
                     'scripts/authoring_gates.py', 'scripts/dialogue_layout.py',
                     'assets/fixed-header.txt',
                     'modules/screenplay-standardizer/scripts/character_variants.py', 'SKILL.md'):
            with self.subTest(name=name):
                before = sr.rules_digest()
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b' changed semantic rule')
                self.assertNotEqual(sr.rules_digest(), before)
                path.write_bytes(original)

    def test_missing_required_rule_fails_closed(self):
        (self.root / 'references/storyboard-rules.md').unlink()
        with self.assertRaisesRegex(ValueError, '规则文件缺失.*storyboard-rules'):
            sr.rules_digest()


if __name__ == '__main__':
    unittest.main()
