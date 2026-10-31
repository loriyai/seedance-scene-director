"""Hard gate for new scene-text storyboard authoring.

The final TXT format remains unchanged.  New projects bind a side-car trace
which records one authoring decision per shot.  The trace is deliberately
small: it binds the source beat, visible change, natural timing estimate and
any justified hold/exception.  It cannot prove how a model produced text,
but it prevents a draft with no shot-level decisions from entering preview or
commit and makes stale/generic attestations fail closed.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


POLICY = "shot-by-shot-v1"
VERSION = 1
TRACE_SUFFIX = ".authoring.json"

GENERIC = {
    "通过", "已检查", "符合要求", "符合剧情", "无问题", "同前", "同上",
    "为了节奏", "自然", "保持画面", "动作依据", "继续", "保持状态",
}
HOLD_WORDS = re.compile(
    r"注视|凝视|盯着|盯住|看向|看着|望向|望着|目光|视线|保持眼神|"
    r"听取|等待|等候|停留|静静地看|继续听|保持不动|保持站立|保持坐姿"
)
META_WORDS = re.compile(
    r"动作依据|不显示(?:系统)?文字|不显示面板|不改变(?:画内)?人物身份|"
    r"保留眼神|保留下颌|禁止|不得|不要|避免AI|元提示|审核"
)
TEMPLATE_PHRASES = (
    "说话者在当前关系中完成一次清楚的发声与回应",
    "声音压过现场风声",
    "视线掠过触发怒气的人物，提示音落入反应的空隙",
)
TEMPLATE_PATTERN = re.compile(r"在当前[^；。]{0,30}关系中完成：")
VOICE_AMBIGUITY = re.compile(r"脑海声|脑内声|系统声")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def trace_path(draft, explicit=None):
    if explicit:
        return Path(explicit).resolve()
    draft = Path(draft).resolve()
    return draft.with_name(draft.name + TRACE_SUFFIX)


def _concrete(value):
    if not isinstance(value, str) or not value.strip():
        return False
    compact = re.sub(r"[\W_]+", "", value)
    for phrase in sorted(GENERIC, key=len, reverse=True):
        compact = compact.replace(re.sub(r"[\W_]+", "", phrase), "")
    return bool(compact)


def _require(condition, message, **details):
    if not condition:
        return {"code": message, **details}
    return None


def _body(shot):
    text = shot.get("text", "")
    # The parser already validated the format.  Strip only the fixed envelope
    # so repetition and semantic-risk checks inspect authored prose.
    match = re.search(r"）(.+)音效：", text)
    return match.group(1) if match else text


def _normalized_body(shot):
    value = _body(shot)
    value = re.sub(r"@\{(?:CHAR|SET|PROP)\d+\}", "资产", value)
    value = re.sub(r"[0-9]+", "数", value)
    value = re.sub(r"[，。；：、（）()\[\]“”‘’\s]+", "", value)
    # These are ratio or formatting phrases, not a shot's viewing decision.
    value = re.sub(r"竖屏(?:构图|主体裁幅|安全区|裁幅)|横屏(?:构图|主体裁幅|安全区|裁幅)", "", value)
    return value


def _similar(left, right):
    if not left or not right:
        return 0.0
    a, b = set(left), set(right)
    return len(a & b) / max(1, len(a | b))


def _voice_signature(shot):
    # Keep the projection JSON-native so a trace round-trip does not turn
    # tuples into lists and invalidate an otherwise correct voice binding.
    return [[part.get("record"), part.get("voice"),
             part.get("source_kind", "character"),
             part.get("character_id") or list(part.get("character_ids", [])),
             part.get("source_label")] for part in shot.get("dialogue_parts", [])]


def _row_map(trace):
    result = {}
    for unit in trace.get("units", []):
        for row in unit.get("shots", []):
            result[row.get("shot_key")] = row
    return result


def _trace_payload(trace):
    return {key: value for key, value in trace.items() if key not in ("trace_sha256",)}


def validate(project, state, draft_bytes, units, expected_context_sha256,
             explicit_trace=None, draft_path=None):
    """Validate the required authoring trace for a new bound project.

    Projects bound before this policy existed remain readable and renderable;
    they return ``enforced=False`` until explicitly migrated.  New binds set
    the policy in session.json, so every new scene-text draft is gated.
    """
    if state.get("storyboard_authoring_policy") != POLICY:
        return {"enforced": False, "policy": POLICY, "status": "legacy_compatible"}

    from storyboard_text import _compact

    draft_sha256 = hashlib.sha256(draft_bytes).hexdigest()
    path = trace_path(draft_path or project, explicit_trace)
    errors = []
    if not path.is_file():
        return {"enforced": True, "policy": POLICY, "version": VERSION,
                "status": "blocked", "errors": [{"code": "AUTHORING_TRACE_MISSING",
                "message": f"缺少逐镜写作侧车：{path}"}], "trace_path": str(path)}
    try:
        trace_bytes = path.read_bytes()
        trace = json.loads(trace_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"enforced": True, "policy": POLICY, "version": VERSION,
                "status": "blocked", "errors": [{"code": "AUTHORING_TRACE_INVALID",
                "message": f"逐镜写作侧车无法读取：{exc}"}], "trace_path": str(path)}

    if trace.get("policy") != POLICY or trace.get("version") != VERSION:
        errors.append({"code": "AUTHORING_TRACE_POLICY", "message": "逐镜写作侧车版本不兼容"})
    if trace.get("draft_sha256") != draft_sha256:
        errors.append({"code": "AUTHORING_TRACE_DRAFT_BINDING", "message": "侧车与作者稿摘要不一致"})
    if trace.get("context_sha256") != expected_context_sha256:
        errors.append({"code": "AUTHORING_TRACE_CONTEXT_BINDING", "message": "侧车与当前项目上下文摘要不一致"})
    if trace.get("source_span") != [units[0]["span"][0], units[-1]["span"][1]]:
        errors.append({"code": "AUTHORING_TRACE_SPAN", "message": "侧车来源范围与当前稿件不一致"})

    rows = _row_map(trace)
    expected = []
    for unit in units:
        for index, shot in enumerate(unit.get("shots", []), 1):
            expected.append((f"{unit['draft_key']}#{index}", unit, index, shot))
    expected_keys = [key for key, _, _, _ in expected]
    if list(rows) != expected_keys or len(rows) != len(expected_keys):
        errors.append({"code": "AUTHORING_TRACE_SHOT_COVERAGE",
                       "message": "侧车必须按作者稿顺序一镜一条，不能漏镜、重镜或跳镜"})

    durations = []
    normalized = []
    for key, unit, index, shot in expected:
        row = rows.get(key)
        if not isinstance(row, dict):
            continue
        source_records = row.get("source_records")
        if (not isinstance(source_records, list) or not source_records or
                any(rid not in unit["source_records"] for rid in source_records)):
            errors.append({"code": "AUTHORING_TRACE_SOURCE", "shot": key,
                           "message": "每镜必须绑定当前来源记录"})
        dialogue_records = {part.get("record") for part in shot.get("dialogue_parts", [])}
        if dialogue_records and not dialogue_records.intersection(source_records or []):
            errors.append({"code": "AUTHORING_TRACE_SOURCE", "shot": key,
                           "message": "含台词镜头的侧车必须绑定本镜实际发声来源"})
        for field in ("beat", "visual_change"):
            if not _concrete(row.get(field)):
                errors.append({"code": "AUTHORING_TRACE_EVIDENCE", "shot": key,
                               "field": field, "message": "每镜必须写具体观看重点和可见变化"})
        timing = row.get("timing_basis")
        if not isinstance(timing, dict):
            errors.append({"code": "AUTHORING_TRACE_TIMING", "shot": key,
                           "message": "每镜必须提供自然时长依据"})
            timing = {}
        duration = shot["end"] - shot["start"]
        durations.append(duration)
        if timing.get("duration_seconds") != duration:
            errors.append({"code": "AUTHORING_TRACE_DURATION", "shot": key,
                           "message": "侧车选择时长必须等于作者稿镜窗"})
        action_phases = timing.get("action_phases")
        if (not isinstance(action_phases, list) or
                any(not _concrete(value) for value in action_phases)):
            errors.append({"code": "AUTHORING_TRACE_PHASES", "shot": key,
                           "message": "每镜必须列出实际动作阶段；没有动作时使用空数组"})
        if not _concrete(timing.get("entry_phase")):
            errors.append({"code": "AUTHORING_TRACE_PHASES", "shot": key,
                           "message": "每镜必须说明动作/信息进入镜头时的实际相位"})
        completion = timing.get("completion_point_seconds")
        if (not isinstance(completion, (int, float)) or isinstance(completion, bool)
                or not 0 <= completion <= duration):
            errors.append({"code": "AUTHORING_TRACE_COMPLETION", "shot": key,
                           "message": "每镜必须绑定关键变化完成点，且落在镜窗内"})
        parallel_with = timing.get("parallel_with")
        if (not isinstance(parallel_with, list) or
                any(not _concrete(value) for value in parallel_with)):
            errors.append({"code": "AUTHORING_TRACE_PARALLEL", "shot": key,
                           "message": "每镜必须明确同期关系；无同期内容时使用空数组"})
        if timing.get("parallel") is True and not parallel_with:
            errors.append({"code": "AUTHORING_TRACE_PARALLEL", "shot": key,
                           "message": "声明同期执行时必须列出实际同期的台词、动作或观察对象"})
        if not _concrete(timing.get("tail_load")):
            tail_load = None
        else:
            tail_load = timing.get("tail_load")
        for field in ("speech_seconds", "action_seconds", "observation_seconds"):
            value = timing.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                errors.append({"code": "AUTHORING_TRACE_TIMING", "shot": key,
                               "field": field, "message": "自然时长依据必须分别估计台词、动作和观看负荷"})
        if not _concrete(timing.get("cut_reason")):
            errors.append({"code": "AUTHORING_TRACE_CUT", "shot": key,
                           "message": "每镜必须说明本镜切出的具体观看落点"})
        has_dialogue = bool(shot.get("dialogue_parts"))
        if has_dialogue:
            speech_seconds = timing.get("speech_seconds", 0)
            chars = sum(c.isalnum() for part in shot["dialogue_parts"] for c in part["text"])
            if speech_seconds < chars / 6.0 - 0.05:
                errors.append({"code": "AUTHORING_TRACE_SPEECH_FAST", "shot": key,
                               "message": "自然说法估时低于规则允许的快速语速下限"})
            if speech_seconds > duration + 0.05:
                errors.append({"code": "AUTHORING_TRACE_SPEECH_WINDOW", "shot": key,
                               "message": "自然说法估时超出镜窗"})
            if row.get("voice_signature") != _voice_signature(shot):
                errors.append({"code": "AUTHORING_TRACE_VOICE", "shot": key,
                               "message": "侧车声源/发声类型与作者稿不一致"})
        total_required = max(
            timing.get("speech_seconds", 0),
            timing.get("action_seconds", 0),
            timing.get("observation_seconds", 0),
        ) if timing.get("parallel") is True else sum(
            timing.get(field, 0) for field in ("speech_seconds", "action_seconds", "observation_seconds"))
        if duration + 0.25 < total_required:
            errors.append({"code": "AUTHORING_TRACE_OVERLOAD", "shot": key,
                           "message": "镜窗不足以承载台词、动作和观看负荷"})
        if duration > total_required + 1.5 and not (_concrete(row.get("hold_reason")) or row.get("exception")):
            errors.append({"code": "AUTHORING_TRACE_EMPTY_TAIL", "shot": key,
                           "message": "镜窗长于实际负荷，必须说明来源成立的保持或观察理由"})
        if (isinstance(completion, (int, float)) and duration - completion > 1.0
                and not (tail_load or _concrete(row.get("hold_reason")) or row.get("exception"))):
            errors.append({"code": "AUTHORING_TRACE_EMPTY_TAIL", "shot": key,
                           "message": "关键变化完成后仍有镜尾，必须写明继续观看的具体负荷"})

        body = _body(shot)
        if META_WORDS.search(body):
            errors.append({"code": "AUTHORING_META_PROMPT", "shot": key,
                           "message": "后台纠错句/元提示不能进入最终镜头正文"})
        if any(phrase in body for phrase in TEMPLATE_PHRASES) or TEMPLATE_PATTERN.search(body):
            errors.append({"code": "AUTHORING_TEMPLATE_PHRASE", "shot": key,
                           "message": "最终镜头正文含固定句壳，必须改写为本镜具体人物、动作、视线和声音执行"})
        if VOICE_AMBIGUITY.search(body):
            non_character = any(part.get("source_kind") == "non_character" for part in shot.get("dialogue_parts", []))
            if non_character and not _concrete(row.get("voice_source_note")):
                errors.append({"code": "AUTHORING_VOICE_AMBIGUITY", "shot": key,
                               "message": "非人物播报不能用脑海声等笼统词改写，须绑定原声源"})
        if HOLD_WORDS.search(body):
            if not (_concrete(row.get("gaze_target")) or _concrete(row.get("hold_reason"))):
                errors.append({"code": "AUTHORING_HOLD_UNDER_SPECIFIED", "shot": key,
                               "message": "注视/保持/等待/听取必须说明对象、未决信息或可见变化"})

        normalized.append((key, _normalized_body(shot), row.get("exception"),
                           row.get("beat"), row.get("visual_change"),
                           row.get("timing_basis", {}).get("cut_reason") if isinstance(row.get("timing_basis"), dict) else ""))

    if durations and len(durations) >= 6:
        most = max(durations.count(value) for value in set(durations))
        if most / len(durations) >= 0.8 and not _concrete(trace.get("timing_distribution_basis")):
            errors.append({"code": "AUTHORING_UNIFORM_TIMING", "message":
                           "镜长高度集中，必须提供题材/来源支持的节奏依据；不能默认批量分配4秒或5秒"})

    for left, right in zip(normalized, normalized[1:]):
        left_key, left_body, left_exc, left_beat, left_change, left_cut = left
        right_key, right_body, right_exc, right_beat, right_change, right_cut = right
        repeated_decision = (left_beat == right_beat and left_change == right_change and left_cut == right_cut)
        if (_similar(left_body, right_body) >= 0.86 or repeated_decision) and not (left_exc or right_exc):
            errors.append({"code": "AUTHORING_TEMPLATE_REUSE", "shots": [left_key, right_key],
                           "message": "相邻镜头摄影/动作骨架高度重复，缺少逐镜独立观看依据"})

    trace_sha256 = hashlib.sha256(trace_bytes).hexdigest()
    payload = {"policy": POLICY, "version": VERSION, "trace_sha256": trace_sha256,
               "draft_sha256": draft_sha256, "context_sha256": expected_context_sha256,
               "shot_count": len(expected_keys), "source_span": trace.get("source_span")}
    payload["gate_digest"] = _digest(payload)
    return {"enforced": True, "policy": POLICY, "version": VERSION,
            "status": "passed" if not errors else "blocked", "errors": errors,
            "trace_path": str(path), "trace_sha256": trace_sha256,
            "shot_count": len(expected_keys), "shot_keys": expected_keys,
            "gate_digest": payload["gate_digest"]}


def require(report):
    if report.get("enforced") and report.get("status") != "passed":
        errors = report.get("errors") or [{"message": "逐镜写作闸门未通过"}]
        preview = "; ".join(str(item.get("message", item.get("code", "error"))) for item in errors[:8])
        raise ValueError("逐镜写作闸门：" + preview)

