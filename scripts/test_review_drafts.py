"""Selected review drafts retain complete inputs and cannot authorize delivery."""
import copy
import unittest
import test_asset_decisions as fixtures
from test_asset_decisions import decision_payload, review, ledger


class ReviewDraftTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.AssetDecisionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.data, self.decisions = fixture.data, fixture.decisions
        self.script = ledger.parse_script(fixture.script)
        self.paragraphs = fixture.paragraphs

    def payload(self, data=None, decisions=None):
        data = self.data if data is None else data
        decisions = self.decisions if decisions is None else decisions
        return decision_payload(data, decisions, chars=[item["asset_id"] for item in decisions])

    def test_unrelated_pending_and_inactive_remain_and_never_become_passed(self):
        self.data["pending_reviews"] = [{"kind": "无关道具待核", "asset_id": "PROP0099"}]
        self.data["inactive_assets"]["props"] = [{"id": "PROP0099", "name": "待核道具"}]
        before = copy.deepcopy(self.data)
        payload = self.payload()
        report = review.check_review_drafts(self.script, self.paragraphs, self.data, [payload])
        self.assertEqual(report["status"], "draft_checked")
        self.assertFalse(report["formal_ready"])
        self.assertEqual(report["full_ledger_validation"], "not_performed")
        self.assertEqual((report["remaining_pending_count"], report["remaining_inactive_count"]), (1, 1))
        self.assertEqual(self.data, before)
        with self.assertRaisesRegex(ValueError, "pending_reviews"):
            review.apply_review_batches(self.data, [payload])

    def test_related_pending_does_not_pass_as_unrelated(self):
        self.data["pending_reviews"] = [{"kind": "身份待核", "asset_id": "CHAR0002"}]
        with self.assertRaisesRegex(ValueError, "本批目标"):
            review.check_review_drafts(self.script, self.paragraphs, self.data, [self.payload()])

    def test_two_frozen_batches_check_then_merge_without_hash_refresh(self):
        batches = [self.payload(decisions=self.decisions[:3]), self.payload(decisions=self.decisions[3:])]
        original = copy.deepcopy(batches)
        report = review.check_review_drafts(self.script, self.paragraphs, self.data, batches)
        self.assertEqual([len(batch["checked_targets"]) for batch in report["batches"]], [3, 4])
        combined = review.merge_review_payloads(self.data, batches)
        self.assertEqual(combined["input_ledger_digest"], batches[0]["input_ledger_digest"])
        result, applied = review.apply_review_batches(self.data, batches)
        self.assertEqual(applied["checked_count"], 7)
        self.assertEqual(applied["receipt_count"], 2)
        review.verify_review_decisions(result, batches[0])
        review.verify_review_decisions(result, batches[1])
        ledger.validate(self.script, self.paragraphs, result)
        self.assertEqual(batches, original)

    def test_overlapping_batch_and_old_hash_rejected(self):
        first = self.payload(decisions=self.decisions[:4])
        second = self.payload(decisions=self.decisions[3:])
        with self.assertRaisesRegex(ValueError, "范围重叠"):
            review.merge_review_payloads(self.data, [first, second])
        second = self.payload(decisions=self.decisions[4:])
        second["input_ledger_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "输入台账摘要"):
            review.merge_review_payloads(self.data, [first, second])

    def test_real_source_evidence_and_speaker_are_checked(self):
        for field in ("evidence", "speaker"):
            payload = self.payload()
            if field == "evidence":
                payload["decisions"][0]["values"]["visible_uses"][0]["evidence"] = "原文没有这个共同动作"
            else:
                payload["decisions"][0]["values"]["source_speaker"] = "原文不存在的标签"
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.check_review_drafts(self.script, self.paragraphs, self.data, [payload])

    def test_actual_source_version_and_selected_scene_contract_are_checked(self):
        source = list(self.paragraphs)
        source[0] += "原文改变"
        with self.assertRaisesRegex(ValueError, "实际source"):
            review.check_review_drafts(self.script, source, self.data, [self.payload()])
        script = copy.deepcopy(self.script)
        script[0]["cast_entries"].pop()
        with self.assertRaisesRegex(ValueError, "人物槽位"):
            review.check_review_drafts(script, self.paragraphs, self.data, [self.payload()])

    def test_candidate_draft_checks_real_evidence_and_target_links(self):
        candidate = {"candidate_id": "CAND0001", "name": "桌子", "scene_ids": ["SC0001"],
                     "source_evidence": [{"scene_id": "SC0001", "evidence": "放到桌上", "source_paragraphs": [1]}]}
        self.data["prop_coverage_review"] = {"candidates": [candidate]}
        choice = {"kind": "candidate_disposition", "candidate_id": "CAND0001", "expected_record_digest": review.fingerprint(candidate),
                  "values": {"disposition": "set", "target_id": "SET0001", "requires_reference": True, "reason": "普通桌子作为房间陈设"}}
        payload = decision_payload(self.data, [choice], candidates=["CAND0001"])
        report = review.check_review_drafts(self.script, self.paragraphs, self.data, [payload])
        self.assertEqual(report["batches"][0]["checked_targets"][0]["source_evidence"], "checked")
        choice["values"]["target_id"] = "SET0999"
        with self.assertRaisesRegex(ValueError, "归类目标"):
            review.check_review_drafts(self.script, self.paragraphs, self.data, [decision_payload(self.data, [choice], candidates=["CAND0001"])])
        choice["values"]["target_id"] = "SET0001"
        self.data["prop_coverage_review"]["candidates"][0]["source_evidence"][0]["evidence"] = "原文没有的大桌"
        choice["expected_record_digest"] = review.fingerprint(candidate)
        with self.assertRaisesRegex(ValueError, "证据"):
            review.check_review_drafts(self.script, self.paragraphs, self.data, [decision_payload(self.data, [choice], candidates=["CAND0001"])])

    def test_normalize_guard_checks_old_receipt_then_affected_context(self):
        value, _ = review.apply_review_batches(self.data, [self.payload()])
        changed = copy.deepcopy(value)
        changed["characters"][1]["name"] += "-制作显示名"
        report = review.assert_normalize_review_safe(value, changed)
        self.assertIn("CHAR0002", report["name_only_ids"])
        changed["characters"][1]["visual_facts"].append({"text": "新增外貌约束"})
        with self.assertRaisesRegex(ValueError, "身份、事实或场次依赖"):
            review.assert_normalize_review_safe(value, changed)
        broken = copy.deepcopy(value)
        del broken["characters"][1]["character_kind"]
        with self.assertRaisesRegex(ValueError, "CHAR0002.character_kind"):
            review.assert_normalize_review_safe(broken, value)

    def test_normalize_guard_does_not_rewrite_or_accept_disappearing_review_id(self):
        value, _ = review.apply_review_batches(self.data, [self.payload()])
        after = copy.deepcopy(value)
        after["characters"] = after["characters"][:-1]
        before = copy.deepcopy(value)
        with self.assertRaisesRegex(ValueError, "既有审阅决定失效"):
            review.assert_normalize_review_safe(value, after)
        self.assertEqual(value, before)


if __name__ == "__main__":
    unittest.main()
