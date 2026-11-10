"""Backend-only fragment timing diagnostics; never render or alter dialogue."""
import re
import math
from decimal import Decimal, InvalidOperation
import unicodedata

from speech_timing_policy import (pause_seconds, SPEEDS, PAUSE_RANGES, READING_TOKEN,
                                 character_count, reading_units, capacity_seconds,
                                 punctuation_reference, punctuation_weights)


TEXT_DIALOGUE_CAPACITY_POLICY = 'scene-text-dialogue-capacity-v4'


def _require(value, message):
    if not value:
        raise ValueError(message)


def validate_basis(part, basis):
    """Check exact annotation targets, not the semantic truth of a reading."""
    _require(isinstance(basis, dict) and basis
             and set(basis) <= {'readings', 'overlap', 'pause_seconds'},
             '声音依据只允许readings、overlap或pause_seconds，不另建声音计划')
    if 'pause_seconds' in basis:
        pause = basis['pause_seconds']
        _require(type(pause) in (int, float) and Decimal(str(pause)).is_finite() and pause >= 0,
                 'pause_seconds须为有限非负数，表示本片段窗口内部的实际静默总量')
        try:
            start, end = (Decimal(str(part.get(field))) for field in ('start', 'end'))
        except InvalidOperation:
            start = end = Decimal('NaN')
        if start.is_finite() and end.is_finite() and end > start:
            _require(Decimal(str(pause)) <= end - start,
                     'pause_seconds不得超过本片段窗口，不包含片段前后或片段之间的间隔')
    tokens = {}
    for match in READING_TOKEN.finditer(part['text']):
        tokens.setdefault(match.group(), []).append(match)
    seen = set()
    if 'readings' in basis:
        _require(isinstance(basis['readings'], list) and basis['readings'], 'readings须为非空列表')
        for row in basis['readings']:
            _require(isinstance(row, dict) and set(row) == {'text', 'occurrence', 'units'},
                     '读法须包含原文text、从1开始的occurrence及可发音units')
            token, occurrence, units = row['text'], row['occurrence'], row['units']
            _require(isinstance(token, str) and token in tokens and type(occurrence) is int
                     and 1 <= occurrence <= len(tokens[token]) and (token, occurrence) not in seen,
                     '读法必须唯一定位本声音片段中的完整数字、英文或缩写')
            _require(isinstance(units, list) and units and all(
                isinstance(value, str) and value.strip() == value and 0 < len(value) <= 32
                and not any(c.isspace() for c in value)
                and all(unicodedata.category(c)[0] in ('L', 'M') for c in value)
                and (not any('\u3400' <= c <= '\u9fff' for c in value) or len(value) == 1)
                for value in units), '读法units每项须为一个汉字或一个明确的外语音节，不填写自报总数')
            seen.add((token, occurrence))
    if 'overlap' in basis:
        row = basis['overlap']
        _require(isinstance(row, dict) and set(row) == {'with', 'source_record', 'quote'},
                 '叠话依据须包含with、source_record和原文quote')
        targets = row['with']
        _require(isinstance(targets, list) and targets
                 and all(isinstance(rid, str) and re.fullmatch(r'(?:L|SV)\d{6,}', rid) for rid in targets)
                 and len(set(targets)) == len(targets) and part.get('record') not in targets,
                 '叠话须列出不同来源发言，不能与自身重叠')
        _require(isinstance(row['source_record'], str) and isinstance(row['quote'], str)
                 and len(row['quote'].strip()) >= 2, '叠话须提供具体原文证据')


def _actors(part, actor_key=lambda value: value):
    ids = part.get('character_ids') or ([part['character_id']] if part.get('character_id') else [])
    if ids:
        return {('character', actor_key(cid)) for cid in ids}
    return {('source', part.get('source_label') or part.get('record'))}


def validate_speech_order(unit, records, actor_key=lambda value: value):
    """Preserve per-source order and require exact source evidence for overlaps."""
    parts = [part for shot in unit['shots'] for part in shot.get('dialogue_parts', [])]
    active, pairs, previous_start = [], set(), Decimal('-1')
    for part in parts:
        basis = part.get('speech_basis', {})
        if basis:
            validate_basis(part, basis)
        declaration = basis.get('overlap')
        if declaration:
            evidence = records.get(declaration['source_record'])
            _require(evidence is not None and any(declaration['quote'] in evidence.get(field, '')
                     for field in ('text', 'label')), '叠话证据必须逐字来自本单元绑定来源')
            _require(part.get('direction', '').strip(), '叠话必须在声音表现中写明实际接入及主次关系')
        start, end = Decimal(str(part['start'])), Decimal(str(part['end']))
        _require(start >= previous_start, '声音片段倒序；须按实际开始时间及原文首次发声顺序编写')
        previous_start = start
        active = [prior for prior in active if Decimal(str(prior['end'])) > start]
        targets = set()
        for prior in active:
            _require(not (_actors(part, actor_key) & _actors(prior, actor_key)),
                     '声音片段倒序或重叠：同一声源不得重叠，包括联合发声及同一人物的不同形象')
            _require(declaration and prior['record'] in declaration['with'],
                     '不同声源重叠须有逐字来源依据及对应with记录')
            targets.add(prior['record'])
            pairs.add(frozenset((prior['record'], part['record'])))
        if declaration:
            _require(targets == set(declaration['with']), '叠话声明须准确对应当前实际重叠的来源发言')
        active.append(part)
    return pairs


def _reading_load(part):
    """Count declared syllables plus unchanged text; never rewrite a dialogue."""
    basis = part.get('speech_basis', {})
    if basis:
        validate_basis(part, basis)
    units, missing = reading_units(part['text'], basis.get('readings', []))
    return units, capacity_seconds(units), missing


def _text_load(text):
    """Raw count screening; punctuation counts never add time to its floor."""
    characters = character_count(text)
    commas = sum(char in '，、,' for char in text)
    endings = sum(char in '。！？!?' for char in text)
    minimum = capacity_seconds(characters)
    return characters, commas, endings, minimum


def _speech_window_facts(key, number, parts, start, end):
    """Describe authored unit-relative windows, not measured sound or pacing."""
    row = {'unit': key, 'shot': number, 'shot_start': float(start),
           'shot_end': float(end), 'timing_status': 'unavailable'}
    windows = []
    previous_start = start
    try:
        for part in parts:
            values = [part.get(field) for field in ('start', 'end')]
            if any(isinstance(value, bool) for value in values):
                raise ValueError('布尔值不是秒数')
            t0, t1 = (Decimal(str(value)) for value in values)
            if (not t0.is_finite() or not t1.is_finite()
                    or not start <= previous_start <= t0 < t1 <= end):
                raise ValueError('片段须按顺序位于镜窗内')
            for prior, (p0, p1) in zip(parts[:len(windows)], windows):
                if p1 > t0:
                    declaration = part.get('speech_basis', {}).get('overlap', {})
                    if (_actors(part) & _actors(prior) or prior.get('record') not in declaration.get('with', [])):
                        raise ValueError('重叠片段缺少不同声源及明确叠话依据')
            windows.append((t0, t1))
            previous_start = t0
    except (InvalidOperation, ValueError):
        row['reason'] = '缺少合法有序的镜内声音时窗，未推断或补齐。'
        return row
    fragments = []
    for index, (part, (t0, t1)) in enumerate(zip(parts, windows), 1):
        characters, commas, endings, minimum = _text_load(part['text'])
        duration = t1 - t0
        phonetic_units, phonetic_minimum, missing = _reading_load(part)
        authored_pause = part.get('speech_basis', {}).get('pause_seconds')
        known_required = capacity_seconds(phonetic_units, authored_pause or 0)
        fragments.append({
            'part': index, 'record': part.get('record'),
            'start': float(t0), 'end': float(t1), 'duration': float(duration),
            'dialogue_characters': characters,
            'comma_or_pause_count': commas, 'sentence_end_count': endings,
            'mechanical_minimum': float(minimum),
            'below_mechanical_minimum': duration < minimum,
            'deficit': float(max(Decimal(0), minimum - duration)),
            'phonetic_units': None if missing else phonetic_units,
            'phonetic_minimum': None if missing else float(phonetic_minimum),
            'authored_pause_seconds': authored_pause,
            'pause_status': 'authored' if authored_pause is not None else 'unspecified',
            'known_required_seconds': None if missing else float(known_required),
            'missing_readings': missing,
        })
    row.update(timing_status='available', fragments=fragments,
               before_first_speech=float(windows[0][0] - start),
               after_last_speech=float(end - max(t1 for _, t1 in windows)),
               between_fragments=[float(max(Decimal(0), windows[i][0] - max(t1 for _, t1 in windows[:i])))
                                  for i in range(1, len(windows))],
               overlapping_fragments=[i + 1 for i in range(1, len(windows))
                                      if windows[i][0] < max(t1 for _, t1 in windows[:i])])
    return row


def text_dialogue_capacity(units):
    """Screen raw text and check declared readings in actual speech windows.

    Raw character count is a nonblocking screening diagnostic.
    Source-supported overlap is screened per participant. Actual reading
    capacity uses reading units and authored internal pauses, never inferred
    punctuation silence. Natural pacing still requires actual review.
    """
    errors = []
    warnings = []
    window_facts = []
    checked, skipped, offset = 0, 0, Decimal(0)
    utterances = {}
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
            duration = end - start
            checked += 1
            try:
                facts = _speech_window_facts(key, number, parts, start, end)
            except ValueError as exc:
                errors.append({'code': 'SPEECH_BASIS_INVALID', 'unit': key, 'shot': number, 'kind': str(exc)})
                facts = {'unit': key, 'shot': number, 'timing_status': 'unavailable'}
            window_facts.append(facts)
            groups = {'serial': parts}
            if facts.get('overlapping_fragments'):
                groups = {}
                for part in parts:
                    for actor in _actors(part):
                        groups.setdefault(actor, []).append(part)
            for stream, stream_parts in groups.items():
                characters, commas, endings, minimum = _text_load(''.join(p['text'] for p in stream_parts))
                if duration < minimum:
                    warnings.append({
                        'code': 'DIALOGUE_CAPACITY', 'unit': key, 'shot': number,
                        'minimum': float(minimum), 'duration': float(duration),
                        'deficit': float(minimum - duration),
                        'dialogue_characters': characters, 'comma_or_pause_count': commas,
                        'sentence_end_count': endings, 'stream': stream,
                        'kind': '整镜原字符数仅作容量筛查，不含标点停顿；实际读法与已声明停顿另行检查。',
                    })
            if facts['timing_status'] != 'available':
                errors.append({'code': 'SPEECH_WINDOW_INVALID', 'unit': key, 'shot': number,
                               'kind': facts.get('reason', '声音依据无效，不能批准容量')})
                continue
            for part, fact in zip(parts, facts['fragments']):
                common = {'unit': key, 'shot': number, 'part': fact['part'], 'record': part.get('record')}
                if fact['missing_readings']:
                    errors.append({**common, 'code': 'SPEECH_READING_REQUIRED',
                                   'missing': fact['missing_readings'],
                                   'kind': '数字、英文或缩写须声明本片段的实际读法，不能按字符数冒充发音单位。'})
                if fact['below_mechanical_minimum']:
                    warnings.append({**common, 'code': 'SPEECH_WINDOW_CAPACITY',
                                   'minimum': fact['mechanical_minimum'], 'duration': fact['duration'],
                                   'kind': '片段原字符数仅作容量筛查，不含标点停顿；不单独阻断。'})
                _, phonetic_minimum, missing = _reading_load(part)
                authored_pause = part.get('speech_basis', {}).get('pause_seconds')
                known_required = phonetic_minimum + Decimal(str(authored_pause or 0))
                if not missing and Decimal(str(part['end'])) - Decimal(str(part['start'])) < known_required:
                    errors.append({**common, 'code': 'SPEECH_READING_CAPACITY',
                                   'minimum': float(known_required), 'duration': fact['duration'],
                                   'phonetic_minimum': float(phonetic_minimum),
                                   'authored_pause_seconds': authored_pause,
                                   'kind': '实际声音窗口放不下读法负荷及作者已声明的片段内部停顿。'})
                utterances.setdefault(part.get('record'), []).append({
                    **common, 'start': float(offset + Decimal(str(part['start']))),
                    'end': float(offset + Decimal(str(part['end']))), 'text': part['text'],
                    '_start': offset + Decimal(str(part['start'])),
                    '_end': offset + Decimal(str(part['end'])),
                    'phonetic_units': fact['phonetic_units'], 'missing_readings': missing,
                    'phonetic_minimum': None if missing else phonetic_minimum,
                    'authored_pause_seconds': authored_pause,
                    'known_required_seconds': None if missing else known_required,
                })
        offset += Decimal(str(unit.get('duration', max((s['end'] for s in unit.get('shots', [])), default=0))))
    utterance_facts = []
    for record, parts in utterances.items():
        raw = ''.join(p['text'] for p in parts)
        _, _, _, minimum = _text_load(raw)
        available = sum((p['_end'] - p['_start'] for p in parts), Decimal(0))
        readings_complete = all(p['phonetic_units'] is not None for p in parts)
        reading_minimum = (sum((p['phonetic_minimum'] for p in parts), Decimal(0))
                           if readings_complete else None)
        known_required = (sum((p['known_required_seconds'] for p in parts), Decimal(0))
                          if readings_complete else None)
        declared_pause = sum((Decimal(str(p['authored_pause_seconds'] or 0)) for p in parts), Decimal(0))
        public_parts = [{key: float(value) if isinstance(value, Decimal) else value
                         for key, value in part.items() if not key.startswith('_')} for part in parts]
        # Scan the unchanged utterance once, then locate each cluster in every
        # touched fragment. A cut cannot create a new punctuation pause.
        punctuation = punctuation_reference(raw)
        text_offset = 0
        spans = []
        for part in parts:
            spans.append((text_offset, text_offset + len(part['text']), part))
            text_offset += len(part['text'])
        for cluster in punctuation['clusters']:
            cluster['parts'] = [
                {key: part[key] for key in ('unit', 'shot', 'part')}
                for left, right, part in spans
                if left < cluster['end'] and cluster['start'] < right]
        utterance_facts.append({'record': record, 'parts': public_parts, 'text': raw,
            'speech_seconds': float(available), 'mechanical_minimum': float(minimum),
            'phonetic_minimum': float(reading_minimum) if readings_complete else None,
            'authored_pause_seconds': float(declared_pause),
            'pause_status': 'authored' if all(p['authored_pause_seconds'] is not None for p in parts)
                            else 'partial' if any(p['authored_pause_seconds'] is not None for p in parts)
                            else 'unspecified',
            'known_required_seconds': float(known_required) if readings_complete else None,
            'punctuation_reference': punctuation,
            'between_parts': [float(parts[i]['_start'] - parts[i - 1]['_end']) for i in range(1, len(parts))]})
        if readings_complete and available < known_required:
            errors.append({'code': 'UTTERANCE_CAPACITY', 'unit': parts[0]['unit'], 'shot': parts[0]['shot'],
                           'record': record, 'kind': '本选区同次发言的实际声音窗口合计不足，跨镜间隔不计为发声。'})
    return {'policy': TEXT_DIALOGUE_CAPACITY_POLICY,
            'status': 'blocked' if errors else 'checked',
            'errors': errors, 'warnings': warnings, 'checked_dialogue_shots': checked,
            'mechanical_screening': {'blocking': False, 'acceptance_standard': False,
                'basis': 'raw_characters_without_punctuation_pauses',
                'limits': '字符数仅快速定位容量疑点；不推断读法或停顿，未命中不代表自然语气合格。'},
            'skipped_no_dialogue_shots': skipped,
            'speech_window_diagnostics': {
                'blocking': True, 'time_basis': 'authored_unit_relative_seconds',
                'shots': window_facts,
                'limits': '仅核读法底线及已声明的片段内部停顿；未声明停顿不等于实际无停顿。'
                          '标点仅提供软参考，片段间隔不充作发音时间；自然气口与声画关系须实际审阅。',
            },
            'utterance_diagnostics': {'time_basis': 'selected_units_concatenated_seconds',
                'records': utterance_facts, 'scope': '仅合看本候选实际原话片段；不推断范围外发言或实际录音'},
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
        weights = punctuation_weights([p['text'] for p in parts])
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
