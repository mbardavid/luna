#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${MC_CLIENT:-${SCRIPT_DIR}/mc-client.sh}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
DELIVER_CHANNEL="${MC_DELIVER_CHANNEL:-1476255906894446644}"
DRYRUN_FLAG="${MC_DELIVERY_DRYRUN:-0}"
MESSAGE_TIMEOUT="${MC_MESSAGE_TIMEOUT:-8}"

exec python3 - "$MC_CLIENT" "$OPENCLAW_BIN" "$DELIVER_CHANNEL" "$DRYRUN_FLAG" "$MESSAGE_TIMEOUT" "$@" <<'PY'
import argparse
import json
import subprocess
import sys
import re

mc_client_path = sys.argv[1]
openclaw_bin = sys.argv[2]
default_channel = sys.argv[3]
dryrun_env = sys.argv[4]
message_timeout = float(sys.argv[5]) if sys.argv[5] else 8.0
argv = sys.argv[6:]

parser = argparse.ArgumentParser()
parser.add_argument("--max-to-deliver", type=int, default=50)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--channel", default=default_channel)
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--status", default="done")
args = parser.parse_args(argv)

if dryrun_env == "1":
  args.dry_run = True


def run(cmd):
  cp = subprocess.run(cmd, text=True, capture_output=True)
  if cp.returncode != 0:
    raise RuntimeError(f"command failed: {' '.join(cmd)}\n{cp.stdout}\n{cp.stderr}")
  return cp.stdout.strip()


def mc_list_tasks(status):
  raw = run([mc_client_path, "list-tasks", status])
  data = json.loads(raw or "{}")
  return data.get("items", []) if isinstance(data, dict) else []


def mc_update_task(task_id, status=None, fields=None, comment=None):
  if args.dry_run:
    return {"dry_run": True}

  cmd = [mc_client_path, "update-task", task_id]
  if status:
    cmd += ["--status", status]
  if comment:
    cmd += ["--comment", comment]
  if fields is not None:
    cmd += ["--fields", json.dumps(fields)]
  run(cmd)


def is_false(value):
  if isinstance(value, bool):
    return not value
  if isinstance(value, (int, float)):
    return value == 0
  if isinstance(value, str):
    return value.strip().lower() in {"", "0", "false", "no", "off"}
  return True


def summarize_text(task):
  title = (task.get("title") or "").strip()
  if len(title) > 110:
    title = title[:107] + "..."
  description = (task.get("description") or "").replace("\n", " ").strip()
  if len(description) > 220:
    description = description[:217] + "..."
  progress = task.get("custom_field_values", {}).get("mc_progress", 0)
  summary = ((task.get("custom_field_values", {}).get("mc_output_summary") or "").strip())
  if not summary:
    comments = task.get("comments") or []
    for comment in reversed(comments):
      if not isinstance(comment, dict):
        continue
      text = str(comment.get("message", comment.get("body", "")) or "")
      if not text:
        continue
      for line in text.replace("\r", "").split("\n"):
        m = re.search(r"summary=(.+)$", line.strip(), flags=re.IGNORECASE)
        if m:
          summary = m.group(1).strip()
          break
      if summary:
        break

  if summary and len(summary) > 600:
    summary = summary[:597] + "..."

  outcome_line = f"Resultado: {summary}" if summary else f"Descrição: {description}"
  return (
    f"[Mission Control] Entrega automática de tarefa concluída\n"
    f"Task: {title}\n"
    f"Task ID: {task.get('id')}\n"
    f"Progresso: {progress}%\n"
    f"{outcome_line}"
  )


def send_message(channel, text):
  if args.dry_run:
    print(f"[dry-run] would send message to channel {channel}:\n{text}")
    return True

  cmd = [
    openclaw_bin,
    "message",
    "send",
    "--channel",
    "discord",
    "--target",
    str(channel),
    "--message",
    text,
  ]
  cp = subprocess.run(cmd, text=True, capture_output=True, timeout=message_timeout)
  if cp.returncode != 0:
    raise RuntimeError(f"message send failed: {cp.stderr.strip()}")
  return True


tasks = mc_list_tasks(args.status)
pending = [
  task
  for task in tasks
  if is_false((task.get("custom_field_values") or {}).get("mc_delivered", False))
]

delivered_count = 0
failed_count = 0
scanned = 0

for task in pending[: args.max_to_deliver]:
  scanned += 1
  try:
    payload = summarize_text(task)
    send_message(args.channel, payload)
    mc_update_task(
      task.get("id"),
      status="done",
      fields={"mc_delivered": True},
      comment="mc-delivery delivered automatically",
    )
    delivered_count += 1
    if args.verbose:
      print(f"delivered={task.get('id')}")
  except Exception as exc:  # pragma: no cover
    failed_count += 1
    task_id = task.get("id")
    try:
      mc_update_task(
        task_id,
        comment=f"mc-delivery failed: {exc}",
      )
    except Exception:
      pass
    if args.verbose:
      print(f"failed={task_id} error={exc}")

print(
  json.dumps(
    {
      "scanned": scanned,
      "delivered": delivered_count,
      "failed": failed_count,
      "pending_total": len(pending),
      "dry_run": args.dry_run,
      "channel": args.channel,
    },
    sort_keys=True,
  )
)
PY
