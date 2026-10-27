"""Versioned, content-verified project transfer. Archive code is never executed."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import tempfile
import uuid
import zipfile

import project_paths as paths
import project_continuation as continuation

MAX_FILES = 50000
MAX_BYTES = 2 * 1024 ** 3
MAX_FILE_BYTES = 256 * 1024 ** 2
PATH_KEYS = {'path', 'draft', 'output', 'actual_output', 'original_review', 'context', 'preview',
             'assembly_proof', 'review_reuse', 'manifest', 'source', 'script', 'ledger', 'review'}
CREATIVE_FILES = ('references/storyboard-rules.md', 'references/storyboard-workflow.md',
                  'references/storyboard-text-format.md', 'assets/fixed-header-visual-v3.txt')
LEGACY_CONTEXT_FILES = ('references/storyboard-rules.md', 'references/storyboard-text-format.md',
                       'scripts/storyboard_text.py', 'scripts/storyboard_text_delivery.py',
                       'scripts/storyboard_context.py', 'scripts/segment_naming.py',
                       'scripts/script_partition.py', 'scripts/video_settings.py')


def legacy_context(state, project, runtime):
    import director as d
    from script_partition import load_plan
    from video_settings import get_ratio
    value = {key: state.get(key) for key in ('files', 'records', 'ledger', 'style_hash', 'units', 'entry')}
    value['text_rules'] = {name: d.file_hash(Path(runtime) / name) for name in LEGACY_CONTEXT_FILES}
    value['video_ratio'], value['partition_plan'] = get_ratio(project, state), load_plan(project, state)
    return d.sha(d.packed(value))


def make_review_bridge(project, state, point, runtime):
    if not point or point['stage'] != 'passed_pending_delivery':
        return None
    import director as d
    from storyboard_context import context_hash
    report = continuation.read(paths.resolve(project, point['artifacts']['review']['path']))
    current = context_hash(state, project)
    inherited = continuation.review_bridge(project, point['artifacts']['draft']['sha256'], current)
    if inherited:
        return inherited
    if report['context_sha256'] == current:
        return None
    if any(d.file_hash(Path(runtime) / name) != d.file_hash(d.ROOT / name) for name in CREATIVE_FILES):
        return None
    if report['context_sha256'] != legacy_context(state, project, runtime):
        return None
    return {'policy': 'legacy-path-binding-equivalence-v1', 'draft_sha256': point['artifacts']['draft']['sha256'],
            'old_context_sha256': report['context_sha256'], 'new_context_sha256': current,
            'report_digest': d.sha(d.packed(report)),
            'creative_files': {name: d.file_hash(d.ROOT / name) for name in CREATIVE_FILES},
            'semantic_review': 'original_report_preserved_not_reperformed'}


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def write(path, value):
    import director as d
    d.write_json(paths.io_path(path), value)


def excluded(relative):
    parts = Path(relative).parts
    name = parts[-1].lower()
    return (any(p in ('.git', '__pycache__', '.portable') for p in parts)
            or name.endswith(('.pyc', '.lock', '.tmp')) or name in ('.env', 'connection.json', 'worker.json',
                'worker.log', 'monitor.json', 'control.json', 'project-transfer.json', 'auth.json',
                'credentials.json', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519')
            or name.startswith('.env.'))


def absolute(value):
    return isinstance(value, str) and (Path(value).is_absolute() or PureWindowsPath(value).is_absolute()
                                        or value.startswith('project://'))


class Collector:
    def __init__(self, project):
        self.project = paths.canonical(project)
        self.files, self.path_map, self.queue, self.directories = {}, {}, [], {}

    def add(self, filename, member=None, alias=None):
        if Path(filename).is_symlink():
            raise ValueError('交接包不跟随符号链接')
        file = paths.canonical(filename)
        if file.is_symlink() or not paths.io_path(file).is_file():
            raise ValueError('依赖不是普通文件：' + str(filename))
        member = member or self.path_map.get(str(file)) or ('project/' + file.relative_to(self.project).as_posix()
                            if file.is_relative_to(self.project) and '.portable' not in file.relative_to(self.project).parts
                            else 'dependencies/' + paths.digest(str(file).encode())[:24] + '/' + file.name)
        paths.safe_name(member)
        previous = self.files.get(member)
        if previous and previous != file:
            raise ValueError('依赖目标冲突')
        if member not in self.files:
            self.files[member] = file
            self.queue.append(file)
        self.path_map[str(file)] = member
        if alias:
            self.path_map[alias] = member
        return member

    def tree(self, directory, prefix=None):
        directory = paths.canonical(directory)
        if not directory.is_dir():
            raise ValueError('依赖目录不存在：' + str(directory))
        prefix = prefix or 'dependencies/trees/' + paths.digest(str(directory).encode())[:20]
        self.directories[str(directory)] = prefix
        for current, directories, filenames in os.walk(paths.io_path(directory), followlinks=False):
            current = Path(current)
            directories[:] = sorted(name for name in directories
                if not excluded(paths.canonical(current / name).relative_to(directory)))
            for name in directories:
                if (current / name).is_symlink():
                    raise ValueError('交接包不跟随符号链接：' + str(current / name))
            for name in sorted(filenames):
                file = current / name
                rel = paths.canonical(file).relative_to(directory)
                if not excluded(rel):
                    self.add(file, prefix + '/' + rel.as_posix())

    def closure(self):
        seen = set()
        while self.queue:
            file = self.queue.pop()
            if file in seen or file.suffix != '.json':
                continue
            seen.add(file)
            try:
                value = continuation.read(file)
            except (ValueError, UnicodeError):
                continue

            def visit(item):
                if isinstance(item, dict):
                    for key, child in item.items():
                        if key in PATH_KEYS and absolute(child):
                            try:
                                target = paths.resolve(self.project, child)
                            except ValueError:
                                continue  # Historical, unregistered paths are not executable inputs.
                            if target.is_file() and not excluded(target.name):
                                self.add(target, alias=child)
                        elif absolute(key) and isinstance(child, str) and len(child) == 64:
                            try:
                                target = paths.resolve(self.project, key)
                            except ValueError:
                                continue
                            if target.is_file() and continuation.sha(target) == child:
                                self.add(target, alias=key)
                        if isinstance(child, (dict, list)):
                            visit(child)
                elif isinstance(item, list):
                    for child in item:
                        visit(child)
            visit(value)
        old = paths.read_metadata(self.project)
        if old:
            for alias, member in old['path_map'].items():
                target = paths.destination(self.project, member)
                if str(target) in self.path_map:
                    self.path_map[alias] = self.path_map[str(target)]
            for alias, member in old.get('directory_map', {}).items():
                target = paths.destination(self.project, member)
                if str(target) in self.directories:
                    self.directories[alias] = self.directories[str(target)]


def export_project(project, output, includes=(), handoff=False, related_projects=()):
    import director as d
    import canvas_jobs
    project, output = paths.canonical(project), paths.io_path(output)
    if output.is_relative_to(project) or output.exists():
        raise ValueError('交接包须另存于项目之外，且不得覆盖现有文件')
    if canvas_jobs.worker_active(project):
        raise ValueError('当前画布写者仍在运行；先在安全检查点停止后导出')
    state = d.load(project)
    local = continuation.deliveries(project)
    checkpoint = continuation.resume(project, state)['checkpoint']
    collector = Collector(project)
    collector.tree(project, 'project')
    source_folder = Path(state['files']['source']['path']).parent
    if not source_folder.is_relative_to(project):
        collector.tree(source_folder)
    profile = project / 'project-tooling-active.json'
    runtime = paths.resolve(project, continuation.read(profile)['tool_root']) if profile.is_file() else d.ROOT
    # Pinned project code is evidence only. The importer executes installed Skill code.
    collector.tree(runtime)
    related_rows = []
    related_paths = list(related_projects)
    prior_meta = paths.read_metadata(project)
    if prior_meta:
        related_paths += [paths.resolve(project, row['session']).parent for row in prior_meta.get('related_projects', [])]
    for other in related_paths:
        other = Path(other).resolve()
        sibling = d.load(other) if not prior_meta else continuation.read(other / 'session.json')
        if sibling['records'] != state['records']:
            raise ValueError('关联项目来源记录不一致；不能合并范围覆盖')
        collector.tree(other)
        receipts = []
        for receipt in sibling['receipts']:
            target = paths.resolve(project if prior_meta else other, receipt['output'])
            collector.add(target, alias=receipt['output'])
            receipts.append({'request_id': receipt['request_id'], 'output': receipt['output'],
                             'output_sha256': continuation.sha(target),
                             'span': [min(u['span'][0] for u in receipt['units']), max(u['span'][1] for u in receipt['units'])]})
        related_rows.append({'session': str(other / 'session.json'), 'sha256': continuation.sha(other / 'session.json'),
                             'unit_count': len(sibling['units']), 'ranges': sibling['ranges'], 'receipts': receipts})
    for record in state['files'].values():
        verified = continuation.verified(project, record['path'], record['sha256'])
        collector.add(verified, alias=record['path'])
    for row in state.get('receipts', []):
        if row.get('output'):
            collector.add(paths.resolve(project, row['output']), alias=row['output'])
    for delivery in local:
        for dependency in delivery['dependencies']:
            collector.add(continuation.verified(project, **{'value': dependency['path'], 'expected': dependency['sha256']}),
                          alias=dependency['path'])
    for filename in includes:
        file = Path(filename).resolve()
        if file.is_dir():
            collector.tree(file)
        else:
            collector.add(file)
    if checkpoint:
        for row in checkpoint.get('artifacts', {}).values():
            collector.add(continuation.verified(project, row['path'], row['sha256']), alias=row['path'])
    collector.closure()
    raw_session = continuation.read(project / 'session.json')
    parent_registry = Path(state.get('batch_scope') or project.parent) / 'batch-names.json'
    registry = continuation.read(parent_registry) if parent_registry.is_file() else {'schema_version': 1, 'entries': {}}
    selected = {key: row for key, row in registry['entries'].items()
                if row.get('project') == str(project)}
    registry = {'schema_version': 1, 'entries': selected,
                'high_watermark': max([registry.get('high_watermark', 0)] + [r['number'] for r in registry['entries'].values()])}
    old = paths.read_metadata(project)
    identity = old['project_id'] if old else str(uuid.uuid5(uuid.NAMESPACE_URL, str(project)))
    files = {member: {'size': paths.io_path(file).stat().st_size, 'sha256': continuation.sha(file)}
             for member, file in collector.files.items()}
    manifest = {'policy': paths.POLICY, 'project_id': identity, 'origin_project': str(project),
                'source_revision': state['revision'], 'files': files, 'path_map': collector.path_map,
                'directory_map': collector.directories,
                'local_deliveries': local, 'checkpoint': checkpoint, 'related_projects': related_rows,
                'batch_registry': registry, 'package_kind': 'handoff' if handoff else 'snapshot',
                'runtime': {'version': (d.ROOT / 'VERSION').read_text(encoding='utf-8').strip(),
                            'context_policy': 'content-context-v2'},
                'cloud': {'restore_paused': True, 'credentials_included': False},
                'formal_receipts': len(state.get('receipts', [])), 'semantic_review': 'not_performed'}
    manifest['review_bridge'] = make_review_bridge(project, state, checkpoint, runtime)
    manifest['package_id'] = paths.digest(packed(manifest))
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.project-export-', suffix='.zip', dir=output.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', packed(manifest))
            for member, file in collector.files.items():
                data = paths.io_path(file).read_bytes()
                if paths.digest(data) != files[member]['sha256']:
                    raise ValueError('导出期间项目发生变化：' + str(file))
                archive.writestr(member, data)
        # Detect changes after copying as well, not only a torn read.
        for member, file in collector.files.items():
            if continuation.sha(file) != files[member]['sha256']:
                raise ValueError('导出期间项目发生变化：' + str(file))
        if continuation.read(project / 'session.json') != raw_session:
            raise ValueError('导出期间发生提交')
        inspect_bundle(temporary)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if handoff:
        write(project / 'project-transfer.json', {'policy': paths.POLICY, 'package_id': manifest['package_id'],
              'source_revision': state['revision'], 'state': 'transferred',
              'note': '本机停止继续写入；重新启用须明确release，之后两份成果按分叉处理。'})
    return {'status': 'exported', 'output': str(output), 'package_id': manifest['package_id'],
            'file_count': len(files), 'local_deliveries': len(local), 'source_frozen': handoff}


def inspect_bundle(filename):
    with zipfile.ZipFile(filename) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_BYTES or any(i.file_size > MAX_FILE_BYTES for i in infos):
            raise ValueError('交接包超过文件数或展开大小上限')
        names = set()
        for info in infos:
            key = paths.collision_key(info.filename)
            if key in names or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                raise ValueError('包内重名、链接或加密文件不可恢复')
            names.add(key)
        if 'manifest.json' not in archive.namelist() or archive.getinfo('manifest.json').file_size > 16 * 1024 ** 2:
            raise ValueError('缺少或超大项目清单')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('policy') != paths.POLICY:
            raise ValueError('不支持的交接包版本')
        if manifest.get('runtime', {}).get('context_policy') != 'content-context-v2':
            raise ValueError('不支持的项目上下文协议')
        payload = {k: v for k, v in manifest.items() if k != 'package_id'}
        if paths.digest(packed(payload)) != manifest.get('package_id'):
            raise ValueError('交接包清单摘要不匹配')
        if set(archive.namelist()) != set(manifest['files']) | {'manifest.json'}:
            raise ValueError('清单与包内文件不一致')
        for member, row in manifest['files'].items():
            if not member.startswith(('project/', 'dependencies/')):
                raise ValueError('未知包内文件域')
            if archive.getinfo(member).file_size != row['size'] or paths.digest(archive.read(member)) != row['sha256']:
                raise ValueError('包内文件摘要不匹配：' + member)
        if any(member not in manifest['files'] for member in manifest['path_map'].values()):
            raise ValueError('路径映射引用了未收录文件')
        for member in manifest.get('directory_map', {}).values():
            paths.safe_name(member)
            if member != 'project' and not member.startswith('dependencies/'):
                raise ValueError('目录映射域不合法')
        return manifest


def verify_import(project, full=False):
    import director as d
    project = Path(project).resolve()
    manifest = paths.read_metadata(project)
    if manifest is None:
        raise ValueError('不是已导入的可迁移项目')
    for sibling in continuation.related(project):
        if continuation.read(paths.resolve(project, sibling['session']))['records'] != d.load(project)['records']:
            raise ValueError('关联项目来源记录已变化')
    if full:
        for member, row in manifest['files'].items():
            if continuation.sha(paths.destination(project, member)) != row['sha256']:
                raise ValueError('已恢复文件与交接包不一致：' + member)
    result = continuation.resume(project, d.load(project))
    for row in manifest.get('local_deliveries', []):
        actual = continuation.local_delivery(project, paths.resolve(project, row['review']).parent)
        if any(actual[key] != row[key] for key in ('segment', 'span', 'unit_count', 'shot_count', 'total_seconds')):
            raise ValueError('本地交付登记与原始依据不一致')
    result.update(package_id=manifest['package_id'], verified_files=len(manifest['files']))
    return result


def import_project(filename, target):
    import batch_naming
    target = paths.io_path(target)
    manifest = inspect_bundle(filename)
    if target.exists():
        existing = paths.read_metadata(target)
        if existing and existing['package_id'] == manifest['package_id']:
            verify_import(target, full=True)
            return {'status': 'already_imported', 'project': str(target), 'package_id': manifest['package_id']}
        raise ValueError('目标目录已存在；不得覆盖或合并不同/更新的项目')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = paths.io_path(tempfile.mkdtemp(prefix='.project-import-', dir=target.parent))
    try:
        with zipfile.ZipFile(filename) as archive:
            for member in manifest['files']:
                output = paths.destination(temporary, member)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(archive.read(member))
        metadata = temporary / '.portable/manifest.json'
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_bytes(packed(manifest))
        metadata.with_suffix('.sha256').write_text(continuation.sha(metadata) + '\n', encoding='ascii')
        # Source processes, locks, login config and controls are not restored.
        write(temporary / 'canvas/jobs/control.json', {'paused': True, 'reason': 'project_import_requires_readback'})
        write(temporary / 'canvas/jobs/worker.json', {'status': 'stopped', 'imported': True})
        write(temporary / '.portable/cloud.json', {'requires_readback': True})
        registry = manifest['batch_registry']
        entries = {}
        for row in registry['entries'].values():
            row = dict(row, project=str(temporary))
            entries[batch_naming._key(temporary, row['request_id'])] = row
        write(temporary / '.portable/batch/batch-names.json', dict(registry, entries=entries))
        verify_import(temporary, full=True)
        # Registry paths are operational data, not historical receipt fields.
        for row in entries.values():
            row['project'] = str(target)
        write(temporary / '.portable/batch/batch-names.json', dict(registry, entries=entries))
        os.replace(temporary, target)
    except BaseException:
        if paths.canonical(temporary).is_relative_to(paths.canonical(target.parent)) and temporary.name.startswith('.project-import-'):
            shutil.rmtree(paths.io_path(temporary, force=True))
        raise
    return {'status': 'imported', 'project': str(target), 'package_id': manifest['package_id'],
            'resume': verify_import(target), 'canvas_auto_resume': False}


def relocate(project, target):
    with tempfile.TemporaryDirectory(prefix='project-relocate-') as temporary:
        bundle = Path(temporary) / 'transfer.zip'
        export_project(project, bundle)
        return import_project(bundle, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('export'); p.add_argument('--project', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--include', type=Path, action='append', default=[])
    p.add_argument('--handoff', action='store_true')
    p.add_argument('--related-project', type=Path, action='append', default=[])
    for command in ('inspect', 'import'):
        p = sub.add_parser(command); p.add_argument('--bundle', type=Path, required=True)
        if command == 'import': p.add_argument('--target', type=Path, required=True)
    for command in ('verify', 'resume', 'checkpoint', 'release', 'resume-canvas', 'relocate'):
        p = sub.add_parser(command); p.add_argument('--project', type=Path, required=True)
        if command == 'relocate': p.add_argument('--target', type=Path, required=True)
        if command == 'checkpoint':
            p.add_argument('--stage', choices=continuation.STAGES, required=True)
            for field in ('scene', 'draft', 'review', 'context', 'note', 'next-segment'):
                p.add_argument('--' + field)
    args = parser.parse_args(argv)
    if args.command == 'export': result = export_project(args.project, args.output, args.include, args.handoff, args.related_project)
    elif args.command == 'inspect':
        m = inspect_bundle(args.bundle)
        result = {k: m[k] for k in ('policy', 'package_id', 'project_id', 'runtime', 'checkpoint')}
        result.update(file_count=len(m['files']), local_deliveries=len(m['local_deliveries']))
    elif args.command == 'import': result = import_project(args.bundle, args.target)
    elif args.command == 'relocate': result = relocate(args.project, args.target)
    elif args.command == 'verify': result = verify_import(args.project, full=True)
    elif args.command == 'resume': result = continuation.resume(args.project)
    elif args.command == 'checkpoint':
        paths.assert_writable(args.project)
        result = continuation.checkpoint(args.project, args.stage, args.scene, args.draft, args.review,
                                         args.context, args.note, args.next_segment)
    elif args.command == 'release':
        write(args.project / 'project-transfer.json', {'state': 'released_as_branch'})
        result = {'status': 'released_as_branch', 'note': '两机后续修改按分叉处理，不自动合并。'}
    else:
        import canvas
        paths.assert_writable(args.project)
        state, document = canvas.connected(args.project, canvas.CLI(args.project))
        for row in state['mappings'].values():
            node_id = row.get('node_id')
            if node_id not in canvas.nodes(document):
                raise ValueError('原映射节点已缺失；保留原ID，先处理现有恢复问题')
            if row.get('prompt_hash') and canvas.digest(canvas.prompt_of(document, node_id)) != row['prompt_hash']:
                raise ValueError('原映射提示词发生外部变化；保留现状，先核对已有冲突')
        if any(row.get('status') in ('uncertain', 'unknown', 'prepared', 'acknowledged') for row in state.get('journal', [])):
            raise ValueError('仍有未确认写入；先按原队列协议对照官方回读处理，不解除迁移保护')
        write(args.project / '.portable/cloud.json', {'requires_readback': False})
        result = {'status': 'canvas_readback_verified', 'queue_still_paused': True}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error))
