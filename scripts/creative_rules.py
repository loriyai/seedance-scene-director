"""Auditable storyboard-rule domains; unknown package material is conservative.

Only scene_review.rules_digest is the public review entry point. This module
implements its current manifest; historical snapshots keep their saved digest.
"""
import ast
import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

POLICY = 'storyboard-rule-domains-v1'
REQUIRED_FILES = (
    'SKILL.md', 'references/runtime.md',
    'references/storyboard-rules.md', 'references/storyboard-workflow.md',
    'references/storyboard-text-format.md', 'references/execution-contract.md',
    'references/rule-domains.md', 'assets/fixed-header.txt',
    'assets/fixed-header-visual-v2.txt', 'assets/fixed-header-visual-v3.txt', 'scripts/director.py',
    'scripts/review_gate.py', 'scripts/scene_review.py', 'scripts/creative_rules.py',
    'scripts/authoring_gates.py', 'scripts/storyboard_assembly.py',
    'scripts/shot_dependencies.py', 'scripts/continuity.py', 'scripts/draft_revision.py',
    'scripts/batch_naming.py', 'scripts/shot_review.py', 'scripts/storyboard_preflight.py',
    'scripts/staging_preflight.py', 'scripts/dialogue_layout.py',
    'scripts/speech_timing_audit.py', 'scripts/audio_sources.py',
    'scripts/prop_reference_review.py', 'scripts/group_reference_review.py',
    'references/asset-workflow.md',
    'modules/screenplay-standardizer/INSTRUCTIONS.md',
    'references/asset-image-design.md',
    'scripts/state_effects.py',
    'modules/screenplay-standardizer/references/asset-ledger.md',
    'modules/screenplay-standardizer/scripts/character_variants.py',
)

# Explicit operational functions only. Unknown functions, imports, constants,
# signatures, decorators and executable top-level statements remain in the hash.
# Render/reference conversion and content validation are deliberately absent.
OPERATION_FUNCTIONS = {
    'scripts/director.py': ('save', 'sync_canvas_replacements'),
    'scripts/pipeline_metrics.py': ('stamp', 'record_event', 'summarize', 'summarize_project', 'main'),
    'scripts/review_metrics.py': ('speech', 'measure', 'main'),
    'scripts/recovery_status.py': ('summarize',),
    'scripts/ensure_dependencies.py': ('load_yaml', 'ensure_pyyaml'),
    'scripts/run_json.py': ('resolved', 'identity', 'validate_destination_name', 'command_paths',
                         'destinations', 'invalid_constant', 'write_log', 'write_new', 'run', 'main'),
    'scripts/canvas_jobs.py': ('directory', 'read', 'metadata_lock', 'alive', 'worker_active',
        'manifests', 'state_file', 'job_state', 'job_total', 'image_inventory', 'input_scope',
        'enqueue', 'validate_job', 'file_stamp', 'status', 'pause', 'resume', 'recover_assets',
        'image_choice', 'start_worker', 'notify_desktop', 'notify_completion', 'notify_attention',
        'publish_event', 'recover_dead_operation_locks', 'run_job', 'dependency_complete',
        'run_worker', 'main'),
    'scripts/canvas.py': ('run_json', 'state_path', 'read_state', 'save', 'project_lock',
        'connected', 'checkpoint', 'check_control', 'write_verified', 'write_acknowledged',
        'ready_image', 'reference_ready', 'reconcile_asset_nodes', 'sync', 'reference_report',
        '_sync', 'layout_positions', 'set_name', 'manage', 'sync_replacements', 'main'),
    'scripts/canvas_job_backend.py': ('check_boundary', 'business_ready', 'wait_business_ready',
        'observation', 'after_observation', 'pristine_initialization', 'missing_business_rejection',
        'recover_asset_rejection', 'asset_update', 'asset_items', 'asset_targets', 'acknowledge',
        'sync_assets', 'run_job'),
    'scripts/canvas_media.py': ('api', 'file_hash', 'scan_images', 'source_infos', 'selected_image',
        'has_image', 'observed_string', 'uploaded_asset', 'bind_image', 'refresh_references', 'images'),
    'scripts/canvas_reference_report.py': ('api', 'collect_issues', 'write_reference_report'),
}

# These files are interface/package metadata, never creative authorities.
# A new file is not exempt merely because it lives in the same directory.
OPERATION_METADATA = frozenset((
    'README.md', 'VERSION', 'component.json', 'agents/openai.yaml',
    'agents/model-routing.json', 'scripts/ensure-cli.js',
    'scripts/self_check.py', 'scripts/core_test_support.py',
))
OPERATION_BLOCKS = {
    'SKILL.md': frozenset(('entry-routing', 'style-intake', 'local-runtime',
                          'asset-pipeline', 'stage-routing', 'delivery')),
    'references/runtime.md': frozenset(('command-reference', 'recovery',
                                       'canvas-operations', 'dependencies')),
}
_MARKER = re.compile(r'^\s*<!-- rule-domain:operations:(start|end) ([a-z][a-z0-9-]*) -->\s*$')


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _phase_target(node):
    return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and node.value.id == 'state' and isinstance(node.slice, ast.Constant)
            and node.slice.value == 'phase')


def _phase_expression(node):
    """Only side-effect-free phase/contract reads; unknown calls remain bound."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id == 'state'
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_phase_expression(x) for x in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _phase_expression(k) and _phase_expression(v)
                   for k, v in zip(node.keys, node.values))
    if isinstance(node, ast.Subscript):
        return (_phase_expression(node.value) and isinstance(node.slice, ast.Constant)
                and node.slice.value in ('phase', 'delivery_contract', 'scope'))
    if isinstance(node, ast.Call):
        return (isinstance(node.func, ast.Attribute) and node.func.attr == 'get'
                and _phase_expression(node.func.value) and not node.keywords
                and 1 <= len(node.args) <= 2 and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ('phase', 'delivery_contract', 'scope')
                and all(_phase_expression(x) for x in node.args))
    if isinstance(node, ast.Compare):
        return _phase_expression(node.left) and all(_phase_expression(x) for x in node.comparators)
    if isinstance(node, ast.IfExp):
        return all(_phase_expression(x) for x in (node.test, node.body, node.orelse))
    if isinstance(node, ast.BoolOp):
        return all(_phase_expression(x) for x in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _phase_expression(node.operand)
    return False


def _phase_statement(node):
    if isinstance(node, ast.Assign):
        return all(_phase_target(x) for x in node.targets) and _phase_expression(node.value)
    if isinstance(node, ast.If):
        return (_phase_expression(node.test) and bool(node.body)
                and all(_phase_statement(x) for x in node.body + node.orelse))
    return False


class _PhaseProjection(ast.NodeTransformer):
    def visit_If(self, node):
        if _phase_statement(node):
            return ast.Expr(value=ast.Constant(value='operations:phase-transition'))
        return self.generic_visit(node)

    def visit_Assign(self, node):
        if _phase_statement(node):
            return ast.Expr(value=ast.Constant(value='operations:phase-transition'))
        return self.generic_visit(node)


def code_projection(text, relative, *, operation_functions=None):
    """AST excludes only named operation bodies; parse failure fails closed."""
    tree = ast.parse(text, filename=relative)
    allowed = (OPERATION_FUNCTIONS.get(relative, ()) if operation_functions is None
               else operation_functions)
    removed = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in allowed:
                node.body = [ast.Expr(value=ast.Constant(value='operations:function-body'))]
                removed.append(node.name)
            elif relative == 'scripts/director.py' and node.name == 'set_style':
                node.body = [_PhaseProjection().visit(copy.deepcopy(x)) for x in node.body]
    return {'ast': ast.dump(tree, include_attributes=False), 'operation_bodies': removed}


@lru_cache(maxsize=64)
def _cached_code_projection(relative, raw, operation_functions):
    """Cache only a pure projection of exact current bytes and its classifier.

    The caller still rereads every file each time. Immutable returned bytes
    cannot be mutated by another consumer to poison the cached projection.
    """
    return _packed(code_projection(raw.decode('utf-8-sig'), relative,
                                   operation_functions=operation_functions))


def text_projection(text, relative):
    """Ignore known, paired operation blocks; unknown prose stays authoritative.

    Markdown headings inside such a block still bind its structure, so adding
    an unknown section under an operations wrapper does not silently disappear.
    """
    allowed = OPERATION_BLOCKS.get(relative, ())
    result, active, heading_scope = [], None, False
    for line in text.splitlines(keepends=True):
        match = _MARKER.match(line.rstrip('\r\n'))
        if match and match.group(2) in allowed:
            direction, name = match.groups()
            if direction == 'start':
                if active is not None:
                    raise ValueError(f'{relative}: operations块不可嵌套')
                active = name
                heading_scope = False
            else:
                if active != name:
                    raise ValueError(f'{relative}: operations块结束标记不匹配')
                active = None
                heading_scope = False
            result.append(line)
        elif match and active is not None:
            raise ValueError(f'{relative}: 已知operations块内出现未知标记，须明确规则归属')
        elif re.match(r'^\s*#{1,6}\s', line):
            # A heading was not covered by the declared paragraph/block role.
            # Keep its following content too, not just the first edit of its name.
            heading_scope = True
            result.append(line)
        elif active is None or heading_scope:
            result.append(line)
    if active is not None:
        raise ValueError(f'{relative}: operations块未关闭')
    return ''.join(result)


def _non_rule(relative):
    path = Path(relative)
    parts = path.parts
    if any(part in ('.git', '__pycache__', '.pytest_cache') for part in parts):
        return 'generated_cache'
    # Tests use this explicit package convention. New production helpers named
    # otherwise remain bound, including unknown helper modules beside tests.
    if path.name.startswith('test_') and path.suffix in ('.py', '.js'):
        return 'test_source'
    if parts and parts[0] == 'logs' and path.suffix in ('.log', '.jsonl', '.txt'):
        return 'operation_log'
    if relative in OPERATION_METADATA:
        return 'operation_metadata'
    return None


def rules_manifest(root=None, *, required_files=None):
    """Hash actual package inventory; new/unknown rules cannot miss a whitelist.

    required_files exists for isolated fixtures. Production callers leave it
    omitted, so missing maintained rule entry points fail before review.
    """
    root = (Path(root) if root is not None else Path(__file__).resolve().parents[1]).resolve()
    required = REQUIRED_FILES if required_files is None else required_files
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise ValueError('分镜复核规则文件缺失：' + '、'.join(missing))
    entries = {}
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if _non_rule(relative):
            continue
        raw = path.read_bytes()
        if relative in OPERATION_FUNCTIONS:
            projection = _cached_code_projection(relative, raw, tuple(OPERATION_FUNCTIONS[relative]))
            entries[relative] = {'domain': 'mixed-code-ast', 'sha256': _hash(projection)}
        elif relative in OPERATION_BLOCKS:
            projection = text_projection(raw.decode('utf-8-sig'), relative)
            entries[relative] = {'domain': 'mixed-text-explicit-blocks',
                                 'sha256': _hash(projection.encode('utf-8'))}
        else:
            entries[relative] = {'domain': 'creative-or-unknown', 'sha256': _hash(raw)}
    return {'policy': POLICY, 'files': entries}


def rules_digest(root=None, *, required_files=None):
    return _hash(_packed(rules_manifest(root, required_files=required_files)))
