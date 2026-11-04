"""Verified, read-only delivery projection for queued Canvas transfers.

The source session, receipts and bound ledger remain unchanged. A transfer may
only add committed character designs and apply a verified delivery title map.
"""
import copy
import hashlib
import json
from pathlib import Path
import director as d

POLICY = 'reviewed-delivery-transfer-v1'


def read_ref(project, ref):
    root = Path(project).resolve()
    path = Path(ref['path']).resolve()
    d.require(root in path.parents, '同步投影凭据必须保存在项目内')
    raw = path.read_bytes()
    d.require(hashlib.sha256(raw).hexdigest() == ref['sha256'], '同步投影凭据已变化：' + str(path))
    return json.loads(raw.decode('utf-8-sig'))


def reference(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': d.file_hash(path)}


def watched_paths(project, ref):
    spec = read_ref(project, ref)
    return [Path(ref['path']), *[Path(spec[k]['path']) for k in ('assets', 'names', 'delivery')],
            Path(read_ref(project, spec['assets'])['independent_review']['path'])]


def project_session(project, source, ref, kind, ids):
    spec = read_ref(project, ref)
    d.require(spec.get('policy') == POLICY and kind in ('assets', 'videos'), '不支持的交付同步投影')
    assets = read_ref(project, spec['assets'])
    review = read_ref(project, assets['independent_review'])
    d.require(assets.get('semantic_review') == 'performed' and review.get('status') == 'passed', '补充人物尚未独立审阅通过')
    accepted = assets['assets']
    keys = [a['id'] for a in accepted]
    d.require(len(keys) == len(set(keys)) and keys == spec['asset_ids'], '补充人物范围不匹配')
    d.require(all(any(r['asset_id'] == key and r['status'] == 'passed' for r in review['assets']) for key in keys), '补充人物逐项审阅缺失')
    units = sorted([u for u in source['units'] if u.get('segment_label') == spec['segment']], key=lambda u:(u['span'][0],u['span'][1]))
    definitions = {}
    for unit in units:
        for row in unit.get('production_cast', []):
            if row.get('asset_id') in keys and 'asset' in row:
                key = row['asset_id']
                d.require(key not in definitions or definitions[key] == row['asset'], '已提交补充人物定义不一致')
                definitions[key] = row['asset']
    d.require(all(definitions.get(a['id']) == a for a in accepted), '补充设计与已提交制作人物定义不一致')
    result = copy.deepcopy(source)
    import production_cast
    # Each definition is already frozen in a committed unit. Do not merge
    # unrelated actor justifications across scenes or change source identities.
    for asset in accepted:
        row = next(r for u in units for r in u.get('production_cast', []) if r.get('asset') == asset)
        result = production_cast.context(result, [row])
    d.reference_catalog(result)
    names = read_ref(project, spec['names'])
    delivery = read_ref(project, spec['delivery'])
    d.require(delivery.get('complete') is True and delivery.get('segment') == spec['segment'] and not delivery.get('missing') and not delivery.get('overlaps'), '完整分段交付核验未通过')
    d.require(len(units) == len(names) == len(delivery['units']) == delivery['unit_count'], '分段数量不匹配')
    scene_numbers = {r['id']:r['number'] for r in source['ledger']['scenes']}
    counts, aliases, proofs = {}, {}, {}
    for index, (unit, name, proof) in enumerate(zip(units, names, delivery['units']), 1):
        sid = unit['main_scene']; counts[sid] = counts.get(sid, 0) + 1
        expected = f"{spec['segment']}{index:02d}-场景{scene_numbers[sid]}-分镜{counts[sid]}"
        d.require(name == {'new':expected,'old':unit['display_name'],'id':unit['id'],'version':unit['version'],'span':unit['span']}, '交付编号未匹配来源顺序或正式身份')
        d.require(all(proof[k] == unit[k] for k in ('id','version','span')) and proof['display_name'] == expected and proof['original_display_name'] == unit['display_name'], '交付核验未匹配正式单元')
        aliases[unit['id']] = expected; proofs[unit['id']] = proof
    d.require(kind != 'videos' or ids == [u['id'] for u in units], '整段同步必须包含全部正式单元并保持来源顺序')
    result['_canvas_transfer'] = {'policy':POLICY,'reference':ref,'aliases':aliases,'proofs':proofs,'asset_ids':keys}
    if kind == 'assets':
        result['_canvas_asset_keys'] = keys
    else:
        import canvas as c
        needed = set().union(*(c.unit_asset_ids(result, u) for u in units))
        items = {item['key']:item for item in c.desired_assets(result)}
        def include(key):
            d.require(key in items, '分段引用未取得正式图片设计：' + key)
            for dependency in items[key].get('image_reference_assets', []):
                if dependency['key'] not in needed:
                    needed.add(dependency['key']); include(dependency['key'])
        for key in list(needed): include(key)
        result['_canvas_asset_keys'] = sorted(needed)
    return result


def render_unit(session, unit, reference_text=None):
    transfer = session.get('_canvas_transfer')
    if not transfer: return d.render_unit(session, unit, reference_text=reference_text)
    proof = transfer['proofs'][unit['id']]
    plain = d.render_unit(session, unit)
    d.require(hashlib.sha256(plain.encode()).hexdigest() == proof['native_render_sha256'], '原生渲染与已交付核验不一致')
    name = transfer['aliases'][unit['id']]
    old = unit['display_name']
    d.require(plain.startswith(old + '（'), '分镜标题格式不匹配')
    published = name + plain[len(old):]
    d.require(hashlib.sha256(published.encode()).hexdigest() == proof['sha256'], '同步文本与正式交付不一致')
    if reference_text is None: return published
    encoded = d.render_unit(session, unit, reference_text=reference_text)
    d.require(encoded.startswith(old + '（'), '编码后分镜标题格式不匹配')
    return name + encoded[len(old):]
