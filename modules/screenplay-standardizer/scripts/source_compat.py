"""Read-only source syntax adapters. Raw source text is never rewritten."""

import re
import hashlib
import json
from pathlib import Path


ACTION_MARKERS_FILE = "action-markers.json"


def action_content(line, markers=None):
    """Read an approved action prefix; return the body without rewriting it."""
    markers = ("△",) if markers is None else markers
    value = line.lstrip()
    for marker in sorted(markers, key=len, reverse=True):
        if value.startswith(marker):
            return value[len(marker):].lstrip()
    return None


def _source_digest(paragraphs):
    packed = json.dumps(paragraphs, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def load_action_markers(source_path):
    """Load source-bound, reader-approved symbols; old projects keep △ only."""
    source_path = Path(source_path)
    if source_path.is_dir():
        source_path = source_path / "source.json"
    path = source_path.parent / ACTION_MARKERS_FILE
    if not path.is_file():
        return ("△",)
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    source = json.loads(source_path.read_text(encoding="utf-8-sig"))
    paragraphs = [item["text"] for item in source["paragraphs"]]
    markers = value.get("markers")
    if (value.get("policy") != "action-markers-v1"
            or value.get("source_digest") != _source_digest(paragraphs)
            or value.get("reviewed") is not True
            or not isinstance(value.get("reason"), str) or not value["reason"].strip()
            or not isinstance(markers, list) or not markers):
        raise ValueError("动作标识登记缺少实读结论或已与原文失配")
    for marker in markers:
        if (not isinstance(marker, str) or not marker or marker != marker.strip()
                or any(char.isalnum() or char.isspace() for char in marker)
                or any(char in ':：\r\n\"“”‘’「」『』' for char in marker)):
            raise ValueError("动作标识须为不含文字、空白、冒号或台词引号的行首符号")
        if marker != "△" and not any(p.lstrip().startswith(marker) for p in paragraphs):
            raise ValueError("动作标识必须在当前原文行首实际出现")
    if len(set(markers)) != len(markers):
        raise ValueError("动作标识不得重复")
    return tuple(dict.fromkeys(["△", *markers]))


def register_action_markers(workdir, markers, reason):
    """Persist a reader decision; symbols are never inferred from punctuation."""
    workdir = Path(workdir)
    source = json.loads((workdir / "source.json").read_text(encoding="utf-8-sig"))
    value = {"policy": "action-markers-v1", "source_digest": _source_digest(
        [item["text"] for item in source["paragraphs"]]), "reviewed": True,
        "markers": list(markers), "reason": reason}
    path = workdir / ACTION_MARKERS_FILE
    old = path.read_bytes() if path.exists() else None
    try:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        load_action_markers(workdir)
    except Exception:
        if old is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(old)
        raise
    return value


QUOTE_PAIRS = {"「": "」", "『": "』", "“": "”", "‘": "’", '"': '"'}
QUOTE_OPEN = "".join(QUOTE_PAIRS)
QUOTE_CLOSE = "".join(QUOTE_PAIRS.values())
VOICE_CUE = r"(?:OS|O\.S\.|画外音)"
CHAPTER_MARKER = re.compile(r"^第[零〇一二两三四五六七八九十百千万\d]+[章节集回幕]$")


def structural_hint(value):
    """Only a bare numbered marker is syntax; a titled chapter stays content."""
    return "独立章节编号" if CHAPTER_MARKER.fullmatch(value.strip()) else None


def standalone_voice(value):
    return bool(re.fullmatch(VOICE_CUE + r"\s*[:：]?", value.strip(), re.I))


def strip_leading_voice(value):
    match = re.match(r"^\s*(" + VOICE_CUE + r")\s*[:：]?\s*(?=[" + re.escape(QUOTE_OPEN) + r"])", value, re.I)
    return (value[match.end():], match.group(1)) if match else (value, None)


def explicit_label(value, names):
    known = "|".join(re.escape(name) for name in sorted(set(names), key=len, reverse=True))
    if not known:
        return None
    pattern = (r"^\s*(?:(?P<prefix>" + VOICE_CUE + r")\s*)?"
               r"(?P<speaker>(?:" + known + r")(?:\s*、\s*(?:" + known + r"))*)"
               r"\s*(?:(?:（(?P<wide>[^（）]*)）|\((?P<ascii>[^()]*)\))|"
               r"(?P<suffix>" + VOICE_CUE + r"))?\s*(?:[：:]\s*|(?=[" + re.escape(QUOTE_OPEN) + r"]))")
    match = re.match(pattern, value, re.I)
    if not match:
        return None
    members = re.split(r"\s*、\s*", match.group("speaker"))
    if len(members) != len(set(members)):
        return None
    cues = [match.group(key) for key in ("prefix", "wide", "ascii", "suffix") if match.group(key)]
    # Multiple cue slots are not silently discarded or conflated.
    cue = "，".join(cues)
    return {"speaker": "、".join(members), "cue": cue, "text": value[match.end():],
            "source_prefix": value[:match.end()],
            "compatibility": bool(match.group("prefix") or match.group("suffix"))}


def quote_blocks(paragraphs, start, end):
    result, stack, pieces, opened, count, mismatches = [], [], [], None, {}, []
    for i in range(start, end + 1):
        for offset, char in enumerate(paragraphs[i - 1]):
            if not stack:
                if char in QUOTE_PAIRS:
                    stack, pieces, opened, mismatches = [char], [], i, []
                    count[i] = count.get(i, 0) + 1
                elif char in QUOTE_CLOSE:
                    count[i] = count.get(i, 0) + 1
                    result.append({"paragraph": i, "end_paragraph": i, "text": "",
                                   "ordinal": count[i], "unclosed": False,
                                   "mismatches": [{"paragraph": i, "offset": offset,
                                                   "actual": char, "expected": "缺少开引号"}]})
                continue
            if char == QUOTE_PAIRS[stack[-1]]:
                stack.pop()
                if not stack:
                    result.append({"paragraph": opened, "end_paragraph": i, "text": "".join(pieces),
                                   "ordinal": count[opened], "unclosed": False, "mismatches": mismatches})
                    opened, pieces = None, []
                    continue
            elif char in QUOTE_PAIRS:
                stack.append(char)
            elif char in QUOTE_CLOSE:
                mismatches.append({"paragraph": i, "offset": offset, "actual": char,
                                   "expected": QUOTE_PAIRS[stack[-1]]})
            pieces.append(char)
    if stack:
        result.append({"paragraph": opened, "end_paragraph": end, "text": "".join(pieces),
                       "ordinal": count[opened], "unclosed": True, "mismatches": mismatches})
    return result


def parsing_records(paragraphs, start, end, names):
    records = []
    for paragraph in range(start, end + 1):
        raw = paragraphs[paragraph - 1]
        label = explicit_label(raw, names)
        if label and label.get("compatibility"):
            records.append({"paragraph": paragraph, "kind": "明确声音标签解析", "source_text": raw,
                            "source_prefix": label["source_prefix"], "speaker": label["speaker"], "cue": label["cue"]})
        stripped, cue = strip_leading_voice(raw)
        if cue:
            records.append({"paragraph": paragraph, "kind": "独立声音前缀解析", "source_text": raw,
                            "parsed_text": stripped, "cue": cue})
        if structural_hint(raw):
            records.append({"paragraph": paragraph, "kind": "结构章节提示", "source_text": raw})
    for block in quote_blocks(paragraphs, start, end):
        records.append({"paragraph": block["paragraph"], "end_paragraph": block["end_paragraph"],
                        "kind": "引号边界解析", "source_text": block["text"],
                        "unclosed": block["unclosed"], "mismatches": block["mismatches"]})
    return records
