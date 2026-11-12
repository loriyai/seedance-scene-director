"""Explicitly authorized verbatim source-text voices; never source dialogue.

Only actual message/screen wording may be declared. Sender attribution, required
information and natural delivery require author and independent human reading.
This module neither creates speech nor grants visual or image eligibility.
"""
import copy
import re

FIELD = 'source_text_voices'
EVENT = re.compile(r'SV\d{6,}')
ANNOUNCER = 'female_announcer'
ANNOUNCER_LABEL = '女播音腔'
ROLES = ('sender_message', 'unattributed_information', 'amount_receipt')


def require(ok, message):
    if not ok:
        raise ValueError('原剧情文字声音：' + message)


def declarations(trace):
    if not isinstance(trace, dict) or FIELD not in trace:
        return []
    require(trace.get('policy') == 'shot-by-shot-v1' and trace.get('version') == 4,
            '须使用v4作者依据')
    if trace[FIELD]:
        require(trace.get('context_policy') in (None, 'selected-content-context-v3')
                and (not trace.get('context_sha256') or trace.get('context_policy') == 'selected-content-context-v3'),
                '新声音声明须使用selected-content-context-v3绑定，不能沿旧上下文旁路')
    return normalize(trace[FIELD])


def normalize(rows):
    from pathlib import Path
    require(isinstance(rows, list), FIELD + '须为数组')
    result, seen = [], set()
    fields = {'event_id', 'scene_id', 'evidence', 'speaker', 'voice',
              'reading_role', 'basis', 'authorization'}
    for original in rows:
        require(isinstance(original, dict) and set(original) == fields, '声明字段错误')
        row = copy.deepcopy(original)
        for key in ('event_id', 'scene_id', 'speaker', 'voice', 'reading_role', 'basis'):
            require(isinstance(row[key], str) and row[key].strip() == row[key] and row[key], key + '须为非空文字')
        require(EVENT.fullmatch(row['event_id']) and row['event_id'] not in seen, '须有独立、唯一SV事件ID')
        seen.add(row['event_id'])
        require(re.fullmatch(r'SC\d{4,}', row['scene_id']), '场次ID错误')
        require(row['voice'] in ('画外音', '内心想法os'), '只允许画外音或内心想法os')
        require(row['reading_role'] in ROLES, 'reading_role错误')
        require(row['speaker'] == ANNOUNCER or re.fullmatch(r'CHAR\d{4,}', row['speaker']), '声源须为真实CHAR或female_announcer')
        if row['reading_role'] == 'sender_message':
            require(row['speaker'] != ANNOUNCER, '明确发送者消息须使用其真实CHAR声源')
        else:
            require(row['speaker'] == ANNOUNCER and row['voice'] == '画外音', '到账及无明确发送者信息须为女播音腔画外音')
        if row['speaker'] == ANNOUNCER:
            require(row['voice'] == '画外音', '女播音腔只能画外音')
        evidence = row['evidence']
        require(isinstance(evidence, dict) and set(evidence) == {'record_id', 'quote', 'occurrence'}, '证据须包含record_id、quote及occurrence')
        require(isinstance(evidence['record_id'], str) and re.fullmatch(r'L\d{6,}', evidence['record_id'])
                and isinstance(evidence['quote'], str) and evidence['quote'].strip()
                and not any(c in evidence['quote'] for c in '\r\n\x00')
                and type(evidence['occurrence']) is int and evidence['occurrence'] > 0, '须有精确原文字及从1开始的occurrence')
        auth = row['authorization']
        require(isinstance(auth, dict) and set(auth) == {'path', 'sha256'}
                and isinstance(auth['path'], str) and Path(auth['path']).is_absolute()
                and isinstance(auth['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', auth['sha256']), '须绑定明确操作授权文件')
        result.append(row)
    return sorted(result, key=lambda row: row['event_id'])


def location(row, record):
    """Locate non-overlapping literal occurrences; never normalize source text."""
    quote = row['evidence']['quote']
    offset = -len(quote)
    for _ in range(row['evidence']['occurrence']):
        offset = record['text'].find(quote, offset + len(quote))
        require(offset >= 0, '证据quote/occurrence越出实际选区原文字')
    return offset, offset + len(quote)


def validate(state, rows, selected, *, source_state=None, authorize=True):
    from production_cast import _authorized
    rows = normalize(rows)
    if not rows:
        return rows
    require(state.get('storyboard_authoring_policy') == 'shot-by-shot-v1', '仅用于逐镜文字分镜')
    records = {r['id']: r for r in state['records']}
    full = {r['id']: r for r in (source_state or state)['records']}
    chars = {a['id'] for a in state['ledger']['characters']}
    cache, ranges = {}, {}
    for row in rows:
        evidence = row['evidence']
        record = records.get(evidence['record_id'])
        require(record is not None and record['id'] in selected and record['scene'] == row['scene_id']
                and record['kind'] == 'action', '证据须来自实际选区同场action，不得借对白或未选后文')
        start, end = location(row, full[record['id']])
        # The full-record occurrence remains stable even when a candidate is
        # clipped. Confirm both its coordinates and literal slice are selected.
        from storyboard_text import _compact
        absolute_start = full[record['id']]['start'] + len(_compact(full[record['id']]['text'][:start]))
        absolute_end = full[record['id']]['start'] + len(_compact(full[record['id']]['text'][:end]))
        require(record['start'] <= absolute_start < absolute_end <= record['end']
                and evidence['quote'] in record['text'], '证据越出实际选区原文字')
        require(row['speaker'] == ANNOUNCER or row['speaker'] in chars, '发送者须为已有真实CHAR')
        for prior_start, prior_end in ranges.setdefault(record['id'], []):
            require(end <= prior_start or start >= prior_end, '同一原文字位置不能重复声明声音事件')
        ranges[record['id']].append((start, end))
        if authorize:
            _authorized(row, record, FIELD, cache)
    return rows


def project(rows, unit):
    return [copy.deepcopy(row) for row in normalize(rows)
            if row['scene_id'] in unit['scene_ids'] and row['evidence']['record_id'] in unit['source_records']]


def apply(state, units, rows, *, source_state=None):
    rows = validate(state, rows, {rid for unit in units for rid in unit['source_records']}, source_state=source_state)
    for unit in units:
        local = project(rows, unit)
        # A shared boundary action can have several event fragments. A unit
        # freezes only events it actually speaks, rather than all neighbors.
        spoken = {p['record'] for shot in unit['shots'] for p in shot['dialogue_parts']}
        local = [row for row in local if row['event_id'] in spoken]
        if local:
            unit[FIELD] = local
    used = {row['event_id'] for unit in units for row in unit.get(FIELD, [])}
    require(used == {row['event_id'] for row in rows}, '声明事件须被实际消费，不能静默丢弃')


def from_units(units):
    merged = {}
    for unit in units:
        for row in normalize(unit.get(FIELD, [])):
            require(project([row], unit) == [row], '冻结事件越出单元消费来源')
            key = row['event_id']
            require(key not in merged or merged[key] == row, '共享SV事件定义不一致')
            merged[key] = row
    return normalize(list(merged.values()))


def speakers(unit):
    return {row['speaker'] for row in normalize(unit.get(FIELD, [])) if row['speaker'] != ANNOUNCER}


def event_map(units):
    return {row['event_id']: row for row in from_units(units)}


def check_part(part, row, unit):
    require(part['voice'] == ('内心想法' if row['voice'] == '内心想法os' else row['voice']), '事件声型与声明不一致')
    if row['speaker'] == ANNOUNCER:
        require(part.get('source_kind') == 'non_character' and part.get('source_label') == ANNOUNCER_LABEL,
                '无发送者及到账事件须保留女播音腔声源')
    else:
        require(part.get('character_id') == row['speaker'] and not part.get('character_ids')
                and row['speaker'] in unit['character_ids'], '事件声源须为声明发送者并列入人物名单')


def source_record(part, unit):
    if EVENT.fullmatch(part['record']):
        rows = {r['event_id']: r for r in unit.get(FIELD, [])}
        require(part['record'] in rows, 'SV声音缺少已授权声明')
        return rows[part['record']]['evidence']['record_id']
    return part['record']


def voice_scope_only_ids(state, unit):
    """Out-of-scene sender permission cannot create a visible body or image."""
    declared_speakers = speakers(unit)
    if not declared_speakers:
        return set()
    from storyboard_text import scene_asset_ids
    from source_references import photo_characters
    scenes = {s['id']: s for s in state['ledger']['scenes']}
    visual = set().union(*(set(scene_asset_ids(scenes[sid])['characters']) for sid in unit['scene_ids']))
    for field in ('production_cast', 'source_background_slots', 'source_participant_slots'):
        visual.update(row['asset_id'] for row in unit.get(field, []))
    visual.update(row['asset_id'] for row in photo_characters(unit.get('source_references', [])))
    return declared_speakers - visual


def voice_only_ids(state, unit):
    """Exclude only pure SV sound identities, never an actual image subject.

    A scene roster gives visual eligibility, not proof of an image use. Real
    visual references and preexisting ordinary speech keep their image rights.
    """
    declared = speakers(unit)
    if not declared:
        return set()
    from storyboard_text import _refs
    visual = set()
    fields = [unit['global'], unit['time'], unit['weather']] + [shot['text'] for shot in unit['shots']]
    for value in fields:
        visual.update(_refs(value, unit['draft_key']))
    original_voices = set()
    for shot in unit['shots']:
        for part in shot['dialogue_parts']:
            if not EVENT.fullmatch(part['record']):
                original_voices.update(part.get('character_ids') or ([part['character_id']] if part.get('character_id') else []))
    return declared - visual - original_voices


def require_visual_scope(unit, voice_only):
    if not voice_only:
        return
    from storyboard_text import _refs
    # Stable references are always required for visible bodies. Naming the
    # sender elsewhere does not silently acquire a visual right.
    fields = [unit['global'], unit['time'], unit['weather']] + [shot['text'] for shot in unit['shots']]
    require(not any(set(_refs(value, unit['draft_key'])) & voice_only for value in fields),
            '声音专用发送者不得借声明进入画面或图片引用')
