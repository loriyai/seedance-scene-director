"""Partial previews must retain complete source validation and frozen dependencies."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import SKILL, fixture, save, design
import test_group_mask_design as group_fixture
import design_preview as preview


class DesignPreviewTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "design-preview-test-work"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)
        self.style = (self.work / "video-style.txt").read_text(encoding="utf-8").strip()

    def subset(self, *ids):
        result = copy.deepcopy(self.payload)
        result["designs"] = [record for record in result["designs"] if record["asset_id"] in ids]
        return result

    def files(self, ids, expected=None):
        return preview.preview_from_files(self.script, self.source, self.work / "ledger.json",
                                          self.work / "design.json", self.work / "video-style.txt", ids, expected)

    def test_first_partial_preview_matches_final_prompt_without_input_mutation(self):
        payload = self.subset("CHAR0001")
        save(self.work / "design.json", payload)
        before = {p: p.read_bytes() for p in self.work.rglob("*") if p.is_file()}
        report = self.files(["CHAR0001"])
        full = design.apply_design(self.data, self.payload, self.style)
        self.assertEqual(report["previews"][0]["image_prompt"], full["characters"][0]["image_prompt"])
        self.assertEqual(report["requested_asset_ids"], ["CHAR0001"])
        self.assertEqual(report["status"], "partial_preview")
        self.assertFalse(report["formal_ready"])
        self.assertFalse(report["bindable"])
        self.assertEqual(report["semantic_review"], "not_performed")
        self.assertNotIn("characters", report)
        self.assertEqual(before, {p: p.read_bytes() for p in self.work.rglob("*") if p.is_file()})
        self.assertEqual(report, self.files(["CHAR0001"], report["input_sha256"]))
        with self.assertRaisesRegex(ValueError, "覆盖全部"):
            design.apply_design(self.data, payload, self.style)

    def test_entire_ledger_is_validated_even_for_unrelated_character_preview(self):
        self.data["props"][0]["visible_uses"].pop()
        save(self.work / "ledger.json", self.data)
        save(self.work / "design.json", self.subset("CHAR0001"))
        with self.assertRaisesRegex(ValueError, "至少需要2次"):
            self.files(["CHAR0001"])

    def test_hash_drift_is_rejected_and_scope_changes_have_different_bindings(self):
        save(self.work / "design.json", self.subset("CHAR0001"))
        report = self.files(["CHAR0001"])
        save(self.work / "video-style.txt", "另一种风格")
        with self.assertRaisesRegex(ValueError, "冻结输入已变化"):
            self.files(["CHAR0001"], report["input_sha256"])
        with self.assertRaisesRegex(ValueError, "精确包含"):
            self.files(["CHAR0001"], {"ledger": report["input_sha256"]["ledger"]})
        designed = design.apply_design(self.data, self.payload, self.style)
        one = preview.preview_design(designed, {"designs": []}, self.style, ["CHAR0001"])
        two = preview.preview_design(designed, {"designs": []}, self.style, ["PROP0001"])
        self.assertNotEqual(one["binding_sha256"], two["binding_sha256"])

    def test_duplicate_unknown_and_out_of_scope_ids_are_rejected(self):
        payload = self.subset("CHAR0001")
        with self.assertRaisesRegex(ValueError, "范围重复"):
            preview.preview_design(self.data, payload, self.style, ["CHAR0001", "CHAR0001"])
        with self.assertRaisesRegex(ValueError, "不是图片资产"):
            preview.preview_design(self.data, payload, self.style, ["CHAR9999"])
        payload["designs"].append(copy.deepcopy(payload["designs"][0]))
        with self.assertRaisesRegex(ValueError, "重复资产设计"):
            preview.preview_design(self.data, payload, self.style, ["CHAR0001"])
        with self.assertRaisesRegex(ValueError, "超出预览范围"):
            preview.preview_design(self.data, self.payload, self.style, ["CHAR0001"])
        self.data["characters"].append(copy.deepcopy(self.data["characters"][0]))
        with self.assertRaisesRegex(ValueError, "重复资产ID"):
            preview.preview_design(self.data, self.subset("CHAR0001"), self.style, ["CHAR0001"])

    def linked_mask(self):
        return group_fixture.GroupMaskDesignTests.linked_mask(self)

    def masked(self):
        return group_fixture.GroupMaskDesignTests.masked(self)

    def test_mask_closure_requires_base_and_prop_and_matches_final_compile(self):
        self.linked_mask()
        with self.assertRaisesRegex(ValueError, "旧设计版本无效|缺少设计依赖"):
            preview.preview_design(self.data, self.subset("CHAR0002"), self.style, ["CHAR0002"])
        payload = self.subset("CHAR0001", "CHAR0002", "PROP0001")
        data_before, payload_before = copy.deepcopy(self.data), copy.deepcopy(payload)
        report = preview.preview_design(self.data, payload, self.style, ["CHAR0002"])
        self.assertEqual(report["dependency_asset_ids"], ["CHAR0001", "CHAR0002", "PROP0001"])
        full = design.apply_design(self.data, self.payload, self.style)
        prompts = {item["id"]: item["image_prompt"] for category in design.ASSET_CATEGORIES for item in full[category]}
        for item in report["previews"]:
            self.assertEqual(item["image_prompt"], prompts[item["asset_id"]])
        self.assertEqual(data_before, self.data)
        self.assertEqual(payload_before, payload)

    def test_existing_dependencies_must_be_valid_before_and_after_new_designs(self):
        self.linked_mask()
        complete = design.apply_design(self.data, self.payload, self.style)
        report = preview.preview_design(complete, {"designs": []}, self.style, ["CHAR0002"])
        self.assertTrue(all(item["design_origin"] == "reused_verified" for item in report["previews"]))
        changed_prop = self.subset("PROP0001")
        changed_prop["designs"][0]["materials_palette"] = "深红色织物"
        with self.assertRaisesRegex(ValueError, "过期source_context_digest"):
            preview.preview_design(complete, changed_prop, self.style, ["CHAR0002"])
        new_mask_and_prop = self.subset("CHAR0002", "PROP0001")
        next(record for record in new_mask_and_prop["designs"] if record["asset_id"] == "PROP0001")["materials_palette"] = "深红色织物"
        revised = preview.preview_design(complete, new_mask_and_prop, self.style, ["CHAR0002"])
        masked = next(record for record in revised["previews"] if record["asset_id"] == "CHAR0002")
        self.assertNotIn("深红色织物", masked["image_prompt"])
        self.assertIn("@猪猪头套 ", masked["image_prompt"])
        self.assertIn("深红色织物", next(row for row in revised["previews"] if row["asset_id"] == "PROP0001")["image_prompt"])

    def test_stale_source_facts_style_anchor_and_prompt_cannot_be_reused(self):
        complete = design.apply_design(self.data, self.payload, self.style)
        changed = copy.deepcopy(complete)
        changed["source_digest"] = "new-source"
        with self.assertRaisesRegex(ValueError, "来源或依赖过期"):
            preview.preview_design(changed, {"designs": []}, self.style, ["CHAR0001"])
        changed = copy.deepcopy(complete)
        changed["characters"][0]["visual_facts"][0]["text"] = "蓝色布衣"
        with self.assertRaisesRegex(ValueError, "事实过期"):
            preview.preview_design(changed, {"designs": []}, self.style, ["CHAR0001"])
        with self.assertRaisesRegex(ValueError, "风格不同"):
            preview.preview_design(complete, {"designs": []}, "新风格", ["CHAR0001"])
        changed = copy.deepcopy(complete)
        changed["characters"][0]["image_prompt"] += "隐藏修改"
        with self.assertRaisesRegex(ValueError, "旧提示词"):
            preview.preview_design(changed, {"designs": []}, self.style, ["CHAR0001"])
        anchor = copy.deepcopy(self.payload["identity_anchors"][0])
        anchor["face"] = "另一张脸"
        with self.assertRaisesRegex(ValueError, "新共享依赖影响"):
            preview.preview_design(complete, {"designs": [], "identity_anchors": [anchor]}, self.style, ["CHAR0001"])

    def test_same_identity_group_requires_complete_dependencies_and_consistent_anchor(self):
        first = self.data["characters"][0]
        first["appearance"] = {"identity": "陈默", "incarnation": "第一世"}
        other = copy.deepcopy(first)
        other.update(id="CHAR0002", name="陈默-第二套衣服")
        self.data["characters"].append(other)
        payload = self.subset("CHAR0001")
        with self.assertRaisesRegex(ValueError, "旧设计版本无效|缺少设计依赖"):
            preview.preview_design(self.data, payload, self.style, ["CHAR0001"])
        second = dict(payload["designs"][0], asset_id="CHAR0002", identity_anchor_id="other")
        payload["designs"].append(second)
        payload["identity_anchors"].append(dict(payload["identity_anchors"][0], id="other"))
        with self.assertRaisesRegex(ValueError, "同身份形象使用不同锚点"):
            preview.preview_design(self.data, payload, self.style, ["CHAR0001"])

    def test_set_preview_keeps_location_facts_and_parent_chain(self):
        base = self.data["scene_assets"][0]
        child = dict(copy.deepcopy(base), id="SET0002", name="陈府-婚礼", variant="decor", parent_id="SET0001")
        self.data["scene_assets"].append(child)
        payload = self.subset("SET0001")
        payload["designs"].append(dict(payload["designs"][0], asset_id="SET0002", furnishings="原长桌位置保留，墙侧增加红绸"))
        report = preview.preview_design(self.data, payload, self.style, ["SET0002"])
        self.assertEqual(report["dependency_asset_ids"], ["LOC0001", "SET0001", "SET0002"])
        self.assertEqual(report["preview_count"], 2)
        child["parent_id"] = "SET9999"
        with self.assertRaisesRegex(ValueError, "缺少SET父资产"):
            preview.preview_design(self.data, payload, self.style, ["SET0002"])
        child["parent_id"] = "SET0002"
        with self.assertRaisesRegex(ValueError, "父链存在循环"):
            preview.preview_design(self.data, payload, self.style, ["SET0002"])

    def test_legacy_prompt_bytes_remain_unchanged(self):
        self.payload["image_design_version"] = design.LEGACY_DESIGN_VERSION
        complete = design.apply_design(self.data, self.payload, "古装写实")
        expected = "ce6b25ae354365edacc1db422ed4568c739b2c66a1288497098ab4a7ca522f4b"
        report = preview.preview_design(complete, {"designs": []}, "古装写实", ["CHAR0001"])
        item = report["previews"][0]
        value = item["image_prompt"] + "\n" + json.dumps(item["image_format"], ensure_ascii=False, sort_keys=True)
        self.assertEqual(hashlib.sha256(value.encode()).hexdigest(), expected)
        self.assertEqual(complete, design.apply_design(complete, self.payload, "古装写实"))


if __name__ == "__main__":
    unittest.main()
