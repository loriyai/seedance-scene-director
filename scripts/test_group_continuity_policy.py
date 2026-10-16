#!/usr/bin/env python3
"""Reviewed v2 group selection, source evidence, compatibility and migration."""

import copy
import tempfile
import unittest
from pathlib import Path

from test_asset_visual_design import SKILL, fixture, save, ledger, migration, design
import test_group_visual_policy as legacy


class GroupContinuityPolicyTests(unittest.TestCase):
    def setUp(self):
        root = SKILL.parent / "tests-group-continuity"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)

    def tearDown(self):
        self.temp.cleanup()

    def add_group(self, basis="core_continuity", occurrences=(1,)):
        mode = "prompt_only" if basis == "prompt_only" else "image_asset"
        group = legacy.GroupVisualPolicyTests.add_group(self, mode, occurrences)
        self.data["group_policy"] = design.GROUP_POLICY
        group["extraction_review"].update(
            selection_basis=basis, requires_reference=mode == "image_asset",
            story_role="门边村民围观是这一段反应镜头的可见主体",
            consistency_reason="同一围观过程的正反打须固定队列轮廓、前后层次与衣着配色",
            text_sufficiency_reason="远景只呈现泛化围观人群，无需匹配具体脸部、固定队列或个人动作；人数层次与衣着类别可由文字保持")
        return group

    def validate(self, data=None):
        ledger.validate(ledger.parse_script(self.script), self.paragraphs, data or self.data)

    def review_payload(self, group, policy=None):
        payload = {"groups": [{"character_id": group["id"], **{
            key: copy.deepcopy(group[key]) for key in ("visual_mode", "extraction_review", "visible_uses")}}]}
        if policy is not None:
            payload["group_policy"] = policy
        return payload

    def test_core_one_visible_event_can_have_image_and_stable_cast_identity(self):
        group = self.add_group()
        self.validate()
        result = design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")
        self.validate(result)
        self.assertIn("image_prompt", result["characters"][0])
        self.assertEqual(result["scenes"][0]["character_ids"], [group["id"], "CHAR0001"])

    def test_reusable_one_event_and_renamed_duplicate_fail(self):
        group = self.add_group("reusable")
        with self.assertRaisesRegex(ValueError, "至少需要2次独立可见出场"):
            self.validate()
        group["visible_uses"].append(dict(group["visible_uses"][0], event_id="镜头二"))
        with self.assertRaisesRegex(ValueError, "证据重叠"):
            self.validate()

    def test_reusable_two_independent_events_pass(self):
        self.add_group("reusable", (1, 2))
        self.validate()

    def test_core_requires_story_role_and_consistency_reason(self):
        group = self.add_group()
        for field in ("story_role", "consistency_reason"):
            with self.subTest(field=field):
                old = group["extraction_review"].pop(field)
                with self.assertRaises(ValueError):
                    self.validate()
                group["extraction_review"][field] = old

    def test_core_requires_located_visible_evidence(self):
        group = self.add_group()
        group["visible_uses"] = []
        with self.assertRaisesRegex(ValueError, "至少需要1次"):
            self.validate()

    def test_prompt_only_requires_exclusion_and_text_sufficiency(self):
        group = self.add_group("prompt_only")
        self.validate()
        for field in ("text_sufficiency_reason", "exclusion_reason"):
            with self.subTest(field=field):
                old = group["extraction_review"].pop(field)
                with self.assertRaises(ValueError):
                    self.validate()
                group["extraction_review"][field] = old

    def test_requires_reference_is_strict_bool_and_matches_image_mode(self):
        group = self.add_group()
        for value in (False, None, 1, "true"):
            with self.subTest(value=value):
                group["extraction_review"]["requires_reference"] = value
                with self.assertRaisesRegex(ValueError, "requires_reference"):
                    self.validate()

    def test_reference_required_group_cannot_be_prompt_only(self):
        group = self.add_group("prompt_only")
        group["extraction_review"]["requires_reference"] = True
        with self.assertRaisesRegex(ValueError, "requires_reference=false"):
            self.validate()

    def test_selection_and_visual_mode_must_agree(self):
        group = self.add_group()
        group["extraction_review"]["selection_basis"] = "prompt_only"
        with self.assertRaisesRegex(ValueError, "图片须选择"):
            self.validate()
        group["visual_mode"] = "prompt_only"
        group["extraction_review"].update(selection_basis="core_continuity", requires_reference=False)
        with self.assertRaisesRegex(ValueError, "仅文字群像"):
            self.validate()

    def test_unknown_or_missing_selection_is_not_inferred(self):
        group = self.add_group()
        for value in (None, "one_event", ""):
            with self.subTest(value=value):
                group["extraction_review"]["selection_basis"] = value
                with self.assertRaisesRegex(ValueError, "selection_basis"):
                    self.validate()

    def test_core_does_not_bypass_independent_speech(self):
        self.add_group()
        save(self.script, self.script.read_text(encoding="utf-8") + "村民-群像OS：这里有人。\n")
        with self.assertRaisesRegex(ValueError, "具有独立台词"):
            self.validate()

    def test_named_character_cannot_be_demoted_by_v2_review_fields(self):
        group = self.add_group("prompt_only")
        named = next(item for item in self.data["characters"] if item["id"] == "CHAR0001")
        named.update(visual_mode="prompt_only", extraction_review=copy.deepcopy(group["extraction_review"]))
        with self.assertRaisesRegex(ValueError, "仅无姓名"):
            self.validate()

    def test_review_defaults_keep_existing_v1_and_untagged_legacy(self):
        group = legacy.GroupVisualPolicyTests.add_group(self, "image_asset", (1, 2))
        payload = self.review_payload(group)
        result = design.apply_group_review(self.data, payload)
        self.assertEqual(result["group_policy"], design.LEGACY_GROUP_POLICY)
        self.validate(result)
        self.data.pop("group_policy")
        result = design.apply_group_review(self.data, payload)
        self.assertEqual(result["group_policy"], design.LEGACY_GROUP_POLICY)
        self.assertNotIn("selection_basis", result["characters"][0]["extraction_review"])

    def test_review_defaults_keep_v2_and_explicit_upgrade_is_supported(self):
        group = self.add_group()
        payload = self.review_payload(group)
        result = design.apply_group_review(self.data, payload)
        self.assertEqual(result["group_policy"], design.GROUP_POLICY)
        self.data["group_policy"] = design.LEGACY_GROUP_POLICY
        result = design.apply_group_review(self.data, self.review_payload(group, design.GROUP_POLICY))
        self.assertEqual(result["group_policy"], design.GROUP_POLICY)
        self.validate(result)
        self.assertEqual(self.data["group_policy"], design.LEGACY_GROUP_POLICY)

    def test_explicit_upgrade_does_not_infer_missing_review_fields(self):
        group = legacy.GroupVisualPolicyTests.add_group(self, "prompt_only")
        with self.assertRaisesRegex(ValueError, "selection_basis"):
            design.apply_group_review(self.data, self.review_payload(group, design.GROUP_POLICY))

    def test_explicit_null_or_unknown_policy_is_rejected(self):
        group = self.add_group()
        for policy in (None, "unsupported"):
            with self.subTest(policy=policy):
                payload = self.review_payload(group)
                payload["group_policy"] = policy
                with self.assertRaisesRegex(ValueError, "不支持的群像"):
                    design.apply_group_review(self.data, payload)

    def test_policy_upgrade_changes_group_digest_but_not_unrelated_character(self):
        group = self.add_group("reusable", (1, 2))
        named = next(item for item in self.data["characters"] if item["id"] == "CHAR0001")
        current = design.source_context_digest(self.data, "characters", group)
        named_current = design.source_context_digest(self.data, "characters", named)
        self.data["group_policy"] = design.LEGACY_GROUP_POLICY
        self.assertNotEqual(current, design.source_context_digest(self.data, "characters", group))
        self.assertEqual(named_current, design.source_context_digest(self.data, "characters", named))

    def test_legacy_group_digest_matches_prechange_baseline(self):
        group = legacy.GroupVisualPolicyTests.add_group(self, "image_asset", (1, 2))
        # Recorded from v0.11-prop-policy.1 before the group policy change.
        self.assertEqual(design.source_context_digest(self.data, "characters", group),
                         "22f0376b73fe02f13e6058784850f7ec0a4795184f3f27cb7868ccfdf6ea8fc1")

    def test_policy_change_requires_recompile_even_when_review_fields_are_unchanged(self):
        self.add_group("reusable", (1, 2))
        self.data["group_policy"] = design.LEGACY_GROUP_POLICY
        result = design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")
        result["group_policy"] = design.GROUP_POLICY
        with self.assertRaisesRegex(ValueError, "出处或场次映射"):
            self.validate(result)

    def test_selection_review_change_invalidates_compiled_group_design(self):
        group = self.add_group("reusable", (1, 2))
        result = design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")
        result["characters"][0]["extraction_review"]["selection_basis"] = "core_continuity"
        with self.assertRaisesRegex(ValueError, "出处或场次映射"):
            self.validate(result)

    def test_unchanged_core_migration_keeps_mode_review_and_one_use(self):
        group = self.add_group()
        self.data = design.apply_design(self.data, self.payload, "3D国漫写实厚涂风格")
        old = self.work / "old-ledger.json"
        save(old, self.data)
        result, report = migration.migrate(old, self.source, self.script, self.work, {})
        self.assertEqual(report["pending_reviews"], [])
        migrated = next(item for item in result["characters"] if item["id"] == group["id"])
        self.assertEqual(migrated["extraction_review"], group["extraction_review"])
        self.assertEqual(migrated["visible_uses"], group["visible_uses"])
        self.assertEqual(migrated["visual_mode"], "image_asset")
        self.validate(result)

    def test_changed_source_migration_preserves_mode_and_old_digest_but_requires_review(self):
        group = self.add_group()
        old = self.work / "old-ledger.json"
        old_source = self.work / "old-source.json"
        save(old, self.data)
        save(old_source, self.source.read_text(encoding="utf-8"))
        self.paragraphs[1] += "陈默随后离开。"
        save(self.source, {"paragraphs": [{"id": n, "text": p} for n, p in enumerate(self.paragraphs, 1)]})
        result, report = migration.migrate(old, old_source, self.script, self.work,
                                          {"scenes": {"SC0001": [1], "SC0002": [2]}})
        migrated = next(item for item in result["characters"] if item["id"] == group["id"])
        self.assertEqual(migrated["visual_mode"], "image_asset")
        self.assertEqual(migrated["visible_uses"], group["visible_uses"])
        self.assertEqual(migrated["extraction_review"]["source_digest"], self.data["source_digest"])
        self.assertIs(migrated["extraction_review"]["reviewed_full_script"], False)
        self.assertTrue(any(item["kind"] == "群像全剧筛选待复核" for item in report["pending_reviews"]))

    def test_scene_relocation_without_source_change_invalidates_review(self):
        group = self.add_group()
        result = copy.deepcopy(self.data)
        result["scenes"][0]["source_range"] = [1, 2]
        pending = []
        migration.preserve_group_reviews(self.data, result, self.paragraphs, ledger.parse_script(self.script), pending)
        self.assertEqual(result["characters"][0]["visual_mode"], group["visual_mode"])
        self.assertIs(result["characters"][0]["extraction_review"]["reviewed_full_script"], False)
        self.assertEqual(pending[0]["kind"], "群像全剧筛选待复核")

    def test_migration_does_not_auto_promote_prompt_only(self):
        group = self.add_group("prompt_only", (1, 2))
        old = self.work / "old-ledger.json"
        save(old, self.data)
        result, report = migration.migrate(old, self.source, self.script, self.work, {})
        self.assertEqual(report["pending_reviews"], [])
        self.assertEqual(result["characters"][0]["visual_mode"], "prompt_only")
        self.assertEqual(result["characters"][0]["extraction_review"], group["extraction_review"])

    def test_reusable_migration_still_flags_one_visible_event(self):
        self.add_group("reusable")
        old = self.work / "old-ledger.json"
        save(old, self.data)
        result, report = migration.migrate(old, self.source, self.script, self.work, {})
        self.assertEqual(result["characters"][0]["visual_mode"], "image_asset")
        self.assertEqual(result["characters"][0]["extraction_review"]["selection_basis"], "reusable")
        self.assertTrue(any(item["kind"] == "群像图片复用门槛待核" for item in report["pending_reviews"]))
        self.assertIs(result["characters"][0]["extraction_review"]["reviewed_full_script"], False)


if __name__ == "__main__":
    unittest.main()
