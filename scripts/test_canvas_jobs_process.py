#!/usr/bin/env python3
"""Real background process lifecycle with an injected, network-free worker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import canvas as c
import canvas_jobs as jobs
import director as d


class BackgroundProcessChecks(unittest.TestCase):
    def test_independent_monitor_notifies_after_forced_worker_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            source = project / 'source.json'
            d.write_json(source, {'paragraphs': []})
            session = {'schema_version': 1, 'revision': 0, 'phase': 'range', 'style_hash': None,
                       'files': {'source': {'path': str(source), 'sha256': d.file_hash(source)}},
                       'drafts': {}, 'ledger': {'schema_version': 3, 'characters': [],
                                              'scene_assets': [], 'props': [], 'scenes': []},
                       'units': [], 'receipts': [], 'ranges': []}
            d.write_json(project / 'session.json', session)
            state = c.blank_state()
            state['binding'] = {'canvas_id': 'MONITOR_TEST_ROOT', 'account_id': 'MONITOR_TEST_USER'}
            c.save(project, state)
            jobs.enqueue(project, 'assets')
            worker_source = '''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import canvas_jobs as jobs
import canvas as c
project = Path(sys.argv[1])
c.CLI = lambda *a, **k: (_ for _ in ()).throw(AssertionError('No CLI'))
jobs.notify_attention = jobs.notify_completion = lambda text: None
def run(project, manifest, check_control, progress):
    (project / 'entered').write_text('ready')
    while True:
        time.sleep(.05)
jobs.run_worker(project, runner=run, run_id='offline-kill-test')
'''
            monitor_source = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import canvas_jobs as jobs
project = Path(sys.argv[1])
def attention(text):
    (project / 'notified').write_text(text, encoding='utf-8')
    return {'status': 'test_notification_recorded'}
jobs.notify_attention = attention
jobs.notify_completion = lambda text: None
jobs.monitor_worker(project, int(sys.argv[3]), 'offline-kill-test')
'''
            options = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
                       'env': {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}}
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW
            worker = subprocess.Popen([sys.executable, '-c', worker_source, str(project), str(Path(__file__).parent)], **options)
            monitor = None
            try:
                deadline = time.monotonic() + 10
                while not (project / 'entered').exists() and worker.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((project / 'entered').exists())
                monitor = subprocess.Popen([sys.executable, '-c', monitor_source, str(project), str(Path(__file__).parent), str(worker.pid)], **options)
                worker.kill()
                worker.communicate(timeout=5)
                _, stderr = monitor.communicate(timeout=10)
                self.assertEqual(monitor.returncode, 0, stderr.decode('utf-8', 'replace'))
                current = jobs.status(project)
                self.assertEqual(current['jobs'][0]['status'], 'needs_attention')
                self.assertEqual(current['events'][0]['error']['code'], 'WORKER_EXITED_UNEXPECTEDLY')
                self.assertTrue((project / 'notified').exists())
            finally:
                for process in (worker, monitor):
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_foreground_can_append_storyboards_while_worker_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            source = project / 'source.json'
            d.write_json(source, {'paragraphs': []})
            session = {'schema_version': 1, 'revision': 0, 'phase': 'range', 'style_hash': None,
                       'files': {'source': {'path': str(source), 'sha256': d.file_hash(source)}},
                       'drafts': {}, 'ledger': {'schema_version': 3, 'characters': [],
                                              'scene_assets': [], 'props': [], 'scenes': []},
                       'units': [], 'receipts': [], 'ranges': []}
            d.write_json(project / 'session.json', session)
            state = c.blank_state()
            state['binding'] = {'canvas_id': 'PROCESS_TEST_ROOT', 'account_id': 'PROCESS_TEST_USER'}
            c.save(project, state)
            queued = jobs.enqueue(project, 'assets')
            runner = '''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import canvas_jobs as jobs
import canvas as c
project = Path(sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError("No real CLI in background-process test")
c.CLI = forbidden
jobs.notify_completion = lambda text: None
def run(project, manifest, check_control, progress):
    (project / 'entered').write_text('ready')
    deadline = time.monotonic() + 15
    while not (project / 'release').exists():
        if time.monotonic() > deadline:
            raise RuntimeError('test handshake timeout')
        time.sleep(.02)
    check_control()
    progress({'completed': 0, 'total': 0})
    return {'status': 'synced'}
jobs.run_worker(project, runner=run)
'''
            options = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
                       'env': {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}}
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen([sys.executable, '-c', runner, str(project), str(Path(__file__).parent)], **options)
            try:
                deadline = time.monotonic() + 10
                while not (project / 'entered').exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((project / 'entered').exists())
                self.assertTrue(jobs.status(project)['worker_active'])
                session['units'].append({'id': 'local-new-unit', 'version': 1, 'prompt': '本地新分镜'})
                session['ranges'].append({'span': [0, 10]})
                session['phase'] = 'nodes_choice'
                d.save(project, session)
                (project / 'release').write_text('continue')
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr.decode('utf-8', 'replace'))
                current = jobs.status(project)
                self.assertFalse(current['worker_active'])
                self.assertEqual(current['jobs'][0]['job_id'], queued['job_id'])
                self.assertEqual(current['jobs'][0]['status'], 'completed')
                self.assertEqual(len(current['events']), 1)
                self.assertEqual(d.read_json(project / 'session.json'), session)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
