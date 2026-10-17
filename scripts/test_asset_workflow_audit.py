"""Workflow regressions: early candidate gates and proven local design reuse."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_asset_visual_design import SKILL, fixture, save, ledger, migration, design
from test_prop_coverage_policy import add_coverage


class AssetWorkflowAuditTests(unittest.TestCase):
    def setUp(self):
        root = Path(os.environ.get("SEEDANCE_AUDIT_TEST_DIR", Path.cwd() / "work/asset-workflow-tests"))
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)
        self.style = (self.work / "video-style.txt").read_text(encoding="utf-8").strip()
        self.module = SKILL / "modules/screenplay-standardizer/scripts"

    def designed(self, data=None, payload=None):
        return design.apply_design(data or self.data, payload or self.payload, self.style, paragraphs=self.paragraphs)

    def save_current(self, data):
        save(self.work / "ledger-current.json", data)
        return self.work / "ledger-current.json"

    def add_independent_scene(self):
        self.paragraphs.append("风吹过空院。")
        heading = "场景3-日-外-空院"
        text = heading + "\n人物：无；\n△ " + self.paragraphs[-1] + "\n"
        save(self.work / "scenes/scene_003_0003-0003.txt", text)
        save(self.script, self.script.read_text(encoding="utf-8") + "\n" + text)
        save(self.source, {"paragraphs": [{"id": i, "text": text} for i, text in enumerate(self.paragraphs, 1)]})
        self.data["source_digest"] = ledger.digest(self.paragraphs)
        self.data["locations"].append({"id": "LOC0002", "name": "空院", "aliases": [], "visual_facts": [],
                                       "identity_status": "confirmed", "identity_basis": "独立的院落场景"})
        self.data["scene_assets"].append({"id": "SET0002", "name": "空院", "location_id": "LOC0002",
                                          "variant": "base", "parent_id": None, "visual_facts": []})
        self.data["scenes"].append({"id": "SC0003", "number": 3, "heading": heading,
                                     "source_range": [3, 3], "location_id": "LOC0002", "scene_asset_id": "SET0002",
                                     "character_ids": [], "prop_ids": [], "visual_facts": [],
                                     "time_basis": {"kind": "格式默认", "source_paragraphs": []}})
        other = copy.deepcopy(self.payload["designs"][1])
        other.update(asset_id="SET0002", basis="仅空院环境，和陈府人物道具无关", space="院墙围合的空院，门内留有完整通道",
                     furnishings="院内不设家具", lighting="均匀自然日光")
        self.payload["designs"].append(other)

    def local_scopes(self):
        for item in self.payload["designs"]:
            item["source_scope"] = {"kind": "scenes", "scene_ids": ["SC0003"] if item["asset_id"] == "SET0002" else ["SC0001", "SC0002"],
                                    "basis": "设计只依赖所列出现场次的已审环境和形象，其他场次没有额外设计约束"}

    def run_cli(self, name, *args):
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, str(self.module / name), *map(str, args)],
                              capture_output=True, encoding="utf-8", env=env)

    def test_candidate_preflight_checks_before_design_and_does_not_publish(self):
        path = self.save_current(self.data)
        before = path.read_bytes()
        result = ledger.preflight(self.script, self.source, path)
        self.assertEqual(result["stage"], "candidate")
        self.assertEqual(result["semantic_review"], "not_performed")
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "完整美术设计"):
            ledger.preflight(self.script, self.source, path, "designed")

    def test_candidate_gate_rejects_invalid_identity_before_authoring_images(self):
        self.data["scenes"][0]["character_ids"] = []
        with self.assertRaises(ValueError):
            ledger.preflight(self.script, self.source, self.save_current(self.data))

    def test_candidate_gate_rejects_unreviewed_reverse_coverage(self):
        add_coverage(self.data)
        self.data["prop_coverage_review"]["reviewed_full_script"] = False
        with self.assertRaisesRegex(ValueError, "反向覆盖审阅未完成"):
            ledger.preflight(self.script, self.source, self.save_current(self.data))

    def test_patch_changes_one_asset_without_reauthoring_other_designs(self):
        original = self.designed()
        patch = {"designs": [copy.deepcopy(self.payload["designs"][-1])]}
        patch["designs"][0]["state"] = "完整未损，边缘保留细微磨痕"
        result = design.apply_design(original, patch, self.style, patch=True, paragraphs=self.paragraphs)
        self.assertEqual(result["characters"], original["characters"])
        self.assertEqual(result["scene_assets"], original["scene_assets"])
        self.assertNotEqual(result["props"][0]["image_prompt"], original["props"][0]["image_prompt"])

    def test_patch_rejects_changed_shared_anchor_if_dependent_asset_not_included(self):
        original = self.designed()
        anchor = copy.deepcopy(self.payload["identity_anchors"][0])
        anchor["hair"] = "乌黑短发"
        patch = {"identity_anchors": [anchor], "designs": [self.payload["designs"][-1]]}
        with self.assertRaisesRegex(ValueError, "CHAR0001.*依赖已变化"):
            design.apply_design(original, patch, self.style, patch=True, paragraphs=self.paragraphs)

    def test_patch_never_reblesses_stale_unselected_design_hash(self):
        original = self.designed()
        original["characters"][0]["visual_design"]["styling"] = "未经审阅的新衣服"
        with self.assertRaisesRegex(ValueError, "CHAR0001"):
            design.apply_design(original, {"designs": [self.payload["designs"][-1]]}, self.style,
                                patch=True, paragraphs=self.paragraphs)

    def test_patch_rejects_style_switch_and_new_asset_without_design(self):
        original = self.designed()
        with self.assertRaisesRegex(ValueError, "切换风格"):
            design.apply_design(original, {"designs": [self.payload["designs"][-1]]}, "新风格", patch=True)
        original["characters"][0].pop("visual_design")
        with self.assertRaisesRegex(ValueError, "没有可复用设计"):
            design.apply_design(original, {"designs": [self.payload["designs"][-1]]}, self.style, patch=True)

    def test_local_source_scope_reuses_unrelated_assets_after_source_change(self):
        self.add_independent_scene()
        self.local_scopes()
        original = self.designed()
        changed = copy.deepcopy(original)
        self.paragraphs[2] += "落叶滚过门槛。"
        changed["source_digest"] = ledger.digest(self.paragraphs)
        result = design.apply_design(changed, {"designs": [self.payload["designs"][-1]]}, self.style,
                                     patch=True, paragraphs=self.paragraphs)
        for category in ("characters", "props"):
            self.assertEqual(result[category], original[category])
        self.assertEqual(result["scene_assets"][0], original["scene_assets"][0])

    def test_undeclared_scope_stays_conservative_on_unrelated_source_change(self):
        self.add_independent_scene()
        original = self.designed()
        self.paragraphs[2] += "落叶滚过门槛。"
        original["source_digest"] = ledger.digest(self.paragraphs)
        with self.assertRaisesRegex(ValueError, "CHAR0001"):
            design.apply_design(original, {"designs": [self.payload["designs"][-1]]}, self.style,
                                patch=True, paragraphs=self.paragraphs)

    def test_local_scope_cannot_omit_appearance_or_ignore_changed_dependency(self):
        self.local_scopes()
        self.payload["designs"][0]["source_scope"]["scene_ids"] = ["SC0001"]
        with self.assertRaisesRegex(ValueError, "漏掉资产出现场次"):
            self.designed()
        self.payload["designs"][0]["source_scope"]["scene_ids"] = ["SC0001", "SC0002"]
        original = self.designed()
        self.paragraphs[1] += "他坐在桌旁。"
        original["source_digest"] = ledger.digest(self.paragraphs)
        with self.assertRaisesRegex(ValueError, "CHAR0001"):
            design.apply_design(original, {"designs": [self.payload["designs"][-1]]}, self.style,
                                patch=True, paragraphs=self.paragraphs)

    def test_designed_gate_rejects_tampered_scene_content_identity(self):
        self.local_scopes()
        original = self.designed()
        original["design_source_scene_digests"]["SC0001"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "场次摘要"):
            ledger.preflight(self.script, self.source, self.save_current(original), "designed")

    def test_migration_reports_unrelated_designs_as_reusable(self):
        self.add_independent_scene()
        self.local_scopes()
        original = self.designed()
        old_ledger = self.save_current(original)
        newer = self.work / "new"
        newer.mkdir()
        (newer / "scenes").mkdir()
        for scene in (self.work / "scenes").glob("*.txt"):
            save(newer / "scenes" / scene.name, scene.read_text(encoding="utf-8"))
        changed = self.paragraphs[:]
        changed[2] += "落叶滚过门槛。"
        save(newer / "source.json", {"paragraphs": [{"id": i, "text": text} for i, text in enumerate(changed, 1)]})
        target = newer / "scenes/scene_003_0003-0003.txt"
        save(target, target.read_text(encoding="utf-8").replace(self.paragraphs[2], changed[2]))
        save(newer / "script.txt", "\n".join(p.read_text(encoding="utf-8") for p in sorted((newer / "scenes").glob("*.txt"))))
        _, report = migration.migrate(old_ledger, self.source, newer / "script.txt", newer,
                                      {"scenes": {"SC0001": [1], "SC0002": [2], "SC0003": [3]}})
        self.assertIn("CHAR0001", report["design_status"]["reusable_asset_ids"])
        self.assertEqual([item["asset_id"] for item in report["design_status"]["review_required"]], ["SET0002"])

    def test_module_clis_emit_utf8_even_with_cp1252_environment(self):
        path = self.save_current(self.data)
        pre = self.run_cli("asset_ledger.py", "preflight", self.script, self.source, path)
        self.assertEqual(pre.returncode, 0, pre.stderr)
        self.assertEqual(json.loads(pre.stdout)["status"], "passed")
        review = self.work / "review.json"
        checked = self.run_cli("screenplay_review.py", self.work, "--report", review)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        assembled = self.run_cli("screenplay_io.py", "assemble", self.work, "--review-report", review,
                                 "--output", self.work / "assembled.txt")
        self.assertEqual(assembled.returncode, 0, assembled.stderr)
        compiled = self.run_cli("asset_ledger.py", "design", self.script, self.source, path,
                                self.work / "design.json", "--style-file", self.work / "video-style.txt",
                                "--output", self.work / "designed.json", "--catalog-output", self.work / "catalog.txt")
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        migrated = self.run_cli("asset_migrate.py", path, self.source, self.script, self.work,
                                "--output", self.work / "migrated.json", "--report", self.work / "migration.json")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)


if __name__ == "__main__":
    unittest.main()
