#!/usr/bin/env python3
"""Durable, local job spool for the official Canvas CLI. One writer per canvas."""
import argparse
import base64
import contextlib
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import canvas as c
import director as d


class PauseRequested(BaseException):
    """Stop before a new operation, without classifying it as an unknown write."""


class Superseded(BaseException):
    """A newer authorized manifest replaces this job at an operation boundary."""


def directory(project):
    return Path(project) / "canvas" / "jobs"


def read(path, default=None):
    return d.read_json(path) if path.exists() else copy.deepcopy(default)


class FileLock:
    """OS-owned lock: released by the OS even when the worker is killed."""
    def __init__(self, path):
        self.path, self.handle = Path(path), None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.fstat(self.handle.fileno()).st_size == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            c.fail("BUSY", "该画布已有后台同步进程。")
        return self

    def release(self):
        if self.handle:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


@contextlib.contextmanager
def metadata_lock(project):
    lock = FileLock(directory(project) / "metadata.lock")
    deadline = time.monotonic() + 10
    while True:
        try:
            lock.acquire()
            break
        except c.CanvasError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.05)
    try:
        yield
    finally:
        lock.release()


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied is not evidence of death.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def worker_active(project):
    value = read(directory(project) / "worker.json", {})
    return value.get("status") in ("starting", "running") and alive(value.get("pid"))


def manifests(project):
    return sorted((read(p) for p in directory(project).glob("job-*.json")
                   if not p.name.endswith(".state.json")), key=lambda x: (x["created_at"], x["job_id"]))


def state_file(project, job_id):
    d.require(isinstance(job_id, str) and job_id.startswith("job-") and job_id[4:].isalnum(), "无效任务ID")
    return directory(project) / (job_id + ".state.json")


def job_state(project, job):
    value = read(state_file(project, job["job_id"]), {"job_id": job["job_id"], "status": "queued", "completed": 0})
    # Old jobs gain derived fields in the returned view only; never rewrite history.
    if "total" not in value:
        value["total"] = job_total(job)
    value.setdefault("stage", value["status"])
    return value


def job_total(job):
    if job["kind"] in ("videos", "references"):
        return len(job["options"]["ids"])
    if job["kind"] == "images":
        # Old manifests did not freeze the eligible target count. Do not infer
        # it from all files or rescan today's changed/missing folder as history.
        return job["options"].get("image_target_count")
    ledger = job["session"]["ledger"]
    return (sum(item.get("visual_mode") != "prompt_only" for item in ledger.get("characters", []))
            + len(ledger.get("scene_assets", [])) + len(ledger.get("props", [])))


def image_inventory(folder):
    folder = Path(folder).resolve()
    d.require(folder.is_dir(), "图片文件夹不存在")
    return {str(p.relative_to(folder)): d.file_hash(p) for p in sorted(folder.rglob("*"))
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")}


def input_scope(session, kind, selected_ids):
    # Appending a new range does not invalidate an already frozen sync job.
    scope = {k: session.get(k) for k in ("files", "drafts", "style_hash", "ledger", "video_style")}
    if kind in ("videos", "references"):
        units = {u["id"]: u for u in session["units"]}
        d.require(all(i in units for i in selected_ids), "任务分镜已被移除")
        scope["units"] = [units[i] for i in selected_ids]
    return scope


def enqueue(project, kind, request_id=None, ids=None, folder=None, decisions=None,
            start=False, existing_only=False, references_only=False):
    project = Path(project).resolve()
    d.require(kind in ("assets", "videos", "references", "images"), "不支持的后台任务类型")
    d.require(project != d.ROOT and d.ROOT not in project.parents, "项目不能位于Skill安装目录")
    session, canvas_state = d.load(project), c.read_state(project)
    binding = canvas_state.get("binding")
    d.require(binding, "先配置CLI并绑定指定画布，再提交后台任务")
    selected = [u["id"] for u in c.selected_units(session, request_id, ids)] if kind in ("videos", "references") else []
    options = {"request_id": request_id, "ids": selected, "decisions": decisions or {},
               "existing_only": existing_only or kind == "references", "references_only": references_only or kind == "references"}
    if kind == "images":
        d.require(folder is not None, "上传任务需要明确的图片文件夹")
        options.update(folder=str(Path(folder).resolve()), images=image_inventory(folder))
        matched = c.media.scan_images(session, Path(options["folder"]))["matched"]
        options["image_target_count"] = sum(not options["decisions"].get(item["key"], {}).get("skip") for item in matched)
    scope = input_scope(session, kind, selected)
    input_digest = c.digest(scope)
    identity = c.digest({"binding": binding, "kind": kind, "input_digest": input_digest, "options": options,
                         "asset_contract": c.ASSET_CONTRACT})
    job_id = "job-" + identity[:32]
    with metadata_lock(project):
        path = directory(project) / (job_id + ".json")
        if request_id:
            previous = [j for j in manifests(project) if j["kind"] == kind
                        and j["options"].get("request_id") == request_id]
            d.require(all(j["input_digest"] == input_digest and j["options"]["ids"] == selected for j in previous),
                      "request_id已用于不同任务内容，请使用新提交ID")
        reused = path.exists()
        if not reused:
            previous_jobs = manifests(project)
            dependencies = [j["job_id"] for j in previous_jobs if j["kind"] == "assets"
                            and j["binding"] == binding
                            and job_state(project, j)["status"] != "completed"] if kind != "assets" else []
            supersedes = [j["job_id"] for j in previous_jobs if j["kind"] == kind and j["binding"] == binding
                          and j["options"]["ids"] == selected and j["options"].get("folder") == options.get("folder")
                          and job_state(project, j)["status"] != "completed"]
            job = {"schema": 1, "asset_contract": c.ASSET_CONTRACT, "job_id": job_id, "created_at": time.time_ns(), "kind": kind,
                   "binding": binding, "input_digest": input_digest, "options": options,
                   "dependencies": dependencies, "supersedes": supersedes, "session": copy.deepcopy(session)}
            job["manifest_digest"] = c.digest(job)
            d.write_json(path, job)
            d.write_json(state_file(project, job_id), {"job_id": job_id, "status": "queued", "stage": "queued",
                                                      "completed": 0, "total": job_total(job)})
        result = {"job_id": job_id, "status": job_state(project, read(path))["status"], "reused": reused}
    if start:
        result["worker"] = start_worker(project)
    return result


def validate_job(project, job):
    payload = {k: v for k, v in job.items() if k != "manifest_digest"}
    d.require(c.digest(payload) == job["manifest_digest"], "任务JSON被修改，请重新提交任务")
    d.require(job.get("asset_contract") == c.ASSET_CONTRACT,
              "任务冻结的画布字段契约与当前版本不同；保留原任务和暂停状态，请核对后重新提交，不执行旧任务。")
    d.require(c.read_state(project).get("binding") == job["binding"], "画布绑定已变化，停止旧任务")
    session = d.load(Path(project))
    d.require(c.digest(input_scope(session, job["kind"], job["options"]["ids"])) == job["input_digest"],
              "任务来源、台账、风格或选中分镜已变化，请重新提交")
    if job["kind"] == "images":
        d.require(image_inventory(job["options"]["folder"]) == job["options"]["images"], "待上传图片已变化，请重新提交")
    return session


def file_stamp(path):
    """Cheap change signal only; content hashes remain authoritative at boundaries."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class JobInputGuard:
    """Reuse unchanged local verification between full node/group boundary checks."""
    def __init__(self, project, job):
        self.project, self.job = Path(project), job
        self.paths, self.stamps = [], None

    def watched_paths(self):
        session = self.job["session"]
        paths = [self.project / "session.json", self.project / "video-style.txt"]
        files = session.get("files", {})
        paths.extend(Path(row["path"]) for row in files.values())
        if "source" in files:
            scenes = Path(files["source"]["path"]).parent / "scenes"
            paths.extend([scenes, *scenes.glob("*.txt")])
        if self.job["kind"] == "images":
            folder = Path(self.job["options"]["folder"])
            # Watching directories detects added/renamed files without rehashing
            # the entire image library at every pause/CLI control check.
            paths.append(folder)
            paths.extend(path for path in folder.rglob("*") if path.is_dir()
                         or path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))
        return list(dict.fromkeys(paths))

    def signature(self):
        return {str(path): file_stamp(path) for path in self.paths}

    def check(self, force=False):
        if not force and self.stamps is not None and self.signature() == self.stamps:
            return
        self.paths = self.watched_paths()
        before = self.signature()
        validate_job(self.project, self.job)
        after = self.signature()
        d.require(before == after, "任务输入在校验期间变化；停止并按当前内容重新提交")
        self.stamps = after


def event_id(event):
    """Derive stable IDs for legacy events without rewriting their history."""
    return event.get("event_id") or "event-" + c.digest([
        event["job_id"], event.get("event", "completed"),
        event.get("signature"), event.get("created_at")])[:32]


def current_events(items, events, binding, control):
    """Only the current target's immutable manifests can identify old events."""
    by_id = {job["job_id"]: job for job in items if job["binding"] == binding}
    reports = control.get("event_reports", {})
    result = []
    for event in events:
        job = by_id.get(event.get("job_id"))
        if not job or event.get("binding", job["binding"]) != binding:
            continue
        identifier = event_id(event)
        result.append({**event, "event_id": identifier, "binding": job["binding"],
                       "request_id": job["options"].get("request_id"),
                       "unit_ids": job["options"].get("ids", []),
                       "reported": identifier in reports})
    return result


def status(project):
    project = Path(project)
    items = manifests(project)
    jobs = [{**job_state(project, j), "kind": j["kind"], "dependencies": j["dependencies"]} for j in items]
    active = worker_active(project)
    for item in jobs:
        if item["status"] == "running" and not active:
            item["status"] = "interrupted"
    binding = c.read_state(project).get("binding")
    current_asset_ids = {j["job_id"] for j in items if j["kind"] == "assets" and j["binding"] == binding}
    assets = [j for j in jobs if j["job_id"] in current_asset_ids]
    latest = assets[-1] if assets else None
    control = read(directory(project) / "control.json", {})
    events = current_events(items, read(directory(project) / "events.json", []), binding, control)
    choice = control.get("image_choice", {})
    ready = bool(latest and latest["status"] == "completed")
    if ready:
        # Completion belongs to the frozen target and input. Rebinding a canvas
        # or changing the current ledger must not unlock an old upload menu.
        latest_manifest = next(j for j in items if j["job_id"] == latest["job_id"])
        try:
            validate_job(project, latest_manifest)
        except (ValueError, OSError, KeyError, TypeError):
            ready = False
    states = {row["job_id"]: row for row in jobs}
    # A resolved/replaced failure remains history, but is not a fresh warning.
    latest_events = {event["job_id"]: event["event_id"] for event in events}
    pending_events = [event for event in events if not event["reported"]
                      and latest_events[event["job_id"]] == event["event_id"]
                      and states[event["job_id"]]["status"] == event.get("event", "completed")]
    video_followups = []
    handled = control.get("video_followups", {})
    by_id = {job["job_id"]: job for job in items}
    for event in events:
        if (event["kind"] != "videos" or event.get("event", "completed") != "completed"
                or states[event["job_id"]]["status"] != "completed"
                or event["job_id"] in handled):
            continue
        try:
            validate_job(project, by_id[event["job_id"]])
        except (ValueError, OSError, KeyError, TypeError):
            continue
        video_followups.append({"job_id": event["job_id"], "event_id": event["event_id"],
                                "request_id": event["request_id"], "unit_ids": event["unit_ids"],
                                "completion_reported": event["reported"]})
    return {"jobs": jobs, "worker_active": active, "paused": control.get("paused", False),
            "assets_ready": ready, "image_choice_available": ready and choice.get("asset_job_id") != latest["job_id"],
            "asset_job_id": latest["job_id"] if latest else None,
            "events": events, "pending_events": pending_events,
            "video_followups": video_followups, "video_followup_available": bool(video_followups)}


def report_event(project, identifier):
    """Called by the foreground only after it actually reports this event."""
    with metadata_lock(project):
        current = status(project)
        event = next((row for row in current["events"] if row["event_id"] == identifier), None)
        d.require(event is not None, "事件不存在或不属于当前画布，不能标记已报告")
        if event["reported"]:
            return {"status": "already_reported", "event_id": identifier}
        d.require(any(row["event_id"] == identifier for row in current["pending_events"]),
                  "事件已解决或被替代，请读取当前待报告事件")
        control = read(directory(project) / "control.json", {})
        control.setdefault("event_reports", {})[identifier] = {"job_id": event["job_id"],
            "binding": event["binding"], "reported_at": time.time()}
        d.write_json(directory(project) / "control.json", control)
    return {"status": "reported", "event_id": identifier}


def video_followup(project, job_id, action):
    """Record an offered/handled next step; never dispatch work or change phase."""
    d.require(action in ("shown", "continue", "modify", "finish", "already-directed"), "无效后续入口处理方式")
    with metadata_lock(project):
        current = status(project)
        event = next((row for row in current["events"] if row["job_id"] == job_id
                      and row["kind"] == "videos" and row.get("event", "completed") == "completed"), None)
        d.require(event is not None and event["reported"], "先向用户报告本批视频节点的完整核验结果")
        job = next(row for row in manifests(project) if row["job_id"] == job_id)
        d.require(job_state(project, job)["status"] == "completed", "视频任务尚未完整核验")
        validate_job(project, job)
        control = read(directory(project) / "control.json", {})
        followups = control.setdefault("video_followups", {})
        previous = followups.get(job_id)
        if previous and (previous["action"] == action or action == "shown"):
            return {"status": "already_handled", "job_id": job_id, "action": previous["action"]}
        d.require(not previous or previous["action"] == "shown", "本批后续入口已处理；新请求直接按用户指示执行")
        followups[job_id] = {"action": action, "binding": event["binding"], "updated_at": time.time()}
        d.write_json(directory(project) / "control.json", control)
    return {"status": "shown" if action == "shown" else "handled", "job_id": job_id, "action": action}


def pause(project):
    with metadata_lock(project):
        control = read(directory(project) / "control.json", {})
        control.update(paused=True)
        d.write_json(directory(project) / "control.json", control)
    return {"status": "pause_requested", "message": "停止派发新操作；正在提交的请求保留结果后暂停。"}


def resume(project, start=True):
    with metadata_lock(project):
        d.require(not worker_active(project), "后台任务尚在结束当前请求，请稍后恢复")
        control = read(directory(project) / "control.json", {})
        control["paused"] = False
        d.write_json(directory(project) / "control.json", control)
        for job in manifests(project):
            state = job_state(project, job)
            if state["status"] in ("paused", "running", "needs_attention", "blocked"):
                state.update(status="queued")
                d.write_json(state_file(project, job["job_id"]), state)
    return start_worker(project) if start else {"status": "queued"}


def recover_assets(project, operation_id, apply=False, fingerprint=None, cli=None):
    """Keep pause unchanged; serialize official readback and local journal repair."""
    from canvas_job_backend import recover_asset_rejection
    binding = c.read_state(project).get("binding")
    d.require(binding, "恢复需要原画布绑定")
    guard = FileLock(Path(tempfile.gettempdir()) / ("seedance-worker-" + c.digest([binding["account_id"], binding["canvas_id"]]) + ".lock"))
    guard.acquire()
    try:
        d.require(not worker_active(project), "后台进程尚未停止，不能恢复日志")
        with c.project_lock(project):
            return recover_asset_rejection(project, cli or c.CLI(project), operation_id, apply, fingerprint)
    finally:
        guard.release()


def image_choice(project, choice):
    d.require(str(choice) in ("1", "2"), "请选择上传或暂不上传")
    with metadata_lock(project):
        current = status(project)
        d.require(current["assets_ready"], "资产同步尚未完整核验，上传菜单暂不可用")
        control = read(directory(project) / "control.json", {})
        control["image_choice"] = {"asset_job_id": current["asset_job_id"], "choice": str(choice)}
        d.write_json(directory(project) / "control.json", control)
    return {"status": "upload_pending" if str(choice) == "1" else "skipped"}


def start_worker(project):
    project = Path(project).resolve()
    with metadata_lock(project):
        if read(directory(project) / "control.json", {}).get("paused"):
            return {"status": "paused"}
        if worker_active(project):
            return {"status": "already_running"}
        log = (directory(project) / "worker.log").open("ab")
        options = {"cwd": str(project), "stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
                   "env": {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        try:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker", "--project", str(project)], **options)
        finally:
            log.close()
        d.write_json(directory(project) / "worker.json", {"pid": process.pid, "status": "starting"})
    return {"status": "started", "pid": process.pid}


def notify_desktop(text, warning=False):
    # Starting a helper is not evidence that a popup was shown or read.
    if os.name != "nt":
        return {"status": "not_attempted", "reason": "unsupported_platform"}
    attempted_at = time.time()
    script = "$w=New-Object -ComObject WScript.Shell; $null=$w.Popup('" + text.replace("'", "''") + "',8,'小云雀同步'," + ("48" if warning else "64") + ")"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        process = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                   creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {"status": "start_failed", "attempted_at": attempted_at, "error": c.safe_message(exc)}
    return {"status": "started_result_unknown", "attempted_at": attempted_at, "pid": process.pid}


def notify_completion(text):
    return notify_desktop(text)


def notify_attention(text):
    return notify_desktop(text, warning=True)


def publish_event(project, job, state):
    """Durable, deduplicated failure notifications never imply completion."""
    event = state["status"]
    if event not in ("completed", "needs_attention"):
        return
    events = read(directory(project) / "events.json", [])
    count = state.get("completed", 0)
    label = {"assets": "资产节点", "videos": "视频节点", "references": "视频引用", "images": "图片挂载"}[job["kind"]]
    if event == "completed":
        if any(e["job_id"] == job["job_id"] and e.get("event", "completed") == event for e in events):
            return
        message = f"已完成{count}个{label}的同步核验"
        payload = {}
    else:
        error = state.get("error") or {"code": state.get("result", {}).get("status", "INCOMPLETE"),
                                      "message": "同步目标尚未全部核验，请查看待处理项。"}
        payload = {"error": error, "total": state.get("total"),
                   "conflicts": state.get("result", {}).get("conflicts", [])}
        signature = c.digest([job["job_id"], count, payload])
        if any(e.get("signature") == signature for e in events):
            return
        payload["signature"] = signature
        target = ("，节点" + error["node_id"]) if error.get("node_id") else ""
        command = ("，命令" + error["command"]) if error.get("command") else ""
        message = f"{label}同步需要处理，已核验{count}/{state.get('total', '?')}项{target}{command}：" + c.safe_message(error.get("message", error.get("code", "")))
    published = {"job_id": job["job_id"], "kind": job["kind"], "event": event,
                 "count": count, "total": state.get("total"), "binding": job["binding"],
                 "message": message, "created_at": time.time(), **payload}
    published["event_id"] = event_id(published)
    published["desktop_notification"] = {"status": "attempt_outcome_unknown"}
    events.append(published)
    d.write_json(directory(project) / "events.json", events)
    if job["binding"] != c.read_state(project).get("binding"):
        notification = {"status": "not_attempted", "reason": "non_current_canvas"}
    else:
        try:
            notification = (notify_completion if event == "completed" else notify_attention)(message)
        except Exception as exc:
            # Notification failures must not reclassify completed remote work.
            notification = {"status": "attempt_outcome_unknown", "error": c.safe_message(exc)}
    published["desktop_notification"] = (notification if isinstance(notification, dict)
                                          else {"status": "attempt_outcome_unknown"})
    d.write_json(directory(project) / "events.json", events)


def recover_dead_operation_locks(project):
    binding = c.read_state(project).get("binding")
    paths = [Path(project) / "canvas" / "operation.lock"]
    if binding:
        paths.append(Path(tempfile.gettempdir()) / ("seedance-canvas-" + c.digest([binding["account_id"], binding["canvas_id"]]) + ".lock"))
    for path in paths:
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            d.require(raw.strip().isdigit(), "旧操作锁格式异常，需人工检查")
            if not alive(int(raw)) and path.read_text(encoding="utf-8") == raw:
                path.unlink()  # Only a provably dead PID; journal recovery still runs before writes.


def run_job(project, manifest, check_control, progress):
    from canvas_job_backend import run_job as backend
    return backend(project, manifest, check_control, progress)


def dependency_complete(job_id, states, seen=()):
    if job_id in seen:
        return False
    state = states.get(job_id, {})
    if state.get("status") == "completed":
        return True
    if state.get("status") == "superseded" and state.get("replacement_id"):
        return dependency_complete(state["replacement_id"], states, (*seen, job_id))
    return False


def run_worker(project, runner=None):
    project = Path(project).resolve()
    binding = c.read_state(project).get("binding")
    d.require(binding, "后台同步需要画布绑定")
    guard = FileLock(Path(tempfile.gettempdir()) / ("seedance-worker-" + c.digest([binding["account_id"], binding["canvas_id"]]) + ".lock"))
    guard.acquire()
    runner = runner or run_job
    try:
        with metadata_lock(project):
            d.write_json(directory(project) / "worker.json", {"pid": os.getpid(), "status": "running"})
        while True:
            with metadata_lock(project):
                if c.read_state(project).get("binding") != binding:
                    d.write_json(directory(project) / "worker.json", {
                        "pid": os.getpid(), "status": "stopped", "reason": "CANVAS_BINDING_CHANGED"})
                    guard.release()
                    return {**status(project), "worker_stop_reason": "CANVAS_BINDING_CHANGED"}
                control = read(directory(project) / "control.json", {})
                all_jobs = manifests(project)
                states = {j["job_id"]: job_state(project, j) for j in all_jobs}
                current_jobs = [j for j in all_jobs if j["binding"] == binding]
                current_ids = {j["job_id"] for j in current_jobs}
                for old in all_jobs:
                    old_state = states[old["job_id"]]
                    if old["job_id"] not in current_ids and old_state["status"] == "queued":
                        old_state.update(status="needs_attention", error={"code": "CANVAS_BINDING_CHANGED",
                            "message": "任务所属画布已不是当前绑定；保留任务，未执行远端操作。"})
                        d.write_json(state_file(project, old["job_id"]), old_state)
                        publish_event(project, old, old_state)
                for newer in current_jobs:
                    for old_id in newer.get("supersedes", []):
                        if old_id in current_ids and states[old_id]["status"] != "completed":
                            states[old_id].update(status="superseded", replacement_id=newer["job_id"])
                            d.write_json(state_file(project, old_id), states[old_id])
                queued = [j for j in current_jobs if states[j["job_id"]]["status"] == "queued"]
                job = next((j for j in queued if all(dependency_complete(dep, states) for dep in j["dependencies"])), None)
                if control.get("paused") or not job:
                    d.write_json(directory(project) / "worker.json", {"pid": os.getpid(), "status": "stopped"})
                    guard.release()
                    return status(project)
                state = states[job["job_id"]]
                state.update(status="running", started_at=time.time(), error=None)
                d.write_json(state_file(project, job["job_id"]), state)

            input_guard = JobInputGuard(project, job)
            manifest_cache = {}

            def check_control(force=False):
                if c.read_state(project).get("binding") != binding:
                    c.fail("CANVAS_BINDING_CHANGED", "后台进程所持画布锁与当前绑定不同，请重新启动后台同步。")
                if read(directory(project) / "control.json", {}).get("paused"):
                    raise PauseRequested()
                for path in directory(project).glob("job-*.json"):
                    if path.name.endswith(".state.json"):
                        continue
                    stamp = file_stamp(path)
                    cached_stamp, cached = manifest_cache.get(path, (None, None))
                    if cached is None or cached_stamp != stamp:
                        cached = read(path)
                        manifest_cache[path] = (stamp, cached)
                    if job["job_id"] in cached.get("supersedes", []):
                        raise Superseded()
                input_guard.check(force=force)

            check_control.boundary = lambda: check_control(force=True)

            def progress(value):
                if value.get("stage") in ("verifying_node", "node_verified", "node_needs_attention"):
                    check_control.boundary()
                state.update(value)
                state["status"] = "running"
                state["updated_at"] = time.time()
                d.write_json(state_file(project, job["job_id"]), state)

            try:
                check_control()
                recover_dead_operation_locks(project)
                with c.project_lock(project):
                    check_control()
                    result = runner(project, job, check_control, progress)
                state.update(result=result, status="completed" if result.get("status") == "synced" else "needs_attention")
            except PauseRequested:
                state["status"] = "paused"
            except Superseded:
                state["status"] = "superseded"
            except (ValueError, OSError, KeyError, TypeError) as exc:
                state.update(status="needs_attention", error={"code": getattr(exc, "code", "ERROR"), "message": c.safe_message(exc)})
                state["error"].update({key: value for key, value in getattr(exc, "details", {}).items()
                                      if key in ("operation", "node_id", "key", "command") and isinstance(value, str)})
            state["finished_at"] = time.time()
            state["stage"] = state["status"]
            with metadata_lock(project):
                d.write_json(state_file(project, job["job_id"]), state)
                publish_event(project, job, state)
            if state["status"] not in ("completed", "superseded"):
                with metadata_lock(project):
                    d.write_json(directory(project) / "worker.json", {"pid": os.getpid(), "status": "stopped"})
                    guard.release()
                    return status(project)  # No later writes following an uncertain or rejected operation.
    finally:
        if guard.handle is not None:
            with metadata_lock(project):
                d.write_json(directory(project) / "worker.json", {"pid": os.getpid(), "status": "stopped"})
                guard.release()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("enqueue", "start", "worker", "status", "pause", "resume", "image-choice", "recover-assets", "report-event", "video-followup"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--kind", choices=("assets", "videos", "references", "images"))
    parser.add_argument("--request-id")
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--choice", choices=("1", "2"))
    parser.add_argument("--event-id")
    parser.add_argument("--job-id")
    parser.add_argument("--action", choices=("shown", "continue", "modify", "finish", "already-directed"))
    parser.add_argument("--operation-id")
    parser.add_argument("--fingerprint")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "enqueue":
            result = enqueue(args.project, args.kind, args.request_id, args.ids, args.folder,
                             read(args.decisions) if args.decisions else None, args.start)
        elif args.command == "recover-assets":
            d.require(args.operation_id, "恢复必须指定原操作ID")
            result = recover_assets(args.project, args.operation_id, args.apply, args.fingerprint)
        elif args.command == "image-choice":
            result = image_choice(args.project, args.choice)
        elif args.command == "report-event":
            d.require(args.event_id, "必须指定已向用户报告的事件ID")
            result = report_event(args.project, args.event_id)
        elif args.command == "video-followup":
            d.require(args.job_id and args.action, "必须指定视频任务ID及实际后续处理方式")
            result = video_followup(args.project, args.job_id, args.action)
        else:
            function = {"start": start_worker, "worker": run_worker, "status": status, "pause": pause, "resume": resume}[args.command]
            result = function(args.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "code": getattr(exc, "code", "ERROR"), "message": c.safe_message(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
