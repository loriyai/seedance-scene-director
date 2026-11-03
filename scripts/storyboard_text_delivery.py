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


def _delivery_errors(camera, capacity):
    errors = []
    for row in camera['errors']:
        errors.append({**row, 'code': 'CAMERA_CONSTRAINT',
                       'message': f'摄影约束：{row["unit"]}/镜头{row["shot"]}：{row["kind"]}'})
    for row in capacity['errors']:
        errors.append({**row,
                       'message': f'台词容量下限：{row["unit"]}/镜头{row["shot"]}：{row["code"]}'})
    return errors


def _raise_parsed_diagnostics(error, state, replace_ids=None, *, check_camera=True, source_trace=None):
    """Enrich only source-checked parser failures without rereading or writing."""
    from speech_timing_audit import text_dialogue_capacity
    errors = list(error.errors)
    if state.get('storyboard_authoring_policy') == storyboard_authoring_gate.POLICY:
        errors.extend(storyboard_authoring_gate.body_errors(error.units))
        if isinstance(source_trace, dict) and source_trace.get('version') == 4:
            errors.extend(storyboard_authoring_gate.source_mapping_errors(state, error.units, source_trace))
    camera = {'errors': []}
    if check_camera:
        camera = text_core.camera_constraints(error.units, *text_core.camera_context(state, error.units, replace_ids))
    errors.extend(_delivery_errors(camera, text_dialogue_capacity(error.units)))
    raise text_core.TextValidationError(errors) from error


def check_incremental(project, draft, span=None, authoring_trace=None):
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
    try:
        units = text_core.parse_incremental(raw.decode('utf-8-sig'), state, span,
            continuity_props=storyboard_authoring_gate.candidate_carry_props(draft, authoring_trace))
    except text_core.TextValidationError as exc:
        _raise_parsed_diagnostics(exc, state)
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


def bind_trace(project, draft, decisions, output=None, span=None):
    """Save metadata around authored v4 decisions; never infer creative data."""
    from project_paths import assert_writable

    assert_writable(project)
    project, draft, decisions = (Path(value).resolve() for value in (project, draft, decisions))
    state = d.load(project)
    raw = draft.read_bytes()
    authored = d.read_json(decisions)
    from continuity import carry_declarations
    try:
        units = text_core.parse(raw.decode('utf-8-sig'), state, source_span=list(span) if span else None,
                               continuity_props=carry_declarations(authored))
    except text_core.TextValidationError as exc:
        if not isinstance(authored, dict) or authored.get('version') != 4:
            exc.errors.extend(storyboard_authoring_gate.source_mapping_errors(state, exc.units, authored))
        _raise_parsed_diagnostics(exc, state, source_trace=authored)
    actual_span = [units[0]['span'][0], units[-1]['span'][1]]
    if span is not None:
        d.require(list(span) == actual_span, '草稿未覆盖指定来源范围')
    else:
        scenes = {unit['main_scene'] for unit in units}
        records = [record for record in state['records'] if record['scene'] in scenes]
        d.require(actual_span == [records[0]['start'], records[-1]['end']],
                  '局部稿件绑定须提供准确--span')
    context = context_hash(state, project)
    mapping_errors = storyboard_authoring_gate.source_mapping_errors(state, units, authored)
    if mapping_errors:
        _raise_parsed_diagnostics(text_core.TextValidationError(mapping_errors, units), state)
    bound = storyboard_authoring_gate.bind_metadata(authored, raw, units, context)
    target = safe_output(state, project, output or storyboard_authoring_gate.trace_path(draft),
                         (draft, decisions))
    d.require(draft.read_bytes() == raw and context_hash(state, project) == context,
              '绑定期间输入发生变化，请重新读取')
    write_preview(target, (json.dumps(bound, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return {'status': 'bound', 'authoring_trace': str(target),
            'draft_sha256': bound['draft_sha256'], 'context_sha256': context,
            'semantic_review': 'not_performed',
            'limits': '仅核对明确来源定位并绑定元数据，未判断或生成创作内容，仍须完整preview和独立审阅。'}


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
    try:
        units, info = text_core._parse_for_delivery(raw, state, source_span=parse_span,
            continuity_props=storyboard_authoring_gate.candidate_carry_props(draft, authoring_trace))
    except text_core.TextValidationError as exc:
        trace = None
        if state.get('storyboard_authoring_policy') == storyboard_authoring_gate.POLICY:
            path = storyboard_authoring_gate.trace_path(draft, authoring_trace)
            try:
                trace = d.read_json(path)
            except OSError:
                exc.errors.append({'code': 'AUTHORING_TRACE_MISSING', 'message': f'缺少作者依据：{path}'})
            except ValueError:
                exc.errors.append({'code': 'AUTHORING_TRACE_FORMAT', 'message': '作者依据JSON无法读取'})
            else:
                if not isinstance(trace, dict):
                    exc.errors.append({'code': 'AUTHORING_TRACE_INVALID', 'message': '作者依据必须是对象'})
            if isinstance(trace, dict) and trace.get('version') == 4:
                expected = {'draft_sha256': hashlib.sha256(draft_bytes).hexdigest(),
                            'context_sha256': context_hash(state, project)}
                for field, value in expected.items():
                    if trace.get(field) != value:
                        exc.errors.append({'code': 'AUTHORING_TRACE_BINDING',
                                           'message': f'作者依据绑定不匹配：{field}；旧依据不用于当前来源诊断'})
                        trace = None
                        break
        _raise_parsed_diagnostics(exc, state, replace_ids, check_camera=check_camera, source_trace=trace)
    context = context_hash(state, project)
    authoring = storyboard_authoring_gate._validate_for_delivery(
        project, state, draft_bytes, units, context, authoring_trace,
        draft_path=draft, observations_summary=info['observations'])
    from speech_timing_audit import text_dialogue_capacity
    info['dialogue_capacity'] = text_dialogue_capacity(units)
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
    diagnostics = _delivery_errors({'errors': []}, info['dialogue_capacity'])
    if check_camera:
        try:
            require_camera_constraints(camera)
        except ValueError:
            if not camera['errors']:
                raise
            diagnostics.extend(_delivery_errors(camera, {'errors': []}))
    try:
        storyboard_authoring_gate.require(authoring)
    except storyboard_authoring_gate.AuthoringGateError as exc:
        # Preserve the gate report while collecting independent delivery errors.
        exc.errors = list(authoring.get('errors', [])) + diagnostics
        if diagnostics:
            exc.args = (str(exc) + '；' + '；'.join(row['message'] for row in diagnostics),)
        raise
    if diagnostics:
        raise text_core.TextValidationError(diagnostics)
    info['camera_constraints'] = camera
    info['warnings'].extend(camera['warnings'])
    info['warnings'].extend(authoring.get('warnings', []))
    binding = {'draft_sha256': hashlib.sha256(draft_bytes).hexdigest(),
               'context_sha256': context,
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


def _historical_render_state(state, receipt):
    """Make a verified historical receipt renderable after a replacement.

    A replacement may remove the old unit from ``state['units']`` while the
    receipt remains the source of truth for re-export.  Verify the receipt
    against the saved session before projecting its exact units into a
    read-only render state; this is not a general authorization bypass.
    """
    d.require(isinstance(receipt, dict), '历史回执格式无效')
    request_id = receipt.get('request_id')
    saved = next((row for row in state.get('receipts', [])
                  if row.get('request_id') == request_id), None)
    d.require(saved is not None and saved == receipt,
              '历史回执未与项目中保存的回执一致；不能直接重导出')
    projected = copy.deepcopy(state)
    current = projected.setdefault('units', [])
    for unit in receipt.get('units', []):
        if unit not in current:
            current.append(copy.deepcopy(unit))
    return projected


def render_units(state, units, preview=False, *, authoring_gate=None,
                 historical_receipt=None):
    render_state = (_historical_render_state(state, historical_receipt)
                    if historical_receipt is not None else state)
    results = []
    counts = {}
    scenes = {scene['id']: scene for scene in render_state['ledger']['scenes']}
    context = (text_core._context(render_state)
               if any(u.get('output_format') == text_core.OUTPUT_FORMAT for u in units) else None)
    for index, value in enumerate(units, 1):
        unit = copy.deepcopy(value)
        d.require(unit.get('export_format') in (None, EXPORT_FORMAT), '未知的文字分镜导出版式')
        if preview and unit.get('naming_policy') != segment_naming.POLICY:
            scene = unit['main_scene']
            counts[scene] = counts.get(scene, 0) + 1
            unit['display_name'] = f'预览{index:02d}-场景{scenes[scene]["number"]}-分镜{counts[scene]}'
        if unit.get('output_format') == text_core.OUTPUT_FORMAT:
            results.append(text_core._render_unit(render_state, unit, context,
                                                 authoring_gate=authoring_gate))
        else:
            results.append(d.render_unit(render_state, unit, authoring_gate=authoring_gate))
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


def _require_current_preview(project, draft, state, info):
    d.require(hashlib.sha256(draft.read_bytes()).hexdigest() == info['draft_sha256'],
              '作者稿在预览期间变化，请重新运行预览')
    d.require(context_hash(state, project) == info['context_sha256'],
              '分镜上下文在预览期间变化，请重新运行预览和核对审阅')
    _require_current_carry_trace(info)


def _require_current_carry_trace(info):
    gate = info.get('authoring_gate', {})
    if gate.get('continuity_props'):
        d.require(d.file_hash(gate['trace_path']) == gate['trace_sha256'],
                  '跨场道具依据在执行期间变化，请重新绑定并核对审阅')


def preview(project, draft, output=None, span=None, replace_ids=None, activity_id=None,
            authoring_trace=None):
    from project_paths import assert_writable
    assert_writable(project)
    project, draft = Path(project).resolve(), Path(draft).resolve()
    state, units, info = prepare(project, draft, span, replace_ids,
                                 authoring_trace=authoring_trace)
    payload = render_units(state, units, preview=True,
                           authoring_gate=info.get('authoring_gate')).encode('utf-8')
    artifact = {key: info[key] for key in ('draft_sha256', 'context_sha256', 'span', 'scenes')}
    artifact.update(draft=str(draft), output_sha256=hashlib.sha256(payload).hexdigest())
    if info.get('authoring_gate', {}).get('continuity_props'):
        artifact['authoring_trace_sha256'] = info['authoring_gate']['trace_sha256']
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
                    _require_current_preview(project, draft, state, info)
                    return {**info, 'output': str(target), 'output_sha256': artifact['output_sha256'],
                            'preview_reused': True, 'preview_event_ids': [row['event_id'] for row in matches]}
        else:
            raise ValueError('预览版本路径已耗尽；保留现场并选择新的输出路径')
    _require_current_preview(project, draft, state, info)
    try:
        write_preview(target, payload)
    except FileExistsError as exc:
        raise ValueError('预览路径已被占用；未覆盖文件，请重新运行自动路径预览') from exc
    d.require(target.read_bytes() == payload, '预览写入核验失败，未记录成功事件')
    artifact['output'] = str(target)
    events = []
    try:
        events = storyboard_metrics._record_preview_events(
            project, info['scenes'], at=now(), activity_id=activity_id, artifact=artifact)
    except (OSError, ValueError) as exc:
        raise ValueError(f'预览文件已写入，但成功事件未全部记录；保留产物 {target}，请核对日志：{exc}') from exc
    return {**info, 'output': str(target), 'output_sha256': artifact['output_sha256'],
            'preview_reused': False, 'preview_event_ids': [row['event_id'] for row in events]}


def event(project, name, scene=None, note=None, **fields):
    return storyboard_metrics.record_event(project, name, at=now(), scene=scene, note=note, **fields)


def timing(project, command_logs=None):
    return storyboard_metrics.timing(project, command_logs=command_logs)


def _recover_committed(project, state, receipt, draft, output, span, replace_ids, authoring_trace):
    """Recover the exact accepted artifact before consulting current context."""
    from continuity import carry_declarations, unit_carry_props
    d.require(receipt.get('policy') == POLICY, 'request_id已用于其他内容；不能覆盖历史请求')
    units = receipt.get('units')
    d.require(isinstance(units, list) and units, '历史回执缺少完整单元')
    raw = Path(draft).read_bytes()
    draft_hash = hashlib.sha256(raw).hexdigest()
    final_review = receipt.get('final_review', {})
    d.require(final_review.get('draft_sha256') == draft_hash,
              'request_id已用于其他内容；作者稿与原交付不同')
    saved_span = [units[0]['span'][0], units[-1]['span'][1]]
    d.require(span is None or list(span) == saved_span, '重试来源范围与原交付不同')
    mode = 'replace' if replace_ids else 'append'
    d.require(mode == receipt.get('mode'), '重试方式与原交付不同')
    if replace_ids:
        d.require(list(replace_ids) == [u['id'] for u in units], '重试替换ID与原交付不同')
    header = units[0].get('header_text')
    if header is None:
        header = read_header(project, state, required=bool(state.get('header_hash')))
    binding = {'policy': POLICY, 'draft_sha256': draft_hash, 'mode': mode,
               'replace_ids': replace_ids or [], 'span': saved_span,
               'header_sha256': d.sha(header) if header is not None else None,
               'video_ratios': list(dict.fromkeys(u.get('video_ratio') for u in units
                                                if u.get('video_ratio') is not None)),
               'ratio_profiles': list(dict.fromkeys(u['ratio_profile'] for u in units
                                                   if u.get('ratio_profile')))}
    carried = unit_carry_props(units)
    if carried:
        binding['continuity_props'] = carried
    d.require(digest(binding) == receipt.get('request_hash'),
              'request_id已用于其他内容；历史请求绑定不一致')
    # Compare syntax projections to the exact original bytes. Do not rejudge
    # old creative choices or revalidate anchors against later replacements.
    context = text_core._slice_state(state, saved_span)
    parsed = text_core._parse_units(raw.decode('utf-8-sig'), context)
    d.require(len(parsed) == len(units), '历史回执单元数与作者稿不一致')
    for actual, frozen in zip(parsed, units):
        for field, value in actual.items():
            if field != 'span':  # Shared dialogue boundaries own punctuation after checking.
                d.require(frozen.get(field) == value, f'历史回执派生字段与作者稿不一致：{field}')
    gate = receipt.get('authoring_gate') or {}
    scenes = list(dict.fromkeys(sid for u in units for sid in u['scene_ids']))
    frozen_info = {'draft_sha256': draft_hash, 'context_sha256': final_review.get('context_sha256'),
                   'scenes': scenes, 'authoring_gate': gate}
    if gate.get('enforced'):
        d.require(gate.get('status') == 'passed', '历史作者闸门未通过')
        trace_path = storyboard_authoring_gate.trace_path(draft, authoring_trace)
        trace_raw = trace_path.read_bytes()
        d.require(hashlib.sha256(trace_raw).hexdigest() == gate.get('trace_sha256'),
                  'request_id已用于其他内容；作者依据与原交付不同')
        trace = json.loads(trace_raw.decode('utf-8-sig'))
        d.require(carry_declarations(trace) == carried, '历史跨场道具承诺与原作者依据不一致')
        shot_keys = [f'{u["draft_key"]}#{i}' for u in units for i in range(1, len(u['shots']) + 1)]
        d.require(gate.get('shot_keys') == shot_keys and gate.get('shot_count') == len(shot_keys),
                  '历史作者闸门镜头范围与回执不一致')
        payload = {'policy': gate['policy'], 'version': gate['version'],
                   'trace_sha256': gate['trace_sha256'], 'draft_sha256': draft_hash,
                   'context_sha256': final_review.get('context_sha256'),
                   'shot_count': len(shot_keys), 'source_span': trace.get('source_span')}
        if carried:
            payload['continuity_props'] = carried
        d.require(digest(payload) == gate.get('gate_digest'), '历史作者闸门绑定摘要不一致')
    verify_review(final_review, frozen_info)
    d.require(Path(output).resolve() == Path(receipt['output']).resolve(),
              '重试须沿用原交付路径；另行导出使用render')
    target = d.validate_output_path(state, project, output)
    rendered = render_units(state, units, authoring_gate=gate, historical_receipt=receipt)
    if receipt.get('output_sha256') is not None:
        d.require(hashlib.sha256(rendered.encode('utf-8')).hexdigest() == receipt['output_sha256'],
                  '历史回执与原交付内容摘要不一致')
    # Recheck the saved receipt and both caller-owned inputs at the write boundary.
    current = d.load(project)
    d.require(next((r for r in current['receipts'] if r['request_id'] == receipt['request_id']), None) == receipt,
              '恢复期间历史回执变化，请重读后重试')
    d.require(hashlib.sha256(Path(draft).read_bytes()).hexdigest() == draft_hash, '恢复期间作者稿变化')
    if gate.get('enforced'):
        d.require(hashlib.sha256(trace_path.read_bytes()).hexdigest() == gate['trace_sha256'],
                  '恢复期间作者依据变化')
    if target.exists():
        d.require(target.read_text(encoding='utf-8-sig') == rendered,
                  '已交付文件被外部修改；保留现场，使用render另存')
    else:
        write_preview(target, rendered.encode('utf-8'))
    return {'status': 'already_committed', 'output': str(target), **d.receipt_summary(receipt)}


def commit(project, draft, request_id, review, output, span=None,
           replace_ids=None, range_confirmation=None, authoring_trace=None):
    from project_paths import assert_writable
    assert_writable(project)
    project = Path(project).resolve()
    d.require(isinstance(request_id, str) and request_id.strip(), 'request_id不能为空')
    current = d.load(project)
    prior = next((r for r in current['receipts'] if r['request_id'] == request_id), None)
    if prior is not None:
        return _recover_committed(project, current, prior, draft, output, span,
                                  replace_ids, authoring_trace)
    state, units, info = prepare(project, draft, span, replace_ids, check_camera=False,
                                 authoring_trace=authoring_trace)
    mode = 'replace' if replace_ids else 'append'
    request_binding = {'policy': POLICY, 'draft_sha256': info['draft_sha256'],
                           'mode': mode, 'replace_ids': replace_ids or [], 'span': info['span'],
                           'header_sha256': info.get('header_sha256'),
                           'video_ratios': info.get('video_ratios', []),
                           'ratio_profiles': [p['profile_id'] for p in info.get('ratio_profiles', [])]}
    from continuity import unit_carry_props
    carried = unit_carry_props(units)
    if carried:
        request_binding['continuity_props'] = carried
    request_hash = digest(request_binding)
    require_camera_constraints(info['camera_constraints'])
    final_review = verify_review(d.read_json(review), info)
    _require_current_carry_trace(info)
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
        _require_current_carry_trace(info)
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
        rendered = render_units(working, units,
                                authoring_gate=info.get('authoring_gate'))
        receipt['output_sha256'] = hashlib.sha256(rendered.encode('utf-8')).hexdigest()
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
    p = commands.add_parser('bind-trace', help='为已写好的v4依据绑定元数据并另存，不改创作内容')
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--draft', type=Path, required=True)
    p.add_argument('--decisions', type=Path, required=True)
    p.add_argument('--output', type=Path)
    p.add_argument('--span', type=int, nargs=2)
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
    p.add_argument('--reason', choices=sorted(storyboard_metrics.WAIT_REASONS),
                   help='review_waiting的当前实际原因，配合--note说明观察依据')
    p = commands.add_parser('invalidate-event', help='追加撤销错误阶段事件的记录，不修改原日志')
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--event-id', required=True)
    p.add_argument('--note', required=True)
    p = commands.add_parser('timing')
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--command-log', type=Path, action='append', default=[],
                   help='可重复指定已有真实命令计时JSON，与创作及审阅窗口分别展示')
    p = commands.add_parser('context', help='只读导出一场来源、合法引用、版本与已交付边界')
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--scene', required=True)
    p.add_argument('--span', type=int, nargs=2)
    p.add_argument('--authoring-trace', type=Path,
                   help='可选v4作者依据，仅展示其中显式声明的本场延续道具')
    args = parser.parse_args()
    try:
        if args.command == 'bind-trace':
            result = bind_trace(args.project, args.draft, args.decisions, args.output, args.span)
        elif args.command == 'check':
            if args.incremental:
                d.require(not args.replace_ids, '增量检查不接受--replace-ids；指定修改使用正常check/preview')
                result = check_incremental(args.project, args.draft, args.span, args.authoring_trace)
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
            d.atomic_text(target, render_units(
                state, receipt['units'], authoring_gate=receipt.get('authoring_gate'),
                historical_receipt=receipt))
            result = {'output':str(target), **d.receipt_summary(receipt)}
        elif args.command == 'event':
            d.require(args.name != 'delivered', 'delivered由实际commit保存后记录，不能手工宣告')
            result = event(args.project, args.name, args.scene, args.note,
                           activity_id=args.activity_id, coverage=args.coverage,
                           issue_kind=args.issue_kind, reason=args.reason)
        elif args.command == 'invalidate-event':
            result = storyboard_metrics.invalidate_event(args.project, args.event_id, at=now(), note=args.note)
        elif args.command == 'context':
            result = scene_context(args.project, args.scene, args.span, args.authoring_trace)
        else:
            result = timing(args.project, command_logs=args.command_log)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result = {'status': 'error', 'message': str(exc)}
        if isinstance(exc, storyboard_authoring_gate.AuthoringGateError):
            result['errors'] = getattr(exc, 'errors', exc.report.get('errors', []))
            result['authoring_gate'] = exc.report
        elif isinstance(exc, text_core.TextValidationError):
            result['errors'] = exc.errors
        print(json.dumps(result, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
