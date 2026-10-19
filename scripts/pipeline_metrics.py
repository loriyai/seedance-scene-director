"""Record actual local phase events and counts without estimating model time or savings."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

STAGES = {'standardize', 'assets', 'design', 'review', 'delivery', 'storyboard', 'revise', 'sync'}
STATUSES = {'started', 'draft_saved', 'checked', 'needs_review', 'compiled', 'failed', 'finished', 'cancelled',
            'work_started', 'work_finished'}
CHECK_KINDS = {'structure', 'source', 'semantic', 'package', 'inputs'}
WORK_CATEGORIES = {'confirmation', 'author_revision', 'review_revision', 'tool_repair'}


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('时间须带时区')
    return parsed


def record_event(project, stage, task_key, status, *, object_ids=(), input_digest=None,
                 reason=None, actual_model=None, tokens=None, occurred_at=None,
                 attempt_id=None, revision_digest=None, check_kind=None,
                 work_id=None, category=None, parent_work_id=None, measurement_scope=None):
    if stage not in STAGES or status not in STATUSES or not isinstance(task_key, str) or not task_key.strip():
        raise ValueError('阶段、任务标记或状态无效')
    ids = list(object_ids)
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError('对象ID须非空且不重复')
    if tokens is not None and (type(tokens) is not int or tokens < 0):
        raise ValueError('tokens只登记宿主提供的实际非负整数，未知留空')
    if check_kind is not None and (check_kind not in CHECK_KINDS or status not in {'checked', 'needs_review', 'failed'}):
        raise ValueError('check_kind仅用于检查事件，须明确实际检查类型')
    if status in {'work_started', 'work_finished'}:
        if not isinstance(work_id, str) or not work_id.strip() or category not in WORK_CATEGORIES:
            raise ValueError('工作窗口须提供work_id及category')
        if status == 'work_started' and not (isinstance(reason, str) and reason.strip()):
            raise ValueError('窗口开始须说明实际确认事项或问题，不把所有检查都记为返工')
    elif any(value is not None for value in (work_id, category, parent_work_id)):
        raise ValueError('work_id/category/parent_work_id仅用于工作窗口')
    if parent_work_id is not None and (not isinstance(parent_work_id, str) or not parent_work_id.strip() or parent_work_id == work_id):
        raise ValueError('parent_work_id须非空且不能指向自身')
    when = occurred_at or datetime.now(timezone.utc).isoformat()
    stamp(when)
    entry = {'schema_version': 2, 'event_id': str(uuid.uuid4()), 'stage': stage, 'task_key': task_key, 'status': status,
             'object_ids': ids, 'input_digest': input_digest, 'reason': reason,
             'actual_model': actual_model, 'tokens': tokens, 'occurred_at': when,
             'revision_digest': revision_digest, 'check_kind': check_kind,
             'work_id': work_id, 'category': category, 'parent_work_id': parent_work_id}
    if attempt_id is not None:
        entry['attempt_id'] = attempt_id
    if measurement_scope is not None:
        entry['measurement_scope'] = measurement_scope
    path = Path(project) / 'pipeline-events.jsonl'
    if path.exists():
        previous = [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        task_events = [event for event in previous if event['stage'] == stage and event['task_key'] == task_key
                       and event.get('attempt_id') == attempt_id]
        if task_events and stamp(when) < stamp(task_events[-1]['occurred_at']):
            raise ValueError('同一任务事件时间倒序，未追加事件')
        # Reject accidental reuse before append so the append-only log remains
        # queryable. Unknown endpoints remain valid and explicitly unmeasured.
        if work_id is not None:
            related = [event for event in task_events if event.get('work_id') == work_id]
            work_windows([*related, entry])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return entry


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def handoff_input_digest(manifest):
    """Identify frozen inputs; this does not re-verify or update their hashes."""
    value = {'inputs': manifest.get('inputs'), 'session_creative': manifest.get('session_creative')}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def record_handoff_event(handoff, status, *, revision_file=None, **fields):
    path = Path(handoff).resolve()
    manifest = json.loads(path.read_text(encoding='utf-8-sig'))
    if manifest.get('routing_policy') != 'stage-handoff-v2':
        raise ValueError('旧交接仅按原证据读取，不补写新生命周期事件')
    project = Path(manifest['project']).resolve()
    if path != project / 'agents' / manifest['id'] / 'handoff.json':
        raise ValueError('交接路径与项目或attempt_id不一致')
    state_path = path.parent / 'handoff-state.json'
    state = json.loads(state_path.read_text(encoding='utf-8-sig')) if state_path.exists() else {}
    if state.get('status') != 'active':
        raise ValueError('只记录当前活动交接的真实事件，已关闭交接不回填')
    if status in {'started', 'finished', 'cancelled'}:
        raise ValueError('交接起止直接读取prepare/close，不重复手工登记')
    when = fields.get('occurred_at') or datetime.now(timezone.utc).isoformat()
    if stamp(when) < stamp(manifest['prepared_at']):
        raise ValueError('关联事件不能早于交接prepare；未知开始不回填')
    fields['occurred_at'] = when
    if status in {'checked', 'needs_review', 'failed'} and not fields.get('check_kind'):
        raise ValueError('交接检查事件须明确check_kind；结构检查不代表语义通过')
    if revision_file is not None:
        revision_path = Path(revision_file).resolve()
        if (path.parent / 'output') not in revision_path.parents or not revision_path.is_file():
            raise ValueError('revision_file须是本交接output中的实际稿件或检查输入清单')
        fields['revision_digest'] = file_digest(revision_path)
    if status in {'draft_saved', 'checked', 'needs_review', 'failed'} and not fields.get('revision_digest'):
        raise ValueError('稿件或检查事件须绑定实际revision_file，不能把冻结来源当作稿件版本')
    return record_event(project, manifest['stage'], manifest['brief']['scope']['task_key'], status,
                        attempt_id=manifest['id'], input_digest=handoff_input_digest(manifest),
                        measurement_scope='handoff_prepare_to_close', **fields)


def interval_totals(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    union = sum((end - start).total_seconds() for start, end in merged)
    total = sum((end - start).total_seconds() for start, end in spans)
    return {'interval_count': len(spans), 'seconds_sum': total,
            'interval_union_seconds': union, 'interval_overlap_seconds': total - union}


def work_windows(items):
    """Explicit work windows, never inferred from failed checks or file mtimes."""
    grouped = {}
    for item in items:
        if item['status'] in {'work_started', 'work_finished'}:
            grouped.setdefault(item['work_id'], []).append(item)
    rows, spans = [], []
    by_category = {name: [] for name in sorted(WORK_CATEGORIES)}
    for identifier, events in grouped.items():
        categories = {event['category'] for event in events}
        parents = {event.get('parent_work_id') for event in events}
        starts = [stamp(event['occurred_at']) for event in events if event['status'] == 'work_started']
        ends = [stamp(event['occurred_at']) for event in events if event['status'] == 'work_finished']
        if len(categories) != 1 or len(parents) != 1 or len(starts) > 1 or len(ends) > 1:
            raise ValueError('同一work_id只能代表一个类别和一对起止；新一轮使用新的work_id')
        category, parent = next(iter(categories)), next(iter(parents))
        start, end = starts[0] if starts else None, ends[0] if ends else None
        if start is not None and end is not None and end < start:
            raise ValueError('工作窗口结束早于开始')
        elapsed = (end - start).total_seconds() if start is not None and end is not None else None
        if elapsed is not None:
            spans.append((start, end))
            by_category[category].append((start, end))
        rows.append({'work_id': identifier, 'category': category, 'parent_work_id': parent,
                     'started_at': start.isoformat() if start else None,
                     'finished_at': end.isoformat() if end else None, 'elapsed_seconds': elapsed,
                     'coverage': 'missing_start' if start is None else 'missing_finish' if end is None else 'bounded',
                     'input_digests': list(dict.fromkeys(e['input_digest'] for e in events if e.get('input_digest'))),
                     'revision_digests': list(dict.fromkeys(e['revision_digest'] for e in events if e.get('revision_digest'))),
                     'reasons': list(dict.fromkeys(e['reason'] for e in events if e.get('reason')))})
    revision_spans = [span for name, values in by_category.items() if name != 'confirmation' for span in values]
    return {'windows': rows, **interval_totals(spans),
            'revision_windows': interval_totals(revision_spans),
            'by_category': {name: interval_totals(values) for name, values in by_category.items()},
            'limits': '窗口含等待、修订及复验；确认成本与作者自修、主审退回、工具修复分别显示。嵌套或重叠只按并集计覆盖时间，各类别之和不可当净返工；缺起点或终点不补算。'}


def summarize(events):
    tasks, seen = {}, set()
    for event in events:
        if event['event_id'] in seen:
            raise ValueError('重复事件不可重复计数')
        seen.add(event['event_id'])
        # Retries may intentionally share a logical task_key. A handoff attempt
        # still has its own measured interval; do not bridge the retry gap.
        key = (event['stage'], event['task_key'], event.get('attempt_id', ''))
        tasks.setdefault(key, []).append(event)
    rows, spans, first_checks, typed_checks = [], [], [], []
    for (stage, key, attempt), items in sorted(tasks.items()):
        if any(stamp(b['occurred_at']) < stamp(a['occurred_at']) for a, b in zip(items, items[1:])):
            raise ValueError('同一任务事件时间倒序')
        start = next((stamp(item['occurred_at']) for item in items if item['status'] == 'started'), None)
        finish = next((stamp(item['occurred_at']) for item in reversed(items) if item['status'] in ('finished', 'cancelled')), None)
        elapsed = (finish - start).total_seconds() if start is not None and finish is not None else None
        if elapsed is not None and elapsed < 0:
            raise ValueError('结束早于开始')
        if elapsed is not None:
            spans.append((start, finish))
        # Versions have independent first checks, rather than counting a failed
        # old input and a new input as one repeated failure.
        checks = {}
        for item in items:
            version = ('/'.join((item['input_digest'], item['revision_digest']))
                       if item.get('input_digest') and item.get('revision_digest')
                       else item.get('revision_digest') or item.get('input_digest'))
            if item['status'] in ('checked', 'needs_review', 'failed') and version:
                kind = item.get('check_kind') or 'unspecified'
                identity = f'{kind}:{version}' if item.get('check_kind') else version
                if identity not in checks:
                    checks[identity] = item['status']
                    typed_checks.append({'kind': kind, 'status': item['status']})
        first_checks.extend(checks.values())
        current = {}
        for item in items:
            if item['status'] not in {'work_started', 'work_finished'}:
                for aid in item['object_ids']:
                    current[aid] = item['status']
        coverage = ('missing_start' if start is None else 'missing_finish' if finish is None else
                    'late_start' if any(stamp(item['occurred_at']) < start for item in items) else 'bounded')
        draft = next((stamp(e['occurred_at']) for e in items if e['status'] == 'draft_saved'), None)
        first_check_times = {}
        for event in items:
            if event['status'] in {'checked', 'needs_review', 'failed'}:
                first_check_times.setdefault(event.get('check_kind') or 'unspecified', event['occurred_at'])
        rows.append({'stage': stage, 'task_key': key, 'status': items[-1]['status'],
                     'attempt_id': attempt or None,
                     'measurement_scope': items[0].get('measurement_scope', 'manual_events'),
                     'coverage': coverage,
                     'started_at': start.isoformat() if start is not None else None,
                     'finished_at': finish.isoformat() if finish is not None else None,
                     'elapsed_seconds': elapsed,
                     'counts': {status: sum(value == status for value in current.values())
                                for status in ('draft_saved', 'checked', 'needs_review', 'compiled')},
                     'current_object_status': current,
                     'unbound_check_events': sum(item['status'] in ('checked', 'needs_review', 'failed')
                                                and not (item.get('revision_digest') or item.get('input_digest')) for item in items),
                     'rework_ids': sorted({aid for item in items if item['status'] == 'needs_review' for aid in item['object_ids']}),
                     'rework_ids_basis': '兼容字段：needs_review对象仅表示待复核，不等于已发生返工。实际确认与修订分别看work中的窗口类别。',
                     'reasons': list(dict.fromkeys(item['reason'] for item in items if item.get('reason'))),
                     'first_checks': checks,
                     'milestones': {
                         'first_draft_at': draft.isoformat() if draft is not None else None,
                         'start_to_first_draft_seconds': (draft - start).total_seconds()
                             if start is not None and draft is not None and draft >= start else None,
                         'first_checks_at': first_check_times,
                         'first_draft_to_first_check_seconds': {kind: (stamp(when) - draft).total_seconds()
                             if draft is not None and stamp(when) >= draft else None
                             for kind, when in first_check_times.items()}},
                     'work': work_windows(items)})
    wall = (max(end for _, end in spans) - min(start for start, _ in spans)).total_seconds() if spans else None
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    union = sum((end - start).total_seconds() for start, end in merged)
    total = sum((b - a).total_seconds() for a, b in spans)
    return {'tasks': rows, 'completed_window_seconds': wall,
            'completed_interval_count': len(spans),
            'completed_task_seconds_sum': total,
            'completed_interval_union_seconds': union,
            'completed_interval_overlap_seconds': total - union,
            'completed_window_uncovered_seconds': wall - union if wall is not None else None,
            'incomplete_or_late_tasks': sum(row['coverage'] != 'bounded' for row in rows),
            'total_pipeline_seconds': None,
            'first_check_passed': sum(value == 'checked' for value in first_checks),
            'first_check_total': len(first_checks),
            'first_checks_by_kind': {kind: {'passed': sum(e['kind'] == kind and e['status'] == 'checked' for e in typed_checks),
                                           'total': sum(e['kind'] == kind for e in typed_checks)}
                                     for kind in sorted({e['kind'] for e in typed_checks})},
            'limits': '仅覆盖已记录的起止区间；窗口可含等待与未覆盖间隔，任务之和可含并行重叠。缺失或晚登记开始不补算；未知前后区间不计入全流程。首检按稿件版本和check_kind区分，未分类旧检查不代表语义通过；finished也不等于语义通过。不能据此声称模型计算时间、工具耗时、Token或节省比例。'}


def summarize_project(project):
    """Read existing lifecycle timestamps; never backfill invented start times.

    Handoff windows are reported separately from manual events, because adding
    the two would count the same work twice. prepare -> close includes dispatch,
    waiting and root acceptance, and must not be described as model runtime.
    """
    project = Path(project)
    path = project / 'pipeline-events.jsonl'
    events = [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines()
              if line.strip()] if path.exists() else []
    # Linked events belong inside their handoff window, never in a second manual
    # timeline. Older unlinked events retain their original reporting contract.
    def is_linked(event):
        return event.get('measurement_scope') == 'handoff_prepare_to_close' and bool(event.get('attempt_id'))
    result = summarize([event for event in events if not is_linked(event)])
    lifecycle, unknown = [], []
    linked = {event['event_id'] for event in events if is_linked(event)}
    for path in sorted((project / 'agents').glob('*/handoff.json')):
        manifest = json.loads(path.read_text(encoding='utf-8-sig'))
        if manifest.get('routing_policy') != 'stage-handoff-v2' or not manifest.get('prepared_at'):
            unknown.append(str(path))
            continue
        # Paths, not task_key alone, distinguish retries. Legacy envelopes are
        # readable but never assigned a guessed lifecycle start.
        identifier = manifest.get('id') or path.parent.name
        base = {'stage': manifest['stage'], 'task_key': manifest['brief']['scope']['task_key'],
                'attempt_id': identifier, 'object_ids': [], 'input_digest': None,
                'actual_model': None, 'tokens': None, 'reason': None,
                'measurement_scope': 'handoff_prepare_to_close'}
        lifecycle.append({**base, 'event_id': f'{identifier}:prepared', 'status': 'started',
                          'occurred_at': manifest['prepared_at']})
        state_path = path.parent / 'handoff-state.json'
        state = json.loads(state_path.read_text(encoding='utf-8-sig')) if state_path.exists() else {}
        for event in events:
            if not is_linked(event) or event.get('attempt_id') != identifier:
                continue
            if event['stage'] != base['stage'] or event['task_key'] != base['task_key']:
                raise ValueError('已关联事件与交接阶段或task_key不一致')
            when = stamp(event['occurred_at'])
            if when < stamp(manifest['prepared_at']) or (state.get('closed_at') and when > stamp(state['closed_at'])):
                raise ValueError('关联事件超出交接生命周期，不能计为有效阶段工时')
            lifecycle.append(event)
            linked.discard(event['event_id'])
        if state.get('status') in ('completed', 'cancelled') and state.get('closed_at'):
            lifecycle.append({**base, 'event_id': f'{identifier}:closed',
                              'status': 'finished' if state['status'] == 'completed' else 'cancelled',
                              'reason': state.get('reason'), 'occurred_at': state['closed_at']})
    # prepare/close are persisted independently; order their actual timestamps
    # alongside manually recorded milestones without changing the source log.
    result['handoff_metrics'] = summarize(sorted(lifecycle, key=lambda e: stamp(e['occurred_at'])))
    result['handoff_metrics']['unmeasured_handoffs'] = unknown
    result['handoff_metrics']['limits'] += ' 交接区间包括分派、等待和主代理验收；与手工事件分开展示，不相加。'
    result['unmatched_handoff_event_ids'] = sorted(linked)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('record', 'status'))
    parser.add_argument('--project', type=Path)
    parser.add_argument('--handoff', type=Path, help='record关联当前交接，起止自动读取prepare/close')
    parser.add_argument('--stage', choices=sorted(STAGES))
    parser.add_argument('--task-key')
    parser.add_argument('--status', choices=sorted(STATUSES))
    parser.add_argument('--object-id', action='append', default=[])
    parser.add_argument('--input-digest')
    parser.add_argument('--revision-file', type=Path, help='当前实际稿件或检查输入清单，自动计算版本摘要')
    parser.add_argument('--check-kind', choices=sorted(CHECK_KINDS))
    parser.add_argument('--work-id', help='一个实际工作窗口的稳定标记；新一轮使用新标记')
    parser.add_argument('--category', choices=sorted(WORK_CATEGORIES))
    parser.add_argument('--parent-work-id', help='仅标识嵌套关系，汇总仍取区间并集')
    parser.add_argument('--reason')
    parser.add_argument('--actual-model', help='仅填写宿主可靠返回的实际执行模型，未知省略')
    parser.add_argument('--tokens', type=int, help='仅填写实际Token计数，未知省略')
    args = parser.parse_args()
    try:
        if args.command == 'record':
            fields = dict(object_ids=args.object_id, reason=args.reason,
                          actual_model=args.actual_model, tokens=args.tokens,
                          check_kind=args.check_kind, work_id=args.work_id,
                          category=args.category, parent_work_id=args.parent_work_id)
            if args.handoff:
                if any((args.project, args.stage, args.task_key, args.input_digest)):
                    raise ValueError('--handoff已确定项目、阶段、任务及输入集，不同时手填这些字段')
                result = record_handoff_event(args.handoff, args.status, revision_file=args.revision_file, **fields)
            else:
                if args.project is None:
                    raise ValueError('未关联交接时须提供--project')
                result = record_event(args.project, args.stage, args.task_key, args.status,
                                      input_digest=args.input_digest,
                                      revision_digest=file_digest(args.revision_file) if args.revision_file else None,
                                      **fields)
        else:
            if args.project is None or args.handoff is not None:
                raise ValueError('status使用--project读取现有事件与交接')
            result = summarize_project(args.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f'错误：{exc}\n')


if __name__ == '__main__':
    main()
