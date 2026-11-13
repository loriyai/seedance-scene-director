import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import canvas as c
import canvas_transport as transport
import director as d


def fixture():
    return {'rootCanvasId': 'ROOT', 'assets': {
        'ROOT': {'type': 'canvas', 'content': {'nodes': {'V': {'type': 'biz/video'}, 'R': {'type': 'biz/role'}}, 'edges': {}}},
        'V': {'type': 'biz/video', 'content': {'name': 'v', 'generation': {'prompt': 'hello', 'references': [], 'durationMs': 5000}}},
        'R': {'type': 'biz/role', 'content': {'basicInfo': {'name': 'r', 'appearanceName': ''}}}}}


class TransportTests(unittest.TestCase):
    def test_script_entry_uses_same_error_class_as_helpers(self):
        code = """import runpy, sys
sys.argv = ['canvas.py', '--help']
try:
    runpy.run_path('canvas.py', run_name='__main__')
except SystemExit:
    pass
import canvas, canvas_transport
from unittest.mock import patch
calls = []
def read(timeout):
    calls.append(timeout)
    if len(calls) == 1:
        canvas.fail('CLI_TRANSIENT', 'EOF')
    return True
with patch.object(canvas_transport.time, 'sleep'):
    assert canvas_transport.read_retry(read, lambda: None)
assert len(calls) == 2
"""
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=Path(c.__file__).parent,
                                capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sdk_diagnostics_before_final_json_preserve_returncode(self):
        output = 'SDK version { value: 1 }\n' + json.dumps({'ok': True, 'data': {'name': '中文'}})
        reply = subprocess.CompletedProcess([], 0, output, '')
        with patch.object(transport, 'execute', return_value=reply):
            self.assertTrue(c.run_json(['node'], '.')['ok'])
        reply.returncode = 1
        with patch.object(transport, 'execute', return_value=reply):
            with self.assertRaises(c.CanvasError):
                c.run_json(['node'], '.')
        with self.assertRaises(ValueError):
            transport.final_json(output + '\ntruncated tail')

    def test_known_edit_is_not_hidden_by_another_missing_field(self):
        self.assertEqual(c.compare_expected({'prompt': 'human change'},
            {'prompt': 'before', 'name': 'asset'}), 'conflict')
        self.assertEqual(c.compare_expected([{'prompt': 'human change'}],
            [{'prompt': 'before', 'name': 'asset'}]), 'conflict')

    def test_guard_recovery_codes_survive_before_and_after_handler(self):
        for code in ('READBACK_PENDING', 'CONCURRENT_EDIT'):
            for not_sent in (True, False):
                result = subprocess.CompletedProcess([], 1, json.dumps({'ok': False, 'code': code,
                    'message': 'guard decision', 'not_sent': not_sent, 'recovery_path': 'saved-evidence.json'}), '')
                with patch.object(transport, 'execute', return_value=result):
                    with self.assertRaises(c.CanvasError) as caught:
                        c.run_json(['node'], '.')
                self.assertEqual(caught.exception.code, code)
                self.assertIs(caught.exception.details['not_sent'], not_sent)
                self.assertEqual(caught.exception.details['recovery_path'], 'saved-evidence.json')

    def test_eof_read_retries_but_auth_and_writes_do_not(self):
        self.assertEqual(transport.error_code('read tcp: wsarecv: An existing connection was forcibly closed by the remote host.'), 'CLI_TRANSIENT')
        for status in (429, 502, 503, 504):
            self.assertEqual(transport.error_code(f'HTTP status {status}'), 'CLI_TRANSIENT')
        for status in (401, 403):
            self.assertEqual(transport.error_code(f'HTTP status {status}'), 'CLI_AUTH_ERROR')
        replies = [c.CanvasError('CLI_TRANSIENT', 'EOF'), {'ok': True}]
        calls = []
        def read(timeout):
            calls.append(timeout)
            item = replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        with patch.object(transport.time, 'sleep'):
            self.assertTrue(transport.read_retry(read, lambda: None)['ok'])
        self.assertEqual(len(calls), 2)
        with patch.object(transport, 'execute', return_value=subprocess.CompletedProcess([], 1, '', 'POST EOF')) as execute:
            with self.assertRaises(c.CanvasError) as error:
                c.run_json(['write'], '.')
            self.assertEqual(error.exception.code, 'CLI_TRANSIENT')
            self.assertEqual(execute.call_count, 1)
        with patch.object(transport.time, 'sleep') as sleep:
            with self.assertRaises(c.CanvasError) as error:
                transport.read_retry(lambda _: c.fail('CLI_AUTH_ERROR', '403'), lambda: None)
            self.assertEqual(error.exception.code, 'CLI_AUTH_ERROR')
            sleep.assert_not_called()

    def test_missing_key_empty_value_and_conflicting_value_are_distinct(self):
        doc = fixture()
        target = {'action': 'generation_settings', 'node_id': 'V', 'value': {'references': [], 'durationMs': 5000}}
        del doc['assets']['V']['content']['generation']['references']
        self.assertEqual(c.target_state(doc, target), 'matched')
        del doc['assets']['V']['content']['generation']['durationMs']
        self.assertEqual(c.target_state(doc, target), 'unknown')
        doc['assets']['V']['content']['generation']['durationMs'] = 0
        self.assertEqual(c.target_state(doc, target), 'conflict')
        field = {'action': 'field', 'node_id': 'R', 'value': ''}
        del doc['assets']['R']['content']['basicInfo']['appearanceName']
        self.assertTrue(c.target_satisfied(doc, field))
        doc['assets']['R']['content']['basicInfo']['appearanceName'] = False
        self.assertFalse(c.target_satisfied(doc, field))
        doc['assets']['R']['content'].pop('basicInfo')
        self.assertEqual(c.target_state(doc, field), 'unknown')
        doc['assets'].pop('V')
        self.assertEqual(c.target_state(doc, target), 'unknown')

    def test_optional_empty_keys_do_not_trigger_fingerprint_conflict(self):
        baseline = fixture()
        altered = copy.deepcopy(baseline)
        altered['assets']['R']['content']['basicInfo'].pop('appearanceName')
        altered['assets']['V']['content']['generation'].pop('references')
        for nid in ('R', 'V'):
            self.assertEqual(c.semantic_fingerprint(baseline, nid), c.semantic_fingerprint(altered, nid))
        altered['assets']['V']['content']['generation']['durationMs'] = 6000
        self.assertNotEqual(c.semantic_fingerprint(baseline, 'V'), c.semantic_fingerprint(altered, 'V'))

    def test_source_layout_is_ignored_but_media_and_identity_stay_strict(self):
        import canvas_batch
        baseline = fixture()
        altered = copy.deepcopy(baseline)
        altered['assets']['ROOT']['content']['nodes']['R'].update(x=700, y=300, w=320, h=200, order=2, style={'color': 'blue'})
        target = {'action': 'semantic_asset_snapshot', 'node_id': 'R',
                  'projection': 'source-business-v1', 'value': c.source_fingerprint(baseline, 'R')}
        self.assertTrue(c.target_satisfied(altered, target))
        class Client:
            def batch_snapshot(self, binding):
                return {'document': altered, 'asset_versions': {'ROOT': 2, 'R': 1, 'V': 1}}
        state = {'binding': {'canvas_id': 'ROOT'}}
        self.assertIs(canvas_batch.snapshot(Client(), state, baseline, [{'node_id': 'V'}], sources=['R'])['document'], altered)
        self.assertNotEqual(c.semantic_fingerprint(baseline, 'R'), c.semantic_fingerprint(altered, 'R'))
        altered['assets']['R']['content']['appearanceInfo'] = {'pippitAssetId': 'MEDIA-NEW'}
        self.assertFalse(c.target_satisfied(altered, target))
        with self.assertRaises(c.CanvasError) as error:
            canvas_batch.snapshot(Client(), state, baseline, [{'node_id': 'V'}], sources=['R'])
        self.assertEqual(error.exception.code, 'CONCURRENT_EDIT')
        altered = copy.deepcopy(baseline)
        altered['assets']['ROOT']['content']['nodes']['R']['type'] = 'biz/scene'
        self.assertFalse(c.target_satisfied(altered, target))

    def test_reference_semantics_preserve_media_and_source_identity(self):
        doc = fixture()
        expected = {'id': 'row-old', 'edgeId': 'old-edge', 'sourceNodeId': 'R',
                    'pippitAssetId': 'MEDIA', 'assetId': 'IMAGE', 'type': 'image',
                    'dataReferenceSelection': ['IMAGE']}
        actual = {**expected, 'id': 'row-new', 'edgeId': 'new-edge'}
        doc['assets']['V']['content']['generation']['references'] = [actual]
        target = {'action': 'semantic_generation_settings', 'node_id': 'V', 'value': {'references': [expected]}}
        self.assertTrue(c.target_satisfied(doc, target))
        actual['dataReferenceSelection'] = ['OTHER']
        self.assertFalse(c.target_satisfied(doc, target))
        legacy = {'id': 'R', 'type': 'image', 'pippitAssetId': 'MEDIA', 'dataReferenceSelection': ['IMAGE']}
        self.assertEqual(c.semantic_references([legacy], doc)[0]['id'], 'R')

    def test_read_budget_exhaustion_is_pending(self):
        checks = []
        def fail_read(timeout):
            checks.append(timeout)
            c.fail('READBACK_INCOMPLETE', 'missing asset')
        with patch.object(transport.time, 'sleep'):
            with self.assertRaises(c.CanvasError) as error:
                transport.read_retry(fail_read, lambda: None)
        self.assertEqual(error.exception.code, 'READBACK_PENDING')
        self.assertEqual(len(checks), 3)

    def test_nested_missing_media_and_names_are_unknown_but_empty_is_conflict(self):
        doc = fixture()
        reference = {'sourceNodeId': 'R', 'type': 'image', 'assetId': 'M', 'pippitAssetId': 'P'}
        target = {'action': 'semantic_generation_settings', 'node_id': 'V', 'value': {'references': [reference]}}
        row = {key: value for key, value in reference.items() if key != 'assetId'}
        doc['assets']['V']['content']['generation']['references'] = [row]
        self.assertEqual(c.target_state(doc, target), 'unknown')
        row['assetId'] = ''
        self.assertEqual(c.target_state(doc, target), 'conflict')
        patch = {'action': 'patch', 'node_id': 'R', 'patch': {'basicInfo': {'name': 'r', 'appearanceName': ''}}}
        doc['assets']['R']['content']['basicInfo'] = {'name': 'r'}
        self.assertEqual(c.target_state(doc, patch), 'matched')
        doc['assets']['R']['content']['basicInfo'] = {'appearanceName': ''}
        self.assertEqual(c.target_state(doc, patch), 'unknown')

    def test_source_projection_retains_nested_missing_evidence(self):
        doc = fixture()
        target = {'action': 'semantic_asset_snapshot', 'node_id': 'R', 'projection': 'source-business-v1',
                  'expected': c.semantic_projection(doc, 'R', source_only=True), 'value': c.source_fingerprint(doc, 'R')}
        doc['assets']['R']['content']['basicInfo'].pop('name')
        self.assertEqual(c.target_state(doc, target), 'unknown')
        doc['assets']['R']['content']['basicInfo']['name'] = ''
        self.assertEqual(c.target_state(doc, target), 'conflict')

    def test_complete_targeted_read_requires_payload_and_version(self):
        doc = fixture()
        calls = []
        class Client:
            def native(self, *args, **kwargs):
                ids = [args[i + 1] for i, value in enumerate(args) if value == '--asset-id']
                calls.append(ids)
                return {'team_id': '12', 'assets': [{'PippitAssetID': nid, 'version': 2,
                    'TextInfo': {'content': json.dumps(doc['assets'][nid])}} for nid in ids]}
        result = transport.snapshot(Client(), {'canvas_id': 'ROOT', 'team_id': '12'}, ['V'])
        self.assertEqual(calls, [['ROOT'], ['V']])
        self.assertEqual(set(result['document']['assets']), {'ROOT', 'V'})
        self.assertTrue(result['complete'])
        for row in ({'PippitAssetID': 'V', 'version': 2}, {'PippitAssetID': 'V', 'TextInfo': {'content': doc['assets']['V']}}):
            with self.assertRaises(c.CanvasError) as error:
                transport.decode_assets({'team_id': '12', 'assets': [row]}, ['V'], '12')
            self.assertEqual(error.exception.code, 'READBACK_INCOMPLETE')

    def test_windows_replace_sharing_failure_retries_only_local_write(self):
        real_replace = d.os.replace
        count = []
        def replace(source, target):
            count.append(1)
            if len(count) == 1:
                error = PermissionError('sharing violation')
                error.winerror = 32
                raise error
            return real_replace(source, target)
        with tempfile.TemporaryDirectory() as directory, patch.object(d.os, 'replace', side_effect=replace), patch.object(d.time, 'sleep'):
            target = Path(directory) / 'state.json'
            d.write_json(target, {'ack': True})
            self.assertEqual(d.read_json(target), {'ack': True})
            self.assertEqual(len(count), 2)


if __name__ == '__main__':
    unittest.main()
