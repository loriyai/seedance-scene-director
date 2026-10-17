"""Bind a human/agent semantic review to the exact final request and context.

prepare creates a PENDING checklist, never an approval. The reviewer completes
it after reading the request, source and diagnostics. No audio/media required.
"""
import argparse
from pathlib import Path
import sys

import director as d

CHECKS = ('source_coverage', 'fragment_timing', 'continuity', 'staging_performance')
POLICY = 'final-request-review-v2'


def digest(value):
    return d.sha(d.packed(value))


def context_digest(state):
    return digest({key: state.get(key) for key in
                   ('files', 'drafts', 'records', 'ledger', 'style_hash', 'entry', 'units', 'ranges')})


def warning_records(warnings):
    return {digest(w): w for w in warnings}


def accepted_authoring_warnings(state, request):
    """Reuse exact, already supplied writer decisions, never invent approval.

    The independent shot review and all aggregate verdicts stay pending. A
    source, speech/action plan or warning change must pass readiness again.
    """
    from authoring_gates import audio_ready, POLICY as WORKFLOW_POLICY
    from storyboard_preflight import check_audio_plan
    from shot_dependencies import scope_warning
    accepted = {}
    for index, unit in enumerate(request.get('units', [])):
        if unit.get('workflow_policy') != WORKFLOW_POLICY:
            continue
        result = audio_ready(state, unit, check_audio_plan(unit))
        if result['status'] != 'ready':
            continue
        key = unit.get('id', f'unit-{index + 1}')
        for item in unit.get('audio_preflight', {}).get('warning_decisions', []):
            warning = scope_warning(dict(item['warning'], unit=key), request.get('units', []))
            accepted[digest(warning)] = {'warning_sha256': digest(warning),
                                        'warning': warning, 'decision': 'accepted',
                                        'basis': item['basis']}
    return accepted


def pending(state, request, warnings):
    from shot_review import targets
    accepted = accepted_authoring_warnings(state, request)
    return {'policy': POLICY, 'request_sha256': digest(request), 'context_sha256': context_digest(state),
            'status': 'pending', 'reviewer': '',
            'checks': {key: {'status': 'pending', 'basis': ''} for key in CHECKS},
            'shot_reviews': targets(state, request),
            'warning_decisions': [accepted.get(key, {'warning_sha256': key, 'warning': value, 'decision': 'pending', 'basis': ''})
                                  for key, value in warning_records(warnings).items()]}


def verify(state, request, report, warnings):
    d.require(isinstance(report, dict) and report.get('policy') == POLICY, '缺少最终稿语义复核记录')
    d.require(report.get('request_sha256') == digest(request), '复核后的草稿已改变，须复核最终版本')
    d.require(report.get('context_sha256') == context_digest(state), '复核上下文已变化，须重新核对来源与相邻单元')
    d.require(report.get('status') == 'passed' and isinstance(report.get('reviewer'), str)
              and report['reviewer'].strip(), '最终稿复核尚未通过或缺少复核者')
    checks = report.get('checks')
    d.require(isinstance(checks, dict), '缺少逐项语义复核')
    for key in CHECKS:
        item = checks.get(key, {})
        d.require(isinstance(item, dict) and item.get('status') == 'passed'
                  and isinstance(item.get('basis'), str) and item['basis'].strip(), f'语义复核未完成：{key}')
    decisions = report.get('warning_decisions')
    d.require(isinstance(decisions, list) and all(isinstance(item, dict) for item in decisions), '警告处置记录无效')
    keys = [item.get('warning_sha256') for item in decisions]
    d.require(all(isinstance(key, str) for key in keys) and len(keys) == len(set(keys))
              and set(keys) == set(warning_records(warnings)), '警告清单已变化或未逐项复核')
    for item in decisions:
        d.require(item.get('warning') == warning_records(warnings)[item['warning_sha256']],
                  '警告原对象与当前检查不一致；不能仅保留旧摘要')
        d.require(item.get('decision') in ('resolved', 'accepted') and isinstance(item.get('basis'), str)
                  and item['basis'].strip(), '检查警告须逐项说明修正结果或保留依据')
    from scene_review import verify_reuse_evidence
    verify_reuse_evidence(state, request, report, warnings)
    from shot_review import verify_targets
    verify_targets(state, request, report.get('shot_reviews'), d.require)
    return {'policy': POLICY, 'status': 'passed', 'request_sha256': digest(request),
            'context_sha256': context_digest(state), 'report_sha256': digest(report),
            'reviewer': report['reviewer'], 'checks': checks, 'warning_decisions': decisions,
            'shot_reviews': report['shot_reviews']}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'verify'))
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--require-workflow', action='store_true')
    parser.add_argument('--partial', action='store_true', help='prepare a draft-prefix review only; never verify a partial batch')
    args = parser.parse_args()
    try:
        state, request = d.load(args.project), d.read_json(args.request)
        if args.require_workflow:
            d.require_workflow(request)
        d.require(not args.partial or args.command == 'prepare', '--partial仅用于准备场次草稿审阅；最终verify必须完整检查')
        warnings = d.validate(state, request, partial=args.partial)
        if args.command == 'prepare':
            # Exclusive creation keeps prior review evidence intact.
            target = args.report.resolve()
            d.require(target != d.ROOT and d.ROOT not in target.parents, '复核记录不能写入Skill包内')
            report = pending(state, request, warnings)
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open('x', encoding='utf-8') as stream:
                import json
                json.dump(report, stream, ensure_ascii=False, indent=2)
            result = {'status': 'review_pending', 'report': str(args.report), 'request_sha256': digest(request)}
        else:
            result = verify(state, request, d.read_json(args.report), warnings)
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
