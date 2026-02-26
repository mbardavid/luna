#!/usr/bin/env bash
# heartbeat-check.sh — Deterministic heartbeat replacement (zero AI tokens)
#
# Replaces: OpenClaw AI heartbeat (Gemini Flash agentTurn)
# Absorbs:  mc-failure-detector.sh (dead session detection)
#
# Flow:
#   1. Gateway health check
#   2. Active hours check (São Paulo timezone)
#   3. Fetch sessions + MC tasks (one call each)
#   4. Failure detection: in_progress tasks with dead sessions → notify
#   5. If subagents active → idle (work in progress)
#   6. If tasks in_progress → idle
#   7. Pull oldest inbox task FIFO → dispatch to Luna via Discord
#
# State: /tmp/.heartbeat-check-state.json (dedup + cooldowns)
# Lock:  /tmp/.heartbeat-check.lock (flock, prevents concurrent runs)
# Log:   logs/heartbeat-check.log
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"

exec python3 - "$@" <<'PYEOF'
import json
import os
import subprocess
import sys
import time
import fcntl
from datetime import datetime, timezone

# === CONFIG ===
WORKSPACE = os.environ.get("WORKSPACE", "/home/openclaw/.openclaw/workspace")

SCRIPTS_DIR = os.path.join(WORKSPACE, "scripts")
MC_CLIENT = os.path.join(SCRIPTS_DIR, "mc-client.sh")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
OPENCLAW_CONFIG = os.environ.get("OPENCLAW_CONFIG", "/home/openclaw/.openclaw/openclaw.json")
GATEWAY_URL = os.environ.get("MC_GATEWAY_URL", "ws://127.0.0.1:18789")
DISCORD_CHANNEL = os.environ.get("HEARTBEAT_DISCORD_CHANNEL", "1473367119377731800")
STATE_FILE = os.environ.get("HEARTBEAT_STATE_FILE", "/tmp/.heartbeat-check-state.json")
LOCK_FILE = "/tmp/.heartbeat-check.lock"
LOG_DIR = os.path.join(WORKSPACE, "logs")
LOG_FILE = os.path.join(LOG_DIR, "heartbeat-check.log")

# Tuning
ACTIVE_HOUR_START = 8   # São Paulo local time
ACTIVE_HOUR_END = 24    # 00:00 (midnight)
FAILURE_COOLDOWN_MS = 30 * 60 * 1000   # 30min cooldown per failure notification
DISPATCH_TIMEOUT_MS = 2 * 60 * 60 * 1000  # 2h — re-dispatch if task still in inbox

# Dry-run support
DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv or DRY_RUN

# === SETUP ===
os.makedirs(LOG_DIR, exist_ok=True)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    if VERBOSE:
        print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_dispatched_id": "", "dispatched_at": 0, "notified_failures": {}}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"WARN: failed to save state: {e}")


# === LOCK ===
lock_fd = None
try:
    lock_fd = open(LOCK_FILE, "w")
    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except (IOError, OSError):
    log("SKIP: already running (flock)")
    sys.exit(0)


def run_cmd(cmd, timeout=30):
    try:
        cp = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        if cp.returncode != 0:
            raise RuntimeError(f"exit {cp.returncode}: {cp.stderr.strip()}")
        return cp.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timeout after {timeout}s: {' '.join(cmd[:3])}...")


def load_gateway_token():
    env_token = os.environ.get("MC_GATEWAY_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        with open(OPENCLAW_CONFIG, "r") as fp:
            data = json.load(fp)
        return data["gateway"]["auth"]["token"]
    except Exception as e:
        raise RuntimeError(f"gateway token not found: {e}")


_gw_token = None
def gateway_call(method, params=None):
    global _gw_token
    if _gw_token is None:
        _gw_token = load_gateway_token()
    params_json = json.dumps(params or {})
    cmd = [
        OPENCLAW_BIN, "gateway", "call",
        "--url", GATEWAY_URL,
        "--token", _gw_token,
        "--json", "--params", params_json,
        method,
    ]
    raw = run_cmd(cmd, timeout=15)
    return json.loads(raw) if raw else {}


def mc_list_tasks():
    raw = run_cmd([MC_CLIENT, "list-tasks"], timeout=15)
    data = json.loads(raw or "{}")
    if isinstance(data, dict):
        return data.get("items", [])
    return []


def send_discord(message):
    if DRY_RUN:
        log(f"DRY-RUN would send: {message[:100]}...")
        return True
    try:
        cmd = [
            OPENCLAW_BIN, "message", "send",
            "--channel", "discord",
            "--target", DISCORD_CHANNEL,
            "--message", message,
        ]
        run_cmd(cmd, timeout=10)
        return True
    except Exception as e:
        log(f"ERROR: Discord send failed: {e}")
        return False


# ============================================================
# PHASE 1: Gateway health check
# ============================================================
try:
    gateway_call("sessions.list", {})
except Exception as e:
    log(f"SKIP: gateway unreachable: {e}")
    sys.exit(0)
log("Gateway OK")

# ============================================================
# PHASE 2: Active hours check (São Paulo)
# ============================================================
try:
    import zoneinfo
    sp_tz = zoneinfo.ZoneInfo("America/Sao_Paulo")
except ImportError:
    import dateutil.tz as dtz
    sp_tz = dtz.gettz("America/Sao_Paulo")

sp_hour = datetime.now(sp_tz).hour
if sp_hour < ACTIVE_HOUR_START:
    log(f"SKIP: outside active hours ({sp_hour}h São Paulo)")
    sys.exit(0)
log(f"Active hours OK ({sp_hour}h São Paulo)")

# ============================================================
# PHASE 3: Fetch data (sessions + MC tasks)
# ============================================================
try:
    session_data = gateway_call("sessions.list", {})
except Exception as e:
    log(f"ERROR: sessions.list failed: {e}")
    sys.exit(1)

sessions = []
if isinstance(session_data, dict):
    sessions = session_data.get("sessions", [])
elif isinstance(session_data, list):
    sessions = session_data
sessions_by_key = {
    s.get("key", ""): s for s in sessions if isinstance(s, dict) and s.get("key")
}

try:
    tasks = mc_list_tasks()
except Exception as e:
    log(f"ERROR: MC list-tasks failed: {e}")
    sys.exit(1)

log(f"Data: {len(sessions)} sessions, {len(tasks)} tasks")

now_ms = int(time.time() * 1000)
state = load_state()

# ============================================================
# PHASE 4: Failure detection (absorbs mc-failure-detector)
# ============================================================
notified_failures = state.get("notified_failures", {})
new_failures = []

for task in tasks:
    status = str(task.get("status", "")).lower()
    if status != "in_progress":
        continue

    task_id = task.get("id", "")
    fields = task.get("custom_field_values") or {}
    session_key = str(fields.get("mc_session_key", "") or "").strip()

    if not session_key:
        continue

    # Session still active?
    if session_key in sessions_by_key:
        session = sessions_by_key[session_key]
        s_status = str(session.get("status", "")).lower()
        if s_status not in ("failed", "error", "ended"):
            continue  # Session alive — skip

    # Dead session detected — check cooldown
    prev = notified_failures.get(task_id, {})
    prev_at = prev.get("at", 0) if isinstance(prev, dict) else 0
    if now_ms - prev_at < FAILURE_COOLDOWN_MS:
        continue  # Already notified recently

    title = task.get("title", "(sem título)")
    new_failures.append({
        "task_id": task_id,
        "title": title,
        "session_key": session_key,
    })

if new_failures:
    lines = ["⚠️ **Heartbeat**: subagent(s) falharam com tasks abertas no MC:\n"]
    for f in new_failures:
        lines.append(f"• `{f['task_id'][:8]}` — **{f['title']}** (sessão: `...{f['session_key'][-12:]}`)")
        notified_failures[f["task_id"]] = {"at": now_ms, "session": f["session_key"]}
    lines.append("\nInvestigar e re-spawnar ou marcar como failed.")

    if send_discord("\n".join(lines)):
        log(f"NOTIFY: {len(new_failures)} failure(s) detected")
        state["notified_failures"] = notified_failures
        save_state(state)
    sys.exit(0)  # Don't dispatch when there are failures to address

# ============================================================
# PHASE 5: Check active subagents
# ============================================================
active_subagents = [
    s for s in sessions
    if isinstance(s, dict)
    and "subagent" in s.get("key", "")
    and s.get("status", "").lower() in ("active", "running", "")
    # Exclude sessions that have been idle too long (>30min = probably stale)
    and (now_ms - (s.get("updatedAt", 0) or 0)) < 30 * 60 * 1000
]

if active_subagents:
    labels = [s.get("label", s.get("key", "?"))[:40] for s in active_subagents[:3]]
    log(f"SKIP: {len(active_subagents)} active subagent(s): {', '.join(labels)}")
    sys.exit(0)

# ============================================================
# PHASE 6: Check in_progress tasks
# ============================================================
in_progress = [t for t in tasks if str(t.get("status", "")).lower() == "in_progress"]
if in_progress:
    titles = [t.get("title", "?")[:40] for t in in_progress[:3]]
    log(f"SKIP: {len(in_progress)} task(s) in_progress: {', '.join(titles)}")
    sys.exit(0)

# ============================================================
# PHASE 7: Pull oldest inbox task (FIFO) with filtering
# ============================================================
inbox = [t for t in tasks if str(t.get("status", "")).lower() == "inbox"]
inbox.sort(key=lambda t: t.get("created_at", ""))

# Load blocklist and dependency chain
BLOCKLIST_FILE = os.path.join(WORKSPACE, "config", "heartbeat-blocklist.json")
blocked_ids = set()
dep_chain = {}
try:
    with open(BLOCKLIST_FILE) as f:
        bl = json.load(f)
    blocked_ids = set(bl.get("blocked_task_ids", {}).keys())
    dep_chain = bl.get("dependency_chain", {})
except Exception:
    pass  # No blocklist = no filtering

# Build task status lookup for dependency checking
task_status_by_id = {t.get("id", ""): t.get("status", "").lower() for t in tasks}

# Filter inbox: remove blocked tasks and tasks with unmet dependencies
eligible = []
for t in inbox:
    tid = t.get("id", "")
    title = t.get("title", "?")

    # Check blocklist
    if tid in blocked_ids:
        log(f"FILTER: {tid[:8]} blocked (human-gate): {title[:40]}")
        continue

    # Check dependency chain (from config file)
    deps = dep_chain.get(tid, [])
    unmet = [d for d in deps if task_status_by_id.get(d, "").lower() != "done"]
    if unmet:
        unmet_short = ", ".join(d[:8] for d in unmet)
        log(f"FILTER: {tid[:8]} has unmet deps ({unmet_short}): {title[:40]}")
        continue

    # Check MC's native depends_on_task_ids
    mc_deps = t.get("depends_on_task_ids", []) or []
    mc_unmet = [d for d in mc_deps if task_status_by_id.get(d, "").lower() != "done"]
    if mc_unmet:
        unmet_short = ", ".join(d[:8] for d in mc_unmet)
        log(f"FILTER: {tid[:8]} has unmet MC deps ({unmet_short}): {title[:40]}")
        continue

    eligible.append(t)

if not eligible:
    filtered_count = len(inbox) - len(eligible)
    log(f"IDLE: no eligible inbox tasks ({len(inbox)} total, {filtered_count} filtered)")
    # Clean old failure notifications (>24h)
    cleanup_threshold = now_ms - 24 * 60 * 60 * 1000
    cleaned = {k: v for k, v in notified_failures.items()
               if isinstance(v, dict) and v.get("at", 0) > cleanup_threshold}
    if len(cleaned) != len(notified_failures):
        state["notified_failures"] = cleaned
        save_state(state)
    sys.exit(0)

next_task = eligible[0]
task_id = next_task.get("id", "")
title = next_task.get("title", "(sem título)")
description = next_task.get("description", "")[:200]

# ============================================================
# PHASE 8: Dedup — already dispatched this task?
# ============================================================
last_id = state.get("last_dispatched_id", "")
last_at = state.get("dispatched_at", 0)

if task_id == last_id and (now_ms - last_at) < DISPATCH_TIMEOUT_MS:
    elapsed_min = int((now_ms - last_at) / 60000)
    log(f"SKIP: task {task_id[:8]} already dispatched {elapsed_min}min ago")
    sys.exit(0)

# ============================================================
# PHASE 9: Dispatch to Luna via Discord
# ============================================================
# Build context-rich message for Luna
assigned = str(next_task.get("assigned_agent_id", "") or "")[:8]
depends = next_task.get("depends_on_task_ids", []) or []

msg_parts = [
    f"📋 **Heartbeat dispatch** — próxima task do inbox:",
    f"",
    f"**{title}** (`{task_id[:8]}`)",
]
if description:
    msg_parts.append(f"> {description[:150]}{'...' if len(description) > 150 else ''}")
if assigned:
    msg_parts.append(f"Agente: `{assigned}`")
if depends:
    dep_ids = ", ".join(f"`{d[:8]}`" for d in depends[:3])
    msg_parts.append(f"Depende de: {dep_ids}")

msg_parts.append(f"\nEligible: **{len(eligible)}** | Total inbox: **{len(inbox)}** task(s)")

msg = "\n".join(msg_parts)

if send_discord(msg):
    log(f"DISPATCH: {task_id[:8]} — {title}")
    state["last_dispatched_id"] = task_id
    state["dispatched_at"] = now_ms
    save_state(state)
else:
    log(f"ERROR: failed to dispatch {task_id[:8]}")
    sys.exit(1)

PYEOF
