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
    return read(state_file(project, job["job_id"]), {"job_id": job["job_id"], "status": "queued", "completed": 0})


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
    scope = input_scope(session, kind, selected)
    input_digest = c.digest(scope)
    identity = c.digest({"binding": binding, "kind": kind, "input_digest": input_digest, "options": options})
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
            job = {"schema": 1, "job_id": job_id, "created_at": time.time_ns(), "kind": kind,
                   "binding": binding, "input_digest": input_digest, "options": options,
                   "dependencies": dependencies, "supersedes": supersedes, "session": copy.deepcopy(session)}
            job["manifest_digest"] = c.digest(job)
            d.write_json(path, job)
            d.write_json(state_file(project, job_id), {"job_id": job_id, "status": "queued", "completed": 0})
        result = {"job_id": job_id, "status": job_state(project, read(path))["status"], "reused": reused}
    if start:
        result["worker"] = start_worker(project)
    return result


def validate_job(project, job):
    payload = {k: v for k, v in job.items() if k != "manifest_digest"}
    d.require(c.digest(payload) == job["manifest_digest"], "任务JSON被修改，请重新提交任务")
    d.require(c.read_state(project).get("binding") == job["binding"], "画布绑定已变化，停止旧任务")
    session = d.load(Path(project))
    d.require(c.digest(input_scope(session, job["kind"], job["options"]["ids"])) == job["input_digest"],
              "任务来源、台账、风格或选中分镜已变化，请重新提交")
    if job["kind"] == "images":
        d.require(image_inventory(job["options"]["folder"]) == job["options"]["images"], "待上传图片已变化，请重新提交")


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
    events = read(directory(project) / "events.json", [])
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
    return {"jobs": jobs, "worker_active": active, "paused": control.get("paused", False),
            "assets_ready": ready, "image_choice_available": ready and choice.get("asset_job_id") != latest["job_id"],
            "asset_job_id": latest["job_id"] if latest else None,
            "events": events}


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


def notify_completion(text):
    # Best effort desktop notification. Durable events remain authoritative.
    if os.name != "nt":
        return
    script = "$w=New-Object -ComObject WScript.Shell; $null=$w.Popup('" + text.replace("'", "''") + "',8,'小云雀同步',64)"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                         creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


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

            def check_control():
                if c.read_state(project).get("binding") != binding:
                    c.fail("CANVAS_BINDING_CHANGED", "后台进程所持画布锁与当前绑定不同，请重新启动后台同步。")
                if read(directory(project) / "control.json", {}).get("paused"):
                    raise PauseRequested()
                if any(job["job_id"] in j.get("supersedes", []) for j in manifests(project)):
                    raise Superseded()
                validate_job(project, job)

            def progress(value):
                state.update(value)
                state["status"] = "running"
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
            state["finished_at"] = time.time()
            with metadata_lock(project):
                d.write_json(state_file(project, job["job_id"]), state)
                if state["status"] == "completed":
                    events = read(directory(project) / "events.json", [])
                    if not any(e["job_id"] == job["job_id"] for e in events):
                        count = state.get("completed", 0)
                        label = {"assets": "资产节点", "videos": "视频节点", "references": "视频引用", "images": "图片挂载"}[job["kind"]]
                        message = f"已完成{count}个{label}的同步核验"
                        events.append({"job_id": job["job_id"], "kind": job["kind"], "count": count, "message": message, "created_at": time.time()})
                        d.write_json(directory(project) / "events.json", events)
                        notify_completion(message)
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("enqueue", "start", "worker", "status", "pause", "resume", "image-choice"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--kind", choices=("assets", "videos", "references", "images"))
    parser.add_argument("--request-id")
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--choice", choices=("1", "2"))
    args = parser.parse_args()
    try:
        if args.command == "enqueue":
            result = enqueue(args.project, args.kind, args.request_id, args.ids, args.folder,
                             read(args.decisions) if args.decisions else None, args.start)
        elif args.command == "image-choice":
            result = image_choice(args.project, args.choice)
        else:
            function = {"start": start_worker, "worker": run_worker, "status": status, "pause": pause, "resume": resume}[args.command]
            result = function(args.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "code": getattr(exc, "code", "ERROR"), "message": c.safe_message(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
