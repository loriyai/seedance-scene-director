#!/usr/bin/env python3
"""Offline integration checks for ledger inventory, empty references and recovery."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import canvas as c


def add_node(doc, node_id, kind, name, image=None, generation=None):
    c.nodes(doc)[node_id] = {"type": c.KINDS[kind]}
    data = c.initial_data({"name": name, "kind": kind})
    if image:
        if kind in ("role", "scene"):
            data["appearanceInfo"] = {"pippitAssetId": image}
        else:
            data["pippitAssetId"] = image
    if generation is not None:
        data["generation"] = copy.deepcopy(generation)
    doc["assets"][node_id] = {"content": data}


class WorkflowCLI:
    """Mirror only documented SDK mutation shapes; never invoke the real CLI."""
    def __init__(self, doc):
        self.doc = copy.deepcopy(doc)
        self.calls, self.writes, self.allocations = [], [], []
        self.omit_edges_for = set()
        self.reject_prompts_for = set()
        self.ignore_prompt_for = set()
        self.ignore_create_names = set()

    def auth(self):
        return {"identity": "USER"}

    def allocate(self):
        value = "ALLOC" + str(len(self.allocations) + 1)
        self.allocations.append(value)
        return value

    def command(self, canvas_id, name, value=None):
        self.calls.append(name)
        if name == "get_snapshot":
            return {"document": copy.deepcopy(self.doc)}
        self.writes.append((name, copy.deepcopy(value)))
        if name == "create_biz_node":
            node_id = value["id"]
            initial = value["initialData"]
            if initial.get("nodeName", initial.get("name")) in self.ignore_create_names:
                return {}
            c.nodes(self.doc)[node_id] = {"type": c.KINDS[value["nodeKind"]], **value["topLeft"]}
            self.doc["assets"][node_id] = {"content": copy.deepcopy(value["initialData"])}
            return {}
        if name == "update_node_data":
            c.content(self.doc, value["id"]).update(copy.deepcopy(value["patch"]))
            return {}
        if name == "delete_edges":
            edges = self.doc["assets"]["ROOT"]["content"]["edges"]
            for edge_id in value["ids"]:
                edges.pop(edge_id, None)
            return {}
        if name == "xyq.generation.update_prompt":
            target = value["nodeId"]
            if target in self.reject_prompts_for:
                c.fail("COMMAND_REJECTED", "fake definite rejection")
            if target in self.ignore_prompt_for:
                return {}  # Acknowledged command but no verified text: stop as uncertain.
            generation = c.content(self.doc, target).setdefault("generation", {})
            generation["prompt"] = value["prompt"]
            references = generation.setdefault("references", [])
            edges = self.doc["assets"]["ROOT"]["content"]["edges"]
            for source in c.node_asset_ids(value["prompt"]):
                if target in self.omit_edges_for:
                    continue
                present = any(edge.get("source") == source and edge.get("target") == target
                              and edge.get("type") == "reference" for edge in edges.values())
                if present:
                    continue  # The SDK's existing-edge noop causes the post-upload row gap.
                edge_id = source + "-" + target
                edges[edge_id] = {"id": edge_id, "source": source, "target": target, "type": "reference"}
                if c.kind_of(c.nodes(self.doc)[source]) == "role" and c.role_reference_media(self.doc, source, target):
                    references.append({"sourceNodeId": source, "nodeAssetId": source, "id": source, "type": "image"})
            return {}
        raise AssertionError("Unexpected fake CLI mutation: " + name)


class ReferenceWorkflowChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.session = {"ledger": {"schema_version": 3,
            "characters": [{"id": "CHAR1", "name": "Actor"}],
            "scene_assets": [{"id": "SET1", "name": "Room"}],
            "scenes": [{"id": "SC1", "scene_asset_id": "SET1", "prop_ids": ["PROP1"]}],
            "props": [{"id": "PROP1", "name": "Pen"}]}, "receipts": [], "units": []}
        for index in (1, 2):
            self.session["units"].append({"id": "场景1-分镜" + str(index), "version": 1, "duration": 4,
                "character_ids": ["CHAR1"], "scene_ids": ["SC1"], "prop_ids": ["PROP1"],
                "global": "standing", "shots": [], "test_prompt": "@Actor @Room @Pen action\n@黑幕"})
        self.doc = {"rootCanvasId": "ROOT", "assets": {"ROOT": {"type": "canvas", "content": {
            "nodes": {}, "edges": {}, "settings": {"readOnly": False}}}}}
        add_node(self.doc, "BLACK", "image", "黑幕")
        add_node(self.doc, "EXTRA", "image", "Unrelated", "UNCHANGED")
        self.state = c.blank_state()
        self.state["binding"] = {"canvas_id": "ROOT", "account_id": "USER"}
        c.save(self.project, self.state)
        self.cli = WorkflowCLI(self.doc)

    def sync(self, ids=None, **kwargs):
        ids = ids or [unit["id"] for unit in self.session["units"]]
        def render(session, unit, reference_text=None):
            return reference_text(unit["test_prompt"]) if reference_text else unit["test_prompt"]
        with patch.object(c.d, "load", return_value=self.session), patch.object(c.d, "render_unit", side_effect=render), \
             patch.object(c.media, "model_settings", return_value=({"modelKey": "seedance2.5", "resolution": "720p"},
                 {"duration": {"min": 4, "max": 30, "step": 1}})):
            return c.sync(self.project, self.cli, "videos", ids=ids, **kwargs)

    def prepared(self):
        for key, nid, kind, name in (("CHAR1", "ROLE", "role", "Actor"), ("SET1", "SET", "scene", "Room"),
                                    ("PROP1", "PROP", "image", "Pen")):
            add_node(self.cli.doc, nid, kind, name)
            self.state["mappings"][key] = {"node_id": nid, "kind": kind, "name": name, "stage": "ready"}
        self.state["black_node_id"] = "BLACK"
        for unit in self.session["units"]:
            uid = unit["id"]
            add_node(self.cli.doc, uid, "video", uid, generation={"prompt": "", "references": [],
                "modelKey": "seedance2.5", "resolution": "720p", "durationMs": 4000})
            self.state["mappings"][uid] = {"node_id": uid, "kind": "video", "name": uid, "stage": "ready",
                "prompt_hash": c.digest(""), "references_bound": False, "version": 1}
        c.save(self.project, self.state)

    def test_unmapped_existing_assets_are_adopted_by_unique_name_and_kind(self):
        for nid, kind, name in (("ROLE", "role", "Actor"), ("SET", "scene", "Room"), ("PROP", "image", "Pen")):
            add_node(self.cli.doc, nid, kind, name)
        extra_before = c.fingerprint(self.cli.doc, "EXTRA")
        result = self.sync(ids=[self.session["units"][0]["id"]])
        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["asset_reconciliation"]["adopted"], ["CHAR1", "SET1", "PROP1"])
        self.assertEqual(result["asset_reconciliation"]["created"], [])
        self.assertEqual(len(self.cli.allocations), 1)  # Only the video node was new.
        state = c.read_state(self.project)
        target = state["mappings"][self.session["units"][0]["id"]]["node_id"]
        self.assertEqual(set(c.node_asset_ids(c.prompt_of(self.cli.doc, target))), {"ROLE", "SET", "PROP", "BLACK"})
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, target, c.prompt_of(self.cli.doc, target)))
        self.assertEqual(c.fingerprint(self.cli.doc, "EXTRA"), extra_before)
        self.assertTrue(Path(result["reference_report"]).is_file())
        self.assertEqual({issue["key"] for issue in result["reference_issues"] if issue["reason"] == "missing_image"},
                         {"CHAR1", "SET1", "PROP1", "BLACK"})

    def test_missing_asset_nodes_are_created_including_unused_ledger_assets(self):
        self.session["ledger"]["characters"].append({"id": "CHAR2", "name": "Unused"})
        result = self.sync(ids=[self.session["units"][0]["id"]])
        state = c.read_state(self.project)
        self.assertEqual(result["asset_reconciliation"]["created"], ["CHAR1", "CHAR2", "SET1", "PROP1"])
        self.assertEqual(state["mappings"]["CHAR2"]["kind"], "role")
        self.assertEqual(c.name_of(self.cli.doc, state["mappings"]["PROP1"]["node_id"]), "Pen")
        self.assertTrue(all(entry["status"] == "verified" for entry in state["journal"]))
        target = state["mappings"][self.session["units"][0]["id"]]["node_id"]
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, target, c.prompt_of(self.cli.doc, target)))
        self.assertTrue(state["mappings"][self.session["units"][0]["id"]]["references_bound"])

    def test_stale_id_reuses_unique_remote_asset_without_duplicates(self):
        add_node(self.cli.doc, "RIGHT", "role", "Actor")
        self.state["mappings"]["CHAR1"] = {"node_id": "DELETED", "kind": "role", "name": "Actor", "stage": "ready"}
        c.save(self.project, self.state)
        self.sync(ids=[self.session["units"][0]["id"]])
        self.assertEqual(c.read_state(self.project)["mappings"]["CHAR1"]["node_id"], "RIGHT")
        self.assertEqual([nid for nid in c.nodes(self.cli.doc) if c.name_of(self.cli.doc, nid) == "Actor"], ["RIGHT"])

    def test_preview_plans_adoption_and_creation_without_persisting_or_writing(self):
        add_node(self.cli.doc, "ROLE", "role", "Actor")
        before = c.state_path(self.project).read_bytes()
        result = self.sync(preview=True)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["asset_reconciliation"]["adopted"], ["CHAR1"])
        self.assertEqual(result["asset_reconciliation"]["created"], ["SET1", "PROP1"])
        self.assertEqual(c.state_path(self.project).read_bytes(), before)
        self.assertEqual(self.cli.writes, [])
        self.assertEqual(self.cli.allocations, [])
        self.assertFalse((self.project / "canvas" / "reference-check.json").exists())

    def test_same_name_duplicates_wrong_type_and_manual_rename_are_conflicts(self):
        add_node(self.cli.doc, "R1", "role", "Actor")
        add_node(self.cli.doc, "R2", "role", "Actor")
        add_node(self.cli.doc, "S_WRONG", "image", "Room")
        add_node(self.cli.doc, "P_EDITED", "image", "Pen renamed")
        self.state["mappings"]["PROP1"] = {"node_id": "P_EDITED", "kind": "image", "name": "Pen", "stage": "ready"}
        c.save(self.project, self.state)
        result = self.sync(ids=[self.session["units"][0]["id"]])
        conflicts = {row["key"]: row["reason"] for row in result["asset_reconciliation"]["conflicts"]}
        self.assertEqual(conflicts, {"CHAR1": "ambiguous_node", "SET1": "same_name_wrong_kind", "PROP1": "node_name_changed"})
        state = c.read_state(self.project)
        target = state["mappings"][self.session["units"][0]["id"]]["node_id"]
        self.assertEqual(set(c.node_asset_ids(c.prompt_of(self.cli.doc, target))), {"BLACK"})
        self.assertIn("@Actor ", c.prompt_of(self.cli.doc, target))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(self.cli.allocations), 1)
        self.assertEqual(c.name_of(self.cli.doc, "P_EDITED"), "Pen renamed")

    def test_explicit_reuse_resolves_ambiguous_name_with_current_fingerprint(self):
        add_node(self.cli.doc, "R1", "role", "Actor")
        add_node(self.cli.doc, "R2", "role", "Actor")
        result = self.sync(ids=[self.session["units"][0]["id"]], decisions={"CHAR1": {
            "reuse": "R2", "fingerprint": c.fingerprint(self.cli.doc, "R2")}})
        self.assertEqual(result["status"], "synced")
        self.assertEqual(c.read_state(self.project)["mappings"]["CHAR1"]["node_id"], "R2")
        self.assertIn("R1", c.nodes(self.cli.doc))
        self.assertEqual(result["asset_reconciliation"]["conflicts"], [])

    def test_explicit_correct_reuse_can_replace_a_renamed_or_retyped_mapping(self):
        add_node(self.cli.doc, "OLD", "image", "Actor manually changed")
        add_node(self.cli.doc, "RIGHT", "role", "Actor")
        old_before = c.fingerprint(self.cli.doc, "OLD")
        self.state["mappings"]["CHAR1"] = {"node_id": "OLD", "kind": "role", "name": "Actor", "stage": "ready"}
        c.save(self.project, self.state)
        result = self.sync(ids=[self.session["units"][0]["id"]], decisions={"CHAR1": {
            "reuse": "RIGHT", "fingerprint": c.fingerprint(self.cli.doc, "RIGHT")}})
        self.assertEqual(result["status"], "synced")
        self.assertEqual(c.read_state(self.project)["mappings"]["CHAR1"]["node_id"], "RIGHT")
        self.assertEqual(c.fingerprint(self.cli.doc, "OLD"), old_before)

    def test_stale_reuse_fingerprint_is_rejected_without_adopting_candidate(self):
        add_node(self.cli.doc, "R1", "role", "Actor")
        add_node(self.cli.doc, "R2", "role", "Actor")
        result = self.sync(ids=[self.session["units"][0]["id"]], decisions={"CHAR1": {
            "reuse": "R2", "fingerprint": "stale"}})
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("CHAR1", c.read_state(self.project)["mappings"])
        self.assertEqual(result["asset_reconciliation"]["conflicts"][0]["reason"], "reuse_fingerprint_changed")

    def test_known_missing_edge_keeps_prompt_and_continues_next_video(self):
        self.prepared()
        first, second = [unit["id"] for unit in self.session["units"]]
        self.cli.omit_edges_for.add(first)
        result = self.sync()
        self.assertEqual(result["status"], "partial")
        self.assertTrue(c.prompt_of(self.cli.doc, first))
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, second, c.prompt_of(self.cli.doc, second)))
        state = c.read_state(self.project)
        self.assertEqual(state["pending_units"], [first])
        self.assertTrue(all(entry["status"] == "verified" for entry in state["journal"]))
        self.assertFalse(state["mappings"][first]["references_bound"])
        self.assertTrue(state["mappings"][second]["references_bound"])
        self.assertIn("reference_failed", {row["reason"] for row in result["failures"]})

    def test_definitely_rejected_prompt_keeps_previous_text_and_continues(self):
        self.prepared()
        first, second = [unit["id"] for unit in self.session["units"]]
        self.cli.reject_prompts_for.add(first)
        result = self.sync()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(c.prompt_of(self.cli.doc, first), "")
        self.assertTrue(c.prompt_of(self.cli.doc, second))
        self.assertIn("rejected", {entry["status"] for entry in c.read_state(self.project)["journal"]})
        self.assertTrue(Path(result["reference_report"]).is_file())

    def test_unknown_text_write_stops_batch_and_saves_report(self):
        self.prepared()
        first, second = [unit["id"] for unit in self.session["units"]]
        self.cli.ignore_prompt_for.add(first)
        with self.assertRaises(c.CanvasError) as caught:
            self.sync()
        self.assertEqual(caught.exception.code, "UNCERTAIN_WRITE")
        state = c.read_state(self.project)
        self.assertEqual(state["journal"][-1]["status"], "uncertain")
        self.assertEqual(c.prompt_of(self.cli.doc, second), "")
        self.assertTrue(Path(caught.exception.details["reference_report"]).is_file())
        self.assertTrue((self.project / "canvas" / "reference-check.json").is_file())
        failure = state["last_reference_failures"][-1]
        self.assertEqual((failure["key"], failure["node_id"]), (first, first))
        report = Path(caught.exception.details["reference_report"]).read_text(encoding="utf-8")
        self.assertNotIn("未定位", report)
        self.assertIn("资产或分镜：" + first + "；节点ID：" + first, report)

    def test_existing_only_reference_refresh_does_not_adopt_or_expand_assets(self):
        self.prepared()
        self.session["ledger"]["characters"].append({"id": "CHAR2", "name": "Unmapped"})
        add_node(self.cli.doc, "UNMAPPED", "role", "Unmapped")
        self.sync(ids=[self.session["units"][0]["id"]], existing_only=True, references_only=True)
        state = c.read_state(self.project)
        self.assertNotIn("CHAR2", state["mappings"])
        self.assertEqual(self.cli.allocations, [])
        self.assertFalse(any(name == "create_biz_node" for name, _ in self.cli.writes))

    def test_unknown_asset_creation_and_resume_report_exact_asset_and_allocated_id(self):
        self.cli.ignore_create_names.add("Actor")
        for attempt in (1, 2):
            with self.subTest(attempt=attempt), self.assertRaises(c.CanvasError) as caught:
                self.sync()
            self.assertEqual(caught.exception.code, "UNCERTAIN_WRITE")
            state = c.read_state(self.project)
            failure = state["last_reference_failures"][-1]
            self.assertEqual((failure["key"], failure["name"], failure["node_id"]), ("CHAR1", "Actor", "ALLOC1"))
            report = Path(caught.exception.details["reference_report"]).read_text(encoding="utf-8")
            self.assertNotIn("未定位", report)
            self.assertIn("资产或分镜：Actor；节点ID：ALLOC1", report)
        self.assertEqual(self.cli.allocations, ["ALLOC1"])

    def test_later_role_image_upload_refreshes_only_that_old_reference_edge(self):
        self.prepared()
        uid = self.session["units"][0]["id"]
        self.sync(ids=[uid])
        edges = self.cli.doc["assets"]["ROOT"]["content"]["edges"]
        self.assertEqual(c.content(self.cli.doc, uid)["generation"]["references"], [])
        preserved = {key: copy.deepcopy(value) for key, value in edges.items() if value["source"] != "ROLE"}
        c.content(self.cli.doc, "ROLE")["appearanceInfo"] = {"pippitAssetId": "UPLOADED_ROLE"}
        self.cli.writes.clear()
        result = self.sync(ids=[uid], existing_only=True, references_only=True)
        self.assertEqual(result["status"], "synced")
        self.assertEqual(self.cli.writes[0], ("delete_edges", {"ids": ["ROLE-" + uid]}))
        self.assertTrue(c.role_reference_row(c.content(self.cli.doc, uid)["generation"]["references"], "ROLE"))
        self.assertEqual({key: value for key, value in edges.items() if value["source"] != "ROLE"}, preserved)

    def test_empty_black_missing_or_ambiguous_is_never_generated(self):
        c.nodes(self.cli.doc).pop("BLACK")
        self.cli.doc["assets"].pop("BLACK")
        result = self.sync(ids=[self.session["units"][0]["id"]])
        self.assertEqual(result["status"], "partial")
        self.assertFalse(any(c.name_of(self.cli.doc, nid) == "黑幕" for nid in c.nodes(self.cli.doc)))
        self.assertIn("BLACK", {row["key"] for row in result["pending_references"]})
        self.assertFalse(any("generate" in name for name, _ in self.cli.writes))

    def test_ambiguous_black_is_not_adopted_or_substituted(self):
        add_node(self.cli.doc, "BLACK2", "image", "黑幕", "SECOND")
        result = self.sync(ids=[self.session["units"][0]["id"]])
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("black_node_id", c.read_state(self.project))
        pending = next(row for row in result["pending_references"] if row["key"] == "BLACK")
        self.assertEqual(pending["reason"], "ambiguous_node")
        self.assertEqual(set(pending["matches"]), {"BLACK", "BLACK2"})

    def test_inventory_is_once_then_only_required_before_write_and_readback(self):
        self.prepared()
        self.sync(ids=[self.session["units"][0]["id"]])
        # One connected inventory, one target concurrency read, one prompt readback.
        self.assertEqual(self.cli.calls.count("get_snapshot"), 3)
        self.assertEqual([name for name, _ in self.cli.writes], ["xyq.generation.update_prompt"])

    def test_one_bad_prompt_encoding_does_not_block_the_other_unit(self):
        self.prepared()
        first, second = [unit["id"] for unit in self.session["units"]]
        def render(session, unit, reference_text=None):
            if unit["id"] == first:
                raise ValueError("fake invalid renderer input")
            return reference_text(unit["test_prompt"]) if reference_text else unit["test_prompt"]
        with patch.object(c.d, "load", return_value=self.session), patch.object(c.d, "render_unit", side_effect=render), \
             patch.object(c.media, "model_settings", return_value=({"modelKey": "seedance2.5", "resolution": "720p"},
                 {"duration": {"min": 4, "max": 30, "step": 1}})):
            result = c.sync(self.project, self.cli, "videos", ids=[first, second])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(c.prompt_of(self.cli.doc, first), "")
        self.assertTrue(c.prompt_references_satisfied(self.cli.doc, second, c.prompt_of(self.cli.doc, second)))
        self.assertIn("prompt_encoding_failed", {row["reason"] for row in result["failures"]})
        self.assertTrue(Path(result["reference_report"]).is_file())


if __name__ == "__main__":
    unittest.main()
