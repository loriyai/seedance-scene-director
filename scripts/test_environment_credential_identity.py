import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import canvas


class EnvironmentCredentialIdentityTests(unittest.TestCase):
    def test_native_uid_remains_authoritative(self):
        with patch.dict('os.environ', {'XYQ_ACCESS_KEY': 'test-only-key'}):
            identity, kind = canvas.credential_identity({'logged_in': True, 'source': 'browser', 'uid': 'user-a'})
        self.assertEqual((identity, kind), ('user-a', 'cli-account-or-scope'))

    def test_environment_scope_is_stable_and_secret_free(self):
        status = {'logged_in': True, 'source': 'environment'}
        with patch.dict('os.environ', {'XYQ_ACCESS_KEY': 'test-only-key-a'}):
            first = canvas.credential_identity(status)
            self.assertEqual(first, canvas.credential_identity(status))
        with patch.dict('os.environ', {'XYQ_ACCESS_KEY': 'test-only-key-b'}):
            second = canvas.credential_identity(status)
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1], 'environment-credential-scope')
        self.assertNotIn('test-only-key', json.dumps(first))
        self.assertEqual(status, {'logged_in': True, 'source': 'environment'})

    def test_missing_credentials_and_other_sources_still_fail(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(canvas.credential_identity({'logged_in': True, 'source': 'environment'}), (None, None))
        with patch.dict('os.environ', {'XYQ_ACCESS_KEY': 'test-only-key'}):
            for status in ({'logged_in': False, 'source': 'environment'}, {'logged_in': True, 'source': 'browser'}):
                self.assertEqual(canvas.credential_identity(status), (None, None))

    def test_auth_stores_only_scope_and_key_changes_block_old_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            cli = canvas.CLI(folder)
            with patch.object(cli, 'native', return_value={'logged_in': True, 'source': 'environment'}), \
                    patch.dict('os.environ', {'XYQ_ACCESS_KEY': 'test-only-first-secret'}):
                identity = cli.auth()['identity']
            self.assertNotIn('test-only-first-secret', (Path(folder) / 'canvas/connection.json').read_text())
            state = canvas.blank_state()
            state['binding'] = {'account_id': identity, 'canvas_id': 'ROOT', 'team_id': '12'}
            canvas.save(folder, state)
            with patch.object(cli, 'native', return_value={'logged_in': True, 'source': 'environment'}), \
                    patch.dict('os.environ', {'XYQ_ACCESS_KEY': 'test-only-second-secret'}), \
                    patch.object(canvas, 'document', side_effect=AssertionError('Must stop before canvas access')):
                with self.assertRaises(canvas.CanvasError) as error:
                    canvas.connected(folder, cli)
            self.assertEqual(error.exception.code, 'ACCOUNT_CHANGED')


if __name__ == '__main__':
    unittest.main()
