import sys
from pathlib import Path

_workspace = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_workspace / 'scripts'))

from bootstrap_luna_x_growth_canary import card_specs


def test_card_specs_define_active_project_and_m0() -> None:
    specs = card_specs()
    project = specs['project']
    m0 = specs['milestones'][0]

    assert project['fields']['mc_card_type'] == 'project'
    assert project['fields']['mc_dispatch_policy'] == 'human_hold'
    assert project['fields']['mc_outcome_ref'].endswith('scorecard-latest.json')
    assert m0['fields']['mc_generation_key'] == 'luna-x-growth-m0'
    assert m0['fields']['mc_chairman_state'] == 'active'


def test_card_specs_include_m0_seed_commands() -> None:
    specs = card_specs()
    analytics_workstream = next(item for item in specs['workstreams'] if item['fields']['mc_generation_key'] == 'luna-x-growth-m0-ws3')
    seeds = analytics_workstream['fields']['mc_task_seed_spec']

    assert any(seed['qa_checks'] == 'bash scripts/luna_x_session_recover.sh' for seed in seeds)
    assert any(seed['qa_checks'] == 'bash scripts/luna_x_growth_baseline.sh' for seed in seeds)
    assert any(seed['qa_checks'] == 'bash scripts/luna_x_growth_daily.sh' for seed in seeds)

    positioning_workstream = next(item for item in specs['workstreams'] if item['fields']['mc_generation_key'] == 'luna-x-growth-m0-ws1')
    positioning_seeds = positioning_workstream['fields']['mc_task_seed_spec']
    assert any(seed['expected_artifacts'] == 'docs/luna-x-growth-charter.md' for seed in positioning_seeds)
    assert any('content-pillars.md' in seed['expected_artifacts'] for seed in positioning_seeds)
