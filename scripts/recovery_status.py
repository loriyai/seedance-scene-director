"""Read-only recovery view derived from canonical session and frozen job state."""
from pathlib import Path

import director as d
import canvas as c
import canvas_jobs as jobs
from project_paths import state_view


def summarize(project, state, queue=None):
    project = Path(project)
    # Callers may hold the in-memory session object produced before the
    # project path was canonicalized (notably on macOS, where /var resolves
    # through /private/var). The recovery view is read-only, so normalize a
    # defensive copy before recomputing frozen input digests.
    state = state_view(project, state)
    queue = jobs.status(project) if queue is None else queue
    binding = c.read_state(project).get('binding')
    manifests = {job['job_id']: job for job in jobs.manifests(project)}
    current = []
    historical = []
    # One read-only status view uses one observation per dependency set/folder.
    # Do not persist this cache: the next status call must see fresh inputs.
    input_digests, image_inventories = {}, {}
    for row in queue['jobs']:
        job = manifests[row['job_id']]
        try:
            valid = (job['manifest_digest'] == c.digest({key: value for key, value in job.items() if key != 'manifest_digest'})
                     and job['binding'] == binding)
            if valid:
                key = (job['kind'], tuple(job['options']['ids']))
                if key not in input_digests:
                    input_digests[key] = c.digest(jobs.frozen_input_scope(project, state, job))
                valid = job['input_digest'] == input_digests[key]
            if valid and job['kind'] == 'images':
                folder = job['options']['folder']
                if folder not in image_inventories:
                    image_inventories[folder] = jobs.image_inventory(folder)
                valid = image_inventories[folder] == job['options']['images']
        except (OSError, ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            historical.append(row['job_id'])
            continue
        current.append({key: row[key] for key in
                        ('job_id', 'kind', 'status', 'stage', 'completed', 'total', 'error', 'updated_at') if key in row})
    assets = [row for row in current if row['kind'] == 'assets']
    latest = state.get('receipts', [])[-1] if state.get('receipts') else None
    result = {'source': 'session+canvas/jobs', 'read_only': True,
              'phase': state['phase'], 'revision': state.get('revision'),
              'latest_storyboard': None if latest is None else {
                  'request_id': latest['request_id'], 'batch_label': latest.get('batch_label'),
                  'unit_count': len(latest['units']), 'output': latest.get('output')},
              'unit_count': len(state.get('units', [])), 'jobs': current,
              'historical_job_ids': historical, 'paused': queue['paused'],
              'worker_active': queue['worker_active'], 'asset_progress': assets[-1] if assets else None,
              'legacy_intake': {'present': False, 'authoritative': False, 'differences': []}}
    legacy_path = project / 'intake-state.json'
    if legacy_path.is_file():
        legacy = result['legacy_intake']
        legacy['present'] = True
        try:
            old = d.read_json(legacy_path)
            comparisons = {'phase': result['phase']}
            if assets:
                comparisons['asset_transfer_verified_count'] = assets[-1]['completed']
            if latest:
                # Old projects used inconsistent storyboard objects. Report the
                # ambiguity instead of inventing a historical migration.
                old_latest = old.get('latest_storyboard', old.get('last_storyboard'))
                if old_latest is not None:
                    legacy['storyboard_snapshot'] = old_latest
                    legacy['current_request_id'] = latest['request_id']
            for key, value in comparisons.items():
                if key in old and old[key] != value:
                    legacy['differences'].append({'field': key, 'snapshot': old[key], 'current': value})
        except (OSError, ValueError, TypeError, AttributeError):
            legacy['error'] = '旧intake快照无法读取；不用于恢复决策。'
    from project_continuation import resume
    result['continuation'] = resume(project, state)
    return result
