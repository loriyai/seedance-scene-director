"""Backend-only fragment timing diagnostics; never render or alter dialogue."""
import re
import math

from dialogue_layout import pause_seconds, SPEEDS, PAUSE_RANGES


def audit(unit):
    warnings = []
    for event in unit.get('audio', []):
        if event.get('timing_basis', 'text_estimate') != 'text_estimate':
            continue  # Existing recording receipts remain readable; no recording is required.
        parts = [part for shot in unit['shots'] for part in shot.get('dialogue_parts', [])
                 if part['record'] == event['record']]
        if not parts:
            continue
        rid = event['record']
        def warn(kind, **details):
            warnings.append({'unit': unit.get('id', '当前单元'), 'record': rid, 'kind': kind, **details})
        gaps = [0.0] + [round(parts[i]['start'] - parts[i-1]['end'], 3) for i in range(1, len(parts))]
        internal_budget = max(0.0, event['pause'] - sum(gaps))
        weights = [pause_seconds(p['text']) for p in parts]
        explicit_pause = ['pause' in p for p in parts]
        if any(explicit_pause) and not all(explicit_pause):
            warn('片段停顿登记不完整', review='各片段pause全部填写或全部省略，避免混用两种停顿分配。')
        if all(explicit_pause):
            pauses = [p['pause'] for p in parts]
            if any(type(p) not in (int, float) or not math.isfinite(p) or p < 0 for p in pauses):
                raise ValueError('片段pause须为有限非负秒数')
            if abs(sum(pauses) + sum(gaps) - event['pause']) > .003:
                warn('片段停顿与整句总停顿不一致', registered_pause=event['pause'],
                     fragment_pause=round(sum(pauses), 3), gap_seconds=round(sum(gaps), 3))
        else:
            total_weight = sum(weights)
            pauses = [internal_budget * w / total_weight if total_weight else internal_budget / len(parts)
                      for w in weights]
        units = []
        for i, part in enumerate(parts):
            value = part.get('spoken_units')
            if value is not None:
                if type(value) is not int or value <= 0 or not isinstance(part.get('spoken_basis'), str) or not part['spoken_basis'].strip():
                    raise ValueError('片段spoken_units须为正整数，且以spoken_basis说明数字/英文实际念法或计数依据')
            elif len(parts) == 1:
                value = event['units']
            elif re.search(r'[0-9A-Za-z]', part['text']):
                warn('片段发音单位待核', part=i+1,
                     review='数字或英文不能按字符数估时；后台登记spoken_units和spoken_basis，不改变原台词。')
            else:
                value = sum(c.isalnum() for c in part['text'])
            units.append(value)
            if i and gaps[i] > .001:
                boundary = parts[i-1]['text'].rstrip()
                mark = boundary[-1:] if boundary else ''
                limit = (PAUSE_RANGES['sentence'][1] if mark in '。？！?!' and mark
                         else PAUSE_RANGES['comma'][1] if mark in '，、,' and mark else None)
                # Other punctuation and unmarked semantic breaks require context,
                # not a mechanically borrowed comma/sentence threshold.
                if limit is not None and gaps[i] > limit + .001:
                    warn('分句间停顿偏长需复核', part=i+1, gap_seconds=gaps[i],
                         basis=part.get('gap_basis', ''), review='核对原文情绪、接话或动作依据；不要求所有分句零间隔。')
            if value is None:
                continue
            net = part['end'] - part['start'] - pauses[i]
            low, high = SPEEDS[event['speed']]
            rate = value / net if net > 0 else None
            if rate is None or not low - .01 <= rate <= high + .01:
                warn('片段估计净语速超档', part=i+1, units=value,
                     start=part['start'], end=part['end'], pause=round(pauses[i], 3),
                     rate=round(rate, 3) if rate is not None else None,
                     min_seconds=round(value/high + pauses[i], 3),
                     max_seconds=round(value/low + pauses[i], 3),
                     pause_basis='explicit' if all(explicit_pause) else 'estimated_distribution')
        if all(v is not None for v in units) and sum(units) != event['units']:
            warn('片段发音单位合计与整句不一致', event_units=event['units'], fragment_units=sum(units))
    return warnings
