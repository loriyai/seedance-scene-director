"""Real review receipts survive migration only while their recorded effects hold."""
import copy
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import fixture, save, ledger
import asset_migrate as migration
import asset_review as review

SKILL = Path(__file__).resolve().parent.parent


class ReviewMigrationTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "tests-review-migration"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, data, _ = fixture(self.work, with_prop=False)
        evidence = "众人一同走入房间并齐声说好。"
        self.paragraphs[0] += evidence
        data["source_digest"] = ledger.digest(self.paragraphs)
        character = {"id": "CHAR0002", "name": "众人-基础形象", "source_entry": "众人-基础形象",
                     "aliases": [], "visual_facts": [], "personality_facts": []}
        data["characters"].append(character)
        data["scenes"][0]["character_ids"].append("CHAR0002")
        old_record = {"id": "PROP0042", "name": "普通碗", "aliases": [], "visual_facts": []}
        data["inactive_assets"]["props"] = [old_record]
        values = {"character_kind": "speaking_ensemble", "visual_mode": "image_asset", "source_speaker": "众人",
                  "ensemble_review": {"kind": "speaking_ensemble", "reviewed_full_script": True,
                    "source_digest": data["source_digest"], "identity_basis": "明确共同发声且成员不能完整展开",
                    "speech_basis": "原文明确齐声说好", "consistency_reason": "走入和齐声需要稳定多人外观"},
                  "visible_uses": [{"scene_id": "SC0001", "event_id": "众人入内齐声", "evidence": evidence, "source_paragraphs": [1]}]}
        payload = {"review_policy": review.POLICY, "source_digest": data["source_digest"],
                   "input_ledger_digest": review.fingerprint(data),
                   "scope": {"character_ids": ["CHAR0002"], "candidate_ids": [], "archive_ids": ["PROP0042"]},
                   "decisions": [{"kind": "speaking_ensemble", "asset_id": "CHAR0002",
                                  "expected_record_digest": review.fingerprint(character), "values": values},
                                 {"kind": "archive_inactive", "category": "props", "asset_id": "PROP0042",
                                  "expected_record_digest": review.fingerprint(old_record), "reason": "普通餐具无固定参考需要，明确排除"}]}
        self.data, _ = review.apply_review_decisions(data, payload)
        self.data["asset_decision_history"] = [{"reason": "较早审阅历史", "asset_decision_receipts": []}]
        save(self.source, {"paragraphs": [{"id": n, "text": text} for n, text in enumerate(self.paragraphs, 1)]})
        draft = self.work / "scenes/scene_001_0001-0001.txt"
        save(draft, draft.read_text(encoding="utf-8").replace("人物：陈默-基础形象；", "人物：陈默-基础形象；众人-基础形象；") + "众人(齐声)：好。\n")
        save(self.script, "\n".join(path.read_text(encoding="utf-8") for path in sorted((self.work / "scenes").glob("*.txt"))))
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, self.data)
        self.old = self.work / "old-ledger.json"
        save(self.old, self.data)
        self.old_source = self.work / "old-source.json"
        save(self.old_source, self.source.read_text(encoding="utf-8"))

    def migrate(self, mapping=None):
        return migration.migrate(self.old, self.old_source, self.script, self.work, mapping or {})

    def test_unchanged_valid_receipts_and_all_history_are_preserved(self):
        result, _ = self.migrate()
        self.assertEqual(result["asset_decision_receipts"], self.data["asset_decision_receipts"])
        self.assertEqual(result["asset_archive"], self.data["asset_archive"])
        self.assertEqual(result["asset_decision_history"], self.data["asset_decision_history"])
        self.assertEqual(review.verify_review_decisions(result)["status"], "matched")
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, result)

    def test_changed_source_moves_intact_receipts_to_history_and_blocks_delivery(self):
        new_paragraphs = list(self.paragraphs)
        new_paragraphs[1] += "他停了一下。"
        save(self.source, {"paragraphs": [{"id": n, "text": text} for n, text in enumerate(new_paragraphs, 1)]})
        result, _ = self.migrate({"scenes": {"SC0001": 1, "SC0002": 2}})
        self.assertEqual(result["asset_decision_receipts"], [])
        self.assertEqual(result["asset_decision_history"][:-1], self.data["asset_decision_history"])
        history = result["asset_decision_history"][-1]
        self.assertEqual(history["asset_decision_receipts"], self.data["asset_decision_receipts"])
        pending = next(item for item in result["pending_reviews"] if item["kind"] == "资产审阅决定待复核")
        self.assertEqual(pending["history_digest"], review.fingerprint(history))
        self.assertIn("人工流程", pending["action"])
        self.assertEqual(result["asset_archive"], self.data["asset_archive"])
        with self.assertRaises(ValueError):
            ledger.validate(ledger.parse_script(self.script), new_paragraphs, result)

    def test_same_source_changed_reviewed_field_cannot_reuse_receipt(self):
        text = self.script.read_text(encoding="utf-8").replace("众人", "群众")
        save(self.script, text)
        path = self.work / "scenes/scene_001_0001-0001.txt"
        save(path, path.read_text(encoding="utf-8").replace("众人", "群众"))
        result, _ = self.migrate({"characters": {"CHAR0002": "群众-基础形象"}})
        self.assertEqual(result["source_digest"], self.data["source_digest"])
        self.assertEqual(result["asset_decision_receipts"], [])
        self.assertEqual(result["asset_decision_history"][-1]["asset_decision_receipts"], self.data["asset_decision_receipts"])
        self.assertTrue(any(item["kind"] == "资产审阅决定待复核" for item in result["pending_reviews"]))

    def test_old_ledger_without_new_fields_stays_compatible(self):
        legacy = copy.deepcopy(self.data)
        for key in ("asset_archive", "asset_decision_receipts", "asset_decision_history"):
            legacy.pop(key)
        save(self.old, legacy)
        result, _ = self.migrate()
        self.assertNotIn("asset_archive", result)
        self.assertNotIn("asset_decision_receipts", result)
        self.assertNotIn("asset_decision_history", result)
        self.assertFalse(any(item["kind"] == "资产审阅决定待复核" for item in result["pending_reviews"]))

    def test_all_inactive_categories_reserve_ids_for_new_scene_and_location(self):
        old = copy.deepcopy(self.data)
        old["inactive_assets"].update(scenes=[{"id": "SC0010"}], locations=[{"id": "LOC0010"}],
                                      scene_assets=[{"id": "SET0010"}], props=[{"id": "PROP0010"}])
        save(self.old, old)
        new_paragraphs = self.paragraphs + ["陈默走进街道。"]
        save(self.source, {"paragraphs": [{"id": n, "text": text} for n, text in enumerate(new_paragraphs, 1)]})
        new_scene = "场景3-日-外-街道\n人物：陈默-基础形象；\n△ 陈默走进街道。\n"
        save(self.work / "scenes/scene_003_0003-0003.txt", new_scene)
        save(self.script, self.script.read_text(encoding="utf-8") + "\n" + new_scene)
        result, _ = self.migrate()
        self.assertEqual(result["scenes"][-1]["id"], "SC0011")
        self.assertEqual(result["scenes"][-1]["location_id"], "LOC0011")
        self.assertEqual(result["inactive_assets"], old["inactive_assets"])


if __name__ == "__main__":
    unittest.main()
