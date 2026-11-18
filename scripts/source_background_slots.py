"""Source-scoped, silent prompt-only background slots; never ledger edits.

Evidence and authorization are mechanically checked. Anonymous group identity,
text sufficiency, continued presence and staging remain author/reviewer judgments.
"""
import copy
import re
from pathlib import Path

FIELD = 'source_background_slots'
SLOT_FIELDS = (FIELD, 'source_participant_slots')
_FIELDS = {'scene_id', 'asset_id', 'name', 'source_records', 'evidence', 'basis',
           'text_sufficiency_reason', 'authorization'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def declarations(trace, *, field=FIELD):
    if not isinstance(trace, dict) or field not in trace:
        return []
    require(trace.get('policy') == 'shot-by-shot-v1' and trace.get('version') == 4,
            '静默背景槽仅用于v4作者依据')
    return normalize(trace[field], field=field)


def normalize(rows, *, field=FIELD):
    require(field in SLOT_FIELDS, '未知静默来源槽协议')
    require(isinstance(rows, list), field + '须为数组')
    result, seen = [], set()
    for original in rows:
        optional = {'continuity'} if field == FIELD else set()
        require(isinstance(original, dict) and _FIELDS <= set(original)
                and set(original) <= _FIELDS | optional, '静默背景槽字段错误')
        row = copy.deepcopy(original)
        for key in ('scene_id', 'asset_id', 'name', 'basis', 'text_sufficiency_reason'):
            require(isinstance(row[key], str) and row[key].strip() == row[key] and row[key],
                    '静默背景槽须有名称、来源、具体依据与人工作出的文字充分性说明')
        require(re.fullmatch(r'SC\d{4,}', row['scene_id']) and re.fullmatch(r'CHAR\d{4,}', row['asset_id']), '静默背景槽ID错误')
        require(not any(c in row['name'] for c in '@{}\r\n') and row['name'] != '黑幕', '静默背景槽名称不安全')
        require(row['asset_id'] not in seen, '同一候选背景CHAR只能声明一次；不能隐式跨来源合并身份')
        seen.add(row['asset_id'])
        rids, quotes = row['source_records'], {}
        require(isinstance(rids, list) and rids and len(set(rids)) == len(rids)
                and all(isinstance(rid, str) and re.fullmatch(r'L\d{6,}', rid) for rid in rids), '背景槽须有准确来源记录')
        require(isinstance(row['evidence'], list), '背景槽须有逐记录原句')
        for item in row['evidence']:
            require(isinstance(item, dict) and set(item) == {'record_id', 'quote'}
                    and isinstance(item['record_id'], str) and item['record_id'] not in quotes
                    and isinstance(item['quote'], str) and item['quote'].strip(), '背景槽原句证据错误')
            quotes[item['record_id']] = item['quote']
        require(set(quotes) == set(rids), '背景槽证据须恰好覆盖声明记录')
        if 'continuity' in row:
            continuity = row['continuity']
            require(isinstance(continuity, dict) and set(continuity) == {'anchor', 'basis'}
                    and isinstance(continuity['basis'], str) and continuity['basis'].strip(),
                    '背景延续须有准确anchor和实际持续在场依据')
            anchor = continuity['anchor']
            require(isinstance(anchor, dict) and set(anchor) in (
                {'record_id', 'quote'}, {'record_id', 'quote', 'unit_id', 'version'})
                and isinstance(anchor.get('record_id'), str)
                and re.fullmatch(r'L\d{6,}', anchor['record_id'])
                and isinstance(anchor.get('quote'), str) and anchor['quote'].strip(),
                '背景延续anchor须有动作记录及原句；前序单元须同时声明unit_id/version')
            if 'unit_id' in anchor:
                require(isinstance(anchor['unit_id'], str) and anchor['unit_id'].strip()
                        and type(anchor['version']) is int and anchor['version'] >= 1,
                        '背景延续前序单元ID或版本错误')
        ref = row['authorization']
        require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}
                and isinstance(ref['path'], str) and Path(ref['path']).is_absolute()
                and isinstance(ref['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', ref['sha256']), '背景槽须绑定实际授权文件SHA')
        row['source_records'] = sorted(rids)
        row['evidence'] = [{'record_id': rid, 'quote': quotes[rid]} for rid in sorted(rids)]
        result.append(row)
    return sorted(result, key=lambda r: (r['scene_id'], r['asset_id']))


def asset(row):
    return {'id': row['asset_id'], 'name': row['name'], 'visual_mode': 'prompt_only'}


def _inventory(state):
    items = list(state['ledger']['characters'])
    # Supplemental image designs already frozen in history own their IDs too.
    for unit in [*state.get('units', []), *[u for r in state.get('receipts', []) for u in r.get('units', [])]]:
        items.extend(r['asset'] for r in unit.get('production_cast', []) if 'asset' in r)
        for slot_field in SLOT_FIELDS:
            items.extend(asset(r) for r in unit.get(slot_field, []))
    return items


def _frozen_anchor(state, row):
    """Only an explicit current unit/version can authorize a carried identity."""
    anchor = row.get('continuity', {}).get('anchor', {})
    if 'unit_id' not in anchor:
        return None
    unit = next((u for u in state.get('units', []) if u.get('id') == anchor['unit_id']), None)
    if (unit is None or unit.get('version') != anchor['version']
            or row['scene_id'] not in unit.get('scene_ids', [])
            or row['asset_id'] not in unit.get('character_ids', [])
            or anchor['record_id'] not in unit.get('source_records', [])):
        return None
    evidence = {'record_id': anchor['record_id'], 'quote': anchor['quote']}
    for prior in unit.get(FIELD, []):
        if prior.get('scene_id') != row['scene_id'] or asset(prior) != asset(row):
            continue
        old_anchor = prior.get('continuity', {}).get('anchor', {})
        inherited = {k: old_anchor.get(k) for k in ('record_id', 'quote')} == evidence
        direct = any(e.get('record_id') == anchor['record_id'] and anchor['quote'] in e.get('quote', '')
                     for e in prior.get('evidence', []))
        if direct or inherited:
            return unit
    return None


def context(state, rows, *, field=FIELD):
    """Read-only catalog overlay; does not add scene members or voice rights."""
    rows = normalize(rows, field=field)
    if not rows:
        return state
    from storyboard_text import _character_aliases
    own = {r['asset_id']: r for r in state.get('_' + field, [])}
    inventory = _inventory(state)
    result = copy.deepcopy(state)
    chars = {a['id']: a for a in result['ledger']['characters']}
    frozen_units = list(state.get('units', []))
    frozen_units.extend(u for receipt in state.get('receipts', []) for u in receipt.get('units', []))
    for row in rows:
        aid, proposed = row['asset_id'], asset(row)
        aliases = _character_aliases(proposed)
        identity = {k: v for k, v in row.items() if k not in ('source_records', 'evidence')}
        frozen = [prior for unit in frozen_units for prior in unit.get(field, [])
                  if {k: v for k, v in prior.items() if k not in ('source_records', 'evidence')} == identity]
        carried = field == FIELD and _frozen_anchor(state, row) is not None
        for old in inventory:
            if old['id'] == aid:
                require(old == proposed and (own.get(aid) == row or frozen or carried),
                    '背景槽ID与既有人物/设计/冻结槽冲突')
            else:
                require(not aliases & _character_aliases(old), '背景槽名称与既有具名/发声/群体身份冲突')
        require(aid not in chars or chars[aid] == proposed, '不能覆盖既有人物')
        chars[aid] = proposed
        inventory.append(proposed)
    result['ledger']['characters'] = list(chars.values())
    result['_' + field] = copy.deepcopy(rows)
    return result


def validate(state, rows, selected, *, source_state=None, field=FIELD):
    rows = normalize(rows, field=field)
    if not rows:
        return []
    require(state.get('storyboard_authoring_policy') == 'shot-by-shot-v1', '静默背景槽仅用于逐镜文字分镜')
    context(source_state or state, rows, field=field)
    scenes = {s['id']: s for s in state['ledger']['scenes']}
    records = {r['id']: r for r in state['records']}
    full_state = source_state or state
    full_records = {r['id']: r for r in full_state['records']}
    order = {r['id']: i for i, r in enumerate(full_state['records'])}
    from production_cast import _authorized
    cache = {}
    for row in rows:
        require(row['scene_id'] in scenes and set(row['source_records']) <= set(selected), '背景槽越出本候选实际记录')
        require(all(rid in records and records[rid]['scene'] == row['scene_id']
                    for rid in row['source_records']), '背景槽实际使用记录不存在或跨场')
        continuity = row.get('continuity')
        if continuity:
            anchor = continuity['anchor']
            previous = None
            if 'unit_id' in anchor:
                previous = _frozen_anchor(full_state, row)
                require(previous is not None, '选区外背景延续须引用当前已冻结同群体的单元及准确版本')
                source = full_records.get(anchor['record_id'])
                if source is not None:
                    from director import portion
                    start, end = previous['span']
                    source = dict(source, text=portion(source, start, end),
                                  start=max(source['start'], start), end=min(source['end'], end))
            else:
                # A record ID may intersect this candidate while its original
                # group-establishment quote lies outside the clipped portion.
                require(anchor['record_id'] in selected,
                        '选区外背景延续须引用当前已冻结同群体的单元及准确版本')
                source = records.get(anchor['record_id'])
            require(source is not None and source['scene'] == row['scene_id']
                    and source['kind'] == 'action' and not source.get('speaker')
                    and anchor['quote'] in source['text'],
                    '背景延续anchor须为同场明示群体的准确动作原句，不能借对白或选区外片段')
            require(all(order[source['id']] <= order.get(rid, -1) for rid in row['source_records']),
                    '背景延续不能借未来动作建立先前在场')
            if previous is not None:
                require(previous.get('span', [0, float('inf')])[1]
                        <= min(records[rid]['start'] for rid in row['source_records']),
                        '背景延续前序单元须先于本次实际使用记录')
            _authorized(row, source, field, cache)
        for evidence in row['evidence']:
            record = records.get(evidence['record_id'])
            require(record is not None and record['scene'] == row['scene_id']
                    and (continuity or (record['kind'] == 'action' and not record.get('speaker')))
                    and evidence['quote'] in record['text'],
                    '背景槽证据须逐字位于本次裁剪记录；无延续声明须为动作，不能借完整未选记录')
            _authorized(row, record, field, cache)
    return rows


def project(rows, unit, *, field=FIELD):
    result = []
    for row in normalize(rows, field=field):
        local = [rid for rid in row['source_records'] if rid in unit['source_records']]
        if row['scene_id'] in unit['scene_ids'] and local:
            result.append({**copy.deepcopy(row), 'source_records': local,
                           'evidence': [copy.deepcopy(e) for e in row['evidence'] if e['record_id'] in local]})
    return result


def apply(state, units, rows, *, source_state=None, field=FIELD):
    rows = validate(state, rows, [rid for u in units for rid in u['source_records']], source_state=source_state, field=field)
    for unit in units:
        local = project(rows, unit, field=field)
        if local:
            unit[field] = local
    return rows


def require_usage(units, rows, *, field=FIELD):
    """A known slot's bare label cannot bypass its record scope or CHAR roster."""
    from storyboard_text import _character_aliases, _mask_quoted_prose
    silent = {row['asset_id'] for row in rows}
    for unit in units:
        prose = _mask_quoted_prose(unit.get('global', '') + '\n' + '\n'.join(s.get('text', '') for s in unit.get('shots', [])))
        local = {r['asset_id'] for r in unit.get(field, [])}
        for row in rows:
            aid = row['asset_id']
            mentioned = ('@{' + aid + '}') in prose or any(label in prose for label in _character_aliases(asset(row)))
            if mentioned:
                require(aid in local and aid in unit.get('character_ids', []),
                        '静默背景槽正文/局部实体不能用空asset_ids或裸称绕过本单元真实记录与CHAR名单')
        for shot in unit.get('shots', []):
            for part in shot.get('dialogue_parts', []):
                ids = part.get('character_ids', [part.get('character_id')])
                require(not silent.intersection(ids), '静默背景槽不授予独立声音或制作发言者资格')


def usage_rows(units, *, field=FIELD):
    """Collect identities for usage checks without merging separate approvals."""
    identities = {}
    for unit in units:
        for row in normalize(unit.get(field, []), field=field):
            require(project([row], unit, field=field) == [row], '冻结背景槽越出单元真实消费记录')
            previous = identities.get(row['asset_id'])
            require(previous is None or (previous['scene_id'] == row['scene_id']
                    and asset(previous) == asset(row)), '冻结背景槽身份或场次不一致')
            identities.setdefault(row['asset_id'], row)
    return [identities[aid] for aid in sorted(identities)]


def frozen_context(state, units):
    """Overlay each frozen scope separately; never synthesize a combined grant."""
    identities = usage_rows(units)
    result = state
    for unit in units:
        result = context(result, unit.get(FIELD, []))
    if identities:
        # This private list supplies names only; actual scope remains on each
        # unit, and no authorization or anchor is refreshed for history.
        result['_' + FIELD] = copy.deepcopy(identities)
    return result


def from_units(units, *, field=FIELD):
    merged = {}
    for unit in units:
        for row in normalize(unit.get(field, []), field=field):
            require(project([row], unit, field=field) == [row], '冻结背景槽越出单元真实消费记录')
            aid = row['asset_id']
            if aid not in merged:
                merged[aid] = copy.deepcopy(row)
                continue
            old = merged[aid]
            require({k: v for k, v in old.items() if k not in ('source_records', 'evidence')}
                    == {k: v for k, v in row.items() if k not in ('source_records', 'evidence')}, '冻结背景槽身份/授权/文字依据不一致')
            quotes = {e['record_id']: e['quote'] for e in old['evidence']}
            for e in row['evidence']:
                require(e['record_id'] not in quotes or quotes[e['record_id']] == e['quote'], '共享背景槽原句不一致')
                quotes[e['record_id']] = e['quote']
            old['source_records'] = sorted(quotes)
            old['evidence'] = [{'record_id': rid, 'quote': quotes[rid]} for rid in sorted(quotes)]
    return normalize(list(merged.values()), field=field)
