#!/usr/bin/env python3
"""Regression coverage for reviewed prop continuity and reverse source audit."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import SKILL, fixture, save, ledger, design


def add_coverage(data):
    data["asset_policy"] = design.PROP_POLICY
    data["prop_coverage_review"] = {
        "source_digest": data["source_digest"], "reviewed_full_script": True,
        "reviewed_scene_ids": [scene["id"] for scene in data["scenes"]],
        "candidates": []}
    for item in data["props"]:
        data["prop_coverage_review"]["candidates"].append({
            "candidate_id": "PC" + item["id"][4:], "name": item["name"],
            "scene_ids": [scene["id"] for scene in data["scenes"] if item["id"] in scene["prop_ids"]],
            "source_evidence": [{key: value for key, value in use.items() if key != "event_id"}
                                for use in item["visible_uses"]],
            "disposition": "asset", "target_id": item["id"], "requires_reference": True,
            "reason": "原文明示拿取与收入怀中，需要固定令牌轮廓、孔位及握持比例"})
    return data


class PropCoveragePolicyTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "tests-prop-coverage"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)
        add_coverage(self.data)

    def tearDown(self):
        self.temp.cleanup()

    @property
    def review(self):
        return self.data["prop_coverage_review"]

    @property
    def candidate(self):
        return self.review["candidates"][0]

    def validate(self):
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, self.data)

    def core_one_scene(self):
        self.paragraphs = self.paragraphs[:1]
        self.data["scenes"] = self.data["scenes"][:1]
        self.data["source_digest"] = ledger.digest(self.paragraphs)
        prop = self.data["props"][0]
        prop["visible_uses"] = prop["visible_uses"][:1]
        prop["extraction_review"].update(
            kind="core_continuity", story_role="取出令牌放上桌是本场交接动作主体",
            consistency_reason="交接动作的建立镜头与手部特写需保持令牌孔位、轮廓和握持比例一致")
        save(self.script, "场景1-日-内-陈府\n人物：陈默-基础形象；\n△ " + self.paragraphs[0] + "\n")
        save(self.source, {"paragraphs": [{"id": 1, "text": self.paragraphs[0]}]})
        add_coverage(self.data)

    def test_reusable_path_remains_valid_under_v2(self):
        self.validate()

    def test_single_scene_core_prop_has_no_vehicle_name_exception(self):
        self.core_one_scene()
        self.validate()
        result = design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")
        self.assertIn("青铜令牌", result["props"][0]["image_prompt"])

    def test_single_scene_reusable_still_needs_two_uses(self):
        self.core_one_scene()
        self.data["props"][0]["extraction_review"]["kind"] = "special_reusable"
        with self.assertRaisesRegex(ValueError, "至少需要2次"):
            self.validate()

    def test_core_requires_story_role_and_specific_continuity_review(self):
        self.core_one_scene()
        for field in ("story_role", "consistency_reason"):
            with self.subTest(field=field):
                value = self.data["props"][0]["extraction_review"].pop(field)
                with self.assertRaises(ValueError):
                    self.validate()
                self.data["props"][0]["extraction_review"][field] = value

    def test_core_requires_at_least_one_located_visible_use(self):
        self.core_one_scene()
        self.data["props"][0]["visible_uses"] = []
        with self.assertRaisesRegex(ValueError, "至少需要1次"):
            self.validate()

    def test_core_is_not_silently_enabled_on_v1(self):
        self.core_one_scene()
        self.data["asset_policy"] = design.LEGACY_PROP_POLICY
        with self.assertRaisesRegex(ValueError, "特殊主要道具"):
            self.validate()

    def test_missing_or_incomplete_coverage_is_rejected(self):
        for replacement in (None, {}, dict(self.review, reviewed_full_script=False)):
            with self.subTest(review=replacement):
                original = self.data["prop_coverage_review"]
                self.data["prop_coverage_review"] = replacement
                with self.assertRaisesRegex(ValueError, "反向覆盖审阅未完成"):
                    self.validate()
                self.data["prop_coverage_review"] = original

    def test_coverage_must_match_current_source(self):
        self.review["source_digest"] = "stale"
        with self.assertRaisesRegex(ValueError, "原文版本不匹配"):
            self.validate()

    def test_all_scenes_must_be_reviewed_once(self):
        for scenes in (["SC0001"], ["SC0001", "SC0001", "SC0002"], ["SC0001", "SC9999"]):
            with self.subTest(scenes=scenes):
                self.review["reviewed_scene_ids"] = scenes
                with self.assertRaisesRegex(ValueError, "全部场次"):
                    self.validate()

    def test_every_prop_requires_reverse_asset_mapping(self):
        self.review["candidates"] = []
        with self.assertRaisesRegex(ValueError, "全部关联场次"):
            self.validate()

    def test_reverse_mapping_must_cover_all_prop_scenes(self):
        self.candidate["scene_ids"] = ["SC0001"]
        self.candidate["source_evidence"] = self.candidate["source_evidence"][:1]
        with self.assertRaisesRegex(ValueError, "全部关联场次"):
            self.validate()

    def test_candidate_ids_are_unique(self):
        self.review["candidates"].append(copy.deepcopy(self.candidate))
        with self.assertRaisesRegex(ValueError, "重复道具候选ID"):
            self.validate()

    def test_reference_requirement_cannot_be_downgraded_to_prompt_only(self):
        self.candidate.update(disposition="prompt_only", target_id=None)
        with self.assertRaisesRegex(ValueError, "不能归入prompt_only"):
            self.validate()

    def test_image_asset_requires_reference_true(self):
        self.candidate["requires_reference"] = False
        with self.assertRaisesRegex(ValueError, "独立道具资产"):
            self.validate()

    def test_target_type_and_scene_link_are_checked(self):
        self.candidate["target_id"] = "CHAR0001"
        with self.assertRaisesRegex(ValueError, "类型错误"):
            self.validate()
        self.candidate.update(disposition="set", target_id="SET0001")
        self.data["scene_assets"].append(dict(self.data["scene_assets"][0], id="SET9999"))
        self.candidate["target_id"] = "SET9999"
        with self.assertRaisesRegex(ValueError, "未关联候选场次"):
            design.validate_prop_coverage(self.data)

    def test_evidence_is_checked_against_original_and_every_scene(self):
        self.candidate["source_evidence"][1]["evidence"] = "从未发生的令牌交接"
        with self.assertRaisesRegex(ValueError, "未出现在"):
            self.validate()

    def test_evidence_missing_scene_or_wrong_paragraph_fails(self):
        evidence = self.candidate["source_evidence"].pop()
        with self.assertRaisesRegex(ValueError, "未覆盖全部候选场次"):
            self.validate()
        self.candidate["source_evidence"].append(evidence)
        evidence["source_paragraphs"] = [1]
        with self.assertRaisesRegex(ValueError, "对应场次"):
            self.validate()

    def test_empty_props_still_require_completed_reverse_audit(self):
        self.data["props"] = []
        for scene in self.data["scenes"]:
            scene["prop_ids"] = []
        self.data.pop("prop_coverage_review")
        with self.assertRaisesRegex(ValueError, "空props也不能跳过"):
            self.validate()
        add_coverage(self.data)
        self.validate()  # An explicit no-candidate review is valid; no automatic NLP claim.

    def test_character_set_and_prompt_only_are_explicit_accounted_dispositions(self):
        for disposition, target, quote in (("character", "CHAR0001", "陈默穿灰色布衣"),
                                           ("set", "SET0001", "放到桌上"),
                                           ("prompt_only", None, "收入怀中")):
            sid = "SC0002" if disposition == "prompt_only" else "SC0001"
            self.review["candidates"].append({
                "candidate_id": "PC-" + disposition, "name": disposition,
                "scene_ids": [sid], "source_evidence": [{"scene_id": sid, "evidence": quote,
                    "source_paragraphs": [2 if sid == "SC0002" else 1]}],
                "disposition": disposition, "target_id": target, "reason": "已按原文核查对应目标及用途",
                "requires_reference": target is not None})
        self.validate()

    def test_init_writes_unreviewed_v2_draft_and_render_design_are_gated(self):
        out = self.work / "draft.json"
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.initialize(self.script, self.work, out)
        initialized = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(initialized["asset_policy"], "visual-continuity-v2")
        self.assertIs(initialized["prop_coverage_review"]["reviewed_full_script"], False)
        self.review["reviewed_full_script"] = False
        save(self.work / "ledger.json", self.data)
        with self.assertRaisesRegex(ValueError, "反向覆盖审阅未完成"):
            ledger.render(self.script, self.source, self.work / "ledger.json", self.work / "assets.txt")
        with self.assertRaisesRegex(ValueError, "反向覆盖审阅未完成"):
            design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")
        self.assertFalse((self.work / "assets.txt").exists())

    def test_changed_coverage_decision_invalidates_compiled_design(self):
        value = design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")
        value["prop_coverage_review"]["candidates"][0]["reason"] = "重审新的镜头结构需求，尚未重新编译"
        with self.assertRaisesRegex(ValueError, "出处或场次映射已改变"):
            design.validate_visual_design(value)


if __name__ == "__main__":
    unittest.main()
