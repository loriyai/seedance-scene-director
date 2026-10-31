#!/usr/bin/env python3
"""Local text-first storyboard checks, delivery and clock events.

No calls to legacy authoring gates, semantic auto-approval, or remote services.
The small review record attests a human/agent reading; it cannot prove that read.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import director as d
import batch_naming
import ratio_profiles
import storyboard_text as text_core
import storyboard_authoring_gate
from storyboard_context import context_hash, scene_context
import storyboard_metrics
import segment_naming
from video_settings import get_ratio
from video_header import read as read_header, POLICY as HEADER_POLICY

POLICY = 'scene-text-v1'
EXPORT_FORMAT = 'ratio-summary-v1'


def require_dialogue_capacity(units):
    """Reject impossible speech capacity; never redistribute author timings."""
    from speech_timing_audit import text_dialogue_capacity
    report = text_dialogue_capacity(units)
    d.require(not report['errors'], '台词容量下限：' + '; '.join(
        f'{row["unit"]}/镜头{row["shot"]}：{row["code"]}' for row in report['errors']))
    return report


def check_incremental(project, draft, span=None):
    """Read only an authored scene prefix; not a preview or delivery approval.

    Incomplete source tails and a final 1–3 second in-progress unit can be
    inspected without inventing text, padding time, recording preview events,
    allocating IDs or changing the default complete-scene validation.
    """
    project, draft = Path(project).resolve(), Path(draft).resolve()
    state = d.load(project)
    d.require(state.get('style_hash'), '请先保存本项目风格')
    header = read_header(project, state, required=bool(state.get('header_hash')))
    ratio = get_ratio(project, state)
    profile = ratio_profiles.profile_summary(ratio)
    raw = draft.read_bytes()
    units = text_core.parse_incremental(raw.decode('utf-8-sig'), state, span)
    camera = text_core.camera_constraints(units, *text_core.camera_context(state, units))
    require_camera_constraints(camera)
    capacity = require_dialogue_capacity(units)
    selected = list(dict.fromkeys(sid for unit in units for sid in unit['scene_ids']))
    actual_span = [units[0]['span'][0], units[-1]['span'][1]]
    return {
        'status': 'incremental_checked', 'validation_scope': 'authored_prefix_only',
        'semantic_review': 'not_performed', 'delivery_eligible': False,
        'requires_complete_scene_preview': True, 'unit_count': len(units),
        'shot_count': sum(len(unit['shots']) for unit in units),
        'total_duration': sum(unit['duration'] for unit in units),
        'span': actual_span, 'scenes': selected,
        'draft_sha256': hashlib.sha256(raw).hexdigest(),
        'context_sha256': context_hash(state, project),
        'video_ratio': ratio,
        'ratio_profile': profile,
        'camera_constraints': camera, 'dialogue_capacity': capacity,
        'warnings': camera['warnings'],
        'limits': '仅检查已经逐镜写出的前缀；没有推断动作覆盖、补写后文或审阅通过。完整场景仍须正常preview及独立主审。',
    }


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return d.sha(d.packed(value))


def prepare(project, draft, span=None, replace_ids=None, *, check_camera=True,
            authoring_trace=None):
    project, draft = Path(project).resolve(), Path(draft).resolve()
    state = d.load(project)
    d.require(state.get('style_hash'), '请先保存本项目风格')
    header = read_header(project, state, required=bool(state.get('header_hash')))
    header_policy = HEADER_POLICY if header is not None else d.HEADER_POLICY
    draft_bytes = draft.read_bytes()
    raw = draft_bytes.decode('utf-8-sig')
    parse_span = list(span) if span is not None else None
    if replace_ids:
        existing = {unit['id']: unit for unit in state['units']}
        d.require(len(set(replace_ids)) == len(replace_ids) and all(key in existing for key in replace_ids),
                  '替换ID不存在或重复')
        selected_old = [existing[key] for key in replace_ids]
        indices = [next(i for i,u in enumerate(state['units']) if u['id'] == key) for key in replace_ids]
        d.require(all(b == a + 1 for a,b in zip(indices, indices[1:])) and
                  all(left['span'][0] <= right['span'][0] <= left['span'][1] for left,right in zip(selected_old, selected_old[1:])),
                  '不连续的指定单元请分别保存局部草稿并提交；不要把中间未选剧情混入修改')
        parse_span = [selected_old[0]['span'][0], selected_old[-1]['span'][1]]
        d.require(span is None or list(span) == parse_span, '--span与指定修改单元范围不同')
    units = text_core.parse(raw, state, source_span=parse_span)
    info = text_core.validate(state, units)
    authoring = storyboard_authoring_gate.validate(
        project, state, draft_bytes, units, context_hash(state, project), authoring_trace,
        draft_path=draft)
    storyboard_authoring_gate.require(authoring)
    info['dialogue_capacity'] = require_dialogue_capacity(units)
    selected = list(dict.fromkeys(sid for unit in units for sid in unit['scene_ids']))
    actual_span = [units[0]['span'][0], units[-1]['span'][1]]
    if replace_ids:
        d.require(len(replace_ids) == len(units) and len(set(replace_ids)) == len(replace_ids),
                  '替换ID须与草稿单元逐一对应且不重复')
        previous = {unit['id']: unit for unit in state['units']}
        for key, unit in zip(replace_ids, units):
            d.require(key in previous, f'指定分镜不存在：{key}')
            old = previous[key]
            d.require(unit['span'] == old['span'] and unit['main_scene'] == old['main_scene'],
                      f'{key}：局部修改须保留来源范围及场景')
            unit['id'] = key
    elif span is not None:
        d.require(list(span) == actual_span, '草稿未完整覆盖明确指定的来源范围')
    else:
        records = [r for r in state['records'] if r['scene'] in selected]
        d.require(records and actual_span == [records[0]['start'], records[-1]['end']],
                  '默认须覆盖所选完整场景；明确制作局部范围时提供 --span START END')
    style = (project / 'video-style.txt').read_text(encoding='utf-8-sig').strip()
    ratio = get_ratio(project, state)
    for unit in units:
        unit.update(style=style, output_format=POLICY,
                    header_policy=header_policy,
                    black_reference_mode=d.BLACK_REFERENCE_MODE,
                    export_format=EXPORT_FORMAT,
                    voice_display_policy=text_core.VOICE_DISPLAY_POLICY)
        if header is not None:
            unit['header_text'] = header
        else:
            unit.pop('header_text', None)
        ratio_profiles.apply_to_unit(unit, ratio)
        if replace_ids:
            old = previous[unit['id']]
            # A targeted edit retains its original numbering and video shape;
            # changing a default cannot silently change an existing unit.
            for key in (*segment_naming.IDENTITY_FIELDS, 'display_name', 'batch_label', 'batch_index',
                        'video_ratio', 'ratio_profile', 'ratio_profile_version',
                        'ratio_profile_digest'):
                if key in old:
                    unit[key] = copy.deepcopy(old[key])
                elif key in ('video_ratio', 'ratio_profile', 'ratio_profile_version', 'ratio_profile_digest'):
                    # Historical units may predate the profile registry.  Do
                    # not silently attach a new profile while replacing them.
                    unit.pop(key, None)
            for key in ('header_policy', 'header_text'):
                if key in old:
                    unit[key] = copy.deepcopy(old[key])
                else:
                    unit.pop(key, None)
        if unit.get('ratio_profile') is not None:
            ratio_profiles.require_unit_profile(unit)
    plan = segment_naming.active_plan(project, state)
    identities = segment_naming.assignments(state, plan, units) if not replace_ids else None
    if identities:
        for unit, identity in zip(units, identities):
            unit.update(identity)
    camera = text_core.camera_constraints(units, *text_core.camera_context(state, units, replace_ids))
    if check_camera:
        require_camera_constraints(camera)
    info['camera_constraints'] = camera
    info['warnings'].extend(camera['warnings'])
    binding = {'draft_sha256': hashlib.sha256(draft_bytes).hexdigest(),
               'context_sha256': context_hash(state, project),
               'header_sha256': d.sha(header)}
    profiles = [ratio_profiles.profile_summary(unit['video_ratio']) for unit in units
                if unit.get('video_ratio') is not None]
    info = {**info, **binding, 'scenes': selected, 'span': actual_span,
            'video_ratios': list(dict.fromkeys(unit.get('video_ratio') for unit in units
                                               if unit.get('video_ratio') is not None)),
            'ratio_profiles': list({profile['profile_id']: profile for profile in profiles}.values()),
            'semantic_review': 'not_performed',
            'authoring_gate': authoring,
            'history': d.range_review(state, actual_span, project=project) if not replace_ids else None}
    from project_continuation import review_bridge
    bridge = review_bridge(project, binding['draft_sha256'], binding['context_sha256'])
    if bridge:
        info['review_bridge'] = bridge
    return state, units, info


def require_camera_constraints(report):
    d.require(not report['errors'], '摄影约束：' + '; '.join(
        f'{row["unit"]}/镜头{row["shot"]}：{row["kind"]}' for row in report['errors']))


def verify_review(report, info):
    d.require(info.get('validation_scope') != 'authored_prefix_only',
              '增量检查不能作为完整场景审阅或交付依据')
    d.require(isinstance(report, dict), '场次审阅须为JSON对象')
    for key in ('draft_sha256', 'context_sha256'):
        bridge = info.get('review_bridge')
        equivalent = (key == 'context_sha256' and bridge
                      and report.get(key) == bridge['old_context_sha256']
                      and info[key] == bridge['new_context_sha256']
                      and d.sha(d.packed(report)) == bridge['report_digest'])
        d.require(report.get(key) == info[key] or equivalent, f'审阅绑定不匹配：{key}；核对实际变化后更新本次场次结论')
    d.require(isinstance(report.get('reviewer'), str) and report['reviewer'].strip(), '缺少实际独立审阅者')
    rows = report.get('scenes')
    d.require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows), '缺少场次审阅结论')
    d.require([row.get('scene_id') for row in rows] == info['scenes'], '审阅场景须与本次稿件顺序及范围一致')
    for row in rows:
        d.require(row.get('status') == 'passed' and row.get('issues') == [],
                  f'{row.get("scene_id")} 仍有未关闭问题')
        d.require(isinstance(row.get('basis'), str) and row['basis'].strip(), '缺少实际成稿审阅及问题关闭说明')
    gate = info.get('authoring_gate', {})
    if gate.get('enforced'):
        d.require(report.get('semantic_review') == 'performed',
                  '新逐镜写作政策要求语义审阅明确标记为performed')
        d.require(report.get('authoring_trace_sha256') == gate.get('trace_sha256'),
                  '审阅未绑定当前逐镜写作侧车')
        d.require(report.get('authoring_gate_digest') == gate.get('gate_digest'),
                  '审阅未绑定当前逐镜写作闸门结果')
        d.require(report.get('shot_count') == gate.get('shot_count'),
                  '审阅镜头总数与逐镜写作稿不一致')
        expected_shots = gate.get('shot_keys', [])
        reviewed = report.get('reviewed_shots')
        d.require(reviewed == expected_shots,
                  '审阅必须按当前稿件顺序列出全部镜头；不能用通用场级passed代替逐镜阅读')
    return copy.deepcopy(report)


def safe_output(state, project, output, inputs=()):
    result = d.validate_output_path(state, project, output)
    d.require(result not in {Path(p).resolve() for p in inputs}, '输出不能覆盖输入或审阅记录')
    d.require(not result.exists(), '输出文件已存在；另存新版本')
    return result


def render_units(state, units, preview=False):
    results = []
    counts = {}
    scenes = {scene['id']: scene for scene in state['ledger']['scenes']}
    for index, value in enumerate(units, 1):
        unit = copy.deepcopy(value)
        d.require(unit.get('export_format') in (None, EXPORT_FORMAT), '未知的文字分镜导出版式')
        if preview and unit.get('naming_policy') != segment_naming.POLICY:
            scene = unit['main_scene']
            counts[scene] = counts.get(scene, 0) + 1
            unit['display_name'] = f'预览{index:02d}-场景{scenes[scene]["number"]}-分镜{counts[scene]}'
        results.append(d.render_unit(state, unit))
    if units and all(unit.get('export_format') == EXPORT_FORMAT for unit in units):
        total = sum(unit['duration'] for unit in units)
        minutes, seconds = divmod(total, 60)
        results.append(f'本次共{len(units)}个分镜单元，总时长 {minutes}分 {seconds}秒')
    return '\n\n'.join(results) + '\n'


def write_preview(target, payload):
    """Exclusively create a new artifact; never replace an existing version."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        try:
            stream.write(payload)
        except BaseException:
            stream.close()
            target.unlink()
            raise


def preview(project, draft, output=None, span=None, replace_ids=None, activity_id=None,
            authoring_trace=None):
    from project_paths import assert_writable
    assert_writable(project)
    project, draft = Path(project).resolve(), Path(draft).resolve()
    state, units, info = prepare(project, draft, span, replace_ids,
                                 authoring_trace=authoring_trace)
    payload = render_units(state, units, preview=True).encode('utf-8')
    artifact = {key: info[key] for key in ('draft_sha256', 'context_sha256', 'span', 'scenes')}
    artifact.update(draft=str(draft), output_sha256=hashlib.sha256(payload).hexdigest())
    rows, _ = storyboard_metrics.effective_events(storyboard_metrics.read_events(project))
    starts = [i for i, row in enumerate(rows) if row['event'] == 'start']
    rows = rows[starts[-1]:] if starts else rows
    explicit = output is not None
    if explicit:
        target = safe_output(state, project, output, (draft,))
    else:
        folder = project / 'text-storyboards/previews'
        stem = f'preview-{info["draft_sha256"][:16]}-{info["context_sha256"][:16]}'
        for index in range(1, 10001):
            target = d.validate_output_path(state, project, folder / f'{stem}-{index:03d}.txt')
            d.require(target != draft, '预览不能覆盖作者稿')
            if not target.exists():
                break
            if target.is_file() and target.read_bytes() == payload:
                current = {**artifact, 'output': str(target)}
                matches = [row for row in rows if row.get('producer') == storyboard_metrics.PREVIEW_PRODUCER
                           and row.get('artifact') == current and row.get('activity_id') == activity_id]
                if set(row.get('scene') for row in matches) == set(info['scenes']):
                    d.require(hashlib.sha256(draft.read_bytes()).hexdigest() == info['draft_sha256'],
                              '作者稿在预览期间变化，请重新运行预览')
                    return {**info, 'output': str(target), 'output_sha256': artifact['output_sha256'],
                            'preview_reused': True, 'preview_event_ids': [row['event_id'] for row in matches]}
        else:
            raise ValueError('预览版本路径已耗尽；保留现场并选择新的输出路径')
    d.require(hashlib.sha256(draft.read_bytes()).hexdigest() == info['draft_sha256'],
              '作者稿在预览期间变化，请重新运行预览')
    try:
        write_preview(target, payload)
    except FileExistsError as exc:
        raise ValueError('预览路径已被占用；未覆盖文件，请重新运行自动路径预览') from exc
    d.require(target.read_bytes() == payload, '预览写入核验失败，未记录成功事件')
    artifact['output'] = str(target)
    events = []
    try:
        for scene in info['scenes']:
            events.append(storyboard_metrics.record_event(
                project, 'previewed', at=now(), scene=scene, activity_id=activity_id,
                producer=storyboard_metrics.PREVIEW_PRODUCER, artifact=artifact))
    except (OSError, ValueError) as exc:
        raise ValueError(f'预览文件已写入，但成功事件未全部记录；保留产物 {target}，请核对日志：{exc}') from exc
    return {**info, 'output': str(target), 'output_sha256': artifact['output_sha256'],
            'preview_reused': False, 'preview_event_ids': [row['event_id'] for row in events]}


def event(project, name, scene=None, note=None, **fields):
    return storyboard_metrics.record_event(project, name, at=now(), scene=scene, note=note, **fields)


def timing(project):
    return storyboard_metrics.timing(project)


def commit(project, draft, request_id, review, output, span=None,
           replace_ids=None, range_confirmation=None, authoring_trace=None):
    from project_paths import assert_writable
    assert_writable(project)
    project = Path(project).resolve()
    d.require(isinstance(request_id, str) and request_id.strip(), 'request_id不能为空')
    state, units, info = prepare(project, draft, span, replace_ids, check_camera=False,
                                 authoring_trace=authoring_trace)
    mode = 'replace' if replace_ids else 'append'
    request_hash = digest({'policy': POLICY, 'draft_sha256': info['draft_sha256'],
                           'mode': mode, 'replace_ids': replace_ids or [], 'span': info['span'],
                           'header_sha256': info.get('header_sha256'),
                           'video_ratios': info.get('video_ratios', []),
                           'ratio_profiles': [p['profile_id'] for p in info.get('ratio_profiles', [])]})
    previous_receipt = next((r for r in state['receipts'] if r['request_id'] == request_id), None)
    if previous_receipt:
        d.require(previous_receipt.get('policy') == POLICY and previous_receipt['request_hash'] == request_hash,
                  'request_id已用于其他内容；不能覆盖历史请求')
        d.require(Path(output).resolve() == Path(previous_receipt['output']).resolve(),
                  '重试须沿用原交付路径；另行导出使用render')
        # A retry recovers its exact saved receipt, never a recomputed approval.
        target = Path(previous_receipt['output']).resolve()
        rendered = render_units(state, previous_receipt['units'])
        d.validate_output_path(state, project, target)
        if target.exists():
            d.require(target.read_text(encoding='utf-8-sig') == rendered,
                      '已交付文件被外部修改；保留现场，使用render另存')
        else:
            d.atomic_text(target, rendered)
        return {'status': 'already_committed', 'output': str(target), **d.receipt_summary(previous_receipt)}
    require_camera_constraints(info['camera_constraints'])
    final_review = verify_review(d.read_json(review), info)
    if mode == 'append' and info['history']['issues']:
        d.require(range_confirmation == info['history']['confirmation_token'],
                  '范围重叠/间隔/回退需要已取得的本次用户授权，不得自动填写确认')
    target = safe_output(state, project, output, (draft, review))
    plan = segment_naming.active_plan(project, state) if mode == 'append' else None
    scope = batch_naming.scope_for(project, state.get('batch_scope'))
    d.require(scope != d.ROOT.resolve() and d.ROOT.resolve() not in scope.parents, '批次登记不能写入Skill目录')
    d.require(not state.get('batch_scope') or (scope / batch_naming.REGISTRY).is_file(), '原批次登记丢失；请恢复原登记')
    # Both append and replace serialize their tiny session mutation. No drafts,
    # full-prefix snapshots or per-shot approval ledgers are created here.
    allocation = (batch_naming.allocate(scope, project, request_id, request_hash)
                  if mode == 'append' and plan is None else batch_naming._lock(scope))
    with allocation as batch_label:
        d.require(d.read_json(project / 'session.json')['revision'] == state['revision'], '项目已有新的提交；重读后重试')
        d.require(context_hash(state, project) == info['context_sha256'],
                  '比例、拆分方案或规则在提交期间变化；请重新预览和核对审阅')
        working = copy.deepcopy(state)
        counts = {}
        scenes = {scene['id']: scene for scene in state['ledger']['scenes']}
        identities = segment_naming.assignments(working, plan, units) if plan else None
        for index, unit in enumerate(units, 1):
            main = unit['main_scene']
            if mode == 'append':
                number = working['next_numbers'].get(main, 1)
                counts[main] = counts.get(main, 0) + 1
                prior = (units[index - 2] if index > 1 else
                         next((u for u in reversed(working['units']) if u['span'][1] == unit['span'][0]), None))
                unit.update(id=f'场景{scenes[main]["number"]}-分镜{number}', version=1,
                            batch_id=request_id, batch_label=batch_label, batch_index=index,
                            display_name=f'{batch_label}{index:02d}-场景{scenes[main]["number"]}-分镜{counts[main]}',
                            predecessor=prior['id'] if prior else None, previous_versions=[])
                if identities:
                    unit.update(identities[index - 1])
                working['next_numbers'][main] = number + 1
                working['units'].append(unit)
            else:
                old = next(u for u in working['units'] if u['id'] == unit['id'])
                unit.update(version=old['version'] + 1,
                            previous_versions=old.get('previous_versions', []) + [{k:v for k,v in old.items() if k != 'previous_versions'}])
                for key in ('batch_id', 'batch_label', 'batch_index', 'display_name', 'predecessor',
                            *segment_naming.IDENTITY_FIELDS):
                    if key in old:
                        unit[key] = copy.deepcopy(old[key])
                working['units'][working['units'].index(old)] = unit
        if mode == 'append':
            row = {'span': info['span'], 'batch_id': request_id,
                   'confirmed': bool(range_confirmation)}
            if plan:
                row.update(segment_plan_id=plan['plan_id'],
                           batch_labels=list(dict.fromkeys(unit['batch_label'] for unit in units)))
            else:
                row['batch_label'] = batch_label
                working['batch_scope'] = str(scope)
            working['ranges'].append(row)
        receipt = {'policy': POLICY, 'request_id': request_id, 'request_hash': request_hash,
                   'mode': mode, 'output': str(target), 'units': copy.deepcopy(units),
                   'display_format': d.RECEIPT_DISPLAY_FORMAT, 'export_format': EXPORT_FORMAT,
                   'video_ratios': list(dict.fromkeys(unit.get('video_ratio') for unit in units
                                                     if unit.get('video_ratio') is not None)),
                   'ratio_profiles': list(dict.fromkeys(
                       unit['ratio_profile'] for unit in units if unit.get('ratio_profile'))),
                   'final_review': final_review,
                   'authoring_gate': copy.deepcopy(info.get('authoring_gate')),
                   'committed_at': now()}
        if len(receipt['video_ratios']) == 1:
            receipt['video_ratio'] = receipt['video_ratios'][0]
        if len(receipt['ratio_profiles']) == 1:
            receipt['ratio_profile'] = receipt['ratio_profiles'][0]
        if mode == 'append' and plan is None:
            receipt.update(batch_scope=str(scope), batch_label=batch_label)
        elif plan:
            receipt.update(naming_policy=segment_naming.POLICY, segment_plan_id=plan['plan_id'],
                           batch_labels=list(dict.fromkeys(unit['batch_label'] for unit in units)))
        # Render before committing so a format error cannot create an unusable receipt.
        rendered = render_units(working, units)
        working['receipts'].append(receipt)
        working['phase'] = 'nodes_choice'
        d.save(project, working)
    d.atomic_text(target, rendered)
    event(project, 'delivered', note=request_id)
    return {'status': 'committed', 'output': str(target), 'ids': [u['id'] for u in units],
            'names': [u['display_name'] for u in units], 'canvas_sync': 'not_requested',
            **d.receipt_summary(receipt)}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'preview', 'commit'):
        p = commands.add_parser(name)
        p.add_argument('--project', type=Path, required=True)
        p.add_argument('--draft', type=Path, required=True)
        p.add_argument('--span', type=int, nargs=2)
        p.add_argument('--replace-ids', nargs='+')
        p.add_argument('--authoring-trace', type=Path,
                       help='逐镜写作侧车；省略时读取<draft>.authoring.json')
        if name == 'check':
            p.add_argument('--incremental', action='store_true',
                           help='只读检查当前场已写前缀；不生成实际稿、编号或审阅通过')
        if name != 'check':
            p.add_argument('--output', type=Path, required=name == 'commit')
        if name == 'preview':
            p.add_argument('--activity-id', help='与本次起草窗口相同的activity-id，未指定时使用默认窗口')
        if name == 'commit':
            p.add_argument('--request-id', required=True)
            p.add_argument('--review', type=Path, required=True)
            p.add_argument('--range-confirmation')
    p = commands.add_parser('render')
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--request-id', required=True)
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('event')
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--scene')
    p.add_argument('--note')
    p.add_argument('--activity-id', help='同场同阶段并行或多次尝试时区分工作窗口')
    p.add_argument('--coverage', choices=sorted(storyboard_metrics.COVERAGES))
    p.add_argument('--issue-kind', choices=sorted(storyboard_metrics.WORK_CATEGORIES))
    p = commands.add_parser('invalidate-event', help='追加撤销错误阶段事件的记录，不修改原日志')
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--event-id', required=True)
    p.add_argument('--note', required=True)
    p = commands.add_parser('timing')
    p.add_argument('--project', type=Path, required=True)
    p = commands.add_parser('context', help='只读导出一场来源、合法引用、版本与已交付边界')
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--scene', required=True)
    p.add_argument('--span', type=int, nargs=2)
    args = parser.parse_args()
    try:
        if args.command == 'check':
            if args.incremental:
                d.require(not args.replace_ids, '增量检查不接受--replace-ids；指定修改使用正常check/preview')
                result = check_incremental(args.project, args.draft, args.span)
            else:
                state, units, result = prepare(args.project, args.draft, args.span, args.replace_ids,
                                               authoring_trace=args.authoring_trace)
        elif args.command == 'preview':
            result = preview(args.project, args.draft, args.output, args.span, args.replace_ids,
                             args.activity_id, args.authoring_trace)
        elif args.command == 'commit':
            result = commit(args.project, args.draft, args.request_id, args.review, args.output,
                            args.span, args.replace_ids, args.range_confirmation, args.authoring_trace)
        elif args.command == 'render':
            state = d.load(args.project)
            receipt = next((r for r in state['receipts'] if r['request_id'] == args.request_id), None)
            d.require(receipt is not None, '提交记录不存在')
            target = safe_output(state, args.project, args.output)
            d.atomic_text(target, render_units(state, receipt['units']))
            result = {'output':str(target), **d.receipt_summary(receipt)}
        elif args.command == 'event':
            result = event(args.project, args.name, args.scene, args.note,
                           activity_id=args.activity_id, coverage=args.coverage, issue_kind=args.issue_kind)
        elif args.command == 'invalidate-event':
            result = storyboard_metrics.invalidate_event(args.project, args.event_id, at=now(), note=args.note)
        elif args.command == 'context':
            result = scene_context(args.project, args.scene, args.span)
        else:
            result = timing(args.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status':'error','message':str(exc)},ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
