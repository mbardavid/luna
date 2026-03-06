#!/usr/bin/env python3
"""Render a scorecard for the Luna X growth canary from baseline + snapshot artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _post_score(post: dict[str, Any]) -> float:
    metrics = post.get("metrics") or {}
    replies = int(metrics.get("replies") or 0)
    reposts = int(metrics.get("reposts") or 0)
    likes = int(metrics.get("likes") or 0)
    views = int(metrics.get("views") or 0)
    return replies * 2 + reposts * 2 + likes + (views / 100.0)


def _guardrail_flags(snapshot: dict[str, Any], delta_followers: int) -> list[str]:
    flags: list[str] = []
    prohibited_markers = {
        "guaranteed": "guaranteed-returns-language",
        "100x": "hype-language-100x",
        "giveaway": "giveaway-language",
        "dm me": "dm-solicitation",
        "pump": "pump-language",
    }
    posts = snapshot.get("recent_posts") or []
    for post in posts:
        text = str(post.get("text") or "").lower()
        for marker, flag in prohibited_markers.items():
            if marker in text and flag not in flags:
                flags.append(flag)
    profile = snapshot.get("profile") or {}
    following_current = int(profile.get("following") or 0)
    following_baseline = int((snapshot.get("baseline_profile") or {}).get("following") or profile.get("following") or 0)
    if delta_followers <= 0 and (following_current - following_baseline) >= 25:
        flags.append("possible-follow-farming")
    if str(snapshot.get("session_state") or "") != "ok":
        flags.append("session-not-ready")
    return flags


def _suggested_action(delta_followers: int, flags: list[str], snapshot: dict[str, Any]) -> str:
    session_state = str(snapshot.get("session_state") or "unknown")
    if session_state != "ok":
        return "steering"
    if flags:
        return "steering"
    if delta_followers < 0:
        return "adjust"
    if delta_followers == 0:
        return "adjust"
    return "continue"


def _recent_theme_list(snapshot: dict[str, Any]) -> list[str]:
    themes = snapshot.get("recent_themes") or []
    return [str(item).strip() for item in themes if str(item).strip()][:5]


def build_scorecard(baseline: dict[str, Any], snapshot: dict[str, Any], *, baseline_path: Path, snapshot_path: Path) -> dict[str, Any]:
    baseline_profile = baseline.get("profile") or {}
    snapshot_profile = snapshot.get("profile") or {}
    followers_baseline = int(baseline_profile.get("followers") or 0)
    followers_current = int(snapshot_profile.get("followers") or 0)
    following_baseline = int(baseline_profile.get("following") or 0)
    following_current = int(snapshot_profile.get("following") or 0)
    delta_followers = followers_current - followers_baseline
    delta_following = following_current - following_baseline
    posts = list(snapshot.get("recent_posts") or [])
    posts.sort(key=_post_score, reverse=True)
    top_posts = posts[:3]
    bottom_posts = list(reversed(posts[-2:])) if posts else []
    format_counts = Counter(str(post.get("format") or "unknown") for post in posts)
    snapshot = dict(snapshot)
    snapshot["baseline_profile"] = baseline_profile
    flags = _guardrail_flags(snapshot, delta_followers)
    payload = {
        "generated_at": _now_iso(),
        "account": snapshot.get("account") or baseline.get("account") or {},
        "session_state": str(snapshot.get("session_state") or baseline.get("session_state") or "unknown"),
        "baseline_path": str(baseline_path),
        "snapshot_path": str(snapshot_path),
        "followers_baseline": followers_baseline,
        "followers_current": followers_current,
        "net_followers_delta": delta_followers,
        "following_baseline": following_baseline,
        "following_current": following_current,
        "net_following_delta": delta_following,
        "recent_themes": _recent_theme_list(snapshot),
        "recent_formats": dict(format_counts),
        "top_posts": top_posts,
        "bottom_posts": bottom_posts,
        "guardrail_flags": flags,
        "suggested_action": _suggested_action(delta_followers, flags, snapshot),
        "analytics": snapshot.get("analytics") or {},
    }
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    account = payload.get("account") or {}
    handle = str(account.get("handle") or "(unknown)").strip()
    top_posts = payload.get("top_posts") or []
    bottom_posts = payload.get("bottom_posts") or []
    recent_formats = payload.get("recent_formats") or {}
    flags = payload.get("guardrail_flags") or []
    lines = [
        "# Luna X Growth Scorecard",
        f"Generated: {payload.get('generated_at')}",
        "",
        "## Snapshot",
        f"- Account: `{handle}`",
        f"- Session: `{payload.get('session_state')}`",
        f"- Followers: {payload.get('followers_current')} (baseline {payload.get('followers_baseline')}, delta {payload.get('net_followers_delta'):+d})",
        f"- Following: {payload.get('following_current')} (baseline {payload.get('following_baseline')}, delta {payload.get('net_following_delta'):+d})",
        f"- Suggested action: `{payload.get('suggested_action')}`",
        "",
        "## Themes",
        f"- {', '.join(payload.get('recent_themes') or ['(none)'])}",
        "",
        "## Formats",
    ]
    if recent_formats:
        lines.extend(f"- `{name}`: {value}" for name, value in sorted(recent_formats.items()))
    else:
        lines.append("- No recent posts captured.")
    lines.extend(["", "## Top Posts"])
    if top_posts:
        lines.extend(
            f"- score={_post_score(post):.2f} | {str(post.get('text') or '').strip()[:160]}"
            for post in top_posts
        )
    else:
        lines.append("- No recent posts captured.")
    lines.extend(["", "## Bottom Posts"])
    if bottom_posts:
        lines.extend(
            f"- score={_post_score(post):.2f} | {str(post.get('text') or '').strip()[:160]}"
            for post in bottom_posts
        )
    else:
        lines.append("- No recent posts captured.")
    lines.extend(["", "## Guardrails"])
    if flags:
        lines.extend(f"- `{flag}`" for flag in flags)
    else:
        lines.append("- No guardrail flags.")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Path to baseline JSON artifact")
    parser.add_argument("--snapshot", required=True, help="Path to current profile snapshot JSON artifact")
    parser.add_argument("--output", required=True, help="Path to output JSON scorecard")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_path = Path(args.baseline).expanduser().resolve()
    snapshot_path = Path(args.snapshot).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    baseline = _load_json(baseline_path)
    snapshot = _load_json(snapshot_path)
    payload = build_scorecard(baseline, snapshot, baseline_path=baseline_path, snapshot_path=snapshot_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path = output_path.with_suffix(".md")
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
