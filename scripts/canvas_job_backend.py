#!/usr/bin/env python3
"""CLI-only queue backend. Serialized writes, bounded grouped asset verification."""
import copy
import json
from pathlib import Path
import time
import uuid

import canvas as c
import director as d

ASSET_READY_ATTEMPTS = 3
ASSET_RETRY_ATTEMPTS = 3
ASSET_READY_DELAY = 0.5


def check_boundary(cli):
    callback = getattr(getattr(cli, "check_control", None), "boundary", None)
    if callable(callback):
        callback()
    else:
        c.check_control(cli)


def business_ready(doc, node_id, kind):
    """Canvas node existence alone does not establish the business asset."""
    return (node_id in c.nodes(doc) and c.kind_of(c.nodes(doc)[node_id]) == kind
            and isinstance(doc.get("assets", {}).get(node_id, {}).get("content"), dict)
            and isinstance(c.content(doc, node_id).get("basicInfo"), dict)
            and isinstance(c.content(doc, node_id)["basicInfo"].get("name"), str)
            and bool(c.content(doc, node_id)["basicInfo"]["name"]))


def wait_business_ready(project, cli, state, node_id, kind, doc=None):
    for attempt in range(ASSET_READY_ATTEMPTS):
        c.check_control(cli)
        if doc is None or attempt:
            doc = c.document(cli, state["binding"])
        if business_ready(doc, node_id, kind):
            for mapping in state["mappings"].values():
                if mapping.get("node_id") == node_id:
                    mapping["business_readiness"] = {"status": "available", "attempts": attempt + 1,
                                                     "checked_at": time.time(), "fingerprint": c.fingerprint(doc, node_id)}
            c.save(project, state)
            return doc
        if node_id in c.nodes(doc) and c.kind_of(c.nodes(doc)[node_id]) != kind:
            c.fail("CONCURRENT_EDIT", "新建资产节点类型已变化，停止写入。", node_id=node_id)
        if attempt + 1 < ASSET_READY_ATTEMPTS:
            c.check_control(cli)
            time.sleep(ASSET_READY_DELAY)
    for mapping in state["mappings"].values():
        if mapping.get("node_id") == node_id:
            mapping["business_readiness"] = {"status": "unavailable", "attempts": ASSET_READY_ATTEMPTS,
                                             "checked_at": time.time()}
    c.save(project, state)
    c.fail("ASSET_NOT_READY", "创建已确认，但业务资产尚未就绪；保留原ID，等待明确恢复。", node_id=node_id)


def observation(doc, node_id):
    data = c.content(doc, node_id)
    appearance = copy.deepcopy(data.get("appearanceInfo") or {})
    description = appearance.pop("description", "")
    return {"kind": c.kind_of(c.nodes(doc)[node_id]), "name": c.name_of(doc, node_id),
            "basic_name": data.get("basicInfo", {}).get("name", ""),
            "view": data.get("basicInfo", {}).get("appearanceName", ""),
            "description": description, "media": appearance,
            "source_infos": copy.deepcopy(data.get("sourceInfos", {}))}


def after_observation(before, value):
    result = copy.deepcopy(before)
    field = value.get("field")
    if field in ("characterName", "sceneName"):
        result.update(name=value["value"], basic_name=value["value"])
    elif field in ("appearanceName", "sceneViewName"):
        result["view"] = value["value"]
    elif field in ("appearance", "description"):
        result["description"] = value["value"]
    return result


def pristine_initialization(doc, node_id, kind, name):
    current = observation(doc, node_id)
    factory_name = "未命名角色" if kind == "role" else "未命名场景"
    return (current["name"] in (name, factory_name) and current["basic_name"] in (name, factory_name)
            and not current["view"] and not current["description"] and not c.ready_image(doc, node_id)
            and not current["media"].get("candidatePippitAssetIds") and not current["source_infos"])


def missing_business_rejection(entry):
    """A narrow allowlist, based on the observed official negative response."""
    evidence = entry.get("rejection")
    if evidence is None:
        try:
            evidence = c.rejection_details(json.loads(entry.get("error", "")))
        except (ValueError, TypeError):
            return False
    return (entry.get("command") == "xyq.scene.updateDescription" and isinstance(evidence, dict)
            and evidence.get("ok") is False and evidence.get("code") == "INVARIANT_FAILED"
            and evidence.get("message") == "scene asset not found")


def recover_asset_rejection(project, cli, operation_id, apply=False, fingerprint=None):
    """Read back a legacy misclassified rename; apply changes local journal only."""
    state, doc = c.connected(project, cli)
    entry = next((e for e in state["journal"] if e["id"] == operation_id), None)
    d.require(entry is not None, "找不到指定操作")
    value = entry.get("input", {})
    node_id = value.get("nodeId")
    matches = [(key, m) for key, m in state["mappings"].items() if m.get("node_id") == node_id]
    d.require(entry.get("source") == "asset_job" and entry.get("status") == "uncertain"
              and missing_business_rejection(entry) and value.get("field") == "sceneName"
              and value.get("assetId") == node_id and len(matches) == 1,
              "只支持有明确拒绝证据的旧资产改名记录；未知写入仍须核对")
    key, mapping = matches[0]
    d.require(mapping.get("kind") == "scene" and mapping.get("stage") == "writing"
              and value.get("value") == mapping.get("name")
              and entry.get("targets") == [{"action": "rename", "node_id": node_id, "value": value["value"]}],
              "操作与原资产映射不一致")
    earlier = state["journal"][:state["journal"].index(entry)]
    creation = next((e for e in reversed(earlier) if e.get("command") == "create_biz_node"
                     and e.get("input", {}).get("id") == node_id), None)
    d.require(creation and creation.get("status") in ("acknowledged", "verified")
              and creation["input"].get("nodeKind") == "scene"
              and creation["input"].get("initialData") == c.initial_data({"kind": "scene", "name": mapping["name"]}),
              "缺少同一原ID的已确认创建记录")
    relevant = [e for e in state["journal"] if e is not entry
                and any(t.get("node_id") == node_id for t in e.get("targets", []))]
    d.require(not any(e.get("status") in ("pending", "uncertain") for e in relevant),
              "原节点还有其他未知操作，不能恢复")
    d.require(business_ready(doc, node_id, "scene"), "原业务资产尚未就绪，未修改日志")
    current = observation(doc, node_id)
    d.require(pristine_initialization(doc, node_id, "scene", mapping["name"]),
              "原节点已有其他内容或人工修改，不能自动恢复")
    result = {"status": "recoverable", "operation_id": operation_id, "key": key, "node_id": node_id,
              "fingerprint": c.fingerprint(doc, node_id), "remote_write": False}
    if apply:
        d.require(fingerprint == result["fingerprint"], "请先预览并使用相同的当前节点摘要；节点变化时重新核对")
        entry.update(status="rejected", rejection=c.rejection_details(json.loads(entry["error"])),
                     recovery={"reason": "legacy_definite_rejection", "previous_status": "uncertain",
                               "checked_at": time.time(), "fingerprint": fingerprint}, baseline=current)
        if all(c.target_satisfied(doc, t) for t in entry["targets"]):
            entry.update(status="verified", resolution="readback_after_rejection", after={node_id: fingerprint})
        c.save(project, state)
        result["status"] = "recovered_local_only"
        result["journal_status"] = entry["status"]
    return result


def asset_update(project, cli, state, command, value, targets, baseline):
    """Retry only an explicit asset-not-found refusal on the unchanged real ID."""
    for attempt in range(ASSET_RETRY_ATTEMPTS):
        try:
            acknowledge(project, cli, state, command, value, targets, baseline=baseline)
            return after_observation(baseline, value)
        except c.CanvasError as exc:
            if exc.code != "COMMAND_REJECTED":
                raise
            entry = state["journal"][-1]
            if not missing_business_rejection(entry):
                raise
            fresh = c.document(cli, state["binding"])
            node_id = value["nodeId"]
            if not business_ready(fresh, node_id, "scene"):
                fresh = wait_business_ready(project, cli, state, node_id, "scene", fresh)
            current = observation(fresh, node_id)
            expected = after_observation(baseline, value)
            if current == expected and all(c.target_satisfied(fresh, t) for t in targets):
                entry.update(status="verified", resolution="readback_after_rejection",
                             after={node_id: c.fingerprint(fresh, node_id)})
                c.save(project, state)
                return expected
            if current != baseline:
                c.fail("CONCURRENT_EDIT", "拒绝后的原节点字段已变化，停止重试。", node_id=node_id, operation=entry["id"])
            if attempt + 1 == ASSET_RETRY_ATTEMPTS:
                raise
            c.check_control(cli)
            time.sleep(ASSET_READY_DELAY)


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


def acknowledge(project, cli, state, command, value, targets, baseline=None):
    # Pause is checked BEFORE journaling, so an unsent request is never ambiguous.
    c.check_control(cli)
    entry = {"id": uuid.uuid4().hex, "command": command, "input": copy.deepcopy(value),
             "targets": copy.deepcopy(targets), "status": "pending", "source": "asset_job"}
    if baseline is not None:
        entry["baseline"] = copy.deepcopy(baseline)
    state["journal"].append(entry)
    c.save(project, state)
    try:
        cli.command(state["binding"]["canvas_id"], command, value)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        rejected = getattr(exc, "code", None) == "COMMAND_REJECTED"
        entry.update(status="rejected" if rejected else "uncertain", error=c.safe_message(exc),
                     error_code=getattr(exc, "code", "ERROR"))
        if rejected:
            entry["rejection"] = getattr(exc, "details", {}).get("rejection")
        c.save(project, state)
        if rejected:
            c.fail("COMMAND_REJECTED", c.safe_message(exc), operation=entry["id"], command=command,
                   node_id=value.get("nodeId", value.get("id")))
        c.fail("UNCERTAIN_WRITE", command + "写入结果未知；保留原ID，停止后续写入。原因：" + c.safe_message(exc),
               operation=entry["id"], command=command, node_id=value.get("nodeId", value.get("id")))
    entry["status"] = "acknowledged"
    for previous in state["journal"]:
        if (previous.get("status") == "rejected" and previous.get("source") == "asset_job"
                and previous.get("command") == command and previous.get("input") == value):
            previous["resolved_by"] = entry["id"]
    c.save(project, state)


def sync_assets(project, cli, session, progress, decisions=None, batch_size=10):
    d.require(1 <= batch_size <= 50, "分组核对数量须为1至50；这不是服务端并发限制")
    decisions = decisions or {}
    items = asset_items(session)
    state, doc = c.connected(project, cli)
    # A confirmed canvas node may not yet expose its backing business asset.
    for key, mapping in state["mappings"].items():
        node_id, kind = mapping.get("node_id"), mapping.get("kind")
        if mapping.get("stage") == "writing" and kind in ("role", "scene") and node_id in c.nodes(doc):
            doc = wait_business_ready(project, cli, state, node_id, kind, doc)
    c.reconcile(project, cli, state, doc, decisions=decisions)
    for key, mapping in state["mappings"].items():
        node_id = mapping.get("node_id")
        creation = next((e for e in state["journal"] if e.get("command") == "create_biz_node"
                         and e.get("input", {}).get("id") == node_id and e.get("status") == "verified"), None)
        business_writes = [e for e in state["journal"] if e.get("source") == "asset_job"
                           and e.get("input", {}).get("nodeId") == node_id]
        if (mapping.get("stage") == "writing" and mapping.get("kind") in ("role", "scene")
                and creation and not business_writes and node_id in c.nodes(doc)
                and not pristine_initialization(doc, node_id, mapping["kind"], mapping["name"])):
            c.fail("CONCURRENT_EDIT", "原新建资产在初始化前已有其他内容，停止覆盖。", key=key, node_id=node_id)
        rejected = next((e for e in reversed(state["journal"]) if e.get("status") == "rejected"
                         and not e.get("resolved_by")
                         and e.get("source") == "asset_job" and e.get("input", {}).get("nodeId") == node_id), None)
        if rejected and rejected.get("baseline") and node_id in c.nodes(doc):
            current = observation(doc, node_id)
            if current not in (rejected["baseline"], after_observation(rejected["baseline"], rejected["input"])):
                c.fail("CONCURRENT_EDIT", "原资产在拒绝后已变化，不能自动续写。", key=key, node_id=node_id)
            if all(c.target_satisfied(doc, t) for t in rejected["targets"]):
                rejected.update(status="verified", resolution="readback_after_rejection",
                                after={node_id: c.fingerprint(doc, node_id)})
                c.save(project, state)
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
                and c.content(doc, node_id).get("basicInfo", {}).get("name") in
                    (mapping["name"], "未命名角色" if mapping["kind"] == "role" else "未命名场景")):
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
        check_boundary(cli)
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
                if is_new:
                    doc = wait_business_ready(project, cli, state, node_id, item["kind"])
                    data = c.content(doc, node_id)
                    if not pristine_initialization(doc, node_id, item["kind"], item["name"]):
                        c.fail("CONCURRENT_EDIT", "新建资产在初始化前已有其他内容，停止覆盖。", key=item["key"], node_id=node_id)
                baseline = observation(doc, node_id)
                command = "xyq." + item["kind"] + ".updateDescription"
                if is_new or c.name_of(doc, node_id) != item["name"] or data.get("basicInfo", {}).get("name") != item["name"]:
                    baseline = asset_update(project, cli, state, command,
                                {"nodeId": node_id, "assetId": node_id, "field": "characterName" if item["kind"] == "role" else "sceneName", "value": item["name"]},
                                [{"action": "rename", "node_id": node_id, "value": item["name"]}], baseline)
                if not is_new and data.get("basicInfo", {}).get("appearanceName") != "":
                    baseline = asset_update(project, cli, state, command,
                                {"nodeId": node_id, "assetId": node_id, "field": "appearanceName" if item["kind"] == "role" else "sceneViewName", "value": ""},
                                [{"action": "field", "node_id": node_id, "value": ""}], baseline)
                if is_new or data.get("appearanceInfo", {}).get("description") != item["description"]:
                    baseline = asset_update(project, cli, state, command,
                                {"nodeId": node_id, "assetId": node_id, "field": "appearance" if item["kind"] == "role" else "description", "value": item["description"]},
                                [{"action": "description", "node_id": node_id, "value": item["description"]}], baseline)
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
        check_boundary(cli)
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
        matched = c.media.scan_images(session, Path(options["folder"]))["matched"]
        total = sum(not options["decisions"].get(item["key"], {}).get("skip") for item in matched)
        progress({"total": total, "stage": "uploading_images"})
        result = c.media.images(project, cli, Path(options["folder"]), options["decisions"])
        check_boundary(cli)
        count = len(result.get("imported", [])) + len(result.get("unchanged", []))
        progress({"completed": count, "total": total})
        # The original image workflow reports unmatched/missing files separately.
        unresolved = any(result.get(key) for key in ("processing", "conflicts", "unbound_assets", "duplicates", "invalid"))
        result["status"] = "synced" if result.get("status") == "images_imported" and not unresolved and result.get("references", {}).get("status") != "pending" else "partial"
        return result
    versions = {u["id"]: u["version"] for u in session["units"]}
    verified = {}
    # Counts below refer to nodes read back during this run, not merely old mappings.
    progress({"completed": 0, "total": len(options["ids"]), "stage": "preparing", "node_ids": {}})
    def node_progress(value):
        value = dict(value)
        key = value.pop("verified_unit", None)
        if key in options["ids"]:
            mapping = c.read_state(project)["mappings"].get(key, {})
            if (mapping.get("stage") == "ready" and mapping.get("version") == versions[key]
                    and mapping.get("references_bound") is True):
                verified[key] = mapping["node_id"]
        progress({**value, "completed": len(verified), "total": len(options["ids"]), "node_ids": dict(verified)})
    result = c.sync(project, cli, "videos", request_id=options["request_id"], ids=options["ids"],
                    decisions=options["decisions"], existing_only=options["existing_only"],
                    references_only=options["references_only"], bind_references=True, session_override=session,
                    progress=node_progress)
    progress({"completed": len(verified), "total": len(options["ids"]), "node_ids": verified,
              "stage": "verified" if len(verified) == len(options["ids"]) else "needs_attention"})
    if result.get("status") == "synced" and (len(verified) != len(options["ids"]) or result.get("unbound_units") or result.get("skipped")):
        result["status"] = "partial"
    return result
