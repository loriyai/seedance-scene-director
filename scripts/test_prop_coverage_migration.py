#!/usr/bin/env python3
"""Keep reverse-audit evidence and invalidate it on meaningful migration changes."""

import copy
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import SKILL, fixture, save, ledger, migration, design
from test_prop_coverage_policy import add_coverage


class PropCoverageMigrationTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "tests-prop-coverage-migration"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work / "old")
        add_coverage(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def run_migration(self, new_work=None, new_script=None, mapping=None):
        save(self.work / "old/ledger.json", self.data)
        return migration.migrate(self.work / "old/ledger.json", self.source,
                                 new_script or self.script, new_work or self.source.parent, mapping or {})

    def test_unchanged_v2_preserves_complete_review_and_design(self):
        self.data = design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")
        original = copy.deepcopy(self.data)
        result, report = self.run_migration()
        self.assertEqual(result["prop_coverage_review"], original["prop_coverage_review"])
        self.assertTrue(result["prop_coverage_review"]["reviewed_full_script"])
        self.assertEqual(report["pending_reviews"], [])
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, result)

    def test_core_single_visible_event_is_not_flagged_as_reuse_failure(self):
        # One continuous event can span two scenes; core approval still needs
        # reviewed evidence for both scene associations in the coverage audit.
        self.data["props"][0]["visible_uses"] = self.data["props"][0]["visible_uses"][:1]
        self.data["props"][0]["extraction_review"].update(
            kind="core_continuity", story_role="令牌交接是这一段的核心动作",
            consistency_reason="交接过程不同机位需保持令牌轮廓、孔位与握持结构")
        result, report = self.run_migration()
        self.assertTrue(result["prop_coverage_review"]["reviewed_full_script"])
        self.assertFalse(any(item["kind"] == "主要道具复用门槛待核" for item in report["pending_reviews"]))
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, result)

    def test_changed_source_retains_candidates_and_old_digest_for_review(self):
        newer = self.work / "new"
        new_script, _, paragraphs, _, _ = fixture(newer)
        paragraphs[1] += " 他转身离开。"
        save(newer / "source.json", {"paragraphs": [{"id": i, "text": text} for i, text in enumerate(paragraphs, 1)]})
        scene = newer / "scenes/scene_002_0002-0002.txt"
        text = scene.read_text(encoding="utf-8").rstrip() + " 他转身离开。\n"
        save(scene, text)
        save(new_script, (newer / "scenes/scene_001_0001-0001.txt").read_text(encoding="utf-8") + "\n" + text)
        result, report = self.run_migration(newer, new_script, {"scenes": {"SC0001": [1], "SC0002": [2]}})
        review = result["prop_coverage_review"]
        self.assertEqual(review["candidates"], self.data["prop_coverage_review"]["candidates"])
        self.assertEqual(review["source_digest"], self.data["source_digest"])
        self.assertNotEqual(review["source_digest"], result["source_digest"])
        self.assertIs(review["reviewed_full_script"], False)
        self.assertTrue(any(item["kind"] == "道具反向覆盖待复核" for item in report["pending_reviews"]))

    def test_relocated_scene_mapping_invalidates_even_without_source_change(self):
        result = copy.deepcopy(self.data)
        result["scenes"][0]["id"] = "SC0003"
        pending = []
        migration.preserve_prop_coverage(self.data, result, self.paragraphs, pending)
        self.assertIs(result["prop_coverage_review"]["reviewed_full_script"], False)
        self.assertEqual(result["prop_coverage_review"]["candidates"], self.data["prop_coverage_review"]["candidates"])
        self.assertEqual(pending[0]["kind"], "道具反向覆盖待复核")

    def test_changed_target_name_or_link_invalidates_existing_review(self):
        for change in ("name", "scene_link"):
            with self.subTest(change=change):
                result = copy.deepcopy(self.data)
                if change == "name":
                    result["props"][0]["name"] = "另一版本令牌"
                else:
                    result["scenes"][0]["prop_ids"] = []
                pending = []
                migration.preserve_prop_coverage(self.data, result, self.paragraphs, pending)
                self.assertIs(result["prop_coverage_review"]["reviewed_full_script"], False)
                self.assertEqual(len(pending), 1)

    def test_stale_or_unfinished_review_never_becomes_approved_on_noop(self):
        for field, value in (("source_digest", "stale"), ("reviewed_full_script", False)):
            with self.subTest(field=field):
                saved = copy.deepcopy(self.data["prop_coverage_review"])
                self.data["prop_coverage_review"][field] = value
                result, report = self.run_migration()
                self.assertIs(result["prop_coverage_review"]["reviewed_full_script"], False)
                self.assertTrue(any(item["kind"] == "道具反向覆盖待复核" for item in report["pending_reviews"]))
                self.data["prop_coverage_review"] = saved

    def test_v1_single_use_still_triggers_original_two_use_review(self):
        self.data["asset_policy"] = design.LEGACY_PROP_POLICY
        self.data.pop("prop_coverage_review")
        self.data["props"][0]["visible_uses"] = self.data["props"][0]["visible_uses"][:1]
        result, report = self.run_migration()
        self.assertNotIn("prop_coverage_review", result)
        self.assertTrue(any(item["kind"] == "主要道具复用门槛待核" for item in report["pending_reviews"]))
        self.assertFalse(any(item["kind"] == "道具反向覆盖待复核" for item in report["pending_reviews"]))


if __name__ == "__main__":
    unittest.main()
