"""Contextual display and explicit appearance-time checks for new text units.

Stable author tokens remain the only bindings. No prose/name search rewrites
creative text, and an evidence match never certifies the author's semantics.
"""
from decimal import Decimal
from pathlib import Path
import hashlib
import re
import sys

POLICY = 'identity-reference-v1'
FIELD = 'appearance_states'
REF = re.compile(r'@\{((?:CHAR|SET|PROP)\d{4,})\}')


def require(ok, message):
    if not ok:
        raise ValueError('身份与引用：' + message)


def source_modules():
    path = str(Path(__file__).resolve().parents[1] / 'modules' / 'screenplay-standardizer' / 'scripts')
    if path not in sys.path:
        sys.path.insert(0, path)
    from cast_contract import split_cast_entry
    from character_variants import character_identity
    return split_cast_entry, character_identity


class Names:
    def __init__(self, state, assets, unit=None):
        self.state, self.assets = state, assets
        self.split_entry, self.identity_of = source_modules()
        self.characters = {a['id']: a for a in state['ledger'].get('characters', [])}
        self.characters.update({k: v for k, v in assets.items() if k.startswith('CHAR')})
        self.unit = unit or {}
        self.locations = {a['id']: a for a in state['ledger'].get('locations', [])}

    def base(self, aid):
        seen = set()
        while self.characters[aid].get('appearance_variant', {}).get('base_asset_id'):
            require(aid not in seen, '人物形象基础关系循环')
            seen.add(aid)
            aid = self.characters[aid]['appearance_variant']['base_asset_id']
            require(aid in self.characters, '人物形象基础不存在')
        return aid

    def identity(self, aid):
        return self.identity_of(self.characters, self.base(aid))

    def character(self, aid, scene_id=None):
        item = self.characters[self.base(aid)]
        # Anonymous supplemental slots have an explicitly reviewed display
        # name, not a missing source-entry inferred from a production suffix.
        slot_names = {row['name']
                      for field in ('source_background_slots', 'source_participant_slots')
                      for row in self.state.get('_' + field, [])
                      if row['asset_id'] == aid and row['scene_id'] == scene_id
                      and item.get('visual_mode') == 'prompt_only'
                      and row['name'] == item['name']}
        # These are reviewed source fields, never a truncated production name.
        if slot_names:
            require(len(slot_names) == 1, aid + '静默槽显示名不唯一')
            value = next(iter(slot_names))
        elif item.get('scene_clothing'):
            value = item['scene_clothing']['base_name']
        elif item.get('source_speaker'):
            value = item['source_speaker']
        elif item.get('source_entry'):
            value = self.split_entry(item['source_entry'])[0]
        else:
            # Legacy ledgers may use name as their source entry. Resolve it
            # against an actual source label before treating it as a name.
            candidates = {label for r in self.state['records']
                          if r.get('speaker') and r.get('source_kind') != 'non_character'
                          and (scene_id is None or r['scene'] == scene_id)
                          for label in r['speaker'].split('、')
                          if item['name'] == label or item['name'].startswith(label + '-')}
            for scene in self.state['ledger'].get('scenes', []):
                if scene_id is not None and scene['id'] != scene_id:
                    continue
                for entry in scene.get('characters', []):
                    if isinstance(entry, str) and entry == item['name']:
                        candidates.add(self.split_entry(entry)[0])
            # Load only an already-bound standard script when a legacy silent
            # cast has no explicit source_entry. The full entry must match.
            source_file = self.state.get('files', {}).get('script', {})
            script = source_file.get('path')
            if script and Path(script).is_file():
                raw = Path(script).read_bytes()
                require(hashlib.sha256(raw).hexdigest() == source_file.get('sha256'),
                        '绑定标准剧本已变化，不能用未核验人物行恢复名称')
                source_scenes = {scene['number']: scene for scene in self.state['ledger'].get('scenes', [])
                                 if 'number' in scene}
                own_scenes = {scene['id'] for scene in source_scenes.values()
                              if self.base(aid) in scene.get('character_ids', [])
                              or any(t.get('base_asset_id') == self.base(aid)
                                     for t in scene.get('character_appearances', []))}
                scope = {scene_id} if scene_id in own_scenes else own_scenes
                current_scene = None
                for line in raw.decode('utf-8-sig').splitlines():
                    heading = re.match(r'场景(\d+)(?=-(?:日|夜)-(?:内|外)-|：(?:日|夜)·(?:内|外)·)', line)
                    if heading:
                        current_scene = source_scenes.get(int(heading.group(1)), {}).get('id')
                    elif line.startswith('人物：') and current_scene in scope:
                        for entry in line[3:].rstrip('；').split('；'):
                            if entry == item['name']:
                                candidates.add(self.split_entry(entry)[0])
            require(len(candidates) == 1, aid + '缺少唯一已审源人物名称；补齐source_entry/source_speaker，不能截断制作名猜测')
            value = next(iter(candidates))
        require(isinstance(value, str) and value.strip() and not any(c in value for c in '@{}\r\n'), aid + '人物显示名无效')
        return value

    def scene(self, aid):
        item = self.assets[aid]
        loc = self.locations.get(item.get('location_id'))
        if loc:
            return loc['name']
        # Old data can retain an exact source location without LOC metadata.
        candidates = {s.get('location') for s in self.state['ledger'].get('scenes', [])
                      if s.get('scene_asset_id') == aid and s.get('location')}
        if len(candidates) == 1:
            return next(iter(candidates))
        # An unqualified SET name is the only available old source label; do
        # not remove night/time/structure suffixes from it.
        return item['name']


def parse_declaration(value):
    require(isinstance(value, dict) and {'scene_id', 'base_asset_id', 'events'} <= set(value) and set(value) <= {'scene_id', 'base_asset_id', 'events', 'shots'},
            '形象状态须含scene_id/base_asset_id/events')
    require(isinstance(value['events'], list), '形象状态events须为数组')
    require(value['events'] or value.get('shots'), '无事件或叙事层作用域时不填写形象状态')
    if 'shots' in value:
        require(isinstance(value['shots'], list) and value['shots'] and all(type(n) is int and n > 0 for n in value['shots'])
                and len(set(value['shots'])) == len(value['shots']), '形象作用域shots须为不重复正整数镜号')
    for row in value['events']:
        require(isinstance(row, dict) and {'event_id', 'record_id', 'quote'} <= set(row)
                and set(row) <= {'event_id', 'record_id', 'quote', 'at'}, '形象事件字段不合法')
        require(all(isinstance(row[k], str) and row[k].strip() for k in ('event_id', 'record_id', 'quote')), '形象事件缺少原文定位')
        if 'at' in row:
            require(type(row['at']) in (int, float) and Decimal(str(row['at'])).is_finite(), '形象完成时间须为有限秒数')
    return value


def _text(value):
    return ''.join(value.split())


def _source_view(value, names):
    # Standardization may expand a declared costume source slot. Compare only
    # this reviewed source-name mapping; never infer names from production names.
    aliases = {}
    for aid, item in names.characters.items():
        entry = item.get('source_entry')
        if entry:
            aliases[entry] = names.character(aid)
    if aliases:
        pattern = re.compile('|'.join(re.escape(k) for k in sorted(aliases, key=len, reverse=True)))
        value = pattern.sub(lambda m: aliases[m.group(0)], value)
    return ''.join(c for c in value if c.isalnum())


def _event_positions(event, sid, records, names):
    evidence = _source_view(event.get('evidence', ''), names)
    if not evidence:
        return []
    rows = [r for r in records.values() if r['scene'] == sid]
    text, ends = '', []
    for record in rows:
        text += _source_view(record['text'], names)
        ends.append(len(text))
    matches = []
    for found in re.finditer(re.escape(evidence), text):
        index = next(i for i, end in enumerate(ends) if found.end() <= end)
        if rows[index]['kind'] == 'action':
            matches.append(rows[index])
    return matches


def timelines(state, unit, names):
    """Resolve only reviewed tracks; author supplies completion times, not us."""
    records = {r['id']: r for r in state['records']}
    order = {rid: i for i, rid in enumerate(records)}
    scenes = {s['id']: s for s in state['ledger']['scenes']}
    a, b = order[unit['source_records'][0]], order[unit['source_records'][-1]]
    declarations = {}
    for row in unit.get(FIELD, []):
        parse_declaration(row)
        key = row['scene_id'], row['base_asset_id']
        require(key not in declarations, '同一场人物的形象状态重复')
        declarations[key] = row
    result = {}
    used_declarations = set()
    for sid in unit['scene_ids']:
        for track in scenes[sid].get('character_appearances', []):
            base = track.get('base_asset_id', track['initial_asset_id'])
            family = {track['initial_asset_id'], base}
            for event in track.get('transitions', []):
                family.update((event['from_asset_id'], event['to_asset_id']))
            if not family.intersection(unit['character_ids']):
                continue
            key = sid, base
            declared = declarations.get(key, {'events': []})
            if key in declarations:
                used_declarations.add(key)
            bound = {}
            for row in declared['events']:
                require(row['event_id'] not in bound, '形象事件重复')
                bound[row['event_id']] = row
            events = track.get('transitions', [])
            require(set(bound) <= {e.get('event_id') for e in events}, '形象状态引用未知已审事件')
            active, changes, previous_index, previous_time = track['initial_asset_id'], [], -1, Decimal('-1')
            for event in events:
                eid = event.get('event_id')
                row = bound.get(eid)
                if row:
                    record = records.get(row['record_id'])
                    require(record is not None and record['scene'] == sid and record['kind'] == 'action'
                            and row['quote'] in record['text'], '形象事件须精确绑定同场标准动作原文')
                    matched = _event_positions(event, sid, records, names)
                    require(record['id'] in {r['id'] for r in matched},
                            '形象事件须绑定完整已审事件evidence结束处，不能借同场无关动作切换')
                    index = order[record['id']]
                else:
                    matches = _event_positions(event, sid, records, names)
                    require(len(matches) == 1, f'{sid}/{base}/{eid}无法唯一对应标准动作；用形象状态绑定record_id和quote')
                    record, index = matches[0], order[matches[0]['id']]
                require(index >= previous_index, '形象事件的标准来源顺序与已审链不一致')
                previous_index = index
                if index < a:
                    require(row is None or 'at' not in row, '单元之前的形象事件不能填写本单元完成时间')
                    active = event['to_asset_id']
                elif index <= b:
                    require(row is not None and 'at' in row, f'{sid}/{base}/{eid}须由作者填写本单元实际完成秒数at')
                    at = Decimal(str(row['at']))
                    require(0 <= at <= unit['duration'] and at > previous_time, '形象完成时间须在单元内按变化链严格递增')
                    previous_time = at
                    changes.append({'at': at, 'from': event['from_asset_id'], 'to': event['to_asset_id'],
                                    'event_id': eid, 'record_id': record['id']})
                else:
                    require(row is None or 'at' not in row, '未来形象事件不能填写本单元完成时间')
            initial = active
            for change in changes:
                require(change['from'] == active, '形象转换前态与当前状态不一致')
                active = change['to']
            scope = declared.get('shots', list(range(1, len(unit['shots']) + 1)))
            require(all(n <= len(unit['shots']) for n in scope), '形象作用域镜号超出单元')
            result[key] = {'initial': initial, 'changes': changes, 'family': family, 'final': active,
                           'shots': scope, 'explicit_scope': 'shots' in declared}
    require(set(declarations) == used_declarations, '形象状态不属于当前单元实际使用的已审人物变化链')
    return result


def active_at(timeline, at):
    value = timeline['initial']
    for event in timeline['changes']:
        if event['at'] <= Decimal(str(at)):
            value = event['to']
    return value


def check(state, unit, names):
    require(unit.get('reference_display_policy') in (None, POLICY), '未知引用显示策略')
    if unit.get('reference_display_policy') != POLICY:
        return {}
    # Check display names before accepting the new format, including silent cast.
    for aid in unit['character_ids']:
        names.character(aid, unit['main_scene'])
    tracks = timelines(state, unit, names)
    for key, track in tracks.items():
        family = track['family']
        colliding = [other for other, value in tracks.items() if other != key and family & value['family']]
        require(not colliding or track['explicit_scope'], '并行叙事层须在形象状态shots明确实际镜号，不推定全局形象')
        if 1 in track['shots']:
            opening = {m.group(1) for m in REF.finditer(unit['global'])} & family
            opening_allowed = {active_at(value, 0) for other, value in tracks.items()
                               if 1 in value['shots'] and family & value['family']}
            require(opening <= opening_allowed, '人物站位引用了尚未生效或已结束的形象')
        for number, shot in enumerate(unit['shots'], 1):
            if number in track['shots']:
                allowed = {active_at(track, shot['start'])}
                for event in track['changes']:
                    if Decimal(str(shot['start'])) <= event['at'] <= Decimal(str(shot['end'])):
                        allowed.update((event['from'], event['to']))
                for other, value in tracks.items():
                    if other != key and number in value['shots'] and family & value['family']:
                        allowed.add(active_at(value, shot['start']))
                        allowed.update(e['to'] for e in value['changes'] if shot['start'] <= e['at'] <= shot['end'])
                used = {m.group(1) for m in REF.finditer(shot['text'])} & family
                require(used <= allowed, '镜头引用了尚未生效或已结束的形象')
            for part in shot['dialogue_parts']:
                source = next((r for r in state['records'] if r['id'] == part['record']), None)
                if source is None:
                    source = next(({'scene': e['scene_id']} for e in unit.get('source_text_voices', [])
                                   if e['event_id'] == part['record']), None)
                if source is not None and source['scene'] != key[0]:
                    continue
                ids = part.get('character_ids', [part.get('character_id')])
                chosen = set(ids) & family
                require(chosen <= {active_at(track, part['start'])}, '声音须引用片段开始时已生效的形象；不能凭同场成员资格提前换装')
    return tracks


def roster(unit, names, asset, tracks):
    groups = []
    remaining = set(unit['character_ids'])
    emitted_tracks = set()
    for aid in unit['character_ids']:
        if aid not in remaining:
            continue
        applicable = [(key, value) for key, value in tracks.items() if aid in value['family'] and key not in emitted_tracks]
        if applicable:
            key, track = applicable[0]
            emitted_tracks.add(key)
            start = active_at(track, 0)
            parts = [names.character(start, unit['main_scene']) + asset(start) + '（起始）']
            for event in track['changes']:
                if event['at'] > 0:
                    target = event['to']
                    parts.append(names.character(target, unit['main_scene']) + asset(target)
                                 + f'（{event["at"]}秒起）')
            require({start, *(e['to'] for e in track['changes'])} <= set(unit['character_ids']),
                    '人物名单须覆盖当前单元实际生效的形象')
            groups.append(' → '.join(parts) + (f'（镜头{"、".join(map(str, track["shots"]))}）' if track['explicit_scope'] else ''))
            remaining -= track['family']
        else:
            same = [cid for cid in unit['character_ids'] if cid in remaining and names.identity(cid) == names.identity(aid)]
            if len(same) == 1:
                groups.append(names.character(aid, unit['main_scene']) + asset(aid))
            else:
                parts = []
                for cid in same:
                    uses = [str(n) for n, shot in enumerate(unit['shots'], 1)
                            if cid in {m.group(1) for m in REF.finditer(shot['text'])}
                            or any(cid in part.get('character_ids', [part.get('character_id')]) for part in shot['dialogue_parts'])]
                    require(uses, '同身份多形象须在实际镜头中明确使用，不能只在名单并列')
                    parts.append(names.character(cid, unit['main_scene']) + asset(cid) + '（镜头' + '、'.join(uses) + '）')
                groups.append('〔' + ' ／ '.join(parts) + '〕')
            remaining -= set(same)
    for key, track in tracks.items():
        if key in emitted_tracks:
            continue
        start = active_at(track, 0)
        parts = [names.character(start, key[0]) + asset(start) + '（起始）']
        for event in track['changes']:
            if event['at'] > 0:
                parts.append(names.character(event['to'], key[0]) + asset(event['to']) + f'（{event["at"]}秒起）')
        groups.append(' → '.join(parts) + f'（镜头{"、".join(map(str, track["shots"]))}）')
    return '，'.join(groups) or '无'
