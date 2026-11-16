"""User-selected space defaults, isolated by credential identity; no secrets."""
import json
import os
from pathlib import Path
import re
import tempfile


def defaults_path():
    base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return base / 'seedance-scene-director' / 'canvas-space-defaults.json'


def load():
    path = defaults_path()
    if not path.exists():
        return {'schema': 1, 'selections': {}}
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('schema') != 1
            or not isinstance(value.get('selections'), dict)):
        raise ValueError('默认空间记录格式异常；保留原文件，不自动覆盖')
    return value


def selection(identity):
    value = load()['selections'].get(identity)
    if value is None:
        return None
    if (not isinstance(value, dict) or not isinstance(value.get('team_id'), str)
            or not re.fullmatch(r'\d+', value['team_id'])
            or int(value['team_id']) > 9223372036854775807):
        raise ValueError('当前凭据的默认空间记录无效；保留原文件')
    return dict(value)


def save_selection(identity, team_id, name):
    if (not isinstance(identity, str) or not identity
            or not isinstance(team_id, str) or not re.fullmatch(r'\d+', team_id)
            or int(team_id) > 9223372036854775807):
        raise ValueError('保存默认空间需要有效凭据作用域和空间ID')
    path = defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = path.with_suffix('.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('默认空间设置正被其他进程占用；未覆盖现有记录') from None
    temporary = None
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(str(os.getpid()))
        value = load()
        selected = {'team_id': str(int(team_id)), 'name': str(name)}
        value['selections'][identity] = selected
        fd, temporary = tempfile.mkstemp(prefix='.canvas-space-', dir=path.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
        return selected
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()
        lock.unlink(missing_ok=True)
