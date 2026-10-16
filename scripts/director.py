#!/usr/bin/env python3
"""Persist checked screenplay bindings, locate ranges, validate and render prompts."""
import argparse
from contextlib import nullcontext
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from decimal import Decimal

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dialogue_layout
import continuity
import batch_naming
HEADER_SHA = "8001822afdf324cbbfa3eb284b2a03ccbde084b2e70fa66763a26b5c738e5962"
STYLES = {"1": "3D国风动漫,暗黑武侠/超写实仙侠风格", "2": "3D国风动漫，眷思量风格，东方奇幻唯美风格"}
SPEEDS = {"常速": (4.0, 5.2), "快节奏": (4.5, 5.8), "情绪慢速": (3.3, 4.4)}
FRAMINGS = {"大远景", "大全景", "远景", "全景", "中景", "中近景", "近景", "特写"}
CLOSE = {"近景", "特写"}
OUTPUT_FORMAT = "reference-v3"
RECEIPT_DISPLAY_FORMAT = "batch-names-v1"
LEGACY_RECEIPT_DISPLAY_FORMAT = "numbered-units-v1"
BLACK_REFERENCE_MODE = "none-v1"
PHOTOGRAPHY_FIELDS = ("camera", "composition", "depth_of_field", "lighting")
FACE_FRAMINGS = CLOSE | {"中近景"}


def face_focus(shot, character_ids):
    """Recognize explicit face emphasis only; this is not image/semantic analysis."""
    focus = shot.get("focus", [])
    if not isinstance(focus, list) or len(focus) != 1 or focus[0] not in character_ids:
        return None
    if shot.get("face_closeup") is True:
        return focus[0]
    value = shot.get("composition", "")
    composition = compact(value) if isinstance(value, str) else ""
    # Generic smiling/looking in action text does not establish a face composition.
    exclusions = ("不拍脸", "不拍面部", "脸部不入画", "面部不入画", "脸不入画", "只拍手", "仅拍手", "只拍道具")
    if any(term in composition for term in exclusions):
        return None
    explicit = ("面部占", "脸部占", "正脸占", "脸庞占", "面孔占", "面部居", "脸部居", "正脸居",
                "脸庞居", "面孔居", "面部位于", "脸部位于", "正脸位于", "面部处于", "脸部处于",
                "聚焦面部", "聚焦脸部", "以面部为", "以脸部为", "拍摄面部", "拍摄脸部")
    labels = {"脸部反应", "面部反应", "表情反应", "正脸反应", "面部特写", "脸部特写", "正脸特写", "脸部近景", "面部近景"}
    label = shot.get("label", "")
    return focus[0] if any(term in composition for term in explicit) or (isinstance(label, str) and compact(label) in labels) else None


def photography_warnings(state, entries, boundary_only=False):
    """Review explicit planning patterns; warnings never reject motivated shots."""
    characters = {c["id"] for c in state["ledger"]["characters"]}
    warnings = []

    def emit(kind, window, note):
        if boundary_only and len({item[0] for item in window}) == 1:
            return
        warnings.append({"kind": kind, "shots": [{"unit": key, "shot": number} for key, number, _ in window],
                         "review": note})

    def signature(shot):
        # Older receipts without focus/camera are not guessed or migrated.
        if not isinstance(shot.get("focus"), list) or not isinstance(shot.get("camera"), str) or not shot["camera"].strip():
            return None
        return (shot.get("framing"), tuple(sorted(shot["focus"])), shot.get("angle"), compact(shot["camera"]))

    for index, (_, number, shot) in enumerate(entries):
        face = face_focus(shot, characters)
        frontal = face if shot.get("angle") == "正面" and shot.get("framing") in FACE_FRAMINGS else None
        if number == 1 and face and shot.get("framing") in CLOSE and not boundary_only:
            emit("首镜面部近景需审查", entries[index:index + 1],
                 "确认面部反应是开场的必要看点；需要交代人物关系或空间时评估更完整的构图。首镜面部近景允许保留。")
        if index:
            previous = entries[index - 1][2]
            other_face = face_focus(previous, characters)
            if (frontal and shot.get("framing") in CLOSE and other_face
                    and previous.get("angle") == "正面" and previous.get("framing") in CLOSE):
                emit("连续正面面部近景需审查", entries[index - 1:index + 1],
                     "连续两镜为明确的正面面部近景/特写；检查每镜新增表演信息、焦点及空间关系。合法正反打与关键反应可以保留。只识别明确面部信号，不保证发现全部怼脸画面。")
        if index < 2:
            continue
        window = entries[index - 2:index + 1]
        signatures = [signature(item[2]) for item in window]
        if signatures[0] is not None and len(set(signatures)) == 1:
            emit("连续三镜摄影重复需审查", window,
                 "景别、focus、angle和camera相同；检查本镜是否新增信息、情绪或观看重点，无新增内容时合并或重新规划。有目的的重复允许保留。")
        faces = [face_focus(item[2], characters) if item[2].get("angle") == "正面"
                 and item[2].get("framing") in FACE_FRAMINGS else None for item in window]
        if faces[0] is not None and len(set(faces)) == 1:
            emit("同人物连续正面面部镜需审查", window,
                 "同人物连续三镜正面面部中近景/近景/特写；评估关系、动作或空间的其他看点，不用换个景别标签维持同一怼脸画面。只识别明确面部信号，仍须人工审查。")
    return warnings


def boundary_photography_warnings(state, units):
    """Review source-adjacent rendition links without changing stored receipts."""
    by_id = {u["id"]: index for index, u in enumerate(units) if "id" in u}
    predecessors = {}
    for index, unit in enumerate(units):
        previous = by_id.get(unit.get("predecessor"))
        if previous is None or previous >= index or units[previous]["span"][1] != unit["span"][0]:
            previous = next((i for i in range(index - 1, -1, -1)
                             if units[i]["span"][1] == unit["span"][0]), None)
        predecessors[index] = previous
    warnings = []
    for index, unit in enumerate(units):
        previous = predecessors[index]
        if previous is None:
            continue
        prefix = []
        # Two preceding shots suffice for every two/three-shot review window.
        while previous is not None and len(prefix) < 2:
            prior = units[previous]
            items = [(prior.get("id", f"待提交单元{previous + 1}"), number + 1, shot)
                     for number, shot in enumerate(prior["shots"])]
            prefix = items[-(2 - len(prefix)):] + prefix
            previous = predecessors[previous]
        key = unit.get("id", f"待提交单元{index + 1}")
        current = [(key, number + 1, shot) for number, shot in enumerate(unit["shots"][:2])]
        warnings.extend(photography_warnings(state, prefix + current, boundary_only=True))
    return warnings


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
        f.write(text)
        temporary = Path(f.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def compact(text):
    return "".join(c for c in text if c.isalnum())


def anchor_signature(text, start=0, end=None):
    """Protect numeric meaning while retaining existing compact coordinates."""
    end = len(text) if end is None else end
    # Inspect whole intersecting numbers so a bare 5 cannot match 1.5 or -5.
    numeric_matches = [m for m in re.finditer(r"[+\-−±]?\d+(?:[.．:：/／]\d+)*(?:[%％‰°])?", text)
                       if m.start() < end and m.end() > start]
    numbers = [m.group().translate(str.maketrans("．：／％−", ".:/%-")) for m in numeric_matches]
    operators = [(len(compact(text[start:i])), c.translate(str.maketrans("−／％", "-/%")))
                 for i, c in enumerate(text) if start <= i < end and c in "+-−±×÷=<>≤≥≠≈*/／%％‰°"
                 and not any(m.start() <= i < m.end() for m in numeric_matches)]
    return numbers, operators


def anchor_occurrences(text, quote):
    positions = [i for i, c in enumerate(text) if c.isalnum()]
    fragment = compact(quote)
    expected = anchor_signature(quote)
    for index in occurrences(compact(text), fragment):
        left, right = positions[index], positions[index + len(fragment) - 1] + 1
        # Include adjacent non-body symbols for signs and trailing percentages.
        while left and not text[left - 1].isalnum():
            left -= 1
        while right < len(text) and not text[right].isalnum():
            right += 1
        if anchor_signature(text, left, right) == expected:
            yield index


def interval(value, maximum):
    require(isinstance(value, list) and len(value) == 2 and all(type(n) is int for n in value), "范围须为两个整数的数组")
    a, b = value
    require(0 <= a < b <= maximum, "范围为空、倒序或超出标准剧本")
    return a, b


def dependency(path=None):
    # A distributable package must never silently select a machine-local version.
    found = (Path(path) if path else ROOT / "modules" / "screenplay-standardizer").resolve()
    instructions = found / ("INSTRUCTIONS.md" if (found / "INSTRUCTIONS.md").is_file() else "SKILL.md")
    required = {"INSTRUCTIONS.md", "references/asset-ledger.md", "references/review.md",
                "scripts/asset_ledger.py", "scripts/asset_migrate.py",
                "scripts/asset_visual_design.py", "scripts/character_variants.py",
                "scripts/screenplay_io.py", "scripts/screenplay_review.py"}
    require(instructions.is_file() and all((found / p).is_file() for p in required if p != "INSTRUCTIONS.md"),
            "剧本标准化模块不完整；请重新解压完整Skill包")
    if not path or (found / "component.json").is_file():
        manifest_path = found / "component.json"
        require(manifest_path.is_file(), "缺少内置模块摘要清单；请恢复完整Skill包")
        manifest = read_json(manifest_path)
        require(manifest.get("component") == "screenplay-standardizer" and manifest.get("bundle_format") == 1
                and isinstance(manifest.get("files"), dict) and set(manifest["files"]) == required,
                "内置模块摘要清单不兼容或不完整")
        for relative, expected in manifest["files"].items():
            require(file_hash(found / relative) == expected, f"内置模块文件已变化：{relative}；请恢复完整包或同步更新摘要清单")
    sys.path.insert(0, str(found / "scripts"))
    ledger = importlib.import_module("asset_ledger")
    review = importlib.import_module("screenplay_review")
    require(Path(ledger.__file__).resolve().parent == found / "scripts", "依赖模块位置冲突；请使用独立Python进程")
    return found, ledger, review


def build_records(script_text, ledger):
    scenes = {s["number"]: s for s in ledger["scenes"]}
    records, cursor, scene, heading_line = [], 0, None, 0
    for line_no, raw in enumerate(script_text.splitlines(), 1):
        line = raw.strip()
        heading = re.fullmatch(r"场景(\d+)(?=-(?:日|夜)-(?:内|外)-|：(?:日|夜)·(?:内|外)·)[：-](日|夜)[·-](内|外)[·-](.+)", line)
        if heading:
            scene = scenes[int(heading.group(1))]
            heading_line = line_no
        elif not line or line.startswith("人物："):
            continue
        else:
            require(scene is not None, "标准剧本正文出现在场景标题之前")
            label, voice, speaker = "", None, None
            if line.startswith("△ "):
                kind, text = "action", line[2:]
            else:
                require("：" in line, f"标准剧本第{line_no}行无法识别")
                label, text = line.split("：", 1)
                speaker = re.sub(r"[（(][^（）()]*[）)]$", "", label).strip()
                modifier = re.search(r"[（(]([^（）()]*)[）)]$", label)
                cue = modifier.group(1).strip() if modifier else ""
                inner = (re.search(r"(?<![A-Za-z])os(?![A-Za-z])", cue, re.IGNORECASE)
                         or cue == "内心" or any(word in cue for word in ("内心想法", "内心独白", "心理独白")))
                voice = ("内心想法" if inner else "画外音" if "画外音" in cue or speaker == "画外音" else
                         "旁白" if "旁白" in cue or speaker == "旁白" else "说")
                kind = "dialogue"
            size = len(compact(text))
            require(size > 0, f"第{line_no}行没有可定位正文，请复核标准剧本")
            records.append({"id": f"L{line_no:06d}", "line": line_no, "local_line": line_no - heading_line + 1, "scene": scene["id"],
                            "kind": kind, "speaker": speaker, "label": label, "voice": voice,
                            "text": text, "start": cursor, "end": cursor + size})
            cursor += size
    require(records, "标准剧本没有正文")
    return records, cursor


def bind(args):
    project = args.project.resolve()
    require(not (project / "session.json").exists(), "项目已绑定；使用已有状态或新建项目目录，不覆盖旧历史")
    dep, ledger_mod, review_mod = dependency(args.standardizer)
    paths = {"script": args.script.resolve(), "source": args.source.resolve(),
             "review": args.review.resolve(), "ledger": args.ledger.resolve()}
    report, ledger = read_json(paths["review"]), read_json(paths["ledger"])
    workdir = paths["source"].parent
    require(report.get("review_version") == review_mod.REVIEW_VERSION and report.get("status") == "passed", "标准剧本复核未通过或版本不兼容")
    require(all(report.get("counts", {}).get(k) == 0 for k in ("missing", "extra", "speaker")), "复核仍有未处理项")
    require(report.get("fingerprint") == review_mod.fingerprint_workdir(workdir), "复核指纹失效；请重新复核")
    merged = "\n\n".join(p.read_text(encoding="utf-8-sig").strip() for _, p in review_mod.scene_files(workdir)) + "\n"
    script_text = paths["script"].read_text(encoding="utf-8-sig")
    require(script_text == merged, "标准剧本与通过复核的逐场稿合并内容不一致")
    ledger_mod.validate(ledger_mod.parse_script(paths["script"]), ledger_mod.source_paragraphs(paths["source"]), ledger)
    require(ledger.get("schema_version") == 3, "新绑定须使用区分场次与布景的schema 3台账；旧项目先离线审计升级")
    require(not ledger.get("pending_reviews") and not any(ledger.get("inactive_assets", {}).values()), "资产仍有待核项目")
    records, length = build_records(script_text, ledger)
    style_path = project / "video-style.txt"
    style = style_path.read_text(encoding="utf-8-sig").strip() if style_path.is_file() else None
    if style is not None:
        validate_style(style)
    if ledger.get("image_design_version"):
        require(style is not None, "已完成美术设计的台账须先保存对应视频风格")
        require(style == ledger.get("visual_style", {}).get("text"),
                "资产美术设计风格与视频风格不一致；请按所选风格重新完成资产设计")
    state = {"schema_version": 1, "revision": 0, "phase": "asset_choice", "standardizer": str(dep),
             "files": {key: {"path": str(path), "sha256": file_hash(path)} for key, path in paths.items()},
             "drafts": report["fingerprint"]["drafts"], "length": length, "records": records,
             "ledger": ledger, "entry": {"min": args.min_duration, "max": 30, "durations": args.durations},
             "style_hash": sha(style) if style is not None else None,
             "ranges": [], "units": [], "next_numbers": {}, "receipts": []}
    require(4 <= args.min_duration <= 30, "入口下限须在4～30秒内")
    if args.durations:
        require(all(args.min_duration <= d <= 30 for d in args.durations), "合法档位超出入口时长范围")
    write_json(project / "session.json", state)
    return {"status": "bound", "project": str(project), "phase": state["phase"], "length": length}


def load(project, verify_style=True):
    state = read_json(project / "session.json")
    require(state.get("schema_version") == 1, "项目状态版本不兼容")
    for label, record in state["files"].items():
        require(Path(record["path"]).is_file() and file_hash(record["path"]) == record["sha256"], f"{label}已变化；请按改稿流程重新绑定")
    scene_dir = Path(state["files"]["source"]["path"]).parent / "scenes"
    current = {p.name: sha(p.read_text(encoding="utf-8-sig")) for p in scene_dir.glob("*.txt")}
    require(current == state["drafts"], "逐场稿已变化；请重新复核绑定")
    if verify_style and state["style_hash"]:
        require(sha((project / "video-style.txt").read_text(encoding="utf-8-sig").strip()) == state["style_hash"], "风格文件被外部更改；请用style命令更新")
    return state


def save(project, state):
    state["revision"] += 1
    write_json(project / "session.json", state)


def validate_style(style):
    require(style.strip() and "\n" not in style and "\r" not in style, "风格须为非空单行文本")


def set_style(args, state=None):
    require(args.choice != "3" or args.file is not None, "自定义风格须指定--file")
    style = STYLES[args.choice] if args.choice in STYLES else args.file.read_text(encoding="utf-8-sig").strip()
    validate_style(style)
    # Semantic conflicts (including negated requests) are reviewed by the caller.
    atomic_text(args.project / "video-style.txt", style + "\n")
    if state is not None:
        state["style_hash"], state["phase"] = sha(style), "range"
        save(args.project, state)
    return {"status": "style_saved", "style_file": str((args.project / "video-style.txt").resolve())}


def portion(record, start, end):
    a, b = max(start, record["start"]), min(end, record["end"])
    if a >= b:
        return ""
    positions = [i for i, c in enumerate(record["text"]) if c.isalnum()]
    left = 0 if a == record["start"] else positions[a - record["start"]]
    right = len(record["text"]) if b == record["end"] else positions[b - record["start"]]
    return record["text"][left:right]


def excerpt(state, start, end):
    return "\n".join(portion(r, start, end) for r in state["records"] if r["start"] < end and r["end"] > start)


def occurrences(text, fragment):
    cursor = 0
    while True:
        index = text.find(fragment, cursor)
        if index < 0:
            break
        yield index
        cursor = index + 1


def anchors(state, quote):
    fragment = compact(quote)
    require(fragment, "起止锚点不能为空或只有标点")
    results = {}
    # Both surfaces map every matching character back to canonical body offsets.
    for with_labels in (False, True):
        surface, positions = [], []
        for r in state["records"]:
            if with_labels:
                label = compact(r["label"])
                surface.extend([r["label"], "："])
                positions.extend([r["start"]] * len(label))
            surface.extend([r["text"], "\n"])
            positions.extend(range(r["start"], r["end"]))
        for i in anchor_occurrences("".join(surface), quote):
            a, b = positions[i], positions[i + len(fragment) - 1] + 1
            results[(a, b)] = {"span": [a, b], "approximate": False}
    if results:
        return list(results.values())
    source = read_json(state["files"]["source"]["path"])
    report = read_json(state["files"]["review"]["path"])
    char_paragraph, original = [], []
    mapping = {}
    records = {(r["scene"], r["local_line"]): r for r in state["records"]}
    scene_by_number = {s["number"]: s["id"] for s in state["ledger"]["scenes"]}
    for scene in report["scenes"]:
        # Review records use filename and local scene line numbers.
        match = re.search(r"scene_(\d+)_", scene.get("file", scene.get("filename", "")))
        if not match:
            match = re.search(r"场景(\d+)[：-]", scene.get("scene", ""))
        if not match:
            continue
        scene_id = scene_by_number[int(match.group(1))]
        draft = (Path(state["files"]["source"]["path"]).parent / "scenes" / scene["file"]).read_text(encoding="utf-8-sig").splitlines()
        leading = next(i for i, line in enumerate(draft) if line.strip())
        for entry in scene["paragraph_map"]:
            destinations = [records.get((scene_id, d["line"] - leading)) for d in entry.get("destinations", [])]
            mapping[entry["paragraph"]] = [r for r in destinations if r]
    for paragraph in source["paragraphs"]:
        text = compact(paragraph["text"])
        original.extend([paragraph["text"], "\n"])
        char_paragraph.extend([paragraph["id"]] * len(text))
    for i in anchor_occurrences("".join(original), quote):
        ids = set(char_paragraph[i:i + len(fragment)])
        destinations = [r for n in ids for r in mapping.get(n, [])]
        if destinations:
            a, b = min(r["start"] for r in destinations), max(r["end"] for r in destinations)
            results[(a, b)] = {"span": [a, b], "approximate": True}
    return list(results.values())


def merged_intervals(intervals):
    result = []
    for a, b in sorted(intervals):
        if result and a <= result[-1][1]:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([a, b])
    return result


def range_review(state, span):
    a, b = interval(span, state["length"])
    issues = []
    overlaps = merged_intervals([[max(a, h["span"][0]), min(b, h["span"][1])] for h in state["ranges"]
                                if max(a, h["span"][0]) < min(b, h["span"][1])])
    issues.extend({"kind": "重叠", "span": pair, "text": excerpt(state, *pair)} for pair in overlaps)
    if state["ranges"]:
        last = state["ranges"][-1]["span"]
        if a > last[1]:
            cursor = last[1]
            for x, y in merged_intervals([h["span"] for h in state["ranges"]]):
                if y <= cursor or x >= a:
                    continue
                if x > cursor:
                    issues.append({"kind": "间隔", "span": [cursor, min(x, a)], "text": excerpt(state, cursor, min(x, a))})
                cursor = max(cursor, y)
            if cursor < a:
                issues.append({"kind": "间隔", "span": [cursor, a], "text": excerpt(state, cursor, a)})
        if a < last[0]:
            issues.append({"kind": "顺序回退", "span": span, "text": excerpt(state, a, b)})
    token = sha(packed([state["revision"], span, issues])) if issues else None
    return {"issues": issues, "confirmation_token": token}


def locate(args, state):
    request = read_json(args.anchors)
    starts, ends = anchors(state, request["start"]), anchors(state, request["end"])
    candidates = {}
    for start in starts:
        for end in ends:
            a, b = start["span"][0], end["span"][1]
            if start["span"][0] <= end["span"][0] and start["span"][1] <= b:
                span = [a, b]
                candidates[(a, b)] = {"span": span, "approximate": start["approximate"] or end["approximate"],
                                      "text": excerpt(state, a, b), **range_review(state, span)}
    values = list(candidates.values())
    if len(values) > 100:
        return {"status": "need_longer_anchors", "candidate_count": len(values)}
    status = "not_found_or_reversed" if not values else "needs_selection" if len(values) > 1 or values[0]["approximate"] else "located"
    return {"status": status, "candidates": values}


def milliseconds(value):
    require(type(value) in (int, float), "声音时码须为数字")
    number = Decimal(str(value)) * 1000
    require(number.is_finite() and number == number.to_integral_value(), "声音时码最多三位小数")
    return int(number)


def fixed_header():
    text = (ROOT / "assets" / "fixed-header.txt").read_text(encoding="utf-8-sig").strip()
    require(sha(text) == HEADER_SHA, "固定抬头被修改；请恢复原样常量")
    return text.splitlines()


def reference_catalog(state, asset_ids=None):
    ledger = state["ledger"]
    catalog = {item["id"]: item["name"] for category in ("characters", "props") for item in ledger[category]}
    if ledger.get("schema_version") == 3:
        catalog.update({item["id"]: item["name"] for item in ledger["scene_assets"]})
    else:
        # Legacy snapshots remain readable; canvas writes require an audited upgrade.
        catalog.update({item["id"]: item["heading"] for item in ledger["scenes"]})
    if asset_ids is not None:
        catalog = {key: name for key, name in catalog.items() if key in asset_ids}
    owners = {}
    for asset_id, name in catalog.items():
        require(name and name == name.strip() and name != "黑幕" and "@" not in name and not any(c in name for c in "{}\r\n"), f"资产名称为空、含引用分隔符或占用黑幕名称：{asset_id} {name}")
        require(name not in owners, f"资产名称重名，无法唯一引用图片：{owners.get(name)} / {asset_id} {name}；请先处理台账命名")
        owners[name] = asset_id
    return catalog


def scene_asset_ids(state, scene_ids):
    scenes = {s["id"]: s for s in state["ledger"]["scenes"]}
    return list(dict.fromkeys(scenes[sid]["scene_asset_id"] for sid in scene_ids)) if state["ledger"].get("schema_version") == 3 else list(scene_ids)


def prompt_only_ids(state):
    return {item["id"] for item in state["ledger"]["characters"] if item.get("visual_mode") == "prompt_only"}


def reference_name(state, asset_id, catalog):
    name = catalog[asset_id]
    return name if asset_id in prompt_only_ids(state) else "@" + name + " "


def asset_text(state, unit, text):
    """Resolve explicit references only; never replace bare substrings or dialogue."""
    scenes = {s["id"]: s for s in state["ledger"]["scenes"]}
    allowed = set(scene_asset_ids(state, unit["scene_ids"]) + unit["character_ids"])
    allowed.update(pid for sid in unit["scene_ids"] for pid in scenes[sid]["prop_ids"])
    catalog = reference_catalog(state, allowed)
    plain = prompt_only_ids(state)
    by_name = {name: aid for aid, name in catalog.items()}
    names = sorted(by_name, key=len, reverse=True)
    pattern = re.compile(r"@(?:\{([^{}]+)\}|(" + "|".join(re.escape(name) for name in names) + r")(?= ))")
    used, output, cursor = set(), [], 0
    for at in re.finditer("@", text):
        if at.start() < cursor:
            continue
        match = pattern.match(text, at.start())
        require(match is not None, f"无法识别资产引用（名称后须有英文空格）：{text[at.start():at.start() + 40]}")
        asset_id = match.group(1) or by_name[match.group(2)]
        require(asset_id in catalog and asset_id in allowed, f"资产引用不属于本单元或不存在：{asset_id}")
        output.extend([text[cursor:at.start()], ("" if asset_id in plain else "@") + catalog[asset_id]])
        if asset_id not in plain and match.group(1) and text[match.end():match.end() + 1] != " ":
            output.append(" ")
        used.add(asset_id)
        cursor = match.end()
    output.append(text[cursor:])
    return "".join(output), used


def unit_name(unit):
    return unit.get("display_name", unit["id"])


def resolve_unit_id(state, value):
    """Resolve exact stable IDs, full visible names or a unique batch short name."""
    matches = {unit["id"] for unit in state["units"] if value == unit["id"] or value == unit_name(unit)
               or (unit.get("display_name") and value == unit["display_name"].split("-", 1)[0])}
    require(len(matches) == 1, f"分镜名称不存在或无法唯一定位：{value}")
    return next(iter(matches))


def resolved_request(state, request):
    request = copy.deepcopy(request)
    if request.get("mode") == "replace":
        for unit in request.get("units", []):
            unit["id"] = resolve_unit_id(state, unit.get("id"))
    return request


def validate_unit(state, unit, request_span, *, require_continuity=False):
    require(unit.get("output_format") == OUTPUT_FORMAT, "新生成/修改单元须采用reference-v3输出模板；旧交付仅保留读取和渲染")
    a, b = interval(unit["span"], state["length"])
    duration = unit["duration"]
    require(type(duration) is int and state["entry"]["min"] <= duration <= 30, "单元时长不合法")
    if state["entry"]["durations"]:
        require(duration in state["entry"]["durations"], "单元时长不在入口合法档位")
    selected = [r for r in state["records"] if r["start"] < b and r["end"] > a]
    covered_scenes = list(dict.fromkeys(r["scene"] for r in selected))
    require(unit["scene_ids"] == covered_scenes, "单元涉及场景与标准来源不一致或顺序错误")
    require(unit["main_scene"] in covered_scenes, "主场景不在本单元来源中")
    if len(covered_scenes) > 1:
        require(unit.get("cross_scene_reason") in ("回忆", "想象"), "实际切场必须截断；只允许回忆/想象跨场")
        evidence = unit.get("cross_scene_evidence", "")
        source = Path(state["files"]["script"]["path"]).read_text(encoding="utf-8-sig")
        require(evidence and evidence in source, "跨场回忆/想象须提供标准剧本原句证据")
    if duration < 26:
        reason = unit.get("short_reason")
        require(reason in ("scene_cut", "range_end", "script_end", "natural_beat"), "短单元缺少边界或自然叙事落点依据")
        if reason == "natural_beat":
            require(isinstance(unit.get("short_basis"), str) and unit["short_basis"].strip(), "自然短单元须记录动作/语义完成依据，不填充时长")
        if reason == "scene_cut":
            next_record = next((r for r in state["records"] if r["start"] >= b), None)
            require(selected[-1]["end"] == b and next_record and next_record["scene"] != selected[-1]["scene"], "短单元终点并非实际场景边界")
        elif reason == "range_end":
            require(b == request_span[1], "范围末尾短单元必须结束于选定范围终点")
        elif reason == "script_end":
            require(b == state["length"], "全剧末尾短单元未到全剧终点")
    require(all(isinstance(unit.get(k), str) and unit[k].strip() for k in ("time", "weather", "global", "start_state", "end_state")), "缺少时间/天气/人物站位/承接状态")
    require(not any(c in unit["global"] for c in "\r\n"), "人物站位须为单行")
    scenes = {s["id"]: s for s in state["ledger"]["scenes"]}
    chars = {c["id"]: c for c in state["ledger"]["characters"]}
    props = {p["id"]: p for p in state["ledger"]["props"]}
    variants = None
    if state["ledger"].get("character_variant_policy"):
        dependency()
        variants = importlib.import_module("character_variants")
    scene_chars = {sid: set(variants.scene_character_ids(scene) if variants else scene["character_ids"])
                   for sid, scene in scenes.items()}
    allowed_chars = set().union(*(scene_chars[s] for s in covered_scenes))
    allowed_props = set().union(*(set(scenes[s]["prop_ids"]) for s in covered_scenes))
    require(isinstance(unit["character_ids"], list) and len(unit["character_ids"]) == len(set(unit["character_ids"]))
            and set(unit["character_ids"]) <= allowed_chars, "单元人物不在关联台账中或重复")
    require(isinstance(unit["prop_ids"], list) and len(unit["prop_ids"]) == len(set(unit["prop_ids"]))
            and set(unit["prop_ids"]) <= allowed_props, "单元主要道具不在关联台账中或重复")
    asset_text(state, unit, unit["global"])
    shots, warnings = unit["shots"], []
    require(shots, "单元没有镜头")
    if duration >= 26:
        require(8 <= len(shots) <= 13, "26～30秒单元镜数必须在8～13镜")
    if duration >= 29 and len(shots) != 11 or 26 <= duration <= 28 and len(shots) not in (9, 10):
        require(isinstance(unit.get("count_reason"), str) and unit["count_reason"].strip(), "偏离默认镜数须在后台记录自然表达依据")
    cursor, closeups, used_chars, used_props = 0, 0, set(), set()
    for i, shot in enumerate(shots):
        start, end = shot["start"], shot["end"]
        require(type(start) is int and type(end) is int and start == cursor and 1 <= end - start <= 5, "镜头时间轴不连续或单镜不在1～5秒")
        framing = shot["framing"]
        require(framing in FRAMINGS, "无法识别景别")
        closeups += framing == "特写"
        require(shot["scene_id"] in covered_scenes, "镜头场景不在来源内")
        shot_assets = scene_chars[shot["scene_id"]] | set(scenes[shot["scene_id"]]["prop_ids"])
        shot_references = shot_assets | set(scene_asset_ids(state, [shot["scene_id"]]))
        subjects = shot["subjects"]
        require(isinstance(subjects, list) and len(subjects) == len(set(subjects)), "主体列表格式错误或重复")
        valid_subjects = set(unit["character_ids"]) | set(unit["prop_ids"])
        require(set(subjects) <= valid_subjects, "镜头主体未列入本单元资产")
        require(set(subjects) <= shot_assets, "镜头使用了其他场景的资产")
        require("focus" in shot or not subjects, "有人物或主要道具的镜头须明确观看焦点focus")
        if "focus" in shot:
            focus = shot["focus"]
            require(isinstance(focus, list) and len(focus) == len(set(focus))
                    and set(focus) <= set(subjects), "观看焦点须为本镜参与资产的无重复子集")
            require(bool(focus) or not subjects, "有人物或主要道具的镜头须明确观看焦点")
        purpose = shot.get("purpose")
        require(isinstance(purpose, str) and purpose.strip() and not any(c in purpose for c in "\r\n"),
                "reference-v3每镜purpose须为非空单行，说明新增信息/情绪与摄影选择依据")
        require(compact(purpose) and compact(purpose) not in ("同前", "同上", "无", "电影感", "推进剧情", "体现情绪", "展示人物"),
                "镜头purpose不能使用空泛占位值；须具体说明观看重点与摄影选择依据")
        for field in ("label", "transition"):
            value = shot.get(field)
            require(isinstance(value, str) and value.strip() and not any(c in value for c in "\r\n[]"),
                    f"镜头{field}须为非空单行，不能含换行或方括号")
            _, referenced = asset_text(state, unit, value)
            require(referenced <= shot_references,
                    f"镜头{field}引用了其他场景的资产")
        photography_references = set()
        for field in PHOTOGRAPHY_FIELDS:
            value = shot.get(field)
            require(isinstance(value, str) and value.strip() and not any(c in value for c in "\r\n")
                    and "：“" not in value, f"镜头{field}须为非空单行摄影描述，台词仅填入dialogue_parts")
            require(compact(value) and compact(value) not in ("同前", "同上", "无", "电影感"),
                    f"镜头{field}不能使用无信息占位值；须具体说明本镜摄影状态")
            _, referenced = asset_text(state, unit, value)
            require(referenced <= shot_references,
                    f"镜头{field}引用了其他场景的资产")
            photography_references.update(referenced)
        text = shot["text"]
        require(isinstance(text, str) and text.strip() and not any(c in text for c in "\r\n") and "：“" not in text, "镜头正文须为非空单段，台词仅填入dialogue_parts由程序渲染")
        for value in (text, shot.get("connector", "")):
            require(not isinstance(value, str) or not re.search(r"发声估时|实配音\s*\d|同句续声|继续说完同一|承接.{0,12}(?:誓言|台词).{0,8}(?:部分|末尾)", value),
                    "镜头正文不得泄漏估时或用抽象续声代替实际台词")
        _, referenced = asset_text(state, unit, text)
        require(set(subjects) <= referenced | photography_references | set(shot.get("focus", [])), "正文参与者须使用@台账完整名称或@{资产ID}引用；主焦点由景别字段引用")
        require(referenced <= shot_references,
                "镜头正文引用了其他场景的资产")
        if not subjects:
            evidence = shot.get("environment_evidence", "")
            require(evidence and evidence in excerpt(state, a, b), "无人物/核心道具镜头须有本范围原文明示环境证据")
        require(shot.get("angle") in ("正面", "斜侧", "侧拍", "背拍", "俯拍", "仰拍", "第一人称", "客观"), "机位分类不合法")
        if "connector" in shot:
            require(isinstance(shot["connector"], str), "连接词须为字符串")
            if shot["connector"].strip():
                _, referenced = asset_text(state, unit, shot["connector"])
                require(referenced <= shot_references,
                        "镜头连接词引用了其他场景的资产")
        for field in ("sound_foreground", "sound_environment"):
            if field in shot:
                require(isinstance(shot[field], str) and not any(c in shot[field] for c in "\r\n"), "镜头音效须为单行字符串，可留空")
                _, referenced = asset_text(state, unit, shot[field])
                require(referenced <= shot_references,
                        "镜头音效引用了其他场景的资产")
        used_chars.update(set(subjects) & set(chars))
        used_props.update(set(subjects) & set(props))
        cursor = end
    require(cursor == duration, "各镜头时长总和不等于单元时长")
    if closeups > 2:
        warnings.append({"kind": "单元特写较集中需审查", "closeup_count": closeups,
                         "review": "超过两镜特写仅触发审查，不是数量配额；检查每镜必要信息、焦点变化及空间关系。"})
    events = unit["audio"]
    require(isinstance(events, list), "audio须为数组")
    dialogue_layout.validate_voice_policy(unit)
    expected = {r["id"]: r for r in selected if r["kind"] == "dialogue"}
    require(len(events) == len(expected) and {e["record"] for e in events} == set(expected), "声音事件遗漏、重复或不属于本单元台词")
    previous_start, voice_ids, used_record_ids = -1, set(), []
    for event in events:
        record = expected[event["record"]]
        used_record_ids.append(record["id"])
        require(event["text"] == portion(record, a, b), "台词与标准剧本所选部分不逐字一致")
        require(event["voice"] == record["voice"], "发声类型与标准剧本不一致")
        cid = event["character_id"]
        require(cid in unit["character_ids"] and cid in scene_chars[record["scene"]], "声源未列入本单元/原场景人物")
        name = chars[cid].get("source_entry", chars[cid]["name"])
        speaker = name.split("-群像-", 1)[0] + "-群像" if "-群像-" in name else name.split("-", 1)[0]
        speaker = chars[cid].get("source_speaker", speaker)
        require(speaker == record["speaker"], "台词说话人与台账声源不一致")
        t0, t1, pause = (milliseconds(event[k]) for k in ("start", "end", "pause"))
        require(0 <= t0 < t1 <= duration * 1000 and 0 <= pause < t1 - t0, "声音时间越界、倒序或停顿非法")
        require(t0 >= previous_start, "声音事件须按开始时间排序")
        previous_start = t0
        for other in events[:events.index(event)]:
            if max(t0, milliseconds(other["start"])) < min(t1, milliseconds(other["end"])):
                evidence = event.get("overlap_evidence", "")
                require(evidence and evidence in Path(state["files"]["script"]["path"]).read_text(encoding="utf-8-sig"), "声音重叠缺少原剧情明确依据")
        require(event["speed"] in SPEEDS and type(event["units"]) is int and event["units"] > 0, "语速档位或可发音单位非法")
        require(event.get("timing_basis", "text_estimate") in ("text_estimate", "recording"), "声音估时依据不合法")
        if event.get("timing_basis") == "recording":
            require(isinstance(event.get("recording_evidence"), str) and event["recording_evidence"].strip(), "实配音时码须注明录音测量依据")
        rate = event["units"] * 1000 / (t1 - t0 - pause)
        low, high = SPEEDS[event["speed"]]
        timing = {}
        if event.get("timing_basis", "text_estimate") == "text_estimate":
            source_pause = dialogue_layout.pause_seconds(event["text"])
            fast = dialogue_layout.estimate(event["text"], event["units"], high)
            slow = dialogue_layout.estimate(event["text"], event["units"], low)
            minimum = round(fast - source_pause + pause / 1000, 3)
            maximum = round(slow - source_pause + pause / 1000, 3)
            in_band = minimum - .001 <= (t1 - t0) / 1000 <= maximum + .001
            timing = {"text_estimate": {"min_seconds": minimum, "max_seconds": maximum,
                      "source_pause_seconds": source_pause, "registered_pause_seconds": pause / 1000}}
        else:
            in_band = low <= rate <= high
        if not in_band:
            warnings.append({"record": record["id"], "kind": "估计净语速超档", "rate": round(rate, 3), **timing})
        voice_ids.add(cid)
    require(used_record_ids == list(expected), "台词事件顺序与标准剧本不同")
    require(set(unit["character_ids"]) == used_chars | voice_ids, "人物名单存在未出镜/未发声角色，或遗漏主体")
    require(set(unit["prop_ids"]) == used_props, "主要道具名单与镜头主体不一致")
    displayed, order, lookup = {e["record"]: [] for e in events}, [], {e["record"]: e for e in events}
    for shot in shots:
        for part in shot.get("dialogue_parts", []):
            rid, text = part["record"], part["text"]
            require(rid in lookup and isinstance(text, str) and text, "镜头台词片段不对应本单元声音事件")
            event = lookup[rid]
            require(max(shot["start"] * 1000, milliseconds(event["start"])) < min(shot["end"] * 1000, milliseconds(event["end"])), "台词片段所在镜头不在该声音事件时间内")
            displayed[rid].append(text)
            order.append(rid)
    for event in events:
        require("".join(displayed[event["record"]]) == event["text"], "镜头台词有漏字、重复、改词或标点差异")
    # Source order applies unless overlap has explicit source evidence.
    if not any(e.get("overlap_evidence") for e in events):
        collapsed = [rid for i, rid in enumerate(order) if i == 0 or rid != order[i - 1]]
        require(collapsed == list(expected), "镜头台词片段顺序与标准剧本不一致")
    if unit.get("dialogue_layout") == dialogue_layout.POLICY:
        dialogue_layout.validate(unit)
        from speech_timing_audit import audit as audit_fragment_timing
        warnings.extend(audit_fragment_timing(unit))
        warnings.extend(dialogue_layout.voice_warnings(unit))
    key = unit.get("id", "当前单元")
    warnings.extend(photography_warnings(state, [(key, i + 1, shot) for i, shot in enumerate(shots)]))
    continuity_result = continuity.validate_unit(state, unit, required=require_continuity)
    if continuity_result is not None:
        warnings.extend(continuity_result["warnings"])
    if variants:
        variants.validate_storyboard(state, unit)
    from prop_reference_review import check as check_prop_references
    warnings.extend(check_prop_references(state, unit))
    from group_reference_review import check as check_group_references
    warnings.extend(check_group_references(state, unit))
    from shot_review import diagnostics as shot_diagnostics, appearance_reference_warnings
    warnings.extend(shot_diagnostics(unit))
    warnings.extend(appearance_reference_warnings(state, unit))
    if unit.get('workflow_policy') == 'scene-review-b-v2':
        from authoring_gates import validate_staging
        validate_staging(state, unit)
    return warnings


def require_workflow(request):
    """New workflow entry points opt in; historical receipts remain readable."""
    from storyboard_preflight import POLICY
    units = request.get("units", [])
    require(isinstance(units, list) and units and all(
        isinstance(unit, dict) and unit.get("workflow_policy") == POLICY for unit in units),
        "B流程新单元须登记workflow_policy=scene-review-b-v2；不升级未选旧单元")


def validate(state, request, partial=False):
    fixed_header()
    require(state["style_hash"], "尚未选择并保存视频风格")
    require(request.get("mode") in ("append", "replace"), "mode须为append或replace")
    require(isinstance(request.get("request_id"), str) and request["request_id"].strip(), "缺少幂等request_id")
    units = request["units"]
    require(isinstance(units, list) and units, "请求没有分镜单元")
    for unit in units:
        require(unit.get("output_format") == OUTPUT_FORMAT, "新生成/修改单元须采用reference-v3输出模板；旧交付仅保留读取和渲染")
        if unit.get("audio"):
            require(unit.get("dialogue_layout") == dialogue_layout.POLICY,
                    "新生成/修改的有声单元须采用semantic-v2台词布局；旧交付仅保留读取和渲染")
    request = resolved_request(state, request)
    units = request["units"]
    from storyboard_preflight import validate as validate_preflight
    warnings = validate_preflight(state, request)
    if request["mode"] == "append":
        a, b = interval(request["span"], state["length"])
        review = range_review(state, [a, b])
        if review["issues"]:
            require(request.get("range_confirmation") == review["confirmation_token"], "范围重叠/间隔/回退尚未获得本次确认")
        cursor = a
        for unit in units:
            require(unit["span"][0] == cursor and unit["span"][1] <= b, "单元来源范围不连续、重复或越界")
            warnings.extend(validate_unit(state, unit, [a, b], require_continuity=True))
            cursor = unit["span"][1]
        require(cursor == b or partial, "本批单元未完整覆盖选定范围")
        prospective = state["units"] + units
        touched = set(range(len(state["units"]), len(prospective)))
    else:
        require(not partial, "局部替换必须完整检查所有提交单元")
        ids = [u.get("id") for u in units]
        require(len(ids) == len(set(ids)), "重复替换同一单元")
        existing = {u["id"]: u for u in state["units"]}
        for unit in units:
            require(unit.get("id") in existing, "指定分镜编号不存在")
            old = existing[unit["id"]]
            require(unit["span"] == old["span"] and unit["main_scene"] == old["main_scene"], "局部修改不能改变来源范围或主场景")
            # range_end refers to the original batch boundary, never an arbitrary replacement boundary.
            boundary = next(r["span"] for r in state["ranges"] if r["batch_id"] == old["batch_id"])
            warnings.extend(validate_unit(state, unit, boundary, require_continuity=True))
        replacements = {u["id"]: u for u in units}
        prospective = [dict(u, **{**replacements[u["id"]], "batch_id": u.get("batch_id"),
                                  "predecessor": u.get("predecessor")})
                       if u["id"] in replacements else u for u in state["units"]]
        touched = {i for i, u in enumerate(prospective) if u["id"] in replacements}
    warnings.extend(continuity.validate_chain(state, prospective, touched))
    total = sum(len(u["shots"]) for u in prospective)
    special = sum(s["framing"] == "特写" for u in prospective for s in u["shots"])
    if special * 100 > total * 15:
        warnings.append({"kind": "累计特写比例较高需审查", "closeup_count": special,
                         "total_shots": total, "percent": round(special * 100 / total, 3), "review_reference_percent": 15,
                         "review": "15%仅为诊断参考，不限制提交；低于参考线也不代表摄影质量合格，仍审查看点、空间与面部重复。"})
    warnings.extend(boundary_photography_warnings(state, prospective))
    return warnings


def render_unit(state, unit, reference_text=None):
    convert = reference_text or (lambda text: text)
    chars = {c["id"]: c["name"] for c in state["ledger"]["characters"]}
    scenes = {s["id"]: s["heading"] for s in state["ledger"]["scenes"]}
    relevant = set(unit["character_ids"] + scene_asset_ids(state, unit["scene_ids"]))
    relevant.update(pid for scene in state["ledger"]["scenes"] if scene["id"] in unit["scene_ids"] for pid in scene["prop_ids"])
    catalog = reference_catalog(state, relevant)
    header = fixed_header()
    structured_format = unit.get("output_format") == OUTPUT_FORMAT
    current_format = unit.get("output_format") in ("reference-v2", OUTPUT_FORMAT)
    reference_format = unit.get("output_format") in ("reference-v1", "reference-v2", OUTPUT_FORMAT)
    people = ("，".join(convert(reference_name(state, c, chars)) for c in unit["character_ids"]) + "；") if reference_format else "".join(chars[c] + convert(reference_name(state, c, chars)) + "；" for c in unit["character_ids"])
    scene_text = "".join(("" if structured_format else scenes[s])
                         + convert("@" + catalog[scene_asset_ids(state, [s])[0]] + " ") + "；"
                         for s in unit["scene_ids"])
    require(unit.get("black_reference_mode") in (None, "edge-only-v1", BLACK_REFERENCE_MODE), "未知的黑幕引用模式")
    lines = [f'{unit_name(unit)}（时长：{unit["duration"]}秒）']
    if unit.get("black_reference_mode") not in ("edge-only-v1", BLACK_REFERENCE_MODE):
        lines.append(convert("@黑幕"))
    lines.extend([unit["style"] + ";" + header[0], header[1],
             "人物：" + (people if unit["character_ids"] else "无；"),
             "场景：" + scene_text + f'时间：{unit["time"]}；天气：{unit["weather"]}；'])
    if current_format:
        positions = unit["global"].removeprefix("人物站位：").strip()
        lines.append("人物站位：" + convert(asset_text(state, unit, positions)[0]))
    elif not reference_format:
        lines.append(convert(asset_text(state, unit, unit["global"])[0]))
    for i, shot in enumerate(unit["shots"]):
        if not reference_format and i and shot.get("connector", "").strip():
            lines.append(convert(asset_text(state, unit, shot["connector"])[0]))
        parts = []
        for part in shot.get("dialogue_parts", []):
            event = next(e for e in unit["audio"] if e["record"] == part["record"])
            timing = dialogue_layout.timing(part, shot, event.get("timing_basis") == "recording") if not current_format and unit.get("dialogue_layout") == dialogue_layout.LEGACY_POLICY else ""
            delivery = dialogue_layout.delivery_text(part, event, unit) if structured_format else ""
            parts.append(convert(reference_name(state, event["character_id"], chars)) + event["voice"] + timing + delivery + '：“' + part["text"] + '”')
        for link in shot.get("dialogue_continuations", []):
            event = next(e for e in unit["audio"] if e["record"] == link["record"])
            chunks = [p for s in unit["shots"] for p in s.get("dialogue_parts", []) if p["record"] == link["record"]]
            chunk = chunks[link["segment"]]
            parts.append(convert(reference_name(state, event["character_id"], chars)) +
                         f'同句续声至{dialogue_layout.clock(chunk["end"])}秒。')
        speech = (" " + " ".join(parts)) if parts else ""
        body = convert(asset_text(state, unit, shot["text"])[0])
        if reference_format:
            connector = shot.get("connector", "").strip()
            if connector:
                body += " " + convert(asset_text(state, unit, connector)[0])
            body += speech
            if body[-1:] not in ("。", "！", "？", "!", "?", "；", ";"):
                body += "。"
            focus = shot.get("focus", [])
            focal_ids = focus or [scene_asset_ids(state, [shot["scene_id"]])[0]]
            focal = "、".join(convert(reference_name(state, aid, catalog)) for aid in focal_ids)
            sounds = [convert(asset_text(state, unit, shot[field].strip().rstrip("。"))[0])
                      for field in ("sound_foreground", "sound_environment")
                      if shot.get(field, "").strip().rstrip("。") not in ("", "无")]
            sound = "+".join(sounds) or "无"
            label = convert(asset_text(state, unit, shot["label"])[0])
            transition = convert(asset_text(state, unit, shot["transition"])[0])
            if structured_format:
                framing_text = f'（{focal}的{shot["framing"]}；转场：{transition}）'
                photography = [convert(asset_text(state, unit, shot[field].strip().rstrip("，；。"))[0])
                               for field in PHOTOGRAPHY_FIELDS]
                body = "，".join(photography) + "；" + body
            else:
                framing_text = f'景别：{focal}的{shot["framing"]}；转场：{transition}'
                framing_text = framing_text + '，' if current_format else '（' + framing_text + '）'
            lines.append(f'·{shot["start"]:02d}秒-{shot["end"]:02d}秒[镜头{i + 1}-{label}]：{framing_text}{body}音效：{sound}。')
        else:
            lines.append(f'[{shot["start"]}秒-{shot["end"]}秒] {body}{speech}')
    return "\n".join(lines)


def validate_output_path(state, project, output, batch_scope=None):
    output = Path(output).resolve()
    project = Path(project).resolve()
    protected = [Path(x["path"]).resolve() for x in state["files"].values()]
    protected.extend((project / n).resolve() for n in ("session.json", "video-style.txt"))
    scope = batch_naming.scope_for(project, state.get("batch_scope"), batch_scope)
    protected.extend(scope / n for n in (batch_naming.REGISTRY, ".batch-names.lock"))
    directories = [ROOT.resolve(), (project / "canvas").resolve(), (project / "scenes").resolve()]
    if "source" in state["files"]:
        directories.append((Path(state["files"]["source"]["path"]).resolve().parent / "scenes").resolve())
    require(output not in protected and not any(output == folder or folder in output.parents for folder in directories),
            "交付路径不能覆盖来源、逐场稿、运行状态或Skill文件")
    return output


def render_receipt(state, receipt, project=None):
    if project is not None:
        validate_output_path(state, project, receipt["output"])
    if receipt.get("batch_scope"):
        batch_naming.recover(receipt["batch_scope"])
    display_format = receipt.get("display_format")
    require(display_format in (None, LEGACY_RECEIPT_DISPLAY_FORMAT, RECEIPT_DISPLAY_FORMAT), "未知的交付展示格式")
    rendered = []
    for number, unit in enumerate(receipt["units"], 1):
        text = render_unit(state, unit)
        if display_format == LEGACY_RECEIPT_DISPLAY_FORMAT:
            text = f"{number:02d} - " + text
        rendered.append(text)
    atomic_text(receipt["output"], "\n\n".join(rendered) + "\n")


def receipt_summary(receipt):
    total = sum(unit["duration"] for unit in receipt["units"])
    minutes, seconds = divmod(total, 60)
    count = len(receipt["units"])
    action = "修改" if receipt.get("mode") == "replace" else "生成"
    return {"unit_count": count, "total_seconds": total, "minutes": minutes, "seconds": seconds,
            "summary": f"本次{action}分镜共{count}个，总时长{minutes}分{seconds}秒。"}


def sync_canvas_replacements(project, receipt):
    if receipt.get("mode") != "replace" or not (project / "canvas" / "state.json").is_file():
        return {"status": "not_bound"}
    try:
        from canvas import read_state
        from canvas_jobs import enqueue
        mappings = read_state(project)["mappings"]
        selected = [u["id"] for u in receipt["units"] if mappings.get(u["id"], {}).get("kind") == "video"]
        if not selected:
            return {"status": "not_bound"}
        return enqueue(project, "videos", request_id=receipt["request_id"], ids=selected, existing_only=True, start=True)
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        return {"status": "pending", "message": str(exc)}


def commit(args, state):
    request = read_json(args.request)
    request_hash = sha(packed(request))
    old_receipt = next((r for r in state["receipts"] if r["request_id"] == request.get("request_id")), None)
    if old_receipt:
        require(old_receipt["request_hash"] == request_hash, "request_id已用于不同请求；请使用新ID")
        render_receipt(state, old_receipt, args.project)
        return {"status": "already_committed", "output": old_receipt["output"], "ids": [u["id"] for u in old_receipt["units"]],
                "names": [unit_name(u) for u in old_receipt["units"]],
                "canvas_sync": sync_canvas_replacements(args.project, old_receipt), **receipt_summary(old_receipt)}
    if getattr(args, "require_workflow", False):
        require_workflow(request)
    scope = None
    if request["mode"] == "append":
        scope = batch_naming.scope_for(args.project, state.get("batch_scope"), getattr(args, "batch_scope", None))
        require(scope != ROOT.resolve() and ROOT.resolve() not in scope.parents,
                "批次登记不能保存到Skill安装目录")
        require(not state.get("batch_scope") or (scope / batch_naming.REGISTRY).is_file(),
                "已绑定的批次登记丢失；请恢复原登记，不能重新从A开始")
    output = validate_output_path(state, args.project, args.output, scope)
    require(not output.exists(), "交付文件已存在；请使用新文件名以保留旧版本")
    warnings = validate(state, request)
    from review_gate import verify as verify_final_review
    review_path = getattr(args, "review", None)
    require(review_path is not None, "新提交须提供--review最终稿复核记录；结构检查通过不代表语义复核通过")
    final_review = verify_final_review(state, request, read_json(review_path), warnings)
    request = resolved_request(state, request)
    style = (args.project / "video-style.txt").read_text(encoding="utf-8-sig").strip()
    allocation = (batch_naming.allocate(scope, args.project, request["request_id"], request_hash)
                  if scope is not None else nullcontext(None))
    with allocation as batch_label:
        session_path = Path(args.project) / "session.json"
        if session_path.is_file():
            require(read_json(session_path).get("revision") == state.get("revision"),
                    "项目状态已被另一提交更新；请重新读取后重试")
        working = copy.deepcopy(state)
        units = copy.deepcopy(request["units"])
        scenes = {s["id"]: s for s in working["ledger"]["scenes"]}
        for index, unit in enumerate(units, 1):
            unit["style"] = style
            unit["black_reference_mode"] = BLACK_REFERENCE_MODE
            if request["mode"] == "append":
                main = unit["main_scene"]
                number = working["next_numbers"].get(main, 1)
                prior = next((u for u in reversed(working["units"]) if u["span"][1] == unit["span"][0]), None)
                unit.update(id=f'场景{scenes[main]["number"]}-分镜{number}', version=1,
                            batch_id=request["request_id"], previous_versions=[], predecessor=prior["id"] if prior else None,
                            batch_label=batch_label, batch_index=index)
                unit["display_name"] = f'{batch_label}{index:02d}-{unit["id"]}'
                working["next_numbers"][main] = number + 1
                working["units"].append(unit)
            else:
                old = next(u for u in working["units"] if u["id"] == unit["id"])
                unit.update(version=old["version"] + 1, batch_id=old["batch_id"], predecessor=old.get("predecessor"),
                            previous_versions=old.get("previous_versions", []) + [{k: v for k, v in old.items() if k != "previous_versions"}])
                for field in ("display_name", "batch_label", "batch_index"):
                    unit.pop(field, None)
                    if field in old:
                        unit[field] = old[field]
                working["units"][working["units"].index(old)] = unit
        if request["mode"] == "append":
            working["ranges"].append({"span": request["span"], "batch_id": request["request_id"],
                                     "confirmed": bool(request.get("range_confirmation")), "batch_label": batch_label})
            working["batch_scope"] = str(scope)
        receipt = {"request_id": request["request_id"], "request_hash": request_hash, "mode": request["mode"],
                   "output": str(output), "units": copy.deepcopy(units), "display_format": RECEIPT_DISPLAY_FORMAT,
                   "final_review": final_review}
        if scope is not None:
            receipt.update(batch_scope=str(scope), batch_label=batch_label)
        working["receipts"].append(receipt)
        working["phase"] = "nodes_choice"
        save(args.project, working)
        state.clear()
        state.update(working)
    # State is authoritative. A failed filesystem delivery can be recovered with render.
    render_receipt(state, receipt, args.project)
    return {"status": "committed", "output": str(output), "ids": [u["id"] for u in units],
            "names": [unit_name(u) for u in units], "warnings": warnings,
            "canvas_sync": sync_canvas_replacements(args.project, receipt), **receipt_summary(receipt)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("bind")
    p.add_argument("--project", type=Path, required=True)
    for name in ("script", "source", "review", "ledger"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--standardizer", type=Path)
    p.add_argument("--min-duration", type=int, default=4)
    p.add_argument("--durations", type=int, nargs="+")
    for command in ("style", "phase", "status", "locate", "range", "check", "commit", "render"):
        p = sub.add_parser(command)
        p.add_argument("--project", type=Path, required=True)
        if command == "style":
            p.add_argument("--choice", choices=("1", "2", "3"), required=True)
            p.add_argument("--file", type=Path)
        elif command == "phase":
            p.add_argument("--value", choices=("asset_choice", "image_choice", "image_folder", "style", "range", "nodes_choice", "followup"), required=True)
        elif command == "locate":
            p.add_argument("--anchors", type=Path, required=True)
        elif command == "range":
            p.add_argument("--start", type=int, required=True)
            p.add_argument("--end", type=int, required=True)
        elif command in ("check", "commit"):
            p.add_argument("--request", type=Path, required=True)
            p.add_argument("--require-workflow", action="store_true", help="新B流程要求所有选中单元显式使用scene-review-b-v1；旧回执幂等导出不升级")
            if command == "check":
                p.add_argument("--partial", action="store_true")
            else:
                p.add_argument("--output", type=Path, required=True)
                p.add_argument("--review", type=Path, help="与最终请求及当前上下文摘要一致的语义复核记录；旧提交幂等导出无需补录")
                p.add_argument("--batch-scope", type=Path, help="同一对话共享的批次登记目录；自定义项目跨项目共享时指定")
        elif command == "render":
            p.add_argument("--request-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "bind":
            result = bind(args)
        else:
            args.project = args.project.resolve()
            state = (None if args.command == "style" and not (args.project / "session.json").exists()
                     else load(args.project, verify_style=args.command != "style"))
            if args.command == "style":
                result = set_style(args, state)
            elif args.command == "phase":
                state["phase"] = args.value
                save(args.project, state)
                result = {"phase": state["phase"]}
            elif args.command == "status":
                result = {k: state[k] for k in ("phase", "revision", "entry", "ranges", "next_numbers")}
                result["units"] = [{k: u[k] for k in ("id", "version", "span", "duration", "start_state", "end_state")} for u in state["units"]]
                for item, unit in zip(result["units"], state["units"]):
                    item["name"] = unit_name(unit)
                result["batch_scope"] = state.get("batch_scope")
                if (args.project / "canvas" / "jobs").is_dir():
                    from canvas_jobs import status as job_status
                    result["canvas_jobs"] = job_status(args.project)
                from recovery_status import summarize
                result["recovery"] = summarize(args.project, state, result.get("canvas_jobs"))
            elif args.command == "locate":
                result = locate(args, state)
            elif args.command == "range":
                result = {"span": [args.start, args.end], **range_review(state, [args.start, args.end])}
            elif args.command == "check":
                if args.require_workflow:
                    require_workflow(read_json(args.request))
                result = {"status": "passed", "structural_check": "passed", "semantic_review": "not_performed",
                          "partial": args.partial, "warnings": validate(state, read_json(args.request), args.partial)}
            elif args.command == "commit":
                result = commit(args, state)
            else:
                receipt = next((r for r in state["receipts"] if r["request_id"] == args.request_id), None)
                require(receipt is not None, "找不到该提交记录")
                render_receipt(state, receipt, args.project)
                result = {"status": "rendered", "output": receipt["output"], **receipt_summary(receipt)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError, StopIteration) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
