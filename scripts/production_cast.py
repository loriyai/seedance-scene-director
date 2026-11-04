"""Explicit, source-scoped production performers and supplemental CHAR designs.

Source names and dialogue remain immutable. This is candidate-local data, not
a rewritten ledger, identity proof, semantic approval or cloud permission.
"""
import copy
import hashlib
import json
from pathlib import Path
import re


def require(ok, message):
    if not ok:
        raise ValueError('制作人物对应：' + message)


def declarations(trace):
    if not isinstance(trace, dict) or 'production_cast' not in trace:
        return []
    require(trace.get('policy') == 'shot-by-shot-v1' and trace.get('version') == 4,
            '须使用v4作者依据')
    return normalize(trace['production_cast'])


def normalize(rows):
    require(isinstance(rows, list), 'production_cast须为数组')
    result, seen = [], set()
    required = {'scene_id', 'actor_key', 'source_asset_id', 'asset_id', 'source_records', 'evidence', 'basis', 'authorization'}
    for original in rows:
        require(isinstance(original, dict) and required <= set(original)
                and set(original) <= required | {'sample_id', 'asset', 'kind'}, '声明字段错误')
        row = copy.deepcopy(original)
        row.setdefault('kind', 'performer')
        require(row['kind'] in ('performer', 'visual_group', 'appearance'), 'kind非法')
        for field in ('scene_id', 'actor_key', 'source_asset_id', 'asset_id', 'basis'):
            require(isinstance(row[field], str) and row[field].strip(), field + '须为非空文字')
        require(re.fullmatch(r'CHAR\d{4,}', row['asset_id']) and
                re.fullmatch(r'CHAR\d{4,}', row['source_asset_id']), '只允许CHAR制作对应')
        key = tuple(row[k] for k in ('scene_id', 'actor_key', 'source_asset_id', 'asset_id', 'kind'))
        require(key not in seen, '同场同一制作对应只声明一次')
        seen.add(key)
        rids = row['source_records']
        require(isinstance(rids, list) and rids and all(isinstance(r, str) and re.fullmatch(r'L\d{6,}', r) for r in rids)
                and len(rids) == len(set(rids)), '须有准确、不重复来源记录')
        quotes = {}
        require(isinstance(row['evidence'], list), 'evidence须为数组')
        for item in row['evidence']:
            require(isinstance(item, dict) and set(item) == {'record_id', 'quote'}
                    and isinstance(item['record_id'], str) and item['record_id'] not in quotes
                    and isinstance(item['quote'], str) and item['quote'].strip(), '逐记录证据错误')
            quotes[item['record_id']] = item['quote']
        require(set(quotes) == set(rids), '证据须恰好覆盖声明记录')
        row['source_records'] = sorted(rids)
        row['evidence'] = [{'record_id': rid, 'quote': quotes[rid]} for rid in sorted(rids)]
        auth = row['authorization']
        require(isinstance(auth, dict) and set(auth) == {'path', 'sha256'}
                and isinstance(auth['path'], str) and Path(auth['path']).is_absolute()
                and isinstance(auth['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', auth['sha256']), '须绑定明确操作授权文件')
        if 'sample_id' in row:
            require(isinstance(row['sample_id'], str) and row['sample_id'].strip(), 'sample_id须为非空文字')
        result.append(row)
    return sorted(result, key=lambda r: tuple(r[k] for k in ('scene_id', 'actor_key', 'asset_id', 'source_asset_id', 'kind')))


def context(state, rows):
    """Overlay only explicitly supplied candidate designs in a read-only copy."""
    rows = normalize(rows)
    if not any('asset' in row for row in rows):
        return state
    result = copy.deepcopy(state)
    chars = {a['id']: a for a in result['ledger']['characters']}
    names = {a['name']: a['id'] for a in chars.values()}
    definitions = {}
    for row in rows:
        if 'asset' not in row:
            continue
        asset = row['asset']
        require(isinstance(asset, dict) and asset.get('id') == row['asset_id']
                and set(asset) <= {'id', 'name', 'source_entry', 'source_speaker', 'visual_mode',
                                  'visual_design', 'image_prompt', 'image_format', 'appearance'}, '补充CHAR字段错误')
        require(all(isinstance(asset.get(k), str) and asset[k].strip() for k in ('name', 'source_entry', 'image_prompt'))
                and not any(c in asset['name'] for c in '@{}\r\n'), '补充CHAR缺少完整安全名称、原条目或实际图片提示词')
        require(asset.get('visual_mode') == 'image_asset' and isinstance(asset.get('visual_design'), dict)
                and asset.get('image_format', {}).get('aspect_ratio') == '16:9', '补充CHAR须有16:9图片设计')
        aid = asset['id']
        require(aid not in definitions or definitions[aid] == asset, '补充CHAR定义不一致')
        definitions[aid] = asset
        require(asset['name'] not in names or names[asset['name']] == aid, '补充CHAR名称与既有身份冲突')
        old = chars.get(aid)
        if old is not None:
            require(all(k not in old or old[k] == value for k, value in asset.items()),
                    '不能覆盖已有CHAR设计或身份；只允许补充确实缺失的字段')
            chars[aid] = {**old, **copy.deepcopy(asset)}
        else:
            chars[aid] = copy.deepcopy(asset)
        names[asset['name']] = aid
    result['ledger']['characters'] = list(chars.values())
    return result


def _authorized(row, record, operation, cache):
    ref = row['authorization']
    key = (ref['path'], ref['sha256'])
    if key not in cache:
        raw = Path(ref['path']).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == ref['sha256'], '操作授权文件已变化')
        value = json.loads(raw.decode('utf-8-sig'))
        require(value.get('status') == 'user_explicitly_approved', '未取得明确制作授权')
        cache[key] = value
    auth = cache[key]
    require(operation in auth.get('authorized_operations', []), '授权不含本操作类型')
    require(any(row['scene_id'] in item.get('scene_ids', []) and record['id'] in item.get('source_records', [])
                and item['span'][0] <= record['start'] < record['end'] <= item['span'][1]
                for item in auth.get('items', [])), '制作对应越出明确授权的来源范围')


def validate(state, units, rows):
    from storyboard_text import scene_asset_ids, _speaker
    rows = normalize(rows)
    if not rows:
        return []
    require(state.get('storyboard_authoring_policy') == 'shot-by-shot-v1', '仅用于逐镜文字分镜')
    scoped = context(state, rows)
    chars = {a['id']: a for a in scoped['ledger']['characters']}
    scenes = {s['id']: s for s in state['ledger']['scenes']}
    records = {r['id']: r for r in state['records']}
    selected = {rid for unit in units for rid in unit['source_records']}
    cache, voices, actors = {}, set(), {}
    for row in rows:
        sid, aid = row['scene_id'], row['asset_id']
        require(sid in scenes and row['source_asset_id'] in scene_asset_ids(scenes[sid])['characters']
                and aid in chars, '源人物须为本场合法CHAR，制作参考须真实存在')
        members = chars[aid].get('visual_design', {}).get('group_members', [])
        if 'sample_id' in row:
            sample = next((m for m in members if m.get('sample_id') == row['sample_id']), None)
            require(sample is not None, '所选样本不在实际图片设计内')
            signature = tuple(sample.get(k) for k in ('age', 'body', 'face', 'hair'))
        else:
            require(not members or row['kind'] in ('visual_group', 'appearance'), '个体制作须明确选择群板中的单个样本')
            signature = ('asset', aid)
        if row['kind'] != 'visual_group':
            require(row['actor_key'] not in actors or actors[row['actor_key']] == signature,
                    '同一稳定执行者对应了不同身体；服装版本须共用相同身体样本')
            actors[row['actor_key']] = signature
        for item in row['evidence']:
            record = records.get(item['record_id'])
            require(record is not None and record['id'] in selected and record['scene'] == sid
                    and item['quote'] in record['text'], '证据越出本次实际选区或记录')
            _authorized(row, record, 'production_cast', cache)
            if 'asset' in row:
                _authorized(row, record, 'supplemental_character_assets', cache)
            if (row['kind'] == 'performer' and record['kind'] == 'dialogue'
                    and record.get('source_kind') != 'non_character'
                    and _speaker(chars[row['source_asset_id']], sid) == record['speaker']):
                require(record['id'] not in voices, '同条原话对应了多个制作发言者')
                voices.add(record['id'])
    return rows


def project(rows, unit):
    result = []
    for row in rows:
        local = set(row['source_records']) & set(unit['source_records'])
        if row['scene_id'] in unit['scene_ids'] and local:
            result.append({**copy.deepcopy(row), 'source_records': sorted(local),
                           'evidence': [copy.deepcopy(e) for e in row['evidence'] if e['record_id'] in local]})
    return result


def apply(state, units, rows):
    rows = validate(state, units, rows)
    for unit in units:
        local = project(rows, unit)
        if local:
            unit['production_cast'] = local


def from_units(units):
    merged = {}
    for unit in units:
        for row in normalize(unit.get('production_cast', [])):
            require(project([row], unit) == [row], '单元制作对应越出消费记录')
            key = tuple(row[k] for k in ('scene_id', 'actor_key', 'asset_id', 'source_asset_id', 'kind'))
            old = merged.get(key)
            if old:
                require({k: v for k, v in old.items() if k not in ('source_records', 'evidence')}
                        == {k: v for k, v in row.items() if k not in ('source_records', 'evidence')}, '冻结制作对应不一致')
                evidence = {e['record_id']: e['quote'] for e in old['evidence']}
                for e in row['evidence']:
                    require(e['record_id'] not in evidence or evidence[e['record_id']] == e['quote'], '共享证据不一致')
                    evidence[e['record_id']] = e['quote']
                row['source_records'] = sorted(evidence)
                row['evidence'] = [{'record_id': rid, 'quote': evidence[rid]} for rid in sorted(evidence)]
            merged[key] = row
    return normalize(list(merged.values()))


def voice_actor(unit, record, characters):
    from storyboard_text import _speaker
    rows = [r for r in unit.get('production_cast', []) if r['kind'] == 'performer'
            and record['id'] in r['source_records']
            and _speaker(characters[r['source_asset_id']], record['scene']) == record.get('speaker')]
    require(len(rows) <= 1, '单条原话制作声源不唯一')
    return rows[0] if rows else None


def require_current_authorizations(rows):
    for path, digest in {(r['authorization']['path'], r['authorization']['sha256']) for r in rows}:
        require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest,
                '制作授权在执行期间变化；须重新匹配候选与实审')
