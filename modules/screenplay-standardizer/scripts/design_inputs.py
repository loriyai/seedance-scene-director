"""Read-only, source-bound design input index; never approves facts or designs.

The index projects existing ledger IDs, facts and absorbed candidates. Authors
record interaction/visibility judgments separately, referring to its binding.
Checking recreates the projection rather than trusting editable derived hashes.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import asset_ledger as ledger
import asset_visual_design as design
import design_preview

POLICY = "asset-design-inputs-v1"
MODULE = Path(__file__).resolve().parents[1]
SKILL = MODULE.parents[1]
RULE_FILES = (
    MODULE / "references/asset-ledger.md",
    MODULE / "references/asset-decisions.md",
    MODULE / "references/observations.md",
    SKILL / "references/asset-image-design.md",
    SKILL / "references/asset-workflow.md",
    SKILL / "references/design-workspace.md",
    MODULE / "scripts/asset_ledger.py",
    MODULE / "scripts/asset_visual_design.py",
    MODULE / "scripts/design_preview.py",
    MODULE / "scripts/design_scope.py",
    Path(__file__).resolve(),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rule_hashes():
    return {str(path.relative_to(SKILL).as_posix()): file_hash(path) for path in RULE_FILES}


def project_inputs(data, shared, asset_ids):
    """For already source-validated callers; the CLI always validates all inputs."""
    catalog = design_preview._catalog(data)
    image_ids = {aid for aid, (category, item) in catalog.items()
                 if category in design.ASSET_CATEGORIES and item.get("visual_mode") != "prompt_only"}
    selected = sorted(image_ids) if asset_ids is None else asset_ids
    require(isinstance(selected, list) and selected and all(isinstance(x, str) for x in selected),
            "设计输入须选择非空图片资产范围")
    require(len(set(selected)) == len(selected) and set(selected) <= image_ids,
            "设计输入范围含重复、未知或非图片资产")
    require(isinstance(shared, dict) and isinstance(shared.get("designs", []), list),
            "共享设计须为含designs数组的对象")
    authored = {}
    for value in shared.get("designs", []):
        require(isinstance(value, dict), "共享设计项须为对象")
        aid = value.get("asset_id")
        require(isinstance(aid, str) and aid in image_ids and aid not in authored,
                "共享设计含未知或重复资产")
        authored[aid] = value
    anchors = {**design.identity_map(data), **design.identity_map(shared)}
    assignments = shared.get("identity_assignments", {})
    require(isinstance(assignments, dict) and set(assignments) <= image_ids,
            "共享身份分配包含未知资产")
    require(all(isinstance(value, str) and value in anchors for value in assignments.values()),
            "共享身份分配引用不存在的锚点")
    # Existing closure provides same-body, wearable, parent SET and LOC edges.
    needed, reasons = design_preview._closure(data, catalog, selected, authored)
    candidates = data.get("prop_coverage_review", {}).get("candidates", [])
    rows = []
    for aid in sorted(needed):
        category, item = catalog[aid]
        if category == "locations":
            continue
        variant = item.get("appearance_variant", {})
        base = variant.get("base_asset_id")
        targets = {aid} | ({base} if base else set())
        current_design = authored.get(aid, item.get("visual_design", {}))
        anchor = assignments.get(aid, current_design.get("identity_anchor_id"))
        facts = copy.deepcopy(design.asset_facts(data, category, item))
        absorbed = [{"candidate_id": candidate["id"],
                     "inherited_from_base": candidate.get("target_id") == base,
                     "record": copy.deepcopy(candidate)}
                    for candidate in candidates if candidate.get("target_id") in targets]
        scene_ids = [scene["id"] for scene in data["scenes"]
                     if (aid in design.scene_character_ids(scene) if category == "characters" else
                         aid in scene["prop_ids"] if category == "props" else
                         scene.get("scene_asset_id") == aid)]
        row = {
            "asset_id": aid, "category": category, "name": item["name"],
            "requested": aid in selected,
            "source_fact_sha256": design.fingerprint(facts),
            "source_context_sha256": design.source_context_digest(data, category, item),
            "facts": facts, "absorbed_candidates": absorbed,
            "scene_refs": [{"scene_id": scene["id"], "source_range": scene["source_range"]}
                           for scene in data["scenes"] if scene["id"] in scene_ids],
            "inheritance": {"base_asset_id": base, "prop_asset_id": variant.get("prop_asset_id"),
                            "location_id": item.get("location_id"), "parent_id": item.get("parent_id"),
                            "identity_anchor_id": anchor},
            "shared_design_sha256": design.fingerprint(current_design) if current_design else None,
            "shared_anchor_sha256": design.fingerprint(anchors[anchor]) if anchor in anchors else None,
            "review_required": ["facts", "interaction_requirements", "current_visible_scope", "inheritance"],
        }
        rows.append(row)
    return {"requested_asset_ids": sorted(selected), "dependency_asset_ids": sorted(needed),
            "dependency_reasons": reasons, "items": rows}


def prepare(script, source, ledger_path, style, asset_ids=None, shared_design=None):
    paths = {"script": Path(script).resolve(), "source": Path(source).resolve(),
             "ledger": Path(ledger_path).resolve(), "style": Path(style).resolve()}
    if shared_design is not None:
        paths["shared_design"] = Path(shared_design).resolve()
    contents = {key: path.read_bytes() for key, path in paths.items()}
    hashes = {key: hashlib.sha256(value).hexdigest() for key, value in contents.items()}
    style_text = contents["style"].decode("utf-8-sig").strip()
    require(style_text and "\n" not in style_text and "\r" not in style_text, "风格须为非空单行")
    data = json.loads(contents["ledger"].decode("utf-8-sig"))
    shared = json.loads(contents["shared_design"].decode("utf-8-sig")) if "shared_design" in contents else {}
    source_data = copy.deepcopy(data)
    for field in ("image_design_version", "visual_style", "identity_anchors"):
        source_data.pop(field, None)
    for category in design.ASSET_CATEGORIES:
        for item in source_data.get(category, []):
            for field in design_preview.DERIVED:
                item.pop(field, None)
    # Validate the full inventory, not a reduced subset that could hide issues.
    ledger.validate(ledger.parse_script(paths["script"]), ledger.source_paragraphs(paths["source"]), source_data)
    rules = rule_hashes()
    result = {"policy": POLICY, "status": "input_index", "formal_ready": False,
              "bindable": False, "semantic_review": "not_performed",
              "input_paths": {key: str(path) for key, path in paths.items()},
              "input_sha256": hashes, "rule_sha256": rules,
              **project_inputs(data, shared, asset_ids)}
    require(all(file_hash(path) == hashes[key] for key, path in paths.items()) and rule_hashes() == rules,
            "生成索引期间输入或规则变化，须重建")
    result["binding_sha256"] = design.fingerprint(result)
    return result


def check(index):
    require(isinstance(index, dict) and index.get("policy") == POLICY, "不是当前设计输入索引")
    paths = index.get("input_paths", {})
    require(isinstance(paths, dict) and set(paths) in
            ({"script", "source", "ledger", "style"}, {"script", "source", "ledger", "style", "shared_design"}),
            "设计输入路径集合无效")
    require(all(isinstance(p, str) and Path(p).is_absolute() for p in paths.values()), "设计输入须为绝对路径")
    require(index.get("input_sha256") == {key: file_hash(Path(path)) for key, path in paths.items()}
            and index.get("rule_sha256") == rule_hashes(), "设计输入或规则已变化；旧索引及相关判断须重新核验")
    current = prepare(paths["script"], paths["source"], paths["ledger"], paths["style"],
                      index.get("requested_asset_ids"), paths.get("shared_design"))
    require(current == index, "设计输入索引内容被修改；请从当前台账重建，不手改投影或摘要")
    return {"status": "current", "binding_sha256": index["binding_sha256"],
            "asset_count": len(index["items"]), "semantic_review": "not_performed",
            "formal_ready": False, "bindable": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("prepare")
    for arg in ("script", "source", "ledger"):
        make.add_argument(arg, type=Path)
    make.add_argument("--style-file", type=Path, required=True)
    make.add_argument("--shared-design", type=Path)
    make.add_argument("--asset-id", nargs="+")
    make.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("check")
    verify.add_argument("index", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            require(not args.output.exists(), "输出已存在；使用新路径保留旧索引")
            result = prepare(args.script, args.source, args.ledger, args.style_file, args.asset_id, args.shared_design)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            result = {"status": "input_index_saved", "path": str(args.output.resolve()),
                      "binding_sha256": result["binding_sha256"], "semantic_review": "not_performed"}
        else:
            result = check(json.loads(args.index.read_text(encoding="utf-8-sig")))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
