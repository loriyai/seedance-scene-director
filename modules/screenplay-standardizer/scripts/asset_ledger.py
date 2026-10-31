#!/usr/bin/env python3
"""Initialize, validate, and render a screenplay asset ledger."""

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

from asset_visual_design import (PROP_POLICY, GROUP_POLICY, apply_design, apply_group_review,
                                 validate_prop_policy, validate_group_policy,
                                 validate_visual_design, visual_assets, scene_source_digests)
from character_variants import (apply_variant_review, scene_character_ids,
                                validate_character_variants)
from cast_contract import split_cast_entry, validate_cast_entries, validate_source_speaker
from asset_review import (prepare_review_payload, apply_review_decisions, verify_review_decisions,
                          validate_asset_lifecycle)
from project_contract import delivery_contract, requires_design


HEADING = re.compile(r"场景(\d+)(?=-(?:日|夜)-(?:内|外)-|：(?:日|夜)·(?:内|外)·)[：-](日|夜)[·-](内|外)[·-](.+)")
SCENE_FILE = re.compile(r"scene_(\d+)_(\d+)-(\d+)\.txt")
ID_PATTERNS = {
    "scenes": re.compile(r"SC\d{4,}"),
    "characters": re.compile(r"CHAR\d{4,}"),
    "locations": re.compile(r"LOC\d{4,}"),
    "props": re.compile(r"PROP\d{4,}"),
    "scene_assets": re.compile(r"SET\d{4,}"),
}
GENERIC_ROLE = re.compile(r"^(?:宾客|同学|女生|男生|医生|男医生|女医生|护士|保镖|工作人员|服务员|路人|司机|助理|店员|老师|教授|大爷|阿姨|侍者|主持人)")


def source_paragraphs(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    paragraphs = data["paragraphs"]
    if any(item["id"] != i for i, item in enumerate(paragraphs, 1)):
        raise ValueError("source.json 段落编号不连续")
    return [item["text"] for item in paragraphs]


def digest(paragraphs: list[str]) -> str:
    packed = json.dumps(paragraphs, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def parse_script(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    scenes = []
    heading_positions = []
    for i, line in enumerate(lines):
        match = HEADING.fullmatch(line.strip())
        if not match:
            continue
        number = int(match.group(1))
        if line.strip().startswith(f"场景{match.group(1)}-"):
            validate_image_name(line.strip())
        if number != len(scenes) + 1:
            raise ValueError(f"剧本场景序号不连续：{number}")
        if i + 1 >= len(lines) or not lines[i + 1].startswith("人物："):
            raise ValueError(f"场景{number} 缺少紧随标题的人物行")
        cast_line = lines[i + 1].strip()
        if not cast_line.endswith("；"):
            raise ValueError(f"场景{number} 人物行未以分号结束")
        if cast_line == "人物：无；":
            entries = []
            names = []
        else:
            entries = cast_line[3:-1].split("；")
            cast_errors = validate_cast_entries(entries)
            if cast_errors:
                raise ValueError(f"场景{number} 人物行不合法：" + "；".join(cast_errors))
            names = [split_cast_entry(entry)[0] for entry in entries]
        scenes.append({
            "number": number,
            "heading": line.strip(),
            "location_name": match.group(4).strip(),
            "cast_entries": entries,
            "cast_names": names,
        })
        heading_positions.append(i)
    if not scenes:
        raise ValueError("标准剧本没有可识别的场景")
    for index, scene in enumerate(scenes):
        end = heading_positions[index + 1] if index + 1 < len(scenes) else len(lines)
        scene["body_text"] = "\n".join(lines[heading_positions[index] + 2:end])
    return scenes


def validate_image_name(name: str) -> None:
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((" ", ".")):
        raise ValueError(f"场景名称含文件名不支持的字符或结尾：{name}")
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    if stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem):
        raise ValueError(f"资产名称占用 Windows 保留设备名，不能用作图片文件名：{name}")


def reference_preflight(data):
    """Fail before delivery/binding, rather than while planning the first shot."""
    owners = {}
    for category in ("characters", "props", "scene_assets"):
        for item in data.get(category, []):
            name = item.get("name")
            if not isinstance(name, str) or not name or name != name.strip() or name == "黑幕" or any(c in name for c in "@{}\r\n"):
                raise ValueError(f"资产名称为空、含引用分隔符或占用黑幕名称：{item['id']}")
            validate_image_name(name)
            if name in owners:
                raise ValueError(f"资产名称重复，须在资产阶段处理：{owners[name]} / {item['id']} {name}")
            owners[name] = item["id"]
    return owners


def normalize_assets(data):
    """Normalize a NEW unbound ledger; retain redirects and physical occurrences.

    Location identity and visual changes are semantic decisions made beforehand.
    This function only applies the agreed deterministic naming/reuse policy.
    """
    if data.get('schema_version') != 3:
        raise ValueError('旧台账须先审核升级；normalize仅用于新未绑定schema 3台账')
    result = copy.deepcopy(data)
    locations = {item['id']: item for item in result['locations']}
    scenes = result['scenes']
    redirects, changes, archived = {}, [], []
    for category, linked_field in (('characters', 'character_ids'), ('props', 'prop_ids')):
        output, shared = [], {}
        for item in result[category]:
            aid, old_name = item['id'], item['name']
            linked = [s for s in scenes if aid in s[linked_field]]
            key = None
            if category == 'characters' and '-群像-' in item.get('source_entry', old_name):
                entry = item.setdefault('source_entry', old_name)
                lids = {s['location_id'] for s in linked}
                if len(lids) != 1:
                    raise ValueError(f"群像 {aid} 须先按已核实地点拆分，不能跨地点复用")
                location = locations[next(iter(lids))]
                if location.get('identity_status') != 'confirmed':
                    raise ValueError(f"群像 {aid} 的地点身份尚未核实")
                qualifier = item.get('group_qualifier', '')
                item['name'] = entry + '-' + location['name'] + (('-' + qualifier) if qualifier else '')
                key = (entry, location['id'], qualifier, item.get('visual_version', 'base'))
                if item.get('visual_version', 'base') != 'base':
                    item['name'] += '-' + item['visual_version']
            elif category == 'characters':
                # Display-only normalization for NEW unbound ledgers. Preserve
                # source_entry and explicit identity/incarnation evidence.
                item.setdefault('source_entry', old_name)
                parts = old_name.split('-')
                if len(parts) >= 3 and parts[1] == '今生':
                    item['name'] = '-'.join([parts[0], *parts[2:]])
            elif category == 'props':
                source_name = item.setdefault('source_name', old_name)
                version = item.get('visual_version', 'base')
                item['name'] = source_name if version == 'base' else source_name + '-' + version
                key = (source_name, version)
            if item['name'] != old_name:
                changes.append({'id': aid, 'from': old_name, 'to': item['name']})
            if key is not None and key in shared:
                target = shared[key]
                redirects[aid] = target['id']
                archived.append(copy.deepcopy(item))
                for field in ('aliases', 'visual_facts', 'personality_facts', 'production_design', 'visible_uses'):
                    for value in item.get(field, []):
                        if value not in target.setdefault(field, []):
                            target[field].append(value)
                if category == 'characters' and (result.get('group_policy') or 'visual_mode' in target or 'visual_mode' in item):
                    # Review the merged full-script group once; a per-scene
                    # decision cannot silently become its final asset policy.
                    target['visual_mode'] = 'prompt_only'
                    target.pop('extraction_review', None)
            else:
                output.append(item)
                if key is not None:
                    shared[key] = item
        if category == 'props':
            merging = set(redirects) | set(redirects.values())
            for scene in scenes:
                occurrences = scene.setdefault('prop_occurrences', [])
                for aid in scene['prop_ids']:
                    if aid in merging and not any(o['asset_id'] == aid for o in occurrences):
                        occurrences.append({'id': scene['id'] + ':' + aid, 'asset_id': aid,
                                            'count': None, 'identity_status': '未核实实体身份'})
                for occurrence in occurrences:
                    occurrence['asset_id'] = redirects.get(occurrence['asset_id'], occurrence['asset_id'])
        for scene in scenes:
            scene[linked_field] = list(dict.fromkeys(redirects.get(aid, aid) for aid in scene[linked_field]))
        result[category] = output
    retired = result.setdefault('retired_ids', [])
    retired.extend(aid for aid in redirects if aid not in retired)
    for item in result['characters']:
        variant = item.get('appearance_variant', {})
        if variant.get('prop_asset_id') in redirects:
            variant['prop_asset_id'] = redirects[variant['prop_asset_id']]
    if 'observations' in result:
        from scene_observations import remap_observation_targets
        result = remap_observation_targets(result, redirects)
    reference_preflight(result)
    from asset_review import assert_normalize_review_safe
    report = {'renamed': changes, 'redirects': redirects, 'merged_assets': archived}
    report['review_impact'] = assert_normalize_review_safe(data, result, report)
    return result, report


def validate_appearance(item, paragraphs, scenes):
    appearance = item.get('appearance')
    if appearance is None:
        return
    if not isinstance(appearance, dict) or any(not isinstance(appearance.get(k), str) or not appearance[k].strip()
        for k in ('identity', 'incarnation', 'variant', 'active_from', 'trigger', 'basis')):
        raise ValueError(f"{item['id']} 形象须登记角色身份、世次、版本、启用场次及依据")
    start = scenes.get(appearance['active_from'])
    stop = scenes.get(appearance.get('active_until')) if appearance.get('active_until') else None
    if not start or (appearance.get('active_until') and not stop) or (stop and stop['number'] < start['number']):
        raise ValueError(f"{item['id']} 形象启用/停用场次无效")
    if appearance['trigger'] not in ('source_appearance', 'rebirth', 'change_clothes', 'time_jump'):
        raise ValueError(f"{item['id']} 形象不能仅因发财、认亲或即将重开而切换")
    a, b = start['source_range']
    if ''.join(appearance['basis'].split()) not in ''.join(''.join(paragraphs[a-1:b]).split()):
        raise ValueError(f"{item['id']} 形象启用依据须出现在启用场原文")
    for scene in scenes.values():
        if item['id'] in scene['character_ids'] and (scene['number'] < start['number'] or stop and scene['number'] > stop['number']):
            raise ValueError(f"{item['id']} 在形象启用区间外出现，不能提前换形象")


def scene_ranges(workdir: Path, count: int, source_count: int) -> list[list[int]]:
    found = []
    for path in (workdir / "scenes").glob("scene_*.txt"):
        match = SCENE_FILE.fullmatch(path.name)
        if not match:
            raise ValueError(f"逐场文件名不符合格式：{path.name}")
        found.append(tuple(int(part) for part in match.groups()))
    found.sort()
    if len(found) != count:
        raise ValueError("逐场中间稿数量与标准剧本场景数不一致")
    expected = 1
    ranges = []
    for number, start, end in found:
        if number != len(ranges) + 1 or start != expected or end < start:
            raise ValueError(f"场景{number} 的原文段落范围不连续")
        ranges.append([start, end])
        expected = end + 1
    if expected != source_count + 1:
        raise ValueError("逐场中间稿未覆盖全部原文段落")
    return ranges


def fact_list(records: list[dict], paragraphs: list[str], scenes_by_id: dict, label: str,
              required_scene: Optional[str] = None) -> None:
    for fact in records:
        if not isinstance(fact, dict):
            raise ValueError(f"{label} 事实必须是对象")
        for key in ("text", "evidence", "attribution"):
            if not isinstance(fact.get(key), str) or not fact[key].strip():
                raise ValueError(f"{label} 事实缺少 {key}")
        refs = fact.get("source_paragraphs")
        scene_ids = fact.get("scene_ids")
        if not isinstance(refs, list) or not refs or any(
            not isinstance(ref, int) or isinstance(ref, bool) or ref < 1 or ref > len(paragraphs)
            for ref in refs
        ) or len(refs) != len(set(refs)):
            raise ValueError(f"{label} 原文段落编号无效")
        if not isinstance(scene_ids, list) or not scene_ids or len(scene_ids) != len(set(scene_ids)):
            raise ValueError(f"{label} 事实缺少有效的 scene_ids")
        if required_scene and scene_ids != [required_scene]:
            raise ValueError(f"{label} 逐场事实只能关联所在场景")
        evidence = "".join(fact["evidence"].split())
        covered_refs = set()
        for scene_id in scene_ids:
            scene = scenes_by_id.get(scene_id)
            if not scene:
                raise ValueError(f"{label} 引用了不存在的场景 {scene_id}")
            start, end = scene["source_range"]
            local_refs = sorted(ref for ref in refs if start <= ref <= end)
            if not local_refs:
                raise ValueError(f"{label} 在场景 {scene_id} 中没有原文证据")
            covered_refs.update(local_refs)
            # Only adjacent paragraphs in this scene may form one continuous
            # citation. Evidence from another scene or a gap cannot fill it in.
            originals, group, previous = [], [], None
            for ref in local_refs:
                if previous is not None and ref != previous + 1:
                    originals.append("".join("".join(group).split()))
                    group = []
                group.append(paragraphs[ref - 1])
                previous = ref
            originals.append("".join("".join(group).split()))
            if not any(evidence in original for original in originals):
                raise ValueError(f"{label} 的证据未出现在场景 {scene_id} 所引连续原文段落中：{fact['evidence']}")
        if covered_refs != set(refs):
            raise ValueError(f"{label} 引用了事实关联场景以外的原文段落")


def validate(script: list[dict], paragraphs: list[str], data: dict, *, require_designed=False) -> None:
    if data.get("schema_version") not in (2, 3):
        raise ValueError("不支持的资产台账 schema_version")
    validate_asset_lifecycle(data)
    verify_review_decisions(data)
    delivery_contract(data)
    if require_designed and not data.get("image_design_version"):
        raise ValueError("新项目完整资产交付须完成design；旧事实台账仅使用兼容读取入口")
    if data.get("source_digest") != digest(paragraphs):
        raise ValueError("资产台账与当前原文版本不匹配")
    if 'observations' in data:
        from scene_observations import validate_imported_observations
        validate_imported_observations(data, paragraphs, script)
    if ("design_source_scene_digests" in data
            and data["design_source_scene_digests"] != scene_source_digests(data, paragraphs)):
        raise ValueError("设计依赖场次摘要与当前原文不一致；通过design重建，不手改摘要")
    categories = {k: v for k, v in ID_PATTERNS.items() if k != "scene_assets" or data["schema_version"] == 3}
    for category in categories:
        if not isinstance(data.get(category), list):
            raise ValueError(f"资产台账缺少 {category} 数组")
    if len(data["scenes"]) != len(script):
        raise ValueError("台账场景数与标准剧本不一致")
    all_ids = set()
    for category, pattern in categories.items():
        for item in data[category]:
            asset_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(asset_id, str) or not pattern.fullmatch(asset_id):
                raise ValueError(f"{category} 存在无效 ID：{asset_id}")
            if asset_id in all_ids:
                raise ValueError(f"重复的资产 ID：{asset_id}")
            all_ids.add(asset_id)
    scenes_by_id = {item["id"]: item for item in data["scenes"]}
    characters = {item["id"]: item for item in data["characters"]}
    locations = {item["id"]: item for item in data["locations"]}
    props = {item["id"]: item for item in data["props"]}
    prop_primary_seen = {item["id"]: False for item in data["props"]}
    location_heading_names = {item["id"]: set() for item in data["locations"]}
    cast_union = {entry for item in script for entry in item["cast_entries"]}
    for item in data["characters"]:
        if item.get("source_entry", item.get("name")) not in cast_union:
            raise ValueError(f"人物资产名称不在任何人物行中：{item.get('name')}")
    for category in ("characters", "locations", "props"):
        for item in data[category]:
            if not isinstance(item.get("name"), str) or not item["name"].strip():
                raise ValueError(f"{item['id']} 缺少名称")
            for field in ('source_entry', 'source_name', 'source_speaker', 'visual_version', 'group_qualifier'):
                if field in item and (not isinstance(item[field], str) or not item[field].strip()):
                    raise ValueError(f"{item['id']} {field}须为非空字符串")
            if not isinstance(item.get("aliases"), list) or any(
                not isinstance(alias, str) or not alias.strip() for alias in item["aliases"]
            ):
                raise ValueError(f"{item['id']} 别名字段无效")
            facts = item.get("visual_facts")
            if not isinstance(facts, list):
                raise ValueError(f"{item['id']} 缺少 visual_facts 数组")
            fact_list(facts, paragraphs, scenes_by_id, item["id"])
            traits = []
            if category == "characters":
                traits = item.get("personality_facts")
                if not isinstance(traits, list):
                    raise ValueError(f"{item['id']} 缺少 personality_facts 数组")
                fact_list(traits, paragraphs, scenes_by_id, item["id"])
                validate_appearance(item, paragraphs, scenes_by_id)
                design = item.get('production_design', [])
                if not isinstance(design, list) or any(not isinstance(f, dict) or f.get('attribution') != '制作设计' for f in design):
                    raise ValueError(f"{item['id']} 制作设计须独立记录并标注制作设计")
                fact_list(design, paragraphs, scenes_by_id, item['id'])
                traits = traits + design
            linked_field = {"characters": "character_ids", "locations": "location_id", "props": "prop_ids"}[category]
            for fact in facts + traits:
                for scene_id in fact["scene_ids"]:
                    scene = scenes_by_id[scene_id]
                    linked = scene_character_ids(scene) if category == "characters" else scene[linked_field]
                    if item["id"] not in (linked if isinstance(linked, list) else [linked]):
                        raise ValueError(f"{item['id']} 的事实关联场景 {scene_id}，但资产未在该场出现")
    expected_start = 1
    for base, scene in zip(script, data["scenes"]):
        if scene.get("number") != base["number"] or scene.get("heading") != base["heading"]:
            raise ValueError(f"场景{base['number']} 与标准剧本不一致")
        source_range = scene.get("source_range")
        if not isinstance(source_range, list) or len(source_range) != 2 or any(
            not isinstance(value, int) or isinstance(value, bool) for value in source_range
        ):
            raise ValueError(f"场景{base['number']} 原文范围无效")
        start, end = source_range
        if start != expected_start or end < start:
            raise ValueError(f"场景{base['number']} 原文范围不连续")
        expected_start = end + 1
        location = locations.get(scene.get("location_id"))
        if not location or base["location_name"] not in [location["name"], *location["aliases"]]:
            raise ValueError(f"场景{base['number']} 地点关联缺失或名称不匹配")
        location_heading_names[location["id"]].add(base["location_name"])
        char_ids, prop_ids = scene.get("character_ids"), scene.get("prop_ids")
        if not isinstance(char_ids, list) or len(char_ids) != len(set(char_ids)) or any(
            ref not in characters for ref in char_ids
        ):
            raise ValueError(f"场景{base['number']} 人物关联无效")
        if not isinstance(prop_ids, list) or len(prop_ids) != len(set(prop_ids)) or any(
            ref not in props for ref in prop_ids
        ):
            raise ValueError(f"场景{base['number']} 道具关联无效")
        linked_names = [characters[ref].get("source_entry", characters[ref]["name"]) for ref in char_ids]
        if linked_names != base["cast_entries"]:
            raise ValueError(f"场景{base['number']} 人物资产名称及顺序须逐字对应人物行完整条目")
        body = "".join(base["body_text"].split())
        for ref in prop_ids:
            item = props[ref]
            variants = [item.get("source_name", item["name"]), *item["aliases"]]
            if not any("".join(value.split()) in body for value in variants):
                raise ValueError(f"场景{base['number']} 道具 {ref} 的名称或别名未出现在该场剧本正文中")
            if "".join(item.get("source_name", item["name"]).split()) in body:
                prop_primary_seen[ref] = True
        occurrences = scene.get('prop_occurrences', [])
        if not isinstance(occurrences, list):
            raise ValueError(f"场景{base['number']} 道具实体记录须为数组")
        occurrence_ids = set()
        for occurrence in occurrences:
            count = occurrence.get('count')
            oid = occurrence.get('id')
            if not isinstance(oid, str) or not oid or oid in occurrence_ids or occurrence.get('asset_id') not in prop_ids or (count is not None and (type(count) is not int or count < 1)):
                raise ValueError(f"场景{base['number']} 道具实体记录无效；共用图片不合并实体/数量")
            occurrence_ids.add(oid)
        for ref in char_ids:
            validate_source_speaker(characters[ref], context=f"场景{base['number']}")
        if not isinstance(scene.get("visual_facts"), list):
            raise ValueError(f"场景{base['number']} 缺少 visual_facts 数组")
        fact_list(scene["visual_facts"], paragraphs, scenes_by_id, scene["id"], scene["id"])
        basis = scene.get("time_basis")
        if not isinstance(basis, dict) or basis.get("kind") not in ("原文明示", "格式默认"):
            raise ValueError(f"场景{base['number']} 须确认时间依据")
        refs = basis.get("source_paragraphs")
        if not isinstance(refs, list) or any(not isinstance(ref, int) or isinstance(ref, bool) or ref < start or ref > end for ref in refs):
            raise ValueError(f"场景{base['number']} 时间证据段落无效")
        if (basis["kind"] == "原文明示" and not refs) or (basis["kind"] == "格式默认" and refs):
            raise ValueError(f"场景{base['number']} 时间依据与证据段落不一致")
    if expected_start != len(paragraphs) + 1:
        raise ValueError("台账场景范围未覆盖全部原文")
    for ref, seen in prop_primary_seen.items():
        if not seen:
            raise ValueError(f"道具 {ref} 的主名称未出现在关联场景的剧本正文中")
    for ref, names in location_heading_names.items():
        if names and locations[ref]["name"] not in names:
            raise ValueError(f"地点 {ref} 的主名称未出现在关联场景标题中")
    if data["schema_version"] == 3:
        validate_scene_assets(data, paragraphs, scenes_by_id, locations)
    reference_preflight(data)
    validate_character_variants(data, paragraphs)
    validate_prop_policy(data, paragraphs, fact_list)
    validate_group_policy(data, paragraphs, fact_list, script)
    validate_visual_design(data)


def candidate_data(data):
    """Validate author facts independently of intentionally stale derivatives."""
    result = copy.deepcopy(data)
    for field in ("image_design_version", "visual_style", "identity_anchors", "design_source_scene_digests"):
        result.pop(field, None)
    for category in ("characters", "scene_assets", "props"):
        for item in result.get(category, []):
            for field in ("visual_design", "image_prompt", "image_format", "image_design_digest"):
                item.pop(field, None)
    return result


def preflight(script_path, source_path, ledger_path, stage="candidate"):
    data = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    paragraphs = source_paragraphs(source_path)
    if stage not in ("candidate", "designed"):
        raise ValueError("preflight阶段须为candidate或designed")
    if data.get("schema_version") != 3:
        raise ValueError("当前资产前检须使用schema 3；旧台账先按迁移流程审阅")
    if stage == "designed" and not data.get("image_design_version"):
        raise ValueError("designed前检须有完整美术设计；候选台账不能冒充成品")
    validate(parse_script(script_path), paragraphs, candidate_data(data) if stage == "candidate" else data)
    return {"status": "passed", "stage": stage, "semantic_review": "not_performed",
            "inputs": {name: {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                       for name, path in (("script", script_path), ("source", source_path), ("ledger", ledger_path))},
            "counts": {category: len(data.get(category, [])) for category in ("scenes", "characters", "scene_assets", "props")}}


def temporal_context(data, paragraphs=None):
    """Validate reviewed chronology metadata; never infer a jump from prose."""
    layers = data.get("temporal_layers", [])
    if not isinstance(layers, list):
        raise ValueError("temporal_layers 必须是数组")
    scenes = {s["id"]: s for s in data["scenes"]}
    marked = bool(layers or any("temporal_layer_id" in s or "set_reuse" in s for s in scenes.values())
                  or any(a.get("variant") == "temporal" or "temporal_layer_id" in a for a in data.get("scene_assets", [])))
    if marked and paragraphs is None:
        raise ValueError("时间层及原样复用须提供 --source，以校验原文证据；不从全文自动识别跳时")
    if marked and data.get("source_digest") != digest(paragraphs):
        raise ValueError("时间层台账与 --source 原文摘要不匹配")

    def evidence_in(record, scene, label):
        evidence, refs = record.get("evidence"), record.get("source_paragraphs")
        if not isinstance(evidence, str) or not evidence.strip() or not isinstance(refs, list) or not refs:
            raise ValueError(f"{label} 须登记连续原文证据及段落")
        start, end = scene["source_range"]
        if any(type(r) is not int or r < start or r > end or r > len(paragraphs) for r in refs) or refs != list(range(min(refs), max(refs) + 1)):
            raise ValueError(f"{label} 证据段落须在依据场内连续、无重复且按顺序登记")
        original = "".join("".join(paragraphs[r - 1] for r in refs).split())
        if "".join(evidence.split()) not in original:
            raise ValueError(f"{label} 证据未出现在所引连续原文段落")

    by_id = {}
    for layer in layers:
        if not isinstance(layer, dict) or any(not isinstance(layer.get(k), str) or not layer[k].strip()
            for k in ("id", "label", "start_scene_id", "review_basis")):
            raise ValueError("时间层须登记 id、label、起始场及原文审阅的跳时依据 review_basis")
        if layer["id"] in by_id:
            raise ValueError(f"重复时间层：{layer['id']}")
        validate_image_name(layer["label"])
        start = scenes.get(layer["start_scene_id"])
        if not start or start.get("temporal_layer_id") != layer["id"]:
            raise ValueError(f"时间层 {layer['id']} 的起始场须明确关联该层")
        evidence_in(layer, start, f"时间层 {layer['id']}")
        if "".join(layer["label"].split()) not in "".join(layer["evidence"].split()):
            raise ValueError(f"时间层 {layer['id']} label 须取证据中的时间跨度原词")
        by_id[layer["id"]] = layer
    first_jump = min((scenes[layer["start_scene_id"]]["number"] for layer in layers), default=None)
    for scene in scenes.values():
        lid = scene.get("temporal_layer_id")
        if first_jump is not None and scene["number"] >= first_jump and "temporal_layer_id" not in scene:
            raise ValueError(f"场次 {scene['id']} 须明确登记实际时间层；回忆原始层用 temporal_layer_id=null")
        if "temporal_layer_id" in scene and lid is not None and lid not in by_id:
            raise ValueError(f"场次 {scene['id']} 引用了未审阅确认的时间层")
        if lid and scene["number"] < scenes[by_id[lid]["start_scene_id"]]["number"]:
            raise ValueError(f"场次 {scene['id']} 早于其时间层首次启用场")
        reuse = scene.get("set_reuse")
        if reuse is not None:
            target = scenes.get(reuse.get("same_as_scene_id")) if isinstance(reuse, dict) else None
            if not lid or not target or target["number"] >= scene["number"] or target["location_id"] != scene["location_id"]:
                raise ValueError(f"场次 {scene['id']} 原样复用须明确指向较早的同 LOC 基准场")
            evidence_in(reuse, scene, f"场次 {scene['id']} 原样复用")
    return by_id


def validate_set_parents(assets):
    for item in assets.values():
        seen, current = set(), item
        while current.get("parent_id") is not None:
            if current["id"] in seen:
                raise ValueError(f"布景 {item['id']} 的 parent_id 存在循环")
            seen.add(current["id"])
            parent = assets.get(current["parent_id"])
            if parent is None or parent["location_id"] != item["location_id"]:
                raise ValueError(f"布景 {item['id']} 的 parent_id 不存在或属于其他 LOC")
            current = parent


def validate_temporal_origins(data):
    scenes = sorted(data["scenes"], key=lambda s: s["number"])
    for asset in data["scene_assets"]:
        if asset.get("variant") == "temporal":
            own = [s for s in scenes if s.get("scene_asset_id") == asset["id"] and
                   s.get("temporal_layer_id") == asset.get("temporal_layer_id")]
            if not own or not any(s.get("scene_asset_id") == asset.get("parent_id") and
                                 s["number"] < own[0]["number"] for s in scenes):
                raise ValueError(f"时间布景 {asset['id']} 须在所属层实际使用，parent 须为此前使用的同 LOC 布景；请审核迁移")
        elif asset.get("variant") == "base" and "temporal_layer_id" in asset:
            first = next(s for s in scenes if s["location_id"] == asset["location_id"])
            if asset["temporal_layer_id"] != first.get("temporal_layer_id"):
                raise ValueError(f"基础布景 {asset['id']} 的时间层须对应该 LOC 首次出场")


def validate_scene_assets(data, paragraphs, scenes, locations):
    """SC identifies an occurrence; SET identifies a reusable visual set."""
    assets = {item["id"]: item for item in data["scene_assets"]}
    names = {item["name"] for category in ("characters", "props") for item in data[category]}
    bases = {}
    for location in locations.values():
        if location.get("identity_status") != "confirmed" or not str(location.get("identity_basis", "")).strip():
            raise ValueError(f"地点 {location['id']} 身份尚未核实，不能建立布景资产")
    for item in assets.values():
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or name != name.strip() or name in names or name == "黑幕" or any(c in name for c in "@{}\r\n"):
            raise ValueError(f"布景资产名称为空、重复或含引用分隔符：{item['id']}")
        validate_image_name(name)
        names.add(name)
        location = locations.get(item.get("location_id"))
        if not location:
            raise ValueError(f"布景资产 {item['id']} 的地点不存在")
        variant = item.get("variant")
        if variant == "base":
            if item.get("parent_id") is not None or location["id"] in bases:
                raise ValueError(f"地点 {location['id']} 只能有一个基础布景")
            bases[location["id"]] = item["id"]
        elif variant == "decor":
            parent = assets.get(item.get("parent_id"))
            if not parent or parent.get("variant") != "base" or parent.get("location_id") != location["id"]:
                raise ValueError(f"布景版本 {item['id']} 必须关联同地点的基础布景")
            if not item.get("visual_facts"):
                raise ValueError(f"布景版本 {item['id']} 缺少真实布景变化的原文证据")
        elif variant == "temporal":
            parent = assets.get(item.get("parent_id"))
            if not parent or parent.get("location_id") != location["id"] or parent["id"] == item["id"]:
                raise ValueError(f"时间布景 {item['id']} 必须关联同 LOC 的较早布景 parent_id")
            if not item.get("temporal_layer_id"):
                raise ValueError(f"时间布景 {item['id']} 缺少已审阅时间层")
        else:
            raise ValueError(f"布景资产 {item['id']} variant 须为 base、decor 或 temporal")
        facts = item.get("visual_facts")
        if not isinstance(facts, list):
            raise ValueError(f"布景资产 {item['id']} 缺少 visual_facts")
        fact_list(facts, paragraphs, scenes, item["id"])
        for fact in facts:
            for sid in fact["scene_ids"]:
                scene = scenes[sid]
                if scene["location_id"] != location["id"] or (variant != "base" and scene.get("scene_asset_id") != item["id"]):
                    raise ValueError(f"布景资产 {item['id']} 的证据关联了其他地点或版本")
    for scene in scenes.values():
        asset = assets.get(scene.get("scene_asset_id"))
        if not asset or asset["location_id"] != scene["location_id"]:
            raise ValueError(f"场次 {scene['id']} 缺少同地点的有效 scene_asset_id")
    used_locations = {scene["location_id"] for scene in scenes.values()}
    if set(bases) != used_locations or set(locations) != used_locations:
        raise ValueError("每个现用地点须恰有一个基础布景；未用地点请归档而非创建节点")
    used_assets = {scene["scene_asset_id"] for scene in scenes.values()}
    if any(item["variant"] != "base" and item["id"] not in used_assets for item in assets.values()):
        raise ValueError("未使用的布景版本须先核实去向，不创建多余节点")
    validate_set_parents(assets)
    # The same deterministic policy checks existing links without rewriting them.
    planned = build_scene_assets(data, paragraphs)
    if len(planned["scene_assets"]) != len(data["scene_assets"]):
        raise ValueError("台账缺少按已审阅时间层建立的布景；请离线 build-scenes 并审核迁移")


def build_scene_assets(data, paragraphs=None):
    """Group only by explicitly confirmed LOC IDs, never by heading/name/daylight."""
    if data.get("schema_version") != 3:
        raise ValueError("旧台账须先制定保留/修改/合并/删除方案并审核升级，不自动转换")
    result = copy.deepcopy(data)
    layers = temporal_context(result, paragraphs)
    locations = {item["id"]: item for item in result["locations"]}
    used_locations = {scene["location_id"] for scene in result["scenes"]}
    if set(locations) != used_locations:
        raise ValueError("请先合并已确认同一实体的地点，并归档未用地点")
    for item in locations.values():
        if item.get("identity_status") != "confirmed" or not str(item.get("identity_basis", "")).strip():
            raise ValueError(f"地点 {item['id']} 身份尚未核实；不按同名自动合并")
    assets = result.setdefault("scene_assets", [])
    used_ids = {item["id"] for item in assets} | set(result.get("retired_ids", []))
    number = max([int(a[3:]) for a in used_ids if re.fullmatch(r"SET\d{4,}", a)] + [0])
    bases = {}
    for item in assets:
        if item.get("variant") == "base":
            lid = item["location_id"]
            if lid in bases:
                raise ValueError(f"地点 {lid} 有重复基础布景；请先审核合并")
            bases[lid] = item["id"]
    new_bases = set()
    for lid, location in locations.items():
        if lid not in bases:
            number += 1
            aid = f"SET{number:04d}"
            bases[lid] = aid
            assets.append({"id": aid, "name": location["name"], "location_id": lid,
                           "variant": "base", "parent_id": None, "visual_facts": []})
            new_bases.add(aid)
    by_id = {item["id"]: item for item in assets}
    validate_set_parents(by_id)
    if not layers:
        # Preserve legacy schema-3 base/decor associations and explicit changes.
        for scene in result["scenes"]:
            aid = scene.get("scene_asset_id")
            if aid is None:
                scene["scene_asset_id"] = bases[scene["location_id"]]
            elif aid not in by_id or by_id[aid]["location_id"] != scene["location_id"]:
                raise ValueError(f"场次 {scene['id']} 已有布景关联无效；请审核，不自动覆盖")
        reference_preflight(result)
        return result
    temporal = {}
    for item in assets:
        if item.get("variant") == "temporal":
            key = (item["location_id"], item.get("temporal_layer_id"))
            if key[1] not in layers or key in temporal:
                raise ValueError(f"布景 {item['id']} 的时间层未核实或同 LOC 同层有重复时间版本")
            temporal[key] = item["id"]
    layer_sets, layer_origins, previous, first_layers = {}, {}, {}, {}
    scenes = {s["id"]: s for s in result["scenes"]}
    for scene in sorted(result["scenes"], key=lambda s: s["number"]):
        aid = scene.get("scene_asset_id")
        lid, layer_id = scene["location_id"], scene.get("temporal_layer_id")
        key = (lid, layer_id)
        if aid is not None and (aid not in by_id or by_id[aid]["location_id"] != lid):
            raise ValueError(f"场次 {scene['id']} 已有布景关联无效；请审核，不自动覆盖")
        if lid not in first_layers:
            first_layers[lid] = layer_id
            if bases[lid] in new_bases and layer_id is not None:
                by_id[bases[lid]]["temporal_layer_id"] = layer_id
        reuse = scene.get("set_reuse")
        if reuse:
            if key in layer_sets:
                raise ValueError(f"场次 {scene['id']} 原样复用须登记于该时间层同 LOC 首次出场，避免追改已有层")
            desired = scenes[reuse["same_as_scene_id"]].get("scene_asset_id")
            if desired not in by_id:
                raise ValueError(f"场次 {scene['id']} 原样基准场尚无有效布景关联")
        elif key in layer_sets:
            desired = layer_sets[key]
        elif first_layers[lid] == layer_id:
            desired = bases[lid]
        elif layer_id is None:
            raise ValueError(f"场次 {scene['id']} 回到原始时间层但缺少此前确认的布景；请核实回忆时间层")
        else:
            desired = temporal.get(key)
            if desired is None:
                if aid is not None:
                    raise ValueError(f"场次 {scene['id']} 已有 SET 与新时间层不一致；须先审核映射/绑定迁移，不自动覆盖")
                number += 1
                desired = f"SET{number:04d}"
                item = {"id": desired, "name": locations[lid]["name"] + "-" + layers[layer_id]["label"],
                        "location_id": lid, "variant": "temporal", "parent_id": previous[lid],
                        "temporal_layer_id": layer_id, "visual_facts": []}
                assets.append(item)
                by_id[desired] = item
                temporal[key] = desired
        if aid is not None and aid != desired:
            # Existing decor states remain explicit choices within one layer.
            explicit_state = (key in layer_sets and (by_id[aid].get("variant") == "decor" or aid == layer_origins[key])) or (
                first_layers[lid] == layer_id and (by_id[aid].get("variant") == "decor" or aid == bases[lid]))
            if reuse or not explicit_state:
                raise ValueError(f"场次 {scene['id']} 已有 SET 与已审阅时间层/原样基准不一致；须审核迁移，不自动覆盖")
            desired = aid
        if by_id[desired].get("variant") == "temporal" and by_id[desired].get("temporal_layer_id") != layer_id:
            # An explicit unchanged-state citation can reuse an earlier layer.
            if not reuse and key not in layer_sets:
                raise ValueError(f"场次 {scene['id']} 时间布景层关联不一致")
        scene["scene_asset_id"] = desired
        layer_origins.setdefault(key, desired)
        layer_sets[key] = desired
        previous[lid] = desired
    for item in assets:
        if "temporal_layer_id" in item and item["temporal_layer_id"] not in layers:
            raise ValueError(f"布景 {item['id']} 引用不存在的时间层")
    validate_temporal_origins(result)
    reference_preflight(result)
    return result


def initialize(script_path: Path, workdir: Path, output: Path, *, observations=None, observations_review=None, project=None) -> None:
    if output.exists():
        raise ValueError("台账已存在；请沿用旧 ID，不要重新初始化覆盖")
    script = parse_script(script_path)
    paragraphs = source_paragraphs(workdir / "source.json")
    ranges = scene_ranges(workdir, len(script), len(paragraphs))
    character_ids = {}
    characters, locations, scenes = [], [], []
    for base, source_range in zip(script, ranges):
        location_name = base["location_name"]
        # Identical generic headings can denote different places. Start separately;
        # merge only after checking the original scene context.
        location_id = f"LOC{len(locations) + 1:04d}"
        locations.append({"id": location_id, "name": location_name, "aliases": [], "visual_facts": [],
                          "identity_status": "pending", "identity_basis": ""})
        linked = []
        for entry in base["cast_entries"]:
            role_name, _ = split_cast_entry(entry)
            key = (entry, base["number"]) if GENERIC_ROLE.match(role_name) or role_name.endswith("-群像") else (entry, None)
            if key not in character_ids:
                asset_id = f"CHAR{len(characters) + 1:04d}"
                character_ids[key] = asset_id
                characters.append({
                    "id": asset_id, "name": entry + '-' + location_name if role_name.endswith('-群像') else entry,
                    "source_entry": entry, "aliases": [],
                    "visual_facts": [], "personality_facts": [],
                })
                if role_name.endswith('-群像'):
                    # This is a draft until the author reviews all occurrences.
                    characters[-1]['visual_mode'] = 'prompt_only'
            linked.append(character_ids[key])
        scenes.append({
            "id": f"SC{base['number']:04d}", "number": base["number"],
            "heading": base["heading"], "source_range": source_range,
            "location_id": location_id, "scene_asset_id": None, "character_ids": linked,
            "prop_ids": [], "visual_facts": [],
            "time_basis": {"kind": "待核", "source_paragraphs": []},
        })
    data = {
        "schema_version": 3, "asset_policy": PROP_POLICY, "group_policy": GROUP_POLICY,
        "source_digest": digest(paragraphs), "scene_assets": [],
        "scenes": scenes, "characters": characters, "locations": locations,
        "props": [], "retired_ids": [], "inactive_assets": {"characters": []}, "pending_reviews": [],
        "prop_coverage_review": {"source_digest": digest(paragraphs), "reviewed_full_script": False,
                                 "reviewed_scene_ids": [], "candidates": []},
    }
    if project is not None:
        from project_contract import read_contract
        contract = read_contract(project)
        if contract is None:
            raise ValueError('新项目须先project-init保存交付契约')
        data['delivery_contract'] = contract
    if observations is not None:
        from scene_observations import import_observations
        payload = json.loads(Path(observations).read_text(encoding='utf-8-sig'))
        review = json.loads(Path(observations_review).read_text(encoding='utf-8-sig')) if observations_review else None
        data, _ = import_observations(data, payload, workdir / 'source.json', workdir, review)
    # The index is a draft until each scene's time basis is checked.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已建立 {len(scenes)} 场、{len(characters)} 人物、{len(locations)} 地点的资产草稿台账：{output}")


def render_fact(fact: dict) -> str:
    refs = "、".join(str(value) for value in fact["source_paragraphs"])
    return f"{fact['text']}（{fact['attribution']}；原文段落 {refs}：{fact['evidence']}）"


ATMOSPHERE = re.compile(r"黑|暗|光|雷|雨|雪|雾|夜色|暖色|潮湿|微凉|明亮")


def visual_text(facts: list[dict]) -> list[str]:
    return list(dict.fromkeys(fact["text"].strip().rstrip("。") for fact in facts))


def set_visual_facts(item, locations, assets):
    """Temporal parent links record lineage, not continued decor conditions."""
    common = locations[item["location_id"]]["visual_facts"]
    if item["variant"] != "base":
        base = next(a for a in assets.values() if a["location_id"] == item["location_id"] and a["variant"] == "base")
        common = common + base["visual_facts"]
    return common + item["visual_facts"]


def render(script_path: Path, source_path: Path, ledger_path: Path, output: Path, *, require_designed=False) -> None:
    script = parse_script(script_path)
    paragraphs = source_paragraphs(source_path)
    data = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    validate(script, paragraphs, data, require_designed=requires_design(data, explicit=require_designed))
    characters = {item["id"]: item for item in data["characters"]}
    locations = {item["id"]: item for item in data["locations"]}
    props = {item["id"]: item for item in data["props"]}
    scene_assets = {item["id"]: item for item in data.get("scene_assets", [])}
    lines = ["角色列表"]
    for number, item in enumerate(visual_assets(data, "characters"), 1):
        features = "、".join(visual_text(item["visual_facts"])) or "外观未明示"
        lines.append(f"角色{number:02d} - {item['name']}：" + (item["image_prompt"] if item.get("image_prompt") else features + "。"))
        if item.get('production_design') and not item.get("image_prompt"):
            lines.append('  制作设计：' + '、'.join(visual_text(item['production_design'])) + '。')
        numbers = sorted({scene["number"] for scene in data["scenes"] if item["id"] in scene_character_ids(scene)})
        lines.append("  出现的场景：" + ("，".join(f"场景{number}" for number in numbers) or "无") + "。")

    lines.extend(["", "场景列表"])
    for number, scene in enumerate(data["scene_assets"] if data["schema_version"] == 3 else data["scenes"], 1):
        loc = locations[scene["location_id"]]
        if data["schema_version"] == 3:
            elements = visual_text(set_visual_facts(scene, locations, scene_assets))
            description = scene.get("image_prompt")
            lines.append(f"场景{number:02d} - {scene['name']}" + ("：" + description if description else f"｜关键元素：{'、'.join(dict.fromkeys(elements)) or '未明示'}。"))
            numbers = sorted(s["number"] for s in data["scenes"] if s["scene_asset_id"] == scene["id"])
            lines.append("  使用的场次：" + ("，".join(f"场景{n}" for n in numbers) or "无（基础布景供版本继承）") + "。")
            continue
        scene_facts = visual_text(scene["visual_facts"])
        atmosphere = [part for part in scene_facts if ATMOSPHERE.search(part)]
        elements = [part for part in scene_facts if part not in atmosphere]
        elements += visual_text(loc["visual_facts"])
        elements += [props[ref]["name"] for ref in scene["prop_ids"]]
        elements = list(dict.fromkeys(elements))
        if not elements:
            elements = [script[scene["number"] - 1]["location_name"]]
        lines.append(f"场景{number:02d} - {scene['heading']}｜氛围：{'、'.join(atmosphere) or '未明示'}；关键元素：{'、'.join(elements)}。")

    lines.extend(["", "主要道具列表"])
    for number, item in enumerate(data["props"], 1):
        features = "、".join(visual_text(item["visual_facts"])) or "外形未明示"
        lines.append(f"道具{number:02d} - {item['name']}：" + (item["image_prompt"] if item.get("image_prompt") else features + "。"))
        numbers = sorted({scene["number"] for scene in data["scenes"] if item["id"] in scene["prop_ids"]})
        lines.append("  使用的场次：" + ("，".join(f"场景{n}" for n in numbers) or "无") + "。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=output.parent, delete=False) as temp:
        temp.write("\n".join(lines) + "\n")
        temp_path = Path(temp.name)
    try:
        os.replace(temp_path, output)
    finally:
        temp_path.unlink(missing_ok=True)
    print(f"已验证并生成资产 TXT：{output}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="从剧本和逐场稿建立空事实资产索引")
    init.add_argument("script", type=Path)
    init.add_argument("workdir", type=Path)
    init.add_argument("--output", type=Path, required=True)
    init.add_argument('--project', type=Path, help='新项目的交付契约目录')
    init.add_argument('--observations', type=Path)
    init.add_argument('--observations-review', type=Path)
    build = commands.add_parser("build-scenes", help="按已核实LOC及人工审阅时间层建立布景，不扫描全文猜跳时")
    build.add_argument("ledger", type=Path)
    build.add_argument("--source", type=Path, help="时间层/原样复用元数据存在时必需，提供 prepare 生成的 source.json")
    build.add_argument("--output", type=Path, required=True)
    normalize = commands.add_parser('normalize', help='新未绑定台账的群像地点命名、同名道具视觉复用及全局引用预检')
    normalize.add_argument('ledger', type=Path)
    normalize.add_argument('--output', type=Path, required=True)
    normalize.add_argument('--report', type=Path, required=True)
    normalize.add_argument('--group-review', type=Path, help='对normalize后全部群像的显式筛选审阅JSON')
    normalize.add_argument('--source', type=Path, help='--group-review时必需，校验原文版本与独立出场证据')
    view = commands.add_parser("render", help="验证台账并生成易读资产 TXT")
    view.add_argument("script", type=Path)
    view.add_argument("source", type=Path)
    view.add_argument("ledger", type=Path)
    view.add_argument("--output", type=Path, required=True)
    view.add_argument("--require-designed", action="store_true", help="新项目完整资产交付门槛；兼容读取旧事实台账时不指定")
    asset_check = commands.add_parser("validate", aliases=["preflight"], help="只读全量前检；新项目契约自动要求完整设计，候选检查显式用--candidate")
    asset_check.add_argument("script", type=Path)
    asset_check.add_argument("source", type=Path)
    asset_check.add_argument("ledger", type=Path)
    asset_check.add_argument("--require-designed", action="store_true")
    asset_check.add_argument('--candidate', action='store_true', help='显式候选前检，不签发完整交付通过')
    asset_check.add_argument("--stage", choices=("candidate", "designed"), help="兼容显式前检阶段；不与交付选项混用")
    asset_check.add_argument("--report", type=Path, help="可选前检报告，须为新文件")
    template = commands.add_parser("review-template", help="依据明确的kind及稳定ID生成待填审阅模板和真实摘要，不自动确认语义")
    template.add_argument("ledger", type=Path)
    template.add_argument("selections", type=Path)
    template.add_argument("--output", type=Path, required=True)
    for command in ("review-apply", "review-verify", "review-check"):
        decision = commands.add_parser(command, help="按稳定ID应用审阅决定并全量校验" if command == "review-apply" else "只读核对审阅决定与实际台账")
        decision.add_argument("script", type=Path)
        decision.add_argument("source", type=Path)
        decision.add_argument("ledger", type=Path)
        decision.add_argument("decisions", type=Path)
        if command != 'review-verify':
            decision.add_argument('--batch', type=Path, action='append', default=[], help='同一冻结输入的其他非重叠决定草稿')
        if command == "review-apply":
            decision.add_argument("--output", type=Path, required=True)
            decision.add_argument("--report", type=Path, required=True)
    variants = commands.add_parser("variants", help="按已审原文登记同一人物的佩戴形象与同场切换，输出新草稿后重新design")
    variants.add_argument("script", type=Path)
    variants.add_argument("source", type=Path)
    variants.add_argument("ledger", type=Path)
    variants.add_argument("review_json", type=Path)
    variants.add_argument("--output", type=Path, required=True)
    design = commands.add_parser("design", help="校验作者的制作设计JSON并确定性编译GPT-image-2资产提示词，不调用生图")
    design.add_argument("script", type=Path)
    design.add_argument("source", type=Path)
    design.add_argument("ledger", type=Path)
    design.add_argument("design_json", type=Path)
    design.add_argument("--style-file", type=Path, required=True)
    design.add_argument("--output", type=Path, required=True)
    design.add_argument("--catalog-output", type=Path)
    design.add_argument("--patch", action="store_true", help="仅更新变化设计并校验未变依赖")
    preview = commands.add_parser("design-check", help="只读检查指定设计及依赖闭包；局部预览不能作为交付或绑定")
    preview.add_argument("script", type=Path)
    preview.add_argument("source", type=Path)
    preview.add_argument("ledger", type=Path)
    preview.add_argument("design_json", type=Path)
    preview.add_argument("--style-file", type=Path, required=True)
    preview.add_argument("--asset-id", action="append", required=True)
    preview.add_argument("--expected-hashes", type=Path, help="script/source/ledger/design/style文件SHA256对象")
    impact = commands.add_parser('impact', help='只读列出直接、依赖与未知影响；不复用旧语义通过')
    impact.add_argument('before_ledger', type=Path)
    impact.add_argument('after_ledger', type=Path)
    for name in ('before-source', 'after-source', 'before-design', 'after-design'):
        impact.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'impact':
            from asset_impact import analyze_asset_impact
            def read_optional(path):
                return json.loads(path.read_text(encoding='utf-8-sig')) if path else None
            print(json.dumps(analyze_asset_impact(read_optional(args.before_ledger), read_optional(args.after_ledger),
                             before_source=read_optional(args.before_source), after_source=read_optional(args.after_source),
                             before_design=read_optional(args.before_design), after_design=read_optional(args.after_design)), ensure_ascii=False, indent=2))
        elif args.command == "init":
            initialize(args.script, args.workdir, args.output, observations=args.observations,
                       observations_review=args.observations_review, project=args.project)
        elif args.command == "review-template":
            if args.output.exists() or args.output.resolve() in {args.ledger.resolve(), args.selections.resolve()}:
                raise ValueError("审阅模板须保存为新文件，不覆盖台账或选择文件")
            template = prepare_review_payload(json.loads(args.ledger.read_text(encoding="utf-8-sig")),
                                              json.loads(args.selections.read_text(encoding="utf-8-sig")))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(template, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"status": "review_template", "output": str(args.output.resolve()), "semantic_review": "required"}, ensure_ascii=False))
        elif args.command in ("validate", "preflight") and args.stage is not None:
            if args.candidate or args.require_designed:
                raise ValueError("--stage不能与--candidate或--require-designed混用")
            if args.report and args.report.exists():
                raise ValueError("前检报告须为新文件，不覆盖输入或历史证据")
            report = preflight(args.script, args.source, args.ledger, args.stage)
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
        elif args.command in ("validate", "preflight", "review-apply", "review-verify", "review-check"):
            script = parse_script(args.script)
            paragraphs = source_paragraphs(args.source)
            data = json.loads(args.ledger.read_text(encoding="utf-8-sig"))
            if args.command == 'review-check':
                from asset_review import check_review_drafts
                report = check_review_drafts(script, paragraphs, data, [json.loads(path.read_text(encoding='utf-8-sig')) for path in [args.decisions, *args.batch]])
            elif args.command == "review-apply":
                outputs = [args.output, args.report]
                inputs = [args.script, args.source, args.ledger, args.decisions, *args.batch]
                if any(path.exists() for path in outputs) or len({path.resolve() for path in outputs}) != 2 or any(path.resolve() in {p.resolve() for p in inputs} for path in outputs):
                    raise ValueError("审阅应用的台账和报告须为不同的新文件，不覆盖输入或既有产物")
                payload = json.loads(args.decisions.read_text(encoding="utf-8-sig"))
                if args.batch:
                    from asset_review import apply_review_batches
                    result, report = apply_review_batches(data, [payload, *[json.loads(path.read_text(encoding='utf-8-sig')) for path in args.batch]])
                else:
                    result, report = apply_review_decisions(data, payload)
                # Nothing is written until actual fields and all source,
                # identity, policy, lifecycle and design contracts pass.
                validate(script, paragraphs, result)
                report["full_validation"] = "passed"
                report["output_ledger"] = str(args.output.resolve())
                serialized = [(args.output, result), (args.report, report)]
                for path, value in serialized:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("x", encoding="utf-8", newline="\n") as stream:
                        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            else:
                candidate = getattr(args, 'candidate', False)
                gate = requires_design(data, explicit=getattr(args, 'require_designed', False)) if args.command != 'review-verify' else False
                if candidate and getattr(args, 'require_designed', False):
                    raise ValueError('--candidate与--require-designed不能同时指定')
                validate(script, paragraphs, data, require_designed=gate and not candidate)
                report = verify_review_decisions(data, json.loads(args.decisions.read_text(encoding="utf-8-sig"))) if args.command == "review-verify" else verify_review_decisions(data)
                report = {"status": "candidate_checked" if candidate else "passed", "require_designed": gate and not candidate, "decision_check": report,
                          **({'formal_ready': False} if candidate else {})}
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "design-check":
            from design_preview import preview_from_files
            expected = json.loads(args.expected_hashes.read_text(encoding="utf-8-sig")) if args.expected_hashes else None
            print(json.dumps(preview_from_files(args.script, args.source, args.ledger, args.design_json,
                                                args.style_file, args.asset_id, expected_hashes=expected), ensure_ascii=False, indent=2))
        elif args.command == 'normalize':
            if args.output.resolve() == args.report.resolve() or args.output.exists() or args.report.exists():
                raise ValueError('归一化输出/报告须为不同的新文件；不覆盖已绑定台账或映射')
            original = json.loads(args.ledger.read_text(encoding='utf-8-sig'))
            data, report = normalize_assets(original)
            if args.group_review:
                if not args.source:
                    raise ValueError('--group-review须提供--source校验原文证据')
                paragraphs = source_paragraphs(args.source)
                if data.get('source_digest') != digest(paragraphs):
                    raise ValueError('群像审阅原文版本与台账不匹配')
                data = apply_group_review(data, json.loads(args.group_review.read_text(encoding='utf-8-sig')))
                validate_group_policy(data, paragraphs, fact_list)
                report['group_reviews'] = [{'id': item['id'], 'visual_mode': item['visual_mode']}
                                           for item in data['characters'] if 'visual_mode' in item]
                from asset_review import assert_normalize_review_safe
                report['review_impact'] = assert_normalize_review_safe(original, data, report)
            for path, value in ((args.output, data), (args.report, report)):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(f"已完成命名预检：改名{len(report['renamed'])}项，复用{len(report['redirects'])}项")
        elif args.command == "build-scenes":
            if args.output.exists():
                raise ValueError("输出已存在；请另选文件，避免覆盖人工修改")
            data = build_scene_assets(json.loads(args.ledger.read_text(encoding="utf-8-sig")),
                                      source_paragraphs(args.source) if args.source else None)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"已建立 {len(data['scene_assets'])} 个布景资产索引：{args.output}")
        elif args.command == "variants":
            if args.output.exists():
                raise ValueError("形象审阅输出须为新文件，不覆盖已绑定台账")
            paragraphs = source_paragraphs(args.source)
            data = json.loads(args.ledger.read_text(encoding="utf-8-sig"))
            result = apply_variant_review(data, json.loads(args.review_json.read_text(encoding="utf-8-sig")), paragraphs)
            # Existing designs remain archived on the new draft. They are not
            # silently recompiled or declared valid after adding appearances.
            source_data = candidate_data(result)
            validate(parse_script(args.script), paragraphs, source_data)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"已保存佩戴形象审阅草稿：{args.output}；沿用原人物行及ID，须重新design并明确重绑定后用于新分镜")
        elif args.command == "design":
            outputs = [args.output] + ([args.catalog_output] if args.catalog_output else [])
            inputs = [args.script, args.source, args.ledger, args.design_json, args.style_file]
            if any(path.exists() for path in outputs) or len({path.resolve() for path in outputs}) != len(outputs) or any(path.resolve() in {value.resolve() for value in inputs} for path in outputs):
                raise ValueError("design输出须为不同的新文件，不覆盖输入、旧台账或旧分类TXT")
            script = parse_script(args.script)
            paragraphs = source_paragraphs(args.source)
            data = json.loads(args.ledger.read_text(encoding="utf-8-sig"))
            # Recompilation is also the explicit path for reviewed changes to
            # facts/style. Validate source structure without stale derivatives.
            source_data = candidate_data(data)
            validate(script, paragraphs, source_data)
            result = apply_design(data, json.loads(args.design_json.read_text(encoding="utf-8-sig")),
                                  args.style_file.read_text(encoding="utf-8-sig"), patch=args.patch, paragraphs=paragraphs)
            validate(script, paragraphs, result, require_designed=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if args.catalog_output:
                render(args.script, args.source, args.output, args.catalog_output, require_designed=True)
            print(f"已编译完整GPT-image-2资产设计：{args.output}；未调用生图模型")
        else:
            render(args.script, args.source, args.ledger, args.output, require_designed=args.require_designed)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
