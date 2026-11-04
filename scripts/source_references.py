"""Candidate-local existing SET/PROP references tied to exact source records.

This validates locatable declarations, never location identity or story meaning.
No ledger, source, binding or historical receipt is changed by this module.
"""
import copy


def declarations(trace):
    if not isinstance(trace, dict) or 'source_references' not in trace:
        return []
    if trace.get('policy') != 'shot-by-shot-v1' or trace.get('version') != 4:
        raise ValueError('source_references须使用v4作者依据')
    rows = trace['source_references']
    if not isinstance(rows, list):
        raise ValueError('source_references须为数组')
    return copy.deepcopy(rows)


def normalize(rows):
    """Stable syntax normalization, also usable for frozen-request recovery."""
    if not isinstance(rows, list):
        raise ValueError('source_references须为数组')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'scene_id', 'asset_id', 'source_records', 'evidence', 'basis'}:
            raise ValueError('来源引用须填写scene_id/asset_id/source_records/evidence/basis')
        sid, aid = row['scene_id'], row['asset_id']
        if not all(isinstance(v, str) and v.strip() for v in (sid, aid, row['basis'])):
            raise ValueError('来源引用场次、资产与basis须为非空文字')
        if (sid, aid) in seen:
            raise ValueError('同场同一资产只能声明一次来源引用')
        seen.add((sid, aid))
        rids, evidence = row['source_records'], row['evidence']
        if (not isinstance(rids, list) or not rids or not all(isinstance(r, str) for r in rids)
                or len(set(rids)) != len(rids) or not isinstance(evidence, list)):
            raise ValueError('来源引用须有非空、不重复source_records及逐记录evidence')
        quotes = {}
        for item in evidence:
            if (not isinstance(item, dict) or set(item) != {'record_id', 'quote'}
                    or not isinstance(item['record_id'], str) or item['record_id'] in quotes
                    or not isinstance(item['quote'], str) or not item['quote'].strip()):
                raise ValueError('来源引用evidence须为不重复的record_id及非空精确quote')
            quotes[item['record_id']] = item['quote']
        if set(quotes) != set(rids):
            raise ValueError('来源引用evidence须恰好覆盖声明记录')
        ordered = sorted(rids)
        result.append(dict(scene_id=sid, asset_id=aid, source_records=ordered,
                           evidence=[{'record_id': rid, 'quote': quotes[rid]} for rid in ordered],
                           basis=row['basis']))
    return sorted(result, key=lambda row: (row['scene_id'], row['asset_id']))


def validate(state, rows, selected_records):
    rows = normalize(rows)
    if rows and state.get('storyboard_authoring_policy') != 'shot-by-shot-v1':
        raise ValueError('来源引用仅用于启用逐镜作者依据的文字分镜')
    records = {r['id']: r for r in state.get('records', [])}
    selected = set(selected_records)
    scenes = {s['id'] for s in state.get('ledger', {}).get('scenes', [])}
    allowed = {a['id'] for field in ('scene_assets', 'props')
               for a in state.get('ledger', {}).get(field, [])
               if a['id'].startswith('SET' if field == 'scene_assets' else 'PROP')}
    for row in rows:
        if row['scene_id'] not in scenes or row['asset_id'] not in allowed:
            raise ValueError('来源引用须为既有场次及既有SET/PROP，不能新增资产或人物')
        for item in row['evidence']:
            record = records.get(item['record_id'])
            if (record is None or record['id'] not in selected or record['scene'] != row['scene_id']
                    or item['quote'] not in record['text']):
                raise ValueError('来源引用记录或精确证据越出当前场次及实际选区')
    return rows


def project(rows, unit):
    """Only records actually consumed by this unit grant reference permission."""
    result = []
    for row in rows:
        local = [rid for rid in row['source_records'] if rid in unit['source_records']]
        if row['scene_id'] in unit['scene_ids'] and local:
            result.append({**copy.deepcopy(row), 'source_records': local,
                           'evidence': [copy.deepcopy(e) for e in row['evidence'] if e['record_id'] in local]})
    return result


def apply(state, units, rows):
    rows = validate(state, rows, [rid for unit in units for rid in unit['source_records']])
    for unit in units:
        local = project(rows, unit)
        if local:
            unit['source_references'] = local
    return rows


def from_units(units):
    """Reassemble frozen declarations without consulting today's permissions."""
    merged = {}
    for unit in units:
        for row in normalize(unit.get('source_references', [])):
            if project([row], unit) != [row]:
                raise ValueError('单元来源引用超出其真实消费记录')
            key = (row['scene_id'], row['asset_id'])
            if key not in merged:
                merged[key] = copy.deepcopy(row)
                continue
            old = merged[key]
            if old['basis'] != row['basis']:
                raise ValueError('同场同一资产的来源引用basis不一致')
            quotes = {e['record_id']: e['quote'] for e in old['evidence']}
            for evidence in row['evidence']:
                rid, quote = evidence['record_id'], evidence['quote']
                if rid in quotes and quotes[rid] != quote:
                    raise ValueError('共享来源记录的引用证据不一致')
                quotes[rid] = quote
            old['source_records'] = sorted(quotes)
            old['evidence'] = [{'record_id': rid, 'quote': quotes[rid]} for rid in sorted(quotes)]
    return normalize(list(merged.values()))
