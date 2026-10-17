"""Conservative, read-only impact analysis; never renew source/review digests."""

import copy
from collections import defaultdict, deque

from asset_review import fingerprint


POLICY = "asset-change-impact-v1"
ASSETS = ("characters", "locations", "scene_assets", "props")
IMAGE_CATEGORIES = {"characters", "scene_assets", "props"}
CATEGORIES = ("scenes", *ASSETS)
DERIVED = {"image_prompt", "image_format", "image_design_digest"}
DESIGN_DERIVED = {"source_fact_digest", "source_context_digest"}
BOOKKEEPING = {"asset_decision_receipts", "asset_decision_history", "asset_archive", "retired_ids", "inactive_assets", "pending_reviews"}
GLOBAL_POLICIES = {"schema_version", "asset_policy", "group_policy", "character_variant_policy", "image_design_version", "visual_style", "temporal_layers"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _catalog(data):
    result = {}
    for category in CATEGORIES:
        records = data.get(category, [])
        _require(isinstance(records, list), f"{category}须为数组")
        for item in records:
            _require(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] not in result, "影响分析输入ID无效或重复")
            result[item["id"]] = (category, item)
    return result


def _author_record(item):
    value = {key: copy.deepcopy(entry) for key, entry in item.items() if key not in DERIVED}
    if isinstance(value.get("visual_design"), dict):
        value["visual_design"] = {key: entry for key, entry in value["visual_design"].items() if key not in DESIGN_DERIVED}
    return value


def _design_records(payload):
    if payload is None:
        return {}
    _require(isinstance(payload, dict) and isinstance(payload.get("designs"), list), "设计影响输入须包含designs数组")
    result = {}
    for item in payload["designs"]:
        _require(isinstance(item, dict) and isinstance(item.get("asset_id"), str) and item["asset_id"] not in result, "设计影响输入asset_id无效或重复")
        result[item["asset_id"]] = {key: copy.deepcopy(value) for key, value in item.items() if key not in DESIGN_DERIVED}
    return result


def _anchors(value):
    result = {}
    for anchor in value or []:
        _require(isinstance(anchor, dict) and isinstance(anchor.get("id"), str) and anchor["id"] not in result, "共享身份锚点ID无效或重复")
        result[anchor["id"]] = anchor
    return result


def _dependencies(data, authored=None):
    """Dependency -> dependent edges, plus concrete reasons for each edge."""
    catalog = _catalog(data)
    edges = defaultdict(dict)
    groups = defaultdict(set)
    authored = authored or {}
    def edge(source, target, reason):
        if source and target and source != target:
            edges[source][target] = reason
    for aid, (category, item) in catalog.items():
        if category == "characters":
            variant = item.get("appearance_variant", {})
            edge(variant.get("base_asset_id"), aid, "base_character_or_clothing_dependency")
            edge(variant.get("prop_asset_id"), aid, "shared_covering_PROP_dependency")
            appearance = item.get("appearance")
            if isinstance(appearance, dict) and appearance.get("identity") and appearance.get("incarnation"):
                groups[("body", appearance["identity"], appearance["incarnation"])].add(aid)
            design = authored.get(aid, item.get("visual_design", {}))
            anchor = design.get("identity_anchor_id") if isinstance(design, dict) else None
            if anchor:
                groups[("anchor", anchor)].add(aid)
                edge("IDENTITY:" + anchor, aid, "shared_identity_anchor_dependency")
        elif category == "scene_assets":
            edge(item.get("location_id"), aid, "LOC_space_or_facts_dependency")
            edge(item.get("parent_id"), aid, "SET_parent_version_dependency")
            if item.get("variant") != "base":
                for base in data.get("scene_assets", []):
                    if base.get("location_id") == item.get("location_id") and base.get("variant") == "base":
                        edge(base["id"], aid, "SET_base_facts_dependency")
        elif category == "scenes":
            for field in ("location_id", "scene_asset_id"):
                edge(aid, item.get(field), "changed_scene_context")
            for field in ("character_ids", "prop_ids"):
                for target in item.get(field, []):
                    edge(aid, target, "changed_scene_context")
            for track in item.get("character_appearances", []):
                for field in ("base_asset_id", "initial_asset_id"):
                    edge(aid, track.get(field), "changed_scene_appearance_context")
                for event in track.get("transitions", []):
                    edge(aid, event.get("from_asset_id"), "changed_scene_appearance_context")
                    edge(aid, event.get("to_asset_id"), "changed_scene_appearance_context")
    for members in groups.values():
        for source in members:
            for target in members:
                edge(source, target, "shared_actual_identity_dependency")
    return edges


def analyze_asset_impact(before, after, *, before_source=None, after_source=None,
                         before_design=None, after_design=None):
    """Describe required work; the result is never proof of reusable review."""
    _require(isinstance(before, dict) and isinstance(after, dict), "影响分析需要变更前后完整台账")
    old, new = _catalog(before), _catalog(after)
    old_design, new_design = _design_records(before_design), _design_records(after_design)
    known = dict(old)
    known.update(new)
    direct, dependent = {}, {}
    unknown_reasons, administrative = [], []
    graph = defaultdict(dict)
    for data, designs in ((before, old_design), (after, new_design)):
        for source, targets in _dependencies(data, designs).items():
            graph[source].update(targets)

    def add(aid, change, reason, semantic=True, recompile=True, *, fields=None):
        row = direct.setdefault(aid, {"id": aid, "category": known.get(aid, ("identity_anchor", {}))[0],
                                     "change": change, "reasons": [], "changed_fields": [],
                                     "requires_semantic_review": False, "requires_recompile": False})
        if reason not in row["reasons"]:
            row["reasons"].append(reason)
        row["changed_fields"] = sorted(set(row["changed_fields"]) | set(fields or []))
        row["requires_semantic_review"] |= semantic
        row["requires_recompile"] |= bool(recompile and row["category"] in IMAGE_CATEGORIES)
        if semantic and row["change"] == "name_only":
            row["change"] = change

    for aid in sorted(set(old) | set(new)):
        if aid not in old:
            add(aid, "added", "new_asset_or_scene_requires_source_and_identity_review")
        elif aid not in new:
            add(aid, "removed", "removed_asset_or_scene_requires_reference_review")
        else:
            previous, current = old[aid][1], new[aid][1]
            a, b = _author_record(previous), _author_record(current)
            changed = {field for field in set(a) | set(b) if a.get(field) != b.get(field) or (field in a) != (field in b)}
            if old[aid][0] != new[aid][0]:
                add(aid, "category_changed", "stable_ID_category_changed_unknown_scope")
                unknown_reasons.append("stable_ID_category_changed")
            elif changed == {"name"}:
                add(aid, "name_only", "only_production_display_name_changed_source_identity_unchanged", semantic=False, fields=changed)
            elif changed:
                change = "scene_context" if old[aid][0] == "scenes" else "local_facts" if changed <= {"visual_facts", "personality_facts", "production_design"} else "asset_context_or_design"
                add(aid, change, "author_inputs_changed", fields=changed)
            elif previous != current:
                raw_changed = {field for field in set(previous) | set(current) if previous.get(field) != current.get(field) or (field in previous) != (field in current)}
                add(aid, "derived_only", "derived_fields_changed_require_deterministic_verification", semantic=False, fields=raw_changed)

    if before.get("source_digest") != after.get("source_digest"):
        unknown_reasons.append("source_digest_changed_global_context_must_be_reviewed")
    if (before_source is None) != (after_source is None):
        unknown_reasons.append("only_one_source_snapshot_provided")
    elif before_source is not None and before_source != after_source:
        unknown_reasons.append("actual_source_input_changed_global_or_unknown_context")
    for field in sorted(GLOBAL_POLICIES):
        if before.get(field) != after.get(field):
            unknown_reasons.append("global_policy_or_style_changed:" + field)
    known_top = set(CATEGORIES) | BOOKKEEPING | GLOBAL_POLICIES | {"source_digest", "identity_anchors", "prop_coverage_review", "delivery_contract"}
    for field in sorted(set(before) | set(after)):
        if before.get(field) == after.get(field):
            continue
        if field in BOOKKEEPING or field == "delivery_contract":
            administrative.append({"field": field, "required_check": "lifecycle_review_history_or_delivery_contract_validation"})
        elif field not in known_top:
            unknown_reasons.append("unclassified_top_level_change:" + field)

    old_anchors = _anchors((before_design or {}).get("identity_anchors", before.get("identity_anchors", [])))
    new_anchors = _anchors((after_design or {}).get("identity_anchors", after.get("identity_anchors", [])))
    for key in sorted(set(old_anchors) | set(new_anchors)):
        if old_anchors.get(key) != new_anchors.get(key):
            add("IDENTITY:" + key, "shared_identity_anchor", "identity_anchor_changed")
    if (before_design is None) != (after_design is None):
        unknown_reasons.append("only_one_design_payload_provided")
    elif before_design is not None:
        for aid in sorted(set(old_design) | set(new_design)):
            if old_design.get(aid) != new_design.get(aid):
                add(aid, "design_payload", "author_design_payload_changed")
                if aid not in known:
                    unknown_reasons.append("design_targets_unknown_asset:" + aid)
        if before_design.get("image_design_version") != after_design.get("image_design_version"):
            unknown_reasons.append("design_payload_compiler_version_changed")
        for key in (set(before_design) | set(after_design)) - {"identity_anchors", "designs", "image_design_version"}:
            if before_design.get(key) != after_design.get(key):
                unknown_reasons.append("unclassified_design_payload_change:" + key)

    def candidate_map(data):
        values = data.get("prop_coverage_review", {}).get("candidates", [])
        result = {}
        for item in values:
            _require(isinstance(item, dict) and isinstance(item.get("candidate_id"), str) and item["candidate_id"] not in result, "候选影响输入ID重复或无效")
            result[item["candidate_id"]] = item
        return result
    old_candidates, new_candidates = candidate_map(before), candidate_map(after)
    for cid in sorted(set(old_candidates) | set(new_candidates)):
        a, b = old_candidates.get(cid), new_candidates.get(cid)
        if a == b:
            continue
        targets = {value.get("target_id") for value in (a, b) if value and value.get("target_id")}
        for target in targets:
            add(target, "candidate_decision", "candidate_classification_or_evidence_changed:" + cid)
        if not targets:
            for item in (a, b):
                for sid in (item or {}).get("scene_ids", []):
                    add(sid, "candidate_decision", "unreferenced_candidate_changed_requires_local_scene_review:" + cid)
    old_coverage = {k: v for k, v in before.get("prop_coverage_review", {}).items() if k != "candidates"}
    new_coverage = {k: v for k, v in after.get("prop_coverage_review", {}).items() if k != "candidates"}
    if old_coverage != new_coverage:
        unknown_reasons.append("full_script_candidate_coverage_declaration_changed")

    for origin, record in list(direct.items()):
        queue, visited = deque([origin]), {origin}
        while queue:
            source = queue.popleft()
            for target, why in graph.get(source, {}).items():
                if target not in visited:
                    visited.add(target)
                    queue.append(target)
                if target == origin:
                    continue
                category = known.get(target, ("unknown", {}))[0]
                row = dependent.setdefault(target, {"id": target, "category": category,
                    "reasons": [], "trigger_ids": [], "requires_semantic_review": False, "requires_recompile": category in IMAGE_CATEGORIES})
                if why not in row["reasons"]:
                    row["reasons"].append(why)
                if origin not in row["trigger_ids"]:
                    row["trigger_ids"].append(origin)
                row["requires_semantic_review"] |= record["requires_semantic_review"]
    unknown_reasons = sorted(set(unknown_reasons))
    affected_assets = sorted(aid for aid, (category, _) in known.items() if category in ASSETS)
    unknown = [{"id": aid, "category": known[aid][0], "reasons": unknown_reasons,
                "requires_semantic_review": True, "requires_recompile": known[aid][0] in IMAGE_CATEGORIES} for aid in affected_assets] if unknown_reasons else []
    inputs = {"before_ledger": fingerprint(before), "after_ledger": fingerprint(after)}
    for name, value in (("before_source", before_source), ("after_source", after_source), ("before_design", before_design), ("after_design", after_design)):
        if value is not None:
            inputs[name] = fingerprint(value)
    binding = {"policy": POLICY, "input_sha256": inputs}
    combined = list(direct.values()) + list(dependent.values()) + unknown
    return {"policy": POLICY, "status": "impact_report", "formal_ready": False, "automatic_review_reuse": False,
            **binding, "binding_sha256": fingerprint(binding),
            "direct": [direct[key] for key in sorted(direct)],
            "dependent": [dependent[key] for key in sorted(dependent)], "unknown": unknown,
            "global_or_unknown_reasons": unknown_reasons, "administrative_changes": administrative,
            "required_semantic_review_ids": sorted({row["id"] for row in combined if row["requires_semantic_review"]}),
            "required_recompile_ids": sorted({row["id"] for row in combined if row["requires_recompile"] and row["id"] in known and known[row["id"]][0] in IMAGE_CATEGORIES}),
            "unchanged_ids": sorted(set(known) - {row["id"] for row in combined}),
            "limitations": ["影响清单不刷新摘要、不证明原审阅可复用；现有source_digest及完整校验保持原样。",
                            "仅name变化建议确定性重编译；正式接纳仍须现有完整检查，不自动放行。",
                            "来源、全局政策或未知字段变化按全局待核处理，不凭经验推断不影响其他资产。"]}
