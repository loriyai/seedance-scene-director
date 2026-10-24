#!/usr/bin/env python3
"""Audit source coverage and dialogue attribution before assembling a script."""

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

from asset_ledger import digest, scene_ranges, source_paragraphs, split_cast_entry
from cast_contract import validate_cast_entries
import source_compat


SCENE_FILE = re.compile(r"scene_(\d+)_(\d+)-(\d+)\.txt$")
TRANSITION = re.compile(r"^(?:遮挡转场|转场)\s*")
REVIEW_VERSION = 9
FRAGMENT_POLICY = "source-fragments-v1"
BOUNDARY_POLICY = "narrative-space-v1"
# A finite risk recognizer, not a scene splitter. Unlisted vehicle names still
# require the author's scene-boundary review; matches never prove an error.
MOBILE_INTERIOR = re.compile(r"(?:车内|车厢(?:内)?|驾驶室|驾驶舱|机舱|船舱|"
                             r"(?:老头乐|汽车|轿车|房车|客车|巴士|公交车|出租车|列车|火车|地铁)内)$")
BOUNDARY_REASONS = {"explicit_transition", "time_change", "location_change",
                    "event_discontinuity", "independent_vehicle_space"}
# Diagnostic only: locate source evidence inside an existing scene, including
# late location speech. Never infer a destination/current position or split.
LOCATION_WORD = (r"(?:弯道|弯路|路口|街道|街|道路|公路|巷|桥|广场|车站|机场|"
                 r"医院|学校|房间|院|门口|楼|泳池|池边|岸边)")
INTERNAL_MOVEMENT = re.compile(
    r"(?:驶离|驶入|驶出|开进|开出|转入|抵达|到达|来到|走进|走出|进入|离开|通过)"
    r"[^。！？\n]{0,16}" + LOCATION_WORD)
CURRENT_LOCATION_SPEECH = re.compile(
    r"(?:速来|快来|赶来|我(?:现在|就)?在|我们(?:现在|就)?在|当前位置[：:])"
    r"[^。！？\n，,；;]{0,18}(?:" + LOCATION_WORD + r"|这里|这儿|此处)")


def compact(value):
    return "".join(char for char in value if char.isalnum())


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def scene_files(workdir):
    paths = []
    for path in (workdir / "scenes").glob("*.txt"):
        match = SCENE_FILE.fullmatch(path.name)
        if not match:
            raise ValueError(f"逐场文件名不符合格式：{path.name}")
        paths.append((int(match.group(1)), path))
    paths.sort()
    return paths


def fingerprint_workdir(workdir):
    paragraphs = source_paragraphs(workdir / "source.json")
    paths = scene_files(workdir)
    scene_ranges(workdir, len(paths), len(paragraphs))
    return {"source_digest": digest(paragraphs),
            "drafts": {path.name: sha(path.read_text(encoding="utf-8-sig")) for _, path in paths}}


def cue_reason(value, names):
    squeezed = re.sub(r"\s+", "", value)
    if not squeezed:
        return "空段"
    if source_compat.standalone_voice(squeezed):
        return "独立声音提示"
    if squeezed in names:
        return "独立角色名提示"
    if any(squeezed.startswith(name) and source_compat.standalone_voice(squeezed[len(name):]) for name in names):
        return "独立角色声音提示"
    return source_compat.structural_hint(value)


def explicit_label(value, names):
    label = source_compat.explicit_label(value, names)
    return {key: label[key] for key in ("speaker", "cue", "text")} if label else None


def normalized_cue(value):
    value = compact(value).lower()
    return "内心想法" if value in ("os", "内心os", "内心想法") else value


def draft_parts(path):
    lines = [(i, line.strip()) for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1) if line.strip()]
    if len(lines) < 2 or not lines[0][1].startswith("场景") or not lines[1][1].startswith("人物："):
        raise ValueError(f"场景中间稿缺少标题或人物行：{path.name}")
    cast = lines[1][1]
    if not cast.endswith("；"):
        raise ValueError(f"人物行未以分号结束：{path.name}")
    entries = [] if cast == "人物：无；" else cast[3:-1].split("；")
    errors = validate_cast_entries(entries)
    if errors:
        raise ValueError(f"{path.name} 人物行不合法：" + "；".join(errors))
    names = [split_cast_entry(part)[0] for part in entries]
    body, dialogues = [], []
    position = 0
    for number, line in lines[2:]:
        if line.startswith("△ "):
            kind, value, speaker = "action", TRANSITION.sub("", line[2:]), None
        elif "：" in line:
            label, value = line.split("：", 1)
            speaker = re.sub(r"[（(][^（）()]*[）)]$", "", label).strip()
            kind = "dialogue"
            cue = re.search(r"[（(]([^（）()]*)[）)]$", label)
            dialogues.append({"line": number, "speaker": speaker, "text": value,
                              "cue": cue.group(1) if cue else "",
                              "non_character": bool(cue and cue.group(1) == "非人物播报")})
        else:
            raise ValueError(f"{path.name}:{number} 正文行无法识别")
        normalized = compact(value)
        body.append({"line": number, "kind": kind, "text": value, "start": position, "end": position + len(normalized)})
        position += len(normalized)
    return lines[0][1], names, body, dialogues


def quote_blocks(paragraphs, start, end):
    return source_compat.quote_blocks(paragraphs, start, end)


def cue_hint(paragraphs, start, paragraph, names):
    ordered = sorted(names, key=len, reverse=True)
    for i in range(paragraph, max(start - 1, paragraph - 9), -1):
        value = paragraphs[i - 1].strip()
        if i < paragraph and any(char in value for char in source_compat.QUOTE_CLOSE):
            break
        label = explicit_label(value, ordered)
        block_count = len(quote_blocks([value], 1, 1))
        if label and block_count <= 1:
            return label["speaker"]
        for name in ordered:
            if value.startswith(name):
                suffix = value[len(name):].strip()
                if block_count > 1:
                    return None
                if not suffix or source_compat.standalone_voice(suffix) or suffix.startswith(tuple("：:" + source_compat.QUOTE_OPEN)):
                    return name
                # A later clause's speech verb does not make the first action
                # subject its speaker. Complex/multiple-speaker cues stay manual.
                if any(other != name and other in suffix for other in names):
                    return None
                if re.match(r"^(?:(?:低声|轻声|高声|大声|小声|笑着|哭着|冷冷地|缓缓地|哽咽着)\s*)*"
                            r"(?:说道|问道|喊道|叫道|答道|回答|开口(?:说道|道)?|说|问|喊|叫|答)"
                            r"(?=[：:「“\"『\s]|$)", suffix):
                    return name
    return None


def context(paragraphs, start, end, first, last):
    return ([paragraphs[i - 1].strip() for i in range(max(start, first - 2), first)],
            [paragraphs[i - 1].strip() for i in range(last + 1, min(end, last + 1) + 1)])


def review_key(kind, scene_label, text, before, after, ordinal):
    payload = [kind, re.sub(r"^场景\d+[：-]", "", scene_label), compact(text),
               [compact(x) for x in before], [compact(x) for x in after], ordinal]
    return sha(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))[:24]


def decision_issue(decision, template):
    if decision is None:
        return "待人工确认说话人或引号用途"
    if not isinstance(decision, dict) or any(decision.get(field) != value for field, value in template.items()):
        return "旧复核决定已失效，需重新确认"
    return None


def non_character_issues(dialogue, decision, names, source_text):
    """An explicit decision, never a machine-name heuristic, grants this class."""
    issues = []
    if dialogue["speaker"] in names:
        issues.append("非人物播报声源不得列入人物行")
    if not (decision and decision.get("classification") == "非人物播报"
            and decision.get("source_label") == dialogue["speaker"]
            and isinstance(decision.get("reason"), str) and decision["reason"].strip()):
        issues.append("非人物播报须有绑定当前原文的声音分类决定")
    if dialogue["text"] != source_text:
        issues.append("非人物播报文字和标点须逐字保留原文")
    return issues


def issue_category(issue):
    if "引号未闭合" in issue or "引号闭合符" in issue:
        return "source_format"
    if "已失效" in issue:
        return "stale_decision"
    if "非人物播报" in issue:
        return "sound_classification"
    if "文字" in issue or "未能对应" in issue or "未匹配" in issue:
        return "text_alignment"
    if "人物行" in issue:
        return "cast_contract"
    return "speaker_ambiguity"


def punctuation_diagnostics(paragraph_map, paragraphs, dialogues, labels, lines):
    """Nonblocking differences; author resolves them before freezing, not after assemble."""
    result = []
    for dialogue in dialogues:
        refs = [item["paragraph"] for item in paragraph_map
                if any(dest["line"] == dialogue["line"] and dest["kind"] == "dialogue"
                       for dest in item["destinations"])]
        candidates = []
        for ref in refs:
            raw, _ = source_compat.strip_leading_voice(paragraphs[ref - 1].strip())
            label = explicit_label(raw, labels)
            raw = label["text"] if label else raw
            blocks = quote_blocks([raw], 1, 1)
            candidates.extend(block["text"] for block in blocks if not block["unclosed"])
            if not blocks:
                candidates.append(raw)
        source = next((value for value in candidates if compact(value) == compact(dialogue["text"])), None)
        if source is not None and source != dialogue["text"]:
            result.append({"kind": "标点或空白差异", "source_paragraphs": refs, "draft_line": dialogue["line"],
                           "source_text": source, "draft_text": dialogue["text"],
                           "blocking": False, "action": "冻结前核对原标点；仅允许有依据的格式补标点，不改字词"})
    for line in lines:
        if line["kind"] != "action":
            continue
        refs = [item["paragraph"] for item in paragraph_map if item["destinations"] == [{"line": line["line"], "kind": "action"}]]
        source = "".join(TRANSITION.sub("", paragraphs[ref - 1].strip()) for ref in refs)
        if refs and compact(source) == compact(line["text"]) and source != line["text"]:
            result.append({"kind": "动作标点或空白差异", "source_paragraphs": refs, "draft_line": line["line"],
                           "source_text": source, "draft_text": line["text"], "blocking": False,
                           "action": "冻结前核对原标点与允许补标点，不改字词"})
    return result


def scene_decision_hash(scene, decisions):
    speaker_keys = sorted({item["decision_key"] for item in scene["speaker_items"]})
    paragraph_keys = sorted({item["decision_key"] for item in scene["paragraph_map"] if "decision_key" in item})
    fragment_keys = sorted({item["fragment_key"] for item in scene["paragraph_map"] if "fragment_key" in item})
    relevant = {"speakers": {key: decisions.get(key) for key in speaker_keys},
                "paragraphs": {key: decisions.get("paragraphs", {}).get(key) for key in paragraph_keys},
                "fragments": {key: decisions.get("fragments", {}).get(key) for key in fragment_keys}}
    return sha(json.dumps(relevant, ensure_ascii=False, sort_keys=True))


def fragment_surfaces(path, lines, dialogues):
    """Offsets refer to the actual saved line, including its format prefix."""
    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    surfaces = {}
    heading_number = next(i for i, raw in enumerate(raw_lines, 1) if raw.strip())
    raw = raw_lines[heading_number - 1]
    # Only the location component can receive source content; not 场景/日/内.
    location = raw.strip().split("-", 3)[-1]
    location_end = len(raw.rstrip())
    surfaces[(heading_number, "heading")] = (location_end - len(location), location_end)
    for line in lines:
        raw = raw_lines[line["line"] - 1]
        end = len(raw.rstrip())
        surfaces[(line["line"], line["kind"])] = (end - len(line["text"]), end)
    for dialogue in dialogues:
        raw = raw_lines[dialogue["line"] - 1]
        label = raw[:raw.index("：")]
        cue = re.search(r"[（(]([^（）()]*)[）)]\s*$", label)
        if cue:
            surfaces[(dialogue["line"], "cue")] = cue.span(1)
    return raw_lines, surfaces


def checked_span(span, text, label):
    if (not isinstance(span, list) or len(span) != 2
            or any(type(n) is not int for n in span)
            or not 0 <= span[0] < span[1] <= len(text)):
        raise ValueError(f"{label}须为原始文字内零基半开字符范围")
    return span


def check_fragment_mapping(mapping, template, candidate, names, lines, dialogues,
                           raw_lines, surfaces, cursor, used_targets, paragraphs, end):
    """Validate a full paragraph partition, never an omission exemption.

    This deliberately supports only verbatim content and the existing finite
    voice syntax. Human attribution stays in the independent speaker review.
    """
    if not isinstance(mapping, dict) or any(mapping.get(k) != v for k, v in template.items()):
        raise ValueError("源片段映射已失效；须重新核对原文、上下文、段号和场稿")
    if not isinstance(mapping.get("reason"), str) or not mapping["reason"].strip():
        raise ValueError("源片段映射须说明格式迁移及声音归属依据")
    fragments = mapping.get("fragments")
    if not isinstance(fragments, list) or not fragments:
        raise ValueError("源片段映射须有非空 fragments")
    raw = template["source_text"]
    offset = len(raw.rstrip()) - len(candidate)
    required = {i for i in range(offset, len(raw.rstrip())) if raw[i].isalnum()}
    covered, targets, body_spans, voices, destinations = set(), set(), [], [], []
    previous_source_end, previous_target = offset, None
    by_line = {line["line"]: line for line in lines}
    by_dialogue = {line["line"]: line for line in dialogues}
    for fragment in fragments:
        if not isinstance(fragment, dict):
            raise ValueError("源片段条目须为对象")
        a, b = checked_span(fragment.get("source_range"), raw, "源片段")
        source = raw[a:b]
        if (a < previous_source_end or a < offset or fragment.get("source_text") != source
                or not compact(source)):
            raise ValueError("源片段须逐字绑定且有正文；范围不得倒序、重叠或包含已解析标签")
        previous_source_end = b
        covered.update(i for i in range(a, b) if raw[i].isalnum())
        target = fragment.get("target")
        if not isinstance(target, dict) or type(target.get("line")) is not int:
            raise ValueError("源片段目标须有实际成稿行号")
        number, kind = target["line"], target.get("kind")
        if kind not in ("heading", "action", "dialogue", "cue") or (number, kind) not in surfaces:
            raise ValueError("源片段目标行与 heading/action/dialogue/cue 类型不符")
        target_raw = raw_lines[number - 1]
        c, d = checked_span(target.get("range"), target_raw, "目标片段")
        low, high = surfaces[(number, kind)]
        if (not low <= c < d <= high or target.get("text") != target_raw[c:d]
                or not compact(target_raw[c:d])):
            raise ValueError("目标片段须逐字绑定实际文字，且位于对应正文/地点/声音标签内")
        occupied = {(number, i) for i in range(c, d)}
        if occupied & (used_targets | targets):
            raise ValueError("目标片段已被消费，不能跨段或段内重叠、重复使用")
        if previous_target is not None and (number, c) < previous_target:
            raise ValueError("源片段对应的成稿范围不得倒序")
        previous_target = (number, d)
        targets.update(occupied)
        if kind == "cue":
            if (not source_compat.standalone_voice(source)
                    or any(char.isalnum() for char in raw[b:])
                    or (a and raw[a - 1].isascii() and raw[a - 1].isalnum())
                    or [c, d] != [low, high]
                    or normalized_cue(source) != normalized_cue(target_raw[c:d])):
                raise ValueError("cue 仅接受段末独立声音语法并完整对应声音标签，不能把正文改作提示")
            speaker = fragment.get("speaker")
            dialogue = by_dialogue[number]
            if (not isinstance(speaker, str) or speaker != dialogue["speaker"]
                    or any(member not in names for member in speaker.split("、"))
                    or dialogue["non_character"]):
                raise ValueError("cue 迁移声源必须逐字对应当前人物台词标签")
            linked = fragment.get("dialogue_source")
            if not isinstance(linked, dict):
                raise ValueError("cue 须绑定紧邻的原台词段落")
            refs = linked.get("source_range")
            next_paragraph = next((p for p in range(template["paragraph"] + 1, end + 1)
                                   if paragraphs[p - 1].strip()), None)
            if (not isinstance(refs, list) or len(refs) != 2
                    or any(type(p) is not int for p in refs) or refs[0] != next_paragraph
                    or not refs[0] <= refs[1] <= end
                    or linked.get("source_lines") != paragraphs[refs[0] - 1:refs[1]]):
                raise ValueError("cue 须逐字绑定紧邻且连续的原台词范围")
            source_label = explicit_label(paragraphs[refs[0] - 1], names)
            if source_label and source_label["speaker"] != speaker:
                raise ValueError("cue 迁移声源与紧邻原台词的明确角色标签不一致")
            # A suffix must sit immediately before the receiving dialogue in
            # the draft too; it cannot leap over an action or another speaker.
            earlier = [line for line in lines if line["line"] < number]
            if body_spans and (not earlier or earlier[-1]["line"] != body_spans[-1][2]):
                raise ValueError("cue 须紧邻本段动作对应的下一条台词")
            voices.append({"paragraph": template["paragraph"], "speaker": speaker,
                           "draft_line": number, "source_range": refs, "text": source})
        else:
            if compact(source) != compact(target_raw[c:d]):
                raise ValueError("源片段与目标字词不一致；映射不能删除或增加正文")
            if kind == "heading":
                if not TRANSITION.match(raw.strip()) or cursor or body_spans:
                    raise ValueError("只有场首转场原段可映射到实际标题地点")
            else:
                line = by_line[number]
                found = line["start"] + len(compact(target_raw[low:c]))
                finish = found + len(compact(target_raw[c:d]))
                if found < (body_spans[-1][1] if body_spans else cursor):
                    raise ValueError("正文目标已被消费或与原文顺序不一致")
                body_spans.append((found, finish, number))
        destination = {"line": number, "kind": kind}
        if destination not in destinations:
            destinations.append(destination)
    if covered != required:
        raise ValueError("源片段范围必须完整覆盖本段所有正文、动作、时间和声音标记")
    return destinations, body_spans, targets, voices


def check_scene(paragraphs, start, end, path, decisions):
    from screenplay_io import validate_scene
    file_match = SCENE_FILE.fullmatch(path.name)
    if file_match:
        errors = validate_scene(path.read_text(encoding="utf-8-sig"), int(file_match.group(1)), path)
        if errors:
            raise ValueError("逐场入口检查未通过：" + "；".join(errors))
    heading, names, lines, dialogues = draft_parts(path)
    raw_lines, surfaces = fragment_surfaces(path, lines, dialogues)
    source_labels = names + [item["speaker"] for item in dialogues if item["non_character"]]
    flat = "".join(compact(line["text"]) for line in lines)
    cursor, missing, extra, paragraph_map = 0, [], [], []
    used_targets, mapped_voices, fragment_templates = set(), [], {}
    source_hash = sha(json.dumps(paragraphs[start - 1:end], ensure_ascii=False))
    draft_hash = sha(path.read_text(encoding="utf-8-sig"))

    def preserve_cue(source_cue, destinations, paragraph):
        if not source_cue:
            return
        target_dialogues = [item for item in dialogues
                            if any(dest["line"] == item["line"] and dest["kind"] == "dialogue"
                                   for dest in destinations)]
        if (len(target_dialogues) != 1
                or normalized_cue(target_dialogues[0]["cue"]) != normalized_cue(source_cue)):
            missing.append({"paragraph": paragraph, "text": source_cue,
                            "issue": "原文台词括号提示未在对应台词标签完整保留"})
            return
        number = target_dialogues[0]["line"]
        low, high = surfaces[(number, "cue")]
        occupied = {(number, i) for i in range(low, high)}
        if used_targets & occupied:
            missing.append({"paragraph": paragraph, "text": source_cue,
                            "issue": "声音标签目标已被其他源片段消费，不能重复使用"})
        used_targets.update(occupied)

    for paragraph in range(start, end + 1):
        value = paragraphs[paragraph - 1].strip()
        before, after = context(paragraphs, start, end, paragraph, paragraph)
        disposition_key = review_key("paragraph", heading, value, before, after, 1)
        disposition = decisions.get("paragraphs", {}).get(disposition_key)
        template = {"policy": FRAGMENT_POLICY, "paragraph": paragraph,
                    "source_text": paragraphs[paragraph - 1], "context_before": before,
                    "context_after": after, "source_hash": source_hash, "draft_hash": draft_hash}
        fragment_templates[paragraph] = (disposition_key, template)
        candidate = TRANSITION.sub("", value)
        candidate, leading_cue = source_compat.strip_leading_voice(candidate)
        source_label = explicit_label(candidate, source_labels)
        if source_label:
            candidate = source_label["text"]
        source_cue = source_label["cue"] if source_label else leading_cue
        if disposition_key in decisions.get("fragments", {}):
            mapping = decisions["fragments"][disposition_key]
            try:
                if disposition is not None:
                    raise ValueError("同段不能同时使用源片段映射与纯提示豁免")
                destinations, spans, targets, voices = check_fragment_mapping(
                    mapping, template, candidate, names, lines, dialogues,
                    raw_lines, surfaces, cursor, used_targets, paragraphs, end)
            except ValueError as exc:
                missing.append({"paragraph": paragraph, "text": value, "issue": str(exc)})
                paragraph_map.append({"paragraph": paragraph, "source_text": value,
                                      "status": "映射待核", "destinations": []})
                continue
            for found, finish, _ in spans:
                if found > cursor:
                    extra.append({"text": flat[cursor:found][:120], "before_paragraph": paragraph,
                                  "draft_lines": [line["line"] for line in lines
                                                  if line["start"] < found and line["end"] > cursor]})
                cursor = finish
            used_targets.update(targets)
            mapped_voices.extend(voices)
            preserve_cue(source_cue, destinations, paragraph)
            paragraph_map.append({"paragraph": paragraph, "source_text": value,
                                  "status": "已映射", "destinations": destinations,
                                  "fragments": copy.deepcopy(mapping["fragments"])})
            continue
        reason = cue_reason(value, names)
        # A marker that is actually preserved as dialogue is content, not layout.
        if reason == "独立章节编号" and any(compact(value) == compact(item["text"]) for item in dialogues):
            reason = None
        if reason:
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "格式提示", "reason": reason, "destinations": []})
            continue
        if disposition is not None:
            if (isinstance(disposition, dict) and disposition.get("source_text") == value
                    and disposition.get("context_before") == before and disposition.get("context_after") == after
                    and isinstance(disposition.get("reason"), str) and disposition["reason"].strip()):
                paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "人工确认纯提示",
                                      "reason": disposition["reason"], "decision_key": disposition_key, "destinations": []})
                continue
            missing.append({"paragraph": paragraph, "text": value, "issue": "旧段落去向决定已失效"})
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "待核", "decision_key": disposition_key,
                                  "decision_template": {"source_text": value, "context_before": before, "context_after": after}, "destinations": []})
            continue
        fragment = compact(candidate)
        if not fragment:
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "纯标点", "reason": "仅含标点", "destinations": []})
            continue
        found = flat.find(fragment, cursor)
        heading_number = next(number for number, kind in surfaces if kind == "heading")
        heading_low, heading_high = surfaces[(heading_number, "heading")]
        heading_raw = raw_lines[heading_number - 1]
        heading_positions = [i for i in range(heading_low, heading_high) if heading_raw[i].isalnum()]
        heading_found = compact(heading_raw[heading_low:heading_high]).find(fragment)
        heading_targets = {(heading_number, i) for i in heading_positions[heading_found:heading_found + len(fragment)]}
        if (found < 0 and TRANSITION.match(value) and heading_found >= 0
                and not heading_targets & used_targets):
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "转场标题", "reason": "转场词进入场景标题",
                                  "destinations": [{"line": heading_number, "kind": "heading"}]})
            used_targets.update(heading_targets)
            continue
        if found < 0:
            missing.append({"paragraph": paragraph, "text": value})
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "未覆盖", "decision_key": disposition_key,
                                  "decision_template": {"source_text": value, "context_before": before, "context_after": after}, "destinations": []})
            continue
        if found > cursor:
            extra.append({"text": flat[cursor:found][:120], "before_paragraph": paragraph,
                          "draft_lines": [line["line"] for line in lines if line["start"] < found and line["end"] > cursor]})
        finish = found + len(fragment)
        destinations = [{"line": line["line"], "kind": line["kind"]} for line in lines
                        if line["start"] < finish and line["end"] > found]
        preserve_cue(source_cue, destinations, paragraph)
        paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "已覆盖", "destinations": destinations})
        cursor = finish
    if cursor < len(flat):
        extra.append({"text": flat[cursor:][:120], "after_paragraph": end,
                      "draft_lines": [line["line"] for line in lines if line["end"] > cursor]})

    quote_items, assigned_lines = [], set()
    for block in quote_blocks(paragraphs, start, end):
        before, after = context(paragraphs, start, end, block["paragraph"], block["end_paragraph"])
        key = review_key("quote", heading, block["text"], before, after, block["ordinal"])
        source_lines = {dest["line"] for item in paragraph_map
                        if block["paragraph"] <= item["paragraph"] <= block["end_paragraph"]
                        for dest in item["destinations"] if dest["kind"] == "dialogue"}
        matching = [d for d in dialogues if d["line"] in source_lines and d["line"] not in assigned_lines
                    and compact(block["text"]) == compact(d["text"])]
        dialogue = matching[0] if matching else None
        template = {"source_text": block["text"], "context_before": before, "context_after": after,
                    "draft_text": dialogue["text"] if dialogue else ""}
        decision = decisions.get(key)
        issues = []
        if block["unclosed"]:
            issues.append("原文引号未闭合")
        if block.get("mismatches"):
            issues.append("原文引号闭合符不匹配；须显式处理，不自动猜测")
        if dialogue and dialogue["non_character"]:
            template["source_lines"] = paragraphs[block["paragraph"] - 1:block["end_paragraph"]]
        invalid = decision_issue(decision, template) if decision is not None else None
        if invalid:
            issues.append(invalid)
            decision = None
        if decision and decision.get("classification") == "非台词":
            if dialogue:
                issues.append("引号已匹配成稿台词，不能标为非台词")
        elif dialogue:
            assigned_lines.add(dialogue["line"])
            if dialogue["non_character"]:
                issues.extend(non_character_issues(dialogue, decision, names, block["text"]))
            else:
                hint = cue_hint(paragraphs, start, block["paragraph"], names)
                cast_match = all(part in names for part in dialogue["speaker"].split("、"))
                if not cast_match:
                    issues.append("说话人未逐字列入人物行")
                if (not hint or (hint != dialogue["speaker"] and hint not in dialogue["speaker"].split("、"))) and not (decision and decision.get("speaker") == dialogue["speaker"]):
                    issues.append("待人工确认说话人")
                if decision and decision.get("classification") == "非人物播报":
                    issues.append("非人物播报须在成稿声源标签明确标记")
        else:
            issues.append("原文引号未匹配成稿台词；若非台词须人工确认")
        quote_items.append({"kind": "原文引号", "paragraph": block["paragraph"], "end_paragraph": block["end_paragraph"],
                            "decision_key": key, "source_text": block["text"], "draft_line": dialogue["line"] if dialogue else None,
                            "speaker": dialogue["speaker"] if dialogue else None, "cue_hint": cue_hint(paragraphs, start, block["paragraph"], names),
                            "issue": issues, "decision_template": template})

    for dialogue in dialogues:
        if dialogue["line"] in assigned_lines:
            continue
        refs = [item["paragraph"] for item in paragraph_map
                if any(dest["line"] == dialogue["line"] and dest["kind"] == "dialogue"
                       for dest in item["destinations"])]
        source_text = "".join(paragraphs[ref - 1] for ref in refs)
        first, last = (min(refs), max(refs)) if refs else (start, start)
        before, after = context(paragraphs, start, end, first, last)
        ordinal = sum(1 for item in dialogues if item["line"] <= dialogue["line"] and compact(item["text"]) == compact(dialogue["text"]))
        key = review_key("unquoted", heading, source_text + dialogue["text"], before, after, ordinal)
        template = {"source_text": source_text, "context_before": before, "context_after": after, "draft_text": dialogue["text"]}
        if dialogue["non_character"]:
            template["source_lines"] = [paragraphs[ref - 1] for ref in refs]
        decision = decisions.get(key)
        issues = []
        invalid = decision_issue(decision, template) if decision is not None else None
        if invalid:
            issues.append(invalid)
            decision = None
        if not refs or compact(dialogue["text"]) not in compact(source_text):
            issues.append("无引号台词未能对应原文文字")
        hint = cue_hint(paragraphs, start, first, names)
        if dialogue["non_character"]:
            spoken_parts = []
            for ref in refs:
                raw, _ = source_compat.strip_leading_voice(paragraphs[ref - 1].strip())
                label = explicit_label(raw, source_labels)
                spoken_parts.append(label["text"] if label else raw)
            issues.extend(non_character_issues(dialogue, decision, names, "".join(spoken_parts)))
        else:
            if any(part not in names for part in dialogue["speaker"].split("、")):
                issues.append("说话人未逐字列入人物行")
            if (not hint or hint not in dialogue["speaker"].split("、")) and not (decision and decision.get("speaker") == dialogue["speaker"]):
                issues.append("无引号台词说话人待核")
            if decision and decision.get("classification") == "非人物播报":
                issues.append("非人物播报须在成稿声源标签明确标记")
        quote_items.append({"kind": "无引号台词", "paragraph": first, "source_paragraphs": refs,
                            "decision_key": key, "source_text": source_text, "draft_line": dialogue["line"],
                            "speaker": dialogue["speaker"], "cue_hint": hint, "issue": issues,
                            "decision_template": template})
    for item in quote_items:
        item["issue_categories"] = sorted({issue_category(issue) for issue in item["issue"]})
    for voice in mapped_voices:
        first, last = voice["source_range"]
        linked = [item for item in quote_items if item["draft_line"] == voice["draft_line"]
                  and item["paragraph"] == first
                  and item.get("end_paragraph", max(item.get("source_paragraphs") or [first])) == last]
        if (len(linked) != 1 or linked[0]["speaker"] != voice["speaker"] or linked[0]["issue"]):
            missing.append({"paragraph": voice["paragraph"], "text": voice["text"],
                            "issue": "cue 迁移须对应所绑定的紧邻原台词及独立通过复核的相同说话人"})
            next(item for item in paragraph_map if item["paragraph"] == voice["paragraph"])["status"] = "映射待核"
    for item in paragraph_map:
        item["fragment_key"], item["fragment_template"] = fragment_templates[item["paragraph"]]
    result = {"scene": heading, "file": path.name, "source_range": [start, end],
            "source_hash": source_hash, "draft_hash": draft_hash, "paragraph_map": paragraph_map,
            "coverage_missing": missing, "coverage_extra": extra, "speaker_items": quote_items,
            "parsing_transforms": source_compat.parsing_records(paragraphs, start, end, source_labels),
            "punctuation_diagnostics": punctuation_diagnostics(paragraph_map, paragraphs, dialogues, source_labels, lines)}
    result["decision_hash"] = scene_decision_hash(result, decisions)
    return result


def shift_cached(scene, new_start, file_name):
    result = copy.deepcopy(scene)
    delta = new_start - result["source_range"][0]
    result["source_range"] = [value + delta for value in result["source_range"]]
    result["file"] = file_name
    for item in result["paragraph_map"]:
        item["paragraph"] += delta
        if "fragment_template" in item:
            item["fragment_template"]["paragraph"] += delta
    for item in result["coverage_missing"]:
        item["paragraph"] += delta
    for item in result["coverage_extra"]:
        for key in ("before_paragraph", "after_paragraph"):
            if key in item:
                item[key] += delta
    for item in result["speaker_items"]:
        item["paragraph"] += delta
        if "end_paragraph" in item:
            item["end_paragraph"] += delta
        if "source_paragraphs" in item:
            item["source_paragraphs"] = [value + delta for value in item["source_paragraphs"]]
    for item in result.get("parsing_transforms", []):
        item["paragraph"] += delta
        if "end_paragraph" in item:
            item["end_paragraph"] += delta
        for mismatch in item.get("mismatches", []):
            mismatch["paragraph"] += delta
    for item in result.get("punctuation_diagnostics", []):
        item["source_paragraphs"] = [value + delta for value in item["source_paragraphs"]]
    return result


def selected_ranges(paths, source_count, scope, scene_numbers):
    if scope not in ("full", "partial", "sample"):
        raise ValueError("复核scope须为full、partial或sample")
    if scope == "full" and scene_numbers is not None:
        raise ValueError("full复核不能筛选场次；样本请显式使用sample或partial")
    if scene_numbers is not None:
        requested = set(scene_numbers)
        if not requested or len(requested) != len(scene_numbers) or any(type(n) is not int or n < 1 for n in requested):
            raise ValueError("选定场次须为非空、不重复的正整数")
        missing = requested - {number for number, _ in paths}
        if missing:
            raise ValueError(f"选定场次没有逐场稿：{sorted(missing)}")
        paths = [(number, path) for number, path in paths if number in requested]
    if not paths:
        raise ValueError("没有可复核的逐场稿")
    ranges, seen, previous_end = [], set(), 0
    for number, path in paths:
        match = SCENE_FILE.fullmatch(path.name)
        start, end = int(match.group(2)), int(match.group(3))
        if number in seen or number < 1 or start < 1 or end < start or end > source_count or start <= previous_end:
            raise ValueError(f"{path.name} 场次重复或原文范围越界、倒序、重叠")
        seen.add(number)
        ranges.append((start, end))
        previous_end = end
    return paths, ranges


def boundary_review(paragraphs, all_paths, selected_numbers, decisions):
    """Flag camera-space/vehicle-space boundaries and bind human retention.

    Always inspect immediate neighbours, including unselected sample scenes.
    Do not infer a merge, a new location, an asset, or semantic approval.
    """
    items, fingerprints = [], {}
    for (left_number, left), (right_number, right) in zip(all_paths, all_paths[1:]):
        if not {left_number, right_number} & set(selected_numbers):
            continue
        lm, rm = SCENE_FILE.fullmatch(left.name), SCENE_FILE.fullmatch(right.name)
        left_range, right_range = [int(lm[2]), int(lm[3])], [int(rm[2]), int(rm[3])]
        if left_range[1] + 1 != right_range[0]:
            continue  # a partial workdir has no known adjacent boundary here
        if any(a < 1 or b < a or b > len(paragraphs) for a, b in (left_range, right_range)):
            raise ValueError("场界相邻场稿的原文范围无效")
        left_text, right_text = left.read_text(encoding="utf-8-sig"), right.read_text(encoding="utf-8-sig")
        left_heading = next((line.strip() for line in left_text.splitlines() if line.strip()), "")
        right_heading = next((line.strip() for line in right_text.splitlines() if line.strip()), "")
        if not any(MOBILE_INTERIOR.search(heading) for heading in (left_heading, right_heading)):
            continue
        start, end = max(left_range[0], left_range[1] - 1), min(right_range[1], right_range[0] + 1)
        template = {"policy": BOUNDARY_POLICY, "headings": [left_heading, right_heading],
                    "source_ranges": [left_range, right_range],
                    "source_hashes": [sha(json.dumps(paragraphs[a - 1:b], ensure_ascii=False))
                                      for a, b in (left_range, right_range)],
                    "draft_hashes": [sha(left_text), sha(right_text)],
                    "context_paragraphs": list(range(start, end + 1)),
                    "context": paragraphs[start - 1:end]}
        key = sha(json.dumps(template, ensure_ascii=False, sort_keys=True))[:24]
        decision = decisions.get("boundaries", {}).get(key)
        issues = []
        if decision is None:
            issues.append("移动载体内外切换须复核叙事场界；机位进入车内本身不构成新场")
        elif not isinstance(decision, dict) or any(decision.get(k) != value for k, value in template.items()):
            issues.append("旧场界决定已失效，须核对相邻场稿和原文后重新决定")
        else:
            refs, evidence = decision.get("source_paragraphs"), decision.get("evidence")
            if (decision.get("outcome") != "retain"
                    or decision.get("reason_kind") not in BOUNDARY_REASONS
                    or not isinstance(decision.get("reason"), str) or not decision["reason"].strip()):
                issues.append("保留场界须明确真实转场/时间/地点/事件变化或独立载体空间的具体理由；合并须修改场稿后重检")
            if (not isinstance(refs, list) or not refs or any(type(ref) is not int for ref in refs)
                    or refs[0] < left_range[0] or refs[-1] > right_range[1]
                    or refs != list(range(refs[0], refs[-1] + 1))
                    or not isinstance(evidence, str) or not evidence.strip()
                    or evidence not in "\n".join(paragraphs[ref - 1] for ref in refs)):
                issues.append("场界决定须引用相邻两场范围内连续原文段落的逐字证据")
        items.append({"kind": "mobile_space_boundary", "scene_numbers": [left_number, right_number],
                      "decision_key": key, "decision_template": template, "issue": issues,
                      "status": "needs_review" if issues else "human_retained",
                      "action": "核对真实叙事地点和时间；连续上车/车内外反打留在同场，实质转场保留边界。车辆作为移动道具候选，内景参考需求单独审阅。"})
        fingerprints.update({left.name: template["draft_hashes"][0], right.name: template["draft_hashes"][1]})
    return items, fingerprints


def internal_boundary_risks(paragraphs, paths, ranges):
    """Return nonblocking source signals; a human must judge narrative space."""
    risks = []
    for (number, path), (start, end) in zip(paths, ranges):
        signals = []
        for paragraph in range(start, end + 1):
            text = paragraphs[paragraph - 1]
            for kind, pattern in (("movement", INTERNAL_MOVEMENT),
                                  ("current_location_speech", CURRENT_LOCATION_SPEECH)):
                # The first source paragraph may establish this scene's place.
                if kind == "movement" and paragraph == start:
                    continue
                for match in pattern.finditer(text):
                    signals.append({"kind": kind, "paragraph": paragraph,
                                    "source_text": text, "range": list(match.span()),
                                    "evidence": match.group(),
                                    "context_before": paragraphs[max(start - 1, paragraph - 3):paragraph - 1],
                                    "context_after": paragraphs[paragraph:min(end, paragraph + 2)]})
        if signals:
            risks.append({"scene_number": number, "file": path.name,
                          "source_range": [start, end], "signals": signals,
                          "risk_scan": "heuristic_not_complete", "blocking": False,
                          "semantic_review": "not_performed",
                          "action": "回查场内主要地点及后文当前地点回指；区分所在、目的地与提及。连续事件不证明同场，位移也不自动要求拆场。"})
    return risks


def make_report(workdir, decisions, previous=None, scope="full", scene_numbers=None):
    if (not isinstance(decisions, dict) or not isinstance(decisions.get("paragraphs", {}), dict)
            or not isinstance(decisions.get("boundaries", {}), dict)
            or not isinstance(decisions.get("fragments", {}), dict)):
        raise ValueError("确认文件须为 JSON 对象；paragraphs、boundaries、fragments 也须为对象")
    paragraphs = source_paragraphs(workdir / "source.json")
    all_paths = scene_files(workdir)
    paths, ranges = selected_ranges(all_paths, len(paragraphs), scope, scene_numbers)
    if scope == "full":
        ranges = scene_ranges(workdir, len(paths), len(paragraphs))
    decision_digest = sha(json.dumps(decisions, ensure_ascii=False, sort_keys=True))
    prior = {}
    if previous and previous.get("review_version") == REVIEW_VERSION:
        for item in previous.get("scenes", []):
            prior.setdefault((item["source_hash"], item["draft_hash"]), []).append(item)
    scenes, reused = [], 0
    for (_, path), (start, end) in zip(paths, ranges):
        source_hash = sha(json.dumps(paragraphs[start - 1:end], ensure_ascii=False))
        draft_hash = sha(path.read_text(encoding="utf-8-sig"))
        candidates = [item for item in prior.get((source_hash, draft_hash), [])
                      if item.get("decision_hash") == scene_decision_hash(item, decisions)
                      and (item["source_range"] == [start, end]
                           or not any(part.get("fragment_key") in decisions.get("fragments", {})
                                      for part in item["paragraph_map"]))]
        if len(candidates) == 1:
            scenes.append(shift_cached(candidates[0], start, path.name))
            reused += 1
        else:
            scenes.append(check_scene(paragraphs, start, end, path, decisions))
    missing = sum(len(item["coverage_missing"]) for item in scenes)
    extra = sum(len(item["coverage_extra"]) for item in scenes)
    speaker = sum(bool(part["issue"]) for item in scenes for part in item["speaker_items"])
    issue_counts = {}
    for scene in scenes:
        for item in scene["speaker_items"]:
            for category in item.get("issue_categories", []):
                issue_counts[category] = issue_counts.get(category, 0) + 1
    boundaries, boundary_drafts = boundary_review(paragraphs, all_paths, [n for n, _ in paths], decisions)
    boundary_pending = sum(bool(item["issue"]) for item in boundaries)
    if boundary_pending:
        issue_counts["scene_boundary"] = boundary_pending
    status = ("passed" if scope == "full" else scope + "_passed") if not (missing or extra or speaker or boundary_pending) else "needs_review"
    fingerprint = {"source_digest": digest(paragraphs),
                   "drafts": {path.name: sha(path.read_text(encoding="utf-8-sig")) for _, path in paths}}
    return {"review_version": REVIEW_VERSION, "fragment_policy": FRAGMENT_POLICY, "scope": scope, "status": status,
            "coverage_complete": scope == "full", "reviewed_scene_numbers": [n for n, _ in paths],
            "source_paragraph_count": len(paragraphs), "fingerprint": fingerprint, "decision_digest": decision_digest,
            "boundary_policy": BOUNDARY_POLICY, "boundary_items": boundaries, "boundary_drafts": boundary_drafts,
            "internal_boundary_risks": internal_boundary_risks(paragraphs, paths, ranges),
            "counts": {"missing": missing, "extra": extra, "speaker": speaker, "reused_scenes": reused,
                       "scene_boundary": boundary_pending,
                       "format_hints": sum(part["status"] in ("格式提示", "人工确认纯提示", "纯标点", "转场标题")
                                           for scene in scenes for part in scene["paragraph_map"]),
                       "punctuation_diagnostics": sum(len(scene["punctuation_diagnostics"]) for scene in scenes)},
            "issue_counts": issue_counts, "scenes": scenes}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--previous-report", type=Path)
    parser.add_argument("--scope", choices=("full", "partial", "sample"), default="full")
    parser.add_argument("--scenes", type=int, nargs="+", help="局部复核选定场次；不能用于full")
    args = parser.parse_args()
    try:
        decisions = json.loads(args.decisions.read_text(encoding="utf-8-sig")) if args.decisions else {}
        if not isinstance(decisions, dict):
            raise ValueError("说话人确认文件须为 decision_key 到确认对象的 JSON 对象")
        previous = json.loads(args.previous_report.read_text(encoding="utf-8-sig")) if args.previous_report else None
        report = make_report(args.workdir, decisions, previous, args.scope, args.scenes)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        counts = report["counts"]
        print(f"逐场复核：漏文 {counts['missing']}，额外文字 {counts['extra']}，台词待处理 {counts['speaker']}，复用未改场景 {counts['reused_scenes']}；报告：{args.report}")
        print(f"场内地点风险 {len(report['internal_boundary_risks'])} 场（有限词表提示，须结合原文作语义判断；机械通过不代表场界正确）")
        if report["status"] == "needs_review":
            raise ValueError("逐场复核未通过，请修正逐场稿或补全有效的人工确认")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
