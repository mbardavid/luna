#!/usr/bin/env bash
# test-dual-run-validator.sh — Tests for dual-run-validator.sh
#
# Creates fixture log data and verifies all classification outcomes.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
VALIDATOR="${WORKSPACE_ROOT}/scripts/dual-run-validator.sh"

# Test workspace with fixture data
TEST_DIR="$(mktemp -d /tmp/test-dual-run-XXXXXX)"
TEST_LOG_DIR="${TEST_DIR}/logs"
TEST_STATE_DIR="${TEST_DIR}/state"
mkdir -p "$TEST_LOG_DIR" "$TEST_STATE_DIR"

PASS=0
FAIL=0
TOTAL=0

cleanup() {
    rm -rf "$TEST_DIR"
}
trap cleanup EXIT

assert_contains() {
    local desc="$1"
    local haystack="$2"
    local needle="$3"
    ((TOTAL++)) || true

    if echo "$haystack" | grep -qF -- "$needle"; then
        echo "  ✅ PASS: $desc"
        ((PASS++)) || true
    else
        echo "  ❌ FAIL: $desc"
        echo "    Expected to find: '$needle'"
        echo "    In output (first 200 chars): ${haystack:0:200}"
        ((FAIL++)) || true
    fi
}

assert_not_contains() {
    local desc="$1"
    local haystack="$2"
    local needle="$3"
    ((TOTAL++)) || true

    if ! echo "$haystack" | grep -qF -- "$needle"; then
        echo "  ✅ PASS: $desc"
        ((PASS++)) || true
    else
        echo "  ❌ FAIL: $desc (found '$needle' but shouldn't)"
        ((FAIL++)) || true
    fi
}

assert_file_exists() {
    local desc="$1"
    local path="$2"
    ((TOTAL++)) || true

    if [[ -f "$path" ]]; then
        echo "  ✅ PASS: $desc"
        ((PASS++)) || true
    else
        echo "  ❌ FAIL: $desc (file not found: $path)"
        ((FAIL++)) || true
    fi
}

assert_json_field() {
    local desc="$1"
    local file="$2"
    local field="$3"
    local expected="$4"
    ((TOTAL++)) || true

    local actual
    actual=$(python3 -c "import json; d=json.load(open('$file')); print($field)" 2>/dev/null || echo "__ERROR__")

    if [[ "$actual" == "$expected" ]]; then
        echo "  ✅ PASS: $desc"
        ((PASS++)) || true
    else
        echo "  ❌ FAIL: $desc (expected='$expected', got='$actual')"
        ((FAIL++)) || true
    fi
}

# ── Generate timestamps within test window ────────────────────────────────

NOW_TS=$(date -u '+%Y-%m-%d %H:%M:%S')
RECENT_TS=$(date -u -d '30 minutes ago' '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -u '+%Y-%m-%d %H:%M:%S')

# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Test Suite: dual-run-validator.sh"
echo "═══════════════════════════════════════════════════════════════"

# ── Test 1: Empty logs (NONE classification) ──────────────────────────────
echo ""
echo "── Test 1: Empty logs → NONE classification ──"

# Create empty log files
> "${TEST_LOG_DIR}/mc-failure-detector.log"
> "${TEST_LOG_DIR}/mc-description-watchdog.log"
> "${TEST_LOG_DIR}/gateway-wake-sentinel.log"
> "${TEST_LOG_DIR}/pmm-status-updater.log"
> "${TEST_LOG_DIR}/heartbeat-v3.log"

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$VALIDATOR" --hours 1 2>&1) || true

assert_contains "Shows NONE for empty logs" "$OUTPUT" "NONE"
assert_contains "Shows safe to deprecate" "$OUTPUT" "safe to deprecate"
assert_file_exists "JSON report created" "${TEST_STATE_DIR}/dual-run-report.json"
assert_json_field "JSON safe_to_deprecate=True" "${TEST_STATE_DIR}/dual-run-report.json" "d['summary']['safe_to_deprecate']" "True"

# ── Test 2: MATCH classification ─────────────────────────────────────────
echo ""
echo "── Test 2: Both systems detect → MATCH ──"

# Populate failure detector with recent data
cat > "${TEST_LOG_DIR}/mc-failure-detector.log" <<EOF
[${RECENT_TS}] [failure-detector] No failures detected
[${RECENT_TS}] [failure-detector] No failures detected
[${RECENT_TS}] [failure-detector] No failures detected
EOF

# Populate heartbeat-v3 with matching Phase 4 data
cat > "${TEST_LOG_DIR}/heartbeat-v3.log" <<EOF
[${RECENT_TS}] Phase 1: Gateway OK
[${RECENT_TS}] Phase 4: 0 failure(s) detected
[${RECENT_TS}] Phase 4.5: Circuit breaker closed
[${RECENT_TS}] Phase 4.8: Description quality OK
[${RECENT_TS}] Phase 5.5: 0 qa-review, 0 orphan, 0 stale
[${RECENT_TS}] Phase 1: Gateway OK
[${RECENT_TS}] Phase 4: 0 failure(s) detected
[${RECENT_TS}] Phase 4.8: Description quality OK
[${RECENT_TS}] Phase 5.5: 0 qa-review, 0 orphan, 0 stale
EOF

# Clear other logs
> "${TEST_LOG_DIR}/mc-description-watchdog.log"
> "${TEST_LOG_DIR}/gateway-wake-sentinel.log"
> "${TEST_LOG_DIR}/pmm-status-updater.log"

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$VALIDATOR" --hours 1 2>&1) || true

assert_contains "Shows MATCH for failure category" "$OUTPUT" "MATCH"
assert_contains "Table header present" "$OUTPUT" "Category"

# ── Test 3: ENHANCED classification ──────────────────────────────────────
echo ""
echo "── Test 3: heartbeat-v3 detects more → ENHANCED ──"

# Old log: nothing
> "${TEST_LOG_DIR}/mc-failure-detector.log"
> "${TEST_LOG_DIR}/mc-description-watchdog.log"
> "${TEST_LOG_DIR}/gateway-wake-sentinel.log"
> "${TEST_LOG_DIR}/pmm-status-updater.log"

# heartbeat-v3: active with detections
cat > "${TEST_LOG_DIR}/heartbeat-v3.log" <<EOF
[${RECENT_TS}] Phase 1: Gateway OK
[${RECENT_TS}] Phase 1: PMM alive (PID 12345)
[${RECENT_TS}] Phase 4: 0 failure(s) detected
[${RECENT_TS}] Phase 4.8: Description quality OK
[${RECENT_TS}] Phase 5.5: 0 qa-review, 0 orphan, 0 stale
[${RECENT_TS}] Phase 1: Gateway OK
[${RECENT_TS}] Phase 1: PMM alive (PID 12345)
[${RECENT_TS}] Phase 4: 0 failure(s) detected
[${RECENT_TS}] Phase 4.8: Description quality OK
[${RECENT_TS}] Phase 5.5: 0 qa-review, 1 orphan, 0 stale
EOF

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$VALIDATOR" --hours 1 2>&1) || true

assert_contains "Shows ENHANCED when only new has data" "$OUTPUT" "ENHANCED"

# ── Test 4: GAP classification ───────────────────────────────────────────
echo ""
echo "── Test 4: Old detects, new doesn't → GAP ──"

# Old: has detections
cat > "${TEST_LOG_DIR}/gateway-wake-sentinel.log" <<EOF
[${RECENT_TS}] [sentinel] RESTART DETECTED: boot=510409:1772072440 (was=99999:1000000000)
[${RECENT_TS}] [sentinel] WAKE: gateway restarted, briefing injected (wake #1)
[${RECENT_TS}] [sentinel] Same boot — no restart detected
[${RECENT_TS}] [sentinel] RESTART DETECTED: boot=724125:1772117289 (was=510409:1772072440)
EOF

# heartbeat-v3: no gateway data
cat > "${TEST_LOG_DIR}/heartbeat-v3.log" <<EOF
[${RECENT_TS}] Phase 4: 0 failure(s) detected
[${RECENT_TS}] Phase 4.8: Description quality OK
[${RECENT_TS}] Phase 5.5: 0 qa-review, 0 orphan, 0 stale
EOF

> "${TEST_LOG_DIR}/mc-failure-detector.log"
> "${TEST_LOG_DIR}/mc-description-watchdog.log"
> "${TEST_LOG_DIR}/pmm-status-updater.log"

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$VALIDATOR" --hours 1 2>&1) || true

assert_contains "Shows GAP when old has more detections" "$OUTPUT" "GAP"
assert_contains "Warns about gaps" "$OUTPUT" "WARNING"
assert_json_field "JSON gap > 0" "${TEST_STATE_DIR}/dual-run-report.json" "d['summary']['gap']" "1"
assert_json_field "JSON safe_to_deprecate=False" "${TEST_STATE_DIR}/dual-run-report.json" "d['summary']['safe_to_deprecate']" "False"

# ── Test 5: --help flag ──────────────────────────────────────────────────
echo ""
echo "── Test 5: --help flag ──"

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$VALIDATOR" --help 2>&1) || true

assert_contains "Help shows usage" "$OUTPUT" "Usage"
assert_contains "Help shows categories" "$OUTPUT" "failure"
assert_contains "Help mentions JSON report" "$OUTPUT" "JSON report"

# ── Test 6: --verbose flag ───────────────────────────────────────────────
echo ""
echo "── Test 6: --verbose flag ──"

# Set up some data
cat > "${TEST_LOG_DIR}/mc-failure-detector.log" <<EOF
[${RECENT_TS}] [failure-detector] No failures detected
EOF
cat > "${TEST_LOG_DIR}/heartbeat-v3.log" <<EOF
[${RECENT_TS}] Phase 1: Gateway OK
[${RECENT_TS}] Phase 4: 0 failure(s) detected
[${RECENT_TS}] Phase 4.8: Description quality OK
[${RECENT_TS}] Phase 5.5: 0 qa-review, 0 orphan, 0 stale
EOF
> "${TEST_LOG_DIR}/mc-description-watchdog.log"
> "${TEST_LOG_DIR}/gateway-wake-sentinel.log"
> "${TEST_LOG_DIR}/pmm-status-updater.log"

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$VALIDATOR" --hours 1 --verbose 2>&1) || true

assert_contains "Verbose shows log excerpts" "$OUTPUT" "Detailed Log Excerpts"
assert_contains "Verbose shows Phase 4 section" "$OUTPUT" "heartbeat-v3.log Phase 4"

# ── Test 7: Idempotency ──────────────────────────────────────────────────
echo ""
echo "── Test 7: Idempotency — running twice produces same result ──"

OUTPUT1=$(WORKSPACE="$TEST_DIR" bash "$VALIDATOR" --hours 1 2>&1) || true
OUTPUT2=$(WORKSPACE="$TEST_DIR" bash "$VALIDATOR" --hours 1 2>&1) || true

# Extract just the classification lines (ignore timestamps)
CLS1=$(echo "$OUTPUT1" | grep -E 'MATCH|ENHANCED|GAP|NONE' | head -5)
CLS2=$(echo "$OUTPUT2" | grep -E 'MATCH|ENHANCED|GAP|NONE' | head -5)

((TOTAL++)) || true
if [[ "$CLS1" == "$CLS2" ]]; then
    echo "  ✅ PASS: Idempotent — same classifications on re-run"
    ((PASS++)) || true
else
    echo "  ❌ FAIL: Non-idempotent — classifications changed"
    ((FAIL++)) || true
fi

# ── Test 8: JSON report structure ────────────────────────────────────────
echo ""
echo "── Test 8: JSON report structure ──"

assert_json_field "JSON has hours_analyzed" "${TEST_STATE_DIR}/dual-run-report.json" "d['hours_analyzed']" "1"
assert_json_field "JSON has 5 categories" "${TEST_STATE_DIR}/dual-run-report.json" "len(d['categories'])" "5"
assert_json_field "JSON category names correct" "${TEST_STATE_DIR}/dual-run-report.json" \
    "','.join(c['name'] for c in d['categories'])" "failure,stale,description,gateway,pmm"

# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Results: ${PASS}/${TOTAL} passed, ${FAIL} failed"
echo "═══════════════════════════════════════════════════════════════"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
