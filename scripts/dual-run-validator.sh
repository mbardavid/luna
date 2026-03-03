#!/usr/bin/env bash
# dual-run-validator.sh — Compare old detector logs vs heartbeat-v3 logs
#
# Validates that heartbeat-v3 covers all detection categories previously
# handled by individual cron scripts. Produces a JSON report + human summary.
#
# Categories:
#   1. failure    — mc-failure-detector.sh → heartbeat-v3 Phase 4
#   2. stale      — mc-stale-task-detector  → heartbeat-v3 Phase 5.5
#   3. description — mc-description-watchdog.sh → heartbeat-v3 Phase 4.8
#   4. gateway    — gateway-wake-sentinel.sh → heartbeat-v3 Phase 1
#   5. pmm        — pmm-status-updater.sh → heartbeat-v3 Phase 1 (PMM)
#
# Classification:
#   MATCH    — Both old and new detect the same events
#   ENHANCED — heartbeat-v3 detects MORE than the old script
#   GAP      — Old script detects something heartbeat-v3 misses
#   NONE     — Neither system detected events (no data)
#
# Usage:
#   bash scripts/dual-run-validator.sh [--hours N] [--verbose] [--help]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${WORKSPACE:-$(dirname "$SCRIPT_DIR")}"
LOG_DIR="${WORKSPACE}/logs"
STATE_DIR="${WORKSPACE}/state"
REPORT_FILE="${STATE_DIR}/dual-run-report.json"

# Defaults
HOURS=24
VERBOSE=0

# ── Arg parsing ──────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Compare old detector logs vs heartbeat-v3 logs to validate migration coverage.

Options:
  --hours N     Look back N hours (default: 24)
  --verbose     Show detailed log excerpts
  --help        Show this help

Categories checked:
  failure       mc-failure-detector.sh → heartbeat-v3 Phase 4
  stale         mc-stale-task-detector  → heartbeat-v3 Phase 5.5
  description   mc-description-watchdog.sh → heartbeat-v3 Phase 4.8
  gateway       gateway-wake-sentinel.sh → heartbeat-v3 Phase 1
  pmm           pmm-status-updater.sh → heartbeat-v3 Phase 1 (PMM)

Output:
  JSON report:  state/dual-run-report.json
  Human summary: stdout
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hours)
            HOURS="${2:-24}"
            shift 2
            ;;
        --verbose)
            VERBOSE=1
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            ;;
    esac
done

# ── Utility functions ────────────────────────────────────────────────────────

# Get cutoff ISO timestamp for --hours filtering (used by awk)
get_cutoff_iso() {
    date -u -d "${HOURS} hours ago" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || \
    date -u -v-"${HOURS}"H '+%Y-%m-%d %H:%M:%S' 2>/dev/null || \
    echo "1970-01-01 00:00:00"
}

# Filter log lines within the time window using awk (fast)
filter_by_time() {
    local file="$1"
    local cutoff
    cutoff=$(get_cutoff_iso)

    if [[ ! -f "$file" || ! -s "$file" ]]; then
        return 0
    fi

    awk -v cutoff="$cutoff" '
    {
        # Extract timestamp: [2026-03-02 23:50:02] or [2026-03-02 18:30:11 UTC]
        if (match($0, /\[?([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})/, m)) {
            ts = m[1]
            if (ts >= cutoff) print
        }
    }' "$file"
}

# Count matching lines in a file within time window
count_matches() {
    local file="$1"
    local pattern="$2"
    if [[ ! -f "$file" ]]; then
        echo 0
        return
    fi
    local result
    result=$(filter_by_time "$file" | grep -cE "$pattern" 2>/dev/null) || true
    echo "${result:-0}"
}

# Get matching lines (for verbose mode)
get_matches() {
    local file="$1"
    local pattern="$2"
    if [[ ! -f "$file" ]]; then
        return 0
    fi
    local result
    result=$(filter_by_time "$file" | grep -E "$pattern" 2>/dev/null) || true
    if [[ -n "$result" ]]; then
        echo "$result"
    fi
}

# ── Category analysis functions ──────────────────────────────────────────────

# Category 1: Failure detection
analyze_failure() {
    local old_log="${LOG_DIR}/mc-failure-detector.log"
    local new_log="${LOG_DIR}/heartbeat-v3.log"

    local old_detections old_no_detect new_detections new_no_detect
    # Old: looks for actual failure lines (not "No failures")
    old_detections=$(count_matches "$old_log" '\[failure-detector\].*[^N][^o] failure|FAILURE|failed session')
    old_no_detect=$(count_matches "$old_log" 'No failures detected')
    # New: Phase 4 with >0 failures
    new_detections=$(count_matches "$new_log" 'Phase 4:.*[1-9][0-9]* failure')
    new_no_detect=$(count_matches "$new_log" 'Phase 4:.*0 failure')

    local old_total=$((old_detections + old_no_detect))
    local new_total=$((new_detections + new_no_detect))

    classify "failure" "$old_total" "$old_detections" "$new_total" "$new_detections"
}

# Category 2: Stale task detection
analyze_stale() {
    local old_log="${LOG_DIR}/mc-stale-task-detector.log"
    local new_log="${LOG_DIR}/heartbeat-v3.log"

    local old_detections old_runs new_detections new_runs
    # Old: stale task detector log (may not exist if already deprecated)
    old_detections=$(count_matches "$old_log" 'stale|STALE')
    old_runs=$(count_matches "$old_log" '.')
    # New: Phase 5.5 stale detection
    new_detections=$(count_matches "$new_log" 'Phase 5\.5:.*[1-9][0-9]* stale')
    new_runs=$(count_matches "$new_log" 'Phase 5\.5:')

    classify "stale" "$old_runs" "$old_detections" "$new_runs" "$new_detections"
}

# Category 3: Description quality
analyze_description() {
    local old_log="${LOG_DIR}/mc-description-watchdog.log"
    local new_log="${LOG_DIR}/heartbeat-v3.log"

    local old_detections old_runs new_detections new_runs
    # Old: description watchdog
    old_detections=$(count_matches "$old_log" 'poor descriptions|no structure|⚠️')
    old_runs=$(count_matches "$old_log" '.')
    # New: Phase 4.8
    new_detections=$(count_matches "$new_log" 'Phase 4\.8:.*[^O][^K]|Description quality.*poor|description quality.*[1-9]')
    new_runs=$(count_matches "$new_log" 'Phase 4\.8:')

    classify "description" "$old_runs" "$old_detections" "$new_runs" "$new_detections"
}

# Category 4: Gateway health
analyze_gateway() {
    local old_log="${LOG_DIR}/gateway-wake-sentinel.log"
    local new_log="${LOG_DIR}/heartbeat-v3.log"

    local old_detections old_runs new_detections new_runs
    # Old: sentinel restart detection
    old_detections=$(count_matches "$old_log" 'RESTART DETECTED|gateway restarted')
    old_runs=$(count_matches "$old_log" '\[sentinel\]')
    # New: Phase 1 gateway status
    new_detections=$(count_matches "$new_log" 'gateway unreachable|Gateway.*restart|Phase 1:.*Gateway.*fail')
    new_runs=$(count_matches "$new_log" 'Phase 1:.*Gateway')

    classify "gateway" "$old_runs" "$old_detections" "$new_runs" "$new_detections"
}

# Category 5: PMM monitoring
analyze_pmm() {
    local old_log="${LOG_DIR}/pmm-status-updater.log"
    local new_log="${LOG_DIR}/heartbeat-v3.log"

    local old_detections old_runs new_detections new_runs
    # Old: PMM crash/recovery
    old_detections=$(count_matches "$old_log" 'PMM crashed|PMM recovered')
    old_runs=$(count_matches "$old_log" '.')
    # New: Phase 1 PMM status
    new_detections=$(count_matches "$new_log" 'PMM dead|PMM was dead|PMM.*auto-restart|PMM.*restart')
    new_runs=$(count_matches "$new_log" 'Phase 1:.*PMM|PMM:')

    classify "pmm" "$old_runs" "$old_detections" "$new_runs" "$new_detections"
}

# ── Classification logic ────────────────────────────────────────────────────

# Global arrays to collect results (bash 4+)
declare -a RESULTS_CATEGORIES=()
declare -a RESULTS_CLASSIFICATIONS=()
declare -a RESULTS_OLD_RUNS=()
declare -a RESULTS_OLD_DETECTIONS=()
declare -a RESULTS_NEW_RUNS=()
declare -a RESULTS_NEW_DETECTIONS=()

classify() {
    local category="$1"
    local old_runs="$2"
    local old_detections="$3"
    local new_runs="$4"
    local new_detections="$5"

    local classification

    if [[ "$old_runs" -eq 0 && "$new_runs" -eq 0 ]]; then
        classification="NONE"
    elif [[ "$old_runs" -eq 0 && "$new_runs" -gt 0 ]]; then
        classification="ENHANCED"
    elif [[ "$old_runs" -gt 0 && "$new_runs" -eq 0 ]]; then
        classification="GAP"
    elif [[ "$new_detections" -ge "$old_detections" ]]; then
        if [[ "$new_runs" -gt "$old_runs" ]]; then
            classification="ENHANCED"
        else
            classification="MATCH"
        fi
    else
        # New detected fewer events — possible gap
        classification="GAP"
    fi

    local idx=${#RESULTS_CATEGORIES[@]}
    RESULTS_CATEGORIES+=("$category")
    RESULTS_CLASSIFICATIONS+=("$classification")
    RESULTS_OLD_RUNS+=("$old_runs")
    RESULTS_OLD_DETECTIONS+=("$old_detections")
    RESULTS_NEW_RUNS+=("$new_runs")
    RESULTS_NEW_DETECTIONS+=("$new_detections")
}

# ── Run all analyses ────────────────────────────────────────────────────────

echo "═══════════════════════════════════════════════════════════════"
echo "  Dual-Run Validator — heartbeat-v3 Migration Coverage Check"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Time window: last ${HOURS} hour(s)"
echo "Workspace:   ${WORKSPACE}"
echo "Log dir:     ${LOG_DIR}"
echo ""

analyze_failure
analyze_stale
analyze_description
analyze_gateway
analyze_pmm

# ── Human-readable output ────────────────────────────────────────────────────

echo "┌──────────────┬────────────────┬────────────┬────────────┬────────────┬────────────┐"
echo "│ Category     │ Classification │ Old Runs   │ Old Detect │ New Runs   │ New Detect │"
echo "├──────────────┼────────────────┼────────────┼────────────┼────────────┼────────────┤"

total_match=0
total_enhanced=0
total_gap=0
total_none=0

for i in "${!RESULTS_CATEGORIES[@]}"; do
    cat="${RESULTS_CATEGORIES[$i]}"
    cls="${RESULTS_CLASSIFICATIONS[$i]}"
    or="${RESULTS_OLD_RUNS[$i]}"
    od="${RESULTS_OLD_DETECTIONS[$i]}"
    nr="${RESULTS_NEW_RUNS[$i]}"
    nd="${RESULTS_NEW_DETECTIONS[$i]}"

    # Color/emoji by classification
    local_icon=""
    case "$cls" in
        MATCH)    local_icon="✅"; ((total_match++)) || true ;;
        ENHANCED) local_icon="🚀"; ((total_enhanced++)) || true ;;
        GAP)      local_icon="⚠️ "; ((total_gap++)) || true ;;
        NONE)     local_icon="➖"; ((total_none++)) || true ;;
    esac

    printf "│ %-12s │ %s %-11s │ %10s │ %10s │ %10s │ %10s │\n" \
        "$cat" "$local_icon" "$cls" "$or" "$od" "$nr" "$nd"
done

echo "└──────────────┴────────────────┴────────────┴────────────┴────────────┴────────────┘"
echo ""

# Summary
echo "Summary:"
echo "  ✅ MATCH:    ${total_match}"
echo "  🚀 ENHANCED: ${total_enhanced}"
echo "  ⚠️  GAP:      ${total_gap}"
echo "  ➖ NONE:     ${total_none}"
echo ""

if [[ "$total_gap" -gt 0 ]]; then
    echo "⚠️  WARNING: ${total_gap} category(ies) show gaps — review before deprecating old scripts!"
else
    echo "✅ All categories covered by heartbeat-v3 — safe to deprecate old scripts."
fi

# ── Verbose output ───────────────────────────────────────────────────────────

if [[ "$VERBOSE" -eq 1 ]]; then
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Detailed Log Excerpts (last ${HOURS}h)"
    echo "═══════════════════════════════════════════════════════════════"

    echo ""
    echo "── failure (mc-failure-detector.log) ──"
    get_matches "${LOG_DIR}/mc-failure-detector.log" '.' | tail -5

    echo ""
    echo "── failure (heartbeat-v3.log Phase 4) ──"
    get_matches "${LOG_DIR}/heartbeat-v3.log" 'Phase 4:' | tail -5

    echo ""
    echo "── stale (heartbeat-v3.log Phase 5.5) ──"
    get_matches "${LOG_DIR}/heartbeat-v3.log" 'Phase 5\.5:' | tail -5

    echo ""
    echo "── description (mc-description-watchdog.log) ──"
    get_matches "${LOG_DIR}/mc-description-watchdog.log" '.' | tail -5

    echo ""
    echo "── description (heartbeat-v3.log Phase 4.8) ──"
    get_matches "${LOG_DIR}/heartbeat-v3.log" 'Phase 4\.8:' | tail -5

    echo ""
    echo "── gateway (gateway-wake-sentinel.log) ──"
    get_matches "${LOG_DIR}/gateway-wake-sentinel.log" '\[sentinel\]' | tail -5

    echo ""
    echo "── gateway (heartbeat-v3.log Phase 1 Gateway) ──"
    get_matches "${LOG_DIR}/heartbeat-v3.log" 'Phase 1:.*Gateway' | tail -5

    echo ""
    echo "── pmm (pmm-status-updater.log) ──"
    get_matches "${LOG_DIR}/pmm-status-updater.log" 'PMM' | tail -5

    echo ""
    echo "── pmm (heartbeat-v3.log Phase 1 PMM) ──"
    get_matches "${LOG_DIR}/heartbeat-v3.log" 'PMM' | tail -5
fi

# ── JSON report ──────────────────────────────────────────────────────────────

mkdir -p "$STATE_DIR"

python3 - "$REPORT_FILE" "$HOURS" \
    "${RESULTS_CATEGORIES[*]}" \
    "${RESULTS_CLASSIFICATIONS[*]}" \
    "${RESULTS_OLD_RUNS[*]}" \
    "${RESULTS_OLD_DETECTIONS[*]}" \
    "${RESULTS_NEW_RUNS[*]}" \
    "${RESULTS_NEW_DETECTIONS[*]}" \
    "$total_match" "$total_enhanced" "$total_gap" "$total_none" \
    <<'PYEOF'
import json
import sys
from datetime import datetime, timezone

report_file = sys.argv[1]
hours = int(sys.argv[2])
categories = sys.argv[3].split()
classifications = sys.argv[4].split()
old_runs = [int(x) for x in sys.argv[5].split()]
old_detections = [int(x) for x in sys.argv[6].split()]
new_runs = [int(x) for x in sys.argv[7].split()]
new_detections = [int(x) for x in sys.argv[8].split()]
t_match = int(sys.argv[9])
t_enhanced = int(sys.argv[10])
t_gap = int(sys.argv[11])
t_none = int(sys.argv[12])

report = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "hours_analyzed": hours,
    "summary": {
        "match": t_match,
        "enhanced": t_enhanced,
        "gap": t_gap,
        "none": t_none,
        "safe_to_deprecate": t_gap == 0
    },
    "categories": []
}

for i, cat in enumerate(categories):
    report["categories"].append({
        "name": cat,
        "classification": classifications[i],
        "old_script": {
            "runs": old_runs[i],
            "detections": old_detections[i]
        },
        "heartbeat_v3": {
            "runs": new_runs[i],
            "detections": new_detections[i]
        }
    })

with open(report_file, "w") as f:
    json.dump(report, f, indent=2)

print(f"\n📄 JSON report written to: {report_file}")
PYEOF

echo ""
echo "Done."
