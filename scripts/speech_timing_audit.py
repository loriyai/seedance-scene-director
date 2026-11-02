"""Backend-only fragment timing diagnostics; never render or alter dialogue."""
import re
import math
from decimal import Decimal, InvalidOperation
import unicodedata

from dialogue_layout import pause_seconds, SPEEDS, PAUSE_RANGES


TEXT_DIALOGUE_CAPACITY_POLICY = 'scene-text-dialogue-capacity-v1'


def _text_load(text):
    characters = sum(unicodedata.category(char)[0] in ('L', 'N') for char in text)
    commas = sum(char in '，、,' for char in text)
    endings = sum(char in '。！？!?' for char in text)
    minimum = (Decimal(characters) / Decimal(6)
               + Decimal(commas) / Decimal(10)
               + Decimal(endings) / Decimal(5))
    return characters, commas, endings, minimum


def _speech_window_facts(key, number, parts, start, end):
    """Describe authored unit-relative windows, not measured sound or pacing."""
    row = {'unit': key, 'shot': number, 'shot_start': float(start),
           'shot_end': float(end), 'timing_status': 'unavailable'}
    windows = []
    previous_end = start
    try:
        for part in parts:
            values = [part.get(field) for field in ('start', 'end')]
            if any(isinstance(value, bool) for value in values):
                raise ValueError('布尔值不是秒数')
            t0, t1 = (Decimal(str(value)) for value in values)
            if (not t0.is_finite() or not t1.is_finite()
                    or not start <= previous_end <= t0 < t1 <= end):
                raise ValueError('片段须按顺序位于镜窗内')
            windows.append((t0, t1))
            previous_end = t1
    except (InvalidOperation, ValueError):
        # The parser owns structural validation; this diagnostic adds no gate.
        row['reason'] = '缺少合法有序的镜内声音时窗，未推断或补齐。'
        return row
    fragments = []
    for index, (part, (t0, t1)) in enumerate(zip(parts, windows), 1):
        characters, commas, endings, minimum = _text_load(part['text'])
        duration = t1 - t0
        fragments.append({
            'part': index, 'record': part.get('record'),
            'start': float(t0), 'end': float(t1), 'duration': float(duration),
            'dialogue_characters': characters,
            'comma_or_pause_count': commas, 'sentence_end_count': endings,
            'mechanical_minimum': float(minimum),
            'below_mechanical_minimum': duration < minimum,
            'deficit': float(max(Decimal(0), minimum - duration)),
        })
    row.update(timing_status='available', fragments=fragments,
               before_first_speech=float(windows[0][0] - start),
               after_last_speech=float(end - windows[-1][1]),
               between_fragments=[float(windows[i][0] - windows[i - 1][1])
                                  for i in range(1, len(windows))])
    return row


def text_dialogue_capacity(units):
    """Check the mechanical fastest-speech lower bound for authored text.

    The complete shot window is compared against all of its exact dialogue
    fragments combined.  This pure diagnostic never edits speech, timing,
    cuts or any creative prose, and a checked result is not semantic approval
    or proof that the dialogue sounds natural at the chosen delivery.
    """
    errors = []
    window_facts = []
    checked, skipped = 0, 0
    for unit in units:
        key = unit.get('draft_key', unit.get('id', '当前单元'))
        for number, shot in enumerate(unit.get('shots', []), 1):
            parts = shot.get('dialogue_parts', [])
            if not parts:
                skipped += 1
                continue
            texts = [part.get('text') for part in parts]
            if not all(isinstance(text, str) for text in texts):
                raise ValueError(f'{key}/镜头{number}：台词容量检查须有原文text字符串')
            try:
                values = [shot.get(field) for field in ('start', 'end')]
                if any(isinstance(value, bool) for value in values):
                    raise ValueError('布尔值不是秒数')
                start, end = (Decimal(str(value)) for value in values)
                if not start.is_finite() or not end.is_finite() or end <= start:
                    raise ValueError('时间窗须为有限正时长')
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f'{key}/镜头{number}：台词容量检查须有合法起止秒数') from exc
            text = ''.join(texts)
            characters, commas, endings, minimum = _text_load(text)
            duration = end - start
            checked += 1
            window_facts.append(_speech_window_facts(key, number, parts, start, end))
            if duration < minimum:
                errors.append({
                    'code': 'DIALOGUE_CAPACITY', 'unit': key, 'shot': number,
                    'minimum': float(minimum), 'duration': float(duration),
                    'deficit': float(minimum - duration),
                    'dialogue_characters': characters,
                    'comma_or_pause_count': commas,
                    'sentence_end_count': endings,
                    'kind': '镜头时长低于台词机械最低时长，须由作者重分镜窗或切点；程序不自动改稿。',
                })
    return {'policy': TEXT_DIALOGUE_CAPACITY_POLICY,
            'status': 'blocked' if errors else 'checked',
            'errors': errors, 'checked_dialogue_shots': checked,
            'skipped_no_dialogue_shots': skipped,
            'speech_window_diagnostics': {
                'blocking': False, 'time_basis': 'authored_unit_relative_seconds',
                'shots': window_facts,
                'limits': '仅列作者声音时窗与原话机械负荷；不是实际录音测量或语速批准。'
                          '窄窗仅供定位，宽窗及前后间隔不自动判为拖慢；程序不改稿或配秒。',
            },
            'semantic_review': 'not_performed'}


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
