#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
MC_CONFIG_PATH_DEFAULT_LOCAL="${SCRIPT_DIR}/../config/mission-control-ids.local.json"
MC_CONFIG_PATH_DEFAULT_VERSIONED="${SCRIPT_DIR}/../config/mission-control-ids.json"
MC_CONFIG_PATH="${MC_CONFIG_PATH:-$MC_CONFIG_PATH_DEFAULT_LOCAL}"
if [ ! -f "$MC_CONFIG_PATH" ] && [ -f "$MC_CONFIG_PATH_DEFAULT_VERSIONED" ]; then
  MC_CONFIG_PATH="$MC_CONFIG_PATH_DEFAULT_VERSIONED"
fi

OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
DISCORD_CHANNEL="${MC_COST_CHANNEL:-1473367119377731800}"
DAYS_BACK="${MC_COST_DAYS_BACK:-7}"
DRY_RUN="${MC_COST_DRYRUN:-0}"

exec python3 - "$MC_CONFIG_PATH" "$OPENCLAW_BIN" "$DISCORD_CHANNEL" "$DAYS_BACK" "$DRY_RUN" "$@" <<'PY'
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib import request

cfg_path, openclaw_bin, default_channel, days_back_s, dry_run_s = sys.argv[1:6]
argv = sys.argv[6:]

days_back = int(days_back_s)
dry_run = dry_run_s == "1"

parser = argparse.ArgumentParser()
parser.add_argument("--channel", default=default_channel)
parser.add_argument("--limit", type=int, default=500)
parser.add_argument("--verbose", action="store_true")
args = parser.parse_args(argv)

cfg = json.load(open(cfg_path, "r", encoding="utf-8"))
api_url = str(cfg.get("api_url", "")).rstrip("/")
board_id = cfg.get("board_id")
public_url = str(cfg.get("public_url", "")).rstrip("/")

token = os.environ.get("MC_AUTH_TOKEN") or cfg.get("auth_token")
if not token or "REPLACE_ME" in str(token):
  raise SystemExit("missing MC_AUTH_TOKEN/auth_token")

headers = {
  "Authorization": f"Bearer {token}",
  "Accept": "application/json",
}


def http_json(path: str):
  url = f"{api_url}{path}"
  req = request.Request(url, headers=headers)
  with request.urlopen(req, timeout=30) as resp:
    raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def fetch_tasks(status: str):
  data = http_json(f"/boards/{board_id}/tasks?status={status}&limit={args.limit}")
  return data.get("items", []) if isinstance(data, dict) else []


cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

rows = []
# Backend currently supports status filter for: done, inbox, in_progress, review.
# 'failed' is not a native status filter; failures are represented via mc_last_error.
for status in ("done",):
  for t in fetch_tasks(status):
    if not isinstance(t, dict):
      continue
    updated = t.get("updated_at") or t.get("created_at")
    if not updated:
      continue
    try:
      ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
      if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
      continue
    if ts < cutoff:
      continue
    fields = t.get("custom_field_values") or {}
    cost = fields.get("mc_actual_cost_usd")
    try:
      cost = float(cost) if cost is not None else 0.0
    except Exception:
      cost = 0.0
    rows.append({
      "id": t.get("id"),
      "title": t.get("title") or "(sem título)",
      "status": status,
      "cost": cost,
    })

total = sum(r["cost"] for r in rows)
top = sorted(rows, key=lambda r: r["cost"], reverse=True)[:5]

lines = [
  f"[Mission Control] Cost report (últimos {days_back} dias)",
  f"Tasks consideradas: {len(rows)}",
  f"Custo total (mc_actual_cost_usd): ${total:.2f}",
]
if top:
  lines.append("Top 5 por custo:")
  for r in top:
    title = r["title"].strip().replace("\n", " ")
    if len(title) > 90:
      title = title[:87] + "..."
    link = f"{public_url}/boards/{board_id}?task={r['id']}" if public_url else ""
    lines.append(f"- ${r['cost']:.2f} | {title} | {r['status']}" + (f" | {link}" if link else ""))

msg = "\n".join(lines)

if dry_run:
  print(msg)
  raise SystemExit(0)

cp = subprocess.run([
  openclaw_bin,
  "message",
  "send",
  "--channel",
  "discord",
  "--target",
  str(args.channel),
  "--message",
  msg,
], text=True, capture_output=True)

if cp.returncode != 0:
  raise SystemExit(cp.stderr.strip())

print(json.dumps({"sent": True, "count": len(rows), "total": total, "channel": args.channel}, sort_keys=True))
PY
