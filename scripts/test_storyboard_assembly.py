"""Behavioral checks for explicit draft assembly; no production approvals."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

import continuity
import director
import storyboard_assembly as assembly
import test_audit_director as fixtures


class StrictAssemblyTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.AuditDirectorChecks()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.state = fixture.state
        self.unit = fixture.unit([0, 9])
        self.request = {"mode": "append", "request_id": "fictional-assembly",
                        "span": [0, 9], "units": [self.unit]}

    def assemble(self):
        return assembly.assemble_batch(self.request)["units"][0]

    def test_assembled_decisions_remain_valid_under_existing_director(self):
        before = copy.deepcopy(self.request)
        self.unit.pop("duration")
        self.unit["continuity"].pop("final")
        for shot in self.unit["shots"]:
            shot["duration"] = shot["end"] - shot["start"]
            del shot["start"], shot["end"], shot["subjects"]
        compact_input = copy.deepcopy(self.request)
        assembled = self.assemble()
        self.assertEqual(self.request, compact_input)
        self.assertEqual(assembled["duration"], before["units"][0]["duration"])
        self.assertEqual(assembled["continuity"]["final"], self.unit["continuity"]["initial"])
        self.assertEqual(director.validate_unit(self.state, assembled, [0, 9]), [])
        self.assertNotIn("id", assembled)
        self.assertNotIn("staging_review", assembled)
        self.assertNotIn("audio_preflight", assembled)

    def test_stable_ids_are_allocated_once_and_author_values_are_kept(self):
        self.unit["draft_unit_id"] = "草稿单元 一"
        self.unit["shots"][0]["review_id"] = "镜头甲"
        assembled = assembly.assemble_batch(self.request)
        again = assembly.assemble_batch(assembled)
        self.assertEqual(assembled, again)
        self.assertEqual(again["units"][0]["draft_unit_id"], "草稿单元 一")
        self.assertEqual(again["units"][0]["shots"][0]["review_id"], "镜头甲")
        self.assertEqual(len({s["review_id"] for s in again["units"][0]["shots"]}), 4)

    def test_missing_visibility_is_not_inferred_from_subjects(self):
        for listed in ([], ["CHAR0001"]):
            with self.subTest(subjects=listed):
                self.unit["shots"][0]["subjects"] = listed
                self.unit["shots"][0]["continuity"]["visibility"].clear()
                with self.assertRaisesRegex(assembly.AssemblyError, r"units\[0\].shots\[0\].continuity.visibility.CHAR0001"):
                    self.assemble()

    def test_existing_strict_draft_cannot_silently_regain_deleted_identity(self):
        assembled = self.assemble()
        for field in ("draft_unit_id", "review_id"):
            with self.subTest(field=field):
                changed = copy.deepcopy(assembled)
                if field == "draft_unit_id":
                    del changed[field]
                else:
                    del changed["shots"][0][field]
                with self.assertRaisesRegex(assembly.AssemblyError, field):
                    assembly.assemble_unit(changed)

    def test_missing_basis_or_camera_or_focus_is_not_defaulted(self):
        for field in ("basis", "camera", "focus", "changes"):
            with self.subTest(field=field):
                before = copy.deepcopy(self.unit)
                shot = self.unit["shots"][0]
                if field == "basis":
                    del shot["continuity"]["visibility"]["CHAR0001"][field]
                elif field == "changes":
                    del shot["continuity"][field]
                else:
                    del shot[field]
                with self.assertRaisesRegex(assembly.AssemblyError, field):
                    self.assemble()
                self.unit.clear()
                self.unit.update(before)

    def add_entity(self, name="other", asset="CHAR0002", present=True):
        self.unit["character_ids"].append(asset)
        contract = self.unit["continuity"]
        contract["entities"][name] = {"asset_ids": [asset]}
        for field in ("initial", "final"):
            contract[field][name] = {"present": present, "asset_id": asset, "facts": {}}
        for shot in self.unit["shots"]:
            shot["continuity"]["visibility"][name] = {
                "mode": "offscreen" if present else "absent",
                "basis": "虚构测试同行者在门外，取景只覆盖室内" if present else "虚构测试同行者尚未进入本场"}

    def test_explicit_group_expands_only_named_entities_and_rejects_conflicts(self):
        self.add_entity()
        first = self.unit["shots"][0]["continuity"]
        first["visibility"] = {}
        first["visibility_groups"] = [{"entities": ["CHAR0001", "other"], "mode": "visible",
                                       "basis": "虚构测试两人分别在门口两侧，宽镜包含两人上身"}]
        self.unit["shots"][0].pop("subjects")
        assembled = self.assemble()
        self.assertNotIn("visibility_groups", assembled["shots"][0]["continuity"])
        self.assertEqual(assembled["shots"][0]["subjects"], ["CHAR0001", "CHAR0002"])
        first["visibility"]["other"] = {"mode": "offscreen", "basis": "重复且冲突"}
        with self.assertRaisesRegex(assembly.AssemblyError, "实体重复判断：other"):
            self.assemble()

    def test_group_omission_never_supplies_complement_as_offscreen(self):
        self.add_entity()
        first = self.unit["shots"][0]["continuity"]
        first["visibility"] = {}
        first["visibility_groups"] = [{"entities": ["CHAR0001"], "mode": "visible",
                                       "basis": "虚构人物在室内取景中心"}]
        with self.assertRaisesRegex(assembly.AssemblyError, "visibility.other"):
            self.assemble()

    def test_presence_visibility_contradictions_and_unreached_asset_fail(self):
        first = self.unit["shots"][0]
        first["continuity"]["visibility"]["CHAR0001"]["mode"] = "absent"
        with self.assertRaisesRegex(assembly.AssemblyError, "在场状态矛盾"):
            self.assemble()
        first["continuity"]["visibility"]["CHAR0001"]["mode"] = "visible"
        first["subjects"] = ["CHAR9999"]
        with self.assertRaisesRegex(assembly.AssemblyError, "未到达该形象"):
            self.assemble()

    def test_transient_variant_requires_actual_visible_selection_not_all_versions(self):
        contract = self.unit["continuity"]
        contract["entities"]["CHAR0001"]["asset_ids"].append("CHAR0002")
        self.unit["character_ids"].append("CHAR0002")
        contract.pop("final")
        first = self.unit["shots"][0]
        first["continuity"]["changes"] = [{"entity": "CHAR0001", "set": {"asset_id": "CHAR0002"},
                                            "evidence": [{"record": self.state["records"][0]["id"], "quote": "站起"}]}]
        for shot in self.unit["shots"][1:]:
            shot["subjects"] = ["CHAR0002"]
            shot["focus"] = ["CHAR0002"]
        first.pop("subjects")
        with self.assertRaisesRegex(assembly.AssemblyError, "须明确实际入画资产"):
            self.assemble()
        first["subjects"] = ["CHAR0002"]
        first["focus"] = ["CHAR0002"]
        assembled = self.assemble()
        self.assertEqual(assembled["shots"][0]["subjects"], ["CHAR0002"])
        self.assertEqual(assembled["continuity"]["final"]["CHAR0001"]["asset_id"], "CHAR0002")
        state = copy.deepcopy(self.state)
        state["ledger"]["characters"].append({"id": "CHAR0002", "name": "陈默-临时测试形象"})
        self.assertEqual(continuity.validate_unit(state, assembled)["final"], assembled["continuity"]["final"])

    def test_replay_derives_final_and_preserves_facts_deletion(self):
        contract = self.unit["continuity"]
        contract["initial"]["CHAR0001"]["facts"] = {"位置": "门内", "手势": "伸手"}
        contract.pop("final")
        self.unit["shots"][1]["continuity"]["changes"] = [{
            "entity": "CHAR0001", "set": {"facts": {"位置": "窗边", "手势": None}},
            "evidence": [{"record": self.state["records"][0]["id"], "quote": "看向窗外"}]}]
        assembled = self.assemble()
        self.assertEqual(assembled["continuity"]["final"]["CHAR0001"]["facts"], {"位置": "窗边"})
        self.assertEqual(self.unit["continuity"]["initial"]["CHAR0001"]["facts"]["手势"], "伸手")

    def test_given_final_or_duration_contradiction_is_not_overwritten(self):
        self.unit["continuity"]["final"]["CHAR0001"]["facts"]["位置"] = "场外"
        with self.assertRaisesRegex(assembly.AssemblyError, "continuity.final"):
            self.assemble()
        self.unit["continuity"]["final"] = copy.deepcopy(self.unit["continuity"]["initial"])
        self.unit["duration"] = 5
        with self.assertRaisesRegex(assembly.AssemblyError, "units\\[0\\].duration"):
            self.assemble()

    def test_new_ids_dont_overwrite_formal_identity_and_duplicates_are_rejected(self):
        self.unit["id"] = "U0001"
        self.assertEqual(self.assemble()["id"], "U0001")
        self.unit["shots"][0]["review_id"] = self.unit["shots"][1]["review_id"] = "same"
        with self.assertRaisesRegex(assembly.AssemblyError, "review_id"):
            self.assemble()
        del self.unit["shots"][1]["review_id"]
        self.unit["draft_unit_id"] = "same-unit"
        self.request["units"].append(copy.deepcopy(self.unit))
        with self.assertRaisesRegex(assembly.AssemblyError, "draft_unit_id"):
            self.assemble()

    def sounding(self):
        self.unit["audio"] = [{"record": "D1", "text": "先看门口。再看窗边。", "start": 0, "end": 2}]
        self.unit["audio_plan"] = [{"record": "D1", "parts": [
            {"text": "先看门口。", "start": 0, "end": 1, "pause": .1,
             "delivery": "压低声音", "transition_basis": "说话者提醒同行者留意入口"},
            {"text": "再看窗边。", "start": 1, "end": 2, "pause": .2}]}]
        for shot in self.unit["shots"]:
            shot.pop("dialogue_parts")

    def test_audio_parts_are_copied_exactly_from_the_single_plan(self):
        self.sounding()
        before = copy.deepcopy(self.unit)
        assembled = self.assemble()
        self.assertEqual(self.unit, before)
        for i in (0, 1):
            self.assertEqual(assembled["shots"][i]["dialogue_parts"],
                             [dict(self.unit["audio_plan"][0]["parts"][i], record="D1")])
        self.assertEqual(assembled["shots"][2]["dialogue_parts"], [])
        self.assertEqual(assembly.assemble_unit(assembled), assembled)

    def test_audio_crossing_shot_boundary_never_splits_or_changes_timing(self):
        self.sounding()
        self.unit["audio_plan"][0]["parts"][0]["end"] = 1.2
        with self.assertRaisesRegex(assembly.AssemblyError, "没有完整容纳该片段的镜窗"):
            self.assemble()
        self.assertEqual(self.unit["audio_plan"][0]["parts"][0]["end"], 1.2)

    def test_audio_missing_plan_and_stale_or_fabricated_parts_are_rejected(self):
        self.sounding()
        plan = self.unit.pop("audio_plan")
        with self.assertRaisesRegex(assembly.AssemblyError, "audio_plan"):
            self.assemble()
        self.unit["audio_plan"] = plan
        self.unit["shots"][0]["dialogue_parts"] = []
        with self.assertRaisesRegex(assembly.AssemblyError, "唯一audio_plan不一致"):
            self.assemble()
        self.unit["shots"][0].pop("dialogue_parts")
        self.unit["audio_plan"][0]["parts"][0]["text"] = "改写台词。"
        with self.assertRaisesRegex(assembly.AssemblyError, "逐字还原"):
            self.assemble()

    def test_cli_writes_new_file_and_never_overwrites_input_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.json", Path(directory) / "draft.json"
            source.write_text(json.dumps(self.request, ensure_ascii=False), encoding="utf-8")
            original = source.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(assembly.main(["assemble", "--input", str(source), "--output", str(output)]), 0)
                created = output.read_bytes()
                self.assertEqual(assembly.main(["assemble", "--input", str(source), "--output", str(output)]), 2)
                self.assertEqual(assembly.main(["assemble", "--input", str(source), "--output", str(source)]), 2)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(output.read_bytes(), created)
            saved = json.loads(created)
            self.assertEqual(director.validate_unit(self.state, saved["units"][0], [0, 9]), [])

    def test_cli_failure_leaves_no_partial_output(self):
        self.unit["shots"][0].pop("camera")
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.json", Path(directory) / "draft.json"
            source.write_text(json.dumps(self.request), encoding="utf-8")
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(assembly.main(["assemble", "--input", str(source), "--output", str(output)]), 2)
            self.assertFalse(output.exists())
            self.assertIn("units[0].shots[0].camera", error.getvalue())


if __name__ == "__main__":
    unittest.main()
