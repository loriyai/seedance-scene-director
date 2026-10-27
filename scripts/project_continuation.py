"""Durable continuation and local-delivery identity, separate from formal receipts."""
import hashlib
import json
import copy
from pathlib import Path
import project_paths as paths

STAGES = ('writing', 'awaiting_review', 'needs_revision', 'passed_pending_delivery', 'delivered')


def read(path):
    return json.loads(paths.io_path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(paths.io_path(path).read_bytes()).hexdigest()


def verified(project, value, expected):
    target = paths.resolve(project, value)
    if not target.is_file() or sha(target) != expected:
        raise ValueError('交接依赖缺失或内容变化：' + str(value))
    return target


def local_delivery(project, folder):
    """Validate native reviewed-local bundle identities, without performing review."""
    folder = paths.io_path(folder)
    bundle, review = read(folder / 'bundle-result.json'), read(folder / 'final-review.json')
    if bundle.get('delivery_mode') != 'local_manual_delivery':
        raise ValueError('不是本地交付成果')
    draft = verified(project, bundle['draft'], bundle['draft_sha256'])
    actual = verified(project, bundle['output'], bundle['output_sha256'])
    if review.get('draft_sha256') != sha(draft) or review.get('actual_output_sha256') != sha(actual):
        raise ValueError('本地终审与实际稿件不匹配')
    rows = review.get('scenes', [])
    if not rows or any(r.get('status') != 'passed' or r.get('issues') != [] or not r.get('basis') for r in rows):
        raise ValueError('本地终审仍有未关闭问题')
    dependencies = []

    def keep(path, expected=None):
        p = paths.resolve(project, str(path))
        if expected is not None:
            verified(project, str(path), expected)
        dependencies.append({'path': str(p), 'sha256': sha(p)})
        return p

    keep(draft); keep(actual); keep(folder / 'bundle-result.json'); keep(folder / 'final-review.json')
    if bundle.get('assembly_proof'):
        keep(bundle['assembly_proof'], bundle['assembly_proof_sha256'])
    previous = bundle['span'][0]
    scene_ids = []
    for row in bundle['scene_dependencies']:
        if row['span'][0] != previous:
            raise ValueError('本地场次来源范围不连续')
        previous = row['span'][1]
        scene_ids.append(row['scene_id'])
        for key, hash_key in (('draft', 'draft_sha256'), ('review', 'review_sha256'),
                              ('preview', 'preview_sha256'), ('actual_output', 'actual_output_sha256'),
                              ('context', 'context_file_sha256'), ('review_reuse', 'review_reuse_sha256')):
            if row.get(key):
                keep(row[key], row[hash_key])
        original = read(paths.resolve(project, row['review']))
        if original.get('draft_sha256') != row['draft_sha256'] or original.get('actual_output_sha256', row['actual_output_sha256']) != row['actual_output_sha256']:
            raise ValueError('原场审阅身份不匹配')
        if any(r.get('status') != 'passed' or r.get('issues') != [] for r in original['scenes']):
            raise ValueError('原场审阅未关闭')
        context_file = paths.resolve(project, row.get('context', str(Path(row['draft']).parent / 'context.json')))
        context = read(context_file)
        if context['span'] != row['span'] or context['scene']['id'] != row['scene_id']:
            raise ValueError('本地场次上下文范围不匹配')
        if context['versions']['context_sha256'] != original['context_sha256']:
            raise ValueError('原审上下文不匹配')
        for bound in context['versions']['files'].values():
            keep(bound['path'], bound['sha256'])
        supplement = context.get('local_association_supplement')
        if supplement:
            proof = read(keep(supplement['path'], supplement.get('sha256')))
            # Preserve and verify all declared dependency bytes; do not execute helpers.
            for filename, expected in proof.get('dependencies', {}).items():
                keep(filename, expected)
    if previous != bundle['span'][1] or scene_ids != [r['scene_id'] for r in rows]:
        raise ValueError('本地终审覆盖不一致')
    return {'delivery_mode': 'local_manual_delivery', 'segment': bundle['segment'],
            'span': bundle['span'], 'unit_count': bundle['unit_count'],
            'shot_count': bundle['shot_count'], 'total_seconds': bundle['total_seconds'],
            'draft': str(draft), 'actual': str(actual), 'review': str(folder / 'final-review.json'),
            'dependencies': list({r['path']: r for r in dependencies}.values()),
            'semantic_review': 'original_reports_preserved_not_reperformed'}


def deliveries(project):
    project = paths.io_path(project)
    manifest = paths.read_metadata(project)
    if manifest:
        values = manifest.get('local_deliveries', [])
        for row in values:
            for dependency in row['dependencies']:
                verified(project, dependency['path'], dependency['sha256'])
        return values
    return [local_delivery(project, file.parent) for file in sorted(
        (Path(project) / 'storyboard-AF').glob('*/bundle-result.json'))
        if read(file).get('delivery_mode') == 'local_manual_delivery'
        and (file.parent / 'final-review.json').is_file()]


def checkpoint(project, stage, scene=None, draft=None, review=None, context=None, note=None, next_segment=None):
    if stage not in STAGES:
        raise ValueError('未知续接阶段')
    if stage != 'delivered' and (not scene or not draft or not context):
        raise ValueError('未交付检查点须包含场次、实际草稿与上下文')
    if stage in ('needs_revision', 'passed_pending_delivery') and not review:
        raise ValueError('该阶段须保留原审报告')
    value = {'policy': 'project-continuation-v1', 'stage': stage, 'scene': scene,
             'next_segment': next_segment, 'note': note, 'artifacts': {}}
    for role, filename in (('draft', draft), ('review', review), ('context', context)):
        if filename:
            p = Path(filename).resolve()
            value['artifacts'][role] = {'path': str(p), 'sha256': sha(p)}
    if review:
        report = read(review)
        if report.get('draft_sha256') != value['artifacts']['draft']['sha256']:
            raise ValueError('检查点审阅不是当前草稿')
        if stage == 'passed_pending_delivery' and any(r.get('status') != 'passed' or r.get('issues') != [] for r in report.get('scenes', [])):
            raise ValueError('审阅未通过，不能记为待交付')
        if not report.get('scenes') or not report.get('reviewer'):
            raise ValueError('缺少实际审阅身份与场次结论')
        if read(context).get('versions', {}).get('context_sha256') != report.get('context_sha256'):
            raise ValueError('检查点原审与所附上下文不匹配')
    from director import write_json
    write_json(Path(project) / 'continuation.json', value)
    return value


def resume(project, state=None):
    import director as d
    project = paths.io_path(project)
    state = d.load(Path(project)) if state is None else state
    if not isinstance(state.get('length'), int):
        return {'status': 'unavailable', 'read_only': True, 'reason': '历史摘要不包含完整来源范围，不能推断续接位置。',
                'canvas_auto_resume': False}
    meta = paths.read_metadata(project)
    explicit = Path(project) / 'continuation.json'
    point = read(explicit) if explicit.is_file() else copy.deepcopy(meta.get('checkpoint') if meta else None)
    if point:
        for row in point.get('artifacts', {}).values():
            verified(project, row['path'], row['sha256'])
        draft_hash = point.get('artifacts', {}).get('draft', {}).get('sha256')
        completed = next((r for r in state.get('receipts', [])
                          if draft_hash and r.get('final_review', {}).get('draft_sha256') == draft_hash), None)
        if completed:
            point.update(stage='delivered', committed_request_id=completed['request_id'],
                         output=completed['output'])
        if point['stage'] == 'passed_pending_delivery':
            from storyboard_context import context_hash
            report = read(paths.resolve(project, point['artifacts']['review']['path']))
            current = context_hash(state, project)
            bridge = review_bridge(project, point['artifacts']['draft']['sha256'], current)
            point['ready_for_commit'] = report['context_sha256'] == current or bool(bridge)
            if not point['ready_for_commit']:
                point['binding_status'] = 'historical_context_requires_impact_check'
    local = deliveries(project)
    siblings = related(project)
    coverage = sorted([r['span'] for r in state.get('ranges', [])] + [r['span'] for r in local]
                      + [r['span'] for sibling in siblings for r in sibling['ranges']])
    cursor, gaps = 0, []
    for start, end in coverage:
        if start > cursor:
            gaps.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < state['length']:
        gaps.append([cursor, state['length']])
    if point is None and not gaps:
        point = {'policy': 'project-continuation-v1', 'stage': 'delivered',
                 'note': '已验证历史覆盖完整绑定范围；不重复生成。', 'artifacts': {}}
    return {'status': 'ready', 'read_only': True, 'project_id': paths.project_id(project),
            'formal_unit_count': len(state.get('units', [])), 'formal_receipt_count': len(state.get('receipts', [])),
            'local_deliveries': [{k: r[k] for k in ('segment', 'span', 'unit_count', 'delivery_mode')} for r in local],
            'related_formal_projects': [{k: r[k] for k in ('session', 'unit_count', 'ranges')} for r in siblings],
            'coverage_complete': not gaps, 'uncovered_spans': gaps,
            'checkpoint': point, 'requires_range': point is None or (point['stage'] == 'delivered' and not point.get('next_segment')),
            'canvas_auto_resume': False, 'semantic_review': 'not_performed'}


def related(project):
    meta = paths.read_metadata(project)
    rows = meta.get('related_projects', []) if meta else []
    for row in rows:
        verified(project, row['session'], row['sha256'])
        for receipt in row['receipts']:
            verified(project, receipt['output'], receipt['output_sha256'])
    return rows


def review_bridge(project, draft_sha256, context_sha256):
    """A bridge preserves an actual old report; it never creates a passed row."""
    meta = paths.read_metadata(project)
    bridge = meta.get('review_bridge') if meta else None
    if not bridge or bridge['draft_sha256'] != draft_sha256 or bridge['new_context_sha256'] != context_sha256:
        return None
    import director as d
    if any(d.file_hash(d.ROOT / name) != expected for name, expected in bridge['creative_files'].items()):
        return None
    return bridge
