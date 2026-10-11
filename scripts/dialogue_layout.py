"""Explicit speech layout; never divide words by shot-duration proportions."""
import re

POLICY = 'semantic-v2'
LEGACY_POLICY = 'semantic-v1'
PUNCTUATION = '，、,。？！?!；;…—～~：:'
TAILS = set('了呢吧啊呀哦嘛么啦呗哟喽的')

def require(ok, message):
    if not ok:
        raise ValueError(message)

def compact(text):
    return ''.join(c for c in text if c.isalnum())

def pause_seconds(text):
    """Repeated punctuation is a single semantic pause."""
    return round(sum(.14 if m[0] in '，、,' else .26 if m[0] in '…—～~' else .34
                     for m in re.findall(r'[，、,]+|[。？！?!；;]+|[…—～~]+', text)), 3)

def estimate(text, units, rate=4.8):
    require(type(units) is int and units > 0 and rate > 0, '发音单位和语速须有效')
    return round(units / rate + pause_seconds(text), 3)

def attach(unit, plans=None):
    """Place whole utterances or author-supplied semantic chunks at exact times.

    plans: record -> [{text,start,end}]. Default: quote the entire event once.
    New chunks must fit their own shot. Authors supply semantic boundaries;
    this helper never guesses word cuts. Legacy receipts retain v1 links.
    """
    unit['dialogue_layout'] = LEGACY_POLICY if unit.get('output_format') == 'reference-v1' else POLICY
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
    local = unit['dialogue_layout'] == POLICY
    events = {e['record']: e for e in unit['audio']}
    grouped = {rid: [] for rid in events}
    actual_links = []
    for i, shot in enumerate(unit['shots']):
        for part in shot.get('dialogue_parts', []):
            require(part.get('record') in events, '台词片段声源不存在')
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
                if index < len(parts)-1:
                    require(text.rstrip()[-1] in PUNCTUATION, '台词切点必须位于原文完整分句或自然气口，不能从词句中间切字')
                require(text.lstrip()[0] not in PUNCTUATION, '禁止将标点留给下一台词片段')
            shot = unit['shots'][i]
            require(shot['start'] <= t0 < shot['end'], '完整台词必须写在实际开始发声的镜头')
            if local:
                require(t1 <= shot['end']+.001, '台词片段必须完整落在本镜；长句按标点和语义分句，或重排切点')
            if t1-t0 <= 5+.001:
                require(t1 <= shot['end']+.001, '可在单镜说完的短句必须完整落在一镜；调整切点或发声起点')
            if len(parts) == 1:
                low = {'常速':4.0,'快节奏':4.5,'情绪慢速':3.3}[event['speed']]
                if event['units']/low + event['pause'] <= 5+.001:
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
