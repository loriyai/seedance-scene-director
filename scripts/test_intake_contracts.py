"""Intake regression fixtures; all temporary artifacts stay beside the candidate."""

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
MODULE = SKILL / "modules/screenplay-standardizer/scripts"
sys.path.insert(0, str(MODULE))
import screenplay_io
import screenplay_review as review
from cast_contract import validate_cast_entries

TEST_ROOT = SKILL.parent / "tests-intake-contracts"


class IntakeContracts(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="intake-", dir=TEST_ROOT)
        self.work = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, paragraphs):
        (self.work / "source.json").write_text(json.dumps({"paragraphs": [
            {"id": n, "text": text} for n, text in enumerate(paragraphs, 1)]}, ensure_ascii=False), encoding="utf-8")

    def draft(self, body, cast="人物：林舟-基础形象；", number=1, start=1, end=1):
        directory = self.work / "scenes"
        directory.mkdir(exist_ok=True)
        path = directory / f"scene_{number:03d}_{start:04d}-{end:04d}.txt"
        path.write_text(f"场景{number}-日-内-房间\n{cast}\n{body}\n", encoding="utf-8")
        return path

    def fixture(self, source, body, cast="人物：林舟-基础形象；"):
        self.source([source])
        self.draft(body, cast)
        return review.make_report(self.work, {})

    def sound_decision(self, report):
        item = next(item for item in report["scenes"][0]["speaker_items"] if item["draft_line"] is not None)
        decision = dict(item["decision_template"], classification="非人物播报", source_label="电话提示音",
                        reason="原文明确来自电话自动提示，无人物视觉身份")
        return {item["decision_key"]: decision}

    def test_duplicate_source_person_rejected_before_review_and_assembly(self):
        self.source(["林舟戴上头套。"])
        path = self.draft("△ 林舟戴上头套。", "人物：林舟-基础形象；林舟-戴头套；")
        self.assertTrue(screenplay_io.validate_scene(path.read_text(encoding="utf-8"), 1, path))
        with self.assertRaisesRegex(ValueError, "槽位重复"):
            review.make_report(self.work, {})
        self.assertEqual(validate_cast_entries(["男生1-基础形象", "男生2-基础形象"]), [])

    def test_legal_quote_styles_pass_without_rewriting_raw_source(self):
        for opened, closed in [("「", "」"), ("『", "』"), ("“", "”"), ("‘", "’"), ('"', '"')]:
            with self.subTest(opened=opened):
                report = self.fixture(f"林舟{opened}我知道。{closed}", "林舟：我知道。")
                self.assertEqual(report["status"], "passed", report)
                original = (self.work / "source.json").read_bytes()
                review.make_report(self.work, {})
                self.assertEqual(original, (self.work / "source.json").read_bytes())
                self.assertTrue(report["scenes"][0]["parsing_transforms"])

    def test_nested_legal_quotes_remain_inside_the_spoken_text(self):
        report = self.fixture("林舟：“他说『走吧』。”", "林舟：他说『走吧』。")
        self.assertEqual(report["status"], "passed")

    def test_wrong_closer_is_reported_not_guessed(self):
        for source in ['林舟「我知道。”', '林舟：我知道。」']:
            with self.subTest(source=source):
                report = self.fixture(source, "林舟：我知道。")
                self.assertEqual(report["status"], "needs_review")
                self.assertGreater(report["issue_counts"].get("source_format", 0), 0)

    def test_explicit_os_positions_preserve_cue(self):
        for source in ["林舟OS：“我知道。”", "OS 林舟：“我知道。”", "林舟 O.S.：「我知道。」"]:
            with self.subTest(source=source):
                report = self.fixture(source, "林舟(内心想法os)：我知道。")
                self.assertEqual(report["status"], "passed", report)
                self.assertTrue(any(item["kind"] == "明确声音标签解析" for item in report["scenes"][0]["parsing_transforms"]))
                report = self.fixture(source, "林舟：我知道。")
                self.assertEqual(report["status"], "needs_review")

    def test_explicit_offscreen_positions_preserve_cue(self):
        for source in ["林舟ov：“我知道。”", "ov 林舟：“我知道。”", "林舟VO：「我知道。」", "林舟 V.O.：“我知道。”"]:
            with self.subTest(source=source):
                report = self.fixture(source, "林舟(画外音)：我知道。")
                self.assertEqual(report["status"], "passed", report)
                self.assertTrue(any(item["kind"] == "明确声音标签解析" for item in report["scenes"][0]["parsing_transforms"]))
                report = self.fixture(source, "林舟：我知道。")
                self.assertEqual(report["status"], "needs_review")

    def test_anonymous_numbered_people_remain_distinct_in_quotes_and_os(self):
        names = ["记者1", "记者2", "保镖01", "保镖02", "记者一"]
        for name in names:
            with self.subTest(name=name):
                report = self.fixture(f'{name}OS：“明白。”', f'{name}(内心想法os)：明白。',
                                      "人物：" + "；".join(value + "-基础形象" for value in names) + "；")
                self.assertEqual(report["status"], "passed", report)
                self.assertEqual(report["scenes"][0]["speaker_items"][0]["speaker"], name)
        report = self.fixture('记者1：“明白。”', '记者一：明白。', '人物：记者1-基础形象；记者一-基础形象；')
        self.assertEqual(report["status"], "needs_review")

    def test_sample_checks_heading_and_body_format_before_passing(self):
        self.source(["林舟：甲。"])
        path = self.draft("林舟：甲。")
        path.write_text(path.read_text(encoding="utf-8").replace("场景1-日-内", "场景1-晴天-内"), encoding="utf-8")
        for scope in ("sample", "partial", "full"):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, "标题"):
                review.make_report(self.work, {}, scope=scope)

    def test_only_bare_chapter_marker_is_format(self):
        self.source(["第十章", "林舟：走吧。"])
        self.draft("林舟：走吧。", end=2)
        report = review.make_report(self.work, {})
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["counts"]["format_hints"], 1)
        self.source(["第十章 林舟回家", "林舟：走吧。"])
        report = review.make_report(self.work, {})
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["counts"]["missing"], 1)

    def test_bare_chapter_words_used_as_speech_are_not_discarded(self):
        self.source(["林舟", "第十章"])
        self.draft("林舟：第十章", end=2)
        report = review.make_report(self.work, {})
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["scenes"][0]["paragraph_map"][1]["status"], "已覆盖")

    def test_sample_with_gaps_passes_locally_but_full_requires_all_source(self):
        self.source(["林舟：甲。", "林舟：乙。", "林舟：丙。"])
        self.draft("林舟：甲。")
        self.draft("林舟：丙。", number=3, start=3, end=3)
        report = review.make_report(self.work, {}, scope="sample", scene_numbers=[1, 3])
        self.assertEqual(report["status"], "sample_passed")
        self.assertFalse(report["coverage_complete"])
        with self.assertRaises(ValueError):
            review.make_report(self.work, {})
        with self.assertRaises(ValueError):
            review.make_report(self.work, {}, scope="full", scene_numbers=[1])

    def test_partial_report_cannot_authorize_assembly_even_with_changed_status(self):
        self.fixture("林舟：甲。", "林舟：甲。")
        report = review.make_report(self.work, {}, scope="partial")
        report["status"] = "passed"
        path = self.work / "partial.json"
        path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "复核报告"):
            screenplay_io.assemble(self.work, self.work / "output.txt", path)
        self.assertFalse((self.work / "output.txt").exists())

    def test_partial_cli_exits_success_without_claiming_full_pass(self):
        self.source(["林舟：甲。", "林舟：乙。"])
        self.draft("林舟：甲。")
        destination = self.work / "sample.json"
        command = [sys.executable, "-B", str(MODULE / "screenplay_review.py"), str(self.work),
                   "--scope", "sample", "--scenes", "1", "--report", str(destination)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["status"], "sample_passed")

    def test_sample_full_cache_reuse_remains_bound_to_current_inputs(self):
        self.fixture("林舟：甲。", "林舟：甲。")
        sample = review.make_report(self.work, {}, scope="sample")
        report = review.make_report(self.work, {}, previous=sample)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["counts"]["reused_scenes"], 1)
        self.draft("林舟：乙。")
        stale = review.make_report(self.work, {}, previous=report)
        self.assertEqual(stale["counts"]["reused_scenes"], 0)
        self.assertEqual(stale["status"], "needs_review")

    def test_old_algorithm_cache_is_readable_but_never_reused(self):
        report = self.fixture("林舟：甲。", "林舟：甲。")
        report["review_version"] = 6
        checked = review.make_report(self.work, {}, previous=report)
        self.assertEqual(checked["status"], "passed")
        self.assertEqual(checked["counts"]["reused_scenes"], 0)

    def test_punctuation_diagnostic_keeps_permitted_completion_nonblocking(self):
        report = self.fixture("林舟：知道了", "林舟：知道了。")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["counts"]["punctuation_diagnostics"], 1)
        report = self.fixture("林舟：不，是他。", "林舟：不是他。")
        self.assertEqual(report["counts"]["punctuation_diagnostics"], 1)
        report = self.fixture("林舟点头", "△ 林舟点头。")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["counts"]["punctuation_diagnostics"], 1)

    def test_non_character_broadcast_requires_explicit_bound_decision(self):
        report = self.fixture("电话提示音：“对不起，您拨打的电话已关机。”", "电话提示音(非人物播报)：对不起，您拨打的电话已关机。", "人物：无；")
        self.assertEqual(report["status"], "needs_review")
        self.assertGreater(report["issue_counts"].get("sound_classification", 0), 0)
        decisions = self.sound_decision(report)
        checked = review.make_report(self.work, decisions)
        self.assertEqual(checked["status"], "passed", checked)
        self.assertEqual(checked["counts"]["missing"], 0)

    def test_unquoted_non_character_broadcast_preserves_text(self):
        report = self.fixture("电话提示音：请稍候。", "电话提示音(非人物播报)：请稍候。", "人物：无；")
        checked = review.make_report(self.work, self.sound_decision(report))
        self.assertEqual(checked["status"], "passed", checked)

    def test_broadcast_classification_cannot_hide_changed_text_or_punctuation(self):
        report = self.fixture("电话提示音：“不，是他。”", "电话提示音(非人物播报)：不是他。", "人物：无；")
        checked = review.make_report(self.work, self.sound_decision(report))
        self.assertEqual(checked["status"], "needs_review")
        self.assertTrue(any("逐字" in issue for item in checked["scenes"][0]["speaker_items"] for issue in item["issue"]))

    def test_broadcast_same_text_changed_source_label_invalidates_decision(self):
        report = self.fixture("电话提示音：“请稍候。”", "电话提示音(非人物播报)：请稍候。", "人物：无；")
        decisions = self.sound_decision(report)
        self.source(["林舟：“请稍候。”"])
        checked = review.make_report(self.work, decisions)
        self.assertEqual(checked["status"], "needs_review")
        self.assertTrue(any("失效" in issue for item in checked["scenes"][0]["speaker_items"] for issue in item["issue"]))

    def test_broadcast_cannot_remain_in_cast(self):
        report = self.fixture("电话提示音：“请稍候。”", "电话提示音(非人物播报)：请稍候。", "人物：电话提示音-基础形象；")
        checked = review.make_report(self.work, self.sound_decision(report))
        self.assertEqual(checked["status"], "needs_review")
        self.assertTrue(any("不得列入人物行" in issue for item in checked["scenes"][0]["speaker_items"] for issue in item["issue"]))


if __name__ == "__main__":
    unittest.main()
