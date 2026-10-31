"""Synthetic source/draft mappings; never read or write a production project."""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "modules/screenplay-standardizer/scripts"))
import screenplay_io
import screenplay_review as review


class SourceFragmentMappingTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="source-fragments-", dir=root)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        (self.work / "scenes").mkdir()

    def fixture(self, paragraphs, body, location="泳池"):
        if hasattr(self, "path"):
            self.path.unlink(missing_ok=True)
        self.paragraphs = paragraphs
        self.source = self.work / "source.json"
        self.source.write_text(json.dumps({"paragraphs": [
            {"id": n, "text": text} for n, text in enumerate(paragraphs, 1)]}, ensure_ascii=False), encoding="utf-8")
        self.path = self.work / f"scenes/scene_001_0001-{len(paragraphs):04d}.txt"
        self.path.write_text("场景1-日-外-" + location + "\n人物：阿明-基础形象；阿红-基础形象；\n" + body + "\n", encoding="utf-8")
        self.initial = review.make_report(self.work, {})
        return self.initial

    def piece(self, paragraph, source, line, target_text=None, kind="action", **fields):
        target_text = source if target_text is None else target_text
        raw = self.paragraphs[paragraph - 1]
        draft = self.path.read_text(encoding="utf-8").splitlines()[line - 1]
        a, c = raw.index(source), draft.index(target_text)
        return {"source_range": [a, a + len(source)], "source_text": source,
                "target": {"kind": kind, "line": line, "range": [c, c + len(target_text)], "text": target_text},
                **fields}

    def decisions(self, paragraph, pieces, speaker="阿明", report=None):
        report = report or self.initial
        item = next(item for item in report["scenes"][0]["paragraph_map"] if item["paragraph"] == paragraph)
        result = {"fragments": {item["fragment_key"]: dict(item["fragment_template"],
                    reason="已实读源片段及其对应动作、时间和声音标签，仅作格式迁移。", fragments=pieces)}}
        for spoken in report["scenes"][0]["speaker_items"]:
            if spoken["draft_line"] is not None:
                result[spoken["decision_key"]] = dict(spoken["decision_template"], speaker=speaker)
        return result

    def mapping(self, decisions):
        return next(iter(decisions["fragments"].values()))

    def cue_fixture(self, cue="os", target_cue="内心想法os", spoken="“我知道。”", speaker="阿明", action="阿明垂眸"):
        self.fixture([action + cue, spoken], f"△ {action}。\n{speaker}({target_cue})：我知道。")
        return self.decisions(1, [self.piece(1, action, 3),
            self.piece(1, cue, 4, target_cue, "cue", speaker="阿明",
                dialogue_source={"source_range": [2, 2], "source_lines": [spoken]})])

    def transition_fixture(self):
        self.fixture(["转场 阿明家游泳池 傍晚", "阿明入水。"], "△ 傍晚。\n△ 阿明入水。", "阿明家游泳池")
        return self.decisions(1, [self.piece(1, "阿明家游泳池", 1, kind="heading"), self.piece(1, "傍晚", 3)])

    def checked(self, decisions, previous=None, **kwargs):
        return review.make_report(self.work, decisions, previous=previous, **kwargs)

    def assert_blocked(self, decisions, issue=None):
        report = self.checked(decisions)
        self.assertEqual(report["status"], "needs_review", report)
        if issue:
            self.assertTrue(any(issue in item.get("issue", "") for item in report["scenes"][0]["coverage_missing"]), report)
        return report

    def test_suffix_voice_to_adjacent_dialogue_with_full_action_preserved(self):
        for cue, target in [("os", "内心想法os"), ("O.S.", "内心想法os"),
                            ("ov", "画外音"), ("VO", "画外音"), ("V.O.", "画外音"),
                            ("画外音", "画外音")]:
            with self.subTest(cue=cue):
                decisions = self.cue_fixture(cue, target)
                original = self.source.read_bytes()
                report = self.checked(decisions)
                self.assertEqual(report["status"], "passed", report)
                self.assertEqual(report["counts"]["missing"], 0)
                self.assertEqual(report["counts"]["extra"], 0)
                self.assertEqual(report["scenes"][0]["paragraph_map"][0]["status"], "已映射")
                self.assertEqual(self.source.read_bytes(), original)

    def test_unquoted_dialogue_does_not_include_cue_paragraph_as_spoken_text(self):
        decisions = self.cue_fixture(spoken="我知道。")
        report = self.checked(decisions)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["scenes"][0]["speaker_items"][0]["source_paragraphs"], [2])

    def test_adjacent_dialogue_can_have_multiple_source_paragraphs(self):
        self.fixture(["阿明垂眸os", "“我", "知道。”"], "△ 阿明垂眸。\n阿明(内心想法os)：我知道。")
        decisions = self.decisions(1, [self.piece(1, "阿明垂眸", 3), self.piece(1, "os", 4, "内心想法os", "cue", speaker="阿明",
            dialogue_source={"source_range": [2, 3], "source_lines": ["“我", "知道。”"]})])
        self.assertEqual(self.checked(decisions)["status"], "passed")

    def test_location_to_heading_and_time_to_action_need_no_duplicate_location(self):
        decisions = self.transition_fixture()
        report = self.checked(decisions)
        self.assertEqual(report["status"], "passed", report)
        saved = self.work / "review.json"
        saved.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            screenplay_io.assemble(self.work, self.work / "assembled.txt", saved)
        self.assertEqual((self.work / "assembled.txt").read_text(encoding="utf-8").count("阿明家游泳池"), 1)

    def test_general_verbatim_mapping_can_span_action_and_dialogue(self):
        self.fixture(["阿明说“走吧。”"], "△ 阿明说\n阿明：走吧。")
        decisions = self.decisions(1, [self.piece(1, "阿明说", 3), self.piece(1, "走吧。", 4, kind="dialogue")])
        self.assertEqual(self.checked(decisions)["status"], "passed")

    def test_source_and_target_use_real_character_offsets_including_whitespace(self):
        self.fixture(["  转场 泳池 傍晚  "], "  △ 傍晚。  ")
        decisions = self.decisions(1, [self.piece(1, "泳池", 1, kind="heading"), self.piece(1, "傍晚", 3)])
        self.assertEqual(self.checked(decisions)["status"], "passed")

    def test_deleted_action_or_time_cannot_be_hidden_by_a_partial_partition(self):
        decisions = self.cue_fixture(action="阿明垂眸转身")
        self.mapping(decisions)["fragments"][0] = self.piece(1, "阿明垂眸", 3)
        self.assert_blocked(decisions, "完整覆盖")
        decisions = self.transition_fixture()
        self.mapping(decisions)["fragments"].pop()
        self.assert_blocked(decisions, "完整覆盖")

    def test_deleted_actual_draft_action_or_time_invalidates_whole_mapping(self):
        for original, replacement in [("阿明垂眸", "阿明"), ("傍晚", "")]:
            with self.subTest(original=original):
                decisions = self.cue_fixture() if original == "阿明垂眸" else self.transition_fixture()
                self.path.write_text(self.path.read_text(encoding="utf-8").replace(original, replacement), encoding="utf-8")
                self.assert_blocked(decisions, "已失效")

    def test_extra_draft_content_is_still_reported(self):
        self.fixture(["转场 泳池 傍晚"], "△ 傍晚。\n△ 阿明跳舞。")
        decisions = self.decisions(1, [self.piece(1, "泳池", 1, kind="heading"), self.piece(1, "傍晚", 3)])
        self.assertGreater(self.assert_blocked(decisions)["counts"]["extra"], 0)

    def test_wrong_cue_speaker_or_voice_type_is_blocked(self):
        decisions = self.cue_fixture(speaker="阿红")
        self.assert_blocked(decisions, "声源")
        decisions = self.cue_fixture(target_cue="画外音")
        self.assert_blocked(decisions, "声音语法")

    def test_voice_mapping_cannot_override_an_explicit_source_speaker(self):
        decisions = self.cue_fixture(spoken="阿明：“我知道。”", speaker="阿红")
        self.mapping(decisions)["fragments"][1]["speaker"] = "阿红"
        for key, decision in decisions.items():
            if key != "fragments":
                decision["speaker"] = "阿红"
        self.assert_blocked(decisions, "明确角色标签不一致")

    def test_mapping_cannot_replace_independent_speaker_review(self):
        decisions = self.cue_fixture(action="阿明看向阿红")
        decisions = {"fragments": decisions["fragments"]}
        report = self.assert_blocked(decisions, "独立通过复核")
        self.assertGreater(report["counts"]["speaker"], 0)
        self.assertIsNone(report["scenes"][0]["speaker_items"][0]["cue_hint"])

    def test_voice_mapping_cannot_hide_narration_or_unknown_modifiers(self):
        for cue, target in [("低声", "低声"), ("os转身", "内心想法os"), ("os拔刀", "os拔刀")]:
            with self.subTest(cue=cue):
                decisions = self.cue_fixture(cue, target)
                self.assert_blocked(decisions, "声音语法")

    def test_a_suffix_inside_an_english_word_is_not_voice_syntax(self):
        decisions = self.cue_fixture(action="阿明查看log", cue="os")
        self.assert_blocked(decisions, "声音语法")

    def test_voice_cannot_skip_a_source_action_or_draft_action(self):
        self.fixture(["阿明垂眸os", "阿红转身。", "“我知道。”"], "△ 阿明垂眸。\n△ 阿红转身。\n阿明(内心想法os)：我知道。")
        decisions = self.decisions(1, [self.piece(1, "阿明垂眸", 3), self.piece(1, "os", 5, "内心想法os", "cue", speaker="阿明",
            dialogue_source={"source_range": [3, 3], "source_lines": ["“我知道。”"]})])
        self.assert_blocked(decisions, "紧邻")
        self.fixture(["阿明垂眸os", "“我知道。”"], "△ 阿明垂眸。\n△ 阿红转身。\n阿明(内心想法os)：我知道。")
        decisions = self.decisions(1, [self.piece(1, "阿明垂眸", 3), self.piece(1, "os", 5, "内心想法os", "cue", speaker="阿明",
            dialogue_source={"source_range": [2, 2], "source_lines": ["“我知道。”"]})])
        self.assert_blocked(decisions, "紧邻")

    def test_partial_cue_target_cannot_hide_added_cue_text(self):
        decisions = self.cue_fixture(target_cue="内心想法os，拔刀")
        self.mapping(decisions)["fragments"][1]["target"] = self.piece(1, "os", 4, "内心想法os", "cue")["target"]
        self.assert_blocked(decisions, "声音语法")

    def test_ranges_and_quoted_evidence_must_match_and_not_overlap(self):
        original = self.transition_fixture()
        for mode in ("source_overlap", "wrong_source_text", "wrong_target_text", "range_type", "out_of_bounds", "reverse"):
            with self.subTest(mode=mode):
                decisions = copy.deepcopy(original)
                pieces = self.mapping(decisions)["fragments"]
                if mode == "source_overlap": pieces.append(copy.deepcopy(pieces[-1]))
                elif mode == "wrong_source_text": pieces[0]["source_text"] = "另一处泳池"
                elif mode == "wrong_target_text": pieces[0]["target"]["text"] = "另一处泳池"
                elif mode == "range_type": pieces[0]["source_range"][0] = True
                elif mode == "out_of_bounds": pieces[0]["target"]["range"][1] = 1000
                elif mode == "reverse": pieces.reverse()
                self.assert_blocked(decisions)

    def test_wrong_order_of_actual_target_ranges_is_blocked(self):
        self.fixture(["甲乙"], "△ 乙甲。")
        decisions = self.decisions(1, [self.piece(1, "甲", 3), self.piece(1, "乙", 3)])
        self.assert_blocked(decisions, "倒序")

    def test_heading_format_words_cannot_consume_narrative(self):
        self.fixture(["转场 日 泳池"], "△ 泳池。")
        decisions = self.decisions(1, [self.piece(1, "日", 1, kind="heading"), self.piece(1, "泳池", 3)])
        self.assert_blocked(decisions, "对应正文/地点")
        self.fixture(["泳池"], "△ 傍晚。")
        decisions = self.decisions(1, [self.piece(1, "泳池", 1, kind="heading")])
        self.assert_blocked(decisions, "场首转场")

    def test_heading_target_cannot_be_consumed_twice_across_mappings_or_automatic_match(self):
        for map_first in (True, False):
            with self.subTest(map_first=map_first):
                self.fixture(["转场 泳池", "转场 泳池"], "")
                decisions = self.decisions(2, [self.piece(2, "泳池", 1, kind="heading")])
                if map_first:
                    decisions["fragments"].update(self.decisions(1, [self.piece(1, "泳池", 1, kind="heading")])["fragments"])
                self.assert_blocked(decisions, "已被消费")

    def test_one_cue_cannot_consume_two_source_voice_markers(self):
        decisions = self.cue_fixture(spoken="阿明OS：“我知道。”")
        self.assert_blocked(decisions, "不能重复使用")

    def test_new_mapping_cannot_be_combined_with_whole_paragraph_exemption(self):
        decisions = self.transition_fixture()
        key = next(iter(decisions["fragments"]))
        decisions["paragraphs"] = {key: dict(self.initial["scenes"][0]["paragraph_map"][0]["decision_template"], reason="纯提示")}
        self.assert_blocked(decisions, "纯提示豁免")

    def test_changed_target_source_context_or_paragraph_coordinates_never_reuse(self):
        decisions = self.cue_fixture()
        report = self.checked(decisions)
        self.assertEqual(report["status"], "passed")
        old_key = next(iter(decisions["fragments"]))
        self.paragraphs[1] = "“我知道！”"
        self.source.write_text(json.dumps({"paragraphs": [{"id": n, "text": text} for n, text in enumerate(self.paragraphs, 1)]}, ensure_ascii=False), encoding="utf-8")
        # Context punctuation is intentionally ignored by review_key, but the
        # bound template and source hash must still invalidate the decision.
        changed = self.checked(decisions, report)
        self.assertEqual(changed["counts"]["reused_scenes"], 0)
        self.assertIn(old_key, decisions["fragments"])
        self.assertEqual(changed["status"], "needs_review")
        decisions = self.cue_fixture()
        report = self.checked(decisions)
        self.path.rename(self.work / "scenes/scene_001_0002-0003.txt")
        self.source.write_text(json.dumps({"paragraphs": [{"id": n, "text": text}
            for n, text in enumerate(["第十章"] + self.paragraphs, 1)]}, ensure_ascii=False), encoding="utf-8")
        shifted = self.checked(decisions, report, scope="partial")
        self.assertEqual(shifted["counts"]["reused_scenes"], 0)
        self.assertEqual(shifted["status"], "needs_review")

    def test_mapping_add_edit_delete_invalidate_scene_decision_hash(self):
        decisions = self.transition_fixture()
        report = self.checked(decisions)
        same = self.checked(decisions, report)
        self.assertEqual(same["counts"]["reused_scenes"], 1)
        changed = copy.deepcopy(decisions)
        self.mapping(changed)["reason"] += "已二次实读。"
        rechecked = self.checked(changed, report)
        self.assertEqual(rechecked["counts"]["reused_scenes"], 0)
        self.assertNotEqual(rechecked["scenes"][0]["decision_hash"], report["scenes"][0]["decision_hash"])
        self.assertEqual(self.checked({}, report)["status"], "needs_review")
        added = self.checked(decisions, self.initial)
        self.assertEqual(added["status"], "passed")
        self.assertEqual(added["counts"]["reused_scenes"], 0)

    def test_old_algorithm_cache_is_rechecked_and_cannot_authorize_assembly(self):
        decisions = self.cue_fixture()
        report = self.checked(decisions)
        for version in (7, 8):
            with self.subTest(version=version):
                previous = copy.deepcopy(report)
                previous["review_version"] = version
                checked = self.checked(decisions, previous)
                self.assertEqual(checked["status"], "passed")
                self.assertEqual(checked["counts"]["reused_scenes"], 0)
                saved = self.work / "old-review.json"
                saved.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "当前版本"):
                    screenplay_io.assemble(self.work, self.work / "output.txt", saved)

    def test_old_speaker_and_paragraph_decisions_still_work_after_upgrade(self):
        self.fixture(["第二段说明", "“我知道。”"], "阿明：我知道。")
        paragraph = self.initial["scenes"][0]["paragraph_map"][0]
        spoken = self.initial["scenes"][0]["speaker_items"][0]
        decisions = {"paragraphs": {paragraph["decision_key"]: dict(paragraph["decision_template"], reason="已人工确认的非剧情结构说明")},
                     spoken["decision_key"]: dict(spoken["decision_template"], speaker="阿明")}
        self.assertEqual(self.checked(decisions)["status"], "passed")

    def test_invalid_fragment_container_and_null_mapping_fail_closed(self):
        self.transition_fixture()
        with self.assertRaisesRegex(ValueError, "fragments"):
            self.checked({"fragments": []})
        key = self.initial["scenes"][0]["paragraph_map"][0]["fragment_key"]
        self.assert_blocked({"fragments": {key: None}}, "已失效")


if __name__ == "__main__":
    unittest.main()
