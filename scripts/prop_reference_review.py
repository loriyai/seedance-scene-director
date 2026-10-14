"""Source-backed storyboard checks for reviewed visual-continuity candidates.

This module does not extract nouns or decide which objects are important.
It only checks the author's existing candidate decisions against a new unit.
"""
def compact(text):
    return ''.join(c for c in str(text) if c.isalnum())


def check(state, unit):
    ledger = state['ledger']
    if ledger.get('asset_policy') != 'visual-continuity-v2':
        return []
    selected_scenes = set(unit['scene_ids'])
    candidates = [item for item in ledger.get('prop_coverage_review', {}).get('candidates', [])
                  if item.get('requires_reference') and selected_scenes.intersection(item['scene_ids'])]
    props = {item['id']: item for item in ledger['props']}
    temporary = {key: item for key, item in unit.get('continuity', {}).get('entities', {}).items()
                 if not item.get('asset_ids')}
    warnings = []
    for candidate in candidates:
        target = candidate.get('target_id')
        if candidate.get('disposition') != 'asset' or target not in props:
            continue  # Character/SET integration is checked by its own mapping.
        item = props[target]
        names = {compact(value) for value in (candidate['name'], item['name'], item.get('source_name'),
                  *candidate.get('aliases', []), *item.get('aliases', [])) if value}
        names.discard('')
        for key, entity in temporary.items():
            label = compact(entity.get('label', ''))
            # Exact identity names only. A generic 'car' is not enough to infer
            # the luxury car, and a car owner's name is not the car itself.
            identity_match = label in names
            if identity_match:
                raise ValueError(f"需参考资产的核心物件 {candidate['name']} 被声明为临时实体 {key}；"
                                 f"请使用已登记资产 {target}，不得用空asset_ids绕过图片引用")
        if target in unit.get('prop_ids', []):
            continue
        # Review only a complete, source-backed visible-action quote in this
        # unit. Do not match a name inside a person's name or dialogue mention.
        a, b = unit['span']
        pieces = []
        for record in state['records']:
            if record['scene'] not in selected_scenes or record['start'] >= b or record['end'] <= a:
                continue
            raw = compact(record['text'])
            pieces.append(raw[max(0, a-record['start']):min(len(raw), b-record['start'])])
        selected_text = ''.join(pieces)
        missing = [e for e in candidate.get('source_evidence', []) if e['scene_id'] in selected_scenes
                   and compact(e['evidence']) and compact(e['evidence']) in selected_text]
        if missing:
            warnings.append({'kind': '核心道具引用需复核', 'candidate_id': candidate['candidate_id'],
                             'name': candidate['name'], 'target_id': target,
                             'review': '本单元覆盖已审可见使用证据，但未声明该道具；核对实际画面参与及图片引用，不能直接当普通临时物件。'})
    return warnings
