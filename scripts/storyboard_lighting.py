"""Source-authored unit lighting; no inferred light, geometry or shot prose."""
import re

POLICY = 'unit-lighting-v1'
SESSION_FIELD = 'storyboard_lighting_policy'
TEXT_FIELD = 'lighting'
POLICY_FIELD = 'lighting_policy'
PREFIX = '光照：'

# Only prose that refers back instead of stating the actual lighting is
# rejected. Quoted dialogue is never a member of the inspected body fields.
BACK_REFERENCE = re.compile(
    r'同前|同上|如前|如上|照旧|照前|照上|'
    r'(?:沿用|承接|继承|照搬|参考|按|保持|维持|延续|同|与)(?:前|上)(?:一)?(?:镜头|镜|单元|场次|场)|'
    r'(?:前|上)(?:一)?(?:镜头|镜|单元|场次|场)(?:相同|一致|不变)')
LIGHT_WORD = re.compile(r'光照|照明|灯光|光影|光源|色温|亮度|主光|辅光|补光|反射光|环境反射|光向|明暗|暗部|亮部|受光|背光|光线')


def visual_prose(unit):
    """All authored visual fields, including unit lighting, but no speech."""
    return [unit.get('global', ''), unit.get('time', ''), unit.get('weather', ''),
            unit.get(TEXT_FIELD, '')] + [shot.get('text', '') for shot in unit.get('shots', [])]


def unit_enabled(unit):
    return unit.get(POLICY_FIELD) == POLICY


def errors(units, state=None, *, current=False, historical=False):
    """Check the explicit contract, never approve lighting semantics."""
    state = state or {}
    result = []
    session_policy = state.get(SESSION_FIELD)
    if not historical and session_policy not in (None, POLICY):
        result.append({'code': 'LIGHTING_POLICY', 'message': '未知的分镜光照政策'})
    required = not historical and (current or session_policy == POLICY)
    for unit in units:
        key = unit.get('draft_key', '当前单元')
        policy, baseline = unit.get(POLICY_FIELD), unit.get(TEXT_FIELD)
        if policy not in (None, POLICY):
            result.append({'code': 'LIGHTING_POLICY', 'unit': key, 'message': '单元光照政策不兼容'})
        if required and (policy != POLICY or not isinstance(baseline, str) or not baseline.strip()):
            result.append({'code': 'AUTHORING_LIGHTING_UNDER_SPECIFIED', 'unit': key, 'field': TEXT_FIELD,
                           'message': f'{key}：当前文字候选须在人物站位后、镜头前写完整非空的光照行'})
        if policy != POLICY:
            continue
        if not isinstance(baseline, str) or not baseline.strip():
            result.append({'code': 'UNIT_LIGHTING_EMPTY', 'unit': key, 'message': '单元光照基线不可为空'})
            continue
        # Historical units are verified against frozen source/projection and
        # rendered verbatim. New word policies cannot rewrite saved receipts.
        if historical:
            continue
        if BACK_REFERENCE.search(baseline):
            result.append({'code': 'LIGHTING_BACK_REFERENCE', 'unit': key, 'field': TEXT_FIELD,
                           'message': '光照行须写本单元实际光照，不能用同前、同上或沿用前镜等指代替代'})
        fields = [(name, None, unit.get(name, '')) for name in ('global', 'time', 'weather')]
        fields.extend(('body', index, shot.get('text', ''))
                      for index, shot in enumerate(unit.get('shots', []), 1))
        fields.extend(('voice_direction', index, part.get('direction', ''))
                      for index, shot in enumerate(unit.get('shots', []), 1)
                      for part in shot.get('dialogue_parts', []))
        for field, index, prose in fields:
            for clause in re.split(r'[，；。！？\n]', prose):
                if LIGHT_WORD.search(clause) and BACK_REFERENCE.search(clause):
                    result.append({'code': 'LIGHTING_BACK_REFERENCE', 'unit': key,
                                   **({'shot': index} if index is not None else {}), 'field': field,
                                   'message': '镜头光照须写实际变化或必要明暗落点，不能写光照同前、沿用上镜等指代'})
                    break
    return result
