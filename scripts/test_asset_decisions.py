"""Regressions for persisted review decisions and the shared delivery gate."""
import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import fixture, save, ledger, design
import asset_review as review


SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "modules/screenplay-standardizer/scripts/asset_ledger.py"


def decision_payload(data, decisions, *, chars=(), candidates=(), archive=()):
    return {"review_policy": review.POLICY, "source_digest": data["source_digest"],
            "input_ledger_digest": review.fingerprint(data),
            "scope": {"character_ids": list(chars), "candidate_ids": list(candidates), "archive_ids": list(archive)},
            "decisions": copy.deepcopy(decisions)}


class AssetDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.designs = fixture(self.work, with_prop=False)
        self.data["group_policy"] = design.GROUP_POLICY
        self.decisions = []
        text = self.script.read_text(encoding="utf-8")
        cast, dialogue = [], []
        for n in range(2, 9):
            aid, speaker = f"CHAR{n:04d}", f"集合{n}"
            item = {"id": aid, "name": speaker + "-基础形象", "source_entry": speaker + "-基础形象",
                    "aliases": [], "visual_facts": [], "personality_facts": []}
            self.data["characters"].append(item)
            self.data["scenes"][0]["character_ids"].append(aid)
            cast.append(item["source_entry"] + "；")
            evidence = speaker + "一同走入房间并齐声说好。"
            self.paragraphs[0] += evidence
            dialogue.append(speaker + "(齐声)：好。")
            self.decisions.append({"kind": "speaking_ensemble", "asset_id": aid,
                "expected_record_digest": review.fingerprint(item),
                "values": {"character_kind": "speaking_ensemble", "visual_mode": "image_asset", "source_speaker": speaker,
                    "ensemble_review": {"kind": "speaking_ensemble", "reviewed_full_script": True,
                        "source_digest": "", "identity_basis": "明确为不能完整展开的共同发声集合",
                        "speech_basis": "原文明确共同说好", "consistency_reason": "走入及发声需要固定多人轮廓"},
                    "visible_uses": [{"scene_id": "SC0001", "event_id": speaker + "共同发声",
                        "evidence": evidence, "source_paragraphs": [1]}]}})
        self.data["source_digest"] = ledger.digest(self.paragraphs)
        for decision in self.decisions:
            decision["values"]["ensemble_review"]["source_digest"] = self.data["source_digest"]
        text = text.replace("人物：陈默-基础形象；", "人物：陈默-基础形象；" + "".join(cast), 1)
        text = text.replace("场景2-", "\n".join(dialogue) + "\n\n场景2-", 1)
        save(self.script, text)
        save(self.source, {"paragraphs": [{"id": n, "text": value} for n, value in enumerate(self.paragraphs, 1)]})
        self.payload = decision_payload(self.data, self.decisions, chars=[d["asset_id"] for d in self.decisions])

    def apply(self):
        value, report = review.apply_review_decisions(self.data, self.payload)
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, value)
        return value, report

    def test_seven_decisions_persist_on_copied_records(self):
        before = copy.deepcopy(self.data)
        value, report = self.apply()
        self.assertEqual(report["checked_count"], 7)
        self.assertEqual(self.data, before)
        self.assertTrue(all(c["character_kind"] == "speaking_ensemble" for c in value["characters"][1:]))

    def test_template_computes_actual_hashes_without_claiming_review(self):
        selections = [{"kind": "speaking_ensemble", "asset_id": d["asset_id"]} for d in self.decisions]
        template = review.prepare_review_payload(self.data, selections)
        self.assertEqual(template["input_ledger_digest"], review.fingerprint(self.data))
        self.assertEqual(template["scope"]["character_ids"], self.payload["scope"]["character_ids"])
        self.assertIsNone(template["decisions"][0]["values"]["ensemble_review"])
        self.assertEqual(template["decisions"][0]["expected_record_digest"], self.decisions[0]["expected_record_digest"])
        with self.assertRaises(ValueError):
            review.apply_review_decisions(self.data, template)

    def test_one_of_seven_missing_fields_blocks_later_full_validation(self):
        value, _ = self.apply()
        del value["characters"][4]["character_kind"]
        with self.assertRaisesRegex(ValueError, "CHAR0005.character_kind"):
            ledger.validate(ledger.parse_script(self.script), self.paragraphs, value)

    def test_same_count_wrong_id_is_not_accepted(self):
        self.payload["scope"]["character_ids"][-1] = "CHAR0999"
        with self.assertRaisesRegex(ValueError, "缺少.*CHAR0999.*多余.*CHAR0008"):
            review.apply_review_decisions(self.data, self.payload)

    def test_duplicate_and_missing_decisions_are_rejected(self):
        for changed in (self.payload["decisions"] + [self.payload["decisions"][0]], self.payload["decisions"][:-1]):
            payload = copy.deepcopy(self.payload)
            payload["decisions"] = changed
            with self.assertRaises(ValueError):
                review.apply_review_decisions(self.data, payload)

    def test_changed_input_and_changed_original_record_are_rejected(self):
        changed = copy.deepcopy(self.data)
        changed["characters"][1]["aliases"] = ["临时别称"]
        with self.assertRaisesRegex(ValueError, "输入台账摘要"):
            review.apply_review_decisions(changed, self.payload)
        self.payload["input_ledger_digest"] = review.fingerprint(changed)
        with self.assertRaisesRegex(ValueError, "原值冲突"):
            review.apply_review_decisions(changed, self.payload)

    def test_arbitrary_patch_and_pending_clearing_are_not_operations(self):
        for illegal in ({"patch": []}, {"pending_reviews": []}):
            payload = copy.deepcopy(self.payload)
            payload.update(illegal)
            with self.assertRaisesRegex(ValueError, "patch"):
                review.apply_review_decisions(self.data, payload)

    def test_subsequent_batch_keeps_prior_decisions_checked(self):
        first = decision_payload(self.data, self.decisions[:3], chars=[d["asset_id"] for d in self.decisions[:3]])
        value, _ = review.apply_review_decisions(self.data, first)
        second = decision_payload(value, self.decisions[3:], chars=[d["asset_id"] for d in self.decisions[3:]])
        value, report = review.apply_review_decisions(value, second)
        self.assertEqual((report["receipt_count"], report["checked_count"]), (2, 7))
        value["characters"][1]["visual_mode"] = "prompt_only"
        with self.assertRaisesRegex(ValueError, "CHAR0002.visual_mode"):
            review.verify_review_decisions(value)

    def test_apply_cli_validates_semantics_before_any_output(self):
        self.payload["decisions"][0]["values"]["source_speaker"] = "原文不存在的集合"
        save(self.work / "ledger.json", self.data)
        save(self.work / "decisions.json", self.payload)
        output, report = self.work / "reviewed.json", self.work / "report.json"
        run = subprocess.run([sys.executable, "-B", str(CLI), "review-apply", str(self.script), str(self.source),
            str(self.work / "ledger.json"), str(self.work / "decisions.json"), "--output", str(output), "--report", str(report)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertFalse(output.exists())
        self.assertFalse(report.exists())

    def test_review_check_accepts_collective_speakers_and_rejects_wrong_label(self):
        script = ledger.parse_script(self.script)
        self.assertEqual(review.check_review_drafts(script, self.paragraphs, self.data, [self.payload])["status"], "draft_checked")
        self.payload["decisions"][0]["values"]["source_speaker"] = "另一个集合"
        with self.assertRaisesRegex(ValueError, "人物声源"):
            review.check_review_drafts(script, self.paragraphs, self.data, [self.payload])

    def test_apply_and_verify_cli_preserve_inputs(self):
        save(self.work / "ledger.json", self.data)
        save(self.work / "decisions.json", self.payload)
        inputs = {p: p.read_bytes() for p in (self.script, self.source, self.work / "ledger.json", self.work / "decisions.json")}
        output, report = self.work / "reviewed.json", self.work / "report.json"
        base = [sys.executable, "-B", str(CLI)]
        run = subprocess.run(base + ["review-apply", str(self.script), str(self.source), str(self.work / "ledger.json"),
            str(self.work / "decisions.json"), "--output", str(output), "--report", str(report)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        check = subprocess.run(base + ["review-verify", str(self.script), str(self.source), str(output), str(self.work / "decisions.json")],
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["checked_count"], 7)
        for path, before in inputs.items():
            self.assertEqual(path.read_bytes(), before)

    def test_background_and_candidate_decisions_use_fixed_fields(self):
        data = copy.deepcopy(self.data)
        item = data["characters"][1]
        item["source_entry"] = "路人-群像-基础形象"
        item["name"] = "路人-群像-基础形象-陈府"
        candidate = {"candidate_id": "CAND0001", "name": "普通桌子", "scene_ids": ["SC0001"],
                     "source_evidence": [{"scene_id": "SC0001", "evidence": "桌上", "source_paragraphs": [1]}]}
        data["prop_coverage_review"] = {"candidates": [candidate]}
        records = [{"kind": "background_group", "asset_id": item["id"], "expected_record_digest": review.fingerprint(item),
            "values": {"visual_mode": "prompt_only", "extraction_review": {"kind": "background_group", "reviewed_full_script": True,
                       "source_digest": data["source_digest"], "identity_basis": "无独立台词背景路人", "selection_basis": "prompt_only",
                       "requires_reference": False, "text_sufficiency_reason": "无需固定个体外形", "exclusion_reason": "背景作用"}, "visible_uses": []}},
            {"kind": "candidate_disposition", "candidate_id": candidate["candidate_id"], "expected_record_digest": review.fingerprint(candidate),
             "values": {"disposition": "set", "target_id": "SET0001", "requires_reference": True, "reason": "普通桌子归入现用布景"}}]
        value, report = review.apply_review_decisions(data, decision_payload(data, records, chars=[item["id"]], candidates=[candidate["candidate_id"]]))
        self.assertEqual(report["checked_count"], 2)
        self.assertEqual(value["prop_coverage_review"]["candidates"][0]["target_id"], "SET0001")


class AssetSpeakerPrecheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, _ = fixture(self.work, with_prop=False)
        self.data["group_policy"] = design.GROUP_POLICY
        self.data["characters"][0]["source_speaker"] = "陈默"
        self.paragraphs[0] += "陈默说开始。行人从门前经过。围观路人在门外驻足。"
        self.data["source_digest"] = ledger.digest(self.paragraphs)
        self.selections, entries = [], []
        for number, speaker, evidence in ((2, "行人", "行人从门前经过"), (3, "围观路人", "围观路人在门外驻足")):
            aid, entry = f"CHAR{number:04d}", f"{speaker}-群像-基础形象"
            self.data["characters"].append({"id": aid, "name": entry + "-陈府", "source_entry": entry,
                "aliases": [], "visual_facts": [], "personality_facts": [], "visual_mode": "prompt_only",
                "extraction_review": {"kind": "background_group", "reviewed_full_script": True,
                    "source_digest": self.data["source_digest"], "identity_basis": "门外无独立台词的背景人群",
                    "selection_basis": "prompt_only", "requires_reference": False,
                    "text_sufficiency_reason": "只有行走或驻足行为，无成员辨识需求", "exclusion_reason": "文字足以表达门外背景活动"},
                "visible_uses": [{"scene_id": "SC0001", "event_id": speaker + "门外活动",
                    "evidence": evidence, "source_paragraphs": [1]}]})
            self.data["scenes"][0]["character_ids"].append(aid)
            self.selections.append({"kind": "background_group", "asset_id": aid})
            entries.append(entry + "；")
        text = self.script.read_text(encoding="utf-8")
        text = text.replace("人物：陈默-基础形象；", "人物：陈默-基础形象；" + "".join(entries), 1)
        text = text.replace("场景2-", "陈默：开始。\n△ 行人从门前经过。围观路人在门外驻足。\n\n场景2-", 1)
        save(self.script, text)
        save(self.source, {"paragraphs": [{"id": n, "text": value} for n, value in enumerate(self.paragraphs, 1)]})

    def payload(self, data):
        # Author the frozen decision independently so check also covers old
        # draft files created before prepare gained this early validation.
        decisions = [{**selection, "expected_record_digest": review.fingerprint(item),
                      "values": {field: copy.deepcopy(item[field]) for field in review.CHAR_FIELDS["background_group"]}}
                     for selection, item in zip(self.selections, data["characters"][1:])]
        return decision_payload(data, decisions, chars=[item["asset_id"] for item in self.selections])

    def test_two_background_speaker_mismatches_fail_prepare_check_and_full_validation(self):
        for index, wrong_speaker in ((1, "行人"), (2, "围观路人")):
            with self.subTest(speaker=wrong_speaker):
                data = copy.deepcopy(self.data)
                data["characters"][index]["source_speaker"] = wrong_speaker
                before = copy.deepcopy(data)
                with self.assertRaisesRegex(ValueError, "人物声源"):
                    review.prepare_review_payload(data, self.selections)
                payload = self.payload(data)
                with self.assertRaisesRegex(ValueError, "人物声源"):
                    review.check_review_drafts(ledger.parse_script(self.script), self.paragraphs, data, [payload])
                applied, _ = review.apply_review_decisions(data, payload)
                with self.assertRaisesRegex(ValueError, "人物声源"):
                    ledger.validate(ledger.parse_script(self.script), self.paragraphs, applied)
                self.assertEqual(data, before)

    def test_non_speaking_groups_omit_speaker_and_regular_speaker_still_passes(self):
        before = copy.deepcopy(self.data)
        template = review.prepare_review_payload(self.data, self.selections)
        self.assertEqual(template["scope"]["character_ids"], ["CHAR0002", "CHAR0003"])
        payload = self.payload(self.data)
        report = review.check_review_drafts(ledger.parse_script(self.script), self.paragraphs, self.data, [payload])
        self.assertEqual(report["status"], "draft_checked")
        self.assertFalse(report["formal_ready"])
        applied, _ = review.apply_review_decisions(self.data, payload)
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, applied)
        self.assertEqual(self.data, before)

    def test_legacy_matching_group_speaker_remains_readable(self):
        for item in self.data["characters"][1:]:
            item["source_speaker"] = item["source_entry"].split("-基础形象")[0]
        payload = self.payload(self.data)
        review.check_review_drafts(ledger.parse_script(self.script), self.paragraphs, self.data, [payload])
        applied, _ = review.apply_review_decisions(self.data, payload)
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, applied)

    def test_related_regular_speaker_mismatch_is_caught_by_check(self):
        self.data["characters"][0]["source_speaker"] = "旁人"
        with self.assertRaisesRegex(ValueError, "人物声源"):
            review.check_review_drafts(ledger.parse_script(self.script), self.paragraphs, self.data, [self.payload(self.data)])

    def test_review_check_cli_fails_without_changing_any_input(self):
        self.data["characters"][1]["source_speaker"] = "行人"
        save(self.work / "ledger.json", self.data)
        save(self.work / "decisions.json", self.payload(self.data))
        inputs = {path: path.read_bytes() for path in (self.script, self.source, self.work / "ledger.json", self.work / "decisions.json")}
        run = subprocess.run([sys.executable, "-B", str(CLI), "review-check", str(self.script), str(self.source),
            str(self.work / "ledger.json"), str(self.work / "decisions.json")], capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("人物声源", run.stderr)
        for path, content in inputs.items():
            self.assertEqual(path.read_bytes(), content)


class AssetLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.designs = fixture(self.work, with_prop=False)

    def validate(self, data, **kwargs):
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, data, **kwargs)

    def test_inactive_props_rejected_by_validation_render_and_design(self):
        self.data["inactive_assets"]["props"] = [{"id": "PROP0009", "name": "游戏机"}]
        save(self.work / "ledger.json", self.data)
        with self.assertRaisesRegex(ValueError, "inactive_assets"):
            self.validate(self.data)
        with self.assertRaisesRegex(ValueError, "inactive_assets"):
            ledger.render(self.script, self.source, self.work / "ledger.json", self.work / "catalog.txt")
        self.assertFalse((self.work / "catalog.txt").exists())
        run = subprocess.run([sys.executable, "-B", str(CLI), "design", str(self.script), str(self.source), str(self.work / "ledger.json"),
              str(self.work / "design.json"), "--style-file", str(self.work / "video-style.txt"), "--output", str(self.work / "designed.json")],
              capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse((self.work / "designed.json").exists())

    def archive(self, *, already_retired=False):
        old = {"id": "PROP0009", "name": "游戏机", "visual_facts": []}
        self.data["inactive_assets"]["props"] = [old]
        if already_retired:
            self.data["retired_ids"] = [old["id"]]
        record = {"kind": "archive_inactive", "asset_id": old["id"], "category": "props", "expected_record_digest": review.fingerprint(old),
                  "reason": "已审阅为普通物件，无活动场次引用，不需要独立图片参考"}
        return decision_payload(self.data, [record], archive=[old["id"]])

    def test_explicit_archive_handles_previously_retired_inactive_prop(self):
        payload = self.archive(already_retired=True)
        value, _ = review.apply_review_decisions(self.data, payload)
        self.validate(value)
        self.assertEqual(value["retired_ids"], ["PROP0009"])
        self.assertEqual(value["inactive_assets"]["props"], [])
        self.assertEqual(value["asset_archive"][0]["record"]["name"], "游戏机")

    def test_archive_reason_required_and_pending_never_silently_cleared(self):
        payload = self.archive()
        payload["decisions"][0]["reason"] = ""
        with self.assertRaisesRegex(ValueError, "原因"):
            review.apply_review_decisions(self.data, payload)
        self.data["pending_reviews"] = [{"kind": "人物身份待核", "id": "CHAR0001"}]
        payload = self.archive()
        with self.assertRaisesRegex(ValueError, "pending_reviews"):
            review.apply_review_decisions(self.data, payload)

    def test_retired_id_cannot_have_live_reference(self):
        self.data["retired_ids"] = ["PROP0009"]
        self.data["scenes"][0]["prop_ids"] = ["PROP0009"]
        with self.assertRaisesRegex(ValueError, "退役ID仍被活动记录引用"):
            self.validate(self.data)

    def test_known_migration_retirement_pending_is_explicitly_archived(self):
        old = {"id": "CHAR0009", "name": "已退场人物-基础形象", "visual_facts": [], "personality_facts": []}
        pending = {"kind": "人物可能改名或退场", "asset_id": old["id"], "name": old["name"],
                   "action": "确认新人物行身份后恢复关联，或确认退场后移入 retired_ids"}
        self.data["inactive_assets"]["characters"] = [old]
        self.data["pending_reviews"] = [pending]
        record = {"kind": "archive_inactive", "asset_id": old["id"], "category": "characters",
                  "expected_record_digest": review.fingerprint(old), "reason": "经原文审阅确认已经退场且无现用引用",
                  "resolved_pending_digests": [review.fingerprint(pending)]}
        value, _ = review.apply_review_decisions(self.data, decision_payload(self.data, [record], archive=[old["id"]]))
        self.validate(value)
        self.assertEqual(value["pending_reviews"], [])
        self.assertEqual(value["asset_archive"][0]["resolved_pending_reviews"], [pending])
        other = {"kind": "资产事实证据或出现关系待核", "asset_id": old["id"], "fact": {"text": "原文待核"}}
        self.data["pending_reviews"] = [other]
        record["resolved_pending_digests"] = [review.fingerprint(other)]
        with self.assertRaisesRegex(ValueError, "只允许解决同人物ID"):
            review.apply_review_decisions(self.data, decision_payload(self.data, [record], archive=[old["id"]]))

    def test_new_delivery_requires_design_and_legacy_facts_remain_readable(self):
        self.validate(self.data)
        with self.assertRaisesRegex(ValueError, "完成design"):
            self.validate(self.data, require_designed=True)
        value = design.apply_design(self.data, self.designs, "古装写实电影")
        self.validate(value, require_designed=True)
        save(self.work / "ledger.json", self.data)
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.render(self.script, self.source, self.work / "ledger.json", self.work / "legacy.txt")
        self.assertTrue((self.work / "legacy.txt").is_file())


if __name__ == "__main__":
    unittest.main()
