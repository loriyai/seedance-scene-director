"""Opt-in user-authorized asset references by frozen mapped node identity.

This never writes an asset, guesses an identity from a name, or weakens video,
transport ACK, version/CAS or actual reference/media identity verification.
"""
import copy
import hashlib
import json
from pathlib import Path

KEY = 'asset_reference_policy'
POLICY = 'bound-node-id-v1'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def declaration(decisions):
    if KEY not in (decisions or {}):
        return None
    row = decisions[KEY]
    require(isinstance(row, dict) and set(row) == {'policy', 'authorization'}
            and row['policy'] == POLICY, '未知或不完整的资产引用策略')
    ref = row['authorization']
    require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}
            and isinstance(ref['path'], str) and Path(ref['path']).is_absolute()
            and isinstance(ref['sha256'], str) and len(ref['sha256']) == 64
            and all(c in '0123456789abcdef' for c in ref['sha256']), '引用策略须绑定实际绝对授权路径及SHA256')
    require(not any(k.startswith(('CHAR', 'SET', 'PROP')) and value
                    for k, value in decisions.items()), '按映射ID引用不能混入资产重绑/重建/覆盖选择')
    return copy.deepcopy(row)


def authorize(project, binding, row):
    project = Path(project).resolve()
    ref = row['authorization']
    path = Path(ref['path']).resolve()
    require(project in path.parents, '引用策略授权须在本项目内')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], '引用策略授权文件已变化')
    value = json.loads(raw.decode('utf-8-sig'))
    require(value.get('policy') == 'explicit-bound-node-id-reference-choice-v1'
            and value.get('status') == 'user_explicitly_approved'
            and 'asset_reference_by_bound_node_id' in value.get('authorized_operations', [])
            and isinstance(value.get('actual_user_instruction'), str) and value['actual_user_instruction'].strip(),
            '未取得按绑定节点ID引用的真实明确用户选择')
    scope = value.get('scope', {})
    require(isinstance(scope, dict) and Path(scope.get('project', '')).resolve() == project
            and scope.get('canvas_id') == binding.get('canvas_id')
            and scope.get('account_id') == binding.get('account_id')
            and scope.get('team_id') == binding.get('team_id')
            and scope.get('current_and_subsequent_project_asset_references') is True,
            '引用策略授权不属于当前项目/账号/画布/空间')
    flags = ('reuse_valid_bound_node_id', 'preserve_remote_name',
             'preserve_remote_image_and_main_selection',
             'preserve_remote_generation_prompt_and_other_configuration',
             'require_node_exists_in_bound_canvas', 'require_expected_node_kind',
             'do_not_guess_or_replace_missing_bound_id')
    require(all(value.get('chosen_behavior', {}).get(k) is True for k in flags), '引用策略未获完整明确行为授权')


def freeze(project, session, state, decisions, kind):
    row = declaration(decisions)
    if row is None:
        return None
    require(kind in ('videos', 'references'), '按绑定ID策略仅用于资产引用，不用于资产写入/媒体上传任务')
    binding = state.get('binding') or {}
    require(binding.get('account_id') and binding.get('canvas_id'), '引用策略须有明确当前账号/画布绑定')
    authorize(project, binding, row)
    import canvas as c
    nodes, owners = {}, {}
    for item in c.desired_assets(session):
        aid, expected_kind = item['key'], item['kind']
        mapping = state.get('mappings', {}).get(aid, {})
        nid = mapping.get('node_id')
        require(isinstance(nid, str) and nid and mapping.get('kind') == expected_kind,
                '按ID引用须有原有效映射ID及正确类型，不猜测或重建：' + aid)
        require(nid not in owners, '不同资产映射了同一节点，须核实原映射：' + aid)
        owners[nid] = aid
        nodes[aid] = {'node_id': nid, 'kind': expected_kind}
    return {**row, 'binding': copy.deepcopy(binding), 'nodes': nodes}


def verify(project, session, state, decisions, kind, frozen):
    current = freeze(project, session, state, decisions, kind)
    require(current == frozen, '资产引用策略/授权/冻结映射ID或类型已改变；保留原任务核对，不能按名称回退')
    return current


def assert_mappings(policy, state):
    if policy is None:
        return
    require(policy.get('policy') == POLICY and policy.get('binding') == state.get('binding'), '引用策略账号/画布绑定已改变')
    for aid, expected in policy['nodes'].items():
        actual = state.get('mappings', {}).get(aid, {})
        require(actual.get('node_id') == expected['node_id'] and actual.get('kind') == expected['kind'],
                '冻结的资产映射ID/类型已改变：' + aid)


def source_issue(policy, key, state, doc):
    assert_mappings(policy, state)
    require(doc.get('rootCanvasId') == policy['binding']['canvas_id'], '引用来源快照不属于冻结画布')
    require(key in policy['nodes'], '图片资产不在本任务冻结的ID映射中：' + key)
    import canvas as c
    expected = policy['nodes'][key]
    nid = expected['node_id']
    if nid not in c.nodes(doc):
        return {'reason': 'node_missing', 'node_id': nid}
    actual = c.kind_of(c.nodes(doc)[nid])
    if actual != expected['kind']:
        return {'reason': 'node_type_changed', 'node_id': nid,
                'expected_kind': expected['kind'], 'actual_kind': actual}
    return None


def source_target(policy, doc, node_id):
    """Only frozen asset sources get identity-only concurrency protection."""
    if policy is not None:
        match = next((row for row in policy['nodes'].values() if row['node_id'] == node_id), None)
        if match is not None:
            return {'action': 'bound_asset_identity', 'node_id': node_id, 'kind': match['kind'],
                    'canvas_id': policy['binding']['canvas_id']}
    import canvas as c
    return {'action': 'asset_snapshot', 'node_id': node_id, 'value': c.fingerprint(doc, node_id)}


def reference_media_satisfied(doc, source, target):
    """Verify actual SDK media IDs against that node's latest source records.

    A node ID is never substituted for a media ID. A retained selectable image
    remains valid; names/prompts/main-selection changes alone are not failures.
    """
    import canvas as c
    if source not in c.nodes(doc) or target not in c.nodes(doc):
        return False
    rows = c.content(doc, target).get('generation', {}).get('references', [])
    if not isinstance(rows, list):
        return False
    matching = [r for r in rows if isinstance(r, dict) and any(
        r.get(k) == source for k in ('sourceNodeId', 'nodeAssetId', 'id'))]
    if c.kind_of(c.nodes(doc)[source]) == 'role' and c.role_reference_media(doc, source, target) and not matching:
        return False
    data = c.content(doc, source)
    available = [{**info, 'pippitAssetId': info.get('pippitAssetId', key)}
                 for key, info in c.media.source_infos(data).items() if isinstance(info, dict)]
    available.append(data.get('appearanceInfo', {}) if c.kind_of(c.nodes(doc)[source]) in ('role', 'scene') else data)
    for row in matching:
        ids = {key: row[key] for key in ('pippitAssetId', 'assetId') if isinstance(row.get(key), str) and row[key]}
        if not ids or not any(all(info.get(k) == v for k, v in ids.items()) for info in available):
            return False
    return True
