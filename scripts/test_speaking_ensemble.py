"""Collective dialogue remains literal and compiles to distinct-member boards."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from test_asset_visual_design import fixture, save, ledger, design


class SpeakingEnsembleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(Path(self.temp.name))
        self.paragraphs[0] += "几位学弟相互看了一眼，异口同声：「学姐好。」"
        self.data["source_digest"] = ledger.digest(self.paragraphs)
        self.data["group_policy"] = design.GROUP_POLICY
        self.data["scenes"][0]["character_ids"].append("CHAR0002")
        text = self.script.read_text(encoding="utf-8")
        text = text.replace("人物：陈默-基础形象；", "人物：陈默-基础形象；几位学弟-基础形象；", 1)
        text = text.replace("场景2-", "△ 几位学弟相互看了一眼，异口同声。\n几位学弟(齐声)：学姐好。\n\n场景2-", 1)
        save(self.script, text)
        save(self.source, {"paragraphs": [{"id": i, "text": t} for i, t in enumerate(self.paragraphs, 1)]})
        self.item = {"id": "CHAR0002", "name": "几位学弟-基础形象", "source_entry": "几位学弟-基础形象",
                     "source_speaker": "几位学弟", "character_kind": "speaking_ensemble", "visual_mode": "image_asset",
                     "visual_facts": [], "personality_facts": [], "aliases": [],
                     "ensemble_review": {"kind": "speaking_ensemble", "reviewed_full_script": True,
                       "source_digest": self.data["source_digest"], "identity_basis": "原文几位学弟为集合，未给出全部成员",
                       "speech_basis": "原文明示异口同声学姐好，保留集合声源及字词",
                       "consistency_reason": "多人反应和共同发声需要可辨的学生造型，样本不规定剧情人数"},
                     "visible_uses": [{"scene_id": "SC0001", "event_id": "共同问好",
                       "evidence": "几位学弟相互看了一眼，异口同声：「学姐好。」", "source_paragraphs": [1]}]}
        self.data["characters"].append(self.item)
        self.payload["image_design_version"] = "gpt-image-2-v2"
        self.payload["designs"].append({"asset_id": "CHAR0002", "attribution": "制作设计",
          "basis": "有共同台词的学生集合，需要多人外形样本，人数仍由剧情决定", "reviewed_source_facts": True,
          "group_wardrobe": {"mode": "varied", "era_region": "当代中国高校男生", "basis": "无统一制服要求，日常学生服饰"},
          "group_members": [
            {"sample_id": "01", "age": "成年青年男性", "body": "修长窄肩", "face": "长脸平眉", "hair": "黑色短直发", "styling": "白棉短袖与蓝牛仔裤"},
            {"sample_id": "02", "age": "成年青年男性", "body": "中等身高宽肩", "face": "方脸圆眼", "hair": "黑色微卷短发", "styling": "灰色衬衫与黑休闲裤"}]})

    def compiled(self):
        value = design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, value)
        return value

    def test_collective_dialogue_and_multimember_board(self):
        value = self.compiled()
        item = value["characters"][-1]
        self.assertEqual(item["image_format"]["layout"], "distinct_members_single_front_full_body")
        self.assertEqual(item["image_format"]["subject_kind"], "speaking_ensemble")
        self.assertFalse(item["image_format"]["sample_count_is_story_count"])
        self.assertIn("几位学弟(齐声)：学姐好。", self.script.read_text(encoding="utf-8"))
        self.assertNotIn("其他角色", item["image_prompt"])
        save(Path(self.temp.name) / "designed.json", value)
        ledger.render(self.script, self.source, Path(self.temp.name) / "designed.json", Path(self.temp.name) / "catalog.txt")
        self.assertIn(item["image_prompt"], (Path(self.temp.name) / "catalog.txt").read_text(encoding="utf-8"))

    def test_cannot_demote_collective_speaker(self):
        self.item["visual_mode"] = "prompt_only"
        with self.assertRaises(ValueError): self.compiled()

    def test_needs_current_review_and_source_evidence(self):
        for field, bad in [("ensemble_review", {}), ("visible_uses", [])]:
            with self.subTest(field=field):
                old = self.item[field]; self.item[field] = bad
                with self.assertRaises(ValueError): self.compiled()
                self.item[field] = old

    def test_collective_label_must_exist_in_own_scene(self):
        self.item["source_speaker"] = "不存在的集合"
        with self.assertRaises(ValueError): self.compiled()

    def test_review_change_expires_compiled_prompt(self):
        value = self.compiled()
        value["characters"][-1]["ensemble_review"]["identity_basis"] += "改变"
        with self.assertRaises(ValueError): design.validate_visual_design(value)

    def test_single_face_design_cannot_replace_ensemble(self):
        self.payload["designs"][-1] = {"asset_id": "CHAR0002", "attribution": "制作设计", "basis": "单人占位",
          "reviewed_source_facts": True, "identity_anchor_id": "陈默-第一世", "styling": "白衬衫"}
        with self.assertRaises(ValueError): self.compiled()

    def test_ordinary_single_actor_is_byte_stable_in_both_versions(self):
        # Captured from the unmodified compiler, including prompt/format/digests.
        baseline_hashes = {
            "gpt-image-2-v1": "648678cebe1abd90ceac74bebfda8e3ddf6d876606a6ab24990f3ebd8b103f85",
            "gpt-image-2-v2": "0dd9aefeae5808e78c3eceabb4316dd1b9f0769ad9e29b48b2de3390f833fdd1",
        }
        with tempfile.TemporaryDirectory() as temp:
            script, source, paragraphs, data, payload = fixture(Path(temp))
            before = script.read_bytes()
            for version in ("gpt-image-2-v1", "gpt-image-2-v2"):
                payload["image_design_version"] = version
                actual = design.apply_design(copy.deepcopy(data), copy.deepcopy(payload), "3D国漫写实厚涂风格")
                digest = hashlib.sha256(json.dumps(actual, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                self.assertEqual(digest, baseline_hashes[version])
            self.assertEqual(script.read_bytes(), before)

    def test_no_automatic_collective_classification(self):
        with tempfile.TemporaryDirectory() as temp:
            script, source, paragraphs, data, payload = fixture(Path(temp))
            data["characters"][0]["ensemble_review"] = copy.deepcopy(self.item["ensemble_review"])
            self.assertFalse(design.is_speaking_ensemble(data["characters"][0]))
            result = design.apply_design(data, payload, "3D国漫写实厚涂风格")
            self.assertEqual(result["characters"][0]["image_format"]["layout"], "left_1/3_large_front_face_right_2/3_front_side90_back_full_body")

    def test_stale_ensemble_review_is_rejected(self):
        self.item["ensemble_review"]["source_digest"] = "stale"
        with self.assertRaises(ValueError): self.compiled()

    def test_background_group_speech_still_rejected(self):
        self.item.pop("character_kind")
        self.item["name"] = self.item["source_entry"] = "几位学弟-群像-基础形象"
        self.item["extraction_review"] = {"kind": "background_group", "reviewed_full_script": True,
          "source_digest": self.data["source_digest"], "identity_basis": "测试误把发声集合当背景",
          "selection_basis": "core_continuity", "requires_reference": True,
          "story_role": "共同反应", "consistency_reason": "服装参考"}
        save(self.script, self.script.read_text(encoding="utf-8").replace("几位学弟-基础形象", "几位学弟-群像-基础形象"))
        with self.assertRaisesRegex(ValueError, "独立台词"):
            design.validate_group_policy(self.data, self.paragraphs, ledger.fact_list, ledger.parse_script(self.script))


if __name__ == "__main__":
    unittest.main()
