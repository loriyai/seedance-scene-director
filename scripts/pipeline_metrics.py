"""Record actual local phase events and counts without estimating model time or savings."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

STAGES = {'standardize', 'assets', 'design', 'review', 'delivery', 'storyboard', 'revise', 'sync'}
STATUSES = {'started', 'draft_saved', 'checked', 'needs_review', 'compiled', 'failed', 'finished', 'cancelled'}


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('时间须带时区')
    return parsed


def record_event(project, stage, task_key, status, *, object_ids=(), input_digest=None,
                 reason=None, actual_model=None, tokens=None, occurred_at=None):
    if stage not in STAGES or status not in STATUSES or not isinstance(task_key, str) or not task_key.strip():
        raise ValueError('阶段、任务标记或状态无效')
    ids = list(object_ids)
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError('对象ID须非空且不重复')
    if tokens is not None and (type(tokens) is not int or tokens < 0):
        raise ValueError('tokens只登记宿主提供的实际非负整数，未知留空')
    when = occurred_at or datetime.now(timezone.utc).isoformat()
    stamp(when)
    entry = {'event_id': str(uuid.uuid4()), 'stage': stage, 'task_key': task_key, 'status': status,
             'object_ids': ids, 'input_digest': input_digest, 'reason': reason,
             'actual_model': actual_model, 'tokens': tokens, 'occurred_at': when}
    path = Path(project) / 'pipeline-events.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return entry


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
    rows, spans, first_checks = [], [], []
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
            if item['status'] in ('checked', 'needs_review', 'failed') and item.get('input_digest'):
                checks.setdefault(item.get('input_digest'), item['status'])
        first_checks.extend(checks.values())
        current = {}
        for item in items:
            for aid in item['object_ids']:
                current[aid] = item['status']
        coverage = ('missing_start' if start is None else 'missing_finish' if finish is None else
                    'late_start' if any(stamp(item['occurred_at']) < start for item in items) else 'bounded')
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
                                                and not item.get('input_digest') for item in items),
                     'rework_ids': sorted({aid for item in items if item['status'] == 'needs_review' for aid in item['object_ids']}),
                     'reasons': list(dict.fromkeys(item['reason'] for item in items if item.get('reason'))),
                     'first_checks': checks})
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
            'limits': '仅覆盖已记录的起止区间；窗口可含等待与未覆盖间隔，任务之和可含并行重叠。缺失或晚登记开始不补算；未知前后区间不计入全流程。不能据此声称模型计算时间、工具耗时、Token或节省比例。'}


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
    result = summarize(events)
    lifecycle, unknown = [], []
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
        if state.get('status') in ('completed', 'cancelled') and state.get('closed_at'):
            lifecycle.append({**base, 'event_id': f'{identifier}:closed',
                              'status': 'finished' if state['status'] == 'completed' else 'cancelled',
                              'reason': state.get('reason'), 'occurred_at': state['closed_at']})
    result['handoff_metrics'] = summarize(lifecycle)
    result['handoff_metrics']['unmeasured_handoffs'] = unknown
    result['handoff_metrics']['limits'] += ' 交接区间包括分派、等待和主代理验收；与手工事件分开展示，不相加。'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('record', 'status'))
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--stage', choices=sorted(STAGES))
    parser.add_argument('--task-key')
    parser.add_argument('--status', choices=sorted(STATUSES))
    parser.add_argument('--object-id', action='append', default=[])
    parser.add_argument('--input-digest')
    parser.add_argument('--reason')
    parser.add_argument('--actual-model', help='仅填写宿主可靠返回的实际执行模型，未知省略')
    parser.add_argument('--tokens', type=int, help='仅填写实际Token计数，未知省略')
    args = parser.parse_args()
    try:
        if args.command == 'record':
            result = record_event(args.project, args.stage, args.task_key, args.status,
                                  object_ids=args.object_id, input_digest=args.input_digest, reason=args.reason,
                                  actual_model=args.actual_model, tokens=args.tokens)
        else:
            result = summarize_project(args.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f'错误：{exc}\n')


if __name__ == '__main__':
    main()
