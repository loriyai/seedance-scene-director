"""Persist node-reference checks separately from missing image/video media."""
from pathlib import Path
import uuid

import director as d


KIND_NAMES = {"role": "角色节点", "scene": "场景节点", "image": "道具图片节点", "video": "视频节点"}
REASONS = {
    "missing_image": "缺少可用主图",
    "missing_video": "黑幕视频素材待补",
    "missing_node": "没有对应资产节点",
    "missing_black_node": "没有对应黑幕节点",
    "node_type_changed": "节点类型不一致",
    "video_missing": "视频节点尚未完成创建或同步",
    "reference_not_encoded": "提示词中未建立该资产的节点引用",
    "black_prompt_present": "独立引用的黑幕仍出现在提示词中",
    "reference_edge_missing": "节点引用连线未建立",
    "role_media_reference_missing": "角色图片引用记录尚未刷新",
    "reference_failed": "节点引用回读未通过",
    "existing_name": "同名节点无法唯一确定",
    "cloud_edited": "节点已被人工修改",
    "name_changed": "节点名称已被修改",
    "ambiguous_name": "同名同类型节点有多个候选",
    "wrong_type": "同名节点的类型不一致",
    "node_missing": "没有有效的资产节点映射",
    "node_name_changed": "已绑定资产节点的完整名称已改变",
    "ambiguous_node": "同名同类型资产有多个候选，需要指定节点ID",
    "same_name_wrong_kind": "同名资产节点的类型不一致",
    "asset_skipped": "本次已选择跳过该资产",
    "asset_conflict": "资产映射存在待处理冲突",
    "invalid_reuse_decision": "指定复用的节点与资产名称或类型不匹配",
    "reuse_fingerprint_changed": "所选候选节点在确认后发生变化，需要重新核对",
    "asset_create_rejected": "CLI拒绝创建该资产节点",
    "prompt_encoding_failed": "提示词节点引用编码失败",
    "prompt_write_rejected": "CLI拒绝写入提示词，已保留原内容",
    "mapped_node_missing": "已绑定的视频节点不存在",
    "video_settings_changed": "视频时长与保存记录不一致",
    "operation_failed": "CLI操作未完成",
    "UNCERTAIN_WRITE": "CLI写入结果尚未确认，已保存进度并停止",
    "CONCURRENT_EDIT": "节点在操作期间变化，已保存进度并停止",
    "CLI_TIMEOUT": "CLI超时，需要核对写入结果",
    "CLI_ERROR": "CLI未返回有效结果",
    "COMMAND_REJECTED": "CLI拒绝执行该操作",
}


def api():
    import canvas
    return canvas


def collect_issues(session, state, doc, units, failures=()):
    c = api()
    nodes = c.nodes(doc)
    wanted = c.desired_assets(session)
    by_key = {item["key"]: item for item in wanted}
    use = {item["key"]: [] for item in wanted}
    for unit in units:
        for key in c.unit_asset_ids(session, unit):
            use.setdefault(key, []).append(unit["id"])
    issues = []

    def add(key, reason, node_id=None, unit_ids=None, **extra):
        item = by_key.get(key, {})
        issues.append({"key": key, "name": item.get("name", "黑幕" if key == "BLACK" else key),
                       "kind": extra.pop("kind", item.get("kind", "video")),
                       "node_id": node_id, "reason": reason,
                       "unit_ids": list(unit_ids if unit_ids is not None else use.get(key, [])), **extra})

    for item in wanted:
        mapping = state.get("mappings", {}).get(item["key"], {})
        nid = mapping.get("node_id")
        if nid not in nodes:
            add(item["key"], "missing_node", nid)
        elif c.kind_of(nodes[nid]) != item["kind"]:
            add(item["key"], "node_type_changed", nid)
        elif not c.ready_image(doc, nid):
            add(item["key"], "missing_image", nid)
    black_by_unit = {}
    for unit in units:
        if c.no_black(unit):
            continue
        black, kind, issue = c.black_reference(state, doc, unit["id"])
        black_by_unit[unit["id"]] = black
        if issue is not None:
            reason = "missing_black_node" if issue["reason"] == "node_missing" else issue["reason"]
            add("BLACK", reason, black, [unit["id"]], kind=kind)
        elif not c.reference_ready(doc, black):
            add("BLACK", "missing_video" if kind == "video" else "missing_image", black, [unit["id"]], kind=kind)

    edges = doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {})
    for unit in units:
        target = state.get("mappings", {}).get(unit["id"], {}).get("node_id")
        if target not in nodes or c.kind_of(nodes[target]) != "video":
            add(unit["id"], "video_missing", target, [unit["id"]])
            continue
        prompt = c.prompt_of(doc, target)
        tagged = set(c.node_asset_ids(prompt))
        draft = c.content(doc, target).get("generation", {}).get("references", [])
        keys = sorted(c.unit_asset_ids(session, unit)) + ([] if c.no_black(unit) else ["BLACK"])
        specific_failures = 0
        for key in keys:
            source = black_by_unit[unit["id"]] if key == "BLACK" else state.get("mappings", {}).get(key, {}).get("node_id")
            if source not in nodes:
                continue  # The missing node was recorded above.
            hidden_black = key == "BLACK" and c.edge_only_black(unit)
            if hidden_black and not c.black_prompt_absent(prompt, source):
                add(key, "black_prompt_present", source, [unit["id"]], video_node_id=target,
                    kind=c.kind_of(nodes[source]))
                specific_failures += 1
            if source not in tagged and not hidden_black:
                add(key, "reference_not_encoded", source, [unit["id"]], video_node_id=target,
                    **({"kind": c.kind_of(nodes[source])} if key == "BLACK" else {}))
                specific_failures += 1
                continue
            linked = any(edge.get("source") == source and edge.get("target") == target
                         and edge.get("type") == "reference" for edge in edges.values())
            if not linked:
                add(key, "reference_edge_missing", source, [unit["id"]], video_node_id=target,
                    **({"kind": c.kind_of(nodes[source])} if key == "BLACK" else {}))
                specific_failures += 1
            elif c.kind_of(nodes[source]) == "role" and c.role_reference_media(doc, source, target):
                row_exists = isinstance(draft, list) and any(isinstance(row, dict) and
                    source in [row.get(field, "").strip() for field in ("sourceNodeId", "nodeAssetId", "id")
                               if isinstance(row.get(field), str)] and row.get("type") in ("image", "video", "audio")
                    for row in draft)
                if not row_exists:
                    add(key, "role_media_reference_missing", source, [unit["id"]], video_node_id=target)
                    specific_failures += 1
        if not specific_failures and not c.prompt_references_satisfied(doc, target, prompt):
            add(unit["id"], "reference_failed", target, [unit["id"]])

    for failure in failures:
        value = dict(failure)
        key = value.pop("asset_key", None) or value.get("key") or value.get("unit_id") or "未定位"
        unit_ids = value.get("unit_ids") or ([value["key"]] if value.get("key") in {u["id"] for u in units} else use.get(key, []))
        name = value.get("name") or by_key.get(key, {}).get("name") or key
        kind = value.get("kind") or by_key.get(key, {}).get("kind", "video")
        issues.append({"key": key, "name": name, "kind": kind, "node_id": value.get("node_id"),
                       "reason": value.get("reason", "reference_failed"), "unit_ids": list(unit_ids),
                       "message": c.safe_message(value.get("message", ""))})
    unique = {}
    for issue in issues:
        signature = (issue["key"], issue["node_id"], issue["reason"], issue.get("video_node_id"))
        if signature in unique:
            unique[signature]["unit_ids"] = list(dict.fromkeys(unique[signature]["unit_ids"] + issue["unit_ids"]))
        else:
            unique[signature] = issue
    return list(unique.values())


def write_reference_report(project, session, state, doc, units, failures=(), output=None):
    """Write a user TXT and a resumable JSON record without changing prompt files."""
    c = api()
    project = Path(project).resolve()
    issues = collect_issues(session, state, doc, units, failures)
    missing_images = [issue for issue in issues if issue["reason"] == "missing_image"]
    missing_videos = [issue for issue in issues if issue["reason"] == "missing_video"]
    other = [issue for issue in issues if issue["reason"] not in ("missing_image", "missing_video")]
    unit_names = {unit["id"]: unit.get("display_name", unit["id"]) for unit in session.get("units", units)}
    display_units = lambda ids: "，".join(unit_names.get(uid, uid) for uid in ids)
    if output is None:
        receipt_output = next((r.get("output") for r in reversed(session.get("receipts", [])) if r.get("output")), None)
        directory = Path(receipt_output).resolve().parent if receipt_output else project / "reports"
        output = directory / (project.name + "_资产与引用检查_" + uuid.uuid4().hex[:8] + ".txt")
    output = Path(output).resolve()
    protected = [Path(r["output"]).resolve() for r in session.get("receipts", []) if r.get("output")]
    protected += [Path(v["path"]).resolve() for v in session.get("files", {}).values() if isinstance(v, dict) and v.get("path")]
    d.require(output.suffix.lower() == ".txt" and output not in protected,
              "检查报告须为独立TXT，不能覆盖剧本、台账或分镜提示词")
    d.require(d.ROOT != output and d.ROOT not in output.parents and output.parent != (project / "canvas").resolve(),
              "用户检查报告不能写入Skill或画布后台状态目录")
    lines = ["小云雀资产与引用检查记录", "画布ID：" + str(state.get("binding", {}).get("canvas_id", "未绑定")),
             "本次分镜：" + (display_units(u["id"] for u in units) or "无"), "",
             "缺少图片的资产节点：" + str(len(missing_images)) + "项"]
    for issue in missing_images:
        kind = "黑幕图片节点" if issue["key"] == "BLACK" else KIND_NAMES.get(issue["kind"], issue["kind"])
        image = "黑幕图片" if issue["key"] == "BLACK" else "按完整资产名称命名的" + ("主图" if issue["kind"] in ("role", "scene") else "图片")
        lines.append("- 资产：" + issue["name"] + "；类型：" + kind + "；节点ID：" + str(issue["node_id"] or "未建立")
                     + "；需要补充：" + image + "；涉及分镜：" + (display_units(issue["unit_ids"]) or "本次未使用") + "。")
    if not missing_images:
        lines.append("无。")
    lines += ["", "黑幕视频素材待补：" + str(len(missing_videos)) + "项"]
    for issue in missing_videos:
        lines.append("- 资产：黑幕；类型：黑幕视频节点；节点ID：" + str(issue["node_id"] or "未建立")
                     + "；需要补充：黑幕视频素材；涉及分镜：" + (display_units(issue["unit_ids"]) or "无") + "。")
    if not missing_videos:
        lines.append("无。")
    lines += ["", "节点或引用待处理：" + str(len(other)) + "项"]
    for issue in other:
        details = REASONS.get(issue["reason"], issue["reason"])
        if issue.get("message"):
            details += "：" + c.safe_message(issue["message"])
        name = unit_names.get(issue["key"], issue["name"])
        lines.append("- 资产或分镜：" + name + "；节点ID：" + str(issue["node_id"] or "未建立")
                     + "；涉及分镜：" + (display_units(issue["unit_ids"]) or "无") + "；原因：" + details + "。")
    if not other:
        lines.append("无。")
    lines += ["", "可手动补充上述图片，或明确要求按完整资产名称匹配本地图片上传；补图后刷新引用并重新检查。",
              "黑幕视频素材需另行补充到已记录的视频节点；节点引用完成不表示视频素材已就绪。",
              "本记录未触发图片或视频生成。"]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(output)
    record = {"schema": 1, "canvas_id": state.get("binding", {}).get("canvas_id"),
              "unit_ids": [u["id"] for u in units], "report": str(output), "issues": issues,
              "missing_image_count": len(missing_images), "missing_video_count": len(missing_videos), "reference_issue_count": len(other)}
    d.write_json(project / "canvas" / "reference-check.json", record)
    return {"report": str(output), "issues": issues,
            "missing_image_count": len(missing_images), "missing_video_count": len(missing_videos), "reference_issue_count": len(other)}
