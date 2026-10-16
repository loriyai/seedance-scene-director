"""Observable v2 group/mask boards and byte-stable legacy image compilation."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import fixture, design
import test_group_visual_policy as legacy


class GroupMaskDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)

    def group(self, mode="varied", visual_mode="image_asset"):
        item = legacy.GroupVisualPolicyTests.add_group(self, visual_mode, (1, 2))
        self.payload.pop("image_design_version")
        if visual_mode == "prompt_only":
            return item, None
        record = self.payload["designs"][-1]
        record.pop("identity_anchor_id")
        record.pop("styling")
        record["basis"] = "陈府门边村民，按古装乡居设计不同面貌，不新增职业或姓名"
        record["group_wardrobe"] = {
            "mode": mode, "era_region": "架空古代中原乡居的成年村民，限用素布服饰",
            "basis": "普通村民穿自家衣物，款式与配色各异",
        }
        record["group_members"] = [
            {"sample_id": "sample01", "age": "中年男性", "body": "中等身高，结实宽肩",
             "face": "宽脸，平直浓眉，较宽鼻翼", "hair": "黑发束髻",
             "styling": "深灰交领短褂，浅褐布裤，深灰布鞋"},
            {"sample_id": "sample02", "age": "成年女性", "body": "较矮身形，窄肩匀称",
             "face": "鹅蛋脸，眉尾柔和，小巧鼻形", "hair": "黑发低髻",
             "styling": "灰蓝右衽上衣，深青布裙，褐色布鞋"},
        ]
        if mode in ("uniform", "mixed"):
            record["group_wardrobe"].update(
                shared_style="青灰交领长袍，同制黑色布腰带与黑布鞋",
                basis="同门弟子共同服制；具体身份须由原文先行确认")
        if mode == "uniform":
            for member in record["group_members"]:
                member.pop("styling")
        return item, record

    def masked(self):
        base = self.data["characters"][0]
        item = {"id": "CHAR0002", "name": "陈默-第一世-戴猪猪头套",
                "source_entry": base["source_entry"], "visual_facts": [],
                "appearance_variant": {"base_asset_id": base["id"], "label": "戴猪猪头套",
                                       "kind": "headgear", "design_basis": "原文佩戴头套跨镜保持轮廓",
                                       "source_digest": self.data["source_digest"]}}
        self.data["characters"].append(item)
        record = {"asset_id": item["id"], "attribution": "制作设计",
                  "basis": "沿用灰衣陈默，仅增加已明示的头套佩戴状态", "reviewed_source_facts": True,
                  "covering": "粉色软质猪头头套包住整个头部，圆耳、凸起猪鼻，遮住脸和头发",
                  "visible_identity": "脸和头发均遮住，保留基础人物的修长结实体型与颈肩比例"}
        self.payload["designs"].append(record)
        return item, record

    def compile(self):
        return design.apply_design(self.data, self.payload, "古装写实")

    def linked_mask(self, two_wearers=False):
        item, record = self.masked()
        item["appearance_variant"]["prop_asset_id"] = "PROP0001"
        record["covering"] = {"fit": "头套下缘贴合颈根，正面朝前", "coverage": "完整遮住脸和头发"}
        prop = self.data["props"][0]
        prop.update(name="猪猪头套", source_name="猪猪头套", visual_facts=[])
        prop_design = next(value for value in self.payload["designs"] if value["asset_id"] == prop["id"])
        prop_design.update(structure="整体圆鼓，左右各一只圆耳，前侧凸起猪鼻，底部有佩戴开口",
                           materials_palette="蓝色短绒表层，内衬浅灰柔软织物", scale="可容纳成年人的头部",
                           state="完整且未变形，无额外标记", basis="作为可戴可摘道具，与人物佩戴状态共享外形")
        if two_wearers:
            base = copy.deepcopy(self.data["characters"][0])
            base.update(id="CHAR0003", name="林默-第一世-基础形象", source_entry="林默-基础形象")
            anchor = dict(self.payload["identity_anchors"][0], id="林默-第一世", body="身材高大、肩宽背厚")
            self.payload["identity_anchors"].append(anchor)
            base_design = dict(self.payload["designs"][0], asset_id=base["id"], identity_anchor_id=anchor["id"],
                               styling="深青布长衫，黑色布鞋")
            second = copy.deepcopy(item)
            second.update(id="CHAR0004", name="林默-第一世-戴猪猪头套", source_entry=base["source_entry"])
            second["appearance_variant"]["base_asset_id"] = base["id"]
            second_design = copy.deepcopy(record)
            second_design.update(asset_id=second["id"], visible_identity="脸与头发完全遮挡，保留高大宽肩体型")
            second_design["covering"]["fit"] = "头套下缘落在宽肩上方，正面朝前"
            self.data["characters"].extend((base, second))
            self.payload["designs"].extend((base_design, second_design))
        return item, record, prop, prop_design

    def test_v1_prompts_and_formats_remain_byte_equivalent(self):
        # Hashes captured from the unmodified v1 compiler before this change;
        # they protect replay, not a preferred new prompt phrasing.
        self.payload["image_design_version"] = design.LEGACY_DESIGN_VERSION
        expected = {
            "CHAR0001": "ce6b25ae354365edacc1db422ed4568c739b2c66a1288497098ab4a7ca522f4b",
            "SET0001": "f33be75cf349fc99464015fbeefed9afd2cee5e23b39740dc3683c8bb8b6d4ae",
            "PROP0001": "8124ace5af0f52e8d42e953f78e82f70ddbe5235104af9f0d1e04d7c6d627aa3",
        }
        result = self.compile()
        for category in design.ASSET_CATEGORIES:
            for item in result[category]:
                value = item["image_prompt"] + "\n" + json.dumps(item["image_format"], ensure_ascii=False, sort_keys=True)
                self.assertEqual(hashlib.sha256(value.encode()).hexdigest(), expected[item["id"]])
        self.payload.pop("image_design_version")
        replay = design.apply_design(result, self.payload, "古装写实")
        self.assertEqual(replay, result)

    def test_legacy_group_readback_does_not_silently_switch_layout(self):
        legacy.GroupVisualPolicyTests.add_group(self, "image_asset", (1, 2))
        result = self.compile()
        item = result["characters"][0]
        value = item["image_prompt"] + "\n" + json.dumps(item["image_format"], ensure_ascii=False, sort_keys=True)
        self.assertEqual(hashlib.sha256(value.encode()).hexdigest(),
                         "6b5504b3a71b50bd8c80d7a0eda8e9238d1f3b4a5bfc02f2d196352be16a2ad2")
        design.validate_visual_design(result)

    def test_new_group_board_uses_each_distinct_member_once_without_story_count(self):
        group, record = self.group()
        group["visual_facts"] = [{"text": "剧情中三十名村民", "source_paragraphs": [1],
                                  "evidence": "三十名村民", "scene_ids": ["SC0001"], "attribution": "叙述"}]
        result = self.compile()
        item = result["characters"][0]
        self.assertEqual(result["image_design_version"], design.DESIGN_VERSION)
        self.assertEqual(item["image_format"]["layout"], "distinct_members_single_front_full_body")
        self.assertEqual(item["image_format"]["sample_count"], 2)
        self.assertIs(item["image_format"]["sample_count_is_story_count"], False)
        prompt = item["image_prompt"]
        for member in record["group_members"]:
            self.assertEqual(prompt.count(member["face"]), 1)
            self.assertEqual(prompt.count(member["styling"]), 1)
        self.assertNotIn("无剧情动作、额外手持物、其他角色", prompt)
        self.assertNotIn("最左侧超大正面面部特写", prompt)
        self.assertEqual(self.compile(), result)

    def test_group_requires_real_multiple_designs_and_wardrobe_decision(self):
        _, record = self.group()
        members = copy.deepcopy(record["group_members"])
        cases = (
            ([members[0]], "至少2个"),
            ([members[0], dict(members[0], sample_id="other")], "外貌样本重复"),
            ([members[0], dict(members[1], styling=members[0]["styling"])], "各异服装"),
            ([members[0], dict(members[1], name="凭空新增人物")], "不新增姓名"),
        )
        for values, expected in cases:
            with self.subTest(expected=expected):
                record["group_members"] = values
                with self.assertRaisesRegex(ValueError, expected):
                    self.compile()
        record["group_members"] = members
        record["group_wardrobe"]["era_region"] = ""
        with self.assertRaisesRegex(ValueError, "era_region"):
            self.compile()

    def test_uniform_clothing_inherits_one_shared_outfit_and_rejects_conflict(self):
        _, record = self.group("uniform")
        item = self.compile()["characters"][0]
        self.assertEqual(item["image_format"]["wardrobe_mode"], "uniform")
        self.assertIn(record["group_wardrobe"]["shared_style"], item["image_prompt"])
        record["group_members"][1]["styling"] = "完全不同的红色现代西装"
        with self.assertRaisesRegex(ValueError, "统一服制"):
            self.compile()

    def test_mixed_wardrobe_preserves_explicit_member_outfits(self):
        _, record = self.group("mixed")
        item = self.compile()["characters"][0]
        self.assertEqual(item["image_format"]["wardrobe_mode"], "mixed")
        for member in record["group_members"]:
            self.assertIn(member["styling"], item["image_prompt"])

    def test_prompt_only_group_needs_no_design_and_stays_out_of_images(self):
        self.group(visual_mode="prompt_only")
        result = self.compile()
        self.assertEqual(result["image_design_version"], design.DESIGN_VERSION)
        self.assertNotIn("image_prompt", result["characters"][0])
        self.assertNotIn("CHAR0002", [item["id"] for item in design.visual_assets(result, "characters")])

    def test_masked_board_inherits_identity_clothes_and_keeps_every_view_covered(self):
        _, record = self.masked()
        result = self.compile()
        base, masked = result["characters"]
        self.assertEqual(masked["visual_design"]["identity_anchor_id"], base["visual_design"]["identity_anchor_id"])
        self.assertEqual(masked["visual_design"]["styling"], base["visual_design"]["styling"])
        self.assertEqual(masked["image_format"]["covering_state"], "worn_in_every_view")
        self.assertIn(record["covering"], masked["image_prompt"])
        self.assertNotIn("完整展示头部和清晰五官", masked["image_prompt"])
        self.assertNotIn("五官清晰", masked["image_prompt"])
        self.assertNotIn(self.payload["identity_anchors"][0]["face"], masked["image_prompt"])
        self.assertNotIn(self.payload["identity_anchors"][0]["hair"], masked["image_prompt"])

    def test_mask_variant_cannot_change_body_identity_or_clothes(self):
        _, record = self.masked()
        for field, value in (("identity_anchor_id", "另一个人"), ("styling", "红西装"),
                             ("appearance_overrides", {"body": "另一种体型"})):
            with self.subTest(field=field):
                record[field] = value
                with self.assertRaisesRegex(ValueError, "须继承基础形象"):
                    self.compile()
                record.pop(field)

    def test_always_masked_base_does_not_require_duplicate_asset(self):
        self.payload["designs"][0]["covering"] = "白色无字口罩覆盖口鼻，双耳挂绳，眼睛与头发外露"
        self.payload["designs"][0]["visible_identity"] = "沿用舒展眉眼，乌黑长发整齐束起；鼻唇被口罩遮住"
        result = self.compile()
        self.assertEqual(len(result["characters"]), 1)
        self.assertEqual(result["characters"][0]["image_format"]["covering_state"], "worn_in_every_view")
        self.assertIn("乌黑长发整齐束起", result["characters"][0]["image_prompt"])

    def test_mask_requires_explicit_visible_identity_and_current_transition_context(self):
        item, record = self.masked()
        visible = record.pop("visible_identity")
        with self.assertRaisesRegex(ValueError, "visible_identity"):
            self.compile()
        record["visible_identity"] = visible
        scene = self.data["scenes"][0]
        scene["character_appearances"] = [{
            "base_asset_id": "CHAR0001", "initial_asset_id": "CHAR0001",
            "initial_evidence": {"source_paragraphs": [1], "evidence": "陈默穿灰色布衣", "basis": "原服装状态"},
            "transitions": [{"event_id": "put_on", "from_asset_id": "CHAR0001", "to_asset_id": item["id"],
                             "source_paragraphs": [1], "evidence": "戴上头套", "basis": "显式戴上事件"}],
        }]
        result = self.compile()
        original = design.source_context_digest(result, "characters", result["characters"][1])
        result["scenes"][0]["character_appearances"][0]["transitions"][0]["basis"] = "修改过的事件依据"
        self.assertNotEqual(original, design.source_context_digest(result, "characters", result["characters"][1]))
        with self.assertRaisesRegex(ValueError, "出处或场次映射"):
            design.validate_visual_design(result)

    def test_v1_cannot_silently_render_new_mask_with_bare_face_layout(self):
        self.masked()
        self.payload["image_design_version"] = design.LEGACY_DESIGN_VERSION
        with self.assertRaisesRegex(ValueError, "遮面造型须显式"):
            self.compile()

    def test_linked_prop_supplies_one_authoritative_design_to_multiple_wearers(self):
        _, _, prop, prop_design = self.linked_mask(two_wearers=True)
        result = self.compile()
        wearers = [item for item in result["characters"] if item.get("appearance_variant")]
        self.assertEqual(len(wearers), 2)
        for item in wearers:
            self.assertEqual(item["image_format"]["covering_prop_asset_id"], prop["id"])
            for field in ("structure", "materials_palette", "scale", "state"):
                self.assertIn(prop_design[field], item["image_prompt"])
            self.assertNotIn("粉色软质猪头头套", item["image_prompt"])
        self.assertIn("深青布长衫", wearers[1]["image_prompt"])
        self.assertNotIn("深青布长衫", wearers[0]["image_prompt"])

    def test_linked_prop_rejects_duplicate_freeform_mask_appearance(self):
        _, record, _, _ = self.linked_mask()
        for covering in ("粉色软质猪头头套包住整个头部", {"fit": "贴合头部", "coverage": "遮脸", "color": "粉色"}):
            with self.subTest(covering=covering):
                record["covering"] = covering
                with self.assertRaisesRegex(ValueError, "covering仅填fit/coverage"):
                    self.compile()

    def test_linked_prop_design_edit_invalidates_all_wearers_and_recompile_uses_new_payload(self):
        self.linked_mask(two_wearers=True)
        result = self.compile()
        before = {item["id"]: (item["image_prompt"], item["visual_design"]["source_context_digest"])
                  for item in result["characters"] if item.get("appearance_variant")}
        result["props"][0]["visual_design"]["materials_palette"] = "绿色短绒表层，浅灰柔软内衬"
        for item in result["characters"]:
            if item["id"] in before:
                self.assertNotEqual(before[item["id"]][1], design.source_context_digest(result, "characters", item))
        with self.assertRaisesRegex(ValueError, "原文版本、出处或场次映射已改变"):
            design.validate_visual_design(result)
        next(value for value in self.payload["designs"] if value["asset_id"] == "PROP0001")["materials_palette"] = "绿色短绒表层，浅灰柔软内衬"
        revised = design.apply_design(result, self.payload, "古装写实")
        for item in revised["characters"]:
            if item["id"] in before:
                self.assertIn("绿色短绒表层", item["image_prompt"])
                self.assertNotIn("蓝色短绒表层", item["image_prompt"])
                self.assertNotEqual(before[item["id"]][0], item["image_prompt"])

    def test_linked_prop_fact_edit_invalidates_wearer_source_facts(self):
        _, _, prop, _ = self.linked_mask()
        result = self.compile()
        result["props"][0]["visual_facts"] = [{"text": "左耳有原文明示的缺口", "source_paragraphs": [1],
                                              "evidence": "左耳缺了一角", "scene_ids": ["SC0001"], "attribution": "叙述"}]
        wearer = result["characters"][1]
        self.assertIn("左耳有原文明示的缺口", design.source_constraints(result, "characters", wearer))
        with self.assertRaisesRegex(ValueError, "原文外观事实已改变"):
            design.validate_visual_design(result)
        revised = design.apply_design(result, self.payload, "古装写实")
        self.assertIn("左耳有原文明示的缺口", revised["characters"][1]["image_prompt"])

    def test_recompile_requires_current_source_facts_and_group_design(self):
        self.group()
        result = self.compile()
        result["characters"][0]["visual_design"]["group_members"][0]["face"] = "重新设计的面容"
        with self.assertRaisesRegex(ValueError, "设计或审阅依据已改变"):
            design.validate_visual_design(result)


if __name__ == "__main__":
    unittest.main()
