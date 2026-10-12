"""Video settings and ledger-matched local images for the official canvas CLI."""
import copy
import hashlib
from pathlib import Path
import re

import director as d


def api():
    import canvas
    return canvas


def model_settings(cli):
    c = api()
    catalog = cli.native("model", "list", "--type", "video")
    normalize = lambda x: re.sub(r"[\s_-]+", "", str(x)).lower()
    candidates = [m for m in catalog.get("models", []) if m.get("kind", "video") == "video"
                  and any(normalize(m.get(k, "")) == "seedance2.5" for k in ("key", "name"))]
    if len(candidates) != 1 or not candidates[0].get("key"):
        c.fail("MODEL_UNAVAILABLE", "当前账号无法唯一确定Seedance 2.5；请核对模型目录，不自动换模型。")
    key = candidates[0]["key"]
    response = cli.native("model", "describe", key, "--type", "video")
    model = response.get("model", response)
    if model.get("key") != key:
        c.fail("MODEL_CONFIG_UNKNOWN", "模型详情与目录不一致，请刷新CLI模型配置。")
    resolutions = [x for x in model.get("resolution", {}).get("options", []) if str(x).lower() == "720p"]
    if len(resolutions) != 1:
        c.fail("RESOLUTION_UNAVAILABLE", "当前Seedance 2.5未提供720P，不自动改用其他分辨率。")
    return {"modelKey": key, "resolution": resolutions[0]}, model


def validate_duration(model, seconds, current=None):
    c = api()
    spec = model.get("duration") or {}
    if spec.get("unit", "seconds") != "seconds" or type(seconds) is not int:
        c.fail("DURATION_CONFIG_UNKNOWN", "不能确定模型时长参数的单位。")
    if "options" in spec:
        valid = seconds in spec["options"]
    elif all(type(spec.get(k)) in (int, float) for k in ("min", "max", "step")) and spec["step"] > 0:
        valid = spec["min"] <= seconds <= spec["max"] and (seconds - spec["min"]) % spec["step"] == 0
    else:
        c.fail("DURATION_CONFIG_UNKNOWN", "当前模型未返回可验证的秒数档位；请核对入口，不猜测时长。")
    if not valid:
        c.fail("DURATION_UNSUPPORTED", "分镜时长不在当前Seedance 2.5可用档位内，不自动取整或裁剪。", duration=seconds, allowed=spec)
    values = {**(current or {}), "duration": seconds, "resolution": "720p"}
    for dimension in (spec, model.get("resolution") or {}):
        conditions = dimension.get("active_when_any")
        if conditions and not any(isinstance(row, dict) and all(
                str(values.get(k)).lower() in [str(v).lower() for v in (options if isinstance(options, list) else [options])]
                for k, options in row.items()) for row in conditions):
            c.fail("PARAMETER_CONDITION", "模型参数生效条件需要核对，未写入节点。", conditions=conditions)
    config = model.get("parameter_config") or {}
    if config.get("need_available_combinations"):
        combinations = config.get("available_combinations") or []
        keys = config.get("combination_dimension_keys") or []
        if not keys or any(k not in values for k in keys):
            c.fail("PARAMETER_COMBINATION_UNKNOWN", "模型组合包含未确定的参数；请核对入口，不猜测默认组合。")
        if not any(isinstance(row.get("option_values"), dict) and all(
                str(row["option_values"].get(k)).lower() == str(values[k]).lower() for k in keys) for row in combinations):
            c.fail("PARAMETER_COMBINATION_UNSUPPORTED", "当前模型不支持所选分辨率与时长组合。")


def file_hash(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(512 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def scan_images(session, folder):
    c = api()
    folder = Path(folder).expanduser().resolve()
    d.require(folder.is_dir(), "图片文件夹不存在")
    assets = c.desired_assets(session)
    names = {x["name"] for x in assets}
    found, unmatched, invalid = {}, [], []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        if path.stem not in names:
            unmatched.append(str(path))
            continue
        found.setdefault(path.stem, []).append(path)
    matched, missing, duplicates = [], [], []
    for item in assets:
        files = found.get(item["name"], [])
        if not files:
            missing.append({"key": item["key"], "name": item["name"]})
        elif len(files) > 1:
            duplicates.append({"key": item["key"], "name": item["name"], "files": [str(p) for p in files]})
        else:
            path = files[0]
            try:
                with path.open("rb") as stream:
                    signature = stream.read(12)
                suffix = path.suffix.lower()
                valid = (suffix == ".png" and signature.startswith(b"\x89PNG\r\n\x1a\n") or
                         suffix in (".jpg", ".jpeg") and signature.startswith(b"\xff\xd8\xff") or
                         suffix == ".webp" and signature.startswith(b"RIFF") and signature[8:12] == b"WEBP")
                if not valid:
                    raise ValueError("图片扩展名与文件头不符")
                matched.append({**item, "path": str(path), "file_hash": file_hash(path)})
            except (OSError, ValueError) as exc:
                invalid.append({"key": item["key"], "path": str(path), "reason": str(exc)})
    return {"folder": str(folder), "matched": matched, "missing": missing, "duplicates": duplicates,
            "unmatched": unmatched, "invalid": invalid}


def source_infos(data):
    infos = data.get("sourceInfos") or {}
    if isinstance(infos, dict):
        return infos
    # Compatibility for older imported documents with a source list.
    return {x["pippitAssetId"]: x for x in infos if isinstance(x, dict) and x.get("pippitAssetId")}


def selected_image(doc, node_id):
    c = api()
    data = c.content(doc, node_id)
    if c.kind_of(c.nodes(doc)[node_id]) == "image":
        return data.get("pippitAssetId") or data.get("assetId")
    info = data.get("appearanceInfo") or {}
    if info.get("pippitAssetId"):
        return info["pippitAssetId"]
    selected = [k for k, v in (info.get("candidatePippitAssetIds") or {}).items() if v is True]
    if len(selected) == 1:
        return selected[0]
    mains = [x.get("pippitAssetId", key) for key, x in source_infos(data).items() if x.get("type", "image") == "image" and x.get("isMain")]
    return mains[0] if len(mains) == 1 else None


def has_image(doc, node_id):
    c = api()
    data = c.content(doc, node_id)
    info = data.get("appearanceInfo") or {}
    return bool(c.ready_image(doc, node_id) or info.get("assetId") or info.get("candidatePippitAssetIds")
                or data.get("referenceAppearances") or any(x.get("type", "image") == "image" for x in source_infos(data).values()))


def observed_string(value, keys):
    if isinstance(value, dict):
        for k in keys:
            if isinstance(value.get(k), str) and value[k].strip():
                return value[k].strip()
        for child in value.values():
            found = observed_string(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = observed_string(child, keys)
            if found:
                return found


def uploaded_asset(project, cli, state, item):
    c = api()
    uploads = state.setdefault("uploads", {})
    sha = item["file_hash"]
    entry = uploads.get(sha)
    if entry and entry.get("status") in ("pending", "uncertain") and not entry.get("pippit_asset_id"):
        c.fail("UNCERTAIN_UPLOAD", "上次图片上传结果不明；先核对个人素材库，不重复上传。", file_hash=sha, path=entry["path"])
    if not entry:
        entry = uploads[sha] = {"status": "pending", "path": item["path"]}
        c.save(project, state)
        try:
            result = cli.native("canvas", "upload", "--path", item["path"], "--timeout", "45s", "--poll-interval", "1s", timeout=60)
            pid = result.get("pippit_asset_id") or result.get("locator", {}).get("pippit_asset_id")
            if not isinstance(pid, str) or not pid:
                c.fail("UNCERTAIN_UPLOAD", "上传没有返回真实素材ID，请核对个人素材库。")
            source_id = result.get("locator", {}).get("source_id") or result.get("asset_id")
            entry.update(pippit_asset_id=pid, status="ready" if result.get("state") == "ready" else "processing")
            if isinstance(source_id, str) and source_id:
                entry["asset_id"] = source_id
            c.save(project, state)
        except (ValueError, OSError) as exc:
            entry["status"] = "uncertain"
            c.save(project, state)
            c.fail("UNCERTAIN_UPLOAD", "图片上传返回不明，已保存进度；先核对素材库，不盲目重试。", file_hash=sha, message_detail=c.safe_message(exc))
    # Query every reuse, including ready uploads, to verify availability and resume processing by ID.
    result = cli.native("canvas", "get", "--asset-id", entry["pippit_asset_id"])
    assets = result.get("assets") or []
    if len(assets) != 1:
        entry["status"] = "processing"
        c.save(project, state)
        return None
    source_id = observed_string(assets[0], ("SourceID", "source_id", "sourceId"))
    if source_id:
        entry["asset_id"] = source_id
    entry["status"] = "ready"
    c.save(project, state)
    return entry


def bind_image(project, cli, state, doc, item, uploaded):
    c = api()
    node_id, pid = item["node_id"], uploaded["pippit_asset_id"]
    main = {"pippitAssetId": pid, "assetId": uploaded.get("asset_id", ""), "imageSource": 2, "uploadPath": "optimized"}
    if item["kind"] == "role":
        previous = c.content(doc, node_id).get("appearanceInfo") or {}
        return c.write_verified(project, cli, state, "xyq.role.updateAppearance",
                                {"nodeId": node_id, "assetId": node_id, "expected": {"mainImage": {
                                    k: previous[k] for k in ("assetId", "pippitAssetId") if k in previous}},
                                 "patch": {"mainImage": main}},
                                [{"action": "main_image", "node_id": node_id, "value": pid, "file_hash": item["file_hash"]}])
    if item["kind"] == "scene":
        data = c.content(doc, node_id)
        info = copy.deepcopy(data.get("appearanceInfo") or {})
        # An absent source ID must clear an old main image's source ID.
        info.pop("assetId", None)
        info.update(main)
        info["candidatePippitAssetIds"] = {**{k: False for k in info.get("candidatePippitAssetIds", {})}, pid: True}
        sources = {key: dict(x, isMain=False) if x.get("type", "image") == "image" else copy.deepcopy(x)
                   for key, x in source_infos(data).items()}
        sources[pid] = {**sources.get(pid, {}), "pippitAssetId": pid, "assetId": main["assetId"], "uploadPath": "optimized",
                        "name": "", "type": "image", "isMain": True,
                        "order": sources.get(pid, {}).get("order", len(sources))}
        patch = {"appearanceInfo": info, "sourceInfos": sources}
    else:
        patch = {"source": "uploaded", "pippitAssetId": pid, "uploadPath": "optimized", "assetId": uploaded.get("asset_id", "")}
    return c.write_verified(project, cli, state, "update_node_data", {"id": node_id, "patch": patch},
                            [{"action": "patch", "node_id": node_id, "patch": patch},
                             {"action": "main_image", "node_id": node_id, "value": pid, "file_hash": item["file_hash"]}])


def refresh_references(project, cli, session, changed):
    c = api()
    state = c.read_state(project)
    c.queue_image_references(session, state, changed)
    queued = set(state.get("pending_image_references", []))
    if not queued:
        return {"status": "unchanged"}
    c.save(project, state)
    units = []
    for unit in session["units"]:
        if unit["id"] not in state["mappings"]:
            continue
        mentioned = c.unit_asset_ids(session, unit)
        if mentioned & queued:
            units.append(unit["id"])
    if not units:
        c.consume_image_references(session, state, set(), [])
        c.save(project, state)
        return {"status": "unchanged"}
    try:
        result = c.sync(project, cli, "videos", ids=units, bind_references=True, existing_only=True, references_only=True)
    except (ValueError, OSError) as exc:
        return {"status": "pending", "units": units, "message": c.safe_message(exc)}
    return result


def images(project, cli, folder, decisions=None, preview=False):
    c = api()
    session = d.load(Path(project))
    scan = scan_images(session, folder)
    state, doc = c.connected(project, cli)
    c.reconcile(project, cli, state, doc)
    decisions = decisions or {}
    plan, retained, unchanged, conflicts, unbound = [], [], [], [], []
    for item in scan["matched"]:
        key = item["key"]
        mapping = state["mappings"].get(key, {})
        nid = mapping.get("node_id")
        if nid not in c.nodes(doc) or c.kind_of(c.nodes(doc)[nid]) != item["kind"]:
            unbound.append({"key": key, "name": item["name"]})
            continue
        choice = decisions.get(key, {})
        if choice.get("skip"):
            retained.append(key)
            continue
        pid = selected_image(doc, nid)
        previous = mapping.get("image") or {}
        # Resume a verified bind interrupted before the mapping was saved.
        upload = state.get("uploads", {}).get(item["file_hash"], {})
        if previous.get("file_hash") != item["file_hash"] and pid and pid == upload.get("pippit_asset_id") and any(
                entry["status"] == "verified" and any(t.get("action") == "main_image" and t.get("node_id") == nid
                    and t.get("file_hash") == item["file_hash"] and t.get("value") == pid for t in entry["targets"])
                for entry in state["journal"]):
            previous = {"file_hash": item["file_hash"], "pippit_asset_id": pid}
            mapping["image"] = previous
            c.queue_image_references(session, state, [key])
            c.save(project, state)
        if pid and previous.get("file_hash") == item["file_hash"] and previous.get("pippit_asset_id") == pid:
            unchanged.append(key)
            continue
        if has_image(doc, nid):
            token = {"fingerprint": c.fingerprint(doc, nid), "file_hash": item["file_hash"]}
            if choice.get("replace_image") != token:
                retained.append(key)
                conflicts.append({"key": key, "name": item["name"], "reason": "existing_image", "node_id": nid,
                                  "replace_image": token})
                continue
        plan.append({**item, "node_id": nid, "fingerprint": c.fingerprint(doc, nid)})
    report = {k: v for k, v in scan.items() if k != "matched"}
    report.update(retained=retained, unchanged=unchanged, conflicts=conflicts, unbound_assets=unbound)
    if preview:
        return {"status": "planned", "plan": plan, **report}
    if len(plan) >= 10:
        c.checkpoint(project, cli, state, "seedance batch image import")
    imported, processing = [], []
    for item in plan:
        key, nid = item["key"], item["node_id"]
        if file_hash(item["path"]) != item["file_hash"]:
            c.fail("LOCAL_IMAGE_CHANGED", "本地图片在操作期间发生变化，请重新核对。", key=key)
        fresh = c.document(cli, state["binding"])
        if c.fingerprint(fresh, nid) != item["fingerprint"]:
            c.fail("CONCURRENT_EDIT", "图片目标节点发生变化，已停止。", key=key)
        uploaded = uploaded_asset(project, cli, state, item)
        if not uploaded:
            processing.append(key)
            continue
        fresh = c.document(cli, state["binding"])
        if c.fingerprint(fresh, nid) != item["fingerprint"]:
            c.fail("CONCURRENT_EDIT", "上传期间目标节点发生变化；已保留素材ID，未绑定。", key=key)
        if file_hash(item["path"]) != item["file_hash"]:
            c.fail("LOCAL_IMAGE_CHANGED", "上传期间本地图片发生变化；请重新核对后绑定。", key=key)
        bind_image(project, cli, state, fresh, item, uploaded)
        state["mappings"][key]["image"] = {"file_hash": item["file_hash"], "pippit_asset_id": uploaded["pippit_asset_id"]}
        c.queue_image_references(session, state, [key])
        c.save(project, state)
        imported.append(key)
    references = refresh_references(project, cli, session, imported)
    return {"status": "images_imported", "imported": imported, "processing": processing, "references": references, **report}
