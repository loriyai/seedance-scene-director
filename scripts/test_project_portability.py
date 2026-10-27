"""Observable transfer invariants; synthetic reviews never approve real work."""
import copy
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import test_storyboard_text_delivery as fixture_module
import batch_naming
import director as d
import project_bundle as bundle
import project_continuation as continuation
import project_paths as paths
import storyboard_context as context
import storyboard_text_delivery as delivery
import canvas_jobs


class ProjectPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.TextDeliveryIntegration(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.archive, self.target = self.base / 'project.zip', self.base / '不同用户/新目录/project'
        self.context_file = self.project / 'scene-context.json'
        d.write_json(self.context_file, context.scene_context(self.project, 'SC0001'))
        self.addCleanup(self.cleanup_owned_temporary_trees)

    def cleanup_owned_temporary_trees(self):
        # These two exact roots were allocated by TemporaryDirectory in setUp.
        roots = [paths.canonical(self.fixture.temp.name), paths.canonical(self.temp.name)]
        for root in roots:
            self.assertTrue(root.name.startswith('tmp'))
            if paths.io_path(root).exists():
                shutil.rmtree(paths.io_path(root, force=True))

    def transfer(self):
        bundle.export_project(self.project, self.archive)
        return bundle.import_project(self.archive, self.target)

    def test_pending_review_commits_after_origin_removed(self):
        self.fixture.write_synthetic_review_fixture()
        original = self.fixture.review.read_bytes()
        old_context = context.context_hash(d.load(self.project), self.project)
        continuation.checkpoint(self.project, 'passed_pending_delivery', 'SC0001', self.fixture.draft,
                                self.fixture.review, self.context_file)
        self.transfer()
        shutil.rmtree(self.project)
        self.assertEqual(context.context_hash(d.load(self.target), self.target), old_context)
        report = paths.resolve(self.target, str(self.fixture.review))
        self.assertEqual(report.read_bytes(), original)
        result = delivery.commit(self.target, paths.resolve(self.target, str(self.fixture.draft)),
                                 'after-transfer', report, self.target / 'outputs/new.txt')
        self.assertEqual(result['status'], 'committed')
        self.assertIn('project://', (self.target / 'session.json').read_text(encoding='utf-8'))
        self.assertEqual(len(d.load(self.target)['receipts']), 1)
        self.assertEqual(continuation.resume(self.target)['checkpoint']['stage'], 'delivered')

    def test_legacy_review_bridge_preserves_report_and_rejects_changed_report(self):
        self.fixture.write_synthetic_review_fixture()
        report = continuation.read(self.fixture.review)
        report['context_sha256'] = bundle.legacy_context(d.load(self.project), self.project, d.ROOT)
        d.write_json(self.fixture.review, report)
        ctx = continuation.read(self.context_file)
        ctx['versions']['context_sha256'] = report['context_sha256']
        d.write_json(self.context_file, ctx)
        continuation.checkpoint(self.project, 'passed_pending_delivery', 'SC0001', self.fixture.draft,
                                self.fixture.review, self.context_file)
        self.assertFalse(continuation.resume(self.project)['checkpoint']['ready_for_commit'])
        self.transfer()
        self.assertTrue(continuation.resume(self.target)['checkpoint']['ready_for_commit'])
        report_path = self.target / self.fixture.review.name
        self.assertEqual(report_path.read_bytes(), self.fixture.review.read_bytes())
        _, _, info = delivery.prepare(self.target, self.target / 'draft.txt')
        mutated = copy.deepcopy(report); mutated['scenes'][0]['basis'] = 'changed'
        with self.assertRaises(ValueError): delivery.verify_review(mutated, info)
        delivery.commit(self.target, self.target / 'draft.txt', 'legacy-transfer', report_path,
                        self.target / 'outputs/legacy.txt')

    def test_associated_formal_project_remains_separate_and_covers_its_range(self):
        self.fixture.commit_fixture()
        primary = self.base / 'primary'; shutil.copytree(self.project, primary)
        state = continuation.read(primary / 'session.json')
        state.update(units=[], receipts=[], ranges=[], next_numbers={}, batch_scope=None)
        d.write_json(primary / 'session.json', state)
        bundle.export_project(primary, self.archive, related_projects=[self.project])
        bundle.import_project(self.archive, self.target)
        shutil.rmtree(self.project); shutil.rmtree(primary)
        result = continuation.resume(self.target)
        self.assertEqual(result['formal_unit_count'], 0)
        self.assertEqual(result['related_formal_projects'][0]['unit_count'], 4)
        self.assertTrue(d.range_review(d.load(self.target), [0, state['length']], self.target)['issues'])

    def test_formal_receipt_render_retry_and_next_number(self):
        self.fixture.commit_fixture()
        original = self.fixture.output.read_bytes()
        self.transfer()
        shutil.rmtree(self.project)
        state = d.load(self.target)
        receipt = state['receipts'][0]
        self.assertEqual(Path(receipt['output']).read_bytes(), original)
        result = delivery.commit(self.target, self.target / 'draft.txt', 'fictional-first',
                                 self.target / self.fixture.review.name, Path(receipt['output']))
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual(state['next_numbers'], d.load(self.target)['next_numbers'])
        self.assertEqual(batch_naming._key(self.target, 'same'), batch_naming._key(self.target, 'same'))
        scope = self.target / '.portable/batch'
        registry = batch_naming._registry(scope)
        self.assertTrue(registry['entries'])
        with batch_naming.allocate(scope, self.target, 'new-request', 'new-hash') as label:
            self.assertEqual(label, 'B')

    def test_draft_or_bound_source_change_invalidates_review(self):
        self.fixture.write_synthetic_review_fixture()
        self.transfer()
        draft = self.target / 'draft.txt'
        draft.write_text(draft.read_text(encoding='utf-8').replace('原作', '新稿'), encoding='utf-8')
        with self.assertRaises(ValueError):
            delivery.commit(self.target, draft, 'changed', self.target / self.fixture.review.name,
                            self.target / 'outputs/new.txt')
        (self.target / 'script.txt').write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            d.load(self.target)

    def test_import_is_atomic_and_idempotent(self):
        self.transfer()
        self.assertEqual(bundle.import_project(self.archive, self.target)['status'], 'already_imported')
        (self.target / 'draft.txt').write_text('local change', encoding='utf-8')
        with self.assertRaises(ValueError):
            bundle.import_project(self.archive, self.target)
        other = self.base / 'existing'; other.mkdir()
        with self.assertRaises(ValueError): bundle.import_project(self.archive, other)

    def test_failed_validation_leaves_no_target(self):
        bundle.export_project(self.project, self.archive)
        with patch.object(d, 'load', side_effect=ValueError('synthetic failure')):
            with self.assertRaises(ValueError): bundle.import_project(self.archive, self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.target.parent.glob('.project-import-*')))

    def test_checkpoints_keep_partial_and_revision_states_without_approval(self):
        for stage in ('writing', 'awaiting_review', 'needs_revision'):
            with self.subTest(stage=stage):
                self.fixture.write_synthetic_review_fixture()
                continuation.checkpoint(self.project, stage, 'SC0001', self.fixture.draft,
                                        self.fixture.review if stage == 'needs_revision' else None,
                                        self.context_file, note='synthetic checkpoint')
                self.assertEqual(continuation.resume(self.project)['checkpoint']['stage'], stage)
        self.transfer()
        self.assertEqual(continuation.resume(self.target)['checkpoint']['stage'], 'needs_revision')

    def test_credentials_processes_and_cloud_auto_resume_are_not_restored(self):
        (self.project / 'canvas/jobs').mkdir(parents=True)
        (self.project / 'canvas/connection.json').write_text('{"token":"fictional-not-real"}', encoding='utf-8')
        d.write_json(self.project / 'canvas/jobs/worker.json', {'status': 'stopped', 'pid': 99999999})
        self.transfer()
        self.assertFalse((self.target / 'canvas/connection.json').exists())
        self.assertNotIn('pid', continuation.read(self.target / 'canvas/jobs/worker.json'))
        with self.assertRaises(ValueError): canvas_jobs.start_worker(self.target)
        self.assertTrue(continuation.read(self.target / 'canvas/jobs/control.json')['paused'])

    def test_export_rejects_live_worker_and_handoff_stops_source_writes(self):
        with patch.object(canvas_jobs, 'worker_active', return_value=True):
            with self.assertRaises(ValueError): bundle.export_project(self.project, self.archive)
        bundle.export_project(self.project, self.archive, handoff=True)
        with self.assertRaises(ValueError): delivery.preview(self.project, self.fixture.draft)
        self.assertEqual(d.load(self.project)['revision'], 0)
        bundle.import_project(self.archive, self.target)
        self.assertFalse((self.target / 'project-transfer.json').exists())

    def test_reexport_relocates_again_without_origin_or_first_target(self):
        self.transfer()
        second_archive = self.base / 'second.zip'
        bundle.export_project(self.target, second_archive)
        shutil.rmtree(self.project); shutil.rmtree(self.target)
        second = self.base / 'second-device/project'
        bundle.import_project(second_archive, second)
        self.assertEqual(d.load(second)['revision'], 0)
        self.assertEqual(continuation.resume(second)['status'], 'ready')

    def test_public_cli_export_inspect_import_resume_and_relocate(self):
        self.fixture.cli('director.py', 'project', 'export', '--project', self.project, '--output', self.archive)
        inspected = self.fixture.cli('director.py', 'project', 'inspect', '--bundle', self.archive)
        self.assertEqual(inspected['policy'], paths.POLICY)
        result = self.fixture.cli('director.py', 'project', 'import', '--bundle', self.archive, '--target', self.target)
        self.assertEqual(result['status'], 'imported')
        self.assertEqual(self.fixture.cli('director.py', 'project', 'resume', '--project', self.target)['status'], 'ready')
        relocated = self.base / 'relocated-by-cli'
        result = self.fixture.cli('director.py', 'project', 'relocate', '--project', self.target, '--target', relocated)
        self.assertEqual(result['status'], 'imported')
        self.assertTrue((self.target / 'session.json').exists())

    def test_windows_long_dependency_paths_survive_import(self):
        deep = self.project / 'evidence' / ('a' * 90) / ('b' * 90) / 'original-proof.txt'
        paths.io_path(deep.parent).mkdir(parents=True)
        paths.io_path(deep).write_text('synthetic evidence bytes', encoding='utf-8')
        self.transfer()
        restored = paths.resolve(self.target, str(paths.canonical(deep)))
        self.assertEqual(restored.read_text(encoding='utf-8'), 'synthetic evidence bytes')

    def test_import_into_long_root_is_readable(self):
        self.target = self.base / ('a' * 85) / ('b' * 85) / 'project'
        self.transfer()
        self.assertEqual(d.load(paths.io_path(self.target))['revision'], 0)
        self.assertEqual(continuation.resume(paths.io_path(self.target))['status'], 'ready')

    def test_ordinary_pending_video_job_keeps_digest_without_resuming_cloud(self):
        import canvas
        self.fixture.commit_fixture()
        state = canvas.blank_state()
        state['binding'] = {'canvas_id': 'fictional-canvas', 'account_id': 'fictional-account', 'team_id': '0'}
        canvas.save(self.project, state)
        created = canvas_jobs.enqueue(self.project, 'videos', request_id='fictional-first')
        original_job = canvas_jobs.manifests(self.project)[0]
        self.transfer()
        shutil.rmtree(self.project)
        job = canvas_jobs.manifests(self.target)[0]
        self.assertEqual(job, original_job)
        canvas_jobs.validate_job(self.target, job)
        self.assertTrue(continuation.read(self.target / 'canvas/jobs/control.json')['paused'])

    def test_archive_rejects_traversal_case_duplicates_links_and_corruption(self):
        bundle.export_project(self.project, self.archive)
        raw = self.archive.read_bytes()
        for name, attributes in (('../outside', None), ('C:/escape', None),
                                 ('PROJECT/session.json', None), ('project/link', stat.S_IFLNK | 0o777)):
            with self.subTest(name=name):
                broken = self.base / 'broken.zip'; broken.write_bytes(raw)
                with zipfile.ZipFile(broken, 'a') as z:
                    info = zipfile.ZipInfo(name)
                    if attributes: info.external_attr = attributes << 16
                    z.writestr(info, 'untrusted')
                with self.assertRaises(ValueError): bundle.import_project(broken, self.target)
                self.assertFalse(self.target.exists())
        broken = self.base / 'missing.zip'
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(broken, 'w') as out:
            for name in source.namelist():
                if name != 'project/draft.txt': out.writestr(name, source.read(name))
        with self.assertRaises(ValueError): bundle.inspect_bundle(broken)

    def test_content_digest_and_expansion_limits_are_enforced(self):
        bundle.export_project(self.project, self.archive)
        broken = self.base / 'tampered-content.zip'
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(broken, 'w') as out:
            for name in source.namelist():
                data = source.read(name)
                out.writestr(name, b'x' * len(data) if name == 'project/draft.txt' else data)
        with self.assertRaises(ValueError): bundle.import_project(broken, self.target)
        with patch.object(bundle, 'MAX_BYTES', 1):
            with self.assertRaises(ValueError): bundle.inspect_bundle(self.archive)
        with patch.object(bundle, 'MAX_FILES', 1):
            with self.assertRaises(ValueError): bundle.inspect_bundle(self.archive)

    def test_local_delivery_is_preserved_separately_and_blocks_duplicate_range(self):
        self.fixture.draft.write_text(self.fixture.authored_text(
            records=[r for r in self.fixture.state['records'] if r['scene'] == 'SC0001']), encoding='utf-8')
        self.fixture.write_synthetic_review_fixture()
        info = delivery.preview(self.project, self.fixture.draft)
        ctx = context.scene_context(self.project, 'SC0001')
        folder = self.project / 'storyboard-AF/A'; folder.mkdir(parents=True)
        d.write_json(folder / 'context.json', ctx)
        report = continuation.read(self.fixture.review)
        # Synthetic aggregate follows the real local-manual delivery schema.
        report.update(actual_output_sha256=info['output_sha256'], delivery_mode='local_manual_delivery')
        d.write_json(folder / 'final-review.json', report)
        scene_report = self.project / 'local-scene-review.json'; d.write_json(scene_report, report)
        row = {'scene_id': 'SC0001', 'span': info['span'], 'draft': str(self.fixture.draft),
               'draft_sha256': info['draft_sha256'], 'review': str(scene_report), 'review_sha256': d.file_hash(scene_report),
               'preview': str(self.project / 'local-result.json'), 'context': str(folder / 'context.json'),
               'context_file_sha256': d.file_hash(folder / 'context.json'),
               'actual_output': info['output'], 'actual_output_sha256': info['output_sha256']}
        d.write_json(self.project / 'local-result.json', info)
        row['preview_sha256'] = d.file_hash(self.project / 'local-result.json')
        local = dict(info, segment='A', delivery_mode='local_manual_delivery', total_seconds=info['total_duration'],
                     draft=str(self.fixture.draft), output_sha256=info['output_sha256'], scene_dependencies=[row])
        d.write_json(folder / 'bundle-result.json', local)
        self.transfer()
        shutil.rmtree(self.project)
        result = continuation.resume(self.target)
        self.assertEqual(result['formal_unit_count'], 0)
        self.assertEqual(len(result['local_deliveries']), 1)
        self.assertTrue(d.range_review(d.load(self.target), info['span'], self.target)['issues'])
