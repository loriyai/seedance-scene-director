"""Regressions for product-image directions leaking into wearable boards."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_asset_visual_design import fixture, design
import test_group_mask_design as group_fixtures
import design_preview
import design_scope


class DesignScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(Path(self.temp.name))

    def masked(self):
        return group_fixtures.GroupMaskDesignTests.masked(self)

    def mask(self, two=False):
        return group_fixtures.GroupMaskDesignTests.linked_mask(self, two)

    def compile(self):
        return design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")

    def test_exact_original_leak_fails_at_draft_before_any_other_design_exists(self):
        _, _, _, prop = self.mask()
        prop["scale"] = "按成年人头围适配；本图只展示一个同款外形样本，剧情三件各自独立，不画三件组合、不带人头或手"
        draft = {"designs": [prop]}
        before = copy.deepcopy(draft)
        with self.assertRaisesRegex(ValueError, "PROP0001.scale混入图片构图"):
            design_scope.check_payload(draft)
        self.assertEqual(draft, before)

    def test_preview_rejects_inherited_prop_leak_without_trimming_ledger(self):
        _, _, _, prop = self.mask()
        prop["scale"] += "，不带人头或手"
        payload = copy.deepcopy(self.payload)
        payload["designs"] = [r for r in payload["designs"] if r["asset_id"] != "SET0001"]
        with self.assertRaisesRegex(ValueError, "PROP0001.scale混入图片构图"):
            design_preview.preview_design(self.data, payload, "写实", ["CHAR0002"])
        with self.assertRaisesRegex(ValueError, "PROP0001.scale混入图片构图"):
            self.compile()

    def test_typed_product_view_never_inherited_by_two_wearers(self):
        _, _, _, prop = self.mask(two=True)
        prop["image_presentation"] = {"view": "front_left_three_quarter"}
        prop["basis"] += "；本图只展示一个外形样本，剧情各件独立"
        result = self.compile()
        product = result["props"][0]
        self.assertIn("左前方微俯三分之四视角", product["image_prompt"])
        self.assertIn("无人物、手部", product["image_prompt"])
        self.assertEqual(product["image_format"]["view"], "front_left_three_quarter")
        for char in result["characters"]:
            if char.get("appearance_variant"):
                prompt = char["image_prompt"]
                self.assertIn("正面全身", prompt)
                self.assertNotIn(prop["structure"], prompt)
                self.assertIn("@猪猪头套 ", prompt)
                self.assertNotIn("左前方微俯三分之四视角", prompt)
                self.assertNotIn("无人物、手部", prompt)
                self.assertNotIn("只展示一个外形样本", prompt)
        design.validate_visual_design(result)

    def test_physical_head_hand_and_car_seating_are_legal(self):
        _, _, _, prop = self.mask()
        prop["structure"] = "头部下方有穿戴开口，双手可拉开侧边系带"
        prop["scale"] = "按成年人头围适配，下缘能容头部进出，车内仍能自然转头"
        design_scope.check_payload({"designs": [prop]})
        prop["scale"] = "能挤坐四名成年人的微型四座车，后排长腿会顶前排靠背"
        design_scope.check_payload({"designs": [prop]})
        prop["structure"] = "相机有长焦镜头，侧面有一块显示屏"
        design_scope.check_payload({"designs": [prop]})
        prop["scale"] += "；整车以左前方三分之四角度完整展示"
        with self.assertRaisesRegex(ValueError, "scale混入图片构图"):
            design_scope.check_payload({"designs": [prop]})

    def test_backend_relations_are_kept_but_visual_fields_must_be_self_contained(self):
        _, _, _, prop = self.mask()
        prop["materials_palette"] = "与SET0002一致的粉色塑料"
        with self.assertRaisesRegex(ValueError, "含后台编号SET0002"):
            self.compile()
        prop["materials_palette"] = "粉色塑料"
        result = self.compile()
        self.assertEqual(result["characters"][1]["appearance_variant"]["prop_asset_id"], "PROP0001")
        self.assertNotIn("PROP0001", result["characters"][1]["image_prompt"])

    def test_presentation_is_typed_and_cannot_override_character_layout(self):
        for value in ({"view": "anything"}, {"view": "front", "negative": "不画人头"}):
            self.payload["designs"][0]["image_presentation"] = value
            with self.assertRaisesRegex(ValueError, "image_presentation"):
                self.compile()

    def test_original_source_facts_remain_verbatim(self):
        self.mask()
        fact = {"text": "三人戴上猪猪头套，只露出眼眸", "source_paragraphs": [1],
                "evidence": "三人戴上猪猪头套，只露出眼眸", "scene_ids": ["SC0001"], "attribution": "叙述"}
        self.data["props"][0]["visual_facts"] = [fact]
        result = self.compile()
        self.assertEqual(result["props"][0]["visual_facts"], [fact])
        self.assertNotIn(fact["text"], result["characters"][1]["image_prompt"])
        self.assertIn(fact, design.asset_facts(result, "characters", result["characters"][1]))

    def test_reading_historical_mixed_design_never_rewrites_or_migrates_it(self):
        _, _, _, prop = self.mask()
        prop["scale"] += "；本图不带人物"
        # Reproduce a ledger accepted by the pre-lint v2 writer.
        with patch.object(design_scope, "check_context"):
            historical = self.compile()
        before = copy.deepcopy(historical)
        design.validate_visual_design(historical)
        self.assertEqual(historical, before)
        with self.assertRaisesRegex(ValueError, "混入图片构图"):
            design.apply_design(historical, self.payload, "3D国漫写实厚涂风格")

    def test_prop_change_stales_all_wearers_and_new_compile_propagates(self):
        _, _, _, prop = self.mask(two=True)
        before = self.compile()
        changed = copy.deepcopy(before)
        changed["props"][0]["visual_design"]["materials_palette"] = "粉色短绒"
        with self.assertRaisesRegex(ValueError, "已改变"):
            design.validate_visual_design(changed)
        prop["materials_palette"] = "粉色短绒"
        after = self.compile()
        for old, new in zip(before["characters"], after["characters"]):
            if new.get("appearance_variant"):
                self.assertNotEqual(old["image_design_digest"], new["image_design_digest"])
                self.assertNotIn("粉色短绒", new["image_prompt"])
                self.assertEqual(old["image_prompt"], new["image_prompt"])
        self.assertIn("粉色短绒", after["props"][0]["image_prompt"])


if __name__ == "__main__":
    unittest.main()
