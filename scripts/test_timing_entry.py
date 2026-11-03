"""Exercise the existing CLI with synthetic local events and command records."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import storyboard_text_delivery as delivery


class TimingEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        (self.project / 'session.json').write_text('{}', encoding='utf-8')

    def call(self, command, *args):
        output = io.StringIO()
        argv = ['storyboard_text_delivery.py', command, '--project', str(self.project), *args]
        with patch('sys.argv', argv), redirect_stdout(output):
            status = delivery.main()
        return status, json.loads(output.getvalue())

    def test_wait_reason_requires_actual_note_and_is_only_a_wait_observation(self):
        status, _ = self.call('event', '--name', 'review_waiting', '--reason', 'reviewer_busy')
        self.assertEqual(status, 1)
        self.assertFalse((self.project / 'text-storyboards/events.jsonl').exists())
        status, event = self.call('event', '--name', 'review_waiting', '--scene', 'SC0099',
                                  '--activity-id', 'fictional', '--reason', 'reviewer_busy',
                                  '--note', 'Synthetic observation of occupied reviewer.')
        self.assertEqual(status, 0)
        self.assertEqual(event['reason'], 'reviewer_busy')
        before = (self.project / 'text-storyboards/events.jsonl').read_bytes()
        status, summary = self.call('timing')
        self.assertEqual(status, 0)
        self.assertEqual(summary['handoff_reason_windows'][-1]['reason'], 'reviewer_busy')
        self.assertIsNone(summary['handoff_reason_windows'][-1]['elapsed_seconds'])
        self.assertEqual((self.project / 'text-storyboards/events.jsonl').read_bytes(), before)
        status, _ = self.call('event', '--name', 'review_started', '--reason', 'reviewer_busy',
                              '--note', 'Invalid reason on another event.')
        self.assertEqual(status, 1)
        self.assertEqual((self.project / 'text-storyboards/events.jsonl').read_bytes(), before)

    def test_command_measurements_are_read_separately_and_duplicate_paths_reused(self):
        command = self.project / 'command.json'
        command.write_text(json.dumps({
            'phase': 'preview', 'command': ['python', 'fictional-preview.py'],
            'measurement_kind': 'actual_subprocess_wall_time_not_model_or_semantic_review',
            'started_at': '2026-01-01T00:00:00+00:00',
            'finished_at': '2026-01-01T00:00:01+00:00',
            'elapsed_seconds': 0.99, 'returncode': 1,
        }), encoding='utf-8')
        before = command.read_bytes()
        status, summary = self.call('timing', '--command-log', str(command),
                                     '--command-log', str(command))
        self.assertEqual(status, 0)
        self.assertEqual(len(summary['command_measurements']), 1)
        self.assertEqual(summary['command_measurements'][0]['elapsed_seconds'], 0.99)
        self.assertIsNone(summary['total_wall_seconds'])
        self.assertIsNone(summary['model_seconds'])
        self.assertEqual(command.read_bytes(), before)
        self.assertFalse((self.project / 'text-storyboards/events.jsonl').exists())

    def test_delivery_milestone_requires_native_commit(self):
        status, result = self.call('event', '--name', 'delivered')
        self.assertEqual(status, 1)
        self.assertIn('实际commit', result['message'])
        self.assertFalse((self.project / 'text-storyboards/events.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
