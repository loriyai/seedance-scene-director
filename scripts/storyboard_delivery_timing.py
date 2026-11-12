"""One frozen, deterministic timing conversion after base-draft review.

Only structured timestamps change. Source dialogue, visual prose, cuts and
unit boundaries remain authored. Historical exports consume saved results.
"""
import copy
from decimal import Decimal, ROUND_HALF_UP
import re

import director as d


POLICY = 'reviewed-shot-minus-one-v1'
SHOT_TIME = re.compile(r'^·\d{2}秒-\d{2}秒')
VOICE_TIME = re.compile(r'^(声音\[(?:L|SV)\d{6,},)(\d+(?:\.\d{1,3})?)-(\d+(?:\.\d{1,3})?)(\])')


def _mapped_time(value, base_start, base_length, final_start, final_length):
    result = (Decimal(final_start) + (Decimal(str(value)) - Decimal(base_start))
              * Decimal(final_length) / Decimal(base_length))
    result = result.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
    return int(result) if result == result.to_integral_value() else float(result)


def _window(start, end, base_start, base_length, final_start, final_length):
    d.require(base_start <= start < end <= base_start + base_length,
              '最终时长转换的基础窗口越界')
    values = [_mapped_time(value, base_start, base_length, final_start, final_length)
              for value in (start, end)]
    d.require(final_start <= values[0] < values[1] <= final_start + final_length,
              '最终时长转换使时间窗口不足毫秒精度；请调整基础稿窗口后重新审阅')
    return values


def _retime_shot_line(text, final_start, base_length, final_length):
    line, count = SHOT_TIME.subn(
        f'·{final_start:02d}秒-{final_start + final_length:02d}秒', text, count=1)
    d.require(count == 1, '最终时长转换缺少结构化镜头时码')
    if base_length != final_length and '切黑[' in line:
        from storyboard_blackout import retime_transition
        prefix, separator, rest = line.partition('；转场：')
        transition, closing, suffix = rest.partition('）')
        if separator and '切黑[' in transition:
            transition = retime_transition(
                transition, lambda a, b: _window(a, b, 0, base_length, 0, final_length))
            line = prefix + separator + transition + closing + suffix
    return line


def convert(units, constraints=None):
    """Return final copies and an auditable mapping, never mutate a draft."""
    constraints = constraints or {}
    final_units = copy.deepcopy(units)
    mapping = []
    for base, final in zip(units, final_units):
        d.require(not base.get('delivery_timing_policy'), '已转换的最终单元不能重复减秒')
        d.require(type(base['duration']) is int and 4 <= base['duration'] <= 30,
                  '基础单元须为4～30秒整数')
        exempt = base['duration'] == 4
        cursor = 0
        base_cursor = 0
        shot_mappings = []
        for index, (shot, result) in enumerate(zip(base['shots'], final['shots']), 1):
            start, end = shot['start'], shot['end']
            length = end - start
            d.require(type(start) is int and type(end) is int and start == base_cursor
                      and 1 <= length <= 7, '基础单镜须为连续的1～7秒整数')
            final_length = length - 1 if length >= 4 and not exempt else length
            result.update(start=cursor, end=cursor + final_length)
            result['text'] = _retime_shot_line(shot['text'], cursor, length, final_length)
            for part in result['dialogue_parts']:
                part['start'], part['end'] = _window(
                    part['start'], part['end'], start, length, cursor, final_length)
            row = {'shot_key': f'{base["draft_key"]}#{index}',
                   'base_start': start, 'base_end': end,
                   'final_start': cursor, 'final_end': cursor + final_length}
            local = constraints.get(row['shot_key'], [])
            if local:
                row['base_constraints'] = copy.deepcopy(local)
                row['final_constraints'] = copy.deepcopy(local)
                for constraint in row['final_constraints']:
                    constraint['start'], constraint['end'] = _window(
                        constraint['start'], constraint['end'], 0, length, 0, final_length)
            shot_mappings.append(row)
            base_cursor = end
            cursor += final_length
        d.require(base_cursor == base['duration'] and 4 <= cursor <= 30,
                  '最终单元时长或基础时间轴不合法')
        # Rewrite only structured time fields. Dialogue and prose are untouched.
        lines = []
        shot_index = -1
        for line in base['source_text'].splitlines(keepends=True):
            if SHOT_TIME.match(line):
                shot_index += 1
                row = shot_mappings[shot_index]
                line = _retime_shot_line(line, row['final_start'],
                                         row['base_end'] - row['base_start'],
                                         row['final_end'] - row['final_start'])
            match = VOICE_TIME.match(line)
            if match:
                d.require(shot_index >= 0, '声音时码缺少所属镜头')
                row = shot_mappings[shot_index]
                start, end = _window(float(match[2]), float(match[3]),
                    row['base_start'], row['base_end'] - row['base_start'],
                    row['final_start'], row['final_end'] - row['final_start'])
                if (start, end) != (float(match[2]), float(match[3])):
                    line = f'{match[1]}{start}-{end}{match[4]}' + line[match.end():]
            lines.append(line)
        d.require(shot_index + 1 == len(shot_mappings), '基础文本与镜头数量不一致')
        final.update(source_text=''.join(lines), duration=cursor, delivery_timing_policy=POLICY)
        mapping.append({'draft_key': base['draft_key'], 'base_duration': base['duration'],
                        'final_duration': cursor, 'four_second_unit_exempt': exempt,
                        'shots': shot_mappings})
    summary = {'policy': POLICY, 'base_total_duration': sum(u['duration'] for u in units),
               'final_total_duration': sum(u['duration'] for u in final_units), 'units': mapping,
               'base_units_sha256': d.sha(d.packed(units)),
               'final_units_sha256': d.sha(d.packed(final_units))}
    return final_units, summary


def verify_receipt(receipt):
    """Verify the frozen conversion without applying it to delivered units."""
    timing = receipt.get('delivery_timing')
    if timing is None:
        d.require(not any(u.get('delivery_timing_policy') for u in receipt.get('units', [])),
                  '最终时长转换回执缺少政策记录')
        return receipt['units']
    d.require(isinstance(timing, dict) and timing.get('policy') == POLICY,
              '未知的最终时长转换政策')
    bases = receipt.get('source_units')
    d.require(isinstance(bases, list) and bases, '最终时长转换回执缺少已审基础单元')
    constraints = {shot['shot_key']: shot['base_constraints']
                   for unit in timing.get('units', []) for shot in unit.get('shots', [])
                   if shot.get('base_constraints')}
    expected, summary = convert(bases, constraints)
    d.require(expected == receipt['units'] and summary == timing,
              '最终时长转换与已审基础单元或冻结回执不一致')
    return bases
