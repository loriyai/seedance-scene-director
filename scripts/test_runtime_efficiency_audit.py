"""Offline regressions for dependency-scoped handoff and queue verification reuse."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import agent_handoff as handoff
import canvas_jobs as jobs
import recovery_status
import test_agent_handoff as handoff_fixtures
import test_canvas_jobs as queue_fixtures


class HandoffEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.fx = handoff_fixtures.HandoffTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.session_path = self.fx.project / "session.json"
        self.session = {
            "schema_version": 1, "phase": "range", "revision": 5,
            "next_numbers": {"SC001": 3}, "style_hash": "style",
            "files": {"source": {"path": str(self.fx.source), "sha256": "bound-source"}},
            "ledger": {"characters": [{"id": "CHAR1", "name": "甲"}]},
            "units": [{"id": "A01", "state_out": "门关着"}],
            "ranges": [{"span": [0, 10], "confirmed": True}],
            "receipts": [{"request_id": "prior", "request_hash": "reviewed"}],
            "entry": {"min": 4},
        }
        self.fx.write_json(self.session_path, self.session)
        _, self.path, self.manifest = self.fx.prepare()
        self.fx.artifact(self.manifest)

    def test_interaction_and_allocation_metadata_do_not_discard_creative_work(self):
        self.session.update(phase="image_choice", revision=6, next_numbers={"SC001": 4})
        self.fx.write_json(self.session_path, self.session)
        self.assertEqual(handoff.verify(str(self.path))["result_status"], "draft_complete")
        record = next(row for row in self.manifest["inputs"] if row["path"] == str(self.session_path))
        self.assertEqual(record["fingerprint_policy"], "session-context-v1")

    def test_sources_style_history_neighbors_and_unknown_context_still_invalidate(self):
        changes = {
            "files": {}, "style_hash": "new-style", "ledger": {},
            "units": [{"id": "A01", "state_out": "门打开"}],
            "ranges": [], "receipts": [], "entry": {"min": 8},
            "new_context": "必须保留的新规则",
        }
        for key, value in changes.items():
            with self.subTest(field=key):
                self.fx.write_json(self.session_path, {**self.session, key: value})
                with self.assertRaisesRegex(ValueError, "输入已改变"):
                    handoff.verify(str(self.path))
        self.fx.write_json(self.session_path, self.session)
        self.fx.brief["authorization"] = "扩大操作范围"
        self.fx.save_brief()
        with self.assertRaisesRegex(ValueError, "输入已改变"):
            handoff.verify(str(self.path))

    def test_old_handoffs_keep_byte_level_validation(self):
        record = next(row for row in self.manifest["inputs"] if row["path"] == str(self.session_path))
        record.pop("fingerprint_policy")
        record["sha256"] = handoff.digest(self.session_path)
        self.fx.write_json(self.path, self.manifest)
        self.session["phase"] = "followup"
        self.fx.write_json(self.session_path, self.session)
        with self.assertRaisesRegex(ValueError, "输入已改变"):
            handoff.verify(str(self.path))

    def test_incomplete_result_can_report_missing_inputs_without_fake_draft(self):
        output = Path(self.manifest["output_dir"])
        (output / "候选/分镜.txt").unlink()
        for status in ("needs_input", "failed"):
            self.fx.write_json(output / "result.json", {
                "status": status, "scope": self.manifest["brief"]["scope"],
                "artifacts": ["result.json"], "issues": ["缺少邻接状态"],
            })
            result = handoff.verify(str(self.path))
            self.assertEqual(result["result_status"], status)
            self.assertEqual(set(result["artifact_sha256"]), {"result.json"})
            self.assertFalse(result["submitted"])
        report = json.loads((output / "result.json").read_text(encoding="utf-8"))
        self.fx.write_json(output / "result.json", {**report, "status": "draft_complete"})
        with self.assertRaisesRegex(ValueError, "产物缺失"):
            handoff.verify(str(self.path))


class QueueEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.fx = queue_fixtures.CanvasJobChecks()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def guard(self, kind="assets", **options):
        queued = self.fx.enqueue(kind, **options)
        return jobs.JobInputGuard(self.fx.project, self.fx.read_manifest(queued))

    def test_repeated_control_checks_reuse_hashes_but_boundaries_recheck(self):
        guard = self.guard()
        with patch.object(jobs, "validate_job", wraps=jobs.validate_job) as validate:
            for _ in range(20):
                guard.check()
            self.assertEqual(validate.call_count, 1)
            guard.check(force=True)
            self.assertEqual(validate.call_count, 2)

    def test_phase_change_revalidates_once_without_invalidating_job(self):
        guard = self.guard()
        guard.check()
        self.fx.session.update(phase="nodes_choice", revision=1)
        self.fx.save_session()
        with patch.object(jobs, "validate_job", wraps=jobs.validate_job) as validate:
            guard.check()
            guard.check()
            self.assertEqual(validate.call_count, 1)

    def test_changed_source_and_added_scene_stop_before_more_work(self):
        guard = self.guard()
        guard.check()
        (self.fx.project / "script.txt").write_text("改过的剧情", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已变化"):
            guard.check()
        # Restore the bound source, then show a newly added scene is detected too.
        (self.fx.project / "script.txt").write_text("虚构测试剧本\n", encoding="utf-8")
        guard.check()
        scenes = self.fx.project / "scenes"
        scenes.mkdir()
        (scenes / "new.txt").write_text("未经复核的新场次", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "逐场稿已变化"):
            guard.check()

    def test_image_directory_is_not_rehashed_at_every_control_check(self):
        folder = self.fx.project / "images"
        folder.mkdir()
        image = folder / "陈默-基础形象.png"
        image.write_bytes(b"fixture-before")
        guard = self.guard("images", folder=folder)
        with patch.object(jobs, "image_inventory", wraps=jobs.image_inventory) as inventory:
            for _ in range(20):
                guard.check()
            self.assertEqual(inventory.call_count, 1)
            image.write_bytes(b"fixture-after-change")
            with self.assertRaisesRegex(ValueError, "待上传图片已变化"):
                guard.check()
            self.assertEqual(inventory.call_count, 2)

    def test_full_boundary_hash_does_not_trust_unchanged_metadata(self):
        guard = self.guard()
        with patch.object(jobs, "file_stamp", return_value=("unchanged-metadata",)):
            guard.check()
            (self.fx.project / "script.txt").write_text("元数据相同但内容变更", encoding="utf-8")
            guard.check()
            with self.assertRaisesRegex(ValueError, "已变化"):
                guard.check(force=True)

    def test_input_change_during_full_check_is_not_cached_as_valid(self):
        guard = self.guard()
        original = jobs.validate_job
        def change_after_validation(project, job):
            result = original(project, job)
            (project / "script.txt").write_text("校验结束瞬间变化", encoding="utf-8")
            return result
        with patch.object(jobs, "validate_job", side_effect=change_after_validation):
            with self.assertRaisesRegex(ValueError, "校验期间变化"):
                guard.check()
        self.assertIsNone(guard.stamps)

    def test_recovery_reuses_one_folder_observation_within_each_status_call(self):
        folder = self.fx.project / "images"
        folder.mkdir()
        (folder / "陈默-基础形象.png").write_bytes(b"fixture")
        self.fx.enqueue("images", folder=folder)
        self.fx.enqueue("images", folder=folder, decisions={"CHAR0001": {"skip": True}})
        queue = jobs.status(self.fx.project)
        with patch.object(jobs, "image_inventory", wraps=jobs.image_inventory) as inventory, \
             patch.object(jobs, "input_scope", wraps=jobs.input_scope) as scope:
            view = recovery_status.summarize(self.fx.project, self.fx.session, queue)
            self.assertEqual(len(view["jobs"]), 2)
            self.assertEqual(inventory.call_count, 1)
            self.assertEqual(scope.call_count, 1)
            recovery_status.summarize(self.fx.project, self.fx.session, queue)
            self.assertEqual(inventory.call_count, 2)


if __name__ == "__main__":
    unittest.main()
