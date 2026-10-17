"""Explicit delivery scope for newly initialized projects; legacy files stay readable."""
import json
from pathlib import Path

POLICY = 'project-delivery-v1'
SCOPES = ('assets', 'workflow', 'screenplay')


def validate_contract(value):
    if (not isinstance(value, dict) or set(value) != {'policy', 'scope', 'require_designed'}
            or value['policy'] != POLICY or value['scope'] not in SCOPES
            or type(value['require_designed']) is not bool
            or value['require_designed'] != (value['scope'] != 'screenplay')):
        raise ValueError('项目交付契约无效；须明确assets/workflow/screenplay及对应设计要求')
    return value


def initialize_project(project, scope):
    project = Path(project).resolve()
    if scope not in SCOPES:
        raise ValueError('交付范围无效')
    value = validate_contract({'policy': POLICY, 'scope': scope, 'require_designed': scope != 'screenplay'})
    path = project / 'project-contract.json'
    if path.exists():
        if read_contract(project) != value:
            raise ValueError('项目已有不同交付契约；不可静默覆盖或扩大范围')
        return {'status': 'existing_contract', 'contract': value, 'path': str(path)}
    if (project / 'session.json').exists():
        raise ValueError('旧绑定不自动补写新项目契约；须明确迁移')
    project.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return {'status': 'project_initialized', 'contract': value, 'path': str(path)}


def read_contract(project):
    path = Path(project) / 'project-contract.json'
    return validate_contract(json.loads(path.read_text(encoding='utf-8-sig'))) if path.exists() else None


def delivery_contract(data, project=None):
    value = validate_contract(data['delivery_contract']) if 'delivery_contract' in data else None
    stored = read_contract(project) if project is not None else None
    if value is not None and stored is not None and value != stored:
        raise ValueError('台账与项目交付契约不一致')
    return stored or value


def requires_design(data, *, project=None, explicit=False):
    value = delivery_contract(data, project)
    return bool(explicit or (value and value['require_designed']))
