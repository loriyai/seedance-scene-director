#!/usr/bin/env python3
"""Carry source-bound scene observations into an asset ledger without inferring facts."""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path


POLICY = "observations-v1"
SCENE_FILE = re.compile(r"scene_(\d+)_(\d+)-(\d+)\.txt")
ID_PREFIX = {"characters": "CHAR", "locations": "LOC", "scene_assets": "SET",
             "props": "PROP", "scenes": "SC"}
FACT_FIELDS = {"visual_fact": "visual_facts", "personality_fact": "personality_facts"}
NOTE_KINDS = {"candidate_mention", "identity", "costume_state", "location_identity",
              "wear_event", "source_conflict", "sound_source", "scene_boundary",
              "asset_dependency", "mobile_space"}
RISK_PATTERNS = {
    "mobile_space": re.compile(r"老头乐|汽车|轿车|房车|巴士|公交车|出租车|车内|车厢|驾驶室|机舱|船舱"),
    "wearable_dependency": re.compile(r"头套|面罩|面具|口罩|头盔"),
}
PENDING_KIND = "逐场观察待核"
MIGRATION_PENDING_KIND = "逐场观察迁移待复核"


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_digest(paragraphs):
    return _sha(json.dumps(paragraphs, ensure_ascii=False, separators=(",", ":")))


def _content_digest(text):
    return _digest([line.strip() for line in text.splitlines() if line.strip()])


def _object(value):
    if isinstance(value, (str, Path)):
        value = json.loads(Path(value).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("观察文件或审阅报告须为 JSON 对象")
    return value


def _paragraphs(source_path):
    from asset_ledger import source_paragraphs
    return source_paragraphs(Path(source_path))


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 须为非空文本")
    return value


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def prepare_observations(source_path, workdir, observations, scene_numbers=None):
    """Attach immutable source/draft coordinates; never confirm an observation."""
    paragraphs = _paragraphs(source_path)
    if not isinstance(observations, list):
        raise ValueError("observations 须为列表")
    if scene_numbers is None:
        scene_numbers = {item.get("scene_number") for item in observations if isinstance(item, dict)}
    wanted = set(scene_numbers)
    manifests = []
    for path in sorted((Path(workdir) / "scenes").glob("*.txt")):
        match = SCENE_FILE.fullmatch(path.name)
        if not match or int(match.group(1)) not in wanted:
            continue
        number, start, end = map(int, match.groups())
        content = path.read_text(encoding="utf-8-sig")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        manifests.append({"file": path.name, "scene_number": number, "source_range": [start, end],
                          "heading": lines[0] if lines else "", "draft_sha256": _sha(content),
                          "content_sha256": _content_digest(content)})
    result = {"policy": POLICY, "source_digest": _source_digest(paragraphs),
              "scenes": manifests, "observations": copy.deepcopy(observations)}
    validate_observations(result, source_path, workdir)
    return result


def _validate_payload(payload, paragraphs):
    if payload.get("policy") != POLICY:
        raise ValueError("不支持的逐场观察 policy")
    if payload.get("source_digest") != _source_digest(paragraphs):
        raise ValueError("逐场观察的原文摘要已过期，须重新复核，不能重写旧摘要")
    manifests = payload.get("scenes")
    if not isinstance(manifests, list) or not manifests:
        raise ValueError("逐场观察缺少 scenes 清单")
    by_number, files = {}, set()
    for manifest in manifests:
        if not isinstance(manifest, dict):
            raise ValueError("观察场次清单须为对象")
        filename = manifest.get("file", "")
        match = SCENE_FILE.fullmatch(filename) if isinstance(filename, str) else None
        if not match:
            raise ValueError("观察场次文件名无效，不允许目录或路径跳转")
        number, start, end = map(int, match.groups())
        if (not _positive_int(manifest.get("scene_number")) or manifest["scene_number"] != number
                or manifest.get("source_range") != [start, end] or start < 1
                or start > end or end > len(paragraphs)):
            raise ValueError(f"观察场次与原文范围不一致：{filename}")
        if number in by_number or filename in files:
            raise ValueError(f"观察场次重复：{number}")
        for field in ("draft_sha256", "content_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(field, ""))):
                raise ValueError(f"观察场次缺少有效 {field}")
        _text(manifest.get("heading"), "场次 heading")
        by_number[number] = manifest
        files.add(filename)
    ordered = [by_number[number] for number in sorted(by_number)]
    if any(left["source_range"][1] >= right["source_range"][0] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("观察场次原文范围重叠或次序颠倒")
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError("逐场观察 observations 须为列表")
    ids = set()
    for item in observations:
        if not isinstance(item, dict):
            raise ValueError("逐场观察条目须为对象")
        oid = item.get("observation_id")
        if not isinstance(oid, str) or not re.fullmatch(r"OBS[0-9A-Za-z_-]+", oid) or oid in ids:
            raise ValueError("observation_id 须为不重复的 OBS 标识")
        ids.add(oid)
        number = item.get("scene_number")
        if not _positive_int(number) or number not in by_number:
            raise ValueError(f"{oid} 未绑定有效场次")
        if item.get("kind") not in set(FACT_FIELDS) | NOTE_KINDS:
            raise ValueError(f"{oid} 观察类型不支持")
        if item.get("status") not in ("confirmed", "pending"):
            raise ValueError(f"{oid} 缺少 confirmed/pending 状态")
        for field in ("tentative_subject", "evidence", "basis"):
            _text(item.get(field), f"{oid}.{field}")
        if not isinstance(item.get("requires_decision", False), bool):
            raise ValueError(f"{oid}.requires_decision 须为布尔值")
        if item["kind"] == "source_conflict" and (
                item["status"] != "pending" or item.get("requires_decision") is not True):
            raise ValueError(f"{oid} 原文冲突须保持待核；处理后须明确改为已解决的观察类型及依据")
        refs = item.get("source_paragraphs")
        start, end = by_number[number]["source_range"]
        if (not isinstance(refs, list) or not refs or any(not _positive_int(ref) for ref in refs)
                or refs != sorted(set(refs)) or refs != list(range(refs[0], refs[-1] + 1))
                or refs[0] < start or refs[-1] > end):
            raise ValueError(f"{oid} 原文段落须连续且属于所指场次")
        cited = "\n".join(paragraphs[ref - 1] for ref in refs)
        if item["evidence"] not in cited:
            raise ValueError(f"{oid} 证据不是所引原文的逐字片段")
        target = item.get("target")
        if target is not None:
            if not isinstance(target, dict) or target.get("category") not in ID_PREFIX:
                raise ValueError(f"{oid} target 类别无效")
            if set(target) - {"category", "asset_id", "source_entry", "scene_number"}:
                raise ValueError(f"{oid} target 含未支持字段")
            if target.get("scene_number", number) != number:
                raise ValueError(f"{oid} target 与观察场次不一致")
            if target.get("asset_id") is not None:
                if not re.fullmatch(ID_PREFIX[target["category"]] + r"\d{4,}", str(target["asset_id"])):
                    raise ValueError(f"{oid} target 稳定 ID 与类别不一致")
                if "source_entry" in target:
                    raise ValueError(f"{oid} target 不能同时指定 ID 和 source_entry")
            elif target["category"] == "characters":
                _text(target.get("source_entry"), f"{oid}.target.source_entry")
            elif target["category"] not in ("locations", "scenes"):
                raise ValueError(f"{oid} 此目标类别须明确 asset_id")
        fact = item.get("fact")
        if item["kind"] in FACT_FIELDS:
            if not isinstance(fact, dict):
                raise ValueError(f"{oid} 事实观察须有 fact 对象")
            if set(fact) - {"field", "text", "attribution", "key"}:
                raise ValueError(f"{oid} fact 含未支持字段；证据和场次必须由观察记录提供")
            for field in ("text", "attribution"):
                _text(fact.get(field), f"{oid}.fact.{field}")
            if fact.get("field") != FACT_FIELDS[item["kind"]]:
                raise ValueError(f"{oid} fact.field 与观察类型不一致")
            if "key" in fact:
                _text(fact["key"], f"{oid}.fact.key")
            if target is None and item["status"] == "confirmed":
                raise ValueError(f"{oid} 已确认事实必须有明确目标")
            if target and item["kind"] == "personality_fact" and target["category"] != "characters":
                raise ValueError(f"{oid} personality_facts 只能属于人物")
        elif fact is not None:
            raise ValueError(f"{oid} 候选/决策观察不能夹带可导入 fact")
    return by_number


def _review_scenes(review, payload, paragraphs):
    if review is None:
        return set()
    from screenplay_review import REVIEW_VERSION
    review = _object(review)
    scope = review.get("scope", "full")
    expected = "passed" if scope == "full" else scope + "_passed"
    # Historical immutable import snapshots remain readable after an algorithm
    # upgrade. A new import still requires the current full report version.
    versions = {7, 8, REVIEW_VERSION} if review.get("snapshot_scope") == "observed_scenes_only" else {REVIEW_VERSION}
    if (scope not in ("full", "sample", "partial") or review.get("status") != expected
            or review.get("review_version") not in versions
            or any(item.get("issue") for item in review.get("boundary_items", []))):
        raise ValueError("观察所用审阅报告未通过或版本过期")
    if review.get("fingerprint", {}).get("source_digest") != payload["source_digest"]:
        raise ValueError("观察审阅报告原文摘要不一致")
    entries = {item.get("file"): item for item in review.get("scenes", [])}
    covered = set()
    for manifest in payload["scenes"]:
        filename = manifest["file"]
        if filename not in entries:
            continue
        entry = entries[filename]
        start, end = manifest["source_range"]
        if (review.get("fingerprint", {}).get("drafts", {}).get(filename) != manifest["draft_sha256"]
                or entry.get("draft_hash") != manifest["draft_sha256"]
                or entry.get("source_range") != [start, end]
                or entry.get("source_hash") != _sha(json.dumps(paragraphs[start - 1:end], ensure_ascii=False))
                or entry.get("coverage_missing") or entry.get("coverage_extra")
                or any(item.get("issue") for item in entry.get("speaker_items", []))):
            raise ValueError(f"观察审阅报告的场次已过期或未通过：{filename}")
        covered.add(manifest["scene_number"])
    return covered


def _review_snapshot(review, payload):
    """Retain check receipts, never duplicate paragraph maps or dialogue bodies."""
    if review is None:
        return None
    review = _object(review)
    wanted = {item["file"] for item in payload["scenes"]}
    selected = [item for item in review["scenes"] if item["file"] in wanted]
    result = {"review_version": review["review_version"], "scope": review.get("scope", "full"),
            "status": review["status"], "original_review_digest": _digest(review),
            "snapshot_scope": "observed_scenes_only", "decision_digest": review.get("decision_digest"),
            "fingerprint": {"source_digest": review["fingerprint"]["source_digest"],
                            "drafts": {item["file"]: review["fingerprint"]["drafts"][item["file"]] for item in selected}},
            "scenes": [{key: copy.deepcopy(item[key]) for key in
                        ("file", "source_range", "source_hash", "draft_hash", "coverage_missing", "coverage_extra")}
                       | {"speaker_items": []} for item in selected]}
    if "boundary_policy" in review:
        result.update(boundary_policy=review["boundary_policy"],
                      boundary_items=[{key: copy.deepcopy(item[key]) for key in
                                       ("kind", "scene_numbers", "decision_key", "issue", "status")}
                                      for item in review.get("boundary_items", [])],
                      boundary_drafts=copy.deepcopy(review.get("boundary_drafts", {})))
    return result


def risk_diagnostics(paragraphs, manifests, observations):
    """Source-bound early prompts, never facts, asset selection, or approvals."""
    result = []
    for number, manifest in manifests.items():
        start, end = manifest["source_range"]
        for kind, pattern in RISK_PATTERNS.items():
            refs = [ref for ref in range(start, end + 1) if pattern.search(paragraphs[ref - 1])]
            if not refs:
                continue
            matching_kinds = ({"mobile_space", "scene_boundary", "candidate_mention", "location_identity"}
                              if kind == "mobile_space" else {"wear_event", "asset_dependency", "candidate_mention"})
            recorded = [item["observation_id"] for item in observations
                        if item["scene_number"] == number and item["kind"] in matching_kinds
                        and set(item["source_paragraphs"]) & set(refs)]
            result.append({"kind": kind, "scene_number": number, "source_paragraphs": refs,
                           "examples": [{"paragraph": ref, "text": paragraphs[ref - 1]} for ref in refs[:2]],
                           "observation_ids": recorded, "blocking": False,
                           "action": ("先判叙事场界，再判移动道具及内部参考需求；车内外机位变化不自动新建SC/LOC/SET。"
                                      if kind == "mobile_space" else
                                      "核对可拆卸物、摘戴事件及人物依赖；需要独立参考时先确定PROP物理设计，再预览佩戴CHAR。")})
    return result


def _scene(data, number):
    matches = [scene for scene in data.get("scenes", []) if scene.get("number") == number]
    if len(matches) != 1:
        raise ValueError(f"观察无法唯一定位台账场景 {number}")
    return matches[0]


def _linked(scene, category, asset_id):
    if category == "scenes":
        return scene.get("id") == asset_id
    if category in ("characters", "props"):
        return asset_id in scene.get("character_ids" if category == "characters" else "prop_ids", [])
    return scene.get("location_id" if category == "locations" else "scene_asset_id") == asset_id


def _target(data, item):
    target = item["target"]
    category = target["category"]
    scene = _scene(data, item["scene_number"])
    candidates = [asset for asset in data.get(category, []) if _linked(scene, category, asset.get("id"))]
    if "asset_id" in target:
        candidates = [asset for asset in candidates if asset.get("id") == target["asset_id"]]
    elif category == "characters":
        candidates = [asset for asset in candidates if asset.get("source_entry") == target["source_entry"]]
    if len(candidates) != 1:
        raise ValueError(f"{item['observation_id']} 目标不存在或不唯一；须明确场内原人物条目/稳定 ID")
    return category, candidates[0], scene


def _import_target(data, item):
    saved = data.get("observations", {})
    old = next((part for part in saved.get("source_payload", {}).get("observations", [])
                if part.get("observation_id") == item["observation_id"]), None)
    imported = next((part for part in saved.get("imports", [])
                     if part.get("observation_id") == item["observation_id"]), None)
    if old == item and imported:
        adapted = copy.deepcopy(item)
        adapted["target"] = {"category": imported["category"], "asset_id": imported["target_id"]}
        return _target(data, adapted)
    return _target(data, item)


def _fact(item, scene_id):
    return {"text": item["fact"]["text"], "attribution": item["fact"]["attribution"],
            "evidence": item["evidence"], "source_paragraphs": item["source_paragraphs"],
            "scene_ids": [scene_id]}


def validate_observations(payload, source_path, workdir, review=None, ledger=None):
    """Check references and optional review freshness, not semantic truth."""
    from screenplay_io import validate_scene
    payload = _object(payload)
    paragraphs = _paragraphs(source_path)
    manifests = _validate_payload(payload, paragraphs)
    cast_by_number = {}
    for number, manifest in manifests.items():
        path = Path(workdir) / "scenes" / manifest["file"]
        if not path.is_file():
            raise ValueError(f"观察对应逐场稿不存在：{path.name}")
        content = path.read_text(encoding="utf-8-sig")
        if _sha(content) != manifest["draft_sha256"] or _content_digest(content) != manifest["content_sha256"]:
            raise ValueError(f"观察逐场稿已变化：{path.name}；须复核原观察")
        errors = validate_scene(content, number, path)
        if errors:
            raise ValueError("；".join(errors))
        if next(line.strip() for line in content.splitlines() if line.strip()) != manifest["heading"]:
            raise ValueError("观察场次标题与逐场稿不一致")
        cast = [line.strip() for line in content.splitlines() if line.strip()][1]
        cast_by_number[number] = [] if cast == "人物：无；" else cast[3:-1].split("；")
    if review is not None:
        if _object(review).get("snapshot_scope") is not None:
            raise ValueError("历史观察快照仅供已导入内容核验；新导入须提供当前完整审阅报告")
        for filename, expected_hash in _object(review).get("boundary_drafts", {}).items():
            if not isinstance(filename, str) or not SCENE_FILE.fullmatch(filename):
                raise ValueError("场界审阅的相邻场稿文件名无效")
            path = Path(workdir) / "scenes" / filename
            if not path.is_file() or _sha(path.read_text(encoding="utf-8-sig")) != expected_hash:
                raise ValueError("场界审阅的相邻场稿已过期，须重新复核")
    reviewed = _review_scenes(review, payload, paragraphs)
    facts = [item for item in payload["observations"] if item["kind"] in FACT_FIELDS and item["status"] == "confirmed"]
    if ledger is not None:
        ledger = _object(ledger)
        if ledger.get("source_digest") != payload["source_digest"]:
            raise ValueError("观察与台账原文摘要不一致")
        for number, manifest in manifests.items():
            scene = _scene(ledger, number)
            if scene.get("source_range") != manifest["source_range"] or scene.get("heading") != manifest["heading"]:
                raise ValueError(f"观察与台账的场次去向不一致：{number}")
        for item in facts:
            category, target, _ = _import_target(ledger, item)
            if category == "characters" and target.get("source_entry") not in cast_by_number[item["scene_number"]]:
                raise ValueError(f"{item['observation_id']} 人物目标不在当前逐场稿人物行中")
    return {"status": "checked", "policy": POLICY, "payload_digest": _digest(payload),
            "observation_count": len(payload["observations"]), "confirmed_fact_count": len(facts),
            "reviewed_scene_numbers": sorted(reviewed), "semantic_review": "not_performed",
            "risk_scan": "heuristic_not_complete",
            "risk_diagnostics": risk_diagnostics(paragraphs, manifests, payload["observations"]),
            "ready_to_import_facts": all(item["scene_number"] in reviewed for item in facts)}


def import_observations(data, payload, source_path, workdir, review=None):
    """Return (new ledger, report). No file write, identity inference, or count inference."""
    payload = copy.deepcopy(_object(payload))
    report = validate_observations(payload, source_path, workdir, review, data)
    if not report["ready_to_import_facts"]:
        raise ValueError("已确认事实须有覆盖对应场次的当前 passed/sample_passed/partial_passed 审阅报告")
    result = copy.deepcopy(data)
    previous = result.get("observations")
    imports, redirects = [], {}
    by_id = {item["observation_id"]: item for item in payload["observations"]}
    if previous is not None:
        validate_imported_observations(data, _paragraphs(source_path))
        old_items = {item["observation_id"]: item for item in previous["source_payload"]["observations"]}
        if not set(old_items) <= set(by_id):
            raise ValueError("重新导入不能静默丢弃旧观察；须保留记录或人工归档后重审")
        for imported in previous["imports"]:
            oid = imported["observation_id"]
            if old_items[oid] != by_id[oid]:
                raise ValueError(f"已导入观察 {oid} 发生变化；须先归档并人工处理旧事实")
        imports = copy.deepcopy(previous["imports"])
        redirects = copy.deepcopy(previous.get("target_redirects", {}))
    already = {item["observation_id"] for item in imports}
    conflicts = {}
    for item in payload["observations"]:
        if item["status"] != "confirmed" or item["kind"] not in FACT_FIELDS:
            continue
        category, target, scene = _import_target(result, item)
        if item["fact"].get("key"):
            key = (scene["id"], category, target["id"], item["fact"]["field"], item["fact"]["key"])
            if key in conflicts and conflicts[key] != item["fact"]["text"]:
                raise ValueError(f"{item['observation_id']} 同场同目标的事实 key 冲突，须先人工统筹")
            conflicts[key] = item["fact"]["text"]
        if item["observation_id"] in already:
            continue
        fact = _fact(item, scene["id"])
        field = item["fact"]["field"]
        if fact not in target.setdefault(field, []):
            target[field].append(copy.deepcopy(fact))
        imports.append({"observation_id": item["observation_id"], "category": category,
                        "initial_target_id": target["id"], "target_id": target["id"],
                        "scene_id": scene["id"], "field": field, "fact": copy.deepcopy(fact)})
    queue = [{"observation_id": item["observation_id"], "scene_number": item["scene_number"],
              "kind": item["kind"], "status": item["status"],
              "requires_decision": item.get("requires_decision", False)}
             for item in payload["observations"] if item["observation_id"] not in {entry["observation_id"] for entry in imports}]
    old_digest = previous.get("payload_digest") if previous else None
    pending = result.setdefault("pending_reviews", [])
    # Only this exact prior bundle's own queue entries are replaced. Other gates survive.
    pending[:] = [entry for entry in pending if not (
        entry.get("kind") == PENDING_KIND and entry.get("observations_digest") == old_digest
        and entry.get("observation_id") in by_id and old_digest is not None)]
    for item in payload["observations"]:
        if item["status"] == "pending" and item.get("requires_decision", False):
            pending.append({"kind": PENDING_KIND, "observation_id": item["observation_id"],
                            "observations_digest": report["payload_digest"], "scene_number": item["scene_number"],
                            "detail": item["basis"],
                            "action": "逐项复核该观察，保留同一 observation_id 更新状态/依据并重新导入完整观察文件；原文或场稿变化须归档旧交接并重新审阅"})
    result["observations"] = {"policy": POLICY, "source_digest": payload["source_digest"],
                              "source_payload": payload, "payload_digest": report["payload_digest"],
                              "imports": imports, "review_queue": queue, "target_redirects": redirects,
                              "review_snapshot": _review_snapshot(review, payload)}
    _reconcile_histories(result, payload, report)
    validate_imported_observations(result, _paragraphs(source_path))
    report.update(imported_fact_count=len(imports), review_queue_count=len(queue))
    return result, report


def observation_history_digest(history):
    """Hash immutable archived content, excluding its later explicit resolution."""
    return _digest({key: value for key, value in history.items() if key not in ("history_digest", "resolution")})


def _reconcile_histories(data, payload, report):
    requested = payload.get("reconciliations", [])
    if not isinstance(requested, list):
        raise ValueError("观察 reconciliations 须为列表")
    if not requested:
        return
    histories = data.get("observations_history", [])
    if not isinstance(histories, list):
        raise ValueError("observations_history 须为列表")
    seen = set()
    new_items = {item["observation_id"]: item for item in payload["observations"]}
    new_imports = {item["observation_id"]: item for item in data["observations"]["imports"]}
    for request in requested:
        if not isinstance(request, dict):
            raise ValueError("观察历史重审记录须为对象")
        hdigest = request.get("history_digest")
        if not isinstance(hdigest, str) or hdigest in seen:
            raise ValueError("观察历史重审摘要重复或无效")
        seen.add(hdigest)
        matches = [history for history in histories if history.get("history_digest") == hdigest]
        if len(matches) != 1 or observation_history_digest(matches[0]) != hdigest:
            raise ValueError("观察历史重审摘要找不到唯一且完整的归档")
        history = matches[0]
        _text(request.get("basis"), "观察历史重审依据")
        old_items = {item["observation_id"]: item for item in history["observations"]["source_payload"]["observations"]}
        resolutions = request.get("resolutions")
        if (not isinstance(resolutions, list) or any(not isinstance(item, dict) for item in resolutions)
                or len({item.get("observation_id") for item in resolutions}) != len(resolutions)
                or {item.get("observation_id") for item in resolutions} != set(old_items)):
            raise ValueError("观察历史重审必须逐项处理全部旧 observation_id，不能遗漏")
        for resolution in resolutions:
            oid = resolution["observation_id"]
            _text(resolution.get("basis"), f"{oid} 历史处理依据")
            outcome = resolution.get("outcome")
            replacement = None
            if outcome in ("reconfirmed", "retained_pending"):
                replacement_id = resolution.get("new_observation_id")
                replacement = new_items.get(replacement_id)
                expected_status = "confirmed" if outcome == "reconfirmed" else "pending"
                if replacement is None or replacement["status"] != expected_status:
                    raise ValueError(f"{oid} 历史处理缺少对应状态的当前新观察")
                if outcome == "reconfirmed" and replacement["scene_number"] not in report["reviewed_scene_numbers"]:
                    raise ValueError(f"{oid} 历史重新确认须有当前场次审阅报告")
                if old_items[oid]["kind"] in FACT_FIELDS and outcome == "reconfirmed" and replacement_id not in new_imports:
                    raise ValueError(f"{oid} 旧事实重新确认必须对应本次实际导入的新事实")
            elif outcome != "withdrawn":
                raise ValueError("观察历史处理须为 reconfirmed/retained_pending/withdrawn")
            replacement_import = new_imports.get(resolution.get("new_observation_id"))
            for old_fact in history.get("migrated_imported_facts", []):
                if old_fact["observation_id"] != oid:
                    continue
                targets = [item for item in data.get(old_fact["category"], []) if item.get("id") == old_fact["target_id"]]
                current = targets[0].get(old_fact["field"], []) if len(targets) == 1 else []
                for fact in old_fact["facts"]:
                    if fact not in current:
                        continue
                    if not (outcome == "reconfirmed" and replacement_import
                            and replacement_import["category"] == old_fact["category"]
                            and replacement_import["target_id"] == old_fact["target_id"]
                            and replacement_import["field"] == old_fact["field"]
                            and replacement_import["fact"] == fact):
                        raise ValueError(f"{oid} 旧导入事实仍在台账中；须人工移除或用当前已审阅事实逐项确认")
        pending = data.setdefault("pending_reviews", [])
        matching = [item for item in pending if item.get("kind") == MIGRATION_PENDING_KIND and item.get("history_digest") == hdigest]
        resolution_receipt = {"payload_digest": report["payload_digest"], "source_digest": payload["source_digest"],
                              "basis": request["basis"], "resolutions": copy.deepcopy(resolutions)}
        if not matching and history.get("resolution") != resolution_receipt:
            raise ValueError("该观察历史没有对应待核项；不能覆盖已有重审回执")
        if len(matching) > 1:
            raise ValueError("观察历史存在重复待核项")
        if matching:
            history["resolution"] = resolution_receipt
            pending.remove(matching[0])


def _redirected(asset_id, redirects):
    seen = set()
    while asset_id in redirects:
        if asset_id in seen:
            raise ValueError("观察目标重定向存在循环")
        seen.add(asset_id)
        asset_id = redirects[asset_id]
    return asset_id


def validate_imported_observations(data, paragraphs, script=None):
    """Recheck provenance and imported facts; legacy ledgers remain unaffected."""
    if "observations" not in data:
        return {"status": "not_registered"}
    saved = data["observations"]
    if not isinstance(saved, dict) or saved.get("policy") != POLICY:
        raise ValueError("台账逐场观察交接结构无效")
    payload = saved.get("source_payload")
    if not isinstance(payload, dict) or _digest(payload) != saved.get("payload_digest"):
        raise ValueError("台账原观察摘要不一致，不能静默改写原交接")
    manifests = _validate_payload(payload, paragraphs)
    if data.get("source_digest") != payload["source_digest"] or saved.get("source_digest") != payload["source_digest"]:
        raise ValueError("台账与逐场观察原文摘要不一致")
    bases = {item["number"]: item for item in script} if script is not None else None
    for number, manifest in manifests.items():
        scene = _scene(data, number)
        if scene.get("source_range") != manifest["source_range"] or scene.get("heading") != manifest["heading"]:
            raise ValueError(f"观察场次映射已变化：{number}；须人工重新审阅")
        if bases is not None:
            base = bases.get(number)
            if base is None:
                raise ValueError(f"观察场次不在当前剧本中：{number}")
            cast = "人物：" + ("；".join(base["cast_entries"]) if base["cast_entries"] else "无") + "；"
            content = base["heading"] + "\n" + cast + "\n" + base["body_text"]
            if _content_digest(content) != manifest["content_sha256"]:
                raise ValueError(f"观察场次正文/人物行已变化：{number}；须人工重新审阅")
    reviewed = _review_scenes(saved.get("review_snapshot"), payload, paragraphs)
    by_id = {item["observation_id"]: item for item in payload["observations"]}
    confirmed = {oid for oid, item in by_id.items() if item["status"] == "confirmed" and item["kind"] in FACT_FIELDS}
    imports = saved.get("imports")
    if (not isinstance(imports, list) or any(not isinstance(entry, dict) for entry in imports)
            or len({entry.get("observation_id") for entry in imports}) != len(imports)):
        raise ValueError("观察导入回执缺失或重复")
    if {entry.get("observation_id") for entry in imports} != confirmed:
        raise ValueError("观察已确认事实与实际导入回执不一致；未确认观察不得升格")
    redirects = saved.get("target_redirects", {})
    if not isinstance(redirects, dict):
        raise ValueError("观察目标重定向须为对象")
    for old, new in redirects.items():
        if (not isinstance(old, str) or not isinstance(new, str)
                or old not in data.get("retired_ids", [])
                or not any(re.fullmatch(prefix + r"\d{4,}", old) and re.fullmatch(prefix + r"\d{4,}", new)
                           for prefix in ID_PREFIX.values())):
            raise ValueError("观察目标重定向缺少退役记录或跨越资产类别")
        _redirected(old, redirects)
    fact_keys = {}
    for entry in imports:
        item = by_id[entry["observation_id"]]
        if item["scene_number"] not in reviewed:
            raise ValueError("导入事实缺少覆盖对应场次的有效审阅")
        category = item["target"]["category"]
        initial, current = entry.get("initial_target_id"), entry.get("target_id")
        if not isinstance(initial, str) or not re.fullmatch(ID_PREFIX[category] + r"\d{4,}", initial):
            raise ValueError("观察导入初始目标无效")
        if _redirected(initial, redirects) != current or entry.get("category") != category:
            raise ValueError("观察稳定目标映射不一致")
        if "asset_id" in item["target"] and item["target"]["asset_id"] != initial:
            raise ValueError("观察原始明确目标被改写")
        adapted = copy.deepcopy(item)
        adapted["target"] = {"category": category, "asset_id": current}
        _, target, scene = _target(data, adapted)
        if "source_entry" in item["target"] and target.get("source_entry") != item["target"]["source_entry"]:
            raise ValueError("观察原人物条目与当前目标不一致，不能猜测合并匿名编号")
        fact = _fact(item, scene["id"])
        field = item["fact"]["field"]
        if (entry.get("scene_id") != scene["id"] or entry.get("field") != field
                or entry.get("fact") != fact or fact not in target.get(field, [])):
            raise ValueError(f"观察 {item['observation_id']} 的导入事实或场次归属已变化/遗漏")
        if item["fact"].get("key"):
            key = (scene["id"], category, current, field, item["fact"]["key"])
            if key in fact_keys and fact_keys[key] != fact["text"]:
                raise ValueError("导入观察存在同场同目标事实 key 冲突")
            fact_keys[key] = fact["text"]
    expected_queue = [{"observation_id": item["observation_id"], "scene_number": item["scene_number"],
                       "kind": item["kind"], "status": item["status"],
                       "requires_decision": item.get("requires_decision", False)}
                      for item in payload["observations"] if item["observation_id"] not in confirmed]
    if saved.get("review_queue") != expected_queue:
        raise ValueError("观察候选/待核索引不完整")
    expected_pending = {oid for oid, item in by_id.items() if item["status"] == "pending" and item.get("requires_decision", False)}
    actual_pending = [entry.get("observation_id") for entry in data.get("pending_reviews", [])
                      if entry.get("kind") == PENDING_KIND and entry.get("observations_digest") == saved["payload_digest"]]
    if set(actual_pending) != expected_pending or len(actual_pending) != len(expected_pending):
        raise ValueError("观察真正待核决定与台账 pending_reviews 不一致")
    return {"status": "passed" if script is not None else "source_and_facts_checked",
            "imported_fact_count": len(imports), "scene_content_checked": script is not None,
            "semantic_review": "not_performed"}


def remap_observation_targets(data, redirects):
    """Return a new copy after an explicit asset merge; preserve original bundle hashes."""
    result = copy.deepcopy(data)
    if "observations" not in result or not redirects:
        return result
    saved = result["observations"]
    history = saved.setdefault("target_redirects", {})
    for old, new in redirects.items():
        if old in history and history[old] != new:
            raise ValueError("观察已有目标重定向不可改写")
        history[old] = new
    for old in history:
        _redirected(old, history)
    for entry in saved["imports"]:
        entry["target_id"] = _redirected(entry["initial_target_id"], history)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "check", "import"):
        command = sub.add_parser(name)
        command.add_argument("input", type=Path, help="prepare: observation list; otherwise bundle")
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--workdir", type=Path, required=True)
        if name != "prepare":
            command.add_argument("--review", type=Path)
            command.add_argument("--ledger", type=Path, required=name == "import")
        if name != "check":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = json.loads(args.input.read_text(encoding="utf-8-sig"))
        if args.command == "prepare":
            result = prepare_observations(args.source, args.workdir, value)
            report = {"status": "prepared", "observation_count": len(result["observations"])}
        elif args.command == "check":
            report = validate_observations(value, args.source, args.workdir, args.review, args.ledger)
        else:
            result, report = import_observations(_object(args.ledger), value, args.source, args.workdir, args.review)
        if args.command != "check":
            protected = [args.input, args.source]
            if args.command == "import":
                protected.append(args.ledger)
            if args.output.resolve() in {path.resolve() for path in protected}:
                raise ValueError("观察工具须写入新输出文件，不能覆盖原输入/台账")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"逐场观察检查失败：{exc}\n")


if __name__ == "__main__":
    main()
