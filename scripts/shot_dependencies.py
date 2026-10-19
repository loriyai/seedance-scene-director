"""Evidence-backed local invalidation; absence of a graph remains conservative.

Graphs are reviewer-visible declarations, not NLP-inferred independence. IDs are
draft-local optional identities; this never renumbers committed shots.
"""
import copy

POLICY = 'shot-dependencies-v1'
POLICY_V2 = 'shot-dependencies-v2'
POLICY_V3 = 'shot-dependencies-v3'
WARNING_SCOPE_POLICY = 'warning-scope-v1'
KINDS = {'axis', 'eyeline', 'occlusion', 'action_match', 'composition', 'state', 'other'}


def diagnostics(unit, path='unit'):
    """Validate a declared graph structurally, without judging its evidence.

    Missing declarations remain a supported conservative legacy input. V1's
    original length rule is retained for reading and comparing old snapshots.
    V2 requires nonempty evidence; its adequacy belongs to semantic review.
    """
    if 'review_dependencies' not in unit:
        return []
    g = unit.get('review_dependencies')
    root = path + '.review_dependencies'
    errors = []

    def add(field, code, message):
        errors.append({'path': field, 'code': code, 'message': message})

    if not isinstance(g, dict):
        add(root, 'object_required', '须为依赖声明对象')
        return errors
    if set(g) != {'policy', 'complete', 'basis', 'links'}:
        add(root, 'declaration_fields', '字段须为policy、complete、basis、links')
    policy = g.get('policy')
    if policy not in (POLICY, POLICY_V2, POLICY_V3):
        add(root + '.policy', 'unsupported_policy', '须使用shot-dependencies-v1、v2或v3')
    if g.get('complete') is not True:
        add(root + '.complete', 'complete_required', '完整声明须为true；不能据结构检查推断语义完整')

    def basis(value, field):
        if not isinstance(value, str) or not value.strip():
            add(field, 'nonempty_basis_required', '须提供非空的具体关系依据')
        elif policy == POLICY and len(value.strip()) < 12:
            add(field, 'legacy_basis_length', 'v1依据须至少12字符，实际为' + str(len(value.strip())))

    basis(g.get('basis'), root + '.basis')
    shots = unit.get('shots', [])
    if not isinstance(shots, list) or not shots:
        add(path + '.shots', 'shots_required', '依赖声明须对应非空镜头列表')
        shots = []
    seen = set()
    for n, shot in enumerate(shots):
        sid = shot.get('review_id') if isinstance(shot, dict) else None
        field = f'{path}.shots[{n}].review_id'
        if not isinstance(sid, str) or not sid.strip():
            add(field, 'review_id_required', '须提供非空且稳定的镜头ID')
        elif sid in seen:
            add(field, 'duplicate_review_id', '镜头ID重复：' + sid)
        else:
            seen.add(sid)
    links = g.get('links')
    if not isinstance(links, list):
        add(root + '.links', 'links_required', '须为关系列表')
        return errors
    for n, link in enumerate(links):
        field = f'{root}.links[{n}]'
        if not isinstance(link, dict):
            add(field, 'link_required', '须为关系对象')
            continue
        if set(link) != {'from', 'to', 'kind', 'basis'}:
            add(field, 'link_fields', '字段须为from、to、kind、basis')
        for endpoint in ('from', 'to'):
            if not isinstance(link.get(endpoint), str) or not link[endpoint].strip():
                add(field + '.' + endpoint, 'endpoint_required', '须为非空镜头ID')
        if isinstance(link.get('from'), str) and link.get('from') == link.get('to'):
            add(field + '.to', 'self_dependency', '来源和目标不能为同一镜头')
        if (not isinstance(link.get('kind'), str) or not link['kind'].strip()
                or policy != POLICY_V3 and link['kind'] not in KINDS):
            add(field + '.kind', 'unsupported_kind', '关系类型不受支持')
        basis(link.get('basis'), field + '.basis')
    return errors


def request_diagnostics(request):
    """Check cross-unit IDs only when the complete requested draft is available."""
    units = request.get('units', [])
    errors, ids = [], {}
    declared = any('review_dependencies' in unit for unit in units)
    for i, unit in enumerate(units):
        errors.extend(diagnostics(unit, f'units[{i}]'))
        for n, shot in enumerate(unit.get('shots', [])):
            sid = shot.get('review_id')
            if not isinstance(sid, str) or not sid.strip():
                continue
            field = f'units[{i}].shots[{n}].review_id'
            if sid in ids and declared:
                errors.append({'path': field, 'code': 'duplicate_review_id',
                               'message': '请求内镜头ID重复：' + sid + '；首次位于' + ids[sid]})
            else:
                ids[sid] = field
    for i, unit in enumerate(units):
        if graph(unit) is None:
            continue
        for n, link in enumerate(unit['review_dependencies']['links']):
            for endpoint in ('from', 'to'):
                if link[endpoint] not in ids:
                    errors.append({'path': f'units[{i}].review_dependencies.links[{n}].{endpoint}',
                                   'code': 'unknown_review_id',
                                   'message': '所引用镜头ID不在当前请求：' + link[endpoint]})
    return errors


def _raise_errors(errors):
    if errors:
        raise ValueError('镜头依赖声明无效：' + '；'.join(e['path'] + '：' + e['message'] for e in errors))


def validate_unit(unit, path='unit'):
    _raise_errors(diagnostics(unit, path))


def validate_request(request):
    _raise_errors(request_diagnostics(request))


def graph(unit):
    return unit.get('review_dependencies') if 'review_dependencies' in unit and not diagnostics(unit) else None


def target_declaration(unit, sid, units):
    """V1 targets stay byte-equivalent; v2 binds only this node's incoming edges.

    The complete declarations are always retained in snapshot.request. No basis
    is normalized or discarded: public premises bind every node in their unit,
    and edge evidence binds its actual target and all downstream dependents.
    """
    g = graph(unit)
    if g is None or g['policy'] not in (POLICY_V2, POLICY_V3):
        return copy.deepcopy(unit.get('review_dependencies'))
    declaration = {k: copy.deepcopy(g[k]) for k in ('policy', 'complete', 'basis')}
    declaration['links'] = [copy.deepcopy(link) for owner in units
                            for owner_graph in [graph(owner)] if owner_graph is not None
                            for link in owner_graph['links'] if link['to'] == sid]
    return declaration


def scope_warning(warning, units, local_index=None):
    """Attach tool-resolved scope to new v2 diagnostics; never guess old scope.

    Source spans and scene IDs are stable before formal unit numbering. Shot
    IDs are included only for an explicitly enumerated, resolvable shot list.
    Legacy-only warnings remain exactly unchanged, including their hashes.
    """
    if 'review_scope' in warning:
        return warning
    aliases = {}
    for i, unit in enumerate(units):
        for alias in (unit.get('id'), f'待提交单元{i+1}', f'unit-{i+1}'):
            if isinstance(alias, str):
                aliases.setdefault(alias, set()).add(i)

    def resolve(name):
        if name == '当前单元' and local_index is not None:
            return local_index
        found = aliases.get(name, set()) if isinstance(name, str) else set()
        return next(iter(found)) if len(found) == 1 else None

    indexes, numbers = set(), {}
    names = warning.get('units', [])
    if not isinstance(names, list):
        return warning
    names = list(names)
    if 'unit' in warning:
        names.append(warning['unit'])
    for name in names:
        i = resolve(name)
        if i is None:
            return warning
        indexes.add(i)
    shots = warning.get('shots')
    if isinstance(shots, list) and shots and all(isinstance(x, dict) for x in shots):
        for shot in shots:
            i = resolve(shot.get('unit'))
            if i is None or type(shot.get('shot')) is not int:
                return warning
            indexes.add(i)
            numbers.setdefault(i, set()).add(shot['shot'])
    elif isinstance(shots, list) and shots and all(type(x) is int for x in shots):
        if len(indexes) != 1 and local_index is None:
            return warning
        i = next(iter(indexes)) if len(indexes) == 1 else local_index
        indexes.add(i)
        numbers[i] = set(shots)
    elif type(warning.get('shot')) is int:
        if len(indexes) != 1 and local_index is None:
            return warning
        i = next(iter(indexes)) if len(indexes) == 1 else local_index
        indexes.add(i)
        numbers[i] = {warning['shot']}
    if not indexes and local_index is not None:
        indexes.add(local_index)
    if not indexes or not any(graph(units[i]) and graph(units[i])['policy'] in (POLICY_V2, POLICY_V3) for i in indexes):
        return warning
    entries = []
    for i in sorted(indexes):
        unit = units[i]
        if not isinstance(unit.get('span'), list) or not isinstance(unit.get('scene_ids'), list):
            return warning
        entry = {'span': copy.deepcopy(unit['span']), 'scene_ids': copy.deepcopy(unit['scene_ids'])}
        if isinstance(unit.get('id'), str):
            entry['unit_id'] = unit['id']
        if i in numbers:
            ids = []
            for n in sorted(numbers[i]):
                if not 1 <= n <= len(unit.get('shots', [])):
                    return warning
                sid = unit['shots'][n-1].get('review_id')
                if not isinstance(sid, str) or not sid.strip():
                    return warning
                ids.append(sid)
            entry['review_ids'] = ids
        entries.append(entry)
    return dict(warning, review_scope={'policy': WARNING_SCOPE_POLICY, 'units': entries})


def common(unit):
    # Any unknown unit-level condition remains a public dependency. Review
    # paperwork itself cannot change staging or justify more reuse.
    result = {k: copy.deepcopy(v) for k, v in unit.items() if k not in
              ('shots', 'continuity', 'staging_review', 'audio_preflight', 'identity_decisions')}
    result['continuity'] = {k: copy.deepcopy(v) for k, v in unit.get('continuity', {}).items()
                            if k not in ('initial', 'final')}
    result['all_dialogue_parts'] = [copy.deepcopy(s.get('dialogue_parts', [])) for s in unit.get('shots', [])]
    g = graph(unit)
    if g is not None and g['policy'] in (POLICY_V2, POLICY_V3):
        result['review_dependencies'] = {k: copy.deepcopy(g[k]) for k in ('policy', 'complete', 'basis')}
    if g is not None and g['policy'] == POLICY_V3:
        # These summaries describe entry/exit, not every interior moment.
        # Their precise owner is represented in v3 shot review targets.
        result.pop('start_state', None)
        result.pop('end_state', None)
    return result


def dependencies(snapshot, resets):
    import scene_review as sr
    units = snapshot['request']['units']
    connections = sr._connections(snapshot['state'], snapshot['request'])
    targets = {t['key']: t for t in snapshot['targets']}
    by_id, graphs, invalid = {}, {}, set()
    for i, unit in enumerate(units):
        g = graph(unit)
        graphs[i] = g
        if g is None:
            invalid.add(i)
        for n, shot in enumerate(unit.get('shots', [])):
            sid = shot.get('review_id')
            if not isinstance(sid, str):
                invalid.add(i)
            elif sid in by_id:
                invalid.update((i, by_id[sid][0]))
            elif isinstance(sid, str):
                by_id[sid] = (i, n, targets[f'{i+1}:{n+1}'])
    links = []
    for i, g in graphs.items():
        if g is None: continue
        for link in g['links']:
            if link['from'] not in by_id or link['to'] not in by_id:
                invalid.add(i)
            else:
                links.append(link)
    payloads = {}
    for sid, (i, n, target) in by_id.items():
        ledger_unit = units[i]
        if graphs[i] is not None and graphs[i]['policy'] == POLICY_V2:
            ledger_unit = dict(units[i], review_dependencies=target_declaration(units[i], sid, units))
        payloads[sid] = {'target': sr._actual(target), 'common': common(units[i]),
                        'source': sr._unit_sources(snapshot['state'], units[i]),
                        'ledger': sr._ledger_dependencies(snapshot['state'], ledger_unit),
                        'rules': snapshot['rules_sha256'], 'source_binding': snapshot['source_sha256'],
                        'style': snapshot['state'].get('style_hash'), 'entry': snapshot['state'].get('entry'),
                        'external': sr._external_connections(snapshot['state'], snapshot['request'], i, connections)}

    def boundary_neighbours(sid):
        """Use real selected source neighbours, never array proximity.

        External neighbours remain exact evidence in the reached node's
        payload; they are not invented as selected graph IDs.
        """
        i, n, _ = by_id[sid]
        neighbours = set()
        if n == 0 and i not in resets:
            neighbours.update(units[j]['shots'][-1].get('review_id')
                              for j in connections[i]['previous_selected'])
        if n == len(units[i]['shots']) - 1:
            neighbours.update(units[j]['shots'][0].get('review_id')
                              for j in connections[i]['next_selected'] if j not in resets)
        return neighbours

    output = {}
    for sid, (i, n, target) in by_id.items():
        needed = {sid}
        # Unit edges retain exact neighbour evidence. Interior cuts use stable
        # adjacency/time/state and declared visual dependencies, not all text.
        needed.update(boundary_neighbours(sid))
        # Boundary neighbours can themselves depend on an earlier axis or
        # occluder. Close the graph AFTER adding those neighbours.
        while True:
            more = {l['from'] for l in links if l['to'] in needed}
            if graphs[i] is not None and graphs[i]['policy'] == POLICY_V2:
                # An explicit dependency can reach a different unit's first
                # or last shot. Its implicit cut dependencies must participate
                # in the same fixed point, including any later explicit edges.
                for reached in needed:
                    if reached in by_id:
                        more.update(boundary_neighbours(reached))
            if more <= needed: break
            needed.update(more)
        unknown = (i in invalid or connections[i]['ambiguous'] or
                   any(k not in by_id or by_id[k][0] in invalid or connections[by_id[k][0]]['ambiguous'] for k in needed))
        own = units[i]['shots']
        adjacency = [(s.get('review_id'), s.get('start'), s.get('end')) for s in own[max(0,n-1):n+2]]
        output[target['key']] = {'unknown': unknown, 'dependencies': {k:payloads[k] for k in sorted(k for k in needed if k in payloads)},
                                  'adjacency': adjacency, 'review_id': sid}
        # Record only edges reached by the actual closure. V1-only snapshots
        # retain the exact old fingerprint shape, including whole declarations.
        relevant_v2 = any(g and g['policy'] == POLICY_V2 and
                          (j in {by_id[k][0] for k in needed if k in by_id} or
                           any(link['to'] in needed for link in g['links']))
                          for j, g in graphs.items())
        if relevant_v2:
            output[target['key']]['graph_scope'] = {
                str(j): {**{k: copy.deepcopy(g[k]) for k in ('policy', 'complete', 'basis')},
                         'links': [copy.deepcopy(link) for link in g['links'] if link['to'] in needed]}
                for j, g in graphs.items() if g and
                (j in {by_id[k][0] for k in needed if k in by_id} or
                 any(link['to'] in needed for link in g['links']))}
    # Keep the historical v1/v2 payload algorithm byte-equivalent. V3 is an
    # explicit new projection, never a reinterpretation of old evidence.
    output.update(_dependencies_v3(snapshot, resets, by_id, graphs, invalid, links,
                                   connections, payloads))
    return output


# Fields deliberately understood by v3. Unknown fields are NOT ignored: they
# make a reached source use its full payload, including future extensions.
_SHOT_FIELDS = {'review_id', 'scene_id', 'start', 'end', 'framing', 'angle', 'label',
                'purpose', 'camera', 'composition', 'depth_of_field', 'lighting',
                'subjects', 'focus', 'text', 'transition', 'connector',
                'sound_foreground', 'sound_environment', 'dialogue_parts',
                'dialogue_continuations', 'closeup_reason', 'face_closeup',
                'environment_evidence', 'continuity'}
_VISUAL = {'camera', 'angle', 'framing', 'composition', 'subjects', 'focus',
           'depth_of_field', 'lighting', 'environment_evidence'}
_KIND_FIELDS = {
    'state': {'scene_id', 'start', 'end'},
    'axis': _VISUAL | {'scene_id', 'start', 'end', 'text'},
    'eyeline': _VISUAL | {'scene_id', 'start', 'end', 'text'},
    'occlusion': _VISUAL | {'scene_id', 'start', 'end', 'text'},
    'composition': _VISUAL | {'scene_id', 'start', 'end', 'text', 'transition', 'connector'},
    'action_match': _VISUAL | {'scene_id', 'start', 'end', 'text', 'transition', 'connector'},
}


def unclassified_shot_fields(unit):
    """Preserve unknown shot contracts when certifying a local reset boundary."""
    result = []
    for shot in unit.get('shots', []):
        unknown = {key: copy.deepcopy(value) for key, value in shot.items() if key not in _SHOT_FIELDS}
        block = shot.get('continuity', {})
        extra = {key: copy.deepcopy(value) for key, value in block.items() if key not in ('changes', 'visibility')}
        if extra:
            unknown['continuity'] = extra
        if unknown:
            result.append({'review_id': shot.get('review_id'), 'fields': unknown})
    return result


def _v3_slice_known(payload, kind):
    target, shot = payload['target'], payload['target']['shot']
    block = shot.get('continuity', {})
    return (kind in _KIND_FIELDS and not set(shot) - _SHOT_FIELDS
            and not set(block) - {'changes', 'visibility'}
            and not set(target) - {'key', 'unit_index', 'shot_index', 'source', 'unit_position',
                                   'entering_state', 'exiting_state', 'shot', 'review_cues',
                                   'dependency_declaration', 'entry_summary', 'exit_summary'}
            and all(isinstance(v, dict) and not set(v) - {'mode', 'basis'}
                    for v in block.get('visibility', {}).values()))


def _project_v3(payload, kind):
    """A fixed reviewed relationship determines which source facts it reads.

    Full source/asset/sound/common/rule bindings are retained for every kind.
    Only understood shot presentation fields may be excluded. A state edge
    reads actual replayed state and event evidence, not camera wording.
    """
    target = payload['target']
    shot = target['shot']
    block = shot.get('continuity', {})
    if not _v3_slice_known(payload, kind):
        return copy.deepcopy(payload)
    result = {key: copy.deepcopy(value) for key, value in payload.items() if key != 'target'}
    selected = {key: copy.deepcopy(shot[key]) for key in _KIND_FIELDS[kind] if key in shot}
    selected['continuity'] = {'changes': copy.deepcopy(block.get('changes', []))}
    if kind != 'state':
        selected['continuity']['visibility'] = copy.deepcopy(block.get('visibility', {}))
    result['target'] = {key: copy.deepcopy(target[key]) for key in
                        ('key', 'unit_index', 'shot_index', 'source', 'unit_position',
                         'entering_state', 'exiting_state') if key in target}
    result['target']['shot'] = selected
    # Exit prose belongs to the exit review; state consumers use exact facts.
    if kind != 'state':
        for key in ('entry_summary', 'exit_summary'):
            if key in target:
                result['target'][key] = copy.deepcopy(target[key])
    return result


def _dependencies_v3(snapshot, resets, by_id, graphs, invalid, links, connections, payloads):
    import scene_review as sr
    units = snapshot['request']['units']
    output = {}

    def neighbours(sid):
        i, n, _ = by_id[sid]
        result = set()
        if n == 0 and i not in resets:
            result.update(units[j]['shots'][-1].get('review_id')
                          for j in connections[i]['previous_selected'])
        if n == len(units[i]['shots']) - 1:
            result.update(units[j]['shots'][0].get('review_id')
                          for j in connections[i]['next_selected'] if j not in resets)
        return result

    for sid, (i, n, target) in by_id.items():
        if graphs[i] is None or graphs[i]['policy'] != POLICY_V3:
            continue
        # Pairs retain the type of each read. A node may be read through more
        # than one kind; do not collapse the union to the first encountered.
        required, queue, edges = {(sid, 'all')}, [(sid, 'all')], []
        while queue:
            reached, kind = queue.pop()
            if reached not in by_id:
                continue
            incoming = [link for link in links if link['to'] == reached]
            # A camera/axis relationship into a source cannot change that
            # source's replayed state. Unknown relations still propagate whole.
            owner = by_id[reached][0]
            state_only = (kind == 'state' and graphs[owner] is not None
                          and graphs[owner]['policy'] == POLICY_V3
                          and _v3_slice_known(payloads[reached], kind))
            if state_only:
                incoming = [link for link in incoming if link['kind'] == 'state'
                            or link['kind'] not in _KIND_FIELDS]
            more = {(link['from'], link['kind']) for link in incoming}
            edges.extend(link for link in incoming if link not in edges)
            if not state_only:
                more.update((other, 'action_match') for other in neighbours(reached))
            for pair in more - required:
                required.add(pair)
                queue.append(pair)
        reached_ids = {key for key, _ in required}
        unknown = (i in invalid or any(key not in by_id or by_id[key][0] in invalid
                   or connections[by_id[key][0]]['ambiguous'] for key in reached_ids))
        dependencies = {}
        for key in sorted(reached_ids & set(payloads)):
            kinds = {kind for item, kind in required if item == key}
            payload = copy.deepcopy(payloads[key])
            owner, _, _ = by_id[key]
            # Edges live in graph_scope, not as the source node's unrelated
            # incoming declarations. Own target still binds its full judgment.
            if key != sid:
                payload['target'].pop('dependency_declaration', None)
            if graphs[owner] is None or graphs[owner]['policy'] != POLICY_V3:
                kinds = {'all'}  # Crossing into a legacy source stays broad.
            dependencies[key] = {'reads': {kind: _project_v3(payload, kind) for kind in sorted(kinds)}}
        own = units[i]['shots']
        output[target['key']] = {
            'unknown': unknown, 'dependencies': dependencies, 'review_id': sid,
            'adjacency': [(s.get('review_id'), s.get('start'), s.get('end'))
                          for s in own[max(0, n-1):n+2]],
            'graph_scope': {'policy': POLICY_V3, 'links': copy.deepcopy(edges)}}
    return output


def reasons(old, new, old_report, old_digest, resets):
    """Return per-shot exact dependencies only where both declarations exist."""
    import scene_review as sr
    old_deps, new_deps = dependencies(old, resets), dependencies(new, resets)
    result = {}
    old_ids = [[s.get('review_id') for s in u['shots']] for u in old['request']['units']]
    new_ids = [[s.get('review_id') for s in u['shots']] for u in new['request']['units']]
    for t in new['targets']:
        key, i = t['key'], t['unit_index']-1
        # Missing declarations retain legacy whole-unit conservative behavior.
        if graph(new['request']['units'][i]) is None:
            continue
        why = set()
        a, b = old_deps.get(key), new_deps.get(key)
        if old_digest is None:
            why.add('old_review_digest_required')
        elif old_digest != sr.digest(old_report):
            raise ValueError('旧审阅报告摘要不匹配，拒绝复用')
        if not (old.get('validation', {}).get('status') == new.get('validation', {}).get('status') == 'passed'):
            why.add('full_structural_validation_required')
        # Positional target keys are never remapped by textual similarity. A
        # changed shot sequence invalidates its whole unit, but appending the
        # next unit must not discard the already reviewed prefix. Exact target,
        # boundary-neighbour and declared dependency comparisons below still
        # invalidate every actually connected shot. Inserted/reordered units
        # also fail their own positional sequence/target comparison.
        is_new_unit = i >= len(old_ids)
        if is_new_unit:
            why.add('new_target_without_prior_review')
        elif old_ids[i] != new_ids[i]:
            why.add('shot_identity_order_changed_conservative')
        if b is None or b['unknown'] or (not is_new_unit and (a is None or a['unknown'])):
            why.add('shot_dependency_declaration_missing')
        elif not is_new_unit and a != b:
            why.add('shot_dependency_changed')
            for dep_id in set(a['dependencies']) | set(b['dependencies']):
                av,bv=a['dependencies'].get(dep_id),b['dependencies'].get(dep_id)
                if av != bv:
                    if av is None or bv is None:
                        why.add('connection_changed:'+dep_id)
                    else:
                        for field in set(av)|set(bv):
                            if av.get(field)!=bv.get(field):
                                if field=='target':
                                    for tf in set(av[field])|set(bv[field]):
                                        if av[field].get(tf)!=bv[field].get(tf):
                                            why.add('dependency:'+dep_id+'.'+tf)
                                else: why.add('dependency:'+dep_id+'.'+field)
        result[key]=(why,a,b)
    return result
