"""Bind authored black frames to the original script and measure viewing time."""
from decimal import Decimal
import re


_ID = r'L\d{6,}'
_TIME = r'\d+(?:\.\d{1,3})?'
BLACKOUT_FOCAL = re.compile(rf'黑场\[({_ID})-({_ID})\]')
BLACKOUT_BODY = '纯黑画面，无可见对象。'
INLINE_MARKER = re.compile(rf'切黑\[({_ID})-({_ID}),({_TIME})-({_TIME})秒\]')
_QUOTES = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"')
_CLAUSE = re.compile(r'[，,。；;！？!?\n【】]')
_SUBJECT = r'(?:画面|整屏|全屏|满屏|整幅画幅|全画幅|镜头|影像|屏幕)'
_BLACK = r'(?:纯黑(?:色)?|全黑|一片(?:漆黑|黑暗)|完全(?:黑暗|漆黑)|漆黑一片)'
_CUE = re.compile(
    rf'{_SUBJECT}(?:逐渐|骤然|瞬间|完全|最终|仍然|始终|一直|保持|为|呈现|变成|变为|变得|陷入|转为|进入|只有|仅剩|只剩下?|是)*{_BLACK}'
    rf'|{_SUBJECT}(?:内|中)(?:只有|仅剩|只剩下?)(?:{_BLACK}|黑暗)'
    rf'|{_SUBJECT}(?:骤然|渐渐|瞬间|突然|完全|彻底|随之|逐渐|慢慢)*(?:一黑|变黑|转黑|黑下来|只有黑暗)(?!色|的)'
    r'|(?:暗|渐隐|隐没|淡出)(?:至|到|为)(?:完全)?(?:全黑|纯黑|黑屏|漆黑)'
    r'|(?:全黑|纯黑)(?:一片|无(?:任何)?可见|没有(?:任何)?可见)'
    r'|(?:切(?:至|入|成|为|到)?|淡(?:出)?(?:至|入|成|到|为)|转(?:入|至|为|成)|渐(?:变)?(?:为|至))'
    r'(?:纯黑(?:画面)?|全黑(?:画面)?|黑场|黑屏|黑幕(?=[。；，、\s】]|$)|黑(?![色衣夜暗影发眸布幕板底背景]))'
    r'|(?:纯黑|全黑)(?:画面|空镜)|黑场(?!地|景|灯|道具)|黑屏|cut\s+to\s+black|fade\s+to\s+black|black\s+screen',
    re.IGNORECASE)
_BARE = re.compile(r'\s*(?:转场[：:]\s*)?(?:全黑|纯黑(?:色)?|一片漆黑)(?:\s*\d+(?:\.\d+)?秒)?\s*')
_ENTIRE = re.compile(r'(?:整镜|全程|整段|始终|一直)?(?:保持|画面保持|画面为|画面|只有|仅有|仅剩|只剩|均为|为)?'
                     r'(?:一片纯黑|纯黑(?:色)?(?:画面)?|全黑(?:画面)?|黑屏|黑场)'
                     r'(?:[，,]\s*(?:没有|无)(?:任何)?可见(?:对象|物体|主体|影像))?[。.]?')
_ALL_SHOT = re.compile(r'(?:整镜|整个镜头|全程|从头[至到]尾)(?:画面)?'
                       r'(?:只有|为|均为|保持|维持|只剩下?|都是|完全是)?(?:纯黑|全黑|黑屏|黑场)')
_CUT_VERB = re.compile(r'切|淡[入出至]|渐隐|转[至为入黑]|变黑|一黑|cut to|fade to', re.IGNORECASE)
_NEGATIVE = re.compile(r'(?:不要|不用|不能|不可|不应|不得|不必|不让|避免|禁止|并非|并未|没有|未能|拒绝|切勿|无需|不|未|(?<!分)别|如果|假如|若|是否|以免)[^，。；！？【】]{0,18}$')
_OBJECT = re.compile(r'(?:手机|电视|电脑|显示器|显示屏|监控|平板|车载屏|银幕上|荧幕上|投影仪|投影布|相机|电子屏|车机|屏幕上|屏中|片中片)[^，。；！？【】]{0,10}$')
_MENTION = re.compile(r'(?:提到|谈起|说起|写着|写有|写下|念出|印着|读到|讨论|听说)[^，。；！？【】]{0,10}$')
_WORDS = re.compile(r'(?:二字|两个字|的?字样|的说法|的提议)')
_DARK_SUBJECT = re.compile(r'(?:屋内|屋里|屋中|室内|街道|夜空|灯光|烛光|光线|眼前|视野|视线)[^，。；！？【】]{0,8}$')
_EXIT = re.compile(r'(?:从|由|走出|脱离|离开|结束|打破|驶出|冲出)\s*$')
_EXIT_TAIL = re.compile(r'(?:中|里|后)?(?:切回|切入|亮起|淡入|淡出至可见|恢复可见|结束)')
_SWATCH = re.compile(r'(?:的)?(?:布料|背景布|背景板|幕布|照片|图案|纹样|片段素材|过渡素材|手机|电视|显示器|显示屏|电脑|平板|屏幕道具)')
_DEVICE = re.compile(r'手机|电视|电脑|显示器|平板|监控|投影|车载|车机|手表|游戏机')
_REMAINS_VISIBLE = re.compile(r'(?:依然|仍然?|还|清楚地|清晰地)(?:能|可以?)?(?:看(?:到|见|清)|见|辨)')
_OFFSCREEN = {'画外音', '旁白', '内心想法', '内心想法os', '非人物播报'}


def _cues(text, *, source=False):
    """Positive picture directions only; words quoted or applied to props are data."""
    text = _QUOTES.sub(' ', text)
    if _ENTIRE.fullmatch(text.strip()):
        return [text.strip()]
    matches = []
    for clause in _CLAUSE.split(text):
        body = clause.strip()
        standalone = re.search(r'(?:^|[。；;！？!?【\n])\s*' + re.escape(body)
                               + r'\s*(?:$|[。；;！？!?】\n])', text) if _BARE.fullmatch(clause) else None
        if standalone and not (source and '一片漆黑' in clause):
            matches.append(clause.strip())
            continue
        if _ENTIRE.fullmatch(body) and not _BARE.fullmatch(clause):
            if '黑屏' not in body or not (_DEVICE.search(text) or _REMAINS_VISIBLE.search(text)):
                matches.append(body)
                continue
        for match in _CUE.finditer(clause):
            left, right = clause[:match.start()], clause[match.end():]
            if (_NEGATIVE.search(left) or _OBJECT.search(left) or _SWATCH.match(right)
                    or _MENTION.search(left) or _WORDS.match(right)
                    or (_EXIT.search(left) and _EXIT_TAIL.match(right))):
                continue
            if (_DARK_SUBJECT.search(left) and not re.search(r'画面|镜头', left[-5:] + match.group())
                    and not match.group().startswith(('切', '淡出至', '淡入'))):
                continue
            # An unspecified screen may be an object inside a visible shot.
            # A script must unambiguously direct the viewer's picture to black.
            screen = match.group().startswith('屏幕') or match.group() == '黑屏' or left.endswith('屏幕')
            if screen and (_DEVICE.search(text) or _REMAINS_VISIBLE.search(text)):
                continue
            if source and screen and not re.fullmatch(
                    r'\s*(?:【\s*)?(?:转场[：:]\s*)?(?:黑屏|画面黑屏)(?:\s*\d+(?:\.\d+)?\s*秒)?\s*[。】]?\s*',
                    text):
                continue
            matches.append(match.group())
    return matches


def _holds_after(text):
    """A later visible action in the same record terminates a cut before any VO."""
    text = _QUOTES.sub(' ', text)
    clauses = [clause.strip(' ：:') for clause in _CLAUSE.split(text) if clause.strip(' ：:')]
    last_cut = max((i for i, clause in enumerate(clauses) if _cues(clause, source=True)), default=-1)
    if last_cut < 0:
        return False
    return all(re.fullmatch(r'(?:保持|持续|停留|停顿)?\s*\d+(?:\.\d+)?\s*秒|'
                            r'(?:只听|传来|响起|听见|画外音|旁白|音效|声音).*', clause)
               for clause in clauses[last_cut + 1:])


class _Source:
    def __init__(self, records):
        self.records = records
        self.positions = {record['id']: i for i, record in enumerate(records)}

    def cut_count(self, anchor):
        return len(_cues(self.records[self.positions[anchor]].get('text', ''), source=True))

    def proof(self, first, last, unit):
        start, end = self.positions.get(first), self.positions.get(last)
        if start is None or end is None or start > end:
            raise ValueError('纯黑画面的剧本来源不存在或倒序')
        selection = self.records[start:end + 1]
        ids = [record['id'] for record in selection]
        if not set(ids).issubset(unit.get('source_records') or ()):
            raise ValueError('黑场声明来源须属于本单元实际来源范围')
        scene = selection[0]['scene']
        if any(record['scene'] != scene for record in selection):
            raise ValueError('黑场声明的剧本来源不得跨场')
        # A continuation may start on a VO in the following unit. Never look
        # back through a picture action, an on-camera line, or a scene change.
        anchor = start
        while anchor >= 0:
            record = self.records[anchor]
            if record['scene'] != scene:
                break
            if record['kind'] != 'dialogue' or record.get('voice') not in _OFFSCREEN:
                break
            anchor -= 1
        if anchor < 0:
            raise ValueError('此处剧本没有明确的切黑或全黑画面指示')
        cue = self.records[anchor]
        if (cue['scene'] != scene or cue['kind'] != 'action' or not _cues(cue.get('text', ''), source=True)
                or (end > anchor and not _holds_after(cue.get('text', '')))):
            raise ValueError('此处剧本没有有效的切黑或全黑画面指示')
        for record in self.records[anchor + 1:end + 1]:
            if record['kind'] != 'dialogue' or record.get('voice') not in _OFFSCREEN:
                raise ValueError('黑场来源范围内出现另一个动作或画内发声，须重新定位切黑依据')
        return ids, cue['id']


def blackout_records(focal, body, records, unit):
    """Resolve a full-shot declaration to the record IDs used by the author trace."""
    if focal != '黑场' and not focal.startswith('黑场['):
        return None
    match = BLACKOUT_FOCAL.fullmatch(focal)
    if match is None:
        raise ValueError('黑场须绑定原剧本来源，写作 黑场[L000010-L000010]')
    if body.strip() != BLACKOUT_BODY:
        raise ValueError('整镜黑场的视觉正文须为“纯黑画面，无可见对象。”')
    ids, _ = _Source(records).proof(*match.groups(), unit)
    return ids


def _markers(transition):
    markers = list(INLINE_MARKER.finditer(transition))
    if '切黑[' in INLINE_MARKER.sub('', transition):
        raise ValueError('镜内黑场须写作 切黑[L000010-L000010,0-1.25秒]，使用镜内相对秒数，最多三位小数')
    return markers


def inline_record_ids(transition, records, unit):
    markers = _markers(transition)
    if not markers:
        return []
    source = _Source(records)
    ids = []
    for match in markers:
        selected, _ = source.proof(match[1], match[2], unit)
        ids.extend(selected)
    return list(dict.fromkeys(ids))


def display_transition(transition):
    return INLINE_MARKER.sub(lambda m: f'镜内{m[3]}-{m[4]}秒纯黑', transition)


def _seconds(value):
    return format(Decimal(str(value)).normalize(), 'f')


def retime_transition(value, remap):
    def replace(match):
        start, end = remap(float(match[3]), float(match[4]))
        return f'切黑[{match[1]}-{match[2]},{_seconds(start)}-{_seconds(end)}秒]'
    return INLINE_MARKER.sub(replace, value)


def _shot_intervals(shot, fields, source, unit, report):
    """Return viewer-relative black intervals and their original cut records."""
    _, _, _, label, focal, transition, body, _ = fields
    length = Decimal(str(shot['end'])) - Decimal(str(shot['start']))
    full = BLACKOUT_FOCAL.fullmatch(focal)
    if full or focal == '黑场':
        anchor = None
        try:
            if not full:
                raise ValueError('整镜黑场缺少原剧本的切黑来源绑定')
            _, anchor = source.proof(*full.groups(), unit)
        except ValueError as exc:
            report('BLACKOUT_SOURCE', str(exc))
        if body.strip() != BLACKOUT_BODY or '切黑[' in transition:
            report('BLACKOUT_TIMING', '整镜纯黑的正文和镜内黑场标记不一致')
        return [(Decimal(0), length, anchor)]
    try:
        markers = _markers(transition)
    except ValueError as exc:
        report('BLACKOUT_TIMING', str(exc))
        return []
    plain_transition = INLINE_MARKER.sub('', transition)
    fields_to_check = (plain_transition, body, focal, label)
    if not markers and any(_cues(field) for field in fields_to_check):
        report('BLACKOUT_SOURCE', '黑场须绑定剧本在该处的明确切黑依据；镜内纯黑还须在转场标明起止秒数')
        if _ENTIRE.fullmatch(body.strip()) or _BARE.fullmatch(body.strip('。')):
            return [(Decimal(0), length, None)]
    spans = []
    for match in markers:
        start, end = Decimal(match[3]), Decimal(match[4])
        if not 0 <= start < end <= length or (spans and start < spans[-1][1]):
            report('BLACKOUT_TIMING', '镜内纯黑区间须在本镜范围内顺序排列，起止有效且不得重叠')
            continue
        anchor = None
        try:
            _, anchor = source.proof(match[1], match[2], unit)
        except ValueError as exc:
            report('BLACKOUT_SOURCE', str(exc))
        spans.append((start, end, anchor))
    if markers:
        # An author can describe the declared cut and return in ordinary
        # visual prose. Count new cut verbs, not repeated names for its state.
        if any(sum(bool(_CUT_VERB.search(cue)) for cue in _cues(field)) > len(markers)
               for field in fields_to_check):
            report('BLACKOUT_TIMING', '正文所写的切黑次数多于已标明起止时间的镜内黑场')
        if (_ENTIRE.fullmatch(body.strip()) or _ALL_SHOT.search(body)) and sum(
                end - start for start, end, _ in spans) < length:
            report('BLACKOUT_TIMING', '正文写为整镜纯黑，与所标镜内黑场时段矛盾')
    return spans


def blackout_errors(units, records, *, target_unit_ids=None):
    """Check one ordered viewing sequence, optionally reporting only affected edits.

    Unit clocks restart at zero. Only a positive interval of visible picture
    ends a run. Saved neighbours supply boundary context without being edited.
    """
    from storyboard_text import SHOT

    errors = []
    source = _Source(records)
    run = Decimal(0)
    target = None
    anchors, consumed = set(), {}

    def finish():
        nonlocal run, target
        if run > 3 and target is not None:
            key, index = target
            errors.append({'code': 'BLACKOUT_DURATION', 'unit': key, 'shot': index,
                           'message': f'{key}/镜头{index}：连续纯黑已达{_seconds(run)}秒，最多3秒'})
        for anchor in anchors:
            consumed[anchor] = consumed.get(anchor, 0) + 1
        anchors.clear()
        run, target = Decimal(0), None

    for unit in units:
        checked = target_unit_ids is None or id(unit) in target_unit_ids
        key = unit.get('draft_key', unit.get('id', ''))
        shots = unit.get('shots')
        if not isinstance(shots, list) or not shots:
            finish()
            continue
        for index, shot in enumerate(shots, 1):
            def report(code, message):
                if checked:
                    errors.append({'code': code, 'unit': key, 'shot': index,
                                   'message': f'{key}/镜头{index}：{message}'})

            match = SHOT.fullmatch(shot['text']) if isinstance(shot, dict) and isinstance(shot.get('text'), str) else None
            if not match or not all(isinstance(shot.get(k), (int, float, Decimal)) for k in ('start', 'end')):
                finish()
                continue
            spans = _shot_intervals(shot, match.groups(), source, unit, report)
            cursor = Decimal(0)
            for start, end, anchor in spans:
                if start > cursor:
                    finish()
                if anchor:
                    if anchor not in anchors and consumed.get(anchor, 0) >= source.cut_count(anchor):
                        report('BLACKOUT_SOURCE', '恢复可见画面后再次切黑，须有该处独立的剧本切黑依据')
                    anchors.add(anchor)
                run += end - start
                if checked:
                    target = (key, index)
                cursor = end
            length = Decimal(str(shot['end'])) - Decimal(str(shot['start']))
            if cursor < length:
                finish()
    finish()
    return errors
