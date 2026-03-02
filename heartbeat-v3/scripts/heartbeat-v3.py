#!/usr/bin/env python3
"""
heartbeat-v3.py — Autonomous dispatch engine with filesystem queue architecture.

Architecture: Bash Detecta, Filesystem Enfileira, Luna Consome, Bash Escala.

Replaces: heartbeat-v2.sh (heredoc Python) — separated into standalone .py file.

Flow:
  Phase 1:   Gateway health check
  Phase 2:   Active hours (8h-0h São Paulo)
  Phase 3:   Fetch sessions + MC tasks
  Phase 4:   Failure detection → queue file + system-event nudge (NOT cron one-shot)
  Phase 4.5: Circuit breaker check
  Phase 4.6: Resource check (skip if degraded/critical)
  Phase 4.7: Rate limit (max 3 dispatches/hour)
  Phase 5:   Active subagents check (max 2 concurrent)
  Phase 5.5: Stale dispatch detection (in_progress no session_key >15min)
  Phase 7:   Pull eligible inbox task (FIFO + blocklist + dependency chain)
  Phase 8:   Dedup
  Phase 9:   Dispatch → queue file + system-event nudge (NOT cron isolated)

State: /tmp/.heartbeat-check-state.json (enhanced)
Lock:  /tmp/.heartbeat-check.lock
Log:   logs/heartbeat-v3.log

Key difference from v2:
  - Phase 4 & 9 write to filesystem queue instead of creating cron one-shots
  - No more AI in the critical path of dispatch
  - Escalation is handled by a separate bash script (queue-escalation.sh)
"""

# === IMPORT VALIDATION (fail fast) ===
import sys

_required_modules = ['json', 'os', 'subprocess', 'time', 'fcntl', 'tempfile', 'pathlib']
_missing = []
for _mod in _required_modules:
    try:
        __import__(_mod)
    except ImportError:
        _missing.append(_mod)
if _missing:
    print(f"FATAL: Missing required stdlib modules: {', '.join(_missing)}", file=sys.stderr)
    print("heartbeat-v3 requires Python 3.10+ with standard library.", file=sys.stderr)
    sys.exit(1)

try:
    import zoneinfo
    _has_zoneinfo = True
except ImportError:
    _has_zoneinfo = False
    try:
        import dateutil.tz
        _has_dateutil = True
    except ImportError:
        _has_dateutil = False
        print("FATAL: Neither zoneinfo (Python 3.9+) nor python-dateutil available.", file=sys.stderr)
        print("Cannot determine São Paulo timezone for active hours check.", file=sys.stderr)
        sys.exit(1)

import json
import os
import subprocess
import time
import fcntl
import tempfile
from datetime import datetime, timezone
from pathlib import Path


# === CONFIG ===
# Resolve workspace: env var → parent of parent of this script
WORKSPACE = os.environ.get(
    "WORKSPACE",
    str(Path(__file__).resolve().parent.parent.parent)  # heartbeat-v3/scripts/../../../workspace
)

# V3-specific config
V3_DIR = os.environ.get(
    "HEARTBEAT_V3_DIR",
    str(Path(__file__).resolve().parent.parent)  # heartbeat-v3/scripts/.. → heartbeat-v3/
)
V3_CONFIG_FILE = os.path.join(V3_DIR, "config", "v3-config.json")

# Load V3 config
try:
    with open(V3_CONFIG_FILE) as f:
        V3_CONFIG = json.load(f)
except Exception as e:
    print(f"FATAL: Cannot load v3-config.json: {e}", file=sys.stderr)
    sys.exit(1)

QUEUE_DIR = V3_CONFIG.get("queue_dir", os.path.join(V3_DIR, "queue"))
QUEUE_PENDING = os.path.join(QUEUE_DIR, "pending")
QUEUE_ACTIVE = os.path.join(QUEUE_DIR, "active")
QUEUE_DONE = os.path.join(QUEUE_DIR, "done")
QUEUE_FAILED = os.path.join(QUEUE_DIR, "failed")

SCRIPTS_DIR = os.path.join(WORKSPACE, "scripts")
MC_CLIENT = os.path.join(SCRIPTS_DIR, "mc-client.sh")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
OPENCLAW_CONFIG = os.environ.get("OPENCLAW_CONFIG", "/home/openclaw/.openclaw/openclaw.json")
GATEWAY_URL = os.environ.get("MC_GATEWAY_URL", "ws://127.0.0.1:18789")
DISCORD_CHANNEL = V3_CONFIG.get("discord_channel", "1473367119377731800")
NOTIFICATIONS_CHANNEL = V3_CONFIG.get("notifications_channel", "1476255906894446644")
STATE_FILE = os.environ.get("HEARTBEAT_STATE_FILE", "/tmp/.heartbeat-check-state.json")
LOCK_FILE = "/tmp/.heartbeat-check.lock"
LOG_DIR = os.path.join(WORKSPACE, "logs")
LOG_FILE = os.path.join(LOG_DIR, "heartbeat-v3.log")

# Agent mapping file
AGENT_IDS_FILE = os.path.join(WORKSPACE, "config", "mc-agent-ids.json")
MC_CONFIG_FILE = os.path.join(WORKSPACE, "config", "mission-control-ids.local.json")

# Tuning
ACTIVE_HOUR_START = 8     # São Paulo local time
ACTIVE_HOUR_END = 24      # 00:00 (midnight)
MAX_DISPATCHES_PER_HOUR = V3_CONFIG.get("max_dispatches_per_hour", 3)
MAX_CONCURRENT_IN_PROGRESS = V3_CONFIG.get("max_concurrent_in_progress", 2)
MIN_DISPATCH_INTERVAL_MS = 5 * 60 * 1000   # 5min between dispatches
DISPATCH_TIMEOUT_MS = 2 * 60 * 60 * 1000   # 2h — re-dispatch if task still inbox
DISPATCH_STALE_MS = 15 * 60 * 1000         # 15min without session_key = stale

# Circuit breaker
CB_FAILURE_THRESHOLD = V3_CONFIG.get("circuit_breaker_max_failures", 3)
CB_WINDOW_MS = V3_CONFIG.get("circuit_breaker_cooldown_minutes", 30) * 60 * 1000
CB_COOLDOWN_MS = V3_CONFIG.get("circuit_breaker_cooldown_minutes", 30) * 60 * 1000

# Failure detection
FAILURE_COOLDOWN_MS = 30 * 60 * 1000     # 30min cooldown per failure notification
MAX_RETRIES = 2

# Dry-run support
DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv or DRY_RUN
RESET_CB = "--reset-circuit-breaker" in sys.argv


# === SETUP ===
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(QUEUE_PENDING, exist_ok=True)
os.makedirs(QUEUE_ACTIVE, exist_ok=True)
os.makedirs(QUEUE_DONE, exist_ok=True)
os.makedirs(QUEUE_FAILED, exist_ok=True)
os.makedirs(os.path.join(QUEUE_DIR, "escalated"), exist_ok=True)


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    if VERBOSE:
        print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_dispatched_id": "",
        "dispatched_at": 0,
        "notified_failures": {},
        "dispatch_history": [],
        "circuit_breaker": {
            "state": "closed",
            "failures": 0,
            "last_failure_at": 0,
            "opened_at": 0,
        },
    }


def save_state(state: dict) -> None:
    """Atomic write: write to temp file then rename."""
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(STATE_FILE), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except Exception as e:
        log(f"WARN: failed to save state: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def ensure_state_fields(state: dict) -> dict:
    """Ensure all v3 fields exist (backward compat with v1/v2 state)."""
    state.setdefault("last_dispatched_id", "")
    state.setdefault("dispatched_at", 0)
    state.setdefault("notified_failures", {})
    state.setdefault("dispatch_history", [])
    state.setdefault("circuit_breaker", {
        "state": "closed",
        "failures": 0,
        "last_failure_at": 0,
        "opened_at": 0,
    })
    cb = state["circuit_breaker"]
    cb.setdefault("state", "closed")
    cb.setdefault("failures", 0)
    cb.setdefault("last_failure_at", 0)
    cb.setdefault("opened_at", 0)
    return state


# === LOCK ===
lock_fd = None
try:
    lock_fd = open(LOCK_FILE, "w")
    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except (IOError, OSError):
    log("SKIP: already running (flock)")
    sys.exit(0)


def run_cmd(cmd: list, timeout: int = 30) -> str:
    try:
        cp = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        if cp.returncode != 0:
            raise RuntimeError(f"exit {cp.returncode}: {cp.stderr.strip()}")
        return cp.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timeout after {timeout}s: {' '.join(cmd[:3])}...")


def load_gateway_token() -> str:
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


def gateway_call(method: str, params: dict = None) -> dict:
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


def mc_list_tasks() -> list:
    raw = run_cmd([MC_CLIENT, "list-tasks"], timeout=15)
    data = json.loads(raw or "{}")
    if isinstance(data, dict):
        return data.get("items", [])
    return []


def mc_update_task(task_id: str, **kwargs) -> bool:
    """Update a task via mc-client.sh."""
    cmd = [MC_CLIENT, "update-task", task_id]
    if "status" in kwargs:
        cmd.extend(["--status", kwargs["status"]])
    if "comment" in kwargs:
        cmd.extend(["--comment", kwargs["comment"]])
    if "fields" in kwargs:
        fields_val = kwargs["fields"]
        cmd.extend(["--fields", json.dumps(fields_val) if isinstance(fields_val, dict) else fields_val])
    if DRY_RUN:
        log(f"DRY-RUN mc-update: {' '.join(cmd)}")
        return True
    try:
        run_cmd(cmd, timeout=15)
        return True
    except Exception as e:
        log(f"ERROR: mc-update failed: {e}")
        return False


def send_discord(channel: str, message: str) -> bool:
    """Send Discord message via openclaw CLI."""
    if DRY_RUN:
        log(f"DRY-RUN discord({channel}): {message[:120]}...")
        return True
    try:
        cmd = [
            OPENCLAW_BIN, "message", "send",
            "--channel", "discord",
            "--target", channel,
            "--message", message,
        ]
        run_cmd(cmd, timeout=10)
        return True
    except Exception as e:
        log(f"ERROR: Discord send to {channel} failed: {e}")
        return False


def load_agent_mapping() -> dict:
    """Build UUID → agent name mapping from MC config."""
    mapping = {}
    try:
        with open(MC_CONFIG_FILE) as f:
            data = json.load(f)
        agents = data.get("agents", {})
        for name, uuid in agents.items():
            agent_id = "main" if name.lower() == "luna" else name.lower().replace("_", "-")
            mapping[uuid] = agent_id
    except Exception:
        pass
    return mapping


def resolve_agent_name(uuid_str: str, mapping: dict) -> str:
    """Resolve MC agent UUID to OpenClaw agent name."""
    if not uuid_str:
        return "luan"  # Default worker
    if uuid_str in mapping:
        return mapping[uuid_str]
    for full_uuid, name in mapping.items():
        if full_uuid.startswith(uuid_str):
            return name
    return "luan"  # Fallback


def record_cb_failure(state: dict) -> None:
    """Record a circuit breaker failure."""
    now = int(time.time() * 1000)
    cb = state["circuit_breaker"]

    if now - cb.get("last_failure_at", 0) > CB_WINDOW_MS:
        cb["failures"] = 0

    cb["failures"] = cb.get("failures", 0) + 1
    cb["last_failure_at"] = now

    if cb["failures"] >= CB_FAILURE_THRESHOLD:
        cb["state"] = "open"
        cb["opened_at"] = now
        log(f"CIRCUIT BREAKER: OPEN after {cb['failures']} failures")

    save_state(state)


# === QUEUE OPERATIONS ===

def write_queue_item(item_type: str, task_id: str, payload: dict) -> str:
    """
    Atomically write a queue item to pending/.

    Returns the filename written, or empty string on failure.
    Format: {timestamp}-{type}-{task_id_short}.json
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    task_short = task_id[:8] if task_id else "unknown"
    filename = f"{timestamp}-{item_type}-{task_short}.json"
    target_path = os.path.join(QUEUE_PENDING, filename)

    queue_item = {
        "version": 1,
        "type": item_type,
        "task_id": task_id,
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "heartbeat-v3",
        **payload,
    }

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=QUEUE_PENDING, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(queue_item, f, indent=2)
        os.replace(tmp_path, target_path)
        log(f"QUEUE: wrote {filename}")
        return filename
    except Exception as e:
        log(f"ERROR: queue write failed: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return ""


def send_system_event_nudge(title: str, task_id: str) -> bool:
    """
    Send a system-event nudge to Luna's main session.

    This injects a system message into the main session context
    WITHOUT creating a new session. Luna sees it on next interaction.
    """
    if DRY_RUN:
        log(f"DRY-RUN: system-event nudge for {task_id[:8]} — {title}")
        return True

    nudge_msg = (
        f"📋 Nova tarefa na dispatch queue: **{title}** (`{task_id[:8]}`). "
        f"Verifique workspace/heartbeat-v3/queue/pending/ e processe."
    )

    try:
        # Use openclaw cron add with --system-event for a lightweight nudge
        result = run_cmd([
            OPENCLAW_BIN, "cron", "add",
            "--at", "+10s",
            "--agent", "main",
            "--system-event", nudge_msg,
            "--delete-after-run",
            "--name", f"queue-nudge-{task_id[:8]}",
            "--json",
        ], timeout=15)
        log(f"NUDGE: system-event sent for {task_id[:8]}")
        return True
    except Exception as e:
        # Nudge failure is non-fatal — escalation.sh will catch stale items
        log(f"WARN: system-event nudge failed (non-fatal): {e}")
        return False


def analyze_session_failure(session_key: str) -> tuple:
    """Analyze a dead session to determine failure type."""
    failure_type = "UNKNOWN"
    adjustments = "nenhum ajuste específico"

    try:
        history = gateway_call("chat.history", {
            "sessionKey": session_key,
            "limit": 3,
        })
    except Exception as e:
        log(f"WARN: chat.history failed for {session_key}: {e}")
        return failure_type, adjustments

    messages = []
    if isinstance(history, dict):
        messages = history.get("messages", history.get("items", []))
    elif isinstance(history, list):
        messages = history

    all_text = ""
    tool_calls = []
    for msg in messages:
        content = str(msg.get("content", "") or msg.get("text", "") or "")
        all_text += content.lower() + " "
        tc = msg.get("toolCalls", msg.get("tool_calls", []))
        if tc:
            tool_calls.extend(tc if isinstance(tc, list) else [tc])

    stop_reason = ""
    error_msg = ""
    for msg in messages:
        sr = msg.get("stopReason", msg.get("stop_reason", ""))
        if sr:
            stop_reason = str(sr).lower()
        em = msg.get("errorMessage", msg.get("error_message", msg.get("error", "")))
        if em:
            error_msg = str(em).lower()

    combined = all_text + " " + stop_reason + " " + error_msg

    if "401" in combined or "unauthorized" in combined or "auth" in combined:
        failure_type = "AUTH_EXPIRED"
        adjustments = "verificar credenciais, possivelmente trocar modelo"
    elif "timeout" in combined or "timed out" in combined:
        failure_type = "TIMEOUT"
        adjustments = "aumentar runTimeoutSeconds (1.5x)"
    elif "oom" in combined or "out of memory" in combined or "signal 9" in combined or "killed" in combined:
        failure_type = "OOM"
        adjustments = "reduzir contexto, adicionar constraint de brevidade"
    elif "rate limit" in combined or "429" in combined or "quota" in combined:
        failure_type = "RATE_LIMITED"
        adjustments = "aguardar cooldown, possivelmente trocar modelo"
    else:
        if len(tool_calls) >= 3:
            tool_names = [
                str(tc.get("name", tc.get("function", {}).get("name", "")))
                for tc in tool_calls if isinstance(tc, dict)
            ]
            if tool_names and len(set(tool_names)) == 1:
                failure_type = "LOOP_DEGENERATIVO"
                adjustments = "simplificar task, trocar modelo"

        if failure_type == "UNKNOWN":
            if stop_reason in ("stop", "end_turn"):
                failure_type = "INCOMPLETE"
                adjustments = "re-spawn com 'continue de onde parou'"
            else:
                failure_type = "GENERIC_ERROR"
                adjustments = "re-tentar sem ajustes específicos"

    return failure_type, adjustments


def build_dispatch_payload(task: dict, agent_name: str, eligible_count: int, in_progress_count: int) -> dict:
    """Build the queue payload for a dispatch item."""
    title = task.get("title", "(sem título)")
    task_id = task.get("id", "")
    description = task.get("description", "")[:500]
    priority = task.get("priority", "medium")

    return {
        "title": title,
        "agent": agent_name,
        "priority": priority,
        "context": {
            "description": description,
            "eligible_count": eligible_count,
            "in_progress_count": in_progress_count,
        },
        "constraints": {
            "max_age_minutes": V3_CONFIG.get("escalation_critical_minutes", 30),
            "timeout_seconds": 600,
        },
        "spawn_params": {
            "agent": agent_name,
            "task_id": task_id,
            "title": title,
            "description": description,
            "priority": priority,
        },
    }


def build_failure_payload(task: dict, failure_type: str, retry_count: int, adjustments: str, session_key: str) -> dict:
    """Build the queue payload for a failure respawn item."""
    title = task.get("title", "(sem título)")
    description = task.get("description", "")[:300]

    return {
        "title": title,
        "agent": "luan",  # Default for respawn
        "priority": "high",
        "context": {
            "description": description,
            "failure_type": failure_type,
            "retry_count": retry_count,
            "adjustments": adjustments,
            "dead_session_key": session_key,
        },
        "constraints": {
            "max_age_minutes": V3_CONFIG.get("escalation_critical_minutes", 30),
            "timeout_seconds": 600,
        },
    }


# ============================================================
# START
# ============================================================
log("=" * 60)
log("heartbeat-v3 starting")

now_ms = int(time.time() * 1000)
state = load_state()
state = ensure_state_fields(state)

# Handle --reset-circuit-breaker
if RESET_CB:
    state["circuit_breaker"] = {
        "state": "closed",
        "failures": 0,
        "last_failure_at": 0,
        "opened_at": 0,
    }
    save_state(state)
    log("Circuit breaker RESET to closed")
    sys.exit(0)

# ============================================================
# PHASE 1: Gateway health check
# ============================================================
try:
    gateway_call("sessions.list", {})
except Exception as e:
    log(f"SKIP: gateway unreachable: {e}")
    sys.exit(0)
log("Phase 1: Gateway OK")

# ============================================================
# PHASE 2: Active hours check (São Paulo)
# ============================================================
if _has_zoneinfo:
    sp_tz = zoneinfo.ZoneInfo("America/Sao_Paulo")
else:
    sp_tz = dateutil.tz.gettz("America/Sao_Paulo")

sp_hour = datetime.now(sp_tz).hour
if sp_hour < ACTIVE_HOUR_START:
    log(f"SKIP: outside active hours ({sp_hour}h São Paulo)")
    sys.exit(0)
log(f"Phase 2: Active hours OK ({sp_hour}h São Paulo)")

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
    record_cb_failure(state)
    sys.exit(1)

log(f"Phase 3: {len(sessions)} sessions, {len(tasks)} tasks")

# Load agent mapping
agent_mapping = load_agent_mapping()
log(f"Agent mapping: {len(agent_mapping)} agents")

# ============================================================
# PHASE 4: Failure detection + queue-based respawn
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
    is_dead = False
    if session_key in sessions_by_key:
        session = sessions_by_key[session_key]
        s_status = str(session.get("status", "")).lower()
        if s_status in ("failed", "error", "ended"):
            is_dead = True
        else:
            continue  # Session alive — skip
    else:
        is_dead = True  # Session not found = dead

    if not is_dead:
        continue

    # Dead session detected — check cooldown
    prev = notified_failures.get(task_id, {})
    prev_at = prev.get("at", 0) if isinstance(prev, dict) else 0
    if now_ms - prev_at < FAILURE_COOLDOWN_MS:
        continue  # Already handled recently

    title = task.get("title", "(sem título)")

    # Analyze the failure
    failure_type, adjustments = analyze_session_failure(session_key)
    retry_count = int(fields.get("mc_retry_count", 0) or 0)

    log(f"FAILURE: task {task_id[:8]} — {title} — type={failure_type}, retry={retry_count}")

    if retry_count < MAX_RETRIES:
        # === V3 CHANGE: Write queue file instead of cron one-shot ===
        payload = build_failure_payload(task, failure_type, retry_count, adjustments, session_key)
        queue_file = write_queue_item("respawn", task_id, payload)

        if queue_file:
            # Send system-event nudge (non-blocking)
            send_system_event_nudge(f"🔄 Respawn: {title}", task_id)

        # Update MC task
        if not DRY_RUN:
            mc_update_task(task_id,
                fields={"mc_retry_count": str(retry_count + 1)},
                status="in_progress",
                comment=f"[heartbeat-v3] failure detected ({failure_type}), queued for respawn")

        notif_msg = (
            f"⚠️ **Heartbeat V3** task falhou: `{task_id[:8]}` — **{title}**\n"
            f"Erro: {failure_type} | Retry #{retry_count + 1}/{MAX_RETRIES}\n"
            f"Enfileirado para respawn automático via queue."
        )
        send_discord(DISCORD_CHANNEL, notif_msg)
        send_discord(NOTIFICATIONS_CHANNEL, notif_msg)

    else:
        # Max retries exceeded — move to review
        if not DRY_RUN:
            mc_update_task(task_id,
                status="review",
                comment=f"[heartbeat-v3] {failure_type} — max retries ({MAX_RETRIES}) exceeded, moving to review")

        fail_msg = (
            f"⚠️ **Heartbeat V3** task falhou {MAX_RETRIES}x: `{task_id[:8]}` — **{title}**\n"
            f"Erro: {failure_type}\n"
            f"Requer intervenção humana."
        )
        send_discord(DISCORD_CHANNEL, fail_msg)
        send_discord(NOTIFICATIONS_CHANNEL, fail_msg)

    notified_failures[task_id] = {"at": now_ms, "session": session_key, "type": failure_type}
    new_failures.append({
        "task_id": task_id,
        "title": title,
        "session_key": session_key,
        "type": failure_type,
    })

if new_failures:
    state["notified_failures"] = notified_failures
    save_state(state)

log(f"Phase 4: {len(new_failures)} failure(s) detected")

# ============================================================
# PHASE 4.5: Circuit breaker check
# ============================================================
cb = state["circuit_breaker"]
if cb["state"] == "open":
    elapsed = now_ms - cb.get("opened_at", 0)
    if elapsed > CB_COOLDOWN_MS:
        cb["state"] = "half-open"
        log("Phase 4.5: Circuit breaker → HALF-OPEN (cooldown elapsed)")
        save_state(state)
    else:
        mins_left = (CB_COOLDOWN_MS - elapsed) // 60000
        log(f"SKIP: circuit breaker OPEN ({mins_left}min until cooldown)")
        sys.exit(0)
elif cb["state"] == "half-open":
    log("Phase 4.5: Circuit breaker HALF-OPEN — allowing 1 dispatch test")
else:
    log("Phase 4.5: Circuit breaker closed")

# ============================================================
# PHASE 4.6: Resource check
# ============================================================
RESOURCE_STATE_FILE = "/tmp/.mc-resource-state.json"
resource_level = "ok"
try:
    with open(RESOURCE_STATE_FILE) as f:
        resource_data = json.load(f)
    resource_level = resource_data.get("level", "ok")
except Exception:
    pass  # No resource state = assume OK

if resource_level in ("critical", "degraded"):
    log(f"SKIP: resources {resource_level} — no dispatch")
    sys.exit(0)
log(f"Phase 4.6: Resources OK ({resource_level})")

# ============================================================
# PHASE 4.7: Rate limit check
# ============================================================
dispatch_history = state.get("dispatch_history", [])
recent_dispatches = [d for d in dispatch_history if now_ms - d.get("at", 0) < 3600 * 1000]
if len(recent_dispatches) >= MAX_DISPATCHES_PER_HOUR:
    log(f"SKIP: rate limit ({len(recent_dispatches)}/{MAX_DISPATCHES_PER_HOUR} dispatches this hour)")
    sys.exit(0)
log(f"Phase 4.7: Rate limit OK ({len(recent_dispatches)}/{MAX_DISPATCHES_PER_HOUR})")

# ============================================================
# PHASE 5: Check active subagents + in_progress tasks
# ============================================================
# Exclude SERVICE tasks (persistent, never complete — e.g. PMM bot)
SERVICE_TITLE_PREFIXES = ["PMM Service:", "🤖 PMM"]
in_progress = [
    t for t in tasks
    if str(t.get("status", "")).lower() == "in_progress"
    and not any(str(t.get("title", "")).startswith(pfx) for pfx in SERVICE_TITLE_PREFIXES)
]
if len(in_progress) >= MAX_CONCURRENT_IN_PROGRESS:
    titles = [t.get("title", "?")[:40] for t in in_progress[:3]]
    log(f"SKIP: {len(in_progress)} tasks in_progress (max {MAX_CONCURRENT_IN_PROGRESS}): {', '.join(titles)}")
    sys.exit(0)

active_subagents = [
    s for s in sessions
    if isinstance(s, dict)
    and "subagent" in s.get("key", "")
    and s.get("status", "").lower() in ("active", "running", "")
    and (now_ms - (s.get("updatedAt", 0) or 0)) < 30 * 60 * 1000
]
if len(active_subagents) >= MAX_CONCURRENT_IN_PROGRESS:
    labels = [s.get("label", s.get("key", "?"))[:40] for s in active_subagents[:3]]
    log(f"SKIP: {len(active_subagents)} active subagent(s): {', '.join(labels)}")
    sys.exit(0)

log(f"Phase 5: {len(in_progress)} in_progress, {len(active_subagents)} active subagents")

# ============================================================
# PHASE 5.5: Stale dispatch detection
# ============================================================
for t in in_progress:
    task_id_check = t.get("id", "")
    fields = t.get("custom_field_values") or {}
    session_key = str(fields.get("mc_session_key", "") or "").strip()

    if not session_key:
        if task_id_check == state.get("last_dispatched_id"):
            dispatch_age = now_ms - state.get("dispatched_at", 0)
            if dispatch_age > DISPATCH_STALE_MS:
                title = t.get("title", "?")
                log(f"STALE: task {task_id_check[:8]} dispatched {dispatch_age // 60000}min ago, no session_key")
                mc_update_task(task_id_check,
                    status="inbox",
                    comment=f"[heartbeat-v3] rollback — no session after {dispatch_age // 60000}min")
                state["last_dispatched_id"] = ""
                save_state(state)
                send_discord(NOTIFICATIONS_CHANNEL,
                    f"⏳ **Heartbeat V3** stale dispatch rollback: `{task_id_check[:8]}` — **{title}** "
                    f"(no session after {dispatch_age // 60000}min)")
                log("Phase 5.5: stale dispatch rolled back — exiting")
                sys.exit(0)

log("Phase 5.5: No stale dispatches")

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
    pass

task_status_by_id = {t.get("id", ""): t.get("status", "").lower() for t in tasks}

eligible = []
for t in inbox:
    tid = t.get("id", "")
    title = t.get("title", "?")

    if tid in blocked_ids:
        log(f"FILTER: {tid[:8]} blocked (human-gate): {title[:40]}")
        continue

    deps = dep_chain.get(tid, [])
    unmet = [d for d in deps if task_status_by_id.get(d, "").lower() != "done"]
    if unmet:
        unmet_short = ", ".join(d[:8] for d in unmet)
        log(f"FILTER: {tid[:8]} has unmet deps ({unmet_short}): {title[:40]}")
        continue

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
description = next_task.get("description", "")[:500]

log(f"Phase 7: Eligible task: {task_id[:8]} — {title}")

# ============================================================
# PHASE 8: Dedup — already dispatched this task?
# ============================================================
last_id = state.get("last_dispatched_id", "")
last_at = state.get("dispatched_at", 0)

if task_id == last_id and (now_ms - last_at) < DISPATCH_TIMEOUT_MS:
    elapsed_min = int((now_ms - last_at) / 60000)
    log(f"SKIP: task {task_id[:8]} already dispatched {elapsed_min}min ago")
    sys.exit(0)

for d in recent_dispatches:
    if d.get("task_id") == task_id:
        elapsed_min = int((now_ms - d.get("at", 0)) / 60000)
        log(f"SKIP: task {task_id[:8]} dispatched {elapsed_min}min ago (from history)")
        sys.exit(0)

log("Phase 8: Dedup OK")

# ============================================================
# PHASE 9: Dispatch via QUEUE (NOT cron one-shot)
# ============================================================
assigned_uuid = str(next_task.get("assigned_agent_id", "") or "")
agent_name = resolve_agent_name(assigned_uuid, agent_mapping)

log(f"Phase 9: Dispatching {task_id[:8]} → {agent_name}")

# Step 9a: Mark task in_progress
if not mc_update_task(task_id,
    status="in_progress",
    comment=f"[heartbeat-v3] dispatching to {agent_name} via queue"):
    log("ERROR: failed to mark task in_progress")
    record_cb_failure(state)
    sys.exit(1)

# Step 9b: Fast dispatch via openclaw agent (replaces queue + nudge)
dispatch_payload = build_dispatch_payload(next_task, agent_name, len(eligible), len(in_progress))
fast_dispatch_script = os.path.join(WORKSPACE, "scripts", "mc-fast-dispatch.sh")

dispatch_ok = False
dispatch_method = "queue"  # fallback

if os.path.isfile(fast_dispatch_script) and os.access(fast_dispatch_script, os.X_OK):
    # Try fast dispatch first (direct openclaw agent call)
    try:
        description = next_task.get("description", "")[:2000]
        task_msg = f"## MC Task: {title}\n\n{description}"
        
        fd_result = run_cmd([
            fast_dispatch_script,
            "--agent", agent_name,
            "--task", task_msg,
            "--title", title,
            "--from-mc", task_id,
            "--timeout", "600",
        ], timeout=620)
        
        log(f"FAST DISPATCH: {task_id[:8]} → {agent_name} (direct)")
        dispatch_ok = True
        dispatch_method = "fast"
    except Exception as e:
        log(f"WARN: fast dispatch failed ({e}), falling back to queue")

if not dispatch_ok:
    # Fallback: write queue file + nudge (old method)
    queue_filename = write_queue_item("dispatch", task_id, dispatch_payload)
    
    if not queue_filename:
        log("ERROR: queue write failed — rolling back")
        mc_update_task(task_id,
            status="inbox",
            comment="[heartbeat-v3] rollback — queue write failed")
        record_cb_failure(state)
        sys.exit(1)
    
    send_system_event_nudge(title, task_id)
    dispatch_method = "queue"
    dispatch_ok = True

# Step 9d: Update state
state["last_dispatched_id"] = task_id
state["dispatched_at"] = now_ms
state["dispatch_history"].append({
    "task_id": task_id,
    "at": now_ms,
    "queue_file": dispatch_method,
    "agent": agent_name,
    "method": dispatch_method,
})
# Trim history to last 24h
state["dispatch_history"] = [d for d in state["dispatch_history"]
                              if now_ms - d.get("at", 0) < 24 * 3600 * 1000]

# Circuit breaker: success in half-open → close
if cb["state"] == "half-open":
    cb["state"] = "closed"
    cb["failures"] = 0
    log("CIRCUIT BREAKER: HALF-OPEN → CLOSED (dispatch succeeded)")

save_state(state)

# Step 9e: Notify #notifications
notif_msg = (
    f"📋 **Heartbeat V3** dispatch: `{task_id[:8]}` — **{title}** → `{agent_name}`\n"
    f"Eligible: {len(eligible)} | In-progress: {len(in_progress)} | Via: {dispatch_method}"
)
send_discord(NOTIFICATIONS_CHANNEL, notif_msg)

log(f"DISPATCH: {task_id[:8]} → {agent_name} (method: {dispatch_method})")
log("heartbeat-v3 complete")
