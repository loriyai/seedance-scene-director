"""Read-only scene inputs and the shared text-review context binding.

Exports are disposable views of the bound project, never a new fact ledger.
No state, continuity conclusion or semantic approval is inferred here.
"""
import copy
from pathlib import Path

import director as d
import ratio_profiles
import storyboard_text as text_core


LEGACY_CONTEXT_POLICY = 'content-context-v2'
SCOPED_CONTEXT_POLICY = 'selected-content-context-v3'


def candidate_context_policy(trace, state=None):
    """Unmarked bound traces keep v2; unbound new decisions start at v3."""
    trace = trace if isinstance(trace, dict) else {}
    policy = trace.get('context_policy')
    if policy is None:
        legacy_project = state is not None and state.get('storyboard_authoring_policy') != 'shot-by-shot-v1'
        return LEGACY_CONTEXT_POLICY if trace.get('context_sha256') or legacy_project else SCOPED_CONTEXT_POLICY
    d.require(policy in (LEGACY_CONTEXT_POLICY, SCOPED_CONTEXT_POLICY), '未知的分镜上下文绑定协议')
    return policy


def context_scope(state, span, authoring=None, *, policy=SCOPED_CONTEXT_POLICY):
    if policy == LEGACY_CONTEXT_POLICY:
        return None
    d.require(policy == SCOPED_CONTEXT_POLICY, '未知的分镜上下文绑定协议')
    start, end = d.interval(span, state['length'])
    selected = [r for r in state['records'] if r['start'] < end and r['end'] > start]
    unit = {'scene_ids': list(dict.fromkeys(r['scene'] for r in selected)),
            'source_records': [r['id'] for r in selected]}
    from continuity import carry_declarations
    from source_references import declarations, normalize, project as project_refs
    import production_cast as casting
    refs = {'continuity_props': sorted([r for r in carry_declarations(authoring)
              if r['scene_id'] in unit['scene_ids']], key=lambda r: (r['scene_id'], r['asset_id'])),
            'source_references': project_refs(normalize(declarations(authoring)), unit),
            'production_cast': casting.project(casting.declarations(authoring), unit)}
    import source_background_slots as background
    slots = background.project(background.declarations(authoring), unit)
    if slots:
        refs[background.FIELD] = slots
    import source_participant_slots as participants
    slots = participants.project(participants.declarations(authoring), unit)
    if slots:
        refs[participants.FIELD] = slots
    import source_text_voices as text_voices
    voice_rows = text_voices.project(text_voices.declarations(authoring), unit)
    if voice_rows:
        refs[text_voices.FIELD] = voice_rows
    return {'policy': policy, 'span': [start, end], 'references': refs}


def _nearby_indices(rows, span):
    """All intersections plus every version at the nearest actual boundaries."""
    start, end = span
    before = max((r['span'][1] for r in rows if r['span'][1] <= start), default=None)
    after = min((r['span'][0] for r in rows if r['span'][0] >= end), default=None)
    return {i for i, r in enumerate(rows) if (r['span'][0] < end and r['span'][1] > start)
            or r['span'][1] == before or r['span'][0] == after}


def _scoped_history(state, project, scope):
    """Project actual history dependencies without mutating any saved unit."""
    rows = [{'kind': 'unit', 'span': u['span'], 'value': u} for u in state.get('units', [])]
    conservative = []
    if project is not None:
        from project_continuation import deliveries, related, sha
        from project_paths import resolve
        for row in deliveries(project):
            value = {'segment': row['segment'], 'span': row['span'],
                'draft': sha(resolve(project, row['draft'])), 'actual': sha(resolve(project, row['actual'])),
                'review': sha(resolve(project, row['review']))}
            rows.append({'kind': 'local', 'span': row['span'], 'value': value})
        for row in related(project):
            # A related session is a frozen input container, not a reason to
            # hash unrelated units whenever its verified manifest is updated.
            sibling = d.read_json(resolve(project, row['session']))
            for unit in sibling.get('units', []):
                rows.append({'kind': 'related_unit', 'span': unit['span'],
                             'origin': row['session'], 'value': unit})
            for receipt in row['receipts']:
                if isinstance(receipt.get('span'), list) and len(receipt['span']) == 2:
                    rows.append({'kind': 'related_output', 'span': receipt['span'],
                        'origin': row['session'], 'value': {k: receipt.get(k) for k in
                            ('request_id', 'span', 'output_sha256')}})
                else:
                    # Legacy external receipts without coordinates cannot be
                    # safely localized. Preserve their former global binding.
                    conservative.append({'session_sha256': row['sha256'], 'ranges': row['ranges'],
                                         'output_sha256': receipt['output_sha256']})
    selected = _nearby_indices(rows, scope['span'])
    references = scope['references']
    anchors = {r['anchor']['unit_id'] for r in references['continuity_props']
               if 'unit_id' in r.get('anchor', {})}
    actor_keys = {r['actor_key'] for r in references['production_cast']}
    for index, row in enumerate(rows):
        if row['kind'] not in ('unit', 'related_unit'):
            continue
        unit = row['value']
        if (unit.get('id') in anchors or any(r.get('actor_key') in actor_keys
                                              for r in unit.get('production_cast', []))):
            selected.add(index)
    # Keep stored order: when multiple equal boundaries exist, the camera
    # resolver's selected version can itself depend on that order.
    return {'selected': [rows[i] for i in range(len(rows)) if i in selected],
            'legacy_external_without_span': conservative}


def context_hash(state, project=None, *, scope=None):
    from project_paths import semantic_files
    data = {key: state.get(key) for key in
            ('files', 'records', 'ledger', 'style_hash', 'header_hash', 'header_policy', 'units', 'entry')}
    data['files'] = semantic_files(state)
    data['context_policy'] = LEGACY_CONTEXT_POLICY if scope is None else SCOPED_CONTEXT_POLICY
    if scope is not None:
        d.require(scope.get('policy') == SCOPED_CONTEXT_POLICY, '局部上下文协议错误')
        data.pop('units', None)
        data['selected_scope'] = scope
        data['history_dependencies'] = _scoped_history(state, project, scope)
    required = ('references/storyboard-rules.md', 'references/storyboard-text-format.md',
                'scripts/storyboard_text.py', 'scripts/storyboard_text_delivery.py',
                'scripts/storyboard_blackout.py',
                'scripts/storyboard_delivery_timing.py',
                'scripts/storyboard_authoring_gate.py', 'scripts/storyboard_context.py',
                'scripts/continuity.py', 'scripts/source_references.py', 'scripts/production_cast.py',
                'modules/screenplay-standardizer/scripts/character_variants.py',
                'scripts/segment_naming.py', 'scripts/script_partition.py',
                'scripts/video_settings.py', 'scripts/ratio_profiles.py',
                'scripts/video_header.py',
                'scripts/project_paths.py', 'scripts/project_continuation.py',
                'references/storyboard-workflow.md', 'references/execution-contract.md')
    data['text_rules'] = {name: d.file_hash(d.ROOT / name) for name in required}
    if scope and scope.get('references', {}).get('source_background_slots'):
        name = 'scripts/source_background_slots.py'
        data['text_rules'][name] = d.file_hash(d.ROOT / name)
    if scope and scope.get('references', {}).get('source_participant_slots'):
        for name in ('scripts/source_participant_slots.py', 'scripts/source_background_slots.py'):
            data['text_rules'][name] = d.file_hash(d.ROOT / name)
    if scope and scope.get('references', {}).get('source_text_voices'):
        for name in ('scripts/source_text_voices.py', 'scripts/speech_timing_audit.py', 'scripts/canvas.py'):
            data['text_rules'][name] = d.file_hash(d.ROOT / name)
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
        if scope is None:
            data['local_history'] = [{'segment': r['segment'], 'span': r['span'],
                'draft': sha(resolve(project, r['draft'])), 'actual': sha(resolve(project, r['actual'])),
                'review': sha(resolve(project, r['review']))} for r in deliveries(project)]
            data['related_history'] = [{'session_sha256': r['sha256'], 'ranges': r['ranges'],
                                      'outputs': [s['output_sha256'] for s in r['receipts']]} for r in related(project)]
    return d.sha(d.packed(data))


def scene_context(project, scene_id, span=None, authoring_trace=None):
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
    trace = d.read_json(authoring_trace) if authoring_trace is not None else None
    policy = candidate_context_policy(trace, state)
    binding_scope = context_scope(state, selected_span, trace, policy=policy)
    start, end = d.interval(selected_span, state['length'])
    d.require(full_span[0] <= start < end <= full_span[1], '上下文范围必须在指定场次内')
    selected = [copy.deepcopy(r) for r in selected if r['start'] < end and r['end'] > start]
    for record in selected:
        # Keep the complete original record for context and expose its exact
        # requested slice separately; never silently trim a quoted source.
        record['selected_text'] = d.portion(record, start, end)
        record['selected_span'] = [max(start, record['start']), min(end, record['end'])]
    allowed = text_core.scene_asset_ids(scene)
    carried, source_refs, cast_rows, background_rows, participant_rows, text_voice_rows = [], [], [], [], [], []
    if authoring_trace is not None:
        from continuity import carry_declarations, validate_carry_props
        declared = carry_declarations(d.read_json(authoring_trace))
        local = [row for row in declared if isinstance(row, dict) and row.get('scene_id') == scene_id]
        carried = validate_carry_props(state, local, {scene_id})
        allowed['props'] = list(dict.fromkeys(allowed['props'] + [row['asset_id'] for row in carried]))
        from source_references import declarations, normalize, validate, project as project_refs
        declared_refs = normalize(declarations(d.read_json(authoring_trace)))
        local_refs = project_refs(declared_refs, {'scene_ids': [scene_id], 'source_records': [r['id'] for r in selected]})
        # Validate quotes against the actual requested slice, never the full
        # records also returned for reading context.
        scoped = text_core._slice_state(state, selected_span)
        source_refs = validate(scoped, local_refs, [r['id'] for r in selected], source_state=state)
        for row in source_refs:
            field = ('characters' if row['asset_id'].startswith('CHAR') else
                     'scene_assets' if row['asset_id'].startswith('SET') else 'props')
            allowed[field] = list(dict.fromkeys(allowed[field] + [row['asset_id']]))
        import production_cast as casting
        local_unit = {'scene_ids': [scene_id], 'source_records': [r['id'] for r in selected]}
        cast_rows = casting.project(casting.declarations(d.read_json(authoring_trace)), local_unit)
        scoped = text_core._slice_state(state, selected_span)
        cast_rows = casting.validate(scoped, [local_unit], cast_rows)
        assets = text_core._context(casting.context(state, cast_rows))[3]
        allowed['characters'] = list(dict.fromkeys(allowed['characters'] + [r['asset_id'] for r in cast_rows]))
        import source_background_slots as background
        background_rows = background.project(background.declarations(trace), local_unit)
        background_rows = background.validate(scoped, background_rows, local_unit['source_records'], source_state=state)
        assets = text_core._context(background.context(casting.context(state, cast_rows), background_rows))[3]
        allowed['characters'] = list(dict.fromkeys(allowed['characters'] + [r['asset_id'] for r in background_rows]))
        import source_participant_slots as participants
        participant_rows = participants.project(participants.declarations(trace), local_unit)
        participant_rows = participants.validate(scoped, participant_rows, local_unit['source_records'], source_state=state)
        overlaid = background.context(casting.context(state, cast_rows), background_rows)
        assets = text_core._context(participants.context(overlaid, participant_rows))[3]
        allowed['characters'] = list(dict.fromkeys(allowed['characters'] + [r['asset_id'] for r in participant_rows]))
        import source_text_voices as text_voices
        text_voice_rows = text_voices.project(text_voices.declarations(trace), local_unit)
        text_voice_rows = text_voices.validate(scoped, text_voice_rows, local_unit['source_records'], source_state=state)
    ids = list(dict.fromkeys([aid for values in allowed.values() for aid in values]
                            + [row['speaker'] for row in text_voice_rows if row['speaker'] != 'female_announcer']))
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
        'continuity_props': carried, 'source_references': source_refs, 'production_cast': cast_rows,
        **({'source_background_slots': background_rows} if background_rows else {}),
        **({'source_participant_slots': participant_rows} if participant_rows else {}),
        **({'source_text_voices': text_voice_rows, 'source_text_voice_character_ids': sorted(
             {r['speaker'] for r in text_voice_rows if r['speaker'] != 'female_announcer'})} if text_voice_rows else {}),
        'style': (project / 'video-style.txt').read_text(encoding='utf-8-sig').strip(),
        'video_header': header,
        'video_ratio': ratio,
        'ratio_profile': ratio_profiles.profile_summary(ratio),
        'versions': {'session_revision': state['revision'], 'files': copy.deepcopy(state['files']),
                     'style_sha256': state['style_hash'],
                     'header_sha256': state.get('header_hash') or (d.sha(header) if header is not None else None),
                     'context_policy': policy,
                     'context_sha256': context_hash(state, project, scope=binding_scope)},
        'neighbours': neighbours, 'local_neighbours': local_neighbours, 'semantic_review': 'not_performed',
        'limits': '仅派生本场资料；局部范围的完整记录及source_lines提供阅读上下文，不扩大span。'
                  '外观设计不代表当前剧情状态；未交付的前场末态须由写者另行提供，程序不推断接触、持物或通路。',
    }
