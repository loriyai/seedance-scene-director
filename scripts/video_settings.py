"""Project video ratio and a chat-local default; asset dimensions are independent."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

import ratio_profiles

RATIOS = dict(ratio_profiles.CHOICES)
LABELS = {ratio: ratio_profiles.profile_for_ratio(ratio)['label']
          for ratio in ratio_profiles.CHOICES.values()}
RATIO_FILE = 'video-ratio.txt'
DEFAULTS_FILE = 'video-defaults.json'
WORKFLOW_FILE = 'workflow-settings.json'
WORKFLOW_POLICY = 'intake-flow-v2'


def validate_ratio(value):
    return ratio_profiles.validate_ratio(value)


def profile_for_ratio(value):
    return ratio_profiles.profile_for_ratio(validate_ratio(value))


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_default(project):
    path = Path(project).resolve().parent / DEFAULTS_FILE
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if value.get('policy') != 'chat-video-default-v1':
        raise ValueError('当前聊天的视频比例默认设置不兼容')
    return validate_ratio(value.get('video_ratio'))


def get_ratio(project, state=None, required=False):
    project = Path(project).resolve()
    path = project / RATIO_FILE
    configured = bool(state and state.get('video_ratio_hash'))
    if not path.exists():
        if required or configured or (project / WORKFLOW_FILE).exists():
            raise ValueError('尚未保存本项目视频比例，请先选择横屏16:9或竖屏9:16')
        return '16:9'
    value = validate_ratio(path.read_text(encoding='utf-8-sig').strip())
    if configured and digest(value) != state['video_ratio_hash']:
        raise ValueError('视频比例文件被外部更改；请用ratio命令更新')
    if configured and state.get('video_ratio_profile'):
        expected = ratio_profiles.profile_for_ratio(value)
        if (state['video_ratio_profile'] != expected['profile_id']
                or (state.get('video_ratio_profile_version') is not None
                    and state['video_ratio_profile_version'] != expected['profile_version'])
                or (state.get('video_ratio_profile_digest') is not None
                    and state['video_ratio_profile_digest'] != expected['profile_digest'])):
            raise ValueError('视频比例画幅配置与项目状态不一致；请用ratio命令更新')
    return value


def save_ratio(project, value=None, *, inherit=False):
    """Only persist the selection; do not parse scripts or initialize a project."""
    project = Path(project).resolve()
    style = project / 'video-style.txt'
    if not style.is_file() or not style.read_text(encoding='utf-8-sig').strip():
        raise ValueError('请先确认并保存视频风格，再保存视频比例')
    from video_header import require_intake as require_header
    require_header(project)
    if inherit:
        if value is not None:
            raise ValueError('继承比例与指定新比例不能同时使用')
        value = read_default(project)
        if value is None:
            raise ValueError('当前聊天尚未确认视频比例，请先选择')
    value = validate_ratio(value)
    _write(project / RATIO_FILE, value + '\n')
    if not inherit:
        _write(project.parent / DEFAULTS_FILE, json.dumps(
            {'policy': 'chat-video-default-v1', 'video_ratio': value},
            ensure_ascii=False, indent=2) + '\n')
    return {'status': 'ratio_saved', 'video_ratio': value, 'label': LABELS[value],
            'ratio_file': str(project / RATIO_FILE), 'next_step': 'prepare'}


def require_intake(project):
    project = Path(project).resolve()
    style = project / 'video-style.txt'
    if not style.is_file() or not style.read_text(encoding='utf-8-sig').strip():
        raise ValueError('完整剧本须先确认并保存视频风格')
    from video_header import require_intake as require_header
    require_header(project)
    return get_ratio(project, required=True)


def mark_workflow(project):
    """Called after project-init, never during either settings-only gate."""
    require_intake(project)
    _write(Path(project) / WORKFLOW_FILE, json.dumps(
        {'policy': WORKFLOW_POLICY}, ensure_ascii=False, indent=2) + '\n')


def status(project, state=None):
    project = Path(project).resolve()
    style = project / 'video-style.txt'
    saved_style = style.is_file() and bool(style.read_text(encoding='utf-8-sig').strip())
    saved_ratio = (project / RATIO_FILE).is_file()
    ratio = get_ratio(project, state) if saved_ratio else None
    from video_header import status as header_status
    header = header_status(project, state)
    return {'style_saved': saved_style, 'video_header': header,
            'video_ratio': ratio,
            'chat_default_ratio': read_default(project),
            'next_step': ('style' if not saved_style else
                          'header' if not header['header_saved'] else
                          'ratio' if not saved_ratio else 'prepare')}


def get_profile(project, state=None, required=False):
    """Resolve the storyboard profile selected by the saved project ratio."""
    return profile_for_ratio(get_ratio(project, state, required=required))
