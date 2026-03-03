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
ACTIVE_HOUR_START = V3_CONFIG.get("active_hour_start", 6)   # São Paulo local time
ACTIVE_HOUR_END = V3_CONFIG.get("active_hour_end", 24)      # 00:00 (midnight)
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

# Review dispatcher tuning
REVIEW_DISPATCH_COOLDOWN_MS = V3_CONFIG.get("review_dispatch_cooldown_minutes", 30) * 60 * 1000  # 30min default
REVIEW_STALE_IGNORE_DAYS = 14                        # Ignore review tasks older than 14 days

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
    state.setdefault("review_dispatched", {})
    # Phase 1 absorbed detectors state
    state.setdefault("absorbed", {
        "pmm_restarts": [],             # [{at: ms, pid: int}, ...]
        "alerted_description_violations": {},  # {task_id: {at: ms}}
        "completion_pending_notified": {},     # {task_id: {at: ms}}
    })
    absorbed = state["absorbed"]
    absorbed.setdefault("pmm_restarts", [])
    absorbed.setdefault("alerted_description_violations", {})
    absorbed.setdefault("completion_pending_notified", {})
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
    fields = task.get("custom_field_values") or {}

    return {
        "title": title,
        "agent": agent_name,
        "priority": priority,
        "context": {
            "description": description,
            "eligible_count": eligible_count,
            "in_progress_count": in_progress_count,
            "rejection_feedback": fields.get("mc_rejection_feedback", ""),
            "authorization_status": fields.get("mc_authorization_status", ""),
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


def build_review_payload(task: dict) -> dict:
    """Build the queue payload for a review dispatch to Luna."""
    title = task.get("title", "(sem título)")
    task_id = task.get("id", "")
    description = task.get("description", "")[:800]
    priority = task.get("priority", "medium")
    fields = task.get("custom_field_values") or {}

    return {
        "title": title,
        "agent": "main",  # Luna reviews, not Luan
        "priority": "high",
        "context": {
            "description": description,
            "original_agent": fields.get("mc_assigned_agent", "luan"),
            "session_key": fields.get("mc_session_key", ""),
            "review_depth": fields.get("mc_review_depth", "standard"),
            "risk_profile": fields.get("mc_risk_profile", "medium"),
            "rejection_feedback": fields.get("mc_rejection_feedback", ""),
            "authorization_status": fields.get("mc_authorization_status", ""),
        },
        "constraints": {
            "max_age_minutes": V3_CONFIG.get("escalation_critical_minutes", 30),
            "timeout_seconds": 900,  # Reviews may take longer
        },
        "spawn_params": {
            "agent": "main",
            "task_id": task_id,
            "title": title,
            "description": description,
            "priority": "high",
        },
    }


# === PMM CONFIG ===
PMM_CONFIG = V3_CONFIG.get("pmm", {})
PMM_AUTO_RESTART = PMM_CONFIG.get("auto_restart", True)
PMM_PID_FILE = os.path.join(WORKSPACE, PMM_CONFIG.get("pid_file", "polymarket-mm/paper/data/production_trading.pid"))
PMM_RESTART_COOLDOWN_MS = PMM_CONFIG.get("restart_cooldown_minutes", 5) * 60 * 1000
PMM_MAX_RESTARTS_PER_HOUR = PMM_CONFIG.get("max_restarts_per_hour", 3)
PMM_ENV_FILE = os.path.join(WORKSPACE, PMM_CONFIG.get("env_file", "polymarket-mm/.env"))
PMM_DEFAULT_CONFIG = os.path.join(WORKSPACE, PMM_CONFIG.get("default_config", "polymarket-mm/paper/runs/prod-003.yaml"))

# === DESCRIPTION QUALITY CONFIG ===
DESC_CONFIG = V3_CONFIG.get("description_quality", {})
DESC_MIN_LENGTH = DESC_CONFIG.get("min_length", 200)
DESC_MARKERS = DESC_CONFIG.get("required_markers", ["## ", "Objective", "Objetivo", "Context", "Criteria", "Problem", "Approach"])
DESC_CHECK_STATUSES = set(DESC_CONFIG.get("check_statuses", ["inbox", "in_progress", "review"]))

# === FAILURE CLASSIFICATION CONFIG ===
FC_CONFIG = V3_CONFIG.get("failure_classification", {})
FC_LOOP_THRESHOLD = FC_CONFIG.get("loop_threshold", 5)
FC_KNOWN_PROVIDER_ERRORS = FC_CONFIG.get("known_provider_errors", ["thinking.signature", "RESOURCE_EXHAUSTED", "capacity"])


def parse_env_file(env_path: str) -> dict:
    """Parse a .env file into a dict. Stdlib only (no python-dotenv)."""
    env = {}
    if not os.path.exists(env_path):
        return env
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Remove surrounding quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                env[key] = value
    except Exception:
        pass
    return env


def check_pmm_health(state: dict) -> dict:
    """
    Check if PMM bot is alive. If dead, auto-restart with cooldown.

    Absorbs: pmm-status-updater.sh PID check logic

    Returns:
        {"alive": bool, "pid": int|None, "restarted": bool, "error": str|None}
    """
    result = {"alive": False, "pid": None, "restarted": False, "error": None}

    if not PMM_AUTO_RESTART:
        result["error"] = "auto_restart disabled"
        return result

    # 1. Read PID file
    if not os.path.exists(PMM_PID_FILE):
        result["error"] = "no PID file"
        result["alive"] = None  # Unknown — never started
        return result

    try:
        with open(PMM_PID_FILE) as f:
            pid = int(f.read().strip())
        result["pid"] = pid
    except (ValueError, OSError) as e:
        result["error"] = f"bad PID file: {e}"
        return result

    # 2. Check if process alive (kill -0)
    try:
        os.kill(pid, 0)
        result["alive"] = True
        # Clear crash loop alert flag when PMM is healthy again
        absorbed = state.get("absorbed", {})
        if absorbed.get("pmm_crash_loop_alerted"):
            del absorbed["pmm_crash_loop_alerted"]
            state["absorbed"] = absorbed
        return result  # Running — all good
    except ProcessLookupError:
        result["alive"] = False
    except PermissionError:
        result["alive"] = True  # Process exists but we can't signal it
        return result

    # 3. Dead — check restart cooldown
    now_ms = int(time.time() * 1000)
    absorbed = state.get("absorbed", {})
    pmm_restarts = absorbed.get("pmm_restarts", [])

    # Trim to last hour
    one_hour_ago = now_ms - 3600 * 1000
    pmm_restarts = [r for r in pmm_restarts if r.get("at", 0) > one_hour_ago]

    # Check max restarts per hour
    if len(pmm_restarts) >= PMM_MAX_RESTARTS_PER_HOUR:
        result["error"] = f"max restarts/hour ({PMM_MAX_RESTARTS_PER_HOUR}) exceeded"
        log(f"PMM: restart suppressed — {len(pmm_restarts)} restarts in last hour")
        # Alert once when rate limit first triggers (crash loop detected)
        crash_loop_key = "pmm_crash_loop_alerted"
        if not absorbed.get(crash_loop_key):
            discord_alert(
                "⚠️ **PMM Crash Loop**: bot reiniciou "
                f"{len(pmm_restarts)}x na última hora e continua morrendo. "
                "Rate limit ativo — verificar kill switch / config.",
            )
            absorbed[crash_loop_key] = {"at": now_ms}
            state["absorbed"] = absorbed
        return result

    # Check cooldown from last restart
    if pmm_restarts:
        last_restart = max(r.get("at", 0) for r in pmm_restarts)
        if now_ms - last_restart < PMM_RESTART_COOLDOWN_MS:
            elapsed_s = (now_ms - last_restart) // 1000
            cooldown_s = PMM_RESTART_COOLDOWN_MS // 1000
            result["error"] = f"cooldown ({elapsed_s}s / {cooldown_s}s)"
            return result

    # 4. Attempt restart
    if DRY_RUN:
        log("PMM: DRY-RUN would restart PMM")
        result["restarted"] = True
        return result

    try:
        # Load .env for environment variables
        pmm_env = parse_env_file(PMM_ENV_FILE)
        env = {**os.environ, **pmm_env}

        # Find config file
        config_path = PMM_DEFAULT_CONFIG
        if not os.path.exists(config_path):
            result["error"] = f"config not found: {config_path}"
            return result

        pmm_dir = os.path.dirname(os.path.dirname(PMM_PID_FILE))  # polymarket-mm/paper/
        cmd = [
            sys.executable, "-m", "runner",
            "--mode", "live",
            "--config", config_path,
        ]

        proc = subprocess.Popen(
            cmd,
            cwd=pmm_dir,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Write new PID
        try:
            with open(PMM_PID_FILE, "w") as f:
                f.write(str(proc.pid))
        except Exception as e:
            log(f"PMM: restarted (PID {proc.pid}) but failed to write PID file: {e}")

        result["restarted"] = True
        result["pid"] = proc.pid
        result["alive"] = True

        # Record restart
        pmm_restarts.append({"at": now_ms, "pid": proc.pid})
        absorbed["pmm_restarts"] = pmm_restarts
        state["absorbed"] = absorbed

        log(f"PMM: auto-restarted (new PID {proc.pid})")

    except Exception as e:
        result["error"] = f"restart failed: {e}"
        log(f"PMM: restart FAILED: {e}")

    return result


def classify_failure(session_key: str) -> tuple:
    """
    Enhanced failure classification with 6 categories.

    Absorbs: mc-failure-detector.sh classification logic

    Returns:
        (failure_type, recommended_adjustment)

    Types:
        LOOP_DEGENERATIVO   — same tool called N+ times in last messages
        INCOMPLETE          — stopReason=stop/end_turn but no COMPLETION_STATUS
        THINKING_SIGNATURE  — "thinking.signature: Field required" error
        PROVIDER_ERROR      — API/provider level error (400, 429, 500, capacity)
        TIMEOUT             — session exceeded runTimeoutSeconds
        GENERIC_ERROR       — unclassifiable
    """
    failure_type = "GENERIC_ERROR"
    adjustments = "re-tentar sem ajustes específicos"

    try:
        history = gateway_call("chat.history", {
            "sessionKey": session_key,
            "limit": 5,
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

    # Check for known provider errors first (THINKING_SIGNATURE, RESOURCE_EXHAUSTED, etc.)
    for known_error in FC_KNOWN_PROVIDER_ERRORS:
        if known_error.lower() in combined:
            failure_type = "THINKING_SIGNATURE" if "thinking.signature" in known_error.lower() else "PROVIDER_ERROR"
            adjustments = f"erro de provider ({known_error}), trocar modelo ou aguardar"
            return failure_type, adjustments

    # Auth errors
    if "401" in combined or "unauthorized" in combined or "auth" in combined:
        failure_type = "PROVIDER_ERROR"
        adjustments = "verificar credenciais, possivelmente trocar modelo"
        return failure_type, adjustments

    # Timeout
    if "timeout" in combined or "timed out" in combined:
        failure_type = "TIMEOUT"
        adjustments = "aumentar runTimeoutSeconds (1.5x)"
        return failure_type, adjustments

    # OOM — classify as PROVIDER_ERROR
    if "oom" in combined or "out of memory" in combined or "signal 9" in combined or "killed" in combined:
        failure_type = "PROVIDER_ERROR"
        adjustments = "reduzir contexto, adicionar constraint de brevidade"
        return failure_type, adjustments

    # Rate limit — classify as PROVIDER_ERROR
    if "rate limit" in combined or "429" in combined or "quota" in combined:
        failure_type = "PROVIDER_ERROR"
        adjustments = "aguardar cooldown, possivelmente trocar modelo"
        return failure_type, adjustments

    # Loop degenerativo
    if len(tool_calls) >= FC_LOOP_THRESHOLD:
        tool_names = [
            str(tc.get("name", tc.get("function", {}).get("name", "")))
            for tc in tool_calls if isinstance(tc, dict)
        ]
        if tool_names and len(set(tool_names)) == 1:
            failure_type = "LOOP_DEGENERATIVO"
            adjustments = "simplificar task, trocar modelo"
            return failure_type, adjustments

    # Incomplete — session ended normally but no COMPLETION_STATUS
    if stop_reason in ("stop", "end_turn"):
        # Check if there's a COMPLETION_STATUS in the text
        if "completion_status" in combined:
            failure_type = "INCOMPLETE"
            adjustments = "re-spawn com 'continue de onde parou'"
        else:
            failure_type = "INCOMPLETE"
            adjustments = "re-spawn com 'continue de onde parou'"
        return failure_type, adjustments

    return failure_type, adjustments


def check_description_quality(tasks: list, state: dict) -> list:
    """
    Audit active task descriptions for quality.

    Absorbs: mc-description-watchdog.sh logic

    Checks:
        - Length >= MIN_LENGTH chars
        - Has structural markers (##, Objective, Context, Criteria)
        - No placeholder text

    Returns list of violations (task_id, title, issues).
    Dedup via state["absorbed"]["alerted_description_violations"].
    """
    absorbed = state.get("absorbed", {})
    alerted = absorbed.get("alerted_description_violations", {})
    violations = []

    for task in tasks:
        status = str(task.get("status", "")).lower()
        if status not in DESC_CHECK_STATUSES:
            continue

        task_id = task.get("id", "")
        if task_id in alerted:
            continue

        desc = task.get("description", "") or ""
        title = task.get("title", "?")
        issues = []

        if len(desc) < DESC_MIN_LENGTH:
            issues.append(f"short ({len(desc)} chars)")

        if not any(marker in desc for marker in DESC_MARKERS) and len(desc) < 500:
            issues.append("no structure")

        if issues:
            violations.append({
                "task_id": task_id,
                "title": title[:50],
                "status": status,
                "issues": ", ".join(issues),
            })
            alerted[task_id] = {"at": int(time.time() * 1000)}

    absorbed["alerted_description_violations"] = alerted
    state["absorbed"] = absorbed
    return violations


def check_session_completion(session_key: str) -> str:
    """
    Check if a dead session completed its task (has COMPLETION_STATUS).

    Returns: "complete", "partial", "blocked", or "" (not found/no completion).
    """
    try:
        history = gateway_call("chat.history", {
            "sessionKey": session_key,
            "limit": 5,
        })
    except Exception as e:
        log(f"WARN: chat.history for completion check failed ({session_key}): {e}")
        return ""

    messages = []
    if isinstance(history, dict):
        messages = history.get("messages", history.get("items", []))
    elif isinstance(history, list):
        messages = history

    # Search from newest to oldest for COMPLETION_STATUS
    for msg in reversed(messages):
        content = str(msg.get("content", "") or msg.get("text", "") or "")
        if "COMPLETION_STATUS:" in content:
            # Extract status value
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("COMPLETION_STATUS:"):
                    status_val = line.split(":", 1)[1].strip().lower()
                    if status_val in ("complete", "partial", "blocked"):
                        return status_val
        # Also check for structured completion markers
        if "status:" in content.lower() and ("complete" in content.lower() or "partial" in content.lower()):
            if "complete" in content.lower():
                return "complete"
            if "partial" in content.lower():
                return "partial"

    return ""


def detect_stale_and_completions(tasks: list, sessions_by_key: dict, state: dict) -> list:
    """
    Enhanced stale detection that also identifies completions pending QA.

    Absorbs: mc-stale-task-detector.sh logic

    Detects:
        1. ORPHAN: task in_progress/review with NO session_key
        2. COMPLETION_PENDING: task with dead session that had COMPLETION_STATUS
           → generates qa-review queue item
        3. STALE: task with dead session, no completion → existing respawn behavior

    Returns list of detection results.
    """
    absorbed = state.get("absorbed", {})
    notified_completions = absorbed.get("completion_pending_notified", {})
    now_ms = int(time.time() * 1000)
    completion_cooldown_ms = 30 * 60 * 1000  # 30 min cooldown

    results = []
    live_keys = set(sessions_by_key.keys())

    for task in tasks:
        status = str(task.get("status", "")).lower()
        if status not in ("in_progress", "review"):
            continue

        task_id = task.get("id", "")
        title = task.get("title", "?")
        fields = task.get("custom_field_values") or {}
        session_key = str(fields.get("mc_session_key", "") or "").strip()

        # Skip service tasks
        if any(str(title).startswith(pfx) for pfx in SERVICE_TITLE_PREFIXES):
            continue

        if not session_key:
            # ORPHAN — task without executor
            results.append({
                "type": "orphan",
                "task_id": task_id,
                "title": title,
                "status": status,
            })
            continue

        # Check if session is alive
        if session_key in live_keys:
            sess = sessions_by_key[session_key]
            s_status = str(sess.get("status", "")).lower()
            if s_status not in ("failed", "error", "ended"):
                continue  # Session alive — skip

        # Session is dead — check completion cooldown
        prev = notified_completions.get(task_id, {})
        prev_at = prev.get("at", 0) if isinstance(prev, dict) else 0
        if now_ms - prev_at < completion_cooldown_ms:
            continue  # Already notified recently

        # Try to determine if completion happened
        completion_status = check_session_completion(session_key)

        if completion_status in ("complete", "partial"):
            results.append({
                "type": "qa-review",
                "task_id": task_id,
                "title": title,
                "session_key": session_key,
                "completion_status": completion_status,
                "status": status,
            })
            notified_completions[task_id] = {"at": now_ms, "completion": completion_status}
        else:
            results.append({
                "type": "stale",
                "task_id": task_id,
                "title": title,
                "session_key": session_key,
                "status": status,
            })

    absorbed["completion_pending_notified"] = notified_completions
    state["absorbed"] = absorbed
    return results


# Backward compat alias for analyze_session_failure
analyze_session_failure_legacy = None  # will be set below


def build_qa_review_payload(task_id: str, title: str, session_key: str, completion_status: str) -> dict:
    """Build the queue payload for a qa-review item."""
    return {
        "title": title,
        "agent": "main",  # Luna reviews
        "priority": "high",
        "context": {
            "task_id": task_id,
            "task_title": title,
            "session_key": session_key,
            "completion_status": completion_status,
            "action": "QA review: ler completion report, inspecionar 2+ arquivos, rodar verification checks",
        },
        "constraints": {
            "max_age_minutes": V3_CONFIG.get("escalation_critical_minutes", 30),
            "timeout_seconds": 900,
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
# PHASE 1: Gateway health check + PMM health check
# ============================================================
try:
    gateway_call("sessions.list", {})
except Exception as e:
    log(f"SKIP: gateway unreachable: {e}")
    sys.exit(0)
log("Phase 1: Gateway OK")

# Phase 1 Enhanced: PMM Health Check
pmm_result = check_pmm_health(state)
if pmm_result.get("alive") is True:
    log(f"Phase 1: PMM alive (PID {pmm_result.get('pid', '?')})")
elif pmm_result.get("alive") is None:
    log(f"Phase 1: PMM status unknown ({pmm_result.get('error', 'no info')})")
elif pmm_result.get("restarted"):
    log(f"Phase 1: PMM was dead → auto-restarted (PID {pmm_result.get('pid', '?')})")
    send_discord(NOTIFICATIONS_CHANNEL,
        f"🔄 **PMM Auto-Restart**: bot was dead, restarted (PID {pmm_result.get('pid', '?')})")
    save_state(state)
else:
    error = pmm_result.get("error", "unknown")
    log(f"Phase 1: PMM dead, restart skipped ({error})")
    if "max restarts" in str(error):
        send_discord(DISCORD_CHANNEL,
            f"⚠️ **PMM**: dead but max restarts/hour exceeded. Requires manual intervention.")
        send_discord(NOTIFICATIONS_CHANNEL,
            f"⚠️ **PMM**: dead but max restarts/hour exceeded. Requires manual intervention.")

# ============================================================
# PHASE 2: Active hours check (São Paulo)
# ============================================================
if _has_zoneinfo:
    sp_tz = zoneinfo.ZoneInfo("America/Sao_Paulo")
else:
    sp_tz = dateutil.tz.gettz("America/Sao_Paulo")

sp_hour = datetime.now(sp_tz).hour
if ACTIVE_HOUR_START == 0 and ACTIVE_HOUR_END == 24:
    log(f"Phase 2: Active hours 24/7 ({sp_hour}h São Paulo)")
elif sp_hour < ACTIVE_HOUR_START:
    log(f"SKIP: outside active hours ({sp_hour}h São Paulo)")
    sys.exit(0)
else:
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

    # Analyze the failure (enhanced 6-category classification)
    failure_type, adjustments = classify_failure(session_key)
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
# PHASE 4.8: Description quality audit
# ============================================================
desc_violations = check_description_quality(tasks, state)
if desc_violations:
    violation_lines = []
    for v in desc_violations[:5]:  # Cap at 5 to avoid spam
        violation_lines.append(f"  • `{v['task_id'][:8]}` **{v['title']}** ({v['status']}): {v['issues']}")
    violation_msg = (
        f"⚠️ **Description Quality**: {len(desc_violations)} task(s) with poor descriptions\n"
        + "\n".join(violation_lines)
    )
    send_discord(NOTIFICATIONS_CHANNEL, violation_msg)
    save_state(state)
    log(f"Phase 4.8: {len(desc_violations)} description violation(s) found")
else:
    log("Phase 4.8: Description quality OK")

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
# PHASE 5.5: Stale dispatch detection + Completion pending QA
# ============================================================
# First: existing stale dispatch rollback for recently dispatched tasks
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

# Second: Enhanced completion/stale detection (absorbs mc-stale-task-detector.sh)
stale_results = detect_stale_and_completions(tasks, sessions_by_key, state)
qa_review_count = 0
orphan_count = 0
stale_count = 0

for result in stale_results:
    rtype = result.get("type", "")
    rtask_id = result.get("task_id", "")
    rtitle = result.get("title", "?")

    if rtype == "qa-review":
        # Completion pending QA → write qa-review queue item
        qa_payload = build_qa_review_payload(
            rtask_id, rtitle,
            result.get("session_key", ""),
            result.get("completion_status", "complete"),
        )
        queue_file = write_queue_item("qa-review", rtask_id, qa_payload)
        if queue_file:
            send_system_event_nudge(f"🔍 QA Review: {rtitle}", rtask_id)
            qa_msg = (
                f"🔍 **Completion Pending QA**: `{rtask_id[:8]}` — **{rtitle}**\n"
                f"Status: {result.get('completion_status', '?')} | Enfileirado para review."
            )
            send_discord(NOTIFICATIONS_CHANNEL, qa_msg)
        qa_review_count += 1

    elif rtype == "orphan":
        orphan_msg = (
            f"🟡 **Orphan Task**: `{rtask_id[:8]}` — **{rtitle}** ({result.get('status', '?')}): sem executor"
        )
        send_discord(NOTIFICATIONS_CHANNEL, orphan_msg)
        orphan_count += 1

    elif rtype == "stale":
        stale_count += 1

if stale_results:
    save_state(state)

log(f"Phase 5.5: {qa_review_count} qa-review, {orphan_count} orphan, {stale_count} stale")

# ============================================================
# PHASE 6: Review dispatcher (HIGH PRIORITY — runs before inbox)
#
# Review tasks from Luan take priority over new inbox dispatch.
# Routes to Luna (main) for validation. Luan never self-reviews.
# ============================================================
review_tasks = [
    t for t in tasks
    if str(t.get("status", "")).lower() == "review"
    and not any(str(t.get("title", "")).startswith(pfx) for pfx in SERVICE_TITLE_PREFIXES)
]
# Sort by updated_at (most recently moved to review first)
review_tasks.sort(key=lambda t: t.get("updated_at", t.get("created_at", "")), reverse=True)

# Filter: ignore stale reviews (>14 days) and already-dispatched (cooldown 2h)
review_dispatched = state.get("review_dispatched", {})
eligible_reviews = []
for t in review_tasks:
    tid = t.get("id", "")
    title_check = t.get("title", "?")

    # Age filter: skip reviews older than REVIEW_STALE_IGNORE_DAYS
    created_at = t.get("created_at", "")
    if created_at:
        try:
            from datetime import datetime as _dt
            task_age_days = (datetime.now(timezone.utc) - _dt.fromisoformat(created_at.replace("Z", "+00:00"))).days
            if task_age_days > REVIEW_STALE_IGNORE_DAYS:
                log(f"REVIEW SKIP: {tid[:8]} too old ({task_age_days}d): {title_check[:40]}")
                continue
        except Exception:
            pass

    # Cooldown filter: don't re-dispatch within 2h
    prev = review_dispatched.get(tid, {})
    prev_at = prev.get("at", 0) if isinstance(prev, dict) else 0
    if now_ms - prev_at < REVIEW_DISPATCH_COOLDOWN_MS:
        log(f"REVIEW SKIP: {tid[:8]} dispatched {(now_ms - prev_at) // 60000}min ago")
        continue

    eligible_reviews.append(t)

# Decision: if review tasks found, they take priority over inbox
next_task = None
dispatch_type = "inbox"  # default

if eligible_reviews:
    next_task = eligible_reviews[0]
    dispatch_type = "review"
    task_id = next_task.get("id", "")
    title = next_task.get("title", "(sem título)")
    description = next_task.get("description", "")[:500]
    log(f"Phase 6: Review task FOUND (priority=high): {task_id[:8]} — {title}")
    log(f"Phase 6: {len(eligible_reviews)} eligible review(s), skipping inbox")
else:
    log(f"Phase 6: No eligible review tasks ({len(review_tasks)} total)")

# ============================================================
# PHASE 7: Pull oldest inbox task (FIFO) — ONLY if Phase 6 found nothing
# ============================================================
if next_task is None:
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

        # Blocklist stall alert: only if some tasks are stuck for non-intentional reasons
        # Don't alert when ALL filtered tasks are in the explicit blocklist (intentional gating)
        non_blocklist_filtered = [t for t in inbox if t.get("id", "") not in blocked_ids]
        if inbox and filtered_count == len(inbox) and len(non_blocklist_filtered) > 0:
            blocklist_stall_key = "blocklist_stall_last_alert"
            last_stall_alert = state.get(blocklist_stall_key, 0)
            stall_alert_cooldown = 6 * 60 * 60 * 1000  # 6h
            if now_ms - last_stall_alert > stall_alert_cooldown:
                stall_msg = (
                    f"⚠️ **Blocklist Stall** — {len(non_blocklist_filtered)} inbox tasks stuck "
                    f"(deps unmet, etc). {len(blocked_ids)} intentionally gated. No work can proceed."
                )
                send_discord(NOTIFICATIONS_CHANNEL, stall_msg)
                state[blocklist_stall_key] = now_ms
                log(f"ALERT: blocklist stall — {len(non_blocklist_filtered)} stuck, {len(blocked_ids)} gated")
        elif inbox and filtered_count == len(inbox):
            log(f"IDLE: all {len(inbox)} inbox tasks intentionally gated — no alert needed")

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
    dispatch_type = "inbox"

    log(f"Phase 7: Eligible inbox task: {task_id[:8]} — {title}")

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
# Route: review tasks → Luna (main); inbox tasks → normal agent resolution
if dispatch_type == "review":
    agent_name = "main"  # Luna reviews
else:
    assigned_uuid = str(next_task.get("assigned_agent_id", "") or "")
    agent_name = resolve_agent_name(assigned_uuid, agent_mapping)

log(f"Phase 9: Dispatching {task_id[:8]} → {agent_name} (type={dispatch_type})")

# Step 9a: Mark task in_progress (for inbox) or add comment (for review — keep status)
if dispatch_type == "review":
    # Don't change status — task stays in review. Just add a comment.
    mc_update_task(task_id,
        comment=f"[heartbeat-v3] dispatching review to Luna for validation")
else:
    if not mc_update_task(task_id,
        status="in_progress",
        comment=f"[heartbeat-v3] dispatching to {agent_name} via queue"):
        log("ERROR: failed to mark task in_progress")
        record_cb_failure(state)
        sys.exit(1)

# Step 9b: Fast dispatch via openclaw agent (replaces queue + nudge)
if dispatch_type == "review":
    dispatch_payload = build_review_payload(next_task)
else:
    dispatch_payload = build_dispatch_payload(next_task, agent_name, len(eligible) if "eligible" in dir() else 0, len(in_progress))
fast_dispatch_script = os.path.join(WORKSPACE, "scripts", "mc-fast-dispatch.sh")

dispatch_ok = False
dispatch_method = "queue"  # fallback

if os.path.isfile(fast_dispatch_script) and os.access(fast_dispatch_script, os.X_OK):
    # Try fast dispatch first (direct openclaw agent call)
    try:
        description = next_task.get("description", "")[:2000]
        if dispatch_type == "review":
            task_msg = (
                f"## Review Task (HIGH PRIORITY)\n\n"
                f"**Título:** {title}\n"
                f"**MC Task ID:** {task_id}\n\n"
                f"Luan completou esta task e moveu para review. "
                f"Valide o trabalho do Luan:\n"
                f"1. Verifique workspace-luan/memory/active-tasks.md para status\n"
                f"2. Revise os arquivos criados/modificados\n"
                f"3. Rode testes se aplicável\n"
                f"4. Se aprovado, feche o MC card como done via mc-client.sh update-task {task_id} --status done\n"
                f"5. Se reprovado, mova de volta para in_progress com comentário explicando o que ajustar\n\n"
                f"## Descrição Original\n{description}"
            )
        else:
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

# Track review dispatch for cooldown dedup
if dispatch_type == "review":
    review_dispatched = state.get("review_dispatched", {})
    review_dispatched[task_id] = {"at": now_ms, "agent": agent_name}
    # Trim old entries (>7 days)
    review_dispatched = {k: v for k, v in review_dispatched.items()
                          if isinstance(v, dict) and now_ms - v.get("at", 0) < 7 * 24 * 3600 * 1000}
    state["review_dispatched"] = review_dispatched
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
if dispatch_type == "review":
    notif_msg = (
        f"🔍 **Heartbeat V3** review dispatch: `{task_id[:8]}` — **{title}** → `{agent_name}` (Luna)\n"
        f"Prioridade: HIGH | Reviews pendentes: {len(eligible_reviews)} | Via: {dispatch_method}"
    )
else:
    notif_msg = (
        f"📋 **Heartbeat V3** dispatch: `{task_id[:8]}` — **{title}** → `{agent_name}`\n"
        f"Eligible: {len(eligible) if 'eligible' in dir() else '?'} | In-progress: {len(in_progress)} | Via: {dispatch_method}"
    )
send_discord(NOTIFICATIONS_CHANNEL, notif_msg)

log(f"DISPATCH: {task_id[:8]} → {agent_name} (method: {dispatch_method})")
log("heartbeat-v3 complete")
