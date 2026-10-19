"""Stable draft identity and field-derived author cues, without auto approval."""
import copy
import json
import unittest

import authoring_gates as ag
import staging_preflight as staging
import test_staging_compact


class DraftRelatedTests(unittest.TestCase):
    def setUp(self):
        fixture = test_staging_compact.StagingCompactTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.state, self.request = fixture.root, fixture.state, fixture.request
        for n, unit in enumerate(self.request['units'], 1):
            unit.update(draft_policy=ag.DRAFT_POLICY, draft_unit_id=f'草稿-{n}')
            unit['action_plan'] = [{'record': self.state['records'][n - 1]['id'],
                                    'quote': '站起', 'start': 0, 'end': 1}]
            for i, shot in enumerate(unit['shots'], 1):
                shot['review_id'] = f'draft-{n}-shot-{i}'
        self.old = self.save('old.json')

    def save(self, name):
        path = self.root / name
        raw = json.dumps(self.request, ensure_ascii=False).encode('utf-8')
        view = staging.compact(self.state, self.request, raw, path)
        path.write_text(json.dumps(view, ensure_ascii=False), encoding='utf-8')
        return path

    def related(self, name='new.json'):
        return staging.related_view(self.save(name), previous=self.old)

    def test_append_without_formal_ids_aligns_and_roundtrips(self):
        self.assertTrue(all('id' not in u for u in self.request['units']))
        self.request['units'][0]['shots'][2]['camera'] = '由侧面缓慢横移至门边'
        path = self.save('new.json')
        view = staging.related_view(path, previous=self.old)
        self.assertEqual(view['policy'], 'staging-related-display-v2')
        self.assertTrue(all(u['comparison'] == 'aligned' for u in view['units']))
        self.assertEqual(view['units'][0]['identity'], {'field': 'draft_unit_id', 'value': '草稿-1'})
        self.assertEqual(staging.expand(path), staging.prepare(self.state, self.request))
        self.assertEqual(view['unmatched_previous_units'], [])

    def test_reorder_matches_identity_not_row(self):
        self.request['units'].reverse()
        view = self.related()
        row = view['units'][0]
        self.assertEqual(row['identity']['value'], '草稿-2')
        self.assertEqual(row['comparison'], 'aligned')
        self.assertTrue(all(s['difference'] == {'set': [], 'remove': []} for s in row['shots']))
        self.assertTrue(all(s['related_checks'] == [] for s in row['shots']))
        self.assertTrue(all(s['status'] == 'pending' for s in row['shots']))

    def test_strict_missing_multiline_or_duplicate_identity_is_rejected(self):
        original = copy.deepcopy(self.request)
        for invalid in (None, '', '  ', 4, [], 'a\nb', 'a\n', 'a\u2028b'):
            with self.subTest(invalid=invalid):
                self.request = copy.deepcopy(original)
                self.request['units'][0]['draft_unit_id'] = invalid
                with self.assertRaisesRegex(ValueError, 'draft_unit_id'):
                    staging.prepare(self.state, self.request)
        self.request = copy.deepcopy(original)
        self.request['units'][1]['draft_unit_id'] = self.request['units'][0]['draft_unit_id']
        with self.assertRaisesRegex(ValueError, '不可重复'):
            staging.prepare(self.state, self.request)

    def test_source_change_cannot_align_the_same_draft_identity(self):
        self.request['units'][0]['span'] = [0, 10]
        view = self.related()
        row = view['units'][0]
        self.assertEqual(row['comparison'], 'unmatched_or_ambiguous_unit')
        self.assertEqual(row['comparison_reason'], 'source_changed')
        self.assertNotIn('previous', row['shots'][0])
        self.assertEqual(row['shots'][0]['related_checks'][0]['reason'], 'alignment_unavailable')
        self.assertIn(1, view['unmatched_previous_units'])

    def test_source_text_change_cannot_align_even_when_span_is_identical(self):
        self.state['records'][0]['text'] += '！'
        # Keep offsets valid while changing the selected source itself.
        self.state['records'][0]['text'] = self.state['records'][0]['text'].replace('陈默', '人物')
        view = self.related()
        self.assertEqual(view['units'][0]['comparison_reason'], 'source_changed')

    def test_legacy_missing_and_duplicate_ids_are_readable_but_not_guessed(self):
        for unit in self.request['units']:
            unit.pop('draft_policy')
            unit.pop('draft_unit_id')
        old = self.save('legacy-missing.json')
        current = self.save('legacy-current.json')
        view = staging.related_view(current, previous=old)
        self.assertTrue(all(u['comparison_reason'] == 'missing_stable_unit_id' for u in view['units']))
        for unit in self.request['units']:
            unit['id'] = 'same-id'
        old = self.save('legacy-duplicates.json')
        view = staging.related_view(self.save('legacy-duplicates-current.json'), previous=old)
        self.assertTrue(all(u['comparison_reason'] == 'missing_or_duplicate_unit_identity' for u in view['units']))

    def test_draft_precedence_does_not_match_formal_id_of_same_spelling(self):
        unit = self.request['units'][0]
        unit.pop('draft_policy')
        unit['id'] = unit.pop('draft_unit_id')
        view = self.related()
        self.assertEqual(view['units'][0]['comparison'], 'unmatched_or_ambiguous_unit')

    def test_camera_only_change_with_no_changes_has_targeted_pending_cues(self):
        unit = self.request['units'][0]
        shot = unit['shots'][2]
        before = copy.deepcopy(shot)
        audio = ag.audio_payload(self.state, unit)
        old_binding = ag.staging_pending(self.state, unit)['input_sha256']
        self.assertEqual(shot['continuity']['changes'], [])
        shot['camera'] = '从副驾驶侧窗向后排拍摄'
        view = self.related()
        item = view['units'][0]['shots'][2]
        check = next(c for c in item['related_checks'] if c['reason'] == 'photography_changed')
        self.assertIn(['shot', 'camera'], check['changed_paths'])
        self.assertIn('shot.continuity.visibility', check['fields_to_check'])
        self.assertIn('shot.depth_of_field', check['fields_to_check'])
        self.assertEqual(check['status'], 'pending')
        self.assertEqual(shot['continuity']['visibility'], before['continuity']['visibility'])
        self.assertEqual(ag.audio_payload(self.state, unit), audio)
        self.assertNotEqual(ag.staging_pending(self.state, unit)['input_sha256'], old_binding)
        self.assertEqual(view['dependency_scope'], 'not_computed')

    def test_action_plan_changes_prompt_even_after_the_action_window(self):
        unit = self.request['units'][0]
        unit['action_plan'][0]['end'] = .8
        view = self.related()
        early, later = view['units'][0]['shots'][0], view['units'][0]['shots'][3]
        self.assertEqual(later['current']['action_windows'], [])
        for item in (early, later):
            check = next(c for c in item['related_checks'] if c['reason'] == 'action_plan_changed')
            self.assertIn('shot.continuity.changes', check['fields_to_check'])
            self.assertEqual(check['status'], 'pending')

    def test_entering_state_changes_prompt_even_without_current_shot_event(self):
        unit = self.request['units'][0]
        unit['shots'][0]['continuity']['changes'][0]['set']['facts']['姿态'] = '站稳'
        unit['continuity']['final']['CHAR0001']['facts']['姿态'] = '站稳'
        item = self.related()['units'][0]['shots'][2]
        self.assertEqual(item['current']['shot']['continuity']['changes'], [])
        check = next(c for c in item['related_checks'] if c['reason'] == 'state_or_spatial_context_changed')
        self.assertIn(['entering_state', 'CHAR0001', 'facts', '姿态'], check['changed_paths'])

    def test_first_read_and_unchanged_comparisons_never_approve(self):
        view = staging.related_view(self.old)
        self.assertEqual(view['units'][0]['shots'][0]['related_checks'][0]['reason'], 'first_read')
        self.assertEqual(view['status'], 'pending')
        unchanged = self.related()
        self.assertEqual(unchanged['units'][0]['shots'][0]['related_checks'], [])
        self.assertEqual(unchanged['units'][0]['shots'][0]['status'], 'pending')

    def test_strict_checks_version_extra_fields_without_extra_per_shot_forms(self):
        unit = self.request['units'][0]
        pending = ag.staging_pending(self.state, unit)
        self.assertEqual(pending['correlation_policy'], ag.CORRELATION_POLICY)
        self.assertEqual(len(pending['change_checks']), 2)
        self.assertIn('camera', pending['change_checks'][0]['fields'])
        self.assertIn('lighting', pending['change_checks'][0]['fields'])
        review = copy.deepcopy(pending)
        review.update(status='reviewed', reviewer='fictional author',
                      render_basis='测试中人物站起后朝向窗边，四个取景内的可见姿态一致。',
                      timing_state_basis='测试中两次状态事件依次重放，后两镜维持进入状态。')
        for check in review['change_checks']:
            check.update(status='reviewed', basis='测试事件后的姿态与摄影、可见范围及正文一致。')
        unit['staging_review'] = review
        ag.validate_staging(self.state, unit)
        del review['correlation_policy']
        with self.assertRaisesRegex(ValueError, '关联核对策略'):
            ag.validate_staging(self.state, unit)
        unit.pop('draft_policy')
        unit.pop('draft_unit_id')
        legacy = ag.staging_pending(self.state, unit)
        self.assertNotIn('correlation_policy', legacy)
        self.assertEqual(legacy['change_checks'][0]['fields'], list(ag.FIELDS))


if __name__ == '__main__':
    unittest.main()
