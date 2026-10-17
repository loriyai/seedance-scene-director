"""Versioned authoring prerequisites. Mechanical checks never write approval.

Only scene-review-b-v2 opts in. Historical inputs and receipt renders stay intact.
The writer supplies semantic judgments; pending templates are diagnostic data.
"""
import copy
import hashlib
import json
import math
import re
from pathlib import Path

POLICY = 'scene-review-b-v2'
GENERIC = {'通过', '已检查', '符合剧情', '为了节奏', '符合要求', '无问题', '同前', '同上'}
FIELDS = ('text', 'composition', 'depth_of_field', 'visibility', 'continuity.changes',
          'initial/final', 'start/end_state')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def concrete(text):
    """Reject empty/template-only evidence, never infer quality from length."""
    if not isinstance(text, str) or not text.strip():
        return False
    residual = re.sub(r'[\W_]+', '', text)
    for phrase in sorted(GENERIC, key=len, reverse=True):
        residual = residual.replace(phrase, '')
    return bool(residual)


def error(code, kind, **kw):
    return {'code': code, 'kind': kind, **kw}


def audio_payload(state, unit):
    """One current plan; camera-only changes do not invalidate speech decisions."""
    from storyboard_preflight import _parts
    errors = []
    parts = _parts(unit, errors)
    normalized = {rid: [{k: v for k, v in p.items() if k != 'record'} for p in ps]
                  for rid, ps in parts.items()}
    ids = {e.get('record') for e in unit.get('audio', [])}
    span = unit.get('span')
    if isinstance(span, list) and len(span) == 2 and all(type(n) is int for n in span):
        ids.update(r.get('id') for r in state.get('records', [])
                   if type(r.get('start')) is int and type(r.get('end')) is int
                   and r['start'] < span[1] and r['end'] > span[0])
    rules = {name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
             for name in ('authoring_gates.py','storyboard_preflight.py','dialogue_layout.py','speech_timing_audit.py','audio_sources.py')}
    return {'policy':POLICY, 'rules':rules, 'audio': unit.get('audio', []), 'parts': normalized,
            'duration': unit.get('duration'), 'span': unit.get('span'),
            'actions': unit.get('action_plan'),
            'source': [r for r in state.get('records', []) if r.get('id') in ids or
                       r.get('id') in {a.get('record') for a in unit.get('action_plan', [])}],
            'characters': [c for c in state.get('ledger', {}).get('characters', []) if c.get('id') in
                           {cid for e in unit.get('audio', []) for cid in
                            e.get('character_ids', [e.get('character_id')])}],
            'voice_direction': unit.get('voice_direction'), 'dialogue_layout': unit.get('dialogue_layout')}


def audio_ready(state, unit, checked):
    """Separate structural pass from readiness for photography/freeze.

    Decisions refer to exact warning objects and the whole unit speech/action
    input. No generic warning exemptions, timing padding or automatic approvals.
    """
    from storyboard_preflight import _number, _parts, _source_audio
    errors = []
    errors.extend(_source_audio(state, unit))
    if not state.get('records'):
        errors.append(error('AUDIO_SOURCE_UNVERIFIED', '离线计划只证明内部结构；进入详细摄影须提供绑定来源'))
    span = unit.get('span')
    source_end = state.get('length', max((r.get('end', 0) for r in state.get('records', [])), default=0))
    if not (isinstance(span, list) and len(span) == 2 and all(type(n) is int for n in span)
            and type(source_end) is int and 0 <= span[0] < span[1] <= source_end):
        errors.append(error('AUDIO_SOURCE_SPAN', '动作/声音计划须先锁定合法且非空的来源范围'))
    duration = unit.get('duration')
    window_end = duration if _number(duration) else 0
    if type(duration) is not int or not 4 <= duration <= 30:
        errors.append(error('AUDIO_PLAN_DURATION', '先确定4至30秒合法整数单元时长'))
    entry = state.get('entry', {})
    if (type(duration) is int and (duration < entry.get('min', 4) or
            entry.get('durations') and duration not in entry['durations'])):
        errors.append(error('AUDIO_ENTRY_DURATION', '声音计划时长不在本项目已确认入口档位'))
    if unit.get('audio') and (not isinstance(unit.get('audio_plan'), list) or not unit['audio_plan']):
        errors.append(error('AUDIO_CANONICAL_PLAN', '有声新单元须保留唯一audio_plan并从该计划同步dialogue_parts'))
    actions = unit.get('action_plan')
    if not isinstance(actions, list):
        errors.append(error('AUDIO_ACTION_PLAN', '须登记action_plan；无必须先后动作时用空数组并在语义核对中说明'))
        actions = []
    records = {r.get('id'): r for r in state.get('records', [])}
    events = {e.get('record'): e for e in unit.get('audio', [])}
    for a in actions:
        if not isinstance(a, dict):
            errors.append(error('AUDIO_ACTION_WINDOW', '动作骨架须为对象')); continue
        source = records.get(a.get('record'), {})
        from continuity import portion
        span = unit.get('span')
        selected = (portion(source, span) if source and isinstance(span, list) and len(span)==2 and
                    all(type(n) is int for n in span) and all(k in source for k in ('start','end')) else '')
        if (not isinstance(a.get('quote'), str) or not a['quote'] or
                a['quote'] not in selected or
                not all(_number(a.get(k)) for k in ('start', 'end')) or
                not 0 <= a['start'] < a['end'] <= window_end):
            errors.append(error('AUDIO_ACTION_WINDOW', '动作骨架须绑定原文及合法时间窗', record=a.get('record')))
            continue
        for relation in ('before', 'after'):
            rid = a.get(relation)
            if rid is not None and (rid not in events or
                    (a['end'] > events[rid]['start'] + .001 if relation == 'before'
                     else a['start'] < events[rid]['end'] - .001)):
                errors.append(error('AUDIO_ACTION_ORDER', '必须先后的动作和台词时间窗冲突', record=a.get('record')))
    all_parts = _parts(unit, [])
    for rid, parts in all_parts.items():
        for index, part in enumerate(parts, 1):
            if all(_number(part.get(k)) for k in ('start', 'end')):
                if (not 0 <= part['start'] < part['end'] <= window_end or
                        math.ceil(part['end'] - .001) - math.floor(part['start'] + .001) > 6):
                    errors.append(error('AUDIO_CUT_WINDOW', '片段无法完整容纳于1至6秒整数切点镜头；先调整自然分句或时间安排', record=rid, part=index))
    if type(duration) is int and 4 <= duration <= 30:
        windows = [p for ps in all_parts.values() for p in ps
                   if all(_number(p.get(k)) for k in ('start','end'))]
        allowed = {t for t in range(duration+1) if not any(p['start']+.001 < t < p['end']-.001 for p in windows)}
        # Keep counts as well as reachability: legal shot durations alone
        # do not establish a partition satisfying the existing 8-13 rule.
        reachable = {0: {0}}
        for t in range(1,duration+1):
            if t in allowed:
                counts = {n + 1 for k in range(1, 7)
                          for n in reachable.get(t - k, set())}
                if counts:
                    reachable[t] = counts
        if duration not in reachable:
            errors.append(error('AUDIO_CUT_PARTITION', '所有语义片段无法共同构成1至6秒整数镜头分区；须在摄影前调整分句/切点'))
        elif duration >= 26 and not reachable[duration].intersection(range(8, 14)):
            errors.append(error('AUDIO_CUT_COUNT', '当前片段只允许与8至13镜规则冲突的整数切点组合；须在摄影前调整自然分句/切点',
                                feasible_counts=sorted(reachable[duration])))
    payload = audio_payload(state, {**unit, 'action_plan': actions})
    binding = digest(payload)
    review = unit.get('audio_preflight', {})
    if not (isinstance(review, dict) and review.get('input_sha256') == binding and
            concrete(review.get('basis')) and isinstance(review.get('reviewer'), str) and review['reviewer'].strip()):
        errors.append(error('AUDIO_SEMANTIC_PENDING', '当前声音/动作骨架尚未由写者具体核对，或旧依据已失效'))
    decisions = review.get('warning_decisions', []) if isinstance(review, dict) else []
    if not isinstance(decisions, list):
        decisions = []
        errors.append(error('AUDIO_WARNING_SCHEMA', '声音警告处置须为数组'))
    warnings = {digest(w): w for w in checked['warnings']}
    seen, pending = set(), []
    for d in decisions:
        if not isinstance(d, dict):
            errors.append(error('AUDIO_WARNING_SCHEMA', '声音警告处置须为对象')); continue
        key = d.get('warning_sha256')
        if key in seen or key not in warnings or d.get('warning') != warnings.get(key):
            errors.append(error('AUDIO_WARNING_STALE', '声音警告原对象已变化、重复或不属于当前计划'))
        seen.add(key)
    for key, warning in warnings.items():
        matching = [d for d in decisions if isinstance(d, dict) and d.get('warning_sha256') == key]
        parts = _parts(unit, []).get(warning.get('record'), [])
        n = warning.get('part', 0)
        part = parts[n-1] if type(n) is int and 1 <= n <= len(parts) else events.get(warning.get('record'), {})
        detail = {'text': part.get('text'), 'window': {k:part.get(k) for k in ('start','end','pause')}}
        if (len(matching) != 1 or matching[0].get('decision') != 'accepted' or
                not concrete(matching[0].get('basis')) or any(matching[0].get(k)!=v for k,v in detail.items())):
            pending.append({'warning_sha256': key, 'warning': warning, **detail, 'decision': 'pending', 'basis': ''})
    return {'status': 'ready' if not errors and not pending and not checked['errors'] else 'blocked',
            'errors': errors, 'pending_warnings': pending, 'input_sha256': binding,
            'semantic_template': {'input_sha256': binding, 'reviewer': '', 'basis': '',
                                  'warning_decisions': pending}}


def staging_material(state, unit):
    """Actual rendered text plus backend time/state, never purpose alone."""
    from director import render_unit, HEADER_POLICY
    from shot_review import targets
    from continuity import validate_unit
    replay = validate_unit(state, unit, required=True)
    current = copy.deepcopy(unit)
    current.setdefault('id', '待编号')
    from director import sha
    if not isinstance(current.get('style'), str) or sha(current['style']) != state.get('style_hash'):
        raise ValueError('冻结前预览须填写与已保存video-style.txt摘要一致的style')
    current['black_reference_mode'] = 'none-v1'
    current['header_policy'] = HEADER_POLICY
    rendered = render_unit(state, current)
    shots = targets(state, {'units': [unit]})
    fields = {k: v for k, v in unit.items() if k not in
              ('staging_review', 'audio_preflight', 'identity_decisions', 'review_dependencies')}
    # A per-unit target projection cannot carry an edge whose destination is in
    # another unit. Bind the complete declaration once at its owning unit in v2;
    # historical v1 material and digests retain their original shape.
    from shot_dependencies import POLICY_V2
    declaration = unit.get('review_dependencies')
    if isinstance(declaration, dict) and declaration.get('policy') == POLICY_V2:
        fields['review_dependencies'] = copy.deepcopy(declaration)
    return {'rendered': rendered, 'timing_and_state': fields, 'targets': shots,
            'replay': replay}


def staging_pending(state, unit):
    material = staging_material(state, unit)
    checks = []
    for t in material['targets']:
        # Every actual state event prompts correlated field inspection. Even a
        # no-change shot can carry stale posture: it remains in actual render.
        if t['shot'].get('continuity', {}).get('changes'):
            checks.append({'shot': t['shot_index'], 'fields': list(FIELDS), 'status': 'pending', 'basis': ''})
    return {'input_sha256': digest(material), 'reviewer': '', 'status': 'pending',
            'render_basis': '', 'timing_state_basis': '', 'change_checks': checks}


def validate_staging(state, unit):
    expected = staging_pending(state, unit)
    actual = unit.get('staging_review', {})
    if not isinstance(actual, dict) or actual.get('input_sha256') != expected['input_sha256']:
        raise ValueError('冻结前实际渲染/后台时码状态检查缺失或过期；运行staging_preflight准备当前清单')
    if not (actual.get('status') == 'reviewed' and actual.get('reviewer') and
            concrete(actual.get('render_basis')) and concrete(actual.get('timing_state_basis'))):
        raise ValueError('冻结前须实读同版本渲染和时码状态，填写具体依据')
    checks = actual.get('change_checks')
    if not isinstance(checks, list) or len(checks) != len(expected['change_checks']):
        raise ValueError('状态事件关联字段核对缺项')
    for a, e in zip(checks, expected['change_checks']):
        if (a.get('shot') != e['shot'] or a.get('fields') != e['fields'] or
                a.get('status') != 'reviewed' or not concrete(a.get('basis'))):
            raise ValueError('状态变化后须核对实际关联字段，未变字段可说明保持依据')
