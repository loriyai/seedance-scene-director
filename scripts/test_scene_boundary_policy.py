#!/usr/bin/env python3
"""Fictional source-grounded scene boundaries and early asset-risk regressions."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "modules/screenplay-standardizer/scripts"))
import screenplay_review as review
import scene_observations as observations
import screenplay_io


class SceneBoundaryPolicyTests(unittest.TestCase):
    def setUp(self):
        test_root = SKILL.parent / "tests-scene-boundaries"
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.work = Path(self.temp.name)
        (self.work / "scenes").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, paragraphs, scenes):
        self.paragraphs = paragraphs
        (self.work / "source.json").write_text(json.dumps({"paragraphs": [
            {"id": i, "text": value} for i, value in enumerate(paragraphs, 1)
        ]}, ensure_ascii=False), encoding="utf-8")
        for number, (place, start, end) in enumerate(scenes, 1):
            path = self.work / "scenes" / f"scene_{number:03d}_{start:04d}-{end:04d}.txt"
            path.write_text(f"场景{number}-日-{'内' if review.MOBILE_INTERIOR.search(place) else '外'}-{place}\n"
                            "人物：阿明-基础形象；\n" +
                            "\n".join("△ " + p for p in paragraphs[start - 1:end]) + "\n", encoding="utf-8")

    def retained(self, report, refs, evidence, kind="location_change"):
        item = report["boundary_items"][0]
        decision = copy.deepcopy(item["decision_template"])
        decision.update(outcome="retain", reason_kind=kind, source_paragraphs=refs,
                        evidence=evidence, reason="核对虚构原文，车辆已进入新道路，场界依据是实际地点变化。")
        return {"boundaries": {item["decision_key"]: decision}}

    def record(self, number=1, kind="candidate_mention", ref=1):
        return {"observation_id": "OBS0001", "scene_number": number, "kind": kind,
                "tentative_subject": "虚构小车及乘客", "source_paragraphs": [ref],
                "evidence": self.paragraphs[ref - 1], "status": "pending",
                "requires_decision": False, "basis": "待核实际场界与制作参考需求"}

    def test_bar_entry_mask_start_flags_camera_only_split_before_assets(self):
        self.fixture(["老头乐停在酒吧门口。", "阿明坐入老头乐，戴上头套。", "阿明启动车辆。"],
                     [("酒吧门口", 1, 1), ("老头乐内", 2, 3)])
        report = review.make_report(self.work, {})
        self.assertEqual(report["counts"]["missing"], 0)
        self.assertEqual(report["counts"]["scene_boundary"], 1)
        self.assertEqual(report["status"], "needs_review")
        self.assertNotIn("props", report)
        self.assertNotIn("scene_assets", report)

    def test_same_action_sequence_in_one_scene_passes_without_losing_cast_or_action(self):
        self.fixture(["老头乐停在酒吧门口。", "阿明坐入老头乐，戴上头套。", "阿明启动车辆。"],
                     [("酒吧门口", 1, 3)])
        report = review.make_report(self.work, {})
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["boundary_items"], [])
        self.assertEqual(len(report["scenes"][0]["paragraph_map"]), 3)

    def test_assemble_checks_pending_boundary_count_in_addition_to_status(self):
        self.fixture(["老头乐停在门口。", "阿明坐入老头乐。"], [("酒吧门口", 1, 1), ("老头乐内", 2, 2)])
        report = review.make_report(self.work, {})
        report["status"] = "passed"  # A caller cannot clear the gate by changing this alone.
        saved = self.work / "review.json"
        saved.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未处理"):
            screenplay_io.assemble(self.work, self.work / "script.txt", saved)

    def test_intersection_to_backseat_is_also_reviewed(self):
        self.fixture(["老头乐停在红绿灯处。", "后排的阿明蜷着腿。"],
                     [("城市路口红绿灯处", 1, 1), ("老头乐内", 2, 2)])
        report = review.make_report(self.work, {})
        self.assertEqual(report["counts"]["scene_boundary"], 1)
        self.assertEqual(report["boundary_items"][0]["scene_numbers"], [1, 2])

    def test_real_road_change_can_be_retained_with_source_evidence(self):
        self.fixture(["老头乐驶离路口。", "车驶入金荣街。", "阿明仍坐在后排。"],
                     [("城市路口红绿灯处", 1, 1), ("老头乐内", 2, 3)])
        report = review.make_report(self.work, {})
        decisions = self.retained(report, [2], "车驶入金荣街。")
        accepted = review.make_report(self.work, decisions, previous=report)
        self.assertEqual(accepted["status"], "passed")
        self.assertEqual(len(accepted["scenes"]), 2)
        self.assertEqual(accepted["boundary_items"][0]["status"], "human_retained")
        self.assertEqual(accepted["scenes"][1]["scene"], "场景2-日-内-老头乐内")

    def test_explicit_pool_transition_remains_a_scene_no_automatic_merge(self):
        self.fixture(["阿明坐在车内。", "转场 游泳池", "阿明跳入水中。"],
                     [("老头乐内", 1, 1), ("游泳池", 2, 3)])
        report = review.make_report(self.work, {})
        accepted = review.make_report(self.work, self.retained(report, [2], "转场 游泳池", "explicit_transition"))
        self.assertEqual(accepted["status"], "passed")
        self.assertEqual(len(accepted["scenes"]), 2)

    def test_sample_cannot_avoid_boundary_by_selecting_only_car_scene(self):
        self.fixture(["老头乐停在门口。", "阿明坐入老头乐。"], [("酒吧门口", 1, 1), ("老头乐内", 2, 2)])
        report = review.make_report(self.work, {}, scope="sample", scene_numbers=[2])
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(len(report["scenes"]), 1)
        self.assertEqual(len(report["boundary_drafts"]), 2)

    def test_changed_neighbour_invalidates_old_boundary_decision_even_with_cached_scene(self):
        self.fixture(["老头乐驶离路口。", "车驶入金荣街。"], [("路口", 1, 1), ("老头乐内", 2, 2)])
        initial = review.make_report(self.work, {})
        decisions = self.retained(initial, [2], "车驶入金荣街。")
        accepted = review.make_report(self.work, decisions)
        path = next((self.work / "scenes").glob("scene_001_*"))
        path.write_text(path.read_text(encoding="utf-8").replace("-路口", "-南路口"), encoding="utf-8")
        changed = review.make_report(self.work, decisions, previous=accepted, scope="sample", scene_numbers=[2])
        self.assertEqual(changed["counts"]["reused_scenes"], 1)
        self.assertEqual(changed["status"], "needs_review")

    def test_retention_does_not_accept_camera_only_reason_type_or_false_evidence(self):
        self.fixture(["老头乐停在路口。", "阿明转头。"], [("路口", 1, 1), ("老头乐内", 2, 2)])
        report = review.make_report(self.work, {})
        for kind, evidence in [("camera_change", "阿明转头。"), ("location_change", "车驶入金荣街。")]:
            with self.subTest(kind=kind, evidence=evidence):
                bad = review.make_report(self.work, self.retained(report, [2], evidence, kind))
                self.assertEqual(bad["status"], "needs_review")

    def test_station_building_is_not_classified_as_vehicle_interior(self):
        self.fixture(["阿明离开广场。", "阿明走进车站。"], [("广场", 1, 1), ("车站内", 2, 2)])
        report = review.make_report(self.work, {})
        self.assertEqual(report["boundary_items"], [])
        self.assertEqual(report["status"], "passed")

    def test_early_risks_are_bound_to_source_without_automatic_assets_or_approval(self):
        self.fixture(["老头乐停在酒吧门口。", "阿明戴上头套。"], [("酒吧门口", 1, 2)])
        records = [self.record()]
        original = copy.deepcopy(records)
        payload = observations.prepare_observations(self.work / "source.json", self.work, records)
        report = observations.validate_observations(payload, self.work / "source.json", self.work)
        self.assertEqual(records, original)
        self.assertEqual(payload["observations"], original)
        self.assertEqual(report["confirmed_fact_count"], 0)
        self.assertEqual(report["semantic_review"], "not_performed")
        self.assertEqual({d["kind"] for d in report["risk_diagnostics"]}, {"mobile_space", "wearable_dependency"})
        self.assertTrue(all(not d["blocking"] for d in report["risk_diagnostics"]))
        self.assertNotIn("props", payload)

    def test_explicit_dependency_observation_cannot_smuggle_a_fact(self):
        self.fixture(["阿明戴上头套。"], [("酒吧门口", 1, 1)])
        record = self.record(kind="asset_dependency")
        observations.prepare_observations(self.work / "source.json", self.work, [record])
        record["fact"] = {"field": "visual_facts", "text": "粉色头套", "attribution": "叙述"}
        with self.assertRaisesRegex(ValueError, "不能夹带"):
            observations.prepare_observations(self.work / "source.json", self.work, [record])

    def test_new_observation_import_checks_unselected_boundary_neighbour_freshness(self):
        self.fixture(["老头乐驶离路口。", "车驶入金荣街。"], [("路口", 1, 1), ("老头乐内", 2, 2)])
        initial = review.make_report(self.work, {}, scope="sample", scene_numbers=[2])
        checked = review.make_report(self.work, self.retained(initial, [2], "车驶入金荣街。"),
                                     scope="sample", scene_numbers=[2])
        payload = observations.prepare_observations(self.work / "source.json", self.work, [self.record(number=2, ref=2)])
        observations.validate_observations(payload, self.work / "source.json", self.work, checked)
        path = next((self.work / "scenes").glob("scene_001_*"))
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "相邻场稿已过期"):
            observations.validate_observations(payload, self.work / "source.json", self.work, checked)

    def test_legacy_snapshot_readable_but_cannot_authorize_new_import(self):
        self.fixture(["阿明戴上头套。"], [("酒吧门口", 1, 1)])
        payload = observations.prepare_observations(self.work / "source.json", self.work, [self.record()])
        report = review.make_report(self.work, {})
        for version in (7, 8):
            with self.subTest(version=version):
                snapshot = observations._review_snapshot(report, payload)
                snapshot["review_version"] = version
                self.assertEqual(observations._review_scenes(snapshot, payload, self.paragraphs), {1})
                with self.assertRaisesRegex(ValueError, "历史观察快照"):
                    observations.validate_observations(payload, self.work / "source.json", self.work, snapshot)
                report["review_version"] = version
                with self.assertRaisesRegex(ValueError, "版本过期"):
                    observations.validate_observations(payload, self.work / "source.json", self.work, report)


if __name__ == "__main__":
    unittest.main()
