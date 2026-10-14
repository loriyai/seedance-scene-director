"""Flag observable group-reference risks, without deciding narrative importance."""

POLICY = "visual-continuity-v2"


def compact(text):
    return ''.join(c for c in str(text) if c.isalnum())


def check(state, unit):
    ledger = state['ledger']
    if ledger.get('group_policy') != POLICY:
        return []
    selected_scenes = set(unit['scene_ids'])
    linked = {aid for scene in ledger['scenes'] if scene['id'] in selected_scenes
              for aid in scene['character_ids']}
    groups = [item for item in ledger['characters'] if item['id'] in linked
              and '-群像-' in item.get('source_entry', item['name'])]
    # A production name, source entry or explicit alias can identify a group.
    # Shared labels and shared action quotes cannot establish identity.
    identities = {}
    for item in groups:
        for value in (item['name'], item.get('source_entry'), *item.get('aliases', [])):
            if value and compact(value):
                identities.setdefault(compact(value), set()).add(item['id'])
    for key, entity in unit.get('continuity', {}).get('entities', {}).items():
        matched = identities.get(compact(entity.get('label', '')), set())
        if entity.get('asset_ids') or len(matched) != 1:
            continue
        item = next(group for group in groups if group['id'] in matched)
        if item.get('visual_mode') == 'image_asset':
            raise ValueError(f"图片群像 {item['name']} 被声明为临时实体 {key}；"
                             f"请使用已登记资产 {item['id']}，不能用空asset_ids绕过图片引用")
    a, b = unit['span']
    action_spans, previous_scene = {}, None
    for record in state['records']:
        sid = record['scene']
        if (sid not in selected_scenes or record['start'] >= b or record['end'] <= a
                or record.get('kind') != 'action'):
            previous_scene = None
            continue
        text = compact(record['text'])[max(0, a-record['start']):min(record['end'], b)-record['start']]
        spans = action_spans.setdefault(sid, [])
        if sid != previous_scene:
            spans.append(text)
        else:
            spans[-1] += text
        previous_scene = sid
    warnings = []
    for item in groups:
        aid = item['id']
        participated = [i + 1 for i, shot in enumerate(unit['shots']) if aid in shot['subjects']]
        focused = [i + 1 for i, shot in enumerate(unit['shots']) if aid in shot.get('focus', [])]
        if item.get('visual_mode') == 'prompt_only' and (len(participated) >= 2 or focused):
            warnings.append({'kind': '群像图片需求需复核', 'character_id': aid,
                             'shots': participated, 'focus_shots': focused,
                             'review': '仅文字群像参与多镜头或成为主焦点；结合实际画面复核文字是否仍足够、是否需固定群体外观或单独成员参考。次数和景别不自动决定建图，队形人数与动作仍由连续性记录控制。'})
        if aid not in unit.get('character_ids', []) and any(
                use['scene_id'] in selected_scenes and compact(use['evidence'])
                and any(compact(use['evidence']) in text for text in action_spans.get(use['scene_id'], []))
                for use in item.get('visible_uses', [])):
            warnings.append({'kind': '群像画面覆盖需复核', 'character_id': aid,
                             'review': '本单元覆盖已审群像可见出场证据，但未登记该人物；核对画内、画外安排及原动作是否保留，不因仅文字模式而遗漏群像。'})
    return warnings
