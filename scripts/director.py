#!/usr/bin/env python3
"""Persist checked screenplay bindings, locate ranges, validate and render prompts."""
import argparse
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
HEADER_SHA = "8001822afdf324cbbfa3eb284b2a03ccbde084b2e70fa66763a26b5c738e5962"
STYLES = {"1": "3D国风动漫,暗黑武侠/超写实仙侠风格", "2": "3D国风动漫，眷思量风格，东方奇幻唯美风格"}
SPEEDS = {"常速": (4.0, 5.2), "快节奏": (4.5, 5.8), "情绪慢速": (3.3, 4.4)}
FRAMINGS = {"大远景", "大全景", "远景", "全景", "中景", "中近景", "近景", "特写"}
CLOSE = {"近景", "特写"}


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
                voice = ("内心想法" if "os" in label.lower() or "内心" in label else
                         "画外音" if "画外音" in label else "旁白" if "旁白" in label else "说")
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
    state = {"schema_version": 1, "revision": 0, "phase": "asset_choice", "standardizer": str(dep),
             "files": {key: {"path": str(path), "sha256": file_hash(path)} for key, path in paths.items()},
             "drafts": report["fingerprint"]["drafts"], "length": length, "records": records,
             "ledger": ledger, "entry": {"min": args.min_duration, "max": 30, "durations": args.durations},
             "style_hash": None, "ranges": [], "units": [], "next_numbers": {}, "receipts": []}
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


def set_style(args, state):
    style = STYLES[args.choice] if args.choice in STYLES else args.file.read_text(encoding="utf-8-sig").strip()
    require(style.strip() and "\n" not in style and "\r" not in style, "风格须为非空单行文本")
    # Semantic conflicts (including negated requests) are reviewed by the caller.
    atomic_text(args.project / "video-style.txt", style + "\n")
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
        chars, positions = [], []
        for r in state["records"]:
            if with_labels:
                label = compact(r["label"])
                chars.extend(label)
                positions.extend([r["start"]] * len(label))
            body = compact(r["text"])
            chars.extend(body)
            positions.extend(range(r["start"], r["end"]))
        for i in occurrences("".join(chars), fragment):
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
        original.extend(text)
        char_paragraph.extend([paragraph["id"]] * len(text))
    for i in occurrences("".join(original), fragment):
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


def reference_catalog(state):
    ledger = state["ledger"]
    catalog = {item["id"]: item["name"] for category in ("characters", "props") for item in ledger[category]}
    if ledger.get("schema_version") == 3:
        catalog.update({item["id"]: item["name"] for item in ledger["scene_assets"]})
    else:
        # Legacy snapshots remain readable; canvas writes require an audited upgrade.
        catalog.update({item["id"]: item["heading"] for item in ledger["scenes"]})
    owners = {}
    for asset_id, name in catalog.items():
        require(name and name == name.strip() and name != "黑幕" and "@" not in name and not any(c in name for c in "{}\r\n"), f"资产名称为空、含引用分隔符或占用黑幕名称：{asset_id} {name}")
        require(name not in owners, f"资产名称重名，无法唯一引用图片：{owners.get(name)} / {asset_id} {name}；请先处理台账命名")
        owners[name] = asset_id
    return catalog


def scene_asset_ids(state, scene_ids):
    scenes = {s["id"]: s for s in state["ledger"]["scenes"]}
    return list(dict.fromkeys(scenes[sid]["scene_asset_id"] for sid in scene_ids)) if state["ledger"].get("schema_version") == 3 else list(scene_ids)


def asset_text(state, unit, text):
    """Resolve explicit references only; never replace bare substrings or dialogue."""
    catalog = reference_catalog(state)
    scenes = {s["id"]: s for s in state["ledger"]["scenes"]}
    allowed = set(scene_asset_ids(state, unit["scene_ids"]) + unit["character_ids"])
    allowed.update(pid for sid in unit["scene_ids"] for pid in scenes[sid]["prop_ids"])
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
        output.extend([text[cursor:at.start()], "@" + catalog[asset_id]])
        if match.group(1) and text[match.end():match.end() + 1] != " ":
            output.append(" ")
        used.add(asset_id)
        cursor = match.end()
    output.append(text[cursor:])
    return "".join(output), used


def validate_unit(state, unit, request_span):
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
        require(reason in ("scene_cut", "range_end", "script_end"), "短单元缺少切场/范围结束/全剧结束依据")
        if reason == "scene_cut":
            next_record = next((r for r in state["records"] if r["start"] >= b), None)
            require(selected[-1]["end"] == b and next_record and next_record["scene"] != selected[-1]["scene"], "短单元终点并非实际场景边界")
        elif reason == "range_end":
            require(b == request_span[1], "范围末尾短单元必须结束于选定范围终点")
        else:
            require(b == state["length"], "全剧末尾短单元未到全剧终点")
    require(all(isinstance(unit.get(k), str) and unit[k].strip() for k in ("time", "weather", "global", "start_state", "end_state")), "缺少时间/天气/全局方案/承接状态")
    scenes = {s["id"]: s for s in state["ledger"]["scenes"]}
    chars = {c["id"]: c for c in state["ledger"]["characters"]}
    props = {p["id"]: p for p in state["ledger"]["props"]}
    allowed_chars = set().union(*(set(scenes[s]["character_ids"]) for s in covered_scenes))
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
    cursor, previous, closeups, used_chars, used_props = 0, None, 0, set(), set()
    for i, shot in enumerate(shots):
        start, end = shot["start"], shot["end"]
        require(type(start) is int and type(end) is int and start == cursor and 1 <= end - start <= 5, "镜头时间轴不连续或单镜不在1～5秒")
        framing = shot["framing"]
        require(framing in FRAMINGS, "无法识别景别")
        require(framing != previous and not (framing in CLOSE and previous in CLOSE), "相邻镜头景别重复或连续近景/特写")
        if framing in CLOSE:
            require(shot.get("closeup_reason") in ("情绪峰值", "核心道具", "动作高光", "剧情伏笔"), "近景/特写不在白名单")
        if i == 0:
            require(not shot.get("face_closeup", False), "单元首镜禁止脸部特写")
        closeups += framing == "特写"
        require(shot["scene_id"] in covered_scenes, "镜头场景不在来源内")
        subjects = shot["subjects"]
        require(isinstance(subjects, list) and len(subjects) == len(set(subjects)), "主体列表格式错误或重复")
        valid_subjects = set(unit["character_ids"]) | set(unit["prop_ids"])
        require(set(subjects) <= valid_subjects, "镜头主体未列入本单元资产")
        require(set(subjects) <= set(scenes[shot["scene_id"]]["character_ids"] + scenes[shot["scene_id"]]["prop_ids"]), "镜头使用了其他场景的资产")
        text = shot["text"]
        require(isinstance(text, str) and text.strip() and "\n" not in text and "：“" not in text, "镜头正文须为非空单段，台词仅填入dialogue_parts由程序渲染")
        require(framing in text, "正文缺少已声明景别")
        _, referenced = asset_text(state, unit, text)
        require(set(subjects) <= referenced, "正文主体须使用@台账完整名称或@{资产ID}引用")
        require(referenced <= set(scenes[shot["scene_id"]]["character_ids"] + scenes[shot["scene_id"]]["prop_ids"] + scene_asset_ids(state, [shot["scene_id"]])),
                "镜头正文引用了其他场景的资产")
        if not subjects:
            evidence = shot.get("environment_evidence", "")
            require(evidence and evidence in excerpt(state, a, b), "无人物/核心道具镜头须有本范围原文明示环境证据")
        require(shot.get("angle") in ("正面", "斜侧", "侧拍", "背拍", "俯拍", "仰拍", "第一人称", "客观"), "机位分类不合法")
        if i:
            require(isinstance(shot.get("connector"), str) and shot["connector"].strip(), "缺少自然连接词")
            asset_text(state, unit, shot["connector"])
        used_chars.update(set(subjects) & set(chars))
        used_props.update(set(subjects) & set(props))
        cursor, previous = end, framing
    require(cursor == duration, "各镜头时长总和不等于单元时长")
    require(closeups <= 2, "单元特写超过2镜")
    events = unit["audio"]
    require(isinstance(events, list), "audio须为数组")
    expected = {r["id"]: r for r in selected if r["kind"] == "dialogue"}
    require(len(events) == len(expected) and {e["record"] for e in events} == set(expected), "声音事件遗漏、重复或不属于本单元台词")
    previous_start, voice_ids, used_record_ids = -1, set(), []
    for event in events:
        record = expected[event["record"]]
        used_record_ids.append(record["id"])
        require(event["text"] == portion(record, a, b), "台词与标准剧本所选部分不逐字一致")
        require(event["voice"] == record["voice"], "发声类型与标准剧本不一致")
        cid = event["character_id"]
        require(cid in unit["character_ids"] and cid in scenes[record["scene"]]["character_ids"], "声源未列入本单元/原场景人物")
        name = chars[cid]["name"]
        speaker = name.split("-群像-", 1)[0] + "-群像" if "-群像-" in name else name.split("-", 1)[0]
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
        rate = event["units"] * 1000 / (t1 - t0 - pause)
        low, high = SPEEDS[event["speed"]]
        if not low <= rate <= high:
            warnings.append({"record": record["id"], "kind": "估计净语速超档", "rate": round(rate, 3)})
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
    return warnings


def validate(state, request, partial=False):
    fixed_header()
    require(state["style_hash"], "尚未选择并保存视频风格")
    require(request.get("mode") in ("append", "replace"), "mode须为append或replace")
    require(isinstance(request.get("request_id"), str) and request["request_id"].strip(), "缺少幂等request_id")
    units = request["units"]
    require(isinstance(units, list) and units, "请求没有分镜单元")
    warnings = []
    if request["mode"] == "append":
        a, b = interval(request["span"], state["length"])
        review = range_review(state, [a, b])
        if review["issues"]:
            require(request.get("range_confirmation") == review["confirmation_token"], "范围重叠/间隔/回退尚未获得本次确认")
        cursor = a
        for unit in units:
            require(unit["span"][0] == cursor and unit["span"][1] <= b, "单元来源范围不连续、重复或越界")
            warnings.extend(validate_unit(state, unit, [a, b]))
            cursor = unit["span"][1]
        require(cursor == b or partial, "本批单元未完整覆盖选定范围")
        prospective = state["units"] + units
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
            warnings.extend(validate_unit(state, unit, boundary))
        replacements = {u["id"]: u for u in units}
        prospective = [dict(u, **replacements[u["id"]]) if u["id"] in replacements else u for u in state["units"]]
    total = sum(len(u["shots"]) for u in prospective)
    special = sum(s["framing"] == "特写" for u in prospective for s in u["shots"])
    require(special * 100 <= total * 15, "当前剧本有效镜头的累计特写比例超过15%")
    # Check only source-adjacent units. Intentional repeat batches are separate renditions.
    sequences = {}
    for unit in prospective:
        key = unit.get("batch_id", "__current_append__")
        sequences.setdefault(key, []).append(unit)
    for sequence in sequences.values():
        sequence.sort(key=lambda u: u["span"][0])
        for left, right in zip(sequence, sequence[1:]):
            if left["span"][1] == right["span"][0] and left["main_scene"] == right["main_scene"]:
                f1, f2 = left["shots"][-1]["framing"], right["shots"][0]["framing"]
                require(f1 != f2 and not (f1 in CLOSE and f2 in CLOSE), "相邻单元边界景别重复或连续近景/特写")
    by_id = {u["id"]: u for u in prospective if "id" in u}
    for unit in prospective:
        previous = by_id.get(unit.get("predecessor"))
        if previous and previous["main_scene"] == unit["main_scene"]:
            f1, f2 = previous["shots"][-1]["framing"], unit["shots"][0]["framing"]
            require(f1 != f2 and not (f1 in CLOSE and f2 in CLOSE), "修改破坏了跨批次相邻单元的景别衔接")
    if request["mode"] == "append" and state["units"]:
        previous = next((u for u in reversed(state["units"]) if u["span"][1] == units[0]["span"][0]), None)
        if previous and previous["main_scene"] == units[0]["main_scene"]:
            f1, f2 = previous["shots"][-1]["framing"], units[0]["shots"][0]["framing"]
            require(f1 != f2 and not (f1 in CLOSE and f2 in CLOSE), "续接批次边界景别不合规")
    return warnings


def render_unit(state, unit, reference_text=None):
    convert = reference_text or (lambda text: text)
    chars = {c["id"]: c["name"] for c in state["ledger"]["characters"]}
    scenes = {s["id"]: s["heading"] for s in state["ledger"]["scenes"]}
    catalog = reference_catalog(state)
    header = fixed_header()
    lines = [f'{unit["id"]}（时长：{unit["duration"]}秒）', convert("@黑幕"), unit["style"] + ";" + header[0], header[1],
             "人物：" + ("".join(chars[c] + convert("@" + chars[c] + " ") + "；" for c in unit["character_ids"]) or "无；"),
             "场景：" + "".join(scenes[s] + convert("@" + catalog[scene_asset_ids(state, [s])[0]] + " ") + "；" for s in unit["scene_ids"]) + f'时间：{unit["time"]}；天气：{unit["weather"]}；',
             convert(asset_text(state, unit, unit["global"])[0])]
    for i, shot in enumerate(unit["shots"]):
        if i:
            lines.append(convert(asset_text(state, unit, shot["connector"])[0]))
        parts = []
        for part in shot.get("dialogue_parts", []):
            event = next(e for e in unit["audio"] if e["record"] == part["record"])
            parts.append(convert("@" + chars[event["character_id"]] + " ") + event["voice"] + '：“' + part["text"] + '”')
        speech = (" " + " ".join(parts)) if parts else ""
        lines.append(f'[{shot["start"]}秒-{shot["end"]}秒] {convert(asset_text(state, unit, shot["text"])[0])}{speech}')
    return "\n".join(lines)


def render_receipt(state, receipt):
    atomic_text(receipt["output"], "\n\n".join(render_unit(state, u) for u in receipt["units"]) + "\n")


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
        from canvas import sync_replacements
        return sync_replacements(project, [u["id"] for u in receipt["units"]])
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        return {"status": "pending", "message": str(exc)}


def commit(args, state):
    request = read_json(args.request)
    request_hash = sha(packed(request))
    old_receipt = next((r for r in state["receipts"] if r["request_id"] == request.get("request_id")), None)
    if old_receipt:
        require(old_receipt["request_hash"] == request_hash, "request_id已用于不同请求；请使用新ID")
        render_receipt(state, old_receipt)
        return {"status": "already_committed", "output": old_receipt["output"], "ids": [u["id"] for u in old_receipt["units"]],
                "canvas_sync": sync_canvas_replacements(args.project, old_receipt), **receipt_summary(old_receipt)}
    warnings = validate(state, request)
    style = (args.project / "video-style.txt").read_text(encoding="utf-8-sig").strip()
    units = copy.deepcopy(request["units"])
    scenes = {s["id"]: s for s in state["ledger"]["scenes"]}
    for unit in units:
        unit["style"] = style
        if request["mode"] == "append":
            main = unit["main_scene"]
            number = state["next_numbers"].get(main, 1)
            prior = next((u for u in reversed(state["units"]) if u["span"][1] == unit["span"][0] and u["main_scene"] == main), None)
            unit.update(id=f'场景{scenes[main]["number"]}-分镜{number}', version=1,
                        batch_id=request["request_id"], previous_versions=[], predecessor=prior["id"] if prior else None)
            state["next_numbers"][main] = number + 1
            state["units"].append(unit)
        else:
            old = next(u for u in state["units"] if u["id"] == unit["id"])
            unit.update(version=old["version"] + 1, batch_id=old["batch_id"], predecessor=old.get("predecessor"),
                        previous_versions=old.get("previous_versions", []) + [{k: v for k, v in old.items() if k != "previous_versions"}])
            state["units"][state["units"].index(old)] = unit
    if request["mode"] == "append":
        state["ranges"].append({"span": request["span"], "batch_id": request["request_id"],
                                "confirmed": bool(request.get("range_confirmation"))})
    output = args.output.resolve()
    protected = [Path(x["path"]).resolve() for x in state["files"].values()] + [(args.project / n).resolve() for n in ("session.json", "video-style.txt")]
    require(output not in protected and ROOT not in output.parents, "交付路径不能覆盖来源、运行状态或Skill文件")
    require(not output.exists(), "交付文件已存在；请使用新文件名以保留旧版本")
    receipt = {"request_id": request["request_id"], "request_hash": request_hash, "mode": request["mode"], "output": str(output), "units": copy.deepcopy(units)}
    state["receipts"].append(receipt)
    state["phase"] = "nodes_choice"
    save(args.project, state)
    # State is authoritative. A failed filesystem delivery can be recovered with render.
    render_receipt(state, receipt)
    return {"status": "committed", "output": str(output), "ids": [u["id"] for u in units], "warnings": warnings,
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
            if command == "check":
                p.add_argument("--partial", action="store_true")
            else:
                p.add_argument("--output", type=Path, required=True)
        elif command == "render":
            p.add_argument("--request-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "bind":
            result = bind(args)
        else:
            args.project = args.project.resolve()
            state = load(args.project, verify_style=args.command != "style")
            if args.command == "style":
                require(args.choice != "3" or args.file is not None, "自定义风格须指定--file")
                result = set_style(args, state)
            elif args.command == "phase":
                state["phase"] = args.value
                save(args.project, state)
                result = {"phase": state["phase"]}
            elif args.command == "status":
                result = {k: state[k] for k in ("phase", "revision", "entry", "ranges", "next_numbers")}
                result["units"] = [{k: u[k] for k in ("id", "version", "span", "duration", "start_state", "end_state")} for u in state["units"]]
            elif args.command == "locate":
                result = locate(args, state)
            elif args.command == "range":
                result = {"span": [args.start, args.end], **range_review(state, [args.start, args.end])}
            elif args.command == "check":
                result = {"status": "passed", "partial": args.partial, "warnings": validate(state, read_json(args.request), args.partial)}
            elif args.command == "commit":
                result = commit(args, state)
            else:
                receipt = next((r for r in state["receipts"] if r["request_id"] == args.request_id), None)
                require(receipt is not None, "找不到该提交记录")
                render_receipt(state, receipt)
                result = {"status": "rendered", "output": receipt["output"], **receipt_summary(receipt)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError, StopIteration) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
