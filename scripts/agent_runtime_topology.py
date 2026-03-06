
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / 'workspace'
OPENCLAW_CONFIG_PATH = ROOT / 'openclaw.json'
MC_CONFIG_PATH = WORKSPACE / 'config' / 'mission-control-ids.local.json'
MC_SHORT_IDS_PATH = WORKSPACE / 'config' / 'mc-agent-ids.json'

PERSISTENT_CANONICAL = [
    'main',
    'luan',
    'crypto-sage',
    'quant-strategist',
    'dispatcher',
    'cto-ops',
]
RUNTIME_WORKSPACES = {
    'main': str(ROOT / 'workspace-main'),
    'luan': str(ROOT / 'workspace-luan'),
    'crypto-sage': str(ROOT / 'workspace-crypto-sage'),
    'quant-strategist': str(ROOT / 'workspace-quant-strategist'),
    'dispatcher': str(ROOT / 'workspace-dispatcher'),
    'cto-ops': str(ROOT / 'workspace-cto-ops'),
}
MC_REQUIRED = {'main', 'luan', 'crypto-sage', 'quant-strategist', 'cto-ops'}
REQUIRED_BOOTSTRAP = [
    'AGENTS.md',
    'HEARTBEAT.md',
    'IDENTITY.md',
    'SOUL.md',
    'TOOLS.md',
    'USER.md',
    'MEMORY.md',
    'memory/active-tasks.md',
    'memory/lessons.md',
    'memory/workflow-registry.md',
]
PLACEHOLDERS = {
    'cto-ops-agent-01',
    '00000000-0000-0000-0000-000000000cto',
}
ALIASES = {
    'luna': 'main',
    'luan-dev': 'luan',
    'luan_dev': 'luan',
    'blockchain-operator': 'crypto-sage',
    'blockchain_operator': 'crypto-sage',
    'crypto_sage': 'crypto-sage',
    'quant_strategist': 'quant-strategist',
    'cto_ops': 'cto-ops',
}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def normalize_agent_name(value: object) -> str:
    text = str(value or '').strip().lower()
    if not text:
        return ''
    text = text.replace(' ', '-')
    text = ALIASES.get(text, text)
    return text.replace('_', '-')


def load_openclaw_agents() -> dict[str, str]:
    data = load_json(OPENCLAW_CONFIG_PATH)
    agents = {}
    for item in data.get('agents', {}).get('list', []):
        agent_id = normalize_agent_name(item.get('id'))
        workspace = str(item.get('workspace') or '').strip()
        if agent_id and workspace:
            agents[agent_id] = workspace
    return agents


def load_full_id_map(mc_config_path: Path | None = None) -> dict[str, str]:
    data = load_json(mc_config_path or MC_CONFIG_PATH)
    resolved: dict[str, str] = {}
    for raw_name, raw_id in (data.get('agents') or {}).items():
        name = normalize_agent_name(raw_name)
        if name and raw_id and raw_id not in PLACEHOLDERS:
            resolved[name] = str(raw_id)
    return resolved


def load_short_id_map(short_ids_path: Path | None = None, full_id_map: dict[str, str] | None = None) -> dict[str, str]:
    full_ids = full_id_map or load_full_id_map()
    data = load_json(short_ids_path or MC_SHORT_IDS_PATH)
    resolved: dict[str, str] = {}
    for raw_name, raw_id in data.items():
        name = normalize_agent_name(raw_name)
        if name and raw_id and raw_id not in PLACEHOLDERS:
            resolved[name] = str(raw_id)
    for name, full in full_ids.items():
        resolved.setdefault(name, full[:8])
    return resolved


def build_assigned_agent_lookup(
    mc_config_path: Path | None = None,
    short_ids_path: Path | None = None,
) -> dict[str, str]:
    full_ids = load_full_id_map(mc_config_path)
    short_ids = load_short_id_map(short_ids_path, full_ids)
    lookup: dict[str, str] = {}
    for raw, canonical in ALIASES.items():
        lookup[raw] = canonical
    for canonical, full in full_ids.items():
        lookup[full] = canonical
        lookup[full[:8]] = canonical
        lookup[canonical] = canonical
    for canonical, short in short_ids.items():
        lookup[short] = canonical
        lookup[canonical] = canonical
    return lookup


def resolve_assigned_agent(value: object, lookup: dict[str, str] | None = None) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    resolved_lookup = lookup or build_assigned_agent_lookup()
    if text in resolved_lookup:
        return resolved_lookup[text]
    normalized = normalize_agent_name(text)
    if normalized in resolved_lookup:
        return resolved_lookup[normalized]
    for candidate, canonical in resolved_lookup.items():
        if len(candidate) >= 8 and candidate.startswith(text):
            return canonical
    return ''


def resolve_full_id(name: object) -> str:
    return load_full_id_map().get(normalize_agent_name(name), '')


def resolve_short_id(name: object) -> str:
    full_ids = load_full_id_map()
    short_ids = load_short_id_map(full_id_map=full_ids)
    return short_ids.get(normalize_agent_name(name), '')


def resolve_workspace(name: object) -> str:
    return RUNTIME_WORKSPACES.get(normalize_agent_name(name), '')


def expected_daily_files() -> tuple[str, str]:
    if ZoneInfo is not None:
        tz = ZoneInfo('America/Sao_Paulo')
        today = datetime.now(tz).date()
    else:
        today = datetime.utcnow().date()
    yesterday = today.fromordinal(today.toordinal() - 1)
    return today.isoformat() + '.md', yesterday.isoformat() + '.md'


def validate_topology() -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    runtime = load_openclaw_agents()
    full_ids = load_full_id_map()
    short_ids = load_short_id_map(full_id_map=full_ids)
    lookup = build_assigned_agent_lookup()
    today_name, yesterday_name = expected_daily_files()

    for name in PERSISTENT_CANONICAL:
        expected_workspace = RUNTIME_WORKSPACES[name]
        actual_workspace = runtime.get(name)
        if actual_workspace != expected_workspace:
            errors.append(f'{name}: expected workspace {expected_workspace}, found {actual_workspace or "<missing>"}')
            continue
        workspace_path = Path(actual_workspace)
        if not workspace_path.exists():
            errors.append(f'{name}: workspace does not exist: {workspace_path}')
            continue
        for rel in REQUIRED_BOOTSTRAP:
            if not (workspace_path / rel).exists():
                errors.append(f'{name}: missing bootstrap file {rel}')
        if name in PERSISTENT_CANONICAL:
            memory_dir = workspace_path / 'memory'
            if memory_dir.exists():
                missing_daily = [fname for fname in (today_name, yesterday_name) if not (memory_dir / fname).exists()]
                if missing_daily:
                    errors.append(f'{name}: missing daily memory files {", ".join(missing_daily)}')

    if runtime.get('cto-ops') == str(ROOT / 'workspace' / 'agents' / 'cto-ops'):
        errors.append('cto-ops still points to legacy agents/cto-ops runtime path')
    if any(path == str(ROOT / 'workspace-ops') for path in runtime.values()):
        errors.append('workspace-ops is still referenced by active runtime')

    for name in MC_REQUIRED:
        full = full_ids.get(name, '')
        short = short_ids.get(name, '')
        if not full:
            errors.append(f'{name}: missing full MC id')
            continue
        if full in PLACEHOLDERS:
            errors.append(f'{name}: full MC id is still placeholder')
        if not short:
            errors.append(f'{name}: missing short MC id')
        elif short != full[:8]:
            errors.append(f'{name}: short MC id {short} does not match {full[:8]}')
        if resolve_assigned_agent(full, lookup) != name:
            errors.append(f'{name}: full id does not resolve back to canonical agent name')
        if short and resolve_assigned_agent(short, lookup) != name:
            errors.append(f'{name}: short id does not resolve back to canonical agent name')

    if 'cto-ops-agent-01' in json.dumps(load_json(MC_SHORT_IDS_PATH)):
        errors.append('mc-agent-ids.json still contains cto-ops-agent-01')
    if '00000000-0000-0000-0000-000000000cto' in json.dumps(load_json(MC_CONFIG_PATH)):
        errors.append('mission-control-ids.local.json still contains placeholder CTO UUID')

    allow_lookup = {item['id']: item.get('subagents', {}).get('allowAgents', []) for item in load_json(OPENCLAW_CONFIG_PATH).get('agents', {}).get('list', [])}
    for principal in ('main', 'dispatcher'):
        if 'cto-ops' not in allow_lookup.get(principal, []):
            errors.append(f'{principal}: allowAgents missing cto-ops')

    return {
        'ok': not errors,
        'errors': errors,
        'warnings': warnings,
        'runtime': runtime,
        'full_ids': full_ids,
        'short_ids': short_ids,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Runtime topology helper and validator.')
    sub = parser.add_subparsers(dest='command', required=True)

    p_norm = sub.add_parser('normalize')
    p_norm.add_argument('value')

    p_full = sub.add_parser('full-id')
    p_full.add_argument('value')

    p_short = sub.add_parser('short-id')
    p_short.add_argument('value')

    p_workspace = sub.add_parser('workspace')
    p_workspace.add_argument('value')

    p_assigned = sub.add_parser('assigned-agent')
    p_assigned.add_argument('value')

    p_validate = sub.add_parser('validate')
    p_validate.add_argument('--json', action='store_true')

    p_dump = sub.add_parser('dump')
    p_dump.add_argument('--json', action='store_true')

    args = parser.parse_args()

    if args.command == 'normalize':
        value = normalize_agent_name(args.value)
        if not value:
            return 1
        print(value)
        return 0
    if args.command == 'full-id':
        value = resolve_full_id(args.value)
        if not value:
            return 1
        print(value)
        return 0
    if args.command == 'short-id':
        value = resolve_short_id(args.value)
        if not value:
            return 1
        print(value)
        return 0
    if args.command == 'workspace':
        value = resolve_workspace(args.value)
        if not value:
            return 1
        print(value)
        return 0
    if args.command == 'assigned-agent':
        value = resolve_assigned_agent(args.value)
        if not value:
            return 1
        print(value)
        return 0
    if args.command == 'dump':
        payload = {
            'runtime': load_openclaw_agents(),
            'full_ids': load_full_id_map(),
            'short_ids': load_short_id_map(),
        }
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0
    if args.command == 'validate':
        report = validate_topology()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            if report['ok']:
                print('OK')
            for item in report['errors']:
                print(f'ERROR: {item}')
            for item in report['warnings']:
                print(f'WARN: {item}')
        return 0 if report['ok'] else 1
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
