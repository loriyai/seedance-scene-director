"""Official-SDK offline execution against an in-memory transaction server.

No login, network, production files or real Canvas writes are used.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import canvas as c
import canvas_batch as batch
import canvas_job_backend as backend
import director as d
from test_canvas_job_backend import AssetJobCLI


def installed_entry():
    explicit = os.environ.get("SEEDANCE_TEST_CANVAS_ENTRY")
    candidates = [Path(explicit)] if explicit else []
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "npm/node_modules/@pippit-dev/cli/scripts/run.js")
    return next((p for p in candidates if p.is_file()), None)


ENTRY = installed_entry()


def apply_patch(doc, patch):
    asset_id = patch["asset_id"]
    if patch["path"] == "":
        if patch["op"] == "remove":
            doc["assets"].pop(asset_id, None)
        else:
            doc["assets"][asset_id] = copy.deepcopy(patch["value"])
        return
    parts = [p.replace("~1", "/").replace("~0", "~") for p in patch["path"].split("/")[1:]]
    parent = doc["assets"][asset_id]
    for key in parts[:-1]:
        parent = parent[int(key)] if isinstance(parent, list) else parent[key]
    key = parts[-1]
    if isinstance(parent, list):
        if patch["op"] == "remove":
            parent.pop(int(key))
        elif patch["op"] == "add":
            parent.insert(len(parent) if key == "-" else int(key), copy.deepcopy(patch["value"]))
        else:
            parent[int(key)] = copy.deepcopy(patch["value"])
    elif patch["op"] == "remove":
        parent.pop(key, None)
    else:
        parent[key] = copy.deepcopy(patch["value"])


class BatchCLI(AssetJobCLI):
    def __init__(self, doc):
        super().__init__(doc)
        self.batch_writes = []
        self.batch_reads = 0
        self.reject_batch = False
        self.lose_ack = False
        self.damage_edge = False

    def compile_batch(self, doc, commands):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "input.json"
            file.write_text(json.dumps({"mode": "compile", "canvas_entry": str(ENTRY), "document": doc,
                                        "commands": commands}, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run([shutil.which("node"), str(Path(c.__file__).with_name("canvas_cli_batch.js")), str(file)],
                                    encoding="utf-8", capture_output=True, timeout=30,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        value = json.loads(result.stdout)
        if value.get("ok") is not True:
            c.fail("LOCAL_COMPILE_FAILED", value.get("message", result.stderr))
        return value["data"]

    def batch_snapshot(self, binding):
        self.control()
        self.batch_reads += 1
        doc = copy.deepcopy(self.doc)
        if self.damage_edge and self.batch_writes:
            root = doc["assets"][doc["rootCanvasId"]]["content"]
            root["edges"] = {}
        return {"document": doc, "asset_versions": {key: 1 for key in doc["assets"]}}

    def apply_batch(self, request):
        self.control()
        self.batch_writes.append(copy.deepcopy(request))
        if self.reject_batch:
            c.fail("COMMAND_REJECTED", "rejected before applying")
        for patch_value in request["transactions"][0]["patches"]:
            apply_patch(self.doc, patch_value)
        if self.lose_ack:
            c.fail("CLI_TIMEOUT", "server applied the batch but ACK was lost")
        return {"results": [{"status": "ack"}]}


@unittest.skipUnless(ENTRY and shutil.which("node"), "Official Canvas SDK not installed; set SEEDANCE_TEST_CANVAS_ENTRY")
class BatchBehavior(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.doc = {"rootCanvasId": "ROOT", "assets": {"ROOT": {"type": "canvas", "content": {
            "nodes": {}, "edges": {}, "settings": {"readOnly": False}}}}}
        self.cli = BatchCLI(copy.deepcopy(self.doc))
        self.state = c.blank_state()
        self.state["binding"] = {"canvas_id": "ROOT", "account_id": "USER", "team_id": "0"}
        c.save(self.project, self.state)
        self.session = {"ledger": {"schema_version": 3, "scenes": [], "characters": [
            {"id": "CHAR0001", "name": "Actor", "image_prompt": "complete actor board"}],
            "scene_assets": [{"id": "SET0001", "name": "Room", "image_prompt": "complete room board"}],
            "props": [{"id": "PROP0001", "name": "Mask", "image_prompt": "complete mask board"}]}, "units": [], "receipts": []}

    def assets(self, size=10):
        return backend.sync_assets(self.project, self.cli, self.session, lambda value: None, batch_size=size, batch_mode=True)

    def video_items(self, count=2):
        state = c.read_state(self.project)
        role = state["mappings"]["CHAR0001"]["node_id"]
        return [{"key": f"UNIT{i}", "name": f"A{i:02}", "kind": "video", "version": 1,
                 "prompt": f'Preserve <node-asset label="Actor">{role}</node-asset> once.',
                 "duration": 10, "settings": {"durationMs": 10000, "ratio": "16:9"},
                 "references_bound": True, "reference_keys": ["CHAR0001"], "black_reference_mode": "none-v1"}
                for i in range(1, count + 1)]

    def videos(self, items):
        state = c.read_state(self.project)
        context = {"failures": [], "doc": self.cli.doc, "progress": lambda value: None}
        result = batch.sync_videos(self.project, self.cli, state, copy.deepcopy(self.cli.doc), items, [], context)
        return result, context

    def test_three_asset_kinds_one_transaction_and_exact_fields(self):
        result = self.assets()
        self.assertEqual(result["status"], "synced")
        self.assertEqual(len(self.cli.batch_writes), 1)
        self.assertEqual(self.cli.batch_reads, 2)
        state = c.read_state(self.project)
        for item in c.desired_assets(self.session):
            node_id = state["mappings"][item["key"]]["node_id"]
            self.assertTrue(all(c.target_satisfied(self.cli.doc, t) for t in c.asset_targets(item, node_id)))
            self.assertTrue(c.target_satisfied(self.cli.doc, c.asset_settings_target(node_id)))
        self.assertEqual(self.cli.allocation_batches, [3])

    def test_asset_node_references_without_images_complete_and_record_missing(self):
        self.session["ledger"]["characters"].append({"id": "CHAR0002", "name": "Actor-Masked",
            "image_prompt": "Use @Actor with @Mask .",
            "image_format": {"reference_policy": "asset-image-reference-v1", "reference_asset_ids": ["CHAR0001", "PROP0001"]},
            "visual_design": {"image_reference": {"base_asset_id": "CHAR0001", "changes": ["mask"],
                "preserve": ["clothes"], "prop_asset_ids": ["PROP0001"]}}})
        result = self.assets()
        self.assertEqual(result["status"], "synced")
        self.assertEqual(len(self.cli.batch_writes), 1)
        state = c.read_state(self.project)
        target = state["mappings"]["CHAR0002"]
        self.assertEqual(len(batch.reference_edges(self.cli.doc, target["node_id"])), 2)
        self.assertEqual(target["stage"], "ready")
        self.assertEqual([p["reason"] for p in target["image_input"]["pending"]], ["missing_image", "missing_image"])
        self.assertEqual(c.content(self.cli.doc, target["node_id"])["generation"]["references"], [])

    def test_multiple_video_nodes_and_empty_role_references_share_one_transaction(self):
        self.assets()
        before = len(self.cli.batch_writes)
        (_, counts, verified), context = self.videos(self.video_items(3))
        self.assertEqual(len(self.cli.batch_writes) - before, 1)
        self.assertEqual(counts["created"], 3)
        self.assertEqual(len(verified), 3)
        for path in context["video_write_receipts"]:
            receipt = d.read_json(path)
            self.assertTrue(receipt["core_verified"] and receipt["references_verified"])
            self.assertEqual(len(batch.reference_edges(self.cli.doc, receipt["node_id"])), 1)
        self.assertFalse(any("generate" in cmd for cmd, _ in self.cli.writes))

    def test_lost_ack_does_not_reallocate_or_replay_and_recovers_same_ids(self):
        self.assets()
        self.cli.lose_ack = True
        with self.assertRaises(c.CanvasError) as failure:
            self.videos(self.video_items())
        self.assertEqual(failure.exception.code, "UNCERTAIN_WRITE")
        state = c.read_state(self.project)
        ids = {k: v["node_id"] for k, v in state["mappings"].items()}
        self.assertEqual(state["journal"][-1]["status"], "uncertain")
        writes = len(self.cli.batch_writes)
        c.reconcile(self.project, self.cli, state, self.cli.doc)
        self.assertEqual(state["journal"][-1]["status"], "verified")
        self.assertEqual(ids, {k: v["node_id"] for k, v in state["mappings"].items()})
        self.assertEqual(len(self.cli.batch_writes), writes)

    def test_ack_without_reference_graph_never_counts_complete(self):
        self.assets()
        self.cli.damage_edge = True
        with patch.object(c, "VIDEO_READBACK_DELAY", 0), self.assertRaises(c.CanvasError) as failure:
            self.videos(self.video_items())
        self.assertEqual(failure.exception.code, "READBACK_FAILED")
        state = c.read_state(self.project)
        self.assertTrue(all(v["stage"] != "ready" for k, v in state["mappings"].items() if k.startswith("UNIT")))

    def test_unknown_prompt_source_rejected_locally_before_write(self):
        self.assets()
        items = self.video_items()
        items[1]["prompt"] = "<node-asset>UNKNOWN</node-asset>"
        writes = len(self.cli.batch_writes)
        with self.assertRaises(c.CanvasError):
            self.videos(items)
        self.assertEqual(len(self.cli.batch_writes), writes)

    def test_official_role_media_rows_and_reference_edges_are_preserved(self):
        self.assets()
        state = c.read_state(self.project)
        role = state["mappings"]["CHAR0001"]["node_id"]
        c.content(self.cli.doc, role).setdefault("appearanceInfo", {}).update(
            pippitAssetId="IMAGE-MATERIAL", assetId="CLOUD-MEDIA")
        (_, _, verified), _ = self.videos(self.video_items())
        self.assertEqual(len(verified), 2)
        state = c.read_state(self.project)
        for key in verified:
            nid = state["mappings"][key]["node_id"]
            refs = c.content(self.cli.doc, nid)["generation"]["references"]
            self.assertEqual([(r["sourceNodeId"], r["pippitAssetId"], r["assetId"]) for r in refs],
                             [(role, "IMAGE-MATERIAL", "CLOUD-MEDIA")])
            self.assertEqual(refs[0]["edgeId"], batch.reference_edges(self.cli.doc, nid)[0]["id"])

    def test_group_size_bounds_writes_and_readbacks_for_twenty_one_videos(self):
        self.assets()
        writes, reads = len(self.cli.batch_writes), self.cli.batch_reads
        (_, _, verified), _ = self.videos(self.video_items(21))
        self.assertEqual(len(verified), 21)
        self.assertEqual(len(self.cli.batch_writes) - writes, 3)
        self.assertEqual(self.cli.batch_reads - reads, 6)

    def test_existing_video_preserves_model_resolution_ratio_and_extra_config(self):
        self.assets()
        self.videos(self.video_items(1))
        state = c.read_state(self.project)
        nid = state["mappings"]["UNIT1"]["node_id"]
        generation = c.content(self.cli.doc, nid)["generation"]
        generation.update(modelKey="user-model", resolution="4K", ratio="9:16", seed=42)
        item = self.video_items(1)[0]
        item.update(node_id=nid, version=2)
        item["settings"] = {"durationMs": 12000}
        self.videos([item])
        actual = c.content(self.cli.doc, nid)["generation"]
        self.assertEqual({k: actual[k] for k in ("modelKey", "resolution", "ratio", "seed")},
                         {"modelKey": "user-model", "resolution": "4K", "ratio": "9:16", "seed": 42})
        self.assertEqual(actual["durationMs"], 12000)

    def test_pause_before_send_is_known_cancelled_and_reuses_reserved_ids(self):
        from canvas_jobs import PauseRequested
        self.assets()
        writes = len(self.cli.batch_writes)
        with patch.object(self.cli, "apply_batch", side_effect=PauseRequested()):
            with self.assertRaises(PauseRequested):
                self.videos(self.video_items())
        state = c.read_state(self.project)
        self.assertEqual(state["journal"][-1]["status"], "cancelled")
        self.assertEqual(len(self.cli.batch_writes), writes)
        allocated = {k: v["node_id"] for k, v in state["mappings"].items() if k.startswith("UNIT")}
        self.assertTrue(all(state["mappings"][key]["stage"] == "allocated" for key in allocated))
        items = self.video_items()
        for item in items:
            item["allocated_id"] = allocated[item["key"]]
        self.videos(items)
        self.assertEqual(allocated, {k: v["node_id"] for k, v in c.read_state(self.project)["mappings"].items() if k.startswith("UNIT")})

    def test_direct_video_entry_uses_batch_plan_and_acceptance(self):
        self.assets()
        items = self.video_items()
        self.session["units"] = [{"id": item["key"], "version": 1} for item in items]
        writes = len(self.cli.batch_writes)
        context = {"failures": [], "progress": lambda value: None, "batch_mode": True}
        with patch.object(c, "desired_videos", return_value=(items, [])):
            result = c._sync(self.project, self.cli, "videos", ids=[i["key"] for i in items],
                             context=context, session_override=self.session)
        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["sync_protocol"], c.BATCH_PROTOCOL)
        self.assertEqual(len(self.cli.batch_writes) - writes, 1)

    def test_new_missing_asset_limit_is_ten_legacy_remains_three(self):
        self.session["ledger"]["scene_assets"] = []
        self.session["ledger"]["props"] = []
        self.session["ledger"]["characters"] = [{"id": f"CHAR{i:04}", "name": f"Person{i}", "image_prompt": f"board {i}"}
                                                for i in range(1, 12)]
        state, doc = c.connected(self.project, self.cli)
        _, report = c.reconcile_asset_nodes(self.project, self.cli, self.session, state, doc, batch_mode=True)
        self.assertTrue(report["blocked"])
        self.assertEqual(len(report["missing_assets"]), 11)
        self.assertEqual(self.cli.batch_writes, [])
        self.session["ledger"]["characters"].pop()
        state, doc = c.connected(self.project, self.cli)
        _, legacy = c.reconcile_asset_nodes(self.project, self.cli, self.session, state, doc)
        self.assertTrue(legacy["blocked"])
        _, report = c.reconcile_asset_nodes(self.project, self.cli, self.session, state, doc, batch_mode=True)
        self.assertEqual(len(report["created"]), 10)
        self.assertEqual(len(self.cli.batch_writes), 1)


if __name__ == "__main__":
    unittest.main()
