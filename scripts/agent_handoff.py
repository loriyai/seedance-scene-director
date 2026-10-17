"""Prepare immutable local handoff envelopes; never invoke models or submit work."""
import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

SKILL_ROOT = Path(__file__).resolve().parents[1]
STAGES = {"standardize", "assets", "design", "storyboard", "revise", "review", "sync"}
EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}


def object_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def session_creative_snapshot(value):
    # Unknown fields remain protected. Only documented administrative counters
    # are excluded; actual sources, units, ranges and receipts stay immutable.
    return {key: item for key, item in value.items() if key not in {'phase', 'revision'}}


def design_workspace_module():
    folder = str(SKILL_ROOT / 'modules/screenplay-standardizer/scripts')
    if folder not in sys.path:
        sys.path.insert(0, folder)
    import design_workspace
    return design_workspace


def group_inputs(brief, stage):
    value = brief.get('design_group')
    if value is None:
        return None, []
    require(stage == 'design' and isinstance(value, dict)
            and set(value) == {'baseline_path', 'plan_path', 'group_id', 'submission'},
            'design_group仅用于design，须明确baseline_path/plan_path/group_id/submission')
    baseline_path, plan_path = absolute(value['baseline_path']), absolute(value['plan_path'])
    baseline, plan = read_json(baseline_path), read_json(plan_path)
    module = design_workspace_module()
    # The workspace helper owns the plan schema and its input bindings.
    module.validate_baseline(baseline, require_files=True)
    module.validate_plan(baseline, plan)
    groups = plan['groups']
    group = next((item for item in groups if item['group_id'] == value['group_id']), None)
    require(group is not None, 'design_group不属于冻结计划')
    submission = relative(value['submission']).as_posix()
    require(submission in brief['expected_outputs'], '设计组submission须在预期产物中')
    return {'baseline_path': str(baseline_path), 'plan_path': str(plan_path),
            'baseline_sha256': digest(baseline_path), 'plan_sha256': digest(plan_path),
            'group_id': value['group_id'], 'owned_asset_ids': group['owned_asset_ids'],
            'submission': submission}, [baseline_path, plan_path]


def check_reservations(base, stage, group, limit):
    active = []
    for path in base.glob('*/handoff.json'):
        previous = read_json(path)
        if previous.get('routing_policy') != 'stage-handoff-v2':
            continue
        state_path = path.parent / 'handoff-state.json'
        state = read_json(state_path) if state_path.exists() else {'status': 'active'}
        if state.get('status') == 'active' and previous.get('stage') != 'review':
            active.append(previous)
    if stage == 'review':
        return
    if not group:
        require(not active, '已有起草任务占用；先完成或明确关闭旧交接')
        return
    require(len(active) < limit, '独立设计组达到配置并发上限；等待并关闭已完成交接')
    for previous in active:
        old = previous.get('design_group')
        require(previous.get('stage') == 'design' and old is not None,
                '不能与非分组起草任务同时写作')
        require(old['baseline_sha256'] == group['baseline_sha256'] and old['plan_sha256'] == group['plan_sha256'],
                '并行设计组必须使用同一冻结基线和计划')
        require(not set(old['owned_asset_ids']).intersection(group['owned_asset_ids']),
                '并行设计资产责任ID重叠')


def assert_bind_ready(project_value):
    """Refuse binding before root has accepted and closed modern handoffs.

    Binding changes the absence/presence of session.json, which is itself an
    immutable handoff input. Check before any bind writes, not after verify has
    already been invalidated. Legacy envelopes have no reservation lifecycle;
    leave their historical read/verification contract unchanged.
    """
    project = Path(project_value).resolve()
    active = []
    for path in sorted((project / 'agents').glob('*/handoff.json')):
        manifest = read_json(path)
        if manifest.get('routing_policy') != 'stage-handoff-v2':
            continue
        state_path = path.parent / 'handoff-state.json'
        state = read_json(state_path) if state_path.exists() else {'status': 'active'}
        require(state.get('status') in {'active', 'completed', 'cancelled'},
                f'交接状态无效，不能绑定：{path}')
        if state['status'] == 'active':
            active.append(str(path))
    require(not active,
            '绑定前须先verify、完成实际业务验收，再close completed；仍有未关闭交接：'
            + '；'.join(active))
    return {'status': 'bind_ready', 'active_handoffs': 0}


def close(handoff_value, status, reason):
    handoff = absolute(handoff_value)
    manifest = read_json(handoff)
    require(manifest.get('routing_policy') == 'stage-handoff-v2', '旧交接不改写新调度状态')
    require(status in {'completed', 'cancelled'} and nonempty(reason), '关闭须明确状态和依据')
    state_path = handoff.parent / 'handoff-state.json'
    previous = read_json(state_path) if state_path.exists() else {'status': 'active'}
    if previous.get('status') in {'completed', 'cancelled'}:
        require(previous['status'] == status, '交接终态不可改写或复活')
        return previous
    if status == 'completed':
        result = verify(str(handoff))
        require(result['result_status'] == 'draft_complete', '未完成草稿不能关闭为completed')
    value = {'status': status, 'reason': reason, 'closed_at': datetime.now(timezone.utc).isoformat()}
    if status == 'completed':
        # Freeze the exact accepted draft identities before root materializes
        # official outputs. This is provenance, not a semantic-pass claim.
        value['artifact_sha256'] = result['artifact_sha256']
    (handoff.parent / 'handoff-state.json').write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return value


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def read_json(path):
    with path.open(encoding="utf-8-sig") as stream:
        value = json.load(stream)
    require(isinstance(value, dict), f"JSON 必须为对象：{path}")
    return value


def absolute(value, *, directory=False):
    require(nonempty(value), "需要非空绝对路径")
    path = Path(value)
    require(path.is_absolute(), f"需要绝对路径：{value}")
    require(path.is_dir() if directory else path.is_file(), f"路径不存在或类型错误：{value}")
    return path.resolve()


def relative(value):
    require(nonempty(value), "相对文件名不能为空")
    name = value.replace("\\", "/")
    parts = name.split("/")
    require(not PureWindowsPath(value).drive and not name.startswith("/")
            and all(p not in ("", ".", "..") and not any(c in p for c in ':<>"|?*\0')
                    and not p.endswith((".", " ")) for p in parts),
            f"不安全的相对文件名：{value}")
    return Path(*parts)


def within(path, parent):
    return path == parent or parent in path.parents


def rule_file(value):
    path = (SKILL_ROOT / relative(value)).resolve()
    require(within(path, SKILL_ROOT) and path.is_file(), f"规则文件缺失或越界：{value}")
    return path


def configuration():
    config = read_json(rule_file("agents/model-routing.json"))
    require(type(config.get("schema_version")) is int and config["schema_version"] == 1,
            "model-routing schema_version 必须为 1")
    profiles, stages, escalation = (config.get(k) for k in ("profiles", "stages", "escalation"))
    require(isinstance(profiles, dict) and {"creative", "operations", "specialist"} <= set(profiles), "profiles 格式错误")
    for name, profile in profiles.items():
        require(nonempty(name) and isinstance(profile, dict) and nonempty(profile.get("model"))
                and isinstance(profile.get("reasoning_effort"), str)
                and profile["reasoning_effort"] in EFFORTS, f"profile 配置错误：{name}")
    require(isinstance(stages, dict) and set(stages) == STAGES, "stages 格式错误")
    for stage, entry in stages.items():
        require(isinstance(entry, dict) and nonempty(entry.get("profile")) and entry["profile"] in profiles
                and isinstance(entry.get("references"), list), f"stage 配置错误：{stage}")
        for reference in entry["references"]:
            rule_file(reference)
    require(isinstance(escalation, dict) and nonempty(escalation.get("profile")) and escalation["profile"] in profiles
            and type(escalation.get("max_attempts")) is int and escalation["max_attempts"] == 1
            and isinstance(escalation.get("eligible_stages"), list)
            and all(isinstance(stage, str) and stage in stages for stage in escalation["eligible_stages"])
            and len(set(escalation["eligible_stages"])) == len(escalation["eligible_stages"]), "escalation 配置错误")
    scheduling = config.get("scheduling", {"fork_turns": "none"})
    require(isinstance(scheduling, dict) and scheduling.get("fork_turns", "none") == "none", "fork_turns 必须为 none")
    require(config.get('handoff_policy') in (None, 'stage-handoff-v2'), '未知handoff_policy')
    if config.get('handoff_policy') == 'stage-handoff-v2':
        limit = scheduling.get('max_active_design_groups', 2)
        require(type(limit) is int and limit >= 1, '设计组并发上限须为正整数')
    return config


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def output_names(value, field="expected_outputs"):
    require(isinstance(value, list) and bool(value), f"{field} 必须为非空列表")
    names = [relative(item).as_posix() for item in value]
    require("result.json" in value, f"{field} 必须包含根级 result.json")
    require(len({name.casefold() for name in names}) == len(names), f"{field} 不能重复")
    return names


def task_key(brief):
    require(isinstance(brief, dict) and isinstance(brief.get("scope"), dict)
            and nonempty(brief["scope"].get("task_key")), "scope 必须为包含非空 task_key 的对象")
    return brief["scope"]["task_key"]


def verify_files(output, names):
    for name in names:
        path = (output / relative(name)).resolve()
        require(within(path, output) and path.is_file() and path.stat().st_size > 0,
                f"产物缺失、为空或链接越界：{name}")


def prepare(project_value, stage, brief_value, escalation_reason=None):
    project = absolute(project_value, directory=True)
    installed = (Path.home() / ".codex" / "skills" / "seedance-scene-director").resolve()
    require(not any(within(project, root) for root in (SKILL_ROOT, installed)), "项目不能位于 Skill 包内")
    config = configuration()
    require(stage in config["stages"], f"未知阶段：{stage}")
    profile = config["stages"][stage]["profile"]
    if escalation_reason is not None:
        require(nonempty(escalation_reason), "升级必须提供非空理由")
        require(stage in config["escalation"]["eligible_stages"], "此阶段不允许升级 specialist")
        profile = config["escalation"]["profile"]
    brief_path = absolute(brief_value)
    brief = read_json(brief_path)
    require(nonempty(brief.get("objective")) and nonempty(brief.get("authorization")), "objective 与 authorization 必须为非空文字")
    task_key(brief)
    require(isinstance(brief.get("inputs"), list) and brief["inputs"], "inputs 必须为非空绝对文件路径列表")
    require("context_notes" not in brief or isinstance(brief["context_notes"], str), "context_notes 必须为文字")
    names = output_names(brief.get("expected_outputs"))
    modern = config.get('handoff_policy') == 'stage-handoff-v2'
    review_stage = brief.get('review_stage')
    if modern and stage == 'review':
        require(review_stage in STAGES - {'review', 'sync'}, 'review须明确review_stage以加载被审阶段规则')
    elif review_stage is not None:
        require(stage == 'review', 'review_stage只能用于review')
    group, group_paths = group_inputs(brief, stage) if modern else (None, [])
    paths = [absolute(value) for value in brief["inputs"]] + [brief_path]
    paths += group_paths
    session = None
    if modern and (project / 'session.json').exists():
        session = {'path': str(project / 'session.json'),
                   'sha256': object_digest(session_creative_snapshot(read_json(project / 'session.json')))}
        paths = [path for path in paths if path != project / 'session.json']
    paths += [project / name for name in (('video-style.txt', 'project-contract.json') if modern else ('session.json', 'video-style.txt')) if (project / name).exists()]
    references = config['stages'][review_stage if modern and stage == 'review' else stage]['references']
    common = ['references/execution-contract.md', 'agents/model-routing.json'] if modern else ['SKILL.md', 'agents/model-routing.json', 'references/model-routing.md']
    paths += [rule_file(name) for name in common + references]
    records = [{"path": str(path), "sha256": digest(path)} for path in dict.fromkeys(paths)]
    base = project / "agents"
    require(within(base.resolve(), project)
            and not any(within(base.resolve(), root) for root in (SKILL_ROOT, installed)),
            "agents 目录不能链接到项目外或 Skill 包内")
    identifier = str(uuid.uuid4())
    folder = base / identifier
    output = folder / "output"
    requested = {key: config["profiles"][profile][key] for key in ("model", "reasoning_effort")}
    requested["fork_turns"] = "none"
    manifest = {"schema_version": 1, "id": identifier, "stage": stage, "profile": profile,
                "project": str(project), "requested": requested, "observed_model": None,
                "escalation_reason": escalation_reason, "brief": brief, "inputs": records,
                "expected_outputs": names, "output_dir": str(output)}
    if modern:
        manifest.update(routing_policy='stage-handoff-v2', prepared_at=datetime.now(timezone.utc).isoformat(),
                        session_creative=session, design_group=group, review_stage=review_stage)
        check_reservations(base, stage, group, config.get('scheduling', {}).get('max_active_design_groups', 2))
    # Validate everything before reserving a unique directory. Never overwrite a prior handoff.
    base.mkdir(exist_ok=True)
    folder.mkdir()
    output.mkdir()
    handoff = folder / "handoff.json"
    with handoff.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    if modern:
        (folder / 'handoff-state.json').write_text(json.dumps({'status': 'active'}, ensure_ascii=False), encoding='utf-8')
    task = ("你是已分派的执行子代理，直接完成本阶段，不再分派或创建子代理；升级与缺失上下文返回主代理。"
            f"读取交接清单 {handoff} 及其中全部规则/输入，核对范围和当前授权。"
            f"只把候选产物写入 {output}，不得直接提交、修改源项目/台账/队列或进行云端写入。"
            "authorization 仅描述现有授权，不产生新授权。材料缺失或冲突时明确报告，不能猜测补齐。"
            "交付时写根级 result.json，包含 status、scope.task_key、artifacts（含 result.json）、依据、验证结果与待核事项；"
            "模型请求配置不能当作实际模型执行凭证。")
    return {"handoff": str(handoff), **requested, "task": task}


def verify(handoff_value):
    handoff = absolute(handoff_value)
    manifest = read_json(handoff)
    require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1,
            "handoff schema_version 必须为 1")
    if manifest.get('routing_policy') == 'stage-handoff-v2':
        state_path = handoff.parent / 'handoff-state.json'
        if state_path.exists():
            require(read_json(state_path).get('status') != 'cancelled', '已取消交接不可接受为有效草稿')
        require(bool(manifest.get('session_creative')) == (Path(manifest['project']) / 'session.json').is_file(),
                '项目绑定存在性已改变，须重新交接')
    require(isinstance(manifest.get("inputs"), list) and manifest["inputs"], "handoff 缺少输入记录")
    output = handoff.parent / "output"
    require(manifest.get("output_dir") == str(output) and output.is_dir() and output.resolve() == output,
            "output_dir 必须为 handoff 同目录中的实际 output 目录")
    for record in manifest["inputs"]:
        require(isinstance(record, dict), "输入记录格式错误")
        path = absolute(record.get("path"))
        require(digest(path) == record.get("sha256"), f"输入已改变，须重新交接：{path}")
    session = manifest.get('session_creative')
    if session:
        require(object_digest(session_creative_snapshot(read_json(absolute(session['path'])))) == session['sha256'],
                '项目创作输入已改变，须重新交接')
    names = output_names(manifest.get("expected_outputs"))
    verify_files(output, names)
    result = read_json(output / "result.json")
    require(isinstance(result.get("status"), str)
            and result["status"] in {"draft_complete", "needs_input", "failed"}, "result.json status 无效")
    require(task_key(result) == task_key(manifest.get("brief")), "result.json scope.task_key 与交接任务不符")
    artifacts = output_names(result.get("artifacts"), "artifacts")
    verify_files(output, artifacts)
    group = manifest.get('design_group')
    group_report = None
    if group and result['status'] == 'draft_complete':
        group_report = design_workspace_module().verify_group(
            read_json(Path(group['baseline_path'])), read_json(Path(group['plan_path'])),
            group['group_id'], read_json(output / relative(group['submission'])))
        group_report = {'status': group_report['status'], 'group_id': group['group_id'],
                        'owned_asset_ids': group['owned_asset_ids'],
                        'report_sha256': object_digest(group_report), 'semantic_review': 'not_performed'}
    return {"status": "artifacts_present", "result_status": result["status"], "handoff": str(handoff),
            "artifact_sha256": {name: digest(output / name) for name in dict.fromkeys([*names, *artifacts])},
            "semantic_review": "not_performed", "submitted": False, **({'design_group_check': group_report} if group else {})}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--project", required=True)
    prep.add_argument("--stage", choices=sorted(STAGES), required=True)
    prep.add_argument("--brief", required=True)
    prep.add_argument("--escalation-reason")
    check = sub.add_parser("verify")
    check.add_argument("--handoff", required=True)
    closed = sub.add_parser('close')
    closed.add_argument('--handoff', required=True)
    closed.add_argument('--status', choices=('completed', 'cancelled'), required=True)
    closed.add_argument('--reason', required=True)
    ready = sub.add_parser('bind-ready')
    ready.add_argument('--project', required=True)
    args = parser.parse_args()
    try:
        result = (assert_bind_ready(args.project) if args.command == 'bind-ready' else
                  prepare(args.project, args.stage, args.brief, args.escalation_reason)
                  if args.command == "prepare" else close(args.handoff, args.status, args.reason)
                  if args.command == 'close' else verify(args.handoff))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
