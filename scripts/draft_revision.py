"""Revise an exact saved draft by stable identities and expected old values.

This local authoring tool never edits project state or creates review approval.
Use inspect to obtain a bound edit, then apply to an exclusive new output.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import sys
import uuid

import authoring_gates
import storyboard_assembly as assembly

POLICY = 'exact-draft-revision-v1'
PROTECTED = {'id', 'draft_unit_id', 'draft_policy', 'review_id', 'shots',
             'audio_preflight', 'staging_review', 'identity_decisions'}


def require(condition, message):
    if not condition:
        raise ValueError('精确修订：' + message)


def tokens(pointer):
    require(isinstance(pointer, str) and pointer.startswith('/') and pointer != '/',
            'path须为非空JSON Pointer')
    result = []
    for raw in pointer[1:].split('/'):
        require(not re.search(r'~(?![01])', raw), 'JSON Pointer转义无效')
        result.append(raw.replace('~1', '/').replace('~0', '~'))
    return result


def member(parent, key):
    if isinstance(parent, list):
        require(bool(re.fullmatch(r'0|[1-9][0-9]*', key)), '数组下标须为规范整数')
        key = int(key)
        require(key < len(parent), '数组下标不存在；本接口不按索引新增或删除镜头')
        return True, key
    require(isinstance(parent, dict), 'path的父节点须为对象或数组')
    return key in parent, key


def locate(document, unit_id, shot_id=None):
    units = document.get('units')
    require(isinstance(units, list) and units, '须为含units的草稿请求')
    ids = [u.get('draft_unit_id') for u in units]
    require(all(authoring_gates.stable_identifier(i) for i in ids) and len(set(ids)) == len(ids),
            '单元身份缺失或重复；先首次严格装配，不能按位置猜测')
    matches = [u for u in units if u['draft_unit_id'] == unit_id]
    require(len(matches) == 1, '目标draft_unit_id不存在')
    unit = matches[0]
    require(unit.get('draft_policy') == assembly.DRAFT_POLICY, '只修订strict-draft-v1草稿')
    if shot_id is None:
        return unit, unit
    shots = unit.get('shots', [])
    ids = [s.get('review_id') for s in shots]
    require(all(authoring_gates.stable_identifier(i) for i in ids) and len(set(ids)) == len(ids),
            '镜头身份缺失或重复')
    matches = [s for s in shots if s['review_id'] == shot_id]
    require(len(matches) == 1, '目标review_id不存在')
    return unit, matches[0]


def resolve(target, path):
    parts = tokens(path)
    parent = target
    for part in parts[:-1]:
        exists, key = member(parent, part)
        require(exists, 'path的父节点不存在')
        parent = parent[key]
    exists, key = member(parent, parts[-1])
    return parent, key, exists, parts


def inspect_edit(document, unit_id, path, shot_id=None):
    _, target = locate(document, unit_id, shot_id)
    parent, key, exists, _ = resolve(target, path)
    edit = {'unit_id': unit_id, 'path': path, 'op': 'replace' if exists else 'add'}
    if shot_id is not None:
        edit['shot_id'] = shot_id
    if exists:
        edit['old'] = copy.deepcopy(parent[key])
    edit['value'] = copy.deepcopy(parent[key]) if exists else None
    return {'policy': POLICY, 'input_sha256': authoring_gates.digest(document), 'edits': [edit]}


def apply_revision(document, plan):
    """Return (new request, change summary); any failure leaves both inputs intact."""
    require(isinstance(document, dict) and isinstance(plan, dict), '输入及修订计划须为对象')
    require(set(plan) <= {'policy', 'input_sha256', 'edits', 'output_request_id'}, '修订计划含未知字段')
    require(plan.get('policy') == POLICY, '未知修订协议')
    require(plan.get('input_sha256') == authoring_gates.digest(document), '输入版本已变化，重新inspect')
    edits = plan.get('edits')
    require(isinstance(edits, list) and edits, 'edits须为非空列表')
    result = copy.deepcopy(document)
    affected, paths, seen = {}, {}, set()
    for index, edit in enumerate(edits):
        require(isinstance(edit, dict), f'edits[{index}]须为对象')
        op = edit.get('op')
        expected = {'unit_id', 'path', 'op'} | ({'shot_id'} if 'shot_id' in edit else set())
        expected |= {'old'} if op == 'remove' else {'value'} if op == 'add' else {'old', 'value'}
        require(op in {'add', 'remove', 'replace'} and set(edit) == expected,
                f'edits[{index}]操作或字段不合法')
        unit, target = locate(result, edit['unit_id'], edit.get('shot_id'))
        parent, key, exists, parts = resolve(target, edit['path'])
        require(parts[0] not in PROTECTED, '身份、镜头列表及审阅判断不能通过字段修订接口修改')
        require(not (parts[0] == 'continuity' and len(parts) == 1), '须精确指定continuity内部字段')
        if unit.get('action_state_plan') is not None:
            require(parts[0] not in {'start_state', 'end_state'} and
                    parts[:2] not in [['continuity', 'changes'], ['continuity', 'final']],
                    '动作单源草稿须修改权威动作或效果，不能手改派生状态')
        require(parts[0] != 'dialogue_parts', '须修改audio_plan，不能手改派生声音片段')
        identity = (edit['unit_id'], edit.get('shot_id'), tuple(parts))
        for prior in seen:
            require(identity[:2] != prior[:2] or not
                    (identity[2][:len(prior[2])] == prior[2] or prior[2][:len(identity[2])] == identity[2]),
                    '同一目标的修订路径重复或互相覆盖')
        seen.add(identity)
        if op == 'add':
            require(not exists and isinstance(parent, dict), 'add仅能创建不存在的对象字段')
        else:
            require(exists and authoring_gates.digest(parent[key]) == authoring_gates.digest(edit['old']),
                    f'edits[{index}]旧值不匹配，拒绝套用过期修改')
        if op == 'remove':
            require(isinstance(parent, dict), '本接口不删除数组项；明确替换权威计划数组')
            del parent[key]
        else:
            require(op == 'add' or authoring_gates.digest(parent[key]) != authoring_gates.digest(edit['value']),
                    '修订没有实际变化')
            parent[key] = copy.deepcopy(edit['value'])
        affected[edit['unit_id']] = unit
        paths.setdefault(edit['unit_id'], []).append({'shot_id': edit.get('shot_id'), 'path': edit['path']})

    summary = []
    for uid, unit in affected.items():
        old_unit, _ = locate(document, uid)
        timing_changed = any(p['shot_id'] is not None and tokens(p['path'])[0] in
                             {'start', 'end', 'duration'} for p in paths[uid])
        audio_changed = any(p['shot_id'] is None and tokens(p['path'])[0] in
                            {'audio', 'audio_plan'} for p in paths[uid])
        if timing_changed:
            unit.pop('duration', None)
        if timing_changed or audio_changed:
            for shot in unit['shots']:
                shot.pop('dialogue_parts', None)
        state_changed = timing_changed or any(p['shot_id'] is None and
                            (tokens(p['path'])[0] in {'action_plan', 'action_state_plan'} or
                             tokens(p['path'])[:2] == ['continuity', 'initial']) for p in paths[uid])
        if state_changed and unit.get('action_state_plan') is not None:
            import state_effects
            unit = state_effects.clear_derived(unit)
        elif any(tokens(p['path'])[:2] in [['continuity', 'initial'], ['continuity', 'changes']]
                 for p in paths[uid]):
            unit['continuity'].pop('final', None)
        removed = []
        if 'staging_review' in unit:
            del unit['staging_review']
            removed.append('staging_review')
        assembled = assembly.assemble_unit(unit)
        if authoring_gates.audio_payload({}, old_unit) != authoring_gates.audio_payload({}, assembled):
            if 'audio_preflight' in assembled:
                del assembled['audio_preflight']
                removed.append('audio_preflight')
        result['units'][next(i for i, u in enumerate(result['units']) if u['draft_unit_id'] == uid)] = assembled
        summary.append({'unit_id': uid, 'edits': paths[uid], 'removed_reviews': removed})
    new_id = plan.get('output_request_id', 'revision-' + str(uuid.uuid4()))
    require(authoring_gates.stable_identifier(new_id) and new_id != document.get('request_id'),
            '新稿须使用不同的非空request_id，避免命中旧提交回执')
    result['request_id'] = new_id
    return result, {'policy': POLICY, 'input_sha256': authoring_gates.digest(document),
                    'output_sha256': authoring_gates.digest(result), 'changes': summary,
                    'status': 'revised_unreviewed'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('--input', required=True, type=Path)
    inspect.add_argument('--unit', required=True)
    inspect.add_argument('--shot')
    inspect.add_argument('--path', required=True)
    apply = sub.add_parser('apply')
    apply.add_argument('--input', required=True, type=Path)
    apply.add_argument('--plan', required=True, type=Path)
    apply.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.input.read_text(encoding='utf-8-sig'))
        if args.command == 'inspect':
            summary = inspect_edit(document, args.unit, args.path, args.shot)
        else:
            require(args.output.resolve() not in {args.input.resolve(), args.plan.resolve()}, '输出不能覆盖输入或修订计划')
            require(not args.output.exists(), '输出已存在；保留旧稿并选择新路径')
            plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
            result, summary = apply_revision(document, plan)
            with args.output.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            summary['output'] = str(args.output.resolve())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
