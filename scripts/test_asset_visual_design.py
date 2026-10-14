#!/usr/bin/env python3
"""Evidence and rendering regressions for reviewed GPT-image-2 asset design."""

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

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "modules/screenplay-standardizer/scripts"))
import asset_ledger as ledger
import asset_migrate as migration
import asset_visual_design as design


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def fixture(work, with_prop=True):
    paragraphs = ["陈默穿灰色布衣，取出一枚青铜令牌，放到桌上。", "陈默再次拿起青铜令牌，收入怀中。"]
    data = {"schema_version": 3, "asset_policy": design.PROP_POLICY,
            "source_digest": ledger.digest(paragraphs), "scenes": [],
            "characters": [{"id": "CHAR0001", "name": "陈默-第一世-基础形象", "source_entry": "陈默-基础形象",
                            "aliases": [], "personality_facts": [], "visual_facts": [
                                {"text": "灰色布衣", "evidence": "穿灰色布衣", "source_paragraphs": [1],
                                 "scene_ids": ["SC0001"], "attribution": "叙述"}]}],
            "locations": [{"id": "LOC0001", "name": "陈府", "aliases": [], "visual_facts": [],
                           "identity_status": "confirmed", "identity_basis": "原文与场次审阅确认同一陈府房间"}],
            "scene_assets": [{"id": "SET0001", "name": "陈府", "location_id": "LOC0001",
                              "variant": "base", "parent_id": None, "visual_facts": []}],
            "props": [], "retired_ids": [], "inactive_assets": {"characters": []}, "pending_reviews": []}
    drafts = []
    for number, text in enumerate(paragraphs, 1):
        heading = f"场景{number}-日-内-陈府"
        body = heading + "\n人物：陈默-基础形象；\n△ " + text + "\n"
        save(work / f"scenes/scene_{number:03d}_{number:04d}-{number:04d}.txt", body)
        drafts.append(body)
        data["scenes"].append({"id": f"SC{number:04d}", "number": number, "heading": heading,
                               "source_range": [number, number], "location_id": "LOC0001",
                               "scene_asset_id": "SET0001", "character_ids": ["CHAR0001"],
                               "prop_ids": ["PROP0001"] if with_prop else [], "visual_facts": [],
                               "time_basis": {"kind": "格式默认", "source_paragraphs": []}})
    if with_prop:
        data["props"].append({"id": "PROP0001", "name": "青铜令牌", "source_name": "青铜令牌",
                              "aliases": [], "visual_facts": [],
                              "extraction_review": {"kind": "special_reusable", "consistency_reason": "两次拿取时轮廓与图案需保持一致"},
                              "visible_uses": [
                                  {"scene_id": "SC0001", "event_id": "取出并放桌", "evidence": "取出一枚青铜令牌，放到桌上", "source_paragraphs": [1]},
                                  {"scene_id": "SC0002", "event_id": "拿起并收怀", "evidence": "再次拿起青铜令牌，收入怀中", "source_paragraphs": [2]}]})
    script, source = work / "script.txt", work / "source.json"
    save(script, "\n".join(drafts))
    save(source, {"paragraphs": [{"id": number, "text": text} for number, text in enumerate(paragraphs, 1)]})
    payload = {"identity_anchors": [{"id": "陈默-第一世", "age": "二十余岁成年男性",
                                   "body": "身形修长结实，肩背舒展，比例自然",
                                   "face": "清晰流畅的脸型，眉眼舒展，鼻梁挺直，保留真实皮肤纹理",
                                   "hair": "乌黑长发整齐束起，发际线自然"}],
               "designs": [
                   {"asset_id": "CHAR0001", "attribution": "制作设计", "basis": "古装环境与陈默已明示灰色布衣；补足未写的面部与发型",
                    "reviewed_source_facts": True, "identity_anchor_id": "陈默-第一世", "styling": "灰色粗布交领衣，剪裁朴素整洁，深灰布腰带与布鞋，无额外首饰"},
                   {"asset_id": "SET0001", "attribution": "制作设计", "basis": "陈府室内和令牌放桌动作，需要稳定桌面与行动空间",
                    "reviewed_source_facts": True, "space": "正厅入口正对后墙，侧窗在右侧，活动区居中，窗下长桌与入口之间留出两人通行的过道",
                    "materials_palette": "木构与浅米色墙面，暗棕家具，朴素而细致的古装建筑纹理",
                    "furnishings": "一张长桌位于侧窗下，两把木椅靠后墙，普通桌椅作为场景陈设",
                    "lighting": "窗侧柔和自然日光，明暗过渡细腻，暗部保留木纹"}]}
    if with_prop:
        payload["designs"].append({"asset_id": "PROP0001", "attribution": "制作设计", "basis": "需保持两次出入怀的令牌外形一致；不添加剧情密码或铭文",
                                  "reviewed_source_facts": True, "structure": "圆角长方形令牌，顶端小孔，边缘简洁凸起，不设计未知文字",
                                  "materials_palette": "青铜主体，低调金属光泽，少量自然氧化细节",
                                  "scale": "可由单手完整握持，孔与主体比例合理", "state": "完整未损，表面少量正常磨痕"})
    save(work / "ledger.json", data)
    save(work / "design.json", payload)
    save(work / "video-style.txt", "古装写实电影，雅致克制的配色，真实材质与自然光感。\n")
    return script, source, paragraphs, data, payload


class AssetVisualDesignTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "tests-asset-design"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, value):
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, value)

    def designed(self, value=None, payload=None):
        return design.apply_design(value or self.data, payload or self.payload, "古装写实电影，雅致克制的配色，真实材质与自然光感。")

    def test_independent_visible_uses_pass(self):
        self.validate(self.data)

    def test_one_visible_use_fails_despite_repeated_prop_words(self):
        self.data["props"][0]["visible_uses"].pop()
        with self.assertRaisesRegex(ValueError, "至少需要2次"):
            self.validate(self.data)

    def test_same_event_label_cannot_be_counted_twice(self):
        uses = self.data["props"][0]["visible_uses"]
        uses[1] = copy.deepcopy(uses[0])
        with self.assertRaisesRegex(ValueError, "同一独立事件重复"):
            self.validate(self.data)

    def test_two_labels_for_same_evidence_cannot_be_counted_twice(self):
        uses = self.data["props"][0]["visible_uses"]
        uses[1] = dict(uses[0], event_id="另一个标签")
        with self.assertRaisesRegex(ValueError, "证据重叠"):
            self.validate(self.data)

    def test_foreign_scene_evidence_fails(self):
        self.data["props"][0]["visible_uses"][1]["source_paragraphs"] = [1]
        with self.assertRaisesRegex(ValueError, "没有原文证据"):
            self.validate(self.data)

    def test_furniture_cannot_pass_by_claiming_special(self):
        prop = self.data["props"][0]
        prop["source_name"] = "桌"
        with self.assertRaisesRegex(ValueError, "固定大件家具"):
            design.validate_prop_policy(self.data, self.paragraphs, ledger.fact_list)

    def test_ordinary_items_are_excluded_special_items_still_need_two_uses(self):
        prop = self.data["props"][0]
        prop["source_name"] = "水杯"
        with self.assertRaisesRegex(ValueError, "特殊可辨形制"):
            design.validate_prop_policy(self.data, self.paragraphs, ledger.fact_list)
        prop["extraction_review"]["special_features"] = "原文确认杯底独特家族图案，需跨场匹配"
        design.validate_prop_policy(self.data, self.paragraphs, ledger.fact_list)
        prop["visible_uses"].pop()
        with self.assertRaisesRegex(ValueError, "至少需要2次"):
            design.validate_prop_policy(self.data, self.paragraphs, ledger.fact_list)

    def test_legacy_ledger_keeps_single_use_compatibility(self):
        self.data.pop("asset_policy")
        self.data["props"][0].pop("visible_uses")
        self.data["props"][0].pop("extraction_review")
        self.validate(self.data)

    def test_empty_prop_list_is_valid_and_new_init_marks_policy(self):
        script, source, paragraphs, value, payload = fixture(self.work / "empty", with_prop=False)
        ledger.validate(ledger.parse_script(script), paragraphs, value)
        out = self.work / "initialized.json"
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.initialize(script, source.parent, out)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["asset_policy"], design.PROP_POLICY)

    def test_compilation_is_deterministic_preserves_facts_and_input(self):
        original = copy.deepcopy(self.data)
        value = self.designed()
        self.assertEqual(value, self.designed())
        self.assertEqual(self.data, original)
        self.assertEqual(value["characters"][0]["visual_facts"], original["characters"][0]["visual_facts"])
        self.validate(value)
        prompt = value["characters"][0]["image_prompt"]
        for fragment in ("占满画面1/3宽度", "贯穿画面高度", "右侧2/3", "超大正面面部特写", "平视前方", "90度侧面全身", "头顶和脚底对齐", "灰色布衣"):
            self.assertIn(fragment, prompt)
        self.assertEqual(value["characters"][0]["image_format"]["aspect_ratio"], "16:9")
        self.assertIn("完全无人物", value["scene_assets"][0]["image_prompt"])
        self.assertIn("单件居中完整入镜", value["props"][0]["image_prompt"])

    def test_stale_prompt_or_source_facts_fail(self):
        value = self.designed()
        value["characters"][0]["image_prompt"] += "另换一套衣服"
        with self.assertRaisesRegex(ValueError, "提示词或版式"):
            self.validate(value)
        value = self.designed()
        value["characters"][0]["visual_facts"][0]["text"] = "穿灰色布衣"
        with self.assertRaisesRegex(ValueError, "事实已改变"):
            self.validate(value)

    def test_source_mapping_change_invalidates_existing_design(self):
        value = self.designed()
        value["scenes"][1]["temporal_layer_id"] = None
        value["scenes"][1]["character_ids"] = []
        with self.assertRaisesRegex(ValueError, "出处或场次映射"):
            design.validate_visual_design(value)

    def test_source_digest_change_rejects_design_without_touching_visible_fields(self):
        value = self.designed()
        value["source_digest"] = ledger.digest(self.paragraphs + ["新增剧情上下文"])
        with self.assertRaisesRegex(ValueError, "原文版本"):
            design.validate_visual_design(value)
        plain = copy.deepcopy(self.data)
        plain["source_digest"] = value["source_digest"]
        design.validate_visual_design(plain)  # Old ledgers without design remain unaffected.

    def test_source_edit_migration_marks_design_review_and_explicit_design_recovers(self):
        value = self.designed()
        old_path = self.work / "old-designed.json"
        save(old_path, value)
        new_work = self.work / "revised"
        new_script, new_source, paragraphs, new_data, payload = fixture(new_work)
        revised_paragraphs = paragraphs.copy()
        revised_paragraphs[1] += "他想起往日经历。"
        save(new_source, {"paragraphs": [{"id": number, "text": text} for number, text in enumerate(revised_paragraphs, 1)]})
        changed_text = new_script.read_text(encoding="utf-8").replace(paragraphs[1], revised_paragraphs[1])
        save(new_script, changed_text)
        save(new_work / "scenes/scene_002_0002-0002.txt",
             "场景2-日-内-陈府\n人物：陈默-基础形象；\n△ " + revised_paragraphs[1] + "\n")
        migrated, report = migration.migrate(old_path, self.source, new_script, new_work,
                                              {"scenes": {"SC0001": [1], "SC0002": [2]}})
        self.assertTrue(any(item["kind"] == "资产制作设计待复核" for item in report["pending_reviews"]))
        # An author reviewed current ledger retains valid evidence/links but
        # still contains its previous compiled design, so design must replace it.
        current = copy.deepcopy(value)
        current["source_digest"] = ledger.digest(revised_paragraphs)
        current_path = new_work / "current-ledger.json"
        save(current_path, current)
        command = [sys.executable, "-X", "utf8", str(SKILL / "modules/screenplay-standardizer/scripts/asset_ledger.py"),
                   "design", str(new_script), str(new_source), str(current_path), str(new_work / "design.json"),
                   "--style-file", str(new_work / "video-style.txt"), "--output", str(new_work / "redesigned.json")]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        restored = json.loads((new_work / "redesigned.json").read_text(encoding="utf-8"))
        ledger.validate(ledger.parse_script(new_script), revised_paragraphs, restored)
        self.assertNotEqual(restored["characters"][0]["visual_design"]["source_context_digest"],
                            value["characters"][0]["visual_design"]["source_context_digest"])

    def test_style_preserves_internal_spaces_and_rejects_multiple_lines(self):
        value = design.apply_design(self.data, self.payload, "  古装  写实  ")
        self.assertEqual(value["visual_style"]["text"], "古装  写实")
        with self.assertRaisesRegex(ValueError, "单行"):
            design.apply_design(self.data, self.payload, "古装\n写实")

    def test_shared_identity_anchor_and_explicit_hair_variant(self):
        value = copy.deepcopy(self.data)
        variant = dict(value["characters"][0], id="CHAR0002", name="陈默-第一世-束发形象")
        variant["visual_facts"] = []
        value["characters"].append(variant)
        payload = copy.deepcopy(self.payload)
        payload["designs"].append(dict(payload["designs"][0], asset_id="CHAR0002",
                                     appearance_overrides={"hair": "同一黑发改束高髻"},
                                     variant_differences=["已登记束发版本仅改变发髻，脸部与体型保留"]))
        out = self.designed(value, payload)
        self.assertEqual(out["characters"][0]["visual_design"]["identity_anchor_id"], out["characters"][1]["visual_design"]["identity_anchor_id"])
        self.assertIn("同一黑发改束高髻", out["characters"][1]["image_prompt"])
        self.assertIn("脸部与体型保留", out["characters"][1]["image_prompt"])
        payload["designs"][-1].pop("variant_differences")
        with self.assertRaisesRegex(ValueError, "明确差异"):
            self.designed(value, payload)

    def test_structured_identity_requires_shared_anchor_and_different_bodies_separate(self):
        value = copy.deepcopy(self.data)
        value["characters"][0]["appearance"] = {"identity": "陈默", "incarnation": "第一世"}
        second = copy.deepcopy(value["characters"][0])
        second.update(id="CHAR0002", name="陈默-第一世-换装形象")
        value["characters"].append(second)
        payload = copy.deepcopy(self.payload)
        anchor = dict(payload["identity_anchors"][0], id="另一个锚点")
        payload["identity_anchors"].append(anchor)
        payload["designs"].append(dict(payload["designs"][0], asset_id="CHAR0002", identity_anchor_id="另一个锚点"))
        with self.assertRaisesRegex(ValueError, "相同身份与世次"):
            self.designed(value, payload)
        payload["designs"][-1]["identity_anchor_id"] = payload["designs"][0]["identity_anchor_id"]
        self.designed(value, payload)
        value["characters"][1]["appearance"]["incarnation"] = "第二世"
        with self.assertRaisesRegex(ValueError, "不同身份或世次"):
            self.designed(value, payload)

    def test_changed_design_basis_requires_recompilation(self):
        value = self.designed()
        value["characters"][0]["visual_design"]["basis"] = "改了依据尚未重新审阅"
        with self.assertRaisesRegex(ValueError, "设计或审阅依据已改变"):
            design.validate_visual_design(value)

    def test_face_override_requires_verified_source_fact(self):
        self.payload["designs"][0]["appearance_overrides"] = {"face": "新增眼角疤痕"}
        self.payload["designs"][0]["variant_differences"] = ["新增疤痕"]
        with self.assertRaisesRegex(ValueError, "覆盖face须指定已验证原文事实"):
            self.designed()

    def test_missing_design_or_source_review_fails(self):
        self.payload["designs"][0].pop("reviewed_source_facts")
        with self.assertRaisesRegex(ValueError, "审阅原文明示"):
            self.designed()
        self.payload["designs"][0]["reviewed_source_facts"] = True
        self.payload["designs"].pop()
        with self.assertRaisesRegex(ValueError, "覆盖全部"):
            self.designed()

    def test_numbered_catalog_uses_same_json_prompts(self):
        value = self.designed()
        path, output = self.work / "designed.json", self.work / "catalog.txt"
        save(path, value)
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.render(self.script, self.source, path, output)
        text = output.read_text(encoding="utf-8")
        for category, prefix in (("characters", "角色"), ("scene_assets", "场景"), ("props", "道具")):
            item = value[category][0]
            self.assertIn(prefix + "01 - " + item["name"] + "：" + item["image_prompt"], text)
        self.assertIn("出现的场景：场景1，场景2。", text)
        self.assertIn("使用的场次：场景1，场景2。", text)

    def test_cli_design_and_recompile_stale_style_use_fresh_outputs(self):
        command = [sys.executable, str(SKILL / "modules/screenplay-standardizer/scripts/asset_ledger.py"),
                   "design", str(self.script), str(self.source), str(self.work / "ledger.json"),
                   str(self.work / "design.json"), "--style-file", str(self.work / "video-style.txt"),
                   "--output", str(self.work / "designed.json"), "--catalog-output", str(self.work / "catalog.txt")]
        env = dict(os.environ, PYTHONUTF8="1")
        first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        repeat = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertNotEqual(repeat.returncode, 0)
        save(self.work / "video-style.txt", "古装写实电影，柔和明亮的中间调")
        command[command.index(str(self.work / "ledger.json"))] = str(self.work / "designed.json")
        command[command.index("--output") + 1] = str(self.work / "redesigned.json")
        command[command.index("--catalog-output") + 1] = str(self.work / "recatalog.txt")
        changed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertIn("柔和明亮", json.loads((self.work / "redesigned.json").read_text(encoding="utf-8"))["visual_style"]["text"])

    def test_unchanged_migration_preserves_design_and_visible_uses(self):
        value = self.designed()
        old_path = self.work / "designed.json"
        save(old_path, value)
        out, report = migration.migrate(old_path, self.source, self.script, self.work, {})
        self.assertEqual(out["visual_style"], value["visual_style"])
        self.assertEqual(out["props"][0]["visible_uses"], value["props"][0]["visible_uses"])
        self.assertFalse(report["pending_reviews"])
        self.validate(out)


if __name__ == "__main__":
    unittest.main()
