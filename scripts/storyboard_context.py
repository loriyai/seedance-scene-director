"""Read-only scene inputs and the shared text-review context binding.

Exports are disposable views of the bound project, never a new fact ledger.
No state, continuity conclusion or semantic approval is inferred here.
"""
import copy
from pathlib import Path

import director as d
import ratio_profiles
import storyboard_text as text_core


def context_hash(state, project=None):
    from project_paths import semantic_files
    data = {key: state.get(key) for key in
            ('files', 'records', 'ledger', 'style_hash', 'header_hash', 'header_policy', 'units', 'entry')}
    data['files'] = semantic_files(state)
    data['context_policy'] = 'content-context-v2'
    required = ('references/storyboard-rules.md', 'references/storyboard-text-format.md',
                'scripts/storyboard_text.py', 'scripts/storyboard_text_delivery.py',
                'scripts/storyboard_authoring_gate.py', 'scripts/storyboard_context.py',
                'scripts/segment_naming.py', 'scripts/script_partition.py',
                'scripts/video_settings.py', 'scripts/ratio_profiles.py',
                'scripts/video_header.py',
                'scripts/project_paths.py', 'scripts/project_continuation.py',
                'references/storyboard-workflow.md', 'references/execution-contract.md')
    data['text_rules'] = {name: d.file_hash(d.ROOT / name) for name in required}
    if project is not None:
        from video_settings import get_ratio
        from script_partition import load_plan
        ratio = get_ratio(project, state)
        data['video_ratio'] = ratio
        data['ratio_profile'] = ratio_profiles.profile_summary(ratio)
        from video_header import read as read_header
        header = read_header(project, state, required=bool(state.get('header_hash')))
        data['video_header'] = d.sha(header) if header is not None else None
        data['partition_plan'] = load_plan(project, state)
        from project_continuation import deliveries, related, sha
        from project_paths import resolve
        data['local_history'] = [{'segment': r['segment'], 'span': r['span'],
            'draft': sha(resolve(project, r['draft'])), 'actual': sha(resolve(project, r['actual'])),
            'review': sha(resolve(project, r['review']))} for r in deliveries(project)]
        data['related_history'] = [{'session_sha256': r['sha256'], 'ranges': r['ranges'],
                                  'outputs': [s['output_sha256'] for s in r['receipts']]} for r in related(project)]
    return d.sha(d.packed(data))


def scene_context(project, scene_id, span=None):
    from video_settings import get_ratio
    from video_header import read as read_header
    project = Path(project).resolve()
    state = d.load(project)
    ratio = get_ratio(project, state)
    header = read_header(project, state, required=bool(state.get('header_hash')))
    d.require(state.get('style_hash'), '请先保存本项目风格')
    records, _, scenes, assets = text_core._context(state)
    d.require(scene_id in scenes, f'场次不存在：{scene_id}')
    scene = scenes[scene_id]
    selected = [r for r in records if r['scene'] == scene_id]
    d.require(selected, '场次没有已绑定来源记录')
    full_span = [selected[0]['start'], selected[-1]['end']]
    selected_span = list(span) if span is not None else full_span
    start, end = d.interval(selected_span, state['length'])
    d.require(full_span[0] <= start < end <= full_span[1], '上下文范围必须在指定场次内')
    selected = [copy.deepcopy(r) for r in selected if r['start'] < end and r['end'] > start]
    for record in selected:
        # Keep the complete original record for context and expose its exact
        # requested slice separately; never silently trim a quoted source.
        record['selected_text'] = d.portion(record, start, end)
        record['selected_span'] = [max(start, record['start']), min(end, record['end'])]
    allowed = text_core.scene_asset_ids(scene)
    ids = list(dict.fromkeys(aid for values in allowed.values() for aid in values))
    d.require(set(ids) <= assets.keys(), '场次台账引用了不存在的资产')
    # image_prompt repeats the visual design and is not a storyboard input.
    fields = ('id', 'name', 'source_entry', 'source_speaker', 'visual_mode',
              'appearance', 'visual_facts', 'visual_design')
    projected = [{key: copy.deepcopy(assets[aid][key]) for key in fields if key in assets[aid]}
                 for aid in ids]
    units = state.get('units', [])
    before = [u for u in units if u['span'][1] <= start]
    after = [u for u in units if u['span'][0] >= end]
    previous_end = max((u['span'][1] for u in before), default=None)
    next_start = min((u['span'][0] for u in after), default=None)

    def boundary(values, coordinate, side):
        return [unit_view(u) for u in values if u['span'][side] == coordinate]

    def unit_view(unit):
        fields = ('id', 'display_name', 'version', 'output_format', 'span',
                  'main_scene', 'scene_ids', 'source_records')
        result = {key: copy.deepcopy(unit[key]) for key in fields if key in unit}
        if unit.get('output_format') == text_core.OUTPUT_FORMAT:
            result['source_text'] = unit['source_text']
        else:
            result['rendered_text'] = d.render_unit(state, unit)
        return result

    # Preserve actual delivered text, including old-format units, so a reader
    # can judge continuity. Do not invent a machine-written end-state summary.
    neighbours = {
        'previous': boundary(before, previous_end, 1),
        'previous_is_adjacent': previous_end == start,
        'next': boundary(after, next_start, 0),
        'next_is_adjacent': next_start == end,
        'crossing_boundary': [unit_view(u) for u in units
                              if u['span'][0] < start < u['span'][1]
                              or u['span'][0] < end < u['span'][1]],
    }
    script = Path(state['files']['script']['path']).read_text(encoding='utf-8-sig')
    lines = script.splitlines()
    first, last = selected[0]['line'], selected[-1]['line']
    from project_continuation import deliveries
    local = deliveries(project)
    local_neighbours = []
    for row in local:
        if row['span'][1] == start or row['span'][0] == end:
            from project_paths import resolve
            local_neighbours.append({'delivery_mode': row['delivery_mode'], 'span': row['span'],
                'segment': row['segment'], 'actual_text': resolve(project, row['actual']).read_text(encoding='utf-8-sig')})
    return {
        'policy': 'scene-text-v1', 'scene': copy.deepcopy(scene),
        'span': selected_span, 'full_scene_span': full_span,
        'source_lines': {'first': first, 'last': last, 'text': '\n'.join(lines[first - 1:last])},
        'records': selected, 'allowed_asset_ids': allowed, 'assets': projected,
        'style': (project / 'video-style.txt').read_text(encoding='utf-8-sig').strip(),
        'video_header': header,
        'video_ratio': ratio,
        'ratio_profile': ratio_profiles.profile_summary(ratio),
        'versions': {'session_revision': state['revision'], 'files': copy.deepcopy(state['files']),
                     'style_sha256': state['style_hash'],
                     'header_sha256': state.get('header_hash') or (d.sha(header) if header is not None else None),
                     'context_sha256': context_hash(state, project)},
        'neighbours': neighbours, 'local_neighbours': local_neighbours, 'semantic_review': 'not_performed',
        'limits': '仅派生本场资料；局部范围的完整记录及source_lines提供阅读上下文，不扩大span。'
                  '外观设计不代表当前剧情状态；未交付的前场末态须由写者另行提供，程序不推断接触、持物或通路。',
    }
