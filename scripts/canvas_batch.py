"""Batched Canvas execution: official local compilation, one transaction, grouped readback.

The CLI adapter owns SDK behavior; canvas.py owns field/reference contracts;
this module owns grouping, durable identities, transaction receipts and acceptance.
"""
import copy
from pathlib import Path
import time
import uuid

import canvas as c
import director as d

DEFAULT_BATCH_SIZE = 12
VIDEO_BATCH_SIZE = 6
ORDINARY_COMMANDS = {"create_biz_node", "create_node", "update_node_data", "update_asset", "create_edge", "delete_edges"}


def command(name, value):
    return {"name": name, "input": copy.deepcopy(value)}


def reference_edges(doc, node_id):
    return sorted((copy.deepcopy(e) for e in doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {}).values()
                   if e.get("target") == node_id and e.get("type") == "reference"), key=lambda e: e["id"])


def exact_targets(doc, node_id):
    return [{"action": "semantic_generation_settings", "node_id": node_id,
             "value": copy.deepcopy(c.content(doc, node_id).get("generation") or {})},
            {"action": "semantic_reference_graph", "node_id": node_id, "value": reference_edges(doc, node_id)}]


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
        if node_id and (node_id not in c.nodes(fresh) or node_id not in c.nodes(previous) or node_id not in fresh['assets']):
            c.fail('READBACK_PENDING', '批次中的目标或引用节点尚未完整返回。', node_id=node_id)
        if node_id and node_id in previous['assets']:
            source_only = node_id not in targets
            observation = c.compare_expected(c.semantic_projection(fresh, node_id, source_only),
                                             c.semantic_projection(previous, node_id, source_only))
            if observation != 'matched':
                c.fail('READBACK_PENDING' if observation == 'unknown' else 'CONCURRENT_EDIT',
                       '批次中的目标或引用资产需重新核验。', node_id=node_id)
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


def semantic_steps(baseline, compiled):
    """Use public commands; SDK patches are only an offline change detector."""
    before, after = baseline["document"], compiled["document"]
    created = set(c.nodes(after)) - set(c.nodes(before))
    changed = {p["assetId"] for p in compiled["patches"]} - {before["rootCanvasId"]}
    if created and changed <= created:
        # The official factories and domain commands have already constructed
        # these new nodes. create_node accepts that official node/asset shape,
        # preserving business display names as well as the asset content.
        mutations = [{"kind": "create_node", "node": copy.deepcopy(c.nodes(after)[nid]),
                      "assets": [copy.deepcopy(after["assets"][nid])], "autoCreateNodeAsset": False}
                     for nid in c.nodes(after) if nid in created]
        old_edges = before["assets"][before["rootCanvasId"]]["content"].get("edges", {})
        new_edges = after["assets"][after["rootCanvasId"]]["content"].get("edges", {})
        d.require(all(new_edges.get(key) == edge for key, edge in old_edges.items()),
                  "新建批次不得修改已有引用边")
        mutations.extend({"kind": "create_edge", "edge": copy.deepcopy(edge)}
                         for key, edge in new_edges.items() if key not in old_edges)
        return [command("apply_mutations", {"atomic": True, "intent": "Seedance 创建已编译的新节点及引用", "mutations": mutations})]
    steps, ordinary = [], []
    for cmd in compiled["commands"]:
        if cmd["name"] in ORDINARY_COMMANDS:
            ordinary.append({"kind": cmd["name"], **copy.deepcopy(cmd["input"])})
            continue
        if ordinary:
            steps.append(command("apply_mutations", {"atomic": True, "intent": "Seedance 节点字段更新", "mutations": ordinary}))
            ordinary = []
        steps.append(copy.deepcopy(cmd))
    if ordinary:
        steps.append(command("apply_mutations", {"atomic": True, "intent": "Seedance 节点字段更新", "mutations": ordinary}))
    return steps


def step_commands(step):
    if step["name"] == "apply_mutations":
        return [command(value["kind"], {key: val for key, val in value.items() if key != "kind"})
                for value in step["input"]["mutations"]]
    return [step]


def intermediate_targets(before, after):
    targets = []
    for node_id in c.nodes(after):
        if c.fingerprint(before, node_id) == c.fingerprint(after, node_id):
            continue
        old, new = c.content(before, node_id), c.content(after, node_id)
        targets.append({"action": "node_kind", "node_id": node_id, "kind": c.kind_of(c.nodes(after)[node_id])})
        targets.extend(exact_targets(after, node_id))
        fields = {key: copy.deepcopy(value) for key, value in new.items() if key != "generation" and old.get(key) != value}
        if fields:
            targets.append({"action": "patch", "node_id": node_id, "patch": fields})
    return targets


def submit(project, cli, state, baseline, compiled, targets, receipts=(), asset_reference_policy=None):
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
        if target['action'] == 'asset_snapshot':
            target = {'action': 'semantic_asset_snapshot', 'node_id': sid,
                      'value': c.source_fingerprint(compiled['document'], sid),
                      'projection': 'source-business-v1',
                      'expected': c.semantic_projection(compiled['document'], sid, source_only=True)}
        if target['action'] == 'bound_asset_identity':
            target['consumers'] = sorted(nid for nid in touched if sid in c.node_asset_ids(c.prompt_of(compiled['document'], nid))
                or any(e['source'] == sid for e in reference_edges(compiled['document'], nid)))
        targets.append(target)
    if not patches:
        return None
    c.check_control(cli)
    doc = baseline["document"]
    steps = semantic_steps(baseline, compiled)
    current = doc
    entry = None
    source_only_ids = {target['node_id'] for target in targets
                       if target['action'] in {'semantic_asset_snapshot', 'bound_asset_identity'}} - touched
    for index, step in enumerate(steps):
        final = index == len(steps) - 1
        # The public runtime checks these saved expectations immediately before
        # dispatch, in the same session that performs the operation.
        expected = compiled["document"] if final else cli.compile_batch(current, step_commands(step))["document"]
        checks = targets if final else intermediate_targets(current, expected)
        if not final:
            checks.extend(copy.deepcopy(target) for target in targets
                          if target['action'] in {'semantic_asset_snapshot', 'bound_asset_identity'})
            checked_ids = {target['node_id'] for target in checks}
            checks.extend({'action': 'node_kind', 'node_id': node_id, 'kind': c.kind_of(c.nodes(expected)[node_id])}
                          for node_id in {target['node_id'] for target in targets} - checked_ids
                          if node_id in c.nodes(expected))
        expected_nodes = [{'node_id': node_id, 'node': copy.deepcopy(c.nodes(current).get(node_id)),
                           'asset': copy.deepcopy(current['assets'].get(node_id)), 'source_only': node_id in source_only_ids}
                          for node_id in sorted({target['node_id'] for target in targets})]
        current_edges = current['assets'][current['rootCanvasId']]['content'].get('edges', {})
        consumers = {target['node_id'] for target in targets
                     if target['action'] not in {'semantic_asset_snapshot', 'bound_asset_identity'}}
        guarded_edges = {edge_id for edge_id, edge in current_edges.items() if edge.get('target') in consumers}
        if step['name'] == 'apply_mutations':
            guarded_edges.update(mutation['edge']['id'] for mutation in step['input']['mutations']
                                 if mutation['kind'] == 'create_edge')
        expected_edges = [{'edge_id': edge_id, 'value': copy.deepcopy(current_edges.get(edge_id))}
                          for edge_id in sorted(guarded_edges)]
        entry = submit_step(project, cli, state, step, checks, receipts, compiled["runtime_sha256"], expected_nodes, expected_edges)
        if not final:
            current = readback(project, cli, state, entry, checks)
    return entry


def submit_step(project, cli, state, step, targets, receipts, runtime_sha256, expected_nodes, expected_edges):
    operation = uuid.uuid4().hex
    request = {**step, 'expected_nodes': expected_nodes, 'expected_edges': expected_edges}
    d.require(len(d.packed(request).encode("utf-8")) <= 64 * 1024 * 1024, "批量事务超过CLI输入限制，尚未发送")
    path = Path(project) / "canvas" / "batches" / (operation + ".json")
    d.write_json(path, {"protocol": c.BATCH_PROTOCOL, "request": request, "runtime_sha256": runtime_sha256})
    entry = {"id": operation, "command": "canvas_batch", "protocol": c.BATCH_PROTOCOL,
             "input": {"request_path": str(path), "request_sha256": c.digest(request)},
             "targets": copy.deepcopy(targets), "status": "pending"}
    previous_mappings = c.read_state(project)['mappings']
    for receipt in receipts:
        receipt["steps"].append(operation)
        receipt["batch_operation_id"] = operation
        d.write_json(receipt["path"], receipt)
    state["journal"].append(entry)
    c.save(project, state)
    try:
        if hasattr(cli, 'command_guarded'):
            result = cli.command_guarded(state["binding"]["canvas_id"], step["name"], step["input"], expected_nodes, expected_edges)
        else:
            result = cli.command(state["binding"]["canvas_id"], step["name"], step["input"])
    except BaseException as exc:
        # Queue control exceptions are raised before transport. Unlike timeouts,
        # these are known not-sent requests and must not poison future resume.
        from canvas_jobs import PauseRequested, Superseded
        if isinstance(exc, (PauseRequested, Superseded)):
            entry.update(status="cancelled", resolution="control_before_send")
            for mapping in state["mappings"].values():
                if mapping.get("node_id") in {t["node_id"] for t in targets}:
                    mapping["stage"] = "allocated" if step["name"] == "apply_mutations" and any(
                        m.get("node", {}).get("id") == mapping["node_id"] for m in step["input"]["mutations"]) else "writing"
            c.save(project, state)
            raise
        if not isinstance(exc, (ValueError, OSError, KeyError, TypeError)):
            # A killed process/unknown failure leaves the durable pending entry.
            raise
        if getattr(exc, 'details', {}).get('not_sent') is True:
            entry.update(status='rejected', error=c.safe_message(exc), not_sent=True)
            for key, mapping in state['mappings'].items():
                if mapping.get('node_id') in {target['node_id'] for target in targets} and key in previous_mappings:
                    state['mappings'][key] = previous_mappings[key]
            for receipt in receipts:
                receipt.update(status='rejected', error=c.safe_message(exc))
                d.write_json(receipt['path'], receipt)
            c.save(project, state)
            raise
        entry.update(status="uncertain", error=c.safe_message(exc))
        c.save(project, state)
        # The public command may have saved before its reply was lost. Resolve
        # the original IDs by reads only; never issue the command twice here.
        try:
            readback(project, cli, state, entry, targets)
            return entry
        except (ValueError, OSError, KeyError, TypeError):
            c.fail("UNCERTAIN_WRITE", "批量命令结果仍待核对；保留原ID与请求。" + c.safe_message(exc), operation=operation)
    entry["status"] = "acknowledged"
    entry["ack"] = {"command": step["name"], "result_sha256": c.digest(result)}
    acknowledgement = getattr(cli, 'last_command_result', {})
    if acknowledgement.get('ok') is True:
        entry['ack']['ok'] = True
    if type(acknowledgement.get('revision')) is int:
        # This is the SDK store revision, not a server asset-version fence.
        entry['ack']['sdk_revision'] = acknowledgement['revision']
    c.save(project, state)
    return entry


def readback(project, cli, state, entry, targets):
    observations = []
    node_ids = sorted({t["node_id"] for t in targets})
    for attempt in range(c.VIDEO_READBACK_ATTEMPTS):
        boundary(cli)
        try:
            doc = cli.batch_snapshot(state["binding"], node_ids=node_ids)["document"]
        except c.CanvasError as exc:
            if exc.code not in {"CLI_TIMEOUT", "CLI_ERROR", "READBACK_INCOMPLETE", "READBACK_PENDING", "NETWORK_ERROR"}:
                raise
            if entry:
                entry["readback_error"] = c.safe_message(exc)
                c.save(project, state)
            if exc.code == 'READBACK_PENDING':
                # The native read layer has already consumed its retry budget.
                raise
            if attempt + 1 == c.VIDEO_READBACK_ATTEMPTS:
                c.fail("READBACK_PENDING", "写入后的只读查询暂不可用；已保存原ID与提交状态。")
            time.sleep(c.VIDEO_READBACK_DELAY * (2 ** attempt))
            continue
        observations = [c.target_state(doc, t) for t in targets]
        if all(value == "matched" for value in observations):
            if entry:
                entry.update(status="verified", after={t["node_id"]: c.fingerprint(doc, t["node_id"]) for t in targets})
                c.save(project, state)
            return doc
        if attempt + 1 < c.VIDEO_READBACK_ATTEMPTS:
            c.check_control(cli)
            time.sleep(c.VIDEO_READBACK_DELAY * (2 ** attempt))
    if entry:
        entry.update(readback_status="pending" if "unknown" in observations else "conflict",
                     error="批次字段或引用仍待核验" if "unknown" in observations else "批次字段或引用回读差异")
        c.save(project, state)
    c.fail("READBACK_PENDING" if "unknown" in observations else "READBACK_FAILED", "批次最终字段或引用未全部通过核验；保留原事务和节点，停止续写。",
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
    entry = submit(project, cli, state, baseline, compiled, targets)
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
        entry = submit(project, cli, state, baseline, compiled, targets)
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


def video_matches(doc, item, references_only=False):
    node_id = item.get("node_id")
    if not node_id or node_id not in doc["assets"] or node_id not in c.nodes(doc):
        return False
    checks = [{"action": "node_kind", "node_id": node_id, "kind": "video"},
              {"action": "prompt", "node_id": node_id, "value": item["prompt"]}]
    if not references_only:
        checks.extend([{"action": "patch", "node_id": node_id,
                        "patch": {field: item["name"] for field in ("name", "title", "caption")}},
                       {"action": "generation_settings", "node_id": node_id, "value": item.get("settings", {})}])
    return all(c.target_satisfied(doc, target) for target in checks) and c.video_references_satisfied(doc, node_id, item)


def sync_videos(project, cli, state, doc, plan, pending, context, references_only=False, batch_size=VIDEO_BATCH_SIZE, asset_reference_policy=None):
    policy_args = {'asset_reference_policy': asset_reference_policy} if asset_reference_policy is not None else {}
    if asset_reference_policy is not None:
        context['asset_reference_policy'] = asset_reference_policy
    positions = c.layout_positions(doc, plan)
    created = updated = reused = 0
    verified = set()
    remaining = []
    for item in plan:
        if video_matches(doc, item, references_only):
            if c.accept_synced_item(project, state, doc, item, pending, context, references_only):
                verified.add(item["key"])
                reused += 1
                continue
        remaining.append(item)
    plan = remaining
    for offset in range(0, len(plan), batch_size):
        group = plan[offset:offset + batch_size]
        sources = {sid for i in group for sid in c.node_asset_ids(i["prompt"])}
        sources.update(i["black_node_id"] for i in group if c.edge_only_black(i) and i.get("black_node_id"))
        baseline = snapshot(cli, state, doc, group, sources, **policy_args)
        doc = baseline["document"]
        ids = reserve(project, cli, state, group)
        commands, receipts, targets = [], [], []
        for item, node_id in zip(group, ids):
            context["active"] = {"key": item["key"], "node_id": node_id, "kind": "video", "name": item["name"]}
            is_new = not item.get("node_id")
            before = c.content(doc, node_id)
            names = {f: item["name"] for f in ("name", "caption", "title")}
            if is_new:
                commands.append(command("create_biz_node", {"nodeKind": "video", "id": node_id,
                    "initialData": {**names, "generation": copy.deepcopy(item.get("settings", {}))},
                    "topLeft": positions[item["key"]]}))
                created += 1
            else:
                reused += 1
                if not references_only:
                    generation = {**copy.deepcopy(before.get("generation") or {}), **item["settings"]}
                    commands.append(command("update_node_data", {"id": node_id, "patch": {**names, "generation": generation}}))
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
        compiled = cli.compile_batch(doc, commands)
        for item, receipt in zip(group, receipts):
            node_id = receipt["node_id"]
            receipt["reference_targets"] = exact_targets(compiled["document"], node_id)
            targets.extend(receipt["reference_targets"])
            d.write_json(receipt["path"], receipt)
        entry = submit(project, cli, state, baseline, compiled, targets, receipts, **policy_args)
        try:
            actual = readback(project, cli, state, entry, targets)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            for receipt in receipts:
                receipt.update(status="pending_verification" if entry and entry["status"] in ("acknowledged", "verified") else "uncertain", error=c.safe_message(exc))
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
