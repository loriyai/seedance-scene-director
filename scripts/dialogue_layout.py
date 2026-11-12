"""Explicit speech layout; never divide words by shot-duration proportions."""
import re

POLICY = 'semantic-v2'
LEGACY_POLICY = 'semantic-v1'
VOICE_POLICY = 'event-inheritance-v1'
SPEECH_RENDER_POLICY = 'explicit-cues-v1'
# One numerical source for the legacy text-estimate consumers. These are
# diagnostic ranges, not playback controls or mandatory speaking instructions.
SPEEDS = {'常速': (4.8, 5.4), '快节奏': (4.8, 6.0), '情绪慢速': (4.0, 4.8)}
PAUSE_RANGES = {'comma': (.1, .2), 'sentence': (.1, .25)}
DEFAULT_RATE = sum(SPEEDS['常速']) / 2
# Ellipses, dashes, colons and semicolons need contextual interpretation. This
# fallback only weights an estimate; an explicitly registered pause replaces it.
SEMANTIC_PAUSE_ESTIMATE = .26
PUNCTUATION = '，、,。？！?!；;…—～~：:'
TAILS = set('了呢吧啊呀哦嘛么啦呗哟喽的')
DELIVERY_FIELDS = (('volume', '音量'), ('ending', '尾音'),
                   ('texture', '发声质感'), ('articulation', '咬字'))
SPEED_LABELS = {'常速': '自然语速', '快节奏': '较快语速', '情绪慢速': '放缓语速'}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def compact(text):
    return ''.join(c for c in text if c.isalnum())

def prepare_render_policy(unit):
    """Version newly authored/rearranged v3 drafts before any preview or review.

    Receipt readers never call this function. Re-exporting an old unit therefore
    cannot silently replace its persisted rendering behavior.
    """
    require(unit.get('output_format') == 'reference-v3',
            'speech_render_policy须使用reference-v3')
    require(unit.get('speech_render_policy', SPEECH_RENDER_POLICY) == SPEECH_RENDER_POLICY,
            '未知speech_render_policy，不能静默迁移')
    unit['speech_render_policy'] = SPEECH_RENDER_POLICY
    return unit

def validate_delivery_value(unit, value):
    """Shared schema for event baselines and local transitions, never inference."""
    require(unit.get('output_format') == 'reference-v3' and unit.get('dialogue_layout') == POLICY,
            'delivery仅用于reference-v3与semantic-v2；旧交付不自动补标')
    require(isinstance(value, dict) and bool(value), 'delivery须为非空对象；没有声音表现要求时省略')
    require(set(value) <= {key for key, _ in DELIVERY_FIELDS},
            'delivery只允许volume/ending/texture/articulation；语速来自audio.speed，停顿来自audio.pause')
    for field, cue in value.items():
        require(isinstance(cue, str) and cue.strip() and re.search(r'[\u4e00-\u9fff]', cue)
                and not any(c in cue for c in '\r\n@"\'“”‘’（）()[]{}')
                and compact(cue) not in ('无', '同前', '同上'),
                f'delivery.{field}须为具体单行中文声音描述，不含引号、括号或资产引用')

def validate_voice_policy(unit):
    if 'speech_render_policy' in unit:
        require(unit['speech_render_policy'] == SPEECH_RENDER_POLICY,
                'speech_render_policy只支持explicit-cues-v1；历史交付省略以保留原渲染')
        require(unit.get('output_format') == 'reference-v3',
                'speech_render_policy须使用reference-v3')
    if 'voice_direction' in unit:
        require(unit['voice_direction'] == VOICE_POLICY,
                'voice_direction只支持event-inheritance-v1；旧交付省略以保留原渲染')
        require(unit.get('output_format') == 'reference-v3' and unit.get('dialogue_layout') == POLICY,
                'voice_direction须使用reference-v3与semantic-v2')
    for event in unit.get('audio', []):
        if 'delivery' in event:
            require(unit.get('voice_direction') == VOICE_POLICY,
                    'audio.delivery须显式启用voice_direction: event-inheritance-v1')
            validate_delivery_value(unit, event['delivery'])

def validate_delivery(unit, part):
    """Validate reliable structure; review still checks source and timing basis."""
    if 'delivery' in part:
        validate_delivery_value(unit, part['delivery'])
    modern = unit.get('voice_direction') == VOICE_POLICY
    transition = 'delivery' in part or 'delivery_reset' in part
    if 'delivery_reset' in part or 'transition_basis' in part:
        require(modern, 'delivery_reset/transition_basis须启用event-inheritance-v1')
    if not modern:
        return
    if 'delivery_reset' in part:
        require(part['delivery_reset'] is True, 'delivery_reset只能为true；不重置时省略')
        require('delivery' not in part, 'delivery_reset清除继承表现，不得同时填写delivery')
    if transition:
        basis = part.get('transition_basis')
        require(isinstance(basis, str) and len(compact(basis)) >= 6
                and re.search(r'[\u4e00-\u9fff]', basis)
                and not any(c in basis for c in '\r\n@"\'“”‘’（）()[]{}')
                and compact(basis) not in ('同前', '同上', '语气变化', '原文如此', '自然变化'),
                '声音覆盖或重置须填具体单行transition_basis，说明原文转折及发生位置；整句基线填audio.delivery')
    else:
        require('transition_basis' not in part, 'transition_basis须对应delivery覆盖或delivery_reset')

def effective_delivery(unit, part, event):
    """A cue persists within one event; a cut alone never clears it."""
    if unit.get('voice_direction') != VOICE_POLICY:
        return part.get('delivery') or {}
    value = dict(event.get('delivery') or {})
    for shot in unit.get('shots', []):
        for current in shot.get('dialogue_parts', []):
            if current.get('record') != event['record']:
                continue
            if current.get('delivery_reset') is True:
                value.clear()
            value.update(current.get('delivery') or {})
            if current is part:
                return value
    raise ValueError('声音表现解析的片段不属于当前单元声音事件')

def delivery_text(part, event, unit=None):
    """Render explicit cues; unversioned receipts retain their historical text."""
    modern = unit is not None and unit.get('voice_direction') == VOICE_POLICY
    if unit is not None and 'speech_render_policy' in unit:
        require(unit['speech_render_policy'] == SPEECH_RENDER_POLICY,
                '未知speech_render_policy，不能按历史策略渲染')
    explicit_only = unit is not None and unit.get('speech_render_policy') == SPEECH_RENDER_POLICY
    value = effective_delivery(unit, part, event) if modern else part.get('delivery')
    if not value and (explicit_only or not (modern and event['speed'] != '常速')):
        return ''
    value = value or {}
    cues = []
    if 'volume' in value:
        cues.append('音量：' + value['volume'].strip())
    if not explicit_only:
        cues.append('语速：' + SPEED_LABELS[event['speed']])
    cues.extend(label + '：' + value[key].strip() for key, label in DELIVERY_FIELDS[1:] if key in value)
    return '（' + '；'.join(cues) + '）'

def voice_warnings(unit):
    """Flag legacy partial annotation, without inventing missing directions."""
    if unit.get('voice_direction') == VOICE_POLICY:
        return []
    warnings = []
    for event in unit.get('audio', []):
        parts = [p for shot in unit.get('shots', []) for p in shot.get('dialogue_parts', [])
                 if p.get('record') == event['record']]
        annotated = [i + 1 for i, part in enumerate(parts) if part.get('delivery')]
        if not annotated or len(parts) < 2:
            continue
        if len(annotated) != len(parts):
            warnings.append({'unit': unit.get('id', '当前单元'), 'record': event['record'],
                             'kind': '跨镜声音表现可能漏续', 'annotated_parts': annotated,
                             'detail': '同一声音事件仅部分片段有声音表现；复核原文是否持续，旧稿不自动补标。'})
        elif any(part.get('delivery') != parts[0].get('delivery') for part in parts[1:]):
            warnings.append({'unit': unit.get('id', '当前单元'), 'record': event['record'],
                             'kind': '跨镜声音变化待复核',
                             'detail': '同一句的局部表现不同；复核是否有原文转折，不能因切镜重置语气。'})
    return warnings

def pause_seconds(text):
    """Estimate each punctuation cluster once, never replace an explicit pause."""
    total = 0.0
    for cluster in re.findall(r'[，、,。？！?!；;…—～~：:]+', text):
        if any(c in '。？！?!' for c in cluster):
            total += sum(PAUSE_RANGES['sentence']) / 2
        elif any(c in '；;…—～~：:' for c in cluster):
            total += SEMANTIC_PAUSE_ESTIMATE
        else:
            total += sum(PAUSE_RANGES['comma']) / 2
    return round(total, 3)

def estimate(text, units, rate=DEFAULT_RATE):
    require(type(units) is int and units > 0 and rate > 0, '发音单位和语速须有效')
    return round(units / rate + pause_seconds(text), 3)

def attach(unit, plans=None):
    """Place whole utterances or author-supplied semantic chunks at exact times.

    plans: record -> [{text,start,end}]. Default: quote the entire event once.
    New chunks must fit their own shot. Authors supply semantic boundaries;
    this helper never guesses word cuts. Legacy receipts retain v1 links.
    """
    unit['dialogue_layout'] = LEGACY_POLICY if unit.get('output_format') == 'reference-v1' else POLICY
    if unit.get('output_format') == 'reference-v3':
        prepare_render_policy(unit)
    for shot in unit['shots']:
        shot['dialogue_parts'] = []
        shot.pop('dialogue_continuations', None)
    for event in unit['audio']:
        chunks = (plans or {}).get(event['record'],
            [{'text': event['text'], 'start': event['start'], 'end': event['end']}])
        for index, chunk in enumerate(chunks):
            part = dict(chunk, record=event['record'])
            shot = next((s for s in unit['shots'] if s['start'] <= part['start'] < s['end']), None)
            require(shot is not None, '台词起点无对应镜头')
            shot['dialogue_parts'].append(part)
            for following in unit['shots']:
                if unit['dialogue_layout'] == LEGACY_POLICY and following['start'] > shot['start'] and following['start'] < part['end']:
                    following.setdefault('dialogue_continuations', []).append(
                        {'record': event['record'], 'segment': index})
    validate(unit)
    return unit

def validate(unit):
    require(unit.get('dialogue_layout') in (POLICY, LEGACY_POLICY), '须采用semantic-v2台词布局；禁止按镜时比例切字')
    validate_voice_policy(unit)
    local = unit['dialogue_layout'] == POLICY
    events = {e['record']: e for e in unit['audio']}
    grouped = {rid: [] for rid in events}
    actual_links = []
    for i, shot in enumerate(unit['shots']):
        for part in shot.get('dialogue_parts', []):
            require(part.get('record') in events, '台词片段声源不存在')
            validate_delivery(unit, part)
            grouped[part['record']].append((i, part))
        for link in shot.get('dialogue_continuations', []):
            require(link.get('record') in events and type(link.get('segment')) is int, '续声引用无效')
            actual_links.append((i, link['record'], link['segment']))
    expected_links = []
    require(not local or not actual_links, 'semantic-v2逐镜写实际台词，不使用抽象续声引用')
    for rid, event in events.items():
        parts = grouped[rid]
        require(parts and ''.join(p['text'] for _, p in parts) == event['text'], '语义台词有漏字、重复或改词')
        for _, part in parts:
            require(all(isinstance(part.get(k), (int,float)) for k in ('start','end')), '台词须有实际发声时码')
        require(abs(parts[0][1]['start']-event['start']) <= .001 and
                abs(parts[-1][1]['end']-event['end']) <= .001, '台词实际时码未覆盖声音事件首尾')
        previous_end = event['start']
        gaps = 0
        for index, (i, part) in enumerate(parts):
            text = part['text']
            t0, t1 = part['start'], part['end']
            require(t0 >= previous_end-.001 and t0 < t1 <= event['end']+.001,
                    '语义片段发声时码倒序、重叠或越界')
            require(round(t0,3) == t0 and round(t1,3) == t1, '发声时码最多三位小数')
            require(compact(text), '禁止把纯标点分配成独立台词')
            if len(parts) > 1:
                require(not (len(compact(text)) == 1 and compact(text) in TAILS), '禁止孤立句尾语气词')
                basis = part.get('breath_basis')
                if basis is not None:
                    require(isinstance(basis, str) and basis.strip() and not any(c in basis for c in '\r\n')
                            and re.search(r'[\u4e00-\u9fff]', basis), '人工自然气口依据须为非空单行中文说明')
                if index < len(parts)-1:
                    require(text.rstrip()[-1] in PUNCTUATION or local and basis,
                            '台词切点必须位于原文完整分句或自然气口，不能从词句中间切字；无标点气口须填breath_basis')
                require(text.lstrip()[0] not in PUNCTUATION, '禁止将标点留给下一台词片段')
            shot = unit['shots'][i]
            require(shot['start'] <= t0 < shot['end'], '完整台词必须写在实际开始发声的镜头')
            if local:
                require(t1 <= shot['end']+.001, '台词片段必须完整落在本镜；长句按标点和语义分句，或重排切点')
            if t1-t0 <= 6+.001:
                require(t1 <= shot['end']+.001, '可在单镜说完的短句必须完整落在一镜；调整切点或发声起点')
            if len(parts) == 1:
                low = SPEEDS[event['speed']][0]
                if event['units']/low + event['pause'] <= 6+.001:
                    require(t1 <= shot['end']+.001, '不能把自然短句拉长后跨镜；应重排镜头')
            for j, following in enumerate(unit['shots']):
                if j > i and following['start'] < t1:
                    expected_links.append((j,rid,index))
            gaps += max(0,t0-previous_end)
            previous_end = t1
        require(gaps <= event['pause']+.001, '片段之间的实际停顿超过声音事件登记的总停顿')
    require(len(actual_links) == len(set(actual_links)) and sorted(actual_links) == sorted(expected_links),
            '跨镜续声引用缺失、重复或超出实际发声时段')

def clock(value):
    return f'{value:.3f}'.rstrip('0').rstrip('.')

def timing(part, shot, measured=False):
    continuity = '，跨镜连续声' if part['end'] > shot['end'] else ''
    label = '实配音' if measured else '发声估时'
    return f'（{label}{clock(part["start"])}秒至{clock(part["end"])}秒{continuity}）'
