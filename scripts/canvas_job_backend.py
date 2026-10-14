#!/usr/bin/env python3
"""CLI-only queue backend. Serialized writes, bounded grouped asset verification."""
import copy
from pathlib import Path
import uuid

import canvas as c
import director as d


class ControlledCLI(c.CLI):
    def __init__(self, project, check_control):
        super().__init__(project)
        self.check_control = check_control

    def native(self, *args, **kwargs):
        self.check_control()
        return super().native(*args, **kwargs)


def asset_items(session):
    source = {row["id"]: row for category in ("characters", "scene_assets", "props")
              for row in session["ledger"][category]}
    result = c.desired_assets(session)
    for item in result:
        prompt = source[item["key"]].get("image_prompt")
        d.require(isinstance(prompt, str) and prompt.strip(), "资产缺少已定稿的image_prompt：" + item["key"])
        item["description"] = prompt
    return result


def asset_targets(item, node_id):
    targets = [{"action": "node_kind", "node_id": node_id, "kind": item["kind"]}]
    if item["kind"] in ("role", "scene"):
        targets += [{"action": "rename", "node_id": node_id, "value": item["name"]},
                    {"action": "field", "node_id": node_id, "value": ""},
                    {"action": "description", "node_id": node_id, "value": item["description"]}]
    else:
        targets += [{"action": "patch", "node_id": node_id, "patch": {"name": item["name"], "caption": item["name"]}},
                    {"action": "prompt", "node_id": node_id, "value": item["description"]}]
    return targets


def acknowledge(project, cli, state, command, value, targets):
    # Pause is checked BEFORE journaling, so an unsent request is never ambiguous.
    c.check_control(cli)
    entry = {"id": uuid.uuid4().hex, "command": command, "input": copy.deepcopy(value),
             "targets": copy.deepcopy(targets), "status": "pending", "source": "asset_job"}
    state["journal"].append(entry)
    c.save(project, state)
    try:
        cli.command(state["binding"]["canvas_id"], command, value)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        entry.update(status="uncertain", error=c.safe_message(exc))
        c.save(project, state)
        c.fail("UNCERTAIN_WRITE", "资产命令未获得确认，保留原ID，停止后续写入。", operation=entry["id"])
    entry["status"] = "acknowledged"
    c.save(project, state)


def sync_assets(project, cli, session, progress, decisions=None, batch_size=10):
    d.require(1 <= batch_size <= 50, "分组核对数量须为1至50；这不是服务端并发限制")
    decisions = decisions or {}
    items = asset_items(session)
    state, doc = c.connected(project, cli)
    c.reconcile(project, cli, state, doc, decisions=decisions)
    plan, conflicts = c.asset_reconciliation_plan(session, state, doc, decisions)
    for candidate in plan:
        if candidate["resolution"] == "adopted" and decisions.get(candidate["key"], {}).get("reuse") != candidate["node_id"]:
            conflicts.append({"key": candidate["key"], "reason": "candidate_requires_choice", "node_id": candidate["node_id"],
                              "fingerprint": c.fingerprint(doc, candidate["node_id"])})
    # A created node may still display its factory name when interrupted before rename.
    for issue in list(conflicts):
        mapping = state["mappings"].get(issue["key"], {})
        node_id = mapping.get("node_id")
        latest = next((e for e in reversed(state["journal"]) if e.get("status") == "verified"
                       and node_id in e.get("after", {})), None)
        if (issue["reason"] == "node_name_changed" and mapping.get("stage") in ("allocated", "writing")
                and node_id in c.nodes(doc) and latest and latest["after"][node_id] == c.fingerprint(doc, node_id)
                and c.name_of(doc, node_id) in (mapping["name"], "未命名角色", "未命名场景")
                and c.content(doc, node_id).get("basicInfo", {}).get("name") == mapping["name"]):
            plan.append(dict(next(i for i in items if i["key"] == issue["key"]), node_id=node_id, resolution="mapped"))
            conflicts.remove(issue)
    if conflicts:
        return {"status": "needs_decision", "conflicts": conflicts}
    by_key = {p["key"]: p for p in plan}
    completed, pending, node_ids = [], [], {}
    for item in items:
        item.update({k: v for k, v in by_key[item["key"]].items() if k not in item})
        node_id = item.get("node_id")
        if node_id and all(c.target_satisfied(doc, t) for t in asset_targets(item, node_id)):
            completed.append(item["key"])
            node_ids[item["key"]] = node_id
            state["mappings"][item["key"]] = {**state["mappings"].get(item["key"], {}), "node_id": node_id,
                "kind": item["kind"], "name": item["name"], "stage": "ready", "appearance_name": "",
                "description_hash": c.digest(item["description"])}
        else:
            pending.append(item)
    c.save(project, state)
    progress({"completed": len(completed), "total": len(items), "node_ids": node_ids})
    if not pending:
        return {"status": "synced", "count": len(items), "created": 0, "reused": len(completed)}
    c.checkpoint(project, cli, state, "seedance asset job")
    positions = c.layout_positions(doc, pending)
    created = 0
    for offset in range(0, len(pending), batch_size):
        c.check_control(cli)
        group = pending[offset:offset + batch_size]
        fresh = c.document(cli, state["binding"])
        for item in group:
            node_id = item.get("node_id")
            if node_id and c.fingerprint(fresh, node_id) != c.fingerprint(doc, node_id):
                c.fail("CONCURRENT_EDIT", "本组资产自盘点后已变化，停止覆盖。", key=item["key"], node_id=node_id)
            if not node_id and any(c.kind_of(n) == item["kind"] and c.name_of(fresh, nid) == item["name"]
                                   for nid, n in c.nodes(fresh).items()):
                c.fail("CONCURRENT_EDIT", "本组新建前出现同名节点，请重新核对。", key=item["key"])
            if node_id:
                data = c.content(fresh, node_id)
                current = data.get("appearanceInfo", {}).get("description", "") if item["kind"] in ("role", "scene") else c.prompt_of(fresh, node_id)
                mapping = state["mappings"].get(item["key"], {})
                approved = decisions.get(item["key"], {}).get("overwrite_fingerprint") == c.fingerprint(fresh, node_id)
                if current and current != item["description"] and not approved:
                    c.fail("CONCURRENT_EDIT", "资产已有不同描述，需明确选择后修改。", key=item["key"], node_id=node_id)
                view = data.get("basicInfo", {}).get("appearanceName", "")
                if view and mapping.get("stage") == "ready" and not approved:
                    c.fail("CONCURRENT_EDIT", "资产形象/视图名称已改变。", key=item["key"], node_id=node_id)
        doc = fresh
        new_items = [item for item in group if not item.get("node_id") and not item.get("allocated_id")]
        if new_items:
            c.check_control(cli)
            reserved = cli.allocate_many(len(new_items))
            for item, node_id in zip(new_items, reserved):
                item["allocated_id"] = node_id
                state["mappings"][item["key"]] = {"node_id": node_id, "name": item["name"], "kind": item["kind"], "stage": "allocated"}
            c.save(project, state)  # All reserved identities survive before any create is sent.
        for item in group:
            node_id = item.get("node_id") or item["allocated_id"]
            data = c.content(doc, node_id)
            is_new = not item.get("node_id")
            mapping = state["mappings"].setdefault(item["key"], {"node_id": node_id, "name": item["name"], "kind": item["kind"]})
            mapping["stage"] = "allocated" if is_new else "writing"
            c.save(project, state)
            if is_new:
                acknowledge(project, cli, state, "create_biz_node",
                            {"nodeKind": item["kind"], "id": node_id, "initialData": c.initial_data(item), "topLeft": positions[item["key"]]},
                            [{"action": "node_kind", "node_id": node_id, "kind": item["kind"]}])
                created += 1
                mapping["stage"] = "writing"
                c.save(project, state)
            if item["kind"] in ("role", "scene"):
                command = "xyq." + item["kind"] + ".updateDescription"
                if is_new or c.name_of(doc, node_id) != item["name"] or data.get("basicInfo", {}).get("name") != item["name"]:
                    acknowledge(project, cli, state, command,
                                {"nodeId": node_id, "assetId": node_id, "field": "characterName" if item["kind"] == "role" else "sceneName", "value": item["name"]},
                                [{"action": "rename", "node_id": node_id, "value": item["name"]}])
                if not is_new and data.get("basicInfo", {}).get("appearanceName") != "":
                    acknowledge(project, cli, state, command,
                                {"nodeId": node_id, "assetId": node_id, "field": "appearanceName" if item["kind"] == "role" else "sceneViewName", "value": ""},
                                [{"action": "field", "node_id": node_id, "value": ""}])
                if is_new or data.get("appearanceInfo", {}).get("description") != item["description"]:
                    acknowledge(project, cli, state, command,
                                {"nodeId": node_id, "assetId": node_id, "field": "appearance" if item["kind"] == "role" else "description", "value": item["description"]},
                                [{"action": "description", "node_id": node_id, "value": item["description"]}])
            else:
                patch = {"name": item["name"], "caption": item["name"]}
                if is_new or not all(data.get(k) == v for k, v in patch.items()):
                    acknowledge(project, cli, state, "update_node_data", {"id": node_id, "patch": patch},
                                [{"action": "patch", "node_id": node_id, "patch": patch}])
                if is_new or not c.target_satisfied(doc, {"action": "prompt", "node_id": node_id, "value": item["description"]}):
                    acknowledge(project, cli, state, "xyq.generation.update_prompt", {"nodeId": node_id, "prompt": item["description"]},
                                [{"action": "prompt", "node_id": node_id, "value": item["description"]}])
            item["node_id"] = node_id
        # All fields for every node are checked together; no sampling or skipped fields.
        doc = c.document(cli, state["binding"])
        c.reconcile(project, cli, state, doc)
        for item in group:
            node_id = item["node_id"]
            if not all(c.target_satisfied(doc, target) for target in asset_targets(item, node_id)):
                c.fail("READBACK_FAILED", "资产节点最终字段未全部通过核对。", key=item["key"], node_id=node_id)
            mapping = state["mappings"][item["key"]]
            mapping.update(stage="ready", name=item["name"], appearance_name="", description_hash=c.digest(item["description"]))
            completed.append(item["key"])
            node_ids[item["key"]] = node_id
        c.save(project, state)
        progress({"completed": len(completed), "total": len(items), "node_ids": copy.deepcopy(node_ids)})
    return {"status": "synced", "count": len(items), "created": created, "reused": len(items) - created}


def run_job(project, manifest, check_control, progress):
    cli = ControlledCLI(project, check_control)
    session, options, kind = manifest["session"], manifest["options"], manifest["kind"]
    check_control()
    if kind == "assets":
        return sync_assets(project, cli, session, progress, options["decisions"])
    if kind == "images":
        result = c.media.images(project, cli, Path(options["folder"]), options["decisions"])
        count = len(result.get("imported", [])) + len(result.get("unchanged", []))
        progress({"completed": count})
        # The original image workflow reports unmatched/missing files separately.
        unresolved = any(result.get(key) for key in ("processing", "conflicts", "unbound_assets", "duplicates", "invalid"))
        result["status"] = "synced" if result.get("status") == "images_imported" and not unresolved and result.get("references", {}).get("status") != "pending" else "partial"
        return result
    result = c.sync(project, cli, "videos", request_id=options["request_id"], ids=options["ids"],
                    decisions=options["decisions"], existing_only=options["existing_only"],
                    references_only=options["references_only"], bind_references=True, session_override=session)
    mappings = c.read_state(project)["mappings"]
    versions = {u["id"]: u["version"] for u in session["units"]}
    verified = {key: mappings[key]["node_id"] for key in options["ids"] if key in mappings
                and mappings[key].get("stage") == "ready" and mappings[key].get("version") == versions[key]
                and mappings[key].get("references_bound") is True}
    progress({"completed": len(verified), "total": len(options["ids"]), "node_ids": verified})
    if result.get("status") == "synced" and (len(verified) != len(options["ids"]) or result.get("unbound_units") or result.get("skipped")):
        result["status"] = "partial"
    return result
