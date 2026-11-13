"""Batched Canvas execution: official local compilation, one transaction, grouped readback.

The CLI adapter owns SDK behavior; canvas.py owns field/reference contracts;
this module owns grouping, durable identities, transaction receipts and acceptance.
"""
import copy
from pathlib import Path
import time
import uuid

import canvas as c
import director as d

DEFAULT_BATCH_SIZE = 20
VIDEO_BATCH_SIZE = DEFAULT_BATCH_SIZE
ORDINARY_COMMANDS = {"create_biz_node", "create_node", "update_node_data", "update_asset", "create_edge", "delete_edges"}


def recovery_request(project, entry):
    """Load the immutable, integrity-checked request; historical writes stay read-only."""
    if entry.get('command') != 'canvas_batch' or entry.get('protocol') != c.BATCH_PROTOCOL:
        return None
    saved = entry.get('input', {})
    path = Path(saved.get('request_path', ''))
    if not path.is_file():
        path = Path(project) / 'canvas' / 'batches' / (entry['id'] + '.json')
    if not path.is_file():
        c.fail('RECOVERY_RECORD_INVALID', '原批次请求文件缺失，无法核实自动补做内容。', operation=entry['id'])
    try:
        record = d.read_json(path)
    except (ValueError, OSError, TypeError):
        c.fail('RECOVERY_RECORD_INVALID', '原批次请求文件不可读或损坏，不能自动补做。', operation=entry['id'])
    if not isinstance(record, dict):
        c.fail('RECOVERY_RECORD_INVALID', '原批次请求结构无效，不能自动补做。', operation=entry['id'])
    request = record.get('request')
    if record.get('protocol') != c.BATCH_PROTOCOL or not isinstance(request, dict) or c.digest(request) != saved.get('request_sha256'):
        c.fail('RECOVERY_RECORD_INVALID', '原批次请求完整性核验失败，不能自动补做。', operation=entry['id'])
    nodes, edges = request.get('expected_nodes'), request.get('expected_edges')
    valid_nodes = (isinstance(nodes, list) and bool(nodes) and all(isinstance(row, dict)
        and isinstance(row.get('node_id'), str) and row['node_id'] and {'node', 'asset'} <= set(row)
        and (row['node'] is None or isinstance(row['node'], dict)) and (row['asset'] is None or isinstance(row['asset'], dict)) for row in nodes))
    valid_edges = (isinstance(edges, list) and all(isinstance(row, dict) and isinstance(row.get('edge_id'), str)
        and row['edge_id'] and 'value' in row and (row['value'] is None or isinstance(row['value'], dict)) for row in edges))
    if (not valid_nodes or not valid_edges or len({row['node_id'] for row in nodes}) != len(nodes)
            or len({row['edge_id'] for row in edges}) != len(edges)
            or not isinstance(request.get('expected_edge_targets', []), list)
            or not all(isinstance(node_id, str) for node_id in request.get('expected_edge_targets', []))):
        c.fail('RECOVERY_RECORD_INVALID', '原批次前置核验记录缺失或结构无效，不能自动补做。', operation=entry['id'])
    return request


def recoverable_command(request):
    """Only deterministic setters with durable identities may be retried.

    The public CLI has no caller-supplied transaction id. Upload, generation,
    arbitrary invoke commands and JSON-patch append/increment are never replayed.
    """
    name, value = request.get('name'), request.get('input')
    if not isinstance(value, dict):
        return False
    if name == 'xyq.generation.update_prompt':
        return set(value) <= {'nodeId', 'prompt'} and isinstance(value.get('nodeId'), str) and isinstance(value.get('prompt'), str)
    if name in {'xyq.role.updateDescription', 'xyq.scene.updateDescription'}:
        return (set(value) <= {'nodeId', 'assetId', 'field', 'value'} and value.get('assetId') == value.get('nodeId')
                and isinstance(value.get('nodeId'), str) and isinstance(value.get('value'), str)
                and value.get('field') in {'characterName', 'sceneName', 'appearanceName', 'sceneViewName'})
    if name == 'xyq.role.updateAppearance':
        return (set(value) <= {'nodeId', 'assetId', 'expected', 'patch'} and value.get('assetId') == value.get('nodeId')
                and isinstance(value.get('nodeId'), str) and isinstance(value.get('patch'), dict)
                and bool(value['patch']) and set(value['patch']) <= {'generationPrompt', 'generation'})
    if name != 'apply_mutations' or value.get('atomic') is not True or not isinstance(value.get('mutations'), list) or not value['mutations']:
        return False
    for mutation in value['mutations']:
        if not isinstance(mutation, dict):
            return False
        kind = mutation.get('kind')
        if kind == 'create_node':
            valid = (set(mutation) <= {'kind', 'node', 'assets', 'autoCreateNodeAsset'}
                     and isinstance(mutation.get('node'), dict) and isinstance(mutation['node'].get('id'), str) and isinstance(mutation.get('assets'), list)
                     and mutation.get('autoCreateNodeAsset') is False)
        elif kind == 'create_biz_node':
            valid = set(mutation) <= {'kind', 'nodeKind', 'id', 'initialData', 'topLeft'} and isinstance(mutation.get('id'), str)
        elif kind == 'update_node_data':
            valid = set(mutation) <= {'kind', 'id', 'patch'} and isinstance(mutation.get('id'), str) and isinstance(mutation.get('patch'), dict)
        elif kind == 'update_asset':
            valid = (set(mutation) <= {'kind', 'assetId', 'contentPatch'} and isinstance(mutation.get('assetId'), str)
                     and isinstance(mutation.get('contentPatch'), dict))
        elif kind == 'create_edge':
            valid = (set(mutation) <= {'kind', 'edge'} and isinstance(mutation.get('edge'), dict)
                     and all(isinstance(mutation['edge'].get(key), str) for key in ('id', 'source', 'target')))
        elif kind == 'delete_edges':
            valid = set(mutation) <= {'kind', 'ids'} and isinstance(mutation.get('ids'), list) and all(isinstance(value, str) for value in mutation['ids'])
        else:
            return False
        if not valid:
            return False
    return True


def retryable_write_error(exc):
    if isinstance(exc, c.CanvasError):
        if exc.code in {'CLI_TRANSIENT', 'CLI_TIMEOUT', 'READBACK_PENDING', 'READBACK_INCOMPLETE', 'NETWORK_ERROR', 'UNCERTAIN_WRITE'}:
            return True
        return exc.code == 'CLI_ERROR' and any(term in str(exc).lower() for term in ('有效json', 'invalid json', 'acknowledg', 'lost ack'))
    return isinstance(exc, (TimeoutError, ConnectionError))


def edge_targets(entry, request):
    return request.get('expected_edge_targets', sorted({target['node_id'] for target in entry['targets']
        if target['action'] not in {'semantic_asset_snapshot', 'asset_snapshot', 'bound_asset_identity'}}))


def expected_projection(doc, node_id, row):
    value = c.semantic_projection(doc, node_id, row.get('source_only', False), identity_only=row.get('identity_only', False))
    if row.get('reference_identity_only'):
        data = (value.get('asset') or {}).get('content', {})
        data = data['data'] if isinstance(data.get('data'), dict) else data
        generation = data.get('generation')
        if isinstance(generation, dict):
            references = generation.get('references', [])
            if isinstance(references, list):
                projected = []
                for reference in references:
                    if not isinstance(reference, dict):
                        projected.append(reference)
                        continue
                    source = reference.get('sourceNodeId') or reference.get('nodeAssetId')
                    if not source and reference.get('id') in c.nodes(doc):
                        source = reference['id']
                    projected.append({'sourceNodeId': source, 'type': reference.get('type')} if source else reference)
                generation['references'] = sorted(projected, key=d.packed)
    return value


def before_state(snapshot, entry, request):
    """Missing keys remain unknown; only a complete native snapshot proves before."""
    doc = snapshot['document']
    rows = request.get('expected_nodes')
    if snapshot.get('complete') is not True or not isinstance(rows, list) or not rows:
        return 'unknown'
    states = []
    for row in rows:
        if not isinstance(row, dict) or not {'node_id', 'node', 'asset'} <= set(row):
            return 'unknown'
        node_id = row['node_id']
        if row['node'] is not None and node_id not in c.nodes(doc):
            states.append('unknown')
            continue
        if node_id in c.nodes(doc) and node_id not in doc['assets']:
            states.append('unknown')
            continue
        expected = {'rootCanvasId': doc['rootCanvasId'], 'assets': {doc['rootCanvasId']: {'content': {
            'nodes': {node_id: row['node']} if row['node'] is not None else {}}}}}
        if row['asset'] is not None:
            expected['assets'][node_id] = row['asset']
        # Keep known source nodes available for the legacy reference-row id alias.
        expected['assets'][doc['rootCanvasId']]['content']['nodes'].update(
            {nid: node for nid, node in c.nodes(doc).items() if nid != node_id})
        states.append(c.compare_expected(expected_projection(doc, node_id, row), expected_projection(expected, node_id, row)))
    edges = doc['assets'][doc['rootCanvasId']]['content'].get('edges')
    if not isinstance(edges, dict) or not isinstance(request.get('expected_edges'), list):
        return 'unknown'
    expected_edges = {row['edge_id']: row['value'] for row in request['expected_edges']}
    for edge_id, expected in expected_edges.items():
        states.append('unknown' if expected is not None and edge_id not in edges else c.compare_expected(edges.get(edge_id), expected))
    consumers = set(edge_targets(entry, request))
    expected_ids = {edge_id for edge_id, edge in expected_edges.items() if edge is not None and edge.get('target') in consumers}
    actual_ids = {edge_id for edge_id, edge in edges.items() if edge.get('target') in consumers}
    if actual_ids - expected_ids:
        states.append('conflict')
    if expected_ids - actual_ids:
        states.append('unknown')
    return 'conflict' if 'conflict' in states else 'unknown' if 'unknown' in states else 'matched'


_ABSENT = object()


def mixed_state(actual, before, after):
    """A partial commit may contain only original-before or intended-after values."""
    if actual == before or actual == after:
        return 'matched'
    if actual is _ABSENT:
        return 'unknown'
    if all(isinstance(value, dict) for value in (actual, before, after)):
        states = [mixed_state(actual.get(key, _ABSENT), before.get(key, _ABSENT), after.get(key, _ABSENT))
                  for key in actual.keys() | before.keys() | after.keys()]
        return 'conflict' if 'conflict' in states else 'unknown' if 'unknown' in states else 'matched'
    if isinstance(actual, dict) and isinstance(after, dict) and before in (None, _ABSENT):
        # A newly created object must be complete before an existing-create can
        # be removed from the remaining request. Missing data is not absence.
        return c.compare_expected(actual, after)
    return 'conflict'


def recovery_projection(doc, node_id):
    value = c.semantic_projection(doc, node_id)
    data = (value.get('asset') or {}).get('content', {})
    data = data['data'] if isinstance(data.get('data'), dict) else data
    generation = data.get('generation')
    if isinstance(generation, dict) and isinstance(generation.get('references'), list):
        generation['references'] = c.semantic_references(generation['references'], doc)
    return value


def partial_request(cli, observation, entry, request):
    """Compile the saved step from its original before, then retain only unfinished work."""
    if observation.get('complete') is not True or not recoverable_command(request) or not hasattr(cli, 'compile_batch'):
        return 'unknown', None
    current = observation['document']
    if any(row['node_id'] in c.nodes(current) and row['node_id'] not in current['assets'] for row in request['expected_nodes']):
        return 'unknown', None
    baseline = copy.deepcopy(current)
    consumers = set(edge_targets(entry, request))
    root = baseline['assets'][baseline['rootCanvasId']]['content']
    root['edges'] = {key: edge for key, edge in root['edges'].items() if edge.get('target') not in consumers}
    root['edges'].update({row['edge_id']: copy.deepcopy(row['value']) for row in request['expected_edges'] if row['value'] is not None})
    for row in request['expected_nodes']:
        node_id = row['node_id']
        if row.get('identity_only'):
            # Source edits are intentionally preserved. Business compilation
            # must resolve current media from this original bound node id.
            continue
        if row['node'] is None:
            root['nodes'].pop(node_id, None)
        else:
            root['nodes'][node_id] = copy.deepcopy(row['node'])
        if row['asset'] is None:
            baseline['assets'].pop(node_id, None)
        else:
            baseline['assets'][node_id] = copy.deepcopy(row['asset'])
    desired = cli.compile_batch(baseline, step_commands(request))['document']
    states = []
    for row in request['expected_nodes']:
        node_id = row['node_id']
        if row.get('identity_only'):
            expected = {'node_id': node_id, 'node': {key: row['node'][key] for key in ('type',) if key in (row.get('node') or {})},
                        'asset': {key: row['asset'][key] for key in ('type', 'pippitAssetId') if key in (row.get('asset') or {})}}
            if node_id not in c.nodes(current) or node_id not in current['assets']:
                states.append('unknown')
            else:
                states.append(c.compare_expected(c.identity_projection(current, node_id), expected))
        elif row.get('source_only'):
            states.append(c.compare_expected(c.semantic_projection(current, node_id, True), c.semantic_projection(baseline, node_id, True)))
        else:
            project = lambda doc: expected_projection(doc, node_id, row) if row.get('reference_identity_only') else recovery_projection(doc, node_id)
            states.append(mixed_state(project(current), project(baseline), project(desired)))
    old_edges = baseline['assets'][baseline['rootCanvasId']]['content']['edges']
    new_edges = desired['assets'][desired['rootCanvasId']]['content']['edges']
    actual_edges = current['assets'][current['rootCanvasId']]['content']['edges']
    if request['name'] == 'xyq.generation.update_prompt':
        # This business handler may allocate a fresh edge UUID on each local
        # compilation. Preserve a previously committed equivalent reference;
        # explicitly supplied create_edge ids never use this normalization.
        identity = lambda edge: tuple(edge.get(key) for key in ('source', 'target', 'type'))
        for edge_id in list(set(new_edges) - set(old_edges)):
            expected = new_edges[edge_id]
            matches = [key for key, actual in actual_edges.items() if key not in old_edges and identity(actual) == identity(expected)]
            if len(matches) > 1:
                return 'conflict', None
            if len(matches) == 1:
                del new_edges[edge_id]
                new_edges[matches[0]] = copy.deepcopy(actual_edges[matches[0]])
    edge_ids = {key for source in (old_edges, new_edges, actual_edges) for key, edge in source.items()
                if edge.get('target') in consumers} | {row['edge_id'] for row in request['expected_edges']}
    states.extend(mixed_state(actual_edges.get(key, _ABSENT), old_edges.get(key, _ABSENT), new_edges.get(key, _ABSENT)) for key in edge_ids)
    state = 'conflict' if 'conflict' in states else 'unknown' if 'unknown' in states else 'matched'
    if state != 'matched':
        return state, None
    derived = copy.deepcopy(request)
    if request['name'] == 'xyq.role.updateAppearance' and isinstance(derived['input'].get('expected'), dict):
        generation = c.content(current, request['input']['nodeId']).get('generation', {})
        for key in derived['input']['expected']:
            if key == 'generation':
                derived['input']['expected'][key] = copy.deepcopy(generation)
            elif key == 'generationPrompt':
                derived['input']['expected'][key] = generation.get('prompt')
            else:
                return 'conflict', None
    if request['name'] == 'apply_mutations':
        remaining = []
        for mutation in derived['input']['mutations']:
            kind = mutation['kind']
            if kind in {'create_node', 'create_biz_node'}:
                node_id = mutation['node']['id'] if kind == 'create_node' else mutation['id']
                if node_id in c.nodes(current):
                    if recovery_projection(current, node_id) != recovery_projection(desired, node_id):
                        return 'unknown', None
                    continue
            if kind == 'create_edge' and actual_edges.get(mutation['edge']['id']) == mutation['edge']:
                continue
            if kind == 'delete_edges':
                mutation['ids'] = [key for key in mutation['ids'] if key in actual_edges]
                if not mutation['ids']:
                    continue
            remaining.append(mutation)
        if not remaining:
            # Final target checks, rather than an empty command, must accept it.
            return 'unknown', None
        derived['input']['mutations'] = remaining
    derived['expected_nodes'] = [{**row, 'node': copy.deepcopy(c.nodes(current).get(row['node_id'])),
                                  'asset': copy.deepcopy(current['assets'].get(row['node_id']))}
                                 for row in request['expected_nodes']]
    derived['expected_edges'] = [{'edge_id': key, 'value': copy.deepcopy(actual_edges.get(key))} for key in sorted(edge_ids)]
    derived['expected_edge_targets'] = sorted(consumers)
    return 'matched', derived


def verified_entry(project, state, entry, doc):
    entry.update(status='verified', after={target['node_id']: c.fingerprint(doc, target['node_id']) for target in entry['targets']})
    entry.pop('readback_status', None)
    for mapping in state['mappings'].values():
        path = mapping.get('video_write_receipt')
        if not path or not Path(path).is_file():
            continue
        receipt = d.read_json(path)
        checks = receipt.get('targets', []) + receipt.get('reference_targets', [])
        if (receipt.get('protocol') == c.BATCH_PROTOCOL and entry['id'] in receipt.get('steps', [])
                and checks and all(c.target_satisfied(doc, target) for target in checks)):
            receipt.update(status='verified', core_verified=True, references_verified=True,
                           checks=[{'action': target['action'], 'matched': True} for target in checks])
            d.write_json(path, receipt)
    c.save(project, state)
    return doc


def acknowledge(entry, cli, name, result):
    entry['status'] = 'acknowledged'
    entry['ack'] = {'command': name, 'result_sha256': c.digest(result)}
    acknowledgement = getattr(cli, 'last_command_result', {})
    if acknowledgement.get('ok') is True:
        entry['ack']['ok'] = True
    if type(acknowledgement.get('revision')) is int:
        entry['ack']['sdk_revision'] = acknowledgement['revision']


def verify_membership(project, state, entry, request, observation, preview=False):
    if not request:
        return
    doc = observation['document']
    missing = sorted(row['node_id'] for row in request['expected_nodes']
                     if row['node'] is not None and row['node_id'] not in c.nodes(doc))
    if not missing:
        entry.pop('missing_nodes_observation', None)
        return
    version = observation.get('asset_versions', {}).get(doc['rootCanvasId'])
    previous = entry.get('missing_nodes_observation', {})
    complete = observation.get('complete') is True and type(version) is int
    count = previous.get('count', 0) + 1 if complete and previous.get('ids') == missing and previous.get('root_version') == version else 1
    if complete:
        entry['missing_nodes_observation'] = {'ids': missing, 'root_version': version, 'count': count}
        if not preview:
            c.save(project, state)
    if complete and count >= 2:
        c.fail('CONCURRENT_EDIT', '连续完整回读确认原节点已不在画布；保留原ID，不自动重建。', operation=entry['id'], node_ids=missing)
    c.fail('READBACK_PENDING', '原节点暂未出现在根图，继续核对同一画布版本和节点ID。', operation=entry['id'])


def reference_drift(doc, targets):
    """Return stale consumers only after every owned field and source identity matches."""
    from canvas_asset_reference_policy import reference_media_satisfied
    consumers = set()
    for target in targets:
        identity = copy.deepcopy(target)
        related = identity.pop('consumers', []) if target['action'] == 'bound_asset_identity' else []
        if not c.target_satisfied(doc, identity):
            return None
        consumers.update(node_id for node_id in related if not reference_media_satisfied(doc, target['node_id'], node_id))
    return consumers


def refresh_reference_drift(project, cli, state, entry, doc):
    """Complete committed nodes with bounded, separately journaled reference steps.

    A source may change after fixed create_node payloads were compiled. Never
    replay those creates: persist official stale-edge removal/prompt refresh
    steps, each with its own durable guard and operation id. The parent counts
    complete only after its final actual media identities pass.
    """
    from canvas_asset_reference_policy import reference_media_satisfied
    refresh = entry.setdefault('reference_refresh', {'plans': []})
    all_ids = sorted({target['node_id'] for target in entry['targets']})
    source_ids = {target['node_id'] for target in entry['targets'] if target['action'] == 'bound_asset_identity'}
    owned = []
    for target in entry['targets']:
        target = copy.deepcopy(target)
        if target['action'] == 'bound_asset_identity':
            target.pop('consumers', None)
        owned.append(target)
    for _ in range(2):
        plan = refresh['plans'][-1] if refresh['plans'] and refresh['plans'][-1]['status'] == 'running' else None
        if plan is None:
            consumers = reference_drift(doc, entry['targets'])
            if consumers is None:
                c.fail('CONCURRENT_EDIT', '引用刷新前目标内容或来源身份已改变，保留现有内容。', operation=entry['id'])
            if not consumers:
                refresh['status'] = 'verified'
                return verified_entry(project, state, entry, doc)
            edges = doc['assets'][doc['rootCanvasId']]['content']['edges']
            stale = sorted(edge_id for edge_id, edge in edges.items() if edge.get('target') in consumers
                and edge.get('source') in source_ids and not reference_media_satisfied(doc, edge['source'], edge['target']))
            commands = []
            for node_id in sorted(consumers):
                generation = c.content(doc, node_id).get('generation', {})
                references = generation.get('references', [])
                kept = []
                for reference in references:
                    source = next((reference.get(key) for key in ('sourceNodeId', 'nodeAssetId', 'id')
                                   if reference.get(key) in source_ids), None)
                    valid = True
                    if source:
                        single = copy.deepcopy(doc)
                        c.content(single, node_id)['generation']['references'] = [reference]
                        valid = reference_media_satisfied(single, source, node_id)
                    if valid:
                        kept.append(reference)
                if kept != references:
                    # update_prompt preserves a saved row for the same source.
                    # Remove only media rows no longer belonging to that source
                    # under a strict current-target guard, then let the official
                    # business handler select its current real media identities.
                    commands.append(command('update_node_data', {'id': node_id,
                        'patch': {'generation': {**copy.deepcopy(generation), 'references': kept}}}))
            if stale:
                commands.append(command('delete_edges', {'ids': stale}))
            commands.extend(command('xyq.generation.update_prompt', {'nodeId': node_id, 'prompt': c.prompt_of(doc, node_id)})
                            for node_id in sorted(consumers))
            compiled = cli.compile_batch(doc, commands)
            if not compiled['patches']:
                c.fail('REFERENCE_REFRESH_UNSUPPORTED', '当前来源媒体不能由官方提示词刷新命令重新关联；未重建节点。', operation=entry['id'])
            steps = semantic_steps({'document': doc}, compiled)
            plan = {'status': 'running', 'steps': [], 'operations': []}
            current = doc
            for index, step in enumerate(steps):
                expected = cli.compile_batch(current, step_commands(step))['document']
                checks = copy.deepcopy(owned) if index == len(steps) - 1 else intermediate_targets(current, expected)
                if step['name'] == 'apply_mutations':
                    checks.extend({'action': 'semantic_generation_settings', 'node_id': mutation['id'],
                                   'value': {'references': copy.deepcopy(c.content(expected, mutation['id']).get('generation', {}).get('references', []))}}
                                  for mutation in step['input']['mutations'] if mutation['kind'] == 'update_node_data'
                                  and 'references' in mutation.get('patch', {}).get('generation', {}))
                checked = {target['node_id'] for target in checks}
                checks.extend({'action': 'node_kind', 'node_id': node_id, 'kind': c.kind_of(c.nodes(expected)[node_id])}
                              for node_id in all_ids if node_id not in checked and node_id in c.nodes(expected))
                current_edges = current['assets'][current['rootCanvasId']]['content']['edges']
                guarded_consumers = {step['input']['nodeId']} if step['name'] == 'xyq.generation.update_prompt' else consumers
                plan['steps'].append({'step': step, 'targets': checks,
                    'expected_nodes': [{'node_id': node_id, 'node': copy.deepcopy(c.nodes(current).get(node_id)),
                        'asset': copy.deepcopy(current['assets'].get(node_id)), 'identity_only': node_id in source_ids,
                        'reference_identity_only': step['name'] == 'xyq.generation.update_prompt' and node_id not in source_ids}
                        for node_id in all_ids],
                    'expected_edges': [{'edge_id': edge_id, 'value': copy.deepcopy(edge)} for edge_id, edge in current_edges.items()
                                       if edge.get('target') in guarded_consumers],
                    'expected_edge_targets': sorted(guarded_consumers)})
                current = expected
            plan['sha256'] = c.digest(plan['steps'])
            plan['runtime_sha256'] = compiled['runtime_sha256']
            refresh['plans'].append(plan)
            refresh['status'] = 'running'
            c.save(project, state)
        if c.digest(plan['steps']) != plan.get('sha256'):
            c.fail('RECOVERY_RECORD_INVALID', '引用刷新计划完整性核验失败。', operation=entry['id'])
        for index, saved in enumerate(plan['steps']):
            if index < len(plan['operations']):
                child = next((row for row in state['journal'] if row['id'] == plan['operations'][index]), None)
                if child is None:
                    c.fail('RECOVERY_RECORD_INVALID', '引用刷新步骤回执缺失。', operation=entry['id'])
                expected_request = {**saved['step'], 'expected_nodes': saved['expected_nodes'],
                                    'expected_edges': saved['expected_edges'], 'expected_edge_targets': saved['expected_edge_targets']}
                if (child.get('reference_refresh_parent') != entry['id']
                        or child.get('input', {}).get('request_sha256') != c.digest(expected_request)):
                    c.fail('RECOVERY_RECORD_INVALID', '引用刷新子步骤与原计划不一致。', operation=entry['id'])
                if child['status'] != 'verified':
                    doc = recover_entry(project, cli, state, child)
            else:
                child = submit_step(project, cli, state, saved['step'], saved['targets'], (), plan['runtime_sha256'],
                    saved['expected_nodes'], saved['expected_edges'], saved['expected_edge_targets'], parent_refresh=(entry, plan))
                doc = readback(project, cli, state, child, saved['targets'])
        # A resumed plan can contain only already verified children. Read the
        # current source rows once before accepting their parent receipt.
        doc = cli.batch_snapshot(state['binding'], node_ids=all_ids)['document']
        plan['status'] = 'verified'
        c.save(project, state)
        if all(c.target_satisfied(doc, target) for target in entry['targets']):
            refresh['status'] = 'verified'
            return verified_entry(project, state, entry, doc)
    c.fail('READBACK_PENDING', '来源媒体仍在变化；已保存引用刷新步骤，将继续使用原节点自动核验。', operation=entry['id'])


def recover_entry(project, cli, state, entry, preview=False, observation=None):
    """Resolve one original step, then safely retry only its unchanged before state."""
    request = recovery_request(project, entry)
    ids = {target['node_id'] for target in entry['targets']}
    if request:
        ids.update(row['node_id'] for row in request.get('expected_nodes', []))
    boundary(cli)
    if observation is None:
        observation = cli.batch_snapshot(state['binding'], node_ids=sorted(ids))
    doc = observation['document']
    running_refresh = bool(entry.get('reference_refresh', {}).get('plans') and entry['reference_refresh']['plans'][-1]['status'] == 'running')
    if not preview and running_refresh:
        # An in-flight stale-edge removal can still arrive after this snapshot.
        # Resolve every dispatched child before the parent may count complete,
        # even if the source has changed back and currently looks acceptable.
        verify_membership(project, state, entry, request, observation)
        return refresh_reference_drift(project, cli, state, entry, doc)
    after = [c.target_state(doc, target) for target in entry['targets']]
    if all(value == 'matched' for value in after):
        return doc if preview else verified_entry(project, state, entry, doc)
    if request and not recoverable_command(request):
        c.fail('RECOVERY_UNSUPPORTED', '该原请求不属于已核验的确定性命令，不能自动补做。', operation=entry['id'])
    verify_membership(project, state, entry, request, observation, preview)
    if not preview and reference_drift(doc, entry['targets']):
        return refresh_reference_drift(project, cli, state, entry, doc)
    before = before_state(observation, entry, request) if request else 'unknown'
    active_request = request
    if before != 'matched' and request and recoverable_command(request):
        before, active_request = partial_request(cli, observation, entry, request)
    if before == 'unknown':
        c.fail('READBACK_PENDING', '原批次尚缺完整核验结果，将继续自动读取。', operation=entry['id'])
    if before == 'conflict':
        c.fail('CONCURRENT_EDIT', '原节点或引用已发生新变化，不能覆盖；原批次和节点ID已保留。', operation=entry['id'])
    if preview or not active_request or not recoverable_command(active_request) or not hasattr(cli, 'command_guarded'):
        c.fail('UNCERTAIN_WRITE', '原请求尚未完成，当前命令不满足自动补做条件。', operation=entry['id'])
    # Journal the attempt before transport. The request file, operation id and
    # all node/edge identities remain unchanged across every retry.
    entry['recovery_attempts'] = entry.get('recovery_attempts', 0) + 1
    entry['recovery_before_versions'] = observation.get('asset_versions', {})
    entry['resolution'] = 'retry_unchanged_before' if active_request == request else 'continue_partial_commit'
    if active_request != request:
        path = Path(project) / 'canvas' / 'batches' / (entry['id'] + '.recovery-' + str(entry['recovery_attempts']) + '.json')
        d.write_json(path, {'protocol': c.BATCH_PROTOCOL, 'operation_id': entry['id'], 'request': active_request,
                           'original_request_sha256': entry['input']['request_sha256']})
        entry['recovery_request_path'] = str(path)
        entry['recovery_request_sha256'] = c.digest(active_request)
    c.save(project, state)
    try:
        result = cli.command_guarded(state['binding']['canvas_id'], active_request['name'], active_request['input'],
            active_request['expected_nodes'], active_request['expected_edges'], edge_targets(entry, active_request), entry['id'])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        entry['recovery_error'] = c.safe_message(exc)
        entry['recovery_not_sent'] = getattr(exc, 'details', {}).get('not_sent') is True
        c.save(project, state)
        # A late first ACK can make the second guard reject. Resolve original
        # targets before diagnosing a real external edit or scheduling a retry.
        try:
            observation = cli.batch_snapshot(state['binding'], node_ids=sorted(ids))
        except (ValueError, OSError, KeyError, TypeError):
            if not retryable_write_error(exc):
                raise exc
            raise
        doc = observation['document']
        if all(c.target_satisfied(doc, target) for target in entry['targets']):
            return verified_entry(project, state, entry, doc)
        if not retryable_write_error(exc):
            raise
        verify_membership(project, state, entry, request, observation)
        observed_before = before_state(observation, entry, request)
        if observed_before != 'matched':
            observed_before, _ = partial_request(cli, observation, entry, request)
        if observed_before == 'conflict':
            c.fail('CONCURRENT_EDIT', '自动恢复时发现新的节点或引用内容，已保护现有内容。', operation=entry['id'])
        c.fail('READBACK_PENDING', '原批次尚未完成，将继续自动恢复同一请求和节点ID。', operation=entry['id'])
    acknowledge(entry, cli, active_request['name'], result)
    c.save(project, state)
    return readback(project, cli, state, entry, entry['targets'], recover=False)


def command(name, value):
    return {"name": name, "input": copy.deepcopy(value)}


def reference_edges(doc, node_id):
    return sorted((copy.deepcopy(e) for e in doc["assets"][doc["rootCanvasId"]]["content"].get("edges", {}).values()
                   if e.get("target") == node_id and e.get("type") == "reference"), key=lambda e: e["id"])


def exact_targets(doc, node_id, reference_identity_only=False):
    generation = copy.deepcopy(c.content(doc, node_id).get('generation') or {})
    if reference_identity_only:
        generation.pop('references', None)
    return [{"action": "semantic_generation_settings", "node_id": node_id,
             "value": generation},
            {"action": "semantic_reference_graph", "node_id": node_id, "value": reference_edges(doc, node_id)}]


def boundary(cli):
    callback = getattr(getattr(cli, "check_control", None), "boundary", None)
    callback() if callable(callback) else c.check_control(cli)


def snapshot(cli, state, previous, items, sources=(), asset_reference_policy=None, source_identity_only=False):
    boundary(cli)
    value = cli.batch_snapshot(state["binding"])
    fresh = value["document"]
    targets = {i.get('node_id') for i in items}
    from canvas_asset_reference_policy import source_target
    for node_id in targets | set(sources):
        if node_id and node_id not in targets:
            identity = source_target(asset_reference_policy, previous, node_id, identity_only=source_identity_only)
            if identity['action'] == 'bound_asset_identity':
                if not c.target_satisfied(fresh, identity):
                    c.fail('CONCURRENT_EDIT', '引用资产原节点已消失或类型改变，不能按名称替代。', node_id=node_id)
                continue
        if node_id and (node_id not in c.nodes(fresh) or node_id not in c.nodes(previous) or node_id not in fresh['assets']):
            c.fail('READBACK_PENDING', '批次中的目标或引用节点尚未完整返回。', node_id=node_id)
        if node_id and node_id in previous['assets']:
            source_only = node_id not in targets
            observation = c.compare_expected(c.semantic_projection(fresh, node_id, source_only),
                                             c.semantic_projection(previous, node_id, source_only))
            if observation != 'matched':
                c.fail('READBACK_PENDING' if observation == 'unknown' else 'CONCURRENT_EDIT',
                       '批次中的目标或引用资产需重新核验。', node_id=node_id)
    for item in items:
        if not item.get("node_id") and any(c.kind_of(n) == item["kind"] and c.name_of(fresh, nid) == item["name"]
                                           for nid, n in c.nodes(fresh).items()):
            c.fail("CONCURRENT_EDIT", "批量创建前出现同名节点，停止写入。", key=item["key"])
    return value


def reserve(project, cli, state, items):
    missing = [i for i in items if not i.get("node_id") and not i.get("allocated_id")]
    if missing:
        c.check_control(cli)
        for item, node_id in zip(missing, cli.allocate_many(len(missing))):
            item["allocated_id"] = node_id
            state["mappings"][item["key"]] = {"node_id": node_id, "name": item["name"],
                                               "kind": item["kind"], "stage": "allocated"}
        c.save(project, state)
    ids = [i.get("node_id") or i["allocated_id"] for i in items]
    d.require(len(set(ids)) == len(ids), "一个批次不能重复写入同一节点")
    return ids


def semantic_steps(baseline, compiled):
    """Use public commands; SDK patches are only an offline change detector."""
    before, after = baseline["document"], compiled["document"]
    created = set(c.nodes(after)) - set(c.nodes(before))
    changed = {p["assetId"] for p in compiled["patches"]} - {before["rootCanvasId"]}
    if created and changed <= created:
        # The official factories and domain commands have already constructed
        # these new nodes. create_node accepts that official node/asset shape,
        # preserving business display names as well as the asset content.
        mutations = [{"kind": "create_node", "node": copy.deepcopy(c.nodes(after)[nid]),
                      "assets": [copy.deepcopy(after["assets"][nid])], "autoCreateNodeAsset": False}
                     for nid in c.nodes(after) if nid in created]
        old_edges = before["assets"][before["rootCanvasId"]]["content"].get("edges", {})
        new_edges = after["assets"][after["rootCanvasId"]]["content"].get("edges", {})
        d.require(all(new_edges.get(key) == edge for key, edge in old_edges.items()),
                  "新建批次不得修改已有引用边")
        mutations.extend({"kind": "create_edge", "edge": copy.deepcopy(edge)}
                         for key, edge in new_edges.items() if key not in old_edges)
        return [command("apply_mutations", {"atomic": True, "intent": "Seedance 创建已编译的新节点及引用", "mutations": mutations})]
    steps, ordinary = [], []
    for cmd in compiled["commands"]:
        if cmd["name"] in ORDINARY_COMMANDS:
            ordinary.append({"kind": cmd["name"], **copy.deepcopy(cmd["input"])})
            continue
        if ordinary:
            steps.append(command("apply_mutations", {"atomic": True, "intent": "Seedance 节点字段更新", "mutations": ordinary}))
            ordinary = []
        steps.append(copy.deepcopy(cmd))
    if ordinary:
        steps.append(command("apply_mutations", {"atomic": True, "intent": "Seedance 节点字段更新", "mutations": ordinary}))
    return steps


def step_commands(step):
    if step["name"] == "apply_mutations":
        return [command(value["kind"], {key: val for key, val in value.items() if key != "kind"})
                for value in step["input"]["mutations"]]
    return [step]


def intermediate_targets(before, after):
    targets = []
    for node_id in c.nodes(after):
        if (c.fingerprint(before, node_id) == c.fingerprint(after, node_id)
                and reference_edges(before, node_id) == reference_edges(after, node_id)):
            continue
        old, new = c.content(before, node_id), c.content(after, node_id)
        targets.append({"action": "node_kind", "node_id": node_id, "kind": c.kind_of(c.nodes(after)[node_id])})
        targets.extend(exact_targets(after, node_id, reference_identity_only=c.kind_of(c.nodes(after)[node_id]) == 'video'))
        fields = {key: copy.deepcopy(value) for key, value in new.items() if key != "generation" and old.get(key) != value}
        if fields:
            targets.append({"action": "patch", "node_id": node_id, "patch": fields})
    return targets


def submit(project, cli, state, baseline, compiled, targets, receipts=(), asset_reference_policy=None, source_identity_only=False):
    """Save complete requests and immutable identities before each public step."""
    patches = compiled["patches"]
    policy = asset_reference_policy
    if policy is not None:
        # A reference-only choice cannot smuggle any asset field mutation through
        # an SDK patch, including source node metadata stored in the root asset.
        for row in policy['nodes'].values():
            nid = row['node_id']
            d.require(c.fingerprint(compiled['document'], nid) == c.fingerprint(baseline['document'], nid),
                      '按绑定ID引用不得编译任何资产字段写入：' + nid)
    # Source selection changes after compilation must not silently validate an
    # older plan. Sources without media are valid and still receive real edges.
    touched = {t["node_id"] for t in targets}
    sources = {sid for nid in touched for sid in c.node_asset_ids(c.prompt_of(compiled["document"], nid))}
    sources.update(e["source"] for nid in touched for e in reference_edges(compiled["document"], nid))
    from canvas_asset_reference_policy import source_target
    for sid in sources - touched:
        target = source_target(policy, compiled['document'], sid, identity_only=source_identity_only)
        if target['action'] == 'asset_snapshot':
            target = {'action': 'semantic_asset_snapshot', 'node_id': sid,
                      'value': c.source_fingerprint(compiled['document'], sid),
                      'projection': 'source-business-v1',
                      'expected': c.semantic_projection(compiled['document'], sid, source_only=True)}
        if target['action'] == 'bound_asset_identity':
            target['consumers'] = sorted(nid for nid in touched if sid in c.node_asset_ids(c.prompt_of(compiled['document'], nid))
                or any(e['source'] == sid for e in reference_edges(compiled['document'], nid)))
        targets.append(target)
    if not patches:
        return None
    c.check_control(cli)
    doc = baseline["document"]
    steps = semantic_steps(baseline, compiled)
    current = doc
    entry = None
    source_only_ids = {target['node_id'] for target in targets
                       if target['action'] in {'semantic_asset_snapshot', 'bound_asset_identity'}} - touched
    identity_only_ids = {target['node_id'] for target in targets if target['action'] == 'bound_asset_identity'} - touched
    for index, step in enumerate(steps):
        final = index == len(steps) - 1
        # The public runtime checks these saved expectations immediately before
        # dispatch, in the same session that performs the operation.
        expected = compiled["document"] if final else cli.compile_batch(current, step_commands(step))["document"]
        checks = targets if final else intermediate_targets(current, expected)
        if not final:
            for target in targets:
                if target['action'] in {'semantic_asset_snapshot', 'bound_asset_identity'}:
                    source_check = copy.deepcopy(target)
                    # Media rows for later business steps are not expected yet.
                    # Identity is checked here; every consumer is checked after
                    # the final step has resolved the current sources.
                    source_check.pop('consumers', None)
                    checks.append(source_check)
            checked_ids = {target['node_id'] for target in checks}
            checks.extend({'action': 'node_kind', 'node_id': node_id, 'kind': c.kind_of(c.nodes(expected)[node_id])}
                          for node_id in {target['node_id'] for target in targets} - checked_ids
                          if node_id in c.nodes(expected))
        expected_nodes = [{'node_id': node_id, 'node': copy.deepcopy(c.nodes(current).get(node_id)),
                           'asset': copy.deepcopy(current['assets'].get(node_id)), 'source_only': node_id in source_only_ids,
                           'identity_only': node_id in identity_only_ids,
                           'reference_identity_only': step['name'] == 'xyq.generation.update_prompt'
                               and node_id not in source_only_ids and c.kind_of(c.nodes(current).get(node_id, {})) == 'video'}
                          for node_id in sorted({target['node_id'] for target in targets})]
        current_edges = current['assets'][current['rootCanvasId']]['content'].get('edges', {})
        consumers = {target['node_id'] for target in targets
                     if target['action'] not in {'semantic_asset_snapshot', 'bound_asset_identity'}}
        guarded_edges = {edge_id for edge_id, edge in current_edges.items() if edge.get('target') in consumers}
        if step['name'] == 'apply_mutations':
            guarded_edges.update(mutation['edge']['id'] for mutation in step['input']['mutations']
                                 if mutation['kind'] == 'create_edge')
        expected_edges = [{'edge_id': edge_id, 'value': copy.deepcopy(current_edges.get(edge_id))}
                          for edge_id in sorted(guarded_edges)]
        entry = submit_step(project, cli, state, step, checks, receipts, compiled["runtime_sha256"], expected_nodes, expected_edges, sorted(consumers))
        if not final:
            current = readback(project, cli, state, entry, checks)
    return entry


def submit_step(project, cli, state, step, targets, receipts, runtime_sha256, expected_nodes, expected_edges, expected_edge_targets=None, parent_refresh=None):
    operation = uuid.uuid4().hex
    request = {**step, 'expected_nodes': expected_nodes, 'expected_edges': expected_edges,
               'expected_edge_targets': expected_edge_targets or []}
    d.require(len(d.packed(request).encode("utf-8")) <= 64 * 1024 * 1024, "批量事务超过CLI输入限制，尚未发送")
    path = Path(project) / "canvas" / "batches" / (operation + ".json")
    d.write_json(path, {"protocol": c.BATCH_PROTOCOL, "request": request, "runtime_sha256": runtime_sha256})
    entry = {"id": operation, "command": "canvas_batch", "protocol": c.BATCH_PROTOCOL,
             "input": {"request_path": str(path), "request_sha256": c.digest(request)},
             "targets": copy.deepcopy(targets), "status": "pending"}
    if parent_refresh is not None:
        parent, plan = parent_refresh
        plan['operations'].append(operation)
        entry['reference_refresh_parent'] = parent['id']
    previous_mappings = c.read_state(project)['mappings']
    for receipt in receipts:
        receipt["steps"].append(operation)
        receipt["batch_operation_id"] = operation
        d.write_json(receipt["path"], receipt)
    state["journal"].append(entry)
    c.save(project, state)
    try:
        if hasattr(cli, 'command_guarded'):
            result = cli.command_guarded(state["binding"]["canvas_id"], step["name"], step["input"], expected_nodes, expected_edges, expected_edge_targets or [], operation)
        else:
            result = cli.command(state["binding"]["canvas_id"], step["name"], step["input"])
    except BaseException as exc:
        # Queue control exceptions are raised before transport. Unlike timeouts,
        # these are known not-sent requests and must not poison future resume.
        from canvas_jobs import PauseRequested, Superseded
        if isinstance(exc, (PauseRequested, Superseded)):
            entry.update(status="cancelled", resolution="control_before_send")
            for mapping in state["mappings"].values():
                if mapping.get("node_id") in {t["node_id"] for t in targets}:
                    mapping["stage"] = "allocated" if step["name"] == "apply_mutations" and any(
                        m.get("node", {}).get("id") == mapping["node_id"] for m in step["input"]["mutations"]) else "writing"
            c.save(project, state)
            raise
        if not isinstance(exc, (ValueError, OSError, KeyError, TypeError)):
            # A killed process/unknown failure leaves the durable pending entry.
            raise
        if getattr(exc, 'details', {}).get('not_sent') is True:
            entry.update(status='rejected', error=c.safe_message(exc), not_sent=True)
            for key, mapping in state['mappings'].items():
                if mapping.get('node_id') in {target['node_id'] for target in targets} and key in previous_mappings:
                    state['mappings'][key] = previous_mappings[key]
            for receipt in receipts:
                receipt.update(status='rejected', error=c.safe_message(exc))
                d.write_json(receipt['path'], receipt)
            c.save(project, state)
            raise
        entry.update(status="uncertain", error=c.safe_message(exc))
        c.save(project, state)
        if not retryable_write_error(exc):
            # A definite rejection is not an endless retry condition. A lost
            # late ACK can still be accepted by one strictly read-only check.
            try:
                observed = cli.batch_snapshot(state['binding'], node_ids=sorted({target['node_id'] for target in targets}))
                if all(c.target_satisfied(observed['document'], target) for target in targets):
                    verified_entry(project, state, entry, observed['document'])
                    return entry
            except (ValueError, OSError, KeyError, TypeError):
                pass
            raise
        # Resolve the original IDs first. A complete unchanged before snapshot
        # may safely admit the original deterministic request through the guard.
        try:
            readback(project, cli, state, entry, targets)
            return entry
        except c.CanvasError as recovery_error:
            if recovery_error.code in {'CONCURRENT_EDIT', 'READBACK_PENDING', 'RECOVERY_RECORD_INVALID'}:
                raise
            c.fail("UNCERTAIN_WRITE", "批量命令结果仍待核对；保留原ID与请求。" + c.safe_message(exc), operation=operation)
    acknowledge(entry, cli, step['name'], result)
    c.save(project, state)
    return entry


def readback(project, cli, state, entry, targets, recover=True):
    observations = []
    node_ids = sorted({t["node_id"] for t in targets})
    for attempt in range(c.VIDEO_READBACK_ATTEMPTS):
        boundary(cli)
        try:
            observed = cli.batch_snapshot(state["binding"], node_ids=node_ids)
            doc = observed['document']
        except c.CanvasError as exc:
            if exc.code not in {"CLI_TIMEOUT", "CLI_ERROR", "CLI_TRANSIENT", "READBACK_INCOMPLETE", "READBACK_PENDING", "NETWORK_ERROR"}:
                raise
            if entry:
                entry["readback_error"] = c.safe_message(exc)
                c.save(project, state)
            if exc.code == 'READBACK_PENDING':
                # The native read layer has already consumed its retry budget.
                raise
            if attempt + 1 == c.VIDEO_READBACK_ATTEMPTS:
                c.fail("READBACK_PENDING", "写入后的只读查询暂不可用；已保存原ID与提交状态。")
            time.sleep(c.VIDEO_READBACK_DELAY * (2 ** attempt))
            continue
        observations = [c.target_state(doc, t) for t in targets]
        if all(value == "matched" for value in observations):
            if entry and entry.get('reference_refresh', {}).get('plans') and entry['reference_refresh']['plans'][-1]['status'] == 'running':
                return recover_entry(project, cli, state, entry, observation=observed)
            return verified_entry(project, state, entry, doc) if entry else doc
        if attempt + 1 < c.VIDEO_READBACK_ATTEMPTS:
            c.check_control(cli)
            time.sleep(c.VIDEO_READBACK_DELAY * (2 ** attempt))
    if entry:
        entry.update(readback_status="pending" if "unknown" in observations else "conflict",
                     error="批次字段或引用仍待核验" if "unknown" in observations else "批次字段或引用回读差异")
        c.save(project, state)
        if recover and entry.get('protocol') == c.BATCH_PROTOCOL and hasattr(cli, 'command_guarded'):
            return recover_entry(project, cli, state, entry, observation=observed)
        if not recover and entry.get('protocol') == c.BATCH_PROTOCOL:
            request = recovery_request(project, entry)
            verify_membership(project, state, entry, request, observed)
            before = before_state(observed, entry, request) if request else 'unknown'
            if before != 'matched' and request and recoverable_command(request):
                before, _ = partial_request(cli, observed, entry, request)
            c.fail('CONCURRENT_EDIT' if before == 'conflict' else 'READBACK_PENDING',
                   '恢复后的原批次仍需核验；保留同一事务及节点ID。', operation=entry['id'])
    c.fail("READBACK_PENDING" if "unknown" in observations else "READBACK_FAILED", "批次最终字段或引用未全部通过核验；保留原事务和节点，停止续写。",
           operation=entry["id"] if entry else "")


def write_asset_group(project, cli, state, doc, items, positions):
    sources = {s["node_id"] for i in items for s in i.get("image_reference_sources", [])}
    baseline = snapshot(cli, state, doc, items, sources)
    doc = baseline["document"]
    ids = reserve(project, cli, state, items)
    creates = [command("create_biz_node", {"nodeKind": i["kind"], "id": nid,
                "initialData": c.initial_data(i), "topLeft": positions[i["key"]]})
               for i, nid in zip(items, ids) if not i.get("node_id")]
    staged = cli.compile_batch(doc, creates)["document"] if creates else doc
    commands = list(creates)
    # Factory node display names may differ from initial asset content. Initialize
    # every dependency's business name before resolving any derived reference.
    for item, node_id in zip(items, ids):
        for name, value, _ in c.asset_initialization_steps(staged, item, node_id):
            commands.append(command(name, value))
    if commands:
        staged = cli.compile_batch(doc, commands)["document"]
    targets = []
    for item, node_id in zip(items, ids):
        item.update(c.resolve_asset_image_item(item, state, staged))
        for name, value, _ in c.asset_initialization_steps(staged, item, node_id):
            commands.append(command(name, value))
        targets.extend(c.asset_targets(item, node_id))
        if item.get("verify_image_defaults"):
            targets.append(c.asset_settings_target(node_id))
    compiled = cli.compile_batch(doc, commands)
    # Compile derived-asset reference inputs after all IDs exist locally. The
    # same pure contract is used by the sequential historical execution path.
    input_plans = []
    for item, node_id in zip(items, ids):
        if not item.get("image_reference_assets"):
            continue
        original = dict(item, node_id=node_id)
        local_state = copy.deepcopy(state)
        local_state["mappings"].setdefault(item["key"], {}).update(node_id=node_id,
            prompt_hash=c.digest(c.prompt_of(compiled["document"], node_id)))
        plan = c.asset_image_reference_plan(original, local_state, compiled["document"])
        input_plans.append(plan)
        commands.extend(plan["commands"])
        targets.extend(plan["targets"])
    if input_plans:
        compiled = cli.compile_batch(doc, commands)
    for item, node_id in zip(items, ids):
        targets.extend(exact_targets(compiled["document"], node_id))
        state["mappings"].setdefault(item["key"], {}).update(node_id=node_id, kind=item["kind"],
            name=item["name"], stage="writing", batch_initialization=True)
    entry = submit(project, cli, state, baseline, compiled, targets)
    actual = readback(project, cli, state, entry, targets)
    for item, node_id in zip(items, ids):
        item["node_id"] = node_id
        state["mappings"][item["key"]].update(stage="ready", appearance_name="",
            prompt_hash=c.digest(c.prompt_of(actual, node_id)), asset_contract=c.ASSET_CONTRACT)
        state["mappings"][item["key"]].pop("batch_initialization", None)
    for plan in input_plans:
        state["mappings"][plan["key"]]["image_input"] = plan["image_input"]
    c.save(project, state)
    return actual


def sync_asset_inputs(project, cli, session, state, doc, keys=None, batch_size=DEFAULT_BATCH_SIZE):
    items = [i for i in c.desired_assets(session) if i.get("image_reference_assets") and (keys is None or i["key"] in keys)]
    for offset in range(0, len(items), batch_size):
        group = items[offset:offset + batch_size]
        for item in group:
            item["node_id"] = state["mappings"].get(item["key"], {}).get("node_id")
        sources = {state["mappings"].get(r["key"], {}).get("node_id") for i in group for r in i["image_reference_assets"]}
        baseline = snapshot(cli, state, doc, group, sources - {None})
        plans = [c.asset_image_reference_plan(i, state, baseline["document"]) for i in group]
        commands = [cmd for p in plans for cmd in p["commands"]]
        compiled = cli.compile_batch(baseline["document"], commands)
        targets = [t for i in group for t in exact_targets(compiled["document"], i["node_id"])]
        targets.extend(t for p in plans for t in p["targets"])
        for plan in plans:
            state["mappings"][plan["key"]]["image_input"] = {**plan["image_input"], "status": "pending"}
        entry = submit(project, cli, state, baseline, compiled, targets)
        doc = readback(project, cli, state, entry, targets)
        for plan in plans:
            mapping = state["mappings"][plan["key"]]
            mapping.update(image_input=plan["image_input"], prompt_hash=c.digest(c.prompt_of(doc, plan["node_id"])))
        c.save(project, state)
    return doc


def sync_asset_groups(project, cli, session, state, doc, pending, completed, node_ids, progress, batch_size):
    positions = c.layout_positions(doc, pending)
    created = sum(not i.get("node_id") for i in pending)
    for offset in range(0, len(pending), batch_size):
        group = pending[offset:offset + batch_size]
        doc = write_asset_group(project, cli, state, doc, group, positions)
        for item in group:
            completed.append(item["key"])
            node_ids[item["key"]] = item["node_id"]
        progress({"stage": "batch_verified", "completed": len(completed), "node_ids": copy.deepcopy(node_ids)})
    doc = sync_asset_inputs(project, cli, session, state, doc)
    return {"status": "synced", "count": len(completed), "created": created, "reused": len(completed) - created,
            "sync_protocol": c.BATCH_PROTOCOL, "image_inputs": c.asset_image_input_status(state)}


def prepare_assets(project, cli, session, state, doc, plan, summary):
    pending = []
    for item in plan:
        key, node_id = item["key"], item.get("node_id")
        if item.get('preserve_bound_asset') and node_id in c.nodes(doc):
            state['mappings'].setdefault(key, {}).update(node_id=node_id, kind=item['kind'], origin=item['resolution'])
            summary['reused'].append(key)
        elif item["resolution"] == "create" or state["mappings"].get(key, {}).get("stage") in ("allocated", "writing"):
            pending.append(dict(item, verify_image_defaults=True))
        else:
            state["mappings"].setdefault(key, {}).update(node_id=node_id, kind=item["kind"], name=item["name"],
                stage="ready", origin=item["resolution"])
            summary["adopted" if item["resolution"] == "adopted" else "reused"].append(key)
    positions = c.layout_positions(doc, pending)
    for offset in range(0, len(pending), DEFAULT_BATCH_SIZE):
        group = pending[offset:offset + DEFAULT_BATCH_SIZE]
        doc = write_asset_group(project, cli, state, doc, group, positions)
        summary["created"].extend(i["key"] for i in group)
    keys = {i["key"] for i in pending}
    doc = sync_asset_inputs(project, cli, session, state, doc, keys)
    summary["image_inputs"] = c.asset_image_input_status(state, keys)
    c.save(project, state)
    return doc, summary


def video_matches(doc, item, references_only=False):
    node_id = item.get("node_id")
    if not node_id or node_id not in doc["assets"] or node_id not in c.nodes(doc):
        return False
    checks = [{"action": "node_kind", "node_id": node_id, "kind": "video"},
              {"action": "prompt", "node_id": node_id, "value": item["prompt"]}]
    if not references_only:
        checks.extend([{"action": "patch", "node_id": node_id,
                        "patch": {field: item["name"] for field in ("name", "title", "caption")}},
                       {"action": "generation_settings", "node_id": node_id, "value": item.get("settings", {})}])
    return all(c.target_satisfied(doc, target) for target in checks) and c.video_references_satisfied(doc, node_id, item)


def sync_videos(project, cli, state, doc, plan, pending, context, references_only=False, batch_size=VIDEO_BATCH_SIZE, asset_reference_policy=None):
    policy_args = {'source_identity_only': True,
                   **({'asset_reference_policy': asset_reference_policy} if asset_reference_policy is not None else {})}
    if asset_reference_policy is not None:
        context['asset_reference_policy'] = asset_reference_policy
    positions = c.layout_positions(doc, plan)
    created = updated = reused = 0
    verified = set()
    remaining = []
    for item in plan:
        if video_matches(doc, item, references_only):
            if c.accept_synced_item(project, state, doc, item, pending, context, references_only):
                verified.add(item["key"])
            reused += 1
            # A known unresolved asset/name issue cannot be repaired by writing
            # the same already-matched bytes again or accepting them twice.
            continue
        remaining.append(item)
    plan = remaining
    for offset in range(0, len(plan), batch_size):
        group = plan[offset:offset + batch_size]
        sources = {sid for i in group for sid in c.node_asset_ids(i["prompt"])}
        sources.update(i["black_node_id"] for i in group if c.edge_only_black(i) and i.get("black_node_id"))
        baseline = snapshot(cli, state, doc, group, sources, **policy_args)
        doc = baseline["document"]
        ids = reserve(project, cli, state, group)
        commands, receipts, targets = [], [], []
        for item, node_id in zip(group, ids):
            context["active"] = {"key": item["key"], "node_id": node_id, "kind": "video", "name": item["name"]}
            is_new = not item.get("node_id")
            before = c.content(doc, node_id)
            names = {f: item["name"] for f in ("name", "caption", "title")}
            if is_new:
                commands.append(command("create_biz_node", {"nodeKind": "video", "id": node_id,
                    "initialData": {**names, "generation": copy.deepcopy(item.get("settings", {}))},
                    "topLeft": positions[item["key"]]}))
                created += 1
            else:
                reused += 1
                if not references_only:
                    generation = {**copy.deepcopy(before.get("generation") or {}), **item["settings"]}
                    commands.append(command("update_node_data", {"id": node_id, "patch": {**names, "generation": generation}}))
            # Refresh old edge-only role references using the shared contract.
            stale = c.stale_role_reference_edges(doc, node_id, item["prompt"])
            if stale:
                commands.append(command("delete_edges", {"ids": stale}))
            if c.edge_only_black(item) and item.get("black_node_id"):
                source = item["black_node_id"]
                d.require(c.black_prompt_absent(item["prompt"], source), "独立黑幕不能混入提示词")
                if c.reference_edge(doc, source, node_id) is None:
                    commands.append(command("create_edge", {"edge": {"id": "edge-black-" + uuid.uuid4().hex,
                        "type": "reference", "source": source, "target": node_id, "sourceHandle": "right", "targetHandle": "left"}}))
            commands.append(command("xyq.generation.update_prompt", {"nodeId": node_id, "prompt": item["prompt"]}))
            core = [{"action": "node_kind", "node_id": node_id, "kind": "video"},
                    {"action": "prompt_text", "node_id": node_id, "value": item["prompt"]}]
            if not references_only:
                core += [{"action": "patch", "node_id": node_id, "patch": names},
                         {"action": "generation_settings", "node_id": node_id, "value": item["settings"]}]
            receipt_id = uuid.uuid4().hex
            receipt = {"id": receipt_id, "key": item["key"], "node_id": node_id, "protocol": c.BATCH_PROTOCOL,
                       "status": "writing", "steps": [], "targets": core, "reference_targets": [],
                       "references_only": references_only,
                       "path": str(Path(project) / "canvas" / "video-write-receipts" / (receipt_id + ".json"))}
            receipts.append(receipt)
            targets.extend(core)
            mapping = state["mappings"].setdefault(item["key"], {})
            mapping.update(node_id=node_id, kind="video", name=item["name"], stage="writing",
                           video_receipt_id=receipt_id, video_write_receipt=receipt["path"])
        compiled = cli.compile_batch(doc, commands)
        for item, receipt in zip(group, receipts):
            node_id = receipt["node_id"]
            receipt["reference_targets"] = exact_targets(compiled["document"], node_id, reference_identity_only=True)
            targets.extend(receipt["reference_targets"])
            d.write_json(receipt["path"], receipt)
        entry = submit(project, cli, state, baseline, compiled, targets, receipts, **policy_args)
        try:
            actual = readback(project, cli, state, entry, targets)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            for receipt in receipts:
                receipt.update(status="pending_verification" if entry and entry["status"] in ("acknowledged", "verified") else "uncertain", error=c.safe_message(exc))
                d.write_json(receipt["path"], receipt)
            raise
        for item, receipt in zip(group, receipts):
            item["node_id"] = receipt["node_id"]
            receipt.update(status="verified", core_verified=True, references_verified=True,
                checks=[{"action": t["action"], "matched": c.target_satisfied(actual, t)}
                        for t in receipt["targets"] + receipt["reference_targets"]])
            d.write_json(receipt["path"], receipt)
            context.setdefault("video_write_receipts", []).append(receipt["path"])
            if c.accept_synced_item(project, state, actual, item, pending, context, references_only):
                verified.add(item["key"])
            updated += c.fingerprint(doc, item["node_id"]) != c.fingerprint(actual, item["node_id"])
        doc = actual
        context["doc"] = doc
    context.pop("active", None)
    return doc, {"created": created, "updated": updated, "reused": reused}, verified
