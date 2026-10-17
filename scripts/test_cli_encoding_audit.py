"""Offline subprocess checks: CLI text must stay UTF-8 under a cp1252 host."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import review_metrics
import storyboard_preflight
import test_storyboard_preflight as preflight_fixtures


SCRIPTS = Path(__file__).resolve().parent
ENTRY_POINTS = (
    "storyboard_preflight.py", "staging_preflight.py", "review_metrics.py",
    "review_gate.py", "scene_review.py", "canvas_jobs.py",
)


class CLIEncodingAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="seedance-编码-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, filename, value):
        path = self.root / filename
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def run_cli(self, script, *args):
        # Override only this child. A global UTF-8 environment would mask the bug.
        env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0",
               "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
                                cwd=self.root, env=env, capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        stdout, stderr = result.stdout.decode("utf-8"), result.stderr.decode("utf-8")
        self.assertNotIn("UnicodeEncodeError", stdout + stderr)
        return result.returncode, stdout, stderr

    def test_all_help_entry_points_work_without_host_utf8(self):
        for script in ENTRY_POINTS:
            with self.subTest(script=script):
                code, out, err = self.run_cli(script, "--help")
                self.assertEqual(code, 0, err)
                self.assertIn("usage:", out)
                self.assertEqual(err, "")

    def test_preflight_success_json_matches_the_actual_checker(self):
        plan = preflight_fixtures.unit()
        path = self.write("中文声音计划.json", plan)
        code, out, err = self.run_cli("storyboard_preflight.py", "--plan", path)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), storyboard_preflight.check_audio_plan(plan))
        self.assertEqual(err, "")

    def test_preflight_error_json_preserves_chinese_path_and_nonzero_status(self):
        missing = self.root / "不存在的前置输入.json"
        code, out, err = self.run_cli("storyboard_preflight.py", "--plan", missing)
        self.assertNotEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"][0]["code"], "PREFLIGHT_INPUT")
        self.assertIn("无法读取或校验前置输入", out)
        self.assertIn(missing.name, out)
        self.assertEqual(err, "")

    def test_preflight_argument_error_is_readable_on_stderr(self):
        code, out, err = self.run_cli("storyboard_preflight.py")
        self.assertNotEqual(code, 0)
        self.assertIn("须提供", err)
        self.assertEqual(out, "")

    def test_staging_success_returns_exact_chinese_output_path(self):
        state = self.write("状态.json", {})
        request = self.write("请求.json", {"units": []})
        output = self.root / "中文交付目录" / "待审清单.json"
        code, out, err = self.run_cli("staging_preflight.py", "--state", state,
                                      "--request", request, "--output", output)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"status": "pending", "output": str(output), "format": "full"})
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"status": "pending", "units": []})

    def test_metrics_success_keeps_chinese_measurement_limits(self):
        request = {"units": []}
        proof = {"request_sha256": review_metrics.digest(request),
                 "old_request_sha256": review_metrics.digest(request), "shot_provenance": []}
        before = self.write("前稿.json", request)
        after = self.write("后稿.json", request)
        provenance = self.write("复核来源.json", proof)
        output = self.root / "实测记录.jsonl"
        code, out, err = self.run_cli("review_metrics.py", "--before", before, "--after", after,
                                      "--provenance", provenance, "--output", output,
                                      "--started-at", "2026-10-16T08:00:00",
                                      "--finished-at", "2026-10-16T08:01:00")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result, json.loads(output.read_text(encoding="utf-8")))
        self.assertIn("本次实际返修区间耗时", result["limits"])
        self.assertEqual(result["measured_elapsed_seconds"], 60)

    def test_review_errors_preserve_chinese_stderr(self):
        code, out, err = self.run_cli("review_gate.py", "prepare", "--project", self.root / "中文项目",
                                      "--request", self.root / "请求.json", "--report", self.root / "报告.json")
        self.assertNotEqual(code, 0)
        self.assertIn("中文项目", err)
        self.assertEqual(out, "")
        code, out, err = self.run_cli("scene_review.py", "freeze", "--out", self.root / "快照.json")
        self.assertNotEqual(code, 0)
        self.assertIn("须指定且只指定", err)
        self.assertEqual(out, "")

    def test_staging_and_metrics_failures_preserve_chinese_input_paths(self):
        missing = self.root / "未找到的中文输入.json"
        cases = (
            ("staging_preflight.py", ("--state", missing, "--request", missing,
                                       "--output", self.root / "暂存.json")),
            ("review_metrics.py", ("--before", missing, "--after", missing,
                                    "--provenance", missing, "--output", self.root / "记录.jsonl",
                                    "--started-at", "2026-10-16T08:00:00", "--finished-at", "2026-10-16T08:01:00")),
        )
        for script, args in cases:
            with self.subTest(script=script):
                code, out, err = self.run_cli(script, *args)
                self.assertNotEqual(code, 0)
                self.assertIn(missing.name, err)
                self.assertEqual(out, "")

    def test_queue_local_error_is_utf8_json_and_never_starts_worker(self):
        code, out, err = self.run_cli("canvas_jobs.py", "image-choice", "--project", self.root, "--choice", "1")
        self.assertNotEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["status"], "error")
        self.assertIn("资产同步尚未完整核验", result["message"])
        self.assertEqual(err, "")
        self.assertFalse((self.root / "canvas" / "jobs" / "worker.json").exists())


if __name__ == "__main__":
    unittest.main()
