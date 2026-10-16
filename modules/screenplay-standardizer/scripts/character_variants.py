"""Explicit, source-backed masked appearances of an existing character.

The source cast remains unchanged. A visual asset is not a second person, and
the presence of a mask word never creates an appearance automatically.
"""
import copy
import hashlib
import json
import re


POLICY = "source-masked-v1"


def require(condition, message):
    if not condition:
        raise ValueError("人物佩戴形象：" + message)


def line(value):
    return isinstance(value, str) and bool(value.strip()) and not any(c in value for c in "\r\n")


def compact(value):
    return "".join(c for c in value if c.isalnum())


def scene_appearance_tracks(scene):
    return scene.get("character_appearances", [])


def scene_character_ids(scene):
    """Source cast plus explicitly authorized visual alternatives, in order."""
    result = list(scene.get("character_ids", []))
    for track in scene_appearance_tracks(scene):
        result.append(track["initial_asset_id"])
        for event in track["transitions"]:
            result.extend((event["from_asset_id"], event["to_asset_id"]))
    return list(dict.fromkeys(result))


def canonical_character_id(data, asset_id):
    item = next((item for item in data.get("characters", []) if item["id"] == asset_id), None)
    return item.get("appearance_variant", {}).get("base_asset_id", asset_id) if item else asset_id


def evidence_position(record, paragraphs, scene, label):
    require(isinstance(record, dict) and line(record.get("evidence")) and line(record.get("basis")),
            label + "须填写原文evidence与具体审阅basis")
    refs = record.get("source_paragraphs")
    require(isinstance(refs, list) and refs and all(type(ref) is int for ref in refs)
            and refs == list(range(min(refs), max(refs) + 1)), label + "须引用连续且不重复的原文段落")
    a, b = scene["source_range"]
    require(a <= refs[0] <= refs[-1] <= b <= len(paragraphs), label + "证据不在关联场次")
    quote = "".join(record["evidence"].split())
    original = "".join("".join(paragraphs[ref - 1] for ref in refs).split())
    require(quote and original.count(quote) == 1, label + "证据须在所引原文连续段落中唯一出现")
    offset = sum(len("".join(text.split())) for text in paragraphs[:refs[0] - 1])
    return offset + original.index(quote), offset + original.index(quote) + len(quote)


def validate_character_variants(data, paragraphs):
    variants = [item for item in data.get("characters", []) if "appearance_variant" in item]
    has_tracks = any("character_appearances" in scene for scene in data.get("scenes", []))
    if "character_variant_policy" not in data and not variants and not has_tracks:
        return
    require(data.get("character_variant_policy") == POLICY and data.get("schema_version") == 3,
            "须显式启用schema 3与source-masked-v1，旧台账不自动升级")
    actual_digest = hashlib.sha256(json.dumps(paragraphs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    require(data.get("source_digest") == actual_digest, "台账与审阅原文版本不匹配")
    chars = {item["id"]: item for item in data["characters"]}
    props = {item["id"] for item in data.get("props", [])}
    variant_ids = {item["id"] for item in variants}
    labels = set()
    for item in variants:
        meta = item["appearance_variant"]
        require(isinstance(meta, dict), item["id"] + " appearance_variant须为对象")
        require(set(meta) <= {"base_asset_id", "label", "kind", "design_basis", "source_digest", "prop_asset_id"},
                item["id"] + " appearance_variant含未知字段")
        base = chars.get(meta.get("base_asset_id"))
        require(base is not None and base["id"] != item["id"] and "appearance_variant" not in base
                and "-群像-" not in base.get("source_entry", base["name"]),
                item["id"] + "须直接关联既有个体基础形象，不能递归继承或指向群像")
        require(meta.get("kind") in ("headgear", "face_covering") and line(meta.get("label"))
                and line(meta.get("design_basis")), item["id"] + "须明确佩戴类型、造型名与参考必要性")
        key = (base["id"], meta["label"])
        require(key not in labels, item["id"] + "同一基础人物的相同佩戴标签须复用已有资产")
        labels.add(key)
        require(meta.get("source_digest") == data["source_digest"], item["id"] + "佩戴形象审阅已过期")
        require(item.get("source_entry", item["name"]) == base.get("source_entry", base["name"])
                and item.get("source_speaker") == base.get("source_speaker"), item["id"] + "须继承基础形象的原人物条目与声源")
        require("appearance" not in item, item["id"] + "不能另造世次或场次形象；身份与服装由base_asset_id继承")
        require(item.get("visual_mode", "image_asset") == "image_asset", item["id"] + "佩戴版本必须是图片资产")
        require("prop_asset_id" not in meta or meta["prop_asset_id"] in props, item["id"] + "关联佩戴道具不存在")
    used = set()
    for scene in data["scenes"]:
        tracks = scene_appearance_tracks(scene)
        require(isinstance(tracks, list), scene["id"] + " character_appearances须为数组")
        require(not variant_ids.intersection(scene["character_ids"]), scene["id"] + "佩戴版本不可追加到原人物行名单冒充第二个人")
        bases = set()
        for track in tracks:
            require(isinstance(track, dict) and set(track) == {"base_asset_id", "initial_asset_id", "initial_evidence", "transitions"},
                    scene["id"] + "形象轨迹字段错误")
            base_id = track["base_asset_id"]
            require(isinstance(base_id, str) and base_id in scene["character_ids"] and base_id not in bases,
                    scene["id"] + "形象轨迹须唯一对应本场原人物")
            bases.add(base_id)
            family = {base_id} | {item["id"] for item in variants if item["appearance_variant"]["base_asset_id"] == base_id}
            current = track["initial_asset_id"]
            require(current in family, scene["id"] + "初始形象不属于本角色")
            initial_start, _ = evidence_position(track["initial_evidence"], paragraphs, scene, scene["id"] + "初始状态")
            local_used = {current}
            transitions = track["transitions"]
            require(isinstance(transitions, list), scene["id"] + "transitions须为数组")
            events, previous_end = set(), -1
            for event in transitions:
                require(isinstance(event, dict) and set(event) == {"event_id", "from_asset_id", "to_asset_id", "source_paragraphs", "evidence", "basis"},
                        scene["id"] + "切换事件字段错误")
                require(line(event.get("event_id")) and event["event_id"] not in events, scene["id"] + "切换事件ID重复或缺失")
                events.add(event["event_id"])
                require(event["from_asset_id"] == current and event["to_asset_id"] in family
                        and event["to_asset_id"] != current, scene["id"] + "切换必须从当前同一角色形象到其另一合法形象")
                start, end = evidence_position(event, paragraphs, scene, scene["id"] + ":" + event["event_id"])
                require(start >= previous_end and start >= initial_start, scene["id"] + "佩戴/摘下事件证据重叠或顺序倒置，初始依据不能来自后续状态")
                previous_end, current = end, event["to_asset_id"]
                local_used.update((event["from_asset_id"], current))
            used.update(local_used)
            for aid in local_used:
                prop = chars[aid].get("appearance_variant", {}).get("prop_asset_id")
                require(not prop or prop in scene["prop_ids"], scene["id"] + "佩戴版本引用的道具未关联本场")
    require(variant_ids <= used, "存在未登记任何本场初始状态或切换事件的佩戴版本")


def apply_variant_review(data, payload, paragraphs):
    """Register a reviewed complete snapshot; allocate only genuinely new IDs."""
    require(isinstance(payload, dict) and payload.get("policy") == POLICY
            and payload.get("reviewed") is True and payload.get("source_digest") == data.get("source_digest"),
            "审阅JSON须声明当前原文摘要、source-masked-v1与reviewed=true")
    require(isinstance(payload.get("variants"), list) and isinstance(payload.get("scene_appearances"), list),
            "审阅须包含完整variants及scene_appearances数组")
    result = copy.deepcopy(data)
    result["character_variant_policy"] = POLICY
    chars = {item["id"]: item for item in result["characters"]}
    occupied = set(chars) | set(result.get("retired_ids", [])) | {item["id"] for item in result.get("inactive_assets", {}).get("characters", [])}
    old_ids = {item["id"] for item in result["characters"] if "appearance_variant" in item}
    submitted = set()
    for review in payload["variants"]:
        require(isinstance(review, dict) and set(review) <= {"id", "base_asset_id", "name", "label", "kind", "design_basis", "prop_asset_id"},
                "variants条目字段错误")
        base = chars.get(review.get("base_asset_id"))
        require(base is not None and "appearance_variant" not in base, "基础人物ID不存在或自身为佩戴版本")
        aid = review.get("id")
        if aid is None:
            # A rerun without IDs reuses the unique reviewed base/label pair.
            prior = [item["id"] for item in chars.values() if item.get("appearance_variant", {}).get("base_asset_id") == base["id"]
                     and item["appearance_variant"].get("label") == review.get("label")]
            require(len(prior) <= 1, "基础人物/佩戴标签不唯一，须明确原ID")
            if prior:
                aid = prior[0]
            else:
                counter = max((int(value[4:]) for value in occupied if re.fullmatch(r"CHAR\d{4,}", value)), default=0) + 1
                aid = f"CHAR{counter:04d}"
        require(isinstance(aid, str) and re.fullmatch(r"CHAR\d{4,}", aid) and aid not in submitted
                and (aid not in occupied or aid in old_ids), "佩戴资产ID重复、已停用或占用基础人物ID")
        require(line(review.get("name")), aid + "须填写唯一完整制作名，展示序号不进入name")
        submitted.add(aid)
        occupied.add(aid)
        if aid in chars:
            item = chars[aid]
            require(item["appearance_variant"]["base_asset_id"] == base["id"], "已有佩戴ID不能改绑另一个人物")
        else:
            item = {"id": aid, "aliases": [], "visual_facts": [], "personality_facts": []}
            result["characters"].append(item)
            chars[aid] = item
        item.update(name=review["name"], source_entry=base.get("source_entry", base["name"]))
        if "source_speaker" in base:
            item["source_speaker"] = base["source_speaker"]
        else:
            item.pop("source_speaker", None)
        item["appearance_variant"] = {key: copy.deepcopy(value) for key, value in review.items() if key not in ("id", "name")}
        item["appearance_variant"]["source_digest"] = result["source_digest"]
    require(old_ids <= submitted, "审阅不能静默删除旧佩戴资产；完整列出已有ID并按既有迁移流程处理停用")
    scenes = {scene["id"]: scene for scene in result["scenes"]}
    for scene in scenes.values():
        scene.pop("character_appearances", None)
    for value in payload["scene_appearances"]:
        require(isinstance(value, dict) and value.get("scene_id") in scenes, "形象轨迹场次不存在")
        scenes[value["scene_id"]].setdefault("character_appearances", []).append(
            {key: copy.deepcopy(item) for key, item in value.items() if key != "scene_id"})
    validate_character_variants(result, paragraphs)
    return result


def _event_span(state, scene_id, event):
    records = [record for record in state["records"] if record["scene"] == scene_id]
    text = "".join(compact(record["text"]) for record in records)
    quote = compact(event["evidence"])
    require(quote and text.count(quote) == 1, scene_id + "切换证据须能唯一定位到标准剧本正文；请扩大连续动作引文")
    positions = [position for record in records for position in range(record["start"], record["end"])]
    index = text.index(quote)
    return positions[index], positions[index + len(quote) - 1] + 1


def validate_storyboard(state, unit):
    """Check source-time appearance changes after ordinary continuity validation."""
    data = state["ledger"]
    if data.get("character_variant_policy") != POLICY:
        return
    variants = {item["id"]: item["appearance_variant"]["base_asset_id"] for item in data["characters"] if "appearance_variant" in item}
    tracked_bases = set(variants.values())
    characters = {item['id']: item for item in data['characters']}

    def identity(asset_id):
        base_id = variants.get(asset_id, asset_id)
        base = characters.get(base_id, {})
        appearance = base.get('appearance', {})
        if line(appearance.get('identity')) and line(appearance.get('incarnation')):
            return ('explicit', appearance['identity'], appearance['incarnation'])
        return ('asset', base_id)

    tracked_identities = {identity(base) for base in tracked_bases}
    contract = unit.get("continuity")
    require(isinstance(contract, dict), "新启用佩戴策略的分镜须有连续性记录")
    definitions = contract["entities"]
    owners = {}
    for entity, definition in definitions.items():
        ids = definition["asset_ids"]
        families = {identity(aid) for aid in ids}
        affected = families & tracked_identities
        if affected:
            require(len(families) == 1, "佩戴形象实体不能合并另一个人物或道具")
            person = next(iter(affected))
            require(person not in owners, "同一人物的基础/佩戴形象必须登记为同一个连续性实体，不能冒充多人")
            owners[person] = entity
    if not owners:
        return
    scenes = {scene["id"]: scene for scene in data["scenes"]}
    current = {key: value["asset_id"] for key, value in contract["initial"].items()}
    prepared, cursors = {}, {}
    expected_states = {}
    previous_scene = None
    records = {record['id']: record for record in state['records']}
    for shot in unit["shots"]:
        scene_id = shot["scene_id"]
        tracks = {track["base_asset_id"]: track for track in scene_appearance_tracks(scenes[scene_id])}
        entry_resets = {}
        for person, entity in owners.items():
            scene_bases = [aid for aid in scenes[scene_id]['character_ids'] if identity(aid) == person]
            require(len(scene_bases) <= 1, scene_id + '同一身份与世次不能作为两个源人物参与同场佩戴轨迹')
            if not scene_bases:
                continue
            base = scene_bases[0]
            key = (scene_id, base)
            if key not in prepared:
                track = tracks.get(base)
                expected, selected = (track["initial_asset_id"] if track else base), []
                for event in track["transitions"] if track else []:
                    start, end = _event_span(state, scene_id, event)
                    require(not (start < unit["span"][0] < end or start < unit["span"][1] < end),
                            "分镜来源边界不能拆开佩戴/摘下事件的证据；请在完整动作边界分段")
                    if end <= unit["span"][0]:
                        expected = event["to_asset_id"]
                    elif start < unit["span"][1]:
                        selected.append(event)
                prepared[key], cursors[key], expected_states[key] = selected, 0, expected
            expected = expected_states[key]
            if current[entity] != expected:
                # A source-backed scene transition can enter another clothing
                # version, or a separately established masked state. It is not
                # a second on-screen wear/remove event. The ordinary continuity
                # validator already checks this change's source evidence.
                require(previous_scene is not None and previous_scene != scene_id,
                        scene_id + "单元初始佩戴状态与已发生的原文事件不一致，不能提前使用后续形象")
                entry_resets[entity] = expected
        for change in shot["continuity"]["changes"]:
            entity = change["entity"]
            target = change["set"].get("asset_id", current[entity])
            if target != current[entity]:
                base = variants.get(current[entity], current[entity])
                target_base = variants.get(target, target)
                if entity in entry_resets:
                    require(target == entry_resets[entity] and identity(target) == identity(current[entity]),
                            scene_id + '切场后的形象变化须进入本场同一身份已审初始形象')
                    require(any(records.get(proof.get('record'), {}).get('scene') == scene_id
                                for proof in change['evidence']),
                            scene_id + '跨场形象变化须引用进入场次的原文证据，不能借用旧场证据')
                    del entry_resets[entity]
                elif identity(current[entity]) in tracked_identities or identity(target) in tracked_identities:
                    require(base == target_base, scene_id + '不同服装基础形象须按有原文依据的切场状态变化处理，不能冒充摘戴事件')
                    key = (scene_id, base)
                    events, index = prepared.get(key, []), cursors.get(key, 0)
                    require(index < len(events), scene_id + "不存在本单元范围内已审阅的佩戴形象切换")
                    event = events[index]
                    require(event["from_asset_id"] == current[entity] and event["to_asset_id"] == target,
                            scene_id + "佩戴/摘下顺序与已审阅事件不一致")
                    require(any(compact(proof.get("quote", "")) == compact(event["evidence"]) for proof in change["evidence"]),
                            scene_id + "形象切换须引用对应事件的完整已审动作证据")
                    cursors[key] += 1
                    expected_states[key] = target
            current[entity] = target
        require(not entry_resets, scene_id + '切场后须登记进入本场合法形象的有依据状态变化')
        previous_scene = scene_id
    require(all(cursors[key] == len(events) for key, events in prepared.items()),
            "本单元遗漏已审阅佩戴/摘下事件的形象状态切换")
