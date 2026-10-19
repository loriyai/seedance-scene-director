"""Expose shot review targets without claiming to infer filmable space.

Diagnostics prompt review, never impose camera movement or rewrite shots.
"""
import copy
import re


CUE_POLICY = 'shot-review-cues-v2'
LEGACY_CUE_POLICY = 'shot-review-cues-v1'


def diagnostics(unit):
    issues = []
    key = unit.get('id', '当前单元')
    for number, shot in enumerate(unit.get('shots', []), 1):
        fields = []
        purpose, label, camera = (shot.get(k, '') for k in ('purpose', 'label', 'camera'))
        if label and purpose in (label + '的原有信息通过该取景完整可读；' + camera,
                                 label + '原有信息完整可读；' + camera):
            fields.append('purpose')
        if any(text in shot.get('depth_of_field', '') for text in (
                '与本镜动作接触点清楚，前后方已有结构略柔而可辨',
                '主焦点所在中层清晰，参与者位置可辨，后层保留空间轮廓',
                '主体所在焦层清晰，保留构图中既有空间层次')):
            fields.append('depth_of_field')
        if '保持本镜已限定的身体局部可见' in shot.get('composition', ''):
            fields.append('composition')
        for entity, view in shot.get('continuity', {}).get('visibility', {}).items():
            if re.fullmatch(r'实体在既定位置，本镜取景集中于.+，未覆盖该位置', view.get('basis', '')):
                fields.append('visibility.' + entity)
        if fields:
            issues.append({'kind': '逐镜模板化依据需复核', 'unit': key, 'shot': number,
                           'fields': fields,
                           'review': '核实实际对焦对象、接触部位与取景边界；不得用标签或字段齐全证明空间合理。'
                                     '相同景深、固定镜头或画外人物均可合理保留，须写具体依据。'})
    return issues


def appearance_reference_warnings(state, unit):
    """Review visual references without treating a voice ID as a second person."""
    ledger = state.get('ledger', {})
    if ledger.get('character_variant_policy') != 'source-masked-v1':
        return []
    variants = {item['id']: item['appearance_variant']['base_asset_id']
                for item in ledger.get('characters', []) if item.get('appearance_variant')}
    tracked = set(variants.values())
    current = copy.deepcopy(unit.get('continuity', {}).get('initial', {}))
    events = {event['record']: event for event in unit.get('audio', [])}
    warnings = []
    for number, shot in enumerate(unit.get('shots', []), 1):
        reached = list(copy.deepcopy(current).values())
        for change in shot.get('continuity', {}).get('changes', []):
            item = current.get(change['entity'])
            if item is not None:
                for key in ('asset_id', 'present'):
                    if key in change['set']:
                        item[key] = change['set'][key]
                reached.append(copy.deepcopy(item))
        active = {}
        for item in reached:
            aid = item.get('asset_id')
            base = variants.get(aid, aid)
            if item.get('present') and base in tracked:
                active.setdefault(base, set()).add(aid)
        references = {}
        for field in ('text', 'camera', 'composition', 'depth_of_field', 'lighting',
                      'label', 'transition', 'connector', 'sound_foreground', 'sound_environment'):
            for aid in re.findall(r'@\{(CHAR\d+)\}', shot.get(field, '')):
                references.setdefault(aid, set()).add(field)
        for part in shot.get('dialogue_parts', []):
            event = events.get(part.get('record'), {})
            aid = event.get('character_id')
            if aid:
                references.setdefault(aid, set()).add('audio.' + event.get('voice', ''))
        for aid, fields in references.items():
            base = variants.get(aid, aid)
            if base in active and aid not in active[base]:
                warnings.append({'kind': '佩戴形象引用需复核', 'unit': unit.get('id', '当前单元'),
                                 'shot': number, 'reference': aid, 'fields': sorted(fields),
                                 'reached_appearances': sorted(active[base]),
                                 'review': '引用形象与本镜实际到达的造型不同；核对是否为旧形象误引用。'
                                           '声源身份不等于画内造型，跨镜同句及OS须逐镜复核，不能拆改原台词或擅自替换声源。'})
    return warnings


def targets(state, request, *, cue_policy=CUE_POLICY):
    """Snapshot requested shots and their source/state, without issuing verdicts."""
    from continuity import portion
    if cue_policy not in (LEGACY_CUE_POLICY, CUE_POLICY):
        raise ValueError('未知逐镜审阅提示策略')
    result = []
    for unit_index, unit in enumerate(request.get('units', []), 1):
        current_cues = (cue_policy == CUE_POLICY
                        and unit.get('workflow_policy') == 'scene-review-b-v2')
        a, b = unit['span']
        source = [{'record': r['id'], 'scene': r['scene'], 'kind': r['kind'],
                   'text': portion(r, [a, b])}
                  for r in state.get('records', []) if r['start'] < b and r['end'] > a]
        current = copy.deepcopy(unit.get('continuity', {}).get('initial', {}))
        for shot_index, shot in enumerate(unit.get('shots', []), 1):
            cues = []
            camera = shot.get('camera', '')
            legal_shot = (current_cues
                          and type(shot.get('start')) is int
                          and type(shot.get('end')) is int
                          and 1 <= shot['end'] - shot['start'] <= 6)
            if legal_shot:
                cues.append('1至6秒摄影选择：按观看重点、动作路径、信息揭示及剪辑承接比较固定与运动；'
                            '不因短默认固定、不因长强制运动；缺少选择须待补，不得由程序回填。'
                            '结合相邻镜头复核摄影是否服务当前观看任务，动静分布仅用于定位复核，不设比例。')
            if '固定' in camera:
                if (cue_policy == LEGACY_CUE_POLICY
                        and unit.get('review_dependencies', {}).get('policy') in ('shot-dependencies-v2', 'shot-dependencies-v3')
                        and type(shot.get('start')) is int and type(shot.get('end')) is int
                        and 4 <= shot['end'] - shot['start'] <= 6):
                    cues.append('4至6秒镜头含固定描述：核对是否仅为实际运镜后的短暂稳定落点；整镜固定不合格，须有服务观看重点的相机位移或转向，移焦与主体动作不算。')
                elif current_cues:
                    cues.append('固定镜头：核对稳定观察的具体作用、信息读清所需时长和切点；'
                                '有依据时保留，不因时长自动接受或否决，也不为增加运动数量而修改。')
                else:
                    cues.append('固定镜头：核对原有动作或保持的用途、信息读清所需时长和切点；不要求补运镜。')
            if any(text in camera for text in ('移', '推', '拉', '跟', '升', '降', '摇', '环绕')):
                if current_cues:
                    cues.append('运动镜头：核对方向、起止取景、途中可见性、遮挡和落点，以及当前时长内的可读性；'
                                '同步核对构图、景深、光照和切点。词语命中仅提示复核，不证明实际相机运动。')
                else:
                    cues.append('运动镜头：核对起止取景、途中可见性、遮挡和落点；不只检查起始构图。')
            views = shot.get('continuity', {}).get('visibility', {})
            if any(v.get('mode') in ('offscreen', 'occluded') for v in views.values()):
                cues.append('画外/遮挡：用实际位置和取景边界核对；换焦点不构成离场。')
            changes = shot.get('continuity', {}).get('changes', [])
            result.append({'key': f'{unit_index}:{shot_index}', 'unit_index': unit_index,
                           'shot_index': shot_index, 'source': source,
                           'unit_position': unit.get('global', ''),
                           'entering_state': copy.deepcopy(current),
                           'shot': copy.deepcopy(shot), 'review_cues': cues,
                           'status': 'pending', 'basis': ''})
            if 'review_dependencies' in unit:
                from shot_dependencies import target_declaration
                result[-1]['dependency_declaration'] = target_declaration(
                    unit, shot.get('review_id'), request.get('units', []))
            for change in changes:
                item = current.get(change['entity'])
                if item is None:
                    continue
                for field, value in change['set'].items():
                    if field == 'facts':
                        for name, fact in value.items():
                            if fact is None:
                                item['facts'].pop(name, None)
                            else:
                                item['facts'][name] = fact
                    else:
                        item[field] = copy.deepcopy(value)
            if unit.get('review_dependencies', {}).get('policy') == 'shot-dependencies-v3':
                # New v3 targets own their actual exit and only the summary
                # belonging to this endpoint. Old targets remain unchanged.
                result[-1]['exiting_state'] = copy.deepcopy(current)
                if shot_index == 1:
                    result[-1]['entry_summary'] = copy.deepcopy(unit.get('start_state'))
                if shot_index == len(unit.get('shots', [])):
                    result[-1]['exit_summary'] = {
                        'text': copy.deepcopy(unit.get('end_state')),
                        'final': copy.deepcopy(unit.get('continuity', {}).get('final'))}
    return result


def verify_targets(state, request, submitted, require):
    expected = targets(state, request)
    require(isinstance(submitted, list) and len(submitted) == len(expected),
            '逐镜复核须覆盖本次全部镜头')
    for actual, target in zip(submitted, expected):
        require(isinstance(actual, dict) and set(actual) == set(target), '逐镜复核字段不完整')
        require(all(actual[key] == target[key] for key in set(target) - {'status', 'basis'}),
                '逐镜复核快照与最终镜头或来源不一致')
        require(actual.get('status') == 'passed' and isinstance(actual.get('basis'), str)
                and bool(actual['basis'].strip()), '逐镜复核尚未完成')
        require(actual['basis'].strip() not in ('通过', '已检查', '同前', '同上',
                                               '字段完整', '构图合理', '符合要求'),
                '逐镜复核须说明实际对象、空间或观看目的，不能只填通过')
