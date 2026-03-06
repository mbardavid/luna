import sys
from pathlib import Path

_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir.endswith('/tests'):
    _scripts_dir = str(Path(_scripts_dir).parent / 'scripts')
sys.path.insert(0, _scripts_dir)

from luna_x_growth_scorecard import build_scorecard


def test_build_scorecard_computes_delta_and_action() -> None:
    baseline = {
        'account': {'handle': '@luna'},
        'session_state': 'ok',
        'profile': {'followers': 100, 'following': 50},
    }
    snapshot = {
        'account': {'handle': '@luna'},
        'session_state': 'ok',
        'profile': {'followers': 108, 'following': 51},
        'recent_themes': ['crypto', 'automation'],
        'recent_posts': [
            {'text': 'Calm market structure take', 'format': 'short_post', 'metrics': {'likes': 5, 'reposts': 2, 'replies': 1, 'views': 300}},
            {'text': 'Another post', 'format': 'short_post', 'metrics': {'likes': 1, 'reposts': 0, 'replies': 0, 'views': 120}},
        ],
    }

    payload = build_scorecard(
        baseline,
        snapshot,
        baseline_path=Path('/tmp/baseline.json'),
        snapshot_path=Path('/tmp/snapshot.json'),
    )

    assert payload['net_followers_delta'] == 8
    assert payload['suggested_action'] == 'continue'
    assert payload['recent_themes'] == ['crypto', 'automation']
    assert payload['top_posts'][0]['text'] == 'Calm market structure take'


def test_build_scorecard_flags_guardrail_language() -> None:
    baseline = {
        'account': {'handle': '@luna'},
        'session_state': 'ok',
        'profile': {'followers': 100, 'following': 50},
    }
    snapshot = {
        'account': {'handle': '@luna'},
        'session_state': 'ok',
        'profile': {'followers': 100, 'following': 80},
        'recent_posts': [
            {'text': 'Guaranteed 100x giveaway, DM me', 'format': 'short_post', 'metrics': {'likes': 0, 'reposts': 0, 'replies': 0, 'views': 0}},
        ],
    }

    payload = build_scorecard(
        baseline,
        snapshot,
        baseline_path=Path('/tmp/baseline.json'),
        snapshot_path=Path('/tmp/snapshot.json'),
    )

    assert 'guaranteed-returns-language' in payload['guardrail_flags']
    assert payload['suggested_action'] == 'steering'
