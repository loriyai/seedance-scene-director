"""Modern stage routing, session projection and single-writer reservations."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import agent_handoff as handoff
import test_agent_handoff as legacy


class StageHandoffTests(unittest.TestCase):
    # Reuse setup/helper functions, not the legacy test suite.
    write_json = legacy.HandoffTests.write_json
    save_config = legacy.HandoffTests.save_config
    save_brief = legacy.HandoffTests.save_brief
    prepare = legacy.HandoffTests.prepare
    artifact = legacy.HandoffTests.artifact

    def setUp(self):
        legacy.HandoffTests.setUp(self)
        self.config['handoff_policy'] = 'stage-handoff-v2'
        self.config['scheduling'] = {'fork_turns': 'none', 'max_active_design_groups': 2}
        self.config['stages']['review']['references'] = []
        self.config['stages']['assets']['references'] = ['references/assets.md']
        for name in ('execution-contract.md', 'assets.md'):
            (self.skill / 'references' / name).write_text('领域规则', encoding='utf-8')
        self.save_config()

    def test_review_loads_selected_domain_and_minimal_common_rules(self):
        self.brief['review_stage'] = 'assets'
        self.save_brief()
        _, _, manifest = self.prepare('review')
        names = {Path(item['path']).name for item in manifest['inputs']}
        self.assertTrue({'assets.md', 'execution-contract.md', 'model-routing.json'} <= names)
        self.assertNotIn('SKILL.md', names)
        self.assertNotIn('stage.md', names)

    def test_review_needs_domain_before_state_creation(self):
        with self.assertRaisesRegex(ValueError, 'review_stage'):
            self.prepare('review')
        self.assertFalse((self.project / 'agents').exists())

    def test_phase_and_revision_change_allowed_actual_unit_change_rejected(self):
        state = {'phase': 'asset_choice', 'revision': 1, 'units': [], 'style_hash': 'a', 'unknown': 1}
        self.write_json(self.project / 'session.json', state)
        _, path, manifest = self.prepare()
        self.artifact(manifest)
        state.update(phase='range', revision=2)
        self.write_json(self.project / 'session.json', state)
        self.assertEqual(handoff.verify(str(path))['result_status'], 'draft_complete')
        for field, value in (('units', [{'id': 'new'}]), ('style_hash', 'b'), ('unknown', 2)):
            bad = {**state, field: value}
            self.write_json(self.project / 'session.json', bad)
            with self.assertRaises(ValueError):
                handoff.verify(str(path))

    def test_reservation_blocks_second_writer_until_actual_complete(self):
        _, path, manifest = self.prepare()
        with self.assertRaisesRegex(ValueError, '占用'):
            self.prepare('assets')
        self.brief['review_stage'] = 'assets'
        self.save_brief()
        self.prepare('review')
        # Restore immutable brief input before accepting original draft.
        self.brief.pop('review_stage')
        self.save_brief()
        with self.assertRaises(ValueError):
            handoff.close(str(path), 'completed', '等待产物')
        self.artifact(manifest)
        handoff.close(str(path), 'completed', '文件与业务核对已完成')
        self.prepare('assets')

    def test_cancel_requires_reason_and_does_not_claim_semantic_pass(self):
        _, path, _ = self.prepare()
        with self.assertRaises(ValueError):
            handoff.close(str(path), 'cancelled', '')
        result = handoff.close(str(path), 'cancelled', '已在宿主停止写者并确认退出')
        self.assertEqual(result['status'], 'cancelled')
        self.assertNotIn('semantic_review', result)
        with self.assertRaisesRegex(ValueError, '终态'):
            handoff.close(str(path), 'completed', '不能复活')
        with self.assertRaisesRegex(ValueError, '已取消'):
            handoff.verify(str(path))
        self.prepare()

    def test_new_binding_during_unbound_draft_invalidates_handoff(self):
        _, path, manifest = self.prepare()
        self.artifact(manifest)
        self.write_json(self.project / 'session.json', {'phase': 'complete', 'revision': 1})
        with self.assertRaisesRegex(ValueError, '存在性'):
            handoff.verify(str(path))

    def test_group_reservations_enforce_same_plan_disjoint_ids_and_limit(self):
        base = self.project / 'reservations'
        base.mkdir()
        group = {'baseline_sha256': 'b', 'plan_sha256': 'p', 'owned_asset_ids': ['CHAR1']}
        folder = base / 'first'; folder.mkdir()
        self.write_json(folder / 'handoff.json', {'routing_policy': 'stage-handoff-v2', 'stage': 'design', 'design_group': group})
        other = {**group, 'owned_asset_ids': ['CHAR2']}
        handoff.check_reservations(base, 'design', other, 2)
        for bad, limit in ((group, 2), ({**other, 'plan_sha256': 'other'}, 2), (other, 1)):
            with self.assertRaises(ValueError):
                handoff.check_reservations(base, 'design', bad, limit)
        with self.assertRaises(ValueError):
            handoff.check_reservations(base, 'standardize', None, 2)


if __name__ == '__main__':
    unittest.main()
