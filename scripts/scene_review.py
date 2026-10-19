"""Freeze scene work, consolidate findings, and prepare conservative re-review.

This module copies existing, exactly bound judgments; it never makes a semantic
judgment or closes a finding. All generated reports remain pending and must pass
the ordinary full-request review_gate before commit. Snapshots are private review
evidence, not prompts: give reviewers the scene and dependencies they need.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import sys

import review_gate as gate
import shot_review


POLICY = 'scene-review-conservative-v1'
VERSION = 1
GENERIC_BASES = {'通过', '已检查', '同前', '同上', '字段完整', '构图合理', '符合要求'}
ASSET_ID = re.compile(r'^(?:CHAR|PROP|SET|LOC|SC)\d+$')
def digest(value):
    return gate.digest(value)


def require(condition, message):
    if not condition:
        raise ValueError('场次复核：' + message)


def rules_digest():
    """Current semantic/validation contract; historical snapshots keep theirs."""
    import creative_rules
    return creative_rules.rules_digest(Path(__file__).resolve().parent.parent)


def source_binding(state):
    # Ledger contents are dependency-selected below; a ledger file timestamp or
    # unrelated asset change must not invalidate every scene.
    return {key: copy.deepcopy(value) for key, value in state.items()
            if key in ('drafts', 'records', 'length')} | {
                'files': {key: copy.deepcopy(value) for key, value in state.get('files', {}).items()
                          if key in ('script', 'source', 'review')}}


def _snapshot_payload(snapshot):
    return {key: value for key, value in snapshot.items() if key != 'snapshot_sha256'}


def _validation_scope(state, request, warnings, partial, require_workflow, validation_passed):
    require(all(type(flag) is bool for flag in (partial, require_workflow, validation_passed)),
            '验证范围选项须为布尔值')
    spans = [copy.deepcopy(unit['span']) for unit in request.get('units', [])]
    return {'validator': 'director.validate', 'status': 'passed' if validation_passed else 'not_run',
            'partial': partial, 'require_workflow': require_workflow,
            'request_span': copy.deepcopy(request.get('span')), 'unit_spans': spans,
            'covered_span': [spans[0][0], spans[-1][1]] if spans else None,
            'request_sha256': digest(request), 'context_sha256': gate.context_digest(state),
            'warnings_sha256': digest(warnings), 'final_full_validation_required': True}


def freeze(state, request, warnings=None, *, partial=False, require_workflow=False,
           validation_passed=False):
    """Return an immutable-content snapshot; no session mutation or approval.

    Direct callers get validation.status=not_run. Set validation_passed only
    immediately after director.validate succeeds with the recorded partial flag
    (and require_workflow succeeds when requested), as the CLI does below.
    """
    # Historical v1 snapshots may contain declarations that required a
    # conservative fallback. New v2/unknown declarations must not acquire that
    # fallback as a way to bypass their explicit structural contract.
    from shot_dependencies import POLICY as LEGACY_DEPENDENCIES, validate_request as validate_dependencies
    if any('review_dependencies' in unit and
           (not isinstance(unit['review_dependencies'], dict) or
            unit['review_dependencies'].get('policy') != LEGACY_DEPENDENCIES)
           for unit in request.get('units', [])):
        validate_dependencies(request)
    warnings = [] if warnings is None else warnings
    snapshot = {'policy': POLICY, 'version': VERSION, 'cue_policy': shot_review.CUE_POLICY,
                'scope_scene_ids': sorted({sid for u in request.get('units', [])
                                           for sid in u.get('scene_ids', [])}),
                'source_sha256': digest(source_binding(state)),
                'context_sha256': gate.context_digest(state),
                'request_sha256': digest(request), 'rules_sha256': rules_digest(),
                'state': copy.deepcopy(state), 'request': copy.deepcopy(request),
                'warnings': copy.deepcopy(warnings),
                'validation': _validation_scope(state, request, warnings, partial,
                                                require_workflow, validation_passed),
                'targets': shot_review.targets(state, request)}
    snapshot['snapshot_sha256'] = digest(snapshot)
    return snapshot


def verify_snapshot(snapshot):
    require(isinstance(snapshot, dict) and snapshot.get('policy') == POLICY
            and snapshot.get('version') == VERSION, '快照版本不兼容')
    require(snapshot.get('snapshot_sha256') == digest(_snapshot_payload(snapshot)), '冻结快照已改变')
    state, request = snapshot['state'], snapshot['request']
    require(snapshot.get('request_sha256') == digest(request)
            and snapshot.get('context_sha256') == gate.context_digest(state)
            and snapshot.get('source_sha256') == digest(source_binding(state)), '快照绑定不一致')
    cue_policy = snapshot.get('cue_policy', shot_review.LEGACY_CUE_POLICY)
    require(cue_policy in (shot_review.LEGACY_CUE_POLICY, shot_review.CUE_POLICY),
            '快照审阅提示策略不兼容')
    require(snapshot.get('targets') == shot_review.targets(state, request, cue_policy=cue_policy),
            '快照镜头不一致')
    validation = snapshot.get('validation')
    if validation is not None:
        require(isinstance(validation, dict), '快照验证范围格式错误')
        expected = _validation_scope(state, request, snapshot['warnings'], validation.get('partial'),
                                     validation.get('require_workflow'), validation.get('status') == 'passed')
        require(validation == expected, '快照验证范围与请求不一致')
    return snapshot


def _actual(item):
    return {key: value for key, value in item.items() if key not in ('status', 'basis')}


def _basis(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip() not in GENERIC_BASES


def _passed(item):
    return item.get('status') == 'passed' and _basis(item.get('basis'))


def _bind_old_report(snapshot, report):
    # Historical v2 reports remain readable evidence, but their absent rule
    # binding cannot authorize reuse under current rules. Never rewrite their
    # snapshots or pretend that a current digest existed in the old report.
    require(isinstance(report, dict) and report.get('policy') in (gate.LEGACY_POLICY, gate.POLICY)
            and report.get('request_sha256') == snapshot['request_sha256']
            and report.get('context_sha256') == snapshot['context_sha256'], '旧审阅与冻结版本不匹配')
    if report['policy'] == gate.POLICY:
        require(isinstance(report.get('rules_sha256'), str) and bool(report['rules_sha256'])
                and report['rules_sha256'] == snapshot.get('rules_sha256'),
                '旧审阅规则绑定与冻结快照不匹配')
    actual, expected = report.get('shot_reviews'), snapshot['targets']
    require(isinstance(actual, list) and len(actual) == len(expected), '旧审阅缺少镜头或重复镜头')
    for item, target in zip(actual, expected):
        require(isinstance(item, dict) and set(item) == set(target)
                and _actual(item) == _actual(target), '旧审阅镜头快照不匹配')
    expected_warnings = gate.warning_records(snapshot['warnings'])
    decisions = report.get('warning_decisions')
    require(isinstance(decisions, list) and all(isinstance(w, dict) for w in decisions), '旧警告记录无效')
    keys = [w.get('warning_sha256') for w in decisions]
    require(len(keys) == len(set(keys)) and set(keys) == set(expected_warnings), '旧警告清单不匹配')
    for item in decisions:
        require(item.get('warning') == expected_warnings[item['warning_sha256']]
                and digest(item.get('warning')) == item['warning_sha256'], '旧警告对象与哈希不一致')
    if 'boundary_reviews' in report:
        boundaries = report['boundary_reviews']
        expected_boundaries = boundary_targets(snapshot['state'], snapshot['request'])
        require(isinstance(boundaries, list) and len(boundaries) == len(expected_boundaries),
                '旧局部边界记录缺项或重复')
        for boundary_row, expected_boundary in zip(boundaries, expected_boundaries):
            require(isinstance(boundary_row, dict) and set(boundary_row) == set(expected_boundary)
                    and _boundary_actual(boundary_row) == _boundary_actual(expected_boundary),
                    '旧局部边界绑定与冻结版本不匹配')
            if boundary_row.get('status') == 'passed':
                _boundary_partition(boundary_row)
    return {item['key']: item for item in actual}


def _path_value(target, field):
    require(isinstance(field, str) and field.strip(), '问题缺少字段路径')
    value = target
    try:
        for part in field.split('.'):
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError('场次复核：问题字段不存在：' + field) from None
    return value


def merge_issues(snapshot, issue_lists):
    """Consolidate by explicit root cause, keeping every finding and conflict.

    Each issue requires root_cause, target (shot key), field (dot path), basis,
    requested_change, closure_condition. Each bundle must bind snapshot_sha256,
    or every issue must supply expected_sha256 matching its frozen field. Any
    supplied binding must match. No patch is applied or finding auto-closed.
    """
    verify_snapshot(snapshot)
    targets = {target['key']: target for target in snapshot['targets']}
    groups, by_field = {}, {}
    for collection in issue_lists:
        issues = collection.get('issues', []) if isinstance(collection, dict) else collection
        require(isinstance(issues, list), '问题单须为数组或含issues数组的对象')
        bundle_hash = collection.get('snapshot_sha256') if isinstance(collection, dict) else None
        if isinstance(collection, dict) and 'snapshot_sha256' in collection:
            require(bundle_hash == snapshot['snapshot_sha256'], '反馈来自不同冻结快照')
        bound_bundle = bundle_hash == snapshot['snapshot_sha256']
        for issue in issues:
            require(isinstance(issue, dict), '问题项格式错误')
            for key in ('root_cause', 'target', 'field', 'basis', 'requested_change', 'closure_condition'):
                require(isinstance(issue.get(key), str) and issue[key].strip(), '问题缺少' + key)
            require(issue['target'] in targets, '问题镜头不属于冻结版本')
            original = _path_value(targets[issue['target']], issue['field'])
            original_hash = digest(original)
            require(bound_bundle or 'expected_sha256' in issue, '反馈未绑定冻结快照或原字段哈希')
            require(issue.get('expected_sha256', original_hash) == original_hash, '问题原字段已改变')
            hit = copy.deepcopy(issue)
            hit.update(expected_sha256=original_hash, original_value=copy.deepcopy(original), status='pending')
            hit['feedback_binding'] = {'snapshot_sha256': bundle_hash,
                                       'field_sha256': original_hash,
                                       'mode': 'snapshot' if bound_bundle else 'field'}
            hit['issue_sha256'] = digest(hit)
            group = groups.setdefault(issue['root_cause'], {'root_cause': issue['root_cause'],
                                                          'status': 'pending', 'hits': []})
            # Preserve all originating evidence, including repeated hits.
            group['hits'].append(hit)
            by_field.setdefault((issue['target'], issue['field']), []).append(hit)
    conflicts = []
    for (target, field), hits in by_field.items():
        if len({(hit['requested_change'], hit['closure_condition']) for hit in hits}) > 1:
            conflicts.append({'target': target, 'field': field, 'status': 'unresolved',
                              'issue_sha256s': [hit['issue_sha256'] for hit in hits],
                              'reason': '同一冻结字段收到不同要求，须先人工合并，不采用最后覆盖'})
    return {'policy': POLICY, 'snapshot_sha256': snapshot['snapshot_sha256'],
            'status': 'conflicts' if conflicts else 'pending', 'groups': list(groups.values()),
            'conflicts': conflicts, 'finding_count': sum(len(g['hits']) for g in groups.values())}


def revise_plan(snapshot, merged):
    """Return one pending worklist; closure is always a later human/agent act."""
    verify_snapshot(snapshot)
    require(merged.get('policy') == POLICY and merged.get('snapshot_sha256') == snapshot['snapshot_sha256'],
            '问题单与冻结版本不匹配')
    findings = [hit for group in merged.get('groups', []) for hit in group.get('hits', [])]
    # Re-check the bases instead of trusting a copied consolidated file.
    checked = merge_issues(snapshot, [findings])
    return {'policy': POLICY, 'snapshot_sha256': snapshot['snapshot_sha256'],
            'status': 'blocked_conflicts' if checked['conflicts'] else 'pending_revision',
            'conflicts': checked['conflicts'],
            'changes': [{'target': hit['target'], 'field': hit['field'],
                         'expected_sha256': hit['expected_sha256'],
                         'requested_change': hit['requested_change'],
                         'closure_condition': hit['closure_condition'],
                         'status': 'pending', 'closure_basis': ''} for hit in findings]}


def _ids(value):
    found = set()
    if isinstance(value, dict):
        for item in value.values():
            found.update(_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_ids(item))
    elif isinstance(value, str):
        if ASSET_ID.fullmatch(value):
            found.add(value)
        found.update(re.findall(r'@\{((?:CHAR|PROP|SET|LOC|SC)\d+)\}', value))
    return found


def _catalog_ids(value, catalog):
    if isinstance(value, dict):
        return set().union(*(_catalog_ids(item, catalog) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_catalog_ids(item, catalog) for item in value)) if value else set()
    if isinstance(value, str):
        return ({value} if value in catalog else set()) | (set(re.findall(r'@\{([^}]+)\}', value)) & set(catalog))
    return set()


def _ledger_dependencies(state, unit):
    ledger = state.get('ledger', {})
    catalog = {}
    for category, values in ledger.items():
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict) and isinstance(item.get('id'), str):
                    catalog[item['id']] = {'category': category, 'value': item}
    unit = _review_input(unit)
    needed = _ids(unit) | _catalog_ids(unit, catalog)
    remaining = list(needed)
    while remaining:
        aid = remaining.pop()
        linked_ids = _ids(catalog.get(aid, {})) | _catalog_ids(catalog.get(aid, {}), catalog)
        for linked in linked_ids - needed:
            needed.add(linked)
            remaining.append(linked)
    # Preserve policies and unknown non-catalog data: an unclassified dependency
    # widens reuse, never silently becomes an ignored field.
    metadata = {key: value for key, value in ledger.items()
                if not isinstance(value, list) or any(not isinstance(item, dict) or 'id' not in item
                                                     for item in value)}
    return {'metadata': metadata, 'assets': {aid: catalog.get(aid) for aid in sorted(needed)},
            'missing': sorted(needed - set(catalog))}


def _unit_sources(state, unit):
    a, b = unit['span']
    return [record for record in state.get('records', []) if record['start'] < b and record['end'] > a]


def _identity(unit):
    # Positional keys remain positional. This is a comparison, not a stable-ID
    # migration or an attempt to match inserted/reordered shots semantically.
    return [unit.get('id'), unit.get('span'), unit.get('scene_ids'), len(unit.get('shots', []))]


def _valid_reset(state, unit):
    from continuity import evidence
    boundary = unit.get('continuity', {}).get('boundary', {})
    if boundary.get('kind') != 'reset' or not _basis(boundary.get('reason')):
        return False
    try:
        evidence(state, unit, boundary.get('evidence'), '复用边界', allow_heading=True)
    except (KeyError, TypeError, ValueError):
        return False
    return True


BOUNDARY_POLICY = 'local-boundary-review-v1'


def _fact_paths(states):
    result = set()
    for entity, item in states.items():
        result.update((entity, (field,)) for field in ('present', 'asset_id'))
        result.update((entity, ('facts', fact)) for fact in item.get('facts', {}))
    return result


def _fact_value(states, entity, path):
    value = states.get(entity)
    for field in path:
        if not isinstance(value, dict) or field not in value:
            return {'exists': False}
        value = value[field]
    return {'exists': True, 'value': copy.deepcopy(value)}


def boundary_targets(state, request):
    """Pending local judgments in the existing report; no automatic verdict.

    Only opted-in v3 reset boundaries use this contract. The reviewer classifies
    the generated field union as reset or carried during the ordinary review.
    """
    from shot_dependencies import POLICY_V3, common, unclassified_shot_fields
    connections = _connections(state, request)
    targets = {target['key']: target for target in shot_review.targets(state, request)}
    rows = []
    for index, unit in enumerate(request.get('units', [])):
        contract = unit.get('continuity', {})
        if (unit.get('review_dependencies', {}).get('policy') != POLICY_V3
                or contract.get('boundary', {}).get('kind') != 'reset'):
            continue
        connection = connections[index]
        previous = [request['units'][i] for i in connection['previous_selected']]
        previous += connection['previous_external']
        initial = contract.get('initial', {})
        paths = _fact_paths(initial)
        predecessors = []
        for prior in previous:
            prior_contract = prior.get('continuity', {})
            final = prior_contract.get('final', {})
            paths.update(_fact_paths(final))
            predecessors.append({'identity': _identity(prior),
                                 'entities': copy.deepcopy(prior_contract.get('entities')),
                                 'common': common(prior),
                                 'unclassified_shots': unclassified_shot_fields(prior),
                                 'final': copy.deepcopy(final)})
        rows.append({'policy': BOUNDARY_POLICY, 'key': f'boundary:{index + 1}',
                     'unit_index': index + 1,
                     'binding': {'identity': _identity(unit),
                                 'sources': _unit_sources(state, unit),
                                 'boundary': copy.deepcopy(contract.get('boundary')),
                                 'common': common(unit),
                                 'unclassified_shots': unclassified_shot_fields(unit),
                                 'entities': copy.deepcopy(contract.get('entities')),
                                 'initial': copy.deepcopy(initial),
                                 'first': _actual(targets[f'{index + 1}:1']),
                                 'predecessors': predecessors, 'ambiguous': connection['ambiguous']},
                     'fields': [{'entity': entity, 'path': list(path)} for entity, path in sorted(paths)],
                     'reset_fields': [], 'carried_fields': [], 'status': 'pending', 'basis': ''})
    return rows


def _boundary_actual(row):
    return {key: value for key, value in row.items()
            if key not in ('reset_fields', 'carried_fields', 'status', 'basis')}


def _boundary_partition(row):
    """Validate explicit classification; equality alone never implies reset."""
    classified = []
    for name in ('reset_fields', 'carried_fields'):
        values = row.get(name)
        require(isinstance(values, list), '局部边界缺少' + name)
        for value in values:
            require(isinstance(value, dict) and set(value) == {'entity', 'path'}
                    and isinstance(value['entity'], str) and isinstance(value['path'], list)
                    and value in row['fields'], '局部边界包含未知事实字段')
        classified.extend(digest(value) for value in values)
    require(len(classified) == len(set(classified))
            and set(classified) == {digest(value) for value in row['fields']},
            '局部边界须完整划分重置与持续事实，不能漏项或重复')
    binding = row['binding']
    if row['carried_fields']:
        require(len(binding['predecessors']) == 1 and not binding['ambiguous'],
                '局部边界持续事实须有唯一可核对前序')
    for field in row['carried_fields']:
        before = _fact_value(binding['predecessors'][0]['final'], field['entity'], field['path'])
        after = _fact_value(binding['initial'], field['entity'], field['path'])
        require(before.get('exists') and before == after, '局部边界持续事实与前序不一致')


def verify_boundary_reviews(state, request, submitted):
    expected = boundary_targets(state, request)
    if not expected and submitted is None:
        return
    require(isinstance(submitted, list) and len(submitted) == len(expected),
            '局部边界复核须覆盖当前v3重置边界')
    for actual, target in zip(submitted, expected):
        require(isinstance(actual, dict) and set(actual) == set(target)
                and _boundary_actual(actual) == _boundary_actual(target),
                '局部边界绑定与当前来源、状态或首镜不一致')
        require(_passed(actual), '局部边界尚未实际复核或缺少具体依据')
        require(_valid_reset(state, request['units'][target['unit_index'] - 1]),
                '局部边界重置缺少有效原文依据')
        _boundary_partition(actual)


def _boundary_reusable(old, new, report, row, target):
    if not _passed(row) or not _basis(report.get('reviewer')):
        return False
    if (old['source_sha256'] != new['source_sha256']
            or old['rules_sha256'] != new['rules_sha256']):
        return False
    old_targets = {item['key']: item for item in boundary_targets(old['state'], old['request'])}
    original = old_targets.get(row.get('key'))
    if original is None or _boundary_actual(row) != _boundary_actual(original):
        return False
    try:
        _boundary_partition(row)
    except (ValueError, KeyError, TypeError):
        return False
    if row['fields'] != target['fields']:
        return False
    # Remove only the values explicitly reviewed as reset. Persistent fields,
    # endpoint identities, source, initial state and first shot remain bound.
    before, after = copy.deepcopy(row['binding']), copy.deepcopy(target['binding'])
    if len(before['predecessors']) != len(after['predecessors']):
        return False
    for bound in (before, after):
        for prior in bound['predecessors']:
            if any(not isinstance(item, dict) or set(item) != {'present', 'asset_id', 'facts'}
                   or not isinstance(item['facts'], dict) for item in prior['final'].values()):
                return False  # A future/unclassified state must not be discarded.
            prior['final'] = [{**field, 'value': _fact_value(prior['final'], field['entity'], field['path'])}
                              for field in row['carried_fields']]
    if before != after or after['ambiguous']:
        return False
    candidate = dict(target, reset_fields=row['reset_fields'], carried_fields=row['carried_fields'])
    try:
        _boundary_partition(candidate)
    except (ValueError, KeyError, TypeError):
        return False
    index = target['unit_index'] - 1
    return _valid_reset(old['state'], old['request']['units'][index]) and _valid_reset(new['state'], new['request']['units'][index])


def _prepare_boundary_reuse(old, new, old_report, report, allow_reuse):
    old_rows = {item.get('key'): item for item in old_report.get('boundary_reviews', [])
                if isinstance(item, dict)}
    proof = []
    for target in report.get('boundary_reviews', []):
        previous = old_rows.get(target['key'])
        reused = (allow_reuse and old_report.get('policy') == gate.POLICY and previous is not None
                  and _boundary_reusable(old, new, old_report, previous, target))
        if reused:
            for field in ('reset_fields', 'carried_fields', 'status', 'basis'):
                target[field] = copy.deepcopy(previous[field])
        proof.append({'key': target['key'], 'status': 'reused' if reused else 'pending',
                      'old_boundary_sha256': digest(previous) if previous else None,
                      'current_boundary_sha256': digest(target),
                      'old_report_sha256': digest(old_report)})
    return proof


def _trusted_resets(old, new, report, old_targets, *, allow_local=True):
    result = set()
    local_targets = {item['key']: item for item in boundary_targets(new['state'], new['request'])}
    for row in report.get('boundary_reviews', []):
        target = local_targets.get(row.get('key')) if isinstance(row, dict) else None
        if allow_local and target is not None and report.get('policy') == gate.POLICY and _boundary_reusable(old, new, report, row, target):
            result.add(target['unit_index'] - 1)
    check = report.get('checks', {}).get('continuity', {})
    if not _passed(check) or not _basis(report.get('reviewer')):
        return result
    if old['source_sha256'] != new['source_sha256'] or old['rules_sha256'] != new['rules_sha256']:
        return result
    for index, (a, b) in enumerate(zip(old['request'].get('units', []), new['request'].get('units', []))):
        if b.get('review_dependencies', {}).get('policy') == 'shot-dependencies-v3':
            continue  # v3 requires its actual local boundary judgment.
        reviews = [target for target in old_targets.values() if target['unit_index'] == index + 1]
        unresolved_warning = any(index in _warning_units(item['warning'], old['request']['units'])
                                 and (item.get('decision') not in ('accepted', 'resolved')
                                      or not _basis(item.get('basis')))
                                 for item in report.get('warning_decisions', []))
        if (_identity(a) == _identity(b) and a.get('continuity') == b.get('continuity')
                and _valid_reset(old['state'], a) and _valid_reset(new['state'], b)
                and not unresolved_warning and reviews and all(_passed(target) for target in reviews)):
            result.add(index)
    return result


def _review_input(unit):
    """Writer review notes are evidence about content, not new creative input."""
    return {key: copy.deepcopy(value) for key, value in unit.items()
            if key not in ('staging_review', 'audio_preflight')}


def _connections(state, request):
    """Resolve real source neighbours, including gaps in a replacement request.

    A stable predecessor disambiguates multiple renditions of the same source.
    Otherwise all candidates remain dependencies and ambiguity blocks reuse.
    Array proximity is never evidence of a narrative connection.
    """
    selected, stored = request.get('units', []), state.get('units', [])
    stored_ids = {u.get('id') for u in stored if u.get('id')}
    saved_by_id = {}
    for unit in stored:
        saved_by_id.setdefault(unit.get('id'), []).append(unit)
    replacement_ids, effective = set(), []
    for index, unit in enumerate(selected):
        stable_id = unit.get('id')
        if request.get('mode') == 'replace' and stable_id not in stored_ids:
            # The validator supports display names/short names as well as IDs.
            from director import resolve_unit_id
            stable_id = resolve_unit_id(state, stable_id)
        if request.get('mode') == 'replace':
            replacement_ids.add(stable_id)
        saved = saved_by_id.get(stable_id, [])
        predecessor = (saved[0].get('predecessor') if request.get('mode') == 'replace'
                       and len(saved) == 1 else unit.get('predecessor'))
        effective.append({'kind': 'selected', 'index': index, 'unit': unit,
                          'id': stable_id, 'predecessor': predecessor})
    effective.extend({'kind': 'stored', 'index': index, 'unit': unit,
                      'id': unit.get('id'), 'predecessor': unit.get('predecessor')}
                     for index, unit in enumerate(stored) if unit.get('id') not in replacement_ids)
    result = [{'previous_selected': [], 'next_selected': [],
               'previous_external': [], 'next_external': [], 'ambiguous': False}
              for unit in selected]
    by_end = {}
    for position, unit in enumerate(effective):
        by_end.setdefault(unit['unit'].get('span', [None, None])[1], []).append((position, unit))
    for position, later in enumerate(effective):
        span = later['unit'].get('span', [None, None])
        candidates = [earlier for n, earlier in by_end.get(span[0], []) if n != position]
        explicit = [u for u in candidates if later['predecessor'] is not None
                    and u['id'] == later['predecessor']]
        if len(explicit) == 1:
            candidates = explicit
        ambiguous = len(candidates) > 1
        if later['kind'] == 'selected' and ambiguous:
            result[later['index']]['ambiguous'] = True
        for earlier in candidates:
            if later['kind'] == 'selected':
                field = 'previous_selected' if earlier['kind'] == 'selected' else 'previous_external'
                value = earlier['index'] if earlier['kind'] == 'selected' else _review_input(earlier['unit'])
                result[later['index']][field].append(value)
            if earlier['kind'] == 'selected':
                field = 'next_selected' if later['kind'] == 'selected' else 'next_external'
                value = later['index'] if later['kind'] == 'selected' else _review_input(later['unit'])
                result[earlier['index']][field].append(value)
                result[earlier['index']]['ambiguous'] |= ambiguous
    return result


def _external_connections(state, request, index, connections=None):
    item = (connections or _connections(state, request))[index]
    return {'previous': item['previous_external'], 'next': item['next_external'],
            'ambiguous': item['ambiguous']}


def _dependencies(snapshot, index, resets, connections=None):
    state, request = snapshot['state'], snapshot['request']
    units = request['units']
    unit = units[index]
    connections = connections or _connections(state, request)
    return {'rules_sha256': snapshot['rules_sha256'], 'source_sha256': snapshot['source_sha256'],
            'unit': _review_input(unit), 'sources': _unit_sources(state, unit), 'style_hash': state.get('style_hash'),
            'entry': state.get('entry'), 'ledger': _ledger_dependencies(state, unit),
            'previous': [_review_input(units[i]) for i in connections[index]['previous_selected']]
                        if index not in resets else [],
            'next': [_review_input(units[i]) for i in connections[index]['next_selected'] if i not in resets],
            'external': _external_connections(state, request, index, connections)}


def _components(size, resets, connections):
    result, unseen = [], set(range(size))
    while unseen:
        pending, current = [min(unseen)], []
        while pending:
            index = pending.pop()
            if index not in unseen:
                continue
            unseen.remove(index)
            current.append(index)
            if index not in resets:
                pending.extend(connections[index]['previous_selected'])
            pending.extend(i for i in connections[index]['next_selected'] if i not in resets)
        result.append(sorted(current))
    return result


def _known(unit, dependencies):
    contract = unit.get('continuity', {})
    return (contract.get('version') == 1
            and all(isinstance(contract.get(key), dict) for key in ('initial', 'final', 'entities'))
            and contract.get('boundary', {}).get('kind') in ('reset', 'continuous')
            and not dependencies['external'].get('ambiguous')
            and bool(dependencies['sources']) and not dependencies['ledger']['missing'])


def _affected_units(old, new, resets, old_deps, new_deps, connections=None):
    before, after = old['request']['units'], new['request']['units']
    reasons = {i: set() for i in range(len(after))}
    identities_before, identities_after = list(map(_identity, before)), list(map(_identity, after))
    if identities_before != identities_after:
        mismatch = next((i for i, pair in enumerate(zip(identities_before, identities_after))
                         if pair[0] != pair[1]), min(len(before), len(after)))
        # Deletion at the end still changes the preceding connection.
        for i in range(max(0, mismatch - 1), len(after)):
            reasons[i].add('positional_structure_changed')
    for i in range(len(after)):
        if i >= len(before):
            reasons[i].add('new_unit')
            continue
        for key in new_deps[i]:
            if old_deps[i].get(key) != new_deps[i][key]:
                reasons[i].add('dependency_changed:' + key)
    connections = connections or _connections(new['state'], new['request'])
    components = _components(len(after), resets, connections)
    for component in components:
        unknown = any(not _known(after[i], new_deps[i])
                      or i < len(before) and not _known(before[i], old_deps[i]) for i in component)
        if unknown:
            for i in component:
                reasons[i].add('unknown_dependency_connected_component')
    for start, unit in enumerate(after):
        if start >= len(before):
            continue
        old_contract, contract = before[start].get('continuity', {}), unit.get('continuity', {})
        if old_contract == contract:
            continue
        # A changed declaration cannot be trusted as an automatic reset.
        carry = contract.get('final')
        carried_entities = contract.get('entities')
        carry_known = isinstance(carry, dict) and _known(unit, new_deps[start])
        pending = [(i, carry, carried_entities, carry_known) for i in connections[start]['next_selected']]
        seen = set()
        while pending:
            i, carry, carried_entities, carry_known = pending.pop()
            if i in seen:
                continue
            seen.add(i)
            if i in resets:
                continue
            reasons[i].add('continuity_change_propagation')
            following = after[i].get('continuity', {})
            inherited = (carry_known and following.get('initial') == carry
                         and following.get('entities') == carried_entities
                         and _known(after[i], new_deps[i]))
            if not inherited:
                reasons[i].add('unresolved_continuity_inheritance')
            old_following = before[i].get('continuity', {}) if i < len(before) else {}
            if (inherited and following.get('final') == old_following.get('final')
                    and following.get('entities') == old_following.get('entities')):
                continue
            pending.extend((j, following.get('final'), following.get('entities'), inherited)
                           for j in connections[i]['next_selected'])
    return reasons


def _explicit_warning_scope(warning, units):
    """Resolve generated stable references, never infer a scope from prose."""
    from shot_dependencies import WARNING_SCOPE_POLICY
    scope = warning.get('review_scope')
    if (not isinstance(scope, dict) or set(scope) != {'policy', 'units'} or
            scope.get('policy') != WARNING_SCOPE_POLICY or
            not isinstance(scope.get('units'), list) or not scope['units']):
        return None
    result, seen = [], set()
    for entry in scope['units']:
        if (not isinstance(entry, dict) or not {'span', 'scene_ids'} <= set(entry) or
                set(entry) - {'span', 'scene_ids', 'unit_id', 'review_ids'} or
                not isinstance(entry['span'], list) or len(entry['span']) != 2 or
                not all(type(x) is int for x in entry['span']) or
                not isinstance(entry['scene_ids'], list) or not entry['scene_ids']):
            return None
        candidates = [i for i, unit in enumerate(units)
                      if unit.get('span') == entry['span'] and unit.get('scene_ids') == entry['scene_ids']
                      and ('unit_id' not in entry or unit.get('id') == entry['unit_id'])]
        if len(candidates) != 1 or candidates[0] in seen:
            return None
        i = candidates[0]
        seen.add(i)
        ids = entry.get('review_ids')
        shots = units[i].get('shots', [])
        if ids is None:
            keys = [f'{i+1}:{n+1}' for n in range(len(shots))]
        else:
            if (not isinstance(ids, list) or not ids or
                    not all(isinstance(sid, str) and sid.strip() for sid in ids) or
                    len(ids) != len(set(ids))):
                return None
            keys = []
            for sid in ids:
                indexes = [n for n, shot in enumerate(shots) if shot.get('review_id') == sid]
                if len(indexes) != 1:
                    return None
                keys.append(f'{i+1}:{indexes[0]+1}')
        if not keys:
            return None
        result.append((i, keys))
    return result


def _warning_units(warning, units):
    """Unresolvable diagnostic scope depends on the whole request."""
    if 'review_scope' in warning:
        scoped = _explicit_warning_scope(warning, units)
        return sorted(i for i, _ in scoped) if scoped is not None else list(range(len(units)))
    names = warning.get('units', [])
    names = list(names) if isinstance(names, list) else []
    if 'unit' in warning:
        names.append(warning['unit'])
    lookup = {u.get('id'): index for index, u in enumerate(units) if u.get('id') is not None}
    if names and all(name in lookup for name in names):
        return sorted({lookup[name] for name in names})
    return list(range(len(units)))


def _scoped_warning_dependencies(snapshot, warning, unit_dependencies, shot_dependencies):
    scope = _explicit_warning_scope(warning, snapshot['request']['units'])
    if scope is None:
        return None
    result = {}
    for i, keys in scope:
        if not _known(snapshot['request']['units'][i], unit_dependencies[i]):
            return None
        for key in keys:
            dependency = shot_dependencies.get(key)
            if dependency is None or dependency['unknown']:
                return None
            result[key] = dependency
    return {'review_scope': warning['review_scope'], 'dependencies': result}


def prepare_incremental(old_snapshot, old_report, state, request, warnings=None, *, partial=False,
                        require_workflow=False, validation_passed=False, expected_report_sha256=None,
                        attach_evidence=True):
    """Return (pending gate-compatible report, provenance/statistics sidecar).

    A partial old report is allowed only with exact complete target snapshots;
    only its individual passed records with concrete bases can be reused.
    """
    verify_snapshot(old_snapshot)
    if expected_report_sha256 is not None:
        require(expected_report_sha256 == digest(old_report), '旧审阅报告摘要不匹配，拒绝复用')
    old_targets = _bind_old_report(old_snapshot, old_report)
    legacy_rules_unbound = old_report['policy'] == gate.LEGACY_POLICY
    current = freeze(state, request, warnings, partial=partial, require_workflow=require_workflow,
                     validation_passed=validation_passed)
    report = gate.pending(state, request, current['warnings'])
    require(report['rules_sha256'] == current['rules_sha256'],
            '准备期间创作规则发生变化，须重新冻结并准备审阅')
    local_ready = (expected_report_sha256 is not None
                   and old_snapshot.get('validation', {}).get('status') == 'passed'
                   and current.get('validation', {}).get('status') == 'passed')
    boundary_provenance = _prepare_boundary_reuse(old_snapshot, current, old_report, report, local_ready)
    resets = _trusted_resets(old_snapshot, current, old_report, old_targets, allow_local=local_ready)
    before, after = old_snapshot['request']['units'], request['units']
    old_connections = _connections(old_snapshot['state'], old_snapshot['request'])
    new_connections = _connections(state, request)
    old_deps = [_dependencies(old_snapshot, i, resets, old_connections) for i in range(len(before))]
    new_deps = [_dependencies(current, i, resets, new_connections) for i in range(len(after))]
    reasons = _affected_units(old_snapshot, current, resets, old_deps, new_deps, new_connections)
    from shot_dependencies import reasons as shot_reasons
    granular = shot_reasons(old_snapshot, current, old_report, expected_report_sha256, resets)
    from shot_dependencies import request_diagnostics
    dependency_diagnostics = {'old': request_diagnostics(old_snapshot['request']),
                              'current': request_diagnostics(request)}
    for i, unit in enumerate(after):
        if 'unresolved_continuity_inheritance' in reasons[i]:
            for target in report['shot_reviews']:
                if target['unit_index'] == i+1 and target['key'] in granular:
                    granular[target['key']][0].add('unresolved_continuity_inheritance')
    reviewer = old_report.get('reviewer', '')
    provenance, counts = [], {}
    for target in report['shot_reviews']:
        key, index = target['key'], target['unit_index'] - 1
        previous = old_targets.get(key)
        why = set(granular[key][0]) if key in granular else set(reasons[index])
        if legacy_rules_unbound:
            why.add('old_review_rules_unbound')
        if granular and expected_report_sha256 is None:
            why.add('old_review_digest_required')
        if previous is None or _actual(previous) != _actual(target):
            why.add('target_snapshot_changed')
        if previous is None or not _passed(previous) or not _basis(reviewer):
            why.add('no_concrete_passed_judgment')
        if not why:
            target.update(status='passed', basis=previous['basis'])
        for reason in why:
            counts[reason] = counts.get(reason, 0) + 1
        provenance.append({'key': key, 'status': 'reused' if not why else 'pending',
                           'reasons': sorted(why), 'dependency_sha256': digest(granular[key][2]) if key in granular else digest(new_deps[index]),
                           'old_dependency_sha256': digest(granular[key][1]) if key in granular else (digest(old_deps[index]) if index < len(old_deps) else None),
                           'review_id': target['shot'].get('review_id'),
                           'old_snapshot_sha256': old_snapshot['snapshot_sha256'],
                           'old_report_sha256': digest(old_report),
                           'old_target_key': previous.get('key') if previous else None,
                           'previous_was_reviewed': bool(previous and _passed(previous)),
                           'verified_dependencies': list(granular[key][2]['dependencies']) if key in granular and granular[key][2] else ['whole_unit_and_connections'],
                           'previous_reviewer': reviewer if not why else None,
                           'previous_basis': previous['basis'] if not why else None})
    old_warnings = {w['warning_sha256']: w for w in old_report['warning_decisions']}
    warning_provenance = []
    scoped_shot_dependencies = None
    if any('review_scope' in item['warning'] for item in report['warning_decisions']):
        from shot_dependencies import dependencies as dependency_payloads
        scoped_shot_dependencies = (dependency_payloads(old_snapshot, resets),
                                    dependency_payloads(current, resets))
    for item in report['warning_decisions']:
        # gate.pending may already carry an exact CURRENT writer decision.
        # That is different evidence from reusing the previous final review.
        current_authoring = copy.deepcopy(item) if item.get('decision') == 'accepted' else None
        previous = old_warnings.get(item['warning_sha256'])
        scope = _warning_units(item['warning'], after)
        warning_dependency = [new_deps[i] for i in scope]
        why = []
        if legacy_rules_unbound:
            why.append('old_review_rules_unbound')
        if granular and expected_report_sha256 is None:
            why.append('old_review_digest_required')
        if 'review_scope' in item['warning']:
            old_scope = _scoped_warning_dependencies(old_snapshot, item['warning'], old_deps,
                                                     scoped_shot_dependencies[0])
            new_scope = _scoped_warning_dependencies(current, item['warning'], new_deps,
                                                     scoped_shot_dependencies[1])
            warning_dependency = new_scope if new_scope is not None else warning_dependency
            if old_scope is None or new_scope is None:
                why.append('warning_scope_dependencies_unknown')
            elif old_scope != new_scope or any('unresolved_continuity_inheritance' in reasons[i] for i in scope):
                why.append('warning_dependencies_changed')
        elif any(reasons[i] for i in scope):
            why.append('warning_dependencies_changed')
        if (previous is None or previous.get('warning') != item['warning']
                or previous.get('decision') not in ('accepted', 'resolved')
                or not _basis(previous.get('basis')) or not _basis(reviewer)):
            why.append('no_exact_warning_judgment')
        if not why:
            item.update(decision=previous['decision'], basis=previous['basis'])
        status = 'reused' if not why else ('authoring_reused' if current_authoring else 'pending')
        warning_provenance.append({'warning_sha256': item['warning_sha256'],
                                   'status': status, 'reasons': why,
                                   'dependency_sha256': digest(warning_dependency),
                                   'previous_reviewer': reviewer if not why else None,
                                   'current_authoring_decision': current_authoring if status == 'authoring_reused' else None,
                                   'decision_source': 'current_audio_preflight' if status == 'authoring_reused' else
                                                      ('previous_final_review' if status == 'reused' else None)})
    reused = sum(item['status'] == 'reused' for item in provenance)
    sidecar = {'policy': POLICY, 'old_snapshot_sha256': old_snapshot['snapshot_sha256'],
               'old_request_sha256': old_snapshot['request_sha256'],
               'old_report_sha256': digest(old_report), 'request_sha256': digest(request),
               'context_sha256': gate.context_digest(state), 'rules_sha256': current['rules_sha256'],
               'validation': current['validation'],
               'prepared_report_sha256': digest(report), 'shot_provenance': provenance,
               'warning_provenance': warning_provenance,
               'boundary_provenance': boundary_provenance,
               'dependency_diagnostics': dependency_diagnostics,
               'stats': {'total_shots': len(provenance), 'reused_shots': reused,
                         'pending_shots': len(provenance) - reused, 'reasons': counts,
                         'reused_warnings': sum(w['status'] == 'reused' for w in warning_provenance),
                         'authoring_reused_warnings': sum(w['status'] == 'authoring_reused' for w in warning_provenance),
                         'pending_warnings': sum(w['status'] == 'pending' for w in warning_provenance)}}
    reused_boundaries = [p['key'] for p in boundary_provenance if p['status'] == 'reused']
    if attach_evidence and (granular and (reused or any(p['status'] == 'reused' for p in warning_provenance))
                            or reused_boundaries):
        report['reuse_evidence'] = {
            'old_snapshot': copy.deepcopy(old_snapshot), 'old_report': copy.deepcopy(old_report),
            'expected_report_sha256': expected_report_sha256,
            'reused_keys': [p['key'] for p in provenance if p['status'] == 'reused'],
            'reused_warning_keys': [p['warning_sha256'] for p in warning_provenance if p['status'] == 'reused'],
            'reused_boundary_keys': reused_boundaries,
            'validation': current['validation']}
        sidecar['prepared_report_sha256'] = digest(report)
    return report, sidecar


def verify_reuse_evidence(state, request, report, warnings):
    evidence = report.get('reuse_evidence')
    if evidence is None:
        return
    require(isinstance(evidence, dict), '复用追溯格式错误')
    # Recompute from immutable old targets and the CURRENT request; do not trust
    # a copied reused count or a sidecar's status labels.
    if 'old_snapshot' in evidence:
        old, old_report = evidence['old_snapshot'], evidence['old_report']
    else:
        old = json.loads(Path(evidence['snapshot_path']).read_text(encoding='utf-8-sig'))
        old_report = json.loads(Path(evidence['report_path']).read_text(encoding='utf-8-sig'))
        require(old.get('snapshot_sha256') == evidence.get('snapshot_sha256'), '旧快照文件摘要变化')
        require(digest(old_report) == evidence.get('expected_report_sha256'), '旧报告文件摘要变化')
    validation = evidence['validation']
    require(validation.get('partial') is False and validation.get('status') == 'passed',
            '最终复用验收须完整结构检查')
    regenerated, proof = prepare_incremental(old, old_report, state, request, warnings,
        partial=False, require_workflow=validation.get('require_workflow', False), validation_passed=True,
        expected_report_sha256=evidence.get('expected_report_sha256'), attach_evidence=False)
    keys = [p['key'] for p in proof['shot_provenance'] if p['status'] == 'reused']
    require(keys == evidence.get('reused_keys'), '复用镜头及依赖已变化')
    warning_keys = [p['warning_sha256'] for p in proof['warning_provenance'] if p['status'] == 'reused']
    require(warning_keys == evidence.get('reused_warning_keys', []), '复用警告及依赖已变化')
    boundary_keys = [p['key'] for p in proof['boundary_provenance'] if p['status'] == 'reused']
    require(boundary_keys == evidence.get('reused_boundary_keys', []), '复用局部边界及持续事实已变化')
    actual_boundaries = {item['key']: item for item in report.get('boundary_reviews', [])}
    expected_boundaries = {item['key']: item for item in regenerated.get('boundary_reviews', [])}
    for key in boundary_keys:
        require(actual_boundaries.get(key) == expected_boundaries[key], '复用局部边界与原实际判断不一致：' + key)
    actual_warnings = {w['warning_sha256']: w for w in report['warning_decisions']}
    expected_warnings = {w['warning_sha256']: w for w in regenerated['warning_decisions']}
    for key in warning_keys:
        require(actual_warnings[key] == expected_warnings[key], '复用警告处置与原实际记录不一致：' + key)
    actual = {t['key']: t for t in report['shot_reviews']}
    expected = {t['key']: t for t in regenerated['shot_reviews']}
    for key in keys:
        require(actual[key] == expected[key], '复用判断与原实际审阅记录不一致：' + key)


def _write_new(path, value):
    path = Path(path).resolve()
    root = Path(__file__).resolve().parent.parent
    require(path != root and root not in path.parents, '工作记录不能写入Skill包内')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('freeze', 'merge-issues', 'revise-plan', 'prepare-incremental'))
    parser.add_argument('--project', type=Path)
    parser.add_argument('--state', type=Path, help='offline bound state; never edits session')
    parser.add_argument('--request', type=Path)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--old-report', type=Path)
    parser.add_argument('--old-report-sha256', help='已保存的旧报告规范摘要；精细复用必须匹配，不从当前报告自动信任生成')
    parser.add_argument('--issues', type=Path, nargs='+')
    parser.add_argument('--merged', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--provenance', type=Path)
    parser.add_argument('--partial', action='store_true',
                        help='validate an authored append prefix only; final gate still requires full validation')
    parser.add_argument('--require-workflow', action='store_true',
                        help='require the B workflow policy on every selected new/replaced unit')
    args = parser.parse_args(argv)
    try:
        import director as d
        if args.command in ('freeze', 'prepare-incremental'):
            require(bool(args.project) != bool(args.state), '须指定且只指定--project或--state')
            require(args.request is not None, '缺少--request')
            state = d.load(args.project) if args.project else d.read_json(args.state)
            request = d.read_json(args.request)
            if args.require_workflow:
                d.require_workflow(request)
            warnings = d.validate(state, request, partial=args.partial)
        if args.command == 'freeze':
            result = freeze(state, request, warnings, partial=args.partial,
                            require_workflow=args.require_workflow, validation_passed=True)
        else:
            require(args.snapshot is not None, '缺少--snapshot')
            snapshot = d.read_json(args.snapshot)
            if args.command == 'merge-issues':
                require(bool(args.issues), '缺少--issues')
                result = merge_issues(snapshot, [d.read_json(path) for path in args.issues])
            elif args.command == 'revise-plan':
                require(args.merged is not None, '缺少--merged')
                result = revise_plan(snapshot, d.read_json(args.merged))
            else:
                require(args.old_report is not None and args.provenance is not None,
                        '缺少--old-report或--provenance')
                require(args.out.resolve() != args.provenance.resolve(), '报告和来源记录须使用不同文件')
                require(not args.out.exists() and not args.provenance.exists(), '输出文件已存在，不覆盖已有审阅')
                result, sidecar = prepare_incremental(snapshot, d.read_json(args.old_report),
                                                      state, request, warnings, partial=args.partial,
                                                      require_workflow=args.require_workflow,
                                                      validation_passed=True,
                                                      expected_report_sha256=args.old_report_sha256)
                if 'reuse_evidence' in result:
                    evidence = result['reuse_evidence']
                    evidence.pop('old_snapshot')
                    evidence.pop('old_report')
                    evidence.update(snapshot_path=str(args.snapshot.resolve()), report_path=str(args.old_report.resolve()),
                                    snapshot_sha256=snapshot['snapshot_sha256'])
                    sidecar['prepared_report_sha256'] = digest(result)
                _write_new(args.provenance, sidecar)
        _write_new(args.out, result)
        print(json.dumps({'status': 'prepared_pending', 'output': str(args.out),
                          'command': args.command}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
