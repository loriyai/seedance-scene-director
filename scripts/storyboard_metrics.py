"""Append storyboard events and derive timing/issues from the same log.

No model-time estimates, backfilled timestamps or parallel progress state.
Legacy milestones remain readable; unknown coverage stays explicit.
"""
import hashlib
import json
from pathlib import Path
import uuid

from pipeline_metrics import WORK_CATEGORIES, interval_totals, stamp

PHASES = {
    'draft': ('draft_started', 'previewed'),
    'review': ('review_started', 'review_finished'),
    'revision': ('revision_started', 'revision_finished'),
    'recheck': ('recheck_started', 'recheck_finished'),
    'tool_repair': ('tool_repair_started', 'tool_repair_finished'),
    'handoff': ('handoff_started', 'handoff_finished'),
}
COVERAGES = {'full', 'partial', 'unspecified'}
MILESTONES = {name for pair in PHASES.values() for name in pair}
PREVIEW_PRODUCER = 'storyboard_text_delivery.preview'


def identified(rows):
    """Expose stable IDs for legacy rows without rewriting their original bytes."""
    result, seen = [], set()
    for index, original in enumerate(rows):
        row = dict(original)
        if 'event_id' not in row:
            raw = json.dumps([index, original], sort_keys=True, ensure_ascii=False).encode('utf-8')
            row['event_id'] = 'legacy-' + hashlib.sha256(raw).hexdigest()
        key = row['event_id']
        if not isinstance(key, str) or not key.strip() or key in seen:
            raise ValueError('事件ID为空或重复')
        seen.add(key)
        result.append(row)
    return result


def effective_events(rows):
    rows = identified(rows)
    seen, invalidated = {}, {}
    for row in rows:
        if row['event'] == 'event_invalidated':
            target = row.get('invalidates')
            if (not isinstance(target, str) or target not in seen or target in invalidated or
                    seen[target]['event'] not in MILESTONES or not row.get('note', '').strip()):
                raise ValueError('撤销须指向此前有效的阶段事件，并说明实际原因')
            invalidated[target] = row
        seen[row['event_id']] = row
    active = [row for row in rows if row['event_id'] not in invalidated
              and row['event'] != 'event_invalidated']
    removed = [{'event': seen[key], 'correction': correction}
               for key, correction in invalidated.items()]
    return active, removed


def validate_preview_artifact(artifact, scene):
    if not isinstance(artifact, dict):
        raise ValueError('previewed只能由成功预览携带实际产物记录，不能手工宣告')
    for key in ('draft_sha256', 'context_sha256', 'output_sha256'):
        value = artifact.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('预览产物缺少有效摘要：' + key)
    scenes = artifact.get('scenes')
    if (not isinstance(scenes, list) or not scenes or
            any(not isinstance(s, str) or not s for s in scenes) or
            len(set(scenes)) != len(scenes) or scene not in scenes):
        raise ValueError('预览产物场次不匹配')
    span = artifact.get('span')
    if (not isinstance(span, list) or len(span) != 2 or
            any(type(n) is not int for n in span) or not 0 <= span[0] < span[1]):
        raise ValueError('预览产物缺少准确范围')
    paths = []
    for key, digest in (('draft', 'draft_sha256'), ('output', 'output_sha256')):
        value = artifact.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('预览产物路径须为绝对路径')
        path = Path(value).resolve()
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact[digest]:
            raise ValueError('预览产物不存在或摘要不匹配：' + key)
        paths.append(path)
    if paths[0] == paths[1]:
        raise ValueError('预览不能覆盖作者稿')


def read_events(project):
    path = Path(project) / 'text-storyboards/events.jsonl'
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines()
            if line.strip()] if path.is_file() else []
    for row in rows:
        stamp(row['at'])
        if not isinstance(row['event'], str) or not row['event'].strip():
            raise ValueError('事件名称不能为空')
    rows = identified(rows)
    from project_paths import read_metadata, resolve
    if read_metadata(project):
        for row in rows:
            if row.get('artifact'):
                row['artifact'] = dict(row['artifact'])
                for key in ('draft', 'output'):
                    row['artifact'][key] = str(resolve(project, row['artifact'][key]))
    return rows


def record_event(project, name, *, at, scene=None, note=None, activity_id=None,
                 coverage=None, issue_kind=None, artifact=None, producer=None, invalidates=None):
    project = Path(project).resolve()
    from project_paths import assert_writable
    assert_writable(project)
    if not (project / 'session.json').is_file():
        raise ValueError('计时必须指向已绑定项目')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('事件名称不能为空')
    stamp(at)
    if coverage is not None and coverage not in COVERAGES:
        raise ValueError('coverage须为full/partial/unspecified')
    if issue_kind is not None and (issue_kind not in WORK_CATEGORIES
                                   or not isinstance(note, str) or not note.strip()):
        raise ValueError('问题须提供实际说明和有效issue_kind')
    for value in (activity_id, scene):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError('场次或activity_id不能为空')
    rows = read_events(project)
    if rows and stamp(at) < stamp(rows[-1]['at']):
        raise ValueError('事件时间倒序，未追加；未知起点不回填')
    if name == 'previewed':
        if producer != PREVIEW_PRODUCER:
            raise ValueError('previewed只能由成功预览记录，不能手工宣告')
        validate_preview_artifact(artifact, scene)
    elif artifact is not None or producer is not None:
        raise ValueError('预览产物字段只用于程序预览成功事件')
    if name == 'event_invalidated':
        active, _ = effective_events(rows)
        target = next((r for r in active if r['event_id'] == invalidates), None)
        if target is None or target['event'] not in MILESTONES or not isinstance(note, str) or not note.strip():
            raise ValueError('撤销须指向此前有效的阶段事件，并说明实际原因')
    elif invalidates is not None:
        raise ValueError('invalidates只用于明确撤销事件')
    row = {'event_id': str(uuid.uuid4()), 'at': at, 'event': name}
    row.update({key: value for key, value in {
        'scene': scene, 'note': note, 'activity_id': activity_id,
        'coverage': coverage, 'issue_kind': issue_kind,
        'artifact': artifact, 'producer': producer, 'invalidates': invalidates,
    }.items() if value is not None})
    folder = project / 'text-storyboards'
    folder.mkdir(exist_ok=True)
    with (folder / 'events.jsonl').open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    return row


def invalidate_event(project, event_id, *, at, note):
    return record_event(project, 'event_invalidated', at=at, note=note,
                        issue_kind='tool_repair', invalidates=event_id)


def _window(phase, scene, activity, start, end):
    return {'phase': phase, 'scene': scene, 'activity_id': activity,
            'started_at': start['at'] if start else None,
            'finished_at': end['at'] if end else None,
            'elapsed_seconds': (stamp(end['at']) - stamp(start['at'])).total_seconds()
            if start and end else None,
            'bounds': 'missing_start' if start is None else 'missing_finish' if end is None else 'bounded',
            'measurement_coverage': start.get('coverage', 'unspecified') if start else 'unspecified'}


def summarize(rows):
    rows = identified(rows)
    active, invalidated = effective_events(rows)
    starts = [i for i, row in enumerate(active) if row['event'] == 'start']
    recent = active[starts[-1]:] if starts else active
    raw_start = next((i for i, row in enumerate(rows) if starts and row['event_id'] == recent[0]['event_id']), 0)
    raw_recent = rows[raw_start:]
    finish_index = next((i for i, row in enumerate(recent) if row['event'] == 'complete'), None)
    measured = recent[:finish_index + 1] if finish_index is not None else recent
    if any(stamp(b['at']) < stamp(a['at']) for a, b in zip(measured, measured[1:])):
        raise ValueError('当前运行事件时间倒序，不能可靠汇总')
    wall = (stamp(measured[-1]['at']) - stamp(measured[0]['at'])).total_seconds() \
        if starts and finish_index is not None else None
    mapping = {name: (phase, side) for phase, pair in PHASES.items() for side, name in enumerate(pair)}
    pending, windows = {}, []
    for row in measured:
        if row['event'] not in mapping:
            continue
        phase, side = mapping[row['event']]
        key = (phase, row.get('scene'), row.get('activity_id'))
        if side == 0:
            if key in pending:
                windows.append(_window(*key, pending.pop(key), None))
            pending[key] = row
        else:
            # A subsequent revision/assembly preview is an artifact milestone,
            # not another draft end with an invented missing start.
            if row['event'] == 'previewed' and row.get('producer') == PREVIEW_PRODUCER and key not in pending:
                continue
            windows.append(_window(*key, pending.pop(key, None), row))
    windows.extend(_window(*key, start, None) for key, start in pending.items())
    spans = [(stamp(w['started_at']), stamp(w['finished_at'])) for w in windows
             if w['bounds'] == 'bounded']
    totals = interval_totals(spans)
    # With no task start, phase windows are still useful but cannot establish
    # the complete task wall time or its uncovered portion.
    totals['uncovered_wall_seconds'] = wall - totals['interval_union_seconds'] if wall is not None else None
    phase_totals = {phase: interval_totals([
        (stamp(w['started_at']), stamp(w['finished_at'])) for w in windows
        if w['phase'] == phase and w['bounds'] == 'bounded']) for phase in PHASES}
    scene_totals = {scene: interval_totals([
        (stamp(w['started_at']), stamp(w['finished_at'])) for w in windows
        if w['scene'] == scene and w['bounds'] == 'bounded'])
        for scene in dict.fromkeys(w['scene'] for w in windows) if scene is not None}
    # An interval between milestones includes dispatch, preparation and waits;
    # never label it pure idle time or add it to the task wall clock.
    ready, gaps = {}, []
    for row in measured:
        scene = row.get('scene')
        key = (scene, row.get('activity_id'))
        if row['event'] in {'previewed', 'revision_finished'}:
            ready[key] = row
        elif row['event'] in {'review_started', 'recheck_started'} and key in ready:
            prior = ready.pop(key)
            gaps.append({'scene': scene, 'from': prior['event'], 'to': row['event'],
                         'activity_id': row.get('activity_id'),
                         'started_at': prior['at'], 'finished_at': row['at'],
                         'elapsed_seconds': (stamp(row['at']) - stamp(prior['at'])).total_seconds(),
                         'meaning': '交接间隔，含准备与等待，不等同空转'})
    issues = []
    for row in measured:
        inferred = {'revision_started': 'review_revision', 'tool_repair_started': 'tool_repair'}
        kind = row.get('issue_kind')
        if kind or row['event'] in inferred and row.get('note'):
            issues.append({**row, 'issue_kind': kind or inferred[row['event']],
                           'classification_source': 'explicit' if kind else 'legacy_event_name'})
    return {'events': rows, 'current_run_events': raw_recent, 'effective_run_events': recent,
            'invalidated_events': invalidated, 'total_wall_seconds': wall,
            'complete': bool(starts and finish_index is not None), 'model_seconds': None,
            'phase_windows': windows, 'measured_windows': totals, 'by_phase': phase_totals,
            'by_scene': scene_totals,
            'handoff_gaps': gaps, 'issues': issues,
            'coverage_warnings': [w for w in windows if w['bounds'] != 'bounded'
                                  or w['measurement_coverage'] != 'full'],
            'note': '阶段/审阅可能重叠，不相加；未记录start与complete时总时长未知。'
                    'full仅是记录者声明，非自动证明；partial或unspecified不代表完整创作耗时。'
                    '未覆盖时间包含未打点工作，不能直接称等待或浪费。'}


def timing(project):
    return summarize(read_events(project))
