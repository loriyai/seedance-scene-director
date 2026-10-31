"""Parse, check and render source-bound plain-text storyboards.

The authored UTF-8 text is the sole source. Parsed metadata is a disposable
projection for receipts and Canvas, not a second authoring schema. Public file
commands must call director.load(project) before passing its state here.
"""
from decimal import Decimal
from collections import Counter
import re


OUTPUT_FORMAT = "scene-text-v1"
VOICE_DISPLAY_POLICY = "inner-voice-os-v1"
HEADER = re.compile(r"## (SC\d{4,}) / ([A-Za-z][A-Za-z0-9_-]*) / (L\d{6,})-(L\d{6,})")
REFERENCE = re.compile(r"@\{((?:CHAR|SET|PROP)\d{4,})\}")
SHOT = re.compile(r"·(\d{2})秒-(\d{2})秒\[镜头(\d+)-([^\[\]]+)\]：（(.+)；转场：([^（）]+)）(.+)音效：(.+)。")
VOICE = re.compile(
    r"声音\[(L\d{6,}),(\d+(?:\.\d{1,3})?)-(\d+(?:\.\d{1,3})?)\]："
    r"(@\{CHAR\d{4,}\}(?:、@\{CHAR\d{4,}\})*|[^@{}（）：“]+?)"
    r"(内心想法os|内心想法|画外音|旁白|非人物播报|说)(（[^（）]+）)?：“(.*)”")
FRAMINGS = ("大远景", "大全景", "远景", "全景", "中景", "中近景", "近景", "特写")
PROJECTION_FIELDS = (
    "output_format", "draft_key", "author_key", "main_scene", "source_records", "span",
    "duration", "character_ids", "scene_ids", "prop_ids", "asset_ids", "set_ids",
    "time", "weather", "global", "shots", "source_text",
)


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _compact(text):
    return "".join(c for c in text if c.isalnum())


def _refs(text, context):
    """Only explicit ID references outside literal dialogue are accepted."""
    found, cursor = [], 0
    for at in re.finditer("@", text):
        if at.start() < cursor:
            continue
        match = REFERENCE.match(text, at.start())
        _require(match is not None, f"{context}：资产引用须为 @{{CHAR…}} / @{{SET…}} / @{{PROP…}}，SC不能作图片引用")
        found.append(match.group(1))
        cursor = match.end()
    return list(dict.fromkeys(found))


def _character_aliases(asset):
    """Use ledger labels, never invent short names or rewrite author prose."""
    aliases = set()
    for field in ("source_speaker", "source_entry", "name"):
        value = asset.get(field)
        if not isinstance(value, str):
            continue
        for label in (value.strip(), re.split(r"[-（(]", value, maxsplit=1)[0].strip()):
            if label and not any(c in label for c in "@{}\r\n") and any(c.isalpha() for c in label):
                aliases.add(label)
    return aliases


def _mask_quoted_prose(value):
    """Quoted literals are data, just like the separate source voice rows."""
    result, closing = list(value), []
    pairs = {'“': '”', '「': '」', '『': '』', '"': '"'}
    for index, char in enumerate(value):
        if closing:
            result[index] = ' '
            if char == closing[-1]:
                closing.pop()
            elif char in pairs and char != '"':
                closing.append(pairs[char])
        elif char in pairs:
            result[index] = ' '
            closing.append(pairs[char])
    return ''.join(result)


def character_reference_issues(state, units):
    """Conservative missing-reference diagnostics for newly authored prose.

    Existing @ validation checks only references which were actually written.
    Here known bare names are rejected, while obvious surname/pronoun subject
    ambiguities require author revision rather than guessed name completion.
    Roster, focal bracket and earlier sentences cannot bind the current prose.
    This lexical guard does not prove that every unnamed entity was detected.
    It neither changes text nor applies to historical receipt rendering.
    """
    characters = {a['id']: a for a in state.get('ledger', {}).get('characters', [])}
    scenes = {s['id']: s for s in state.get('ledger', {}).get('scenes', [])}
    issues = []
    for unit in units:
        scope = set(unit.get('character_ids', []))
        for sid in unit.get('scene_ids', []):
            if sid in scenes:
                scope.update(scene_asset_ids(scenes[sid])['characters'])
        aliases, surnames = {}, {}
        for aid in sorted(scope & set(characters)):
            asset = characters[aid]
            for alias in _character_aliases(asset):
                aliases.setdefault(alias, set()).add(aid)
                # A one-character ledger name or a plausible shortened CJK
                # subject is diagnostic only, not an asserted legal surname.
                if re.fullmatch(r'[\u4e00-\u9fff]{1,4}', alias):
                    surnames.setdefault(alias[0], set()).add(aid)
        if not aliases:
            continue
        tokens = sorted(aliases, key=lambda name: (-len(name), name))
        # Longer known ledger labels shield their substrings (王 vs 霸王).
        token_re = re.compile('|'.join(re.escape(name) for name in tokens if len(name) > 1)) if any(len(n) > 1 for n in tokens) else None
        fields = [('global', None, unit.get('global', ''))]
        for index, shot in enumerate(unit.get('shots', []), 1):
            match = SHOT.fullmatch(shot.get('text', ''))
            if match:
                fields.append(('body', index, match.group(7)))
        for field, shot_index, value in fields:
            prose = _mask_quoted_prose(value)
            for sentence in re.split(r'[。！？!?\r\n]', prose):
                references = [(m.start(), m.group(1)) for m in REFERENCE.finditer(sentence)
                              if m.group(1) in characters]
                masked = REFERENCE.sub(lambda m: ' ' * len(m.group(0)), sentence)
                bound_plain = []
                base = {'unit': unit.get('draft_key', '当前单元'), 'field': field,
                        'shot': shot_index, 'semantic_review': 'not_performed'}

                def add(code, mention, candidates, review_required):
                    issues.append({**base, 'code': code, 'mention': mention,
                                   'asset_candidates': sorted(candidates),
                                   'review_required': review_required,
                                   'message': '人物站位/动作正文须明确引用当前主体或对象；名单与景别不能代替正文引用，不自动补名。'})

                # Dialogue is separate from shot prose, and prompt_only names
                # remain plain text by contract.  No blanket name replacement.
                if token_re:
                    for match in token_re.finditer(masked):
                        alias = match.group(0)
                        # Latin names are words, not substrings of other words.
                        if alias.isascii() and ((match.start() and re.match(r'[A-Za-z0-9_]', masked[match.start()-1])) or
                                                (match.end() < len(masked) and re.match(r'[A-Za-z0-9_]', masked[match.end()]))):
                            continue
                        ids = aliases[alias]
                        required = {aid for aid in ids if characters[aid].get('visual_mode') != 'prompt_only'}
                        if required:
                            add('BARE_CHARACTER_NAME', alias, required, False)
                        else:
                            bound_plain.append((match.start(), ids))
                    masked = token_re.sub(lambda m: ' ' * len(m.group(0)), masked)

                for short, candidates in sorted(surnames.items()):
                    required = {aid for aid in candidates if characters[aid].get('visual_mode') != 'prompt_only'}
                    if not required:
                        continue
                    # Do not search a single character anywhere in the prose:
                    # 灯光, 王座, 霸王 and other ordinary words are not subjects.
                    predicate = (r'在(?:其|他|她|画|左|右|前|后|身|旁|门|窗)|双手|两手|左手|右手|'
                                 r'左膝|右膝|左脚|右脚|手中|掌中|身侧|'
                                 r'仍(?:站|坐|托|握|捧)|的(?:头|肩|手|左|右|脸|眼|身)')
                    pattern = re.compile(r'(?:^|[，,；;：:\s])(' + re.escape(short) + r')(?=' + predicate + ')')
                    for match in pattern.finditer(masked):
                        add('AMBIGUOUS_CHARACTER_SHORTHAND', short, required, True)

                pronouns = re.compile(r'(?<!其)(?:他|她)(?:们)?(?=的|在|向|从|把|将|抬|转|站|坐|看|握|伸|双|左|右|仍|正|身|肩|手|头|脸|眼)|其(?=左|右|身|手|头|肩|前|后)')
                for match in pronouns.finditer(masked):
                    prior = {aid for offset, aid in references if offset < match.start()}
                    for offset, ids in bound_plain:
                        if offset < match.start():
                            prior.update(ids)
                    target = re.search(r'(?:向|朝|看向|看着|握住|牵住|抓住|扶住|拉住|推开|抱住|打向|击向)$', masked[:match.start()])
                    if target:
                        add('AMBIGUOUS_CHARACTER_TARGET', match.group(0), scope, True)
                    elif not prior:
                        add('UNBOUND_CHARACTER_PRONOUN', match.group(0), scope, True)
                    elif len(prior) > 1:
                        add('AMBIGUOUS_CHARACTER_PRONOUN', match.group(0), prior, True)
    return issues


def _listed_refs(text, prefix, context):
    if prefix == "CHAR" and text == "无":
        return []
    values = [p.strip() for p in re.split("[，、；]", text) if p.strip()]
    _require(values, f"{context}：资产名单为空")
    ids = []
    for value in values:
        match = REFERENCE.fullmatch(value)
        _require(match is not None and match.group(1).startswith(prefix),
                 f"{context}：名单只能填写 @{{{prefix}…}}")
        ids.append(match.group(1))
    _require(len(ids) == len(set(ids)), f"{context}：名单重复")
    return ids


def _context(state):
    ledger = state.get("ledger", {})
    _require(ledger.get("schema_version") == 3, "文本分镜须使用已绑定的schema 3资产台账")
    records = state.get("records")
    _require(isinstance(records, list) and records, "缺少已绑定的标准剧本records；先调用director.load")
    record_map = {r["id"]: (i, r) for i, r in enumerate(records)}
    _require(len(record_map) == len(records), "绑定records存在重复ID")
    scenes = {s["id"]: s for s in ledger.get("scenes", [])}
    assets = {a["id"]: a for field in ("characters", "scene_assets", "props") for a in ledger.get(field, [])}
    _require(len(assets) == sum(len(ledger.get(field, [])) for field in ("characters", "scene_assets", "props")),
             "台账资产ID重复")
    for aid, item in assets.items():
        name = item.get("name", "")
        _require(isinstance(name, str) and name.strip() == name and name and not any(c in name for c in "@{}\r\n"),
                 f"台账资产名称不合法：{aid}")
    return records, record_map, scenes, assets


def _seconds(value):
    number = Decimal(value)
    return int(number) if number == number.to_integral_value() else float(number)


def _parse_units(text, state):
    _require(isinstance(text, str) and text.strip(), "分镜TXT为空")
    _require("\x00" not in text, "分镜TXT含NUL字符")
    records, record_map, scenes, _ = _context(state)
    units, unit, lines = [], None, []
    stage = 0

    def finish():
        if unit is not None:
            _require(stage == 4 and unit["shots"], f'{unit["draft_key"]}：缺少人物/场景/人物站位/镜头行')
            unit["duration"] = unit["shots"][-1]["end"]
            unit["source_text"] = "\n".join(lines) + "\n"
            units.append(unit)

    for line_no, raw in enumerate(text.removeprefix("\ufeff").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        context = f"TXT第{line_no}行"
        heading = HEADER.fullmatch(line)
        if heading:
            finish()
            sid, key, first, last = heading.groups()
            _require(sid in scenes, f"{context}：未知剧情场次{sid}")
            _require(first in record_map and last in record_map, f"{context}：来源记录不存在")
            a, b = record_map[first][0], record_map[last][0]
            _require(a <= b, f"{context}：来源记录范围倒序")
            selected = records[a:b + 1]
            scene_ids = list(dict.fromkeys(r["scene"] for r in selected))
            _require(sid in scene_ids, f"{context}：主场景不在来源范围内")
            unit = {"output_format": OUTPUT_FORMAT, "draft_key": sid + "/" + key,
                    "author_key": key, "main_scene": sid,
                    "source_records": [r["id"] for r in selected],
                    "span": [selected[0]["start"], selected[-1]["end"]],
                    "scene_ids": scene_ids, "shots": []}
            lines, stage = [line], 0
            continue
        _require(unit is not None, f"{context}：单元外只允许空行；单元须以 ## SC0001 / u01 / L000003-L000010 开始")
        lines.append(line)
        if stage == 0 and line.startswith("人物："):
            _require(line.endswith("；"), f"{context}：人物行须以中文分号结束")
            unit["character_ids"] = _listed_refs(line[3:-1], "CHAR", context)
            stage = 1
        elif stage == 1 and line.startswith("场景："):
            scene = re.fullmatch(r"场景：(.+?)；时间：([^；]+)；天气：([^；]+)；", line)
            _require(scene is not None, f"{context}：场景行须为 场景：@{{SET…}}；时间：…；天气：…；")
            unit["set_ids"] = _listed_refs(scene.group(1), "SET", context)
            unit["time"], unit["weather"] = scene.group(2).strip(), scene.group(3).strip()
            _require(unit["time"] and unit["weather"], f"{context}：时间/天气不可为空")
            stage = 2
        elif stage == 2 and line.startswith("人物站位："):
            unit["global"] = line[5:]
            _require(unit["global"].strip(), f"{context}：人物站位不可为空")
            stage = 3
        elif stage >= 3 and line.startswith("·"):
            match = SHOT.fullmatch(line)
            _require(match is not None, f"{context}：镜头行格式错误；须有时码、镜号、景别括号、转场、正文和音效末句")
            start, end, index, label, focal, transition, body, sound = match.groups()
            _require(int(index) == len(unit["shots"]) + 1, f"{context}：镜头编号须从1连续递增")
            framing = next((f for f in FRAMINGS if focal.endswith("的" + f)), None)
            _require(framing is not None, f"{context}：景别须绑定主体并采用规定景别名称")
            _require(_refs(focal, context), f"{context}：景别主体须使用稳定资产引用")
            _require(all(s.strip() for s in (label, focal, transition, body, sound)), f"{context}：镜头字段不可为空")
            _require("：“" not in line, f"{context}：台词只写在声音来源行，不可直接重复写入镜头正文")
            _require(line.count("音效：") == 1, f"{context}：每镜只能有一个末尾音效字段")
            unit["shots"].append({"start": int(start), "end": int(end), "text": line,
                                  "label": label, "framing": framing, "dialogue_parts": []})
            stage = 4
        elif stage == 4 and line.startswith("声音["):
            match = VOICE.fullmatch(line)
            _require(match is not None, f"{context}：声音行格式错误；使用 声音[L000007,0.2-2.8]：@{{CHAR0001}}说：“原台词”")
            rid, start, end, source, voice_label, direction, spoken = match.groups()
            _require(spoken and _compact(spoken), f"{context}：台词片段不可为空或仅含标点")
            part = {"record": rid, "start": _seconds(start), "end": _seconds(end),
                    # Internal records keep the canonical voice enum.  The
                    # new scene-text export label is rendered separately.
                    "voice": "内心想法" if voice_label == "内心想法os" else voice_label,
                    "direction": direction or "", "text": spoken}
            if source.startswith("@{"):
                ids = [match.group(1) for match in REFERENCE.finditer(source)]
                if len(ids) == 1:
                    part["character_id"] = ids[0]
                else:
                    part["character_ids"] = ids
            else:
                part.update(source_kind="non_character", source_label=source)
            unit["shots"][-1]["dialogue_parts"].append(part)
        else:
            raise ValueError(f"{context}：未知、重复或顺序错误的文本行：{line[:70]}")
    finish()
    _require(units, "分镜TXT没有单元")
    return units


def _scene_characters(scene):
    return set(scene_asset_ids(scene)['characters'])


def scene_asset_ids(scene):
    """Legal scene references shared by the parser and read-only context view."""
    characters = list(scene.get("character_ids", []))
    for track in scene.get("character_appearances", []):
        characters.append(track["initial_asset_id"])
        for event in track.get("transitions", []):
            characters.extend((event["from_asset_id"], event["to_asset_id"]))
    return {'characters': list(dict.fromkeys(characters)),
            'scene_assets': [scene['scene_asset_id']],
            'props': list(dict.fromkeys(scene.get('prop_ids', [])))}


def _speaker(asset):
    name = asset.get("source_entry", asset["name"])
    fallback = name.split("-群像-", 1)[0] + "-群像" if "-群像-" in name else name.split("-", 1)[0]
    return asset.get("source_speaker", fallback)


def _check(units, state, *, allow_partial_tail=False):
    records, record_map, scenes, assets = _context(state)
    keys, previous_last, ordered_dialogue, actual_order = set(), None, [], []
    chunks, units_by_record = {}, {}
    for unit in units:
        key = unit["draft_key"]
        _require(key not in keys, f"重复作者单元键：{key}")
        keys.add(key)
        selected = [record_map[rid][1] for rid in unit["source_records"]]
        a, b = record_map[selected[0]["id"]][0], record_map[selected[-1]["id"]][0]
        if previous_last is not None:
            _require(a == previous_last + 1 or a == previous_last,
                     f"{key}：单元来源范围不连续/重复；仅允许相邻单元共享一条边界来源记录")
        previous_last = b
        expected_sets = list(dict.fromkeys(scenes[sid]["scene_asset_id"] for sid in unit["scene_ids"]))
        _require(unit["set_ids"] == expected_sets, f"{key}：场景SET名单与来源场次及顺序不一致")
        allowed_chars = set().union(*(_scene_characters(scenes[sid]) for sid in unit["scene_ids"]))
        allowed_props = set().union(*(set(scenes[sid].get("prop_ids", [])) for sid in unit["scene_ids"]))
        _require(set(unit["character_ids"]) <= allowed_chars, f"{key}：人物名单包含不属于来源场次的资产")
        _require(set(unit["character_ids"] + expected_sets) <= set(assets), f"{key}：人物/场景名单存在未知资产")
        allowed = set(unit["character_ids"]) | set(expected_sets) | allowed_props
        used = _refs(unit["global"], key + "人物站位")
        used += _refs(unit["time"] + unit["weather"], key + "时间天气")
        cursor, speech_end, local_dialogue = 0, 0, []
        for shot in unit["shots"]:
            _require(shot["start"] == cursor and 1 <= shot["end"] - shot["start"] <= 6,
                     f"{key}：镜头须从00秒连续，单镜为1～6秒整数")
            cursor = shot["end"]
            used += _refs(shot["text"], key + "镜头")
            for part in shot["dialogue_parts"]:
                rid = part["record"]
                _require(rid in unit["source_records"] and record_map[rid][1]["kind"] == "dialogue",
                         f"{key}：声音记录{rid}不属于本单元台词来源")
                record = record_map[rid][1]
                _require(part["voice"] == record["voice"], f"{key}/{rid}：发声类型与原文不一致")
                if record.get("source_kind") == "non_character":
                    _require(part.get("source_kind") == "non_character" and part.get("source_label") == record["speaker"]
                             and part["voice"] == "非人物播报", f"{key}/{rid}：非人物播报声源与原文不一致")
                else:
                    ids = part.get("character_ids", [part.get("character_id")])
                    _require(isinstance(ids, list) and ids and len(ids) == len(set(ids))
                             and all(cid in unit["character_ids"]
                                     and cid in _scene_characters(scenes[record["scene"]])
                                     and cid in assets for cid in ids)
                             and "、".join(_speaker(assets[cid]) for cid in ids) == record["speaker"],
                             f"{key}/{rid}：台词说话人与来源台账不一致")
                    used.extend(ids)
                _require("@" not in part["direction"], f"{key}/{rid}：语气括注只写声音表现，不写资产引用")
                t0, t1 = Decimal(str(part["start"])), Decimal(str(part["end"]))
                _require(shot["start"] <= t0 < t1 <= shot["end"], f"{key}/{rid}：声音时码必须完整落在所属镜头内")
                _require(t0 >= speech_end, f"{key}/{rid}：声音片段倒序或重叠；须按实际发声顺序编写")
                speech_end = t1
                chunks.setdefault(rid, []).append(part["text"])
                units_by_record.setdefault(rid, {}).setdefault(key, []).append(part["text"])
                actual_order.append(rid)
                local_dialogue.append(rid)
        minimum = 1 if allow_partial_tail and unit is units[-1] else 4
        _require(minimum <= cursor <= 30, f"{key}：单元总时长须为{minimum}～30秒")
        expected_dialogue = [r["id"] for r in selected if r["kind"] == "dialogue"]
        _require(list(dict.fromkeys(local_dialogue)) == expected_dialogue,
                 f"{key}：原文台词遗漏或顺序变化；每条来源台词须有实际片段")
        for rid in expected_dialogue:
            if rid not in ordered_dialogue:
                ordered_dialogue.append(rid)
        _require(set(used) <= allowed and set(used) <= set(assets), f"{key}：引用资产不存在、未列入人物名单或不属于来源场次：{sorted(set(used) - (allowed & set(assets)))}")
        unit["prop_ids"] = list(dict.fromkeys(aid for aid in used if aid.startswith("PROP")))
        unit["asset_ids"] = list(dict.fromkeys(unit["character_ids"] + expected_sets + unit["prop_ids"]))
    for issue in character_reference_issues(state, units):
        place = '人物站位' if issue['field'] == 'global' else f'镜头{issue["shot"]}正文'
        _require(False, f'{issue["unit"]}/{place}：{issue["code"]}（{issue["mention"]}）；{issue["message"]}')
    # Continuity wording is checked at the authored-text choke point.  The
    # transition field may say “动作接续”; only generic continuity prose in
    # the visible shot body is blocked here.
    from continuity import validate_prompt_continuity
    for issue in validate_prompt_continuity(units):
        _require(False, f'{issue["unit"]}/镜头{issue["shot"]}：{issue["code"]}；{issue["message"]}')
    collapsed = [rid for i, rid in enumerate(actual_order) if not i or rid != actual_order[i - 1]]
    _require(collapsed == ordered_dialogue, "台词片段顺序与原文不同，或同一台词被其他台词打断")
    for rid in ordered_dialogue:
        _require("".join(chunks[rid]) == record_map[rid][1]["text"], f"{rid}：台词拼接与原文不逐字一致（遗漏、重复、改字或标点变化）")
    # A shared dialogue boundary is assigned exact compact source offsets. Full
    # raw text was checked above, so punctuation is never normalized for fidelity.
    seen = {}
    for unit in units:
        key = unit["draft_key"]
        first, last = (record_map[unit["source_records"][i]][1] for i in (0, -1))
        start = first["start"] + seen.get(first["id"], 0)
        for rid in unit["source_records"]:
            if rid in units_by_record:
                seen[rid] = seen.get(rid, 0) + len(_compact("".join(units_by_record[rid][key])))
        end = last["start"] + seen[last["id"]] if last["kind"] == "dialogue" else last["end"]
        _require(start < end, f"{key}：台词拆段未产生可定位的来源范围")
        unit["span"] = [start, end]
    for prior, unit in zip(units, units[1:]):
        boundary = record_map[unit["source_records"][0]][1]
        shared_action = (prior["source_records"][-1] == boundary["id"] and boundary["kind"] == "action"
                         and prior["span"][1] == boundary["end"] and unit["span"][0] == boundary["start"])
        _require(prior["span"][1] == unit["span"][0] or shared_action,
                 f'{unit["draft_key"]}：来源正文范围不连续；重叠只允许完整的共享边界动作记录')
    # Persisted coordinates use director.portion's punctuation ownership. Check
    # this while the complete source is available, so a selected replacement can
    # later verify its exact slice without needing the adjacent unit again.
    import director as d
    for unit in units:
        for rid in unit["source_records"]:
            if rid in units_by_record:
                actual = "".join(units_by_record[rid][unit["draft_key"]])
                expected = d.portion(record_map[rid][1], *unit["span"])
                _require(actual == expected, f'{unit["draft_key"]}/{rid}：跨单元台词边界标点须留在前段，保持可独立复核的原文切片')


def _slice_state(state, source_span):
    if source_span is None:
        return state
    import director as d
    records, _, _, _ = _context(state)
    _require(isinstance(source_span, (list, tuple)) and len(source_span) == 2
             and all(type(value) is int for value in source_span), "source_span须为两个整数")
    start, end = source_span
    _require(0 <= start < end <= records[-1]["end"], "source_span为空、倒序或超出绑定来源")
    clipped = []
    for record in records:
        if record["start"] < end and record["end"] > start:
            clipped.append(dict(record, text=d.portion(record, start, end),
                                start=max(start, record["start"]), end=min(end, record["end"])))
    _require(clipped, "source_span未覆盖任何绑定来源记录")
    return dict(state, records=clipped)


def parse(text, state, source_span=None):
    """Return checked metadata derived solely from author text and bound state."""
    context = _slice_state(state, source_span)
    units = _parse_units(text, context)
    _check(units, context)
    if source_span is not None:
        _require([units[0]["span"][0], units[-1]["span"][1]] == list(source_span),
                 "作者文本须完整覆盖指定source_span，不能遗漏范围首尾记录")
    return units


def parse_incremental(text, state, source_span=None):
    """Inspect only an authored scene prefix, without completing any content.

    A final spoken fragment may stop at an exact source prefix and the last
    unfinished unit may be shorter than four seconds.  No default parse or
    delivery constraint is relaxed: callers must use the full route before
    preview/review.  Source clipping here is a read-only validation view.
    """
    context = _slice_state(state, source_span)
    units = _parse_units(text, context)
    records, record_map, _, _ = _context(context)
    scene_ids = {sid for unit in units for sid in unit['scene_ids']}
    _require(len(scene_ids) == 1, "增量检查只允许当前单场的连续前缀")
    sid = next(iter(scene_ids))
    first = record_map[units[0]['source_records'][0]][1]
    expected_first = next(record for record in records if record['scene'] == sid)
    _require(first['id'] == expected_first['id'], "增量草稿须从当前场首记录开始；局部起点须明确source_span")
    last = record_map[units[-1]['source_records'][-1]][1]
    end = last['end']
    if last['kind'] == 'dialogue':
        fragment = ''.join(part['text'] for unit in units for shot in unit['shots']
                           for part in shot['dialogue_parts'] if part['record'] == last['id'])
        _require(bool(_compact(fragment)) and last['text'].startswith(fragment),
                 f'{last["id"]}：增量末条台词必须是原话逐字前缀')
        end = last['start'] + len(_compact(fragment))
    prefix = _slice_state(context, [first['start'], end])
    if last['kind'] == 'dialogue':
        # Full delivery owns boundary punctuation in the preceding fragment.
        # An unfinished tail may stop before that punctuation; inspect exactly
        # the written prefix, without appending it or changing the source.
        prefix = dict(prefix, records=[dict(record, text=fragment) if record['id'] == last['id']
                                       else record for record in prefix['records']])
    _check(units, prefix, allow_partial_tail=True)
    return units


def validate(state, units):
    """Reparse the source and reject stale/edited derived metadata, without IO."""
    _require(isinstance(units, list) and units, "分镜单元不能为空")
    _require(all(isinstance(u, dict) and isinstance(u.get("source_text"), str) for u in units), "分镜单元缺少唯一作者文本source_text")
    _require(all(isinstance(u.get("span"), list) and len(u["span"]) == 2 for u in units), "分镜单元缺少有效span")
    fresh = parse("\n".join(u["source_text"] for u in units), state,
                  source_span=[units[0]["span"][0], units[-1]["span"][1]])
    _require(len(fresh) == len(units), "作者文本单元数与缓存不一致")
    for unit, current in zip(units, fresh):
        for field in PROJECTION_FIELDS:
            _require(unit.get(field) == current.get(field), f'{current["draft_key"]}：派生字段{field}与作者文本不一致；请重新parse')
    source_records = list(dict.fromkeys(rid for u in fresh for rid in u["source_records"]))
    warnings = []
    entry = state.get("entry", {})
    for unit in fresh:
        minimum, durations = entry.get("min", 4), entry.get("durations", [])
        if (isinstance(minimum, int) and unit["duration"] < minimum) or (durations and unit["duration"] not in durations):
            warnings.append({"kind": "stored_entry_duration_preference", "unit": unit["draft_key"],
                             "duration": unit["duration"], "stored_minimum": minimum,
                             "stored_durations": durations,
                             "message": "当前时长不符合旧入口保存的时长偏好；新文本格式仍允许4～30秒，实际节奏由渲染文本复核。"})
    return {"status": "checked", "unit_count": len(fresh),
            "shot_count": sum(len(u["shots"]) for u in fresh),
            "total_duration": sum(u["duration"] for u in fresh),
            "span": [fresh[0]["span"][0], fresh[-1]["span"][1]],
            "source_records": source_records, "warnings": warnings, "semantic_review": "not_performed",
            "observations": observations(state, fresh)}


def _camera_wording(shot):
    """Conservative lexical label, never a semantic camera/performance verdict.

    Only the first clause after the framing bracket is eligible. Actor clauses,
    dialogue and sound never supply camera terms. Unrecognized wording is fine.
    """
    if "camera" in shot:
        value = shot.get("camera", "")
    else:
        match = SHOT.fullmatch(shot.get("text", ""))
        if match is None:
            return "unknown"
        value = match.group(7)
    return _camera_clause_wording(value)


def _camera_clause_wording(value):
    if not isinstance(value, str):
        return "unknown"
    clause = re.split(r"[，,；;。.!！？?\r\n]", value, maxsplit=1)[0].strip()
    clause = re.split(r"@\{|[“”]|他说|她说|说话|声音", clause, maxsplit=1)[0]
    fixed = r"固定|锁定"
    moving = r"平移|推近|拉远|跟移|跟拍|跟随|环绕|摇摄|摇镜|移镜|升降|上摇|下摇|左摇|右摇|推进|后拉|横移|后退|拉开|靠近|转摇|转动|转向|俯转|仰转|短移|上移|下移|侧移"
    # Spatial wording may precede an explicit camera noun. Read its predicate,
    # never a motion word later in the photographed actor/object description.
    camera = re.search(r"镜头|摄影机|相机|机位", clause)
    if camera:
        prefix, tail = clause[:camera.start()], clause[camera.end():]
        tail = re.sub(r"^(?:随后|然后|接着)", "", tail)
        if re.search(r"记录|拍摄|捕捉|展示|呈现|说|喊|叫|念", prefix):
            return "unknown"
        if re.match(r"(?:记录|拍摄|捕捉|展示|呈现|人物|演员|角色|主角)", tail):
            tail = ""
        tail = re.split(r"(?<![向朝随])(?:人物|演员|角色|主角)", tail, maxsplit=1)[0]
        has_fixed = bool(re.search(rf"(?:{fixed})$", prefix) or re.match(rf"(?:保持)?(?:{fixed})", tail))
        # Direction phrases belong to the explicit camera predicate, including
        # e.g. 机位向驾驶者小幅推进 and 机位随他向前移动一步.
        motion_start = rf"(?:(?:向|沿|朝|由|从|随)[^，；。]{{1,20}}?|小幅|缓慢|轻微|快速|缓缓)*(?:{moving}|移动)"
        has_moving = bool(re.match(motion_start, tail))
        if re.search(r"不|未|禁止|避免|是否|或|可能|静止|稳定", prefix + tail):
            return "mixed_or_unclear"
        if has_fixed and (has_moving or re.match(rf"(?:保持)?(?:{fixed})(?:后|并|再|随后|然后).*?(?:{moving})", tail)):
            return "mixed_or_unclear"
        if has_moving and re.search(r"(?:后|并|再|随后|然后)(?:保持)?固定", tail):
            return "mixed_or_unclear"
        if has_fixed:
            return "fixed"
        if has_moving:
            return "moving"
        return "unknown"
    # Also recognize ordinary bare camera instructions at the very start.
    # Longer spatial prose without a camera noun remains unknown deliberately.
    position = r"(?:(?:侧后方|斜后方|侧前方|斜侧面|侧面|正面|背面|后方|前方|低角度|高角度|平视|俯视|仰视|缓慢|轻微|快速|缓缓|向左|向右|向前|向后|向上|向下))*"
    if re.match(position + rf"(?:{moving})", clause):
        return "moving"
    return "unknown"


def camera_constraints(units, before=None, after=None):
    """Check explicit new-authoring constraints; never approve film semantics.

    Reading/rendering persisted units does not call this gate. Ambiguous prose
    remains a review target; words about actor motion or focus cannot clear it.
    Neighbouring historical shots are checked only at the selected boundary.
    """
    rows = []
    for unit, selected in ([(before, False)] if before else []) + [(u, True) for u in units] + ([(after, False)] if after else []):
        shots = unit.get("shots", [])
        indices = range(len(shots)) if selected else ([len(shots) - 1] if unit is before else [0])
        for index in indices:
            if not shots:
                continue
            shot = shots[index]
            if not isinstance(shot, dict):
                continue
            label = _camera_wording(shot)
            # The leading clause is intentionally conservative. An explicitly
            # named later camera move makes an initial fixed pose inconclusive.
            if label == "fixed":
                match = SHOT.fullmatch(shot.get("text", ""))
                prose = shot.get("camera", match.group(7) if match else "")
                clauses = re.split(r"[，,；;。.!！？?\r\n]", prose)[1:]
                later = [clause.strip() for clause in clauses
                         if re.match(r"\s*(?:相机|摄影机|镜头|机位|随后|然后|接着)", clause)]
                if any(_camera_clause_wording('相机' + clause if re.match(r'随后|然后|接着', clause) else clause)
                       in ("moving", "mixed_or_unclear") for clause in later):
                    label = "mixed_or_unclear"
            duration = (shot['end'] - shot['start'] if all(type(shot.get(key)) in (int, float) for key in ('start', 'end')) else 0)
            rows.append({"unit": unit.get("id", unit.get("draft_key", "当前单元")),
                         "shot": index + 1, "duration": duration,
                         "camera_wording": label, "selected": selected})
    errors, pending = [], []
    for row in rows:
        if not row['selected']:
            continue
        context = {key: row[key] for key in ('unit', 'shot', 'duration', 'camera_wording')}
        if 5 <= row['duration'] <= 6 and row['camera_wording'] == 'fixed':
            errors.append({"code": "LONG_SHOT_FIXED", "kind": "5～6秒镜头不得全程固定；须设计有观看作用的相机位移或转向", **context})
        elif row['camera_wording'] in ('unknown', 'mixed_or_unclear'):
            pending.append({"code": "CAMERA_SEMANTIC_REVIEW", "kind": "完整摄影描述须由独立场级审阅核实实际运动、镜长及相邻约束；词面未判定不代表合格", **context})
    for left, right in zip(rows, rows[1:]):
        if not (left['selected'] or right['selected']):
            continue
        if left['camera_wording'] == right['camera_wording'] == 'fixed':
            errors.append({"code": "ADJACENT_FIXED", "kind": "叙事上相邻两镜不得均为固定机位", "unit": right['unit'],
                           "shot": right['shot'], "previous_unit": left['unit'], "previous_shot": left['shot']})
        elif not (left['selected'] and right['selected']) and any(row['camera_wording'] in ('unknown', 'mixed_or_unclear') for row in (left, right)):
            pending.append({"code": "CAMERA_BOUNDARY_REVIEW", "kind": "选定范围与既有镜头的相邻摄影关系需独立场级核实，不改写范围外历史", "unit": right['unit'],
                            "shot": right['shot'], "previous_unit": left['unit'], "previous_shot": left['shot']})
    return {"status": "failed" if errors else "needs_semantic_review" if pending else "explicit_constraints_checked",
            "errors": errors, "warnings": pending, "semantic_review": "not_performed"}


def camera_context(state, units, replace_ids=None):
    """Resolve actual adjacent persisted units without rewriting outside scope."""
    stored = state.get('units', [])
    if replace_ids:
        indices = [i for i, unit in enumerate(stored) if unit.get('id') in replace_ids]
        if indices:
            first_old, last_old = stored[min(indices)], stored[max(indices)]
            before = next((u for u in stored if u.get('id') == first_old.get('predecessor')), None)
            if before is None and first_old.get('predecessor') is None:
                before = next((u for u in reversed(stored[:min(indices)])
                               if u.get('span', [None, None])[1] == first_old.get('span', [None])[0]), None)
            after = next((u for u in stored[max(indices) + 1:]
                          if u.get('predecessor') == last_old.get('id')), None)
            if after is None and max(indices) + 1 < len(stored):
                candidate = stored[max(indices) + 1]
                if (candidate.get('span', [None])[0] == last_old.get('span', [None, None])[1]
                        and candidate.get('batch_id') == last_old.get('batch_id')):
                    after = candidate
            return before, after
    first = units[0].get('span', [None])[0] if units else None
    before = next((unit for unit in reversed(stored) if unit.get('span', [None, None])[1] == first), None)
    return before, None


def observations(state, units):
    """Read-only statistics for checked units; no preferences become failures."""
    durations = [unit["duration"] for unit in units]
    distribution = Counter(durations)
    camera = {}
    categories = ("fixed", "moving", "mixed_or_unclear", "unknown")
    for unit in units:
        for shot in unit["shots"]:
            duration = str(shot["end"] - shot["start"])
            counts = camera.setdefault(duration, dict.fromkeys(categories, 0))
            counts[_camera_wording(shot)] += 1
    records = {r["id"]: (index, r) for index, r in enumerate(state["records"])}
    candidates = []
    for left, right in zip(units, units[1:]):
        selected = left["source_records"] + right["source_records"]
        scenes = {records[rid][1]["scene"] for rid in selected}
        last_index, last = records[left["source_records"][-1]]
        first_index, first = records[right["source_records"][0]]
        contiguous = (first_index in (last_index, last_index + 1) and
                      (left["span"][1] == right["span"][0] or
                       (last["id"] == first["id"] and first["kind"] == "action" and
                        left["span"][1] == first["end"] and right["span"][0] == first["start"])))
        combined = left["duration"] + right["duration"]
        if len(scenes) == 1 and contiguous and combined <= 30:
            candidates.append({"units": [left["draft_key"], right["draft_key"]],
                               "source_scene": next(iter(scenes)), "combined_duration": combined,
                               "review": "human_review_only"})
    return {"mode": "non_blocking", "unit_duration_seconds": {
                "distribution": {str(key): distribution[key] for key in sorted(distribution)},
                "mean": sum(durations) / len(durations) if durations else None,
                "min": min(durations) if durations else None, "max": max(durations) if durations else None},
            "same_scene_adjacent_candidates": candidates,
            "camera_wording": {"basis": "lexical_leading_camera_clause_only",
                               "semantic_review": "not_performed",
                               "by_shot_duration_seconds": dict(sorted(camera.items(), key=lambda row: int(row[0])))}}


def render_unit(state, unit, reference_text=None):
    """Render one previously checked unit; quoted speech never enters callbacks."""
    import director as d
    import ratio_profiles
    parsed = _parse_units(unit["source_text"], state)
    _require(len(parsed) == 1, "单元source_text须恰好包含一个单元")
    authored = parsed[0]
    _, _, _, assets = _context(state)
    convert = reference_text or (lambda text: text)

    def asset(aid):
        _require(aid in assets, f"无法渲染未知资产：{aid}")
        item = assets[aid]
        if item.get("visual_mode") == "prompt_only":
            return item["name"]
        return convert("@" + item["name"] + " ")

    def replace(value):
        _refs(value, authored["draft_key"])
        return REFERENCE.sub(lambda match: asset(match.group(1)), value)

    def display_voice(value):
        if unit.get("voice_display_policy") == VOICE_DISPLAY_POLICY and value == "内心想法":
            return "内心想法os"
        return value

    style = unit.get("style", "")
    d.validate_style(style)
    title = unit.get("display_name") or unit.get("id")
    _require(isinstance(title, str) and title and not any(c in title for c in "\r\n"), "渲染前须分配单元id/display_name")
    # scene-text-v1 originally rendered visual-only-v2 even for unmarked input.
    # New authoring sets an explicit policy; old receipts retain that old default.
    header = d.fixed_header(unit.get("header_policy", "visual-only-v2"))
    people = "，".join(asset(cid) for cid in authored["character_ids"]) or "无"
    ratio = unit.get('video_ratio')
    _require(ratio in (None, '16:9', '9:16'), '视频比例须为16:9或9:16')
    # New deliveries freeze a ratio profile alongside the ratio.  Historical
    # units without these fields remain renderable under their saved policy.
    if unit.get('video_ratio') is not None and unit.get('ratio_profile') is not None:
        ratio_profiles.require_unit_profile(unit)
    else:
        ratio_profiles.profile_for_unit(unit)
    ratio_label = '' if ratio is None else ('横屏16:9' if ratio == '16:9' else '竖屏9:16')
    lines = [f'{title}（时长：{authored["duration"]}秒）{ratio_label}', style + ";" + header[0], header[1],
             "人物：" + people + "；",
             "场景：" + "；".join(asset(sid) for sid in authored["set_ids"])
             + f'；时间：{replace(authored["time"])}；天气：{replace(authored["weather"])}；',
             "人物站位：" + replace(authored["global"])]
    for shot in authored["shots"]:
        # Inject the exact source fragments immediately before the final SFX.
        visual, sound = shot["text"].rsplit("音效：", 1)
        line = replace(visual)
        for part in shot["dialogue_parts"]:
            if part.get("source_kind") == "non_character":
                source = part["source_label"]
            elif "character_ids" in part:
                # Joint speech remains one source event and one verbatim line.
                # Stable IDs are validated and retained in the character list;
                # the displayed attribution preserves the original source names.
                source = "、".join(_speaker(assets[cid]) for cid in part["character_ids"])
            else:
                source = asset(part["character_id"])
            line += source + display_voice(part["voice"]) + part["direction"] + '：“' + part["text"] + '”'
        lines.append(line + "音效：" + replace(sound))
    return "\n".join(lines) + "\n"
