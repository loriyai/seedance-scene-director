"""Hard gate for new scene-text storyboard authoring.

The final TXT format remains unchanged.  New projects bind a side-car trace
which records one authoring decision per shot.  The trace is deliberately
small: it binds the source beat, visible change, natural timing estimate,
unit organization and any justified hold/exception.  It cannot prove how a
model produced text, but it prevents a draft with no shot-level decisions or
unit-boundary proof from entering preview or commit and makes stale/generic
attestations fail closed.
"""
from __future__ import annotations

import hashlib
import copy
import json
import re
from pathlib import Path


POLICY = "shot-by-shot-v1"
VERSION = 4
LEGACY_VERSION = 3
TRACE_SUFFIX = ".authoring.json"
UNIT_ORGANIZATION_POLICY = "prefer_26_30_with_exception_proof_v1"
UNIT_BOUNDARY_POLICY = "prefer_26_30_at_unit_boundaries_v2"
UNIT_DECISIONS = {"target_26_30", "short_exception"}
SHORT_REASON_CODES = {
    "scene_boundary", "selected_range_end", "merge_exceeds_30",
    "incompatible_generation", "semantic_cut", "candidate_not_compatible",
}
CANDIDATE_REASON_CODES = {"semantic_cut", "candidate_not_compatible"}

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
GENERIC_STATION = re.compile(r"不同景层保持可见关系|保持可见关系|后景人物做有意义反应|相邻人物或当前道具")
GENERIC_CAMERA = re.compile(r"相关动作的进入相位起拍|镜头平滑跟随后在动作结果处停稳|镜头沿视线缓慢微移呈现听者关系")
GENERIC_LIGHT = re.compile(r"环境光方向与主体明暗落点保持连续|均匀漫射日光落在.+暗部保留真实层次")
DEPTH_WORDS = re.compile(r"景深|焦外|虚化|焦点")
CONCRETE_LIGHT = re.compile(r"左侧|右侧|前方|后方|侧面|逆光|顶光|低位|漫射|阴影|明部|暗部|高光|轮廓")
MAIN_FILL_LIGHT = re.compile(r"主光\s*[:：].{1,80}(?:辅光|环境反射|反射光|补光)\s*[:：]", re.S)
CONCRETE_CUT = re.compile(r"切出|接入|动作锚点|视线锚点|声音先行|结果读清|落点")


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


def _validate_unit_organization(trace, state, units):
    """Require a trace decision for every unit-duration boundary."""
    from storyboard_text import observations

    errors = []
    organization = trace.get("unit_organization")
    if not isinstance(organization, dict):
        return [{"code": "AUTHORING_UNIT_ORGANIZATION",
                 "message": "侧车必须提供逐单元26～30秒优先与短单元例外证明"}], None
    if organization.get("policy") != UNIT_ORGANIZATION_POLICY:
        errors.append({"code": "AUTHORING_UNIT_ORGANIZATION",
                       "message": "侧车未声明当前26～30秒优先规则"})
    rows = organization.get("units")
    expected_keys = [unit["draft_key"] for unit in units]
    if not isinstance(rows, list) or [row.get("draft_key") for row in rows if isinstance(row, dict)] != expected_keys:
        errors.append({"code": "AUTHORING_UNIT_ORGANIZATION",
                       "message": "逐单元组织证明必须按作者稿单元顺序逐条提供"})
        rows = rows if isinstance(rows, list) else []

    summary = observations(state, units).get("preferred_target_summary", {})
    candidate_units = set(summary.get("review_required_units", []))
    by_key = {row.get("draft_key"): row for row in rows if isinstance(row, dict)}
    for unit in units:
        key = unit["draft_key"]
        duration = unit["duration"]
        row = by_key.get(key)
        if row is None:
            errors.append({"code": "AUTHORING_UNIT_ORGANIZATION", "unit": key,
                           "message": "缺少本单元的时长组织决定"})
            continue
        if row.get("duration_seconds") != duration:
            errors.append({"code": "AUTHORING_UNIT_ORGANIZATION", "unit": key,
                           "message": "组织证明中的单元时长必须等于作者稿单元时长"})
        decision = row.get("decision")
        expected_decision = "target_26_30" if 26 <= duration <= 30 else "short_exception"
        if decision != expected_decision or decision not in UNIT_DECISIONS:
            errors.append({"code": "AUTHORING_UNIT_ORGANIZATION", "unit": key,
                           "message": f"{key}须标记为{expected_decision}"})
        if expected_decision == "short_exception":
            reason_code = row.get("reason_code")
            if reason_code not in SHORT_REASON_CODES or not _concrete(row.get("reason")):
                errors.append({"code": "AUTHORING_UNIT_SHORT_REASON", "unit": key,
                               "message": "4～25秒单元必须提供规范原因代码和具体证据"})
            if key in candidate_units and reason_code not in CANDIDATE_REASON_CODES:
                errors.append({"code": "AUTHORING_UNIT_CANDIDATE_REVIEW", "unit": key,
                               "message": "存在可在30秒内组合的相邻候选，必须证明为何不能合并"})
    return errors, summary


def _validate_v3(project, state, draft_bytes, units, expected_context_sha256,
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

    if trace.get("policy") != POLICY or trace.get("version") != LEGACY_VERSION:
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

    organization_errors, organization_summary = _validate_unit_organization(trace, state, units)
    errors.extend(organization_errors)

    durations = []
    normalized = []
    transitions = []
    sounds = []
    station_lines = []
    cue_seen = set()
    all_observation_zero = True
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
        match_transition = re.search(r"；转场：([^）]+）)", shot.get("text", ""))
        transitions.append(match_transition.group(1).rstrip("）") if match_transition else "")
        match_sound = re.search(r"音效：(.+?)。$", shot.get("text", ""))
        sounds.append(match_sound.group(1).strip() if match_sound else "")
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
        if timing.get("observation_seconds", 0) > 0:
            all_observation_zero = False
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
        if duration > total_required + 1.5 and not _concrete(row.get("hold_reason")):
            errors.append({"code": "AUTHORING_TRACE_EMPTY_TAIL", "shot": key,
                           "message": "镜窗长于实际负荷，必须说明来源成立的保持或观察理由"})
        if (isinstance(completion, (int, float)) and duration - completion > 1.0
                and not (tail_load or _concrete(row.get("hold_reason")))):
            errors.append({"code": "AUTHORING_TRACE_EMPTY_TAIL", "shot": key,
                           "message": "关键变化完成后仍有镜尾，必须写明继续观看的具体负荷"})

        body = _body(shot)
        if META_WORDS.search(body):
            errors.append({"code": "AUTHORING_META_PROMPT", "shot": key,
                           "message": "后台纠错句/元提示不能进入最终镜头正文"})
        if any(phrase in body for phrase in TEMPLATE_PHRASES) or TEMPLATE_PATTERN.search(body):
            errors.append({"code": "AUTHORING_TEMPLATE_PHRASE", "shot": key,
                           "message": "最终镜头正文含固定句壳，必须改写为本镜具体人物、动作、视线和声音执行"})
        if GENERIC_CAMERA.search(body):
            errors.append({"code": "AUTHORING_GENERIC_CAMERA", "shot": key,
                           "message": "摄影正文使用泛化运镜句，必须写起移时机、方向、途中关系和终点落点"})
        if (GENERIC_LIGHT.search(body) or not CONCRETE_LIGHT.search(body)
                or not MAIN_FILL_LIGHT.search(body)):
            errors.append({"code": "AUTHORING_LIGHTING_UNDER_SPECIFIED", "shot": key,
                           "message": "每镜必须写主光、辅光/环境反射的具体方向与主体/道具明暗落点，不能使用抽象连续句"})
        if not DEPTH_WORDS.search(body):
            errors.append({"code": "AUTHORING_DEPTH_MISSING", "shot": key,
                           "message": "每镜必须落实景深、焦外、虚化或焦点关系"})
        actor_ids = row.get("actor_ids")
        if not isinstance(actor_ids, list) or not actor_ids:
            errors.append({"code": "AUTHORING_ACTOR_MISSING", "shot": key,
                           "message": "每镜侧车必须记录实际动作执行者或可见表演主体"})
        if not _concrete(row.get("focus_note")):
            errors.append({"code": "AUTHORING_FOCUS_EVIDENCE", "shot": key,
                           "message": "每镜必须说明主焦点与观看理由，不能只按场景首人物取焦点"})
        cue_seen.update(str(value) for value in row.get("source_cues", []) if value)
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

        if GENERIC_STATION.search(unit.get("global", "")):
            station_lines.append(unit.get("draft_key"))

    if durations and len(durations) >= 6:
        most = max(durations.count(value) for value in set(durations))
        if most / len(durations) >= 0.65 and not _concrete(trace.get("timing_distribution_basis")):
            errors.append({"code": "AUTHORING_UNIFORM_TIMING", "message":
                           "镜长高度集中，必须提供题材/来源支持的节奏依据；不能默认批量分配4秒或5秒"})

    if station_lines:
        errors.append({"code": "AUTHORING_GENERIC_STATION", "units": station_lines,
                       "message": "人物站位不能使用不同景层保持可见关系等抽象占位"})
    if transitions and len(transitions) >= 6 and len(set(transitions)) == 1:
        if transitions[0] == "硬切" or not _concrete(trace.get("transition_distribution_basis")):
            errors.append({"code": "AUTHORING_UNIFORM_TRANSITION",
                           "message": "全场统一转场必须有逐镜锚点和明确理由；不能批量使用硬切"})
    if sounds and len(sounds) >= 6 and len(set(sounds)) == 1:
        errors.append({"code": "AUTHORING_UNIFORM_SOUND",
                       "message": "全场统一音效不能代替实际声源；请按动作、距离、方向和来源声音逐镜判断"})
    if expected and all_observation_zero:
        errors.append({"code": "AUTHORING_OBSERVATION_EMPTY",
                       "message": "全稿观看负荷全部为零，侧车不能证明自然镜尾和观察关系"})
    cue_contract = trace.get("source_cue_coverage")
    if not isinstance(cue_contract, dict) or not _concrete(cue_contract.get("status")):
        errors.append({"code": "AUTHORING_SOURCE_CUE_COVERAGE",
                       "message": "侧车必须记录源文括号动作、表演和发声方式的覆盖状态"})

    for left, right in zip(normalized, normalized[1:]):
        left_key, left_body, left_exc, left_beat, left_change, left_cut = left
        right_key, right_body, right_exc, right_beat, right_change, right_cut = right
        repeated_decision = (left_beat == right_beat and left_change == right_change and left_cut == right_cut)
        if (_similar(left_body, right_body) >= 0.78 or repeated_decision):
            errors.append({"code": "AUTHORING_TEMPLATE_REUSE", "shots": [left_key, right_key],
                           "message": "相邻镜头摄影/动作骨架高度重复，缺少逐镜独立观看依据"})

    trace_sha256 = hashlib.sha256(trace_bytes).hexdigest()
    payload = {"policy": POLICY, "version": LEGACY_VERSION, "trace_sha256": trace_sha256,
               "draft_sha256": draft_sha256, "context_sha256": expected_context_sha256,
               "shot_count": len(expected_keys), "source_span": trace.get("source_span")}
    payload["gate_digest"] = _digest(payload)
    return {"enforced": True, "policy": POLICY, "version": LEGACY_VERSION,
            "status": "passed" if not errors else "blocked", "errors": errors,
            "trace_path": str(path), "trace_sha256": trace_sha256,
            "shot_count": len(expected_keys), "shot_keys": expected_keys,
            "preferred_target_summary": organization_summary,
            "gate_digest": payload["gate_digest"]}


def bind_metadata(trace, draft_bytes, units, context_sha256):
    """Bind authored decisions without generating or changing creative fields."""
    import copy

    if trace.get("policy") != POLICY or trace.get("version") != VERSION:
        raise ValueError("仅对当前v4作者依据绑定元数据；不自动转换旧侧车")
    result = copy.deepcopy(trace)
    result.update(draft_sha256=hashlib.sha256(draft_bytes).hexdigest(),
                  context_sha256=context_sha256,
                  source_span=[units[0]["span"][0], units[-1]["span"][1]])
    return result


def _source_cues(record):
    """Return explicit parenthetical performance cues, not inferred actions."""
    label = record.get("label") or ""
    match = re.search(r"[（(]([^（）()]*)[）)]$", label)
    if not match:
        return []
    import sys
    module_path = str(Path(__file__).resolve().parents[1] / "modules/screenplay-standardizer/scripts")
    if module_path not in sys.path:
        sys.path.insert(0, module_path)
    from source_compat import voice_cue_kind

    cue = match.group(1).strip()
    if not cue or voice_cue_kind(cue) or cue in ("旁白", "非人物播报"):
        return []
    return [cue]


def _unit_organization_v4(trace, state, units, observations_summary=None):
    from storyboard_text import observations

    errors = []
    summary = (observations(state, units) if observations_summary is None
               else copy.deepcopy(observations_summary))
    summary["preferred_target_summary"]["policy"] = UNIT_BOUNDARY_POLICY
    organization = trace.get("unit_organization")
    if not isinstance(organization, dict) or organization.get("policy") != UNIT_BOUNDARY_POLICY:
        return [{"code": "AUTHORING_UNIT_ORGANIZATION",
                 "message": "须采用仅作用于单元切分组合的26～30秒优先规则"}], summary
    rows = organization.get("units")
    keys = [unit["draft_key"] for unit in units]
    if (not isinstance(rows, list) or
            [row.get("draft_key") for row in rows if isinstance(row, dict)] != keys):
        return [{"code": "AUTHORING_UNIT_ORGANIZATION",
                 "message": "单元组织记录须与作者稿单元一一对应"}], summary
    direct_candidates = {key for candidate in summary.get("same_scene_adjacent_candidates", [])
                         if 26 <= candidate["combined_duration"] <= 30
                         for key in candidate["units"]}
    for unit, row in zip(units, rows):
        duration = unit["duration"]
        # Classification and duration come from the text, never a second estimate.
        if 26 <= duration <= 30:
            continue
        if row.get("reason_code") not in SHORT_REASON_CODES or not _concrete(row.get("reason")):
            errors.append({"code": "AUTHORING_UNIT_SHORT_REASON", "unit": unit["draft_key"],
                           "message": "短单元须说明自然边界及无法合法组合到26～30秒的具体原因"})
        elif unit["draft_key"] in direct_candidates and row["reason_code"] not in CANDIDATE_REASON_CODES:
            errors.append({"code": "AUTHORING_UNIT_CANDIDATE_REVIEW", "unit": unit["draft_key"],
                           "message": "存在26～30秒相邻组合候选，须具体说明为何不兼容；程序不判断语义合法性"})
    return errors, summary


def _validate_v4(project, state, draft_bytes, units, expected_context_sha256,
                  explicit_trace=None, draft_path=None, *, observations_summary=None):
    import math

    path = trace_path(draft_path or project, explicit_trace)
    if not path.is_file():
        return {"enforced": True, "policy": POLICY, "version": VERSION, "status": "blocked",
                "errors": [{"code": "AUTHORING_TRACE_MISSING", "message": f"缺少作者依据：{path}"}]}
    try:
        trace_bytes = path.read_bytes()
        trace = json.loads(trace_bytes.decode("utf-8-sig"))
        if not isinstance(trace, dict):
            raise ValueError("作者依据必须是对象")
    except (OSError, UnicodeError, ValueError) as exc:
        return {"enforced": True, "policy": POLICY, "version": VERSION, "status": "blocked",
                "errors": [{"code": "AUTHORING_TRACE_INVALID", "message": str(exc)}]}
    errors = []
    if trace.get("policy") != POLICY or trace.get("version") != VERSION:
        errors.append({"code": "AUTHORING_TRACE_POLICY", "message": "作者依据版本不兼容"})
    draft_sha256 = hashlib.sha256(draft_bytes).hexdigest()
    for field, value, code in (
            ("draft_sha256", draft_sha256, "AUTHORING_TRACE_DRAFT_BINDING"),
            ("context_sha256", expected_context_sha256, "AUTHORING_TRACE_CONTEXT_BINDING"),
            ("source_span", [units[0]["span"][0], units[-1]["span"][1]], "AUTHORING_TRACE_SPAN")):
        if trace.get(field) != value:
            errors.append({"code": code, "message": f"作者依据的{field}与当前文件不一致"})
    expected = [(f"{unit['draft_key']}#{index}", unit, shot)
                for unit in units for index, shot in enumerate(unit["shots"], 1)]
    expected_keys = [key for key, _, _ in expected]
    trace_units = trace.get("units", [])
    if not isinstance(trace_units, list) or any(not isinstance(unit, dict) for unit in trace_units):
        trace_units = []
    trace_rows = [row for unit in trace_units for row in unit.get("shots", [])
                  if isinstance(row, dict)]
    if ([unit.get("draft_key") for unit in trace_units] != [unit["draft_key"] for unit in units]
            or [row.get("shot_key") for row in trace_rows] != expected_keys):
        errors.append({"code": "AUTHORING_TRACE_SHOT_COVERAGE",
                       "message": "作者依据须按正文顺序一镜一条，不能漏镜、重镜或跳镜"})
    rows = {row.get("shot_key"): row for row in trace_rows}
    organization_errors, organization = _unit_organization_v4(trace, state, units, observations_summary)
    errors.extend(organization_errors)
    records = {record["id"]: record for record in state["records"]}
    mapped_records, mapped_cues = set(), set()
    durations, normalized, transitions, sounds = [], [], [], []
    for key, unit, shot in expected:
        row = rows.get(key)
        if row is None:
            continue
        source = row.get("source_records")
        if (not isinstance(source, list) or not source or
                any(rid not in unit["source_records"] for rid in source)):
            errors.append({"code": "AUTHORING_TRACE_SOURCE", "shot": key,
                           "message": "每镜须绑定本单元实际来源记录"})
            source = []
        mapped_records.update(source)
        dialogue_records = {part["record"] for part in shot.get("dialogue_parts", [])}
        if not dialogue_records.issubset(source):
            errors.append({"code": "AUTHORING_TRACE_SOURCE", "shot": key,
                           "message": "本镜全部发声来源须包含在镜级来源映射中"})
        for field in ("viewing_decision", "natural_timing"):
            if not _concrete(row.get(field)):
                errors.append({"code": "AUTHORING_TRACE_EVIDENCE", "shot": key,
                               "field": field, "message": "须提供具体观看决定与自然时长依据"})
        cues = row.get("source_cues", [])
        if not isinstance(cues, list):
            cues = [None]
        for cue_ref in cues:
            rid, separator, cue = cue_ref.partition(":") if isinstance(cue_ref, str) else (None, "", "")
            if not separator or rid not in source or cue not in _source_cues(records.get(rid, {})):
                errors.append({"code": "AUTHORING_SOURCE_CUE_MAPPING", "shot": key,
                               "message": "cue定位必须引用本镜实际来源的原括号要求"})
            else:
                mapped_cues.add((rid, cue))
        duration = shot["end"] - shot["start"]
        durations.append(duration)
        constraints = row.get("timing_constraints", [])
        if not isinstance(constraints, list):
            constraints = [None]
        for constraint in constraints:
            valid = isinstance(constraint, dict) and _concrete(constraint.get("label"))
            if valid:
                start, end = constraint.get("start"), constraint.get("end")
                valid = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value) for value in (start, end))
                valid = valid and 0 <= start < end <= duration
            if not valid:
                errors.append({"code": "AUTHORING_TRACE_TIMING_CONSTRAINT", "shot": key,
                               "message": "作者给定的局部时间区间须具体、有限且完整落在镜窗内"})
        body = _body(shot)
        if META_WORDS.search(body):
            errors.append({"code": "AUTHORING_META_PROMPT", "shot": key,
                           "message": "后台纠错说明不能进入提示词正文"})
        if (any(phrase in body for phrase in TEMPLATE_PHRASES) or TEMPLATE_PATTERN.search(body)):
            errors.append({"code": "AUTHORING_TEMPLATE_PHRASE", "shot": key,
                           "message": "正文含固定句壳，须写本镜具体执行内容"})
        if GENERIC_CAMERA.search(body):
            errors.append({"code": "AUTHORING_GENERIC_CAMERA", "shot": key,
                           "message": "摄影路径不得使用泛化占位句"})
        if GENERIC_LIGHT.search(body) or not CONCRETE_LIGHT.search(body) or not MAIN_FILL_LIGHT.search(body):
            errors.append({"code": "AUTHORING_LIGHTING_UNDER_SPECIFIED", "shot": key,
                           "message": "正文须落实具体主光、辅光或反射关系和明暗落点"})
        if not DEPTH_WORDS.search(body):
            errors.append({"code": "AUTHORING_DEPTH_MISSING", "shot": key,
                           "message": "正文须落实实际清晰与焦外关系"})
        if GENERIC_STATION.search(unit.get("global", "")):
            errors.append({"code": "AUTHORING_GENERIC_STATION", "unit": unit["draft_key"],
                           "message": "站位不得使用抽象占位说明"})
        match_transition = re.search(r"；转场：([^）]+）)", shot.get("text", ""))
        transitions.append(match_transition.group(1).rstrip("）") if match_transition else "")
        match_sound = re.search(r"音效：(.+?)。$", shot.get("text", ""))
        sounds.append(match_sound.group(1).strip() if match_sound else "")
        normalized.append((key, _normalized_body(shot)))
    selected_records = {rid for unit in units for rid in unit["source_records"]}
    if selected_records - mapped_records:
        errors.append({"code": "AUTHORING_TRACE_SOURCE_COVERAGE",
                       "records": sorted(selected_records - mapped_records),
                       "message": "镜级来源映射未覆盖选定内容"})
    omissions = trace.get("source_cue_omissions", [])
    if not isinstance(omissions, list):
        omissions = [None]
    omitted = set()
    for omission in omissions:
        rid = omission.get("source_record") if isinstance(omission, dict) else None
        cue = omission.get("cue") if isinstance(omission, dict) else None
        if (rid not in selected_records or cue not in _source_cues(records.get(rid, {}))
                or not _concrete(omission.get("reason"))):
            errors.append({"code": "AUTHORING_SOURCE_CUE_MAPPING",
                           "message": "省略记录须定位原cue并给出待语义审阅的具体合法依据"})
        else:
            omitted.add((rid, cue))
    required_cues = {(rid, cue) for rid in selected_records for cue in _source_cues(records[rid])}
    missing_cues = required_cues - mapped_cues - omitted
    if missing_cues:
        errors.append({"code": "AUTHORING_SOURCE_CUE_COVERAGE", "cues": sorted(missing_cues),
                       "message": "来源括号要求缺少具体镜头定位或待审省略依据；总括状态不能代替"})
    if len(durations) >= 6 and max(durations.count(value) for value in set(durations)) / len(durations) >= 0.65:
        if not _concrete(trace.get("timing_distribution_basis")):
            errors.append({"code": "AUTHORING_UNIFORM_TIMING", "message": "镜长高度集中须说明来源节奏依据"})
    if len(transitions) >= 6 and len(set(transitions)) == 1:
        if transitions[0] == "硬切" or not _concrete(trace.get("transition_distribution_basis")):
            errors.append({"code": "AUTHORING_UNIFORM_TRANSITION", "message": "统一转场须核对具体观看锚点"})
    if len(sounds) >= 6 and len(set(sounds)) == 1:
        errors.append({"code": "AUTHORING_UNIFORM_SOUND", "message": "统一音效须回到实际声源核对"})
    for (left_key, left), (right_key, right) in zip(normalized, normalized[1:]):
        if _similar(left, right) >= 0.78:
            errors.append({"code": "AUTHORING_TEMPLATE_REUSE", "shots": [left_key, right_key],
                           "message": "相邻正文骨架高度重复，须实际复核观看作用"})
    payload = {"policy": POLICY, "version": VERSION,
               "trace_sha256": hashlib.sha256(trace_bytes).hexdigest(),
               "draft_sha256": draft_sha256, "context_sha256": expected_context_sha256,
               "shot_count": len(expected_keys), "source_span": trace.get("source_span")}
    return {**payload, "enforced": True, "status": "blocked" if errors else "passed",
            "errors": errors, "trace_path": str(path), "shot_keys": expected_keys,
            "preferred_target_summary": organization.get("preferred_target_summary", {}),
            "unit_boundary_candidates": organization.get("same_scene_boundary_candidates", []),
            "semantic_review": "not_performed",
            "limits": "仅核验来源定位、绑定、结构、已给定时窗及确定词面风险；不证明自然镜长、动作、可见性、保持或省略合法。",
            "gate_digest": _digest(payload)}


def validate(project, state, draft_bytes, units, expected_context_sha256,
             explicit_trace=None, draft_path=None):
    """Read-only dispatch; old authored sidecars keep their original contract."""
    return _validate_for_delivery(project, state, draft_bytes, units, expected_context_sha256,
                                  explicit_trace, draft_path)


def _validate_for_delivery(project, state, draft_bytes, units, expected_context_sha256,
                           explicit_trace=None, draft_path=None, *, observations_summary=None):
    """Reuse statistics only from the private delivery pipeline's fresh parse."""
    if state.get("storyboard_authoring_policy") != POLICY:
        return {"enforced": False, "policy": POLICY, "status": "legacy_compatible"}
    path = trace_path(draft_path or project, explicit_trace)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        value = None
    validator = _validate_v3 if isinstance(value, dict) and value.get("version") == LEGACY_VERSION else _validate_v4
    if validator is _validate_v4:
        return validator(project, state, draft_bytes, units, expected_context_sha256,
                         explicit_trace, draft_path, observations_summary=observations_summary)
    return validator(project, state, draft_bytes, units, expected_context_sha256,
                     explicit_trace, draft_path)


class AuthoringGateError(ValueError):
    """Keep the legacy message while exposing all original error locations."""

    def __init__(self, message, report):
        super().__init__(message)
        self.report = copy.deepcopy(report)


def require(report):
    if report.get("enforced") and report.get("status") != "passed":
        errors = report.get("errors") or [{"message": "逐镜写作闸门未通过"}]
        preview = "; ".join(str(item.get("message", item.get("code", "error"))) for item in errors[:8])
        raise AuthoringGateError("逐镜写作闸门：" + preview, report)

