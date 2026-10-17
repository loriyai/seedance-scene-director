#!/usr/bin/env python3
"""Source-bound observations, real ledger import, freshness and migration regressions."""

import copy
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "modules/screenplay-standardizer/scripts"))
from test_asset_visual_design import fixture, save
import asset_ledger as ledger
import asset_migrate as migration
import scene_observations as observations
import screenplay_review as review


class SceneObservationTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "tests-scene-observations"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, _ = fixture(self.work, with_prop=False)
        self.data["characters"][0]["visual_facts"] = []
        self.record = {"observation_id": "OBS0001", "scene_number": 1, "kind": "visual_fact",
                       "tentative_subject": "陈默", "source_paragraphs": [1], "evidence": "穿灰色布衣",
                       "status": "confirmed", "basis": "原文明确写出陈默服装；已人工核对归属",
                       "target": {"category": "characters", "source_entry": "陈默-基础形象"},
                       "fact": {"field": "visual_facts", "text": "灰色布衣", "attribution": "叙述", "key": "服装"}}
        self.payload = self.bundle([self.record])
        self.review = review.make_report(self.work, {})
        self.assertEqual(self.review["status"], "passed")

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, records):
        return observations.prepare_observations(self.source, self.work, records)

    def imported(self, payload=None, data=None, checked=None):
        return observations.import_observations(data or self.data, payload or self.payload,
                                               self.source, self.work, checked or self.review)[0]

    def test_real_import_then_normal_full_ledger_validation(self):
        result = self.imported()
        self.assertEqual(result["characters"][0]["visual_facts"][0]["text"], "灰色布衣")
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, result)
        self.assertEqual(result["observations"]["imports"][0]["target_id"], "CHAR0001")
        self.assertEqual(self.data["characters"][0]["visual_facts"], [])
        self.assertEqual(result["observations"]["source_payload"], self.payload)
        self.assertNotIn("paragraph_map", result["observations"]["review_snapshot"]["scenes"][0])

    def test_legacy_ledger_is_unaffected(self):
        self.assertEqual(observations.validate_imported_observations(self.data, self.paragraphs)["status"], "not_registered")

    def test_real_initialize_imports_confirmed_facts_before_asset_ids_are_known(self):
        payload_path, review_path = self.work / "input-observations.json", self.work / "input-review.json"
        save(payload_path, self.payload)
        save(review_path, self.review)
        output = self.work / "initialized.json"
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.initialize(self.script, self.work, output, observations=payload_path, observations_review=review_path)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["characters"][0]["visual_facts"][0]["text"], "灰色布衣")
        self.assertEqual(result["observations"]["imports"][0]["target_id"], "CHAR0001")
        self.assertEqual(result["locations"][0]["identity_status"], "pending")
        self.assertFalse(result["prop_coverage_review"]["reviewed_full_script"])

    def test_location_and_scene_targets_are_explicitly_scene_local(self):
        for category, expected in (("locations", "LOC0001"), ("scenes", "SC0001")):
            record = copy.deepcopy(self.record)
            record["target"] = {"category": category, "scene_number": 1}
            result = self.imported(payload=self.bundle([record]))
            self.assertEqual(result["observations"]["imports"][0]["target_id"], expected)
            self.assertEqual(len(result["locations"]), len(self.data["locations"]))

    def test_target_must_match_actual_draft_cast(self):
        data = copy.deepcopy(self.data)
        data["characters"][0]["source_entry"] = "记者1-基础形象"
        record = copy.deepcopy(self.record)
        record["target"]["source_entry"] = "记者1-基础形象"
        with self.assertRaisesRegex(ValueError, "人物行"):
            self.imported(payload=self.bundle([record]), data=data)

    def test_partial_review_covers_only_its_scene(self):
        checked = review.make_report(self.work, {}, scope="sample", scene_numbers=[1])
        self.imported(checked=checked)
        missing = review.make_report(self.work, {}, scope="partial", scene_numbers=[2])
        with self.assertRaisesRegex(ValueError, "覆盖对应场次"):
            self.imported(checked=missing)

    def test_confirmed_fact_requires_current_review(self):
        with self.assertRaisesRegex(ValueError, "审阅报告"):
            observations.import_observations(self.data, self.payload, self.source, self.work)
        stale = copy.deepcopy(self.review)
        stale["fingerprint"]["drafts"][self.payload["scenes"][0]["file"]] = "a" * 64
        with self.assertRaisesRegex(ValueError, "过期"):
            self.imported(checked=stale)

    def test_observations_can_be_collected_before_full_review(self):
        record = copy.deepcopy(self.record)
        record.update(status="pending", requires_decision=False)
        result, report = observations.import_observations(self.data, self.bundle([record]), self.source, self.work)
        self.assertEqual(result["characters"][0]["visual_facts"], [])
        self.assertEqual(result["pending_reviews"], [])
        self.assertEqual(report["imported_fact_count"], 0)
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, result)

    def test_ordinary_candidate_does_not_block(self):
        record = copy.deepcopy(self.record)
        record.update(kind="candidate_mention", status="pending")
        record.pop("fact")
        record.pop("target")
        result = self.imported(payload=self.bundle([record]))
        self.assertEqual(result["observations"]["review_queue"][0]["status"], "pending")
        self.assertEqual(result["pending_reviews"], [])
        self.assertEqual(result["props"], [])

    def test_real_pending_has_narrow_reimport_resolution(self):
        record = copy.deepcopy(self.record)
        record.update(status="pending", requires_decision=True)
        result = self.imported(payload=self.bundle([record]))
        result["pending_reviews"].append({"kind": "其他待核", "detail": "保留"})
        self.assertEqual(len(result["pending_reviews"]), 2)
        resolved = self.imported(data=result)
        self.assertEqual(resolved["pending_reviews"], [{"kind": "其他待核", "detail": "保留"}])
        self.assertEqual(len(resolved["characters"][0]["visual_facts"]), 1)

    def test_pending_cannot_be_promoted_by_forged_receipt(self):
        record = copy.deepcopy(self.record)
        record["status"] = "pending"
        result = self.imported(payload=self.bundle([record]))
        valid = self.imported()
        result["observations"]["imports"] = valid["observations"]["imports"]
        result["characters"][0]["visual_facts"] = valid["characters"][0]["visual_facts"]
        with self.assertRaisesRegex(ValueError, "不得升格"):
            observations.validate_imported_observations(result, self.paragraphs, ledger.parse_script(self.script))

    def test_wrong_source_paragraph_and_exact_quote_rejected(self):
        for changes in ({"source_paragraphs": [2]}, {"evidence": "灰色布衫"}, {"source_paragraphs": [True]}):
            record = {**self.record, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.bundle([record])

    def test_changed_source_and_draft_invalidate(self):
        result = self.imported()
        with self.assertRaisesRegex(ValueError, "原文摘要"):
            observations.validate_imported_observations(result, [self.paragraphs[0] + "变动", self.paragraphs[1]])
        draft = self.work / "scenes/scene_001_0001-0001.txt"
        draft.write_text(draft.read_text(encoding="utf-8").replace("灰色布衣", "黑色布衣"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "逐场稿已变化"):
            observations.validate_observations(self.payload, self.source, self.work)
        save(self.script, self.script.read_text(encoding="utf-8").replace("灰色布衣", "黑色布衣"))
        with self.assertRaisesRegex(ValueError, "正文/人物行已变化"):
            observations.validate_imported_observations(result, self.paragraphs, ledger.parse_script(self.script))

    def test_missing_target_wrong_category_and_ambiguous_target(self):
        record = copy.deepcopy(self.record)
        record.pop("target")
        with self.assertRaisesRegex(ValueError, "明确目标"):
            self.bundle([record])
        ambiguous = copy.deepcopy(self.data)
        extra = copy.deepcopy(ambiguous["characters"][0])
        extra["id"] = "CHAR0002"
        ambiguous["characters"].append(extra)
        ambiguous["scenes"][0]["character_ids"].append("CHAR0002")
        with self.assertRaisesRegex(ValueError, "不唯一"):
            self.imported(data=ambiguous)
        record = copy.deepcopy(self.record)
        record["target"] = {"category": "characters", "asset_id": "LOC0001"}
        with self.assertRaisesRegex(ValueError, "类别不一致"):
            self.bundle([record])

    def test_anonymous_numbers_are_not_collapsed(self):
        data = copy.deepcopy(self.data)
        data["characters"] = []
        for number, name in enumerate(("记者1", "记者2", "保镖01", "保镖02"), 1):
            data["characters"].append({"id": f"CHAR{number:04d}", "name": name + "-基础形象",
                                       "source_entry": name + "-基础形象", "visual_facts": []})
        data["scenes"][0]["character_ids"] = [item["id"] for item in data["characters"]]
        # Selector mechanics never rewrite or normalize digits; source semantics still need review.
        for number, name in enumerate(("记者1", "记者2", "保镖01", "保镖02"), 1):
            record = copy.deepcopy(self.record)
            record["target"]["source_entry"] = name + "-基础形象"
            category, target, _ = observations._target(data, record)
            self.assertEqual((category, target["id"]), ("characters", f"CHAR{number:04d}"))

    def test_duplicate_ids_conflicting_fact_key_and_source_conflict(self):
        with self.assertRaisesRegex(ValueError, "不重复"):
            self.bundle([self.record, self.record])
        second = copy.deepcopy(self.record)
        second["observation_id"] = "OBS0002"
        second["fact"]["text"] = "另一套灰色布衣"
        with self.assertRaisesRegex(ValueError, "key 冲突"):
            self.imported(payload=self.bundle([self.record, second]))
        conflict = copy.deepcopy(self.record)
        conflict.update(kind="source_conflict", status="confirmed")
        conflict.pop("fact")
        with self.assertRaisesRegex(ValueError, "原文冲突须保持待核"):
            self.bundle([conflict])

    def test_import_is_idempotent_and_cannot_drop_or_change_imported_fact(self):
        result = self.imported()
        again = self.imported(data=result)
        self.assertEqual(result, again)
        changed = copy.deepcopy(self.record)
        changed["fact"]["text"] = "灰色布衣的新解释"
        with self.assertRaisesRegex(ValueError, "已导入观察"):
            self.imported(data=result, payload=self.bundle([changed]))
        empty = copy.deepcopy(self.payload)
        empty["observations"] = []
        with self.assertRaisesRegex(ValueError, "静默丢弃"):
            self.imported(data=result, payload=empty)

    def test_imported_fact_or_queue_tampering_rejected(self):
        result = self.imported()
        result["characters"][0]["visual_facts"] = []
        with self.assertRaisesRegex(ValueError, "变化/遗漏"):
            observations.validate_imported_observations(result, self.paragraphs)
        result = self.imported()
        result["observations"]["source_payload"]["observations"][0]["basis"] = "改写"
        with self.assertRaisesRegex(ValueError, "原观察摘要"):
            observations.validate_imported_observations(result, self.paragraphs)

    def test_explicit_redirect_preserves_raw_payload(self):
        record = copy.deepcopy(self.record)
        record["target"] = {"category": "characters", "asset_id": "CHAR0001"}
        result = self.imported(payload=self.bundle([record]))
        before = copy.deepcopy(result["observations"])
        result["characters"][0]["id"] = "CHAR0002"
        for scene in result["scenes"]:
            scene["character_ids"] = ["CHAR0002"]
        result["retired_ids"] = ["CHAR0001"]
        mapped = observations.remap_observation_targets(result, {"CHAR0001": "CHAR0002"})
        self.assertEqual(mapped["observations"]["source_payload"], before["source_payload"])
        self.assertEqual(mapped["observations"]["payload_digest"], before["payload_digest"])
        self.assertEqual(result["observations"], before)
        observations.validate_imported_observations(mapped, self.paragraphs, ledger.parse_script(self.script))
        again, _ = observations.import_observations(mapped, before["source_payload"], self.source, self.work, self.review)
        self.assertEqual(again, mapped)
        with self.assertRaisesRegex(ValueError, "循环"):
            observations.remap_observation_targets(mapped, {"CHAR0002": "CHAR0001"})

    def test_cli_check_and_import(self):
        bundle_path, review_path = self.work / "observations.json", self.work / "review.json"
        save(bundle_path, self.payload)
        save(review_path, self.review)
        save(self.work / "ledger.json", self.data)
        command = [sys.executable, "-B", str(SKILL / "modules/screenplay-standardizer/scripts/scene_observations.py")]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        args = [str(bundle_path), "--source", str(self.source), "--workdir", str(self.work), "--review", str(review_path)]
        checked = subprocess.run(command + ["check"] + args, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        output = self.work / "imported.json"
        imported = subprocess.run(command + ["import"] + args + ["--ledger", str(self.work / "ledger.json"), "--output", str(output)],
                                  capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, json.loads(output.read_text(encoding="utf-8")))

    def migrate(self, old, changed=False, changed_draft=False):
        old_path = self.work / "old-observation-ledger.json"
        save(old_path, old)
        new_work = self.work / "migrated"
        script, source, paragraphs, _, _ = fixture(new_work, with_prop=False)
        if changed:
            paragraphs[1] += "陈默站起。"
            save(source, {"paragraphs": [{"id": i, "text": text} for i, text in enumerate(paragraphs, 1)]})
            draft = new_work / "scenes/scene_002_0002-0002.txt"
            save(draft, draft.read_text(encoding="utf-8").replace(self.paragraphs[1], paragraphs[1]))
            save(script, script.read_text(encoding="utf-8").replace(self.paragraphs[1], paragraphs[1]))
        if changed_draft:
            draft = new_work / "scenes/scene_001_0001-0001.txt"
            save(draft, draft.read_text(encoding="utf-8").replace("灰色布衣，", "灰色布衣、"))
            save(script, script.read_text(encoding="utf-8").replace("灰色布衣，", "灰色布衣、"))
        result, report = migration.migrate(old_path, self.source, script, new_work,
                                           {"scenes": {"SC0001": [1], "SC0002": [2]}})
        self.assertIsNotNone(result)
        return result, new_work, script, source, paragraphs, report

    def test_real_migration_preserves_unchanged_observations_and_history(self):
        old = self.imported()
        old["observations_history"] = [{"prior": "existing archive remains exact"}]
        result, _, script, _, paragraphs, _ = self.migrate(old)
        self.assertEqual(result["observations"], old["observations"])
        self.assertEqual(result["observations_history"], old["observations_history"])
        ledger.validate(ledger.parse_script(script), paragraphs, result)

    def test_real_migration_keeps_pending_observation_resolvable(self):
        record = copy.deepcopy(self.record)
        record.update(status="pending", requires_decision=True)
        old = self.imported(payload=self.bundle([record]))
        result, new_work, script, source, paragraphs, _ = self.migrate(old)
        self.assertIn("observations", result)
        new_payload = observations.prepare_observations(source, new_work, [self.record])
        result, _ = observations.import_observations(result, new_payload, source, new_work, review.make_report(new_work, {}))
        self.assertEqual(result["pending_reviews"], [])
        ledger.validate(ledger.parse_script(script), paragraphs, result)

    def test_source_or_scene_change_archives_full_record_and_blocks(self):
        for changed, changed_draft in ((True, False), (False, True)):
            with self.subTest(source=changed, draft=changed_draft):
                old = self.imported()
                result, _, script, _, paragraphs, _ = self.migrate(old, changed, changed_draft)
                self.assertNotIn("observations", result)
                history = result["observations_history"][-1]
                self.assertEqual(history["observations"], old["observations"])
                self.assertEqual(history["history_digest"], observations.observation_history_digest(history))
                self.assertTrue(any(item["kind"] == observations.MIGRATION_PENDING_KIND for item in result["pending_reviews"]))
                with self.assertRaises(ValueError):
                    ledger.validate(ledger.parse_script(script), paragraphs, result)

    def test_changed_migration_has_explicit_narrow_reconfirmation_exit(self):
        old = self.imported()
        result, new_work, script, source, paragraphs, _ = self.migrate(old, changed_draft=True)
        history = copy.deepcopy(result["observations_history"][-1])
        result["pending_reviews"].append({"kind": "其他待核", "detail": "不能自动清除"})
        payload = observations.prepare_observations(source, new_work, [self.record])
        payload["reconciliations"] = [{"history_digest": history["history_digest"], "basis": "已重看变化后的场稿及原文",
                                      "resolutions": [{"observation_id": "OBS0001", "outcome": "reconfirmed",
                                                       "new_observation_id": "OBS0001", "basis": "标点变化未改变该服装事实，人工复核确认"}]}]
        checked = review.make_report(new_work, {})
        self.assertEqual(checked["status"], "passed")
        result, _ = observations.import_observations(result, payload, source, new_work, checked)
        self.assertEqual(result["pending_reviews"], [{"kind": "其他待核", "detail": "不能自动清除"}])
        self.assertEqual(result["observations_history"][-1]["observations"], history["observations"])
        self.assertEqual(result["observations_history"][-1]["history_digest"], history["history_digest"])
        observations.validate_imported_observations(result, paragraphs, ledger.parse_script(script))
        # Idempotent exact replay does not rewrite the archived resolution.
        again, _ = observations.import_observations(result, payload, source, new_work, checked)
        self.assertEqual(again, result)

    def test_withdrawal_cannot_leave_old_imported_fact_active(self):
        result, new_work, _, source, _, _ = self.migrate(self.imported(), changed_draft=True)
        history = result["observations_history"][-1]
        record = copy.deepcopy(self.record)
        record.update(kind="candidate_mention", status="pending")
        record.pop("fact")
        record.pop("target")
        payload = observations.prepare_observations(source, new_work, [record])
        payload["reconciliations"] = [{"history_digest": history["history_digest"], "basis": "撤回该事实",
                                      "resolutions": [{"observation_id": "OBS0001", "outcome": "withdrawn", "basis": "重新核实后撤回"}]}]
        with self.assertRaisesRegex(ValueError, "旧导入事实仍在台账"):
            observations.import_observations(result, payload, source, new_work)
        result["characters"][0]["visual_facts"] = []
        resolved, _ = observations.import_observations(result, payload, source, new_work)
        self.assertFalse(any(item["kind"] == observations.MIGRATION_PENDING_KIND for item in resolved["pending_reviews"]))

    def test_history_reconciliation_cannot_omit_or_invent_history(self):
        result, new_work, _, source, _, _ = self.migrate(self.imported(), changed_draft=True)
        payload = observations.prepare_observations(source, new_work, [self.record])
        checked = review.make_report(new_work, {})
        for history_digest in (result["observations_history"][-1]["history_digest"], "f" * 64):
            payload["reconciliations"] = [{"history_digest": history_digest, "basis": "重审", "resolutions": []}]
            with self.assertRaises(ValueError):
                observations.import_observations(result, payload, source, new_work, checked)

    def test_migration_preserves_delivery_contract_without_observations(self):
        old = copy.deepcopy(self.data)
        old["delivery_contract"] = {"test_marker": "must be retained, not replaced"}
        result, *_ = self.migrate(old)
        self.assertEqual(result["delivery_contract"], old["delivery_contract"])
        self.assertNotIn("observations", result)


if __name__ == "__main__":
    unittest.main()
