"""Validate reviewed asset designs and compile reusable GPT-image-2 prompts.

This module never infers appearance, counts words, generates images, or changes
asset identity. Source facts and author supplied production designs stay apart.
"""

import copy
import hashlib
import json
import re

from character_variants import scene_character_ids, scene_appearance_tracks
import design_scope


LEGACY_PROP_POLICY = "visible-reuse-v1"
PROP_POLICY = "visual-continuity-v2"
LEGACY_GROUP_POLICY = "visible-reuse-v1"
GROUP_POLICY = "visual-continuity-v2"
LEGACY_DESIGN_VERSION = "gpt-image-2-v1"
DESIGN_VERSION = "gpt-image-2-v2"
DESIGN_VERSIONS = (LEGACY_DESIGN_VERSION, DESIGN_VERSION)
IMAGE_REFERENCE_POLICY = "asset-image-reference-v1"
ASSET_CATEGORIES = ("characters", "scene_assets", "props")
FURNITURE = re.compile(r"(?:桌|床|椅|柜|沙发|茶几|凳)(?:子|铺)?$")
DAILY_ITEMS = {"锅", "碗", "瓢", "盆", "锅碗瓢盆", "筷子", "餐具", "水杯", "杯子", "手机", "雨伞", "香烟", "茶杯", "盘子", "勺子"}
CHARACTER_LAYOUT = (
    "16:9横版角色参考图，纯白背景。最左侧超大正面面部特写占满画面1/3宽度并贯穿画面高度，平视前方，"
    "完整展示头部和清晰五官；右侧2/3依次并排正面全身、90度侧面全身、背面全身，"
    "三个全身视图等高，头顶和脚底对齐，头顶到脚底完整入镜，不裁切、不重叠。"
    "特写和三个全身视图属于同一角色、同一状态，脸部身份、体型、发型、服装和配色一致。"
)
ANIMAL_LAYOUT = (
    "16:9横版动物参考板，纯白背景。左侧1/3为正面头部特写，完整保留该物种真实头部、耳部和面部结构；"
    "右侧2/3依次并排自然姿态的正面完整身体、90度侧面完整身体、背面完整身体，"
    "三个身体视图比例相同，头部、全部肢体及尾部完整，不裁切、不重叠。"
    "四幅均为同一动物、同一体型、同一毛羽或皮肤状态及配色，按该物种真实解剖结构呈现，不人化。"
)
GROUP_LAYOUT = (
    "16:9横版群像参考板，纯白背景。按下列样本顺序从左到右并排呈现多个外貌各异的人物，"
    "每个样本只出现一次，均为单个完整全身正视图，平视前方，自然静态站姿；"
    "头顶至脚底完整入镜，人物彼此分开、不重叠、不遮挡，保留各自真实身高与体型比例。"
    "不绘面部特写，不绘同一人物的侧面或背面重复视图。"
    "样本数量仅用于展示造型差异，不规定剧情人数、队形、成员身份或各场同一批实体人物。"
)
MASKED_CHARACTER_LAYOUT = (
    "16:9横版角色参考图，纯白背景。最左侧1/3为当前佩戴状态的正面头部特写，"
    "完整展示遮挡物的外形和实际露出的部位；右侧2/3依次并排正面全身、90度侧面全身、背面全身，"
    "三个全身视图等高，头顶和脚底对齐，完整入镜、不裁切、不重叠。"
    "头部特写与三个全身视图均为同一角色、同一体型、同一服装及同一佩戴状态，"
    "遮挡物始终按设定佩戴，不摘下、不掀开、不透明化，不画被遮住的五官或头发。"
)


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{label}须为非空单行文字")
    return value.strip()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def reference_design_ids(item, design=None):
    """Ordered author dependencies, independent of node/media availability."""
    design = item.get("visual_design", {}) if design is None else design
    if not isinstance(design, dict):
        raise ValueError(f"{item['id']}图片设计须为对象")
    reference = design.get("image_reference")
    if reference is None:
        return []
    if (not isinstance(reference, dict) or not {"base_asset_id", "changes", "preserve"} <= set(reference)
            or set(reference) - {"base_asset_id", "changes", "preserve", "prop_asset_ids"}):
        raise ValueError(f"{item['id']} image_reference须含base_asset_id、changes、preserve")
    base_id = reference.get("base_asset_id")
    if not isinstance(base_id, str) or not re.fullmatch(r"CHAR\d{4,}", base_id) or base_id == item["id"]:
        raise ValueError(f"{item['id']}图片基础引用无效或自引用")
    for key in ("changes", "preserve"):
        values = reference.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError(f"{item['id']} image_reference.{key}须为非空文字数组")
        for value in values:
            nonempty(value, f"{item['id']} image_reference.{key}")
    variant = item.get("appearance_variant", {})
    if variant and base_id != variant.get("base_asset_id"):
        raise ValueError(f"{item['id']}图片基础引用必须与已审佩戴基础一致")
    props = reference.get("prop_asset_ids", [])
    if (not isinstance(props, list) or any(not isinstance(pid, str) or not re.fullmatch(r"PROP\d{4,}", pid) for pid in props)
            or len(props) != len(set(props))):
        raise ValueError(f"{item['id']} prop_asset_ids须为唯一PROP图片资产ID数组")
    ids = [base_id, *props]
    if variant.get("prop_asset_id") is not None:
        prop_id = variant["prop_asset_id"]
        if not isinstance(prop_id, str) or not re.fullmatch(r"PROP\d{4,}", prop_id):
            raise ValueError(f"{item['id']}关联道具引用无效")
        if prop_id not in ids:
            ids.append(prop_id)
    return ids


def image_reference_asset_ids(item):
    """Read only the compiled contract; old independent prompts stay old."""
    form = item.get("image_format", {})
    if not isinstance(form, dict):
        raise ValueError(f"{item['id']}图片引用编译合同须为对象")
    policy = form.get("reference_policy")
    if policy is None:
        if "reference_asset_ids" in form or item.get("visual_design", {}).get("image_reference") is not None:
            raise ValueError(f"{item['id']}图片引用编译合同缺失")
        return []
    if policy != IMAGE_REFERENCE_POLICY:
        raise ValueError(f"{item['id']}不支持的图片引用协议：{policy}")
    ids = reference_design_ids(item)
    if not ids or form.get("reference_asset_ids") != ids:
        raise ValueError(f"{item['id']}图片引用与已审设计依赖不一致")
    return ids


def prepare_image_reference(item, design, version):
    """Materialize only newly authored wearable references, never old renders."""
    variant = item.get("appearance_variant")
    if not variant or version != DESIGN_VERSION:
        return
    reference = copy.deepcopy(design.get("image_reference", {}))
    if reference and reference.get("base_asset_id") != variant.get("base_asset_id"):
        raise ValueError(f"{item['id']}图片基础引用必须与已审佩戴基础一致")
    covering = design.get("covering")
    # The existing wearable fields remain the sole owner of fit/coverage.
    changes = ([covering.get("fit"), covering.get("coverage")] if isinstance(covering, dict)
               else [covering])
    reference.update(base_asset_id=variant.get("base_asset_id"), changes=changes)
    reference.setdefault("preserve", ["基础图中未变化的身份、体型和衣装"])
    design["image_reference"] = reference


def validate_image_references(data, asset_ids=None):
    catalog = {item["id"]: (category, item) for category in ASSET_CATEGORIES
               for item in visual_assets(data, category)}
    selected = set(asset_ids) if asset_ids is not None else set(catalog)
    visiting, checked = set(), set()

    def visit(aid):
        if aid in visiting:
            raise ValueError(f"{aid}图片基础引用存在循环")
        if aid in checked:
            return
        visiting.add(aid)
        category, item = catalog[aid]
        value = item.get("visual_design", {})
        ids = reference_design_ids(item, value)
        if ids:
            if category != "characters" or is_group(item) or data.get("image_design_version") != DESIGN_VERSION:
                raise ValueError(f"{aid}图片派生仅用于v2独立角色")
            if ids[0] not in catalog or catalog[ids[0]][0] != "characters":
                raise ValueError(f"{aid}缺少现用基础角色图片资产：{ids[0]}")
            base = catalog[ids[0]][1]
            if is_group(base) or base.get("visual_design", {}).get("identity_anchor_id") != value.get("identity_anchor_id"):
                raise ValueError(f"{aid}图片基础必须为同一身体的独立角色锚点")
            own_body, base_body = item.get("appearance"), base.get("appearance")
            if own_body and base_body and any(own_body.get(k) != base_body.get(k) for k in ("identity", "incarnation")):
                raise ValueError(f"{aid}图片基础不可跨身份或世次的实际身体")
            if item.get("asset_type") != base.get("asset_type") or item.get("species") != base.get("species"):
                raise ValueError(f"{aid}图片基础不可跨身体类型或物种")
            visit(ids[0])
            for prop_id in ids[1:]:
                if prop_id not in catalog or catalog[prop_id][0] != "props":
                    raise ValueError(f"{aid}缺少关联道具图片资产：{prop_id}")
        visiting.remove(aid)
        checked.add(aid)

    for aid in sorted(selected):
        if aid in catalog:
            visit(aid)


def validate_authored_reference_roots(data, authored_ids):
    """A newly authored same-body family has one explicit image foundation."""
    if data.get("image_design_version") != DESIGN_VERSION:
        return
    families = {}
    for item in visual_assets(data, "characters"):
        value = item.get("visual_design", {})
        if not is_group(item) and value.get("identity_anchor_id"):
            families.setdefault(value["identity_anchor_id"], []).append(item)
    authored_ids = set(authored_ids)
    for family in families.values():
        if len(family) < 2 or not any(item["id"] in authored_ids for item in family):
            continue
        roots = [item["id"] for item in family if not reference_design_ids(item)]
        if len(roots) != 1:
            raise ValueError(f"同一身体多形象须明确一个基础图片，其余填写image_reference；独立基础：{roots}")


def image_reference_context(data, item, visiting=None):
    """Bind the actual author dependency chain without generated-hash order."""
    visiting = set() if visiting is None else set(visiting)
    if item["id"] in visiting:
        raise ValueError(f"{item['id']}图片基础引用存在循环")
    visiting.add(item["id"])
    catalog = {value["id"]: value for category in ASSET_CATEGORIES for value in data.get(category, [])}
    dependencies = []
    for aid in reference_design_ids(item):
        if aid not in catalog:
            raise ValueError(f"{item['id']}图片引用依赖不存在：{aid}")
        value = catalog[aid]
        author = {key: copy.deepcopy(child) for key, child in value.get("visual_design", {}).items()
                  if key not in ("source_fact_digest", "source_context_digest")}
        record = {key: copy.deepcopy(value.get(key)) for key in
                  ("id", "name", "appearance", "appearance_variant", "visual_facts", "asset_type", "species")}
        record["visual_design"] = author
        record["dependencies"] = image_reference_context(data, value, visiting)
        dependencies.append(record)
    return dependencies


def is_speaking_ensemble(item):
    return item.get("character_kind") == "speaking_ensemble"


def is_group(item):
    return is_speaking_ensemble(item) or "-群像-" in item.get("source_entry", item.get("name", ""))


def validate_speaking_ensemble(data, item):
    """An explicit collective speaker uses a group board, never background filtering."""
    label = item["id"]
    if item.get("visual_mode") != "image_asset":
        raise ValueError(f"{label}发声群体须保留图片参考，不能设prompt_only")
    if "-群像-" in item.get("source_entry", item.get("name", "")):
        raise ValueError(f"{label}发声群体须保留真实集合称谓，不登记为背景群像")
    if item.get("appearance_variant"):
        raise ValueError(f"{label}发声群体不能作为单人的佩戴形象")
    nonempty(item.get("source_speaker"), f"{label}原文集合声源称谓")
    review = item.get("ensemble_review")
    if not isinstance(review, dict) or review.get("kind") != "speaking_ensemble" or review.get("reviewed_full_script") is not True:
        raise ValueError(f"{label}发声群体须完成集合身份及共同发声审阅")
    if not data.get("source_digest") or review.get("source_digest") != data["source_digest"]:
        raise ValueError(f"{label}发声群体审阅与原文版本不匹配")
    for field in ("identity_basis", "speech_basis", "consistency_reason"):
        nonempty(review.get(field), f"{label}发声群体{field}")


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
            if category == "characters" and is_speaking_ensemble(item):
                validate_speaking_ensemble(data, item)
                continue
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
        if is_speaking_ensemble(item):
            validate_visible_uses(item, paragraphs, scenes, fact_validator, "character_ids", 1, "集合出场或共同发声")
            if script is not None:
                speaker = re.escape(item["source_speaker"])
                pattern = re.compile(r"^\s*" + speaker + r"(?:（[^\n]*?）|\([^\n]*?\))?\s*[:：]\S", re.M)
                numbers = {scene["number"] for scene in scenes.values() if item["id"] in scene["character_ids"]}
                if not any(scene["number"] in numbers and pattern.search(scene["body_text"]) for scene in script):
                    raise ValueError(f"{item['id']}发声群体未对应本组关联场次的集合台词标签")
            continue
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
    groups = {item["id"]: item for item in result["characters"] if is_group(item) and not is_speaking_ensemble(item)}
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


def linked_covering_prop(data, item):
    prop_id = item.get("appearance_variant", {}).get("prop_asset_id")
    if prop_id is None:
        return None
    prop = next((value for value in data.get("props", []) if value["id"] == prop_id), None)
    if prop is None or prop.get("visual_mode") == "prompt_only":
        raise ValueError(f"{item['id']}关联佩戴道具不存在或不是图片资产：{prop_id}")
    return prop


def asset_facts(data, category, item):
    facts = item.get("visual_facts", [])
    if category == "characters" and item.get("appearance_variant"):
        base = next((character for character in data["characters"]
                     if character["id"] == item["appearance_variant"].get("base_asset_id")), None)
        if base:
            facts = base.get("visual_facts", []) + facts
        prop = linked_covering_prop(data, item)
        if prop:
            facts = facts + prop.get("visual_facts", [])
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


def scene_source_digests(data, paragraphs):
    """Content identities for declared design dependencies, never review verdicts."""
    return {scene["id"]: fingerprint(paragraphs[scene["source_range"][0] - 1:scene["source_range"][1]])
            for scene in data["scenes"]}


def design_dependency_scenes(data, category, item):
    scope = item.get("visual_design", {}).get("source_scope")
    if scope is None:
        return None  # Preserve the exact conservative digest of existing designs.
    if not isinstance(scope, dict) or scope.get("kind") not in ("global", "scenes"):
        raise ValueError(f"{item['id']} source_scope.kind须为global或scenes")
    nonempty(scope.get("basis"), f"{item['id']}设计上下文范围依据")
    if scope["kind"] == "global":
        return None
    ids = scope.get("scene_ids")
    scenes = {scene["id"]: scene for scene in data["scenes"]}
    if (not isinstance(ids, list) or not ids or any(not isinstance(sid, str) or sid not in scenes for sid in ids)
            or len(ids) != len(set(ids))):
        raise ValueError(f"{item['id']}设计依赖场次须非空、唯一且存在")
    linked = {scene["id"] for scene in data["scenes"]
              if (item["id"] in scene_character_ids(scene) if category == "characters" else
                  item["id"] in scene["prop_ids"] if category == "props" else
                  scene.get("scene_asset_id") == item["id"])}
    # An author may add context scenes, but cannot omit the asset's own scenes.
    if not linked.issubset(ids):
        raise ValueError(f"{item['id']}设计依赖漏掉资产出现场次：{sorted(linked - set(ids))}")
    digests = data.get("design_source_scene_digests", {})
    if not isinstance(digests, dict) or any(not isinstance(digests.get(sid), str)
            or not re.fullmatch(r"[0-9a-f]{64}", digests[sid]) for sid in ids):
        raise ValueError(f"{item['id']}局部设计依赖缺少当前原文场次摘要；通过design命令建立")
    return sorted(ids)


def source_context_digest(data, category, item):
    linked = []
    for scene in data["scenes"]:
        appears = (item["id"] in scene_character_ids(scene) if category == "characters" else
                   item["id"] in scene["prop_ids"] if category == "props" else
                   scene.get("scene_asset_id") == item["id"])
        if appears:
            mapping = {field: copy.deepcopy(scene.get(field)) for field in
                       ("id", "number", "heading", "source_range", "location_id", "scene_asset_id",
                        "character_ids", "prop_ids", "temporal_layer_id")}
            if scene.get("character_appearances"):
                mapping["character_appearances"] = copy.deepcopy(scene_appearance_tracks(scene))
            linked.append(mapping)
    record = {"source_digest": data.get("source_digest"),
              "category": category, "name": item["name"], "facts": asset_facts(data, category, item),
              "scene_mapping": linked, "source_entry": item.get("source_entry"),
              "source_name": item.get("source_name"), "appearance": item.get("appearance"),
              "visible_uses": item.get("visible_uses"), "variant": item.get("variant"),
              "parent_id": item.get("parent_id"), "location_id": item.get("location_id")}
    if item.get("visual_design", {}).get("image_reference") is not None:
        record["image_reference_dependencies"] = image_reference_context(data, item)
    if "appearance_variant" in item:
        record["appearance_variant"] = copy.deepcopy(item["appearance_variant"])
        base = next((character for character in data["characters"]
                     if character["id"] == item["appearance_variant"].get("base_asset_id")), None)
        if base:
            record["appearance_base"] = {field: copy.deepcopy(base.get(field)) for field in
                                         ("id", "name", "source_entry", "source_speaker", "appearance", "visual_facts")}
        prop = linked_covering_prop(data, item)
        if prop:
            # Bind author inputs rather than derived hashes so a freshly applied
            # shared design is independent of CHAR/PROP compilation order.
            prop_design = prop.get("visual_design", {})
            record["covering_prop"] = {
                field: copy.deepcopy(prop.get(field)) for field in
                ("id", "name", "source_name", "visual_facts", "extraction_review", "visible_uses")}
            record["covering_prop"]["visual_design"] = {
                key: copy.deepcopy(value) for key, value in prop_design.items()
                if key not in ("source_fact_digest", "source_context_digest")}
    # Preserve legacy digests exactly; new reviewed groups bind the extraction
    # decision as well as the source facts to their compiled image prompt.
    if "visual_mode" in item:
        record["visual_mode"] = item["visual_mode"]
        record["extraction_review"] = item.get("extraction_review")
        if is_group(item) and data.get("group_policy") == GROUP_POLICY:
            record["group_policy"] = GROUP_POLICY
    if is_speaking_ensemble(item):
        record["character_kind"] = "speaking_ensemble"
        record["source_speaker"] = item.get("source_speaker")
        record["ensemble_review"] = item.get("ensemble_review")
    if item.get("asset_type") == "animal" and data.get("image_design_version") == DESIGN_VERSION:
        record["asset_type"] = "animal"
        record["species"] = item.get("species")
    if data.get("asset_policy") == PROP_POLICY:
        record["prop_policy"] = PROP_POLICY
        record["prop_coverage"] = [candidate for candidate in
                                   data.get("prop_coverage_review", {}).get("candidates", [])
                                   if candidate.get("target_id") == item["id"]]
        if category == "props":
            record["extraction_review"] = item.get("extraction_review")
    dependencies = design_dependency_scenes(data, category, item)
    if dependencies is not None:
        # Source/evidence validation remains mandatory at the ledger boundary.
        # Global review refreshes must not invalidate an unchanged local design.
        def local_context(value):
            if isinstance(value, dict):
                return {key: local_context(child) for key, child in value.items()
                        if key not in ("source_digest", "source_paragraphs", "source_range", "number")}
            if isinstance(value, list):
                return [local_context(child) for child in value]
            return value
        record = local_context(record)
        record["source_scope"] = item["visual_design"]["source_scope"]
        record["source_scenes"] = {sid: data["design_source_scene_digests"][sid] for sid in dependencies}
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


def validate_group_design(item, design):
    label = item["id"]
    if "identity_anchor_id" in design or design.get("appearance_overrides"):
        raise ValueError(f"{label}群像使用独立样本，不使用单个人物身份锚点或版本覆盖")
    wardrobe = design.get("group_wardrobe")
    if not isinstance(wardrobe, dict) or wardrobe.get("mode") not in ("varied", "uniform", "mixed"):
        raise ValueError(f"{label}group_wardrobe须说明varied/uniform/mixed服装策略")
    for field in ("basis", "era_region"):
        nonempty(wardrobe.get(field), f"{label}group_wardrobe.{field}")
    if wardrobe["mode"] in ("uniform", "mixed"):
        nonempty(wardrobe.get("shared_style"), f"{label}群体共同服制")
    members = design.get("group_members")
    if not isinstance(members, list) or len(members) < 2:
        raise ValueError(f"{label}群像参考板须至少2个外貌可辨的group_members样本；样本数不是剧情人数")
    identifiers, appearances, outfits = set(), set(), []
    for member in members:
        if not isinstance(member, dict):
            raise ValueError(f"{label}群像样本须为对象")
        if set(member) - {"sample_id", "age", "body", "face", "hair", "styling"}:
            raise ValueError(f"{label}群像样本只保存造型，不新增姓名、职业或剧情身份")
        for field in ("sample_id", "age", "body", "face", "hair"):
            nonempty(member.get(field), f"{label}群像样本.{field}")
        if member["sample_id"] in identifiers:
            raise ValueError(f"{label}群像sample_id重复")
        identifiers.add(member["sample_id"])
        appearance = tuple(member[field].strip() for field in ("body", "face", "hair"))
        if appearance in appearances:
            raise ValueError(f"{label}群像外貌样本重复；不能只改编号、年龄或服装冒充不同外貌")
        appearances.add(appearance)
        if wardrobe["mode"] == "uniform":
            if "styling" in member and member["styling"] != wardrobe["shared_style"]:
                raise ValueError(f"{label}统一服制的成员不可各写不同服装；有依据的等级差异改用mixed")
            outfit = wardrobe["shared_style"]
        else:
            outfit = nonempty(member.get("styling"), f"{label}群像成员服装")
        outfits.append(outfit)
    if wardrobe["mode"] == "varied" and len(set(outfits)) != len(outfits):
        raise ValueError(f"{label}各异服装策略须逐人设计不同搭配，统一或分组服制使用uniform/mixed")


def validate_design_record(category, item, design, anchors, version=DESIGN_VERSION):
    label = item["id"]
    if category == "characters" and item.get("asset_type") == "animal" and version == DESIGN_VERSION:
        nonempty(item.get("species"), f"{label}动物物种")
    if category == "characters" and is_speaking_ensemble(item) and version != DESIGN_VERSION:
        raise ValueError(f"{label}发声群体多人参考板须使用{DESIGN_VERSION}")
    if not isinstance(design, dict) or design.get("attribution") != "制作设计":
        raise ValueError(f"{label}视觉设计须独立标注制作设计")
    design_scope.validate_presentation(category, design, version)
    if "image_reference" in design:
        if category != "characters" or version != DESIGN_VERSION or is_group(item):
            raise ValueError(f"{label}图片派生仅用于v2独立角色")
        reference_design_ids(item, design)
    nonempty(design.get("basis"), f"{label}设计依据")
    if design.get("reviewed_source_facts") is not True:
        raise ValueError(f"{label}设计作者须审阅原文明示约束并声明 reviewed_source_facts=true")
    if version == DESIGN_VERSION and category == "characters" and is_group(item):
        validate_group_design(item, design)
        return
    fields = {
        "characters": ("identity_anchor_id", "styling"),
        "scene_assets": ("space", "materials_palette", "furnishings", "lighting"),
        "props": ("structure", "materials_palette", "scale", "state"),
    }[category]
    for field in fields:
        nonempty(design.get(field), f"{label}.{field}")
    if category == "props" and "product_layout" in design:
        if version != DESIGN_VERSION or design["product_layout"] != "multiple_objects":
            raise ValueError(f"{label}组合道具版式仅接受v2显式multiple_objects")
        nonempty(design.get("layout_basis"), f"{label}组合道具须说明原文依据与参考展示范围")
    if category == "characters":
        if design["identity_anchor_id"] not in anchors:
            raise ValueError(f"{label}缺少共享人物身份锚点")
        if item.get("appearance_variant") or "covering" in design:
            if version != DESIGN_VERSION:
                raise ValueError(f"{label}遮面造型须显式采用{DESIGN_VERSION}，不能落入旧版露面模板")
            covering = design.get("covering")
            if item.get("appearance_variant", {}).get("prop_asset_id") is not None:
                if not isinstance(covering, dict) or set(covering) != {"fit", "coverage"}:
                    raise ValueError(f"{label}已关联道具的covering仅填fit/coverage对象，形制色材由PROP统一继承")
                for field in ("fit", "coverage"):
                    nonempty(covering.get(field), f"{label}佩戴关系covering.{field}")
            else:
                nonempty(covering, f"{label}当前遮挡物形制与实际遮挡范围covering")
            nonempty(design.get("visible_identity"), f"{label}佩戴状态实际可见的身份特征visible_identity")
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
            if (field in overrides and not item.get("appearance_variant")
                    and changes.get(field) not in [fact["text"] for fact in item.get("visual_facts", [])]):
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
        if data.get("image_design_version") == DESIGN_VERSION and is_group(item):
            continue
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


def validate_variant_inheritance(data):
    characters = {item["id"]: item for item in data["characters"]}
    for item in visual_assets(data, "characters"):
        variant = item.get("appearance_variant")
        if not variant:
            continue
        base = characters.get(variant.get("base_asset_id"))
        if not base or base.get("appearance_variant") or is_group(base):
            raise ValueError(f"{item['id']}遮面造型须指向现用独立人物基础服装形象")
        for field in ("identity_anchor_id", "styling", "appearance_overrides", "override_source_facts"):
            if item["visual_design"].get(field) != base["visual_design"].get(field):
                raise ValueError(f"{item['id']}遮面造型须继承基础形象的{field}；不同服装须指向对应基础服装形象")


def compile_group_prompt(common, design):
    wardrobe = design["group_wardrobe"]
    description = f"时代、地区与身份约束：{wardrobe['era_region']}；服装设计依据：{wardrobe['basis']}。"
    if wardrobe["mode"] in ("uniform", "mixed"):
        description += f"共同服制：{wardrobe['shared_style']}。"
    for index, member in enumerate(design["group_members"], 1):
        outfit = wardrobe["shared_style"] if wardrobe["mode"] == "uniform" else member["styling"]
        description += (f"从左到右第{index}个造型样本：年龄与性别感{member['age']}；"
                        f"体型{member['body']}；面容{member['face']}；发型{member['hair']}；服装{outfit}。")
    prompt = common + GROUP_LAYOUT + description + "柔和均匀棚拍光，服装剪裁可信，材质层次明确。无剧情动作、额外手持物、说明文字、标签、水印或签名；原文限定的年龄、性别、服制与外貌范围优先，不增加人物身份。"
    form = {"model": "gpt-image-2", "aspect_ratio": "16:9", "background": "pure_white",
            "layout": "distinct_members_single_front_full_body", "sample_count": len(design["group_members"]),
            "sample_count_is_story_count": False, "wardrobe_mode": wardrobe["mode"]}
    return prompt, form


def compile_reference_prompt(data, item, design, style):
    ids = reference_design_ids(item, design)
    catalog = {value["id"]: value for category in ASSET_CATEGORIES for value in data.get(category, [])}
    base = catalog[ids[0]]
    style_end = "" if style.endswith(("。", "！", "？", ".", "!", "?")) else "。"
    reference = design["image_reference"]
    common = f"统一视觉风格：{style}{style_end}以 @{base['name']} 为基础图片，呈现同一角色本版变化完成后的形象。"
    if len(ids) > 1:
        refs = "、".join(f"@{catalog[aid]['name']} " for aid in ids[1:])
        relation = "佩戴" if design.get("covering") and len(ids) == 2 else "本版变化所用物件参考"
        common += f"{relation} {refs}，沿用所引用道具图片的形制、材质及配色。"
    common += "本版仅改变：" + "；".join(reference["changes"]) + "。保留：" + "；".join(reference["preserve"]) + "。"
    form = {"model": "gpt-image-2", "aspect_ratio": "16:9", "background": "pure_white",
            "reference_policy": IMAGE_REFERENCE_POLICY, "reference_asset_ids": ids}
    if design.get("covering"):
        layout = MASKED_CHARACTER_LAYOUT
        form.update(layout="left_1/3_covered_head_right_2/3_front_side90_back_full_body",
                    covering_state="worn_in_every_view")
        if item.get("appearance_variant", {}).get("prop_asset_id"):
            form["covering_prop_asset_id"] = item["appearance_variant"]["prop_asset_id"]
    elif item.get("asset_type") == "animal":
        layout = ANIMAL_LAYOUT
        form.update(layout="left_1/3_animal_head_right_2/3_front_side90_back_complete_body",
                    subject_kind="animal", species=item["species"])
    else:
        layout = CHARACTER_LAYOUT
        form["layout"] = "left_1/3_large_front_face_right_2/3_front_side90_back_full_body"
    prompt = common + layout + "各视图只呈现本版完成状态，保持未改变部分与基础图片一致。使用符合主体结构的自然静态姿态、柔和均匀棚拍光；不绘变化过程、前后对照、额外角色、说明文字、标签、水印或签名。"
    return prompt, form


def compile_prompt(data, category, item, design, anchors):
    style = nonempty(data["visual_style"].get("text"), "视觉风格")
    if category == "characters" and design.get("image_reference") is not None:
        validate_image_references(data, [item["id"]])
        return compile_reference_prompt(data, item, design, style)
    facts = source_constraints(data, category, item)
    hard = ("必须保持的可见特征：" + "；".join(facts) + "。这些特征优先于以下补充描述。") if facts else ""
    style_end = "" if style.endswith(("。", ".", "！", "!", "？", "?")) else "。"
    common = f"统一视觉风格：{style}{style_end}{hard}"
    if category == "characters":
        if data.get("image_design_version") == DESIGN_VERSION and is_group(item):
            common = f"统一视觉风格：{style}{style_end}"
            if facts:
                common += ("原文群体约束：" + "；".join(facts)
                           + "。外貌、年龄、性别及服制约束优先用于造型；原文人数、队形与动作属于剧情，"
                           "不换算成本参考板的样本数、队形或动作。")
            prompt, form = compile_group_prompt(common, design)
            if is_speaking_ensemble(item):
                form["subject_kind"] = "speaking_ensemble"
            return prompt, form
        anchor = dict(anchors[design["identity_anchor_id"]])
        anchor.update(design.get("appearance_overrides", {}))
        if data.get("image_design_version") == DESIGN_VERSION and design.get("covering"):
            # Underlying face/hair stay in the identity anchor, not instructions
            # to reveal hidden features in a currently covered reference board.
            common = f"统一视觉风格：{style}{style_end}"
            if facts:
                common += "原文约束：" + "；".join(facts) + "。仅呈现本佩戴状态实际可见的部分，被遮住的部位保持遮挡。"
            prop = linked_covering_prop(data, item)
            if prop:
                shared = prop["visual_design"]
                covering = (f"遮挡物统一采用道具资产{prop['name']}的已审阅设计：形制与结构{shared['structure']}；"
                            f"材质与颜色{shared['materials_palette']}；尺寸关系{shared['scale']}；状态{shared['state']}。"
                            f"本人物佩戴关系：{design['covering']['fit']}；实际遮挡范围：{design['covering']['coverage']}。"
                            "佩戴关系只决定贴合与遮挡，不另改道具形制、材质或配色；共享造型不代表共用同一个实物。")
            else:
                covering = f"当前遮挡物及遮挡范围：{design['covering']}。"
            description = (f"年龄与性别感：{anchor['age']}；体型与轮廓：{anchor['body']}；"
                           f"继承服装与配饰：{design['styling']}；{covering}"
                           f"当前可见身份特征：{design['visible_identity']}。"
                           "被遮住的真实五官、头发继续使用同一身份锚点，不通过摘戴对照或透视展示。")
            prompt = common + MASKED_CHARACTER_LAYOUT + description + "自然静态站姿，柔和均匀棚拍光，遮挡物结构与材质清晰，服装剪裁可信。无剧情动作、额外手持物、其他角色、说明文字、标签、水印或签名。"
            form = {"model": "gpt-image-2", "aspect_ratio": "16:9", "background": "pure_white",
                    "layout": "left_1/3_covered_head_right_2/3_front_side90_back_full_body",
                    "covering_state": "worn_in_every_view"}
            if prop:
                form["covering_prop_asset_id"] = prop["id"]
            return prompt, form
        if data.get("image_design_version") == DESIGN_VERSION and item.get("asset_type") == "animal":
            species = nonempty(item.get("species"), f"{item['id']}动物物种")
            description = (f"物种：{species}；年龄与性别感：{anchor['age']}；"
                           f"真实体态与肢体结构：{anchor['body']}；头部特征：{anchor['face']}；"
                           f"毛羽或皮肤：{anchor['hair']}；已审阅的服饰或配件状态：{design['styling']}。")
            prompt = common + ANIMAL_LAYOUT + description + "保持物种自然静态姿态，柔和均匀棚拍光，结构和表面细节清晰。不增加未指定的衣物、人类肢体、其他角色、文字标签、水印或签名。"
            return prompt, {"model": "gpt-image-2", "aspect_ratio": "16:9", "background": "pure_white",
                            "layout": "left_1/3_animal_head_right_2/3_front_side90_back_complete_body",
                            "subject_kind": "animal", "species": species}
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
        view = design.get("image_presentation", {}).get("view")
        presentation = design_scope.PROP_VIEWS[view] if view else ""
        if data.get("image_design_version") == DESIGN_VERSION and design.get("product_layout") == "multiple_objects":
            prompt = common + f"组合道具资产：{item['name']}。形制与关键结构：{design['structure']}；材质与颜色：{design['materials_palette']}；尺寸关系：{design['scale']}；当前状态：{design['state']}。"
            prompt += presentation + f"本参考图的组合依据与展示范围：{design['layout_basis']}。整组居中完整入镜，各实体独立、清楚分开、不重叠，不能合并成一个物体。原文明确的数量须保留；未明确的剧情总数不由图片样本数推定。四周留白，比例准确，纯净白色背景，柔和均匀棚拍光，轻淡阴影。无人物、手部、组合以外的额外物品、展示架、剧情环境、额外文字、水印或签名；保留原文明示的物件本体文字或图案。"
            form = {"model": "gpt-image-2", "layout": "multiple_complete_objects", "background": "pure_white"}
            if view:
                form["view"] = view
            return prompt, form
        prompt = common + f"单品道具资产：{item['name']}。形制与关键结构：{design['structure']}；材质与颜色：{design['materials_palette']}；尺寸关系：{design['scale']}；当前状态：{design['state']}。"
        prompt += (presentation or "标准产品摄影视角，立体物件微俯3/4视角，扁平物件正俯平铺。") + "单件居中完整入镜，四周留白，比例准确，结构美观清晰，纯净白色背景，柔和均匀棚拍光，轻淡阴影。无人物、手部、其他物品、展示架、剧情环境、额外文字、水印或签名；保留原文明示的物件本体文字或图案。"
        form = {"model": "gpt-image-2", "layout": "single_complete_product", "background": "pure_white"}
        if view:
            form["view"] = view
    return prompt, form


def validate_design_item(data, category, item, anchors, version):
    design = item.get("visual_design")
    validate_design_record(category, item, design, anchors, version)
    if item.get("image_design_digest") != fingerprint(design):
        raise ValueError(f"{item['id']}制作设计或审阅依据已改变，请重新运行design")
    if design.get("source_fact_digest") != fingerprint(source_constraints(data, category, item)):
        raise ValueError(f"{item['id']}原文外观事实已改变，须审阅设计并重新运行design")
    if design.get("source_context_digest") != source_context_digest(data, category, item):
        raise ValueError(f"{item['id']}原文版本、出处或场次映射已改变，须审阅设计并重新运行design")
    prompt, form = compile_prompt(data, category, item, design, anchors)
    if item.get("image_prompt") != prompt or item.get("image_format") != form:
        raise ValueError(f"{item['id']}生图提示词或版式与台账设计不一致，请重新运行design")
    image_reference_asset_ids(item)


def design_review_status(data):
    """Report exact stale design IDs separately from candidate review gates."""
    result = {"reusable_asset_ids": [], "review_required": [], "shared_errors": []}
    try:
        anchors = identity_map(data)
        version = data.get("image_design_version")
        if version not in DESIGN_VERSIONS:
            raise ValueError("制作设计版本缺失或不支持")
        style = data.get("visual_style")
        if not isinstance(style, dict) or style.get("digest") != fingerprint(style.get("text")):
            raise ValueError("视觉风格与摘要不匹配")
    except (ValueError, KeyError, TypeError) as exc:
        result["shared_errors"].append(str(exc))
        return result
    for category in ASSET_CATEGORIES:
        for item in visual_assets(data, category):
            try:
                validate_design_item(data, category, item, anchors, version)
                result["reusable_asset_ids"].append(item["id"])
            except (ValueError, KeyError, TypeError, StopIteration) as exc:
                result["review_required"].append({"asset_id": item["id"], "reason": str(exc)})
    for validator in (validate_shared_identity, validate_variant_inheritance, validate_image_references):
        try:
            validator(data)
        except (ValueError, KeyError, TypeError) as exc:
            result["shared_errors"].append(str(exc))
    return result


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
    if version not in DESIGN_VERSIONS or data.get("schema_version") != 3:
        raise ValueError("视觉设计须显式使用schema 3与支持的gpt-image-2版本；旧项目不自动迁移")
    style = data.get("visual_style")
    if not isinstance(style, dict) or style.get("digest") != fingerprint(style.get("text")):
        raise ValueError("视觉风格与摘要不匹配，请重新运行design")
    anchors = identity_map(data)
    for category in ASSET_CATEGORIES:
        for item in visual_assets(data, category):
            validate_design_item(data, category, item, anchors, version)
    validate_shared_identity(data)
    validate_variant_inheritance(data)
    validate_image_references(data)


def apply_design(data, payload, style_text, *, patch=False, paragraphs=None):
    validate_prop_coverage(data)
    validate_group_modes(data)
    if data.get("schema_version") != 3:
        raise ValueError("design仅用于已审阅schema 3；旧项目须显式升级，不自动迁移")
    if not isinstance(payload, dict) or not isinstance(payload.get("designs"), list):
        raise ValueError("设计JSON须包含designs数组及identity_anchors数组")
    style_text = style_text.strip()
    nonempty(style_text, "style-file视觉风格")
    result = copy.deepcopy(data)
    if paragraphs is not None:
        result["design_source_scene_digests"] = scene_source_digests(result, paragraphs)
    # A newly designed project uses v2. Explicitly designed v1 ledgers retain
    # their exact compiler unless the author asks for an explicit migration.
    version = payload.get("image_design_version", data.get("image_design_version", DESIGN_VERSION))
    if version not in DESIGN_VERSIONS:
        raise ValueError(f"不支持的image_design_version：{version}")
    if patch and (version != data.get("image_design_version")
                  or style_text != data.get("visual_style", {}).get("text")):
        raise ValueError("局部design不能同时切换风格或设计版本；请显式提交完整设计")
    result["image_design_version"] = version
    result["visual_style"] = {"text": style_text, "digest": fingerprint(style_text)}
    supplied_anchors = payload.get("identity_anchors", [])
    if not isinstance(supplied_anchors, list):
        raise ValueError("identity_anchors须为数组")
    if patch:
        existing_anchors = {anchor["id"]: copy.deepcopy(anchor) for anchor in data.get("identity_anchors", [])}
        supplied_ids = [anchor.get("id") for anchor in supplied_anchors if isinstance(anchor, dict)]
        if len(supplied_ids) != len(supplied_anchors) or any(not isinstance(aid, str) for aid in supplied_ids) or len(set(supplied_ids)) != len(supplied_ids):
            raise ValueError("局部设计identity_anchors存在无效或重复ID")
        existing_anchors.update({anchor["id"]: copy.deepcopy(anchor) for anchor in supplied_anchors})
        result["identity_anchors"] = list(existing_anchors.values())
    else:
        result["identity_anchors"] = copy.deepcopy(supplied_anchors)
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
    changed_ids = set(designs)
    reused = {}
    if patch:
        if not changed_ids or not changed_ids.issubset(expected):
            raise ValueError(f"局部design须指定至少一个现用图片资产；多余{sorted(changed_ids - expected)}")
        for category in ASSET_CATEGORIES:
            for item in visual_assets(result, category):
                if item["id"] not in changed_ids:
                    if not isinstance(item.get("visual_design"), dict):
                        raise ValueError(f"{item['id']}没有可复用设计；请加入本次designs")
                    reused[item["id"]] = copy.deepcopy(item)
                    designs[item["id"]] = copy.deepcopy(item["visual_design"])
    if set(designs) != expected:
        raise ValueError(f"design须覆盖全部现用图片CHAR/SET/PROP，prompt_only群像不填设计；缺少{sorted(expected - set(designs))}，多余{sorted(set(designs) - expected)}")
    characters = {item["id"]: item for item in result["characters"]}
    for item in visual_assets(result, "characters"):
        variant = item.get("appearance_variant")
        if not variant:
            continue
        base = characters.get(variant.get("base_asset_id"))
        if not base or base.get("appearance_variant") or base["id"] not in designs or is_group(base):
            raise ValueError(f"{item['id']}遮面造型须指向现用独立人物基础服装形象")
        current, base_design = designs[item["id"]], designs[base["id"]]
        for field in ("identity_anchor_id", "styling", "appearance_overrides", "override_source_facts"):
            if field in current and current[field] != base_design.get(field):
                raise ValueError(f"{item['id']}遮面造型须继承基础形象的{field}；不同服装须指向对应基础服装形象")
            if field in base_design:
                current[field] = copy.deepcopy(base_design[field])
        if base_design.get("appearance_overrides"):
            current.setdefault("variant_differences", copy.deepcopy(base_design.get("variant_differences", [])))
    for item in result["characters"]:
        if item.get("visual_mode") == "prompt_only":
            for field in ("visual_design", "image_prompt", "image_format", "image_design_digest"):
                item.pop(field, None)
    for category in ASSET_CATEGORIES:
        for item in visual_assets(result, category):
            design = designs[item["id"]]
            if item["id"] in changed_ids:
                prepare_image_reference(item, design, version)
            validate_design_record(category, item, design, anchors, version)
            item["visual_design"] = design
    # All author designs are installed before any dependent CHAR is compiled;
    # it must consume this payload's PROP design, never an old ledger snapshot.
    # New writes check physical fields and their inherited context. Historical
    # validate/render does not retrofit a new scope gate or rewrite old prompts.
    design_scope.check_context(result)
    validate_image_references(result)
    validate_authored_reference_roots(result, changed_ids)
    for category in ASSET_CATEGORIES:
        for item in visual_assets(result, category):
            design = item["visual_design"]
            if item["id"] in reused:
                old = reused[item["id"]]
                prompt, form = compile_prompt(result, category, item, design, anchors)
                if (old.get("image_design_digest") != fingerprint(design)
                        or design.get("source_fact_digest") != fingerprint(source_constraints(result, category, item))
                        or design.get("source_context_digest") != source_context_digest(result, category, item)
                        or old.get("image_prompt") != prompt or old.get("image_format") != form):
                    raise ValueError(f"{item['id']}复用设计的事实、上下文、身份或共享资产依赖已变化；请审阅并加入本次designs")
                # Keep the original record and hashes; never bless stale inputs.
                continue
            design["source_fact_digest"] = fingerprint(source_constraints(result, category, item))
            design["source_context_digest"] = source_context_digest(result, category, item)
            item["image_design_digest"] = fingerprint(design)
            item["image_prompt"], item["image_format"] = compile_prompt(result, category, item, design, anchors)
    validate_visual_design(result)
    return result
