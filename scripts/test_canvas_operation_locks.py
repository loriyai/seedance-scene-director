"""Operation lock ownership, conservative legacy recovery and PID reuse."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import canvas as c
import canvas_jobs as jobs


class OperationLockTests(unittest.TestCase):
    def setUp(self):
        sandbox = tempfile.TemporaryDirectory(prefix='canvas-lock-test-')
        self.addCleanup(sandbox.cleanup)
        self.root = Path(sandbox.name)
        self.enterContext(patch.object(tempfile, 'tempdir', sandbox.name))
        self.project = self.root / 'project'
        self.binding = {'account_id': 'FICTIONAL_OWNER', 'canvas_id': 'FICTIONAL_CANVAS'}
        state = c.blank_state()
        state['binding'] = self.binding
        c.save(self.project, state)
        self.local = self.project / 'canvas/operation.lock'
        self.shared = self.root / ('seedance-canvas-' + c.digest([
            self.binding['account_id'], self.binding['canvas_id']]) + '.lock')

    def owner(self, start='birth-a', token='owner-a'):
        return {'policy': 'canvas-operation-lock-v1', 'pid': 12345,
                'process_start': start, 'owner_token': token}

    def write_owner(self, owner):
        text = json.dumps(owner, sort_keys=True)
        self.local.write_text(text, encoding='utf-8')
        self.shared.write_text(text, encoding='utf-8')
        return text

    def test_new_lock_records_birth_and_token_then_releases_both_paths(self):
        with patch.object(c, 'process_start_identity', return_value='birth-a'):
            with c.project_lock(self.project):
                local = json.loads(self.local.read_text(encoding='utf-8'))
                self.assertEqual(local['pid'], os.getpid())
                self.assertEqual(local['process_start'], 'birth-a')
                self.assertTrue(local['owner_token'])
                self.assertEqual(local, json.loads(self.shared.read_text(encoding='utf-8')))
        self.assertFalse(self.local.exists())
        self.assertFalse(self.shared.exists())

    def test_same_canvas_still_has_one_writer_across_projects(self):
        second = self.root / 'second'
        c.save(second, c.read_state(self.project))
        with c.project_lock(self.project):
            original = self.shared.read_bytes()
            with self.assertRaises(c.CanvasError) as caught:
                with c.project_lock(second):
                    self.fail('second writer entered')
            self.assertEqual(caught.exception.code, 'BUSY')
            self.assertEqual(self.shared.read_bytes(), original)
            self.assertFalse((second / 'canvas/operation.lock').exists())

    def test_live_matching_birth_is_not_recovered(self):
        original = self.write_owner(self.owner())
        with patch.object(jobs, 'alive', return_value=True), \
                patch.object(c, 'process_start_identity', return_value='birth-a'):
            jobs.recover_dead_operation_locks(self.project)
        self.assertEqual(self.local.read_text(encoding='utf-8'), original)
        self.assertTrue(self.shared.exists())

    def test_reused_pid_with_different_birth_recovers_both_paths(self):
        self.write_owner(self.owner())
        with patch.object(jobs, 'alive', return_value=True), \
                patch.object(c, 'process_start_identity', return_value='birth-b'):
            jobs.recover_dead_operation_locks(self.project)
        self.assertFalse(self.local.exists())
        self.assertFalse(self.shared.exists())

    def test_unknown_birth_keeps_live_owner_lock(self):
        for start in ('birth-a', None):
            with self.subTest(start=start):
                original = self.write_owner(self.owner(start=start))
                with patch.object(jobs, 'alive', return_value=True), \
                        patch.object(c, 'process_start_identity', return_value=None):
                    jobs.recover_dead_operation_locks(self.project)
                self.assertEqual(self.local.read_text(encoding='utf-8'), original)
                self.assertTrue(self.shared.exists())

    def test_legacy_pid_requires_proven_process_exit(self):
        self.local.write_text('12345', encoding='utf-8')
        with patch.object(jobs, 'alive', return_value=True), \
                patch.object(c, 'process_start_identity', return_value='unrelated-birth') as identity:
            jobs.recover_dead_operation_locks(self.project)
        identity.assert_not_called()
        self.assertEqual(self.local.read_text(), '12345')
        with patch.object(jobs, 'alive', return_value=False):
            jobs.recover_dead_operation_locks(self.project)
        self.assertFalse(self.local.exists())

    def test_dead_new_owner_does_not_require_birth_lookup(self):
        self.write_owner(self.owner())
        with patch.object(jobs, 'alive', return_value=False), \
                patch.object(c, 'process_start_identity') as identity:
            jobs.recover_dead_operation_locks(self.project)
        identity.assert_not_called()
        self.assertFalse(self.local.exists())
        self.assertFalse(self.shared.exists())

    def test_malformed_or_unknown_lock_format_is_preserved(self):
        for raw in ('not-an-owner', '{}', json.dumps({**self.owner(), 'policy': 'unknown'})):
            with self.subTest(raw=raw):
                self.local.write_text(raw, encoding='utf-8')
                with self.assertRaises(c.CanvasError) as caught:
                    jobs.recover_dead_operation_locks(self.project)
                self.assertEqual(caught.exception.code, 'LOCK_OWNER_INVALID')
                self.assertEqual(self.local.read_text(encoding='utf-8'), raw)

    def test_recovery_does_not_remove_changed_ownership(self):
        self.write_owner(self.owner())
        replacement = json.dumps(self.owner(start='birth-b', token='owner-b'), sort_keys=True)

        def changed_owner(pid):
            self.local.write_text(replacement, encoding='utf-8')
            return 'birth-b'

        with patch.object(c, 'process_start_identity', side_effect=changed_owner):
            self.assertFalse(c.recover_operation_lock(self.local, lambda pid: True))
        self.assertEqual(self.local.read_text(encoding='utf-8'), replacement)

    def test_release_does_not_remove_replacement_owner(self):
        with c.project_lock(self.project):
            replacement = self.write_owner(self.owner(token='replacement-owner'))
        self.assertEqual(self.local.read_text(encoding='utf-8'), replacement)
        self.assertEqual(self.shared.read_text(encoding='utf-8'), replacement)

    @unittest.skipUnless(os.name == 'nt' or sys.platform.startswith('linux'),
                         'Process birth identity is unavailable on this platform')
    def test_current_process_has_stable_real_birth_identity(self):
        first = c.process_start_identity(os.getpid())
        self.assertIsInstance(first, str)
        self.assertTrue(first)
        self.assertEqual(c.process_start_identity(os.getpid()), first)
        self.assertIsNone(c.process_start_identity(0))


if __name__ == '__main__':
    unittest.main()
