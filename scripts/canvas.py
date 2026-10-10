#!/usr/bin/env python3
"""Project-scoped Xiaoyunque canvas operations through the official CLI."""
import argparse
import contextlib
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlparse, parse_qs, urlencode

sys.dont_write_bytecode = True
import director as d
import canvas_media as media

KINDS = {"role": "biz/role", "scene": "biz/scene", "image": "biz/image", "video": "biz/video", "audio": "biz/audio", "text": "biz/text"}


class CanvasError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def fail(code, message, **details):
    raise CanvasError(code, message, **details)


def digest(value):
    return d.sha(d.packed(value))


def safe_message(text):
    # CLI diagnostics can contain authentication headers from a failed request.
    text = re.sub(r'(?i)(bearer\s+)[^\s"\']+', r'\1[隐藏]', str(text))
    return re.sub(r'(?i)((?:access[_-]?key|authorization|token|secret)["\']?\s*[:=]\s*["\']?)[^\s,"\'}]+', r'\1[隐藏]', text)[:1600]


def run_json(argv, cwd, timeout=120):
    try:
        result = subprocess.run([str(x) for x in argv], cwd=str(cwd), shell=False,
                                capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                                env={**os.environ, "PIPPIT_CLI_DISABLE_UPDATE_CHECK": "1"},
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        fail("CLI_TIMEOUT", "CLI超时；先读取画布核对，不直接重放写入。")
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        fail("CLI_ERROR", safe_message(result.stderr or "CLI没有返回有效JSON"))
    if isinstance(value, dict) and value.get("ok") is False:
        fail("COMMAND_REJECTED", safe_message(d.packed(value)))
    if result.returncode:
        fail("CLI_ERROR", safe_message(result.stderr or d.packed(value)))
    return value


class CLI:
    def __init__(self, project):
        self.project = Path(project)
        self.directory = self.project / "canvas"
        self.directory.mkdir(parents=True, exist_ok=True)
        config = self.directory / "connection.json"
        self.config = d.read_json(config) if config.exists() else {}

    def setup(self, node=None):
        node = node or self.config.get("node_path") or shutil.which("node")
        if not node or not Path(node).is_file():
            fail("NODE_REQUIRED", "需要Node.js 16或更高版本，请提供可用Node.js路径。")
        result = run_json([node, Path(__file__).with_name("ensure-cli.js"), "--canvas"], self.project, 300)
        self.config = {"node_path": str(Path(node).resolve()), "cli_path": result["cli_path"],
                       "canvas_entry": result["canvas_entry"], "version": result.get("version")}
        d.write_json(self.directory / "connection.json", self.config)
        return {"status": "configured", "version": self.config["version"]}

    def ready(self):
        if not all(Path(self.config.get(k, "__missing__")).is_file() for k in ("node_path", "cli_path", "canvas_entry")):
            self.setup()

    def native(self, *args, timeout=120):
        self.ready()
        return run_json([self.config["cli_path"], *args], self.project, timeout)

    def auth(self, login=False, force=False):
        status = self.native("status")
        if force or not status.get("logged_in"):
            if not login:
                fail("LOGIN_REQUIRED", "首次使用请完成小云雀CLI浏览器授权。")
            self.native("login", *(["--force"] if force else []), timeout=600)
            status = self.native("status")
        if not status.get("logged_in"):
            fail("LOGIN_REQUIRED", "小云雀CLI尚未完成授权。")
        identity = status.get("uid") or status.get("credential_scope")
        if not identity:
            fail("IDENTITY_REQUIRED", "CLI未返回账号标识；请使用浏览器登录后再绑定画布。")
        status["identity"] = str(identity)
        # Store public account metadata only, never status wholesale.
        self.config.update(account_id=str(identity), source=status.get("source"), expires_at=status.get("expires_at"))
        d.write_json(self.directory / "connection.json", self.config)
        return status

    def command(self, canvas_id, name, value=None):
        self.ready()
        with tempfile.TemporaryDirectory(prefix="command-", dir=self.directory) as temporary:
            path = Path(temporary) / "input.json"
            d.write_json(path, value or {})
            result = run_json([self.config["node_path"], self.config["canvas_entry"], "canvas", "command", "run",
                               name, "--canvas-id", canvas_id, "--file", path], self.project)
        if result.get("ok") is not True:
            fail("CLI_ERROR", "画布命令没有确认成功。")
        return result.get("data", {})

    def allocate(self):
        ids = self.native("canvas", "allocate", "--count", "1").get("asset_ids")
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
            fail("INVALID_ID", "CLI未返回唯一字符串资产ID。")
        return ids[0]


def blank_state():
    return {"schema": 1, "binding": None, "mappings": {}, "journal": [], "pending_units": []}


def state_path(project):
    return Path(project) / "canvas" / "state.json"


def read_state(project):
    path = state_path(project)
    state = d.read_json(path) if path.exists() else blank_state()
    d.require(state.get("schema") == 1, "画布运行状态版本不兼容")
    deleted = {t["node_id"] for e in state["journal"] if e["status"] == "verified"
               for t in e["targets"] if t["action"] == "delete"}
    state["mappings"] = {k: m for k, m in state["mappings"].items() if m["node_id"] not in deleted}
    if state.get("black_node_id") in deleted:
        state.pop("black_node_id")
    for path in (Path(project) / "canvas" / "pending-sync").glob("*.json"):
        marker = d.read_json(path)
        state["pending_units"] = list(dict.fromkeys(state["pending_units"] + list(marker["units"])))
    return state


def save(project, state):
    d.write_json(state_path(project), state)


@contextlib.contextmanager
def project_lock(project):
    directory = Path(project) / "canvas"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "operation.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        fail("BUSY", "本项目有画布操作正在进行；若上次进程已退出，核对日志后再移除操作锁。")
    canvas_lock = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        binding = read_state(project).get("binding")
        if binding:
            canvas_lock = Path(tempfile.gettempdir()) / ("seedance-canvas-" + digest([binding["account_id"], binding["canvas_id"]]) + ".lock")
            try:
                shared_fd = os.open(canvas_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                canvas_lock = None
                fail("BUSY", "该画布已有CLI批量操作正在进行，请稍后续接。")
            with os.fdopen(shared_fd, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
        yield
    finally:
        if canvas_lock:
            canvas_lock.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


def parse_target(target, cli):
    if not target.startswith("https://"):
        d.require(re.fullmatch(r"[A-Za-z0-9_-]+", target), "画布ID格式非法")
        return {"canvas_id": target, "project_id": None, "url": None}
    parsed = urlparse(target)
    d.require(parsed.hostname == "xyq.jianying.com" and parsed.port in (None, 443)
              and not parsed.username and not parsed.password, "请提供小云雀官方画布链接")
    query = parse_qs(parsed.query)
    def one(key):
        values = query.get(key, [])
        d.require(len(values) <= 1, "画布链接含重复参数")
        return values[0] if values else None
    canvas_id, project_id, overview = one("canvasId"), one("projectId"), one("overviewPippitAssetId")
    if not canvas_id and overview:
        raw = cli.native("canvas", "get", "--asset-id", overview)
        candidates = set()
        def walk(value, depth=0):
            if depth > 16:
                return
            if isinstance(value, str) and value[:1] in ("{", "["):
                try:
                    walk(json.loads(value), depth + 1)
                except ValueError:
                    pass
            elif isinstance(value, dict):
                for key, child in value.items():
                    if key in ("canvasAssetId", "canvas_asset_id") and isinstance(child, str) and child:
                        candidates.add(child)
                    else:
                        walk(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    walk(child, depth + 1)
        walk(raw)
        if len(candidates) != 1:
            fail("CANVAS_ID_REQUIRED", "该链接未能唯一解析画布ID，请提供CLI画布ID或含canvasId的链接。")
        canvas_id = candidates.pop()
    d.require(isinstance(canvas_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", canvas_id), "链接缺少有效画布ID")
    # Persist only routing parameters; drop tokens or unrelated URL parameters.
    params = {"projectId": project_id} if project_id else {}
    params.update({"overviewPippitAssetId": overview} if overview else {"canvasId": canvas_id})
    return {"canvas_id": canvas_id, "project_id": project_id, "overview_id": overview,
            "url": "https://xyq.jianying.com/novel/detail/canvas?" + urlencode(params)}


def document(cli, binding):
    doc = cli.command(binding["canvas_id"], "get_snapshot")["document"]
    d.require(doc.get("rootCanvasId") == binding["canvas_id"], "返回的画布与绑定不一致")
    root = doc["assets"][doc["rootCanvasId"]]
    d.require(root.get("type") == "canvas", "目标不是个人画布")
    d.require(not root["content"].get("settings", {}).get("readOnly"), "目标画布为只读")
    return doc


def nodes(doc):
    return doc["assets"][doc["rootCanvasId"]]["content"]["nodes"]


def content(doc, node_id):
    value = doc.get("assets", {}).get(node_id, {}).get("content", {})
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        return value["data"]
    return value if isinstance(value, dict) else {}


def kind_of(node):
    return next((k for k, v in KINDS.items() if v == node.get("type")), node.get("type"))


def name_of(doc, node_id):
    node = nodes(doc)[node_id]
    data = content(doc, node_id)
    if kind_of(node) in ("role", "scene"):
        return node.get("data", {}).get("name") or data.get("basicInfo", {}).get("name") or data.get("nodeName", "")
    return data.get("name") or data.get("title") or data.get("caption") or node.get("data", {}).get("name", "")


def prompt_of(doc, node_id):
    return content(doc, node_id).get("generation", {}).get("prompt", "")


def fingerprint(doc, node_id):
    return digest({"node": nodes(doc).get(node_id), "asset": doc["assets"].get(node_id)})


def bind(project, cli, target, switch=False):
    account = cli.auth()
    state = read_state(project)
    binding = dict(parse_target(target, cli), account_id=account["identity"])
    previous = state.get("binding")
    if previous and previous != binding:
        same = all(previous.get(k) == binding.get(k) for k in ("canvas_id", "account_id"))
        if not same and not switch:
            fail("SWITCH_REQUIRED", "已有画布绑定；仅在用户明确要求切换后使用switch。", binding=previous)
        if not same:
            d.write_json(Path(project) / "canvas" / ("archive-" + uuid.uuid4().hex + ".json"), state)
            state = blank_state()
    permissions = cli.command(binding["canvas_id"], "get_permissions")
    d.require(permissions.get("canvasId") == binding["canvas_id"] and permissions.get("readOnly") is False,
              "不能编辑该画布")
    document(cli, binding)
    state["binding"] = binding
    save(project, state)
    return {"status": "bound", "binding": binding}


def connected(project, cli):
    state = read_state(project)
    if not state.get("binding"):
        fail("CANVAS_REQUIRED", "请提供要操作的小云雀画布链接。")
    account = cli.auth()
    if account["identity"] != state["binding"]["account_id"]:
        fail("ACCOUNT_CHANGED", "CLI账号与绑定账号不同；请恢复原账号或明确切换画布绑定。")
    return state, document(cli, state["binding"])


def checkpoint(project, cli, state, reason):
    # The durable checkpoint requires browser-scoped credentials.
    if cli.auth().get("credential_scope"):
        result = cli.command(state["binding"]["canvas_id"], "create_checkpoint", {"reason": reason})
        state["last_checkpoint"] = result.get("checkpointId")
        save(project, state)
        return result.get("checkpointId")
    return None


def target_satisfied(doc, target):
    node_id, action = target["node_id"], target["action"]
    if action == "delete":
        return node_id not in nodes(doc)
    if node_id not in nodes(doc):
        return False
    if action == "create":
        return kind_of(nodes(doc)[node_id]) == target["kind"] and content(doc, node_id).get("name", content(doc, node_id).get("nodeName")) == target["name"]
    if action == "prompt":
        if prompt_of(doc, node_id) != target["value"]:
            return False
        tags = re.findall(r"<node-asset(?:\s[^>]*)?>([^<]+)</node-asset>", target["value"])
        edges = doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {})
        for source in set(tags):
            if source not in nodes(doc) or not reference_ready(doc, source):
                return False
            if not any(e.get("source") == source and e.get("target") == node_id and e.get("type") == "reference" for e in edges.values()):
                return False
        return True
    if action == "patch":
        return all(content(doc, node_id).get(k) == v for k, v in target["patch"].items())
    if action == "rename":
        return name_of(doc, node_id) == target["value"] and content(doc, node_id).get("basicInfo", {}).get("name") == target["value"]
    if action == "field":
        return content(doc, node_id).get("basicInfo", {}).get("appearanceName") == target["value"]
    if action == "description":
        return content(doc, node_id).get("appearanceInfo", {}).get("description") == target["value"]
    if action == "main_image":
        return media.selected_image(doc, node_id) == target["value"] and ready_image(doc, node_id)
    return False


def reconcile(project, cli, state, doc):
    changed = False
    for entry in state["journal"]:
        if entry["status"] in ("pending", "uncertain"):
            if all(target_satisfied(doc, t) for t in entry["targets"]):
                entry["status"] = "verified"
                entry["after"] = {t["node_id"]: fingerprint(doc, t["node_id"]) for t in entry["targets"]}
                changed = True
            else:
                if changed:
                    save(project, state)
                fail("UNCERTAIN_WRITE", "上次写入结果仍未确认；已停止续写，请核对指定节点和CLI隔离状态。", operation=entry["id"], targets=entry["targets"])
    if changed:
        save(project, state)


def write_verified(project, cli, state, command, value, targets):
    entry = {"id": uuid.uuid4().hex, "command": command, "input": copy.deepcopy(value),
             "targets": copy.deepcopy(targets), "status": "pending"}
    state["journal"].append(entry)
    save(project, state)
    try:
        cli.command(state["binding"]["canvas_id"], command, value)
        doc = document(cli, state["binding"])
        if not all(target_satisfied(doc, t) for t in targets):
            fail("READBACK_FAILED", "节点写入后回读不一致。")
    except (CanvasError, OSError, ValueError, KeyError) as exc:
        try:
            doc = document(cli, state["binding"])
            verified = all(target_satisfied(doc, t) for t in targets)
        except (CanvasError, OSError, ValueError, KeyError):
            verified = False
        if not verified:
            rejected = getattr(exc, "code", None) == "COMMAND_REJECTED"
            entry.update(status="rejected" if rejected else "uncertain", error=safe_message(exc))
            save(project, state)
            if rejected:
                raise
            fail("UNCERTAIN_WRITE", "写入尚未确认，已保存进度并停止；先核对画布，不直接重复执行。", operation=entry["id"])
    entry["status"] = "verified"
    entry["after"] = {t["node_id"]: fingerprint(doc, t["node_id"]) for t in targets}
    save(project, state)
    return doc


def desired_assets(session):
    d.require(session["ledger"].get("schema_version") == 3, "旧台账须先审计并审核保留/修改/合并/删除方案，再升级绑定；不再按场次创建场景节点")
    d.reference_catalog(session)
    return ([{"key": c["id"], "name": c["name"], "kind": "role"} for c in session["ledger"]["characters"]]
            + [{"key": s["id"], "name": s["name"], "kind": "scene"} for s in session["ledger"]["scene_assets"]]
            + [{"key": p["id"], "name": p["name"], "kind": "image"} for p in session["ledger"]["props"]])


def selected_units(session, request_id=None, ids=None):
    current = {u["id"]: u for u in session["units"]}
    if ids:
        wanted = ids
    else:
        receipts = session["receipts"]
        d.require(receipts, "尚无已提交分镜")
        receipt = next((r for r in receipts if r["request_id"] == request_id), None) if request_id else receipts[-1]
        d.require(receipt is not None, "找不到指定批次")
        wanted = [u["id"] for u in receipt["units"]]
    d.require(len(wanted) == len(set(wanted)) and all(x in current for x in wanted), "分镜编号重复或不存在")
    return [current[x] for x in wanted]


def ready_image(doc, node_id):
    c = content(doc, node_id)
    kind = kind_of(nodes(doc)[node_id])
    if kind in ("role", "scene"):
        info = c.get("appearanceInfo") or {}
        candidates = info.get("candidatePippitAssetIds") or {}
        selected = [k for k, v in candidates.items() if v is True]
        main = [x.get("pippitAssetId", key) for key, x in media.source_infos(c).items() if x.get("type", "image") == "image" and x.get("isMain")]
        return bool(info.get("pippitAssetId") or len(selected) == 1 or len(main) == 1)
    return kind == "image" and bool(c.get("pippitAssetId") or c.get("assetId"))


def reference_ready(doc, node_id):
    if kind_of(nodes(doc)[node_id]) in ("video", "audio"):
        media = content(doc, node_id)
        return bool(media.get("pippitAssetId") or media.get("assetId"))
    return ready_image(doc, node_id)


def reference_map(session, state, doc):
    names = d.reference_catalog(session)
    resolved, pending = {}, []
    for key, name in names.items():
        mapping = state["mappings"].get(key, {})
        node_id = mapping.get("node_id")
        if node_id in nodes(doc) and ready_image(doc, node_id):
            resolved[name] = "<node-asset>" + node_id + "</node-asset>"
        else:
            pending.append({"key": key, "name": name})
    black = state.get("black_node_id")
    if black in nodes(doc) and kind_of(nodes(doc)[black]) == "image" and ready_image(doc, black):
        resolved["黑幕"] = "<node-asset>" + black + "</node-asset>"
    else:
        pending.append({"key": "BLACK", "name": "黑幕"})
    return resolved, pending


def reference_renderer(references, all_names=None):
    names = all_names or list(references)
    ordinary = [n for n in names if n != "黑幕"]
    branches = (["(?:" + "|".join(re.escape(n) for n in sorted(ordinary, key=len, reverse=True)) + ")(?= )"] if ordinary else [])
    if "黑幕" in names:
        branches.append(r"黑幕(?=$)")
    pattern = re.compile("@(" + "|".join(branches) + ")") if branches else None
    def convert(text):
        return pattern.sub(lambda m: references.get(m[1], m[0]), text) if pattern else text
    return convert


def desired_videos(session, state, doc, units, bind_references=False):
    d.require(session["ledger"].get("schema_version") == 3, "旧项目须先审核资产去重升级方案，再同步视频及引用")
    flags = {u["id"]: bind_references or state["mappings"].get(u["id"], {}).get("references_bound", False) for u in units}
    refs, pending = reference_map(session, state, doc) if any(flags.values()) else ({}, [])
    convert = reference_renderer(refs, [*d.reference_catalog(session).values(), "黑幕"])
    wanted = [{"key": u["id"], "name": u["id"], "kind": "video", "version": u["version"],
               "duration": u["duration"],
               "prompt": d.render_unit(session, u, reference_text=convert if flags[u["id"]] else None),
               "references_bound": flags[u["id"]]} for u in units]
    needed = set()
    for u in units:
        needed.update(u["character_ids"] + d.scene_asset_ids(session, u["scene_ids"]) + u["prop_ids"])
        for field in [u["global"], *[s["text"] for s in u["shots"]], *[s.get("connector", "") for s in u["shots"]]]:
            needed.update(d.asset_text(session, u, field)[1])
    return wanted, [p for p in pending if p["key"] in needed or p["key"] == "BLACK"]


def sync(project, cli, kind, request_id=None, ids=None, decisions=None, preview=False, bind_references=False,
         existing_only=False, references_only=False):
    session = d.load(Path(project))
    d.require(session["ledger"].get("schema_version") == 3, "旧项目须先审核资产去重升级方案；当前同步不会连接画布或续接旧写入")
    state, doc = connected(project, cli)
    reconcile(project, cli, state, doc)
    if kind == "assets":
        desired, pending = desired_assets(session), []
    else:
        bind_references = bind_references or any(m.get("image") for m in state["mappings"].values())
        desired, pending = desired_videos(session, state, doc, selected_units(session, request_id, ids), bind_references)
    unbound = [x["key"] for x in desired if x["key"] not in state["mappings"]] if existing_only else []
    if existing_only:
        desired = [x for x in desired if x["key"] in state["mappings"]]
    settings, model = media.model_settings(cli) if kind == "videos" and desired and not references_only else ({}, {})
    decisions = decisions or {}
    conflicts, plan, skipped = [], [], []
    for item in desired:
        key = item["key"]
        if decisions.get(key, {}).get("skip") is True:
            skipped.append(key)
            continue
        mapping = state["mappings"].get(key)
        node_id = mapping.get("node_id") if mapping else None
        allocated_id = None
        if mapping and node_id not in nodes(doc) and mapping.get("stage") == "allocated":
            allocated_id, node_id, mapping = node_id, None, None
        if mapping and node_id not in nodes(doc):
            choice = decisions.get(key, {})
            if choice.get("recreate_missing") == node_id:
                mapping, node_id = None, None
            else:
                conflicts.append({"key": key, "reason": "mapped_node_missing", "node_id": node_id})
                continue
        if mapping and kind_of(nodes(doc)[node_id]) != item["kind"]:
            conflicts.append({"key": key, "reason": "node_type_changed", "node_id": node_id})
            continue
        choice = decisions.get(key, {})
        if not mapping:
            matches = [i for i, n in nodes(doc).items() if kind_of(n) == item["kind"] and name_of(doc, i) == item["name"]]
            if matches:
                if choice.get("reuse") in matches and choice.get("fingerprint") == fingerprint(doc, choice["reuse"]):
                    node_id = choice["reuse"]
                else:
                    conflicts.append({"key": key, "reason": "existing_name", "matches": [
                        {"node_id": i, "fingerprint": fingerprint(doc, i)} for i in matches]})
                    continue
        if settings:
            current = content(doc, node_id).get("generation", {}) if node_id else {}
            media.validate_duration(model, item["duration"], current)
            item["settings"] = {**settings, "durationMs": item["duration"] * 1000}
            changed_settings = []
            if node_id and (not mapping or mapping.get("stage") == "ready"):
                baseline = (mapping or {}).get("video_settings", {})
                for field, expected in item["settings"].items():
                    value = current.get(field)
                    if field in ("modelKey", "resolution") and value != expected:
                        changed_settings.append(field)
                    elif field == "durationMs" and field in baseline and value != baseline[field] and value != expected:
                        changed_settings.append(field)
                if changed_settings and choice.get("overwrite_fingerprint") != fingerprint(doc, node_id):
                    conflicts.append({"key": key, "reason": "video_settings_changed", "fields": changed_settings,
                                      "node_id": node_id, "fingerprint": fingerprint(doc, node_id),
                                      "current": {f: current.get(f) for f in item["settings"]}, "desired": item["settings"]})
                    continue
        if node_id and mapping:
            changes = []
            if mapping.get("stage") != "ready":
                expected = mapping.get("adopted_fingerprint")
                for entry in reversed(state["journal"]):
                    if entry["status"] == "verified" and node_id in entry.get("after", {}):
                        expected = entry["after"][node_id]
                        break
                if expected != fingerprint(doc, node_id):
                    changes.append("initialization")
            if mapping.get("stage") == "ready" and name_of(doc, node_id) != mapping.get("name") and name_of(doc, node_id) != item["name"]:
                changes.append("name")
            if mapping.get("stage") == "ready" and "prompt" in item and digest(prompt_of(doc, node_id)) != mapping.get("prompt_hash") and prompt_of(doc, node_id) != item["prompt"]:
                changes.append("prompt")
            if item["kind"] in ("role", "scene") and mapping.get("stage") == "ready":
                current_view = content(doc, node_id).get("basicInfo", {}).get("appearanceName")
                if current_view != mapping.get("appearance_name", "") and current_view != "":
                    changes.append("appearanceName" if item["kind"] == "role" else "sceneViewName")
            if changes and choice.get("overwrite_fingerprint") != fingerprint(doc, node_id):
                conflicts.append({"key": key, "reason": "cloud_edited", "fields": changes, "node_id": node_id,
                                  "fingerprint": fingerprint(doc, node_id)})
                continue
        plan.append(dict(item, node_id=node_id, allocated_id=allocated_id))
    if conflicts or preview:
        return {"status": "needs_decision" if conflicts else "planned", "count": len(plan), "conflicts": conflicts,
                "pending_references": pending, "unbound_units": unbound, "skipped": skipped,
                "plan": [{k: x[k] for k in ("key", "name", "kind", "node_id", "settings") if k in x} for x in plan]}
    if len(plan) >= 10:
        checkpoint(project, cli, state, "seedance batch sync")
    created, updated, reused = 0, 0, 0
    positions = layout_positions(doc, plan)
    for item in plan:
        key, node_id = item["key"], item.get("node_id")
        # Re-read before every node; detect changes since planning.
        fresh = document(cli, state["binding"])
        if node_id and fingerprint(fresh, node_id) != fingerprint(doc, node_id):
            fail("CONCURRENT_EDIT", "画布节点在操作期间发生变化，已停止。", key=key, node_id=node_id)
        doc = fresh
        if not node_id:
            # Recheck name collisions immediately before allocating.
            if any(kind_of(n) == item["kind"] and name_of(doc, i) == item["name"] for i, n in nodes(doc).items()):
                fail("CONCURRENT_EDIT", "操作期间出现同名节点，请重新核对。", key=key)
            node_id = item.get("allocated_id") or cli.allocate()
            state["mappings"][key] = {"node_id": node_id, "kind": item["kind"], "name": item["name"], "stage": "allocated"}
            save(project, state)
            init = initial_data(item)
            command = {"nodeKind": item["kind"], "id": node_id, "initialData": init, "topLeft": positions[key]}
            doc = write_verified(project, cli, state, "create_biz_node", command,
                                 [{"action": "create", "node_id": node_id, "kind": item["kind"], "name": item["name"]}])
            created += 1
        else:
            reused += 1
            if key not in state["mappings"] or state["mappings"][key]["node_id"] != node_id:
                state["mappings"][key] = {"node_id": node_id, "kind": item["kind"], "name": name_of(doc, node_id), "stage": "adopted",
                                          "adopted_fingerprint": fingerprint(doc, node_id)}
                save(project, state)
        before = fingerprint(doc, node_id)
        doc = set_name(project, cli, state, doc, node_id, item["name"])
        if item["kind"] in ("role", "scene"):
            field = "appearanceName" if item["kind"] == "role" else "sceneViewName"
            if content(doc, node_id).get("basicInfo", {}).get("appearanceName") != "":
                doc = write_verified(project, cli, state, "xyq." + item["kind"] + ".updateDescription",
                                     {"nodeId": node_id, "assetId": node_id, "field": field, "value": ""},
                                     [{"action": "field", "node_id": node_id, "value": ""}])
        if "prompt" in item and prompt_of(doc, node_id) != item["prompt"]:
            doc = write_verified(project, cli, state, "xyq.generation.update_prompt", {"nodeId": node_id, "prompt": item["prompt"]},
                                 [{"action": "prompt", "node_id": node_id, "value": item["prompt"]}])
        if item.get("settings"):
            generation = copy.deepcopy(content(doc, node_id).get("generation") or {})
            if any(generation.get(k) != v for k, v in item["settings"].items()):
                generation.update(item["settings"])
                patch = {"generation": generation}
                doc = write_verified(project, cli, state, "update_node_data", {"id": node_id, "patch": patch},
                                     [{"action": "patch", "node_id": node_id, "patch": patch}])
        updated += fingerprint(doc, node_id) != before
        mapping = state["mappings"][key]
        mapping.update(stage="ready", name=item["name"])
        if item["kind"] in ("role", "scene"):
            mapping["appearance_name"] = ""
        if "prompt" in item:
            mapping.update(prompt_hash=digest(item["prompt"]), version=item["version"], references_bound=item["references_bound"])
            if item.get("settings"):
                mapping["video_settings"] = item["settings"]
            if not references_only:
                state["pending_units"] = [x for x in state["pending_units"] if x != key]
            for marker_path in ([] if references_only else (Path(project) / "canvas" / "pending-sync").glob("*.json")):
                marker = d.read_json(marker_path)
                if marker["units"].get(key, 0) <= item["version"]:
                    marker["units"].pop(key, None)
                    if marker["units"]:
                        d.write_json(marker_path, marker)
                    else:
                        marker_path.unlink()
        save(project, state)
    return {"status": "synced", "created": created, "updated": updated, "reused": reused,
            "pending_references": pending, "unbound_units": unbound, "skipped": skipped, "binding": state["binding"]}


def initial_data(item):
    if item["kind"] in ("role", "scene"):
        return {"nodeName": item["name"], "basicInfo": {"name": item["name"], "appearanceName": ""}}
    return {"name": item["name"], "caption": item["name"], **({"title": item["name"]} if item["kind"] == "video" else {}),
            **({"generation": copy.deepcopy(item["settings"])} if item.get("settings") else {})}


def layout_positions(doc, plan):
    existing = list(nodes(doc).values())
    right = max([n.get("x", 0) + n.get("w", 0) for n in existing] + [0]) + 160
    positions, top = {}, 0
    for kind in ("role", "scene", "image", "video"):
        items = [x for x in plan if not x.get("node_id") and x["kind"] == kind]
        for index, item in enumerate(items):
            positions[item["key"]] = {"x": right + index % 4 * 640, "y": top + index // 4 * 620}
        if items:
            top += ((len(items) + 3) // 4) * 620 + 160
    return positions


def set_name(project, cli, state, doc, node_id, name):
    kind = kind_of(nodes(doc)[node_id])
    if kind in ("role", "scene"):
        if name_of(doc, node_id) == name and content(doc, node_id).get("basicInfo", {}).get("name") == name:
            return doc
        return write_verified(project, cli, state, "xyq." + kind + ".updateDescription",
                              {"nodeId": node_id, "assetId": node_id, "field": "characterName" if kind == "role" else "sceneName", "value": name},
                              [{"action": "rename", "node_id": node_id, "value": name}])
    patch = {"name": name, "caption": name}
    if kind == "video":
        patch["title"] = name
    if all(content(doc, node_id).get(k) == v for k, v in patch.items()):
        return doc
    return write_verified(project, cli, state, "update_node_data", {"id": node_id, "patch": patch},
                          [{"action": "patch", "node_id": node_id, "patch": patch}])


def targets_from_request(doc, request, known_deleted=None):
    selectors = request.get("targets")
    d.require(isinstance(selectors, list) and selectors, "targets须列出指定节点")
    selected = []
    for selector in selectors:
        if "id" in selector:
            if selector["id"] not in nodes(doc) and selector["id"] in (known_deleted or set()):
                continue
            matches = [selector["id"]] if selector["id"] in nodes(doc) else []
        else:
            d.require("name" in selector or "kind" in selector, "节点筛选条件不能为空")
            matches = [i for i, n in nodes(doc).items() if ("name" not in selector or name_of(doc, i) == selector["name"])
                       and ("kind" not in selector or kind_of(n) == selector["kind"])]
            if "name" in selector and len(matches) > 1:
                fail("AMBIGUOUS_NODE", "同名节点不唯一，请指定节点ID。", matches=matches)
        d.require(matches, "未找到指定节点")
        selected.extend(matches)
    return list(dict.fromkeys(selected))


def dependents(doc, selected):
    selected = set(selected)
    affected = set()
    root = doc["assets"][doc["rootCanvasId"]]["content"]
    for edge in root.get("edges", {}).values():
        if edge.get("source") in selected and edge.get("target") not in selected:
            affected.add(edge["target"])
    for node_id in nodes(doc):
        if node_id in selected:
            continue
        # Formal prompt references and other structured references (e.g. timelines).
        def contains(value):
            if isinstance(value, dict):
                return any(contains(v) for v in value.values())
            if isinstance(value, list):
                return any(contains(v) for v in value)
            if isinstance(value, str):
                return value in selected or any("<node-asset>" + x + "</node-asset>" in value for x in selected)
            return False
        if contains(content(doc, node_id)):
            affected.add(node_id)
    return sorted(affected)


def manage(project, cli, action, request, preview=False):
    state, doc = connected(project, cli)
    reconcile(project, cli, state, doc)
    known_deleted = {t["node_id"] for e in state["journal"] if e["status"] == "verified"
                     for t in e["targets"] if t["action"] == "delete"} if action == "delete" else set()
    selected = targets_from_request(doc, request, known_deleted)
    already_deleted = [t["id"] for t in request["targets"] if t.get("id") in known_deleted and t["id"] not in nodes(doc)]
    if not selected:
        return {"status": "deleted", "count": 0, "already_deleted": already_deleted, "binding": state["binding"]}
    effects = dependents(doc, selected) if action == "delete" else []
    if effects and request.get("accepted_dependents") != effects:
        return {"status": "needs_decision", "reason": "downstream_references", "node_ids": selected, "dependents": effects}
    d.require(action == "delete" or isinstance(request.get("changes"), dict) and request["changes"], "缺少修改字段")
    allowed = {"name", "prompt", "description", "appearanceName", "sceneViewName"}
    if action != "delete":
        d.require(set(request["changes"]) <= allowed, "修改字段不支持；仅支持name/prompt/description/appearanceName/sceneViewName")
        d.require(all(isinstance(v, str) for v in request["changes"].values()), "修改值必须是字符串")
        for node_id in selected:
            kind = kind_of(nodes(doc)[node_id])
            for field in request["changes"]:
                d.require(field != "appearanceName" or kind == "role", "appearanceName仅适用于角色")
                d.require(field != "sceneViewName" or kind == "scene", "sceneViewName仅适用于场景")
                d.require(field != "description" or kind in ("role", "scene", "text"), "该节点不支持description")
                d.require(field != "prompt" or kind in ("image", "video"), "该节点不支持此生成提示词接口")
    if preview:
        return {"status": "planned", "node_ids": selected, "dependents": effects,
                "fingerprints": {i: fingerprint(doc, i) for i in selected}}
    expected = request.get("fingerprints", {})
    for node_id, value in expected.items():
        if node_id in known_deleted and node_id not in nodes(doc):
            continue
        d.require(node_id in selected and fingerprint(doc, node_id) == value, "节点自预览后已变化")
    checkpoint(project, cli, state, "seedance " + action)
    d.write_json(Path(project) / "canvas" / ("before-" + action + "-" + uuid.uuid4().hex + ".json"), doc)
    for node_id in selected:
        fresh = document(cli, state["binding"])
        if fingerprint(fresh, node_id) != fingerprint(doc, node_id):
            fail("CONCURRENT_EDIT", "节点在操作期间发生变化，已停止。", node_id=node_id)
        doc = fresh
        if action == "delete":
            new_effects = set(dependents(doc, selected)) - set(effects)
            if new_effects:
                fail("CONCURRENT_EDIT", "操作期间新增了下游引用，已停止。", dependents=sorted(new_effects))
            doc = write_verified(project, cli, state, "delete_nodes", {"ids": [node_id]}, [{"action": "delete", "node_id": node_id}])
            state["mappings"] = {k: m for k, m in state["mappings"].items() if m["node_id"] != node_id}
            if state.get("black_node_id") == node_id:
                state.pop("black_node_id")
        else:
            for field, value in request["changes"].items():
                if field == "name":
                    doc = set_name(project, cli, state, doc, node_id, value)
                elif field == "prompt":
                    doc = write_verified(project, cli, state, "xyq.generation.update_prompt", {"nodeId": node_id, "prompt": value},
                                         [{"action": "prompt", "node_id": node_id, "value": value}])
                elif field in ("appearanceName", "sceneViewName"):
                    kind = kind_of(nodes(doc)[node_id])
                    doc = write_verified(project, cli, state, "xyq." + kind + ".updateDescription",
                                         {"nodeId": node_id, "assetId": node_id, "field": field, "value": value},
                                         [{"action": "field", "node_id": node_id, "value": value}])
                elif field == "description":
                    kind = kind_of(nodes(doc)[node_id])
                    if kind == "text":
                        patch = {"plainText": value}
                        doc = write_verified(project, cli, state, "update_node_data", {"id": node_id, "patch": patch},
                                             [{"action": "patch", "node_id": node_id, "patch": patch}])
                    else:
                        doc = write_verified(project, cli, state, "xyq." + kind + ".updateDescription",
                                             {"nodeId": node_id, "assetId": node_id, "field": "appearance" if kind == "role" else "description", "value": value},
                                             [{"action": "description", "node_id": node_id, "value": value}])
            # Keep the last generated baseline. Intentional manual edits are not
            # silently replaced on the next storyboard sync.
            for mapping in state["mappings"].values():
                if mapping["node_id"] == node_id:
                    mapping["last_explicit_edit"] = list(request["changes"])
        save(project, state)
    return {"status": "deleted" if action == "delete" else "updated", "count": len(selected),
            "node_ids": selected, "already_deleted": already_deleted, "binding": state["binding"]}


def sync_replacements(project, ids):
    """Called after a local commit; local delivery never rolls back on cloud failure."""
    state = read_state(project)
    wanted = [i for i in ids if i in state["mappings"] and state["mappings"][i]["kind"] == "video"]
    if not state.get("binding") or not wanted:
        return {"status": "not_bound"}
    session = d.load(Path(project))
    versions = {u["id"]: u["version"] for u in session["units"] if u["id"] in wanted}
    d.write_json(Path(project) / "canvas" / "pending-sync" / (uuid.uuid4().hex + ".json"), {"units": versions})
    try:
        with project_lock(project):
            state = read_state(project)
            save(project, state)
            # Preserve real reference tags on units already bound to pictures.
            plain = [i for i in wanted if not state["mappings"][i].get("references_bound")]
            bound = [i for i in wanted if i not in plain]
            results = []
            for group, refs in ((plain, False), (bound, True)):
                if group:
                    results.append(sync(project, CLI(project), "videos", ids=group, bind_references=refs, existing_only=True))
            return {"status": "synced" if all(r["status"] == "synced" for r in results) else "pending", "results": results}
    except (ValueError, OSError, KeyError) as exc:
        return {"status": "pending", "message": safe_message(exc)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup", "auth", "bind", "status", "assets", "videos", "references", "images", "edit", "delete", "black"))
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--node")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--target")
    parser.add_argument("--switch", action="store_true")
    parser.add_argument("--request-id")
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--node-id")
    parser.add_argument("--folder", type=Path)
    args = parser.parse_args()
    args.project = args.project.resolve()
    try:
        d.require(d.ROOT != args.project and d.ROOT not in args.project.parents, "项目不能位于Skill安装目录")
        cli = CLI(args.project)
        with project_lock(args.project):
            if args.command == "setup":
                result = cli.setup(args.node)
            elif args.command == "auth":
                auth = cli.auth(login=args.login, force=args.force)
                result = {"status": "authenticated", "account_id": auth["identity"]}
            elif args.command == "bind":
                d.require(args.target, "绑定画布需要--target")
                result = bind(args.project, cli, args.target, args.switch)
            elif args.command in ("assets", "videos", "references"):
                result = sync(args.project, cli, "assets" if args.command == "assets" else "videos",
                              args.request_id, args.ids, d.read_json(args.decisions) if args.decisions else None,
                              args.preview, args.command == "references", existing_only=args.command == "references",
                              references_only=args.command == "references")
            elif args.command == "images":
                d.require(args.folder, "明确选择上传图片后，请提供图片文件夹路径--folder")
                result = media.images(args.project, cli, args.folder, d.read_json(args.decisions) if args.decisions else None, args.preview)
            elif args.command in ("edit", "delete"):
                d.require(args.request, "修改/删除需要--request")
                result = manage(args.project, cli, args.command, d.read_json(args.request), args.preview)
            else:
                state, doc = connected(args.project, cli)
                if args.command == "black":
                    reconcile(args.project, cli, state, doc)
                    d.require(args.node_id in nodes(doc) and kind_of(nodes(doc)[args.node_id]) == "image", "黑幕须指定现有图片节点ID")
                    d.require(ready_image(doc, args.node_id), "黑幕节点尚无图片")
                    state["black_node_id"] = args.node_id
                    save(args.project, state)
                    result = {"status": "black_bound", "node_id": args.node_id}
                else:
                    result = {"status": "connected", "binding": state["binding"], "pending_units": state["pending_units"],
                              "pending_image_references": state.get("pending_image_references", []),
                              "unfinished_uploads": [{"file_hash": sha, **entry} for sha, entry in state.get("uploads", {}).items()
                                                     if entry.get("status") != "ready"],
                              "unconfirmed_operations": [{"id": e["id"], "command": e["command"], "status": e["status"]}
                                                         for e in state["journal"] if e["status"] in ("pending", "uncertain")],
                              "nodes": [{"id": i, "kind": kind_of(n), "name": name_of(doc, i), "fingerprint": fingerprint(doc, i)} for i, n in nodes(doc).items()]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "code": getattr(exc, "code", "ERROR"), "message": safe_message(exc),
                          **getattr(exc, "details", {})}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
