"""Validate reviewed asset designs and compile reusable GPT-image-2 prompts.

This module never infers appearance, counts words, generates images, or changes
asset identity. Source facts and author supplied production designs stay apart.
"""

import copy
import hashlib
import json
import re


LEGACY_PROP_POLICY = "visible-reuse-v1"
PROP_POLICY = "visual-continuity-v2"
LEGACY_GROUP_POLICY = "visible-reuse-v1"
GROUP_POLICY = "visual-continuity-v2"
DESIGN_VERSION = "gpt-image-2-v1"
ASSET_CATEGORIES = ("characters", "scene_assets", "props")
FURNITURE = re.compile(r"(?:桌|床|椅|柜|沙发|茶几|凳)(?:子|铺)?$")
DAILY_ITEMS = {"锅", "碗", "瓢", "盆", "锅碗瓢盆", "筷子", "餐具", "水杯", "杯子", "手机", "雨伞", "香烟", "茶杯", "盘子", "勺子"}
CHARACTER_LAYOUT = (
    "16:9横版角色参考图，纯白背景。最左侧超大正面面部特写占满画面1/3宽度并贯穿画面高度，平视前方，"
    "完整展示头部和清晰五官；右侧2/3依次并排正面全身、90度侧面全身、背面全身，"
    "三个全身视图等高，头顶和脚底对齐，头顶到脚底完整入镜，不裁切、不重叠。"
    "特写和三个全身视图属于同一角色、同一状态，脸部身份、体型、发型、服装和配色一致。"
)


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{label}须为非空单行文字")
    return value.strip()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def is_group(item):
    return "-群像-" in item.get("source_entry", item.get("name", ""))


def visual_assets(data, category):
    """CHAR remains the narrative cast index even when no reference image is needed."""
    return [item for item in data.get(category, []) if item.get("visual_mode") != "prompt_only"]


def validate_group_modes(data):
    policy = data.get("group_policy")
    if policy not in (None, LEGACY_GROUP_POLICY, GROUP_POLICY):
        raise ValueError(f"不支持的群像提取政策：{policy}")
    for category in ASSET_CATEGORIES:
        for item in data.get(category, []):
            mode = item.get("visual_mode")
            if mode is not None and mode not in ("image_asset", "prompt_only"):
                raise ValueError(f"{item['id']} visual_mode须为image_asset或prompt_only")
            if category != "characters" or not is_group(item):
                if mode == "prompt_only":
                    raise ValueError(f"{item['id']}仅无姓名、无独立台词的背景群像可设为prompt_only")
                continue
            if policy is None and mode is None:
                continue  # Existing groups are never silently demoted.
            if mode is None:
                raise ValueError(f"{item['id']}新群像须审阅并明确visual_mode")
            review = item.get("extraction_review")
            if not isinstance(review, dict) or review.get("kind") != "background_group" or review.get("reviewed_full_script") is not True:
                raise ValueError(f"{item['id']}群像须完成全剧背景身份与独立出场审阅")
            if not data.get("source_digest") or review.get("source_digest") != data.get("source_digest"):
                raise ValueError(f"{item['id']}群像筛选审阅与原文版本不匹配，须重新审阅")
            nonempty(review.get("identity_basis"), f"{item['id']}无姓名、无独立台词及原文明示身份依据")
            if policy == GROUP_POLICY:
                basis = review.get("selection_basis")
                if basis not in ("reusable", "core_continuity", "prompt_only"):
                    raise ValueError(f"{item['id']}群像selection_basis须为reusable/core_continuity/prompt_only")
                required = review.get("requires_reference")
                if type(required) is not bool:
                    raise ValueError(f"{item['id']}群像须明确requires_reference布尔值")
                if mode == "image_asset":
                    if basis not in ("reusable", "core_continuity") or not required:
                        raise ValueError(f"{item['id']}群像图片须选择reusable或core_continuity且requires_reference=true")
                    if basis == "core_continuity":
                        nonempty(review.get("story_role"), f"{item['id']}原文明示的群像剧情作用")
                elif basis != "prompt_only" or required:
                    raise ValueError(f"{item['id']}仅文字群像须选择prompt_only且requires_reference=false")
                else:
                    nonempty(review.get("text_sufficiency_reason"), f"{item['id']}群像文字描述足够维持效果的具体理由")
            if mode == "image_asset":
                nonempty(review.get("consistency_reason"), f"{item['id']}群像外观一致性理由")
            else:
                nonempty(review.get("exclusion_reason"), f"{item['id']}群像不提取图片的依据")


def group_minimum_visible_uses(data, item):
    if item.get("visual_mode") != "image_asset":
        return 1
    if (data.get("group_policy") == GROUP_POLICY
            and item.get("extraction_review", {}).get("selection_basis") == "core_continuity"):
        return 1
    return 2


def validate_group_policy(data, paragraphs, fact_validator, script=None):
    validate_group_modes(data)
    scenes = {scene["id"]: scene for scene in data["scenes"]}
    for item in data["characters"]:
        if not is_group(item) or (data.get("group_policy") is None and "visual_mode" not in item):
            continue
        entry = item.get("source_entry", item["name"])
        speaker = entry.split("-群像-", 1)[0]
        if script is not None:
            # Speaker lines are a computable exclusion; other identity semantics
            # remain the author's full-script review, backed by source evidence.
            pattern = re.compile(r"^\s*" + re.escape(speaker) + r"(?:-群像)?(?:\s*(?:OS|VO|O\.S\.|V\.O\.))?(?:（[^\n]*?）|\([^\n]*?\))?\s*[:：]", re.M)
            if any(pattern.search(scene["body_text"]) for scene in script):
                raise ValueError(f"{item['id']}群像称谓具有独立台词，须按个体人物核实而非背景群像筛选")
        validate_visible_uses(item, paragraphs, scenes, fact_validator, "character_ids",
                              group_minimum_visible_uses(data, item), "可见出场")


def apply_group_review(data, payload):
    """Apply an explicit author review after normalized CHAR identities are known."""
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        raise ValueError("群像审阅JSON须包含groups数组")
    result = copy.deepcopy(data)
    groups = {item["id"]: item for item in result["characters"] if is_group(item)}
    records = {}
    for record in payload["groups"]:
        if not isinstance(record, dict) or set(record) != {"character_id", "visual_mode", "extraction_review", "visible_uses"}:
            raise ValueError("群像审阅项须含character_id、visual_mode、extraction_review及visible_uses")
        aid = record["character_id"]
        if aid not in groups or aid in records:
            raise ValueError(f"群像审阅ID不存在、非群像或重复：{aid}")
        records[aid] = record
    if set(records) != set(groups):
        raise ValueError(f"全剧群像审阅须覆盖normalize后全部群像：缺少{sorted(set(groups) - set(records))}")
    policy = payload.get("group_policy", data.get("group_policy") or LEGACY_GROUP_POLICY)
    if policy not in (LEGACY_GROUP_POLICY, GROUP_POLICY):
        raise ValueError(f"不支持的群像提取政策：{policy}")
    result["group_policy"] = policy
    for aid, item in groups.items():
        item.update({key: copy.deepcopy(value) for key, value in records[aid].items() if key != "character_id"})
    validate_group_modes(result)
    return result


def validate_visible_uses(item, paragraphs, scenes, fact_validator, linked_field, minimum, label_text):
    label = item["id"]
    uses = item.get("visible_uses")
    if not isinstance(uses, list) or len(uses) < minimum:
        raise ValueError(f"{label}至少需要{minimum}次独立{label_text}，不以词频或对白提及次数计数")
    offsets, cursor = [], 0
    for paragraph in paragraphs:
        offsets.append(cursor)
        cursor += len("".join(paragraph.split()))
    events, spans = set(), []
    for use in uses:
        if not isinstance(use, dict):
            raise ValueError(f"{label}{label_text}须为对象")
        sid, event = use.get("scene_id"), nonempty(use.get("event_id"), f"{label}独立事件标识")
        scene = scenes.get(sid)
        if not scene or label not in scene[linked_field]:
            raise ValueError(f"{label}{label_text}场次不存在或未关联该人物/道具")
        key = (sid, event)
        if key in events:
            raise ValueError(f"{label}同一独立事件重复计数")
        events.add(key)
        refs = use.get("source_paragraphs")
        if not isinstance(refs, list) or not refs or any(type(ref) is not int for ref in refs):
            raise ValueError(f"{label}{label_text}缺少有效原文段落")
        if refs != list(range(min(refs), max(refs) + 1)):
            raise ValueError(f"{label}每次{label_text}证据段落须连续、按顺序且不重复")
        evidence = nonempty(use.get("evidence"), f"{label}{label_text}证据")
        fact_validator([{"text": label_text, "evidence": evidence, "source_paragraphs": refs,
                         "scene_ids": [sid], "attribution": label_text + "审阅"}],
                       paragraphs, scenes, label, sid)
        text = "".join("".join(paragraphs[ref - 1] for ref in refs).split())
        needle = "".join(evidence.split())
        positions = [match.start() for match in re.finditer(re.escape(needle), text)]
        if len(positions) != 1:
            raise ValueError(f"{label}{label_text}证据须唯一定位独立事件，请缩小段落或补全原句")
        start = offsets[refs[0] - 1] + positions[0]
        span = (start, start + len(needle))
        if any(span[0] < previous[1] and previous[0] < span[1] for previous in spans):
            raise ValueError(f"{label}同一原文事件证据重叠，不能重复拆分成2次使用/出场")
        spans.append(span)


def asset_facts(data, category, item):
    facts = item.get("visual_facts", [])
    if category == "scene_assets":
        location = next(loc for loc in data["locations"] if loc["id"] == item["location_id"])
        facts = location["visual_facts"] + facts
        if item["variant"] != "base":
            base = next(asset for asset in data["scene_assets"]
                        if asset["location_id"] == item["location_id"] and asset["variant"] == "base")
            facts = location["visual_facts"] + base["visual_facts"] + item["visual_facts"]
    return facts


def source_constraints(data, category, item):
    # Existing production_design is a legacy proposal, never a source fact.
    return list(dict.fromkeys(fact["text"].strip() for fact in asset_facts(data, category, item)))


def source_context_digest(data, category, item):
    linked = []
    for scene in data["scenes"]:
        appears = (item["id"] in scene["character_ids"] if category == "characters" else
                   item["id"] in scene["prop_ids"] if category == "props" else
                   scene.get("scene_asset_id") == item["id"])
        if appears:
            linked.append({field: copy.deepcopy(scene.get(field)) for field in
                           ("id", "number", "heading", "source_range", "location_id", "scene_asset_id",
                            "character_ids", "prop_ids", "temporal_layer_id")})
    record = {"source_digest": data.get("source_digest"),
              "category": category, "name": item["name"], "facts": asset_facts(data, category, item),
              "scene_mapping": linked, "source_entry": item.get("source_entry"),
              "source_name": item.get("source_name"), "appearance": item.get("appearance"),
              "visible_uses": item.get("visible_uses"), "variant": item.get("variant"),
              "parent_id": item.get("parent_id"), "location_id": item.get("location_id")}
    # Preserve legacy digests exactly; new reviewed groups bind the extraction
    # decision as well as the source facts to their compiled image prompt.
    if "visual_mode" in item:
        record["visual_mode"] = item["visual_mode"]
        record["extraction_review"] = item.get("extraction_review")
        if is_group(item) and data.get("group_policy") == GROUP_POLICY:
            record["group_policy"] = GROUP_POLICY
    if data.get("asset_policy") == PROP_POLICY:
        record["prop_policy"] = PROP_POLICY
        record["prop_coverage"] = [candidate for candidate in
                                   data.get("prop_coverage_review", {}).get("candidates", [])
                                   if candidate.get("target_id") == item["id"]]
        if category == "props":
            record["extraction_review"] = item.get("extraction_review")
    return fingerprint(record)


def validate_prop_coverage(data, paragraphs=None, fact_validator=None):
    """Check an author's reverse audit; do not claim to discover props from prose.

    Without source text this validates completeness, links and review freshness.
    Ledger validation additionally verifies every quoted source occurrence.
    """
    if data.get("asset_policy") != PROP_POLICY:
        return
    review = data.get("prop_coverage_review")
    if not isinstance(review, dict) or review.get("reviewed_full_script") is not True:
        raise ValueError("道具反向覆盖审阅未完成：须逐场审阅全部原文，空props也不能跳过")
    if not data.get("source_digest") or review.get("source_digest") != data["source_digest"]:
        raise ValueError("道具反向覆盖审阅与原文版本不匹配，须重新审阅")
    scenes = {scene["id"]: scene for scene in data["scenes"]}
    reviewed = review.get("reviewed_scene_ids")
    if (not isinstance(reviewed, list) or any(not isinstance(sid, str) for sid in reviewed)
            or len(reviewed) != len(set(reviewed)) or set(reviewed) != set(scenes)):
        raise ValueError("道具反向覆盖审阅须声明全部场次且不得重复")
    candidates = review.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("道具反向覆盖审阅缺少candidates数组")
    categories = {"asset": {item["id"]: item for item in data["props"]},
                  "character": {item["id"]: item for item in data["characters"]},
                  "set": {item["id"]: item for item in data.get("scene_assets", [])}}
    seen, asset_coverage = set(), {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("道具候选审阅须为对象")
        cid = nonempty(candidate.get("candidate_id"), "道具候选稳定candidate_id")
        if cid in seen:
            raise ValueError(f"重复道具候选ID：{cid}")
        seen.add(cid)
        nonempty(candidate.get("name"), f"{cid}候选名称")
        aliases = candidate.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise ValueError(f"{cid}候选aliases须为非空文字数组")
        nonempty(candidate.get("reason"), f"{cid}具体归类理由")
        sids = candidate.get("scene_ids")
        if (not isinstance(sids, list) or not sids or any(not isinstance(sid, str) for sid in sids)
                or len(sids) != len(set(sids)) or any(sid not in scenes for sid in sids)):
            raise ValueError(f"{cid}候选场次无效")
        if type(candidate.get("requires_reference")) is not bool:
            raise ValueError(f"{cid}须明确requires_reference布尔值")
        disposition, target = candidate.get("disposition"), candidate.get("target_id")
        if disposition == "prompt_only":
            if target is not None:
                raise ValueError(f"{cid}普通描述不能指定资产target_id")
            if candidate["requires_reference"]:
                raise ValueError(f"{cid}需要视觉参考的核心物件不能归入prompt_only")
        elif disposition in categories:
            if not isinstance(target, str) or target not in categories[disposition]:
                raise ValueError(f"{cid}归类目标不存在或类型错误")
            if categories[disposition][target].get("visual_mode") == "prompt_only":
                raise ValueError(f"{cid}归类目标不具备图片参考")
            for sid in sids:
                scene = scenes[sid]
                linked = (target in scene["prop_ids"] if disposition == "asset" else
                          target in scene["character_ids"] if disposition == "character" else
                          target == scene.get("scene_asset_id"))
                if not linked:
                    raise ValueError(f"{cid}归类目标未关联候选场次{sid}")
            if disposition == "asset":
                if not candidate["requires_reference"]:
                    raise ValueError(f"{cid}独立道具资产须明确需要视觉参考")
                asset_coverage.setdefault(target, set()).update(sids)
        else:
            raise ValueError(f"{cid}disposition须为asset/character/set/prompt_only")
        evidence = candidate.get("source_evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"{cid}须保存可定位的source_evidence")
        evidence_scenes = set()
        for occurrence in evidence:
            if not isinstance(occurrence, dict) or occurrence.get("scene_id") not in sids:
                raise ValueError(f"{cid}原文证据场次不属于候选场次")
            sid = occurrence["scene_id"]
            evidence_scenes.add(sid)
            quote = nonempty(occurrence.get("evidence"), f"{cid}原文证据")
            refs = occurrence.get("source_paragraphs")
            if (not isinstance(refs, list) or not refs or any(type(ref) is not int for ref in refs)
                    or refs != list(range(min(refs), max(refs) + 1))
                    or any(ref < scenes[sid]["source_range"][0] or ref > scenes[sid]["source_range"][1]
                           for ref in refs)):
                raise ValueError(f"{cid}原文证据须为对应场次内连续、顺序且不重复的段落")
            if paragraphs is not None:
                if fact_validator is None:
                    raise ValueError("验证道具候选原文需要fact_validator")
                fact_validator([{"text": candidate["name"], "evidence": quote,
                                 "source_paragraphs": refs, "scene_ids": [sid],
                                 "attribution": "道具覆盖审阅"}], paragraphs, scenes, cid, sid)
                text = "".join("".join(paragraphs[ref - 1] for ref in refs).split())
                if len(list(re.finditer(re.escape("".join(quote.split())), text))) != 1:
                    raise ValueError(f"{cid}原文证据须唯一定位，请缩小段落或补全原句")
        if evidence_scenes != set(sids):
            raise ValueError(f"{cid}原文证据未覆盖全部候选场次")
    for aid in categories["asset"]:
        linked = {sid for sid, scene in scenes.items() if aid in scene["prop_ids"]}
        if not linked or asset_coverage.get(aid) != linked:
            raise ValueError(f"{aid}须有覆盖全部关联场次的独立资产候选审阅")


def validate_prop_policy(data, paragraphs, fact_validator):
    policy = data.get("asset_policy")
    if policy is None:
        return  # Existing ledgers keep their policy until explicitly reviewed.
    if policy not in (LEGACY_PROP_POLICY, PROP_POLICY):
        raise ValueError(f"不支持的道具提取政策：{policy}")
    scenes = {scene["id"]: scene for scene in data["scenes"]}
    for item in data["props"]:
        label = item["id"]
        review = item.get("extraction_review")
        allowed = ("special_reusable", "core_continuity") if policy == PROP_POLICY else ("special_reusable",)
        if not isinstance(review, dict) or review.get("kind") not in allowed:
            if policy == PROP_POLICY:
                raise ValueError(f"{label}须按special_reusable或core_continuity完成主要道具入选审阅")
            raise ValueError(f"{label}须为需复用的特殊主要道具；普通日用品、家具、服装或临时物件不提取")
        nonempty(review.get("consistency_reason"), f"{label} 前后一致性理由")
        core = review["kind"] == "core_continuity"
        if core:
            nonempty(review.get("story_role"), f"{label}原文明示的关键动作或视觉反差作用")
        names = [item.get("source_name", item["name"]), *item.get("aliases", [])]
        if not core and any(FURNITURE.search(name) for name in names):
            raise ValueError(f"{label}桌床等固定大件家具归入SET，不单独提取")
        if not core and any(name in DAILY_ITEMS for name in names):
            nonempty(review.get("special_features"), f"{label}日用品须说明特殊可辨形制，普通日用品不提取")
        validate_visible_uses(item, paragraphs, scenes, fact_validator, "prop_ids", 1 if core else 2, "可见使用")
    validate_prop_coverage(data, paragraphs, fact_validator)


def validate_design_record(category, item, design, anchors):
    label = item["id"]
    if not isinstance(design, dict) or design.get("attribution") != "制作设计":
        raise ValueError(f"{label}视觉设计须独立标注制作设计")
    nonempty(design.get("basis"), f"{label}设计依据")
    if design.get("reviewed_source_facts") is not True:
        raise ValueError(f"{label}设计作者须审阅原文明示约束并声明 reviewed_source_facts=true")
    fields = {
        "characters": ("identity_anchor_id", "styling"),
        "scene_assets": ("space", "materials_palette", "furnishings", "lighting"),
        "props": ("structure", "materials_palette", "scale", "state"),
    }[category]
    for field in fields:
        nonempty(design.get(field), f"{label}.{field}")
    if category == "characters":
        if design["identity_anchor_id"] not in anchors:
            raise ValueError(f"{label}缺少共享人物身份锚点")
        overrides = design.get("appearance_overrides", {})
        if not isinstance(overrides, dict) or set(overrides) - {"age", "body", "face", "hair"}:
            raise ValueError(f"{label}人物版本覆盖只允许age/body/face/hair")
        for field, value in overrides.items():
            nonempty(value, f"{label}版本覆盖{field}")
        differences = design.get("variant_differences", [])
        if not isinstance(differences, list) or any(not isinstance(value, str) or not value.strip() for value in differences):
            raise ValueError(f"{label}variant_differences须为非空文字数组")
        if overrides and not differences:
            raise ValueError(f"{label}覆盖身份外观时须登记已有版本的明确差异；不能新增角色版本")
        changes = design.get("override_source_facts", {})
        if not isinstance(changes, dict):
            raise ValueError(f"{label}override_source_facts须为字段到原文事实text的对象")
        for field in ("face", "body"):
            if field in overrides and changes.get(field) not in [fact["text"] for fact in item.get("visual_facts", [])]:
                raise ValueError(f"{label}覆盖{field}须指定已验证原文事实作为变化依据，不因换装改脸或改体型")


def identity_map(data):
    values = data.get("identity_anchors", [])
    if not isinstance(values, list):
        raise ValueError("identity_anchors须为数组")
    anchors = {}
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("人物身份锚点须为对象")
        for field in ("id", "age", "body", "face", "hair"):
            nonempty(item.get(field), f"人物身份锚点.{field}")
        if item["id"] in anchors:
            raise ValueError(f"重复人物身份锚点：{item['id']}")
        anchors[item["id"]] = item
    return anchors


def validate_shared_identity(data):
    identities, anchor_owners = {}, {}
    for item in visual_assets(data, "characters"):
        appearance = item.get("appearance")
        if not appearance:
            continue  # No string splitting to guess the identity of old/simple casts.
        key = (appearance["identity"], appearance["incarnation"])
        anchor = item["visual_design"]["identity_anchor_id"]
        if key in identities and identities[key] != anchor:
            raise ValueError(f"{item['id']}相同身份与世次的形象须共享identity_anchor_id")
        if anchor in anchor_owners and anchor_owners[anchor] != key:
            raise ValueError(f"{item['id']}不同身份或世次的实际身体不能共享identity_anchor_id")
        identities[key] = anchor
        anchor_owners[anchor] = key


def compile_prompt(data, category, item, design, anchors):
    style = nonempty(data["visual_style"].get("text"), "视觉风格")
    facts = source_constraints(data, category, item)
    hard = ("必须保持的可见特征：" + "；".join(facts) + "。这些特征优先于以下补充描述。") if facts else ""
    style_end = "" if style.endswith(("。", ".", "！", "!", "？", "?")) else "。"
    common = f"统一视觉风格：{style}{style_end}{hard}"
    if category == "characters":
        anchor = dict(anchors[design["identity_anchor_id"]])
        anchor.update(design.get("appearance_overrides", {}))
        description = f"年龄与性别感：{anchor['age']}；体型与轮廓：{anchor['body']}；五官：{anchor['face']}；发型：{anchor['hair']}；服装与配饰：{design['styling']}。"
        differences = design.get("variant_differences", [])
        if differences:
            description += "本版本差异：" + "；".join(differences) + "；除此之外保持共享身份锚点一致。"
        prompt = common + CHARACTER_LAYOUT + description + "中性自然表情，自然静态站姿，柔和均匀棚拍光，五官清晰，服装剪裁可信，材质层次明确。无剧情动作、额外手持物、其他角色、说明文字、标签、水印或签名；必须保持的可见特征优先。"
        form = {"model": "gpt-image-2", "aspect_ratio": "16:9", "background": "pure_white",
                "layout": "left_1/3_large_front_face_right_2/3_front_side90_back_full_body"}
    elif category == "scene_assets":
        prompt = common + f"场景资产主视图：{item['name']}。空间与稳定相对位置：{design['space']}；材质与配色：{design['materials_palette']}；固定陈设：{design['furnishings']}；基础受光：{design['lighting']}。"
        prompt += "单幅清晰广角建立镜头，平视或微俯，前中后层次有秩序，出入口和主要活动区清楚，保留人物行动通道，空间结构自洽。完全无人物、人影或故事动物，无剧情动作、说明文字、水印或签名。"
        form = {"model": "gpt-image-2", "layout": "single_environment_master_view", "subjects": "no_characters"}
    else:
        prompt = common + f"单品道具资产：{item['name']}。形制与关键结构：{design['structure']}；材质与颜色：{design['materials_palette']}；尺寸关系：{design['scale']}；当前状态：{design['state']}。"
        prompt += "标准产品摄影视角，立体物件微俯3/4视角，扁平物件正俯平铺。单件居中完整入镜，四周留白，比例准确，结构美观清晰，纯净白色背景，柔和均匀棚拍光，轻淡阴影。无人物、手部、其他物品、展示架、剧情环境、额外文字、水印或签名；保留原文明示的物件本体文字或图案。"
        form = {"model": "gpt-image-2", "layout": "single_complete_product", "background": "pure_white"}
    return prompt, form


def validate_visual_design(data):
    validate_prop_coverage(data)
    validate_group_modes(data)
    for item in data["characters"]:
        if item.get("visual_mode") == "prompt_only" and any(field in item for field in ("visual_design", "image_prompt", "image_format", "image_design_digest")):
            raise ValueError(f"{item['id']}仅提示词群像不能保留生图派生字段；请显式重新design")
    version = data.get("image_design_version")
    has_records = any("visual_design" in item or "image_prompt" in item or "image_format" in item
                      for category in ASSET_CATEGORIES for item in data.get(category, []))
    if version is None and not has_records:
        return
    if version != DESIGN_VERSION or data.get("schema_version") != 3:
        raise ValueError("视觉设计须显式使用schema 3与gpt-image-2-v1；旧项目不自动迁移")
    style = data.get("visual_style")
    if not isinstance(style, dict) or style.get("digest") != fingerprint(style.get("text")):
        raise ValueError("视觉风格与摘要不匹配，请重新运行design")
    anchors = identity_map(data)
    for category in ASSET_CATEGORIES:
        for item in visual_assets(data, category):
            design = item.get("visual_design")
            validate_design_record(category, item, design, anchors)
            if item.get("image_design_digest") != fingerprint(design):
                raise ValueError(f"{item['id']}制作设计或审阅依据已改变，请重新运行design")
            facts = source_constraints(data, category, item)
            if design.get("source_fact_digest") != fingerprint(facts):
                raise ValueError(f"{item['id']}原文外观事实已改变，须审阅设计并重新运行design")
            if design.get("source_context_digest") != source_context_digest(data, category, item):
                raise ValueError(f"{item['id']}原文版本、出处或场次映射已改变，须审阅设计并重新运行design")
            prompt, form = compile_prompt(data, category, item, design, anchors)
            if item.get("image_prompt") != prompt or item.get("image_format") != form:
                raise ValueError(f"{item['id']}生图提示词或版式与台账设计不一致，请重新运行design")
    validate_shared_identity(data)


def apply_design(data, payload, style_text):
    validate_prop_coverage(data)
    validate_group_modes(data)
    if data.get("schema_version") != 3:
        raise ValueError("design仅用于已审阅schema 3；旧项目须显式升级，不自动迁移")
    if not isinstance(payload, dict) or not isinstance(payload.get("designs"), list):
        raise ValueError("设计JSON须包含designs数组及identity_anchors数组")
    style_text = style_text.strip()
    nonempty(style_text, "style-file视觉风格")
    result = copy.deepcopy(data)
    result["image_design_version"] = DESIGN_VERSION
    result["visual_style"] = {"text": style_text, "digest": fingerprint(style_text)}
    result["identity_anchors"] = copy.deepcopy(payload.get("identity_anchors", []))
    anchors = identity_map(result)
    designs = {}
    for record in payload["designs"]:
        if not isinstance(record, dict):
            raise ValueError("每条设计须为对象")
        aid = nonempty(record.get("asset_id"), "设计asset_id")
        if aid in designs:
            raise ValueError(f"重复资产设计：{aid}")
        designs[aid] = {key: copy.deepcopy(value) for key, value in record.items() if key != "asset_id"}
    expected = {item["id"] for category in ASSET_CATEGORIES for item in visual_assets(result, category)}
    if set(designs) != expected:
        raise ValueError(f"design须覆盖全部现用图片CHAR/SET/PROP，prompt_only群像不填设计；缺少{sorted(expected - set(designs))}，多余{sorted(set(designs) - expected)}")
    for item in result["characters"]:
        if item.get("visual_mode") == "prompt_only":
            for field in ("visual_design", "image_prompt", "image_format", "image_design_digest"):
                item.pop(field, None)
    for category in ASSET_CATEGORIES:
        for item in visual_assets(result, category):
            design = designs[item["id"]]
            validate_design_record(category, item, design, anchors)
            design["source_fact_digest"] = fingerprint(source_constraints(result, category, item))
            design["source_context_digest"] = source_context_digest(result, category, item)
            item["visual_design"] = design
            item["image_design_digest"] = fingerprint(design)
            item["image_prompt"], item["image_format"] = compile_prompt(result, category, item, design, anchors)
    validate_visual_design(result)
    return result
