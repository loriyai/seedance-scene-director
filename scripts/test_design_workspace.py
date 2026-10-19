"""Real source/ledger fixtures exercise shared designs, grouping and final merge."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import SKILL, fixture, save, design, ledger
import test_character_variants as mask_fixture
import design_workspace as workspace


class AuthorDesignDiffTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"image_design_version": "gpt-image-2-v2",
                        "identity_anchors": [{"id": "person-a", "face": "方脸", "hair": "黑发"},
                                             {"id": "person-b", "face": "圆脸", "hair": "棕发"}],
                        "designs": [{"asset_id": "CHAR0002", "identity_anchor_id": "person-a", "styling": "蓝衣"},
                                    {"asset_id": "CHAR0001", "identity_anchor_id": "person-a", "styling": "白衣"},
                                    {"asset_id": "PROP0001", "structure": "木匣", "parts": ["盖", "底"]}]}

    def test_field_order_and_asset_or_anchor_array_reordering_are_not_changes(self):
        def reverse_keys(value):
            if isinstance(value, dict):
                return {key: reverse_keys(item) for key, item in reversed(list(value.items()))}
            if isinstance(value, list):
                return [reverse_keys(item) for item in value]
            return value

        original = copy.deepcopy(self.payload)
        after = reverse_keys(self.payload)
        after["designs"].reverse()
        after["identity_anchors"].reverse()
        report = workspace.compare_designs(self.payload, after)
        self.assertFalse(report["has_changes"])
        self.assertEqual(report["status"], "compared")
        self.assertEqual(report["semantic_review"], "not_performed")
        self.assertFalse(report["formal_ready"])
        self.assertEqual(report["direct_anchor_references"], [])
        self.assertEqual(self.payload, original)

    def test_actual_added_removed_and_changed_assets_are_sorted_by_id_and_field(self):
        after = copy.deepcopy(self.payload)
        after["designs"] = [after["designs"][2], after["designs"][0]]
        after["designs"][0].update(structure="金属匣", parts=["底", "盖"], z_note="z", a_note="a")
        after["designs"][1].pop("styling")
        after["designs"].extend([{"asset_id": "SET0002"}, {"asset_id": "SET0001"}])
        report = workspace.compare_designs(self.payload, after)
        self.assertTrue(report["has_changes"])
        self.assertEqual(report["assets"], {
            "added": ["SET0001", "SET0002"], "removed": ["CHAR0001"],
            "changed": [{"asset_id": "CHAR0002", "fields": {"added": [], "removed": ["styling"], "changed": []}},
                        {"asset_id": "PROP0001", "fields": {"added": ["a_note", "z_note"], "removed": [],
                                                               "changed": ["parts", "structure"]}}]})

    def test_top_level_values_and_ordered_content_are_compared_without_coercion(self):
        self.payload.update(identity_assignments={"CHAR0002": "person-a", "CHAR0001": "person-a"},
                            review_order=["identity", "structure"], old_note="删除", reviewed=True)
        after = copy.deepcopy(self.payload)
        after.update(identity_assignments={"CHAR0001": "person-a", "CHAR0002": "person-a"},
                     image_design_version="another-version", review_order=["structure", "identity"],
                     reviewed=1, new_note="新增")
        after.pop("old_note")
        report = workspace.compare_designs(self.payload, after)
        self.assertEqual(report["top_level"], {"added": ["new_note"], "removed": ["old_note"],
                                              "changed": ["image_design_version", "review_order", "reviewed"]})
        self.assertEqual(report["assets"]["changed"], [])

    def test_anchor_changes_include_before_and_after_direct_references(self):
        after = copy.deepcopy(self.payload)
        after["identity_anchors"] = [{"id": "person-c", "face": "圆脸"},
                                     {"id": "person-a", "face": "长脸", "hair": "黑发"}]
        after["designs"][0]["identity_anchor_id"] = "person-c"
        # Shared author designs may state assignments separately from records.
        self.payload["identity_assignments"] = {"CHAR0003": "person-b"}
        after["identity_assignments"] = {"CHAR0003": "person-c"}
        report = workspace.compare_designs(self.payload, after)
        self.assertEqual(report["identity_anchors"], {"added": ["person-c"], "removed": ["person-b"],
            "changed": [{"id": "person-a", "fields": {"added": [], "removed": [], "changed": ["face"]}}]})
        self.assertEqual(report["direct_anchor_references"], [
            {"id": "person-a", "before_asset_ids": ["CHAR0001", "CHAR0002"], "after_asset_ids": ["CHAR0001"]},
            {"id": "person-b", "before_asset_ids": ["CHAR0003"], "after_asset_ids": []},
            {"id": "person-c", "before_asset_ids": [], "after_asset_ids": ["CHAR0002", "CHAR0003"]}])

    def test_duplicate_stable_ids_on_either_side_are_rejected(self):
        for side in (0, 1):
            for collection, message in (("designs", "重复资产设计"), ("identity_anchors", "重复人物身份锚点")):
                pair = [copy.deepcopy(self.payload), copy.deepcopy(self.payload)]
                pair[side][collection].append(copy.deepcopy(pair[side][collection][0]))
                with self.subTest(side=side, collection=collection), self.assertRaisesRegex(ValueError, message):
                    workspace.compare_designs(*pair)

    def test_cli_only_reads_input_files_and_returns_errors_for_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before, after = root / "before.json", root / "after.json"
            save(before, self.payload)
            changed = copy.deepcopy(self.payload)
            changed["designs"][0]["styling"] = "红衣"
            save(after, changed)
            command = SKILL / "modules/screenplay-standardizer/scripts/design_workspace.py"

            def run():
                inputs = {path: path.read_bytes() for path in root.iterdir()}
                result = subprocess.run([sys.executable, "-B", str(command), "diff", str(before), str(after)],
                                        capture_output=True, text=True, encoding="utf-8",
                                        env={**os.environ, "PYTHONUTF8": "1"})
                self.assertEqual(inputs, {path: path.read_bytes() for path in root.iterdir()})
                return result.returncode, json.loads(result.stdout)

            code, report = run()
            self.assertEqual(code, 0)
            self.assertEqual(report["assets"]["changed"][0]["asset_id"], "CHAR0002")
            self.assertEqual(report["semantic_review"], "not_performed")
            changed["designs"].append(copy.deepcopy(changed["designs"][0]))
            save(after, changed)
            code, report = run()
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "error")


class DesignWorkspaceTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "design-workspace-test-work"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)
        self.style = (self.work / "video-style.txt").read_text(encoding="utf-8").strip()

    def shared(self, *ids):
        result = copy.deepcopy(self.payload)
        result["identity_assignments"] = {"CHAR0001": self.payload["identity_anchors"][0]["id"]}
        result["designs"] = [record for record in result["designs"] if record["asset_id"] in ids]
        return result

    def freeze(self, *ids):
        save(self.work / "ledger.json", self.data)
        save(self.work / "shared.json", self.shared(*ids))
        return workspace.freeze_from_files(self.script, self.source, self.work / "ledger.json",
                                           self.work / "shared.json", self.work / "video-style.txt")

    def submission(self, baseline, plan, gid, records=None):
        group = workspace.group_spec(baseline, plan, gid)
        if records is None:
            records = [record for record in self.payload["designs"] if record["asset_id"] in group["owned_asset_ids"]]
        return {"baseline_sha256": baseline["baseline_sha256"], "plan_sha256": plan["plan_sha256"],
                "group_id": gid, "owned_asset_ids": group["owned_asset_ids"],
                "read_only_dependency_sha256": group["read_only_dependency_sha256"], "designs": copy.deepcopy(records)}

    def simple(self):
        baseline = self.freeze("CHAR0001")
        plan = workspace.plan(baseline, [{"group_id": "set", "owned_asset_ids": ["SET0001"]},
                                        {"group_id": "prop", "owned_asset_ids": ["PROP0001"]}])
        return baseline, plan

    def test_independent_groups_merge_to_identical_existing_compiler_prompts(self):
        baseline, plan = self.simple()
        before = {path: path.read_bytes() for path in self.work.rglob("*") if path.is_file()}
        submissions = [self.submission(baseline, plan, gid) for gid in ("prop", "set")]
        report = workspace.verify_group(baseline, plan, "prop", submissions[0])
        self.assertEqual(report["status"], "structurally_valid_group")
        self.assertEqual(report["semantic_review"], "not_performed")
        self.assertFalse(report["formal_ready"])
        self.assertFalse(report["bindable"])
        merged = workspace.merge(baseline, plan, submissions)
        self.assertEqual(design.apply_design(self.data, merged, self.style),
                         design.apply_design(self.data, self.payload, self.style))
        self.assertEqual(merged, workspace.merge(baseline, plan, list(reversed(submissions))))
        self.assertEqual(before, {path: path.read_bytes() for path in self.work.rglob("*") if path.is_file()})
        self.assertEqual(len(baseline["ledger"]["props"]), 1)

    def test_all_frozen_zero_groups_and_legacy_prompt_bytes(self):
        self.payload["image_design_version"] = design.LEGACY_DESIGN_VERSION
        baseline = self.freeze("CHAR0001", "SET0001", "PROP0001")
        plan = workspace.plan(baseline, [])
        merged = workspace.merge(baseline, plan, [])
        final = design.apply_design(self.data, merged, "古装写实")
        expected = {"CHAR0001": "ce6b25ae354365edacc1db422ed4568c739b2c66a1288497098ab4a7ca522f4b",
                    "SET0001": "f33be75cf349fc99464015fbeefed9afd2cee5e23b39740dc3683c8bb8b6d4ae",
                    "PROP0001": "8124ace5af0f52e8d42e953f78e82f70ddbe5235104af9f0d1e04d7c6d627aa3"}
        for category in design.ASSET_CATEGORIES:
            for item in final[category]:
                value = item["image_prompt"] + "\n" + json.dumps(item["image_format"], ensure_ascii=False, sort_keys=True)
                self.assertEqual(hashlib.sha256(value.encode()).hexdigest(), expected[item["id"]])

    def test_source_and_shared_file_drift_invalidates_plan_and_submission(self):
        baseline, plan = self.simple()
        submission = self.submission(baseline, plan, "set")
        for path in (self.script, self.source, self.work / "ledger.json", self.work / "shared.json", self.work / "video-style.txt"):
            old = path.read_bytes()
            path.write_bytes(old + b" ")
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, "冻结输入已变化"):
                workspace.verify_group(baseline, plan, "set", submission)
            path.write_bytes(old)
        altered = copy.deepcopy(baseline)
        altered["shared_designs"]["CHAR0001"]["styling"] = "另一套服装"
        altered = workspace._seal({key: value for key, value in altered.items() if key != "baseline_sha256"}, "baseline_sha256")
        with self.assertRaisesRegex(ValueError, "原文件不一致"):
            workspace.validate_baseline(altered)

    def test_whole_ledger_validation_cannot_be_bypassed_by_partial_shared_input(self):
        self.data["props"][0]["visible_uses"].pop()
        with self.assertRaisesRegex(ValueError, "至少需要2次"):
            self.freeze("CHAR0001")

    def test_disjoint_complete_ownership_and_actual_output_ids_are_required(self):
        baseline, plan = self.simple()
        for groups, message in (([{"group_id": "one", "owned_asset_ids": ["SET0001"]}], "覆盖全部"),
                                ([{"group_id": "one", "owned_asset_ids": ["SET0001", "PROP0001"]},
                                  {"group_id": "two", "owned_asset_ids": ["PROP0001"]}], "范围冲突"),
                                ([{"group_id": "one", "owned_asset_ids": ["CHAR0001", "SET0001", "PROP0001"]}], "已冻结")):
            with self.subTest(groups=groups), self.assertRaisesRegex(ValueError, message):
                workspace.plan(baseline, groups)
        submission = self.submission(baseline, plan, "set")
        wrong = copy.deepcopy(submission)
        wrong["designs"] = []
        with self.assertRaisesRegex(ValueError, "实际设计ID"):
            workspace.verify_group(baseline, plan, "set", wrong)
        wrong["designs"] = copy.deepcopy(self.payload["designs"])
        with self.assertRaisesRegex(ValueError, "实际设计ID"):
            workspace.verify_group(baseline, plan, "set", wrong)
        wrong["designs"] = submission["designs"] * 2
        with self.assertRaisesRegex(ValueError, "重复资产设计"):
            workspace.verify_group(baseline, plan, "set", wrong)
        with self.assertRaisesRegex(ValueError, "收齐全部"):
            workspace.merge(baseline, plan, [submission])
        with self.assertRaisesRegex(ValueError, "重复"):
            workspace.merge(baseline, plan, [submission, submission])

    def test_shared_overrides_and_stale_plan_or_dependency_hashes_are_rejected(self):
        baseline, plan = self.simple()
        for field, value, message in (("identity_anchors", [], "精确匹配协议"),
                                      ("baseline_sha256", "old", "过期基线"),
                                      ("plan_sha256", "old", "过期基线"),
                                      ("read_only_dependency_sha256", {}, "只读依赖hash")):
            submission = self.submission(baseline, plan, "set")
            submission[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                workspace.verify_group(baseline, plan, "set", submission)
        changed = copy.deepcopy(plan)
        changed["groups"][0]["owned_asset_ids"].append("CHAR0001")
        with self.assertRaisesRegex(ValueError, "plan_sha256"):
            workspace.validate_plan(baseline, changed)

    def decor(self):
        self.paragraphs[1] += "厅内挂上红绸。"
        self.data["source_digest"] = ledger.digest(self.paragraphs)
        text = self.script.read_text(encoding="utf-8").replace("陈默再次拿起青铜令牌，收入怀中。", self.paragraphs[1])
        save(self.script, text)
        save(self.source, {"paragraphs": [{"id": index, "text": value} for index, value in enumerate(self.paragraphs, 1)]})
        self.data["scenes"][1]["scene_asset_id"] = "SET0002"
        self.data["scene_assets"].append({"id": "SET0002", "name": "陈府-红绸布置", "location_id": "LOC0001",
                                          "variant": "decor", "parent_id": "SET0001", "visual_facts": [
                                              {"text": "厅内挂上红绸", "evidence": "厅内挂上红绸", "source_paragraphs": [2],
                                               "scene_ids": ["SC0002"], "attribution": "叙述"}]})
        baseline = self.freeze("SET0001", "CHAR0001", "PROP0001")
        plan = workspace.plan(baseline, [{"group_id": "decor", "owned_asset_ids": ["SET0002"]}])
        record = {"asset_id": "SET0002", "attribution": "制作设计", "basis": "陈府同一房间，仅按当前原文增加红绸",
                  "reviewed_source_facts": True, "space_reference": {
                      "asset_id": "SET0001", "design_sha256": design.fingerprint(baseline["shared_designs"]["SET0001"]),
                      "differences": [{"field": "furnishings", "operation": "append", "new": "厅内挂有红绸",
                                       "source_fact_text": "厅内挂上红绸", "basis": "本场明示挂上红绸，保持其余布局"}]}}
        return baseline, plan, self.submission(baseline, plan, "decor", [record])

    def test_space_version_is_derived_from_frozen_base_plus_grounded_difference(self):
        baseline, plan, submission = self.decor()
        merged = workspace.merge(baseline, plan, [submission])
        value = next(record for record in merged["designs"] if record["asset_id"] == "SET0002")
        base = baseline["shared_designs"]["SET0001"]
        for key in ("space", "materials_palette", "lighting"):
            self.assertEqual(value[key], base[key])
        self.assertEqual(value["furnishings"], base["furnishings"] + "；厅内挂有红绸")
        self.assertNotIn("space_reference", value)
        final = design.apply_design(self.data, merged, self.style)
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, final, require_designed=True)
        empty = copy.deepcopy(submission)
        empty["designs"][0]["space_reference"]["differences"] = []
        report = workspace.verify_group(baseline, plan, "decor", empty)
        for key in workspace.SPACE_FIELDS:
            self.assertEqual(report["designs"][0][key], base[key])

    def test_space_cannot_replace_shared_plan_or_invent_ungrounded_changes(self):
        baseline, plan, submission = self.decor()
        for change, message in ((lambda record: record.update(space="另一种布局"), "不可同时"),
                                (lambda record: record["space_reference"].update(design_sha256="old"), "hash已过期"),
                                (lambda record: record["space_reference"]["differences"][0].update(source_fact_text="十年后墙皮剥落"), "已验证原文"),
                                (lambda record: record.pop("space_reference"), "必须引用")):
            wrong = copy.deepcopy(submission)
            change(wrong["designs"][0])
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                workspace.verify_group(baseline, plan, "decor", wrong)
        # A validated source variant is still not an independent design group
        # while its base spatial plan is owned by another mutable draft.
        plain_shared = self.shared("CHAR0001", "PROP0001")
        baseline = workspace.freeze(ledger.parse_script(self.script), self.paragraphs, self.data, plain_shared, self.style)
        with self.assertRaisesRegex(ValueError, "跨组依赖"):
            workspace.plan(baseline, [{"group_id": "base", "owned_asset_ids": ["SET0001"]},
                                     {"group_id": "decor", "owned_asset_ids": ["SET0002"]}])
        with self.assertRaisesRegex(ValueError, "先冻结基础空间"):
            workspace.plan(baseline, [{"group_id": "both", "owned_asset_ids": ["SET0001", "SET0002"]}])

    def test_stale_carried_design_context_is_never_silently_refreshed(self):
        baseline, plan = self.simple()
        submission = self.submission(baseline, plan, "prop")
        submission["designs"][0]["source_context_digest"] = "old"
        with self.assertRaisesRegex(ValueError, "过期source_context_digest"):
            workspace.verify_group(baseline, plan, "prop", submission)

    def test_serial_freeze_can_accept_a_verified_inherited_space_version(self):
        baseline, plan, submission = self.decor()
        shared = self.shared("CHAR0001", "SET0001", "PROP0001")
        shared["designs"].extend(copy.deepcopy(submission["designs"]))
        next_baseline = workspace.freeze(ledger.parse_script(self.script), self.paragraphs, self.data, shared, self.style)
        self.assertEqual(set(next_baseline["shared_designs"]), {"CHAR0001", "SET0001", "SET0002", "PROP0001"})
        merged = workspace.merge(next_baseline, workspace.plan(next_baseline, []), [])
        self.assertEqual(design.apply_design(self.data, merged, self.style),
                         design.apply_design(self.data, workspace.merge(baseline, plan, [submission]), self.style))

    def test_spatial_replace_requires_a_unique_exact_match(self):
        baseline, plan, submission = self.decor()
        delta = submission["designs"][0]["space_reference"]["differences"][0]
        delta.update(operation="replace", old="两把木椅靠后墙", new="两把木椅靠后墙，后墙挂有红绸")
        report = workspace.verify_group(baseline, plan, "decor", submission)
        self.assertEqual(report["designs"][0]["furnishings"],
                         baseline["shared_designs"]["SET0001"]["furnishings"].replace(delta["old"], delta["new"]))
        delta["old"] = "未出现的空间配置"
        with self.assertRaisesRegex(ValueError, "唯一精确匹配"):
            workspace.verify_group(baseline, plan, "decor", submission)

    def mask(self):
        paragraphs, data, review, text = mask_fixture.fixture()
        data = mask_fixture.variants.apply_variant_review(data, review, paragraphs)
        save(self.script, text)
        anchor = copy.deepcopy(self.payload["identity_anchors"][0])
        anchor["id"] = "阿明"
        payload = {"identity_anchors": [anchor], "identity_assignments": {"CHAR0001": "阿明", "CHAR0002": "阿明"},
                   "designs": [{"asset_id": "CHAR0001", "attribution": "制作设计", "basis": "原文白衬衫，补足稳定身份",
                                "reviewed_source_facts": True, "identity_anchor_id": "阿明", "styling": "白色衬衫，黑色长裤，黑色鞋"},
                               {"asset_id": "CHAR0002", "attribution": "制作设计", "basis": "原文明示戴猪猪头套",
                                "reviewed_source_facts": True, "covering": "粉色软布猪头头套，遮住整张脸和头发",
                                "visible_identity": "仅保留颈肩和基础体型"},
                               dict(self.payload["designs"][1], asset_id="SET0001", basis="原文明示门口，按古装门口设计") ]}
        return paragraphs, data, payload

    def test_mask_and_same_identity_peers_need_same_group_or_frozen_design(self):
        paragraphs, data, payload = self.mask()
        shared = {**payload, "designs": []}
        baseline = workspace.freeze(ledger.parse_script(self.script), paragraphs, data, shared, self.style)
        with self.assertRaisesRegex(ValueError, "跨组依赖"):
            workspace.plan(baseline, [{"group_id": "base", "owned_asset_ids": ["CHAR0001"]},
                                     {"group_id": "mask", "owned_asset_ids": ["CHAR0002", "SET0001"]}])
        plan = workspace.plan(baseline, [{"group_id": "character", "owned_asset_ids": ["CHAR0001", "CHAR0002"]},
                                        {"group_id": "set", "owned_asset_ids": ["SET0001"]}])
        character = self.submission(baseline, plan, "character", payload["designs"][:2])
        spatial = self.submission(baseline, plan, "set", payload["designs"][2:])
        merged = workspace.merge(baseline, plan, [character, spatial])
        full = design.apply_design(data, merged, self.style)
        self.assertEqual(full["characters"][0]["visual_design"]["styling"], full["characters"][1]["visual_design"]["styling"])
        frozen_shared = {**payload, "designs": [payload["designs"][0]]}
        baseline = workspace.freeze(ledger.parse_script(self.script), paragraphs, data, frozen_shared, self.style)
        plan = workspace.plan(baseline, [{"group_id": "mask", "owned_asset_ids": ["CHAR0002"]},
                                        {"group_id": "set", "owned_asset_ids": ["SET0001"]}])
        group = workspace.group_spec(baseline, plan, "mask")
        self.assertEqual(group["read_only_asset_ids"], ["CHAR0001"])
        self.assertIn("anchor:阿明", group["read_only_dependency_sha256"])
        changed = self.submission(baseline, plan, "mask", [payload["designs"][1]])
        changed["designs"][0]["identity_anchor_id"] = "另一个人"
        with self.assertRaisesRegex(ValueError, "不可修改冻结身份"):
            workspace.verify_group(baseline, plan, "mask", changed)

    def test_linked_prop_dependency_must_be_owned_with_mask_or_frozen(self):
        paragraphs, data, payload = self.mask()
        data["characters"][1]["appearance_variant"]["prop_asset_id"] = "PROP0001"
        data["scenes"][0]["prop_ids"] = ["PROP0001"]
        data["props"] = [{"id": "PROP0001", "name": "猪猪头套", "source_name": "猪猪头套", "aliases": [], "visual_facts": []}]
        payload["designs"][1]["covering"] = {"fit": "头套下缘落在颈根", "coverage": "完整遮住脸和头发"}
        prop = {"asset_id": "PROP0001", "attribution": "制作设计", "basis": "原文明示戴上摘下猪猪头套",
                "reviewed_source_facts": True, "structure": "圆鼓猪头头套，圆耳和凸猪鼻，底部开口",
                "materials_palette": "粉色短绒，浅灰内衬", "scale": "适合成年头部大小", "state": "完整未损"}
        payload["designs"].append(prop)
        baseline = workspace.freeze(ledger.parse_script(self.script), paragraphs, data,
                                    {**payload, "designs": [payload["designs"][0]]}, self.style)
        with self.assertRaisesRegex(ValueError, "跨组依赖"):
            workspace.plan(baseline, [{"group_id": "mask", "owned_asset_ids": ["CHAR0002"]},
                                     {"group_id": "rest", "owned_asset_ids": ["PROP0001", "SET0001"]}])
        baseline = workspace.freeze(ledger.parse_script(self.script), paragraphs, data,
                                    {**payload, "designs": [payload["designs"][0], prop]}, self.style)
        plan = workspace.plan(baseline, [{"group_id": "mask", "owned_asset_ids": ["CHAR0002"]},
                                        {"group_id": "set", "owned_asset_ids": ["SET0001"]}])
        self.assertEqual(workspace.group_spec(baseline, plan, "mask")["read_only_asset_ids"], ["CHAR0001", "PROP0001"])
        submission = self.submission(baseline, plan, "mask", [payload["designs"][1]])
        report = workspace.verify_group(baseline, plan, "mask", submission)
        self.assertIn("粉色短绒", report["previews"][0]["image_prompt"])
        merged = workspace.merge(baseline, plan, [submission, self.submission(baseline, plan, "set", [payload["designs"][2]])])
        self.assertEqual(design.apply_design(data, merged, self.style), design.apply_design(data, payload, self.style))

    def test_full_identity_assignment_is_explicit_and_cannot_disagree_with_body(self):
        shared = self.shared("CHAR0001")
        shared["identity_assignments"] = {}
        with self.assertRaisesRegex(ValueError, "精确覆盖全部"):
            workspace.freeze(ledger.parse_script(self.script), self.paragraphs, self.data, shared, self.style)
        paragraphs, data, payload = self.mask()
        payload["identity_anchors"].append(dict(payload["identity_anchors"][0], id="另一个身体"))
        payload["identity_assignments"]["CHAR0002"] = "另一个身体"
        with self.assertRaisesRegex(ValueError, "共用基础身份锚点"):
            workspace.freeze(ledger.parse_script(self.script), paragraphs, data, {**payload, "designs": []}, self.style)

    def test_cli_real_files_freeze_plan_verify_and_merge(self):
        save(self.work / "shared.json", self.shared("CHAR0001"))
        command = SKILL / "modules/screenplay-standardizer/scripts/design_workspace.py"

        def run(*args):
            result = subprocess.run([sys.executable, str(command), *map(str, args)], capture_output=True,
                                    text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

        baseline = run("freeze", self.script, self.source, self.work / "ledger.json", self.work / "shared.json", self.work / "video-style.txt")
        save(self.work / "baseline.json", baseline)
        save(self.work / "groups.json", [{"group_id": "remaining", "owned_asset_ids": ["SET0001", "PROP0001"]}])
        plan = run("plan", self.work / "baseline.json", self.work / "groups.json")
        save(self.work / "plan.json", plan)
        save(self.work / "submission.json", self.submission(baseline, plan, "remaining"))
        report = run("verify-group", self.work / "baseline.json", self.work / "plan.json", self.work / "submission.json", "--group-id", "remaining")
        self.assertEqual(report["owned_asset_ids"], ["PROP0001", "SET0001"])
        merged = run("merge", self.work / "baseline.json", self.work / "plan.json", self.work / "submission.json")
        self.assertEqual(len(merged["designs"]), 3)
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, design.apply_design(self.data, merged, self.style), require_designed=True)


if __name__ == "__main__":
    unittest.main()
