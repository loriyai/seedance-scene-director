"""Queue recovery keeps historical evidence without claiming fresh verification."""
import unittest

import canvas as c
import canvas_jobs as jobs
import director as d
import test_canvas_jobs as fixtures


class ResumeProgressTests(unittest.TestCase):
    def test_readback_pending_keeps_historical_count_and_can_resume(self):
        fixture = fixtures.CanvasJobChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        queued = fixture.enqueue()
        path = jobs.state_file(fixture.project, queued['job_id'])
        state = jobs.read(path)
        state.update(completed=1, node_ids={'CHAR0001': 'ORIGINAL_NODE'})
        d.write_json(path, state)

        def interrupted(project, manifest, check_control, progress):
            progress({'completed': 0, 'node_ids': {}, 'stage': 'preparing'})
            c.fail('READBACK_PENDING', 'Synthetic read timeout after acknowledged write')

        jobs.run_worker(fixture.project, runner=interrupted)
        state = jobs.read(path)
        self.assertEqual(state['status'], 'needs_attention')
        self.assertEqual(state['stage'], 'awaiting_readback')
        self.assertEqual(state['completed'], 0)
        self.assertEqual(state['cumulative_completed'], 1)
        self.assertEqual(state['cumulative_node_ids'], {'CHAR0001': 'ORIGINAL_NODE'})
        self.assertEqual(state['run_completed'], 0)
        jobs.resume(fixture.project, start=False)

        def recovered(project, manifest, check_control, progress):
            progress({'completed': 1, 'node_ids': {'CHAR0001': 'ORIGINAL_NODE'}, 'stage': 'node_verified'})
            return {'status': 'synced'}

        jobs.run_worker(fixture.project, runner=recovered)
        state = jobs.read(path)
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['run_completed'], 1)
        self.assertEqual(state['cumulative_node_ids'], {'CHAR0001': 'ORIGINAL_NODE'})


if __name__ == '__main__':
    unittest.main()
