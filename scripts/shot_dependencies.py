"""Evidence-backed local invalidation; absence of a graph remains conservative.

Graphs are reviewer-visible declarations, not NLP-inferred independence. IDs are
draft-local optional identities; this never renumbers committed shots.
"""
import copy

POLICY = 'shot-dependencies-v1'
KINDS = {'axis', 'eyeline', 'occlusion', 'action_match', 'composition', 'state', 'other'}


def graph(unit):
    g = unit.get('review_dependencies')
    shots = unit.get('shots', [])
    ids = [s.get('review_id') for s in shots]
    if not (isinstance(g, dict) and set(g) == {'policy', 'complete', 'basis', 'links'} and
            g.get('policy') == POLICY and g.get('complete') is True and
            isinstance(g.get('basis'), str) and len(g['basis'].strip()) >= 12 and
            isinstance(g.get('links'), list) and ids and
            all(isinstance(i, str) and i.strip() for i in ids) and len(ids) == len(set(ids))):
        return None
    for link in g['links']:
        if not (isinstance(link, dict) and set(link) == {'from', 'to', 'kind', 'basis'} and
                isinstance(link.get('from'), str) and isinstance(link.get('to'), str) and
                link['from'] != link['to'] and link['kind'] in KINDS and
                isinstance(link['basis'], str) and len(link['basis'].strip()) >= 12):
            return None
    return g


def common(unit):
    # Any unknown unit-level condition remains a public dependency. Review
    # paperwork itself cannot change staging or justify more reuse.
    result = {k: copy.deepcopy(v) for k, v in unit.items() if k not in
              ('shots', 'continuity', 'staging_review', 'audio_preflight', 'identity_decisions')}
    result['continuity'] = {k: copy.deepcopy(v) for k, v in unit.get('continuity', {}).items()
                            if k not in ('initial', 'final')}
    result['all_dialogue_parts'] = [copy.deepcopy(s.get('dialogue_parts', [])) for s in unit.get('shots', [])]
    return result


def dependencies(snapshot, resets):
    import scene_review as sr
    units = snapshot['request']['units']
    targets = {t['key']: t for t in snapshot['targets']}
    by_id, graphs, invalid = {}, {}, set()
    for i, unit in enumerate(units):
        g = graph(unit)
        graphs[i] = g
        if g is None:
            invalid.add(i)
        for n, shot in enumerate(unit.get('shots', [])):
            sid = shot.get('review_id')
            if sid in by_id:
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
        payloads[sid] = {'target': sr._actual(target), 'common': common(units[i]),
                        'source': sr._unit_sources(snapshot['state'], units[i]),
                        'ledger': sr._ledger_dependencies(snapshot['state'], units[i]),
                        'rules': snapshot['rules_sha256'], 'source_binding': snapshot['source_sha256'],
                        'style': snapshot['state'].get('style_hash'), 'entry': snapshot['state'].get('entry'),
                        'external': sr._external_connections(snapshot['state'], snapshot['request'], i)}
    output = {}
    for sid, (i, n, target) in by_id.items():
        needed = {sid}
        # Unit edges retain exact neighbour evidence. Interior cuts use stable
        # adjacency/time/state and declared visual dependencies, not all text.
        if n == 0 and i > 0 and i not in resets:
            needed.add(units[i-1]['shots'][-1].get('review_id'))
        if n == len(units[i]['shots'])-1 and i+1 < len(units) and i+1 not in resets:
            needed.add(units[i+1]['shots'][0].get('review_id'))
        # Boundary neighbours can themselves depend on an earlier axis or
        # occluder. Close the graph AFTER adding those neighbours.
        while True:
            more = {l['from'] for l in links if l['to'] in needed}
            if more <= needed: break
            needed.update(more)
        unknown = i in invalid or any(k not in by_id or by_id[k][0] in invalid for k in needed)
        own = units[i]['shots']
        adjacency = [(s.get('review_id'), s.get('start'), s.get('end')) for s in own[max(0,n-1):n+2]]
        output[target['key']] = {'unknown': unknown, 'dependencies': {k:payloads[k] for k in sorted(k for k in needed if k in payloads)},
                                  'adjacency': adjacency, 'review_id': sid}
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
        if old_ids != new_ids:
            why.add('shot_identity_order_changed_conservative')
        if a is None or b is None or a['unknown'] or b['unknown']:
            why.add('shot_dependency_declaration_missing')
        elif a != b:
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
