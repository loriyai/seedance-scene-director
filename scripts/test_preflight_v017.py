"""Early diagnostics and packaging, with no semantic approvals or live writes."""
import contextlib
import copy
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_handoff as handoff
import authoring_gates as ag
import storyboard_preflight as preflight
import staging_preflight as staging
import test_agent_handoff
import test_audit_director
import test_staging_compact


class ResultPackageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_agent_handoff.HandoffTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_seed_preserves_scope_without_writing_a_completed_result(self):
        _, path, manifest = self.fixture.prepare()
        seed = manifest['result_template']
        self.assertEqual(seed['scope'], self.fixture.brief['scope'])
        self.assertEqual(seed['status'], 'needs_input')
        self.assertFalse((Path(manifest['output_dir']) / 'result.json').exists())
        self.fixture.write_json(Path(manifest['output_dir']) / 'result.json', seed)
        checked = handoff.check_result(str(path))
        self.assertEqual(checked['result_status'], 'needs_input')
        self.assertEqual(checked['semantic_review'], 'not_performed')
        self.assertEqual(checked['input_verification'], 'not_performed')

    def test_empty_auxiliary_stream_not_required_but_declared_files_remain_strict(self):
        _, path, manifest = self.fixture.prepare()
        self.fixture.artifact(manifest)
        output = Path(manifest['output_dir'])
        (output / 'check.stderr.txt').write_bytes(b'')
        self.assertEqual(handoff.check_result(str(path))['status'], 'package_valid')
        result = handoff.read_json(output / 'result.json')
        result['artifacts'].append('check.stderr.txt')
        self.fixture.write_json(output / 'result.json', result)
        with self.assertRaisesRegex(ValueError, '为空'):
            handoff.check_result(str(path))
        result['artifacts'].remove('check.stderr.txt')
        result['scope'] = {}
        self.fixture.write_json(output / 'result.json', result)
        with self.assertRaisesRegex(ValueError, 'scope'):
            handoff.check_result(str(path))

    def test_packaging_does_not_relax_input_verification_or_legacy_manifests(self):
        _, path, manifest = self.fixture.prepare()
        del manifest['result_template']
        self.fixture.write_json(path, manifest)
        self.fixture.artifact(manifest)
        self.assertEqual(handoff.verify(str(path))['status'], 'artifacts_present')
        self.fixture.source.write_text('modified', encoding='utf-8')
        self.assertEqual(handoff.check_result(str(path))['status'], 'package_valid')
        with self.assertRaisesRegex(ValueError, '输入已改变'):
            handoff.verify(str(path))


class ReferenceClosureTests(unittest.TestCase):
    def setUp(self):
        f = test_audit_director.AuditDirectorChecks()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.state, self.unit = f.state, f.unit([0, 9])
        self.state['ledger']['characters'].append({'id': 'CHAR0002', 'name': '村民群像', 'visual_mode': 'prompt_only'})
        self.state['ledger']['scenes'][0]['character_ids'].append('CHAR0002')
        self.unit['character_ids'].append('CHAR0002')

    def test_aggregates_missing_nonfocus_references_without_adding_cast(self):
        for shot in self.unit['shots'][:2]:
            shot['subjects'].append('CHAR0002')
        before = copy.deepcopy(self.unit)
        report = preflight.reference_closure(self.state, self.unit)
        self.assertEqual([e['shot'] for e in report['errors']], [1, 2])
        self.assertTrue(all(e['asset_ids'] == ['CHAR0002'] for e in report['errors']))
        self.assertEqual(self.unit, before)
        for shot in self.unit['shots'][:2]:
            shot['composition'] += '，@{CHAR0002} 位于远侧。'
        self.assertEqual(preflight.reference_closure(self.state, self.unit)['errors'], [])

    def test_incomplete_photography_is_explicit_and_focus_needs_no_duplicate(self):
        self.unit['shots'][0]['text'] = '保持原姿态。'
        self.assertFalse(preflight.reference_closure(self.state, self.unit)['errors'])
        del self.unit['shots'][1]['camera']
        report = preflight.reference_closure(self.state, self.unit)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['deferred_shots'], [{'shot': 2, 'missing_fields': ['camera']}])


class RelatedDisplayTests(unittest.TestCase):
    def setUp(self):
        self.f = test_staging_compact.StagingCompactTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for n, unit in enumerate(self.f.request['units'], 1):
            unit['id'] = f'U{n}'
            unit['action_plan'] = [{'record': self.f.state['records'][n - 1]['id'],
                                    'quote': '站起', 'start': 0, 'end': 1}]
            for i, shot in enumerate(unit['shots'], 1):
                shot['review_id'] = f'U{n}-S{i}'
        self.old = self.save('old.json')

    def save(self, name):
        path = self.f.root / name
        raw = json.dumps(self.f.request, ensure_ascii=False).encode('utf-8')
        value = staging.compact(self.f.state, self.f.request, raw, path)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def test_camera_only_change_with_no_state_event_is_visible_and_pending(self):
        shot = self.f.request['units'][0]['shots'][2]
        self.assertFalse(shot['continuity']['changes'])
        shot['camera'] = '相机沿侧面横移后短暂稳定'
        current = self.save('current.json')
        view = staging.related_view(current, [1], 3, self.old)
        item = view['units'][0]['shots'][0]
        self.assertEqual(item['comparison'], 'aligned')
        self.assertIn(['shot', 'camera'], [x['path'] for x in item['difference']['set']])
        self.assertEqual(item['current']['shot']['continuity']['visibility'], shot['continuity']['visibility'])
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(view['dependency_scope'], 'not_computed')
        self.assertEqual(staging.expand(current), staging.prepare(self.f.state, self.f.request))

    def test_plan_window_change_shows_current_visibility_without_parsing_prose(self):
        unit = self.f.request['units'][0]
        unit['action_plan'][0]['end'] = .8
        unit['shots'][0]['continuity']['visibility']['CHAR0001']['basis'] = '原可见范围描述，记有1秒旧时点'
        current = self.save('current.json')
        item = staging.related_view(current, [1], 1, self.old)['units'][0]['shots'][0]
        self.assertEqual(item['previous']['action_windows'][0]['value']['end'], 1)
        self.assertEqual(item['current']['action_windows'][0]['value']['end'], .8)
        self.assertIn('1秒旧时点', item['current']['shot']['continuity']['visibility']['CHAR0001']['basis'])
        self.assertEqual(item['status'], 'pending')

    def test_missing_duplicate_ids_and_source_changes_never_guess_alignment(self):
        self.f.request['units'][0]['shots'][1]['review_id'] = 'U1-S1'
        del self.f.request['units'][1]['shots'][0]['review_id']
        current = self.save('duplicates.json')
        view = staging.related_view(current, previous=self.old)
        self.assertEqual(view['units'][0]['shots'][0]['comparison'], 'unmatched_or_ambiguous_shot')
        self.assertEqual(view['units'][1]['shots'][0]['comparison'], 'unmatched_or_ambiguous_shot')
        self.f.request['units'][0]['span'] = [0, 10]
        current = self.save('source-changed.json')
        self.assertEqual(staging.related_view(current, [1], previous=self.old)['units'][0]['comparison'],
                         'unmatched_or_ambiguous_unit')

    def test_tampered_previous_snapshot_is_rejected_and_cannot_approve(self):
        current = self.save('current.json')
        old = json.loads(self.old.read_text(encoding='utf-8'))
        old['status'] = 'passed'
        old['projection_sha256'] = staging._view_digest(old)
        self.old.write_text(json.dumps(old), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '不能作为通过证据'):
            staging.related_view(current, previous=self.old)

    def test_cli_previous_implies_related_and_writes_only_new_display(self):
        current = self.save('current.json')
        before = current.read_bytes()
        output = self.f.root / 'related.json'
        with patch('sys.argv', ['staging_preflight.py', '--expand', str(current), '--previous', str(self.old),
                              '--unit', '1', '--shot', '1', '--output', str(output)]), contextlib.redirect_stdout(io.StringIO()):
            staging.main()
        self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['status'], 'pending')
        self.assertEqual(current.read_bytes(), before)


class EvidenceStructureTests(unittest.TestCase):
    def test_short_specific_evidence_not_rejected_by_character_count(self):
        for text in ('接触已断', '起音在松手后', '仅前臂可见'):
            self.assertTrue(ag.concrete(text), text)

    def test_empty_punctuation_and_template_concatenations_rejected(self):
        for text in (None, '', '  ', '。。。；', '通过', '已检查，符合要求；同上。', '已检查符合剧情为了节奏无问题通过'):
            self.assertFalse(ag.concrete(text), text)


if __name__ == '__main__':
    unittest.main()
