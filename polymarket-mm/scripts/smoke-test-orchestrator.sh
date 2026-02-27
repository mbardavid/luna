#!/bin/bash
# ── PMM Smoke Test Orchestrator ─────────────────────────────────────
# Cron: */30 * * * *
# Pattern: "Bash Detecta, Cron Injeta, Agent Executa"
#
# State machine:
#   IDLE      → start next run if queued
#   RUNNING   → health check, kill if stuck
#   FINISHED  → trigger analysis via Luna (cron one-shot)
#   ANALYZING → wait for analysis to complete
#   ERROR     → retry or notify Discord
#
# State file: /tmp/.smoke-test-state.json
# PID file:   paper/data/paper_trading.pid

set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────
PROJECT_ROOT="/home/openclaw/.openclaw/workspace/polymarket-mm"
STATE_FILE="/tmp/.smoke-test-state.json"
PID_FILE="$PROJECT_ROOT/paper/data/paper_trading.pid"
DATA_DIR="$PROJECT_ROOT/paper/data"
RUNS_DIR="$PROJECT_ROOT/paper/runs"
LOG_FILE="$DATA_DIR/orchestrator.log"
DISCORD_CHANNEL="1473367119377731800"
MAX_CRASHES=3
STUCK_FACTOR="1.2"  # Kill if elapsed > target * factor

mkdir -p "$DATA_DIR" "$RUNS_DIR"

# ── Logging ──────────────────────────────────────────────────────────
log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG_FILE"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

# ── State helpers ────────────────────────────────────────────────────
read_state() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
    else
        echo '{}'
    fi
}

get_field() {
    local field="$1"
    local state
    state=$(read_state)
    echo "$state" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$field',''))" 2>/dev/null || echo ""
}

write_state() {
    echo "$1" > "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

update_field() {
    local field="$1"
    local value="$2"
    local state
    state=$(read_state)
    echo "$state" | python3 -c "
import json,sys
d=json.load(sys.stdin)
v='$value'
# Try to parse as JSON value
try:
    v=json.loads(v)
except:
    pass
d['$field']=v
json.dump(d, sys.stdout, indent=2)
" > "${STATE_FILE}.tmp" 2>/dev/null
    mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

# ── Process check ────────────────────────────────────────────────────
check_process() {
    if [ ! -f "$PID_FILE" ]; then
        echo "NO_PID"
        return
    fi

    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null || echo "")
    if [ -z "$pid" ]; then
        echo "NO_PID"
        return
    fi

    if kill -0 "$pid" 2>/dev/null; then
        echo "ALIVE"
    else
        echo "DEAD"
    fi
}

# ── Elapsed time ─────────────────────────────────────────────────────
elapsed_seconds() {
    local started_at
    started_at=$(get_field "started_at")
    if [ -z "$started_at" ]; then
        echo "0"
        return
    fi
    local start_epoch now_epoch
    start_epoch=$(date -d "$started_at" +%s 2>/dev/null || echo "0")
    now_epoch=$(date +%s)
    echo $(( now_epoch - start_epoch ))
}

# ── Discord notification ─────────────────────────────────────────────
notify_discord() {
    local message="$1"
    log "DISCORD: $message"
    openclaw message send --channel discord --target "$DISCORD_CHANNEL" --message "$message" 2>/dev/null || true
}

# ── Start a run ──────────────────────────────────────────────────────
start_run() {
    local config_path="$1"
    local run_id="$2"
    local duration_h="$3"

    log "Starting run $run_id with config $config_path (${duration_h}h)"

    cd "$PROJECT_ROOT"

    # Start paper trading in background
    nohup python3 -m paper.paper_runner \
        --config "$config_path" \
        --no-test-kill-switch \
        --run-pipeline \
        > "$DATA_DIR/paper_run_${run_id}.log" 2>&1 &

    local pid=$!
    echo "$pid" > "$PID_FILE"

    # Update state
    write_state "$(cat <<EOF
{
    "status": "RUNNING",
    "current_run_id": "$run_id",
    "current_config": "$config_path",
    "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "target_duration_h": $duration_h,
    "crash_count": 0,
    "next_run": null,
    "last_analysis_at": null,
    "pid": $pid
}
EOF
)"

    log "Run $run_id started (PID $pid)"
}

# ── Trigger analysis via Luna ────────────────────────────────────────
trigger_analysis() {
    local run_id="$1"
    local at_iso
    at_iso=$(date -u -d "+30 seconds" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)

    local briefing="Smoke test $run_id terminou. Resultados em paper/data/metrics_latest.json e paper/data/trades.jsonl.

Instruções:
1. Leia paper/data/metrics_latest.json + paper/data/live_state.json
2. Leia paper/data/trades.jsonl (últimas linhas)
3. Gere relatório em paper/reports/${run_id}-report.md
4. Avalie a hipótese testada (PASS/FAIL/INCONCLUSIVE)
5. Decida próximos parâmetros
6. Gere config do próximo run se aplicável
7. Notifique #general-luna com resumo"

    log "Triggering analysis for $run_id via cron one-shot"

    openclaw cron add \
        --at "$at_iso" \
        --agent main \
        --session isolated \
        --name "smoke-analyze-${run_id}" \
        --delete-after-run \
        --timeout-seconds 600 \
        --thinking medium \
        --no-deliver \
        --message "$briefing" 2>/dev/null || {
        log "ERROR: Failed to create analysis cron job"
        notify_discord "⚠️ Smoke test: falha ao criar cron de análise para $run_id"
    }

    update_field "status" "ANALYZING"
    update_field "last_analysis_at" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# ── Main Logic ───────────────────────────────────────────────────────
main() {
    log "=== Orchestrator check ==="

    local status
    status=$(get_field "status")
    local process_state
    process_state=$(check_process)

    log "State: status=$status, process=$process_state"

    case "$process_state" in
        ALIVE)
            # Process is running
            local elapsed target_h
            elapsed=$(elapsed_seconds)
            target_h=$(get_field "target_duration_h")
            target_h=${target_h:-4}
            local target_s
            target_s=$(echo "$target_h * 3600" | bc 2>/dev/null || echo "14400")
            local max_s
            max_s=$(echo "$target_s * $STUCK_FACTOR" | bc 2>/dev/null || echo "17280")

            log "RUNNING: elapsed=${elapsed}s, target=${target_s}s, max=${max_s}s"

            # Check if stuck
            if [ "$elapsed" -gt "${max_s%.*}" ] 2>/dev/null; then
                local pid
                pid=$(cat "$PID_FILE" 2>/dev/null)
                log "STUCK: killing PID $pid (elapsed $elapsed > max $max_s)"
                kill "$pid" 2>/dev/null || true
                sleep 2
                kill -9 "$pid" 2>/dev/null || true
                rm -f "$PID_FILE"
                update_field "status" "FINISHED"
                notify_discord "⚠️ Smoke test $(get_field current_run_id) killed (stuck ${elapsed}s)"
            fi
            ;;

        DEAD)
            # Process died — check if it finished normally or crashed
            local run_id
            run_id=$(get_field "current_run_id")
            rm -f "$PID_FILE"

            # Check if metrics were saved (indicates clean exit)
            if [ -f "$DATA_DIR/metrics_latest.json" ]; then
                log "FINISHED: run $run_id completed"
                update_field "status" "FINISHED"
                trigger_analysis "$run_id"
            else
                # Crash
                local crashes
                crashes=$(get_field "crash_count")
                crashes=${crashes:-0}
                crashes=$((crashes + 1))
                update_field "crash_count" "$crashes"

                if [ "$crashes" -ge "$MAX_CRASHES" ]; then
                    log "ERROR: $crashes crashes for $run_id — giving up"
                    update_field "status" "ERROR"
                    notify_discord "🔴 Smoke test $run_id crashed $crashes times. Stopping. Check logs."
                else
                    log "CRASHED: run $run_id (crash #$crashes/$MAX_CRASHES) — restarting"
                    local config
                    config=$(get_field "current_config")
                    local duration
                    duration=$(get_field "target_duration_h")
                    duration=${duration:-4}
                    notify_discord "⚠️ Smoke test $run_id crashed (attempt $crashes/$MAX_CRASHES). Restarting..."
                    start_run "$config" "$run_id" "$duration"
                fi
            fi
            ;;

        NO_PID)
            # No process running
            case "$status" in
                ANALYZING)
                    log "ANALYZING: waiting for Luna to finish"
                    ;;
                ERROR)
                    log "ERROR state — manual intervention needed"
                    ;;
                RUNNING)
                    # State says running but no PID — likely a race condition
                    log "INCONSISTENT: status=RUNNING but no PID. Setting to IDLE."
                    update_field "status" "IDLE"
                    ;;
                IDLE|"")
                    # Check if there's a next_run queued
                    local next_run
                    next_run=$(get_field "next_run")
                    if [ -n "$next_run" ] && [ "$next_run" != "null" ] && [ "$next_run" != "" ]; then
                        # Parse next_run (expects JSON: {"config": "...", "run_id": "...", "duration_h": N})
                        local config run_id duration
                        config=$(echo "$next_run" | python3 -c "import json,sys; d=json.load(sys.stdin) if isinstance(json.load(open('/dev/stdin') if False else sys.stdin), dict) else {}; print(d.get('config',''))" 2>/dev/null || echo "")
                        run_id=$(echo "$next_run" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('run_id',''))" 2>/dev/null || echo "")
                        duration=$(echo "$next_run" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('duration_h',4))" 2>/dev/null || echo "4")

                        if [ -n "$config" ] && [ -f "$PROJECT_ROOT/$config" ]; then
                            log "Starting queued run: $run_id"
                            start_run "$config" "$run_id" "$duration"
                            update_field "next_run" "null"
                        else
                            log "Queued run config not found: $config"
                        fi
                    else
                        log "IDLE: nothing to do"
                    fi
                    ;;
                *)
                    log "Unknown status: $status"
                    ;;
            esac
            ;;
    esac

    log "=== Check complete ==="
}

main "$@"
