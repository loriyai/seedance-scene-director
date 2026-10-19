"""Compile explicitly planned action effects; never infer effects from prose.

Only action-state-v1 opts in. All functions return independent values and leave
legacy units untouched. Stable action/effect IDs belong to author decisions.
"""
import copy
import hashlib
import json
import math

import continuity


POLICY = 'action-state-v1'


def require(condition, path, message):
    if not condition:
        raise ValueError(f'{path}: {message}')


def line(value, path):
    require(isinstance(value, str) and bool(value.strip()) and len(value.splitlines()) == 1,
            path, '须为非空单行字符串')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def enabled(unit):
    if 'action_state_plan' not in unit:
        return False
    plan = unit['action_state_plan']
    require(isinstance(plan, dict) and plan.get('policy') == POLICY,
            'action_state_plan.policy', '未知状态效果协议；不能回退旧模式绕过校验')
    return True


def fields(patch):
    return {key for key in patch if key != 'facts'} | {
        'facts.' + key for key in patch.get('facts', {})}


def field_name(value, path):
    line(value, path)
    require(value in ('present', 'asset_id') or value.startswith('facts.') and len(value) > 6,
            path, '字段须为present/asset_id/facts.<事实键>')


def field_value(snapshot, entity, field):
    if field.startswith('facts.'):
        facts = snapshot[entity]['facts']
        return {'exists': field[6:] in facts, 'value': facts.get(field[6:])}
    return {'exists': True, 'value': snapshot[entity][field]}


def summary(snapshot):
    """A deterministic backend summary, never prose for new dramatic action."""
    pieces = []
    for entity in sorted(snapshot):
        item = snapshot[entity]
        info = ['在场' if item['present'] else '不在场', '形象=' + (item['asset_id'] or '无图片资产')]
        info.extend(key + '=' + item['facts'][key] for key in sorted(item['facts']))
        pieces.append(entity + '：' + '；'.join(info))
    return ' | '.join(pieces) if pieces else '本范围未登记需跟踪实体'


def _identity(value, seen, path):
    line(value, path)
    require(value not in seen, path, '稳定标识重复')
    seen.add(value)


def _targets(raw, definitions, path):
    require(isinstance(raw, list), path, '须声明关键动作实际影响的状态字段，无影响明确填写[]')
    result = set()
    for index, item in enumerate(raw):
        loc = f'{path}[{index}]'
        require(isinstance(item, dict) and set(item) == {'entity', 'fields'}, loc, '须为entity/fields')
        entity = item['entity']
        require(isinstance(entity, str) and entity in definitions, loc + '.entity', '实体未登记')
        require(isinstance(item['fields'], list) and bool(item['fields']), loc + '.fields', '须明确列出影响字段')
        for field in item['fields']:
            field_name(field, loc + '.fields')
            require((entity, field) not in result, loc + '.fields', '影响字段重复')
            result.add((entity, field))
    return result


def _actions(unit, definitions, state):
    actions = unit.get('action_plan')
    require(isinstance(actions, list), 'action_plan', '新协议沿用唯一action_plan，关键动作须明确登记')
    duration = unit.get('duration')
    require(type(duration) is int and 4 <= duration <= 30, 'duration', '须先确定4至30秒整数时长')
    indexed, ids, targets = {}, set(), {}
    for index, action in enumerate(actions):
        loc = f'action_plan[{index}]'
        require(isinstance(action, dict), loc, '须为关键动作对象')
        action_id = action.get('action_id')
        _identity(action_id, ids, loc + '.action_id')
        for key in ('record', 'quote'):
            line(action.get(key), loc + '.' + key)
        require(all(number(action.get(key)) for key in ('start', 'end')) and
                0 <= action['start'] < action['end'] <= duration,
                loc + '.start/end', '关键动作须有合法窗口；结束不代表持续状态自动释放')
        milestones = action.get('milestones', {})
        require(isinstance(milestones, dict), loc + '.milestones', '须为名称到明确时间的对象')
        for key, at in milestones.items():
            line(key, loc + '.milestones.<name>')
            require(key not in ('start', 'end') and number(at) and action['start'] < at < action['end'],
                    loc + '.milestones.' + key, '里程点须在动作内部，不能覆盖start/end')
        targets[action_id] = _targets(action.get('state_targets'), definitions, loc + '.state_targets')
        if state is not None:
            continuity.evidence(state, unit, [{'record': action['record'], 'quote': action['quote']}], loc)
        indexed[action_id] = action
    return indexed, targets


def _action_time(actions, action_id, at, path):
    require(isinstance(action_id, str) and action_id in actions, path + '.action_id', '关键动作不存在')
    action = actions[action_id]
    line(at, path + '.at')
    require(at in ('start', 'end') or at in action.get('milestones', {}), path + '.at', '动作锚点不存在')
    return action[at] if at in ('start', 'end') else action['milestones'][at]


def _endpoint(endpoint, actions, audio_plans, path):
    require(isinstance(endpoint, dict), path, '须为动作或声音片段端点')
    if 'action_id' in endpoint:
        require(set(endpoint) == {'action_id', 'at'}, path, '动作端点须为action_id/at')
        return _action_time(actions, endpoint['action_id'], endpoint['at'], path)
    require(set(endpoint) == {'record', 'part', 'edge'}, path, '声音片段端点须为record/part/edge')
    record, part, edge = endpoint['record'], endpoint['part'], endpoint['edge']
    require(isinstance(record, str) and record in audio_plans and type(part) is int and
            1 <= part <= len(audio_plans[record]) and edge in ('start', 'end'), path, '声音片段端点不存在')
    value = audio_plans[record][part - 1].get(edge)
    require(number(value), path, '声音片段端点须为有限数值')
    return value


def _audio_plans(unit):
    plans = unit.get('audio_plan', [])
    require(isinstance(plans, list), 'audio_plan', '须为数组')
    events = unit.get('audio', [])
    require(isinstance(events, list) and all(isinstance(event, dict) for event in events),
            'audio', '须为明确的声音事件数组')
    event_ids = {event.get('record') for event in events if isinstance(event.get('record'), str)}
    result = {}
    for index, plan in enumerate(plans):
        loc = f'audio_plan[{index}]'
        require(isinstance(plan, dict) and isinstance(plan.get('record'), str)
                and plan['record'] not in result and isinstance(plan.get('parts'), list), loc, '声音计划记录非法或重复')
        require(plan['record'] in event_ids, loc + '.record', '声音片段计划须指向实际声音事件')
        require(all(isinstance(part, dict) for part in plan['parts']), loc + '.parts', '声音片段须为对象')
        result[plan['record']] = plan['parts']
    return result


def _orders(unit, actions, plan):
    plans = _audio_plans(unit)
    orders = plan.get('order', [])
    require(isinstance(orders, list), 'action_state_plan.order', '须为显式时序约束数组')
    for index, order in enumerate(orders):
        loc = f'action_state_plan.order[{index}]'
        require(isinstance(order, dict) and set(order) == {'before', 'after'}, loc, '须明确before和after端点')
        left = _endpoint(order['before'], actions, plans, loc + '.before')
        right = _endpoint(order['after'], actions, plans, loc + '.after')
        require(left <= right, loc, f'端点时序矛盾：{left}不得晚于{right}')
    # Existing action.before/after retain their full-event meaning.
    events = {event.get('record'): event for event in unit.get('audio', []) if isinstance(event, dict)}
    for action_id, action in actions.items():
        for relation in ('before', 'after'):
            if relation not in action:
                continue
            record = action[relation]
            require(isinstance(record, str) and record in events, f'action_plan.{action_id}.{relation}', '整句声音记录不存在')
            edge = 'start' if relation == 'before' else 'end'
            at = events[record].get(edge)
            require(number(at), f'action_plan.{action_id}.{relation}', '整句声音时窗非法')
            require(action['end'] <= at if relation == 'before' else action['start'] >= at,
                    f'action_plan.{action_id}.{relation}', '与整句声音先后约束冲突')


def _patch(current, definitions, effect, path):
    entity, patch = effect['entity'], effect['set']
    before = copy.deepcopy(current[entity])
    for key in ('present', 'asset_id'):
        if key in patch:
            current[entity][key] = patch[key]
    for key, value in patch.get('facts', {}).items():
        if value is None:
            require(key in current[entity]['facts'], path + '.set.facts.' + key, '不能删除不存在的事实')
            del current[entity]['facts'][key]
        else:
            current[entity]['facts'][key] = value
    continuity.snapshot(current, definitions, path)
    for field in fields(patch):
        require(field_value({entity: before}, entity, field) != field_value(current, entity, field),
                path + '.set.' + field,
                '效果字段未改变状态；不能混入其他变化冒充本字段更新，关系保留须使用精确绑定的retain判断')
    return before, copy.deepcopy(current[entity])


def _relations(plan, definitions):
    relations = plan.get('relations', [])
    require(isinstance(relations, list), 'action_state_plan.relations', '须为关系依赖数组')
    seen, result, tracked = set(), [], set()
    for index, relation in enumerate(relations):
        loc = f'action_state_plan.relations[{index}]'
        require(isinstance(relation, dict) and set(relation) == {'relation_id', 'entity', 'fact', 'depends_on'},
                loc, '须为relation_id/entity/fact/depends_on')
        _identity(relation['relation_id'], seen, loc + '.relation_id')
        require(isinstance(relation['entity'], str) and relation['entity'] in definitions, loc + '.entity', '实体未登记')
        line(relation['fact'], loc + '.fact')
        key = (relation['entity'], relation['fact'])
        require(key not in tracked, loc, '同一关系事实只能登记一次依赖集合')
        tracked.add(key)
        dependencies = _targets(relation['depends_on'], definitions, loc + '.depends_on')
        require(bool(dependencies), loc + '.depends_on', '关系须明确实际参照的状态字段')
        require((relation['entity'], 'facts.' + relation['fact']) not in dependencies,
                loc + '.depends_on', '关系不能依赖自身同一事实')
        result.append((relation, dependencies))
    return result


def _replay(unit, effects, definitions, plan):
    current = copy.deepcopy(unit['continuity']['initial'])
    continuity.snapshot(current, definitions, 'action_state_plan.initial')
    relations = _relations(plan, definitions)
    checks = plan.get('relation_checks', [])
    require(isinstance(checks, list), 'action_state_plan.relation_checks', '须为精确变化的显式保留判断数组')
    for index, check in enumerate(checks):
        loc = f'action_state_plan.relation_checks[{index}]'
        require(isinstance(check, dict) and set(check) == {
            'relation_id', 'trigger_effect_ids', 'trigger_sha256', 'decision', 'basis'}, loc,
            '须绑定relation_id/trigger_effect_ids/trigger_sha256/decision/basis')
        for key in ('relation_id', 'trigger_sha256', 'basis'):
            line(check[key], loc + '.' + key)
        require(check['decision'] == 'retain', loc + '.decision', '只允许对这次精确变化明确保留；更新或删除写effect')
        ids = check['trigger_effect_ids']
        require(isinstance(ids, list) and bool(ids) and all(isinstance(x, str) for x in ids)
                and len(ids) == len(set(ids)), loc + '.trigger_effect_ids', '须列出本次触发效果的唯一ID')
    used, groups = set(), {}
    for item in effects:
        groups.setdefault(item['time'], []).append(item)
    for at, group in groups.items():
        before_group = copy.deepcopy(current)
        changed, written, details = set(), set(), []
        for item in group:
            effect = item['effect']
            entity = effect['entity']
            keys = {(entity, key) for key in fields(effect['set'])}
            require(not written.intersection(keys), 'action_state_plan.effects',
                    f'同一时点{at}重复写同一事实；须明确唯一结果而非靠列表顺序猜测')
            written.update(keys)
            before, after = _patch(current, definitions, effect, 'action_state_plan.effects.' + effect['effect_id'])
            actual = {key for key in keys if field_value(before_group, *key) != field_value(current, *key)}
            changed.update(actual)
            details.append({'effect_id': effect['effect_id'], 'time': at,
                            'action_id': effect['action_id'], 'at': effect['at'],
                            'entity': entity, 'set': copy.deepcopy(effect['set']),
                            'evidence': copy.deepcopy(item['change']['evidence']),
                            'before': before, 'after': after, 'changed': actual})
        for relation, dependencies in relations:
            entity, fact = relation['entity'], relation['fact']
            if fact not in before_group[entity]['facts'] or not dependencies.intersection(changed):
                continue
            if (entity, 'facts.' + fact) in changed:
                continue
            triggers = [{k: v for k, v in item.items() if k != 'changed'}
                        for item in details if dependencies.intersection(item['changed'])]
            ids = sorted(item['effect_id'] for item in triggers)
            binding = digest({'relation': relation, 'triggers': triggers,
                              'span': unit.get('span'), 'main_scene': unit.get('main_scene'),
                              'relation_value': before_group[entity]['facts'][fact]})
            matching = [index for index, check in enumerate(checks)
                        if check['relation_id'] == relation['relation_id'] and
                        sorted(check['trigger_effect_ids']) == ids and check['trigger_sha256'] == binding]
            require(len(matching) == 1, 'action_state_plan.relations.' + relation['relation_id'],
                    '参照状态变化后关系须同刻更新/删除，或明确本次仍成立；待核trigger_effect_ids=' +
                    json.dumps(ids, ensure_ascii=False) + ', trigger_sha256=' + binding)
            used.add(matching[0])
    require(used == set(range(len(checks))), 'action_state_plan.relation_checks', '包含重复、过期或未被本次精确变化使用的retain判断')
    return current


def validate_plan(state, unit):
    """Validate explicit source/time/effect commitments, even before shots exist.

    state=None checks internal structure only. Production entry points supply
    bound state and still require the author's ordinary semantic review.
    """
    if not enabled(unit):
        return None
    if state is not None:
        require(isinstance(state, dict) and isinstance(state.get('records'), list)
                and isinstance(state.get('ledger'), dict) and isinstance(state['ledger'].get('scenes'), list),
                'source_state', '源证据核对须提供完整绑定记录及场景台账')
    plan = unit['action_state_plan']
    require(set(plan) <= {'policy', 'effects', 'order', 'relations', 'relation_checks'} and 'effects' in plan,
            'action_state_plan', '须有唯一effects，其他字段只允许order/relations/relation_checks')
    contract = unit.get('continuity')
    require(isinstance(contract, dict) and isinstance(contract.get('entities'), dict)
            and isinstance(contract.get('initial'), dict), 'continuity', '状态效果计划须有实体定义及进入态')
    definitions = contract['entities']
    for entity, definition in definitions.items():
        line(entity, 'continuity.entities.<id>')
        require(isinstance(definition, dict) and isinstance(definition.get('asset_ids'), list)
                and all(isinstance(x, str) for x in definition['asset_ids']),
                'continuity.entities.' + entity, '实体须有明确asset_ids')
    actions, targets = _actions(unit, definitions, state)
    _orders(unit, actions, plan)
    raw_effects = plan['effects']
    require(isinstance(raw_effects, list), 'action_state_plan.effects', '须为效果数组，无效果明确填写[]')
    ids, effects, fulfilled = set(), [], {key: set() for key in actions}
    for index, effect in enumerate(raw_effects):
        loc = f'action_state_plan.effects[{index}]'
        require(isinstance(effect, dict) and set(effect) <= {
            'effect_id', 'action_id', 'at', 'entity', 'set', 'cut_side'} and
                {'effect_id', 'action_id', 'at', 'entity', 'set'} <= set(effect), loc,
                '须为effect_id/action_id/at/entity/set，可选cut_side；证据从动作记录派生')
        _identity(effect['effect_id'], ids, loc + '.effect_id')
        at = _action_time(actions, effect['action_id'], effect['at'], loc)
        require(isinstance(effect['entity'], str) and effect['entity'] in definitions,
                loc + '.entity', '实体未登记')
        patch = effect['set']
        require(isinstance(patch, dict) and bool(patch) and set(patch) <= {'present', 'asset_id', 'facts'},
                loc + '.set', '只允许非空的present/asset_id/facts效果')
        if 'facts' in patch:
            require(isinstance(patch['facts'], dict) and bool(patch['facts']), loc + '.set.facts', '须为非空事实变化对象')
            for key, value in patch['facts'].items():
                line(key, loc + '.set.facts.<key>')
                if value is not None:
                    line(value, loc + '.set.facts.' + key)
        if 'cut_side' in effect:
            require(effect['at'] not in ('start', 'end') and effect['cut_side'] in ('before', 'after'),
                    loc + '.cut_side', '仅内部里程点可明确切点前/后归属')
        actual_targets = {(effect['entity'], key) for key in fields(patch)}
        require(actual_targets <= targets[effect['action_id']], loc + '.set', '效果字段未列入该动作明确的state_targets')
        fulfilled[effect['action_id']].update(actual_targets)
        action = actions[effect['action_id']]
        effects.append({'time': at, 'effect': copy.deepcopy(effect), 'change': {
            'entity': effect['entity'], 'set': copy.deepcopy(patch),
            'evidence': [{'record': action['record'], 'quote': action['quote']}]}})
    for action_id in actions:
        require(targets[action_id] == fulfilled[action_id], 'action_plan.' + action_id + '.state_targets',
                '存在声明了影响但没有对应效果的字段：' + str(sorted(targets[action_id] - fulfilled[action_id])))
    # Python's stable ordering only orders independent fields at the same time;
    # duplicate writes at that time are rejected by replay, not silently resolved.
    effects.sort(key=lambda item: item['time'])
    final = _replay(unit, effects, definitions, plan)
    return {'policy': POLICY, 'effects': effects, 'final': final,
            'start_state': summary(contract['initial']), 'end_state': summary(final)}


def _assignment(unit, planned):
    shots = unit.get('shots')
    require(isinstance(shots, list) and bool(shots), 'shots', '编译效果须有明确镜窗')
    cursor = 0
    for index, shot in enumerate(shots):
        require(isinstance(shot, dict) and type(shot.get('start')) is int and type(shot.get('end')) is int
                and shot['start'] == cursor and 1 <= shot['end'] - cursor <= 6,
                f'shots[{index}].start/end', '须先确定连续的1至6秒整数镜窗')
        cursor = shot['end']
    require(cursor == unit['duration'], 'duration', '镜窗须完整覆盖单元时长')
    output = [[] for _ in shots]
    for item in planned['effects']:
        effect, at = item['effect'], item['time']
        side = 'after' if effect['at'] == 'start' else 'before' if effect['at'] == 'end' else effect.get('cut_side')
        boundary = any(shot['start'] == at for shot in shots[1:])
        require(not boundary or side is not None, 'action_state_plan.effects.' + effect['effect_id'] + '.cut_side',
                '里程点恰在镜头切点，须明确before或after，不猜动作相位归属')
        if at == 0:
            selected = 0
        elif at == unit['duration']:
            selected = len(shots) - 1
        else:
            candidates = [index for index, shot in enumerate(shots)
                          if (shot['start'] <= at < shot['end'] if side == 'after'
                              else shot['start'] < at <= shot['end'])]
            require(len(candidates) == 1, 'action_state_plan.effects.' + effect['effect_id'], '效果时点没有唯一对应镜窗')
            selected = candidates[0]
        output[selected].append(copy.deepcopy(item['change']))
    return output


def compile_unit(unit, state=None):
    """Compile on a copy; reject conflicting pre-existing derived values."""
    result = copy.deepcopy(unit)
    planned = validate_plan(state, result)
    if planned is None:
        return result
    assigned = _assignment(result, planned)
    for index, (shot, changes) in enumerate(zip(result['shots'], assigned)):
        block = shot.get('continuity')
        require(isinstance(block, dict), f'shots[{index}].continuity', '作者仍须明确可见性，编译器不补取景判断')
        if 'changes' in block:
            require(block['changes'] == changes, f'shots[{index}].continuity.changes', '与权威action_state_plan矛盾；请精确修订后重编译')
        else:
            block['changes'] = changes
    for field in ('start_state', 'end_state'):
        if field in result:
            require(result[field] == planned[field], field, '与权威状态摘要矛盾；请精确修订后重编译')
        else:
            result[field] = planned[field]
    if 'final' in result['continuity']:
        require(result['continuity']['final'] == planned['final'], 'continuity.final', '与权威效果重放矛盾')
    else:
        result['continuity']['final'] = planned['final']
    return result


def clear_derived(unit):
    """On a copy, remove only new-policy derived state, never source decisions."""
    result = copy.deepcopy(unit)
    if not enabled(result):
        return result
    result.pop('start_state', None)
    result.pop('end_state', None)
    if isinstance(result.get('continuity'), dict):
        result['continuity'].pop('final', None)
    for shot in result.get('shots', []):
        if isinstance(shot, dict) and isinstance(shot.get('continuity'), dict):
            shot['continuity'].pop('changes', None)
    return result


def recompile_unit(unit, state=None):
    """For revision callers that already verified old values and version/hash."""
    return compile_unit(clear_derived(unit), state)


def validate_compiled(state, unit):
    """Production guard: every derived field must exist and match the plan."""
    if not enabled(unit):
        return None
    for field in ('start_state', 'end_state'):
        require(field in unit, field, '新状态协议须先编译，再进入现有校验')
    require(isinstance(unit.get('continuity'), dict) and 'final' in unit['continuity'],
            'continuity.final', '新状态协议缺少编译末态')
    for index, shot in enumerate(unit.get('shots', [])):
        require(isinstance(shot, dict) and isinstance(shot.get('continuity'), dict)
                and 'changes' in shot['continuity'], f'shots[{index}].continuity.changes', '新状态协议缺少编译变化')
    compiled = compile_unit(unit, state)
    return {'policy': POLICY, 'final': copy.deepcopy(compiled['continuity']['final'])}
