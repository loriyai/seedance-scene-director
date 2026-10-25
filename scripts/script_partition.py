"""Source-faithful work partitions, independent of screenplay and batch identities."""
import argparse
import bisect
import copy
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

PLAN_FILE = 'script-partition.json'
POLICY = 'partition-v1'
COUNT_POLICY = 'source-non-whitespace-including-punctuation-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def label_for(number):
    require(type(number) is int and number > 0, '分段序号须为正整数')
    result = ''
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(65 + digit) + result
    return result


def source_snapshot(state):
    files = state.get('files', {})
    require(all(key in files for key in ('source', 'script', 'review')), '分段需要已绑定的原文、标准剧本和通过复核的报告')
    result = {}
    for key in ('source', 'script', 'review', *(['action_markers'] if 'action_markers' in files else [])):
        path = Path(files[key]['path'])
        actual = file_digest(path)
        require(actual == files[key]['sha256'], key + '已变化，不能沿用旧分段或旧绑定')
        result[key] = actual
    result['records'] = digest(state['records'])
    result['scenes'] = digest([{key: scene.get(key) for key in ('id', 'number', 'source_range')}
                               for scene in state['ledger']['scenes']])
    return result


def source_data(state):
    source = read_json(state['files']['source']['path'])
    rows = source['paragraphs']
    require(all(row.get('id') == i and isinstance(row.get('text'), str)
                for i, row in enumerate(rows, 1)), '原文段号须从1连续编号')
    paragraphs = [row['text'] for row in rows]
    starts, total = [], 0
    for raw in paragraphs:
        starts.append(total)
        total += len(raw) + 1
    text = '\n'.join(paragraphs)
    return paragraphs, starts, text


def source_offset(position, paragraphs, starts):
    require(isinstance(position, list) and len(position) == 2
            and all(type(n) is int for n in position), '原文边界须为[1基段号,0基段内字符位置]')
    paragraph, offset = position
    if paragraph == len(paragraphs) + 1 and offset == 0:
        return len('\n'.join(paragraphs))
    require(1 <= paragraph <= len(paragraphs) and 0 <= offset <= len(paragraphs[paragraph - 1]), '原文边界超出实际文字')
    return starts[paragraph - 1] + offset


def position_for(offset, paragraphs, starts):
    if offset == len('\n'.join(paragraphs)):
        return [len(paragraphs) + 1, 0]
    index = bisect.bisect_right(starts, offset) - 1
    return [index + 1, offset - starts[index]]


def compact(value):
    return ''.join(char for char in value if char.isalnum())


def exact_source_map(state):
    """Reconstruct character mappings from checked coverage, never fuzzy anchors."""
    module = Path(__file__).resolve().parents[1] / 'modules' / 'screenplay-standardizer' / 'scripts'
    if str(module) not in sys.path:
        sys.path.insert(0, str(module))
    import source_compat
    paragraphs, starts, text = source_data(state)
    report = read_json(state['files']['review']['path'])
    require(report.get('status') == 'passed' and report.get('scope', 'full') == 'full', '分段须使用全稿通过的复核报告')
    require(all(report.get('counts', {}).get(key) == 0 for key in ('missing', 'extra', 'speaker'))
            and report.get('counts', {}).get('scene_boundary', 0) == 0, '复核报告仍有未解决的覆盖、声源或场界问题')
    records = state['records']
    mapped = [None] * state['length']
    scenes = {item['number']: item for item in state['ledger']['scenes']}
    source_dir = Path(state['files']['source']['path']).parent
    markers = source_compat.load_action_markers(source_dir)

    def assign(source_positions, target_positions):
        require(len(source_positions) == len(target_positions), '精确来源映射长度不一致')
        for raw_offset, target in zip(source_positions, target_positions):
            require(0 <= target < len(mapped) and mapped[target] is None, '精确来源映射重叠或越界')
            mapped[target] = raw_offset

    for review in report['scenes']:
        match = re.search(r'scene_(\d+)_', review.get('file', '')) or re.search(r'场景(\d+)', review.get('scene', ''))
        require(match is not None and int(match.group(1)) in scenes, '复核场次无法与绑定场次对应')
        scene = scenes[int(match.group(1))]
        local = [r for r in records if r['scene'] == scene['id']]
        require(local, '场次缺少可分镜的正文')
        by_line = {r['local_line']: r for r in local}
        flat = ''.join(compact(r['text']) for r in local)
        base, cursor = local[0]['start'], 0
        names = list({r.get('speaker') for r in local if r.get('speaker')})
        draft = (source_dir / 'scenes' / review['file']).read_text(encoding='utf-8-sig').splitlines()
        leading = next(i for i, value in enumerate(draft) if value.strip())
        for item in review['paragraph_map']:
            p = item['paragraph']
            raw = paragraphs[p - 1]
            if item.get('status') == '已映射':
                for fragment in item.get('fragments', []):
                    target = fragment['target']
                    if target['kind'] not in ('action', 'dialogue'):
                        continue
                    r = by_line.get(target['line'] - leading)
                    require(r is not None, '显式来源片段缺少标准正文记录')
                    a, b = fragment['source_range']
                    c, d = target['range']
                    target_raw = draft[target['line'] - 1]
                    require(raw[a:b] == fragment['source_text'] and target_raw[c:d] == target['text'], '显式来源片段文字已变化')
                    body_start = len(target_raw.rstrip()) - len(r['text'])
                    require(body_start <= c < d <= len(target_raw.rstrip()), '显式来源片段不在标准正文内部')
                    start = r['start'] + len(compact(target_raw[body_start:c]))
                    src = [starts[p - 1] + i for i in range(a, b) if raw[i].isalnum()]
                    require(compact(raw[a:b]) == compact(target_raw[c:d]), '显式片段不是逐字对应')
                    assign(src, list(range(start, start + len(src))))
                    cursor = start + len(src) - base
                continue
            if item.get('status') != '已覆盖':
                require(item.get('status') in ('格式提示', '人工确认纯提示', '纯标点', '转场标题'), '复核来源条目尚未完整覆盖')
                continue
            candidate = raw.strip()
            action = source_compat.action_content(candidate, markers)
            if action is not None:
                candidate = action
            # The review adapter removes transition syntax before parsing voice
            # and speaker prefixes. Keep that order for source-exact mapping.
            # A transition retained in an action record is also legitimate;
            # select that surface only when its words match the actual record.
            surfaces = [re.sub(r'^(?:遮挡转场|转场)\s*', '', candidate), candidate]
            matching = []
            for surface in dict.fromkeys(surfaces):
                surface, _ = source_compat.strip_leading_voice(surface)
                labeled = source_compat.explicit_label(surface, names)
                body = labeled['text'] if labeled else surface
                value = compact(body)
                if value and flat[cursor:cursor + len(value)] == value:
                    matching.append(body)
            require(bool(matching), '原文与标准正文无法建立精确映射；请核对现有逐场覆盖，不使用近似定位')
            candidate = matching[0]
            value = compact(candidate)
            # Existing adapters remove only a prefix; preserve all actual coordinates.
            start_in_raw = len(raw.rstrip()) - len(candidate)
            require(raw[start_in_raw:len(raw.rstrip())] == candidate, '原文解析不是可追溯的前缀转换')
            src = [starts[p - 1] + i for i in range(start_in_raw, len(raw.rstrip())) if raw[i].isalnum()]
            assign(src, list(range(base + cursor, base + cursor + len(value))))
            cursor += len(value)
        require(cursor == len(flat), '精确来源映射未覆盖整场正文')
    require(all(value is not None for value in mapped), '精确来源映射存在遗漏')
    require(all(a < b for a, b in zip(mapped, mapped[1:])), '原文到标准正文的精确映射存在重复或倒序')
    return mapped


def validate_boundary(row, state, paragraphs, starts, mapped):
    require(isinstance(row, dict) and row.get('safe') is True
            and isinstance(row.get('reason'), str) and row['reason'].strip(), '事件切点必须有实读确认和具体连续性理由')
    offset = row.get('offset')
    require(type(offset) is int and 0 < offset < state['length'], '事件切点须在标准正文内部')
    raw_offset = source_offset(row.get('source_position'), paragraphs, starts)
    require(mapped[offset - 1] < raw_offset <= mapped[offset], '原文切点与标准正文切点不是精确对应')
    for record in state['records']:
        require(not (record['kind'] == 'dialogue' and record['start'] < offset < record['end']), '分工切点不得切断一句台词')
    return {'offset': offset, 'source_offset': raw_offset, 'source_position': row['source_position'],
            'reason': row['reason'], 'kind': 'reviewed_event'}


def boundaries_for(state, mode, review=None):
    paragraphs, starts, text = source_data(state)
    mapped = exact_source_map(state)
    boundaries = [{'offset': 0, 'source_offset': 0, 'source_position': [1, 0], 'kind': 'start'}]
    if mode == 'scene':
        ordered = sorted(state['ledger']['scenes'], key=lambda item: item['number'])
        for scene in ordered[1:]:
            position = [scene['source_range'][0], 0]
            raw_offset = source_offset(position, paragraphs, starts)
            offset = min(r['start'] for r in state['records'] if r['scene'] == scene['id'])
            require(mapped[offset - 1] < raw_offset <= mapped[offset], '已确认场界不能精确映射原文')
            boundaries.append({'offset': offset, 'source_offset': raw_offset, 'source_position': position,
                               'kind': 'scene', 'scene_id': scene['id']})
    else:
        require(mode == 'balanced', '拆分模式须为scene或balanced')
        require(isinstance(review, dict) and review.get('policy') == 'safe-partition-boundaries-v1'
                and review.get('sources') == source_snapshot(state), '安全切点须绑定当前原文、标准稿和复核版本')
        rows = review.get('boundaries')
        require(isinstance(rows, list), '安全切点清单缺少boundaries')
        boundaries.extend(validate_boundary(row, state, paragraphs, starts, mapped) for row in rows)
    boundaries.append({'offset': state['length'], 'source_offset': len(text),
                       'source_position': [len(paragraphs) + 1, 0], 'kind': 'end'})
    boundaries.sort(key=lambda row: row['offset'])
    require(all(a['offset'] < b['offset'] and a['source_offset'] < b['source_offset']
                for a, b in zip(boundaries, boundaries[1:])), '切点不得重复、倒序或产生空分段')
    prefix = [0]
    for char in text:
        prefix.append(prefix[-1] + (not char.isspace()))
    for row in boundaries:
        row['count'] = prefix[row['source_offset']]
    require(all(a['count'] < b['count'] for a, b in zip(boundaries, boundaries[1:])), '合法切点之间不得只有空白')
    return boundaries


def balanced_indices(counts, participants):
    """Exact global minimum of squared deviation, with stable earliest ties."""
    require(type(participants) is int and participants > 0, '动画参与人数须为正整数')
    size = len(counts) - 1
    require(participants <= size, '人数超过合法可切分段数；请减少人数或调整拆分方式，不能生成空段或强拆')
    previous = [None] * (size + 1)
    previous[0] = 0
    trace = []
    for groups in range(1, participants + 1):
        current, parent, hull = [None] * (size + 1), [None] * (size + 1), deque()
        for end in range(groups, size + 1):
            before = end - 1
            if previous[before] is not None:
                line = (-2 * counts[before], previous[before] + counts[before] ** 2, before)
                while len(hull) >= 2:
                    a, b = hull[-2], hull[-1]
                    if (b[1] - a[1]) * (b[0] - line[0]) < (line[1] - b[1]) * (a[0] - b[0]):
                        break
                    hull.pop()
                hull.append(line)
            x = counts[end]
            while len(hull) >= 2 and hull[1][0] * x + hull[1][1] < hull[0][0] * x + hull[0][1]:
                hull.popleft()
            if hull:
                best = hull[0]
                current[end], parent[end] = x * x + best[0] * x + best[1], best[2]
        trace.append(parent)
        previous = current
    result, end = [size], size
    for groups in range(participants - 1, -1, -1):
        end = trace[groups][end]
        result.append(end)
    return list(reversed(result))


def excerpts(text):
    pieces = [piece.strip() for piece in re.findall(r'[^。！？!?\n]+[。！？!?]*|[^\n]+$', text) if piece.strip()]
    require(pieces, '分段没有可展示的原文')
    return '\n'.join(pieces[:2]), '\n'.join(pieces[-2:])


def build_plan(state, participants, mode, review=None):
    require(type(participants) is int and participants > 0, '动画参与人数须为正整数')
    if mode == 'balanced' and participants == 1 and review is None:
        review = boundary_template(state)
    sources = source_snapshot(state)
    points = boundaries_for(state, mode, review)
    indices = balanced_indices([p['count'] for p in points], participants)
    paragraphs, starts, text = source_data(state)
    segments = []
    for index, (a, b) in enumerate(zip(indices, indices[1:]), 1):
        left, right = points[a], points[b]
        part = text[left['source_offset']:right['source_offset']]
        first, last = excerpts(part)
        segments.append({'label': label_for(index), 'span': [left['offset'], right['offset']],
                         'source_range': [left['source_position'], right['source_position']],
                         'source_offsets': [left['source_offset'], right['source_offset']],
                         'word_count': right['count'] - left['count'],
                         'start_excerpt': first, 'end_excerpt': last})
    difference = max(s['word_count'] for s in segments) - min(s['word_count'] for s in segments)
    plan = {'policy': POLICY, 'mode': mode, 'participants': participants, 'sources': sources,
            'count_policy': COUNT_POLICY, 'balance_objective': 'minimum-total-squared-deviation',
            'total_count': points[-1]['count'], 'difference': difference,
            'status': 'needs_confirmation' if mode == 'scene' and difference > 2000 else 'confirmed',
            'boundaries': points, 'segments': segments}
    plan['proposal_hash'] = digest(plan)
    return plan


def _seal(plan):
    plan['integrity'] = digest({key: value for key, value in plan.items() if key != 'integrity'})
    return plan


def load_plan(project, state):
    path = Path(project) / PLAN_FILE
    if not path.is_file():
        return None
    plan = read_json(path)
    require(plan.get('policy') == POLICY and plan.get('status') in ('confirmed', 'needs_confirmation')
            and plan.get('count_policy') == COUNT_POLICY
            and plan.get('integrity') == digest({key: value for key, value in plan.items() if key != 'integrity'}),
            '分段计划损坏或格式不兼容')
    require(plan.get('sources') == source_snapshot(state), '分段计划已与原文或标准稿失配，不能沿用旧范围')
    require(isinstance(plan.get('plan_id'), str) and type(plan.get('version')) is int and plan['version'] > 0, '分段计划缺少稳定身份')
    segments = plan.get('segments', [])
    require(len(segments) == plan.get('participants') and segments, '分段计划缺少完整分段')
    source_cursor = standard_cursor = count = 0
    paragraphs, starts, text = source_data(state)
    for index, segment in enumerate(segments, 1):
        a, b = segment['span']
        x, y = segment['source_offsets']
        require(segment['label'] == label_for(index) and a == standard_cursor and x == source_cursor
                and type(a) is int and type(b) is int and a < b <= state['length']
                and type(x) is int and type(y) is int and x < y <= len(text), '分段范围不连续、重叠或越界')
        require(source_offset(segment['source_range'][0], paragraphs, starts) == x
                and source_offset(segment['source_range'][1], paragraphs, starts) == y
                and segment['word_count'] == sum(not c.isspace() for c in text[x:y]), '分段原文坐标或字数不一致')
        require((segment['start_excerpt'], segment['end_excerpt']) == excerpts(text[x:y]), '分段摘录与原文不一致')
        standard_cursor, source_cursor, count = b, y, count + segment['word_count']
    require(standard_cursor == state['length'] and source_cursor == len(text) and count == plan['total_count'], '分段未完整覆盖来源')
    return plan


def save_plan(project, state, proposal):
    old = load_plan(project, state)
    if old and old.get('proposal_hash') == proposal.get('proposal_hash'):
        return old
    require(not state.get('units'), '已有分镜后不能原位重拆并重置分段身份；请保留当前计划或另建项目')
    plan = copy.deepcopy(proposal)
    plan.update(plan_id=str(uuid.uuid4()), version=old['version'] + 1 if old else 1)
    atomic_json(Path(project) / PLAN_FILE, _seal(plan))
    return plan


def confirm_plan(project, state, proposal_hash):
    plan = load_plan(project, state)
    require(plan is not None and plan['proposal_hash'] == proposal_hash, '确认须绑定当前场景拆分候选，不能确认过期方案')
    if plan['status'] == 'confirmed':
        return plan
    require(plan['mode'] == 'scene' and plan['difference'] > 2000, '当前分段不需要字数差距确认')
    plan.update(status='confirmed', difference_accepted=True)
    atomic_json(Path(project) / PLAN_FILE, _seal(plan))
    return plan


def resolve_segment(project, state, label_or_span):
    plan = load_plan(project, state)
    if plan is None:
        return None
    require(plan['status'] == 'confirmed', '场景拆分字数差距尚未获得忽略确认或改用字数模式')
    for segment in plan['segments']:
        if isinstance(label_or_span, str):
            matched = label_or_span.strip().upper().removesuffix('分段') == segment['label']
        else:
            matched = (isinstance(label_or_span, (list, tuple)) and len(label_or_span) == 2
                       and all(type(n) is int for n in label_or_span)
                       and segment['span'][0] <= label_or_span[0] < label_or_span[1] <= segment['span'][1])
        if matched:
            return {**copy.deepcopy(segment), 'plan_id': plan['plan_id'], 'plan_version': plan['version']}
    return None


def render_plan(plan):
    lines = []
    for segment in plan['segments']:
        lines.extend([f"{segment['label']}分段：共{segment['word_count']}个字",
                      '开始：' + segment['start_excerpt'], '结束：' + segment['end_excerpt'], ''])
    if plan['status'] == 'needs_confirmation':
        lines.extend([f"按照场景均分差距最大为{plan['difference']}字。",
                      '1. 忽略差距，直接拆分', '2. 按照选项2，根据剧本字数来拆分'])
    return '\n'.join(lines).rstrip() + '\n'


def boundary_template(state):
    return {'policy': 'safe-partition-boundaries-v1', 'sources': source_snapshot(state), 'boundaries': []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('plan', 'confirm', 'show', 'resolve', 'boundary-template'):
        command_parser = sub.add_parser(command)
        command_parser.add_argument('--project', type=Path, required=True)
        if command == 'plan':
            command_parser.add_argument('--participants', type=int, required=True)
            command_parser.add_argument('--mode', choices=('scene', 'balanced'), required=True)
            command_parser.add_argument('--boundaries', type=Path)
        elif command == 'confirm':
            command_parser.add_argument('--proposal-hash', required=True)
        elif command == 'resolve':
            command_parser.add_argument('--label', required=True)
        elif command == 'boundary-template':
            command_parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        import director
        state = director.load(args.project)
        if args.command == 'plan':
            proposal = build_plan(state, args.participants, args.mode, read_json(args.boundaries) if args.boundaries else None)
            plan = save_plan(args.project, state, proposal)
            result = {'status': plan['status'], 'plan_id': plan['plan_id'], 'proposal_hash': plan['proposal_hash'],
                      'plan_file': str((args.project / PLAN_FILE).resolve()), 'text': render_plan(plan)}
        elif args.command == 'confirm':
            plan = confirm_plan(args.project, state, args.proposal_hash)
            result = {'status': plan['status'], 'plan_id': plan['plan_id'], 'text': render_plan(plan)}
        elif args.command == 'show':
            plan = load_plan(args.project, state)
            result = {'status': plan['status'], 'text': render_plan(plan)} if plan else {'status': 'not_partitioned'}
        elif args.command == 'resolve':
            segment = resolve_segment(args.project, state, args.label)
            require(segment is not None, '没有与该代号匹配的有效分段')
            result = segment
        else:
            result = boundary_template(state)
            output = director.validate_output_path(state, args.project, args.output)
            require(not output.exists(), '安全切点输出已存在；请使用新路径，不能覆盖旧输入或决定')
            atomic_json(output, result)
        if args.command in ('plan', 'confirm') and plan['status'] == 'confirmed' and state.get('phase') != 'range':
            state['phase'] = 'range'
            director.save(args.project, state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('错误：' + str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
