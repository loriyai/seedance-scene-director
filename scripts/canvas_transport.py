"""CLI 1.0.57 response framing and complete, read-only Canvas queries."""
import json
import os
import re
import signal
import subprocess
import time
from urllib.request import urlopen

CLI_VERSION = '1.0.57'
READ_ATTEMPTS = 3
READ_BUDGET = 60


def final_json(stdout):
    """Accept one terminal JSON document after SDK diagnostic lines."""
    try:
        return json.loads(stdout)
    except (ValueError, TypeError):
        pass
    decoder = json.JSONDecoder()
    candidates = []
    for match in re.finditer(r'(?m)^\s*\{', stdout or ''):
        start = match.start()
        while start < len(stdout) and stdout[start].isspace():
            start += 1
        try:
            value, end = decoder.raw_decode(stdout[start:])
        except ValueError:
            continue
        if isinstance(value, dict) and 'ok' in value and not stdout[start + end:].strip():
            candidates.append(value)
    if len(candidates) != 1:
        raise ValueError('CLI did not return a single terminal JSON result')
    return candidates[0]


def execute(argv, cwd, timeout):
    """Stop the complete process tree on timeout, including native children."""
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with subprocess.Popen([str(x) for x in argv], cwd=str(cwd), shell=False,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding='utf-8', errors='replace',
                          env={**os.environ, 'PIPPIT_CLI_DISABLE_UPDATE_CHECK': '1'},
                          creationflags=flags, start_new_session=os.name != 'nt') as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=10, creationflags=flags)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.kill()
            process.communicate()
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def error_code(message):
    if re.search(r'(?i)\b(401|403)\b|unauthorized|forbidden|login required|未登录|无权限', message):
        return 'CLI_AUTH_ERROR'
    if re.search(r'(?i)\bEOF\b|ECONNRESET|ETIMEDOUT|socket hang up|connection reset|TLS|connection refused|timed out|wsarecv|forcibly closed|connection was aborted', message):
        return 'CLI_TRANSIENT'
    if re.search(r'(?i)did not return requested assets|returned invalid (?:data|asset)|missing.*content', message):
        return 'READBACK_INCOMPLETE'
    return 'CLI_ERROR'


def read_retry(action, check_control):
    import canvas as c
    deadline = time.monotonic() + READ_BUDGET
    for attempt in range(READ_ATTEMPTS):
        check_control()
        try:
            return action(max(1, min(20, deadline - time.monotonic())))
        except c.CanvasError as exc:
            if exc.code not in {'CLI_TRANSIENT', 'CLI_TIMEOUT', 'READBACK_INCOMPLETE'}:
                raise
            if attempt == READ_ATTEMPTS - 1 or time.monotonic() + 2 ** attempt >= deadline:
                c.fail('READBACK_PENDING', '只读核验暂未完成；保留原节点与写入回执，恢复时继续查询。',
                       cause_code=exc.code, attempts=attempt + 1)
            time.sleep(2 ** attempt)


def decode_assets(response, requested, team, deadline=None):
    import canvas as c
    if response.get('team_id') != team or not isinstance(response.get('assets'), list):
        c.fail('READBACK_INCOMPLETE', '画布查询缺少空间或资产列表。')
    assets, versions = {}, {}
    for row in response['assets']:
        asset_id = row.get('PippitAssetID')
        text_info = row.get('TextInfo') or {}
        body = text_info.get('content')
        if not body and text_info.get('download_url'):
            url = text_info['download_url']
            if not isinstance(url, str) or not url.startswith('https://'):
                c.fail('READBACK_INCOMPLETE', '资产内容下载地址无效。')
            try:
                remaining = max(0, deadline - time.monotonic()) if deadline is not None else 20
                if remaining <= 0:
                    c.fail('CLI_TIMEOUT', '资产内容查询时间预算已用尽。')
                chunks, length = [], 0
                with urlopen(url, timeout=min(20, remaining)) as stream:
                    while True:
                        if deadline is not None and time.monotonic() >= deadline:
                            c.fail('CLI_TIMEOUT', '资产内容查询时间预算已用尽。')
                        chunk = stream.read(65536)
                        if not chunk:
                            break
                        length += len(chunk)
                        if length > 64 * 1024 * 1024:
                            c.fail('READBACK_INCOMPLETE', '资产内容超过64 MiB。')
                        chunks.append(chunk)
                body = b''.join(chunks).decode('utf-8')
            except (OSError, UnicodeError):
                c.fail('CLI_TRANSIENT', '资产内容下载暂时失败。')
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                c.fail('READBACK_INCOMPLETE', '资产内容JSON尚未完整返回。')
        if (not isinstance(asset_id, str) or asset_id in assets or not isinstance(body, dict)
                or not isinstance(body.get('content'), dict) or not isinstance(body.get('type'), str)
                or body.get('pippitAssetId', asset_id) != asset_id
                or type(row.get('version')) is not int or row['version'] < 0):
            c.fail('READBACK_INCOMPLETE', '资产身份、内容或版本尚未完整返回。')
        assets[asset_id] = body
        versions[asset_id] = row['version']
    if set(requested) != set(assets):
        c.fail('READBACK_INCOMPLETE', '本次查询的资产未全部返回。', missing_count=len(set(requested) - set(assets)))
    return assets, versions


def snapshot(cli, binding, node_ids=None):
    import canvas as c
    root_id, team = binding['canvas_id'], c.normalize_team_id(binding['team_id'])

    def fetch(ids):
        args = ['canvas', 'get']
        for asset_id in ids:
            args.extend(['--asset-id', asset_id])
        def query(timeout):
            deadline = time.monotonic() + timeout
            response = cli.native(*args, timeout=timeout, retry_read=False)
            result = decode_assets(response, ids, team, deadline=deadline)
            if root_id in result[0]:
                root = result[0][root_id]
                if root['type'] != 'canvas' or not isinstance(root['content'].get('nodes'), dict) or not isinstance(root['content'].get('edges'), dict):
                    c.fail('READBACK_INCOMPLETE', '画布根图缺少节点或连线结构。')
            return result
        return read_retry(query, lambda: c.check_control(cli))

    assets, versions = fetch([root_id])
    root = assets[root_id]
    if root['content'].get('settings', {}).get('readOnly'):
        c.fail('CLI_AUTH_ERROR', '画布当前为只读。')
    members = root['content']['nodes']
    requested = list(dict.fromkeys(node_ids if node_ids is not None else
                    [nid for nid, node in members.items() if str(node.get('type', '')).startswith('biz/')]))
    # Root membership proves whether a node exists. Only fetch members, so an
    # explicitly removed node can be reconciled without a fabricated empty asset.
    requested = [nid for nid in requested if nid != root_id and nid in members]
    for offset in range(0, len(requested), 50):
        fetched, fetched_versions = fetch(requested[offset:offset + 50])
        assets.update(fetched)
        versions.update(fetched_versions)
    return {'protocol': c.BATCH_PROTOCOL, 'document': {'rootCanvasId': root_id, 'assets': assets},
            'asset_versions': versions, 'queried_ids': [root_id, *requested], 'complete': True}
