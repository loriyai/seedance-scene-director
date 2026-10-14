"""Local boundary tests; no model, CLI, canvas or application writes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_handoff as handoff


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.skill, self.project = root / "skill", root / "作品"
        (self.skill / "agents").mkdir(parents=True)
        (self.skill / "references").mkdir()
        self.project.mkdir()
        for name in ("SKILL.md", "references/model-routing.md", "references/stage.md"):
            (self.skill / name).write_text("规则", encoding="utf-8")
        self.config = {"schema_version": 1,
                       "profiles": {"creative": {"model": "gpt-6.1-sol", "reasoning_effort": "high"},
                                    "operations": {"model": "gpt-6.1-sol", "reasoning_effort": "medium"},
                                    "specialist": {"model": "gpt-6-astra", "reasoning_effort": "high"}},
                       "stages": {stage: {"profile": "operations" if stage == "sync" else "creative",
                                          "references": ["references/stage.md"]} for stage in handoff.STAGES},
                       "escalation": {"profile": "specialist", "max_attempts": 1,
                                      "eligible_stages": sorted(handoff.STAGES - {"sync"})}}
        self.save_config()
        self.source = self.project / "原文.txt"
        self.source.write_text("两座坟前，人物静立。", encoding="utf-8")
        self.brief_path = self.project / "任务.json"
        self.brief = {"objective": "生成候选分镜", "scope": {"scene": "SC001", "task_key": "分镜-SC001"},
                      "authorization": "仅本地候选产物", "inputs": [str(self.source)],
                      "expected_outputs": ["候选/分镜.txt", "result.json"]}
        self.save_brief()
        self.patch = patch.object(handoff, "SKILL_ROOT", self.skill)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def save_config(self):
        self.write_json(self.skill / "agents/model-routing.json", self.config)

    def save_brief(self):
        self.write_json(self.brief_path, self.brief)

    def prepare(self, stage="storyboard", reason=None):
        result = handoff.prepare(str(self.project), stage, str(self.brief_path), reason)
        manifest_path = Path(result["handoff"])
        return result, manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))

    def artifact(self, manifest, text="候选分镜"):
        path = Path(manifest["output_dir"]) / "候选/分镜.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        result = {"status": "draft_complete", "scope": manifest["brief"]["scope"],
                  "artifacts": ["候选/分镜.txt", "result.json"]}
        self.write_json(Path(manifest["output_dir"]) / "result.json", result)

    def test_unicode_envelope_auto_inputs_and_nonsemantic_success(self):
        for name in ("session.json", "video-style.txt"):
            (self.project / name).write_text("内容", encoding="utf-8")
        result, path, manifest = self.prepare()
        self.assertEqual(result["fork_turns"], "none")
        self.assertIn("不再分派或创建子代理", result["task"])
        self.assertIsNone(manifest["observed_model"])
        self.assertEqual(manifest["brief"], self.brief)
        inputs = {Path(item["path"]).name for item in manifest["inputs"]}
        self.assertTrue({"session.json", "video-style.txt", "任务.json", "SKILL.md", "model-routing.json", "stage.md"} <= inputs)
        self.artifact(manifest)
        verified = handoff.verify(str(path))
        self.assertEqual(verified["status"], "artifacts_present")
        self.assertEqual(verified["result_status"], "draft_complete")
        self.assertEqual(verified["semantic_review"], "not_performed")
        self.assertFalse(verified["submitted"])

    def test_changed_input_rejected_without_modifying_source_or_manifest(self):
        _, path, manifest = self.prepare()
        self.artifact(manifest)
        self.source.write_text("已经修改", encoding="utf-8")
        before = {p: p.read_bytes() for p in (self.source, path, self.brief_path)}
        with self.assertRaisesRegex(ValueError, "输入已改变"):
            handoff.verify(str(path))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_changed_rules_and_missing_input_rejected(self):
        _, path, _ = self.prepare()
        (self.skill / "references/stage.md").write_text("修改规则", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "输入已改变"):
            handoff.verify(str(path))
        (self.skill / "references/stage.md").write_text("规则", encoding="utf-8")
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "路径不存在"):
            handoff.verify(str(path))

    def test_missing_and_empty_artifacts_rejected(self):
        _, path, manifest = self.prepare()
        with self.assertRaisesRegex(ValueError, "产物缺失"):
            handoff.verify(str(path))
        self.artifact(manifest, "")
        with self.assertRaisesRegex(ValueError, "产物缺失"):
            handoff.verify(str(path))

    def test_result_expectation_is_required_before_state_creation(self):
        self.brief.pop("expected_outputs")
        self.save_brief()
        with self.assertRaises(ValueError):
            self.prepare()
        for outputs in ([], ["候选/分镜.txt"], ["nested/result.json"], ["Result.json"]):
            self.brief["expected_outputs"] = outputs
            self.save_brief()
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertFalse((self.project / "agents").exists())

    def test_task_key_is_required_before_state_creation(self):
        for scope in ("SC001", {"scene": "SC001"}, {"task_key": " "}, {"task_key": 1}):
            self.brief["scope"] = scope
            self.save_brief()
            with self.assertRaisesRegex(ValueError, "task_key"):
                self.prepare()
        self.assertFalse((self.project / "agents").exists())

    def test_missing_invalid_or_mismatched_result_rejected(self):
        _, path, manifest = self.prepare()
        self.artifact(manifest)
        result_path = Path(manifest["output_dir"]) / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result_path.unlink()
        with self.assertRaisesRegex(ValueError, "产物缺失"):
            handoff.verify(str(path))
        result_path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            handoff.verify(str(path))
        for change in ({"status": "approved"}, {"status": None}, {"scope": {"task_key": "其他任务"}}, {"scope": {}}):
            self.write_json(result_path, {**result, **change})
            with self.assertRaises(ValueError):
                handoff.verify(str(path))

    def test_result_artifact_paths_and_files_must_be_valid(self):
        _, path, manifest = self.prepare()
        self.artifact(manifest)
        result_path = Path(manifest["output_dir"]) / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        (Path(manifest["output_dir"]) / "empty.txt").write_text("", encoding="utf-8")
        for artifacts in ([], ["候选/分镜.txt"], ["result.json", "../outside.txt"],
                          ["result.json", str(self.source)], ["result.json", "missing.txt"], ["result.json", "empty.txt"]):
            self.write_json(result_path, {**result, "artifacts": artifacts})
            with self.assertRaises(ValueError):
                handoff.verify(str(path))
        for status in ("needs_input", "failed"):
            self.write_json(result_path, {**result, "status": status})
            verified = handoff.verify(str(path))
            self.assertEqual(verified["result_status"], status)
            self.assertEqual(verified["semantic_review"], "not_performed")
            self.assertFalse(verified["submitted"])

    def test_unsafe_outputs_rejected_before_any_state_created(self):
        for name in ("../outside.txt", "nested/../../outside", "C:\\outside.txt", "/outside.txt", "\\\\server\\share", "out.txt:stream"):
            with self.subTest(name=name):
                self.brief["expected_outputs"] = [name]
                self.save_brief()
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse((self.project / "agents").exists())

    def test_tampered_output_directory_and_names_rejected(self):
        _, path, manifest = self.prepare()
        original = copy.deepcopy(manifest)
        manifest["output_dir"] = str(self.project)
        self.write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "output_dir"):
            handoff.verify(str(path))
        original["expected_outputs"] = ["../原文.txt"]
        self.write_json(path, original)
        with self.assertRaisesRegex(ValueError, "不安全"):
            handoff.verify(str(path))

    def test_output_link_cannot_escape(self):
        _, path, manifest = self.prepare()
        self.artifact(manifest)
        link = Path(manifest["output_dir"]) / "候选/分镜.txt"
        link.unlink()
        try:
            link.symlink_to(self.source)
        except OSError:
            # Windows may deny symlink creation. Still exercise the resolved-path
            # boundary without privileges, rather than silently skip the guard.
            original_resolve = Path.resolve
            def resolved(path, *args, **kwargs):
                return self.source if path == link else original_resolve(path, *args, **kwargs)
            with patch.object(Path, "resolve", resolved):
                with self.assertRaisesRegex(ValueError, "链接越界"):
                    handoff.verify(str(path))
            return
        with self.assertRaisesRegex(ValueError, "链接越界"):
            handoff.verify(str(path))

    def test_escalation_requires_eligible_stage_and_reason(self):
        for stage, reason in (("sync", "疑难"), ("review", "  ")):
            with self.subTest(stage=stage):
                with self.assertRaises(ValueError):
                    self.prepare(stage, reason)
                self.assertFalse((self.project / "agents").exists())
        result, _, manifest = self.prepare("review", "跨世身份存在冲突")
        self.assertEqual(result["model"], "gpt-6-astra")
        self.assertEqual(manifest["escalation_reason"], "跨世身份存在冲突")

    def test_uuid_collision_cannot_overwrite_prior_files(self):
        with patch.object(handoff.uuid, "uuid4", return_value="fixed"):
            _, path, manifest = self.prepare()
            self.artifact(manifest)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                self.prepare()
            self.assertEqual(path.read_bytes(), before)

    def test_configuration_is_the_model_and_route_source(self):
        self.config["profiles"]["operations"] = {"model": "another-available-model", "reasoning_effort": "low"}
        self.config["stages"]["review"]["profile"] = "operations"
        self.save_config()
        result, _, _ = self.prepare("review")
        self.assertEqual((result["model"], result["reasoning_effort"]), ("another-available-model", "low"))

    def test_invalid_profile_reference_effort_or_fork_rejected(self):
        original = copy.deepcopy(self.config)
        mutations = (("stages", "review", "profile", "missing"),
                     ("profiles", "creative", "model", " "),
                     ("profiles", "creative", "reasoning_effort", "unsupported"))
        for section, key, field, value in mutations:
            self.config = copy.deepcopy(original)
            self.config[section][key][field] = value
            self.save_config()
            with self.assertRaises(ValueError):
                self.prepare()
        self.config = original
        self.config["scheduling"] = {"fork_turns": "all"}
        self.save_config()
        with self.assertRaisesRegex(ValueError, "fork_turns"):
            self.prepare()
        self.assertFalse((self.project / "agents").exists())

    def test_invalid_schema_and_missing_rule_fail_without_state(self):
        for value in (True, 2, "1"):
            self.config["schema_version"] = value
            self.save_config()
            with self.assertRaisesRegex(ValueError, "schema_version"):
                self.prepare()
        self.config["schema_version"] = 1
        self.save_config()
        (self.skill / "references/stage.md").unlink()
        with self.assertRaisesRegex(ValueError, "规则文件缺失"):
            self.prepare()
        self.assertFalse((self.project / "agents").exists())

    def test_skill_project_and_bad_brief_rejected_without_state(self):
        with self.assertRaisesRegex(ValueError, "Skill 包"):
            handoff.prepare(str(self.skill), "review", str(self.brief_path))
        for key, value in (("objective", ""), ("authorization", ""), ("scope", {}), ("inputs", []), ("inputs", ["relative.txt"])):
            previous = self.brief[key]
            self.brief[key] = value
            self.save_brief()
            with self.assertRaises(ValueError):
                self.prepare()
            self.brief[key] = previous
        self.assertFalse((self.project / "agents").exists())


if __name__ == "__main__":
    unittest.main()
