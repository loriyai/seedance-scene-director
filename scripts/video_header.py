"""Project-level storyboard header settings.

The current project header is user-authored and stored separately from the
video style.  Legacy units keep their built-in header policy and never read
this file retroactively.
"""
import hashlib
import os
from pathlib import Path
import tempfile


HEADER_FILE = 'video-header.txt'
POLICY = 'custom-v1'
LINE_COUNT = 2


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def validate(text):
    if not isinstance(text, str):
        raise ValueError('固定抬头须为文本')
    if '\r' in text:
        raise ValueError('固定抬头不得包含回车符')
    if not text.strip():
        raise ValueError('固定抬头不能为空')
    lines = text.split('\n')
    if len(lines) != LINE_COUNT or any(not line.strip() for line in lines):
        raise ValueError('固定抬头须为两行非空文本')
    return text


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text + '\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def path(project):
    return Path(project).resolve() / HEADER_FILE


def read(project, state=None, required=True, verify=True):
    target = path(project)
    if not target.is_file():
        if required:
            raise ValueError('尚未保存本项目固定抬头，请先输入并保存')
        return None
    text = validate(target.read_text(encoding='utf-8-sig').rstrip('\n'))
    expected = state.get('header_hash') if state and verify else None
    if expected and digest(text) != expected:
        raise ValueError('固定抬头文件被外部更改；请用header命令更新')
    return text


def save(project, text, state=None):
    text = validate(text)
    _write(path(project), text)
    result = {'status': 'header_saved', 'header_file': str(path(project)),
              'header_hash': digest(text), 'header_policy': POLICY,
              'next_step': 'ratio'}
    if state is not None:
        state['header_hash'] = result['header_hash']
        state['header_policy'] = POLICY
    return result


def save_file(project, source, state=None):
    return save(project, Path(source).read_text(encoding='utf-8-sig').rstrip('\n'), state)


def status(project, state=None):
    target = path(project)
    if not target.is_file():
        return {'header_saved': False, 'header_file': str(target), 'header_policy': None,
                'header_hash': None, 'next_step': 'header'}
    text = read(project, state, required=True)
    return {'header_saved': True, 'header_file': str(target), 'header_policy': POLICY,
            'header_hash': digest(text), 'next_step': 'ratio'}


def require_intake(project, state=None):
    return read(project, state, required=True)
