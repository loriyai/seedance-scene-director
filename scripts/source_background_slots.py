"""Exact-record, silent prompt-only background slots; never source-ledger edits.

Evidence and authorization are mechanically checked. Anonymous group identity,
text sufficiency and all actual staging remain author/reviewer judgments.
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
        require(isinstance(original, dict) and set(original) == _FIELDS, '静默背景槽字段错误')
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
        for old in inventory:
            if old['id'] == aid:
                require(old == proposed and (own.get(aid) == row or frozen),
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
    from production_cast import _authorized
    cache = {}
    for row in rows:
        require(row['scene_id'] in scenes and set(row['source_records']) <= set(selected), '背景槽越出本候选实际记录')
        for evidence in row['evidence']:
            record = records.get(evidence['record_id'])
            require(record is not None and record['scene'] == row['scene_id']
                    and record['kind'] == 'action' and not record.get('speaker')
                    and evidence['quote'] in record['text'], '背景槽证据须逐字位于本次裁剪动作记录，不能授予对白或借完整未选记录')
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
    from storyboard_lighting import visual_prose
    for unit in units:
        prose = _mask_quoted_prose('\n'.join(visual_prose(unit)))
        local = {r['asset_id'] for r in unit.get(field, [])}
        for row in rows:
            aid = row['asset_id']
            mentioned = ('@{' + aid + '}') in prose or any(label in prose for label in _character_aliases(asset(row)))
            if mentioned:
                require(aid in local and aid in unit.get('character_ids', []),
                        '静默背景槽正文/局部实体不能用空asset_ids或裸称绕过本单元真实记录与CHAR名单')


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
