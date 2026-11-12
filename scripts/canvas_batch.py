"""Batched Canvas execution: official local compilation, submission and grouped readback.

The CLI adapter owns SDK behavior; canvas.py owns field/reference contracts;
this module owns grouping, durable identities, transaction receipts and acceptance.
"""
import copy
from pathlib import Path
import time
import uuid

import canvas as c
import director as d

DEFAULT_BATCH_SIZE = 20


def command(name, value):
    return {"name": name, "input": copy.deepcopy(value)}


def uses_semantic_writes(cli):
    supported = getattr(cli, "uses_semantic_writes", None)
    return supported is not None and supported()


def reference_edges(doc, node_id):
    return sorted((copy.deepcopy(e) for e in doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {}).values()
                   if e.get("target") == node_id and e.get("type") == "reference"), key=lambda e: e["id"])


def exact_targets(doc, node_id):
    return [{"action": "generation_settings", "node_id": node_id,
             "value": copy.deepcopy(c.content(doc, node_id).get("generation") or {})},
            {"action": "reference_graph", "node_id": node_id, "value": reference_edges(doc, node_id)}]


def boundary(cli):
    callback = getattr(getattr(cli, "check_control", None), "boundary", None)
    callback() if callable(callback) else c.check_control(cli)


def snapshot(cli, state, previous, items, sources=(), asset_reference_policy=None):
    boundary(cli)
    value = cli.batch_snapshot(state["binding"])
    fresh = value["document"]
    targets = {i.get('node_id') for i in items}
    from canvas_asset_reference_policy import source_target
    for node_id in targets | set(sources):
        if node_id and node_id not in targets:
            identity = source_target(asset_reference_policy, previous, node_id)
            if identity['action'] == 'bound_asset_identity':
                if not c.target_satisfied(fresh, identity):
                    c.fail('CONCURRENT_EDIT', '引用资产原节点已消失或类型改变，不能按名称替代。', node_id=node_id)
                continue
        if node_id and (node_id not in c.nodes(fresh) or node_id not in c.nodes(previous)
                        or c.fingerprint(fresh, node_id) != c.fingerprint(previous, node_id)):
            c.fail("CONCURRENT_EDIT", "批次中的目标或引用资产已变化，停止写入。", node_id=node_id)
    for item in items:
        if not item.get("node_id") and any(c.kind_of(n) == item["kind"] and c.name_of(fresh, nid) == item["name"]
                                           for nid, n in c.nodes(fresh).items()):
            c.fail("CONCURRENT_EDIT", "批量创建前出现同名节点，停止写入。", key=item["key"])
    return value


def reserve(project, cli, state, items):
    missing = [i for i in items if not i.get("node_id") and not i.get("allocated_id")]
    if missing:
        c.check_control(cli)
        for item, node_id in zip(missing, cli.allocate_many(len(missing))):
            item["allocated_id"] = node_id
            state["mappings"][item["key"]] = {"node_id": node_id, "name": item["name"],
                                               "kind": item["kind"], "stage": "allocated"}
        c.save(project, state)
    ids = [i.get("node_id") or i["allocated_id"] for i in items]
    d.require(len(set(ids)) == len(ids), "一个批次不能重复写入同一节点")
    return ids


def submit(project, cli, state, baseline, compiled, commands, targets, receipts=(), asset_reference_policy=None):
    """Save the complete request before sending. Never replay an ambiguous batch."""
    patches = compiled["patches"]
    policy = asset_reference_policy
    if policy is not None:
        # A reference-only choice cannot smuggle any asset field mutation through
        # an SDK patch, including source node metadata stored in the root asset.
        for row in policy['nodes'].values():
            nid = row['node_id']
            d.require(c.fingerprint(compiled['document'], nid) == c.fingerprint(baseline['document'], nid),
                      '按绑定ID引用不得编译任何资产字段写入：' + nid)
    # Source selection changes after compilation must not silently validate an
    # older plan. Sources without media are valid and still receive real edges.
    touched = {t["node_id"] for t in targets}
    sources = {sid for nid in touched for sid in c.node_asset_ids(c.prompt_of(compiled["document"], nid))}
    sources.update(e["source"] for nid in touched for e in reference_edges(compiled["document"], nid))
    from canvas_asset_reference_policy import source_target
    for sid in sources - touched:
        target = source_target(policy, compiled['document'], sid)
        if target['action'] == 'bound_asset_identity':
            target['consumers'] = sorted(nid for nid in touched if sid in c.node_asset_ids(c.prompt_of(compiled['document'], nid))
                or any(e['source'] == sid for e in reference_edges(compiled['document'], nid)))
        targets.append(target)
    if not patches:
        return None
    c.check_control(cli)
    doc, versions = baseline["document"], baseline["asset_versions"]
    operation = uuid.uuid4().hex
    use_semantic = uses_semantic_writes(cli)
    affected = {patch["assetId"] for patch in patches}
    if use_semantic:
        affected.update(t["node_id"] for t in targets)
        affected.add(doc["rootCanvasId"])
    expected = {}
    for asset_id in sorted(affected):
        version = versions.get(asset_id) if asset_id in doc["assets"] else 0
        d.require(type(version) is int and version >= 0, "目标资产缺少可核验版本，不能提交批次：" + asset_id)
        expected[asset_id] = version
    if use_semantic:
        request = {"canvas_id": doc["rootCanvasId"], "expected_versions": expected, "commands": commands}
        send = cli.apply_semantic_batch
        # reserve() has durably bound any newly allocated IDs. Keep the prior
        # mapping and accepted receipt when a precondition rejects all writes.
        stored = c.read_state(project)["mappings"]
        previous_mappings = {key: stored.get(key) for key, value in state["mappings"].items()
                             if value.get("node_id") in touched and stored.get(key) != value}
    else:
        converted = [{"asset_id": p["assetId"], "base_asset_version": expected[p["assetId"]], "op": p["op"],
                      "path": p["path"], **({"value": p["value"]} if p["op"] != "remove" else {})} for p in patches]
        request = {"batch_id": "seedance-" + operation, "client_id": "seedance-batch-" + operation,
                   "root_pippit_asset_id": doc["rootCanvasId"],
                   "transactions": [{"transaction_id": operation, "patches": converted}]}
        send = cli.apply_batch
    d.require(len(d.packed(request).encode("utf-8")) <= 64 * 1024 * 1024, "批量请求超过CLI输入限制，尚未发送")
    path = Path(project) / "canvas" / "batches" / (operation + ".json")
    transport = {"transport": "public-semantic-commands-v1"} if use_semantic else {}
    d.write_json(path, {"protocol": c.BATCH_PROTOCOL, **transport, "request": request,
                        "runtime_sha256": compiled["runtime_sha256"]})
    entry = {"id": operation, "command": "canvas_batch", "protocol": c.BATCH_PROTOCOL,
             **transport, "input": {"request_path": str(path), "request_sha256": c.digest(request)},
             "targets": copy.deepcopy(targets), "status": "pending"}
    for receipt in receipts:
        receipt["steps"].append(operation)
        receipt["batch_operation_id"] = operation
        d.write_json(receipt["path"], receipt)
    state["journal"].append(entry)
    c.save(project, state)
    try:
        send(request)
    except BaseException as exc:
        # Queue control exceptions are raised before transport. Unlike timeouts,
        # these are known not-sent requests and must not poison future resume.
        from canvas_jobs import PauseRequested, Superseded
        if isinstance(exc, (PauseRequested, Superseded)):
            entry.update(status="cancelled", resolution="control_before_send")
            for mapping in state["mappings"].values():
                if mapping.get("node_id") in {t["node_id"] for t in targets}:
                    mapping["stage"] = "ready" if mapping["node_id"] in c.nodes(doc) else "allocated"
            c.save(project, state)
            raise
        if not isinstance(exc, (ValueError, OSError, KeyError, TypeError)):
            # A killed process/unknown failure leaves the durable pending entry.
            raise
        code = getattr(exc, "code", None)
        rejected = code in ("COMMAND_REJECTED", "CONCURRENT_EDIT")
        entry.update(status="rejected" if rejected else "uncertain", error=c.safe_message(exc))
        if use_semantic:
            completed = getattr(exc, "details", {}).get("completed")
            if type(completed) is int:
                entry["confirmed_commands"] = completed
            if rejected:
                for key, previous in previous_mappings.items():
                    if previous is None:
                        state["mappings"].pop(key, None)
                    else:
                        state["mappings"][key] = previous
            for receipt in receipts:
                receipt.update(status=entry["status"], error=entry["error"])
                d.write_json(receipt["path"], receipt)
        c.save(project, state)
        c.fail(code if rejected else "UNCERTAIN_WRITE",
               "批量写入未全部确认；已保留节点ID和请求记录。" + c.safe_message(exc), operation=operation)
    entry["status"] = "acknowledged"
    c.save(project, state)
    return entry


def readback(project, cli, state, entry, targets):
    for attempt in range(c.VIDEO_READBACK_ATTEMPTS):
        boundary(cli)
        doc = cli.batch_snapshot(state["binding"])["document"]
        if all(c.target_satisfied(doc, t) for t in targets):
            if entry:
                entry.update(status="verified", after={t["node_id"]: c.fingerprint(doc, t["node_id"]) for t in targets})
                c.save(project, state)
            return doc
        if attempt + 1 < c.VIDEO_READBACK_ATTEMPTS:
            c.check_control(cli)
            time.sleep(c.VIDEO_READBACK_DELAY)
    if entry:
        entry.update(status="uncertain", error="批次最终字段或引用回读不一致")
        c.save(project, state)
    c.fail("READBACK_FAILED", "批次最终字段或引用未全部通过核验；保留原事务和节点，停止续写。",
           operation=entry["id"] if entry else "")


def write_asset_group(project, cli, state, doc, items, positions):
    sources = {s["node_id"] for i in items for s in i.get("image_reference_sources", [])}
    baseline = snapshot(cli, state, doc, items, sources)
    doc = baseline["document"]
    ids = reserve(project, cli, state, items)
    creates = [command("create_biz_node", {"nodeKind": i["kind"], "id": nid,
                "initialData": c.initial_data(i), "topLeft": positions[i["key"]]})
               for i, nid in zip(items, ids) if not i.get("node_id")]
    staged = cli.compile_batch(doc, creates)["document"] if creates else doc
    commands = list(creates)
    # Factory node display names may differ from initial asset content. Initialize
    # every dependency's business name before resolving any derived reference.
    for item, node_id in zip(items, ids):
        for name, value, _ in c.asset_initialization_steps(staged, item, node_id):
            commands.append(command(name, value))
    if commands:
        staged = cli.compile_batch(doc, commands)["document"]
    targets = []
    for item, node_id in zip(items, ids):
        item.update(c.resolve_asset_image_item(item, state, staged))
        for name, value, _ in c.asset_initialization_steps(staged, item, node_id):
            commands.append(command(name, value))
        targets.extend(c.asset_targets(item, node_id))
        if item.get("verify_image_defaults"):
            targets.append(c.asset_settings_target(node_id))
    compiled = cli.compile_batch(doc, commands)
    # Compile derived-asset reference inputs after all IDs exist locally. The
    # same pure contract is used by the sequential historical execution path.
    input_plans = []
    for item, node_id in zip(items, ids):
        if not item.get("image_reference_assets"):
            continue
        original = dict(item, node_id=node_id)
        local_state = copy.deepcopy(state)
        local_state["mappings"].setdefault(item["key"], {}).update(node_id=node_id,
            prompt_hash=c.digest(c.prompt_of(compiled["document"], node_id)))
        plan = c.asset_image_reference_plan(original, local_state, compiled["document"])
        input_plans.append(plan)
        commands.extend(plan["commands"])
        targets.extend(plan["targets"])
    if input_plans:
        compiled = cli.compile_batch(doc, commands)
    for item, node_id in zip(items, ids):
        targets.extend(exact_targets(compiled["document"], node_id))
        state["mappings"].setdefault(item["key"], {}).update(node_id=node_id, kind=item["kind"],
            name=item["name"], stage="writing", batch_initialization=True)
    entry = submit(project, cli, state, baseline, compiled, commands, targets)
    actual = readback(project, cli, state, entry, targets)
    for item, node_id in zip(items, ids):
        item["node_id"] = node_id
        state["mappings"][item["key"]].update(stage="ready", appearance_name="",
            prompt_hash=c.digest(c.prompt_of(actual, node_id)), asset_contract=c.ASSET_CONTRACT)
        state["mappings"][item["key"]].pop("batch_initialization", None)
    for plan in input_plans:
        state["mappings"][plan["key"]]["image_input"] = plan["image_input"]
    c.save(project, state)
    return actual


def sync_asset_inputs(project, cli, session, state, doc, keys=None, batch_size=DEFAULT_BATCH_SIZE):
    items = [i for i in c.desired_assets(session) if i.get("image_reference_assets") and (keys is None or i["key"] in keys)]
    for offset in range(0, len(items), batch_size):
        group = items[offset:offset + batch_size]
        for item in group:
            item["node_id"] = state["mappings"].get(item["key"], {}).get("node_id")
        sources = {state["mappings"].get(r["key"], {}).get("node_id") for i in group for r in i["image_reference_assets"]}
        baseline = snapshot(cli, state, doc, group, sources - {None})
        plans = [c.asset_image_reference_plan(i, state, baseline["document"]) for i in group]
        commands = [cmd for p in plans for cmd in p["commands"]]
        compiled = cli.compile_batch(baseline["document"], commands)
        targets = [t for i in group for t in exact_targets(compiled["document"], i["node_id"])]
        targets.extend(t for p in plans for t in p["targets"])
        for plan in plans:
            state["mappings"][plan["key"]]["image_input"] = {**plan["image_input"], "status": "pending"}
        entry = submit(project, cli, state, baseline, compiled, commands, targets)
        doc = readback(project, cli, state, entry, targets)
        for plan in plans:
            mapping = state["mappings"][plan["key"]]
            mapping.update(image_input=plan["image_input"], prompt_hash=c.digest(c.prompt_of(doc, plan["node_id"])))
        c.save(project, state)
    return doc


def sync_asset_groups(project, cli, session, state, doc, pending, completed, node_ids, progress, batch_size):
    positions = c.layout_positions(doc, pending)
    created = sum(not i.get("node_id") for i in pending)
    for offset in range(0, len(pending), batch_size):
        group = pending[offset:offset + batch_size]
        doc = write_asset_group(project, cli, state, doc, group, positions)
        for item in group:
            completed.append(item["key"])
            node_ids[item["key"]] = item["node_id"]
        progress({"stage": "batch_verified", "completed": len(completed), "node_ids": copy.deepcopy(node_ids)})
    doc = sync_asset_inputs(project, cli, session, state, doc)
    return {"status": "synced", "count": len(completed), "created": created, "reused": len(completed) - created,
            "sync_protocol": c.BATCH_PROTOCOL, "image_inputs": c.asset_image_input_status(state)}


def prepare_assets(project, cli, session, state, doc, plan, summary):
    pending = []
    for item in plan:
        key, node_id = item["key"], item.get("node_id")
        if item["resolution"] == "create" or state["mappings"].get(key, {}).get("stage") in ("allocated", "writing"):
            pending.append(dict(item, verify_image_defaults=True))
        else:
            state["mappings"].setdefault(key, {}).update(node_id=node_id, kind=item["kind"], name=item["name"],
                stage="ready", origin=item["resolution"])
            summary["adopted" if item["resolution"] == "adopted" else "reused"].append(key)
    positions = c.layout_positions(doc, pending)
    for offset in range(0, len(pending), DEFAULT_BATCH_SIZE):
        group = pending[offset:offset + DEFAULT_BATCH_SIZE]
        doc = write_asset_group(project, cli, state, doc, group, positions)
        summary["created"].extend(i["key"] for i in group)
    keys = {i["key"] for i in pending}
    doc = sync_asset_inputs(project, cli, session, state, doc, keys)
    summary["image_inputs"] = c.asset_image_input_status(state, keys)
    c.save(project, state)
    return doc, summary


def sync_videos(project, cli, state, doc, plan, pending, context, references_only=False, batch_size=DEFAULT_BATCH_SIZE, asset_reference_policy=None):
    policy_args = {'asset_reference_policy': asset_reference_policy} if asset_reference_policy is not None else {}
    if asset_reference_policy is not None:
        context['asset_reference_policy'] = asset_reference_policy
    positions = c.layout_positions(doc, plan)
    created = updated = reused = 0
    verified = set()
    use_semantic = uses_semantic_writes(cli)
    for offset in range(0, len(plan), batch_size):
        group = plan[offset:offset + batch_size]
        sources = {sid for i in group for sid in c.node_asset_ids(i["prompt"])}
        sources.update(i["black_node_id"] for i in group if c.edge_only_black(i) and i.get("black_node_id"))
        baseline = snapshot(cli, state, doc, group, sources, **policy_args)
        doc = baseline["document"]
        ids = reserve(project, cli, state, group)
        # Independent new videos can share one public structural transaction.
        # Source nodes already exist; prompt commands still run in authored order.
        group_creates = (use_semantic and sources.isdisjoint(ids)
                         and all(not i.get("node_id") and c.no_black(i) for i in group))
        creates = []
        commands, receipts, targets = [], [], []
        for item, node_id in zip(group, ids):
            context["active"] = {"key": item["key"], "node_id": node_id, "kind": "video", "name": item["name"]}
            is_new = not item.get("node_id")
            before = c.content(doc, node_id)
            names = {f: item["name"] for f in ("name", "caption", "title")}
            if is_new:
                create = command("create_biz_node", {"nodeKind": "video", "id": node_id,
                                 "initialData": {**names, "generation": copy.deepcopy(item.get("settings", {}))},
                                 "topLeft": positions[item["key"]]})
                if group_creates:
                    creates.append(create)
                else:
                    commands.append(create)
                created += 1
            else:
                reused += 1
                if not references_only:
                    current = before.get("generation") or {}
                    patch = {key: value for key, value in names.items()
                             if not use_semantic or before.get(key) != value}
                    changed = {key: value for key, value in item["settings"].items()
                               if not use_semantic or current.get(key) != value}
                    if changed:
                        # update_node_data replaces the generation object. The
                        # public writer re-merges only these edits at dispatch.
                        patch["generation"] = {**copy.deepcopy(current), **changed}
                    if patch:
                        update = command("update_node_data", {"id": node_id, "patch": patch})
                        if changed and use_semantic:
                            update["generation_overrides"] = changed
                        commands.append(update)
            # Refresh old edge-only role references using the shared contract.
            stale = c.stale_role_reference_edges(doc, node_id, item["prompt"])
            if stale:
                commands.append(command("delete_edges", {"ids": stale}))
            if c.edge_only_black(item) and item.get("black_node_id"):
                source = item["black_node_id"]
                d.require(c.black_prompt_absent(item["prompt"], source), "独立黑幕不能混入提示词")
                if c.reference_edge(doc, source, node_id) is None:
                    commands.append(command("create_edge", {"edge": {"id": "edge-black-" + uuid.uuid4().hex,
                        "type": "reference", "source": source, "target": node_id, "sourceHandle": "right", "targetHandle": "left"}}))
            commands.append(command("xyq.generation.update_prompt", {"nodeId": node_id, "prompt": item["prompt"]}))
            core = [{"action": "node_kind", "node_id": node_id, "kind": "video"},
                    {"action": "prompt_text", "node_id": node_id, "value": item["prompt"]}]
            if not references_only:
                core += [{"action": "patch", "node_id": node_id, "patch": names},
                         {"action": "generation_settings", "node_id": node_id, "value": item["settings"]}]
            receipt_id = uuid.uuid4().hex
            receipt = {"id": receipt_id, "key": item["key"], "node_id": node_id, "protocol": c.BATCH_PROTOCOL,
                       "status": "writing", "steps": [], "targets": core, "reference_targets": [],
                       "references_only": references_only,
                       "path": str(Path(project) / "canvas" / "video-write-receipts" / (receipt_id + ".json"))}
            receipts.append(receipt)
            targets.extend(core)
            mapping = state["mappings"].setdefault(item["key"], {})
            mapping.update(node_id=node_id, kind="video", name=item["name"], stage="writing",
                           video_receipt_id=receipt_id, video_write_receipt=receipt["path"])
        commands = creates + commands
        compiled = cli.compile_batch(doc, commands)
        for item, receipt in zip(group, receipts):
            node_id = receipt["node_id"]
            receipt["reference_targets"] = exact_targets(compiled["document"], node_id)
            targets.extend(receipt["reference_targets"])
            d.write_json(receipt["path"], receipt)
        entry = submit(project, cli, state, baseline, compiled, commands, targets, receipts, **policy_args)
        try:
            actual = readback(project, cli, state, entry, targets)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            for receipt in receipts:
                receipt.update(status="uncertain", error=c.safe_message(exc))
                d.write_json(receipt["path"], receipt)
            raise
        for item, receipt in zip(group, receipts):
            item["node_id"] = receipt["node_id"]
            receipt.update(status="verified", core_verified=True, references_verified=True,
                checks=[{"action": t["action"], "matched": c.target_satisfied(actual, t)}
                        for t in receipt["targets"] + receipt["reference_targets"]])
            d.write_json(receipt["path"], receipt)
            context.setdefault("video_write_receipts", []).append(receipt["path"])
            if c.accept_synced_item(project, state, actual, item, pending, context, references_only):
                verified.add(item["key"])
            updated += c.fingerprint(doc, item["node_id"]) != c.fingerprint(actual, item["node_id"])
        doc = actual
        context["doc"] = doc
    context.pop("active", None)
    return doc, {"created": created, "updated": updated, "reused": reused}, verified
