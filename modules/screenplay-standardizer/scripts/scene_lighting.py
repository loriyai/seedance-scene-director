"""Reviewed lighting states of reusable sets, separate from spatial history.

Only explicitly opted-in ledgers use this contract. It neither infers daylight
from a formatting default nor edits a previously bound/compiled asset.
"""
import copy
import re

POLICY = "scene-lighting-v1"
STATES = {"day": "日景", "night": "夜景", "unspecified": "光照未明"}
FIELDS = ("structure_asset_id", "structure_name", "lighting_state", "image_reference_asset_id")


def enabled(data):
    value = data.get("scene_lighting_policy")
    if value is not None and value != POLICY:
        raise ValueError("不支持的场景光照策略")
    if value is None and any(a.get("variant") == "lighting" or any(k in a for k in FIELDS)
                             for a in data.get("scene_assets", [])):
        raise ValueError("场景光照版本须声明scene_lighting_policy")
    return value == POLICY


def scene_state(scene):
    basis = scene.get("time_basis", {})
    match = re.match(r"场景\d+[-：:]\s*(日|夜)[-·]", scene.get("heading", ""))
    actual = ({"日": "day", "夜": "night"}[match[1]]
              if match and basis.get("kind") == "原文明示" else "unspecified")
    choice = scene.get("scene_lighting")
    if choice is not None:
        if (not isinstance(choice, dict) or set(choice) != {"state", "kind", "basis"}
                or choice.get("state") not in STATES or choice.get("kind") != "制作选择"
                or not isinstance(choice.get("basis"), str) or not choice["basis"].strip()):
            raise ValueError(f"{scene['id']}光照制作选择须含state、kind=制作选择及具体basis")
        if actual != "unspecified" and choice["state"] != actual:
            raise ValueError(f"{scene['id']}光照制作选择与原文明示日夜冲突")
        actual = choice["state"]
    return actual


def structure_id(item):
    return item.get("structure_asset_id", item["id"])


def project_structures(data):
    """Project SC use onto spatial states for the existing temporal contract."""
    result = copy.deepcopy(data)
    result.pop("scene_lighting_policy", None)
    catalog = {a["id"]: a for a in result["scene_assets"]}
    for item in catalog.values():
        if item.get("variant") == "lighting":
            root = catalog.get(item.get("structure_asset_id"))
            if (not root or root.get("variant") == "lighting"
                    or root.get("location_id") != item.get("location_id")
                    or item.get("parent_id") != root["id"]):
                raise ValueError(f"{item['id']}光照版本须关联同LOC的准确结构资产")
            if item.get("visual_facts"):
                raise ValueError(f"{item['id']}固定结构事实须登记到structure_asset_id；逐场光照事实归SC")
        elif item.get("structure_asset_id", item["id"]) != item["id"]:
            raise ValueError(f"{item['id']}结构资产须以自身为structure_asset_id")
    for scene in result["scenes"]:
        asset = catalog.get(scene.get("scene_asset_id"))
        if asset:
            scene["scene_asset_id"] = structure_id(asset)
    result["scene_assets"] = [a for a in result["scene_assets"] if a.get("variant") != "lighting"]
    for asset in result["scene_assets"]:
        if asset.get("structure_name"):
            asset["name"] = asset["structure_name"]
        for key in FIELDS:
            asset.pop(key, None)
    return result


def build(data, paragraphs, build_structures):
    projected = project_structures(data)
    built = build_structures(projected, paragraphs)
    old = {a["id"]: a for a in data["scene_assets"]}
    existing = {}
    for item in old.values():
        if "lighting_state" in item:
            key = (structure_id(item), item["lighting_state"])
            if item["lighting_state"] not in STATES or key in existing:
                raise ValueError("同一结构与光照状态的SET重复或光照状态无效")
            existing[key] = item
    ids = set(old) | set(data.get("retired_ids", [])) | {a["id"] for a in built["scene_assets"]}
    number = max([int(a[3:]) for a in ids if re.fullmatch(r"SET\d{4,}", a)] + [0])
    needs = {}
    for scene in built["scenes"]:
        needs.setdefault(scene["scene_asset_id"], set()).add(scene_state(scene))
    assets, mapping = [], {}
    for root in built["scene_assets"]:
        required = needs.get(root["id"], set())
        # Historical base/temporal structure anchors can remain unused; their
        # lighting must not manufacture a day image dependency for another set.
        state = next((s for s in ("day", "night", "unspecified") if s in required), "unspecified")
        previous = old.get(root["id"], {})
        if previous.get("lighting_state") and previous["lighting_state"] != state:
            raise ValueError(f"{root['id']}光照基础已冻结；新增日夜需求须显式迁移，不改旧图片")
        if not previous.get("lighting_state") and any(k in previous for k in ("visual_design", "image_prompt")):
            raise ValueError(f"{root['id']}已有编译图片；启用光照策略须显式迁移")
        root_name = root["name"]
        current = copy.deepcopy(previous or root)
        current.update(structure_asset_id=root["id"], structure_name=root_name, lighting_state=state)
        if not previous.get("lighting_state"):
            current["name"] = root_name if state == "unspecified" else root_name + "-" + STATES[state]
        current.pop("image_reference_asset_id", None)
        assets.append(current)
        mapping[(root["id"], state)] = current["id"]
        for other in (s for s in ("day", "night", "unspecified") if s in required and s != state):
            item = copy.deepcopy(existing.get((root["id"], other)))
            if item is None:
                number += 1
                item = {"id": f"SET{number:04d}", "name": root_name + "-" + STATES[other],
                        "location_id": root["location_id"], "variant": "lighting",
                        "parent_id": root["id"], "structure_asset_id": root["id"],
                        "structure_name": root_name, "lighting_state": other, "visual_facts": []}
            expected_ref = current["id"] if other == "night" and state == "day" else None
            if "image_reference_asset_id" in item and item["image_reference_asset_id"] != expected_ref:
                raise ValueError(f"{item['id']}夜景图片依赖与同结构日景不一致")
            if expected_ref:
                item["image_reference_asset_id"] = expected_ref
            else:
                item.pop("image_reference_asset_id", None)
            assets.append(item)
            mapping[(root["id"], other)] = item["id"]
    if set(old) - {a["id"] for a in assets}:
        raise ValueError("已有光照版本不再使用；须明确审核归档，不静默删除")
    originals = {s["id"]: s for s in data["scenes"]}
    for scene in built["scenes"]:
        target = mapping[(scene["scene_asset_id"], scene_state(scene))]
        former = old.get(originals[scene["id"]].get("scene_asset_id"), {})
        if former.get("lighting_state") and former["id"] != target:
            raise ValueError(f"{scene['id']}光照关联已变化；须显式迁移，不覆盖原绑定")
        scene["scene_asset_id"] = target
    built["scene_assets"] = assets
    built["scene_lighting_policy"] = POLICY
    return built


def validate(data, paragraphs, build_structures):
    planned = build(data, paragraphs, build_structures)
    fields = ("id", "name", "variant", "location_id", "parent_id", *FIELDS)
    snapshot = lambda d: ([{k: a.get(k) for k in fields} for a in d["scene_assets"]],
                           [(s["id"], s.get("scene_asset_id")) for s in d["scenes"]])
    if snapshot(planned) != snapshot(data):
        raise ValueError("场景光照资产或引用未完整建立；先离线build-scenes并核实")
    return project_structures(data)
