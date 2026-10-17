"""Impact reports bind exact inputs without renewing stale review evidence."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "modules/screenplay-standardizer/scripts"))
import asset_impact as impact


def fixture():
    return {"schema_version": 3, "source_digest": "a" * 64,
        "characters": [
            {"id": "CHAR0001", "name": "甲-基础形象", "visual_facts": [],
             "appearance": {"identity": "甲", "incarnation": "今生"},
             "visual_design": {"identity_anchor_id": "甲", "styling": "灰衣"}},
            {"id": "CHAR0002", "name": "甲-佩戴形象", "visual_facts": [],
             "appearance_variant": {"base_asset_id": "CHAR0001", "prop_asset_id": "PROP0001"},
             "visual_design": {"identity_anchor_id": "甲", "styling": "灰衣"}},
            {"id": "CHAR0003", "name": "无关人物", "visual_facts": []}],
        "locations": [{"id": "LOC0001", "name": "正厅", "visual_facts": []}],
        "scene_assets": [{"id": "SET0001", "name": "正厅", "location_id": "LOC0001", "variant": "base", "parent_id": None,
                          "visual_facts": [], "visual_design": {"space": "入口正对后墙"}},
                         {"id": "SET0002", "name": "正厅-宴会", "location_id": "LOC0001", "variant": "decor", "parent_id": "SET0001",
                          "visual_facts": []}],
        "props": [{"id": "PROP0001", "name": "头套", "visual_facts": [], "visual_design": {"structure": "圆头套", "materials_palette": "白色"}}],
        "scenes": [{"id": "SC0001", "character_ids": ["CHAR0001", "CHAR0002"], "prop_ids": ["PROP0001"],
                    "location_id": "LOC0001", "scene_asset_id": "SET0001", "source_range": [1, 2]},
                   {"id": "SC0002", "character_ids": ["CHAR0003"], "prop_ids": [],
                    "location_id": "LOC0001", "scene_asset_id": "SET0002", "source_range": [3, 3]}],
        "identity_anchors": [{"id": "甲", "face": "长脸"}], "pending_reviews": [], "retired_ids": [], "inactive_assets": {"characters": []}}


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.before = fixture()
        self.after = copy.deepcopy(self.before)

    def report(self, **kwargs):
        snapshots = copy.deepcopy((self.before, self.after))
        result = impact.analyze_asset_impact(self.before, self.after, **kwargs)
        self.assertEqual((self.before, self.after), snapshots)
        self.assertFalse(result["automatic_review_reuse"])
        self.assertFalse(result["formal_ready"])
        return result

    def test_name_only_recompile_not_semantic_but_no_automatic_clearance(self):
        self.after["characters"][2]["name"] = "无关人物-完整制作名"
        report = self.report()
        self.assertEqual(report["direct"][0]["change"], "name_only")
        self.assertFalse(report["direct"][0]["requires_semantic_review"])
        self.assertEqual(report["required_recompile_ids"], ["CHAR0003"])
        self.assertEqual(report["required_semantic_review_ids"], [])

    def test_local_fact_marks_identity_dependents_but_not_unrelated_character(self):
        self.after["characters"][0]["visual_facts"] = [{"text": "左眉有疤"}]
        report = self.report()
        self.assertEqual(report["direct"][0]["change"], "local_facts")
        self.assertIn("CHAR0002", report["required_semantic_review_ids"])
        self.assertNotIn("CHAR0003", report["required_semantic_review_ids"])

    def test_shared_prop_design_marks_masked_character_dependency(self):
        self.after["props"][0]["visual_design"]["materials_palette"] = "灰色"
        report = self.report()
        masked = next(row for row in report["dependent"] if row["id"] == "CHAR0002")
        self.assertIn("PROP0001", masked["trigger_ids"])
        self.assertIn("shared_covering_PROP_dependency", masked["reasons"])
        self.assertNotIn("CHAR0003", report["required_semantic_review_ids"])

    def test_LOC_and_SET_changes_propagate_to_versions(self):
        self.after["locations"][0]["visual_facts"] = [{"text": "门在东侧"}]
        report = self.report()
        self.assertTrue({"SET0001", "SET0002"}.issubset(report["required_semantic_review_ids"]))
        self.assertNotIn("LOC0001", report["required_recompile_ids"])
        self.after = copy.deepcopy(self.before)
        self.after["scene_assets"][0]["visual_design"]["space"] = "入口在左侧"
        report = self.report()
        self.assertIn("SET0002", report["required_semantic_review_ids"])

    def test_source_changed_expands_unknown_to_all_assets(self):
        self.after["source_digest"] = "b" * 64
        report = self.report()
        ids = {row["id"] for row in report["unknown"]}
        self.assertIn("CHAR0003", ids)
        self.assertIn("source_digest_changed_global_context_must_be_reviewed", report["global_or_unknown_reasons"])
        self.assertEqual(self.after["source_digest"], "b" * 64)

    def test_source_file_mismatch_and_unknown_top_level_are_conservative(self):
        report = self.report(before_source={"paragraphs": ["甲"]}, after_source={"paragraphs": ["乙"]})
        self.assertTrue(report["unknown"])
        self.after["unrecognized_identity_policy"] = {"value": "changed"}
        report = self.report()
        self.assertIn("unclassified_top_level_change:unrecognized_identity_policy", report["global_or_unknown_reasons"])

    def test_payload_and_identity_anchor_hashes_bind_report(self):
        first = {"designs": [{"asset_id": "PROP0001", "structure": "原结构"}], "identity_anchors": self.before["identity_anchors"]}
        second = copy.deepcopy(first)
        second["designs"][0]["structure"] = "新结构"
        report = self.report(before_design=first, after_design=second)
        self.assertIn("before_design", report["input_sha256"])
        self.assertNotEqual(report["input_sha256"]["before_design"], report["input_sha256"]["after_design"])
        self.assertIn("CHAR0002", report["required_semantic_review_ids"])
        self.after["identity_anchors"][0]["face"] = "圆脸"
        anchored = self.report()
        self.assertIn("IDENTITY:甲", anchored["required_semantic_review_ids"])
        self.assertIn("CHAR0001", anchored["required_semantic_review_ids"])

    def test_same_input_has_stable_empty_report_and_no_authorization(self):
        report = self.report()
        self.assertEqual(report["direct"], [])
        self.assertEqual(report["dependent"], [])
        self.assertEqual(report["unknown"], [])
        self.assertEqual(report["binding_sha256"], self.report()["binding_sha256"])

    def test_candidate_target_change_reports_both_targets(self):
        candidate = {"candidate_id": "CAND0001", "target_id": "PROP0001", "disposition": "asset", "scene_ids": ["SC0001"]}
        self.before["prop_coverage_review"] = {"candidates": [candidate]}
        self.after["prop_coverage_review"] = {"candidates": [dict(candidate, target_id="SET0001", disposition="set")]}
        report = self.report()
        self.assertTrue({"PROP0001", "SET0001"}.issubset({row["id"] for row in report["direct"]}))


if __name__ == "__main__":
    unittest.main()
