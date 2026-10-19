"""Apply explicit asset review decisions and check their actual persisted effects.

This module never infers identity or performs arbitrary JSON patches. CLI callers
must run the full script/source/ledger validator before publishing a new draft.
"""

import copy
import hashlib
import json
import re

from cast_contract import validate_source_speaker


POLICY = "asset-decisions-v1"
PREFIXES = {"scenes": "SC", "characters": "CHAR", "locations": "LOC",
            "scene_assets": "SET", "props": "PROP"}
CHAR_FIELDS = {
    "speaking_ensemble": {"character_kind", "visual_mode", "source_speaker", "ensemble_review", "visible_uses"},
    "background_group": {"visual_mode", "extraction_review", "visible_uses"},
}
CANDIDATE_FIELDS = {"disposition", "target_id", "requires_reference", "reason"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _nonempty(value, label):
    _require(isinstance(value, str) and bool(value.strip()), f"{label}须为非空文字")
    return value


def _id(category, value):
    return isinstance(value, str) and bool(re.fullmatch(PREFIXES[category] + r"\d{4,}", value))


def _index(records, label):
    _require(isinstance(records, list), f"{label}须为数组")
    result = {}
    for record in records:
        _require(isinstance(record, dict), f"{label}记录须为对象")
        aid = record.get("id")
        _require(isinstance(aid, str) and aid not in result, f"{label}包含重复或无效ID：{aid}")
        result[aid] = record
    return result


def _reference_ids(value, field=""):
    """Read ID-bearing fields, not prose, archive history or retired_ids itself."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _reference_ids(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _reference_ids(child, field)
    elif isinstance(value, str) and (field == "id" or field.endswith("_id") or field.endswith("_ids")):
        yield value


def validate_asset_lifecycle(data, *, allow_pending=False):
    """One lifecycle contract for preflight, design, render and bind.

    Historical retired IDs need not invent reasons. Every new explicit archive
    entry must retain the original record and a reviewed retirement reason.
    allow_pending is private preparation support; it never authorizes delivery.
    """
    active = set()
    for category in PREFIXES:
        for aid in _index(data.get(category, []), category):
            _require(_id(category, aid) and aid not in active, f"现用资产ID无效或重复：{aid}")
            active.add(aid)
    retired = data.get("retired_ids", [])
    _require(isinstance(retired, list) and all(isinstance(aid, str) for aid in retired), "retired_ids须为ID数组")
    _require(len(retired) == len(set(retired)) and not active.intersection(retired), "retired_ids重复或与现用ID冲突")
    _require(all(any(_id(category, aid) for category in PREFIXES) for aid in retired), "retired_ids含无效资产ID")
    references = set()
    for category in PREFIXES:
        references.update(_reference_ids(data.get(category, [])))
    references.update(_reference_ids(data.get("prop_coverage_review", {})))
    _require(not references.intersection(retired), f"退役ID仍被活动记录引用：{sorted(references.intersection(retired))}")
    pending = data.get("pending_reviews", [])
    _require(isinstance(pending, list), "pending_reviews必须是数组")
    inactive = data.get("inactive_assets", {"characters": []})
    _require(isinstance(inactive, dict) and set(inactive).issubset(PREFIXES), "inactive_assets分类无效")
    inactive_ids = set()
    for category, records in inactive.items():
        for aid in _index(records, f"inactive_assets.{category}"):
            _require(_id(category, aid) and aid not in active and (allow_pending or aid not in retired) and aid not in inactive_ids,
                     f"待核资产ID冲突或无效：{aid}")
            inactive_ids.add(aid)
    archive = data.get("asset_archive", [])
    _require(isinstance(archive, list), "asset_archive须为独立归档数组")
    archived = set()
    for entry in archive:
        _require(isinstance(entry, dict) and set(entry) in ({"asset_id", "category", "reason", "record"},
                 {"asset_id", "category", "reason", "record", "resolved_pending_reviews"}), "归档须含asset_id/category/reason/record，仅可附明确已解决的退场待核记录")
        aid, category = entry["asset_id"], entry["category"]
        _require(category in PREFIXES and _id(category, aid) and aid in retired and aid not in archived, "归档ID须唯一且已经退役")
        _require(isinstance(entry["record"], dict) and entry["record"].get("id") == aid, "归档须保留同ID的完整原记录")
        _nonempty(entry["reason"], f"{aid}退役原因")
        resolved = entry.get("resolved_pending_reviews", [])
        _require(isinstance(resolved, list), "已解决的退场待核记录须为数组")
        for previous in resolved:
            _require(isinstance(previous, dict) and previous.get("kind") == "人物可能改名或退场"
                     and previous.get("asset_id") == aid and category == "characters", "归档只能解决同人物ID的明确退场待核项")
        archived.add(aid)
    if not allow_pending:
        _require(not pending, "台账仍有pending_reviews待核项；不能交付或绑定")
        _require(not inactive_ids, f"台账仍有inactive_assets待核项：{sorted(inactive_ids)}；明确排除项须独立归档")


def _payload(payload):
    required = {"review_policy", "source_digest", "input_ledger_digest", "scope", "decisions"}
    _require(isinstance(payload, dict) and set(payload) == required, "审阅决定须包含review_policy/source_digest/input_ledger_digest/scope/decisions，拒绝任意patch字段")
    _require(payload["review_policy"] == POLICY, "不支持的审阅决定版本")
    for name in ("source_digest", "input_ledger_digest"):
        _require(isinstance(payload[name], str) and bool(re.fullmatch(r"[a-f0-9]{64}", payload[name])), f"{name}须为SHA256摘要")
    scope = payload["scope"]
    _require(isinstance(scope, dict) and set(scope) == {"character_ids", "candidate_ids", "archive_ids"}, "scope须明确character_ids/candidate_ids/archive_ids")
    for name, ids in scope.items():
        _require(isinstance(ids, list) and all(isinstance(aid, str) and aid for aid in ids) and len(ids) == len(set(ids)), f"{name}包含重复或无效ID")
    decisions = payload["decisions"]
    _require(isinstance(decisions, list) and bool(decisions), "decisions须为非空数组")
    actual = {name: set() for name in scope}
    for decision in decisions:
        _require(isinstance(decision, dict), "审阅决定须为对象")
        kind = decision.get("kind")
        if kind in CHAR_FIELDS:
            bucket, target, fields = "character_ids", "asset_id", {"kind", "asset_id", "expected_record_digest", "values"}
            _require(isinstance(decision.get("values"), dict) and set(decision["values"]) == CHAR_FIELDS[kind], f"{kind}只允许完整的规定字段")
        elif kind == "candidate_disposition":
            bucket, target, fields = "candidate_ids", "candidate_id", {"kind", "candidate_id", "expected_record_digest", "values"}
            _require(isinstance(decision.get("values"), dict) and set(decision["values"]) == CANDIDATE_FIELDS, "候选归类只允许disposition/target_id/requires_reference/reason")
        elif kind == "archive_inactive":
            bucket, target, fields = "archive_ids", "asset_id", {"kind", "asset_id", "category", "expected_record_digest", "reason"}
            _require(decision.get("category") in PREFIXES, "归档分类无效")
            _nonempty(decision.get("reason"), "明确排除原因")
            if "resolved_pending_digests" in decision:
                fields.add("resolved_pending_digests")
                hashes = decision["resolved_pending_digests"]
                _require(isinstance(hashes, list) and all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes)
                         and len(hashes) == len(set(hashes)), "resolved_pending_digests须为无重复的记录摘要数组")
        else:
            raise ValueError(f"不支持的审阅决定：{kind}")
        _require(set(decision) == fields, f"{kind}含遗漏或未授权字段，拒绝任意patch")
        aid = _nonempty(decision.get(target), target)
        _require(aid not in actual[bucket], f"重复审阅决定ID：{aid}")
        actual[bucket].add(aid)
        _require(isinstance(decision["expected_record_digest"], str) and bool(re.fullmatch(r"[a-f0-9]{64}", decision["expected_record_digest"])), "expected_record_digest须为SHA256")
    for bucket, ids in actual.items():
        expected = set(scope[bucket])
        _require(ids == expected, f"审阅范围{bucket}不一致；缺少{sorted(expected - ids)}，多余{sorted(ids - expected)}")
    _require(not actual["character_ids"].intersection(actual["archive_ids"]), "同一人物不能同时修改与归档")
    return decisions


def _targets(data):
    characters = _index(data.get("characters", []), "characters")
    candidates = {}
    for item in data.get("prop_coverage_review", {}).get("candidates", []):
        cid = item.get("candidate_id")
        _require(isinstance(cid, str) and cid not in candidates, "候选ID重复或无效")
        candidates[cid] = item
    return characters, candidates


def prepare_review_payload(data, selections):
    """Fill fingerprints for explicitly chosen kinds/IDs, without approving facts."""
    _require(isinstance(selections, list) and bool(selections), "审阅选择须为非空数组")
    characters, candidates = _targets(data)
    scope = {"character_ids": [], "candidate_ids": [], "archive_ids": []}
    decisions = []
    for selection in selections:
        _require(isinstance(selection, dict), "审阅选择须为对象")
        kind = selection.get("kind")
        if kind in CHAR_FIELDS:
            _require(set(selection) == {"kind", "asset_id"}, "人物选择只接受kind/asset_id")
            aid, bucket = selection["asset_id"], "character_ids"
            target = characters.get(aid)
            fields = CHAR_FIELDS[kind]
        elif kind == "candidate_disposition":
            _require(set(selection) == {"kind", "candidate_id"}, "候选选择只接受kind/candidate_id")
            aid, bucket = selection["candidate_id"], "candidate_ids"
            target = candidates.get(aid)
            fields = CANDIDATE_FIELDS
        elif kind == "archive_inactive":
            _require(set(selection) == {"kind", "asset_id", "category"} and selection["category"] in PREFIXES, "归档选择只接受kind/asset_id/category")
            aid, bucket = selection["asset_id"], "archive_ids"
            target = _index(data.get("inactive_assets", {}).get(selection["category"], []), "待归档项").get(aid)
            fields = None
        else:
            raise ValueError(f"不支持的审阅选择：{kind}")
        _require(target is not None and aid not in scope[bucket], f"选择的ID不存在或重复：{aid}")
        if kind == "background_group":
            # This frozen field cannot be repaired by a background decision.
            # Reject it before the author fills the decision batch.
            validate_source_speaker(target)
        scope[bucket].append(aid)
        decision = dict(selection, expected_record_digest=fingerprint(target))
        if fields is not None:
            decision["values"] = {key: copy.deepcopy(target.get(key)) for key in sorted(fields)}
            # Existing text can be reused as evidence, but the new decision must
            # be explicitly reviewed. Do not manufacture a passing declaration.
            for key in ("ensemble_review", "extraction_review"):
                if isinstance(decision["values"].get(key), dict):
                    decision["values"][key]["reviewed_full_script"] = False
        else:
            decision["reason"] = ""
            known = [entry for entry in data.get("pending_reviews", []) if isinstance(entry, dict)
                     and entry.get("kind") == "人物可能改名或退场" and entry.get("asset_id") == aid]
            if known:
                decision["resolved_pending_digests"] = [fingerprint(entry) for entry in known]
        decisions.append(decision)
    _require(not set(scope["character_ids"]).intersection(scope["archive_ids"]), "同一人物不能同时修改与归档")
    return {"review_policy": POLICY, "source_digest": data.get("source_digest"), "input_ledger_digest": fingerprint(data),
            "scope": scope, "decisions": decisions}


def _apply_decision_draft(data, payload):
    decisions = _payload(payload)
    _require(payload["input_ledger_digest"] == fingerprint(data), "审阅输入台账摘要已变化，须重新核对决定")
    _require(payload["source_digest"] == data.get("source_digest"), "审阅原文摘要与台账不匹配")
    validate_asset_lifecycle(data, allow_pending=True)
    verify_review_decisions(data)
    result = copy.deepcopy(data)
    # Index only the copied records. Never keep pointers into the caller's data.
    characters, candidates = _targets(result)
    for decision in decisions:
        kind = decision["kind"]
        if kind in CHAR_FIELDS:
            target = characters.get(decision["asset_id"])
        elif kind == "candidate_disposition":
            target = candidates.get(decision["candidate_id"])
        else:
            target = _index(result.get("inactive_assets", {}).get(decision["category"], []), "待归档项").get(decision["asset_id"])
        _require(target is not None, f"审阅目标不存在：{decision.get('asset_id', decision.get('candidate_id'))}")
        _require(fingerprint(target) == decision["expected_record_digest"], f"审阅原值冲突：{decision.get('asset_id', decision.get('candidate_id'))}")
        if kind == "speaking_ensemble":
            values = decision["values"]
            _require(values["character_kind"] == "speaking_ensemble" and values["visual_mode"] == "image_asset", "发声集合必须显式登记speaking_ensemble/image_asset")
            _require("-群像-" not in target.get("source_entry", target.get("name", "")), "发声集合须先保留真实集合称谓，不能直接复用背景群像条目")
            _require("extraction_review" not in target, "发声集合不得保留背景群像审阅；须先复核分类冲突")
            target.update(copy.deepcopy(values))
        elif kind == "background_group":
            _require("-群像-" in target.get("source_entry", target.get("name", "")) and target.get("character_kind") != "speaking_ensemble", "背景群像决定须对应已核实的背景群像条目")
            target.update(copy.deepcopy(decision["values"]))
        elif kind == "candidate_disposition":
            target.update(copy.deepcopy(decision["values"]))
        else:
            aid, category = decision["asset_id"], decision["category"]
            resolved = []
            for pending_digest in decision.get("resolved_pending_digests", []):
                matches = [entry for entry in result.get("pending_reviews", []) if fingerprint(entry) == pending_digest]
                _require(len(matches) == 1, "指定退场待核记录摘要不存在或不唯一")
                previous = matches[0]
                _require(category == "characters" and previous.get("kind") == "人物可能改名或退场"
                         and previous.get("asset_id") == aid, "只允许解决同人物ID的明确退场待核项；其他待核须独立处理")
                resolved.append(copy.deepcopy(previous))
                result["pending_reviews"].remove(previous)
            result["inactive_assets"][category] = [item for item in result["inactive_assets"][category] if item["id"] != aid]
            if aid not in result.setdefault("retired_ids", []):
                result["retired_ids"].append(aid)
            archive = {"asset_id": aid, "category": category, "reason": decision["reason"], "record": copy.deepcopy(target)}
            if resolved:
                archive["resolved_pending_reviews"] = resolved
            result.setdefault("asset_archive", []).append(archive)
    validate_asset_lifecycle(result, allow_pending=True)
    return result


def apply_review_decisions(data, payload):
    result = _apply_decision_draft(data, payload)
    result.setdefault("asset_decision_receipts", []).append({"decision_digest": fingerprint(payload), "payload": copy.deepcopy(payload)})
    validate_asset_lifecycle(result)
    return result, verify_review_decisions(result, payload)


def merge_review_payloads(data, payloads):
    """Merge disjoint decisions frozen against exactly the same original input."""
    _require(isinstance(payloads, list) and bool(payloads), "须提供至少一批审阅决定")
    scope = {key: [] for key in ("character_ids", "candidate_ids", "archive_ids")}
    decisions, seen = [], set()
    for payload in payloads:
        records = _payload(payload)
        _require(payload["input_ledger_digest"] == fingerprint(data), "批次输入台账摘要已变化；禁止刷新摘要拼接旧决定")
        _require(payload["source_digest"] == data.get("source_digest"), "各批须冻结于同一当前原文")
        for key, values in payload["scope"].items():
            for aid in values:
                # Character edits and archival share the asset namespace.
                target = ("candidate" if key == "candidate_ids" else "asset", aid)
                _require(target not in seen, f"审阅批次范围重叠：{aid}")
                seen.add(target)
                scope[key].append(aid)
        decisions.extend(copy.deepcopy(records))
    merged = {"review_policy": POLICY, "source_digest": data.get("source_digest"),
              "input_ledger_digest": fingerprint(data), "scope": scope, "decisions": decisions}
    _payload(merged)
    # This also checks every original record digest against the untouched input.
    _apply_decision_draft(data, merged)
    return merged


def apply_review_batches(data, payloads):
    """Strict submission; CLI must still validate the complete script/ledger."""
    merged = merge_review_payloads(data, payloads)
    result, _ = apply_review_decisions(data, merged)
    # Preserve every original frozen decision file so later review-verify can
    # reconcile a constituent batch as well as check the aggregate ledger.
    result["asset_decision_receipts"].pop()
    result["asset_decision_receipts"].extend({"decision_digest": fingerprint(payload), "payload": copy.deepcopy(payload)} for payload in payloads)
    report = verify_review_decisions(result)
    report["batch_decision_digests"] = [fingerprint(payload) for payload in payloads]
    report["merged_decision_digest"] = fingerprint(merged)
    return result, report


def _checked_scene(data, script_by_number, sid):
    scene = next((scene for scene in data["scenes"] if scene["id"] == sid), None)
    _require(scene is not None, f"审阅场次不存在：{sid}")
    written = script_by_number.get(scene.get("number"))
    _require(written is not None and written.get("heading") == scene.get("heading"), f"审阅场次与标准稿不一致：{sid}")
    characters = _index(data["characters"], "characters")
    linked = scene.get("character_ids")
    _require(isinstance(linked, list) and all(aid in characters for aid in linked), f"审阅场次人物关联无效：{sid}")
    _require([characters[aid].get("source_entry", characters[aid]["name"]) for aid in linked] == written.get("cast_entries"),
             f"审阅场次人物槽位与标准稿不一致：{sid}")
    for aid in linked:
        validate_source_speaker(characters[aid], context=f"审阅场次{sid}")
    return scene, written


def _check_character_decision(data, item, paragraphs, script_by_number, fact_validator):
    from asset_visual_design import (GROUP_POLICY, LEGACY_GROUP_POLICY, nonempty, validate_speaking_ensemble,
                                     validate_visible_uses, group_minimum_visible_uses)
    scenes = {scene["id"]: scene for scene in data["scenes"]}
    relevant = [scene for scene in scenes.values() if item["id"] in scene.get("character_ids", [])]
    _require(bool(relevant), f"审阅人物没有活动场次：{item['id']}")
    bodies = []
    for scene in relevant:
        _, written = _checked_scene(data, script_by_number, scene["id"])
        bodies.append(written["body_text"])
    if item.get("character_kind") == "speaking_ensemble":
        validate_speaking_ensemble(data, item)
        validate_visible_uses(item, paragraphs, scenes, fact_validator, "character_ids", 1, "集合出场或共同发声")
        label = re.escape(item["source_speaker"])
        pattern = re.compile(r"^\s*" + label + r"(?:（[^\n]*?）|\([^\n]*?\))?\s*[:：]\S", re.M)
        _require(any(pattern.search(body) for body in bodies), f"{item['id']}未对应关联场次的集合台词标签")
    else:
        policy, mode = data.get("group_policy"), item.get("visual_mode")
        _require(policy in (None, GROUP_POLICY, LEGACY_GROUP_POLICY), "背景群像策略无效")
        _require(mode in ("image_asset", "prompt_only"), "背景群像须明确图片模式")
        record = item.get("extraction_review")
        _require(isinstance(record, dict) and record.get("kind") == "background_group" and record.get("reviewed_full_script") is True,
                 "背景群像须有作者已完成的明确分类审阅")
        _require(record.get("source_digest") == data.get("source_digest"), "背景群像审阅原文版本已变化")
        nonempty(record.get("identity_basis"), "背景身份依据")
        if policy == GROUP_POLICY:
            basis, needs = record.get("selection_basis"), record.get("requires_reference")
            _require(type(needs) is bool, "requires_reference须为布尔值")
            if mode == "image_asset":
                _require(basis in ("reusable", "core_continuity") and needs, "背景群像图片筛选组合不一致")
                if basis == "core_continuity":
                    nonempty(record.get("story_role"), "核心剧情作用")
            else:
                _require(basis == "prompt_only" and not needs, "纯文字群像筛选组合不一致")
                nonempty(record.get("text_sufficiency_reason"), "文字充分性依据")
        nonempty(record.get("consistency_reason" if mode == "image_asset" else "exclusion_reason"), "图片连续性或排除依据")
        speaker = item.get("source_entry", item["name"]).split("-群像-", 1)[0]
        pattern = re.compile(r"^\s*" + re.escape(speaker) + r"(?:-群像)?(?:\s*(?:OS|VO|O\.S\.|V\.O\.))?(?:（[^\n]*?）|\([^\n]*?\))?\s*[:：]", re.M)
        # Unlike a partial ledger, the complete script remains available here.
        _require(not any(pattern.search(scene["body_text"]) for scene in script_by_number.values()), "背景群像称谓具有独立台词，须重新核实类型")
        validate_visible_uses(item, paragraphs, scenes, fact_validator, "character_ids", group_minimum_visible_uses(data, item), "可见出场")
    for field in ("visual_facts", "personality_facts", "production_design"):
        if field in item:
            fact_validator(item[field], paragraphs, scenes, item["id"])


def _check_candidate_decision(data, candidate, paragraphs, script_by_number, fact_validator):
    from asset_visual_design import (PROP_POLICY, LEGACY_PROP_POLICY, FURNITURE, DAILY_ITEMS,
                                     nonempty, validate_visible_uses)
    cid = candidate["candidate_id"]
    nonempty(candidate.get("name"), f"{cid}名称")
    nonempty(candidate.get("reason"), f"{cid}归类依据")
    sids = candidate.get("scene_ids")
    _require(isinstance(sids, list) and bool(sids) and all(isinstance(sid, str) for sid in sids) and len(sids) == len(set(sids)), f"{cid}候选场次无效")
    scenes = {scene["id"]: scene for scene in data["scenes"]}
    for sid in sids:
        _checked_scene(data, script_by_number, sid)
    aliases = candidate.get("aliases", [])
    _require(isinstance(aliases, list) and all(isinstance(alias, str) and alias.strip() for alias in aliases), f"{cid}别名无效")
    disposition, target = candidate.get("disposition"), candidate.get("target_id")
    _require(type(candidate.get("requires_reference")) is bool, f"{cid}requires_reference须为布尔值")
    categories = {"asset": "props", "character": "characters", "set": "scene_assets"}
    if disposition == "prompt_only":
        _require(target is None and not candidate["requires_reference"], f"{cid}普通描述不能要求独立参考或指定目标")
    else:
        _require(disposition in categories, f"{cid}候选分类无效")
        item = _index(data[categories[disposition]], categories[disposition]).get(target)
        _require(item is not None and item.get("visual_mode") != "prompt_only", f"{cid}归类目标不存在或不具备图片参考")
        for sid in sids:
            scene = scenes[sid]
            linked = target in scene["prop_ids"] if disposition == "asset" else target in scene["character_ids"] if disposition == "character" else target == scene.get("scene_asset_id")
            _require(linked, f"{cid}目标未关联候选场次：{sid}")
        if disposition == "asset":
            _require(candidate["requires_reference"], f"{cid}独立道具须需要图片参考")
            policy = data.get("asset_policy")
            _require(policy in (None, PROP_POLICY, LEGACY_PROP_POLICY), "道具提取策略无效")
            if policy is not None:
                record = item.get("extraction_review")
                allowed = ("special_reusable", "core_continuity") if policy == PROP_POLICY else ("special_reusable",)
                _require(isinstance(record, dict) and record.get("kind") in allowed, f"{target}未完成目标道具入选审阅")
                nonempty(record.get("consistency_reason"), "目标道具连续性理由")
                core = record["kind"] == "core_continuity"
                if core:
                    nonempty(record.get("story_role"), "目标道具核心作用")
                names = [item.get("source_name", item["name"]), *item.get("aliases", [])]
                _require(core or not any(FURNITURE.search(name) for name in names), "普通固定家具须归入SET")
                if not core and any(name in DAILY_ITEMS for name in names):
                    nonempty(record.get("special_features"), "日用品特殊形制")
                validate_visible_uses(item, paragraphs, scenes, fact_validator, "prop_ids", 1 if core else 2, "可见使用")
            fact_validator(item.get("visual_facts", []), paragraphs, scenes, target)
    evidence = candidate.get("source_evidence")
    _require(isinstance(evidence, list) and bool(evidence), f"{cid}缺少真实来源证据")
    covered = set()
    for occurrence in evidence:
        _require(isinstance(occurrence, dict) and occurrence.get("scene_id") in sids, f"{cid}证据关联场次无效")
        sid, refs = occurrence["scene_id"], occurrence.get("source_paragraphs")
        quote = _nonempty(occurrence.get("evidence"), f"{cid}证据")
        _require(isinstance(refs, list) and bool(refs) and all(type(ref) is int for ref in refs)
                 and refs == list(range(min(refs), max(refs) + 1)), f"{cid}证据段落须连续有序")
        fact_validator([{"text": candidate["name"], "evidence": quote, "source_paragraphs": refs,
                         "scene_ids": [sid], "attribution": "道具覆盖审阅"}], paragraphs, scenes, cid, sid)
        text = "".join("".join(paragraphs[ref - 1] for ref in refs).split())
        _require(len(list(re.finditer(re.escape("".join(quote.split())), text))) == 1, f"{cid}证据不能唯一定位")
        covered.add(sid)
    _require(covered == set(sids), f"{cid}证据未覆盖全部候选场次")


def check_review_drafts(script, paragraphs, data, payloads):
    """Read-only checks of real selected evidence; never a full-ledger pass."""
    from asset_ledger import digest, fact_list
    _require(data.get("source_digest") == digest(paragraphs), "台账与实际source段落摘要不一致")
    merged = merge_review_payloads(data, payloads)
    draft = _apply_decision_draft(data, merged)
    selected = set().union(*(set(values) for values in merged["scope"].values()))
    for decision in merged["decisions"]:
        if decision["kind"] == "candidate_disposition" and decision["values"].get("target_id"):
            selected.add(decision["values"]["target_id"])
    relevant_pending = [entry for entry in draft.get("pending_reviews", []) if set(_reference_ids(entry)).intersection(selected)]
    _require(not relevant_pending, "本批目标或归类依赖仍有未解决待核；只允许不相关pending留在草稿中")
    script_by_number = {scene["number"]: scene for scene in script}
    _require(len(script_by_number) == len(script), "标准稿场次重复")
    characters, candidates = _targets(draft)
    batches = []
    for payload in payloads:
        checked = []
        for decision in payload["decisions"]:
            kind = decision["kind"]
            aid = decision.get("asset_id", decision.get("candidate_id"))
            if kind in CHAR_FIELDS:
                _check_character_decision(draft, characters[aid], paragraphs, script_by_number, fact_list)
            elif kind == "candidate_disposition":
                _check_candidate_decision(draft, candidates[aid], paragraphs, script_by_number, fact_list)
            checked.append({"id": aid, "kind": kind, "decision_fields": "checked",
                            "source_evidence": "checked" if kind != "archive_inactive" else "archive_record_and_live_references_checked"})
        batches.append({"decision_digest": fingerprint(payload), "checked_targets": checked})
    binding = {"input_ledger_digest": fingerprint(data), "source_digest": digest(paragraphs),
               "script_digest": fingerprint(script), "batch_decision_digests": [fingerprint(p) for p in payloads],
               "merged_decision_digest": fingerprint(merged)}
    return {"policy": "asset-review-drafts-v1", "status": "draft_checked", "formal_ready": False,
            "full_ledger_validation": "not_performed", "semantic_review": "author_judgment_required",
            "batches": batches, "scope": merged["scope"], "binding": binding, "binding_sha256": fingerprint(binding),
            "remaining_pending_count": len(draft.get("pending_reviews", [])),
            "remaining_inactive_count": sum(len(items) for items in draft.get("inactive_assets", {}).values()),
            "note": "仅检查选定决定、原值、证据和分类结构；不写台账，未检查范围及未解决待核不因此通过。"}


def verify_review_decisions(data, payload=None):
    receipts = data.get("asset_decision_receipts", [])
    _require(isinstance(receipts, list), "asset_decision_receipts须为数组")
    if payload is None and not receipts:
        return {"status": "not_registered", "checked_ids": []}
    effective = {}
    for receipt in receipts:
        _require(isinstance(receipt, dict) and set(receipt) == {"decision_digest", "payload"}, "缺少完整审阅决定回执")
        saved = receipt["payload"]
        _require(receipt["decision_digest"] == fingerprint(saved), "审阅决定回执摘要不一致")
        decisions = _payload(saved)
        _require(saved["source_digest"] == data.get("source_digest"), "审阅决定对应的原文版本已过期")
        for decision in decisions:
            bucket = "candidate" if decision["kind"] == "candidate_disposition" else "archive" if decision["kind"] == "archive_inactive" else "character"
            effective[(bucket, decision.get("asset_id", decision.get("candidate_id")))] = decision
    if payload is not None:
        _require(any(receipt["payload"] == payload and receipt["decision_digest"] == fingerprint(payload) for receipt in receipts), "审阅决定文件与保存回执不一致")
    characters, candidates = _targets(data)
    archive = {entry["asset_id"]: entry for entry in data.get("asset_archive", [])}
    checked = []
    for decision in effective.values():
        kind = decision["kind"]
        aid = decision.get("asset_id", decision.get("candidate_id"))
        if kind == "archive_inactive":
            entry = archive.get(aid)
            _require(entry is not None and entry["category"] == decision["category"] and entry["reason"] == decision["reason"]
                     and fingerprint(entry["record"]) == decision["expected_record_digest"], f"归档决定未完整落盘：{aid}")
            _require([fingerprint(previous) for previous in entry.get("resolved_pending_reviews", [])] == decision.get("resolved_pending_digests", []),
                     f"退场待核处理记录未完整落盘：{aid}")
        else:
            target = (candidates if kind == "candidate_disposition" else characters).get(aid)
            _require(target is not None, f"已确认审阅ID未落盘：{aid}")
            for field, value in decision["values"].items():
                _require(field in target and target[field] == value, f"审阅决定未落盘或已变化：{aid}.{field}")
        checked.append(aid)
    return {"status": "matched", "review_policy": POLICY,
            "receipt_count": len(receipts), "decision_digests": [receipt["decision_digest"] for receipt in receipts],
            "checked_ids": checked, "checked_count": len(checked)}


def _review_context(data, decision):
    """Dependencies supporting a decision; presentation-only fields are omitted."""
    kind = decision["kind"]
    aid = decision.get("asset_id", decision.get("candidate_id"))
    characters, candidates = _targets(data)
    target = candidates.get(aid) if kind == "candidate_disposition" else characters.get(aid)
    if kind == "archive_inactive":
        return next((entry for entry in data.get("asset_archive", []) if entry["asset_id"] == aid), None)
    if target is None:
        return None
    clean = copy.deepcopy(target)
    for field in ("name", "visual_design", "image_prompt", "image_format", "image_design_digest"):
        clean.pop(field, None)
    related = {aid}
    if kind == "candidate_disposition" and target.get("target_id"):
        related.add(target["target_id"])
    scenes = [copy.deepcopy(scene) for scene in data.get("scenes", [])
              if set(_reference_ids(scene)).intersection(related)
              or scene.get("id") in target.get("scene_ids", [])]
    links = {scene.get("location_id") for scene in scenes} | {scene.get("scene_asset_id") for scene in scenes}
    dependencies = []
    for category in ("characters", "props", "locations", "scene_assets"):
        for item in data.get(category, []):
            if item["id"] in links | related and item["id"] != aid:
                record = copy.deepcopy(item)
                for field in ("name", "visual_design", "image_prompt", "image_format", "image_design_digest"):
                    record.pop(field, None)
                dependencies.append(record)
    return {"record": clean, "scenes": scenes, "dependencies": dependencies}


def assert_normalize_review_safe(before, after, normalization_report=None):
    """Reject changed reviewed identities/dependencies before normalize writes.

    A pure display-name/render change may proceed. This does not re-sign, migrate
    or revalidate artistic designs; the normal full validators still apply.
    """
    prior = verify_review_decisions(before)
    if prior["status"] == "not_registered":
        return {"status": "not_registered", "checked_review_ids": [], "name_only_ids": []}
    _require(before.get("asset_decision_receipts") == after.get("asset_decision_receipts"), "normalize不能改写既有审阅回执；请明确回退到结构审阅阶段")
    try:
        verify_review_decisions(after)
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"normalize会使既有审阅决定失效，未写出；请在归并后重新审阅，不刷新旧摘要：{exc}") from exc
    effective = {}
    for receipt in before["asset_decision_receipts"]:
        for decision in receipt["payload"]["decisions"]:
            key = ("candidate" if decision["kind"] == "candidate_disposition" else "archive" if decision["kind"] == "archive_inactive" else "character",
                   decision.get("asset_id", decision.get("candidate_id")))
            effective[key] = decision
    impacted = [key[1] for key, decision in effective.items() if _review_context(before, decision) != _review_context(after, decision)]
    _require(not impacted, f"normalize改变已审资产的身份、事实或场次依赖，未写出；须先回到结构审阅：{sorted(impacted)}")
    names = []
    for category in PREFIXES:
        old, new = _index(before.get(category, []), category), _index(after.get(category, []), category)
        names.extend(aid for aid in set(old) & set(new) if old[aid].get("name") != new[aid].get("name"))
    return {"status": "review_decisions_preserved", "checked_review_ids": prior["checked_ids"],
            "name_only_ids": sorted(names), "design_validation": "still_required"}
