#!/usr/bin/env bash
# gateway-wake-sentinel.sh — Post-restart wake-up detector
#
# Runs every 1min via cron. Detects gateway restart and wakes Luna
# with a briefing about orphaned tasks and system state.
#
# Flow:
#   1. Quick health check via gateway status
#   2. Compare startedAt with saved value
#   3. If changed (restart): collect state → build briefing → inject via cron one-shot
#   4. Cooldown: 5min between wake-ups
#
# State: /tmp/.gateway-wake-sentinel.json
# Log:   logs/gateway-wake-sentinel.log
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
import tempfile
from datetime import datetime, timezone

# === CONFIG ===
WORKSPACE = os.environ.get("WORKSPACE", "/home/openclaw/.openclaw/workspace")

SCRIPTS_DIR = os.path.join(WORKSPACE, "scripts")
MC_CLIENT = os.path.join(SCRIPTS_DIR, "mc-client.sh")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
OPENCLAW_CONFIG = os.environ.get("OPENCLAW_CONFIG", "/home/openclaw/.openclaw/openclaw.json")
GATEWAY_URL = os.environ.get("MC_GATEWAY_URL", "ws://127.0.0.1:18789")
STATE_FILE = "/tmp/.gateway-wake-sentinel.json"
HEARTBEAT_STATE_FILE = "/tmp/.heartbeat-check-state.json"
LOG_DIR = os.path.join(WORKSPACE, "logs")
LOG_FILE = os.path.join(LOG_DIR, "gateway-wake-sentinel.log")

# Tuning
WAKE_COOLDOWN_MS = 5 * 60 * 1000    # 5min between wake-ups
CRON_TIMEOUT_SECONDS = 300           # 5min max for wake-up session

# Dry-run support
DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv or DRY_RUN

# === SETUP ===
os.makedirs(LOG_DIR, exist_ok=True)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [sentinel] {msg}"
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
    return {"last_boot_id": "", "last_wake_at": 0, "wake_count": 0}


def save_state(state):
    """Atomic write."""
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
    try:
        raw = run_cmd([MC_CLIENT, "list-tasks"], timeout=15)
        data = json.loads(raw or "{}")
        if isinstance(data, dict):
            return data.get("items", [])
        return []
    except Exception:
        return []


def iso_at(seconds_from_now=30):
    """Generate ISO timestamp for --at parameter."""
    ts = time.time() + seconds_from_now
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_memory_info():
    """Get memory usage info."""
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        info = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(":")] = int(parts[1])
        total_mb = info.get("MemTotal", 0) // 1024
        available_mb = info.get("MemAvailable", 0) // 1024
        used_mb = total_mb - available_mb
        return f"{used_mb}MB/{total_mb}MB ({int(used_mb/total_mb*100) if total_mb else 0}%)"
    except Exception:
        return "unknown"


# ============================================================
# Step 1: Quick health check — find gateway PID and its start time
# ============================================================
def find_gateway_pid():
    """Find the gateway process PID and its start time."""
    try:
        # Find the openclaw-gateway process
        result = subprocess.run(
            ["pgrep", "-f", "openclaw-gateway"],
            text=True, capture_output=True, timeout=5
        )
        if result.returncode != 0:
            return None, None
        pids = result.stdout.strip().split("\n")
        if not pids or not pids[0]:
            return None, None
        pid = pids[0].strip()
        # Get process start time from /proc/PID creation time
        proc_stat = os.stat(f"/proc/{pid}")
        start_time = str(int(proc_stat.st_mtime))
        return pid, start_time
    except Exception:
        return None, None


# Also do a quick RPC check to confirm gateway is responding
try:
    status = gateway_call("status", {})
except Exception as e:
    # Gateway down — not our problem. Exit silently.
    if VERBOSE:
        log(f"Gateway down: {e}")
    sys.exit(0)

# Get PID-based boot ID
gateway_pid, pid_start_time = find_gateway_pid()
if not gateway_pid or not pid_start_time:
    if VERBOSE:
        log("Could not find gateway PID")
    sys.exit(0)

# ============================================================
# Step 2: Compare boot ID (PID start time)
# ============================================================
current_boot = f"{gateway_pid}:{pid_start_time}"

state = load_state()
last_boot = state.get("last_boot_id", "")

if current_boot == last_boot:
    # Same boot — nothing to do
    if VERBOSE:
        log("Same boot — no restart detected")
    sys.exit(0)

# First run (no saved state) — just save and exit
if not last_boot:
    log(f"First run — saving boot ID: {current_boot}")
    state["last_boot_id"] = current_boot
    save_state(state)
    sys.exit(0)

# ============================================================
# Step 3: Cooldown check
# ============================================================
now_ms = int(time.time() * 1000)
last_wake = state.get("last_wake_at", 0)
if now_ms - last_wake < WAKE_COOLDOWN_MS:
    elapsed = (now_ms - last_wake) // 1000
    log(f"Cooldown active ({elapsed}s since last wake) — saving boot ID and exiting")
    state["last_boot_id"] = current_boot
    save_state(state)
    sys.exit(0)

# ============================================================
# Step 4: Collect state for briefing
# ============================================================
log(f"RESTART DETECTED: boot={current_boot} (was={last_boot})")

# Get MC tasks
tasks = mc_list_tasks()
in_progress = [t for t in tasks if str(t.get("status", "")).lower() == "in_progress"]

# Get heartbeat state
heartbeat_state = {}
try:
    with open(HEARTBEAT_STATE_FILE) as f:
        heartbeat_state = json.load(f)
except Exception:
    pass

# Get memory info
memory = get_memory_info()

# Get uptime from PID start time
uptime_secs = "?"
try:
    proc_start = int(pid_start_time) if pid_start_time else 0
    if proc_start > 0:
        uptime_secs = str(int(time.time()) - proc_start)
except Exception:
    pass

# ============================================================
# Step 5: Build briefing
# ============================================================
task_lines = []
for t in in_progress:
    tid = t.get("id", "")[:8]
    title = t.get("title", "?")
    fields = t.get("custom_field_values") or {}
    sk = str(fields.get("mc_session_key", "") or "")
    sk_display = f"...{sk[-12:]}" if sk else "(none)"
    task_lines.append(f"  - `{tid}` — **{title}** (session: `{sk_display}`)")

if not task_lines:
    task_lines.append("  (nenhuma)")

last_dispatch = heartbeat_state.get("last_dispatched_id", "")
last_dispatch_at = heartbeat_state.get("dispatched_at", 0)
dispatch_info = "(nenhum)"
if last_dispatch:
    mins_ago = (now_ms - last_dispatch_at) // 60000 if last_dispatch_at else "?"
    dispatch_info = f"`{last_dispatch[:8]}` — dispatched {mins_ago}min atrás"

briefing = f"""🔄 **Post-restart wake-up** — Gateway reiniciou.

## Estado detectado pelo sentinel
- **PID:** `{gateway_pid}` (boot: `{current_boot}`)
- **Boot anterior:** `{last_boot}`
- **Uptime:** {uptime_secs}s
- **Memória:** {memory}

## Tasks in_progress no MC (potencialmente órfãs)
{chr(10).join(task_lines)}

## Último dispatch do heartbeat
{dispatch_info}

## Ações recomendadas
1. Verificar sessões ativas via sessions.list — comparar com MC tasks in_progress
2. Para cada task órfã (sessão morta): avaliar se re-spawn ou mover pra review
3. Reportar status no Discord #general-luna
4. O heartbeat regular vai retomar em até 10min

## Constraints
- NÃO usar sudo systemctl stop/restart
- Máx 2 subagents simultâneos no pós-restart (conservar recursos)
- O sentinel detectou este restart e injetou este briefing automaticamente"""

# ============================================================
# Step 6: Inject via cron one-shot
# ============================================================
at_time = iso_at(30)  # Give gateway 30s to fully stabilize

if DRY_RUN:
    log(f"DRY-RUN: would create cron one-shot at {at_time}")
    log(f"DRY-RUN briefing:\n{briefing[:300]}...")
else:
    try:
        result = run_cmd([
            OPENCLAW_BIN, "cron", "add",
            "--at", at_time,
            "--agent", "main",
            "--session", "isolated",
            "--name", "post-restart-wake",
            "--delete-after-run",
            "--timeout-seconds", str(CRON_TIMEOUT_SECONDS),
            "--thinking", "medium",
            "--no-deliver",
            "--message", briefing,
            "--json",
        ], timeout=15)
        cron_data = json.loads(result) if result else {}
        log(f"WAKE: cron created, job={cron_data.get('id', '?')[:12]}, at={at_time}")
    except Exception as e:
        log(f"ERROR: wake cron creation failed: {e}")
        # Still save state to avoid re-triggering
        state["last_boot_id"] = current_boot
        save_state(state)
        sys.exit(1)

# ============================================================
# Step 7: Update state
# ============================================================
state["last_boot_id"] = current_boot
state["last_wake_at"] = now_ms
state["wake_count"] = state.get("wake_count", 0) + 1
save_state(state)

log(f"WAKE: gateway restarted, briefing injected (wake #{state['wake_count']})")
PYEOF
