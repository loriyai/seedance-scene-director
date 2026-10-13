"""Offline checks of declared continuity, not generated-video quality."""
import copy
import unittest

import continuity as c


class ContinuityChecks(unittest.TestCase):
    def setUp(self):
        self.texts = ["师父站在殿内，弟子走进殿内。", "师父把信交给弟子，随后离开。", "次日弟子戴上面具站在城门。"]
        records, cursor = [], 0
        for number, text in enumerate(self.texts, 1):
            end = cursor + sum(ch.isalnum() for ch in text)
            records.append({"id": f"L{number}", "start": cursor, "end": end, "text": text, "scene": "SC1"})
            cursor = end
        self.state = {"records": records, "ledger": {
            "characters": [{"id": "CHAR1", "name": "师父-基础形象"}, {"id": "CHAR2", "name": "弟子-基础形象"},
                           {"id": "CHAR3", "name": "弟子-面具"}],
            "props": [{"id": "PROP1", "name": "信"}],
            "scenes": [{"id": "SC1", "heading": "场景1-日-内-大殿"}, {"id": "SC2", "heading": "场景2-日-外-城门"}]}}
        self.unit = self.make_unit(0, "one")

    def make_unit(self, number, name):
        record = self.state["records"][number]
        initial = {"teacher": {"present": True, "asset_id": "CHAR1", "facts": {"位置": "殿内"}}}
        return {"id": name, "span": [record["start"], record["end"]], "scene_ids": ["SC1"],
                "continuity": {"version": 1, "boundary": {"kind": "continuous"},
                               "entities": {"teacher": {"asset_ids": ["CHAR1"]}},
                               "initial": initial, "final": copy.deepcopy(initial)},
                "shots": [{"subjects": ["CHAR1"], "focus": ["CHAR1"], "continuity": {
                    "changes": [], "visibility": {"teacher": {"mode": "visible", "basis": "师父上身在画面中可见"}}}}]}

    def add_person(self, unit, present=True):
        block = unit["continuity"]
        block["entities"]["pupil"] = {"asset_ids": ["CHAR2"]}
        for key in ("initial", "final"):
            block[key]["pupil"] = {"present": present, "asset_id": "CHAR2", "facts": {}}
        for shot in unit["shots"]:
            shot["continuity"]["visibility"]["pupil"] = {
                "mode": "offscreen" if present else "absent", "basis": "弟子未进入本镜取景"}

    def evidence(self, number=0, quote=None):
        return [{"record": f"L{number + 1}", "quote": quote or self.texts[number]}]

    def test_static_state_is_replayed_without_mutation(self):
        before = copy.deepcopy(self.unit)
        result = c.validate_unit(self.state, self.unit, required=True)
        self.assertEqual(result["initial"], result["final"])
        self.assertEqual(before, self.unit)

    def test_old_unit_optional_but_new_unit_required(self):
        self.unit.pop("continuity")
        self.assertIsNone(c.validate_unit(self.state, self.unit))
        with self.assertRaisesRegex(ValueError, "须填写"):
            c.validate_unit(self.state, self.unit, required=True)

    def test_offscreen_and_occluded_people_stay_present(self):
        self.add_person(self.unit)
        for mode in ("offscreen", "occluded"):
            self.unit["shots"][0]["continuity"]["visibility"]["pupil"]["mode"] = mode
            self.assertTrue(c.validate_unit(self.state, self.unit)["final"]["pupil"]["present"])

    def test_missing_visibility_is_not_silent_inheritance(self):
        self.unit["shots"][0]["continuity"]["visibility"].clear()
        with self.assertRaisesRegex(ValueError, "恰好覆盖"):
            c.validate_unit(self.state, self.unit)

    def test_present_person_cannot_be_declared_absent_without_event(self):
        self.unit["shots"][0]["continuity"]["visibility"]["teacher"]["mode"] = "absent"
        with self.assertRaisesRegex(ValueError, "在场实体不能"):
            c.validate_unit(self.state, self.unit)

    def test_visible_asset_requires_subject_and_subject_requires_visibility(self):
        self.add_person(self.unit)
        shot = self.unit["shots"][0]
        shot["continuity"]["visibility"]["pupil"]["mode"] = "visible"
        with self.assertRaisesRegex(ValueError, "未列入subjects"):
            c.validate_unit(self.state, self.unit)
        shot["subjects"].append("CHAR2")
        c.validate_unit(self.state, self.unit)
        shot["continuity"]["visibility"]["pupil"]["mode"] = "offscreen"
        with self.assertRaisesRegex(ValueError, "没有对应可见实体"):
            c.validate_unit(self.state, self.unit)

    def test_undeclared_final_movement_rejected(self):
        self.unit["continuity"]["final"]["teacher"]["facts"]["位置"] = "门外"
        with self.assertRaisesRegex(ValueError, "重放结果"):
            c.validate_unit(self.state, self.unit)

    def test_entering_can_be_visible_in_same_shot(self):
        self.add_person(self.unit, present=False)
        shot = self.unit["shots"][0]
        shot["subjects"].append("CHAR2")
        shot["continuity"]["changes"] = [{"entity": "pupil", "set": {"present": True},
                                           "evidence": self.evidence(quote="弟子走进殿内")}]
        shot["continuity"]["visibility"]["pupil"]["mode"] = "visible"
        self.unit["continuity"]["final"]["pupil"]["present"] = True
        c.validate_unit(self.state, self.unit)

    def test_leaving_focus_need_not_be_present_at_end(self):
        unit = self.make_unit(1, "leave")
        unit["shots"][0]["continuity"]["changes"] = [{"entity": "teacher", "set": {"present": False},
                                                       "evidence": self.evidence(1, "随后离开")}]
        unit["continuity"]["final"]["teacher"]["present"] = False
        c.validate_unit(self.state, unit)

    def test_fact_change_inherits_other_facts(self):
        self.unit["shots"][0]["continuity"]["changes"] = [{"entity": "teacher", "set": {"facts": {"姿态": "站立"}},
                                                            "evidence": self.evidence(quote="师父站在殿内")}]
        self.unit["continuity"]["final"]["teacher"]["facts"]["姿态"] = "站立"
        result = c.validate_unit(self.state, self.unit)
        self.assertEqual(result["final"]["teacher"]["facts"]["位置"], "殿内")

    def test_change_requires_source_evidence(self):
        self.unit["shots"][0]["continuity"]["changes"] = [{"entity": "teacher", "set": {"present": False}, "evidence": []}]
        with self.assertRaisesRegex(ValueError, "原文证据"):
            c.validate_unit(self.state, self.unit)

    def test_evidence_cannot_borrow_later_part_of_same_record(self):
        self.unit["span"][1] = sum(ch.isalnum() for ch in "师父站在殿内")
        self.unit["shots"][0]["continuity"]["changes"] = [{"entity": "teacher", "set": {"present": False},
                                                            "evidence": self.evidence(quote="弟子走进殿内")}]
        with self.assertRaisesRegex(ValueError, "选定的原文范围"):
            c.validate_unit(self.state, self.unit)

    def test_local_source_entity_does_not_require_image_asset(self):
        block = self.unit["continuity"]
        block["entities"]["doorway"] = {"asset_ids": [], "label": "殿内空间", "evidence": self.evidence(quote="殿内")}
        for key in ("initial", "final"):
            block[key]["doorway"] = {"present": True, "asset_id": None, "facts": {}}
        self.unit["shots"][0]["continuity"]["visibility"]["doorway"] = {"mode": "visible", "basis": "既有殿内空间可见"}
        c.validate_unit(self.state, self.unit)

    def test_source_local_entity_cannot_be_invented(self):
        block = self.unit["continuity"]
        block["entities"]["crowd"] = {"asset_ids": [], "label": "百人军队", "evidence": self.evidence(quote="百人军队")}
        with self.assertRaisesRegex(ValueError, "已到达的原文范围"):
            c.validate_unit(self.state, self.unit)

    def test_registered_asset_cannot_be_relabelled_as_local_entity(self):
        self.unit["continuity"]["entities"]["teacher"] = {
            "asset_ids": [], "label": "师父-基础形象", "evidence": self.evidence(quote="师父")}
        with self.assertRaisesRegex(ValueError, "不能改成"):
            c.validate_unit(self.state, self.unit)

    def test_local_entity_can_inherit_existence_evidence_from_prior_unit(self):
        second = self.make_unit(1, "two")
        for unit in (self.unit, second):
            block = unit["continuity"]
            block["entities"]["hall"] = {"asset_ids": [], "label": "殿内空间", "evidence": self.evidence(quote="殿内")}
            for key in ("initial", "final"):
                block[key]["hall"] = {"present": True, "asset_id": None, "facts": {}}
            unit["shots"][0]["continuity"]["visibility"]["hall"] = {"mode": "visible", "basis": "原有殿内空间仍可见"}
        self.assertEqual(c.validate_chain(self.state, [self.unit, second], [0, 1]), [])

    def test_local_entity_cannot_borrow_existence_from_future_source(self):
        self.unit["continuity"]["entities"]["gate"] = {
            "asset_ids": [], "label": "城门", "evidence": self.evidence(2, "城门")}
        with self.assertRaisesRegex(ValueError, "已到达的原文范围"):
            c.validate_unit(self.state, self.unit)

    def test_image_variant_change_keeps_stable_entity(self):
        unit = self.make_unit(2, "mask")
        unit["continuity"]["entities"] = {"pupil": {"asset_ids": ["CHAR2", "CHAR3"]}}
        unit["continuity"]["initial"] = {"pupil": {"present": True, "asset_id": "CHAR2", "facts": {}}}
        unit["continuity"]["final"] = {"pupil": {"present": True, "asset_id": "CHAR3", "facts": {}}}
        unit["shots"] = [{"subjects": ["CHAR3"], "focus": ["CHAR3"], "continuity": {
            "changes": [{"entity": "pupil", "set": {"asset_id": "CHAR3"}, "evidence": self.evidence(2, "戴上面具")}],
            "visibility": {"pupil": {"mode": "visible", "basis": "戴好面具后的形象可见"}}}}]
        c.validate_unit(self.state, unit)

    def test_continuous_boundary_compares_facts(self):
        second = self.make_unit(1, "two")
        second["continuity"]["initial"]["teacher"]["facts"]["位置"] = "门外"
        second["continuity"]["final"] = copy.deepcopy(second["continuity"]["initial"])
        with self.assertRaisesRegex(ValueError, "承接状态不一致"):
            c.validate_chain(self.state, [self.unit, second], [1])

    def test_new_fact_scope_warns_without_inventing_change(self):
        second = self.make_unit(1, "two")
        for key in ("initial", "final"):
            second["continuity"][key]["teacher"]["facts"]["朝向"] = "面对弟子"
        warnings = c.validate_chain(self.state, [self.unit, second], [1])
        self.assertEqual(warnings[0]["kind"], "新纳入追踪事实需核对既有状态")
        self.assertEqual(warnings[0]["facts"], ["朝向"])

    def test_known_fact_cannot_be_dropped_at_boundary(self):
        second = self.make_unit(1, "two")
        for key in ("initial", "final"):
            second["continuity"][key]["teacher"]["facts"].clear()
        with self.assertRaisesRegex(ValueError, "承接状态不一致"):
            c.validate_chain(self.state, [self.unit, second], [1])

    def test_already_absent_entity_can_stop_being_tracked(self):
        self.add_person(self.unit, present=False)
        self.assertEqual(c.validate_chain(self.state, [self.unit, self.make_unit(1, "two")], [1]), [])

    def test_active_asset_cannot_change_at_boundary_without_event(self):
        second = self.make_unit(1, "two")
        second["continuity"]["entities"]["teacher"]["asset_ids"].append("CHAR2")
        for key in ("initial", "final"):
            second["continuity"][key]["teacher"]["asset_id"] = "CHAR2"
        second["shots"][0]["subjects"] = ["CHAR2"]
        second["shots"][0]["focus"] = ["CHAR2"]
        with self.assertRaisesRegex(ValueError, "承接状态不一致"):
            c.validate_chain(self.state, [self.unit, second], [1])

    def test_continuous_boundary_rejects_dropped_present_entity(self):
        self.add_person(self.unit)
        with self.assertRaisesRegex(ValueError, "遗漏仍在场"):
            c.validate_chain(self.state, [self.unit, self.make_unit(1, "two")], [1])

    def test_known_absent_cannot_become_present_without_change(self):
        self.add_person(self.unit, present=False)
        second = self.make_unit(1, "two")
        self.add_person(second, present=True)
        with self.assertRaisesRegex(ValueError, "承接状态不一致"):
            c.validate_chain(self.state, [self.unit, second], [1])

    def test_newly_tracked_person_prompts_review_not_invented_arrival(self):
        second = self.make_unit(1, "two")
        self.add_person(second)
        warnings = c.validate_chain(self.state, [self.unit, second], [1])
        self.assertEqual(warnings[0]["kind"], "新纳入追踪实体需核对既有状态")

    def test_real_reset_allows_changed_context_with_review(self):
        second = self.make_unit(1, "two")
        second["scene_ids"] = ["SC2"]
        second["continuity"]["initial"]["teacher"]["facts"]["位置"] = "城门"
        second["continuity"]["final"] = copy.deepcopy(second["continuity"]["initial"])
        second["continuity"]["boundary"] = {"kind": "reset", "reason": "原文标题转到城门", "evidence": [
            {"scene": "SC2", "quote": "场景2-日-外-城门"}]}
        self.assertEqual(c.validate_unit(self.state, second)["warnings"][0]["kind"], "连续性重置需人工核实")
        self.assertEqual(c.validate_chain(self.state, [self.unit, second], [1]), [])

    def test_reset_cannot_be_empty_escape_hatch(self):
        self.unit["continuity"]["boundary"] = {"kind": "reset", "reason": "硬切", "evidence": []}
        with self.assertRaisesRegex(ValueError, "原文证据"):
            c.validate_unit(self.state, self.unit)

    def test_replacement_checks_untouched_successor(self):
        second = self.make_unit(1, "two")
        self.unit["shots"][0]["continuity"]["changes"] = [{"entity": "teacher", "set": {"facts": {"姿态": "站立"}},
                                                            "evidence": self.evidence(quote="师父站在殿内")}]
        self.unit["continuity"]["final"]["teacher"]["facts"]["姿态"] = "站立"
        with self.assertRaisesRegex(ValueError, "承接状态不一致"):
            c.validate_chain(self.state, [self.unit, second], [0])

    def test_legacy_neighbour_warns_without_migration_or_revalidation(self):
        old = self.make_unit(1, "old")
        old.pop("continuity")
        old["shots"] = []
        original = copy.deepcopy(old)
        warnings = c.validate_chain(self.state, [self.unit, old], [0])
        self.assertEqual(warnings[0]["kind"], "旧单元连续性边界需人工核对")
        self.assertEqual(old, original)

    def test_unrelated_legacy_links_are_not_revalidated(self):
        unrelated = self.make_unit(2, "unrelated")
        unrelated.pop("continuity")
        self.assertEqual(c.validate_chain(self.state, [self.unit, unrelated], [0]), [])

    def test_backwards_append_checks_successor_stored_earlier(self):
        successor = self.make_unit(1, "existing-later-source")
        successor["continuity"]["initial"]["teacher"]["facts"]["位置"] = "门外"
        successor["continuity"]["final"] = copy.deepcopy(successor["continuity"]["initial"])
        with self.assertRaisesRegex(ValueError, "承接状态不一致"):
            c.validate_chain(self.state, [successor, self.unit], [1])

    def test_ambiguous_predecessors_warn_instead_of_selecting_arbitrarily(self):
        alternative = copy.deepcopy(self.unit)
        alternative["id"] = "alternative"
        successor = self.make_unit(1, "two")
        warnings = c.validate_chain(self.state, [self.unit, alternative, successor], [2])
        self.assertEqual(warnings[0]["kind"], "连续性前序候选不唯一需核对")

    def test_explicit_predecessor_resolves_alternative_without_comparing_other(self):
        alternative = copy.deepcopy(self.unit)
        alternative["id"] = "alternative"
        alternative["continuity"]["final"]["teacher"]["facts"]["位置"] = "不相干版本"
        successor = self.make_unit(1, "two")
        successor["predecessor"] = "one"
        self.assertEqual(c.validate_chain(self.state, [self.unit, alternative, successor], [2]), [])

    def test_malformed_untouched_legacy_state_warns_without_crashing(self):
        old = self.make_unit(1, "old")
        old["continuity"] = {"version": 1, "boundary": {"kind": "continuous"}, "initial": None}
        warnings = c.validate_chain(self.state, [self.unit, old], [0])
        self.assertEqual(warnings[0]["kind"], "旧单元连续性边界需人工核对")


if __name__ == "__main__":
    unittest.main()
