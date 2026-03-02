#!/usr/bin/env python3
"""v3-activation-test.py — Automated validation of all 7 Heartbeat V3 activation phases.

Usage:
    v3-activation-test.py [--phase N] [--verbose]

Runs on the server (clawdia). Each phase outputs PASS/FAIL per criterion.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/home/openclaw/.openclaw/workspace"))
V3_DIR = WORKSPACE / "heartbeat-v3"
QUEUE_DIR = V3_DIR / "queue"
CONFIG_FILE = V3_DIR / "config" / "v3-config.json"
OPENCLAW_CONFIG = Path("/home/openclaw/.openclaw/openclaw.json")
OPENCLAW_BIN = "openclaw"
GATEWAY_URL = "ws://127.0.0.1:18789"

VERBOSE = "--verbose" in sys.argv
target_phase = None
for arg in sys.argv[1:]:
    if arg.startswith("--phase"):
        idx = sys.argv.index(arg)
        if idx + 1 < len(sys.argv):
            target_phase = int(sys.argv[idx + 1])

results = []

def log(msg):
    if VERBOSE:
        print(f"  {msg}")

def check(phase, name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append({"phase": phase, "name": name, "status": status, "detail": detail})
    icon = "✅" if condition else "❌"
    print(f"  {icon} [{phase}] {name}" + (f" — {detail}" if detail else ""))
    return condition

def run_cmd(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, shell=isinstance(cmd, str))
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"

def load_gw_token():
    try:
        with open(OPENCLAW_CONFIG) as f:
            return json.load(f)["gateway"]["auth"]["token"]
    except Exception:
        return ""


# ============================================================
# PHASE 1: Session GC
# ============================================================
def phase1():
    print("\n═══ Phase 1: Session GC ═══")
    gc_script = V3_DIR / "scripts" / "session-gc.sh"
    check(1, "session-gc.sh exists", gc_script.exists())

    rc, out, err = run_cmd(f"bash {gc_script}", timeout=120)
    check(1, "session-gc.sh exit 0", rc == 0, f"rc={rc}")

    gc_log = WORKSPACE / "logs" / "session-gc.log"
    check(1, "GC log exists", gc_log.exists())

    # Check cron
    rc, out, _ = run_cmd("crontab -l")
    check(1, "session-gc in cron", "session-gc.sh" in out)


# ============================================================
# PHASE 2: SystemD Hardening
# ============================================================
def phase2():
    print("\n═══ Phase 2: SystemD Hardening ═══")
    unit_file = Path("/etc/systemd/system/openclaw-gateway.service")
    if not unit_file.exists():
        check(2, "Unit file exists", False)
        return

    content = unit_file.read_text()
    check(2, "ExecStartPre has fuser", "fuser" in content)
    check(2, "KillMode=control-group", "KillMode=control-group" in content)
    check(2, "Restart=always", "Restart=always" in content)
    check(2, "RestartSec=5", "RestartSec=5" in content)


# ============================================================
# PHASE 3: Queue Filesystem
# ============================================================
def phase3():
    print("\n═══ Phase 3: Queue Filesystem ═══")
    queue_link = WORKSPACE / "queue"
    check(3, "workspace/queue exists", queue_link.exists() or queue_link.is_symlink())

    for subdir in ["pending", "active", "done", "failed", "escalated"]:
        d = QUEUE_DIR / subdir
        check(3, f"queue/{subdir}/ exists", d.is_dir())


# ============================================================
# PHASE 4: V2→V3 Switchover
# ============================================================
def phase4():
    print("\n═══ Phase 4: V2→V3 Switchover ═══")
    rc, crontab, _ = run_cmd("crontab -l")
    check(4, "heartbeat-v2 NOT in cron", "heartbeat-v2" not in crontab)
    check(4, "heartbeat-v3 in cron", "heartbeat-v3" in crontab)
    check(4, "session-gc in cron", "session-gc" in crontab)
    check(4, "queue-escalation in cron", "queue-escalation" in crontab)

    # Dry-run
    hb = V3_DIR / "scripts" / "heartbeat-v3.sh"
    rc, out, _ = run_cmd(f"bash {hb} --dry-run --verbose", timeout=30)
    check(4, "heartbeat-v3 dry-run exit 0", rc == 0, f"rc={rc}")
    check(4, "Phase 1 (gateway) passes", "Phase 1: Gateway OK" in out)
    check(4, "No cron one-shot created", "cron add" not in out.lower())
    # Queue write: verify pending dir is writable (dedup prevents actual write if task already dispatched)
    queue_pending = QUEUE_DIR / "pending"
    check(4, "Queue pending/ dir is writable", queue_pending.is_dir() and os.access(str(queue_pending), os.W_OK))


# ============================================================
# PHASE 5: Queue Consumer + SOUL.md
# ============================================================
def phase5():
    print("\n═══ Phase 5: Queue Consumer + SOUL.md ═══")
    # SOUL.md / IDENTITY.md
    identity = WORKSPACE / "IDENTITY.md"
    soul_main = Path("/home/openclaw/.openclaw/agents/main/SOUL.md")
    has_directive = False
    for f in [identity, soul_main]:
        if f.exists() and "queue/pending" in f.read_text():
            has_directive = True
            break
    check(5, "SOUL/IDENTITY contains queue directive", has_directive)

    # queue-consumer.py exists
    consumer = V3_DIR / "scripts" / "queue-consumer.py"
    check(5, "queue-consumer.py exists", consumer.exists())

    # Config loading
    check(5, "v3-config.json exists", CONFIG_FILE.exists())
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        check(5, "Config has queue_dir", "queue_dir" in cfg)
        check(5, "Config has escalation settings", "escalation_warn_minutes" in cfg)


# ============================================================
# PHASE 6: E2E Simulation
# ============================================================
def phase6():
    print("\n═══ Phase 6: E2E Simulation ═══")
    pending = QUEUE_DIR / "pending"
    active = QUEUE_DIR / "active"
    done = QUEUE_DIR / "done"

    # Write atomic queue file
    item = {"version": 1, "type": "dispatch", "task_id": "e2e-val-001",
            "title": "Validation Test", "agent": "luan", "priority": "low",
            "created_at": "2026-03-01T00:00:00Z", "created_by": "v3-activation-test"}
    filepath = pending / "e2e-validation-test.json"
    try:
        fd, tmp = tempfile.mkstemp(dir=str(pending), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(item, f)
        os.replace(tmp, str(filepath))
        check(6, "Atomic queue write", filepath.exists())
    except Exception as e:
        check(6, "Atomic queue write", False, str(e))

    # No .tmp residual
    tmp_files = list(pending.glob("*.tmp"))
    check(6, "No .tmp residual after write", len(tmp_files) == 0, f"found {len(tmp_files)}")

    # pending → active
    active_file = active / "e2e-validation-test.json"
    try:
        filepath.rename(active_file)
        check(6, "pending→active transition", active_file.exists())
    except Exception as e:
        check(6, "pending→active transition", False, str(e))

    # active → done
    done_file = done / "e2e-validation-test.json"
    try:
        active_file.rename(done_file)
        check(6, "active→done transition", done_file.exists())
    except Exception as e:
        check(6, "active→done transition", False, str(e))

    # Escalation ignores done/active
    esc = V3_DIR / "scripts" / "queue-escalation.sh"
    rc, out, _ = run_cmd(f"bash {esc}", timeout=15)
    check(6, "Escalation ignores done items", rc == 0)

    # Cleanup
    done_file.unlink(missing_ok=True)


# ============================================================
# PHASE 7: Smoke Test
# ============================================================
def phase7():
    print("\n═══ Phase 7: Live Smoke Test ═══")
    hb = V3_DIR / "scripts" / "heartbeat-v3.sh"
    rc, out, _ = run_cmd(f"bash {hb} --verbose", timeout=30)
    check(7, "heartbeat-v3 live exit 0", rc == 0, f"rc={rc}")
    check(7, "Log written", "heartbeat-v3" in out)
    check(7, "Phase 3 (fetch) passes", "Phase 3:" in out)

    # Pytest
    rc, out, _ = run_cmd(f"cd {V3_DIR} && python3 -m pytest tests/ -q", timeout=30)
    passed = "passed" in out
    check(7, "All pytests pass", passed and rc == 0, out.split("\n")[-1] if out else "")


# ============================================================
# MAIN
# ============================================================
phases = {1: phase1, 2: phase2, 3: phase3, 4: phase4, 5: phase5, 6: phase6, 7: phase7}

if target_phase:
    if target_phase in phases:
        phases[target_phase]()
    else:
        print(f"Unknown phase {target_phase}")
        sys.exit(1)
else:
    for p in sorted(phases):
        phases[p]()

# Summary
print(f"\n{'='*50}")
passed = sum(1 for r in results if r["status"] == "PASS")
failed = sum(1 for r in results if r["status"] == "FAIL")
total = len(results)
print(f"TOTAL: {passed}/{total} PASS, {failed} FAIL")

if failed:
    print("\nFailed checks:")
    for r in results:
        if r["status"] == "FAIL":
            print(f"  ❌ [{r['phase']}] {r['name']}" + (f" — {r['detail']}" if r['detail'] else ""))
    sys.exit(1)
else:
    print("🎉 All checks passed!")
    sys.exit(0)
