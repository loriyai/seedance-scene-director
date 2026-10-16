"""Fictional test fixture approvals only. Production never auto-approves a review."""
import director as d
import review_gate


def approve_fixture(args, state):
    request = d.read_json(args.request)
    if any(item['request_id'] == request.get('request_id') for item in state.get('receipts', [])):
        return args  # Replay deliberately exercises the original receipt path.
    warnings = d.validate(state, request)
    report = review_gate.pending(state, request, warnings)
    report.update(status='passed', reviewer='offline fictional fixture')
    for check in report['checks'].values():
        check.update(status='passed', basis='Reviewed the controlled fictional test fixture.')
    for item in report['warning_decisions']:
        item.update(decision='accepted', basis='Intentional controlled fixture condition.')
    for shot in report['shot_reviews']:
        shot.update(status='passed', basis='Reviewed the controlled fictional shot fixture, source and state.')
    args.review = args.request.with_suffix('.review.json')
    d.write_json(args.review, report)
    return args
