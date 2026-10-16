"""Opt-in, read-only identity and speech checks before and during shot drafting.

This is a mechanical gate, not a source/acting review. It never rewrites quotes,
adds subjects, chooses a character from a surname, or accepts shot reviews.
An audio-only plan uses the normal unit.audio plus audio_plan=[{record, parts}].
Completed shots use their dialogue_parts instead. Do not keep two diverging plans.
"""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from dialogue_layout import estimate, pause_seconds, PUNCTUATION, TAILS
from speech_timing_audit import SPEEDS

POLICY = "scene-review-b-v2"
LEGACY_POLICY = "scene-review-b-v1"
RENDER_FIELDS = ("text", "connector", "camera", "composition", "depth_of_field",
                 "lighting", "label", "transition", "sound_foreground", "sound_environment")
CLASSIFICATIONS = {"visual_reference", "voice_reference", "prose_mention", "protected_text", "not_character"}
NUMERIC_OR_LATIN = re.compile(r"[0-9A-Za-z]")


def value_digest(value):
    """Hash the exact field, including punctuation and whitespace."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _line(value):
    return isinstance(value, str) and bool(value.strip()) and not any(c in value for c in "\r\n")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _issue(code, detail, **context):
    return {"code": code, "kind": detail, **context}


def _report(errors, warnings, **extra):
    return {"status": "failed" if errors else "passed", "errors": errors, "warnings": warnings, **extra}


def _count_readout(value):
    # Numbers and Latin text need an explicit, countable Chinese pronunciation.
    # Hyphenated syllable tokens are also accepted for a foreign-language line;
    # never assume one Latin character equals one sound unit.
    if isinstance(value, list) and value and all(_line(s) for s in value):
        return len(value)
    if _line(value) and not NUMERIC_OR_LATIN.search(value):
        return sum(c.isalnum() for c in value)
    return None


def _parts(unit, errors):
    plans = unit.get("audio_plan")
    planned = {}
    if plans is not None:
        if not isinstance(plans, list):
            errors.append(_issue("AUDIO_PLAN_SCHEMA", "audio_plan须为record/parts数组"))
        else:
            for plan in plans:
                if (not isinstance(plan, dict) or not _line(plan.get("record"))
                        or not isinstance(plan.get("parts"), list) or plan["record"] in planned):
                    errors.append(_issue("AUDIO_PLAN_SCHEMA", "声音计划记录非法或重复"))
                    continue
                planned[plan["record"]] = plan["parts"]
    grouped = {}
    shots = unit.get("shots")
    if isinstance(shots, list) and shots:
        for n, shot in enumerate(shots, 1):
            if not isinstance(shot, dict) or not isinstance(shot.get("dialogue_parts", []), list):
                errors.append(_issue("AUDIO_SHOT_SCHEMA", "镜头须为对象且dialogue_parts须为数组", shot=n))
                continue
            for part in shot.get("dialogue_parts", []):
                if not isinstance(part, dict) or not _line(part.get("record")):
                    errors.append(_issue("AUDIO_PART_SCHEMA", "dialogue_parts须含record", shot=n))
                    continue
                grouped.setdefault(part["record"], []).append(part)
                if (all(_number(part.get(k)) and _number(shot.get(k)) for k in ("start", "end"))
                        and not shot["start"] <= part["start"] < part["end"] <= shot["end"] + .001):
                    errors.append(_issue("AUDIO_SHOT_BOUNDS", "台词片段须完整落在本镜", record=part["record"], shot=n))
        if planned:
            normalized = lambda ps: [{k: v for k, v in p.items() if k != "record"} if isinstance(p, dict) else p for p in ps]
            if set(planned) != set(grouped) or any(normalized(planned[r]) != normalized(grouped[r]) for r in set(planned) & set(grouped)):
                errors.append(_issue("AUDIO_PLAN_DIVERGED", "声音计划与镜头台词已分叉；修订统一从当前有效计划同步镜头片段"))
        return grouped
    return planned


def check_audio_plan(unit):
    """Validate speech independently of photography; no workflow opt-in needed.

    Each part explicitly registers pause (internal seconds), spoken_units and
    spoken_basis when an override is needed. Digit/Latin parts additionally need
    spoken_readout: Chinese pronunciation or an explicit list of spoken syllables.
    Event.pause equals sum(part.pause) + sum(inter-part gaps); punctuation and
    breath_basis never add another implicit pause to this ledger.
    """
    errors, warnings, estimates = [], [], []
    if not isinstance(unit, dict):
        return _report([_issue("AUDIO_SCHEMA", "声音计划unit须为对象")], [])
    events = unit.get("audio", [])
    if not isinstance(events, list):
        return _report([_issue("AUDIO_SCHEMA", "audio须为数组")], [])
    parts_by_record = _parts(unit, errors)
    ids = [e.get("record") if isinstance(e, dict) else None for e in events]
    valid_ids = [r for r in ids if _line(r)]
    if len(valid_ids) != len(ids) or len(valid_ids) != len(set(valid_ids)):
        errors.append(_issue("AUDIO_RECORDS", "声音事件record为空或重复"))
    for rid in set(parts_by_record) - set(r for r in ids if isinstance(r, str)):
        errors.append(_issue("AUDIO_UNKNOWN_RECORD", "片段对应的声音事件不存在", record=rid))
    for event in events:
        if not isinstance(event, dict):
            errors.append(_issue("AUDIO_SCHEMA", "声音事件须为对象"))
            continue
        rid = event.get("record")
        def err(code, detail, **kw):
            errors.append(_issue(code, detail, record=rid, **kw))
        def warn(code, detail, **kw):
            warnings.append(_issue(code, detail, record=rid, **kw))
        if not _line(rid):
            continue
        recording = event.get("timing_basis") == "recording"
        if recording and not _line(event.get("recording_evidence")):
            err("AUDIO_RECORDING_EVIDENCE", "实配音须注明测量依据，不自动转为文本估时")
        if not _line(event.get("text")):
            err("AUDIO_TEXT", "声音事件须保留非空原台词")
            continue
        valid_event = (all(_number(event.get(k)) for k in ("start", "end", "pause"))
                       and 0 <= event["start"] < event["end"] and 0 <= event["pause"] < event["end"] - event["start"])
        if not valid_event:
            err("AUDIO_TIME", "声音时码及总停顿须有限、非负且保留实际发声时间")
        if not recording and (type(event.get("units")) is not int or event["units"] <= 0):
            err("AUDIO_UNITS", "整句发音单位须为正整数")
        if not isinstance(event.get("speed"), str) or event["speed"] not in SPEEDS:
            err("AUDIO_SPEED", "语速档位无效")
        parts = parts_by_record.get(rid, [])
        if not parts:
            err("AUDIO_PARTS_REQUIRED", "先按语义登记声音片段，再安排摄影镜头")
            continue
        if not all(isinstance(p, dict) and _line(p.get("text")) for p in parts):
            err("AUDIO_PART_SCHEMA", "每个声音片段须有非空原文text")
            continue
        if "".join(p["text"] for p in parts) != event["text"]:
            err("AUDIO_SOURCE_CHANGED", "片段拼接与原台词不逐字一致，存在遗漏、重复或改词")
        units, pauses, gaps, all_times = [], [], [], True
        previous_end = event.get("start")
        for i, part in enumerate(parts):
            at = {"part": i + 1}
            text = part["text"]
            compact = "".join(c for c in text if c.isalnum())
            if not compact:
                err("AUDIO_PUNCTUATION_ONLY", "纯标点不能独立分配为台词", **at)
            if len(parts) > 1:
                if len(compact) == 1 and compact in TAILS:
                    err("AUDIO_ISOLATED_TAIL", "不能孤立句尾语气词", **at)
                if text.lstrip()[0] in PUNCTUATION:
                    err("AUDIO_LEADING_PUNCTUATION", "不能把标点留给下一片段", **at)
                if i < len(parts) - 1 and text.rstrip()[-1] not in PUNCTUATION and not _line(part.get("breath_basis")):
                    err("AUDIO_SEMANTIC_CUT", "无标点切点须说明自然语义气口，不能机械切字", **at)
            explicit = part.get("spoken_units") if not recording else None
            readout = part.get("spoken_readout")
            special = bool(NUMERIC_OR_LATIN.search(text))
            count = sum(c.isalnum() for c in text) if not special else None
            if readout is not None and not recording:
                count = _count_readout(readout)
                if count is None or count <= 0:
                    err("AUDIO_READOUT", "spoken_readout须为可独立计数的中文读法或逐发音单位列表", **at)
                if not _line(part.get("spoken_basis")):
                    err("AUDIO_READOUT_BASIS", "显式实际读法须注明spoken_basis，不能凭空改变原文读音", **at)
            if special and not recording and (readout is None or not _line(part.get("spoken_basis")) or explicit is None):
                err("AUDIO_READOUT_REQUIRED", "含数字或英文的片段（包括单片段）须登记spoken_readout、spoken_basis和spoken_units", **at)
            if explicit is not None:
                if type(explicit) is not int or explicit <= 0 or not _line(part.get("spoken_basis")):
                    err("AUDIO_PART_UNITS", "显式spoken_units须为正整数并注明spoken_basis", **at)
                    explicit = None
                elif count is not None and explicit != count:
                    err("AUDIO_READOUT_COUNT", "片段发音单位与独立读法计数不一致", declared=explicit, counted=count, **at)
            # Never borrow event.units even when there is only one part.
            value = count if count is not None else explicit
            units.append(value)
            pause = part.get("pause")
            if recording and pause is None:
                pauses.append(None)
            elif not _number(pause) or pause < 0:
                err("AUDIO_PART_PAUSE", "每片段须显式登记有限非负pause；不能混用估算分配", **at)
                pauses.append(None)
            else:
                pauses.append(pause)
            valid = (all(_number(part.get(k)) for k in ("start", "end")) and part["start"] < part["end"])
            if not valid:
                err("AUDIO_PART_TIME", "片段须有实际发声起止时码", **at)
                all_times = False
                previous_end = None
                continue
            t0, t1 = part["start"], part["end"]
            if _number(previous_end):
                gap = round(t0 - previous_end, 3)
                gaps.append(gap)
                if gap < -.001:
                    err("AUDIO_TIME_ORDER", "片段时码倒序或重叠", **at)
                if i:
                    ending = parts[i - 1]["text"].rstrip()[-1]
                    limit = .5 if ending in "。？！?!；;…—～~" else .25
                    if gap > limit + .001:
                        warn("AUDIO_LONG_GAP", "分句间停顿偏长需结合原文和表演核实", gap_seconds=gap, gap_basis=part.get("gap_basis", ""), **at)
            else:
                all_times = False
            previous_end = t1
            if valid_event and not event["start"] - .001 <= t0 < t1 <= event["end"] + .001:
                err("AUDIO_EVENT_BOUNDS", "片段时码超出声音事件", **at)
            if _number(pause) and pause >= t1 - t0:
                err("AUDIO_NO_SPEECH_TIME", "片内停顿占满发声片段", **at)
            elif (not recording and value and _number(pause) and pause >= 0
                  and isinstance(event.get("speed"), str) and event["speed"] in SPEEDS):
                low, high = SPEEDS[event["speed"]]
                net = t1 - t0 - pause
                rate = value / net
                punctuation = pause_seconds(text)
                minimum = round(estimate(text, value, high) - punctuation + pause, 3)
                maximum = round(estimate(text, value, low) - punctuation + pause, 3)
                estimates.append({"record": rid, "part": i + 1, "units": value, "source_pause": punctuation,
                                  "registered_pause": pause, "min_seconds": minimum, "max_seconds": maximum})
                if not low - .01 <= rate <= high + .01:
                    warn("AUDIO_RATE", "片段估计净语速超档，须人工核实；不自动拉长台词", rate=round(rate, 3), min_seconds=minimum, max_seconds=maximum, **at)
        if not recording and all(type(v) is int for v in units) and type(event.get("units")) is int and sum(units) != event["units"]:
            err("AUDIO_UNITS_TOTAL", "片段独立发音单位合计与整句不一致", event_units=event["units"], fragment_units=sum(units))
        if all_times and valid_event:
            if abs(parts[0]["start"] - event["start"]) > .001 or abs(parts[-1]["end"] - event["end"]) > .001:
                err("AUDIO_ENDPOINTS", "片段须覆盖声音事件实际首尾")
            if all(p is not None for p in pauses) and len(gaps) == len(parts):
                total = sum(pauses) + sum(gaps[1:])
                if abs(total - event["pause"]) > .003:
                    err("AUDIO_PAUSE_TOTAL", "整句总停顿须等于片内停顿加片间间隙", registered_pause=event["pause"], counted_pause=round(total, 3))
    return _report(errors, warnings, estimates=estimates)


def _scene_characters(scene):
    result = set(scene.get("character_ids", []))
    # Same-person visual variants remain explicitly registered assets.
    for track in scene.get("character_appearances", []):
        result.update(track.get(k) for k in ("base_asset_id", "initial_asset_id") if track.get(k))
        for change in track.get("transitions", []):
            result.update(change.get(k) for k in ("from_asset_id", "to_asset_id") if change.get(k))
    return result


def _voice_ids(unit):
    ids = set()
    for event in unit.get("audio", []):
        if not isinstance(event, dict):
            continue
        values = event.get("character_ids", []) if "character_ids" in event else [event.get("character_id")]
        if isinstance(values, list):
            ids.update(cid for cid in values if _line(cid))
    return ids


def _speaker(item):
    """Use the director's existing full source-entry contract, never a surname."""
    if _line(item.get("source_speaker")):
        return item["source_speaker"]
    entry = item.get("source_entry", item.get("name", ""))
    return entry.split("-群像-", 1)[0] + "-群像" if "-群像-" in entry else entry.split("-", 1)[0]


def _current_assets(unit):
    """Return assets reachable in each shot, replaying only declared changes."""
    current = {key: item.get("asset_id") for key, item in unit.get("continuity", {}).get("initial", {}).items()}
    result = []
    for shot in unit.get("shots", []):
        reached = set(v for v in current.values() if v)
        for change in shot.get("continuity", {}).get("changes", []):
            aid = change.get("set", {}).get("asset_id")
            if aid:
                current[change.get("entity")] = aid
                reached.add(aid)
        result.append(reached)
    return result


def _protected_spans(value, ledger, source_texts):
    spans = []
    for match in re.finditer(r"《[^》]*》", value):
        spans.append(match.span())
    for match in re.finditer(r"[“「]([^”」]*)[”」]", value):
        if any(match.group(0) in text or match.group(1) == text for text in source_texts):
            spans.append(match.span())
    # Exact ledger place/prop labels protect a name embedded in a genuine title.
    person_names = {term for item in ledger.get("characters", []) for term in
                    (item.get("name"), item.get("source_entry"), item.get("source_name"),
                     _speaker(item), *item.get("aliases", [])) if _line(term)}
    for category in ("locations", "scene_assets", "props"):
        for item in ledger.get(category, []):
            for name in (item.get("name"), item.get("source_name"), *item.get("aliases", [])):
                if _line(name) and name not in person_names:
                    spans.extend(m.span() for m in re.finditer(re.escape(name), value))
    return spans


def lint_identity(state, unit):
    """Flag literal, scene-bound candidates; decisions never change asset lists.

    identity_decisions entries bind field, value_sha256, term, start (zero-based),
    classification, basis, and asset_id for a character classification. A visual
    reference must already use @{ID} (or prompt_only's full name); a prose/voice
    mention can be accepted explicitly without adding an image reference.
    """
    errors, warnings, candidates = [], [], []
    ledger = state.get("ledger", {})
    characters = {c["id"]: c for c in ledger.get("characters", [])}
    scenes = {s["id"]: s for s in ledger.get("scenes", [])}
    selected = set(unit.get("scene_ids", []))
    allowed = set().union(*(_scene_characters(scenes[s]) for s in selected if s in scenes)) if selected else set()
    aliases = {}
    for aid in allowed & set(characters):
        item = characters[aid]
        # source_speaker is explicit ledger evidence. No splitting names/surnames.
        for term in (item.get("name"), item.get("source_entry"), item.get("source_name"),
                     _speaker(item), *item.get("aliases", [])):
            if _line(term):
                aliases.setdefault(term, set()).add(aid)
    voices = _voice_ids(unit)
    source_texts = [r.get("text", "") for r in state.get("records", []) if r.get("scene") in selected]
    current_by_shot = _current_assets(unit)
    fields = [("global", unit.get("global", ""), None)]
    fields += [(f"shots[{i}].{field}", shot[field], i) for i, shot in enumerate(unit.get("shots", []))
               for field in RENDER_FIELDS if isinstance(shot.get(field), str)]
    decisions = unit.get("identity_decisions", [])
    if not isinstance(decisions, list):
        return _report([_issue("IDENTITY_DECISIONS", "identity_decisions须为数组")], [])
    used_decisions = set()
    for path, value, shot_index in fields:
        if not isinstance(value, str) or not value:
            continue
        protected = _protected_spans(value, ledger, source_texts)
        protected += [m.span() for m in re.finditer(r"@\{[^{}]+\}", value)]
        for item in characters.values():
            if _line(item.get("name")):
                protected += [m.span() for m in re.finditer(r"@" + re.escape(item["name"]) + r"(?= )", value)]
        consumed = []
        local_ids = (allowed if shot_index is None else
                     _scene_characters(scenes.get(unit["shots"][shot_index].get("scene_id"), {})))
        for term in sorted(aliases, key=lambda t: (-len(t), t)):
            term_ids = aliases[term] & local_ids
            if not term_ids:
                continue
            for match in re.finditer(re.escape(term), value):
                start, end = match.span()
                if any(a <= start and end <= b for a, b in protected + consumed):
                    continue
                if term.isascii() and ((start and value[start - 1].isascii() and value[start - 1].isalnum()) or
                                       (end < len(value) and value[end].isascii() and value[end].isalnum())):
                    continue
                consumed.append((start, end))
                candidate_ids = sorted(term_ids)
                reached = (current_by_shot[shot_index] if shot_index is not None and shot_index < len(current_by_shot)
                           else {item.get("asset_id") for item in unit.get("continuity", {}).get("initial", {}).values()
                                 if _line(item.get("asset_id"))})
                item = {"field": path, "value_sha256": value_digest(value), "term": term, "start": start,
                        "candidate_ids": candidate_ids, "current_asset_ids": sorted(reached & set(candidate_ids)),
                        "voice_asset_ids": sorted(voices & set(candidate_ids)), "resolved": False, "resolution": "pending"}
                matched = [(i, d) for i, d in enumerate(decisions) if isinstance(d, dict)
                           and all(d.get(k) == item[k] for k in ("field", "value_sha256", "term", "start"))]
                if len(matched) > 1:
                    errors.append(_issue("IDENTITY_DUPLICATE_DECISION", "同一候选存在多条决定", field=path, term=term, start=start))
                elif matched:
                    n, decision = matched[0]
                    used_decisions.add(n)
                    cls, aid = decision.get("classification"), decision.get("asset_id")
                    required = {"field", "value_sha256", "term", "start", "classification", "basis"}
                    valid = (cls in CLASSIFICATIONS and _line(decision.get("basis"))
                             and required <= set(decision) <= required | {"asset_id"}
                             and type(decision.get("start")) is int)
                    if cls in ("visual_reference", "voice_reference", "prose_mention"):
                        valid = valid and aid in candidate_ids
                    if cls == "voice_reference":
                        valid = valid and aid in voices
                    if cls == "visual_reference":
                        # An image-asset alias is corrected in text, never exempted.
                        valid = valid and characters.get(aid, {}).get("visual_mode") == "prompt_only" and term == characters[aid]["name"]
                    if valid:
                        item.update(resolved=True, resolution="classified", classification=cls, basis=decision["basis"])
                    else:
                        errors.append(_issue("IDENTITY_INVALID_DECISION", "身份决定不合法；视觉资产须使用稳定引用，声音与普通提及不能自动增添出镜资产", field=path, term=term, start=start))
                if not item["resolved"]:
                    warnings.append(_issue("IDENTITY_REVIEW_REQUIRED", "裸名或已登记别名须按本场身份分类核实；不自动替换", **item))
                candidates.append(item)
    for n, decision in enumerate(decisions):
        if n not in used_decisions:
            errors.append(_issue("IDENTITY_STALE_DECISION", "身份决定未绑定当前字段原值及候选，须复核或移除旧决定", decision=n))
    return _report(errors, warnings, candidates=candidates)


def _source_audio(state, unit):
    """Check supplied source records without requiring a full photography draft."""
    errors = []
    records = {r["id"]: r for r in state.get("records", [])}
    chars = {c["id"]: c for c in state.get("ledger", {}).get("characters", [])}
    span = unit.get("span")
    span_valid = isinstance(span, list) and len(span) == 2 and all(type(x) is int for x in span)
    if span_valid:
        expected = [r["id"] for r in records.values() if r.get("kind") == "dialogue"
                    and all(type(r.get(k)) is int for k in ("start", "end"))
                    and r["start"] < span[1] and r["end"] > span[0]]
        actual = [e.get("record") for e in unit.get("audio", []) if isinstance(e, dict)]
        if actual != expected:
            errors.append(_issue("AUDIO_SOURCE_COVERAGE", "声音计划须完整且按来源顺序覆盖选定范围台词"))
    for event in unit.get("audio", []):
        if not isinstance(event, dict) or not _line(event.get("record")):
            continue
        rid = event.get("record")
        record = records.get(rid)
        if record is None:
            if records:
                errors.append(_issue("AUDIO_SOURCE_RECORD", "声音记录不属于来源", record=rid))
            continue
        text = record.get("text")
        if "kind" in record and record["kind"] != "dialogue":
            errors.append(_issue("AUDIO_SOURCE_RECORD", "声音事件须对应原文台词记录", record=rid))
        if span_valid and all(type(record.get(k)) is int for k in ("start", "end")):
            from continuity import portion
            text = portion(record, span)
        if event.get("text") != text:
            errors.append(_issue("AUDIO_SOURCE_CHANGED", "声音计划原台词与选定原文不一致", record=rid))
        if "voice" in record and event.get("voice") != record["voice"]:
            errors.append(_issue("AUDIO_SOURCE_VOICE", "现场/OS等声源类型与原文不一致", record=rid))
        ids = event.get("character_ids", []) if "character_ids" in event else [event.get("character_id")]
        if not isinstance(ids, list) or not ids or any(not _line(cid) or cid not in chars for cid in ids):
            errors.append(_issue("AUDIO_SOURCE_IDENTITY", "声音身份须对应已登记人物", record=rid))
        elif len(ids) == 1 and record.get("speaker") and record["speaker"] != _speaker(chars[ids[0]]):
            errors.append(_issue("AUDIO_SOURCE_IDENTITY", "声音身份与原文说话人不一致", record=rid))
    return errors


def preflight(state, request):
    """Opt-in request gate. Returns serializable diagnostics without mutations."""
    errors, warnings, units = [], [], []
    if not isinstance(state, dict) or not isinstance(request, dict) or not isinstance(request.get("units"), list):
        return _report([_issue("PREFLIGHT_SCHEMA", "state/request须为对象且request.units须为数组")], [], policy=POLICY, units=[])
    for index, unit in enumerate(request.get("units", [])):
        if not isinstance(unit, dict):
            errors.append(_issue("PREFLIGHT_SCHEMA", "unit须为对象", unit=index + 1))
            continue
        key = unit.get("id", f"unit-{index + 1}")
        if unit.get("workflow_policy") not in (POLICY, LEGACY_POLICY):
            if unit.get("workflow_policy") is not None:
                errors.append(_issue("WORKFLOW_POLICY", "未知的workflow_policy不能绕过前置检查", unit=key))
            units.append({"unit": key, "status": "skipped", "reason": "legacy_or_unmarked"})
            continue
        try:
            audio = check_audio_plan(unit)
            identity = lint_identity(state, unit)
            ue = audio["errors"] + identity["errors"] + _source_audio(state, unit)
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            errors.append(_issue("PREFLIGHT_SCHEMA", "前置数据结构不合法：" + str(exc), unit=key))
            units.append({"unit": key, "status": "failed"})
            continue
        readiness = None
        if unit.get("workflow_policy") == POLICY:
            from authoring_gates import audio_ready
            readiness = audio_ready(state, unit, audio)
        uw = audio["warnings"] + identity["warnings"]
        errors.extend(dict(item, unit=key) for item in ue)
        warnings.extend(dict(item, unit=key) for item in uw)
        units.append({"unit": key, "status": "failed" if ue else "passed", "audio": audio, "identity": identity, "readiness": readiness})
    return _report(errors, warnings, policy=POLICY, units=units)


def validate(state, request):
    result = preflight(state, request)
    if result["errors"]:
        details = "; ".join(f'{e.get("unit", "")}: {e["code"]} {e["kind"]}' for e in result["errors"][:8])
        raise ValueError("分镜前置检查：" + details)
    blocked = [u for u in result['units'] if u.get('readiness') and u['readiness']['status'] != 'ready']
    if blocked:
        raise ValueError('声音尚不具备进入详细摄影/送审的条件：' + json.dumps(blocked, ensure_ascii=False))
    return result["warnings"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--state", type=Path)
    source.add_argument("--project", type=Path, help="读取已绑定项目，核验来源与风格")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--plan", type=Path, help="独立声音计划unit JSON，不要求shots")
    args = parser.parse_args(argv)
    read = lambda p: json.loads(p.read_text(encoding="utf-8-sig"))
    state = None
    try:
        if args.project:
            from director import load
            state = load(args.project)
        elif args.state:
            state = read(args.state)
        if state is not None and not isinstance(state, dict):
            raise ValueError("state须为对象")
        if args.plan:
            if args.request:
                parser.error("--plan与--request二选一")
            plan = read(args.plan)
            result = check_audio_plan(plan)
            if state is not None and isinstance(plan, dict):
                result["errors"].extend(_source_audio(state, plan))
                result["status"] = "failed" if result["errors"] else "passed"
            if isinstance(plan, dict) and plan.get('workflow_policy') == POLICY:
                from authoring_gates import audio_ready
                result['readiness'] = audio_ready(state or {}, plan, result)
        else:
            if state is None or not args.request:
                parser.error("须提供--project或--state以及--request，或独立--plan")
            result = preflight(state, read(args.request))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result = _report([_issue("PREFLIGHT_INPUT", "无法读取或校验前置输入：" + str(exc))], [])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    blocked = result.get("readiness", {}).get("status") == "blocked" or any(u.get("readiness") and u["readiness"]["status"] == "blocked" for u in result.get("units", []))
    return 1 if result["errors"] or blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
