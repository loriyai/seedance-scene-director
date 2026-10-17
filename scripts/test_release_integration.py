"""Compatibility of published asset gates and explicit local preflight stages."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import SKILL, fixture, save, design


class AssetCliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)
        self.ledger = self.work / 'candidate.json'
        save(self.ledger, self.data)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-X', 'utf8',
                               str(SKILL/'modules/screenplay-standardizer/scripts/asset_ledger.py'),
                               'preflight',str(self.script),str(self.source),str(self.ledger),*map(str,args)],
                              capture_output=True,text=True,encoding='utf-8')

    def test_both_explicit_candidate_interfaces_remain_read_only(self):
        before = self.ledger.read_bytes()
        remote = self.run_cli('--candidate')
        self.assertEqual(remote.returncode,0,remote.stderr)
        self.assertFalse(json.loads(remote.stdout)['formal_ready'])
        report=self.work/'report.json'
        local = self.run_cli('--stage','candidate','--report',report)
        self.assertEqual(local.returncode,0,local.stderr)
        self.assertEqual(json.loads(local.stdout)['semantic_review'],'not_performed')
        self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['stage'],'candidate')
        self.assertEqual(self.ledger.read_bytes(),before)

    def test_designed_gate_and_option_conflicts_cannot_bypass_checks(self):
        candidate = self.run_cli('--stage','designed')
        self.assertNotEqual(candidate.returncode,0)
        self.assertIn('完整美术设计',candidate.stderr)
        conflict = self.run_cli('--stage','candidate','--require-designed')
        self.assertNotEqual(conflict.returncode,0)
        self.assertIn('不能与',conflict.stderr)

    def test_stage_report_never_overwrites_an_input(self):
        before=self.ledger.read_bytes()
        result=self.run_cli('--stage','candidate','--report',self.ledger)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.ledger.read_bytes(),before)


if __name__=='__main__':
    unittest.main()
