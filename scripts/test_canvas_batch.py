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
        self.guarded_operations = []

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

    def batch_snapshot(self, binding, node_ids=None):
        self.control()
        self.batch_reads += 1
        doc = copy.deepcopy(self.doc)
        if self.damage_edge and self.batch_writes:
            root = doc["assets"][doc["rootCanvasId"]]["content"]
            root["edges"] = {}
        return {"document": doc, "asset_versions": {key: 1 for key in doc["assets"]},
                'complete': True, 'queried_ids': list(doc['assets'])}

    def command_guarded(self, canvas_id, name, value, expected_nodes, expected_edges, expected_edge_targets=None, operation_id=None):
        self.guarded_operations.append(operation_id)
        request = {'name': name, 'input': value, 'expected_nodes': expected_nodes,
                   'expected_edges': expected_edges, 'expected_edge_targets': expected_edge_targets or []}
        state = batch.before_state({'document': self.doc, 'complete': True}, {'targets': []}, request)
        if state != 'matched':
            c.fail('READBACK_PENDING' if state == 'unknown' else 'CONCURRENT_EDIT', 'test guard did not admit the write', not_sent=True)
        return self.command(canvas_id, name, value)

    def command(self, canvas_id, name, value=None):
        if name in {'get_snapshot', 'get_permissions'}:
            return super().command(canvas_id, name, value)
        self.control()
        self.batch_writes.append({"name": name, "input": copy.deepcopy(value)})
        if self.reject_batch:
            c.fail("COMMAND_REJECTED", "rejected before applying")
        commands = batch.step_commands(batch.command(name, value))
        compiled = self.compile_batch(self.doc, commands)
        for patch_value in compiled['patches']:
            patch_value = {**patch_value, 'asset_id': patch_value['assetId']}
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
        self.videos(self.video_items())
        state = c.read_state(self.project)
        ids = {k: v["node_id"] for k, v in state["mappings"].items()}
        self.assertEqual(state["journal"][-1]["status"], "verified")
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
        self.assertEqual(failure.exception.code, "CONCURRENT_EDIT")
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
        self.assertEqual(len(self.cli.batch_writes) - writes, 2)
        self.assertEqual(self.cli.batch_reads - reads, 4)

    def test_live_reference_labels_do_not_fail_video_readback(self):
        self.assets()
        state = c.read_state(self.project)
        role = state['mappings']['CHAR0001']['node_id']
        c.content(self.cli.doc, role)['appearanceInfo'] = {'pippitAssetId': 'REAL_IMAGE', 'assetId': 'REAL_MEDIA'}
        original = self.cli.batch_snapshot

        def renamed_reference(binding, node_ids=None):
            value = original(binding, node_ids)
            for node_id in c.nodes(value['document']):
                if c.kind_of(c.nodes(value['document'])[node_id]) == 'video':
                    for reference in c.content(value['document'], node_id).get('generation', {}).get('references', []):
                        reference['label'] = 'user changed source title'
                        reference['metadata'] = {'name': 'user changed source title', 'subjectName': 'user changed source title'}
            return value

        with patch.object(self.cli, 'batch_snapshot', side_effect=renamed_reference):
            (_, _, verified), _ = self.videos(self.video_items(2))
        self.assertEqual(verified, {'UNIT1', 'UNIT2'})
        request = d.read_json(c.read_state(self.project)['journal'][-1]['input']['request_path'])['request']
        self.assertTrue(next(row for row in request['expected_nodes'] if row['node_id'] == role)['identity_only'])

    def test_video_preparation_preserves_existing_bound_asset_with_old_writing_stage(self):
        self.assets()
        state = c.read_state(self.project)
        node_id = state['mappings']['CHAR0001']['node_id']
        state['mappings']['CHAR0001']['stage'] = 'writing'
        c.nodes(self.cli.doc)[node_id]['data']['name'] = 'user title'
        c.content(self.cli.doc, node_id)['generation']['prompt'] = 'user prompt'
        before, writes = copy.deepcopy(self.cli.doc), len(self.cli.batch_writes)
        item = {**c.desired_assets(self.session)[0], 'node_id': node_id, 'resolution': 'mapped', 'preserve_bound_asset': True}
        summary = {'created': [], 'reused': [], 'adopted': []}
        batch.prepare_assets(self.project, self.cli, self.session, state, self.cli.doc, [item], summary)
        self.assertEqual(self.cli.doc, before)
        self.assertEqual(len(self.cli.batch_writes), writes)
        self.assertEqual(summary['reused'], ['CHAR0001'])

    def test_cached_generation_update_keeps_strict_reference_guard(self):
        self.assets()
        state = c.read_state(self.project)
        role = state['mappings']['CHAR0001']['node_id']
        c.content(self.cli.doc, role)['appearanceInfo'] = {'pippitAssetId': 'REAL_IMAGE', 'assetId': 'REAL_MEDIA'}
        self.videos(self.video_items(1))
        state = c.read_state(self.project)
        item = self.video_items(1)[0]
        item.update(node_id=state['mappings']['UNIT1']['node_id'], version=2)
        item['settings']['durationMs'] = 12000
        original = self.cli.command_guarded

        def alter_before_ordinary(canvas_id, name, value, expected_nodes, *rest):
            if name == 'apply_mutations':
                target = next(row for row in expected_nodes if row['node_id'] == item['node_id'])
                self.assertFalse(target['reference_identity_only'])
                c.content(self.cli.doc, item['node_id'])['generation']['references'][0]['label'] = 'concurrent target choice'
            return original(canvas_id, name, value, expected_nodes, *rest)

        with patch.object(self.cli, 'command_guarded', side_effect=alter_before_ordinary):
            with self.assertRaises(c.CanvasError) as failure:
                self.videos([item])
        self.assertEqual(failure.exception.code, 'CONCURRENT_EDIT')

    def test_source_media_change_during_create_refreshes_original_nodes_without_recreating(self):
        self.assets()
        state = c.read_state(self.project)
        source = state['mappings']['CHAR0001']['node_id']
        c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'OLD_IMAGE', 'assetId': 'OLD_MEDIA'}
        original = self.cli.command_guarded
        changed = []

        def replace_image_before_create(canvas_id, name, value, *args):
            if name == 'apply_mutations' and any(m['kind'] == 'create_node' for m in value['mutations']) and not changed:
                changed.append(True)
                c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'NEW_IMAGE', 'assetId': 'NEW_MEDIA'}
            return original(canvas_id, name, value, *args)

        with patch.object(self.cli, 'command_guarded', side_effect=replace_image_before_create), patch.object(c, 'VIDEO_READBACK_DELAY', 0):
            (_, counts, verified), _ = self.videos(self.video_items(2))
        self.assertEqual(verified, {'UNIT1', 'UNIT2'})
        self.assertEqual(counts['created'], 2)
        self.assertEqual(self.cli.allocation_batches, [3, 2])
        self.assertEqual(sum(m['kind'] == 'create_node' for write in self.cli.batch_writes if write['name'] == 'apply_mutations'
                             for m in write['input']['mutations']), 5)
        state = c.read_state(self.project)
        parent = next(row for row in state['journal'] if row.get('reference_refresh'))
        self.assertEqual(parent['status'], 'verified')
        self.assertEqual(parent['reference_refresh']['status'], 'verified')
        self.assertEqual(len(parent['reference_refresh']['plans'][0]['operations']), 3)
        for key in verified:
            references = c.content(self.cli.doc, state['mappings'][key]['node_id'])['generation']['references']
            self.assertEqual([(row['pippitAssetId'], row['assetId']) for row in references], [('NEW_IMAGE', 'NEW_MEDIA')])

    def test_reference_refresh_resume_continues_after_verified_delete_without_recreating(self):
        from canvas_jobs import PauseRequested
        self.assets()
        state = c.read_state(self.project)
        source = state['mappings']['CHAR0001']['node_id']
        c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'OLD_IMAGE', 'assetId': 'OLD_MEDIA'}
        original = self.cli.command_guarded
        changed = []

        def pause_refresh(canvas_id, name, value, *args):
            if name == 'apply_mutations' and any(m['kind'] == 'create_node' for m in value['mutations']) and not changed:
                changed.append(True)
                c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'NEW_IMAGE', 'assetId': 'NEW_MEDIA'}
            if name == 'xyq.generation.update_prompt':
                raise PauseRequested()
            return original(canvas_id, name, value, *args)

        with patch.object(self.cli, 'command_guarded', side_effect=pause_refresh), patch.object(c, 'VIDEO_READBACK_DELAY', 0):
            with self.assertRaises(PauseRequested):
                self.videos(self.video_items(1))
        state = c.read_state(self.project)
        node_id = state['mappings']['UNIT1']['node_id']
        parent = next(row for row in state['journal'] if row.get('reference_refresh'))
        operations = list(parent['reference_refresh']['plans'][0]['operations'])
        writes = len(self.cli.batch_writes)
        parent['reference_refresh']['plans'][0]['operations'][0] = state['journal'][0]['id']
        with self.assertRaises(c.CanvasError) as invalid:
            batch.recover_entry(self.project, self.cli, state, parent)
        self.assertEqual(invalid.exception.code, 'RECOVERY_RECORD_INVALID')
        parent['reference_refresh']['plans'][0]['operations'] = list(operations)
        batch.recover_entry(self.project, self.cli, state, parent)
        self.assertEqual(parent['status'], 'verified')
        self.assertEqual(parent['reference_refresh']['plans'][0]['operations'], operations)
        self.assertEqual(len(self.cli.batch_writes), writes + 1)
        self.assertEqual(self.cli.batch_writes[-1]['name'], 'xyq.generation.update_prompt')
        self.assertEqual(state['mappings']['UNIT1']['node_id'], node_id)

    def test_source_media_refresh_never_overwrites_concurrent_video_prompt(self):
        self.assets()
        state = c.read_state(self.project)
        source = state['mappings']['CHAR0001']['node_id']
        c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'OLD_IMAGE', 'assetId': 'OLD_MEDIA'}
        original = self.cli.command_guarded

        def edit_after_create(canvas_id, name, value, *args):
            result = original(canvas_id, name, value, *args)
            if name == 'apply_mutations':
                videos = [m['node']['id'] for m in value['mutations'] if m['kind'] == 'create_node' and m['node']['type'] == 'biz/video']
                for node_id in videos:
                    c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'NEW_IMAGE', 'assetId': 'NEW_MEDIA'}
                    c.content(self.cli.doc, node_id)['generation']['prompt'] = 'a human video edit'
            return result

        with patch.object(self.cli, 'command_guarded', side_effect=edit_after_create), patch.object(c, 'VIDEO_READBACK_DELAY', 0):
            with self.assertRaises(c.CanvasError) as failure:
                self.videos(self.video_items(1))
        self.assertEqual(failure.exception.code, 'CONCURRENT_EDIT')
        state = c.read_state(self.project)
        self.assertEqual(c.content(self.cli.doc, state['mappings']['UNIT1']['node_id'])['generation']['prompt'], 'a human video edit')
        self.assertFalse(any(row.get('reference_refresh') for row in state['journal']))

    def test_parent_does_not_complete_while_unknown_reference_child_can_still_arrive(self):
        self.assets()
        state = c.read_state(self.project)
        source = state['mappings']['CHAR0001']['node_id']
        c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'OLD_IMAGE', 'assetId': 'OLD_MEDIA'}
        original = self.cli.command_guarded
        changed = []

        def unknown_refresh_delete(canvas_id, name, value, *args):
            if name == 'apply_mutations':
                if any(m['kind'] == 'create_node' for m in value['mutations']) and not changed:
                    changed.append(True)
                    c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'NEW_IMAGE', 'assetId': 'NEW_MEDIA'}
                elif any(m['kind'] == 'delete_edges' for m in value['mutations']):
                    c.fail('CLI_TIMEOUT', 'delete response lost; server outcome unknown')
            return original(canvas_id, name, value, *args)

        with patch.object(self.cli, 'command_guarded', side_effect=unknown_refresh_delete), patch.object(c, 'VIDEO_READBACK_DELAY', 0):
            with self.assertRaises(c.CanvasError) as failure:
                self.videos(self.video_items(1))
        self.assertEqual(failure.exception.code, 'READBACK_PENDING')
        state = c.read_state(self.project)
        parent = next(row for row in state['journal'] if row.get('reference_refresh'))
        child_id = parent['reference_refresh']['plans'][0]['operations'][0]
        c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'OLD_IMAGE', 'assetId': 'OLD_MEDIA'}
        self.assertTrue(all(c.target_satisfied(self.cli.doc, target) for target in parent['targets']))

        def assert_parent_pending(*args):
            self.assertNotEqual(parent['status'], 'verified')
            return original(*args)

        with patch.object(self.cli, 'command_guarded', side_effect=assert_parent_pending):
            batch.recover_entry(self.project, self.cli, state, parent)
        self.assertEqual(parent['status'], 'verified')
        self.assertEqual(next(row for row in state['journal'] if row['id'] == child_id)['status'], 'verified')
        self.assertEqual(len(parent['reference_refresh']['plans'][0]['operations']), 2)
        self.assertTrue(all(c.target_satisfied(self.cli.doc, target) for target in parent['targets']))

    def test_continually_changing_source_is_bounded_and_resumes_with_same_nodes(self):
        self.assets()
        state = c.read_state(self.project)
        source = state['mappings']['CHAR0001']['node_id']
        c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'IMAGE0', 'assetId': 'MEDIA0'}
        original = self.cli.command_guarded
        changes = []

        def changing_source(canvas_id, name, value, *args):
            creating = name == 'apply_mutations' and any(m['kind'] == 'create_node' for m in value['mutations'])
            if creating:
                changes.append('create')
                c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'IMAGE1', 'assetId': 'MEDIA1'}
            result = original(canvas_id, name, value, *args)
            if name == 'xyq.generation.update_prompt':
                changes.append('refresh')
                index = len(changes)
                c.content(self.cli.doc, source)['appearanceInfo'] = {'pippitAssetId': 'IMAGE' + str(index), 'assetId': 'MEDIA' + str(index)}
            return result

        with patch.object(self.cli, 'command_guarded', side_effect=changing_source), patch.object(c, 'VIDEO_READBACK_DELAY', 0):
            with self.assertRaises(c.CanvasError) as failure:
                self.videos(self.video_items(1))
        self.assertEqual(failure.exception.code, 'READBACK_PENDING')
        self.assertEqual(changes, ['create', 'refresh', 'refresh'])
        state = c.read_state(self.project)
        parent = next(row for row in state['journal'] if row.get('reference_refresh'))
        self.assertEqual(len(parent['reference_refresh']['plans']), 2)
        node_id = state['mappings']['UNIT1']['node_id']
        batch.recover_entry(self.project, self.cli, state, parent)
        self.assertEqual(parent['status'], 'verified')
        self.assertEqual(state['mappings']['UNIT1']['node_id'], node_id)
        self.assertEqual(self.cli.allocation_batches, [3, 1])

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

    def test_ack_read_eof_retries_only_reads(self):
        self.assets()
        original = self.cli.batch_snapshot
        writes = len(self.cli.batch_writes)
        failures = []

        def snapshot(binding, node_ids=None):
            if node_ids and len(self.cli.batch_writes) > writes and not failures:
                failures.append(True)
                c.fail('CLI_TIMEOUT', 'query transport timed out')
            return original(binding, node_ids)

        with patch.object(self.cli, 'batch_snapshot', side_effect=snapshot), patch.object(c, 'VIDEO_READBACK_DELAY', 0):
            (_, _, verified), _ = self.videos(self.video_items(2))
        self.assertEqual(len(verified), 2)
        self.assertEqual(len(self.cli.batch_writes) - writes, 1)
        self.assertEqual(len(failures), 1)

    def test_omitted_empty_references_pass_exact_batch_acceptance(self):
        self.assets()
        original = self.cli.batch_snapshot

        def snapshot(binding, node_ids=None):
            value = original(binding, node_ids)
            for node_id in c.nodes(value['document']):
                generation = c.content(value['document'], node_id).get('generation', {})
                if generation.get('references') == []:
                    del generation['references']
            return value

        with patch.object(self.cli, 'batch_snapshot', side_effect=snapshot):
            (_, _, verified), _ = self.videos(self.video_items(2))
        self.assertEqual(len(verified), 2)

    def test_verified_current_snapshot_skips_new_writes(self):
        self.assets()
        self.videos(self.video_items(2))
        state = c.read_state(self.project)
        items = self.video_items(2)
        for item in items:
            item['node_id'] = state['mappings'][item['key']]['node_id']
        writes, reads = len(self.cli.batch_writes), self.cli.batch_reads
        (_, _, verified), _ = self.videos(items)
        self.assertEqual(len(verified), 2)
        self.assertEqual(len(self.cli.batch_writes), writes)
        self.assertEqual(self.cli.batch_reads, reads)

    def test_matched_node_with_pending_asset_conflict_is_accepted_once_without_empty_receipt(self):
        self.assets()
        self.videos(self.video_items(1))
        state = c.read_state(self.project)
        item = self.video_items(1)[0]
        item['node_id'] = state['mappings'][item['key']]['node_id']
        pending = [{'key': 'CHAR0001', 'reason': 'node_name_changed'}]
        context = {'failures': [], 'doc': self.cli.doc, 'progress': lambda value: None}
        writes, reads = len(self.cli.batch_writes), self.cli.batch_reads
        receipts = set((self.project / 'canvas' / 'video-write-receipts').glob('*.json'))
        _, counts, verified = batch.sync_videos(self.project, self.cli, state, copy.deepcopy(self.cli.doc), [item], pending, context, references_only=True)
        self.assertEqual(len(context['failures']), 1)
        self.assertEqual(context['failures'][0]['reason'], 'reference_failed')
        self.assertEqual(counts, {'created': 0, 'updated': 0, 'reused': 1})
        self.assertEqual(verified, set())
        self.assertEqual((len(self.cli.batch_writes), self.cli.batch_reads), (writes, reads))
        self.assertEqual(set((self.project / 'canvas' / 'video-write-receipts').glob('*.json')), receipts)

    def test_existing_video_new_reference_accepts_sdk_edge_identity(self):
        self.assets()
        self.videos(self.video_items(1))
        state = c.read_state(self.project)
        item = self.video_items(1)[0]
        item['node_id'] = state['mappings'][item['key']]['node_id']
        scene = state['mappings']['SET0001']['node_id']
        item['prompt'] += f' <node-asset>{scene}</node-asset>'
        item['reference_keys'].append('SET0001')
        (_, _, verified), _ = self.videos([item])
        self.assertEqual(verified, {'UNIT1'})
        self.assertEqual({edge['source'] for edge in batch.reference_edges(self.cli.doc, item['node_id'])},
                         {state['mappings']['CHAR0001']['node_id'], scene})

    def test_same_session_guard_rejection_preserves_allocated_ids(self):
        self.assets()
        writes = len(self.cli.batch_writes)

        def rejected(canvas_id, name, value, expected_nodes, expected_edges, expected_edge_targets=None, operation_id=None):
            self.assertEqual(name, 'apply_mutations')
            self.assertEqual(sum(row['node'] is None for row in expected_nodes), 2)
            self.assertTrue(expected_edges)
            c.fail('CONCURRENT_EDIT', 'guard rejected before execution', not_sent=True)

        with patch.object(self.cli, 'command_guarded', side_effect=rejected, create=True):
            with self.assertRaises(c.CanvasError) as failure:
                self.videos(self.video_items(2))
        self.assertEqual(failure.exception.code, 'CONCURRENT_EDIT')
        state = c.read_state(self.project)
        self.assertEqual(state['journal'][-1]['status'], 'rejected')
        self.assertTrue(state['journal'][-1]['not_sent'])
        self.assertEqual(len(self.cli.batch_writes), writes)
        self.assertEqual([value['stage'] for key, value in state['mappings'].items() if key.startswith('UNIT')],
                         ['allocated', 'allocated'])

    def test_pause_before_send_is_known_cancelled_and_reuses_reserved_ids(self):
        from canvas_jobs import PauseRequested
        self.assets()
        writes = len(self.cli.batch_writes)
        with patch.object(self.cli, "command", side_effect=PauseRequested()):
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

    def pending_video_request(self):
        self.assets()
        before = copy.deepcopy(self.cli.doc)
        self.videos(self.video_items(2))
        state = c.read_state(self.project)
        entry = state['journal'][-1]
        entry['status'] = 'uncertain'
        entry.pop('ack', None)
        c.save(self.project, state)
        self.cli.doc = before
        return state, entry

    def test_unchanged_before_recovers_original_request_and_node_ids(self):
        state, entry = self.pending_video_request()
        ids = copy.deepcopy(state['mappings'])
        request = d.read_json(entry['input']['request_path'])
        count, allocations = len(self.cli.batch_writes), copy.deepcopy(self.cli.allocation_batches)
        c.reconcile(self.project, self.cli, state, copy.deepcopy(self.cli.doc))
        self.assertEqual(entry['status'], 'verified')
        self.assertEqual(entry['recovery_attempts'], 1)
        self.assertEqual(state['mappings'], ids)
        self.assertEqual(self.cli.allocation_batches, allocations)
        self.assertEqual(len(self.cli.batch_writes), count + 1)
        self.assertEqual(d.read_json(entry['input']['request_path']), request)
        self.assertEqual(self.cli.guarded_operations[-1], entry['id'])
        for mapping in state['mappings'].values():
            if mapping.get('video_write_receipt'):
                self.assertEqual(d.read_json(mapping['video_write_receipt'])['status'], 'verified')

    def test_first_transport_failure_before_commit_recovers_inside_submit(self):
        self.assets()
        original = self.cli.command_guarded
        attempts = []

        def lost_before_commit(*args):
            attempts.append(copy.deepcopy(args[2]))
            if len(attempts) == 1:
                c.fail('CLI_TIMEOUT', 'connection lost before server commit')
            return original(*args)

        with patch.object(self.cli, 'command_guarded', side_effect=lost_before_commit), patch.object(c, 'VIDEO_READBACK_DELAY', 0):
            (_, _, verified), _ = self.videos(self.video_items(2))
        state = c.read_state(self.project)
        self.assertEqual(verified, {'UNIT1', 'UNIT2'})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(state['journal'][-1]['status'], 'verified')
        self.assertEqual(state['journal'][-1]['recovery_attempts'], 1)

    def test_multi_step_recovery_retries_only_uncommitted_original_step(self):
        self.assets()
        self.videos(self.video_items(3))
        state = c.read_state(self.project)
        items = self.video_items(3)
        for item in items:
            item.update(node_id=state['mappings'][item['key']]['node_id'], version=2)
            item['prompt'] += ' revised'
        final_id = items[-1]['node_id']
        original = self.cli.command_guarded
        attempted = []
        failed = []

        def fail_last_step(canvas_id, name, value, *rest):
            attempted.append((name, copy.deepcopy(value)))
            if name == 'xyq.generation.update_prompt' and value['nodeId'] == final_id and not failed:
                failed.append(True)
                c.fail('CLI_TIMEOUT', 'last step not committed')
            return original(canvas_id, name, value, *rest)

        with patch.object(self.cli, 'command_guarded', side_effect=fail_last_step), patch.object(c, 'VIDEO_READBACK_DELAY', 0):
            (_, _, verified), _ = self.videos(items)
        prompts = [value['nodeId'] for name, value in attempted if name == 'xyq.generation.update_prompt']
        self.assertEqual(prompts, [item['node_id'] for item in items] + [final_id])
        self.assertEqual(verified, {'UNIT1', 'UNIT2', 'UNIT3'})
        state = c.read_state(self.project)
        self.assertEqual(state['journal'][-1]['recovery_attempts'], 1)
        self.assertTrue(all(entry['status'] == 'verified' for entry in state['journal']))

    def test_recovery_not_sent_read_failure_can_retry_same_operation(self):
        state, entry = self.pending_video_request()
        writes = len(self.cli.batch_writes)
        with patch.object(self.cli, 'command_guarded', side_effect=c.CanvasError('READBACK_PENDING', 'missing guard read', not_sent=True)):
            with self.assertRaises(c.CanvasError) as failure:
                batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(failure.exception.code, 'READBACK_PENDING')
        self.assertEqual(entry['status'], 'uncertain')
        self.assertTrue(entry['recovery_not_sent'])
        self.assertEqual(len(self.cli.batch_writes), writes)
        batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(entry['status'], 'verified')
        self.assertEqual(entry['recovery_attempts'], 2)

    def test_late_original_commit_rejected_by_retry_guard_is_verified(self):
        state, entry = self.pending_video_request()
        original = self.cli.command_guarded
        writes = len(self.cli.batch_writes)

        def late_commit(*args):
            original(*args)
            c.fail('CONCURRENT_EDIT', 'original write arrived before retry guard', not_sent=True)

        with patch.object(self.cli, 'command_guarded', side_effect=late_commit):
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(entry['status'], 'verified')
        self.assertEqual(len(self.cli.batch_writes), writes + 1)

    def test_recovery_preserves_unrelated_node_added_since_before(self):
        state, entry = self.pending_video_request()
        self.cli.doc = self.cli.compile_batch(self.cli.doc, [batch.command('create_biz_node', {
            'nodeKind': 'image', 'id': 'UNRELATED', 'initialData': {'name': 'human note'}, 'topLeft': {'x': 9000, 'y': 9000}})])['document']
        batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(c.content(self.cli.doc, 'UNRELATED')['name'], 'human note')
        self.assertEqual(entry['status'], 'verified')

    def test_recovery_missing_source_asset_remains_unknown_without_write(self):
        state, entry = self.pending_video_request()
        role = state['mappings']['CHAR0001']['node_id']
        del self.cli.doc['assets'][role]
        writes = len(self.cli.batch_writes)
        with self.assertRaises(c.CanvasError) as failure:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(failure.exception.code, 'READBACK_PENDING')
        self.assertEqual(len(self.cli.batch_writes), writes)

    def test_recovery_changed_source_type_is_real_conflict_without_write(self):
        state, entry = self.pending_video_request()
        role = state['mappings']['CHAR0001']['node_id']
        c.nodes(self.cli.doc)[role]['type'] = 'biz/scene'
        writes = len(self.cli.batch_writes)
        with self.assertRaises(c.CanvasError) as failure:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(failure.exception.code, 'CONCURRENT_EDIT')
        self.assertEqual(len(self.cli.batch_writes), writes)

    def test_recovery_rejects_unexpected_incoming_reference(self):
        state, entry = self.pending_video_request()
        node_id = state['mappings']['UNIT1']['node_id']
        self.cli.doc['assets']['ROOT']['content']['edges']['human-edge'] = {
            'id': 'human-edge', 'source': state['mappings']['SET0001']['node_id'], 'target': node_id, 'type': 'reference'}
        with self.assertRaises(c.CanvasError) as failure:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(failure.exception.code, 'CONCURRENT_EDIT')

    def test_recovery_does_not_allow_unreviewed_commands_or_modified_receipt(self):
        self.assertFalse(batch.recoverable_command({'name': 'upload', 'input': {'file': 'a.png'}}))
        self.assertFalse(batch.recoverable_command({'name': 'apply_mutations', 'input': {'atomic': True,
            'mutations': [{'kind': 'invoke_command', 'name': 'generate', 'args': []}]}}))
        state, entry = self.pending_video_request()
        record = d.read_json(entry['input']['request_path'])
        record['request']['input']['intent'] = 'changed after durable request'
        d.write_json(entry['input']['request_path'], record)
        with self.assertRaises(c.CanvasError) as failure:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(failure.exception.code, 'RECOVERY_RECORD_INVALID')

    def test_partial_batch_recovery_skips_committed_nodes_and_edges(self):
        state, entry = self.pending_video_request()
        request = d.read_json(entry['input']['request_path'])['request']
        first_id = state['mappings']['UNIT1']['node_id']
        second_id = state['mappings']['UNIT2']['node_id']
        committed = [mutation for mutation in request['input']['mutations']
                     if mutation.get('node', {}).get('id') == first_id or mutation.get('edge', {}).get('target') == first_id]
        self.cli.doc = self.cli.compile_batch(self.cli.doc, batch.step_commands(batch.command('apply_mutations', {
            'atomic': True, 'mutations': committed})))['document']
        first = copy.deepcopy(batch.recovery_projection(self.cli.doc, first_id))
        count = len(self.cli.batch_writes)
        batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(entry['status'], 'verified')
        self.assertEqual(entry['resolution'], 'continue_partial_commit')
        self.assertEqual(len(self.cli.batch_writes), count + 1)
        remaining = self.cli.batch_writes[-1]['input']['mutations']
        self.assertEqual([mutation['node']['id'] for mutation in remaining if mutation['kind'] == 'create_node'], [second_id])
        self.assertFalse(any(mutation.get('edge', {}).get('target') == first_id for mutation in remaining))
        self.assertEqual(batch.recovery_projection(self.cli.doc, first_id), first)
        self.assertEqual(d.read_json(entry['input']['request_path'])['request'], request)
        self.assertEqual(d.read_json(entry['recovery_request_path'])['operation_id'], entry['id'])

    def test_partial_commit_with_user_edit_is_not_overwritten(self):
        state, entry = self.pending_video_request()
        request = d.read_json(entry['input']['request_path'])['request']
        first_id = state['mappings']['UNIT1']['node_id']
        committed = [mutation for mutation in request['input']['mutations']
                     if mutation.get('node', {}).get('id') == first_id or mutation.get('edge', {}).get('target') == first_id]
        self.cli.doc = self.cli.compile_batch(self.cli.doc, batch.step_commands(batch.command('apply_mutations', {
            'atomic': True, 'mutations': committed})))['document']
        c.content(self.cli.doc, first_id)['generation']['prompt'] = 'a real human edit after partial commit'
        writes = len(self.cli.batch_writes)
        with self.assertRaises(c.CanvasError) as failure:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(failure.exception.code, 'CONCURRENT_EDIT')
        self.assertEqual(len(self.cli.batch_writes), writes)

    def test_permanent_recovery_errors_are_not_changed_to_pending(self):
        state, entry = self.pending_video_request()
        writes = len(self.cli.batch_writes)
        for code in ('CLI_AUTH_ERROR', 'COMMAND_REJECTED', 'CLI_PROTOCOL_UNSUPPORTED', 'CONCURRENT_EDIT', 'CLI_ERROR'):
            with self.subTest(code=code), patch.object(self.cli, 'command_guarded', side_effect=c.CanvasError(code, 'definite rejection')):
                with self.assertRaises(c.CanvasError) as failure:
                    batch.recover_entry(self.project, self.cli, state, entry)
                self.assertEqual(failure.exception.code, code)
        self.assertEqual(len(self.cli.batch_writes), writes)

    def test_missing_or_unsupported_recovery_request_is_not_pending_forever(self):
        state, entry = self.pending_video_request()
        request_path = Path(entry['input']['request_path'])
        request = d.read_json(request_path)['request']
        request_path.unlink()
        with self.assertRaises(c.CanvasError) as failure:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(failure.exception.code, 'RECOVERY_RECORD_INVALID')
        request.update(name='upload', input={'file': 'not-replayable.png'})
        d.write_json(request_path, {'protocol': c.BATCH_PROTOCOL, 'request': request})
        entry['input']['request_sha256'] = c.digest(request)
        with self.assertRaises(c.CanvasError) as failure:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(failure.exception.code, 'RECOVERY_UNSUPPORTED')
        request['expected_nodes'] = []
        d.write_json(request_path, {'protocol': c.BATCH_PROTOCOL, 'request': request})
        entry['input']['request_sha256'] = c.digest(request)
        with self.assertRaises(c.CanvasError) as failure:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(failure.exception.code, 'RECOVERY_RECORD_INVALID')

    def test_permanent_submit_error_does_not_enter_automatic_recovery(self):
        self.assets()
        with patch.object(self.cli, 'command_guarded', side_effect=c.CanvasError('CLI_AUTH_ERROR', 'authentication expired')) as mocked:
            with self.assertRaises(c.CanvasError) as failure:
                self.videos(self.video_items(2))
        self.assertEqual(failure.exception.code, 'CLI_AUTH_ERROR')
        self.assertEqual(mocked.call_count, 1)
        self.assertNotIn('recovery_attempts', c.read_state(self.project)['journal'][-1])

    def test_source_name_prompt_and_optional_field_edits_do_not_block_bound_id_recovery(self):
        state, entry = self.pending_video_request()
        role = state['mappings']['CHAR0001']['node_id']
        generation = c.content(self.cli.doc, role)['generation']
        generation['prompt'] = 'known human change'
        del generation['ratio']
        c.nodes(self.cli.doc)[role].setdefault('data', {})['name'] = 'human selected asset title'
        batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(entry['status'], 'verified')
        self.assertEqual(c.content(self.cli.doc, role)['generation']['prompt'], 'known human change')
        self.assertEqual(c.nodes(self.cli.doc)[role]['data']['name'], 'human selected asset title')

    def test_repeated_complete_root_confirms_deleted_original_node(self):
        state, entry = self.pending_video_request()
        role = state['mappings']['CHAR0001']['node_id']
        del c.nodes(self.cli.doc)[role]
        del self.cli.doc['assets'][role]
        writes = len(self.cli.batch_writes)
        with self.assertRaises(c.CanvasError) as first:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(first.exception.code, 'READBACK_PENDING')
        with self.assertRaises(c.CanvasError) as second:
            batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(second.exception.code, 'CONCURRENT_EDIT')
        self.assertEqual(entry['missing_nodes_observation']['count'], 2)
        self.assertEqual(len(self.cli.batch_writes), writes)

    def test_partial_prompt_reference_keeps_committed_edge_identity(self):
        self.assets()
        self.videos(self.video_items(1))
        state = c.read_state(self.project)
        before = copy.deepcopy(self.cli.doc)
        item = self.video_items(1)[0]
        item['node_id'] = state['mappings']['UNIT1']['node_id']
        scene = state['mappings']['SET0001']['node_id']
        item['prompt'] += f' Also show <node-asset>{scene}</node-asset>.'
        item['reference_keys'].append('SET0001')
        self.videos([item])
        state = c.read_state(self.project)
        entry = state['journal'][-1]
        self.assertEqual(d.read_json(entry['input']['request_path'])['request']['name'], 'xyq.generation.update_prompt')
        completed = copy.deepcopy(self.cli.doc)
        old_edges = before['assets']['ROOT']['content']['edges']
        added = [edge for key, edge in completed['assets']['ROOT']['content']['edges'].items() if key not in old_edges]
        self.assertEqual(len(added), 1)
        edge = copy.deepcopy(added[0])
        edge['id'] = 'server-committed-reference-uuid'
        self.cli.doc = before
        self.cli.doc['assets']['ROOT']['content']['edges'][edge['id']] = edge
        entry['status'] = 'uncertain'
        c.save(self.project, state)
        batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(entry['status'], 'verified')
        edges = batch.reference_edges(self.cli.doc, item['node_id'])
        self.assertEqual([row['id'] for row in edges if row['source'] == scene], [edge['id']])

    def test_partial_role_generation_refreshes_business_expected_from_current(self):
        self.assets()
        state = c.read_state(self.project)
        node_id = state['mappings']['CHAR0001']['node_id']
        baseline = self.cli.batch_snapshot(state['binding'])
        before = copy.deepcopy(self.cli.doc)
        generation = copy.deepcopy(c.content(before, node_id)['generation'])
        desired = {**generation, 'prompt': 'new role board', 'resolution': '4K'}
        cmd = batch.command('xyq.role.updateAppearance', {'nodeId': node_id, 'assetId': node_id,
            'expected': {'generation': generation}, 'patch': {'generation': desired}})
        compiled = self.cli.compile_batch(before, [cmd])
        targets = [{'action': 'semantic_generation_settings', 'node_id': node_id, 'value': desired}]
        entry = batch.submit(self.project, self.cli, state, baseline, compiled, targets)
        self.cli.doc = before
        c.content(self.cli.doc, node_id)['generation']['prompt'] = desired['prompt']
        entry['status'] = 'uncertain'
        c.save(self.project, state)
        batch.recover_entry(self.project, self.cli, state, entry)
        self.assertEqual(entry['status'], 'verified')
        self.assertEqual(self.cli.batch_writes[-1]['input']['expected']['generation']['prompt'], desired['prompt'])
        self.assertEqual(c.content(self.cli.doc, node_id)['generation'], desired)

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
        self.assertEqual(sum(row['name'] == 'apply_mutations' for row in self.cli.batch_writes[writes:]), 1)

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
