#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${MC_CLIENT:-${SCRIPT_DIR}/mc-client.sh}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/openclaw/.openclaw/openclaw.json}"
GATEWAY_URL="${MC_GATEWAY_URL:-ws://127.0.0.1:18789}"

exec python3 - "$MC_CLIENT" "$OPENCLAW_BIN" "$OPENCLAW_CONFIG" "$GATEWAY_URL" "$@" <<'PY'
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

mc_client_path = sys.argv[1]
openclaw_bin = sys.argv[2]
openclaw_config = sys.argv[3]
gateway_url = sys.argv[4]
argv = sys.argv[5:]

parser = argparse.ArgumentParser()
parser.add_argument("--max-retries", type=int, default=int(os.environ.get("MC_MAX_RETRIES", "2")))
parser.add_argument("--stalled-minutes", type=int, default=int(os.environ.get("MC_STALLED_MINUTES", "60")))
parser.add_argument("--session-key-field", default=os.environ.get("MC_SESSION_KEY_FIELD", "mc_session_key"))
parser.add_argument("--retry-count-field", default=os.environ.get("MC_RETRY_COUNT_FIELD", "mc_retry_count"))
parser.add_argument("--active-statuses", default=os.environ.get("MC_ACTIVE_STATUSES", "in_progress,review"))
parser.add_argument("--no-stall-check", action="store_true")
parser.add_argument("--startup-recovery", action="store_true", help="Startup recovery mode: prioritize session rebind recovery over stall detection")
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--now-ms", type=int, default=0)
args = parser.parse_args(argv)

now_ms = args.now_ms or int(time.time() * 1000)
stall_threshold_ms = int(args.stalled_minutes) * 60_000
if args.startup_recovery:
  args.no_stall_check = True
active_statuses = {s.strip().lower() for s in args.active_statuses.split(",") if s.strip()}
if not active_statuses:
  active_statuses = {"in_progress", "review"}


def run(cmd):
  cp = subprocess.run(
    cmd,
    text=True,
    capture_output=True,
  )
  if cp.returncode != 0:
    raise RuntimeError(f"command failed: {' '.join(cmd)}\n{cp.stdout}\n{cp.stderr}")
  return cp.stdout.strip()


def mc_list_tasks():
  raw = run([mc_client_path, "list-tasks"])
  data = json.loads(raw or "{}")
  if isinstance(data, dict):
    return data.get("items", [])
  return []


def mc_update_task(task_id, status=None, comment=None, fields=None):
  if args.dry_run:
    return {
      "dry_run": True,
      "task_id": task_id,
      "status": status,
      "comment": comment,
      "fields": fields,
    }

  cmd = [mc_client_path, "update-task", task_id]
  if status:
    cmd += ["--status", status]
  if comment:
    cmd += ["--comment", comment]
  if fields is not None:
    cmd += ["--fields", json.dumps(fields)]
  raw = run(cmd)
  return json.loads(raw or "{}") if raw else {}


def load_gateway_token():
  env_token = os.environ.get("MC_GATEWAY_TOKEN", "").strip()
  if env_token:
    return env_token

  with open(openclaw_config, "r", encoding="utf-8") as fp:
    data = json.load(fp)
  token = data.get("gateway", {}).get("auth", {}).get("token")
  if not token:
    raise RuntimeError(f"gateway token not found in {openclaw_config}")
  return token


def gateway_call(method, params):
  params_json = json.dumps(params or {})
  cmd = [
    openclaw_bin,
    "gateway",
    "call",
    "--url",
    gateway_url,
    "--token",
    load_gateway_token(),
    "--json",
    "--params",
    params_json,
    method,
  ]
  raw = run(cmd)
  if not raw:
    return {}
  return json.loads(raw)


def sessions_list():
  response = gateway_call("sessions.list", {})
  return response


def _coerce_epoch_ms(value):
  if value is None:
    return None
  if isinstance(value, (int, float)):
    ivalue = int(value)
    # Some gateways report ms (13 digits), others use sec.
    if ivalue > 10**12:
      return ivalue
    if ivalue > 10**10:
      return ivalue * 1000
    if ivalue > 10**9:
      return ivalue * 1000
    return ivalue * 1000
  if isinstance(value, str):
    txt = value.strip()
    if txt.isdigit():
      return _coerce_epoch_ms(int(txt))
    try:
      dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
      return int(dt.timestamp() * 1000)
    except Exception:
      pass
    try:
      # Unix seconds from legacy logs
      return int(float(txt) * 1000)
    except Exception:
      return None
  return None


def session_history_last_ms(session_key):
  history = gateway_call(
    "chat.history",
    {"sessionKey": session_key, "limit": 12, "includeTools": True},
  )
  if not isinstance(history, dict):
    return None
  stamps = []
  for msg in history.get("messages", []) or []:
    if not isinstance(msg, dict):
      continue
    ts = _coerce_epoch_ms(msg.get("timestamp"))
    if ts is not None:
      stamps.append(ts)
  return max(stamps) if stamps else None


def task_progress(task):
  fields = task.get("custom_field_values") or {}
  try:
    return int(fields.get("mc_progress", 0) or 0)
  except Exception:
    return 0


def task_retry_count(task):
  fields = task.get("custom_field_values") or {}
  try:
    return int(fields.get(args.retry_count_field, 0) or 0)
  except Exception:
    return 0


def task_session_key(task):
  fields = task.get("custom_field_values") or {}
  value = fields.get(args.session_key_field)
  if value is not None and str(value).strip():
    return str(value).strip()

  # Backward compatibility: parse last sessionKey comment if field is still missing.
  for comment in task.get("comments", []) or []:
    if not isinstance(comment, dict):
      continue
    text = str(comment.get("message", comment.get("body", "")) or "").strip()
    if not text:
      continue
    match = re.search(r"sessionKey=([^\s,]+)", text, flags=re.IGNORECASE)
    if match:
      return match.group(1).strip()
  return ""


def now_iso():
  return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def maybe_log(msg):
  if args.verbose:
    print(msg)


def task_payload(task):
  fields = task.get("custom_field_values") or {}
  return {
    "id": task.get("id"),
    "status": task.get("status"),
    "title": task.get("title", "(sem título)"),
    "session_key": str(task_session_key(task)),
    "retry_count": task_retry_count(task),
    "progress": task_progress(task),
    "custom_field_values": fields,
  }


def update_counter(stats, key):
  stats[key] = stats.get(key, 0) + 1


stats = {
  "scanned": 0,
  "recovered": 0,
  "moved_to_needs_approval": 0,
  "stalled": 0,
  "unstalled": 0,
  "blocked_missing_session_key": 0,
  "errors": 0,
}


def handle_task(task, sessions_by_key):
  status = str(task.get("status", "")).lower()
  if status not in active_statuses:
    return

  t_id = task.get("id")
  if not t_id:
    return

  payload = task_payload(task)
  update_counter(stats, "scanned")

  session_key = payload["session_key"]
  current_retry = payload["retry_count"]
  progress = payload["progress"]
  fields = payload["custom_field_values"] or {}

  session_entry = sessions_by_key.get(session_key, {}) if session_key else {}

  if not session_key:
    # Missing session linkage is not recoverable automatically: we cannot
    # reconstruct the session without an explicit link step.
    update_counter(stats, "blocked_missing_session_key")
    last_error = str((fields or {}).get("mc_last_error") or "")
    if last_error != "missing_session_key":
      update_counter(stats, "stalled")
      mc_update_task(
        t_id,
        status="review",
        comment=(
          f"[mc-watchdog] {now_iso()} task sem mc_session_key. "
          "Vincule a sessão com mc-link-task-session.sh (ou re-spawn) para retomar."
        ),
        fields={
          **fields,
          args.retry_count_field: current_retry,
          "mc_progress": progress,
          "mc_last_error": "missing_session_key",
        },
      )
    return

  if not session_entry:
    if current_retry < args.max_retries:
      update_counter(stats, "recovered")
      next_retry = current_retry + 1
      mc_update_task(
        t_id,
        status="in_progress",
        comment=(
          f"[mc-watchdog] {now_iso()} sessão ausente para task {t_id}; "
          f"tentativa de recuperação #{next_retry}/{args.max_retries}."
        ),
        fields={
          args.retry_count_field: next_retry,
          "mc_progress": progress,
          "mc_last_error": "retry",
        },
      )
    else:
      last_error = str(fields.get("mc_last_error", "") or "").strip().lower()
      if last_error != "needs_approval":
        update_counter(stats, "moved_to_needs_approval")
        mc_update_task(
          t_id,
          status="review",
          comment=(
            f"[mc-watchdog] {now_iso()} sessão ausente após {current_retry} retries; "
            "requer aprovação para ação manual e retomada."
          ),
          fields={
            args.retry_count_field: current_retry,
            "mc_progress": progress,
            "mc_last_error": "needs_approval",
          },
        )
      else:
        maybe_log(f"[mc-watchdog] task {t_id} já em needs_approval, ignorando re-mark")
    return

  if args.no_stall_check:
    return

  last_activity = session_history_last_ms(session_key)
  if last_activity is None:
    last_activity = _coerce_epoch_ms(session_entry.get("updatedAt"))
  if last_activity is None:
    return

  stale_ms = now_ms - int(last_activity)
  if stale_ms >= stall_threshold_ms:
    last_error = str(fields.get("mc_last_error", "") or "").strip().lower()
    if last_error != "stalled":
      update_counter(stats, "stalled")
      mc_update_task(
        t_id,
        status="review",
        comment=(
          f"[mc-watchdog] {now_iso()} sem atividade há {int(stale_ms / 60_000)}m; "
          "marcado como stalled para revisão."
        ),
        fields={
          args.retry_count_field: current_retry,
          "mc_progress": progress,
          "mc_last_error": "stalled",
        },
      )
    else:
      maybe_log(f"[mc-watchdog] task {t_id} já marcada stalled, ignorando re-mark")
    return

  if status == "review":
    update_counter(stats, "unstalled")
    mc_update_task(
      t_id,
      status="in_progress",
      comment=(
        f"[mc-watchdog] {now_iso()} atividade recente detectada em {int(stale_ms / 1000)}s; "
        "volta para in_progress."
      ),
      fields={"mc_progress": progress},
    )


try:
  tasks = mc_list_tasks()
  session_state = sessions_list()
  session_candidates = []
  if isinstance(session_state, dict):
    session_candidates = session_state.get("sessions", [])
  elif isinstance(session_state, list):
    session_candidates = session_state
  sessions_by_key = {
    item.get("key"): item
    for item in (session_candidates or [])
    if isinstance(item, dict) and item.get("key")
  }

  for task in tasks:
    try:
      handle_task(task, sessions_by_key)
    except Exception as exc:
      update_counter(stats, "errors")
      maybe_log(f"[mc-watchdog] erro em task {task.get('id', '<sem-id>')}: {exc}")

  print(json.dumps(stats, sort_keys=True))
except Exception as exc:
  print(f"mc-watchdog failed: {exc}", file=sys.stderr)
  sys.exit(1)
PY
