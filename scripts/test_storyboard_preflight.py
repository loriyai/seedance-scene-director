"""Early, source-preserving checks; a passing lint never substitutes shot review."""
import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import storyboard_preflight as pf


def unit(text="等我。", units=2, end=.8):
    event = {"record": "L1", "text": text, "units": units, "start": 0, "end": end,
             "pause": .34, "speed": "常速", "character_id": "CHAR1", "voice": "说"}
    return {"id": "U1", "workflow_policy": pf.LEGACY_POLICY, "scene_ids": ["SC1"], "audio": [event],
            "audio_plan": [{"record": "L1", "parts": [{"text": text, "start": 0, "end": end, "pause": .34}]}]}


def state():
    return {"ledger": {
        "characters": [{"id": "CHAR1", "name": "阿明-基础形象", "source_entry": "阿明-基础形象",
                        "source_speaker": "阿明", "aliases": ["004"]},
                       {"id": "CHAR2", "name": "阿红-基础形象", "source_speaker": "阿红", "aliases": []}],
        "scenes": [{"id": "SC1", "character_ids": ["CHAR1", "CHAR2"]}],
        "scene_assets": [{"id": "SET1", "name": "阿明家泳池"}],
        "props": [], "locations": []}}


def shot_unit(text="@{CHAR1} 站在门边。"):
    u = unit()
    parts = u.pop("audio_plan")[0]["parts"]
    u["global"] = "@{CHAR1} 站在门边。"
    u["shots"] = [{"start": 0, "end": 1, "scene_id": "SC1", "subjects": ["CHAR1"], "text": text,
                   "dialogue_parts": [dict(p, record="L1") for p in parts]}]
    u["continuity"] = {"initial": {"person": {"asset_id": "CHAR1"}}, "entities": {"person": {"asset_ids": ["CHAR1"]}}}
    return u


def codes(result):
    return {e["code"] for e in result["errors"]}


class AudioPreflightTests(unittest.TestCase):
    def test_plan_without_shots_passes_and_does_not_mutate(self):
        u = unit()
        before = copy.deepcopy(u)
        result = pf.check_audio_plan(u)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(u, before)
        self.assertEqual(result["estimates"][0]["units"], 2)

    def test_single_fragment_never_trusts_event_units(self):
        u = unit(units=20)
        self.assertIn("AUDIO_UNITS_TOTAL", codes(pf.check_audio_plan(u)))

    def test_single_fragment_digits_require_readout(self):
        u = unit("给我100元。", units=6, end=2)
        p = u["audio_plan"][0]["parts"][0]
        p.update(spoken_units=6, spoken_basis="按金额读一百元")
        self.assertIn("AUDIO_READOUT_REQUIRED", codes(pf.check_audio_plan(u)))
        p["spoken_readout"] = "给我一百元"
        self.assertIn("AUDIO_READOUT_COUNT", codes(pf.check_audio_plan(u)))
        p["spoken_units"] = 5
        u["audio"][0]["units"] = 5
        self.assertFalse(pf.check_audio_plan(u)["errors"])
        self.assertEqual(u["audio"][0]["text"], "给我100元。")

    def test_latin_text_has_explicit_syllable_count_not_letter_count(self):
        u = unit("OK。", units=2, end=.8)
        p = u["audio_plan"][0]["parts"][0]
        p.update(spoken_readout=["oh", "kay"], spoken_units=2, spoken_basis="按英语两个音节读出")
        self.assertFalse(pf.check_audio_plan(u)["errors"])
        p["spoken_readout"] = "OK"
        self.assertIn("AUDIO_READOUT", codes(pf.check_audio_plan(u)))

    def test_explicit_erhua_reading_overrides_character_count_without_editing_quote(self):
        u = unit("玩儿。", units=1, end=.6)
        p = u["audio_plan"][0]["parts"][0]
        p.update(spoken_readout=["玩儿"], spoken_units=1, spoken_basis="儿化读成一个音节")
        self.assertFalse(pf.check_audio_plan(u)["errors"])
        self.assertEqual(p["text"], "玩儿。")
        p.pop("spoken_basis")
        self.assertIn("AUDIO_READOUT_BASIS", codes(pf.check_audio_plan(u)))

    def test_fragment_unit_sum_is_hard_failure(self):
        u = unit()
        p = u["audio_plan"][0]["parts"][0]
        p.update(spoken_units=7, spoken_basis="错误手工计数")
        self.assertIn("AUDIO_READOUT_COUNT", codes(pf.check_audio_plan(u)))

    def two_parts(self):
        u = unit("等我，别走。", units=4, end=1.6)
        u["audio"][0]["pause"] = .74
        u["audio_plan"][0]["parts"] = [
            {"text": "等我，", "start": 0, "end": .64, "pause": .14},
            {"text": "别走。", "start": .9, "end": 1.6, "pause": .34}]
        return u

    def test_one_pause_ledger_counts_internal_plus_gap_once(self):
        u = self.two_parts()
        self.assertFalse(pf.check_audio_plan(u)["errors"])
        u["audio"][0]["pause"] += .1
        self.assertIn("AUDIO_PAUSE_TOTAL", codes(pf.check_audio_plan(u)))

    def test_partial_pause_registration_is_rejected(self):
        u = self.two_parts()
        del u["audio_plan"][0]["parts"][0]["pause"]
        self.assertIn("AUDIO_PART_PAUSE", codes(pf.check_audio_plan(u)))

    def test_no_speech_time_is_hard_error(self):
        u = unit()
        u["audio_plan"][0]["parts"][0]["pause"] = .8
        self.assertIn("AUDIO_NO_SPEECH_TIME", codes(pf.check_audio_plan(u)))

    def test_speed_and_long_gap_are_warnings_not_forced_retiming(self):
        u = self.two_parts()
        result = pf.check_audio_plan(u)
        self.assertFalse(result["errors"])
        self.assertIn("AUDIO_RATE", {w["code"] for w in result["warnings"]})
        self.assertIn("AUDIO_LONG_GAP", {w["code"] for w in result["warnings"]})

    def test_source_text_order_and_overlap_are_errors(self):
        u = self.two_parts()
        u["audio_plan"][0]["parts"][1].update(text="快走。", start=.5)
        cs = codes(pf.check_audio_plan(u))
        self.assertTrue({"AUDIO_SOURCE_CHANGED", "AUDIO_TIME_ORDER"} <= cs)

    def test_semantic_cut_requires_basis_but_basis_adds_no_pause(self):
        u = unit("等我回来。", units=4, end=1.3)
        u["audio_plan"][0]["parts"] = [
            {"text": "等我", "start": 0, "end": .45, "pause": 0},
            {"text": "回来。", "start": .45, "end": 1.3, "pause": .34}]
        self.assertIn("AUDIO_SEMANTIC_CUT", codes(pf.check_audio_plan(u)))
        u["audio_plan"][0]["parts"][0]["breath_basis"] = "请求与返回承诺之间的语义转换"
        self.assertFalse(pf.check_audio_plan(u)["errors"])

    def test_duplicate_plan_and_event_records_rejected(self):
        u = unit()
        u["audio_plan"] *= 2
        u["audio"] *= 2
        self.assertTrue({"AUDIO_PLAN_SCHEMA", "AUDIO_RECORDS"} <= codes(pf.check_audio_plan(u)))

    def test_completed_shots_cannot_hide_diverged_early_plan(self):
        u = shot_unit()
        u["audio_plan"] = unit()["audio_plan"]
        self.assertFalse(pf.check_audio_plan(u)["errors"])
        u["audio_plan"][0]["parts"][0]["pause"] = .2
        self.assertIn("AUDIO_PLAN_DIVERGED", codes(pf.check_audio_plan(u)))

    def test_part_must_fit_shot(self):
        u = shot_unit()
        u["shots"][0]["end"] = .5
        self.assertIn("AUDIO_SHOT_BOUNDS", codes(pf.check_audio_plan(u)))

    def test_recording_receipt_keeps_measured_timing_without_text_metadata(self):
        u = unit("OK100。", units=99, end=1.1)
        u["audio"][0].update(timing_basis="recording", recording_evidence="已有配音片段实测为一点一秒")
        u["audio_plan"][0]["parts"][0].pop("pause")
        before = copy.deepcopy(u)
        result = pf.check_audio_plan(u)
        self.assertFalse(result["errors"])
        self.assertFalse(result["estimates"])
        self.assertEqual(u, before)
        u["audio_plan"][0]["parts"][0]["text"] = "OK200。"
        self.assertIn("AUDIO_SOURCE_CHANGED", codes(pf.check_audio_plan(u)))

    def test_malformed_record_returns_diagnostics_not_traceback(self):
        u = unit()
        u["audio"][0]["record"] = []
        self.assertIn("AUDIO_RECORDS", codes(pf.check_audio_plan(u)))


class IdentityPreflightTests(unittest.TestCase):
    def test_explicit_references_pass(self):
        self.assertFalse(pf.lint_identity(state(), shot_unit())["errors"])

    def test_every_render_field_linted_and_only_known_aliases_detected(self):
        u = shot_unit()
        for field in pf.RENDER_FIELDS:
            u["shots"][0][field] = "004在门边。"
        result = pf.lint_identity(state(), u)
        self.assertEqual(len(result["candidates"]), len(pf.RENDER_FIELDS))
        self.assertFalse(result["errors"])
        self.assertTrue(all(w["resolution"] == "pending" for w in result["warnings"]))
        self.assertTrue(all(c["candidate_ids"] == ["CHAR1"] for c in result["candidates"]))
        # A naked surname or arbitrary numeral is never invented as an alias.
        u = shot_unit("韩在门边，4004并不是004的独立引用。")
        terms = [c["term"] for c in pf.lint_identity(state(), u)["candidates"]]
        self.assertEqual(terms, ["004"])

    def decision(self, u, classification="prose_mention", aid="CHAR2"):
        c = pf.lint_identity(state(), u)["candidates"][0]
        d = {k: c[k] for k in ("field", "value_sha256", "term", "start")}
        d.update(classification=classification, basis="原文说明对方尚未进入房间，只是提到其方向", asset_id=aid)
        u["identity_decisions"] = [d]
        return d

    def test_prose_acceptance_never_adds_subject_voice_or_reference(self):
        u = shot_unit("阿红尚未进屋，机位朝向门口。")
        self.decision(u)
        before = copy.deepcopy(u)
        result = pf.lint_identity(state(), u)
        self.assertFalse(result["errors"])
        self.assertEqual(u, before)
        self.assertEqual(u["shots"][0]["subjects"], ["CHAR1"])
        self.assertEqual(u["audio"][0]["character_id"], "CHAR1")

    def test_visual_alias_cannot_be_exempted_as_asset_reference(self):
        u = shot_unit("阿红抬起右手。")
        self.decision(u, classification="visual_reference")
        self.assertIn("IDENTITY_INVALID_DECISION", codes(pf.lint_identity(state(), u)))
        u["shots"][0]["text"] = "@{CHAR2} 抬起右手。"
        u.pop("identity_decisions")
        self.assertFalse(pf.lint_identity(state(), u)["errors"])

    def test_voice_classification_must_bind_actual_sound_source(self):
        u = shot_unit("阿红的电话声音来自门外。")
        self.decision(u, classification="voice_reference")
        self.assertIn("IDENTITY_INVALID_DECISION", codes(pf.lint_identity(state(), u)))
        u["audio"][0]["character_id"] = "CHAR2"
        self.assertFalse(pf.lint_identity(state(), u)["errors"])
        self.assertEqual(u["shots"][0]["subjects"], ["CHAR1"])

    def test_stale_field_hash_offset_and_unknown_decision_fields_rejected(self):
        for mutate in (lambda d: d.update(value_sha256="old"), lambda d: d.update(start=10),
                       lambda d: d.update(field="not_a_render_field"), lambda d: d.update(approved=True)):
            with self.subTest(mutate=mutate):
                u = shot_unit("阿红尚未进屋。")
                d = self.decision(u)
                mutate(d)
                self.assertTrue(pf.lint_identity(state(), u)["errors"])

    def test_book_title_location_and_exact_source_quotes_protected(self):
        s = state()
        s["records"] = [{"id": "L2", "scene": "SC1", "text": "阿红你别走。"}]
        u = shot_unit("阿明家泳池旁有《阿红传》，纸上写着“阿红你别走。”。")
        self.assertFalse(pf.lint_identity(s, u)["errors"])
        u["shots"][0]["text"] = "纸上写着“阿红已经跑开”。"
        self.assertIn("IDENTITY_REVIEW_REQUIRED", {w["code"] for w in pf.lint_identity(s, u)["warnings"]})
        u["shots"][0]["text"] = "聚焦“阿红”的手部。"
        self.assertTrue(pf.lint_identity(s, u)["candidates"])

    def test_dialogue_is_never_linted_or_rewritten(self):
        u = shot_unit()
        u["audio"][0]["text"] = "阿红你别走。"
        u["shots"][0]["dialogue_parts"][0]["text"] = "阿红你别走。"
        before = copy.deepcopy(u)
        self.assertFalse(pf.lint_identity(state(), u)["errors"])
        self.assertEqual(u, before)

    def test_current_appearance_candidates_follow_declared_changes(self):
        s = state()
        s["ledger"]["characters"].append({"id": "CHAR3", "name": "阿明-戴头套", "source_speaker": "阿明", "aliases": ["004"]})
        s["ledger"]["scenes"][0]["character_appearances"] = [{"base_asset_id": "CHAR1", "initial_asset_id": "CHAR1",
              "transitions": [{"from_asset_id": "CHAR1", "to_asset_id": "CHAR3"}]}]
        u = shot_unit("阿明站在门边。")
        u["shots"][0]["continuity"] = {"changes": [{"entity": "person", "set": {"asset_id": "CHAR3"}}]}
        u["shots"].append({"text": "阿明转过身。", "scene_id": "SC1"})
        candidates = pf.lint_identity(s, u)["candidates"]
        self.assertEqual(candidates[0]["current_asset_ids"], ["CHAR1", "CHAR3"])
        self.assertEqual(candidates[1]["current_asset_ids"], ["CHAR3"])
        self.assertTrue(all(c["candidate_ids"] == ["CHAR1", "CHAR3"] for c in candidates))

    def test_cross_scene_alias_does_not_identify_current_character(self):
        s = state()
        s["ledger"]["scenes"][0]["character_ids"] = ["CHAR1"]
        u = shot_unit("阿红尚未出现。")
        self.assertFalse(pf.lint_identity(s, u)["candidates"])

    def test_existing_full_source_entry_identity_is_used_without_surname_guess(self):
        s = state()
        s["ledger"]["characters"][0].pop("source_speaker")
        u = shot_unit("阿明在门边，韩站在外面。")
        terms = [c["term"] for c in pf.lint_identity(s, u)["candidates"]]
        self.assertEqual(terms, ["阿明"])

    def test_cross_scene_unit_uses_each_shots_own_scene_candidates(self):
        s = state()
        s["ledger"]["scenes"][0]["character_ids"] = ["CHAR1"]
        s["ledger"]["scenes"].append({"id": "SC2", "character_ids": ["CHAR2"]})
        s["ledger"]["characters"][1]["aliases"] = ["004"]
        u = shot_unit("004站在门边。")
        u["scene_ids"].append("SC2")
        c = pf.lint_identity(s, u)["candidates"][0]
        self.assertEqual(c["candidate_ids"], ["CHAR1"])


class RequestAndCliTests(unittest.TestCase):
    def test_legacy_is_unchanged_and_unknown_policy_errors(self):
        u = {"id": "old"}
        self.assertEqual(pf.preflight({}, {"units": [u]})["units"][0]["status"], "skipped")
        u["workflow_policy"] = "misspelled"
        self.assertIn("WORKFLOW_POLICY", codes(pf.preflight({}, {"units": [u]})))

    def test_source_voice_text_and_identity_checked_early(self):
        s = state()
        s["records"] = [{"id": "L1", "scene": "SC1", "text": "等我。", "voice": "OS", "speaker": "阿红"}]
        u = unit()
        result = pf.preflight(s, {"units": [u]})
        self.assertTrue({"AUDIO_SOURCE_VOICE", "AUDIO_SOURCE_IDENTITY"} <= codes(result))
        s["records"][0].update(voice="说", speaker="阿明")
        self.assertFalse(pf.preflight(s, {"units": [u]})["errors"])
        u["audio"][0]["text"] = "等你。"
        self.assertIn("AUDIO_SOURCE_CHANGED", codes(pf.preflight(s, {"units": [u]})))

    def test_validate_raises_and_warnings_do_not_block(self):
        self.assertIsInstance(pf.validate(state(), {"units": [unit(end=2)]}), list)
        with self.assertRaisesRegex(ValueError, "前置检查"):
            pf.validate(state(), {"units": [unit(units=99)]})

    def test_source_span_requires_all_dialogue_records_and_original_speaker(self):
        s = state()
        s["records"] = [{"id": "L1", "kind": "dialogue", "scene": "SC1", "start": 0, "end": 2,
                         "text": "等我。", "voice": "说", "speaker": "阿明"},
                        {"id": "L2", "kind": "dialogue", "scene": "SC1", "start": 2, "end": 4,
                         "text": "别走。", "voice": "说", "speaker": "阿明"}]
        u = unit()
        u["span"] = [0, 4]
        self.assertIn("AUDIO_SOURCE_COVERAGE", codes(pf.preflight(s, {"units": [u]})))
        u["span"] = [0, 2]
        self.assertFalse(pf.preflight(s, {"units": [u]})["errors"])

    def test_malformed_request_and_nested_fields_return_errors(self):
        for request in ([], {"units": None}, {"units": [None]}, {"units": [{"workflow_policy": pf.POLICY, "audio": None}]}):
            self.assertTrue(pf.preflight(state(), request)["errors"])

    def test_cli_plan_offline_and_project_are_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            p = path / "plan.json"
            s = path / "state.json"
            r = path / "request.json"
            p.write_text(json.dumps(unit()), encoding="utf-8")
            s.write_text(json.dumps(state()), encoding="utf-8")
            r.write_text(json.dumps({"units": [unit()]}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pf.main(["--plan", str(p)]), 0)
                self.assertEqual(pf.main(["--state", str(s), "--request", str(r)]), 0)
                with mock.patch("director.load", return_value=state()) as load:
                    self.assertEqual(pf.main(["--project", str(path), "--plan", str(p)]), 0)
                    load.assert_called_once_with(path)
                s.write_text("[]", encoding="utf-8")
                self.assertEqual(pf.main(["--state", str(s), "--plan", str(p)]), 1)
                p.write_text("[]", encoding="utf-8")
                self.assertEqual(pf.main(["--plan", str(p)]), 1)


if __name__ == "__main__":
    unittest.main()
