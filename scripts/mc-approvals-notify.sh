#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CONFIG_PATH_DEFAULT_LOCAL="${SCRIPT_DIR}/../config/mission-control-ids.local.json"
MC_CONFIG_PATH_DEFAULT_VERSIONED="${SCRIPT_DIR}/../config/mission-control-ids.json"
MC_CONFIG_PATH="${MC_CONFIG_PATH:-$MC_CONFIG_PATH_DEFAULT_LOCAL}"
if [ ! -f "$MC_CONFIG_PATH" ] && [ -f "$MC_CONFIG_PATH_DEFAULT_VERSIONED" ]; then
  MC_CONFIG_PATH="$MC_CONFIG_PATH_DEFAULT_VERSIONED"
fi

OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
DISCORD_CHANNEL="${MC_APPROVALS_CHANNEL:-1473367119377731800}"
DRY_RUN="${MC_APPROVALS_DRYRUN:-0}"

exec python3 - "$MC_CONFIG_PATH" "$OPENCLAW_BIN" "$DISCORD_CHANNEL" "$DRY_RUN" "$@" <<'PY'
import argparse
import json
import os
import subprocess
import sys
from urllib import request

cfg_path, openclaw_bin, default_channel, dry_run_str = sys.argv[1:5]
argv = sys.argv[5:]

dry_run = dry_run_str == "1"

parser = argparse.ArgumentParser()
parser.add_argument("--channel", default=default_channel)
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--max", type=int, default=25)
args = parser.parse_args(argv)

with open(cfg_path, "r", encoding="utf-8") as fp:
  cfg = json.load(fp)

api_url = str(cfg.get("api_url", "")).rstrip("/")
board_id = cfg.get("board_id")
public_url = str(cfg.get("public_url", "")).rstrip("/")
token = os.environ.get("MC_AUTH_TOKEN") or cfg.get("auth_token")

if not api_url or not board_id:
  raise SystemExit("missing api_url/board_id in mission-control config")
if not token or "REPLACE_ME" in str(token):
  raise SystemExit("missing MC_AUTH_TOKEN or auth_token in config")

headers = {
  "Authorization": f"Bearer {token}",
  "Accept": "application/json",
  "Content-Type": "application/json",
}


def http_json(method: str, path: str, payload=None):
  url = f"{api_url}{path}"
  data = None if payload is None else json.dumps(payload).encode("utf-8")
  req = request.Request(url, data=data, method=method, headers=headers)
  with request.urlopen(req, timeout=20) as resp:
    raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def send_discord(text: str):
  if dry_run:
    print(f"[dry-run] would notify: {text}")
    return
  cmd = [
    openclaw_bin,
    "message",
    "send",
    "--channel",
    "discord",
    "--target",
    str(args.channel),
    "--message",
    text,
  ]
  cp = subprocess.run(cmd, text=True, capture_output=True)
  if cp.returncode != 0:
    raise RuntimeError(cp.stderr.strip())


def task_needs_notify(task: dict) -> bool:
  fields = task.get("custom_field_values") or {}
  val = fields.get("mc_approval_notified")
  if isinstance(val, bool):
    return not val
  if isinstance(val, (int, float)):
    return val == 0
  if isinstance(val, str):
    return val.strip().lower() in {"", "0", "false", "no", "off"}
  return True


def mark_task_notified(task_id: str):
  http_json(
    "PATCH",
    f"/boards/{board_id}/tasks/{task_id}",
    {
      "custom_field_values": {"mc_approval_notified": True},
      "comment": "mc-approvals-notify: approval notification sent to Discord",
    },
  )


approvals = http_json("GET", f"/boards/{board_id}/approvals?status=pending")
items = approvals.get("items") if isinstance(approvals, dict) else None
items = items or approvals if isinstance(approvals, list) else items
items = items or []

notified = 0
scanned = 0
for appr in items[: args.max]:
  if not isinstance(appr, dict):
    continue
  scanned += 1
  approval_id = appr.get("id")
  status = appr.get("status")
  reasoning = (appr.get("lead_reasoning") or "").strip()
  task_ids = appr.get("task_ids") or ([] if appr.get("task_id") is None else [appr.get("task_id")])

  for task_id in task_ids:
    if not task_id:
      continue
    task = http_json("GET", f"/boards/{board_id}/tasks/{task_id}")
    if not isinstance(task, dict):
      continue
    if not task_needs_notify(task):
      continue

    title = (task.get("title") or "(sem título)").strip()
    if len(title) > 120:
      title = title[:117] + "..."

    link = f"{public_url}/boards/{board_id}?task={task_id}" if public_url else ""
    msg = (
      f"🔔 Approval pendente no Mission Control\n"
      f"Task: {title}\n"
      f"Task ID: {task_id}\n"
      f"Approval ID: {approval_id}\n"
      f"Status: {status}\n"
      + (f"Motivo: {reasoning}\n" if reasoning else "")
      + (f"Link: {link}" if link else "")
    )

    send_discord(msg)
    mark_task_notified(task_id)
    notified += 1

print(json.dumps({"scanned": scanned, "notified": notified, "dry_run": dry_run, "channel": args.channel}, sort_keys=True))
PY
