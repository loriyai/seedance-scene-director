"""Read-only partial design previews over a complete, source-validated ledger.

The report is deliberately not a ledger. It cannot replace full design/render/bind
acceptance, and structural compilation never creates a semantic approval.
"""

import copy
import hashlib
import json
from pathlib import Path

import asset_visual_design as design
import design_scope

POLICY = "asset-design-preview-v1"
INPUT_KEYS = ("script", "source", "ledger", "design", "style")
DERIVED = ("visual_design", "image_prompt", "image_format", "image_design_digest")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _catalog(data):
    catalog = {}
    for category in (*design.ASSET_CATEGORIES, "locations"):
        for item in data.get(category, []):
            aid = item.get("id")
            require(isinstance(aid, str) and aid, "资产缺少稳定ID")
            require(aid not in catalog, f"重复资产ID：{aid}")
            catalog[aid] = (category, item)
    return catalog


def _identity(item):
    appearance = item.get("appearance")
    if isinstance(appearance, dict):
        return (appearance.get("identity"), appearance.get("incarnation"))
    return None


def _closure(data, catalog, requested, authored):
    needed, reasons, visiting = set(), {}, set()

    def add(aid, reason):
        require(aid in catalog, f"缺少设计依赖资产：{aid}")
        category, item = catalog[aid]
        require(item.get("visual_mode") != "prompt_only", f"仅文字群像不接受图片预览：{aid}")
        reasons.setdefault(aid, set()).add(reason)
        if aid in needed:
            return
        require(aid not in visiting, f"设计依赖存在循环：{aid}")
        visiting.add(aid)
        # Mark before same-identity expansion: identity peers form a group,
        # whereas the explicit SET parent graph is checked separately below.
        needed.add(aid)
        if category == "characters":
            variant = item.get("appearance_variant", {})
            if variant:
                base_id = variant.get("base_asset_id")
                require(base_id in catalog and catalog[base_id][0] == "characters", f"{aid}缺少基础人物")
                require(not catalog[base_id][1].get("appearance_variant"), f"{aid}基础人物不能是佩戴版本")
                add(base_id, f"{aid}:base_asset_id")
                if variant.get("prop_asset_id") is not None:
                    prop_id = variant["prop_asset_id"]
                    require(prop_id in catalog and catalog[prop_id][0] == "props", f"{aid}缺少关联PROP")
                    add(prop_id, f"{aid}:prop_asset_id")
            identity = _identity(item)
            anchor = authored.get(aid, item.get("visual_design", {})).get("identity_anchor_id")
            for other in design.visual_assets(data, "characters"):
                other_design = authored.get(other["id"], other.get("visual_design", {}))
                if (identity is not None and _identity(other) == identity or
                        anchor and other_design.get("identity_anchor_id") == anchor):
                    add(other["id"], f"{aid}:shared_identity")
        elif category == "scene_assets":
            loc = item.get("location_id")
            require(loc in catalog and catalog[loc][0] == "locations", f"{aid}缺少LOC事实依赖")
            add(loc, f"{aid}:location_facts")
            chain, current = {aid}, item
            while current.get("parent_id") is not None:
                parent_id = current["parent_id"]
                require(parent_id not in chain, f"SET父链存在循环：{aid}")
                require(parent_id in catalog and catalog[parent_id][0] == "scene_assets", f"{aid}缺少SET父资产")
                parent = catalog[parent_id][1]
                require(parent.get("location_id") == loc, f"{aid}SET父链跨越LOC")
                chain.add(parent_id)
                add(parent_id, f"{aid}:parent_id")
                current = parent
            if item.get("variant") != "base":
                bases = [value["id"] for value in data["scene_assets"]
                         if value.get("location_id") == loc and value.get("variant") == "base"]
                require(len(bases) == 1, f"{aid}须有唯一基础SET")
                add(bases[0], f"{aid}:base_facts")
        visiting.remove(aid)

    for aid in requested:
        require(aid in catalog and catalog[aid][0] in design.ASSET_CATEGORIES, f"预览范围不是图片资产：{aid}")
        add(aid, "requested")
    return needed, {aid: sorted(values) for aid, values in sorted(reasons.items())}


def _check_existing(data, category, item, style_text, version):
    """Check the original frozen dependency before installing any new design."""
    aid = item["id"]
    value = item.get("visual_design")
    require(isinstance(value, dict), f"缺少设计依赖：{aid}；请提供设计或有效旧设计")
    require(data.get("image_design_version") == version, f"{aid}旧设计版本无效，须提供当前设计")
    require(data.get("visual_style") == {"text": style_text, "digest": design.fingerprint(style_text)},
            f"{aid}旧设计风格不同或摘要过期，须提供当前设计")
    anchors = design.identity_map(data)
    design.validate_design_record(category, item, value, anchors, version)
    require(item.get("image_design_digest") == design.fingerprint(value), f"{aid}旧设计摘要过期")
    require(value.get("source_fact_digest") == design.fingerprint(design.source_constraints(data, category, item)),
            f"{aid}旧设计事实过期")
    require(value.get("source_context_digest") == design.source_context_digest(data, category, item),
            f"{aid}旧设计来源或依赖过期")
    prompt, form = design.compile_prompt(data, category, item, value, anchors)
    require(item.get("image_prompt") == prompt and item.get("image_format") == form,
            f"{aid}旧提示词或共享锚点过期")
    return copy.deepcopy(value)


def preview_design(data, payload, style_text, asset_ids, input_hashes=None):
    """Compile selected designs and required dependencies without modifying input.

    File callers must first validate the complete ledger against source/script;
    preview_from_files performs that step. This pure entry is for unit tests and
    already validated in-memory callers, never an alternate formal acceptance.
    """
    require(data.get("schema_version") == 3, "部分设计预览只接受schema 3完整台账")
    require(isinstance(asset_ids, list) and asset_ids and all(isinstance(aid, str) and aid for aid in asset_ids),
            "预览须明确非空asset_id范围")
    require(len(asset_ids) == len(set(asset_ids)), "预览asset_id范围重复")
    require(isinstance(payload, dict) and isinstance(payload.get("designs"), list), "设计JSON须含designs数组")
    style_text = style_text.strip()
    design.nonempty(style_text, "预览风格")
    require("\n" not in style_text and "\r" not in style_text, "预览风格须为单行")
    design.validate_prop_coverage(data)
    design.validate_group_modes(data)
    catalog = _catalog(data)
    authored = {}
    for record in payload["designs"]:
        require(isinstance(record, dict), "设计记录须为对象")
        aid = record.get("asset_id")
        require(isinstance(aid, str) and aid in catalog, f"设计包含未知资产：{aid}")
        require(aid not in authored, f"重复资产设计：{aid}")
        authored[aid] = {key: copy.deepcopy(value) for key, value in record.items() if key != "asset_id"}
    needed, reasons = _closure(data, catalog, asset_ids, authored)
    image_ids = needed - {aid for aid in needed if catalog[aid][0] == "locations"}
    require(set(authored) <= image_ids, f"设计超出预览范围及依赖闭包：{sorted(set(authored) - image_ids)}")
    version = payload.get("image_design_version", data.get("image_design_version", design.DESIGN_VERSION))
    require(version in design.DESIGN_VERSIONS, "不支持的图片设计版本")
    result = copy.deepcopy(data)  # Never manufacture a smaller ledger.
    current = _catalog(result)
    result["image_design_version"] = version
    result["visual_style"] = {"text": style_text, "digest": design.fingerprint(style_text)}
    old_anchors = design.identity_map(data)
    new_anchors = design.identity_map({"identity_anchors": payload.get("identity_anchors", [])})
    merged_anchors = {**copy.deepcopy(old_anchors), **copy.deepcopy(new_anchors)}
    result["identity_anchors"] = list(merged_anchors.values())
    modes = {}
    for aid in sorted(image_ids):
        category, old = catalog[aid]
        value = (copy.deepcopy(authored[aid]) if aid in authored else
                 _check_existing(data, category, old, style_text, version))
        current[aid][1]["visual_design"] = value
        modes[aid] = "authored" if aid in authored else "reused_verified"
    for aid in sorted(image_ids):
        category, item = current[aid]
        variant = item.get("appearance_variant")
        if category != "characters" or not variant:
            continue
        base = current[variant["base_asset_id"]][1]
        require(not design.is_group(base), f"{aid}佩戴基础不能为群体")
        value, base_design = item["visual_design"], base["visual_design"]
        for field in ("identity_anchor_id", "styling", "appearance_overrides", "override_source_facts"):
            require(field not in value or value[field] == base_design.get(field), f"{aid}须继承基础形象的{field}")
            if field in base_design:
                value[field] = copy.deepcopy(base_design[field])
        if base_design.get("appearance_overrides"):
            value.setdefault("variant_differences", copy.deepcopy(base_design.get("variant_differences", [])))
    # Check same-body identity and anchor ownership against the full inventory.
    identities, owners = {}, {}
    for item in design.visual_assets(result, "characters"):
        if version == design.DESIGN_VERSION and design.is_group(item):
            continue
        value, key = item.get("visual_design", {}), _identity(item)
        anchor = value.get("identity_anchor_id")
        if key is None or anchor is None:
            continue
        require(key not in identities or identities[key] == anchor, f"{item['id']}同身份形象使用不同锚点")
        require(anchor not in owners or owners[anchor] == key, f"{item['id']}不同身份共享锚点")
        identities[key], owners[anchor] = anchor, key
    design_scope.check_context(result, image_ids)
    previews = []
    for aid in sorted(image_ids):
        category, item = current[aid]
        value = item["visual_design"]
        design.validate_design_record(category, item, value, merged_anchors, version)
        facts_hash = design.fingerprint(design.source_constraints(result, category, item))
        context_hash = design.source_context_digest(result, category, item)
        for key, expected in (("source_fact_digest", facts_hash), ("source_context_digest", context_hash)):
            require(key not in value or value[key] == expected, f"{aid}设计携带过期{key}，须重新实审当前依赖")
        prompt, form = design.compile_prompt(result, category, item, value, merged_anchors)
        if modes[aid] == "reused_verified":
            old = catalog[aid][1]
            require(prompt == old["image_prompt"] and form == old["image_format"],
                    f"{aid}复用设计受新共享依赖影响，须重新实审并提供设计")
        previews.append({"asset_id": aid, "category": category, "name": item["name"],
                         "requested": aid in asset_ids, "design_origin": modes[aid],
                         "source_fact_sha256": facts_hash, "source_context_sha256": context_hash,
                         "image_prompt": prompt, "image_format": form})
    binding = {"input_sha256": input_hashes or {
        "ledger": design.fingerprint(data), "design": design.fingerprint(payload), "style": design.fingerprint(style_text)},
        "requested_asset_ids": sorted(asset_ids), "dependency_asset_ids": sorted(needed)}
    return {"policy": POLICY, "status": "partial_preview", "formal_ready": False,
            "semantic_review": "not_performed", "bindable": False,
            "required_final_steps": ["full_design", "full_render_validation", "bind"],
            **binding, "binding_sha256": design.fingerprint(binding),
            "dependency_reasons": reasons, "preview_count": len(previews), "previews": previews,
            "field_scope_check": design_scope.POLICY,
            "wearable_review_pairs": [
                {"character_id": aid, "prop_id": current[aid][1]["appearance_variant"]["prop_asset_id"],
                 "review": "实读道具单品与人物佩戴板，确认只继承物理属性，不继承单品构图"}
                for aid in sorted(image_ids)
                if current[aid][0] == "characters"
                and current[aid][1].get("appearance_variant", {}).get("prop_asset_id")]}


def preview_from_files(script_path, source_path, ledger_path, design_path, style_path,
                       asset_ids, expected_hashes=None):
    """Freeze files, validate all source facts, and return a partial-only report."""
    import asset_ledger as ledger
    paths = dict(zip(INPUT_KEYS, map(Path, (script_path, source_path, ledger_path, design_path, style_path))))
    contents = {key: path.read_bytes() for key, path in paths.items()}
    hashes = {key: hashlib.sha256(value).hexdigest() for key, value in contents.items()}
    if expected_hashes is not None:
        require(isinstance(expected_hashes, dict) and set(expected_hashes) == set(INPUT_KEYS),
                "expected_hashes须精确包含script/source/ledger/design/style")
        require(expected_hashes == hashes, "预览冻结输入已变化，不能沿用旧检查")
    data = json.loads(contents["ledger"].decode("utf-8-sig"))
    payload = json.loads(contents["design"].decode("utf-8-sig"))
    source_data = copy.deepcopy(data)
    for field in ("image_design_version", "visual_style", "identity_anchors"):
        source_data.pop(field, None)
    for category in design.ASSET_CATEGORIES:
        for item in source_data.get(category, []):
            for field in DERIVED:
                item.pop(field, None)
    ledger.validate(ledger.parse_script(paths["script"]), ledger.source_paragraphs(paths["source"]), source_data)
    report = preview_design(data, payload, contents["style"].decode("utf-8-sig"), asset_ids, hashes)
    require(all(hashlib.sha256(path.read_bytes()).hexdigest() == hashes[key] for key, path in paths.items()),
            "预览期间输入发生变化，须重新检查")
    return report
