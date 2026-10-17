"""Prepare writer evidence; compact views are displays, never approvals.

Historical full output and staging_review digests remain unchanged. Compact
views reference request/state snapshots and rebuild original evidence on expand.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
import director
from authoring_gates import digest, staging_pending, staging_material

COMPACT_POLICY = 'staging-compact-v1'
SNAPSHOT_POLICY = 'staging-snapshot-v1'


def prepare(state, request):
    """Use the original evidence producer without changing its hash/schema."""
    return {'status': 'pending', 'units': [
        {'unit': u.get('id', i + 1), 'staging_review': staging_pending(state, u),
         'actual': staging_material(state, u)}
        for i, u in enumerate(request['units'])]}


def _delta(previous, current, path=()):
    """Lossless dictionary changes; arrays/scalars are replaced as values."""
    result = {'set': [], 'remove': []}
    if isinstance(previous, dict) and isinstance(current, dict):
        for key in previous.keys() - current.keys():
            result['remove'].append(list(path + (key,)))
        for key, value in current.items():
            if key not in previous:
                result['set'].append({'path': list(path + (key,)), 'value': copy.deepcopy(value)})
            elif previous[key] != value:
                part = _delta(previous[key], value, path + (key,))
                for name in result:
                    result[name].extend(part[name])
    elif previous != current:
        result['set'].append({'path': list(path), 'value': copy.deepcopy(current)})
    for name in result:
        result[name].sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return result


def entering_states(unit_view):
    """Expand displayed entering-state deltas without reading source files."""
    current = copy.deepcopy(unit_view['actual']['entering_initial'])
    result = []
    for target in unit_view['actual']['targets']:
        for path in target['entering_delta']['remove']:
            parent = current
            for key in path[:-1]:
                parent = parent[key]
            del parent[path[-1]]
        for change in target['entering_delta']['set']:
            path = change['path']
            if not path:
                current = copy.deepcopy(change['value'])
                continue
            parent = current
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = copy.deepcopy(change['value'])
        result.append(copy.deepcopy(current))
    return result


def _project_units(request, full):
    units = []
    for index, (unit, entry) in enumerate(zip(request['units'], full['units']), 1):
        material = entry['actual']
        fields = material['timing_and_state']
        contexts, targets = {}, []
        initial = copy.deepcopy(material['targets'][0]['entering_state']) if material['targets'] else {}
        previous = initial
        for target in material['targets']:
            shot = target['shot']
            projected = {k: copy.deepcopy(v) for k, v in target.items()
                         if k not in ('shot', 'entering_state', 'source', 'unit_position',
                                      'dependency_declaration')}
            # Preserve whatever version-specific context the target producer
            # supplies, including whole-unit or per-shot dependency projections.
            for name in ('source', 'unit_position', 'dependency_declaration'):
                if name in target:
                    key = digest(target[name])
                    contexts[key] = copy.deepcopy(target[name])
                    projected[name + '_ref'] = key
            projected['request_pointer'] = f'/units/{index - 1}/shots/{target["shot_index"] - 1}'
            projected['entering_delta'] = _delta(previous, target['entering_state'])
            previous = target['entering_state']
            # Photography prose is already in the exact actual render. Keep
            # visibility, source-backed changes and timing readable as evidence.
            projected['shot'] = {k: copy.deepcopy(shot[k]) for k in
                                 ('review_id', 'start', 'end', 'scene_id', 'label', 'framing',
                                  'angle', 'subjects', 'focus', 'purpose', 'dialogue_parts', 'continuity')
                                 if k in shot}
            targets.append(projected)
        continuity = fields.get('continuity', {})
        continuity_view = {k: copy.deepcopy(v) for k, v in continuity.items()
                           if k not in ('initial', 'final')}
        continuity_view['initial_request_pointer'] = f'/units/{index - 1}/continuity/initial'
        continuity_view['final_sha256'] = digest(continuity.get('final'))
        units.append({'unit': entry['unit'], 'unit_index': index,
                      'request_pointer': f'/units/{index - 1}',
                      'staging_review': copy.deepcopy(entry['staging_review']),
                      'actual_sha256': digest(material),
                      'actual': {'rendered': material['rendered'],
                                 'timing_and_state': {k: copy.deepcopy(v) for k, v in fields.items()
                                                      if k not in ('shots', 'continuity')},
                                 'continuity': continuity_view,
                                 'entering_initial': initial, 'targets': targets,
                                 'contexts': contexts}})
    return units


def _snapshot(output, kind, raw):
    key = hashlib.sha256(raw).hexdigest()
    path = output.parent / '.staging-inputs' / f'{kind}-{key}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as handle:
            handle.write(raw)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError('内容寻址快照已存在但内容不符；不得覆盖')
    return {'policy': SNAPSHOT_POLICY, 'path': path.relative_to(output.parent).as_posix(),
            'sha256': key, 'bytes': len(raw)}


def _view_digest(view):
    return digest({k: v for k, v in view.items() if k != 'projection_sha256'})


def compact(state, request, request_raw, output):
    """Capture original request bytes once; no semantic verdict is generated."""
    full = prepare(state, request)
    state_raw = json.dumps(state, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':')).encode('utf-8')
    view = {'policy': COMPACT_POLICY, 'status': 'pending', 'evidence_use': 'display_only_not_approval',
            'sources': {'request': _snapshot(output, 'request', request_raw),
                        'state': _snapshot(output, 'state', state_raw)},
            'units': _project_units(request, full)}
    view['projection_sha256'] = _view_digest(view)
    return view


def _source(view_path, ref, kind):
    if ref.get('policy') != SNAPSHOT_POLICY:
        raise ValueError('不支持的staging快照策略')
    base = view_path.parent.resolve()
    relative = Path(ref.get('path', ''))
    path = (base / relative).resolve()
    key = ref.get('sha256')
    if (relative.is_absolute() or base not in path.parents or
            path.name != f'{kind}-{key}.json'):
        raise ValueError('staging快照引用须位于紧凑视图目录内且匹配内容寻址名称')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != key or len(raw) != ref.get('bytes'):
        raise ValueError(f'staging {kind}快照摘要不符，来源已变化')
    return path


def expand(view_path, selected=None, shot=None):
    """Verify and rebuild original evidence before selecting a display subset."""
    view = director.read_json(view_path)
    if (view.get('policy') != COMPACT_POLICY or view.get('status') != 'pending' or
            view.get('evidence_use') != 'display_only_not_approval'):
        raise ValueError('只支持pending的staging紧凑展示，不能作为通过证据')
    if view.get('projection_sha256') != _view_digest(view):
        raise ValueError('staging紧凑视图摘要不符，展示内容已变化')
    request_path = _source(view_path, view['sources']['request'], 'request')
    state_path = _source(view_path, view['sources']['state'], 'state')
    state = director.read_json(state_path)
    request = director.resolved_request(state, director.read_json(request_path))
    full = prepare(state, request)
    # Reprojection detects changed producers and a forged view whose display
    # hash was recomputed. Fail closed, never silently refresh an old binding.
    if _project_units(request, full) != view.get('units'):
        raise ValueError('staging重建材料与紧凑视图不符；来源或当前规则已变化')
    selected = list(range(1, len(full['units']) + 1)) if selected is None else selected
    if (not selected or any(type(i) is not int or not 1 <= i <= len(full['units']) for i in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError('unit须为不重复的请求内1起算序号')
    if shot is not None and (len(selected) != 1 or type(shot) is not int):
        raise ValueError('shot展开须只选择一个unit')
    result = {'status': 'pending', 'units': [copy.deepcopy(full['units'][i - 1]) for i in selected]}
    if shot is not None:
        entry = result['units'][0]
        targets = entry['actual']['targets']
        if not 1 <= shot <= len(targets):
            raise ValueError('shot须为所选unit内1起算序号')
        entry['actual'] = {'rendered': entry['actual']['rendered'], 'target': targets[shot - 1]}
        result.update(policy='staging-shot-display-v1', evidence_use='display_only_not_approval',
                      unit_index=selected[0], shot_index=shot)
    return result


def _windows(items, start, end):
    """Keep original values; overlap is navigation, never an event verdict."""
    result = []
    for index, item in enumerate(items):
        a, b = item.get('start'), item.get('end')
        numeric = all(type(v) in (int, float) for v in (a, b, start, end))
        if not numeric or a <= end and b >= start:
            result.append({'index': index, 'relation': 'unresolved_window' if not numeric else
                           'overlap' if a < end and b > start else 'touching_boundary',
                           'value': copy.deepcopy(item)})
    return result


def _panel(entry, target):
    fields, shot = entry['actual']['timing_and_state'], target['shot']
    start, end = shot.get('start'), shot.get('end')
    # All render fields are copied from the canonical target. No prose is parsed
    # for times, movement, visibility or approval-like keywords.
    return {'shot': copy.deepcopy(shot), 'entering_state': copy.deepcopy(target['entering_state']),
            'action_windows': _windows(fields.get('action_plan', []), start, end),
            'audio_windows': _windows(fields.get('audio', []), start, end),
            'audio_plan_parts': [dict(record=item.get('record'), parts=parts)
                                 for item in fields.get('audio_plan', [])
                                 if (parts := _windows(item.get('parts', []), start, end))]}


def _unit_key(entry):
    fields = entry['actual']['timing_and_state']
    identifier = fields.get('id')
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    targets = entry['actual']['targets']
    return digest({'id': identifier, 'span': fields.get('span'), 'scene_ids': fields.get('scene_ids'),
                   'source': targets[0].get('source') if targets else []})


def _unique_index(values, key):
    index, ambiguous = {}, set()
    for value in values:
        identity = key(value)
        if identity is None:
            continue
        if identity in index:
            ambiguous.add(identity)
        index[identity] = value
    return {k: v for k, v in index.items() if k not in ambiguous}


def _shot_key(target):
    key = target['shot'].get('review_id')
    return key if isinstance(key, str) and key.strip() else None


def related_view(view_path, selected=None, shot=None, previous=None):
    """Same-round display from verified snapshots; canonical evidence is intact.

    Old and new fields align only by stable identities and unchanged source.
    Missing/duplicate identities and unmatched units are explicit; we do not
    guess by row number or imply a dependency closure from this display.
    """
    current = expand(view_path)
    indices = list(range(1, len(current['units']) + 1)) if selected is None else selected
    if (not indices or any(type(i) is not int or not 1 <= i <= len(current['units']) for i in indices)
            or len(set(indices)) != len(indices)):
        raise ValueError('unit须为不重复的请求内1起算序号')
    if shot is not None and (len(indices) != 1 or type(shot) is not int or
            not 1 <= shot <= len(current['units'][indices[0] - 1]['actual']['targets'])):
        raise ValueError('shot须为唯一所选unit内1起算序号')
    old = expand(previous) if previous is not None else None
    old_units = _unique_index(old['units'], _unit_key) if old is not None else {}
    current_units = _unique_index(current['units'], _unit_key)
    result = {'policy': 'staging-related-display-v1', 'status': 'pending',
              'evidence_use': 'display_only_not_approval', 'dependency_scope': 'not_computed',
              'current_view': {'path': str(view_path.resolve()), 'sha256': hashlib.sha256(view_path.read_bytes()).hexdigest()},
              'units': []}
    if previous is not None:
        result['previous_view'] = {'path': str(previous.resolve()), 'sha256': hashlib.sha256(previous.read_bytes()).hexdigest()}
        result['unmatched_previous_units'] = [e['unit'] for e in old['units']
                                             if _unit_key(e) not in current_units or _unit_key(e) not in old_units]
    for i in indices:
        entry = current['units'][i - 1]
        key = _unit_key(entry)
        prior = old_units.get(key) if key in current_units else None
        targets = entry['actual']['targets']
        current_shots = _unique_index(targets, _shot_key)
        old_shots = _unique_index(prior['actual']['targets'], _shot_key) if prior else {}
        row = {'unit': entry['unit'], 'unit_index': i,
               'actual_sha256': digest(entry['actual']),
               'unit_context': {k: copy.deepcopy(entry['actual']['timing_and_state'].get(k))
                                for k in ('global', 'span', 'continuity', 'start_state', 'end_state')},
               'shots': []}
        if old is not None:
            row['comparison'] = 'aligned' if prior else 'unmatched_or_ambiguous_unit'
            row['removed_or_ambiguous_previous_shots'] = [t['shot'].get('review_id')
                for t in prior['actual']['targets'] if _shot_key(t) not in current_shots or _shot_key(t) not in old_shots] if prior else []
        for target in targets if shot is None else [targets[shot - 1]]:
            identity = _shot_key(target)
            panel = _panel(entry, target)
            item = {'shot_index': target['shot_index'], 'review_id': identity, 'status': 'pending',
                    'current': panel, 'request_pointer': f'/units/{i - 1}/shots/{target["shot_index"] - 1}'}
            if old is not None:
                prior_target = old_shots.get(identity) if identity in current_shots else None
                if prior_target:
                    old_panel = _panel(prior, prior_target)
                    item.update(comparison='aligned', previous=old_panel, difference=_delta(old_panel, panel))
                else:
                    item['comparison'] = 'unmatched_or_ambiguous_shot'
            row['shots'].append(item)
        result['units'].append(row)
    return result


def _output(path):
    root = Path(__file__).resolve().parent.parent
    output = path.resolve()
    if output == root or root in output.parents:
        raise ValueError('工作记录不能写入Skill包内')
    if output.exists():
        raise FileExistsError('staging输出已存在；请使用新文件名')
    output.parent.mkdir(parents=True, exist_ok=True)
    return output

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--project', type=Path)
    source.add_argument('--state', type=Path)
    source.add_argument('--expand', type=Path, help='验证紧凑视图及来源后展开原材料')
    p.add_argument('--request', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--format', choices=('full', 'compact'), default='full')
    p.add_argument('--unit', type=int, action='append', help='展开请求内1起算单元，可重复指定')
    p.add_argument('--shot', type=int, help='展开所选单元中的镜头及真实进入态')
    p.add_argument('--related', action='store_true', help='同轮并排显示镜窗、摄影、可见性及实际动作/声音/进入态')
    p.add_argument('--previous', type=Path, help='与已冻结紧凑视图按稳定ID对比；不计算正式复核闭包')
    a = p.parse_args()
    if a.expand:
        if a.request or a.format != 'full' or (a.shot is not None and not a.unit):
            p.error('--expand不接受--request/--format compact；--shot须配--unit')
    elif not a.request or a.unit or a.shot is not None or a.related or a.previous:
        p.error('准备材料须提供--request；--unit/--shot/--related/--previous仅用于--expand')
    output = _output(a.output)
    if a.expand:
        result = (related_view(a.expand, a.unit, a.shot, a.previous) if a.related or a.previous
                  else expand(a.expand, a.unit, a.shot))
    else:
        state = director.load(a.project) if a.project else director.read_json(a.state)
        request_raw = a.request.read_bytes()
        # Resolve the exact captured bytes, avoiding a second live file read.
        request = director.resolved_request(state, json.loads(request_raw.decode('utf-8-sig')))
        result = (compact(state, request, request_raw, output) if a.format == 'compact'
                  else prepare(state, request))
    with output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({'status': 'pending', 'output': str(output),
                      'format': result.get('policy', 'full')}, ensure_ascii=False))
if __name__=='__main__': main()
