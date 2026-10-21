"""Shared fictional project fixture; contains no discoverable test cases."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile

import director as d

_, ledger, _ = d.dependency()


def setup_skill_fixture(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.work = Path(self.temp.name)
    self.script = self.work / 'script.txt'
    self.source = self.work / 'source.json'
    cast = '人物：陈默-基础形象；'
    self.paragraphs = [
        '场景1-日-内-陈默卧室', cast, '陈默把笔和笔记本电脑放在桌上。',
        '场景2-夜-内-陈默卧室', cast, '陈默坐下。',
        '场景3-日-内-陈默卧室', cast, '房间挂满婚礼装饰，陈默站着。',
    ]
    self.source.write_text(json.dumps({'paragraphs': [{'id': i, 'text': p}
        for i, p in enumerate(self.paragraphs, 1)]}, ensure_ascii=False), encoding='utf-8')
    self.script.write_text('\n'.join(p if i % 3 != 2 else '△ ' + p
        for i, p in enumerate(self.paragraphs)) + '\n', encoding='utf-8')
    scenes_dir = self.work / 'scenes'
    scenes_dir.mkdir()
    for i in range(3):
        (scenes_dir / f'scene_{i+1:03d}_{i*3+1:04d}-{i*3+3:04d}.txt').write_text(
            '\n'.join(self.script.read_text(encoding='utf-8').splitlines()[i*3:i*3+3]) + '\n', encoding='utf-8')
    path = self.work / 'draft.json'
    with contextlib.redirect_stdout(io.StringIO()):
        ledger.initialize(self.script, self.work, path)
    draft = d.read_json(path)
    # This fixture exercises legacy ledgers; new extraction policy has its own tests.
    draft.pop('asset_policy', None)
    self.draft = copy.deepcopy(draft)
    draft['locations'] = [draft['locations'][0]]
    draft['locations'][0].update(identity_status='confirmed', identity_basis='原文明示陈默所属同一卧室')
    draft['retired_ids'] = ['LOC0002', 'LOC0003']
    for scene in draft['scenes']:
        scene['location_id'] = 'LOC0001'
        scene['time_basis'] = {'kind': '原文明示', 'source_paragraphs': [scene['source_range'][0]]}
    draft['props'] = [{'id': 'PROP0001', 'name': '笔', 'aliases': [], 'visual_facts': []},
                      {'id': 'PROP0002', 'name': '笔记本电脑', 'aliases': [], 'visual_facts': []}]
    draft['scenes'][0]['prop_ids'] = ['PROP0001', 'PROP0002']
    self.data = ledger.build_scene_assets(draft)
    self.state = {'ledger': self.data, 'length': 8, 'entry': {'min': 4, 'durations': []},
        'files': {'script': {'path': str(self.script)}},
        'records': [{'id': 'L000001', 'scene': 'SC0001', 'start': 0, 'end': 8,
                     'kind': 'action', 'text': '陈默将笔放在桌上'}]}
    self.unit = {'id': '场景1-分镜1', 'version': 1, 'span': [0, 8], 'duration': 4,
        'output_format': 'reference-v3',
        'speech_render_policy': 'explicit-cues-v1',
        'scene_ids': ['SC0001'], 'main_scene': 'SC0001', 'character_ids': ['CHAR0001'],
        'prop_ids': ['PROP0001'], 'time': '白天', 'weather': '未明示', 'style': '3D国风动漫',
        'global': '@{CHAR0001} 站在桌前。', 'start_state': '站立', 'end_state': '放笔',
        'short_reason': 'range_end', 'audio': [],
        'shots': [{'start': 0, 'end': 4, 'framing': '中景', 'scene_id': 'SC0001',
                  'subjects': ['CHAR0001', 'PROP0001'], 'angle': '正面',
                  'focus': ['CHAR0001'], 'label': '放笔', 'transition': '硬切',
                  'purpose': '看清放笔动作与人物桌面的关系，以中景固定机位保留接触过程。',
                  'camera': '正面平视固定机位', 'composition': '人物居左，桌面在右侧',
                  'depth_of_field': '中等景深，人物与桌面清晰', 'lighting': '室内既有柔光，桌下保留暗部',
                  'text': '将@{PROP0001} 放在桌上。', 'dialogue_parts': []}]}
