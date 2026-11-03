"""Append storyboard events and derive timing/issues from the same log.

No model-time estimates, backfilled timestamps or parallel progress state.
Legacy milestones remain readable; unknown coverage stays explicit.
"""
import hashlib
import json
import math
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
WAIT_REASONS = {'reviewer_busy', 'dependencies_unready', 'not_dispatched',
                'preparing_handoff', 'user_paused', 'unknown'}
OBSERVATION_PAIRS = {
    'source_read': ('source_read_started', 'source_read_finished'),
    'conception': ('conception_started', 'conception_finished'),
    'source_preparation_to_manuscript': ('source_read_started', 'manuscript_ready'),
    'draft_to_manuscript': ('draft_started', 'manuscript_ready'),
    'manuscript_to_authoring': ('manuscript_ready', 'authoring_ready'),
    'authoring_to_first_preview': ('authoring_ready', 'first_native_preview'),
    'manuscript_to_first_preview': ('manuscript_ready', 'first_native_preview'),
    'candidate_to_review_start': ('candidate_ready', 'review_started'),
    'preview_to_review_submission': ('first_native_preview', 'review_submitted'),
    'submission_to_review_start': ('review_submitted', 'review_started'),
    'review_to_report': ('review_started', 'review_report_finished'),
    'report_to_root_acceptance': ('review_report_finished', 'root_accepted'),
}
OBSERVATION_EVENTS = {name for pair in OBSERVATION_PAIRS.values() for name in pair}
MILESTONES = ({name for pair in PHASES.values() for name in pair}
              | (OBSERVATION_EVENTS - {'first_native_preview'})
              | {'draft_saved', 'draft_finished', 'manuscript_partial', 'review_waiting'})
PREVIEW_PRODUCER = 'storyboard_text_delivery.preview'
COMMAND_MEASUREMENT_KIND = 'actual_subprocess_wall_time_not_model_or_semantic_review'


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
        _validate_wait_reason(row['event'], row.get('reason'), row.get('note'))
    rows = identified(rows)
    from project_paths import read_metadata, resolve
    if read_metadata(project):
        for row in rows:
            if row.get('artifact'):
                row['artifact'] = dict(row['artifact'])
                for key in ('draft', 'output'):
                    row['artifact'][key] = str(resolve(project, row['artifact'][key]))
    return rows


def _validate_wait_reason(name, reason, note):
    if name == 'review_waiting':
        if not isinstance(reason, str) or reason not in WAIT_REASONS or not isinstance(note, str) or not note.strip():
            raise ValueError('review_waiting须提供有效reason与实际观察note')
    elif reason is not None:
        raise ValueError('reason仅用于review_waiting的实际等待状态变化')


def record_event(project, name, *, at, scene=None, note=None, activity_id=None,
                 coverage=None, issue_kind=None, artifact=None, producer=None, invalidates=None,
                 reason=None):
    project = Path(project).resolve()
    from project_paths import assert_writable
    assert_writable(project)
    if not (project / 'session.json').is_file():
        raise ValueError('计时必须指向已绑定项目')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('事件名称不能为空')
    stamp(at)
    _validate_wait_reason(name, reason, note)
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
        'reason': reason,
    }.items() if value is not None})
    folder = project / 'text-storyboards'
    folder.mkdir(exist_ok=True)
    with (folder / 'events.jsonl').open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    return row


def _record_preview_events(project, scenes, *, at, activity_id, artifact):
    """Append scene milestones after verifying artifact bytes at both boundaries.

    This private batch accepts no cached verification from a public caller.
    The public single-event API continues to verify each independent call.
    """
    from project_paths import assert_writable

    project = Path(project).resolve()
    assert_writable(project)
    if not (project / 'session.json').is_file():
        raise ValueError('计时必须指向已绑定项目')
    if (not isinstance(scenes, list) or not scenes
            or any(not isinstance(scene, str) or not scene.strip() for scene in scenes)
            or len(set(scenes)) != len(scenes)):
        raise ValueError('预览场次须完整且不重复')
    if activity_id is not None and (not isinstance(activity_id, str) or not activity_id.strip()):
        raise ValueError('场次或activity_id不能为空')
    stamp(at)
    rows = read_events(project)
    if rows and stamp(at) < stamp(rows[-1]['at']):
        raise ValueError('事件时间倒序，未追加；未知起点不回填')
    validate_preview_artifact(artifact, scenes[0])
    if set(scenes) != set(artifact['scenes']):
        raise ValueError('预览产物场次不匹配')
    events = []
    for scene in scenes:
        row = {'event_id': str(uuid.uuid4()), 'at': at, 'event': 'previewed',
               'scene': scene, 'artifact': artifact, 'producer': PREVIEW_PRODUCER}
        if activity_id is not None:
            row['activity_id'] = activity_id
        events.append(row)
    # Event assembly can run caller/host code (for example UUID generation).
    # Recheck the actual bytes at the append boundary, not once per scene.
    validate_preview_artifact(artifact, scenes[0])
    folder = project / 'text-storyboards'
    folder.mkdir(exist_ok=True)
    with (folder / 'events.jsonl').open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in events))
    return events


def invalidate_event(project, event_id, *, at, note):
    return record_event(project, 'event_invalidated', at=at, note=note,
                        issue_kind='tool_repair', invalidates=event_id)


def _window(phase, scene, activity, start, end):
    coverage = start.get('coverage', 'unspecified') if start else 'unspecified'
    if any(event and event.get('coverage') == 'partial' for event in (start, end)):
        coverage = 'partial'
    result = {'phase': phase, 'scene': scene, 'activity_id': activity,
            'started_at': start['at'] if start else None,
            'finished_at': end['at'] if end else None,
            'elapsed_seconds': (stamp(end['at']) - stamp(start['at'])).total_seconds()
            if start and end else None,
            'bounds': 'missing_start' if start is None else 'missing_finish' if end is None else 'bounded',
            'measurement_coverage': coverage,
            'start_event': start, 'end_event': end}
    if phase in {'review', 'recheck'}:
        result['meaning'] = ('实际阅读开始至实际报告完成的观察区间，含其中的中断。'
                             if end and end['event'] == 'review_report_finished' else
                             f'历史{phase}_finished可能记录主代理验收闭环，不能据此确定实际报告完成时刻。')
    elif phase == 'draft':
        result['meaning'] = 'draft_started至预览的记录区间；note与full声明均不能证明覆盖全部构思创作。'
    return result


def _native_preview(row):
    """Recognize a recorded native success, without rechecking historical files."""
    artifact = row.get('artifact')
    if row.get('producer') != PREVIEW_PRODUCER or not isinstance(artifact, dict):
        return False
    digests = [artifact.get(key) for key in ('draft_sha256', 'context_sha256', 'output_sha256')]
    span = artifact.get('span')
    scenes = artifact.get('scenes')
    return (all(isinstance(value, str) and len(value) == 64
                and all(c in '0123456789abcdef' for c in value) for value in digests)
            and isinstance(span, list) and len(span) == 2
            and all(type(value) is int for value in span) and 0 <= span[0] < span[1]
            and isinstance(scenes, list) and scenes
            and all(isinstance(scene, str) and scene for scene in scenes)
            and len(set(scenes)) == len(scenes) and row.get('scene') in scenes
            and all(isinstance(artifact.get(key), str) and Path(artifact[key]).is_absolute()
                    for key in ('draft', 'output'))
            and Path(artifact['draft']).resolve() != Path(artifact['output']).resolve())


def _observed_window(name, scene, activity, attempt, start, end):
    result = _window(name, scene, activity, start, end)
    result['attempt_index'] = attempt
    return result


def _milestone_views(rows):
    """Pair observations within actual scene/activity attempts, never file mtimes."""
    groups, current = [], {}
    aliases = {'draft_saved': 'manuscript_ready', 'draft_finished': 'candidate_ready',
               'previewed': 'first_native_preview', 'recheck_started': 'review_started'}
    for row in rows:
        name = row['event']
        if name not in MILESTONES:
            continue
        _validate_wait_reason(name, row.get('reason'), row.get('note'))
        if name == 'previewed' and not _native_preview(row):
            continue
        name = aliases.get(name, name)
        key = (row.get('scene'), row.get('activity_id'))
        group = current.get(key)
        prior = group['milestones'] if group else {}
        restart = (
            name == 'revision_started'
            or (name == 'revision_finished' and 'revision_started' not in prior
                and any(item in prior for item in ('review_started', 'review_report_finished', 'root_accepted')))
            or (name in {'source_read_started', 'draft_started', 'conception_started',
                         'review_submitted', 'review_started'} and name in prior)
            or (name in {'source_read_started', 'draft_started'}
                and any(item in prior for item in ('manuscript_ready', 'first_native_preview',
                                                   'review_submitted', 'review_started')))
            or (name == 'review_submitted' and any(item in prior for item in (
                'review_started', 'review_report_finished', 'review_finished', 'recheck_finished', 'root_accepted')))
            or (name == 'review_waiting' and any(item in prior for item in (
                'review_report_finished', 'review_finished', 'recheck_finished', 'root_accepted')))
        )
        if group is None or restart:
            group = {'scene': key[0], 'activity_id': key[1],
                     'attempt_index': group['attempt_index'] + 1 if group else 1,
                     'milestones': {}, 'events': []}
            groups.append(group)
            current[key] = group
        group['events'].append(row)
        group['milestones'].setdefault(name, row)
    windows, waiting = [], []
    for group in groups:
        scene, activity, attempt = (group[key] for key in ('scene', 'activity_id', 'attempt_index'))
        points = group['milestones']
        for name, (left, right) in OBSERVATION_PAIRS.items():
            start, end = points.get(left), points.get(right)
            if name == 'manuscript_to_authoring':
                # Legacy draft_saved records may describe only a partial save.
                # Keep their old views, but require the explicit new boundary.
                start = next((event for event in group['events']
                              if event['event'] == 'manuscript_ready'), None)
            if start or end:
                # An observed endpoint preceding the start cannot bound this
                # interval. Keep both as independent incomplete observations.
                if start and end and stamp(end['at']) < stamp(start['at']):
                    windows.append(_observed_window(name, scene, activity, attempt, None, end))
                    end = None
                windows.append(_observed_window(name, scene, activity, attempt, start, end))
        transitions = [row for row in group['events']
                       if row['event'] in {'review_waiting', 'review_submitted', 'review_started', 'recheck_started'}
                       or row is points.get('first_native_preview')]
        start, reason = None, 'unknown'
        for transition in transitions:
            if start is not None or transition['event'] in {'review_waiting', 'review_started', 'recheck_started'}:
                window = _observed_window('handoff_reason', scene, activity, attempt, start, transition)
                window['reason'] = reason
                waiting.append(window)
            if transition['event'] in {'review_started', 'recheck_started'}:
                start = None
                break
            start = transition
            reason = transition.get('reason', 'unknown')
        if start is not None:
            window = _observed_window('handoff_reason', scene, activity, attempt, start, None)
            window['reason'] = reason
            waiting.append(window)
        group['milestones'] = {name: points.get(name) for name in sorted(OBSERVATION_EVENTS)}
    return groups, windows, waiting


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
    mapping['review_report_finished'] = ('review', 1)
    pending, windows, report_closed = {}, [], set()
    for row in measured:
        if row['event'] not in mapping:
            continue
        phase, side = mapping[row['event']]
        key = (phase, row.get('scene'), row.get('activity_id'))
        if row['event'] == 'review_report_finished':
            recheck_key = ('recheck', row.get('scene'), row.get('activity_id'))
            if key not in pending and recheck_key in pending:
                key = recheck_key
            elif key not in pending and (key in report_closed or recheck_key in report_closed):
                continue
        if side == 0:
            report_closed.discard(key)
            if key in pending:
                windows.append(_window(*key, pending.pop(key), None))
            pending[key] = row
        else:
            if row['event'] in {'review_finished', 'recheck_finished', 'review_report_finished'} and key in report_closed and key not in pending:
                continue
            # A subsequent revision/assembly preview is an artifact milestone,
            # not another draft end with an invented missing start.
            if row['event'] == 'previewed' and row.get('producer') == PREVIEW_PRODUCER and key not in pending:
                continue
            windows.append(_window(*key, pending.pop(key, None), row))
            if row['event'] == 'review_report_finished':
                report_closed.add(key)
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
    observations, milestone_windows, reason_windows = _milestone_views(measured)
    return {'events': rows, 'current_run_events': raw_recent, 'effective_run_events': recent,
            'invalidated_events': invalidated, 'total_wall_seconds': wall,
            'complete': bool(starts and finish_index is not None), 'model_seconds': None,
            'phase_windows': windows, 'measured_windows': totals, 'by_phase': phase_totals,
            'by_scene': scene_totals,
            'handoff_gaps': gaps, 'issues': issues,
            'milestone_observations': observations, 'milestone_windows': milestone_windows,
            'handoff_reason_windows': reason_windows,
            'milestone_meanings': {
                'source_read': '记录的来源阅读区间可同时包含构思，保留原note，不据名称断言纯阅读耗时。',
                'conception': '仅有实际构思起止观察时计区间，缺失边界留空。',
                'source_preparation_to_manuscript': '来源阅读开始至首份候选稿保存的准备、构思与保存合并区间。',
                'draft_to_manuscript': 'draft_started至首份候选稿保存，可能只覆盖临近保存的后段。',
                'manuscript_to_authoring': '完整候选正文保存至匹配作者依据实际保存的观察间隔；依据随正文完成时可为零，不代表单独的纯侧车写作耗时。',
                'authoring_to_first_preview': '匹配作者依据保存至首个原生成功预览，可能包含绑定、机械修正与等待。缺失端点保持未知。',
                'manuscript_to_first_preview': '首份候选稿保存至首个具有原生成功产物记录的预览。',
                'candidate_to_review_start': 'candidate_ready或历史draft_finished观察至实际阅读开始，与首个成功预览分别展示。',
                'preview_to_review_submission': '预览至实际派审事件的观察区间，包含准备和等待。',
                'submission_to_review_start': '派审观察至实际阅读开始，按记录边界解释。',
                'review_to_report': '实际首审或recheck_started复看阅读开始至报告完成，保留原事件与note，缺失报告事件时不能用root验收代替。',
                'report_to_root_acceptance': '报告完成至主代理验收，缺失边界留空。',
            },
            'coverage_warnings': [w for w in windows if w['bounds'] != 'bounded'
                                  or w['measurement_coverage'] != 'full'],
            'note': '阶段/审阅可能重叠，不相加；未记录start与complete时总时长未知。'
                    'full仅是记录者声明，非自动证明；partial或unspecified不代表完整创作耗时。'
                    '未覆盖时间包含未打点工作，不能直接称等待或浪费。'
                    'milestone_windows是同一事件的不同观察视图，不叠加到阶段或总时长。'
                    '等待原因仅从实际review_waiting打点起生效，之前未知，不向前补填。'}


def _command_measurements(paths):
    measurements, seen = [], set()
    for value in paths or []:
        path = Path(value).resolve()
        if path in seen:
            continue
        seen.add(path)
        raw = path.read_bytes()
        row = json.loads(raw.decode('utf-8-sig'))
        valid = isinstance(row, dict) and row.get('measurement_kind') == COMMAND_MEASUREMENT_KIND
        if valid:
            elapsed, command = row.get('elapsed_seconds'), row.get('command')
            code, finished = row.get('returncode'), row.get('finished_at')
            running = elapsed is None and code is None and finished is None
            valid = (row.get('phase') in {'context', 'bind', 'preview', 'review_verify', 'commit_readback'}
                     and isinstance(command, list) and bool(command)
                     and all(isinstance(part, str) and part for part in command)
                     and (running or (type(code) is int and finished is not None
                                      and type(elapsed) in (int, float)
                                      and math.isfinite(elapsed) and elapsed >= 0)))
        try:
            start = stamp(row['started_at'])
            end = stamp(row['finished_at']) if row.get('finished_at') is not None else None
            valid = valid and (end is None or end >= start)
        except (KeyError, TypeError, AttributeError, ValueError):
            valid = False
        if not valid:
            raise ValueError('本地命令计时须包含有效实际起点与命令，完成时须具备结束边界、有限非负时长和返回码：' + str(path))
        measurements.append({**row, 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                             'finished_at': row.get('finished_at'), 'elapsed_seconds': row.get('elapsed_seconds'),
                             'returncode': row.get('returncode'),
                             'bounds': 'bounded' if end is not None else 'missing_finish',
                             'timestamp_elapsed_seconds': (end - start).total_seconds() if end is not None else None})
    return measurements


def timing(project, command_logs=None):
    result = summarize(read_events(project))
    result['command_measurements'] = _command_measurements(command_logs)
    result['command_measurement_note'] = ('仅读取显式提供的实际本地subprocess计时，保留单调时钟时长及时间戳跨度。'
                                          '合法start-only记录的结束、时长与返回码留空，不能从当前时刻补算。'
                                          '这些记录可能嵌套于阶段区间，分别展示，不计为模型时间或加入阶段总时长。')
    return result
