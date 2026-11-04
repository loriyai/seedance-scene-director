"""Authoring-session-v5 lifecycle and provenance checks for scene-text drafts.

This module records author events and provenance metadata.  It never writes or
rewrites storyboard prose.  The author supplies the current draft and the
shot-level source decisions, while this module verifies hashes, scope, order,
and the append-only event chain before delivery.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

import director


PROTOCOL = "authoring-session-v5"
VERSION = 5
CURRENT_FILE = "authoring-session-v5.json"
HISTORY_DIR = "authoring-sessions"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AuthoringSessionError(ValueError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _skill_version() -> str:
    path = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return "unknown"


def session_path(project) -> Path:
    return Path(project).resolve() / CURRENT_FILE


def _history_path(project, session_id: str) -> Path:
    return Path(project).resolve() / HISTORY_DIR / f"{session_id}.json"


def _read_current(project):
    path = session_path(project)
    if not path.is_file():
        raise AuthoringSessionError(
            "AUTHORING_SESSION_NOT_STARTED",
            f"作者会话尚未启动：{path}",
            path=str(path),
        )
    try:
        value = director.read_json(path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise AuthoringSessionError(
            "AUTHORING_SESSION_INVALID",
            f"作者会话无法读取：{exc}",
            path=str(path),
        ) from exc
    if not isinstance(value, dict):
        raise AuthoringSessionError("AUTHORING_SESSION_INVALID", "作者会话必须是对象", path=str(path))
    return value


def _require_bound(project):
    project = Path(project).resolve()
    if not (project / "session.json").is_file():
        raise AuthoringSessionError(
            "PROJECT_UNBOUND",
            f"项目尚未绑定标准剧本和资产：{project / 'session.json'}",
            path=str(project / "session.json"),
        )
    try:
        state = director.load(project)
    except FileNotFoundError as exc:
        raise AuthoringSessionError(
            "PROJECT_UNBOUND",
            f"项目状态文件不存在：{project / 'session.json'}",
            path=str(project / "session.json"),
        ) from exc
    if state.get("authoring_session_protocol") != PROTOCOL:
        raise AuthoringSessionError(
            "AUTHORING_SESSION_UNAVAILABLE",
            "当前绑定项目未启用 authoring-session-v5",
        )
    return state


def require_bound(project):
    """Require only the bound-project state file and return its state.

    Delivery callers use this boundary before invoking ``director.load`` so an
    unbound project is reported with the stable ``PROJECT_UNBOUND`` code.  The
    helper deliberately does not require v5; legacy fixture projects continue
    to use the v4 gate until they are rebound.
    """
    project = Path(project).resolve()
    path = project / "session.json"
    if not path.is_file():
        raise AuthoringSessionError(
            "PROJECT_UNBOUND",
            f"项目尚未绑定标准剧本和资产：{path}",
            path=str(path),
        )
    try:
        return director.read_json(path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise AuthoringSessionError(
            "PROJECT_STATE_INVALID",
            f"项目状态无法读取：{exc}",
            path=str(path),
        ) from exc


def active(project):
    """Return the active v5 session, or raise a structured lifecycle error."""
    return _load_active(project)


def _validate_hash(value, field):
    if value is None:
        return
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise AuthoringSessionError("AUTHORING_HASH_INVALID", f"{field}必须是64位十六进制摘要", field=field)


def _manifest(session, draft_sha256=None):
    chain = session.get("authoring_event_chain", [])
    chain_hash = _digest(chain)
    return {
        "protocol": PROTOCOL,
        "session_id": session["session_id"],
        "writer_identity": session["writer_identity"],
        "parent_draft_sha256": session.get("parent_draft_sha256"),
        "scope": list(session["scope"]),
        "skill_version": session.get("skill_version", _skill_version()),
        "draft_sha256": draft_sha256 if draft_sha256 is not None else session.get("current_draft_sha256"),
        "authoring_event_chain_sha256": chain_hash,
    }


def start(project, scope, writer_identity, parent_draft_sha256=None):
    """Start a new authoring session without creating or editing a draft."""
    state = _require_bound(project)
    if (not isinstance(scope, (list, tuple)) or len(scope) != 2 or
            any(not isinstance(value, int) or isinstance(value, bool) for value in scope) or
            scope[0] >= scope[1]):
        raise AuthoringSessionError("AUTHORING_SCOPE_INVALID", "作者会话范围必须是递增的两个整数")
    if not isinstance(writer_identity, str) or not writer_identity.strip():
        raise AuthoringSessionError("WRITER_IDENTITY_REQUIRED", "必须提供写作者身份")
    _validate_hash(parent_draft_sha256, "parent_draft_sha256")
    path = session_path(project)
    if path.is_file():
        current = _read_current(project)
        if current.get("status") == "active":
            raise AuthoringSessionError(
                "AUTHORING_SESSION_ACTIVE",
                "当前作者会话仍处于active状态；先完成或关闭当前会话",
                session_id=current.get("session_id"),
            )
        session_id = current.get("session_id")
        if session_id:
            history = _history_path(project, session_id)
            history.parent.mkdir(parents=True, exist_ok=True)
            if not history.exists():
                director.atomic_text(history, json.dumps(current, ensure_ascii=False, indent=2) + "\n")
    session_id = uuid.uuid4().hex
    session = {
        "protocol": PROTOCOL,
        "version": VERSION,
        "session_id": session_id,
        "writer_identity": writer_identity.strip(),
        "parent_draft_sha256": parent_draft_sha256,
        "scope": [scope[0], scope[1]],
        "skill_version": _skill_version(),
        "status": "active",
        "started_at": _now(),
        "closed_at": None,
        "current_draft_sha256": None,
        "authoring_event_chain": [],
        "authoring_event_chain_sha256": _digest([]),
    }
    session["provenance_manifest"] = _manifest(session)
    director.write_json(path, session)
    return {
        "status": "started",
        "protocol": PROTOCOL,
        "session_id": session_id,
        "scope": session["scope"],
        "parent_draft_sha256": parent_draft_sha256,
        "session_path": str(path),
        "bound_revision": state.get("revision"),
    }


def _load_active(project):
    _require_bound(project)
    session = _read_current(project)
    if session.get("protocol") != PROTOCOL or session.get("version") != VERSION:
        raise AuthoringSessionError("AUTHORING_SESSION_PROTOCOL", "作者会话协议版本不兼容")
    if session.get("status") != "active":
        raise AuthoringSessionError("AUTHORING_SESSION_CLOSED", "当前作者会话已关闭")
    if not isinstance(session.get("session_id"), str) or not session["session_id"].strip():
        raise AuthoringSessionError("AUTHORING_SESSION_INVALID", "作者会话缺少有效session_id")
    if not isinstance(session.get("writer_identity"), str) or not session["writer_identity"].strip():
        raise AuthoringSessionError("AUTHORING_SESSION_INVALID", "作者会话缺少有效writer_identity")
    scope = session.get("scope")
    if (not isinstance(scope, list) or len(scope) != 2 or
            any(not isinstance(value, int) or isinstance(value, bool) for value in scope) or
            scope[0] >= scope[1]):
        raise AuthoringSessionError("AUTHORING_SCOPE_INVALID", "作者会话范围无效")
    _validate_hash(session.get("parent_draft_sha256"), "parent_draft_sha256")
    _validate_hash(session.get("current_draft_sha256"), "current_draft_sha256")
    if not isinstance(session.get("authoring_event_chain"), list):
        raise AuthoringSessionError("AUTHORING_CHAIN_INVALID", "作者事件链必须是数组")
    return session


def append_event(project, draft, shot_key, body_sha256, source_records,
                 parent_shot_sha256=None):
    """Append one immutable author event.  The draft file is read and never written."""
    session = _load_active(project)
    draft = Path(draft).resolve()
    if not draft.is_file():
        raise AuthoringSessionError("DRAFT_MISSING", f"作者稿不存在：{draft}", path=str(draft))
    if not isinstance(shot_key, str) or not shot_key.strip():
        raise AuthoringSessionError("SHOT_KEY_REQUIRED", "必须提供shot_key")
    _validate_hash(body_sha256, "new_body_sha256")
    _validate_hash(parent_shot_sha256, "parent_shot_sha256")
    if not isinstance(source_records, list) or not source_records or any(not isinstance(x, str) or not x for x in source_records):
        raise AuthoringSessionError("SOURCE_RECORDS_REQUIRED", "每个作者事件必须绑定来源记录")
    chain = session["authoring_event_chain"]
    if any(row.get("shot_key") == shot_key for row in chain):
        raise AuthoringSessionError("SHOT_EVENT_DUPLICATE", f"作者事件已存在：{shot_key}")
    draft_sha256 = hashlib.sha256(draft.read_bytes()).hexdigest()
    event = {
        "event_id": uuid.uuid4().hex,
        "shot_key": shot_key,
        "parent_shot_sha256": parent_shot_sha256,
        "parent_draft_sha256": (session.get("current_draft_sha256")
                                 or session.get("parent_draft_sha256")),
        "new_body_sha256": body_sha256,
        "source_records": list(source_records),
        "draft_sha256": draft_sha256,
        "event_at": _now(),
    }
    chain.append(event)
    session["current_draft_sha256"] = draft_sha256
    session["authoring_event_chain_sha256"] = _digest(chain)
    session["provenance_manifest"] = _manifest(session, draft_sha256)
    director.write_json(session_path(project), session)
    return {
        "status": "event_appended",
        "session_id": session["session_id"],
        "event_id": event["event_id"],
        "shot_key": shot_key,
        "draft_sha256": draft_sha256,
        "authoring_event_chain_sha256": session["authoring_event_chain_sha256"],
    }


def validate_for_delivery(project, draft_bytes, units, expected_scope, trace=None):
    """Validate the current session and optional bound provenance envelope."""
    try:
        session = _load_active(project)
    except AuthoringSessionError as exc:
        return {"status": "blocked", "errors": [{"code": exc.code, "message": str(exc)}]}
    draft_sha256 = hashlib.sha256(draft_bytes).hexdigest()
    chain = session.get("authoring_event_chain", [])
    errors = []
    if list(expected_scope) != list(session.get("scope", [])):
        errors.append({"code": "AUTHORING_SCOPE_MISMATCH", "message": "作者会话范围与当前稿件范围不一致"})
    if session.get("current_draft_sha256") != draft_sha256:
        errors.append({"code": "AUTHORING_DRAFT_BINDING", "message": "作者会话与当前作者稿摘要不一致"})
    if session.get("authoring_event_chain_sha256") != _digest(chain):
        errors.append({"code": "AUTHORING_CHAIN_BROKEN", "message": "作者事件链摘要不一致"})
    manifest = _manifest(session, draft_sha256)
    if session.get("provenance_manifest") != manifest:
        errors.append({"code": "PROVENANCE_MANIFEST_BROKEN", "message": "作者来源证明清单与当前会话不一致"})
    expected_keys = [f"{unit['draft_key']}#{index}"
                     for unit in units for index, _ in enumerate(unit.get("shots", []), 1)]
    actual_keys = [row.get("shot_key") if isinstance(row, dict) else None for row in chain]
    if actual_keys != expected_keys:
        errors.append({"code": "AUTHORING_CHAIN_COVERAGE", "message": "作者事件链必须按正文顺序覆盖全部镜头"})
    by_key = {row.get("shot_key"): row for row in chain if isinstance(row, dict)}
    previous_draft_sha256 = session.get("parent_draft_sha256")
    for index, row in enumerate(chain):
        if not isinstance(row, dict):
            errors.append({"code": "AUTHORING_EVENT_INVALID", "message": "作者事件必须是对象"})
            continue
        for field in ("draft_sha256", "new_body_sha256", "parent_draft_sha256"):
            try:
                _validate_hash(row.get(field), field)
            except AuthoringSessionError as exc:
                errors.append({"code": exc.code, "event": index, "message": str(exc)})
        if row.get("parent_draft_sha256") != previous_draft_sha256:
            errors.append({"code": "AUTHORING_DRAFT_CHAIN", "event": index,
                           "message": "作者事件的父稿摘要未连接到上一版本"})
        previous_draft_sha256 = row.get("draft_sha256")
    if chain and isinstance(chain[-1], dict) and chain[-1].get("draft_sha256") != draft_sha256:
        errors.append({"code": "AUTHORING_FINAL_EVENT_DRAFT", "message": "最后一个作者事件未绑定当前作者稿摘要"})
    for key, unit, shot in ((f"{unit['draft_key']}#{index}", unit, shot)
                            for unit in units for index, shot in enumerate(unit.get("shots", []), 1)):
        row = by_key.get(key)
        if row is None:
            continue
        source_records = row.get("source_records")
        if (not isinstance(source_records, list) or not source_records or
                any(record not in unit.get("source_records", []) for record in source_records)):
            errors.append({"code": "AUTHORING_CHAIN_SOURCE", "shot": key, "message": "作者事件来源记录不属于当前镜头"})
    if trace is not None:
        trace_protocol = trace.get("authoring_session_protocol") if isinstance(trace, dict) else None
        trace_manifest = trace.get("provenance_manifest") if isinstance(trace, dict) else None
        trace_chain = trace.get("authoring_event_chain") if isinstance(trace, dict) else None
        trace_chain_sha256 = trace.get("authoring_event_chain_sha256") if isinstance(trace, dict) else None
        if trace_protocol != PROTOCOL:
            errors.append({"code": "AUTHORING_SESSION_PROTOCOL", "message": "分镜侧车未声明authoring-session-v5协议"})
        if trace_manifest != manifest:
            errors.append({"code": "PROVENANCE_MANIFEST_MISMATCH", "message": "分镜侧车来源证明与当前作者会话不一致"})
        if trace_chain != chain:
            errors.append({"code": "AUTHORING_CHAIN_MISMATCH", "message": "分镜侧车事件链与当前作者会话不一致"})
        if trace_chain_sha256 != manifest["authoring_event_chain_sha256"]:
            errors.append({"code": "AUTHORING_CHAIN_MISMATCH", "message": "分镜侧车事件链摘要与当前作者会话不一致"})
    return {
        "enforced": True,
        "protocol": PROTOCOL,
        "version": VERSION,
        "status": "blocked" if errors else "passed",
        "errors": errors,
        "session_id": session["session_id"],
        "writer_identity": session["writer_identity"],
        "provenance_manifest": manifest,
        "authoring_event_chain": chain,
        "authoring_event_chain_sha256": manifest["authoring_event_chain_sha256"],
    }


def close(project, draft_sha256=None):
    """Close the current session after a successful commit."""
    _require_bound(project)
    session = _read_current(project)
    if session.get("status") != "active":
        return {"status": "already_closed", "session_id": session.get("session_id")}
    if draft_sha256 is not None and session.get("current_draft_sha256") != draft_sha256:
        raise AuthoringSessionError("AUTHORING_DRAFT_BINDING", "关闭作者会话时作者稿摘要不一致")
    session["status"] = "closed"
    session["closed_at"] = _now()
    director.write_json(session_path(project), session)
    return {"status": "closed", "session_id": session["session_id"]}


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start_parser = commands.add_parser("start")
    start_parser.add_argument("--project", type=Path, required=True)
    start_parser.add_argument("--scope", type=int, nargs=2, required=True)
    start_parser.add_argument("--writer-identity", required=True)
    start_parser.add_argument("--parent-draft-sha256")
    event_parser = commands.add_parser("event")
    event_parser.add_argument("--project", type=Path, required=True)
    event_parser.add_argument("--draft", type=Path, required=True)
    event_parser.add_argument("--shot-key", required=True)
    event_parser.add_argument("--new-body-sha256", required=True)
    event_parser.add_argument("--source-record", action="append", required=True)
    event_parser.add_argument("--parent-shot-sha256")
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--project", type=Path, required=True)
    close_parser = commands.add_parser("close")
    close_parser.add_argument("--project", type=Path, required=True)
    close_parser.add_argument("--draft-sha256")
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        if args.command == "start":
            result = start(args.project, args.scope, args.writer_identity, args.parent_draft_sha256)
        elif args.command == "event":
            result = append_event(args.project, args.draft, args.shot_key,
                                  args.new_body_sha256, args.source_record,
                                  args.parent_shot_sha256)
        elif args.command == "status":
            _require_bound(args.project)
            result = _read_current(args.project)
        else:
            result = close(args.project, args.draft_sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except AuthoringSessionError as exc:
        result = {"status": "error", "code": exc.code, "message": str(exc), **exc.details}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
