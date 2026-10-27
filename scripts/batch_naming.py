"""Durable per-chat batch letters; project sessions remain the commit authority."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

REGISTRY = "batch-names.json"


def letter(number):
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ValueError("批次序号须为正整数")
    result = ""
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(65 + digit) + result
    return result


def scope_for(project, stored=None, explicit=None):
    project = Path(project).resolve()
    chosen = Path(explicit).resolve() if explicit else (Path(stored).resolve() if stored else
             project.parent if project.parent.name == "seedance-scene-director" else project)
    if stored and chosen != Path(stored).resolve():
        raise ValueError("项目已有批次作用域，不能静默切换；请沿用原作用域")
    return chosen


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _lock(scope):
    """OS locks release on a crash; the persistent lock file is not a stale lock."""
    scope.mkdir(parents=True, exist_ok=True)
    with (scope / ".batch-names.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("同一对话正在提交另一批分镜；请待其完成后重试") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _key(project, request_id):
    from project_paths import project_id
    identity = project_id(project) or os.path.normcase(str(Path(project).resolve()))
    value = json.dumps([identity, request_id], ensure_ascii=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _registry(scope):
    path = scope / REGISTRY
    value = _read(path) if path.is_file() else {"schema_version": 1, "entries": {}}
    if value.get("schema_version") != 1 or not isinstance(value.get("entries"), dict):
        raise ValueError("批次登记格式不兼容，不能重新从A开始")
    numbers = set()
    for key, item in value["entries"].items():
        number = item.get("number")
        if (not isinstance(number, int) or isinstance(number, bool) or number < 1 or number in numbers
                or item.get("label") != letter(number) or item.get("status") not in ("prepared", "committed")
                or not isinstance(item.get("project"), str) or not Path(item["project"]).is_absolute()
                or not isinstance(item.get("request_id"), str) or not item["request_id"]
                or not isinstance(item.get("request_hash"), str)
                or key != _key(item["project"], item["request_id"])):
            raise ValueError("批次登记损坏或重号；保留现场后核对，不能猜测编号")
        numbers.add(number)
    return value


def _recover(value):
    """Only an atomic session receipt turns a reservation into a consumed letter."""
    changed = False
    for key, item in list(value["entries"].items()):
        if item["status"] != "prepared":
            continue
        session_path = Path(item["project"]) / "session.json"
        session = _read(session_path) if session_path.is_file() else {}
        receipt = next((r for r in session.get("receipts", []) if r["request_id"] == item["request_id"]), None)
        if receipt is None:
            del value["entries"][key]
        else:
            if (receipt.get("request_hash") != item["request_hash"]
                    or receipt.get("batch_label") != item["label"]
                    or receipt.get("mode") != "append"):
                raise ValueError("批次登记与项目提交不一致；请核对保留的会话记录")
            item["status"] = "committed"
        changed = True
    return changed


def recover(scope):
    scope = Path(scope).resolve()
    with _lock(scope):
        value = _registry(scope)
        if _recover(value):
            _write(scope / REGISTRY, value)


@contextmanager
def allocate(scope, project, request_id, request_hash):
    """Hold the scope lock across the caller's atomic session save."""
    scope, project = Path(scope).resolve(), Path(project).resolve()
    with _lock(scope):
        value = _registry(scope)
        _recover(value)
        key = _key(project, request_id)
        if key in value["entries"]:
            raise ValueError("该请求已登记提交；请重新读取项目状态后按原request_id重试")
        number = max([value.get('high_watermark', 0)] + [item['number'] for item in value['entries'].values()]) + 1
        value["entries"][key] = {"number": number, "label": letter(number), "status": "prepared",
                                 "project": str(project), "request_id": request_id, "request_hash": request_hash}
        _write(scope / REGISTRY, value)
        try:
            yield letter(number)
        finally:
            # Recovery also handles a failed session save. A process kill leaves
            # prepared on disk, which the next lock holder resolves identically.
            _recover(value)
            _write(scope / REGISTRY, value)
