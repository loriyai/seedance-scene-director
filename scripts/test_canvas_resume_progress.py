"""Network recovery keeps the same job and original verified identities."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import canvas as c
import canvas_jobs as jobs
import director as d
import test_canvas_jobs as fixtures


class ResumeProgressTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.CanvasJobChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        queued = fixture.enqueue()
        self.path = jobs.state_file(fixture.project, queued['job_id'])

    def test_readback_pending_automatically_recovers_original_ids(self):
        state = jobs.read(self.path)
        state.update(completed=1, node_ids={'CHAR0001': 'ORIGINAL_NODE'})
        d.write_json(self.path, state)
        attempts = []
        def runner(project, manifest, check_control, progress):
            attempts.append(manifest['job_id'])
            progress({'completed': 0, 'node_ids': {}, 'stage': 'preparing'})
            if len(attempts) == 1:
                c.fail('READBACK_PENDING', 'Synthetic read timeout after acknowledged write')
            progress({'completed': 1, 'node_ids': {'CHAR0001': 'ORIGINAL_NODE'}, 'stage': 'node_verified'})
            return {'status': 'synced'}
        with patch.object(jobs.time, 'sleep'):
            jobs.run_worker(self.fixture.project, runner=runner)
        state = jobs.read(self.path)
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['retry_count'], 1)
        self.assertEqual(len(set(attempts)), 1)
        self.assertEqual(state['run_completed'], 1)
        self.assertEqual(state['cumulative_completed'], 1)
        self.assertEqual(state['cumulative_node_ids'], {'CHAR0001': 'ORIGINAL_NODE'})
        self.assertEqual(state['last_error']['code'], 'READBACK_PENDING')
        self.assertFalse(any(e.get('event') == 'needs_attention' for e in jobs.status(self.fixture.project)['events']))

    def test_pause_during_backoff_stops_without_new_attempt(self):
        attempts = []
        def runner(project, manifest, check_control, progress):
            attempts.append(1)
            c.fail('CLI_TRANSIENT', 'EOF')
        with patch.object(jobs.time, 'sleep', side_effect=lambda _: jobs.pause(self.fixture.project)):
            jobs.run_worker(self.fixture.project, runner=runner)
        self.assertEqual(jobs.read(self.path)['status'], 'paused')
        self.assertEqual(len(attempts), 1)

    def test_image_reference_network_error_keeps_job_until_complete(self):
        attempts = []
        def runner(project, manifest, check_control, progress):
            attempts.append(1)
            if len(attempts) == 1:
                return {'status': 'partial', 'references': {'status': 'pending', 'code': 'CLI_TRANSIENT', 'message': 'EOF'}}
            return {'status': 'synced'}
        with patch.object(jobs.time, 'sleep'):
            jobs.run_worker(self.fixture.project, runner=runner)
        self.assertEqual(jobs.read(self.path)['status'], 'completed')
        self.assertEqual(len(attempts), 2)

    def test_image_reference_partial_never_counts_as_complete(self):
        import canvas_job_backend as backend
        manifest = {'kind': 'images', 'session': {}, 'options': {'folder': str(self.fixture.project), 'decisions': {}}}
        cli = SimpleNamespace(check_control=lambda: None)
        for status in ('partial', 'pending', 'needs_decision', 'synced', 'unchanged'):
            result = {'status': 'images_imported', 'imported': ['CHAR0001'], 'references': {'status': status}}
            with patch.object(backend, 'ControlledCLI', return_value=cli), \
                 patch.object(c.media, 'scan_images', return_value={'matched': [{'key': 'CHAR0001'}]}), \
                 patch.object(c.media, 'images', return_value=result):
                actual = backend.run_job(self.fixture.project, manifest, lambda: None, lambda _: None)
            self.assertEqual(actual['status'], 'synced' if status in ('synced', 'unchanged') else 'partial')

    def test_old_falsely_completed_image_job_is_recoverable_without_reupload(self):
        job = {'job_id': 'job-oldimage', 'kind': 'images'}
        path = jobs.state_file(self.fixture.project, job['job_id'])
        d.write_json(path, {'status': 'completed', 'completed': 2, 'total': 2,
                            'result': {'references': {'status': 'partial'}}})
        view = jobs.job_state(self.fixture.project, job)
        self.assertEqual(view['status'], 'needs_attention')
        self.assertEqual(view['completed'], 2)
        self.assertEqual(view['error']['code'], 'REFERENCE_VERIFICATION_REQUIRED')
        self.assertEqual(jobs.read(path)['status'], 'completed')

    def test_auth_error_does_not_repeat_writes(self):
        attempts = []
        def runner(project, manifest, check_control, progress):
            attempts.append(1)
            c.fail('CLI_AUTH_ERROR', '403')
        with patch.object(jobs.time, 'sleep') as sleep:
            jobs.run_worker(self.fixture.project, runner=runner)
        self.assertEqual(jobs.read(self.path)['status'], 'needs_attention')
        self.assertEqual(len(attempts), 1)
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
