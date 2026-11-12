"""Exercise the released semantic CLI and native binary against a loopback Canvas service.

Only the provider HTTP responses and bound account identity are fixtures. The CLI
loads snapshots, runs the official SDK mutations, sends them through its native
transport and gets fresh provider records for readback.
"""
import copy
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import canvas as c
import director as d
from test_canvas_batch import apply_patch, installed_entry
import test_storyboard_text_delivery as fixtures


def semantic_entry():
    entry = installed_entry()
    if entry is None:
        return None
    package = entry.parent.parent
    binary = package / 'bin' / ('pippit-tool-cli.exe' if os.name == 'nt' else 'pippit-tool-cli')
    version = json.loads((package / 'package.json').read_text(encoding='utf-8'))['version']
    if tuple(int(part) for part in version.split('.') if part.isdigit()) >= (1, 0, 57) and binary.is_file():
        return entry, binary
    return None


class CanvasHTTP(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        server = self.server
        url = urlsplit(self.path)
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        endpoint = url.path.rsplit('/', 1)[-1]
        server.requests[endpoint] += 1
        if endpoint == 'query':
            records = []
            for key in request['pippit_asset_ids']:
                if key in server.document['assets']:
                    records.append({'PippitAssetID': key, 'AssetType': 4, 'version': server.versions[key],
                                    'TextInfo': {'content': json.dumps(server.document['assets'][key])}})
            data = {'Assets': records}
        elif endpoint == 'batch_generate_asset_id':
            ids = [str(server.next_id + i) for i in range(request['count'])]
            server.next_id += len(ids)
            data = {'ids': ids}
        elif endpoint == 'batch_patch_asset':
            command = parse_qs(url.query).get('command', [])
            if not command:
                self.send_error(400, 'Missing SDK semantic command identity')
                return
            tx, = request['transactions']
            server.writes.append((command[0], tx['transaction_id']))
            touched = {row['asset_id'] for row in tx['patches']}
            reject = (server.fault == 'reject' or
                      server.fault == 'reject_prompt' and command[0] == 'xyq.generation.update_prompt') or any(
                row.get('base_asset_version', 0) != server.versions.get(row['asset_id'], 0)
                for row in tx['patches'])
            result = {'transaction_id': tx['transaction_id']}
            if reject:
                result.update(status='reject', message='fixture version conflict',
                              current_asset_versions={k: server.versions.get(k, 0) for k in touched})
            else:
                doc = copy.deepcopy(server.document)
                for row in tx['patches']:
                    apply_patch(doc, row)
                server.document = doc
                for key in touched:
                    server.versions[key] = server.versions.get(key, 0) + 1
                if server.fault == 'lost_ack' and command[0] == 'xyq.generation.update_prompt':
                    # The transaction is durable at the provider but its reply is lost.
                    self.close_connection = True
                    return
                result.update(status='ack', asset_versions={k: server.versions[k] for k in touched})
            data = {'results': [result]}
        else:
            self.send_error(404, 'Unexpected fixture endpoint')
            return
        raw = json.dumps({'ret': '0', 'data': data}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@unittest.skipUnless(semantic_entry() and shutil.which('node'),
                     'Set SEEDANCE_TEST_CANVAS_ENTRY to an installed official CLI >= 1.0.57')
class PublicSemanticTransport(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TextDeliveryIntegration('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        self.fixture.commit_fixture()
        self.session = d.load(self.project)
        for group in ('characters', 'props', 'scene_assets'):
            for item in self.session['ledger'][group]:
                item['image_prompt'] = item['name'] + '独立参考图。'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), CanvasHTTP)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        root = '9000'
        self.server.document = {'rootCanvasId': root, 'assets': {root: {
            'pippitAssetId': root, 'type': 'canvas', 'content': {
                'nodes': {}, 'edges': {}, 'settings': {
                    'snap': {'enabled': True, 'threshold': 8, 'targets': ['grid', 'node']},
                    'grid': {'size': 28, 'visible': True}},
                'metadata': {'title': 'Canvas', 'globalBounds': {
                    'minX': -4000, 'minY': -3000, 'maxX': 4000, 'maxY': 3000}}}}}}
        self.server.versions = {root: 1}
        self.server.next_id = 9100
        self.server.writes = []
        self.server.requests = Counter()
        self.server.fault = None
        port = self.server.server_address[1]
        (self.project / 'config.toml').write_text(f'xyq_api_url = "http://127.0.0.1:{port}"\n', encoding='utf-8')
        self.enterContext(patch.dict(os.environ, {
            'XYQ_ACCESS_KEY': 'synthetic-loopback-only', 'PIPPIT_CLI_DISABLE_UPDATE_CHECK': '1',
            'PIPPIT_CLI_DISABLE_TELEMETRY': '1', 'NO_PROXY': '127.0.0.1,localhost',
            'LOCALAPPDATA': str(self.project), 'XDG_STATE_HOME': str(self.project), 'XDG_CONFIG_HOME': str(self.project),
        }))
        entry, binary = semantic_entry()
        self.cli = c.CLI(self.project)
        self.cli.config = {'cli_path': str(binary), 'node_path': shutil.which('node'), 'canvas_entry': str(entry),
                           'canvas_contract': c.ASSET_CONTRACT}
        self.enterContext(patch.object(self.cli, 'auth', return_value={'identity': 'fixture-account'}))
        state = c.blank_state()
        state['binding'] = {'account_id': 'fixture-account', 'team_id': '0', 'canvas_id': root}
        c.save(self.project, state)

    def sync(self, kind, **kwargs):
        return c.sync(self.project, self.cli, kind, session_override=self.session, **kwargs)

    def test_asset_and_storyboard_round_trip(self):
        self.assertEqual(self.sync('assets')['status'], 'synced')
        state = c.read_state(self.project)
        snapshot = self.cli.batch_snapshot(state['binding'])['document']
        for item in c.desired_assets(self.session):
            nid = state['mappings'][item['key']]['node_id']
            self.assertTrue(all(c.target_satisfied(snapshot, target) for target in c.asset_targets(item, nid)))
        units = [u for u in self.session['units'] if any(s['dialogue_parts'] for s in u['shots'])]
        self.assertEqual(len(units), 2)
        before = len(self.server.writes)
        self.assertEqual(self.sync('videos', ids=[u['id'] for u in units])['status'], 'synced')
        calls = Counter(name for name, _ in self.server.writes[before:])
        self.assertEqual(calls, {'apply_mutations': 1, 'xyq.generation.update_prompt': len(units)})
        state = c.read_state(self.project)
        snap = self.cli.batch_snapshot(state['binding'])['document']
        expected, _ = c.desired_videos(self.session, state, snap, units, True)
        for unit, item in zip(units, expected):
            nid = state['mappings'][unit['id']]['node_id']
            self.assertEqual(c.prompt_of(snap, nid), item['prompt'])
            self.assertIn(unit['shots'][0]['dialogue_parts'][0]['text'], c.prompt_of(snap, nid))
            self.assertEqual(c.content(snap, nid)['generation']['durationMs'], unit['duration'] * 1000)
            self.assertEqual(c.content(snap, nid)['generation']['ratio'], '16:9')
            self.assertTrue(c.node_asset_ids(item['prompt']))
            for src in c.node_asset_ids(item['prompt']):
                self.assertIsNotNone(c.reference_edge(snap, src, nid))
        before = self.server.next_id
        self.assertEqual(self.sync('videos', ids=[unit['id']])['status'], 'synced')
        self.assertEqual(self.server.next_id, before)
        self.assertEqual(c.read_state(self.project)['mappings'][unit['id']]['node_id'], nid)
        self.assertTrue(self.server.writes)

    def single_asset(self):
        self.session['ledger']['characters'] = []
        self.session['ledger']['scene_assets'] = []
        self.session['ledger']['props'] = self.session['ledger']['props'][:1]

    def test_explicit_rejection_never_claims_ready(self):
        self.single_asset()
        self.server.fault = 'reject'
        with self.assertRaises(c.CanvasError):
            self.sync('assets')
        self.assertEqual(set(self.server.document['assets']), {'9000'})
        self.assertEqual(len(self.server.writes), 1)
        self.assertFalse(any(m.get('stage') == 'ready' for m in c.read_state(self.project)['mappings'].values()))

    def test_lost_commit_ack_is_not_replayed(self):
        self.single_asset()
        self.server.fault = 'lost_ack'
        with self.assertRaises(c.CanvasError) as fault:
            self.sync('assets')
        self.assertEqual(fault.exception.code, 'UNCERTAIN_WRITE')
        self.assertEqual([command for command, _ in self.server.writes],
                         ['apply_mutations', 'xyq.generation.update_prompt'])
        self.assertEqual(len(self.server.document['assets']), 2)
        state = c.read_state(self.project)
        mapping = state['mappings'][self.session['ledger']['props'][0]['id']]
        self.assertIn(mapping['node_id'], self.server.document['assets'])
        self.assertNotEqual(mapping['stage'], 'ready')
        self.assertEqual(state['journal'][-1]['status'], 'uncertain')
        self.server.fault = None
        self.assertEqual(self.sync('assets')['status'], 'synced')
        updated = c.read_state(self.project)
        self.assertEqual(updated['journal'][-1]['status'], 'verified')
        self.assertEqual(updated['mappings'][self.session['ledger']['props'][0]['id']]['stage'], 'ready')
        self.assertEqual(updated['mappings'][self.session['ledger']['props'][0]['id']]['node_id'], mapping['node_id'])
        self.assertEqual(len(self.server.writes), 2)

    def test_partially_completed_asset_group_is_not_replayed(self):
        self.single_asset()
        self.server.fault = 'reject_prompt'
        with self.assertRaises(c.CanvasError) as first:
            self.sync('assets')
        self.assertEqual(first.exception.code, 'UNCERTAIN_WRITE')
        state = c.read_state(self.project)
        pending = state['mappings'][self.session['ledger']['props'][0]['id']]
        self.assertIn(pending['node_id'], self.server.document['assets'])
        self.assertNotEqual(pending['stage'], 'ready')
        self.assertEqual(state['journal'][-1]['status'], 'uncertain')
        writes = list(self.server.writes)
        self.server.fault = None
        with self.assertRaises(c.CanvasError) as retry:
            self.sync('assets')
        self.assertEqual(retry.exception.code, 'UNCERTAIN_WRITE')
        self.assertEqual(self.server.writes, writes)


if __name__ == '__main__':
    unittest.main()
