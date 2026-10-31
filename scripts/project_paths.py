"""Project-owned references; machine locations never define creative identity."""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata
import os
from functools import lru_cache

POLICY = 'portable-project-v1'


def canonical(value):
    text = str(value)
    if text.startswith('\\\\?\\UNC\\'):
        text = '\\\\' + text[8:]
    elif text.startswith('\\\\?\\'):
        text = text[4:]
    return Path(text).resolve()


def absolute_path(value):
    """Return an absolute path while preserving symlink spelling for displays."""
    return Path(os.path.abspath(os.fspath(value)))


def io_path(value, force=False):
    path = canonical(value)
    text = str(path)
    if os.name == 'nt' and (force or len(text) >= 240):
        return Path('\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text)
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or value.startswith('/'):
        raise ValueError('包内路径必须是相对POSIX路径')
    parts = value.split('/')
    if any(p in ('', '.', '..') or p.endswith((' ', '.')) or re.search(r'[<>"|?*\x00-\x1f]', p) or
           re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in parts):
        raise ValueError('包内路径越界或不能跨平台恢复：' + value)
    return PurePosixPath(value)


def collision_key(value):
    safe_name(value)
    return unicodedata.normalize('NFC', value).casefold()


def read_metadata(project):
    path = io_path(Path(project) / '.portable/manifest.json')
    if not path.exists():
        return None
    seal = path.with_suffix('.sha256')
    if not seal.is_file():
        raise ValueError('项目迁移清单缺少摘要')
    stamp, seal_stamp = path.stat(), seal.stat()
    return _metadata(str(path.resolve()), stamp.st_mtime_ns, stamp.st_size, seal_stamp.st_mtime_ns)


@lru_cache(maxsize=16)
def _metadata(filename, modified, size, seal_modified):
    path = Path(filename)
    raw = path.read_bytes()
    seal = path.with_suffix('.sha256')
    if not seal.is_file() or seal.read_text(encoding='ascii').strip() != digest(raw):
        raise ValueError('项目迁移清单摘要不匹配')
    value = json.loads(raw)
    if value.get('policy') != POLICY:
        raise ValueError('项目迁移清单版本不兼容')
    return value


def destination(project, member):
    member = str(safe_name(member))
    relative = '' if member == 'project' else member[8:] if member.startswith('project/') else '.portable/' + member
    target = (canonical(project) / relative).resolve()
    if not target.is_relative_to(canonical(project)):
        raise ValueError('项目路径逃逸')
    return io_path(target)


def resolve(project, value):
    meta = read_metadata(project)
    if meta is None:
        return Path(value).resolve()
    raw_value = str(value)
    prefix = 'project://' + meta['project_id'] + '/'
    if raw_value.startswith(prefix):
        return destination(project, raw_value[len(prefix):])
    if raw_value in meta['path_map']:
        return destination(project, meta['path_map'][raw_value])
    # Manifest keys are canonicalized at export time.  macOS commonly exposes
    # the same temporary tree as both /var and /private/var; compare the
    # canonical spelling as a fallback without changing user-facing paths.
    canonical_value = str(canonical(raw_value)) if Path(raw_value).is_absolute() else None
    if canonical_value in meta['path_map']:
        return destination(project, meta['path_map'][canonical_value])
    normalized = (canonical_value or raw_value).replace('\\', '/').rstrip('/')
    for origin, member in sorted(meta.get('directory_map', {}).items(), key=lambda item: -len(item[0])):
        prefix_path = str(canonical(origin)).replace('\\', '/').rstrip('/')
        if normalized == prefix_path or normalized.startswith(prefix_path + '/'):
            suffix = normalized[len(prefix_path):].lstrip('/')
            return destination(project, member + ('/' + suffix if suffix else ''))
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path(project) / candidate
    candidate = canonical(candidate)
    if not candidate.is_relative_to(canonical(project)):
        raise ValueError('导入项目引用了未收录的原机路径：' + value)
    return io_path(candidate)


def reference(project, value):
    meta = read_metadata(project)
    if meta is None:
        return str(value)
    path = canonical(resolve(project, str(value)))
    relative = path.relative_to(canonical(project)).as_posix()
    member = relative[len('.portable/'):] if relative.startswith('.portable/') else 'project/' + relative
    return 'project://' + meta['project_id'] + '/' + member


def state_view(project, state, storing=False):
    """Resolve declared session locations only; preserve reports and text verbatim."""
    value = copy.deepcopy(state)
    convert = reference if storing else lambda p, v: str(resolve(p, v))
    for row in value.get('files', {}).values():
        row['path'] = convert(project, row['path'])
    if read_metadata(project):
        scope = str(Path(project).resolve() / '.portable/batch')
        if value.get('batch_scope'):
            value['batch_scope'] = reference(project, scope) if storing else scope
        # The standardizer is installed code, never an executable from the ZIP.
        if not storing:
            value['standardizer'] = str(Path(__file__).resolve().parents[1] / 'modules/screenplay-standardizer')
    for row in value.get('receipts', []):
        if row.get('output'):
            row['output'] = convert(project, row['output'])
        if row.get('batch_scope') and read_metadata(project):
            row['batch_scope'] = reference(project, scope) if storing else scope
    return value


def semantic_files(state):
    rows = state.get('files')
    if rows is None:
        return None
    # Incomplete legacy views retain their original fields; do not invent hashes.
    return {key: {k: v for k, v in row.items() if k != 'path'} if row.get('sha256')
            else copy.deepcopy(row) for key, row in rows.items()}


def project_id(project):
    meta = read_metadata(project)
    return meta['project_id'] if meta else None


def assert_writable(project, cloud=False):
    marker = Path(project) / 'project-transfer.json'
    if marker.is_file() and json.loads(marker.read_text(encoding='utf-8')).get('state') == 'transferred':
        raise ValueError('项目已交接，本机写入已停止；明确release后作为分叉继续')
    location = Path(project) / '.portable/cloud.json'
    if cloud and location.is_file() and json.loads(location.read_text(encoding='utf-8')).get('requires_readback'):
        raise ValueError('迁移后的画布须先在本机配置登录并只读核验原绑定与节点，再明确恢复队列')
