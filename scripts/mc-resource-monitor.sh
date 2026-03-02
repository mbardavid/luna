#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/openclaw/.openclaw/openclaw.json}"
STATE_FILE="${MC_RESOURCE_STATE_FILE:-/home/openclaw/.openclaw/workspace/.mc-resource-state.json}"
MESSAGE_TIMEOUT="${MC_MESSAGE_TIMEOUT:-8}"

exec python3 - "$STATE_FILE" "$OPENCLAW_BIN" "$OPENCLAW_CONFIG" "$MESSAGE_TIMEOUT" "$@" <<'PY'
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time

state_file = sys.argv[1]
openclaw_bin = sys.argv[2]
openclaw_config = sys.argv[3]
message_timeout = float(sys.argv[4]) if sys.argv[4] else 8.0
argv = sys.argv[5:]

parser = argparse.ArgumentParser()
parser.add_argument("--warn-pct", type=float, default=float(os.environ.get("MC_RESOURCE_WARN_PCT", "80")))
parser.add_argument("--degrade-pct", type=float, default=float(os.environ.get("MC_RESOURCE_DEGRADE_PCT", "90")))
parser.add_argument("--recover-pct", type=float, default=float(os.environ.get("MC_RESOURCE_RECOVER_PCT", "85")))
parser.add_argument("--kill-pct", type=float, default=float(os.environ.get("MC_RESOURCE_KILL_PCT", "95")))
parser.add_argument("--channel", default=os.environ.get("MC_RESOURCE_CHANNEL", "1476255906894446644"))
parser.add_argument("--allowlist", default=os.environ.get("MC_RESOURCE_ALLOWLIST", "node_modules/.bin/*,/tmp/puppeteer*"))
parser.add_argument("--kill-allowlist", action="store_true")
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--test-mem-kb", type=int, default=0)
parser.add_argument("--state-stale-ms", type=int, default=int(os.environ.get("MC_RESOURCE_STATE_STALE_MS", str(6 * 60 * 60 * 1000))))
args = parser.parse_args(argv)


def run(cmd):
  cp = subprocess.run(cmd, text=True, capture_output=True)
  if cp.returncode != 0:
    raise RuntimeError(f"command failed: {' '.join(cmd)}\n{cp.stdout}\n{cp.stderr}")
  return cp.stdout.strip()


def parse_meminfo(path="/proc/meminfo"):
  total = None
  avail = None
  with open(path, "r", encoding="utf-8") as fp:
    for line in fp:
      match = re.match(r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB", line)
      if not match:
        continue
      metric = match.group(1)
      value = int(match.group(2))
      if metric == "MemTotal":
        total = value
      elif metric == "MemAvailable":
        avail = value
      if total is not None and avail is not None:
        break
  if total is None or avail is None:
    raise RuntimeError("MemTotal or MemAvailable not found in /proc/meminfo")
  return total, avail


def read_state():
  if not os.path.exists(state_file):
    return {
      "mode": "normal",
      "entered_at_ms": 0,
      "last_warn_at_ms": 0,
      "last_kill_at_ms": 0,
      "kill_count_total": 0,
      "last_check_ms": 0,
      "load_pct": None,
      "load_history": [],
    }
  try:
    with open(state_file, "r", encoding="utf-8") as fp:
      return json.load(fp)
  except Exception:
    return {
      "mode": "normal",
      "entered_at_ms": 0,
      "last_warn_at_ms": 0,
      "last_kill_at_ms": 0,
      "kill_count_total": 0,
      "last_check_ms": 0,
      "load_pct": None,
      "load_history": [],
    }


def write_state(data):
  os.makedirs(os.path.dirname(state_file), exist_ok=True)
  tmp_path = f"{state_file}.tmp"
  with open(tmp_path, "w", encoding="utf-8") as fp:
    json.dump(data, fp, indent=2, sort_keys=True)
  os.replace(tmp_path, state_file)


def send_message(channel, text):
  if args.dry_run:
    print(f"[dry-run] would send: {text}")
    return True
  cp = subprocess.run(
    [
      openclaw_bin,
      "message",
      "send",
      "--channel",
      "discord",
      "--target",
      str(channel),
      "--message",
      text,
    ],
    text=True,
    capture_output=True,
    timeout=message_timeout,
  )
  if cp.returncode != 0:
    raise RuntimeError(f"message send failed: {cp.stderr.strip()}")
  return True


def maybe_kill_allowlist(patterns, _limit_pct):
  if not args.kill_allowlist:
    return []

  proc = run(["ps", "-eo", "pid=,cmd="])
  kill_patterns = [pat.strip() for pat in patterns.split(",") if pat.strip()]
  if not kill_patterns:
    return []

  targets = []
  for line in proc.splitlines():
    if not line.strip():
      continue
    parts = line.strip().split(None, 1)
    if len(parts) != 2:
      continue
    pid_s, cmd = parts
    if not pid_s.isdigit():
      continue
    command = cmd.strip()
    if "openclaw" in command:
      continue
    if not any(
      fnmatch.fnmatch(command, pattern) or pattern in command for pattern in kill_patterns
    ):
      continue
    targets.append((int(pid_s), command))

  killed = []
  for pid, command in targets:
    try:
      os.kill(pid, 15)
      killed.append({"pid": pid, "command": command})
    except Exception:
      pass
  return killed


def mem_load_pct(total, available):
  return 0.0 if total == 0 else ((total - available) / total) * 100.0


def prune_load_history(history, now_ms):
  cutoff = now_ms - args.state_stale_ms
  cleaned = []
  for entry in history or []:
    if not isinstance(entry, dict):
      continue
    if int(entry.get("ts", 0)) >= cutoff:
      cleaned.append(entry)
  return cleaned[-12:]


state = read_state()
state.setdefault("mode", "normal")
state.setdefault("entered_at_ms", 0)
state.setdefault("last_warn_at_ms", 0)
state.setdefault("last_kill_at_ms", 0)
state.setdefault("kill_count_total", 0)
state.setdefault("last_check_ms", 0)
state.setdefault("load_pct", None)
state.setdefault("load_history", [])
state.setdefault("mode_events", 0)
state["mode"] = str(state.get("mode", "normal")).lower()
now_ms = int(time.time() * 1000)
state["load_history"] = prune_load_history(state.get("load_history", []), now_ms=now_ms)

if args.test_mem_kb > 0:
  mem_total = args.test_mem_kb
  mem_avail = int(args.test_mem_kb * 0.05)
else:
  mem_total, mem_avail = parse_meminfo()

load_pct = round(mem_load_pct(mem_total, mem_avail), 2)
in_degrade = state.get("mode") == "degrade"
events = []

if load_pct >= args.warn_pct and (now_ms - int(state.get("last_warn_at_ms", 0))) > 20 * 60 * 1000:
  events.append(f"⚠️ RAM em alerta: {load_pct}% usada (limiar={args.warn_pct}%).")
  state["last_warn_at_ms"] = now_ms

if load_pct >= args.degrade_pct and not in_degrade:
  state["mode"] = "degrade"
  state["entered_at_ms"] = now_ms
  state["mode_events"] = int(state.get("mode_events", 0)) + 1
  events.append(
    f"🛑 Resource degrade mode ativado: RAM {load_pct}% (>= {args.degrade_pct}%). "
    "Spawns críticos em pausa."
  )
elif load_pct <= args.recover_pct and in_degrade:
  state["mode"] = "normal"
  state["entered_at_ms"] = now_ms
  state["mode_events"] = int(state.get("mode_events", 0)) + 1
  events.append(
    f"✅ Recover de recursos: RAM {load_pct}% (<= {args.recover_pct}%). "
    "Retomando operação normal."
  )

# ─── CTO-ops: Gateway memory auto-restart ────────────────────────────
# Check gateway-specific memory (cgroup) separately from system RAM.
# If gateway memory > 80% of high watermark, trigger safe restart.
gateway_restart_triggered = False
try:
  gw_mem_path = "/sys/fs/cgroup/system.slice/openclaw-gateway.service/memory.current"
  gw_high_path = "/sys/fs/cgroup/system.slice/openclaw-gateway.service/memory.high"
  if os.path.exists(gw_mem_path) and os.path.exists(gw_high_path):
    gw_current = int(open(gw_mem_path).read().strip())
    gw_high_raw = open(gw_high_path).read().strip()
    gw_high = int(gw_high_raw) if gw_high_raw != "max" else 0
    if gw_high > 0:
      gw_pct = round(gw_current / gw_high * 100, 1)
      gw_current_mb = gw_current // (1024 * 1024)
      gw_high_mb = gw_high // (1024 * 1024)
      if gw_pct >= 80:
        # Check rate limit via state
        last_gw_restart_ms = int(state.get("last_gateway_restart_ms", 0))
        if (now_ms - last_gw_restart_ms) > 60 * 60 * 1000:  # max 1 per hour from resource-monitor
          restart_script = os.path.join(
            os.environ.get("WORKSPACE", "/home/openclaw/.openclaw/workspace"),
            "scripts", "gateway-safe-restart.sh"
          )
          if os.path.isfile(restart_script):
            reason = f"resource-monitor: gateway memory {gw_pct}% ({gw_current_mb}MB/{gw_high_mb}MB)"
            events.append(
              f"🔄 **CTO-ops auto-restart**: gateway em {gw_pct}% do watermark "
              f"({gw_current_mb}MB/{gw_high_mb}MB). Executando restart seguro..."
            )
            state["last_gateway_restart_ms"] = now_ms
            state["gateway_restart_count"] = int(state.get("gateway_restart_count", 0)) + 1
            if not args.dry_run:
              subprocess.Popen(
                [restart_script, "--auto", "--reason", reason],
                stdout=open(os.path.join(
                  os.environ.get("WORKSPACE", "/home/openclaw/.openclaw/workspace"),
                  "logs", "gateway-safe-restart.log"
                ), "a"),
                stderr=subprocess.STDOUT,
              )
              gateway_restart_triggered = True
          else:
            events.append(f"⚠️ Gateway em {gw_pct}% mas script de restart não encontrado")
        else:
          pass  # rate limited, skip silently
except Exception as e:
  pass  # non-fatal, don't break resource monitor

killed = []
if load_pct >= args.kill_pct and args.kill_allowlist:
  elapsed_ms = now_ms - int(state.get("last_kill_at_ms", 0))
  if elapsed_ms > 60 * 60 * 1000:
    killed = maybe_kill_allowlist(args.allowlist, load_pct)
    if killed:
      state["last_kill_at_ms"] = now_ms
      state["kill_count_total"] = int(state.get("kill_count_total", 0)) + len(killed)
      events.append(
        f"🧨 Kill em allowlist aplicado ({len(killed)} processo(s)): "
        + ", ".join(map(str, [entry.get("pid") for entry in killed[:8]]))
      )
    else:
      events.append("⚠️ kill permitido ativo, mas não há processos compatíveis para encerramento seguro.")

state["load_history"] = prune_load_history(
  state["load_history"] + [{"ts": now_ms, "load_pct": load_pct}],
  now_ms=now_ms,
)
state.update(
  {
    "last_check_ms": now_ms,
    "load_pct": load_pct,
    "mode": state.get("mode", "normal"),
    "updated_at_ms": now_ms,
    "openclaw_config": openclaw_config,
  }
)
write_state(state)

if events:
  payload = "\n".join(events)
  send_message(args.channel, payload)

print(
  json.dumps(
    {
      "load_pct": load_pct,
      "mode": state.get("mode"),
      "event_count": len(events),
      "events": events,
      "killed": len(killed),
      "channel": args.channel,
      "dry_run": args.dry_run,
      "state_file": state_file,
    },
    sort_keys=True,
  )
)
PY
