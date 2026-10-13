"""Validate declared continuity, never infer staging, physics or acting quality.

The optional contract is replayed without changing units or legacy receipts.
New-request callers decide when the contract is required.
"""
import copy


VERSION = 1
MODES = {"visible", "offscreen", "occluded", "absent"}


def require(condition, message):
    if not condition:
        raise ValueError("连续性：" + message)


def line(value):
    return isinstance(value, str) and bool(value.strip()) and not any(c in value for c in "\r\n")


def portion(record, span):
    """Use the director's compact-body coordinates, including partial records."""
    a, b = max(span[0], record["start"]), min(span[1], record["end"])
    if a >= b:
        return ""
    positions = [i for i, c in enumerate(record["text"]) if c.isalnum()]
    left = 0 if a == record["start"] else positions[a - record["start"]]
    right = len(record["text"]) if b == record["end"] else positions[b - record["start"]]
    return record["text"][left:right]


def evidence(state, unit, items, context, allow_heading=False, allow_prior=False):
    require(isinstance(items, list) and bool(items), context + "须有原文证据")
    records = {r["id"]: r for r in state["records"]}
    scenes = {s["id"]: s for s in state["ledger"]["scenes"]}
    for item in items:
        require(isinstance(item, dict) and line(item.get("quote")), context + "证据格式错误")
        quote = item["quote"]
        require(any(c.isalnum() for c in quote), context + "证据不能只有标点")
        if "record" in item:
            require(set(item) == {"record", "quote"} and isinstance(item["record"], str)
                    and item["record"] in records, context + "证据行不存在")
            span = [0, unit["span"][1]] if allow_prior else unit["span"]
            source = portion(records[item["record"]], span)
        else:
            sid = item.get("scene")
            require(allow_heading and set(item) == {"scene", "quote"}
                    and isinstance(sid, str) and sid in unit.get("scene_ids", []) and sid in scenes,
                    context + "只有边界重置可引用本单元场景标题")
            source = scenes[sid]["heading"]
        require(quote in source, context + ("证据不在已到达的原文范围内" if allow_prior else "证据不在本单元选定的原文范围内"))


def snapshot(value, definitions, context):
    require(isinstance(value, dict) and set(value) == set(definitions), context + "须恰好覆盖全部登记实体")
    for key, item in value.items():
        require(isinstance(item, dict) and set(item) == {"present", "asset_id", "facts"},
                context + "实体状态须为present/asset_id/facts")
        require(type(item["present"]) is bool, context + "present须为布尔值")
        aliases = definitions[key]["asset_ids"]
        require((isinstance(item["asset_id"], str) and item["asset_id"] in aliases) if aliases
                else item["asset_id"] is None, context + "当前形象不属于实体登记资产")
        require(isinstance(item["facts"], dict) and all(line(k) and line(v) for k, v in item["facts"].items()),
                context + "facts须为非空单行键和值组成的对象，可留空对象")


def validate_unit(state, unit, required=False):
    """Return replay summary or None for an unmodified legacy unit.

    Raises ValueError for declared contradictions. The return value is private
    validation data and must not be rendered into the user-facing prompt.
    """
    contract = unit.get("continuity")
    if contract is None:
        require(not required, "新生成/指定替换单元须填写continuity状态记录")
        return None
    require(isinstance(contract, dict) and type(contract.get("version")) is int
            and contract["version"] == VERSION, "状态记录版本不兼容")
    require(set(contract) == {"version", "boundary", "entities", "initial", "final"}, "状态记录字段错误")
    boundary = contract["boundary"]
    require(isinstance(boundary, dict) and boundary.get("kind") in ("continuous", "reset"), "边界类型非法")
    warnings = []
    if boundary["kind"] == "reset":
        require(set(boundary) == {"kind", "reason", "evidence"} and line(boundary.get("reason")),
                "边界重置须说明真实时间/空间/叙事层变化")
        evidence(state, unit, boundary.get("evidence"), "边界重置", allow_heading=True)
        warnings.append({"kind": "连续性重置需人工核实", "unit": unit.get("id", "当前单元"),
                         "review": "核实所引原文确实允许时间、空间或叙事层重置；引文存在不证明重置合理。",
                         "reason": boundary["reason"]})
    else:
        require(set(boundary) == {"kind"}, "连续边界不填写重置依据")
    definitions = contract["entities"]
    require(isinstance(definitions, dict), "entities须为对象")
    catalog = {item["id"]: category for category in ("characters", "props")
               for item in state["ledger"][category]}
    registered_labels = {item.get(field) for category in ("characters", "props")
                         for item in state["ledger"][category]
                         for field in ("name", "source_entry", "source_name", "source_speaker")
                         if line(item.get(field))}
    aliases = {}
    for key, item in definitions.items():
        require(line(key) and isinstance(item, dict), "实体ID或定义非法")
        refs = item.get("asset_ids")
        require(isinstance(refs, list) and all(isinstance(a, str) and a in catalog for a in refs)
                and len(refs) == len(set(refs)), "实体asset_ids含未知资产或重复值")
        require(len({catalog[a] for a in refs}) <= 1, "一个实体不能混合人物和道具资产")
        if refs:
            require(set(item) == {"asset_ids"}, "登记资产实体只需asset_ids；身份合并须人工核实")
        else:
            require(set(item) == {"asset_ids", "label", "evidence"} and line(item.get("label")),
                    "未建资产的局部实体/群体须有label和原文证据")
            require(item["label"] not in registered_labels,
                    "已登记资产不能改成asset_ids为空的局部实体绕过引用")
            evidence(state, unit, item.get("evidence"), "局部实体", allow_prior=True)
        for ref in refs:
            aliases.setdefault(ref, set()).add(key)
    snapshot(contract["initial"], definitions, "initial")
    snapshot(contract["final"], definitions, "final")
    current = copy.deepcopy(contract["initial"])
    for number, shot in enumerate(unit["shots"], 1):
        context = f"第{number}镜"
        block = shot.get("continuity")
        require(isinstance(block, dict) and set(block) == {"changes", "visibility"}, context + "缺少changes/visibility")
        changes, visibility = block["changes"], block["visibility"]
        require(isinstance(changes, list), context + "changes须为数组")
        require(isinstance(visibility, dict) and set(visibility) == set(definitions), context + "visibility须恰好覆盖全部登记实体")
        # An entity may enter, leave or change image during the shot. Retain all
        # states actually reached, rather than checking only the final instant.
        reached = {key: [copy.deepcopy(item)] for key, item in current.items()}
        for change in changes:
            require(isinstance(change, dict) and set(change) == {"entity", "set", "evidence"}, context + "变化字段错误")
            key, patch = change["entity"], change["set"]
            require(isinstance(key, str) and key in definitions, context + "变化实体未登记")
            require(isinstance(patch, dict) and bool(patch) and set(patch) <= {"present", "asset_id", "facts"},
                    context + "变化只能更新present/asset_id/facts")
            evidence(state, unit, change["evidence"], context + "状态变化")
            before = copy.deepcopy(current[key])
            for field in ("present", "asset_id"):
                if field in patch:
                    current[key][field] = patch[field]
            if "facts" in patch:
                require(isinstance(patch["facts"], dict) and bool(patch["facts"]), context + "facts变化须为非空对象")
                for fact, value in patch["facts"].items():
                    require(line(fact) and (value is None or line(value)), context + "变化事实格式错误")
                    if value is None:
                        require(fact in current[key]["facts"], context + "不能删除不存在的事实")
                        del current[key]["facts"][fact]
                    else:
                        current[key]["facts"][fact] = value
            snapshot(current, definitions, context)
            require(current[key] != before, context + "变化未改变已登记状态")
            reached[key].append(copy.deepcopy(current[key]))
        subjects, focus = set(shot.get("subjects", [])), set(shot.get("focus", []))
        require(subjects <= set(aliases), context + "视觉参与资产未登记实体")
        require(focus <= subjects, context + "focus不属于subjects")
        visible_assets = set()
        for key, view in visibility.items():
            require(isinstance(view, dict) and set(view) == {"mode", "basis"}
                    and view.get("mode") in MODES and line(view.get("basis")), context + "可见性须填写mode与具体basis")
            present_states = [item for item in reached[key] if item["present"]]
            if view["mode"] == "absent":
                require(not present_states, context + "在场实体不能标为absent；画外/遮挡不等于离场")
            else:
                require(bool(present_states), context + "全镜不在场实体不能可见、遮挡或画外在场")
            if view["mode"] == "visible":
                refs = {item["asset_id"] for item in present_states if item["asset_id"] is not None}
                require(not refs or bool(refs & subjects), context + "可见实体当前形象未列入subjects")
                visible_assets.update(refs & subjects)
        require(subjects <= visible_assets, context + "subjects资产没有对应可见实体；仅画外声源写audio")
        require(focus <= visible_assets, context + "焦点实体必须至少部分可见")
    require(current == contract["final"], "final与逐镜变化重放结果不一致，存在未登记变化或状态遗漏")
    return {"version": VERSION, "warnings": warnings, "initial": copy.deepcopy(contract["initial"]), "final": current,
            "entities": copy.deepcopy(definitions), "boundary": copy.deepcopy(boundary)}


def validate_chain(state, units, touched_indices):
    """Check prospective source-adjacent links touching new/replaced units.

    Untouched old units without the contract stay readable and produce an
    explicit boundary-review warning. No stored unit is upgraded or mutated.
    """
    touched = set(touched_indices)
    require(all(type(i) is int and 0 <= i < len(units) for i in touched), "touched_indices越界")
    summaries = {i: validate_unit(state, units[i], required=True) for i in touched}
    warnings = []
    by_id = {u["id"]: i for i, u in enumerate(units) if "id" in u}
    for index, unit in enumerate(units):
        previous = by_id.get(unit.get("predecessor"))
        if previous is None or previous == index or units[previous]["span"][1] != unit["span"][0]:
            candidates = [i for i, other in enumerate(units)
                          if i != index and other["span"][1] == unit["span"][0]]
            if len(candidates) > 1:
                if index in touched or set(candidates) & touched:
                    warnings.append({"kind": "连续性前序候选不唯一需核对", "unit": unit.get("id", f"待提交单元{index + 1}"),
                                     "candidates": [units[i].get("id", f"待提交单元{i + 1}") for i in candidates],
                                     "review": "多个来源相邻版本且无有效前序链接；不能任意挑选版本证明连续性，须确认实际承接版本。"})
                continue
            previous = candidates[0] if candidates else None
        if previous is None or not ({index, previous} & touched):
            continue
        keys = [units[i].get("id", f"待提交单元{i + 1}") for i in (previous, index)]
        # Reading stored structured summaries does not revalidate old creative
        # content. Only neighbours' boundary data are compared.
        pair = []
        for i in (previous, index):
            saved = summaries.get(i)
            if saved is None:
                saved = units[i].get("continuity")
            comparable = (isinstance(saved, dict) and type(saved.get("version")) is int
                          and saved["version"] == VERSION and isinstance(saved.get("boundary"), dict)
                          and saved["boundary"].get("kind") in ("continuous", "reset"))
            if comparable:
                for key in ("initial", "final"):
                    states = saved.get(key)
                    comparable = comparable and isinstance(states, dict) and all(
                        isinstance(item, dict) and set(item) == {"present", "asset_id", "facts"}
                        and type(item["present"]) is bool and isinstance(item["facts"], dict)
                        for item in states.values())
            pair.append(saved if comparable else None)
        prior, later = pair
        if prior is None or later is None:
            warnings.append({"kind": "旧单元连续性边界需人工核对", "units": keys,
                             "review": "相邻旧单元没有可比状态记录；保留旧内容并人工核对承接，不据此判定连续性通过。"})
            continue
        if later["boundary"]["kind"] == "reset":
            continue
        final, initial = prior["final"], later["initial"]
        for key in set(final) | set(initial):
            old, new = final.get(key), initial.get(key)
            if old is not None and new is not None:
                matches = (old["present"] == new["present"] and old["asset_id"] == new["asset_id"]
                           and all(fact in new["facts"] and new["facts"][fact] == value
                                   for fact, value in old["facts"].items()))
                require(matches, f"{keys[0]}→{keys[1]}实体{key}承接状态不一致；先继承再登记变化，真实切换用有依据的reset")
                added = sorted(set(new["facts"]) - set(old["facts"]))
                if added:
                    warnings.append({"kind": "新纳入追踪事实需核对既有状态", "units": keys, "entity": key,
                                     "facts": added, "review": "前序未登记这些关系/状态；核对原文及既有场面是否支持，不把新增追踪字段误作新发生的动作。"})
            elif old is not None:
                require(not old["present"], f"{keys[0]}→{keys[1]}遗漏仍在场实体{key}")
            elif new["present"]:
                warnings.append({"kind": "新纳入追踪实体需核对既有状态", "units": keys, "entity": key,
                                 "review": "前序未追踪此实体；核对原文和既有场面是否支持其已在场，不自动推断新登场或补写进入动作。"})
    return warnings
