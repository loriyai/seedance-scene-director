"""Early author-field checks; source facts and historical ledgers stay untouched.

This is a narrow lint for known scope leaks, not a semantic approval. Only
new authoring/preview entrypoints call it; old prompt validation keeps its exact
compiler. Product presentation is typed and never a wearable's appearance.
"""
import argparse
import json
import re
from pathlib import Path

POLICY = "asset-description-scope-v1"
PHYSICAL_FIELDS = {
    "characters": ("styling", "covering", "visible_identity", "appearance_overrides",
                   "variant_differences", "group_members", "group_wardrobe"),
    "scene_assets": ("space", "materials_palette", "furnishings", "lighting"),
    "props": ("structure", "materials_palette", "scale", "state"),
}
PROP_VIEWS = {
    "front": "正面平视展示关键结构。",
    "front_left_three_quarter": "左前方微俯三分之四视角展示关键结构。",
    "front_right_three_quarter": "右前方微俯三分之四视角展示关键结构。",
    "top": "正俯视展示完整表面。",
}
BACKEND_ID = re.compile(r"(?<![A-Za-z0-9])(?:CHAR|PROP|SET|LOC|SC)\d{4,}(?![A-Za-z0-9])")
# Match instructions rather than physical anatomy: '头部开口' and '单手握持'
# are valid dimensions. Less explicit semantic conflicts still need real review.
LAYOUT_LEAK = re.compile(
    r"本(?:参考)?图|本画面|(?:画面|图中)(?:只|仅|不|无|展示|呈现)|"
    r"(?:单件|整组|物体|道具)(?:居中|完整入镜)|纯(?:净)?白(?:色)?背景|"
    r"(?:不带|不画|不绘|无|禁止出现)(?:任何)?(?:人物|人头|手部|人手)|"
    r"(?:左|右)?前方[^；。]{0,12}(?:视角|角度)|"
    r"(?:产品摄影|棚拍|广角建立镜头|俯拍|特写|完整入镜|三视图|参考板)|"
    r"(?:3/4|三分之四|正面|侧面|背面|正俯|俯视)(?:视角|角度|展示)|"
    r"(?:不画|不绘|展示)(?:一个|一件|三件|多件)(?:同款|组合|样本|物体)"
)


def strings(value, path):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            if key != "sample_id":
                yield from strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from strings(child, f"{path}[{index}]")


def check_text(value, path, *, physical=True):
    for field, text in strings(value, path):
        hit = BACKEND_ID.search(text)
        if hit:
            raise ValueError(f"{field}含后台编号{hit.group()}；关系用ID字段保存，画面描述须自足")
        hit = LAYOUT_LEAK.search(text) if physical else None
        if hit:
            raise ValueError(f"{field}混入图片构图指令“{hit.group()}”；物理属性只写物体本身，"
                             "展示角度用image_presentation，样本依据用basis/layout_basis，固定版式由编译器负责")


def validate_presentation(category, record, version):
    if "image_presentation" not in record:
        return
    value = record["image_presentation"]
    if (category != "props" or version != "gpt-image-2-v2" or not isinstance(value, dict)
            or set(value) != {"view"} or not isinstance(value["view"], str) or value["view"] not in PROP_VIEWS):
        raise ValueError("image_presentation仅用于v2道具，须为合法view枚举；不接受自由构图文字")


def check_record(category, aid, record, version="gpt-image-2-v2"):
    validate_presentation(category, record, version)
    if version != "gpt-image-2-v2":
        return
    for field in PHYSICAL_FIELDS[category]:
        if field in record:
            check_text(record[field], f"{aid}.{field}")
    # Layout basis is rendered only for composite product images; IDs still
    # have no visual meaning, while legitimate composition text is allowed.
    if record.get("product_layout") == "multiple_objects":
        check_text(record.get("layout_basis", ""), f"{aid}.layout_basis", physical=False)


def check_payload(payload):
    """Draft lint works before a full design or identity group is complete."""
    if not isinstance(payload, dict) or not isinstance(payload.get("designs"), list):
        raise ValueError("设计草稿须包含designs数组")
    version = payload.get("image_design_version", "gpt-image-2-v2")
    if version not in ("gpt-image-2-v1", "gpt-image-2-v2"):
        raise ValueError("不支持的图片设计版本")
    seen = set()
    for record in payload["designs"]:
        if not isinstance(record, dict):
            raise ValueError("设计草稿条目须为对象")
        aid = record.get("asset_id")
        match = re.fullmatch(r"(CHAR|SET|PROP)\d{4,}", aid) if isinstance(aid, str) else None
        if not match or aid in seen:
            raise ValueError(f"设计草稿稳定ID无效或重复：{aid}")
        seen.add(aid)
        check_record({"CHAR": "characters", "SET": "scene_assets", "PROP": "props"}[match[1]], aid, record, version)
    if version == "gpt-image-2-v2":
        for anchor in payload.get("identity_anchors", []):
            if not isinstance(anchor, dict):
                raise ValueError("身份锚点须为对象")
            for field in ("age", "body", "face", "hair"):
                check_text(anchor.get(field, ""), f"anchor:{anchor.get('id')}.{field}")
    return {"policy": POLICY, "status": "known_scope_leaks_checked", "design_count": len(seen),
            "semantic_review": "not_performed", "formal_ready": False,
            "next_step": "在完整候选台账上design-check并实读依赖组预览"}


def check_context(data, asset_ids=None):
    """Inspect the actual inherited fields, including reused PROP dependencies."""
    version = data.get("image_design_version", "gpt-image-2-v2")
    selected = set(asset_ids) if asset_ids is not None else None
    for category in PHYSICAL_FIELDS:
        for item in data.get(category, []):
            if item.get("visual_mode") == "prompt_only" or selected is not None and item["id"] not in selected:
                continue
            record = item.get("visual_design", {})
            check_record(category, item["id"], record, version)
            if version == "gpt-image-2-v2":
                check_text(item["name"], f"{item['id']}.name", physical=False)
                if category == "characters" and record.get("identity_anchor_id"):
                    for anchor in data.get("identity_anchors", []):
                        if anchor.get("id") == record["identity_anchor_id"]:
                            for field in ("age", "body", "face", "hair"):
                                check_text(anchor.get(field, ""), f"anchor:{anchor['id']}.{field}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("design", type=Path)
    args = parser.parse_args()
    try:
        result = check_payload(json.loads(args.design.read_text(encoding="utf-8-sig")))
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
