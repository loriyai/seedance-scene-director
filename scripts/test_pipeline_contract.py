"""Delivery scope and observed progress tests; fictional files, no external actions."""
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import director as d
import pipeline_metrics as metrics
from core_test_support import setup_skill_fixture, ledger
import project_contract as contract


class ProjectContractTests(unittest.TestCase):
    def setUp(self):
        setup_skill_fixture(self)

    def test_contract_creation_idempotence_and_no_silent_scope_change(self):
        report = contract.initialize_project(self.work, 'assets')
        self.assertEqual(report['status'], 'project_initialized')
        self.assertEqual(contract.initialize_project(self.work, 'assets')['status'], 'existing_contract')
        with self.assertRaisesRegex(ValueError, '不同交付'):
            contract.initialize_project(self.work, 'workflow')
        value = contract.read_contract(self.work)
        self.assertTrue(contract.requires_design({}, project=self.work))
        self.assertEqual(value['scope'], 'assets')

    def test_contract_must_match_ledger_and_strict_bool(self):
        value = contract.initialize_project(self.work, 'assets')['contract']
        for bad in ({**value, 'require_designed': 1}, {**value, 'require_designed': False},
                    {**value, 'scope': 'unknown'}):
            with self.assertRaises(ValueError):
                contract.validate_contract(bad)
        with self.assertRaisesRegex(ValueError, '不一致'):
            contract.delivery_contract({'delivery_contract': {**value, 'scope': 'workflow'}}, self.work)

    def test_legacy_read_and_bound_project_are_not_migrated(self):
        self.assertFalse(contract.requires_design(self.data))
        (self.work / 'session.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '旧绑定'):
            contract.initialize_project(self.work, 'assets')
        self.assertFalse((self.work / 'project-contract.json').exists())

    def test_init_carries_contract_and_render_blocks_without_flag(self):
        contract.initialize_project(self.work, 'assets')
        path = self.work / 'contracted.json'
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.initialize(self.script, self.work, path, project=self.work)
        self.assertEqual(d.read_json(path)['delivery_contract'], contract.read_contract(self.work))
        self.data['delivery_contract'] = contract.read_contract(self.work)
        path.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        # Candidate source structure stays usable before design.
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, self.data)
        with self.assertRaises(ValueError):
            ledger.render(self.script, self.source, path, self.work / 'catalog.txt')
        self.assertFalse((self.work / 'catalog.txt').exists())

    def test_only_screenplay_contract_does_not_require_image_design(self):
        value = contract.initialize_project(self.work, 'screenplay')['contract']
        self.assertFalse(contract.requires_design({'delivery_contract': value}))
        self.assertTrue(contract.requires_design({'delivery_contract': value}, explicit=True))


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)

    def event(self, task, status, minute, ids=(), version='v1'):
        return metrics.record_event(self.work, 'design', task, status, object_ids=ids,
                                    input_digest=version, occurred_at=f'2026-10-17T00:{minute:02d}:00+00:00')

    def test_parallel_elapsed_is_separate_from_sum(self):
        events = [self.event('a', 'started', 0), self.event('b', 'started', 1),
                  self.event('a', 'finished', 4), self.event('b', 'finished', 5)]
        result = metrics.summarize(events)
        self.assertEqual(result['completed_window_seconds'], 300)
        self.assertEqual(result['completed_task_seconds_sum'], 480)

    def test_current_object_counts_do_not_claim_stale_pass(self):
        events = [self.event('a', 'draft_saved', 0, ['CHAR1']),
                  self.event('a', 'checked', 1, ['CHAR1']),
                  self.event('a', 'needs_review', 2, ['CHAR1']),
                  self.event('a', 'draft_saved', 3, ['CHAR2'])]
        row = metrics.summarize(events)['tasks'][0]
        self.assertEqual(row['counts'], {'draft_saved': 1, 'checked': 0, 'needs_review': 1, 'compiled': 0})
        self.assertEqual(row['rework_ids'], ['CHAR1'])
        self.assertIsNone(row['elapsed_seconds'])

    def test_first_check_per_input_and_no_fabricated_tokens(self):
        events = [self.event('a', 'failed', 0), self.event('a', 'checked', 1),
                  self.event('a', 'checked', 2, version='v2')]
        result = metrics.summarize(events)
        self.assertEqual((result['first_check_passed'], result['first_check_total']), (1, 2))
        self.assertIsNone(events[0]['tokens'])
        self.assertIsNone(events[0]['actual_model'])
        with self.assertRaises(ValueError):
            metrics.record_event(self.work, 'design', 'a', 'checked', tokens=-1)

    def test_duplicate_event_and_time_reversal_rejected(self):
        entry = self.event('a', 'started', 1)
        with self.assertRaisesRegex(ValueError, '重复'):
            metrics.summarize([entry, copy.deepcopy(entry)])
        with self.assertRaisesRegex(ValueError, '倒序'):
            metrics.summarize([entry, self.event('a', 'checked', 0)])

    def test_unknown_input_is_not_counted_as_verified_first_pass(self):
        result = metrics.summarize([self.event('a', 'checked', 0, version=None)])
        self.assertEqual(result['first_check_total'], 0)
        self.assertEqual(result['tasks'][0]['unbound_check_events'], 1)


if __name__ == '__main__':
    unittest.main()
