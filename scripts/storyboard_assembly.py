"""Assemble explicit storyboard decisions, without making creative decisions.

This optional, pure draft interface does not edit a project, validate source
meaning, grant approval, or migrate legacy requests. Existing director and
review gates remain authoritative after assembly.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import sys
import uuid

import continuity


DRAFT_POLICY = "strict-draft-v1"
SHOT_TEXT_FIELDS = (
    "scene_id", "framing", "angle", "label", "purpose", "camera",
    "composition", "depth_of_field", "lighting", "transition", "text")
UNIT_TEXT_FIELDS = (
    "main_scene", "time", "weather", "global", "start_state", "end_state")


class AssemblyError(ValueError):
    """One missing or contradictory author decision, with a field location."""


def require(condition, path, message):
    if not condition:
        raise AssemblyError(f"{path}: {message}")


def text(value, path):
    require(continuity.line(value) and len(value.splitlines()) == 1,
            path, "须由作者填写非空单行文本")


def unique_strings(value, path):
    require(isinstance(value, list), path, "须为无重复字符串数组，可明确填写空数组")
    for index, item in enumerate(value):
        text(item, f"{path}[{index}]")
    require(len(value) == len(set(value)), path, "存在重复项")


def stable_id(item, field, prefix, path):
    if field not in item:
        item[field] = prefix + str(uuid.uuid4())
    text(item[field], f"{path}.{field}")


def snapshot(value, definitions, path):
    require(isinstance(value, dict), path, "须为实体状态对象")
    require(set(value) == set(definitions), path, "须恰好覆盖已登记实体")
    for entity, item in value.items():
        loc = f"{path}.{entity}"
        require(isinstance(item, dict) and set(item) == {"present", "asset_id", "facts"},
                loc, "须明确填写present/asset_id/facts")
        require(type(item["present"]) is bool, loc + ".present", "须为布尔值")
        assets = definitions[entity]["asset_ids"]
        require((isinstance(item["asset_id"], str) and item["asset_id"] in assets)
                if assets else item["asset_id"] is None,
                loc + ".asset_id", "须为本实体登记形象，无资产实体须为null")
        require(isinstance(item["facts"], dict), loc + ".facts", "须为事实对象")
        for key, value in item["facts"].items():
            text(key, loc + ".facts.<key>")
            text(value, loc + ".facts." + key)


def evidence(items, path):
    require(isinstance(items, list) and bool(items), path, "须明确填写原文证据")
    for index, item in enumerate(items):
        loc = f"{path}[{index}]"
        require(isinstance(item, dict) and set(item) == {"record", "quote"},
                loc, "须为record/quote；原文及语义另由现有检查核对")
        text(item["record"], loc + ".record")
        text(item["quote"], loc + ".quote")


def visibility(block, entities, path):
    """Expand only author-enumerated groups; never complement a visible list."""
    explicit = block.get("visibility", {})
    require(isinstance(explicit, dict), path + ".visibility", "须为逐实体判断对象")
    result = copy.deepcopy(explicit)
    if "visibility_groups" in block:
        groups = block.pop("visibility_groups")
        require(isinstance(groups, list), path + ".visibility_groups", "须为显式共享组数组")
        for index, group in enumerate(groups):
            loc = f"{path}.visibility_groups[{index}]"
            require(isinstance(group, dict) and set(group) == {"entities", "mode", "basis"},
                    loc, "须明确填写entities/mode/basis")
            unique_strings(group["entities"], loc + ".entities")
            require(bool(group["entities"]), loc + ".entities", "共享组须列出实际实体")
            for entity in group["entities"]:
                require(entity in entities, loc + ".entities", f"未登记实体：{entity}")
                require(entity not in result, loc + ".entities", f"实体重复判断：{entity}")
                result[entity] = {"mode": group["mode"], "basis": group["basis"]}
    extra = sorted(set(result) - set(entities))
    require(not extra, path + ".visibility", "含未登记实体：" + ", ".join(extra))
    for entity in entities:
        loc = path + ".visibility." + entity
        require(entity in result, loc, "缺少mode和basis；不能由subjects或其他实体判断补成画外")
        view = result[entity]
        require(isinstance(view, dict) and set(view) == {"mode", "basis"},
                loc, "须明确填写mode和basis")
        require(isinstance(view["mode"], str) and view["mode"] in continuity.MODES,
                loc + ".mode", "可选visible/offscreen/occluded/absent")
        text(view["basis"], loc + ".basis")
    block["visibility"] = result
    return result


def replay_changes(current, definitions, changes, path):
    """Apply explicit changes; do not infer an event from prose or keywords."""
    require(isinstance(changes, list), path, "须明确列出变化，无变化明确填写[]")
    reached = {key: [copy.deepcopy(item)] for key, item in current.items()}
    for index, change in enumerate(changes):
        loc = f"{path}[{index}]"
        require(isinstance(change, dict) and set(change) == {"entity", "set", "evidence"},
                loc, "须明确填写entity/set/evidence")
        entity = change["entity"]
        require(isinstance(entity, str) and entity in definitions, loc + ".entity", "实体未登记")
        patch = change["set"]
        require(isinstance(patch, dict) and bool(patch)
                and set(patch) <= {"present", "asset_id", "facts"},
                loc + ".set", "只允许显式更新present/asset_id/facts")
        evidence(change["evidence"], loc + ".evidence")
        before = copy.deepcopy(current[entity])
        for field in ("present", "asset_id"):
            if field in patch:
                current[entity][field] = patch[field]
        if "facts" in patch:
            facts = patch["facts"]
            require(isinstance(facts, dict) and bool(facts), loc + ".set.facts", "须为非空变化对象")
            for key, value in facts.items():
                text(key, loc + ".set.facts.<key>")
                if value is None:
                    require(key in current[entity]["facts"], loc + ".set.facts." + key,
                            "不能删除不存在的事实")
                    del current[entity]["facts"][key]
                else:
                    text(value, loc + ".set.facts." + key)
                    current[entity]["facts"][key] = value
        snapshot(current, definitions, loc + ".result")
        require(before != current[entity], loc + ".set", "未改变既有状态")
        reached[entity].append(copy.deepcopy(current[entity]))
    return reached


def subjects(shot, views, reached, path):
    candidates = {}
    for entity, view in views.items():
        present = [item for item in reached[entity] if item["present"]]
        loc = path + ".continuity.visibility." + entity
        require((not present) if view["mode"] == "absent" else bool(present), loc + ".mode",
                "可见性与本镜已到达的在场状态矛盾")
        if view["mode"] == "visible":
            candidates[entity] = {item["asset_id"] for item in present if item["asset_id"] is not None}
    if "subjects" not in shot:
        ambiguous = [entity for entity, refs in candidates.items() if len(refs) > 1]
        require(not ambiguous, path + ".subjects",
                "可见实体在镜内到达多个形象，须明确实际入画资产，不自动放入所有版本：" + ", ".join(ambiguous))
        # Entity order is declared by the author; deduplicate shared assets.
        shot["subjects"] = list(dict.fromkeys(ref for refs in candidates.values() for ref in sorted(refs)))
    unique_strings(shot["subjects"], path + ".subjects")
    chosen = set(shot["subjects"])
    allowed = set().union(*candidates.values()) if candidates else set()
    require(chosen <= allowed, path + ".subjects", "资产没有对应可见实体或本镜未到达该形象")
    for entity, refs in candidates.items():
        require(not refs or bool(chosen & refs), path + ".subjects", f"缺少可见实体的实际入画形象：{entity}")
    require("focus" in shot, path + ".focus", "须由作者明确观看焦点，不从subjects推断")
    unique_strings(shot["focus"], path + ".focus")
    require(set(shot["focus"]) <= chosen and (bool(shot["focus"]) or not chosen),
            path + ".focus", "须为可见参与资产的非空子集；无资产镜头明确填写[]")


def derive_dialogue_parts(unit, path="unit"):
    """Return exact per-shot parts from the sole, author-supplied audio plan.

    This pure function is reusable by precise revision tools. It does not split
    words, choose pauses, estimate speech, change timing, or accept stale parts.
    """
    events = unit["audio"]
    plans = unit.get("audio_plan", [])
    require(isinstance(plans, list), path + ".audio_plan", "须为record/parts数组")
    expected = {}
    for index, event in enumerate(events):
        loc = f"{path}.audio[{index}]"
        require(isinstance(event, dict), loc, "须为声音事件对象")
        text(event.get("record"), loc + ".record")
        text(event.get("text"), loc + ".text")
        require(event["record"] not in expected, loc + ".record", "声音事件重复")
        expected[event["record"]] = event
    result = [[] for _ in unit["shots"]]
    seen = set()
    number = lambda value: type(value) in (int, float) and math.isfinite(value)
    for index, plan in enumerate(plans):
        loc = f"{path}.audio_plan[{index}]"
        require(isinstance(plan, dict) and set(plan) == {"record", "parts"}, loc, "须为record/parts对象")
        record = plan["record"]
        text(record, loc + ".record")
        require(record in expected and record not in seen, loc + ".record", "声音事件不存在或计划重复")
        seen.add(record)
        parts = plan["parts"]
        require(isinstance(parts, list) and bool(parts), loc + ".parts", "须明确填写语义片段，不默认整句落镜")
        event = expected[record]
        require(all(number(event.get(key)) for key in ("start", "end"))
                and 0 <= event["start"] < event["end"] <= unit["shots"][-1]["end"],
                path + ".audio." + record, "声音事件时窗须在单元内")
        previous_end = event["start"]
        texts = []
        for part_index, part in enumerate(parts):
            part_loc = f"{loc}.parts[{part_index}]"
            require(isinstance(part, dict), part_loc, "须为作者明确的片段对象")
            text(part.get("text"), part_loc + ".text")
            require("record" not in part or part["record"] == record,
                    part_loc + ".record", "与所属audio_plan声源记录冲突")
            require(all(number(part.get(key)) for key in ("start", "end"))
                    and previous_end <= part["start"] < part["end"] <= event["end"],
                    part_loc + ".start/end", "须为事件内有序且不重叠的明确时窗")
            matches = [n for n, shot in enumerate(unit["shots"])
                       if shot["start"] <= part["start"] < part["end"] <= shot["end"]]
            require(len(matches) == 1, part_loc + ".start/end",
                    "没有完整容纳该片段的镜窗；请作者调整切点，不自动拆词或改时长")
            result[matches[0]].append(dict(copy.deepcopy(part), record=record))
            texts.append(part["text"])
            previous_end = part["end"]
        require("".join(texts) == event["text"], loc + ".parts", "片段拼接须逐字还原声音事件原文")
    require(seen == set(expected), path + ".audio_plan", "须覆盖所有声音事件，不能从镜头片段反推唯一计划")
    return result


def materialize_times(unit, path="unit"):
    """Normalize explicit durations before state effects need the shot grid."""
    shots = unit.get("shots")
    require(isinstance(shots, list) and bool(shots), path + ".shots", "须有作者已明确判断的镜头")
    cursor = 0
    for index, shot in enumerate(shots):
        loc = f"{path}.shots[{index}]"
        require(isinstance(shot, dict), loc, "须为镜头对象")
        if "duration" in shot:
            duration = shot.pop("duration")
            require(type(duration) is int and 1 <= duration <= 6, loc + ".duration", "须为作者指定的1至6秒整数")
            if "start" in shot or "end" in shot:
                require(shot.get("start") == cursor and shot.get("end") == cursor + duration,
                        loc + ".duration", "与显式start/end矛盾")
            else:
                shot["start"], shot["end"] = cursor, cursor + duration
        require(type(shot.get("start")) is int and type(shot.get("end")) is int
                and shot["start"] == cursor and 1 <= shot["end"] - cursor <= 6,
                loc + ".start/end", "须显式给连续1至6秒时码，或给duration供程序累计")
        cursor = shot["end"]
    require(4 <= cursor <= 30, path + ".duration", "累计时长须为4至30秒；不自动补动作或压缩")
    if "duration" in unit:
        require(type(unit["duration"]) is int and unit["duration"] == cursor,
                path + ".duration", "与镜头累计时长矛盾")
    else:
        unit["duration"] = cursor


def assemble_unit(source, path="unit"):
    """Return an independent canonical draft; never modify the input object."""
    require(isinstance(source, dict), path, "须为单元对象")
    unit = copy.deepcopy(source)
    established_identity = "draft_policy" in unit
    if "draft_policy" in unit:
        require(unit["draft_policy"] == DRAFT_POLICY, path + ".draft_policy", "未知装配策略，不能静默迁移")
        require("draft_unit_id" in unit, path + ".draft_unit_id", "已装配草稿丢失稳定ID，不能重新分配伪装原身份")
    unit["draft_policy"] = DRAFT_POLICY
    stable_id(unit, "draft_unit_id", "draft-", path)
    materialize_times(unit, path)
    if "action_state_plan" in unit:
        from state_effects import compile_unit
        unit = compile_unit(unit)
    require(unit.get("output_format") == "reference-v3", path + ".output_format", "本入口要求明确使用reference-v3")
    for field in UNIT_TEXT_FIELDS:
        text(unit.get(field), path + "." + field)
    for field in ("scene_ids", "character_ids", "prop_ids"):
        unique_strings(unit.get(field), path + "." + field)
    require(unit["main_scene"] in unit["scene_ids"], path + ".main_scene", "未列入scene_ids")
    require(isinstance(unit.get("audio"), list), path + ".audio", "须明确填写声音事件，无声音明确填写[]")
    contract = unit.get("continuity")
    require(isinstance(contract, dict), path + ".continuity", "须明确填写实体、进入态及边界")
    require(set(contract) in ({"version", "boundary", "entities", "initial"},
                              {"version", "boundary", "entities", "initial", "final"}),
            path + ".continuity", "字段须为version/boundary/entities/initial及可选final")
    require(type(contract["version"]) is int and contract["version"] == 1,
            path + ".continuity.version", "状态记录版本须为1")
    boundary = contract["boundary"]
    require(isinstance(boundary, dict) and boundary.get("kind") in ("continuous", "reset"),
            path + ".continuity.boundary", "须明确声明边界")
    if boundary["kind"] == "continuous":
        require(set(boundary) == {"kind"}, path + ".continuity.boundary", "连续边界不填写重置依据")
    else:
        require(set(boundary) == {"kind", "reason", "evidence"},
                path + ".continuity.boundary", "重置须有reason及原文evidence，装配不判断重置成立")
        text(boundary["reason"], path + ".continuity.boundary.reason")
        require(isinstance(boundary["evidence"], list) and bool(boundary["evidence"]),
                path + ".continuity.boundary.evidence", "须明确填写原文证据")
    definitions = contract["entities"]
    require(isinstance(definitions, dict), path + ".continuity.entities", "须为实体对象")
    for entity, definition in definitions.items():
        loc = path + ".continuity.entities." + str(entity)
        text(entity, loc)
        require(isinstance(definition, dict), loc, "须为实体定义")
        unique_strings(definition.get("asset_ids"), loc + ".asset_ids")
        require(set(definition) == ({"asset_ids"} if definition["asset_ids"] else {"asset_ids", "label", "evidence"}),
                loc, "资产实体填写asset_ids；无资产实体另须label/evidence")
        if not definition["asset_ids"]:
            text(definition["label"], loc + ".label")
            evidence(definition["evidence"], loc + ".evidence")
    snapshot(contract["initial"], definitions, path + ".continuity.initial")
    current = copy.deepcopy(contract["initial"])
    shots = unit.get("shots")
    require(isinstance(shots, list) and bool(shots), path + ".shots", "须有作者已明确判断的镜头")
    cursor, ids = 0, set()
    for index, shot in enumerate(shots):
        loc = f"{path}.shots[{index}]"
        require(isinstance(shot, dict), loc, "须为镜头对象")
        if established_identity:
            require("review_id" in shot, loc + ".review_id", "已装配草稿须保留镜头ID；新增镜头须明确另建ID")
        stable_id(shot, "review_id", "shot-", loc)
        require(shot["review_id"] not in ids, loc + ".review_id", "本单元镜头标识重复")
        ids.add(shot["review_id"])
        for field in SHOT_TEXT_FIELDS:
            text(shot.get(field), loc + "." + field)
        require(shot["scene_id"] in unit["scene_ids"], loc + ".scene_id", "未列入本单元scene_ids")
        if "dialogue_parts" in shot:
            require(isinstance(shot["dialogue_parts"], list), loc + ".dialogue_parts", "须为片段数组")
        cursor = shot["end"]
        block = shot.get("continuity")
        require(isinstance(block, dict), loc + ".continuity", "须明确填写changes及每个实体的可见性")
        require(set(block) <= {"changes", "visibility", "visibility_groups"} and "changes" in block,
                loc + ".continuity", "须填写changes及visibility或visibility_groups，不允许额外字段")
        views = visibility(block, definitions, loc + ".continuity")
        reached = replay_changes(current, definitions, block["changes"], loc + ".continuity.changes")
        subjects(shot, views, reached, loc)
        require(set(shot["subjects"]) <= set(unit["character_ids"] + unit["prop_ids"]),
                loc + ".subjects", "可见资产未列入本单元character_ids/prop_ids")
    if "final" in contract:
        snapshot(contract["final"], definitions, path + ".continuity.final")
        require(contract["final"] == current, path + ".continuity.final", "与显式变化重放不一致；请修正权威判断")
    else:
        contract["final"] = current
    planned_parts = derive_dialogue_parts(unit, path)
    for index, (shot, parts) in enumerate(zip(shots, planned_parts)):
        loc = f"{path}.shots[{index}].dialogue_parts"
        if "dialogue_parts" in shot:
            require(shot["dialogue_parts"] == parts, loc,
                    "与唯一audio_plan不一致；请通过精确修订入口重新派生，不覆盖矛盾旧稿")
        else:
            shot["dialogue_parts"] = parts
    return unit


def assemble_batch(document):
    """Assemble a normal director request containing units, preserving metadata."""
    require(isinstance(document, dict), "request", "须为含units的分镜请求对象")
    require(isinstance(document.get("units"), list) and bool(document["units"]),
            "units", "须为非空单元数组")
    result = copy.deepcopy(document)
    result["units"] = [assemble_unit(unit, f"units[{index}]")
                       for index, unit in enumerate(document["units"])]
    identities = [unit["draft_unit_id"] for unit in result["units"]]
    require(len(identities) == len(set(identities)), "units.draft_unit_id", "请求内草稿单元标识重复")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    assemble = commands.add_parser("assemble", help="严格装配明确判断，仅写入新的草稿文件")
    assemble.add_argument("--input", required=True, type=Path)
    assemble.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        require(args.input.resolve() != args.output.resolve(), "--output", "不能覆盖输入文件")
        require(not args.output.exists(), "--output", "文件已存在；保留旧稿并选择新路径")
        document = json.loads(args.input.read_text(encoding="utf-8-sig"))
        result = assemble_batch(document)
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        # Exclusive creation also protects against a file appearing after check.
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        print(json.dumps({"status": "assembled", "output": str(args.output.resolve()),
                          "units": len(result["units"]),
                          "notice": "仅结构装配；原文、表演、摄影及审阅仍按现有流程核对"}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
