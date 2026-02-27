#!/usr/bin/env bash
# heartbeat-v2.sh — Autonomous dispatch engine (Bash Detecta, Cron Injeta, Agent Executa)
#
# Replaces: heartbeat-check.sh (v1) — notification-only
# Absorbs:  mc-failure-detector.sh (dead session detection + auto-respawn)
#
# Flow:
#   Phase 1:   Gateway health check
#   Phase 2:   Active hours (8h-0h São Paulo)
#   Phase 3:   Fetch sessions + MC tasks
#   Phase 4:   Failure detection + auto-respawn via cron one-shot
#   Phase 4.5: Circuit breaker check
#   Phase 4.6: Resource check (skip if degraded/critical)
#   Phase 4.7: Rate limit (max 3 dispatches/hour)
#   Phase 5:   Active subagents check (max 2 concurrent)
#   Phase 5.5: Stale dispatch detection (in_progress no session_key >15min)
#   Phase 7:   Pull eligible inbox task (FIFO + blocklist + dependency chain)
#   Phase 8:   Dedup
#   Phase 9:   Dispatch via cron one-shot → Luna isolated session
#
# State: /tmp/.heartbeat-check-state.json (enhanced)
# Lock:  /tmp/.heartbeat-check.lock
# Log:   logs/heartbeat-v2.log
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
import tempfile
from datetime import datetime, timezone

# === CONFIG ===
WORKSPACE = os.environ.get("WORKSPACE", "/home/openclaw/.openclaw/workspace")

SCRIPTS_DIR = os.path.join(WORKSPACE, "scripts")
MC_CLIENT = os.path.join(SCRIPTS_DIR, "mc-client.sh")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
OPENCLAW_CONFIG = os.environ.get("OPENCLAW_CONFIG", "/home/openclaw/.openclaw/openclaw.json")
GATEWAY_URL = os.environ.get("MC_GATEWAY_URL", "ws://127.0.0.1:18789")
DISCORD_CHANNEL = os.environ.get("HEARTBEAT_DISCORD_CHANNEL", "1476255906894446644")
NOTIFICATIONS_CHANNEL = os.environ.get("HEARTBEAT_NOTIFICATIONS_CHANNEL", "1476255906894446644")
STATE_FILE = os.environ.get("HEARTBEAT_STATE_FILE", "/tmp/.heartbeat-check-state.json")
LOCK_FILE = "/tmp/.heartbeat-check.lock"
LOG_DIR = os.path.join(WORKSPACE, "logs")
LOG_FILE = os.path.join(LOG_DIR, "heartbeat-v2.log")

# Agent mapping file
AGENT_IDS_FILE = os.path.join(WORKSPACE, "config", "mc-agent-ids.json")
# MC config for full UUIDs
MC_CONFIG_FILE = os.path.join(WORKSPACE, "config", "mission-control-ids.local.json")

# Tuning
ACTIVE_HOUR_START = 8     # São Paulo local time
ACTIVE_HOUR_END = 24      # 00:00 (midnight)
MAX_DISPATCHES_PER_HOUR = 3
MAX_CONCURRENT_IN_PROGRESS = 2
MIN_DISPATCH_INTERVAL_MS = 5 * 60 * 1000   # 5min between dispatches
CRON_TIMEOUT_SECONDS = 600                  # 10min max per cron job
DISPATCH_TIMEOUT_MS = 2 * 60 * 60 * 1000   # 2h — re-dispatch if task still inbox
DISPATCH_STALE_MS = 15 * 60 * 1000         # 15min without session_key = stale

# Circuit breaker
CB_FAILURE_THRESHOLD = 3
CB_WINDOW_MS = 30 * 60 * 1000       # 30min window for failures
CB_COOLDOWN_MS = 15 * 60 * 1000     # 15min cooldown when OPEN

# Failure detection
FAILURE_COOLDOWN_MS = 30 * 60 * 1000     # 30min cooldown per failure notification
MAX_RETRIES = 2

# Dry-run support
DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv or DRY_RUN
RESET_CB = "--reset-circuit-breaker" in sys.argv

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


def save_state(state):
    """Atomic write: write to temp file then rename."""
    try:
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(STATE_FILE), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except Exception as e:
        log(f"WARN: failed to save state: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def ensure_state_fields(state):
    """Ensure all v2 fields exist (backward compat with v1 state)."""
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


def mc_update_task(task_id, **kwargs):
    """Update a task via mc-client.sh."""
    cmd = [MC_CLIENT, "update-task", task_id]
    if "status" in kwargs:
        cmd.extend(["--status", kwargs["status"]])
    if "comment" in kwargs:
        cmd.extend(["--comment", kwargs["comment"]])
    if "fields" in kwargs:
        cmd.extend(["--fields", json.dumps(kwargs["fields"]) if isinstance(kwargs["fields"], dict) else kwargs["fields"]])
    if DRY_RUN:
        log(f"DRY-RUN mc-update: {' '.join(cmd)}")
        return True
    try:
        run_cmd(cmd, timeout=15)
        return True
    except Exception as e:
        log(f"ERROR: mc-update failed: {e}")
        return False


def send_discord(channel, message):
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


def load_agent_mapping():
    """Build UUID → agent name mapping from MC config."""
    mapping = {}
    # From mc-agent-ids.json (name→short_uuid)
    try:
        with open(AGENT_IDS_FILE) as f:
            data = json.load(f)
        # This file is name→short_uuid, we need full UUIDs from MC config
    except Exception:
        pass

    # From mission-control-ids.local.json (authoritative)
    try:
        with open(MC_CONFIG_FILE) as f:
            data = json.load(f)
        agents = data.get("agents", {})
        for name, uuid in agents.items():
            # Normalize: luna→main (Luna IS the main agent)
            agent_id = "main" if name.lower() == "luna" else name.lower().replace("_", "-")
            mapping[uuid] = agent_id
    except Exception:
        pass

    return mapping


def resolve_agent_name(uuid_str, mapping):
    """Resolve MC agent UUID to OpenClaw agent name."""
    if not uuid_str:
        return "luan"  # Default worker
    # Try full UUID match
    if uuid_str in mapping:
        return mapping[uuid_str]
    # Try prefix match (short UUID)
    for full_uuid, name in mapping.items():
        if full_uuid.startswith(uuid_str):
            return name
    return "luan"  # Fallback


def iso_at(seconds_from_now=10):
    """Generate ISO timestamp for --at parameter."""
    ts = time.time() + seconds_from_now
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_cb_failure(state):
    """Record a circuit breaker failure."""
    now = int(time.time() * 1000)
    cb = state["circuit_breaker"]

    # Reset counter if outside window
    if now - cb.get("last_failure_at", 0) > CB_WINDOW_MS:
        cb["failures"] = 0

    cb["failures"] = cb.get("failures", 0) + 1
    cb["last_failure_at"] = now

    if cb["failures"] >= CB_FAILURE_THRESHOLD:
        cb["state"] = "open"
        cb["opened_at"] = now
        log(f"CIRCUIT BREAKER: OPEN after {cb['failures']} failures")

    save_state(state)


def build_dispatch_message(task, agent_name, eligible_count, in_progress_count):
    """Build the payload for the cron one-shot dispatch."""
    title = task.get("title", "(sem título)")
    task_id = task.get("id", "")
    description = task.get("description", "")[:500]
    priority = task.get("priority", "medium")
    assigned = task.get("assigned_agent_id", "")

    msg = f"""📋 Heartbeat dispatch — execute a task abaixo.

## Task
**Título:** {title}
**MC Task ID:** {task_id}
**Prioridade:** {priority}
**Agente designado:** {agent_name}

## Descrição
{description if description else '(sem descrição)'}

## Instruções
1. Spawnar subagent `{agent_name}` com a task acima
2. Linkar session_key ao MC task via mc-client.sh update-task {task_id} --fields '{{"mc_session_key":"<SESSION_KEY>"}}'
3. NÃO fazer a task você mesma — delegar via sessions_spawn
4. Se a task não tiver agente designado, usar agente `luan` (default)
5. Se spawn falhar por concurrency, atualizar MC task para `inbox` com comentário e NÃO tentar workarounds

## Contexto
- Eligible tasks no inbox: {eligible_count}
- Tasks in_progress: {in_progress_count}
- Dispatch source: heartbeat-v2 automated"""
    return msg


def build_failure_respawn_message(task, failure_type, retry_count, adjustments):
    """Build the payload for failure respawn via cron one-shot."""
    title = task.get("title", "(sem título)")
    task_id = task.get("id", "")
    description = task.get("description", "")[:300]

    msg = f"""🔄 Heartbeat failure respawn — re-executar task que falhou.

## Task
**Título:** {title}
**MC Task ID:** {task_id}
**Retry:** #{retry_count + 1}

## Descrição
{description if description else '(sem descrição)'}

## Análise de Falha
**Tipo de erro:** {failure_type}
**Ajustes aplicados:** {adjustments}

## Instruções
1. Re-spawnar subagent para esta task com os ajustes acima
2. Linkar novo session_key ao MC task
3. Se retry falhar novamente, mover task para `review`
4. Atualizar mc_retry_count no MC

## Contexto
- Esta é uma re-execução automática após falha detectada
- Dispatch source: heartbeat-v2 failure-respawn"""
    return msg


def analyze_session_failure(session_key):
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

    # Analyze last messages for error patterns
    all_text = ""
    tool_calls = []
    for msg in messages:
        content = str(msg.get("content", "") or msg.get("text", "") or "")
        all_text += content.lower() + " "
        # Track tool calls for loop detection
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

    # Pattern matching
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
        # Check for loop (same tool called 3+ times)
        if len(tool_calls) >= 3:
            tool_names = [str(tc.get("name", tc.get("function", {}).get("name", ""))) for tc in tool_calls if isinstance(tc, dict)]
            if tool_names and len(set(tool_names)) == 1:
                failure_type = "LOOP_DEGENERATIVO"
                adjustments = "simplificar task, trocar modelo"

        if failure_type == "UNKNOWN":
            if stop_reason == "stop" or stop_reason == "end_turn":
                failure_type = "INCOMPLETE"
                adjustments = "re-spawn com 'continue de onde parou'"
            else:
                failure_type = "GENERIC_ERROR"
                adjustments = "re-tentar sem ajustes específicos"

    return failure_type, adjustments


# ============================================================
# START
# ============================================================
log("=" * 60)
log("heartbeat-v2 starting")

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
# PHASE 4: Failure detection + auto-respawn
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
        # ── NOTIFY ONLY (no auto-respawn) ──────────────────
        # Auto-respawn via cron one-shot was DISABLED (2026-02-26)
        # because isolated sessions without a channel cause
        # "Channel is required" error storms → OOM → gateway death.
        #
        # Instead: notify, update MC retry count, and let Luna
        # handle re-spawn in her main session (which HAS a channel).
        if not DRY_RUN:
            mc_update_task(task_id,
                fields={"mc_retry_count": str(retry_count + 1)},
                status="review",
                comment=f"[heartbeat-v2] failure detected ({failure_type}), moved to review for manual respawn")

        notif_msg = f"⚠️ **Heartbeat** task falhou: `{task_id[:8]}` — **{title}**\nErro: {failure_type} | Retry #{retry_count + 1}/{MAX_RETRIES}\nRequer re-spawn manual pela Luna."
        send_discord(DISCORD_CHANNEL, notif_msg)
        send_discord(NOTIFICATIONS_CHANNEL, notif_msg)

    else:
        # Max retries exceeded — move to review
        if not DRY_RUN:
            mc_update_task(task_id,
                status="review",
                comment=f"[heartbeat-v2] {failure_type} — max retries ({MAX_RETRIES}) exceeded, moving to review")
        else:
            log(f"DRY-RUN: would move {task_id[:8]} to review")

        # Notify Discord and #notifications
        fail_msg = f"⚠️ **Heartbeat** task falhou {MAX_RETRIES}x: `{task_id[:8]}` — **{title}**\nErro: {failure_type}\nRequer intervenção humana."
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
in_progress = [t for t in tasks if str(t.get("status", "")).lower() == "in_progress"]
if len(in_progress) >= MAX_CONCURRENT_IN_PROGRESS:
    titles = [t.get("title", "?")[:40] for t in in_progress[:3]]
    log(f"SKIP: {len(in_progress)} tasks in_progress (max {MAX_CONCURRENT_IN_PROGRESS}): {', '.join(titles)}")
    sys.exit(0)

# Also check active subagent sessions
active_subagents = [
    s for s in sessions
    if isinstance(s, dict)
    and "subagent" in s.get("key", "")
    and s.get("status", "").lower() in ("active", "running", "")
    # Exclude sessions that have been idle too long (>30min = probably stale)
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
        # Task is in_progress but has no session — check if we dispatched it
        if task_id_check == state.get("last_dispatched_id"):
            dispatch_age = now_ms - state.get("dispatched_at", 0)
            if dispatch_age > DISPATCH_STALE_MS:
                title = t.get("title", "?")
                log(f"STALE: task {task_id_check[:8]} dispatched {dispatch_age // 60000}min ago, no session_key")
                mc_update_task(task_id_check,
                    status="inbox",
                    comment=f"[heartbeat-v2] rollback — no session after {dispatch_age // 60000}min")
                state["last_dispatched_id"] = ""
                save_state(state)
                # Notify
                send_discord(NOTIFICATIONS_CHANNEL,
                    f"⏳ **Heartbeat** stale dispatch rollback: `{task_id_check[:8]}` — **{title}** (no session after {dispatch_age // 60000}min)")
                # Don't dispatch this run — let next run pick it up fresh
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

# Also check dispatch_history for recent dispatch of same task
for d in recent_dispatches:
    if d.get("task_id") == task_id:
        elapsed_min = int((now_ms - d.get("at", 0)) / 60000)
        log(f"SKIP: task {task_id[:8]} dispatched {elapsed_min}min ago (from history)")
        sys.exit(0)

log("Phase 8: Dedup OK")

# ============================================================
# PHASE 9: Dispatch via cron one-shot
# ============================================================
assigned_uuid = str(next_task.get("assigned_agent_id", "") or "")
agent_name = resolve_agent_name(assigned_uuid, agent_mapping)

log(f"Phase 9: Dispatching {task_id[:8]} → {agent_name}")

# Step 9a: Mark task in_progress
if not mc_update_task(task_id,
    status="in_progress",
    comment=f"[heartbeat-v2] dispatching to {agent_name}"):
    log("ERROR: failed to mark task in_progress")
    record_cb_failure(state)
    sys.exit(1)

# Step 9b: Create cron one-shot
dispatch_msg = build_dispatch_message(next_task, agent_name, len(eligible), len(in_progress))

at_time = iso_at(10)
cron_job_id = ""

if DRY_RUN:
    log(f"DRY-RUN: would create cron one-shot at {at_time}")
    log(f"DRY-RUN dispatch message:\n{dispatch_msg[:300]}...")
    cron_job_id = "dry-run-id"
else:
    try:
        result = run_cmd([
            OPENCLAW_BIN, "cron", "add",
            "--at", at_time,
            "--agent", "main",
            "--session", "isolated",
            "--name", f"hb-dispatch-{task_id[:8]}",
            "--delete-after-run",
            "--timeout-seconds", str(CRON_TIMEOUT_SECONDS),
            "--thinking", "low",
            "--no-deliver",
            "--message", dispatch_msg,
            "--json",
        ], timeout=15)
        cron_data = json.loads(result) if result else {}
        cron_job_id = cron_data.get("id", "")
        log(f"CRON: created job {cron_job_id[:8]} at {at_time}")
    except Exception as e:
        # ROLLBACK: revert task to inbox
        log(f"ERROR: cron creation failed: {e}")
        mc_update_task(task_id,
            status="inbox",
            comment="[heartbeat-v2] rollback — cron creation failed")
        record_cb_failure(state)
        sys.exit(1)

# Step 9c: Update state
state["last_dispatched_id"] = task_id
state["dispatched_at"] = now_ms
state["dispatch_history"].append({
    "task_id": task_id,
    "at": now_ms,
    "cron_job_id": cron_job_id,
    "agent": agent_name,
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

# Step 9d: Notify #notifications (read-only informativo)
notif_msg = f"📋 **Heartbeat** dispatch: `{task_id[:8]}` — **{title}** → `{agent_name}`\nEligible: {len(eligible)} | In-progress: {len(in_progress)}"
send_discord(NOTIFICATIONS_CHANNEL, notif_msg)

log(f"DISPATCH: {task_id[:8]} → {agent_name} (cron: {cron_job_id[:8] if cron_job_id else 'n/a'})")
log("heartbeat-v2 complete")
PYEOF
