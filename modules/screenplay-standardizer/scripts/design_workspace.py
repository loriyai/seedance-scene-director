"""Frozen shared designs and disjoint draft groups; never writes a formal ledger.

All validation keeps the complete source-validated inventory. Group acceptance
is structural only; merge returns the existing full-design payload, which must
still pass normal design/render/bind and the author's semantic review.
"""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

import asset_visual_design as design
from design_preview import DERIVED, _catalog, require

POLICY = "asset-design-workspace-v1"
SPACE_FIELDS = ("space", "materials_palette", "furnishings", "lighting")
INPUT_KEYS = ("script", "source", "ledger", "shared_design", "style")


def _seal(value, field):
    result = copy.deepcopy(value)
    result[field] = design.fingerprint(result)
    return result


def _check_seal(value, field):
    require(isinstance(value, dict), "冻结对象须为JSON对象")
    require(value.get(field) == design.fingerprint({k: v for k, v in value.items() if k != field}),
            f"{field}不匹配：冻结内容已被修改")


def _plain(data):
    result = copy.deepcopy(data)
    for key in ("identity_anchors", "visual_style", "image_design_version"):
        result.pop(key, None)
    for category in design.ASSET_CATEGORIES:
        for item in result.get(category, []):
            for key in DERIVED:
                item.pop(key, None)
    return result


def _records(records):
    require(isinstance(records, list), "designs须为数组")
    result = {}
    for record in records:
        require(isinstance(record, dict), "设计记录须为对象")
        aid = record.get("asset_id")
        require(isinstance(aid, str) and aid, "设计缺少asset_id")
        require(aid not in result, f"重复资产设计：{aid}")
        result[aid] = {k: copy.deepcopy(v) for k, v in record.items() if k != "asset_id"}
    return result


def _image_ids(data):
    return {item["id"] for category in design.ASSET_CATEGORIES
            for item in design.visual_assets(data, category)}


def _assignments(data, anchors, assignments, version):
    require(isinstance(assignments, dict), "identity_assignments须显式给出CHAR到锚点的映射")
    expected = {item["id"] for item in design.visual_assets(data, "characters")
                if not (version == design.DESIGN_VERSION and design.is_group(item))}
    require(set(assignments) == expected, "identity_assignments须精确覆盖全部独立图片CHAR")
    require(all(value in anchors for value in assignments.values()), "身份分配引用未知锚点")
    require(set(assignments.values()) == set(anchors), "冻结锚点须全部被现用CHAR引用")
    check = copy.deepcopy(data)
    check["image_design_version"] = version
    for item in check["characters"]:
        if item["id"] in assignments:
            item["visual_design"] = {"identity_anchor_id": assignments[item["id"]]}
    design.validate_shared_identity(check)
    for item in check["characters"]:
        base = item.get("appearance_variant", {}).get("base_asset_id")
        if base:
            require(assignments.get(base) == assignments.get(item["id"]), f"{item['id']}佩戴版本须共用基础身份锚点")


def _dependencies(data, assignments):
    """Hard image dependencies plus peer closure and immutable LOC facts."""
    catalog, ids = _catalog(data), _image_ids(data)
    hard, peers, locations = {aid: set() for aid in ids}, {aid: set() for aid in ids}, {}
    for aid in sorted(ids):
        category, item = catalog[aid]
        if category == "characters":
            variant = item.get("appearance_variant", {})
            for key in ("base_asset_id", "prop_asset_id"):
                if variant.get(key):
                    dep = variant[key]
                    expected = "characters" if key == "base_asset_id" else "props"
                    require(dep in ids and catalog[dep][0] == expected, f"{aid}缺少{key}依赖")
                    hard[aid].add(dep)
            if variant:
                base = catalog[variant["base_asset_id"]][1]
                require(not base.get("appearance_variant") and not design.is_group(base), f"{aid}基础人物无效")
            anchor = assignments.get(aid)
            if anchor:
                peers[aid] = {other for other, value in assignments.items() if value == anchor and other != aid}
        elif category == "scene_assets":
            loc = item.get("location_id")
            require(loc in catalog and catalog[loc][0] == "locations", f"{aid}缺少LOC依赖")
            locations[aid] = loc
            chain, current = {aid}, item
            while current.get("parent_id"):
                dep = current["parent_id"]
                require(dep in ids and catalog[dep][0] == "scene_assets", f"{aid}缺少SET父资产")
                require(dep not in chain, f"{aid}SET父链存在循环")
                current = catalog[dep][1]
                require(current.get("location_id") == loc, f"{aid}SET父链跨越LOC")
                hard[aid].add(dep)
                chain.add(dep)
            if item.get("variant") != "base":
                bases = [other["id"] for other in data["scene_assets"]
                         if other.get("location_id") == loc and other.get("variant") == "base"]
                require(len(bases) == 1, f"{aid}须有唯一基础SET")
                hard[aid].add(bases[0])
    return hard, peers, locations


def _space(data, aid, value, frozen):
    """Flatten a version from an immutable spatial plan and grounded edits."""
    item = _catalog(data)[aid][1]
    reference = value.get("space_reference")
    require(isinstance(reference, dict) and set(reference) == {"asset_id", "design_sha256", "differences"},
            f"{aid}空间版本须引用冻结基础/父SET并显式列出differences，可为空")
    require(not set(value).intersection(SPACE_FIELDS), f"{aid}引用共享空间时不可同时直接重写四个空间字段")
    base_id = item.get("parent_id")
    if not base_id:
        bases = [other["id"] for other in data["scene_assets"]
                 if other.get("location_id") == item.get("location_id") and other.get("variant") == "base"]
        require(len(bases) == 1, f"{aid}缺少唯一基础SET")
        base_id = bases[0]
    require(reference["asset_id"] == base_id and base_id in frozen, f"{aid}必须引用已冻结直接父/基础SET：{base_id}")
    require(reference["design_sha256"] == design.fingerprint(frozen[base_id]), f"{aid}共享空间设计hash已过期")
    result = {k: copy.deepcopy(v) for k, v in value.items() if k != "space_reference"}
    result.update({key: copy.deepcopy(frozen[base_id][key]) for key in SPACE_FIELDS})
    differences, seen = reference["differences"], set()
    require(isinstance(differences, list), f"{aid}differences须为数组")
    facts = {fact["text"] for fact in item.get("visual_facts", [])}
    for delta in differences:
        require(isinstance(delta, dict), f"{aid}空间差异须为对象")
        field, operation = delta.get("field"), delta.get("operation")
        expected = {"field", "operation", "new", "source_fact_text", "basis"}
        if operation == "replace":
            expected.add("old")
        require(set(delta) == expected and operation in ("append", "replace"), f"{aid}空间差异字段或操作无效")
        require(field in SPACE_FIELDS and field not in seen, f"{aid}每个空间字段最多一个明确差异")
        require(delta["source_fact_text"] in facts, f"{aid}空间差异须引用本SET已验证原文事实text")
        design.nonempty(delta["basis"], f"{aid}空间差异依据")
        new = design.nonempty(delta["new"], f"{aid}空间差异new")
        if operation == "replace":
            old = design.nonempty(delta["old"], f"{aid}空间差异old")
            require(result[field].count(old) == 1 and old != new, f"{aid}空间差异old须唯一精确匹配且实际变化")
            result[field] = result[field].replace(old, new, 1)
        else:
            result[field] += "；" + new
        seen.add(field)
    return result


def _compile(data, records, anchors, assignments, version, style, frozen=None):
    """Compile only supplied records on a FULL ledger, borrowing no old designs."""
    frozen = frozen or {}
    result, records = _plain(data), copy.deepcopy(records)
    result.update(image_design_version=version, identity_anchors=list(copy.deepcopy(anchors).values()),
                  visual_style={"text": style, "digest": design.fingerprint(style)})
    catalog, ids = _catalog(result), _image_ids(result)
    require(set(records) <= ids, f"设计包含未知/非图片资产：{sorted(set(records) - ids)}")
    hard, _, _ = _dependencies(result, assignments)
    for aid in records:
        require(hard[aid] <= set(records), f"{aid}缺少已提供的设计依赖：{sorted(hard[aid] - set(records))}")
    # SET references only consume explicitly frozen records, never another
    # group's tentative proposal. Frozen records are already flattened.
    for aid, value in records.items():
        category, item = catalog[aid]
        if "space_reference" in value:
            require(category == "scene_assets" and item.get("variant") != "base", f"{aid}仅空间版本可使用space_reference")
            records[aid] = _space(result, aid, value, frozen)
        if aid in assignments:
            require("identity_anchor_id" not in records[aid] or records[aid]["identity_anchor_id"] == assignments[aid],
                    f"{aid}不可修改冻结身份分配")
            records[aid]["identity_anchor_id"] = assignments[aid]
    for aid in records:
        category, item = catalog[aid]
        variant = item.get("appearance_variant")
        if category == "characters" and variant:
            value, base = records[aid], records[variant["base_asset_id"]]
            for key in ("identity_anchor_id", "styling", "appearance_overrides", "override_source_facts"):
                require(key not in value or value[key] == base.get(key), f"{aid}须继承基础形象的{key}")
                if key in base:
                    value[key] = copy.deepcopy(base[key])
            if base.get("appearance_overrides"):
                value.setdefault("variant_differences", copy.deepcopy(base.get("variant_differences", [])))
        item["visual_design"] = records[aid]
    previews = []
    for aid in sorted(records):
        category, item = catalog[aid]
        value = records[aid]
        design.validate_design_record(category, item, value, anchors, version)
        for key, expected in (("source_fact_digest", design.fingerprint(design.source_constraints(result, category, item))),
                              ("source_context_digest", design.source_context_digest(result, category, item))):
            require(key not in value or value[key] == expected, f"{aid}携带过期{key}，须重新实审当前依赖")
            value[key] = expected
        prompt, form = design.compile_prompt(result, category, item, value, anchors)
        previews.append({"asset_id": aid, "image_prompt": prompt, "image_format": form})
    return records, previews


def _freeze_records(data, records, anchors, assignments, version, style):
    hard, _, _ = _dependencies(data, assignments)
    catalog, compiled, remaining = _catalog(data), {}, copy.deepcopy(records)
    require(set(remaining) <= _image_ids(data), "共享设计包含未知/非图片资产")
    while remaining:
        ready = [aid for aid in sorted(remaining) if hard[aid] <= set(compiled)]
        require(ready, "共享设计缺少基础人物/关联PROP/父SET依赖，或存在依赖循环")
        next_records = copy.deepcopy(compiled)
        for aid in ready:
            value = remaining.pop(aid)
            category, item = catalog[aid]
            if category == "scene_assets" and item.get("variant") != "base":
                require("space_reference" in value, f"{aid}冻结空间版本须引用已冻结基础/父SET")
            next_records[aid] = value
        compiled, _ = _compile(data, next_records, anchors, assignments, version, style, compiled)
    return compiled


def freeze(script, paragraphs, data, shared_payload, style_text, inputs=None):
    """Validate the full source ledger and freeze an explicitly authored baseline.

    This in-memory API is for trusted callers with parsed source; file workflows
    should use freeze_from_files so every later check rechecks source file hashes.
    No old visual_design is implicitly reused. Supply any reusable design again;
    its carried source digests are checked before it is accepted.
    """
    import asset_ledger as ledger
    plain = _plain(data)
    ledger.validate(script, paragraphs, plain)
    require(isinstance(shared_payload, dict), "共享设计须为对象")
    allowed = {"image_design_version", "identity_anchors", "identity_assignments", "designs"}
    require(set(shared_payload) <= allowed, "共享设计含未知字段")
    style = style_text.strip()
    design.nonempty(style, "共享视觉风格")
    require("\n" not in style and "\r" not in style, "视觉风格须为单行")
    version = shared_payload.get("image_design_version", data.get("image_design_version", design.DESIGN_VERSION))
    require(version in design.DESIGN_VERSIONS, "图片设计版本无效")
    anchors = design.identity_map(shared_payload)
    assignments = copy.deepcopy(shared_payload.get("identity_assignments"))
    _assignments(plain, anchors, assignments, version)
    records = _records(shared_payload.get("designs"))
    compiled = _freeze_records(plain, records, anchors, assignments, version, style)
    value = {"policy": POLICY, "kind": "shared_baseline", "ledger": plain,
             "script": copy.deepcopy(script), "source_paragraphs": copy.deepcopy(paragraphs),
             "style": style, "image_design_version": version,
             "identity_anchors": list(copy.deepcopy(anchors).values()), "identity_assignments": assignments,
             "shared_designs": compiled, "input_files": copy.deepcopy(inputs or {})}
    return _seal(value, "baseline_sha256")


def freeze_from_files(script_path, source_path, ledger_path, shared_design_path, style_path):
    import asset_ledger as ledger
    paths = dict(zip(INPUT_KEYS, map(Path, (script_path, source_path, ledger_path, shared_design_path, style_path))))
    raw = {key: path.read_bytes() for key, path in paths.items()}
    inputs = {key: {"path": str(path.resolve()), "sha256": hashlib.sha256(raw[key]).hexdigest()}
              for key, path in paths.items()}
    result = freeze(ledger.parse_script(paths["script"]), ledger.source_paragraphs(paths["source"]),
                    json.loads(raw["ledger"].decode("utf-8-sig")), json.loads(raw["shared_design"].decode("utf-8-sig")),
                    raw["style"].decode("utf-8-sig"), inputs)
    validate_baseline(result)
    return result


def _check_input_files(baseline, require_files=False):
    inputs = baseline.get("input_files")
    require(isinstance(inputs, dict) and (not inputs or set(inputs) == set(INPUT_KEYS)), "冻结输入文件清单不完整")
    require(not require_files or bool(inputs), "生产分组须由freeze_from_files创建真实文件基线")
    for key, entry in inputs.items():
        require(isinstance(entry, dict) and set(entry) == {"path", "sha256"}, "冻结文件须含path/sha256")
        path = Path(entry["path"])
        require(path.is_absolute() and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"],
                f"冻结输入已变化或缺失：{key}")
    return inputs


def validate_baseline(baseline, require_files=False):
    import asset_ledger as ledger
    _check_seal(baseline, "baseline_sha256")
    require(baseline.get("policy") == POLICY and baseline.get("kind") == "shared_baseline", "不是共享设计基线")
    inputs = _check_input_files(baseline, require_files)
    data = baseline["ledger"]
    require(data == _plain(data), "共享基线不能夹带未验证旧设计")
    ledger.validate(baseline["script"], baseline["source_paragraphs"], data)
    anchors = design.identity_map(baseline)
    _assignments(data, anchors, baseline["identity_assignments"], baseline["image_design_version"])
    records, _ = _compile(data, baseline["shared_designs"], anchors, baseline["identity_assignments"],
                          baseline["image_design_version"], baseline["style"])
    require(records == baseline["shared_designs"], "共享设计摘要或依赖已过期")
    if inputs:
        paths = {key: Path(entry["path"]) for key, entry in inputs.items()}
        current = json.loads(paths["ledger"].read_text(encoding="utf-8-sig"))
        shared = json.loads(paths["shared_design"].read_text(encoding="utf-8-sig"))
        require(data == _plain(current) and baseline["script"] == ledger.parse_script(paths["script"]) and
                baseline["source_paragraphs"] == ledger.source_paragraphs(paths["source"]),
                "基线内嵌台账/剧本与冻结原文件不一致")
        require(baseline["style"] == paths["style"].read_text(encoding="utf-8-sig").strip(), "基线风格与原文件不一致")
        require(baseline["identity_anchors"] == shared.get("identity_anchors", []) and
                baseline["identity_assignments"] == shared.get("identity_assignments") and
                baseline["image_design_version"] == shared.get("image_design_version", current.get("image_design_version", design.DESIGN_VERSION)),
                "基线身份/版本与共享设计原文件不一致")
        original = _freeze_records(data, _records(shared.get("designs")), anchors, baseline["identity_assignments"],
                                   baseline["image_design_version"], baseline["style"])
        require(original == records, "基线共享设计与原文件不一致")
    _check_input_files(baseline)
    return baseline


def _group_dependencies(baseline, owned):
    data, shared = baseline["ledger"], baseline["shared_designs"]
    hard, peers, locations = _dependencies(data, baseline["identity_assignments"])
    catalog, closure = _catalog(data), set(owned)
    pending = list(owned)
    while pending:
        aid = pending.pop()
        for dep in hard[aid] | peers[aid]:
            if dep not in closure:
                closure.add(dep)
                pending.append(dep)
    require(closure <= set(owned) | set(shared),
            f"跨组依赖尚未冻结，须同组设计或先串行冻结：{sorted(closure - set(owned) - set(shared))}")
    for aid in owned:
        category, item = catalog[aid]
        if category == "scene_assets" and item.get("variant") != "base":
            require(hard[aid] <= set(shared), f"{aid}空间版本须先冻结基础空间/父链，再分组设计")
    hashes = {f"asset:{aid}": design.fingerprint(shared[aid]) for aid in sorted(closure - set(owned))}
    anchors = design.identity_map(baseline)
    for aid in closure:
        anchor = baseline["identity_assignments"].get(aid)
        if anchor:
            hashes[f"anchor:{anchor}"] = design.fingerprint(anchors[anchor])
        if aid in locations:
            loc = locations[aid]
            hashes[f"location:{loc}"] = design.fingerprint(catalog[loc][1])
    return sorted(closure - set(owned)), dict(sorted(hashes.items()))


def plan(baseline, groups):
    validate_baseline(baseline)
    require(isinstance(groups, list), "groups须为数组")
    expected = _image_ids(baseline["ledger"]) - set(baseline["shared_designs"])
    seen, names, planned = set(), set(), []
    for group in groups:
        require(isinstance(group, dict) and set(group) == {"group_id", "owned_asset_ids"}, "分组只接受group_id/owned_asset_ids")
        gid, owned = group["group_id"], group["owned_asset_ids"]
        require(isinstance(gid, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", gid), "group_id须为安全的短标识")
        require(gid not in names, f"重复分组：{gid}")
        require(isinstance(owned, list) and owned and all(isinstance(aid, str) for aid in owned), f"{gid}须有非空资产范围")
        require(len(owned) == len(set(owned)), f"{gid}拥有范围重复")
        require(not set(owned) & seen, f"分组拥有范围冲突：{sorted(set(owned) & seen)}")
        require(set(owned) <= expected, f"{gid}拥有未知或已冻结资产")
        read_only, hashes = _group_dependencies(baseline, set(owned))
        planned.append({"group_id": gid, "owned_asset_ids": sorted(owned),
                        "read_only_asset_ids": read_only, "read_only_dependency_sha256": hashes})
        seen.update(owned)
        names.add(gid)
    require(seen == expected, f"分组计划须覆盖全部未冻结图片资产：缺少{sorted(expected - seen)}")
    return _seal({"policy": POLICY, "kind": "design_group_plan", "baseline_sha256": baseline["baseline_sha256"],
                  "groups": sorted(planned, key=lambda group: group["group_id"])}, "plan_sha256")


def validate_plan(baseline, group_plan):
    validate_baseline(baseline)
    _check_seal(group_plan, "plan_sha256")
    require(group_plan.get("baseline_sha256") == baseline["baseline_sha256"], "分组计划引用过期共享基线")
    rebuilt = plan(baseline, [{"group_id": group["group_id"], "owned_asset_ids": group["owned_asset_ids"]}
                              for group in group_plan["groups"]])
    require(rebuilt == group_plan, "分组依赖或计划字段与冻结基线不一致")
    return group_plan


def group_spec(baseline, group_plan, group_id):
    validate_plan(baseline, group_plan)
    matches = [group for group in group_plan["groups"] if group["group_id"] == group_id]
    require(len(matches) == 1, f"未知分组：{group_id}")
    return copy.deepcopy(matches[0])


def verify_group(baseline, group_plan, group_id, submission):
    group = group_spec(baseline, group_plan, group_id)
    require(isinstance(submission, dict) and set(submission) == {
        "baseline_sha256", "plan_sha256", "group_id", "owned_asset_ids", "read_only_dependency_sha256", "designs"},
        "分组提交字段须精确匹配协议；不接受改写共享锚点或基线")
    require(submission["baseline_sha256"] == baseline["baseline_sha256"] and
            submission["plan_sha256"] == group_plan["plan_sha256"] and submission["group_id"] == group_id,
            "分组提交引用过期基线/计划或错误分组")
    require(submission["owned_asset_ids"] == group["owned_asset_ids"], "分组声明的拥有范围与计划不一致")
    require(submission["read_only_dependency_sha256"] == group["read_only_dependency_sha256"], "只读依赖hash与计划不一致")
    records = _records(submission["designs"])
    require(set(records) == set(group["owned_asset_ids"]), "分组实际设计ID须精确匹配owned_asset_ids，不得缺项/越界")
    catalog = _catalog(baseline["ledger"])
    for aid, value in records.items():
        category, item = catalog[aid]
        if category == "scene_assets" and item.get("variant") != "base":
            require("space_reference" in value, f"{aid}空间版本必须引用冻结空间，不可独立重写")
    all_records = {**copy.deepcopy(baseline["shared_designs"]), **records}
    compiled, previews = _compile(baseline["ledger"], all_records, design.identity_map(baseline),
                                  baseline["identity_assignments"], baseline["image_design_version"],
                                  baseline["style"], baseline["shared_designs"])
    for aid, frozen in baseline["shared_designs"].items():
        require(compiled[aid] == frozen, f"{aid}分组修改了共享设计或依赖")
    _check_input_files(baseline)
    return {"policy": POLICY, "status": "structurally_valid_group", "semantic_review": "not_performed",
            "formal_ready": False, "bindable": False, "group_id": group_id,
            "baseline_sha256": baseline["baseline_sha256"], "plan_sha256": group_plan["plan_sha256"],
            "submission_sha256": design.fingerprint(submission), "owned_asset_ids": group["owned_asset_ids"],
            "read_only_asset_ids": group["read_only_asset_ids"],
            "designs": [{"asset_id": aid, **compiled[aid]} for aid in group["owned_asset_ids"]],
            "previews": [value for value in previews if value["asset_id"] in records]}


def merge(baseline, group_plan, submissions):
    """Return full legacy-compatible design JSON, never a shortened ledger."""
    import asset_ledger as ledger
    validate_plan(baseline, group_plan)
    require(isinstance(submissions, list), "分组提交须为数组")
    submitted = {}
    for submission in submissions:
        require(isinstance(submission, dict), "分组提交须为对象")
        gid = submission.get("group_id")
        require(isinstance(gid, str) and gid not in submitted, f"重复或缺失提交group_id：{gid}")
        submitted[gid] = submission
    require(set(submitted) == {group["group_id"] for group in group_plan["groups"]}, "合并须精确收齐全部分组，不得缺组/多组")
    records = copy.deepcopy(baseline["shared_designs"])
    for gid in sorted(submitted):
        report = verify_group(baseline, group_plan, gid, submitted[gid])
        records.update(_records(report["designs"]))
    payload = {"image_design_version": baseline["image_design_version"],
               "identity_anchors": copy.deepcopy(baseline["identity_anchors"]),
               "designs": [{"asset_id": aid, **records[aid]} for aid in sorted(records)]}
    complete = design.apply_design(baseline["ledger"], payload, baseline["style"])
    ledger.validate(baseline["script"], baseline["source_paragraphs"], complete, require_designed=True)
    _check_input_files(baseline)
    return payload


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("freeze")
    for name in ("script", "source", "ledger", "shared_design", "style"):
        command.add_argument(name, type=Path)
    command = sub.add_parser("plan")
    command.add_argument("baseline", type=Path)
    command.add_argument("groups", type=Path, help="JSON数组：group_id/owned_asset_ids")
    command = sub.add_parser("verify-group")
    for name in ("baseline", "plan", "submission"):
        command.add_argument(name, type=Path)
    command.add_argument("--group-id", required=True)
    command = sub.add_parser("merge")
    command.add_argument("baseline", type=Path)
    command.add_argument("plan", type=Path)
    command.add_argument("submissions", type=Path, nargs="*")
    # stdout-only JSON avoids hidden writes or accidentally overwriting a formal
    # ledger. The caller chooses a separate draft output path when redirecting.
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            result = freeze_from_files(args.script, args.source, args.ledger, args.shared_design, args.style)
        else:
            baseline = _read(args.baseline)
            validate_baseline(baseline, require_files=True)
            if args.command == "plan":
                result = plan(baseline, _read(args.groups))
            elif args.command == "verify-group":
                result = verify_group(baseline, _read(args.plan), args.group_id, _read(args.submission))
            else:
                result = merge(baseline, _read(args.plan), [_read(path) for path in args.submissions])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
