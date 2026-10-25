"""Pure, receipt-backed numbering for confirmed screenplay partitions.

Partition letters name source assignments; regeneration rounds never replace
old units. This module allocates no files and consumes no shared batch letters.
"""
import copy

POLICY = 'partition-round-v1'
IDENTITY_FIELDS = ('naming_policy', 'segment_plan_id', 'segment_plan_version',
                   'segment_label', 'segment_round', 'segment_index', 'segment_scene_index')


def active_plan(project, state):
    from script_partition import load_plan
    plan = load_plan(project, state)
    if plan is not None and plan.get('status') != 'confirmed':
        raise ValueError('拆分方案尚未确认；请先完成拆分差距选择')
    return plan


def assignments(state, plan, units):
    """Return one identity per unit without modifying plan, state, or units."""
    if plan is None:
        return None
    if plan.get('policy') != 'partition-v1' or plan.get('status') != 'confirmed':
        raise ValueError('仅已确认的 partition-v1 方案可分配分段编号')
    groups = {}
    for index, unit in enumerate(units):
        start, end = unit['span']
        matches = [row for row in plan['segments']
                   if row['span'][0] <= start < end <= row['span'][1]]
        if len(matches) != 1:
            raise ValueError('分镜单元不能跨分段边界；请在已确认的分段切点结束单元')
        segment = matches[0]
        groups.setdefault(segment['label'], {'segment': segment, 'rows': []})['rows'].append((index, unit))
    result = [None] * len(units)
    scenes = {scene['id']: scene['number'] for scene in state['ledger']['scenes']}
    for label, group in groups.items():
        segment, rows = group['segment'], group['rows']
        previous = [u for u in state.get('units', []) if u.get('segment_label') == label]
        if any(u.get('segment_plan_id') != plan['plan_id'] for u in previous):
            raise ValueError('已有分段编号属于其他拆分方案；不能静默迁移或重复分配名称')
        # Pre-partition histories retain their identities and require a separate
        # project; adding a plan cannot silently reinterpret their batch letters.
        if any(u.get('batch_label') == label and u.get('naming_policy') != POLICY
               for u in state.get('units', [])):
            raise ValueError('已有未分段批次占用该字母；不能将历史批次静默改为分段编号')
        full = [rows[0][1]['span'][0], rows[-1][1]['span'][1]] == segment['span']
        current_round = max((u['segment_round'] for u in previous), default=0)
        if full and previous:
            current_round += 1
        current = [u for u in previous if u['segment_round'] == current_round]
        next_index = max((u['segment_index'] for u in current), default=0) + 1
        scene_counts = {}
        for unit in current:
            main = unit['main_scene']
            scene_counts[main] = max(scene_counts.get(main, 0), unit['segment_scene_index'])
        suffix = f'-{current_round}' if current_round else ''
        for index, unit in rows:
            main = unit['main_scene']
            scene_counts[main] = scene_counts.get(main, 0) + 1
            result[index] = {
                'naming_policy': POLICY, 'segment_plan_id': plan['plan_id'],
                'segment_plan_version': plan['version'], 'segment_label': label,
                'segment_round': current_round, 'segment_index': next_index,
                'segment_scene_index': scene_counts[main],
                'batch_label': label, 'batch_index': next_index,
                'display_name': f'{label}{next_index:02d}-场景{scenes[main]}-分镜{scene_counts[main]}{suffix}',
            }
            next_index += 1
    return copy.deepcopy(result)
