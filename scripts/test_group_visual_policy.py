#!/usr/bin/env python3
"""Group cast preservation, reviewed reuse thresholds, and visual-only delivery."""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import SKILL, fixture, save, ledger, migration, design


class GroupVisualPolicyTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "tests-group-policy"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)

    def tearDown(self):
        self.temp.cleanup()

    def add_group(self, mode="prompt_only", occurrences=(1,)):
        uses = []
        drafts = []
        for number, scene in enumerate(self.data["scenes"], 1):
            if number in occurrences:
                evidence = "村民站在门边围观。" if number == 1 else "村民再次来到门边。"
                self.paragraphs[number - 1] += evidence
                scene["character_ids"].insert(0, "CHAR0002")
                uses.append({"scene_id": scene["id"], "event_id": "门边围观" if number == 1 else "再次到来",
                             "evidence": evidence, "source_paragraphs": [number]})
            cast = ("村民-群像-基础形象；" if number in occurrences else "") + "陈默-基础形象；"
            text = scene["heading"] + "\n人物：" + cast + "\n△ " + self.paragraphs[number - 1] + "\n"
            drafts.append(text)
            save(self.work / f"scenes/scene_{number:03d}_{number:04d}-{number:04d}.txt", text)
        save(self.script, "\n".join(drafts))
        save(self.source, {"paragraphs": [{"id": n, "text": p} for n, p in enumerate(self.paragraphs, 1)]})
        self.data["source_digest"] = ledger.digest(self.paragraphs)
        self.data["group_policy"] = design.LEGACY_GROUP_POLICY
        group = {"id": "CHAR0002", "name": "村民-群像-基础形象-陈府", "source_entry": "村民-群像-基础形象",
                 "aliases": [], "visual_facts": [], "personality_facts": [], "visual_mode": mode,
                 "extraction_review": {"kind": "background_group", "reviewed_full_script": True,
                                       "source_digest": self.data["source_digest"],
                                       "identity_basis": "原文明示村民群体，无姓名及独立台词",
                                       "consistency_reason": "两次出场的村民衣着与整体形象需一致",
                                       "exclusion_reason": "无需固定脸部身份，在提示词写明背景村民及衣着即可"},
                 "visible_uses": uses}
        self.data["characters"].insert(0, group)
        if mode == "image_asset":
            self.payload["identity_anchors"].append(dict(self.payload["identity_anchors"][0], id="村民群像"))
            self.payload["designs"].append(dict(self.payload["designs"][0], asset_id="CHAR0002", identity_anchor_id="村民群像"))
        return group

    def validate(self, data=None):
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, data or self.data)

    def test_single_group_retains_cast_but_has_no_image_delivery_or_number_gap(self):
        group = self.add_group()
        self.validate()
        result = design.apply_design(self.data, self.payload, "古装写实")
        self.validate(result)
        self.assertEqual(result["scenes"][0]["character_ids"], ["CHAR0002", "CHAR0001"])
        self.assertNotIn("image_prompt", result["characters"][0])
        self.assertEqual([c["id"] for c in design.visual_assets(result, "characters")], ["CHAR0001"])
        path, output = self.work / "designed.json", self.work / "catalog.txt"
        save(path, result)
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.render(self.script, self.source, path, output)
        text = output.read_text(encoding="utf-8")
        self.assertNotIn(group["name"], text)
        self.assertIn("角色01 - 陈默", text)
        self.assertNotIn("角色02", text)
        self.assertIn("场景01 - 陈府", text)
        self.assertIn("道具01 - 青铜令牌", text)

    def test_two_independent_group_occurrences_allow_image_asset(self):
        self.add_group("image_asset", (1, 2))
        result = design.apply_design(self.data, self.payload, "古装写实")
        self.validate(result)
        self.assertIn("image_prompt", result["characters"][0])

    def test_group_one_occurrence_cannot_be_promoted_by_repetition_or_renamed_event(self):
        group = self.add_group("image_asset")
        with self.assertRaisesRegex(ValueError, "至少需要2次独立可见出场"):
            self.validate()
        group["visible_uses"].append(dict(group["visible_uses"][0], event_id="第二镜另称"))
        with self.assertRaisesRegex(ValueError, "证据重叠"):
            self.validate()

    def test_group_duplicate_event_foreign_evidence_and_missing_reason_fail(self):
        group = self.add_group("image_asset", (1, 2))
        original = copy.deepcopy(group["visible_uses"])
        group["visible_uses"][1] = copy.deepcopy(group["visible_uses"][0])
        with self.assertRaisesRegex(ValueError, "同一独立事件重复"):
            self.validate()
        group["visible_uses"] = copy.deepcopy(original)
        group["visible_uses"][1]["source_paragraphs"] = [1]
        with self.assertRaisesRegex(ValueError, "没有原文证据"):
            self.validate()
        group["visible_uses"] = original
        group["extraction_review"].pop("consistency_reason")
        with self.assertRaisesRegex(ValueError, "一致性理由"):
            self.validate()

    def test_repeated_group_without_consistency_need_can_remain_prompt_only(self):
        group = self.add_group("prompt_only", (1, 2))
        group["extraction_review"].pop("consistency_reason")
        self.validate()
        group["extraction_review"].pop("exclusion_reason")
        with self.assertRaisesRegex(ValueError, "不提取图片"):
            self.validate()

    def test_named_character_cannot_be_silently_demoted(self):
        self.data["characters"][0]["visual_mode"] = "prompt_only"
        with self.assertRaisesRegex(ValueError, "仅无姓名"):
            self.validate()
        with self.assertRaisesRegex(ValueError, "仅无姓名"):
            design.apply_design(self.data, self.payload, "古装写实")

    def test_group_with_independent_speech_is_not_eligible(self):
        self.add_group()
        save(self.script, self.script.read_text(encoding="utf-8") + "村民-群像OS：这里有人。\n")
        with self.assertRaisesRegex(ValueError, "具有独立台词"):
            self.validate()

    def test_review_must_cover_current_full_source_and_cannot_skip_decision(self):
        group = self.add_group()
        group["extraction_review"]["source_digest"] = "stale"
        with self.assertRaisesRegex(ValueError, "原文版本"):
            self.validate()
        group.pop("extraction_review")
        with self.assertRaisesRegex(ValueError, "全剧背景身份"):
            self.validate()
        group.pop("visual_mode")
        with self.assertRaisesRegex(ValueError, "明确visual_mode"):
            self.validate()

    def test_legacy_group_without_policy_is_still_visual(self):
        group = self.add_group("image_asset")
        self.data.pop("group_policy")
        for field in ("visual_mode", "extraction_review", "visible_uses"):
            group.pop(field)
        result = design.apply_design(self.data, self.payload, "古装写实")
        self.validate(result)
        self.assertIn("image_prompt", result["characters"][0])

    def test_review_change_invalidates_group_design_digest(self):
        self.add_group("image_asset", (1, 2))
        result = design.apply_design(self.data, self.payload, "古装写实")
        result["characters"][0]["extraction_review"]["consistency_reason"] = "补充新的一致性理由"
        with self.assertRaisesRegex(ValueError, "出处或场次映射"):
            self.validate(result)

    def test_explicit_redesign_removes_old_group_derivatives_only_after_review(self):
        self.add_group("image_asset", (1, 2))
        result = design.apply_design(self.data, self.payload, "古装写实")
        result["characters"][0]["visual_mode"] = "prompt_only"
        with self.assertRaisesRegex(ValueError, "不能保留生图派生字段"):
            self.validate(result)
        payload = copy.deepcopy(self.payload)
        payload["designs"] = [d for d in payload["designs"] if d["asset_id"] != "CHAR0002"]
        revised = design.apply_design(result, payload, "古装写实")
        self.validate(revised)
        self.assertNotIn("image_prompt", revised["characters"][0])

    def test_normalize_review_cli_and_unchanged_migration_preserve_prompt_only(self):
        group = self.add_group()
        save(self.work / "ledger.json", self.data)
        save(self.work / "groups.json", {"groups": [{"character_id": group["id"], **{
            key: group[key] for key in ("visual_mode", "extraction_review", "visible_uses")}}]})
        out = self.work / "normalized.json"
        command = [sys.executable, str(SKILL / "modules/screenplay-standardizer/scripts/asset_ledger.py"),
                   "normalize", str(self.work / "ledger.json"), "--group-review", str(self.work / "groups.json"),
                   "--source", str(self.source), "--output", str(out), "--report", str(self.work / "report.json")]
        run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
        self.assertEqual(run.returncode, 0, run.stderr)
        result, report = migration.migrate(out, self.source, self.script, self.work, {})
        self.assertEqual(result["group_policy"], design.LEGACY_GROUP_POLICY)
        preserved = next(c for c in result["characters"] if c["id"] == group["id"])
        self.assertEqual(preserved["visual_mode"], "prompt_only")
        self.assertEqual(preserved["visible_uses"], group["visible_uses"])
        self.assertFalse(report["pending_reviews"])
        self.validate(result)

    def test_group_review_rejects_unknown_or_missing_identity(self):
        group = self.add_group()
        with self.assertRaisesRegex(ValueError, "覆盖normalize后全部"):
            design.apply_group_review(self.data, {"groups": []})
        with self.assertRaisesRegex(ValueError, "非群像"):
            design.apply_group_review(self.data, {"groups": [{"character_id": "CHAR0001", **{
                key: group[key] for key in ("visual_mode", "extraction_review", "visible_uses")}}]})

    def test_normalize_merges_evidence_then_requires_final_group_review(self):
        group = self.add_group("image_asset", (1, 2))
        second = copy.deepcopy(group)
        second["id"] = "CHAR0003"
        group["visible_uses"] = [group["visible_uses"][0]]
        second["visible_uses"] = [second["visible_uses"][1]]
        self.data["characters"].append(second)
        self.data["scenes"][1]["character_ids"][0] = "CHAR0003"
        normalized, report = ledger.normalize_assets(self.data)
        self.assertEqual(report["redirects"], {"CHAR0003": "CHAR0002"})
        merged = normalized["characters"][0]
        self.assertEqual(len(merged["visible_uses"]), 2)
        self.assertNotIn("extraction_review", merged)
        with self.assertRaisesRegex(ValueError, "全剧背景身份"):
            design.validate_group_modes(normalized)
        payload = {"groups": [{"character_id": "CHAR0002", "visual_mode": "image_asset",
                               "extraction_review": group["extraction_review"], "visible_uses": merged["visible_uses"]}]}
        reviewed = design.apply_group_review(normalized, payload)
        self.validate(reviewed)

    def test_source_edit_migration_requires_group_review_without_changing_mode(self):
        group = self.add_group()
        old_ledger, old_source = self.work / "old-ledger.json", self.work / "old-source.json"
        save(old_ledger, self.data)
        save(old_source, self.source.read_text(encoding="utf-8"))
        old_paragraph = self.paragraphs[1]
        self.paragraphs[1] += "陈默随后离开。"
        save(self.source, {"paragraphs": [{"id": n, "text": p} for n, p in enumerate(self.paragraphs, 1)]})
        save(self.script, self.script.read_text(encoding="utf-8").replace(old_paragraph, self.paragraphs[1]))
        scene = self.work / "scenes/scene_002_0002-0002.txt"
        save(scene, scene.read_text(encoding="utf-8").replace(old_paragraph, self.paragraphs[1]))
        result, report = migration.migrate(old_ledger, old_source, self.script, self.work,
                                           {"scenes": {"SC0001": [1], "SC0002": [2]}})
        revised = next(c for c in result["characters"] if c["id"] == group["id"])
        self.assertEqual(revised["visual_mode"], "prompt_only")
        self.assertEqual(revised["extraction_review"]["source_digest"], self.data["source_digest"])
        self.assertTrue(any(p["kind"] == "群像全剧筛选待复核" for p in report["pending_reviews"]))

    def test_new_init_marks_group_review_required_without_marking_named_characters(self):
        self.add_group()
        out = self.work / "initialized.json"
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.initialize(self.script, self.work, out)
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["group_policy"], design.GROUP_POLICY)
        group = next(c for c in result["characters"] if "-群像-" in c["source_entry"])
        named = next(c for c in result["characters"] if "-群像-" not in c["source_entry"])
        self.assertEqual(group["visual_mode"], "prompt_only")
        self.assertNotIn("visual_mode", named)
        with self.assertRaisesRegex(ValueError, "全剧背景身份"):
            design.validate_group_modes(result)


if __name__ == "__main__":
    unittest.main()
