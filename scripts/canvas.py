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
import time
import uuid
from urllib.parse import urlparse, parse_qs, urlencode

sys.dont_write_bytecode = True
import director as d
import canvas_media as media

KINDS = {"role": "biz/role", "scene": "biz/scene", "image": "biz/image", "video": "biz/video", "audio": "biz/audio", "text": "biz/text"}
NODE_ASSET_TAG = re.compile(r"<node-asset\b[^>]*>([^<]*)</node-asset>")
VIDEO_READBACK_ATTEMPTS = 3
VIDEO_READBACK_DELAY = 0.5


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
        return self.allocate_many(1)[0]

    def allocate_many(self, count):
        d.require(isinstance(count, int) and 1 <= count <= 5000, "ID申请数量须为1至5000")
        ids = self.native("canvas", "allocate", "--count", str(count)).get("asset_ids")
        if (not isinstance(ids, list) or len(ids) != count
                or not all(isinstance(value, str) and value for value in ids) or len(set(ids)) != count):
            fail("INVALID_ID", "CLI未返回唯一字符串资产ID。")
        return ids


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
        state.pop("black_node_kind", None)
        state.pop("black_video_preparation", None)
    if state.get("legacy_black_node_id") in deleted:
        state.pop("legacy_black_node_id", None)
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
    check_control(cli)
    # The durable checkpoint requires browser-scoped credentials.
    if cli.auth().get("credential_scope"):
        result = cli.command(state["binding"]["canvas_id"], "create_checkpoint", {"reason": reason})
        state["last_checkpoint"] = result.get("checkpointId")
        save(project, state)
        return result.get("checkpointId")
    return None


def node_asset_ids(text):
    """Use the SDK's node-asset tag boundary, including attributes and ID whitespace."""
    return list(dict.fromkeys(match[1].strip() for match in NODE_ASSET_TAG.finditer(str(text)) if match[1].strip()))


def role_reference_media(doc, source, target):
    # CLI 1.0.38 Fa/GO/gL: role media creates a saved reference alongside its edge.
    # Other node references are represented by edges and need not appear in the draft.
    data = content(doc, source)
    sources = media.source_infos(data)
    allowed = {"image"} if kind_of(nodes(doc)[target]) == "image" else {"image", "video", "audio"}
    if sources:
        return any(item.get("type", "image") in allowed for item in sources.values())
    return bool(data.get("appearanceInfo", {}).get("pippitAssetId"))


def role_reference_row(references, source):
    return isinstance(references, list) and any(isinstance(row, dict)
        and source in [row.get(field, "").strip() for field in ("sourceNodeId", "nodeAssetId", "id")
                       if isinstance(row.get(field, ""), str)]
        and row.get("type") in ("image", "video", "audio") for row in references)


def prompt_references_satisfied(doc, node_id, prompt):
    # Official generation.references is an array of objects; node identity aliases
    # are sourceNodeId/nodeAssetId/id (SDK Eie). Empty arrays are valid for ordinary
    # node-edge references, so do not require one draft row per image/scene tag.
    references = content(doc, node_id).get("generation", {}).get("references", [])
    if not isinstance(references, list) or not all(isinstance(row, dict) for row in references):
        return False
    for row in references:
        selection = row.get("dataReferenceSelection", [])
        if not isinstance(selection, list) or not all(isinstance(value, str) for value in selection):
            return False
    edges = doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {})
    for source in node_asset_ids(prompt):
        # A reference edge identifies an asset node even before its main image is
        # uploaded. Missing media is reported separately, never an identity failure.
        if source not in nodes(doc) or kind_of(nodes(doc)[source]) not in ("role", "scene", "image", "video", "audio"):
            return False
        if not any(edge.get("source") == source and edge.get("target") == node_id and edge.get("type") == "reference"
                   for edge in edges.values()):
            return False
        if kind_of(nodes(doc)[source]) == "role" and role_reference_media(doc, source, node_id):
            if not role_reference_row(references, source):
                return False
    return True


def edge_only_black(unit):
    return unit.get("black_reference_mode") == "edge-only-v1"


def reference_edge(doc, source, target):
    edges = doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {})
    return next((edge for edge in edges.values() if edge.get("source") == source
                 and edge.get("target") == target and edge.get("type") == "reference"), None)


def black_prompt_absent(prompt, source):
    return not re.search(r"@黑幕(?=\s|$)", prompt) and source not in node_asset_ids(prompt)


def video_references_satisfied(doc, node_id, item):
    if not prompt_references_satisfied(doc, node_id, item["prompt"]):
        return False
    if edge_only_black(item) and item.get("references_bound"):
        source = item.get("black_node_id")
        return (source in nodes(doc) and kind_of(nodes(doc)[source]) == item.get("black_node_kind")
                and name_of(doc, source) == "黑幕" and black_prompt_absent(item["prompt"], source)
                and reference_edge(doc, source, node_id) is not None)
    return True


def repair_role_reference_rows(project, cli, state, doc, node_id, prompt):
    """Refresh only an old empty-role edge after that role receives its media."""
    edges = doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {})
    references = content(doc, node_id).get("generation", {}).get("references", [])
    ids = []
    for source in node_asset_ids(prompt):
        if source not in nodes(doc) or kind_of(nodes(doc)[source]) != "role":
            continue
        if not role_reference_media(doc, source, node_id) or role_reference_row(references, source):
            continue
        ids.extend(edge_id for edge_id, edge in edges.items() if edge.get("source") == source
                   and edge.get("target") == node_id and edge.get("type") == "reference")
    if not ids:
        return doc
    return write_verified(project, cli, state, "delete_edges", {"ids": ids},
                          [{"action": "reference_edges_absent", "node_id": node_id, "edge_ids": ids}])


def target_satisfied(doc, target):
    node_id, action = target["node_id"], target["action"]
    if action == "delete":
        return node_id not in nodes(doc)
    if node_id not in nodes(doc):
        return False
    if action == "create":
        if target["kind"] == "video":
            # Legacy video create journals predate the explicit rename step.
            # The exact allocated ID and kind confirm creation; the final
            # video receipt owns name/duration/prompt acceptance.
            return kind_of(nodes(doc)[node_id]) == "video"
        return kind_of(nodes(doc)[node_id]) == target["kind"] and content(doc, node_id).get("name", content(doc, node_id).get("nodeName")) == target["name"]
    if action == "node_kind":
        return kind_of(nodes(doc)[node_id]) == target["kind"]
    if action == "duration":
        return (content(doc, node_id).get("generation") or {}).get("durationMs") == target["value"]
    if action == "prompt":
        return prompt_of(doc, node_id) == target["value"] and prompt_references_satisfied(doc, node_id, target["value"])
    if action == "prompt_text":
        return prompt_of(doc, node_id) == target["value"]
    if action == "reference_edges_absent":
        edges = doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {})
        return all(edge_id not in edges for edge_id in target["edge_ids"])
    if action == "reference_edge":
        edge = doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {}).get(target["edge_id"], {})
        source = target["source"]
        return (source in nodes(doc) and kind_of(nodes(doc)[source]) == target["source_kind"]
                and name_of(doc, source) == "黑幕" and edge.get("source") == source
                and edge.get("target") == node_id and edge.get("type") == "reference")
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


def reconcile(project, cli, state, doc, preview=False, decisions=None):
    changed = False
    for entry in state["journal"]:
        if entry["status"] in ("pending", "uncertain", "acknowledged"):
            target_ids = {target["node_id"] for target in entry["targets"]}
            replaced = next((key for key, mapping in state["mappings"].items()
                             if mapping.get("kind") == "video" and target_ids == {mapping["node_id"]}
                             and mapping["node_id"] not in nodes(doc)
                             and (decisions or {}).get(key, {}).get("recreate_missing") == mapping["node_id"]), None)
            if replaced is not None:
                # The user explicitly deleted this exact missing video and asked
                # to recreate it. Preserve the old operation; do not replay it.
                entry.update(status="superseded", resolution="explicitly_recreate_deleted_video", key=replaced)
                changed = True
                continue
            if all(target_satisfied(doc, t) for t in entry["targets"]):
                entry["status"] = "verified"
                entry["after"] = {t["node_id"]: fingerprint(doc, t["node_id"]) for t in entry["targets"]}
                changed = True
            else:
                if changed and not preview:
                    save(project, state)
                fail("UNCERTAIN_WRITE", "上次写入结果仍未确认；已停止续写，请核对指定节点和CLI隔离状态。", operation=entry["id"], targets=entry["targets"])
    if changed and not preview:
        save(project, state)


def check_control(cli):
    callback = getattr(cli, "check_control", None)
    if callable(callback):
        callback()


def write_verified(project, cli, state, command, value, targets):
    check_control(cli)
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


def write_acknowledged(project, cli, state, command, value, targets, receipt):
    """Journal a video step's official ACK; defer snapshot acceptance to its receipt."""
    check_control(cli)
    entry = {"id": uuid.uuid4().hex, "command": command, "input": copy.deepcopy(value),
             "targets": copy.deepcopy(targets), "status": "pending", "video_receipt_id": receipt["id"]}
    state["journal"].append(entry)
    receipt["steps"].append(entry["id"])
    save(project, state)
    try:
        cli.command(state["binding"]["canvas_id"], command, value)
    except (CanvasError, OSError, ValueError, KeyError) as exc:
        rejected = getattr(exc, "code", None) == "COMMAND_REJECTED"
        entry.update(status="rejected" if rejected else "uncertain", error=safe_message(exc))
        save(project, state)
        if rejected:
            raise
        fail("UNCERTAIN_WRITE", "视频写入命令未获得明确确认，已保存节点ID并停止，不直接重放写入。",
             operation=entry["id"], node_id=receipt["node_id"])
    entry["status"] = "acknowledged"
    # Keep field baselines from before the write or from its intended ACK.
    # A later snapshot must not absorb unrelated manual edits on recovery.
    baseline = state["mappings"][receipt["key"]].get("video_initialization_baseline")
    if baseline is not None:
        for target in targets:
            if target["action"] == "patch":
                baseline.update({key: val for key, val in target["patch"].items() if key in ("name", "caption", "title")})
            elif target["action"] == "duration":
                baseline["durationMs"] = target["value"]
            elif target["action"] == "prompt_text":
                baseline["prompt_hash"] = digest(target["value"])
    save(project, state)
    return entry


def video_initialization_changes(doc, node_id, mapping, item):
    baseline = mapping.get("video_initialization_baseline")
    if baseline is None:
        return None  # Legacy mappings keep their previous fingerprint guard.
    current = content(doc, node_id)
    changes = []
    if any(field in baseline and current.get(field) not in (baseline[field], item["name"])
           for field in ("name", "caption", "title")):
        changes.append("name")
    if "durationMs" in baseline and (current.get("generation") or {}).get("durationMs") not in (
            baseline["durationMs"], item.get("settings", {}).get("durationMs")):
        changes.append("durationMs")
    if digest(prompt_of(doc, node_id)) not in (baseline["prompt_hash"], digest(item["prompt"])):
        changes.append("prompt")
    return changes


def video_snapshot(cli, state, node_id):
    """Read generation before a shallow patch, without accepting name/prompt yet."""
    for attempt in range(VIDEO_READBACK_ATTEMPTS):
        doc = document(cli, state["binding"])
        if node_id in nodes(doc):
            d.require(kind_of(nodes(doc)[node_id]) == "video", "视频节点类型已变化，已停止写入")
            return doc
        if attempt + 1 < VIDEO_READBACK_ATTEMPTS:
            time.sleep(VIDEO_READBACK_DELAY)
    fail("UNCERTAIN_WRITE", "已确认创建命令，但节点尚未出现在快照中；保留同一节点ID，停止后续写入。", node_id=node_id)


def write_video(project, cli, state, doc, item, node_id, context, create=None, references_only=False):
    """Create -> rename -> duration -> encoded prompt -> one unified readback receipt."""
    names = {field: item["name"] for field in ("name", "caption", "title")}
    targets = [{"action": "node_kind", "node_id": node_id, "kind": "video"},
               {"action": "prompt_text", "node_id": node_id, "value": item["prompt"]}]
    if not references_only:
        targets[1:1] = [{"action": "patch", "node_id": node_id, "patch": names},
                        {"action": "duration", "node_id": node_id, "value": item["settings"]["durationMs"]}]
    receipt = {"id": uuid.uuid4().hex, "key": item["key"], "node_id": node_id,
               "status": "writing", "steps": [], "targets": copy.deepcopy(targets),
               "reference_targets": [], "references_only": references_only}
    receipt_path = Path(project) / "canvas" / "video-write-receipts" / (receipt["id"] + ".json")
    mapping = state["mappings"][item["key"]]
    mapping["video_receipt_id"] = receipt["id"]
    if not references_only:
        if node_id in nodes(doc):
            mapping["initialization_fingerprint"] = fingerprint(doc, node_id)
        if "video_initialization_baseline" not in mapping:
            if create is not None:
                mapping["video_initialization_baseline"] = {"prompt_hash": digest("")}
            else:
                current = content(doc, node_id)
                mapping["video_initialization_baseline"] = {**{field: current.get(field) for field in names},
                    "durationMs": (current.get("generation") or {}).get("durationMs"),
                    "prompt_hash": digest(prompt_of(doc, node_id))}
        mapping["stage"] = "writing"
    d.write_json(receipt_path, receipt)
    save(project, state)
    try:
        if create is not None:
            write_acknowledged(project, cli, state, "create_biz_node", create,
                               [{"action": "node_kind", "node_id": node_id, "kind": "video"}], receipt)
        if not references_only:
            if create is not None or not all(content(doc, node_id).get(k) == v for k, v in names.items()):
                write_acknowledged(project, cli, state, "update_node_data", {"id": node_id, "patch": names},
                                   [{"action": "patch", "node_id": node_id, "patch": names}], receipt)
            # update_node_data replaces nested values. Read and preserve all generation
            # fields; the only selected parameter is durationMs. No model API is used.
            doc = video_snapshot(cli, state, node_id)
            context["doc"] = doc
            generation = copy.deepcopy(content(doc, node_id).get("generation") or {})
            d.require(isinstance(generation, dict), "generation格式无效，无法安全更新视频时长")
            baseline = mapping["video_initialization_baseline"]
            if digest(prompt_of(doc, node_id)) not in (baseline["prompt_hash"], digest(item["prompt"])):
                fail("CONCURRENT_EDIT", "视频提示词在初始化期间发生人工修改，已停止。", node_id=node_id)
            baseline.setdefault("durationMs", generation.get("durationMs"))
            save(project, state)
            duration = item["settings"]["durationMs"]
            if generation.get("durationMs") != duration:
                generation["durationMs"] = duration
                write_acknowledged(project, cli, state, "update_node_data",
                                   {"id": node_id, "patch": {"generation": generation}},
                                   [{"action": "duration", "node_id": node_id, "value": duration}], receipt)
        if edge_only_black(item) and item.get("references_bound") and item.get("black_node_id"):
            source = item["black_node_id"]
            d.require(black_prompt_absent(item["prompt"], source), "独立黑幕引用不能出现在提示词正文中")
            edge = reference_edge(doc, source, node_id)
            if edge is None:
                # An edge ID is a Canvas edge identity, not an allocated asset ID.
                # Journal its exact identity before sending; unknown ACKs never replay.
                edge = {"id": "edge-black-" + uuid.uuid4().hex, "type": "reference",
                        "source": source, "target": node_id, "sourceHandle": "right", "targetHandle": "left"}
                edge_target = {"action": "reference_edge", "node_id": node_id, "source": source,
                               "source_kind": item["black_node_kind"], "edge_id": edge["id"]}
                mapping["black_reference_edge_id"] = edge["id"]
                receipt["reference_targets"].append(edge_target)
                d.write_json(receipt_path, receipt)
                save(project, state)
                write_acknowledged(project, cli, state, "create_edge", {"edge": edge}, [edge_target], receipt)
            else:
                mapping["black_reference_edge_id"] = edge["id"]
                save(project, state)
        if prompt_of(doc, node_id) != item["prompt"] or not prompt_references_satisfied(doc, node_id, item["prompt"]):
            doc = repair_role_reference_rows(project, cli, state, doc, node_id, item["prompt"])
            context["doc"] = doc
            write_acknowledged(project, cli, state, "xyq.generation.update_prompt",
                               {"nodeId": node_id, "prompt": item["prompt"]},
                               [{"action": "prompt_text", "node_id": node_id, "value": item["prompt"]}], receipt)
        entry = {"id": receipt["id"], "command": "video_write_receipt", "targets": copy.deepcopy(targets),
                 "status": "pending", "video_receipt_id": receipt["id"]}
        state["journal"].append(entry)
        save(project, state)
        for attempt in range(VIDEO_READBACK_ATTEMPTS):
            doc = document(cli, state["binding"])
            context["doc"] = doc
            core_verified = all(target_satisfied(doc, target) for target in targets)
            references_verified = (core_verified and video_references_satisfied(doc, node_id, item)
                                   and all(target_satisfied(doc, target) for target in receipt["reference_targets"])
                                   and not item.get("unresolved_references"))
            if core_verified and references_verified:
                break
            if attempt + 1 < VIDEO_READBACK_ATTEMPTS:
                time.sleep(VIDEO_READBACK_DELAY)
        observed = content(doc, node_id) if node_id in nodes(doc) else {}
        receipt.update(attempts=attempt + 1, core_verified=core_verified, references_verified=references_verified,
                       observed={"kind": kind_of(nodes(doc)[node_id]) if node_id in nodes(doc) else None,
                                 **{field: observed.get(field) for field in names},
                                 "durationMs": (observed.get("generation") or {}).get("durationMs"),
                                 "prompt_hash": digest(prompt_of(doc, node_id)) if node_id in nodes(doc) else None},
                       checks=[{"action": target["action"], "matched": target_satisfied(doc, target)}
                               for target in [*targets, *receipt["reference_targets"]]])
        if not core_verified:
            entry.update(status="uncertain", error="视频统一回读未通过")
            save(project, state)
            fail("UNCERTAIN_WRITE", "视频写入步骤已确认，但最终名称、时长或提示词回读未通过；已保存回执并停止。",
                 operation=entry["id"], node_id=node_id, video_receipt=str(receipt_path))
        after = {node_id: fingerprint(doc, node_id)}
        for operation in state["journal"]:
            if operation.get("video_receipt_id") == receipt["id"]:
                edge_failed = operation.get("command") == "create_edge" and not all(
                    target_satisfied(doc, target) for target in operation["targets"])
                operation.update(status="partial" if edge_failed else "verified", after=after)
        receipt["status"] = "verified" if references_verified else "partial"
        mapping["video_write_receipt"] = str(receipt_path)
        save(project, state)
        d.write_json(receipt_path, receipt)
        context.setdefault("video_write_receipts", []).append(str(receipt_path))
        return doc
    except (CanvasError, OSError, ValueError, KeyError) as exc:
        receipt.update(status="rejected" if getattr(exc, "code", None) == "COMMAND_REJECTED" else "uncertain",
                       error=safe_message(exc))
        mapping["video_write_receipt"] = str(receipt_path)
        save(project, state)
        d.write_json(receipt_path, receipt)
        if isinstance(exc, CanvasError):
            exc.details["video_receipt"] = str(receipt_path)
        raise


def desired_assets(session):
    d.require(session["ledger"].get("schema_version") == 3, "旧台账须先审计并审核保留/修改/合并/删除方案，再升级绑定；不再按场次创建场景节点")
    d.reference_catalog(session)
    return ([{"key": c["id"], "name": c["name"], "kind": "role"} for c in session["ledger"]["characters"]
             if c.get("visual_mode") != "prompt_only"]
            + [{"key": s["id"], "name": s["name"], "kind": "scene"} for s in session["ledger"]["scene_assets"]]
            + [{"key": p["id"], "name": p["name"], "kind": "image"} for p in session["ledger"]["props"]])


def selected_units(session, request_id=None, ids=None):
    current = {u["id"]: u for u in session["units"]}
    if ids:
        wanted = [d.resolve_unit_id(session, value) for value in ids]
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


def black_reference(state, doc, unit_id=None):
    """Resolve the special reference without changing old units or creating nodes."""
    node_map = nodes(doc) if doc else {}
    mapping = state["mappings"].get(unit_id, {}) if unit_id else {}
    pinned = "black_node_id" in mapping
    black = mapping.get("black_node_id") if pinned else state.get("black_node_id")
    expected = mapping.get("black_node_kind") if pinned else state.get("black_node_kind")
    if black in node_map:
        kind = kind_of(node_map[black])
        expected = expected or kind
        if kind not in ("video", "image") or kind != expected:
            return black, expected, {"reason": "node_type_changed", "actual_kind": kind}
        if kind == "video" and name_of(doc, black) != "黑幕":
            return black, kind, {"reason": "node_name_changed", "actual_name": name_of(doc, black)}
        return black, kind, None
    expected = expected or "video"
    if pinned:
        return black, expected, {"reason": "node_missing"}
    matches = [nid for nid, node in node_map.items()
               if kind_of(node) == expected and name_of(doc, nid) == "黑幕"]
    if len(matches) == 1:
        return matches[0], expected, None
    return black, expected, {"reason": "ambiguous_node" if matches else "node_missing", "matches": matches}


def preserve_legacy_black_bindings(state, doc):
    """Pin existing image references before changing the project default to video."""
    black = state.get("black_node_id")
    if state.get("black_node_kind") == "video" or black not in nodes(doc) or kind_of(nodes(doc)[black]) != "image":
        return
    state["legacy_black_node_id"] = black
    for mapping in state["mappings"].values():
        if mapping.get("kind") != "video" or "black_node_id" in mapping:
            continue
        target = mapping.get("node_id")
        if (target in nodes(doc) and black in node_asset_ids(prompt_of(doc, target))
                or mapping.get("references_requested") or mapping.get("references_bound")):
            mapping.update(black_node_id=black, black_node_kind="image")


def reconcile_black_video(project, cli, state, doc, decisions=None, preview=False, context=None):
    """Prepare one black video with resumable create/rename steps, no generation."""
    choice = (decisions or {}).get("BLACK", {})
    result = {"name": "黑幕", "kind": "video", "node_id": None, "resolution": None, "conflicts": []}
    def conflict(reason, node_id=None, **extra):
        result["conflicts"].append({"key": "BLACK", "name": "黑幕", "kind": "video",
                                    "reason": reason, "node_id": node_id, **extra})
        return doc, result
    preserve_legacy_black_bindings(state, doc)
    preparation = state.get("black_video_preparation", {})
    black = preparation.get("node_id") or state.get("black_node_id")
    owned = preparation.get("node_id") == black and preparation.get("stage") in ("allocated", "created")
    if choice.get("skip") is True:
        return conflict("asset_skipped", black)
    if choice.get("reuse") is not None:
        chosen = choice["reuse"]
        if chosen not in nodes(doc) or kind_of(nodes(doc)[chosen]) != "video" or name_of(doc, chosen) != "黑幕":
            return conflict("invalid_reuse_decision", chosen, expected_kind="video")
        if choice.get("fingerprint") != fingerprint(doc, chosen):
            return conflict("reuse_fingerprint_changed", chosen, fingerprint=fingerprint(doc, chosen))
        black, owned = chosen, False
        result["resolution"] = "adopted"
    elif black in nodes(doc) and (owned or state.get("black_node_kind") == "video"
                                  or kind_of(nodes(doc)[black]) == "video"):
        if kind_of(nodes(doc)[black]) != "video":
            return conflict("node_type_changed", black, expected_kind="video", actual_kind=kind_of(nodes(doc)[black]))
        if not owned and name_of(doc, black) != "黑幕":
            return conflict("node_name_changed", black, actual_name=name_of(doc, black), fingerprint=fingerprint(doc, black))
        result["resolution"] = "resumed" if owned else "reused"
    elif not owned:
        matches = [nid for nid, node in nodes(doc).items() if kind_of(node) == "video" and name_of(doc, nid) == "黑幕"]
        if len(matches) > 1:
            return conflict("ambiguous_node", matches=[{"node_id": nid, "fingerprint": fingerprint(doc, nid)} for nid in matches])
        black = matches[0] if matches else None
        result["resolution"] = "adopted" if black else "create"
    if preview:
        result["node_id"] = black
        return doc, result
    if black is None:
        black = cli.allocate()
        preparation = {"node_id": black, "stage": "allocated"}
        state["black_video_preparation"] = preparation
        owned = True
    state.update(black_node_id=black, black_node_kind="video")
    save(project, state)
    result["node_id"] = black
    if context is not None:
        context["active"] = {"key": "BLACK", "name": "黑幕", "kind": "video", "node_id": black}
    if black not in nodes(doc):
        # An unfinished allocated ID is reused; uncertain journals were reconciled
        # before reaching here, so an unconfirmed create is never blindly replayed.
        position = layout_positions(doc, [{"key": "BLACK", "kind": "video"}])["BLACK"]
        doc = write_verified(project, cli, state, "create_biz_node",
                             {"nodeKind": "video", "id": black, "initialData": {}, "topLeft": position},
                             [{"action": "node_kind", "node_id": black, "kind": "video"}])
        preparation = {"node_id": black, "stage": "created",
                       "names": {field: content(doc, black).get(field) for field in ("name", "title", "caption")}}
        state["black_video_preparation"] = preparation
        save(project, state)
    elif owned:
        baseline = preparation.get("names")
        if baseline is None:
            previous = next((row.get("after", {}).get(black) for row in reversed(state["journal"])
                             if row["status"] == "verified" and black in row.get("after", {})), None)
            if previous is not None and previous != fingerprint(doc, black):
                return conflict("cloud_edited", black, fingerprint=fingerprint(doc, black))
            baseline = {field: content(doc, black).get(field) for field in ("name", "title", "caption")}
            preparation.update(stage="created", names=baseline)
            save(project, state)
        if any(content(doc, black).get(field) not in (value, "黑幕") for field, value in baseline.items()):
            return conflict("node_name_changed", black, actual_name=name_of(doc, black), fingerprint=fingerprint(doc, black))
    if owned:
        doc = set_name(project, cli, state, doc, black, "黑幕")
    d.require(kind_of(nodes(doc)[black]) == "video" and name_of(doc, black) == "黑幕", "黑幕视频节点回读未通过")
    state["black_video_preparation"] = {"node_id": black, "stage": "ready"}
    # Explicit videos may recover a deleted video reference for this range.
    # Legacy image pins remain immutable and outside-range units are untouched.
    for unit in context.get("units", []) if context is not None else []:
        mapping = state["mappings"].get(unit["id"], {})
        if mapping.get("black_node_kind") == "video" and mapping.get("black_node_id") not in nodes(doc):
            mapping["black_node_id"] = black
    save(project, state)
    if context is not None:
        context["doc"] = doc
        context.pop("active", None)
    return doc, result


def reference_map(session, state, doc, asset_ids=None, blocked_assets=()):
    node_map = nodes(doc) if doc else {}
    names = d.reference_catalog(session, asset_ids)
    prompt_only = {item["id"] for item in session["ledger"]["characters"] if item.get("visual_mode") == "prompt_only"}
    names = {key: name for key, name in names.items() if key not in prompt_only}
    kinds = {item["id"]: kind for category, kind in (("characters", "role"), ("scene_assets", "scene"), ("props", "image"))
             for item in session["ledger"].get(category, [])}
    resolved, pending = {}, []
    for key, name in names.items():
        mapping = state["mappings"].get(key, {})
        node_id = mapping.get("node_id")
        if key in blocked_assets:
            pending.append({"key": key, "name": name, "reason": "asset_conflict", "node_id": node_id})
            continue
        actual_kind = kind_of(node_map[node_id]) if node_id in node_map else None
        expected_kind = kinds.get(key)
        if node_id in node_map and (actual_kind != expected_kind or mapping.get("kind", expected_kind) != expected_kind):
            pending.append({"key": key, "name": name, "reason": "node_type_changed", "node_id": node_id,
                            "expected_kind": expected_kind, "actual_kind": actual_kind})
        elif node_id in node_map and name_of(doc, node_id) != name:
            pending.append({"key": key, "name": name, "reason": "node_name_changed", "node_id": node_id,
                            "actual_name": name_of(doc, node_id)})
        elif node_id in node_map:
            resolved[name] = "<node-asset>" + node_id + "</node-asset>"
            if not ready_image(doc, node_id):
                pending.append({"key": key, "name": name, "node_id": node_id, "reason": "missing_image"})
        else:
            pending.append({"key": key, "name": name, "reason": "node_missing", "node_id": node_id})
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


def desired_videos(session, state, doc, units, bind_references=False, failures=None, blocked_assets=()):
    d.require(session["ledger"].get("schema_version") == 3, "旧项目须先审核资产去重升级方案，再同步视频及引用")
    flags = {u["id"]: bind_references or state["mappings"].get(u["id"], {}).get("references_requested",
             state["mappings"].get(u["id"], {}).get("references_bound", False)) for u in units}
    needed = set().union(*(unit_asset_ids(session, unit) for unit in units)) if units else set()
    refs, pending = reference_map(session, state, doc, needed, blocked_assets) if any(flags.values()) else ({}, [])
    # Older lookup adapters included one project-wide BLACK issue. The unit-level
    # resolver below owns that special reference now, so do not report it twice.
    pending = [row for row in pending if row.get("key") != "BLACK"]
    wanted = []
    for unit in units:
        unit_refs = dict(refs)
        black, black_kind, black_issue = black_reference(state, doc, unit["id"])
        if flags[unit["id"]]:
            if black_issue is None:
                if not edge_only_black(unit):
                    unit_refs["黑幕"] = "<node-asset>" + black + "</node-asset>"
                if not reference_ready(doc, black):
                    black_issue = {"reason": "missing_video" if black_kind == "video" else "missing_image"}
            if black_issue is not None:
                pending.append({"key": "BLACK", "name": "黑幕", "kind": black_kind, "node_id": black,
                                "unit_ids": [unit["id"]], **black_issue})
        convert = reference_renderer(unit_refs, [*d.reference_catalog(session).values(), "黑幕"])
        try:
            prompt = d.render_unit(session, unit, reference_text=convert if flags[unit["id"]] else None)
            if edge_only_black(unit):
                d.require(black_prompt_absent(prompt, black), "独立黑幕引用不能出现在提示词正文中")
        except (ValueError, KeyError, TypeError) as exc:
            if failures is None:
                raise
            failures.append({"key": unit["id"], "reason": "prompt_encoding_failed", "message": safe_message(exc)})
            continue
        reference_keys = set(unit_asset_ids(session, unit))
        if edge_only_black(unit) or "@黑幕" in prompt or unit_refs.get("黑幕") and unit_refs["黑幕"] in prompt:
            reference_keys.add("BLACK")
        wanted.append({"key": unit["id"], "name": unit.get("display_name", unit["id"]), "kind": "video", "version": unit["version"],
                       "duration": unit["duration"], "prompt": prompt, "references_bound": flags[unit["id"]],
                       "reference_keys": sorted(reference_keys), "black_node_id": black if black_issue is None or black_issue.get("reason") in ("missing_image", "missing_video") else None,
                       "black_node_kind": black_kind, "black_reference_mode": unit.get("black_reference_mode")})
    used = set().union(*(set(item["reference_keys"]) for item in wanted)) if wanted else set()
    return wanted, [p for p in pending if p["key"] in used]


def unit_asset_ids(session, unit):
    mentioned = set(unit["character_ids"] + d.scene_asset_ids(session, unit["scene_ids"]) + unit["prop_ids"])
    fields = [unit["global"]]
    for shot in unit["shots"]:
        fields.extend(shot.get(key, "") for key in (
            "text", "connector", "camera", "composition", "depth_of_field", "lighting",
            "label", "transition", "sound_foreground", "sound_environment"))
    for field in fields:
        mentioned.update(d.asset_text(session, unit, field)[1])
    prompt_only = {item["id"] for item in session["ledger"]["characters"] if item.get("visual_mode") == "prompt_only"}
    return mentioned - prompt_only


def image_reference_units(session, state, key):
    return [unit["id"] for unit in session["units"]
            if state["mappings"].get(unit["id"], {}).get("kind") == "video" and key in unit_asset_ids(session, unit)]


def queue_image_references(session, state, changed=()):
    queued = set(state.get("pending_image_references", [])) | set(changed)
    remaining = state.setdefault("pending_image_reference_units", {})
    for key in queued:
        related = image_reference_units(session, state, key)
        if key in changed or key not in remaining:
            remaining[key] = related
        else:
            remaining[key] = [unit_id for unit_id in remaining[key] if unit_id in related]
    state["pending_image_references"] = sorted(queued)


def consume_image_references(session, state, completed, pending):
    queue_image_references(session, state)
    blocked = {item["key"] for item in pending}
    remaining = state["pending_image_reference_units"]
    for key in list(state["pending_image_references"]):
        if key not in blocked:
            remaining[key] = [unit_id for unit_id in remaining[key] if unit_id not in completed]
        if not remaining[key] and key not in blocked:
            state["pending_image_references"].remove(key)
            remaining.pop(key, None)


def asset_reconciliation_plan(session, state, doc, decisions=None):
    """Resolve the ledger against one inventory, preserving unrelated/manual nodes."""
    by_name = {}
    for node_id, node in nodes(doc).items():
        by_name.setdefault(name_of(doc, node_id), []).append((node_id, kind_of(node)))
    plan, conflicts = [], []
    for item in desired_assets(session):
        key, name, kind = item["key"], item["name"], item["kind"]
        choice = (decisions or {}).get(key, {})
        if choice.get("skip") is True:
            conflicts.append({"key": key, "name": name, "reason": "asset_skipped"})
            continue
        mapping = state["mappings"].get(key, {})
        node_id = mapping.get("node_id")
        if choice.get("reuse") is not None:
            chosen = choice["reuse"]
            valid = chosen in nodes(doc) and kind_of(nodes(doc)[chosen]) == kind and name_of(doc, chosen) == name
            if not valid:
                conflicts.append({"key": key, "name": name, "node_id": chosen, "reason": "invalid_reuse_decision",
                                  "expected_kind": kind})
            elif choice.get("fingerprint") != fingerprint(doc, chosen):
                conflicts.append({"key": key, "name": name, "node_id": chosen, "reason": "reuse_fingerprint_changed",
                                  "fingerprint": fingerprint(doc, chosen)})
            else:
                same_mapping = chosen == node_id and mapping.get("kind", kind) == kind
                plan.append(dict(item, node_id=chosen, resolution="mapped" if same_mapping else "adopted"))
            continue
        if node_id in nodes(doc):
            actual_kind, actual_name = kind_of(nodes(doc)[node_id]), name_of(doc, node_id)
            if actual_kind != kind or mapping.get("kind", kind) != kind:
                conflicts.append({"key": key, "name": name, "reason": "node_type_changed", "node_id": node_id,
                                  "expected_kind": kind, "actual_kind": actual_kind})
            elif actual_name != name:
                conflicts.append({"key": key, "name": name, "reason": "node_name_changed", "node_id": node_id,
                                  "actual_name": actual_name, "fingerprint": fingerprint(doc, node_id)})
            else:
                plan.append(dict(item, node_id=node_id, resolution="mapped"))
            continue
        same_name = by_name.get(name, [])
        matches = [nid for nid, actual_kind in same_name if actual_kind == kind]
        if len(matches) == 1:
            plan.append(dict(item, node_id=matches[0], resolution="adopted"))
        elif len(matches) > 1:
            conflicts.append({"key": key, "name": name, "reason": "ambiguous_node", "matches": [
                {"node_id": nid, "fingerprint": fingerprint(doc, nid)} for nid in matches]})
        elif same_name:
            conflicts.append({"key": key, "name": name, "reason": "same_name_wrong_kind",
                              "expected_kind": kind, "matches": [{"node_id": nid, "actual_kind": actual_kind}
                                                                  for nid, actual_kind in same_name]})
        else:
            plan.append(dict(item, node_id=None, resolution="create",
                             allocated_id=node_id if mapping.get("stage") == "allocated" else None))
    return plan, conflicts


def reconcile_asset_nodes(project, cli, session, state, doc, decisions=None, preview=False, context=None):
    plan, conflicts = asset_reconciliation_plan(session, state, doc, decisions)
    summary = {"created": [], "adopted": [], "reused": [], "conflicts": conflicts,
               "plan": [{key: item[key] for key in ("key", "name", "kind", "node_id", "resolution")} for item in plan]}
    state["asset_reconciliation_conflicts"] = copy.deepcopy(conflicts)
    positions = layout_positions(doc, plan)
    for item in plan:
        key, node_id = item["key"], item.get("node_id")
        if context is not None:
            context["active"] = {"key": key, "name": item["name"], "kind": item["kind"], "node_id": node_id}
        if item["resolution"] == "create":
            if preview:
                summary["created"].append(key)
                continue
            # The current document is the initial inventory or the latest verified
            # mutation result; never run a second full inventory for name matching.
            node_id = item.get("allocated_id") or cli.allocate()
            if context is not None:
                context["active"]["node_id"] = node_id
            state["mappings"][key] = {"node_id": node_id, "kind": item["kind"], "name": item["name"], "stage": "allocated"}
            save(project, state)
            try:
                doc = write_verified(project, cli, state, "create_biz_node",
                                     {"nodeKind": item["kind"], "id": node_id, "initialData": initial_data(item), "topLeft": positions[key]},
                                     [{"action": "create", "node_id": node_id, "kind": item["kind"], "name": item["name"]}])
            except CanvasError as exc:
                if exc.code != "COMMAND_REJECTED":
                    raise
                conflict = {"key": key, "name": item["name"], "node_id": node_id,
                            "reason": "asset_create_rejected", "message": safe_message(exc)}
                conflicts.append(conflict)
                state["asset_reconciliation_conflicts"] = copy.deepcopy(conflicts)
                save(project, state)
                continue
            if context is not None:
                context["doc"] = doc
            summary["created"].append(key)
        else:
            summary["adopted" if item["resolution"] == "adopted" else "reused"].append(key)
        if item["resolution"] != "mapped" or state["mappings"][key].get("stage") != "ready":
            state["mappings"][key] = {"node_id": node_id, "kind": item["kind"], "name": item["name"],
                                      "stage": "ready", "origin": item["resolution"]}
            if item["kind"] in ("role", "scene"):
                state["mappings"][key]["appearance_name"] = content(doc, node_id).get("basicInfo", {}).get("appearanceName", "")
            if not preview:
                save(project, state)
    if not preview:
        save(project, state)
    if context is not None:
        context.pop("active", None)
    return doc, summary


def sync(project, cli, kind, request_id=None, ids=None, decisions=None, preview=False, bind_references=False,
         existing_only=False, references_only=False, session_override=None):
    """Always leave a batch report, including on a stopped/uncertain CLI write."""
    context = {"failures": []}
    try:
        result = _sync(project, cli, kind, request_id, ids, decisions, preview, bind_references,
                       existing_only, references_only, context, session_override)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        if kind == "videos" and not preview and context.get("session") and context.get("state") and context.get("doc"):
            try:
                context["doc"] = document(cli, context["state"]["binding"])
            except (ValueError, OSError, KeyError, TypeError):
                pass  # Keep the last verified snapshot; never guess that a write succeeded.
            details = getattr(exc, "details", {})
            active = dict(context.get("active", {}))
            if not active and details.get("operation"):
                entry = next((row for row in context["state"]["journal"] if row["id"] == details["operation"]), {})
                target = next(iter(entry.get("targets", [])), {})
                node_id = target.get("node_id")
                mapping = next(((key, value) for key, value in context["state"]["mappings"].items()
                                if value.get("node_id") == node_id), None)
                if mapping:
                    key, value = mapping
                    active = {"key": key, "name": value.get("name", key), "kind": value.get("kind"), "node_id": node_id}
            context["failures"].append({**active, "reason": getattr(exc, "code", "operation_failed"),
                                        "message": safe_message(exc), **details})
            try:
                report = reference_report(project, context)
                if isinstance(exc, CanvasError):
                    exc.details["reference_report"] = report.get("report")
            except (ValueError, OSError, KeyError, TypeError) as report_exc:
                if isinstance(exc, CanvasError):
                    exc.details["report_error"] = safe_message(report_exc)
        raise
    if kind == "videos" and not preview:
        report = reference_report(project, context)
        result.update(reference_report=report.get("report"), reference_issues=report.get("issues", []))
    return result


def reference_report(project, context):
    import canvas_reference_report
    state = context["state"]
    state["last_reference_failures"] = copy.deepcopy(context["failures"])
    save(project, state)
    return canvas_reference_report.write_reference_report(project, context["session"], state, context["doc"],
                                                          context.get("units", []), failures=context["failures"])


def _sync(project, cli, kind, request_id=None, ids=None, decisions=None, preview=False, bind_references=False,
          existing_only=False, references_only=False, context=None, session_override=None):
    session = copy.deepcopy(session_override) if session_override is not None else d.load(Path(project))
    d.require(session["ledger"].get("schema_version") == 3, "旧项目须先审核资产去重升级方案；当前同步不会连接画布或续接旧写入")
    state, doc = connected(project, cli)
    if preview:
        state = copy.deepcopy(state)
    context = context if context is not None else {"failures": []}
    context.update(session=session, state=state, doc=doc)
    reconcile(project, cli, state, doc, preview=preview, decisions=decisions)
    asset_summary = None
    if kind == "assets":
        desired, pending = desired_assets(session), []
    else:
        units = selected_units(session, request_id, ids)
        context["units"] = units
        if not existing_only and not references_only:
            doc, asset_summary = reconcile_asset_nodes(project, cli, session, state, doc, decisions, preview, context)
            context["doc"] = doc
            context["failures"].extend(asset_summary["conflicts"])
            doc, black_summary = reconcile_black_video(project, cli, state, doc, decisions, preview, context)
            context["doc"] = doc
            asset_summary["black_video"] = black_summary
            context["failures"].extend(black_summary["conflicts"])
            bind_references = True
        bind_references = bind_references or references_only or any(m.get("image") for m in state["mappings"].values())
        blocked_assets = {item["key"] for item in asset_summary["conflicts"]} if asset_summary else set()
        desired, pending = desired_videos(session, state, doc, units, bind_references, context["failures"], blocked_assets)
        failed_units = {row["key"] for row in context["failures"] if row.get("reason") == "prompt_encoding_failed"}
        if failed_units and not preview:
            state["pending_units"] = list(dict.fromkeys([*state["pending_units"], *sorted(failed_units)]))
            save(project, state)
    unbound = [x["key"] for x in desired if x["key"] not in state["mappings"]] if existing_only else []
    if existing_only:
        desired = [x for x in desired if x["key"] in state["mappings"]]
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
        if (mapping and node_id not in nodes(doc) and mapping.get("stage") == "allocated"
                and decisions.get(key, {}).get("recreate_missing") != node_id):
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
        if item["kind"] == "video" and not references_only:
            current = content(doc, node_id).get("generation", {}) if node_id else {}
            d.require(type(item["duration"]) is int and item["duration"] > 0, "分镜时长须为正整数秒")
            item["settings"] = {"durationMs": item["duration"] * 1000}
            changed_settings = []
            if node_id and (not mapping or mapping.get("stage") == "ready"):
                baseline = (mapping or {}).get("video_settings", {})
                value = current.get("durationMs")
                if "durationMs" in baseline and value != baseline["durationMs"] and value != item["settings"]["durationMs"]:
                    changed_settings.append("durationMs")
                if changed_settings and choice.get("overwrite_fingerprint") != fingerprint(doc, node_id):
                    conflicts.append({"key": key, "reason": "video_settings_changed", "fields": changed_settings,
                                      "node_id": node_id, "fingerprint": fingerprint(doc, node_id),
                                      "current": {f: current.get(f) for f in item["settings"]}, "desired": item["settings"]})
                    continue
        if node_id and mapping:
            changes = []
            if mapping.get("stage") != "ready":
                field_changes = video_initialization_changes(doc, node_id, mapping, item) if item["kind"] == "video" else None
                if field_changes is not None:
                    changes.extend(field_changes)
                else:
                    expected = mapping.get("initialization_fingerprint", mapping.get("adopted_fingerprint"))
                    for entry in reversed(state["journal"]):
                        if entry["status"] == "verified" and node_id in entry.get("after", {}):
                            expected = entry["after"][node_id]
                            break
                    if expected != fingerprint(doc, node_id):
                        changes.append("initialization")
            if not references_only and mapping.get("stage") == "ready":
                fields = ("name", "caption", "title") if item["kind"] == "video" else ()
                name_baseline = mapping.get("video_names", {})
                edited_name = any(content(doc, node_id).get(field) not in (
                    name_baseline.get(field, mapping.get("name")), item["name"])
                    if field in name_baseline else content(doc, node_id).get(field) not in (None, mapping.get("name"), item["name"])
                    for field in fields)
                if edited_name or name_of(doc, node_id) not in (mapping.get("name"), item["name"]):
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
        context["failures"].extend(conflicts)
        return {"status": "needs_decision" if conflicts else "planned", "count": len(plan), "conflicts": conflicts,
                "pending_references": pending, "unbound_units": unbound, "skipped": skipped,
                "asset_reconciliation": asset_summary,
                "plan": [{k: x[k] for k in ("key", "name", "kind", "node_id", "settings") if k in x} for x in plan]}
    if len(plan) >= 10:
        checkpoint(project, cli, state, "seedance batch sync")
    created, updated, reused = 0, 0, 0
    reference_completed = set()
    positions = layout_positions(doc, plan)
    for item in plan:
        key, node_id = item["key"], item.get("node_id")
        check_control(cli)
        context["active"] = {"key": key, "name": item["name"], "kind": item["kind"], "node_id": node_id}
        # Re-read before every node; detect changes since planning.
        fresh = document(cli, state["binding"])
        if node_id and fingerprint(fresh, node_id) != fingerprint(doc, node_id):
            fail("CONCURRENT_EDIT", "画布节点在操作期间发生变化，已停止。", key=key, node_id=node_id)
        reference_sources = set(node_asset_ids(item.get("prompt", "")))
        if edge_only_black(item) and item.get("references_bound") and item.get("black_node_id"):
            reference_sources.add(item["black_node_id"])
        for source in reference_sources:
            if (source not in nodes(fresh) or source not in nodes(doc)
                    or kind_of(nodes(fresh)[source]) != kind_of(nodes(doc)[source])
                    or name_of(fresh, source) != name_of(doc, source)):
                context["doc"] = fresh
                fail("CONCURRENT_EDIT", "引用资产节点在操作期间删除或改名/改类型，已停止。", key=key, node_id=source)
        doc = fresh
        context["doc"] = doc
        create_video = None
        before = fingerprint(doc, node_id) if node_id else None
        if not node_id:
            # Recheck name collisions immediately before allocating.
            if any(kind_of(n) == item["kind"] and name_of(doc, i) == item["name"] for i, n in nodes(doc).items()):
                fail("CONCURRENT_EDIT", "操作期间出现同名节点，请重新核对。", key=key)
            node_id = item.get("allocated_id") or cli.allocate()
            context["active"]["node_id"] = node_id
            state["mappings"][key] = {"node_id": node_id, "kind": item["kind"], "name": item["name"], "stage": "allocated"}
            save(project, state)
            init = {} if item["kind"] == "video" else initial_data(item)
            command = {"nodeKind": item["kind"], "id": node_id, "initialData": init, "topLeft": positions[key]}
            if item["kind"] == "video":
                create_video = command
            else:
                doc = write_verified(project, cli, state, "create_biz_node", command,
                                     [{"action": "create", "node_id": node_id, "kind": item["kind"], "name": item["name"]}])
                context["doc"] = doc
            created += 1
        else:
            reused += 1
            if key not in state["mappings"] or state["mappings"][key]["node_id"] != node_id:
                state["mappings"][key] = {"node_id": node_id, "kind": item["kind"], "name": name_of(doc, node_id), "stage": "adopted",
                                          "adopted_fingerprint": fingerprint(doc, node_id)}
                save(project, state)
        if item["kind"] == "video":
            if (not references_only and node_id in nodes(doc)
                    and decisions.get(key, {}).get("overwrite_fingerprint") == fingerprint(doc, node_id)):
                # The user accepted these exact current fields. Rebase only
                # after the fresh snapshot still matches that authorization.
                state["mappings"][key].pop("video_initialization_baseline", None)
            if item.get("black_node_id") is not None and item["references_bound"]:
                state["mappings"][key].update(black_node_id=item["black_node_id"], black_node_kind=item["black_node_kind"])
                save(project, state)
            item["unresolved_references"] = any(row.get("reason") not in ("missing_image", "missing_video") for row in pending
                                                if row["key"] in item["reference_keys"] and (not row.get("unit_ids") or key in row["unit_ids"]))
            doc = write_video(project, cli, state, doc, item, node_id, context,
                              create=create_video, references_only=references_only)
        else:
            before = fingerprint(doc, node_id)
            doc = set_name(project, cli, state, doc, node_id, item["name"])
        context["doc"] = doc
        if item["kind"] in ("role", "scene"):
            field = "appearanceName" if item["kind"] == "role" else "sceneViewName"
            if content(doc, node_id).get("basicInfo", {}).get("appearanceName") != "":
                doc = write_verified(project, cli, state, "xyq." + item["kind"] + ".updateDescription",
                                     {"nodeId": node_id, "assetId": node_id, "field": field, "value": ""},
                                     [{"action": "field", "node_id": node_id, "value": ""}])
                context["doc"] = doc
        if item["kind"] != "video" and "prompt" in item and (prompt_of(doc, node_id) != item["prompt"]
                                 or not prompt_references_satisfied(doc, node_id, item["prompt"])):
            try:
                doc = repair_role_reference_rows(project, cli, state, doc, node_id, item["prompt"])
                context["doc"] = doc
                doc = write_verified(project, cli, state, "xyq.generation.update_prompt", {"nodeId": node_id, "prompt": item["prompt"]},
                                     [{"action": "prompt_text", "node_id": node_id, "value": item["prompt"]}])
                context["doc"] = doc
            except CanvasError as exc:
                if exc.code != "COMMAND_REJECTED":
                    raise
                context["failures"].append({"key": key, "node_id": node_id, "reason": "prompt_write_rejected",
                                            "message": safe_message(exc)})
                state["pending_units"] = list(dict.fromkeys([*state["pending_units"], key]))
                save(project, state)
                continue
        updated += fingerprint(doc, node_id) != before
        mapping = state["mappings"][key]
        mapping["stage"] = "ready"
        mapping.pop("initialization_fingerprint", None)
        mapping.pop("video_initialization_baseline", None)
        if not references_only:
            mapping["name"] = item["name"]
            if item["kind"] == "video":
                mapping["video_names"] = {field: item["name"] for field in ("name", "caption", "title")}
        if item["kind"] in ("role", "scene"):
            mapping["appearance_name"] = ""
        if "prompt" in item:
            text_verified = prompt_of(doc, node_id) == item["prompt"]
            d.require(text_verified, "提示词文字回读尚未通过，不能标记同步完成")
            references_verified = video_references_satisfied(doc, node_id, item)
            unit_pending = [row for row in pending if row["key"] in item["reference_keys"] and (not row.get("unit_ids") or key in row["unit_ids"])]
            unresolved = any(row.get("reason") not in ("missing_image", "missing_video") for row in unit_pending)
            references_verified = references_verified and not unresolved
            mapping.update(prompt_hash=digest(item["prompt"]), version=item["version"],
                           references_requested=item["references_bound"],
                           references_bound=item["references_bound"] and references_verified)
            if item["references_bound"] and not references_verified:
                context["failures"].append({"key": key, "node_id": node_id, "reason": "reference_failed",
                                            "message": "提示词文字已保存，部分资产节点引用未通过回读。"})
            if item["references_bound"] and references_verified:
                reference_completed.add(key)
            if item.get("settings"):
                mapping["video_settings"] = item["settings"]
            if not references_only and (not item["references_bound"] or references_verified):
                state["pending_units"] = [x for x in state["pending_units"] if x != key]
            elif not references_only:
                state["pending_units"] = list(dict.fromkeys([*state["pending_units"], key]))
            for marker_path in ([] if references_only or item["references_bound"] and not references_verified else (Path(project) / "canvas" / "pending-sync").glob("*.json")):
                marker = d.read_json(marker_path)
                if marker["units"].get(key, 0) <= item["version"]:
                    marker["units"].pop(key, None)
                    if marker["units"]:
                        d.write_json(marker_path, marker)
                    else:
                        marker_path.unlink()
        save(project, state)
    context.pop("active", None)
    if kind == "videos":
        consume_image_references(session, state, reference_completed, pending)
        save(project, state)
    return {"status": "partial" if context["failures"] else "synced", "created": created, "updated": updated, "reused": reused,
            "pending_references": pending, "unbound_units": unbound, "skipped": skipped, "binding": state["binding"],
            "failures": context["failures"], "asset_reconciliation": asset_summary,
            "video_write_receipts": context.get("video_write_receipts", [])}


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
                return value in selected or bool(selected.intersection(node_asset_ids(value)))
            return False
        if contains(content(doc, node_id)):
            affected.add(node_id)
    return sorted(affected)


def manage(project, cli, action, request, preview=False):
    state, doc = connected(project, cli)
    reconcile(project, cli, state, doc)
    known_deleted = {t["node_id"] for e in state["journal"] if e["status"] == "verified"
                     for t in e["targets"] if t["action"] == "delete"} if action == "delete" else set()
    if action == "delete" and not preview:
        d.require(isinstance(request.get("targets"), list) and request["targets"] and all(
            isinstance(target, dict) and isinstance(target.get("id"), str) and target["id"]
            for target in request["targets"]), "删除执行须使用预览确定的确切节点ID")
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
    if action == "delete":
        requested_ids = {target["id"] for target in request["targets"]}
        d.require(isinstance(expected, dict) and set(selected) <= set(expected) <= requested_ids,
                  "删除执行须携带全部当前目标的预览摘要，不得遗漏或夹带其他节点")
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
                state.pop("black_node_kind", None)
                state.pop("black_video_preparation", None)
            if state.get("legacy_black_node_id") == node_id:
                state.pop("legacy_black_node_id", None)
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
            plain = [i for i in wanted if not state["mappings"][i].get("references_requested",
                     state["mappings"][i].get("references_bound"))]
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
                    d.require(args.node_id in nodes(doc) and kind_of(nodes(doc)[args.node_id]) == "video", "黑幕须指定现有视频节点ID")
                    d.require(name_of(doc, args.node_id) == "黑幕", "黑幕视频节点完整名称须为黑幕")
                    preserve_legacy_black_bindings(state, doc)
                    state["black_node_id"] = args.node_id
                    state["black_node_kind"] = "video"
                    state["black_video_preparation"] = {"node_id": args.node_id, "stage": "ready"}
                    save(args.project, state)
                    result = {"status": "black_bound", "node_id": args.node_id}
                else:
                    result = {"status": "connected", "binding": state["binding"], "pending_units": state["pending_units"],
                              "pending_image_references": state.get("pending_image_references", []),
                              "pending_image_reference_units": state.get("pending_image_reference_units", {}),
                              "unfinished_uploads": [{"file_hash": sha, **entry} for sha, entry in state.get("uploads", {}).items()
                                                     if entry.get("status") != "ready"],
                              "unconfirmed_operations": [{"id": e["id"], "command": e["command"], "status": e["status"]}
                                                         for e in state["journal"] if e["status"] in ("pending", "uncertain", "acknowledged")],
                              "nodes": [{"id": i, "kind": kind_of(n), "name": name_of(doc, i), "fingerprint": fingerprint(doc, i)} for i, n in nodes(doc).items()]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "code": getattr(exc, "code", "ERROR"), "message": safe_message(exc),
                          **getattr(exc, "details", {})}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
