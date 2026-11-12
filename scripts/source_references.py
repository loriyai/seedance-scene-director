"""Candidate-local existing references tied to exact source records.

This validates locatable declarations, never location identity or story meaning.
No ledger, source, binding or historical receipt is changed by this module.
"""
import copy
import re


PHOTO_BODY_PART = 'photo-body-part-v1'
_FIELDS = {'scene_id', 'asset_id', 'source_records', 'evidence', 'basis'}
_PHOTO_FIELDS = {'mode', 'carrier_asset_id', 'anchor'}
_IMAGE = re.compile(r'照片|相片|图片|大图')
_TAKE_PHOTO = re.compile(r'拍[^。！？\n]{0,12}(?:照|相片)')


def photo_characters(rows):
    return [row for row in rows if row.get('mode') == PHOTO_BODY_PART]


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
        if not isinstance(row, dict) or set(row) not in (_FIELDS, _FIELDS | _PHOTO_FIELDS):
            raise ValueError('来源引用须填写scene_id/asset_id/source_records/evidence/basis')
        sid, aid = row['scene_id'], row['asset_id']
        if not all(isinstance(v, str) and v.strip() for v in (sid, aid, row['basis'])):
            raise ValueError('来源引用场次、资产与basis须为非空文字')
        if set(row) != _FIELDS:
            anchor = row['anchor']
            if (row['mode'] != PHOTO_BODY_PART or not re.fullmatch(r'CHAR\d{4,}', aid)
                    or not isinstance(row['carrier_asset_id'], str)
                    or not re.fullmatch(r'PROP\d{4,}', row['carrier_asset_id'])
                    or not isinstance(anchor, dict) or set(anchor) != {'record_id', 'quote'}
                    or not isinstance(anchor['record_id'], str)
                    or not re.fullmatch(r'L\d{6,}', anchor['record_id'])
                    or not isinstance(anchor['quote'], str) or not anchor['quote'].strip()):
                raise ValueError('照片局部人物须使用photo-body-part-v1及既有PROP载体、前序record_id/quote锚点')
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
        normalized = dict(scene_id=sid, asset_id=aid, source_records=ordered,
                          evidence=[{'record_id': rid, 'quote': quotes[rid]} for rid in ordered],
                          basis=row['basis'])
        if 'mode' in row:
            normalized.update({key: copy.deepcopy(row[key])
                               for key in ('mode', 'carrier_asset_id', 'anchor')})
        result.append(normalized)
    return sorted(result, key=lambda row: (row['scene_id'], row['asset_id']))


def validate(state, rows, selected_records, *, source_state=None):
    rows = normalize(rows)
    if rows and state.get('storyboard_authoring_policy') != 'shot-by-shot-v1':
        raise ValueError('来源引用仅用于启用逐镜作者依据的文字分镜')
    records = {r['id']: r for r in state.get('records', [])}
    selected = set(selected_records)
    scenes = {s['id']: s for s in state.get('ledger', {}).get('scenes', [])}
    allowed = {a['id'] for field in ('scene_assets', 'props')
               for a in state.get('ledger', {}).get(field, [])
               if a['id'].startswith('SET' if field == 'scene_assets' else 'PROP')}
    for row in rows:
        embedded = row.get('mode') == PHOTO_BODY_PART
        if row['scene_id'] not in scenes or (not embedded and row['asset_id'] not in allowed):
            raise ValueError('来源引用须为既有场次及既有SET/PROP，不能新增资产或人物')
        for item in row['evidence']:
            record = records.get(item['record_id'])
            if (record is None or record['id'] not in selected or record['scene'] != row['scene_id']
                    or item['quote'] not in record['text']):
                raise ValueError('来源引用记录或精确证据越出当前场次及实际选区')
        if embedded:
            _validate_photo(state, source_state if source_state is not None else state,
                            row, records, scenes)
    return rows


def _validate_photo(state, source_state, row, records, scenes):
    """Locate evidence only; this does not prove photo identity or send/receive."""
    from storyboard_text import scene_asset_ids
    characters = {a['id']: a for a in source_state.get('ledger', {}).get('characters', [])}
    props = {a['id']: a for a in source_state.get('ledger', {}).get('props', [])}
    aid, carrier = row['asset_id'], row['carrier_asset_id']
    if (aid not in characters or characters[aid].get('visual_mode') == 'prompt_only'
            or carrier not in props or carrier not in scenes[row['scene_id']].get('prop_ids', [])):
        raise ValueError('照片局部须为既有图片CHAR及本场台账已合法的PROP载体')
    if any(records[rid].get('kind') != 'action' for rid in row['source_records']):
        raise ValueError('照片局部本场证据须为实际图像动作记录，不能仅以对白提及扩权')
    names = [name for name in [props[carrier]['name'], *props[carrier].get('aliases', [])]
             if isinstance(name, str) and name]
    if any(not _IMAGE.search(item['quote']) or not any(name in item['quote'] for name in names)
           for item in row['evidence']):
        raise ValueError('照片局部本场精确证据须同时定位图像层与载体原词')
    originals = {r['id']: r for r in source_state.get('records', [])}
    previous = originals.get(row['anchor']['record_id'])
    previous_scene = scenes.get(previous['scene']) if previous else None
    quote = row['anchor']['quote']
    character_name = characters[aid].get('source_entry', characters[aid]['name'])
    first = min(records[rid]['start'] for rid in row['source_records'])
    if (previous is None or previous.get('kind') != 'action' or previous['end'] > first
            or not previous_scene or aid not in scene_asset_ids(previous_scene)['characters']
            or carrier not in previous_scene.get('prop_ids', [])
            or quote not in previous['text'] or character_name not in quote
            or not _TAKE_PHOTO.search(quote) or not any(name in quote for name in names)):
        raise ValueError('照片局部锚点须为该精确CHAR形象及合法载体已登记的前序拍照动作原句')


def photo_reference_issues(unit, ordinary_characters=()):
    """New-mode lexical containment only, never proof of depicted identity."""
    from storyboard_text import SHOT
    issues = []
    for row in photo_characters(unit.get('source_references', [])):
        aid, carrier = row['asset_id'], row['carrier_asset_id']
        target, container = '@{' + aid + '}', '@{' + carrier + '}'
        visible = False
        for shot in unit.get('shots', []):
            text = shot.get('text', '')
            match = SHOT.fullmatch(text)
            body = match.group(7).split('音效：', 1)[0] if match else ''
            if target not in text:
                continue
            image = bool(_IMAGE.search(body))
            # This declaration cannot narrow another already-legal character
            # reference in a multi-scene unit. Only the new permission is bound
            # to the photo; ordinary references retain their existing checks.
            bound_photo = target in body and image and container in body
            visible = visible or bound_photo
            if aid in ordinary_characters:
                continue
            if not bound_photo:
                issues.append({'code': 'EMBEDDED_CHARACTER_CARRIER', 'unit': unit['draft_key'],
                               'message': '照片局部人物每处正文引用须同时明确照片层及其PROP载体，不能转作实地人物'})
        if not visible:
            issues.append({'code': 'EMBEDDED_CHARACTER_UNUSED', 'unit': unit['draft_key'],
                           'message': '照片局部声明须在消费该来源记录的本单元镜头内实际引用该CHAR'})
    return issues


def project(rows, unit):
    """Only records actually consumed by this unit grant reference permission."""
    result = []
    for row in rows:
        local = [rid for rid in row['source_records'] if rid in unit['source_records']]
        if row['scene_id'] in unit['scene_ids'] and local:
            result.append({**copy.deepcopy(row), 'source_records': local,
                           'evidence': [copy.deepcopy(e) for e in row['evidence'] if e['record_id'] in local]})
    return result


def apply(state, units, rows, *, source_state=None):
    rows = validate(state, rows, [rid for unit in units for rid in unit['source_records']],
                    source_state=source_state)
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
            if ({key: value for key, value in old.items() if key not in ('source_records', 'evidence')}
                    != {key: value for key, value in row.items() if key not in ('source_records', 'evidence')}):
                if 'mode' in old or 'mode' in row:
                    raise ValueError('同场同一资产的来源引用basis或照片局部声明不一致')
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
