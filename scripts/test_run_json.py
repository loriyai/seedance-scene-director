"""Real local subprocess checks; no models, network, or project artifacts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(__file__).with_name("run_json.py").resolve()


class RunJsonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="run-json-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.child = self.root / "child.py"
        self.output = self.root / "result.json"
        self.log = self.root / "diagnostic.json"
        self.marker = self.root / "executed.txt"

    def invoke(self, code, *, output=None, log=None, extra=(), arguments=()):
        self.child.write_text(code, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-B", str(RUNNER), "--output", str(output or self.output),
             "--log", str(log or self.log), *extra, "--", sys.executable, "-B",
             str(self.child), *map(str, arguments)], cwd=self.root,
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"})

    def diagnostic(self):
        return json.loads(self.log.read_text(encoding="utf-8"))

    def marked_code(self):
        return "from pathlib import Path\nPath('executed.txt').write_text('ran')\nprint('{}')\n"

    def assert_preflight_rejected(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.marker.exists(), "unsafe command ran before rejection")

    def test_success_preserves_json_and_stderr(self):
        result = self.invoke("import sys\nprint('{\"中文\": [1, true]}')\nprint('诊断', file=sys.stderr)\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), {"中文": [1, True]})
        self.assertEqual(self.diagnostic()["status"], "succeeded")
        self.assertIn("诊断", self.diagnostic()["stderr"])
        self.assertIn("诊断", result.stderr)

    def test_valid_arrays_and_scalars_are_not_assumed_to_be_objects(self):
        for index, value in enumerate(([1, "二"], None, True, "文本", 3)):
            with self.subTest(value=value):
                payload = json.dumps(value, ensure_ascii=False)
                output, log = self.root / f"r{index}.json", self.root / f"l{index}.json"
                result = self.invoke(f"print({payload!r})", output=output, log=log)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(output.read_text(encoding="utf-8")), value)

    def test_nonzero_exit_keeps_stderr_and_checks_exit_before_json(self):
        result = self.invoke("import sys\nprint('not JSON')\nprint('original failure', file=sys.stderr)\nsys.exit(7)")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        record = self.diagnostic()
        self.assertEqual(record["returncode"], 7)
        self.assertEqual(record["error"], "command exited with code 7")
        self.assertIn("original failure", result.stderr)
        self.assertIn("original failure", record["stderr"])
        self.assertIn("not JSON", record["stdout"])

    def test_nonzero_exit_with_valid_json_does_not_publish_result(self):
        result = self.invoke("import sys\nprint('{}')\nsys.exit(2)")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.diagnostic()["status"], "failed")

    def test_empty_or_invalid_json_does_not_publish_result(self):
        for index, payload in enumerate(("", "  ", "not JSON", "{} trailing", "NaN", "Infinity")):
            with self.subTest(payload=payload):
                output, log = self.root / f"r{index}.json", self.root / f"l{index}.json"
                result = self.invoke(f"print({payload!r})", output=output, log=log)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
                self.assertEqual(json.loads(log.read_text(encoding="utf-8"))["status"], "failed")

    def test_existing_output_and_log_are_not_overwritten_or_executed(self):
        for name in ("output", "log"):
            with self.subTest(name=name):
                path = getattr(self, name)
                path.write_text("keep me", encoding="utf-8")
                result = self.invoke(self.marked_code())
                self.assert_preflight_rejected(result)
                self.assertEqual(path.read_text(encoding="utf-8"), "keep me")
                path.unlink()

    def test_output_log_dotdot_alias_is_rejected(self):
        (self.root / "nested").mkdir()
        result = self.invoke(self.marked_code(), log=self.root / "nested" / ".." / "result.json")
        self.assert_preflight_rejected(result)
        self.assertFalse(self.output.exists())

    def test_argv_path_alias_collision_is_rejected_even_if_missing(self):
        (self.root / "nested").mkdir()
        for argument in ("nested/../result.json", "--source=nested/../diagnostic.json"):
            with self.subTest(argument=argument):
                result = self.invoke(self.marked_code(), arguments=(argument,))
                self.assert_preflight_rejected(result)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.log.exists())

    def test_explicit_indirect_input_collision_is_rejected(self):
        result = self.invoke(self.marked_code(), extra=("--input", str(self.output)))
        self.assert_preflight_rejected(result)
        self.assertFalse(self.output.exists())

    def test_input_template_cannot_be_used_as_log(self):
        template = self.root / "template.json"
        template.write_text('{"template": true}', encoding="utf-8")
        result = self.invoke(self.marked_code(), log=template, arguments=(template,))
        self.assert_preflight_rejected(result)
        self.assertEqual(template.read_text(encoding="utf-8"), '{"template": true}')

    def test_child_created_output_is_not_overwritten(self):
        result = self.invoke("from pathlib import Path\nPath('result.json').write_text('child content')\nprint('{}')")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(), "child content")
        self.assertEqual(self.diagnostic()["status"], "failed")

    def test_shell_metacharacters_remain_literal_arguments(self):
        literal = "& echo injected > executed.txt"
        result = self.invoke("import json, sys\nprint(json.dumps(sys.argv[1:]))", arguments=(literal,))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_text()), [literal])
        self.assertFalse(self.marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows path identity")
    def test_windows_case_alias_is_rejected(self):
        result = self.invoke(self.marked_code(), log=str(self.output).upper())
        self.assert_preflight_rejected(result)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(os.name == "nt", "Windows path identity")
    def test_windows_ambiguous_destinations_are_rejected_before_execution(self):
        # resolve() alone retains these spellings although Win32 may fold them,
        # open a device, or create an alternate stream on the base file.
        for name in ("result.json.", "result.json ", "result.json:stream", "NUL",
                     "nul.json", "COM1.txt", "con/result.json"):
            for destination in ("output", "log"):
                with self.subTest(name=name, destination=destination):
                    result = self.invoke(self.marked_code(), **{destination: self.root / name})
                    self.assert_preflight_rejected(result)
                    self.assertFalse(self.output.exists())
                    self.assertFalse(self.log.exists())

    @unittest.skipUnless(os.name == "nt", "Windows path identity")
    def test_windows_argv_and_explicit_input_aliases_are_protected(self):
        for alias in (str(self.output) + ".", str(self.log) + " ",
                      str(self.output) + ":stream", "\\\\?\\" + str(self.output)):
            for explicit in (False, True):
                with self.subTest(alias=alias, explicit=explicit):
                    kwargs = {"extra": ("--input", alias)} if explicit else {"arguments": (alias,)}
                    result = self.invoke(self.marked_code(), **kwargs)
                    self.assert_preflight_rejected(result)
                    self.assertFalse(self.output.exists())
                    self.assertFalse(self.log.exists())

    @unittest.skipUnless(os.name == "nt", "Windows path identity")
    def test_windows_extended_path_output_log_alias_is_rejected(self):
        result = self.invoke(self.marked_code(), log="\\\\?\\" + str(self.output))
        self.assert_preflight_rejected(result)
        self.assertFalse(self.output.exists())

    def test_symlink_directory_alias_is_rejected_when_supported(self):
        link = self.root / "alias"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        result = self.invoke(self.marked_code(), log=link / "result.json")
        self.assert_preflight_rejected(result)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
