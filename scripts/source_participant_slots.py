"""Exact-record silent prompt-only source participants, including main contact.

Anonymous identity, necessary visibility and text sufficiency require actual
author/reviewer judgment; this module grants neither voice nor image rights.
"""
import source_background_slots as _engine

FIELD = 'source_participant_slots'
asset = _engine.asset


def _call(name, *args, **kwargs):
    try:
        return getattr(_engine, name)(*args, **kwargs, field=FIELD)
    except ValueError as exc:
        raise ValueError(str(exc).replace('source_background_slots', FIELD)
                         .replace('背景', '来源参与者')) from exc


def declarations(trace):
    return _call('declarations', trace)


def normalize(rows):
    return _call('normalize', rows)


def context(state, rows):
    return _call('context', state, rows)


def validate(state, rows, selected, *, source_state=None):
    return _call('validate', state, rows, selected, source_state=source_state)


def project(rows, unit):
    return _call('project', rows, unit)


def apply(state, units, rows, *, source_state=None):
    return _call('apply', state, units, rows, source_state=source_state)


def require_usage(units, rows):
    _call('require_usage', units, rows)
    silent = {row['asset_id'] for row in rows}
    for unit in units:
        for shot in unit.get('shots', []):
            for part in shot.get('dialogue_parts', []):
                ids = part.get('character_ids', [part.get('character_id')])
                _engine.require(not silent.intersection(ids),
                                '静默来源参与者槽不授予独立声音或制作发言者资格')


def from_units(units):
    return _call('from_units', units)
