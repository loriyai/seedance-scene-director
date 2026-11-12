"""Source-bound blackouts in the installed v4 authoring and rendering paths."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import director
import storyboard_authoring_gate as gate
import storyboard_text as storyboard
from test_storyboard_text import fixture, renderable


VISIBLE = ("相机沿门边向右平移，浅景深使手指清晰、窗框在焦外；"
           "主光：窗光照亮手背，辅光：桌面反射托住指间暗部；"
           "@{CHAR0001}推开门扇。音效：门轴微响。")


class BlackoutAuthoringTest(unittest.TestCase):
    def setUp(self):
        self.state, _ = fixture()
        self.state["storyboard_authoring_policy"] = gate.POLICY
        self.set_source("△ 切黑。", "△ 阿青推开门。")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)

    def set_source(self, *lines):
        script = "\n".join(["场景1-日-内-房间", "人物：阿青-基础形象；路人-群像-常服；", *lines])
        self.state["records"], self.state["length"] = director.build_records(script, self.state["ledger"])

    @staticmethod
    def draft(first="L000003", last="L000004", transition=None):
        header = (f"## SC0001 / u01 / {first}-{last}\n人物：@{{CHAR0001}}；\n"
                  "场景：@{SET0001}；时间：白天；天气：晴；\n人物站位：@{CHAR0001}站在门边。\n")
        if transition:
            return header + f"·00秒-05秒[镜头1-推门]：（@{{CHAR0001}}的中景；转场：{transition}）" + VISIBLE + "\n"
        return (header + f"·00秒-02秒[镜头1-停顿]：（黑场[{first}-{first}]；转场：切黑）"
                "纯黑画面，无可见对象。音效：无。\n"
                "·02秒-05秒[镜头2-推门]：（@{CHAR0001}的中景；转场：硬切）" + VISIBLE + "\n")

    def report(self, draft, units, sources=None):
        trace_path = self.project / "draft.authoring.json"
        if sources is not None:
            trace = {"policy": gate.POLICY, "version": gate.VERSION,
                     "draft_sha256": hashlib.sha256(draft.encode()).hexdigest(),
                     "context_sha256": "test", "source_span": units[0]["span"],
                     "units": [{"draft_key": units[0]["draft_key"], "shots": [
                         {"shot_key": f"SC0001/u01#{index}", "source_records": ids,
                          "viewing_decision": "从原剧本指定的切点进入下一段观看。",
                          "natural_timing": "短暂停顿后读清推门动作完成的过程。"}
                         for index, ids in enumerate(sources, 1)]}],
                     "unit_organization": {"policy": gate.UNIT_BOUNDARY_POLICY, "units": [
                         {"draft_key": units[0]["draft_key"], "reason_code": "selected_range_end",
                          "reason": "推门结束处已到本次选择的来源末尾。"}]}}
            trace_path.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
        return gate.validate(self.project, self.state, draft.encode(), units, "test", trace_path)

    def test_explicit_silent_cut_passes_v4_and_exports_plain_black(self):
        draft = self.draft()
        units = storyboard.parse(draft, self.state)
        report = self.report(draft, units, [["L000003"], ["L000004"]])
        self.assertEqual(report["status"], "passed", report["errors"])
        output = storyboard.render_unit(self.state, renderable(units[0]), authoring_gate=report)
        self.assertIn("（黑场；转场：切黑）纯黑画面，无可见对象。", output)
        self.assertNotIn("L000003", output)

    def test_black_without_an_explicit_script_cut_fails(self):
        for action in ("△ 房间停电。", "△ 阿青眼前一黑。", "△ 此处不要切黑。"):
            with self.subTest(action=action):
                self.set_source(action, "△ 阿青推开门。")
                with self.assertRaises(storyboard.TextValidationError) as problem:
                    storyboard.parse(self.draft(), self.state)
                self.assertIn("BLACKOUT_SOURCE", {error["code"] for error in problem.exception.errors})

    def test_missing_or_forged_whole_shot_trace_fails(self):
        draft = self.draft()
        units = storyboard.parse(draft, self.state)
        missing = self.report(draft, units)
        self.assertIn("AUTHORING_TRACE_MISSING", {e["code"] for e in missing["errors"]})
        forged = self.report(draft, units, [["L000004"], ["L000003", "L000004"]])
        self.assertIn("AUTHORING_BLACKOUT_SOURCE", {e["code"] for e in forged["errors"]})
        changed = copy.deepcopy(units)
        changed[0]["shots"][0]["blackout_records"] = ["L000004"]
        forged = self.report(draft, changed, [["L000003"], ["L000004"]])
        self.assertIn("AUTHORING_BLACKOUT_SOURCE", {e["code"] for e in forged["errors"]})

    def test_timed_transition_checks_trace_and_displays_timing_only(self):
        draft = self.draft(transition="切黑[L000003-L000003,1.25-2.5秒]")
        units = storyboard.parse(draft, self.state)
        forged = self.report(draft, units, [["L000004"]])
        self.assertIn("AUTHORING_BLACKOUT_SOURCE", {e["code"] for e in forged["errors"]})
        report = self.report(draft, units, [["L000003", "L000004"]])
        self.assertEqual(report["status"], "passed", report["errors"])
        output = storyboard.render_unit(self.state, renderable(units[0]), authoring_gate=report)
        self.assertIn("1.25-2.5秒", output)
        self.assertNotIn("L000003", output)
        self.assertEqual(draft, units[0]["source_text"])

    def test_later_selected_voiceover_can_continue_an_actual_preceding_cut(self):
        self.set_source("△ 切黑。", "阿青(画外音)：谁？", "△ 阿青推开门。")
        draft = self.draft(first="L000004", last="L000005").replace("·02秒", (
            "声音[L000004,0.1-1.8]：@{CHAR0001}画外音：“谁？”\n·02秒"))
        records = self.state["records"]
        units = storyboard.parse(draft, self.state, [records[1]["start"], records[2]["end"]])
        self.assertEqual(units[0]["shots"][0]["blackout_records"], ["L000004"])
        self.assertEqual(storyboard.validate(self.state, units)["unit_count"], 1)

    def test_cut_later_in_same_record_cannot_authorize_clipped_earlier_action(self):
        self.set_source("△ 门缓缓合上，画面切黑。")
        draft = self.draft(last="L000003").split("·02秒", 1)[0]
        with self.assertRaises(storyboard.TextValidationError) as problem:
            storyboard.parse_incremental(draft, self.state, [0, len("门缓缓合上")])
        self.assertIn("BLACKOUT_SOURCE", {error["code"] for error in problem.exception.errors})


if __name__ == "__main__":
    unittest.main()
