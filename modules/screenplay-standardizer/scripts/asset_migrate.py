#!/usr/bin/env python3
"""Migrate stable screenplay asset IDs through scene edits, splits, and merges."""

import argparse
import copy
import json
import re
from pathlib import Path
from character_variants import scene_character_ids

from asset_ledger import HEADING, GENERIC_ROLE, digest, fact_list, parse_script, scene_ranges, source_paragraphs, split_cast_entry
from asset_visual_design import (PROP_POLICY, GROUP_POLICY, group_minimum_visible_uses, is_group,
                                 validate_group_policy, validate_prop_coverage, validate_visual_design)


PREFIXES = {"scenes": "SC", "characters": "CHAR", "locations": "LOC", "props": "PROP"}


def compact(value):
    return "".join(char for char in value if char.isalnum())


def next_id(prefix, used):
    number = max((int(value[len(prefix):]) for value in used if re.fullmatch(prefix + r"\d+", value)), default=0) + 1
    result = f"{prefix}{number:04d}"
    used.add(result)
    return result


def scene_text(scene, paragraphs):
    start, end = scene["source_range"]
    return tuple(paragraphs[start - 1:end])


def normalize_mapping(raw, old_scenes, new_signatures, old_paragraphs):
    requested = raw.get("scenes", {})
    if not isinstance(requested, dict):
        raise ValueError("mapping.scenes 必须是旧场景 ID 到新场景号数组或 null 的对象")
    old_ids = {scene["id"] for scene in old_scenes}
    if set(requested) - old_ids:
        raise ValueError("mapping.scenes 包含不存在的旧场景 ID")
    result, pending = {}, []
    for scene in old_scenes:
        scene_id = scene["id"]
        if scene_id in requested:
            value = requested[scene_id]
            numbers = [] if value is None else [value] if isinstance(value, int) and not isinstance(value, bool) else value
            if not isinstance(numbers, list) or any(not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > len(new_signatures) for n in numbers) or len(numbers) != len(set(numbers)):
                raise ValueError(f"{scene_id} 的新场景号无效")
            result[scene_id] = numbers
        else:
            signature = scene_text(scene, old_paragraphs)
            exact = [i + 1 for i, candidate in enumerate(new_signatures) if candidate == signature]
            if len(exact) == 1:
                result[scene_id] = exact
            else:
                pending.append({"kind": "场景对应待核", "old_id": scene_id, "exact_candidates": exact})
    return result, pending


def select_primary(mapping, requested, new_count):
    incoming = {i: [] for i in range(1, new_count + 1)}
    for old_id, numbers in mapping.items():
        for number in numbers:
            incoming[number].append(old_id)
    primary_input = requested.get("primary_ids", {})
    if not isinstance(primary_input, dict):
        raise ValueError("mapping.primary_ids 必须是新场景号到旧 SC ID 的对象")
    primary, pending = {}, []
    for number, sources in incoming.items():
        specified = str(number) in primary_input
        chosen = primary_input.get(str(number))
        complex_lineage = len(sources) > 1 or any(len(mapping[sid]) > 1 for sid in sources)
        if specified and chosen is None:
            pass  # Split continuation starts a new SC ID.
        elif chosen is not None:
            if chosen not in sources:
                raise ValueError(f"新场景{number} 的主 ID 不在其旧场景来源中")
            primary[number] = chosen
        elif len(sources) == 1 and not complex_lineage:
            primary[number] = sources[0]
        elif complex_lineage:
            pending.append({"kind": "主场景 ID 待指定", "new_scene": number, "old_ids": sources})
    chosen = list(primary.values())
    if len(chosen) != len(set(chosen)):
        raise ValueError("一个旧 SC ID 不能成为多个新场景的主 ID")
    if set(primary_input) - {str(i) for i in incoming}:
        raise ValueError("primary_ids 包含无效新场景号")
    return incoming, primary, pending


def relocate_refs(refs, old_scene_ids, old_by_id, lineage, new_scenes_by_number, old_paragraphs, new_paragraphs, new_index):
    located = []
    for ref in refs:
        owners = [sid for sid in old_scene_ids if sid in old_by_id and old_by_id[sid]["source_range"][0] <= ref <= old_by_id[sid]["source_range"][1]]
        if len(owners) != 1:
            return None
        candidates = []
        needle = compact(old_paragraphs[ref - 1])
        if not needle:
            return None
        if len(lineage[owners[0]]) == 1:
            number = lineage[owners[0]][0]
            old_start, old_end = old_by_id[owners[0]]["source_range"]
            new_start, new_end = new_scenes_by_number[number]["source_range"]
            if old_paragraphs[old_start - 1:old_end] == new_paragraphs[new_start - 1:new_end]:
                located.append((number, new_start + ref - old_start))
                continue
        for number in lineage[owners[0]]:
            candidates.extend((number, i) for i in new_index[number].get(needle, []))
        if len(candidates) != 1:
            return None
        located.append(candidates[0])
    return located


def migrate_fact(fact, old_by_id, lineage, new_scenes_by_number, old_paragraphs, new_paragraphs, new_index):
    located = relocate_refs(fact["source_paragraphs"], fact["scene_ids"], old_by_id, lineage,
                            new_scenes_by_number, old_paragraphs, new_paragraphs, new_index)
    if not located:
        return None
    revised = copy.deepcopy(fact)
    revised["source_paragraphs"] = sorted({ref for _, ref in located})
    revised["scene_ids"] = list(dict.fromkeys(new_scenes_by_number[number]["id"] for number, _ in located))
    evidence = "".join(revised["evidence"].split())
    original = "".join("".join(new_paragraphs[ref - 1] for ref in revised["source_paragraphs"]).split())
    if evidence not in original:
        return None
    return revised


def preserve_prop_coverage(old, result, paragraphs, pending):
    """Retain reviewed candidates without silently blessing changed context."""
    if old.get("asset_policy") != PROP_POLICY:
        return
    previous = old.get("prop_coverage_review")
    review = copy.deepcopy(previous) if isinstance(previous, dict) else {
        "source_digest": old.get("source_digest"), "reviewed_scene_ids": [], "candidates": []}
    result["prop_coverage_review"] = review
    fields = ("id", "number", "heading", "source_range", "location_id", "scene_asset_id",
              "character_ids", "prop_ids", "temporal_layer_id", "set_reuse")

    def scene_context(data):
        return [{**{field: scene.get(field) for field in fields},
                 "prop_occurrences": scene.get("prop_occurrences", [])} for scene in data["scenes"]]

    def asset_context(data, category):
        items = copy.deepcopy(data.get(category, []))
        if category == "characters":
            # Migration materializes this optional empty array on every CHAR;
            # an absent legacy field and [] carry the same production meaning.
            for item in items:
                item.setdefault("production_design", [])
        return items

    changed = (old.get("source_digest") != result.get("source_digest")
               or scene_context(old) != scene_context(result)
               or any(asset_context(old, category) != asset_context(result, category)
                      for category in ("characters", "locations", "scene_assets", "props")))
    detail = "原文、场次/段落定位或资产目标记录发生变化" if changed else None
    if detail is None:
        try:
            validate_prop_coverage(result, paragraphs, fact_list)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            detail = str(exc)
    if detail is not None:
        review["reviewed_full_script"] = False
        pending.append({"kind": "道具反向覆盖待复核", "detail": detail,
                        "action": "保留旧候选和出处；逐场重审去向、目标与证据后更新审阅，不自动改写旧source_digest"})


def preserve_group_reviews(old, result, paragraphs, script, pending):
    """Keep v2 modes stable and require fresh review after context changes."""
    if old.get("group_policy") != GROUP_POLICY:
        return
    old_items = {item["id"]: item for item in old["characters"]}
    fields = ("id", "number", "heading", "source_range", "location_id", "scene_asset_id",
              "character_ids", "temporal_layer_id")

    def linked_context(data, aid):
        return [{field: scene.get(field) for field in fields} for scene in data["scenes"]
                if aid in scene["character_ids"]]

    def item_context(item):
        value = copy.deepcopy(item)
        value.setdefault("production_design", [])
        return value

    for item in result["characters"]:
        if not is_group(item):
            continue
        original = old_items.get(item["id"])
        changed = (original is None or old.get("source_digest") != result.get("source_digest")
                   or item_context(original) != item_context(item)
                   or linked_context(old, item["id"]) != linked_context(result, item["id"]))
        detail = "原文、群像身份或场次/可见出场定位发生变化" if changed else None
        if detail is None:
            try:
                validate_group_policy({**result, "characters": [item], "scene_assets": [], "props": []},
                                      paragraphs, fact_list, script)
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                detail = str(exc)
        if detail is not None:
            review = item.get("extraction_review")
            if isinstance(review, dict):
                review["reviewed_full_script"] = False
            pending.append({"kind": "群像全剧筛选待复核", "asset_id": item["id"], "detail": detail,
                            "action": "保留原图片模式与证据；重审剧情连续性和文字充分性后更新审阅，不自动升降或刷新旧摘要"})


def migrate(old_ledger_path, old_source_path, new_script_path, new_workdir, requested):
    old = json.loads(old_ledger_path.read_text(encoding="utf-8-sig"))
    if old.get("schema_version") not in (1, 2, 3):
        raise ValueError("旧台账 schema_version 须为 1、2 或 3")
    old_paragraphs = source_paragraphs(old_source_path)
    if old.get("source_digest") != digest(old_paragraphs):
        raise ValueError("旧台账与旧原文摘要不匹配")
    new_paragraphs = source_paragraphs(new_workdir / "source.json")
    bases = parse_script(new_script_path)
    ranges = scene_ranges(new_workdir, len(bases), len(new_paragraphs))
    new_signatures = [tuple(new_paragraphs[start - 1:end]) for start, end in ranges]
    old_scenes = old["scenes"]
    old_by_id = {scene["id"]: scene for scene in old_scenes}
    lineage, pending = normalize_mapping(requested, old_scenes, new_signatures, old_paragraphs)
    if pending:
        return None, {"status": "需要场景对应", "scene_lineage": lineage, "pending_reviews": pending,
                      "mapping_example": {"scenes": {item["old_id"]: ["填写新场景号；删除填 null"] for item in pending}}}
    incoming, primary, primary_pending = select_primary(lineage, requested, len(bases))
    if primary_pending:
        return None, {"status": "需要主场景 ID", "scene_lineage": lineage,
                      "pending_reviews": primary_pending,
                      "mapping_example": {"primary_ids": {str(item["new_scene"]): "选择一个 old_ids；纯新增场景无需指定" for item in primary_pending}}}

    used = set(old.get("retired_ids", []))
    inactive = copy.deepcopy(old.get("inactive_assets", {"characters": []}))
    if not isinstance(inactive, dict) or not isinstance(inactive.get("characters", []), list):
        raise ValueError("旧台账 inactive_assets 无效")
    inactive.setdefault("characters", [])
    for category in PREFIXES:
        used.update(item["id"] for item in old[category])
    used.update(item["id"] for item in inactive["characters"])
    used.update(item["id"] for item in old.get("scene_assets", []))
    retired = set(old.get("retired_ids", []))
    retired.update(set(old_by_id) - set(primary.values()))
    pending = [{"kind": "旧台账待核项", "item": item} for item in old.get("pending_reviews", [])]
    characters = copy.deepcopy(old["characters"])
    original_characters = {item["id"]: item for item in old["characters"]}
    locations = copy.deepcopy(old["locations"])
    props = copy.deepcopy(old["props"])
    char_by_id = {item["id"]: item for item in characters}
    loc_by_id = {item["id"]: item for item in locations}
    name_map = requested.get("characters", {})
    if not isinstance(name_map, dict):
        raise ValueError("mapping.characters 必须是旧人物 ID 到新人物行名称的对象")
    for char_id, name in name_map.items():
        dormant = next((item for item in inactive["characters"] if item["id"] == char_id), None)
        if dormant is not None:
            inactive["characters"].remove(dormant)
            characters.append(dormant)
            char_by_id[char_id] = dormant
        if char_id not in char_by_id or not isinstance(name, str) or not name.strip():
            raise ValueError(f"人物改名映射无效：{char_id}")
        item = char_by_id[char_id]
        if 'appearance_variant' in item:
            raise ValueError('佩戴版本不能独立改名为另一个原人物；请映射base_asset_id对应的原人物后重新审阅形象')
        source_speaker, _ = split_cast_entry(name)
        item['source_entry'] = name
        if 'source_speaker' in item:
            item['source_speaker'] = source_speaker
        # The mapping renames the source entry, not its independent production
        # asset name. Existing image/node names retain their identity.

    scenes = []
    for base, source_range in zip(bases, ranges):
        number = base["number"]
        predecessor_ids = incoming[number]
        old_scene_id = primary.get(number)
        scene_id = old_scene_id if old_scene_id else next_id("SC", used)
        prior = [old_by_id[sid] for sid in predecessor_ids]
        matching_locations = {scene["location_id"] for scene in prior
                              if base["location_name"] in [loc_by_id[scene["location_id"]]["name"],
                                                             *loc_by_id[scene["location_id"]]["aliases"]]}
        if len(matching_locations) == 1:
            location_id = next(iter(matching_locations))
        else:
            location_id = next_id("LOC", used)
            item = {"id": location_id, "name": base["location_name"], "aliases": [], "visual_facts": []}
            locations.append(item)
            loc_by_id[location_id] = item
            if prior:
                pending.append({"kind": "地点身份待核", "new_scene": number, "new_location_id": location_id,
                                "old_location_ids": [scene["location_id"] for scene in prior]})
        linked = []
        for name in base["cast_entries"]:
            role_name, _ = split_cast_entry(name)
            options = {ref for scene in prior for ref in scene["character_ids"] if char_by_id[ref].get("source_entry", char_by_id[ref]["name"]) == name}
            generic = bool(GENERIC_ROLE.match(role_name) or role_name.endswith("-群像"))
            if not options and not generic:
                options = {item["id"] for item in characters if 'appearance_variant' not in item and item.get("source_entry", item["name"]) == name}
            if len(options) == 1:
                linked.append(next(iter(options)))
            else:
                new_id = next_id("CHAR", used)
                item = {"id": new_id, "name": name, "aliases": [], "visual_facts": [], "personality_facts": []}
                characters.append(item)
                char_by_id[new_id] = item
                linked.append(new_id)
                if options:
                    pending.append({"kind": "同名人物身份待核", "new_scene": number, "new_character_id": new_id,
                                    "old_character_ids": sorted(options)})
        if len(predecessor_ids) == 1 and len(lineage[predecessor_ids[0]]) == 1:
            prop_ids = copy.deepcopy(prior[0]["prop_ids"])
        elif len(predecessor_ids) > 1:
            prop_ids = list(dict.fromkeys(ref for scene in prior for ref in scene["prop_ids"]))
        else:
            prop_ids = []
            if prior and prior[0]["prop_ids"]:
                pending.append({"kind": "拆场道具去向待核", "new_scene": number,
                                "old_prop_ids": prior[0]["prop_ids"]})
        scenes.append({"id": scene_id, "number": number, "heading": base["heading"], "source_range": source_range,
                       "location_id": location_id, "character_ids": linked, "prop_ids": prop_ids,
                       "visual_facts": [], "time_basis": {"kind": "待核", "source_paragraphs": []}})
    new_by_number = {scene["number"]: scene for scene in scenes}
    new_by_id = {scene["id"]: scene for scene in scenes}
    new_index = {}
    for scene in scenes:
        start, end = scene["source_range"]
        index = {}
        for ref in range(start, end + 1):
            index.setdefault(compact(new_paragraphs[ref - 1]), []).append(ref)
        new_index[scene["number"]] = index
    # Source casts remain exact. Same-person visual alternatives travel in a
    # separate map, never compete as candidates for the source character slot.
    for scene in scenes:
        prior = [old_by_id[sid] for sid in incoming[scene['number']]]
        tracks = [track for previous in prior for track in previous.get('character_appearances', [])]
        if not tracks:
            continue
        if len(prior) != 1 or len(lineage[prior[0]['id']]) != 1:
            pending.append({'kind': '佩戴形象场次轨迹待核', 'scene_id': scene['id'],
                            'old_tracks': copy.deepcopy(tracks)})
            continue
        scene['character_appearances'] = copy.deepcopy(tracks)
        for track in scene['character_appearances']:
            proofs = [track['initial_evidence'], *track['transitions']]
            for proof in proofs:
                fact = {'text': '形象状态', 'evidence': proof['evidence'],
                        'source_paragraphs': proof['source_paragraphs'],
                        'scene_ids': [prior[0]['id']], 'attribution': '形象审阅'}
                moved = migrate_fact(fact, old_by_id, lineage, new_by_number,
                                     old_paragraphs, new_paragraphs, new_index)
                if moved and moved['scene_ids'] == [scene['id']]:
                    proof['source_paragraphs'] = moved['source_paragraphs']
                else:
                    pending.append({'kind': '佩戴形象事件证据待核', 'scene_id': scene['id'],
                                    'base_asset_id': track['base_asset_id'], 'old_evidence': copy.deepcopy(proof)})
            if track['base_asset_id'] not in scene['character_ids']:
                pending.append({'kind': '佩戴形象原人物关联待核', 'scene_id': scene['id'],
                                'base_asset_id': track['base_asset_id']})
    for old_scene in old_scenes:
        if not lineage[old_scene["id"]]:
            pending.append({"kind": "旧场景删除待核", "scene_id": old_scene["id"], "heading": old_scene["heading"],
                            "character_ids": old_scene["character_ids"], "prop_ids": old_scene["prop_ids"],
                            "visual_facts": old_scene["visual_facts"],
                            **({"old_scene_record": copy.deepcopy(old_scene)} if "temporal_layer_id" in old_scene or "set_reuse" in old_scene else {})})
        for fact in old_scene["visual_facts"]:
            revised = migrate_fact(fact, old_by_id, lineage, new_by_number, old_paragraphs, new_paragraphs, new_index)
            if revised and len(revised["scene_ids"]) == 1:
                target = new_by_id[revised["scene_ids"][0]]
                target["visual_facts"].append(revised)
            else:
                pending.append({"kind": "逐场事实证据待核", "old_scene_id": old_scene["id"], "fact": fact})
    for scene in scenes:
        number = scene["number"]
        ids = incoming[number]
        if len(ids) != 1 or len(lineage[ids[0]]) != 1:
            pending.append({"kind": "时间依据待核", "new_scene": number})
            continue
        prior = old_by_id[ids[0]]
        old_time = prior.get("time_basis", {"kind": "待核", "source_paragraphs": []})
        old_mark = HEADING.fullmatch(prior["heading"])
        new_mark = HEADING.fullmatch(scene["heading"])
        if not old_mark or not new_mark or old_mark.group(2) != new_mark.group(2):
            pending.append({"kind": "时间依据待核", "new_scene": number, "old_value": old_time})
            continue
        if old_time["kind"] == "格式默认":
            scene["time_basis"] = copy.deepcopy(old_time)
        elif old_time["kind"] == "原文明示":
            relocated = relocate_refs(old_time["source_paragraphs"], [ids[0]], old_by_id, lineage,
                                      new_by_number, old_paragraphs, new_paragraphs, new_index)
            if relocated and all(target == number for target, _ in relocated):
                scene["time_basis"] = {"kind": "原文明示", "source_paragraphs": [ref for _, ref in relocated]}
            else:
                pending.append({"kind": "时间依据待核", "new_scene": number, "old_value": old_time})
        else:
            pending.append({"kind": "时间依据待核", "new_scene": number})

    for category, items in (("characters", characters), ("locations", locations), ("props", props)):
        for item in items:
            for field in ("visual_facts", "personality_facts", "production_design") if category == "characters" else ("visual_facts",):
                migrated = []
                for fact in item.get(field, []):
                    revised = migrate_fact(fact, old_by_id, lineage, new_by_number, old_paragraphs, new_paragraphs, new_index)
                    field_name = {"characters": "character_ids", "locations": "location_id", "props": "prop_ids"}[category]
                    linked = bool(revised) and all(item["id"] in (
                        [new_by_id[sid][field_name]] if category == "locations" else
                        scene_character_ids(new_by_id[sid]) if category == "characters" else new_by_id[sid][field_name]
                    ) for sid in revised["scene_ids"])
                    if revised and linked:
                        migrated.append(revised)
                    else:
                        pending.append({"kind": "资产事实证据或出现关系待核", "asset_id": item["id"], "fact": fact})
                item[field] = migrated
            if category in ("characters", "props") and "visible_uses" in item:
                uses = []
                for use in item["visible_uses"]:
                    fact = {"text": "可见使用", "evidence": use["evidence"],
                            "source_paragraphs": use["source_paragraphs"],
                            "scene_ids": [use["scene_id"]], "attribution": "可见使用审阅"}
                    revised = migrate_fact(fact, old_by_id, lineage, new_by_number,
                                           old_paragraphs, new_paragraphs, new_index)
                    linked_field = "character_ids" if category == "characters" else "prop_ids"
                    if revised and len(revised["scene_ids"]) == 1 and item["id"] in new_by_id[revised["scene_ids"][0]][linked_field]:
                        moved = copy.deepcopy(use)
                        moved["scene_id"] = revised["scene_ids"][0]
                        moved["source_paragraphs"] = revised["source_paragraphs"]
                        if len(incoming[new_by_id[moved["scene_id"]]["number"]]) > 1:
                            moved["event_id"] = use["scene_id"] + ":" + use["event_id"]
                        uses.append(moved)
                    else:
                        pending.append({"kind": "群像独立可见出场待核" if category == "characters" else "道具独立可见使用待核",
                                        "asset_id": item["id"], "old_use": copy.deepcopy(use)})
                item["visible_uses"] = uses
                prop_minimum = 1 if (old.get("asset_policy") == PROP_POLICY
                                     and item.get("extraction_review", {}).get("kind") == "core_continuity") else 2
                if category == "props" and len(uses) < prop_minimum and old.get("asset_policy"):
                    pending.append({"kind": "主要道具复用门槛待核", "asset_id": item["id"], "remaining_visible_uses": len(uses)})
                if (category == "characters" and item.get("visual_mode") == "image_asset"
                        and len(uses) < group_minimum_visible_uses(old, item)):
                    pending.append({"kind": "群像图片复用门槛待核", "asset_id": item["id"], "remaining_visible_uses": len(uses)})
            if (category == "characters" and item.get("extraction_review", {}).get("kind") == "background_group"
                    and old.get("group_policy") != GROUP_POLICY):
                if item["extraction_review"].get("source_digest") != digest(new_paragraphs):
                    pending.append({"kind": "群像全剧筛选待复核", "asset_id": item["id"],
                                    "action": "复核全剧出场与身份后更新群像审阅，不自动切换图片资产模式"})
            if category == 'characters' and item.get('appearance'):
                old_anchor = item['appearance']['active_from']
                for field in ('active_from', 'active_until'):
                    old_sid = item['appearance'].get(field)
                    if not old_sid:
                        continue
                    numbers = lineage.get(old_sid, [])
                    if len(numbers) == 1:
                        item['appearance'][field] = new_by_number[numbers[0]]['id']
                    else:
                        pending.append({'kind': '形象启停场次待核', 'asset_id': item['id'], 'old_scene_id': old_sid})
                anchor = old_by_id.get(old_anchor)
                target = new_by_id.get(item['appearance']['active_from'])
                if not anchor or not target or old_paragraphs[anchor['source_range'][0]-1:anchor['source_range'][1]] != new_paragraphs[target['source_range'][0]-1:target['source_range'][1]]:
                    pending.append({'kind': '形象启用依据待核', 'asset_id': item['id']})
    used_characters = {ref for scene in scenes for ref in scene_character_ids(scene)}
    for item in list(characters):
        if 'appearance_variant' in item:
            base = char_by_id.get(item['appearance_variant']['base_asset_id'])
            if base:
                item['source_entry'] = base.get('source_entry', base['name'])
                if 'source_speaker' in base:
                    item['source_speaker'] = base['source_speaker']
                else:
                    item.pop('source_speaker', None)
            if item['id'] not in used_characters or old.get('source_digest') != digest(new_paragraphs) or name_map:
                pending.append({'kind': '佩戴形象审阅待更新', 'asset_id': item['id'],
                                'action': '保留ID、原轨迹和旧审阅摘要，重新核实原人物及事件后运行variants；不能直接刷新摘要'})
            continue
        if item["id"] not in used_characters:
            characters.remove(item)
            stored = copy.deepcopy(item)
            if item["id"] in original_characters:
                stored["visual_facts"] = copy.deepcopy(original_characters[item["id"]]["visual_facts"])
                stored["personality_facts"] = copy.deepcopy(original_characters[item["id"]]["personality_facts"])
            inactive["characters"].append(stored)
            pending.append({"kind": "人物可能改名或退场", "asset_id": item["id"], "name": item["name"],
                            "action": "确认新人物行身份后恢复关联，或确认退场后移入 retired_ids"})
    result = {"schema_version": 2, "source_digest": digest(new_paragraphs), "scenes": scenes,
              "characters": characters, "locations": locations, "props": props,
              "retired_ids": sorted(retired), "inactive_assets": inactive, "pending_reviews": pending}
    for field in ("asset_policy", "group_policy", "character_variant_policy", "image_design_version", "visual_style", "identity_anchors"):
        if field in old:
            result[field] = copy.deepcopy(old[field])
    if old.get("schema_version") == 3:
        result["schema_version"] = 3
        result["scene_assets"] = copy.deepcopy(old["scene_assets"])
        assets = {item["id"]: item for item in result["scene_assets"]}
        for location in locations:
            if "identity_status" not in location:
                location.update(identity_status="pending", identity_basis="")
        for scene in scenes:
            prior = [old_by_id[sid] for sid in incoming[scene["number"]]]
            options = {s.get("scene_asset_id") for s in prior if s["location_id"] == scene["location_id"]}
            options.discard(None)
            scene["scene_asset_id"] = next(iter(options)) if len(options) == 1 else None
            unchanged = len(prior) == 1 and len(lineage[prior[0]["id"]]) == 1 and (
                old_paragraphs[prior[0]["source_range"][0] - 1:prior[0]["source_range"][1]] ==
                new_paragraphs[scene["source_range"][0] - 1:scene["source_range"][1]])
            if scene["scene_asset_id"] not in assets or not unchanged:
                pending.append({"kind": "布景版本关联待核", "scene_id": scene["id"],
                                "candidate_asset_id": scene["scene_asset_id"]})
            if len(prior) == 1 and unchanged and prior[0].get('prop_occurrences'):
                scene['prop_occurrences'] = copy.deepcopy(prior[0]['prop_occurrences'])
            elif any(s.get('prop_occurrences') for s in prior):
                pending.append({'kind': '道具实体出现关系待核', 'scene_id': scene['id'],
                                'old_occurrences': [s.get('prop_occurrences', []) for s in prior]})
        if old.get("temporal_layers") or any("temporal_layer_id" in s or "set_reuse" in s for s in old_scenes):
            # Preserve chronology records even when their anchors are ambiguous;
            # pending_reviews prevents stale evidence from becoming deliverable.
            result["temporal_layers"] = copy.deepcopy(old.get("temporal_layers", []))

            def move_temporal_evidence(record, old_sid, label):
                revised = copy.deepcopy(record)
                numbers = lineage.get(old_sid, [])
                located = relocate_refs(record.get("source_paragraphs", []), [old_sid], old_by_id, lineage,
                                        new_by_number, old_paragraphs, new_paragraphs, new_index)
                if len(numbers) != 1 or not located or any(n != numbers[0] for n, _ in located):
                    pending.append({"kind": label + "证据或场次对应待核", "old_scene_id": old_sid, "record": record})
                    return revised, None
                target = new_by_number[numbers[0]]
                refs = [ref for _, ref in located]
                evidence = "".join(record.get("evidence", "").split())
                valid = refs == list(range(min(refs), max(refs) + 1)) and evidence and evidence in "".join(
                    "".join(new_paragraphs[r - 1] for r in refs).split())
                if not valid:
                    pending.append({"kind": label + "连续原文证据待核", "old_scene_id": old_sid, "record": record})
                    return revised, None
                revised["source_paragraphs"] = refs
                if scene_text(old_by_id[old_sid], old_paragraphs) != scene_text(target, new_paragraphs):
                    pending.append({"kind": label + "上下文审阅待核", "old_scene_id": old_sid, "new_scene_id": target["id"]})
                return revised, target

            for index, layer in enumerate(result["temporal_layers"]):
                revised, target = move_temporal_evidence(layer, layer["start_scene_id"], "时间层")
                if target:
                    revised["start_scene_id"] = target["id"]
                result["temporal_layers"][index] = revised
            for scene in scenes:
                ids = incoming[scene["number"]]
                if len(ids) != 1 or len(lineage[ids[0]]) != 1:
                    pending.append({"kind": "场次实际时间层及原样基准待核", "scene_id": scene["id"], "old_scene_ids": ids,
                                    "old_scene_records": [copy.deepcopy(old_by_id[sid]) for sid in ids]})
                    continue
                prior = old_by_id[ids[0]]
                if "temporal_layer_id" in prior:
                    scene["temporal_layer_id"] = prior["temporal_layer_id"]
                if prior.get("set_reuse"):
                    reuse, target = move_temporal_evidence(prior["set_reuse"], prior["id"], "原样复用")
                    baseline = lineage.get(reuse["same_as_scene_id"], [])
                    if len(baseline) == 1:
                        reuse["same_as_scene_id"] = new_by_number[baseline[0]]["id"]
                    else:
                        pending.append({"kind": "原样基准场对应待核", "scene_id": scene["id"], "record": prior["set_reuse"]})
                    scene["set_reuse"] = reuse
        for item in result["scene_assets"]:
            facts = []
            for fact in item["visual_facts"]:
                revised = migrate_fact(fact, old_by_id, lineage, new_by_number, old_paragraphs, new_paragraphs, new_index)
                linked = revised and all(new_by_id[sid]["location_id"] == item["location_id"] and
                    (item["variant"] == "base" or new_by_id[sid]["scene_asset_id"] == item["id"])
                    for sid in revised["scene_ids"])
                if linked:
                    facts.append(revised)
                else:
                    pending.append({"kind": "布景版本证据待核", "asset_id": item["id"], "fact": fact})
            item["visual_facts"] = facts
            if item["variant"] != "base" and not any(s["scene_asset_id"] == item["id"] for s in scenes):
                pending.append({"kind": "未用布景版本去向待核", "asset_id": item["id"]})
    preserve_group_reviews(old, result, new_paragraphs, bases, pending)
    preserve_prop_coverage(old, result, new_paragraphs, pending)
    if "image_design_version" in old:
        try:
            validate_visual_design(result)
        except (ValueError, KeyError, TypeError, StopIteration) as exc:
            pending.append({"kind": "资产制作设计待复核", "detail": str(exc),
                            "action": "保留原设计，审阅新事实与身份后重新运行design；不得直接清空待核项交付"})
    report = {"status": "迁移草稿", "scene_lineage": lineage, "primary_ids": primary,
              "retired_scene_ids": sorted(set(old_by_id) - set(primary.values())),
              "pending_reviews": pending,
              "counts": {"scenes": len(scenes), "characters": len(characters),
                         "locations": len(locations), "props": len(props), "pending": len(pending),
                         "unchanged_source_scenes": sum(
                             old_paragraphs[old_by_id[sid]["source_range"][0] - 1:old_by_id[sid]["source_range"][1]] ==
                             new_paragraphs[new_by_number[numbers[0]]["source_range"][0] - 1:new_by_number[numbers[0]]["source_range"][1]]
                             for sid, numbers in lineage.items() if len(numbers) == 1)}}
    return result, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_ledger", type=Path)
    parser.add_argument("old_source", type=Path)
    parser.add_argument("new_script", type=Path)
    parser.add_argument("new_workdir", type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        requested = json.loads(args.mapping.read_text(encoding="utf-8-sig")) if args.mapping else {}
        if not isinstance(requested, dict):
            raise ValueError("mapping 必须是 JSON 对象")
        result, report = migrate(args.old_ledger, args.old_source, args.new_script, args.new_workdir, requested)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if result is None:
            raise ValueError(f"请按迁移报告补充映射：{args.report}")
        if args.output.exists():
            raise ValueError("迁移草稿已存在，请另选输出，避免覆盖人工修改")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已保存迁移草稿：{args.output}；待核 {len(report['pending_reviews'])} 项；报告：{args.report}")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
