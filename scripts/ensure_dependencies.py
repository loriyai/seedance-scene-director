#!/usr/bin/env python3
"""Check PyYAML in the selected Python and install it only when missing."""
import importlib
import json
import subprocess
import sys


def load_yaml():
    try:
        module = importlib.import_module('yaml')
    except ModuleNotFoundError as exc:
        if exc.name == 'yaml':
            return None
        raise RuntimeError('PyYAML存在但导入依赖损坏；未自动重装') from exc
    if not callable(getattr(module, 'safe_load', None)):
        raise RuntimeError('当前yaml模块不是可用的PyYAML；未覆盖安装')
    return module


def ensure_pyyaml():
    module = load_yaml()
    status = 'already_installed'
    if module is None:
        try:
            subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                 '--no-input', '--only-binary=:all:', 'PyYAML'],
                check=True, capture_output=True, text=True, timeout=180,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError('PyYAML安装失败；请检查当前Python的pip、网络及写入权限') from exc
        importlib.invalidate_caches()
        module = load_yaml()
        if module is None:
            raise RuntimeError('安装后当前Python仍无法导入PyYAML；未重复安装')
        status = 'installed'
    return {'status': status, 'package': 'PyYAML', 'version': getattr(module, '__version__', 'unknown'),
            'python': sys.executable}


if __name__ == '__main__':
    try:
        print(json.dumps(ensure_pyyaml(), ensure_ascii=True))
    except (RuntimeError, ImportError) as exc:
        print(json.dumps({'status': 'failed', 'message': str(exc)}, ensure_ascii=True), file=sys.stderr)
        sys.exit(1)
