"""Source-backed delivery reports, separate from production wall-clock metrics."""
import hashlib
from pathlib import Path


POLICY = 'segment-delivery-report-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_text_report(project, state, output, text):
    """Create an immutable UTF-8 report; publication uses the normal copy contract."""
    import director
    from project_paths import assert_writable
    assert_writable(project)
    target = director.validate_output_path(state, project, output)
    require(target.suffix.lower() == '.txt', '正式报告输出须为TXT文件')
    require(not target.exists(), '报告文件已存在；请另存新的正式版本')
    payload = text.encode('utf-8')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also closes the check/write race; never replace inputs.
    with target.open('xb') as stream:
        try:
            stream.write(payload)
        except BaseException:
            stream.close()
            target.unlink()
            raise
    require(target.read_bytes() == payload, '正式报告写入后核验失败')
    return {'status': 'exported', 'output': str(target),
            'output_sha256': hashlib.sha256(payload).hexdigest()}


def _validate_receipt(project, receipt):
    from project_paths import resolve
    from storyboard_delivery_timing import verify_receipt
    verify_receipt(receipt)
    require(receipt.get('policy') == 'scene-text-v1', '分段详情当前只统计正式文字分镜回执，不能自动迁移旧格式')
    report = receipt.get('final_review', {})
    scenes = list(dict.fromkeys(sid for unit in receipt['units'] for sid in unit['scene_ids']))
    rows = report.get('scenes', [])
    require(isinstance(report.get('reviewer'), str) and report['reviewer'].strip()
            and isinstance(rows, list) and [row.get('scene_id') for row in rows] == scenes
            and all(row.get('status') == 'passed' and row.get('issues') == []
                    and isinstance(row.get('basis'), str) and row['basis'].strip() for row in rows),
            '正式回执缺少完整的既有审阅闭环')
    gate = receipt.get('authoring_gate', {})
    if gate.get('enforced'):
        require(report.get('semantic_review') == 'performed'
                and report.get('authoring_gate_digest') == gate.get('gate_digest')
                and report.get('authoring_trace_sha256') == gate.get('trace_sha256')
                and report.get('reviewed_shots') == gate.get('shot_keys')
                and report.get('shot_count') == gate.get('shot_count'), '正式回执的审阅与冻结作者依据不匹配')
    require(isinstance(receipt.get('output'), str) and receipt.get('output_sha256'), '正式回执缺少输出及摘要')
    path = resolve(project, receipt['output'])
    require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == receipt['output_sha256'],
            '分镜正式原件缺失或变化，请恢复准确版本后再统计')


def _coverage(state, segment, units):
    """Match native source coverage, including reviewed shared action boundaries."""
    records = {row['id']: row for row in state['records']}
    start, end = segment['span']
    require(units[0]['span'][0] == start and units[-1]['span'][1] == end,
            f"{segment['label']}分段当前轮次未完整覆盖范围")
    previous = None
    for index, unit in enumerate(units, 1):
        a, b = unit['span']
        require(type(a) is int and type(b) is int and start <= a < b <= end,
                f"{segment['label']}分段单元越界或范围无效")
        require(unit.get('segment_index') == index, f"{segment['label']}分段单元序号重复或缺失")
        if previous is not None and previous['span'][1] != a:
            before, after = previous.get('source_records', []), unit.get('source_records', [])
            boundary = records.get(after[0], {}) if after else {}
            shared = (before and after and before[-1] == after[0] and boundary.get('kind') == 'action'
                      and previous['span'][1] == boundary.get('end') and a == boundary.get('start'))
            require(shared, f"{segment['label']}分段来源缺口或无合法依据的重复覆盖")
        previous = unit


def _duration(unit):
    shots = unit.get('shots')
    require(isinstance(shots, list) and shots, '正式单元缺少镜头')
    cursor = 0
    for shot in shots:
        start, end = shot.get('start'), shot.get('end')
        require(type(start) is int and type(end) is int and start == cursor and end > start,
                '正式镜头时码不连续或无效')
        cursor = end
    require(type(unit.get('duration')) is int and cursor == unit['duration'], '正式镜头与单元冻结时长不一致')
    return cursor


def segment_report(project, state):
    """Summarize one complete latest round per segment, with no historical mixing."""
    from script_partition import load_plan
    from segment_naming import POLICY as NAMING_POLICY
    plan = load_plan(project, state)
    require(plan is not None and plan['status'] == 'confirmed', '分段详情需要已核定的正式分段计划')
    stored = state.get('units', [])
    ids = [unit.get('id') for unit in stored]
    require(all(isinstance(key, str) and key for key in ids) and len(set(ids)) == len(ids), '正式单元ID为空或重复')
    receipts = state.get('receipts', [])
    frozen = {}
    for index, receipt in enumerate(receipts):
        for unit in receipt.get('units', []):
            frozen[(unit.get('id'), unit.get('version'))] = (unit, index)
    verified = set()
    rows = []
    for segment in plan['segments']:
        label = segment['label']
        candidates = [unit for unit in stored if unit.get('segment_label') == label]
        require(candidates, f'{label}分段尚未正式提交，不能输出全部分段完成详情')
        require(all(unit.get('naming_policy') == NAMING_POLICY
                    and unit.get('segment_plan_id') == plan['plan_id']
                    and unit.get('segment_plan_version') == plan['version'] for unit in candidates),
                f'{label}分段与当前计划身份或版本不一致')
        require(all(type(unit.get('segment_round')) is int and unit['segment_round'] >= 0
                    and type(unit.get('segment_index')) is int for unit in candidates), '正式分段缺少轮次或序号')
        round_number = max(unit['segment_round'] for unit in candidates)
        units = sorted((unit for unit in candidates if unit['segment_round'] == round_number),
                       key=lambda unit: unit['segment_index'])
        _coverage(state, segment, units)
        for unit in units:
            saved = frozen.get((unit['id'], unit.get('version')))
            require(saved is not None and saved[0] == unit, f"{unit['id']}未与正式冻结回执一致")
            if saved[1] not in verified:
                _validate_receipt(project, receipts[saved[1]])
                verified.add(saved[1])
        total = sum(_duration(unit) for unit in units)
        shot_count = sum(len(unit['shots']) for unit in units)
        rows.append({'label': label, 'span': segment['span'], 'round': round_number,
                     'word_count': segment['word_count'], 'unit_count': len(units),
                     'shot_count': shot_count, 'total_seconds': total,
                     'average_shot_seconds': total / shot_count,
                     'units': [{'id': unit['id'], 'version': unit['version']} for unit in units]})
    total = sum(row['total_seconds'] for row in rows)
    shots = sum(row['shot_count'] for row in rows)
    return {'policy': POLICY, 'status': 'complete', 'plan_id': plan['plan_id'], 'plan_version': plan['version'],
            'count_policy': plan['count_policy'], 'segments': rows,
            'totals': {'word_count': sum(row['word_count'] for row in rows),
                       'unit_count': sum(row['unit_count'] for row in rows),
                       'shot_count': shots, 'total_seconds': total, 'average_shot_seconds': total / shots},
            'receipt_ids': [receipts[index]['request_id'] for index in sorted(verified)]}


def render_segment_report(report):
    lines = ['分段详情', f"计划：{report['plan_id']}；版本：{report['plan_version']}",
             '范围：全部正式分段；每段取当前最新制作轮次并核验完整覆盖，局部修订取当前有效版本。',
             '字数：原剧本非空白字符，含标点、原有标题及人物标签；不统计分镜提示词或工具新增文字。',
             '时长：正式回执冻结的镜头时码；平均单镜时长=总时长÷镜头数，保留两位小数。',
             '仅统计本地正式交付内容；不表示图片、视频生成或画布同步已完成。', '',
             '分段｜制作轮次｜原剧本文字字数｜分镜单元数｜镜头数｜总时长｜平均单镜时长']
    for row in [*report['segments'], {'label': '合计', 'round': None, **report['totals']}]:
        minutes, seconds = divmod(row['total_seconds'], 60)
        round_text = str(row['round'] + 1) if row['round'] is not None else '—'
        lines.append(f"{row['label']}｜{round_text}｜{row['word_count']}｜{row['unit_count']}｜{row['shot_count']}｜"
                     f"{minutes}分{seconds}秒｜{row['average_shot_seconds']:.2f}秒")
    return '\n'.join(lines) + '\n'


def export_segment_report(project, state, output):
    report = segment_report(project, state)
    artifact = write_text_report(project, state, output, render_segment_report(report))
    return {**artifact, 'report': report}
