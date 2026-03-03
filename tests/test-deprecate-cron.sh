#!/usr/bin/env bash
# test-deprecate-cron.sh — Tests for deprecate-cron.sh
#
# Tests dry-run mode, idempotency, archive behavior, and inventory updates.
# All tests use --dry-run or isolated workspace to avoid real system changes.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
DEPRECATOR="${WORKSPACE_ROOT}/scripts/deprecate-cron.sh"

# Test workspace
TEST_DIR="$(mktemp -d /tmp/test-deprecate-XXXXXX)"
mkdir -p "${TEST_DIR}/scripts" "${TEST_DIR}/scripts/archive" "${TEST_DIR}/logs" "${TEST_DIR}/docs" "${TEST_DIR}/state"

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
        echo "    In output (first 300 chars): ${haystack:0:300}"
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

assert_file_not_exists() {
    local desc="$1"
    local path="$2"
    ((TOTAL++)) || true

    if [[ ! -f "$path" ]]; then
        echo "  ✅ PASS: $desc"
        ((PASS++)) || true
    else
        echo "  ❌ FAIL: $desc (file exists but shouldn't: $path)"
        ((FAIL++)) || true
    fi
}

assert_file_contains() {
    local desc="$1"
    local file="$2"
    local needle="$3"
    ((TOTAL++)) || true

    if [[ -f "$file" ]] && grep -qF "$needle" "$file"; then
        echo "  ✅ PASS: $desc"
        ((PASS++)) || true
    else
        echo "  ❌ FAIL: $desc (file '$file' doesn't contain '$needle')"
        ((FAIL++)) || true
    fi
}

# ── Setup fixture files ──────────────────────────────────────────────────

setup_fixture() {
    # Create a fake script
    cat > "${TEST_DIR}/scripts/mc-failure-detector.sh" <<'EOF'
#!/usr/bin/env bash
# mc-failure-detector.sh — Detect subagent failures
echo "detecting failures..."
EOF
    chmod +x "${TEST_DIR}/scripts/mc-failure-detector.sh"

    # Create a cron inventory
    cat > "${TEST_DIR}/docs/cron-inventory.md" <<'EOF'
# Cron Inventory

## Crons Ativos

| # | Freq | Script | Status |
|---|------|--------|--------|
| 1 | */5 | mc-failure-detector.sh | **ATIVO** |
| 2 | */5 | heartbeat-v3.sh | **ATIVO** |

## Scripts Depreciados (Arquivados)

| Script | Absorvido por / Razão | Data |
|--------|-----------------------|------|

---

*Estado final pós-migração.*
EOF

    # Clean archive
    rm -rf "${TEST_DIR}/scripts/archive"
    mkdir -p "${TEST_DIR}/scripts/archive"

    # Clean deprecation log
    rm -f "${TEST_DIR}/logs/deprecation-log.txt"
}

# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Test Suite: deprecate-cron.sh"
echo "═══════════════════════════════════════════════════════════════"

# ── Test 1: --help flag ──────────────────────────────────────────────────
echo ""
echo "── Test 1: --help flag ──"

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$DEPRECATOR" --help 2>&1) || true

assert_contains "Help shows usage" "$OUTPUT" "Usage"
assert_contains "Help shows --script" "$OUTPUT" "--script"
assert_contains "Help shows --dry-run" "$OUTPUT" "--dry-run"
assert_contains "Help shows --reason" "$OUTPUT" "--reason"

# ── Test 2: Missing required args ────────────────────────────────────────
echo ""
echo "── Test 2: Missing required args ──"

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$DEPRECATOR" 2>&1) || true
assert_contains "Error on missing --script" "$OUTPUT" "--script is required"

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$DEPRECATOR" --script test.sh 2>&1) || true
assert_contains "Error on missing --reason" "$OUTPUT" "--reason is required"

# ── Test 3: Dry-run mode ────────────────────────────────────────────────
echo ""
echo "── Test 3: Dry-run mode — no changes made ──"

setup_fixture

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$DEPRECATOR" \
    --script mc-failure-detector.sh \
    --reason "absorbed by heartbeat-v3" \
    --dry-run 2>&1) || true

assert_contains "Dry-run shows DRY-RUN prefix" "$OUTPUT" "DRY-RUN"
assert_contains "Dry-run shows script name" "$OUTPUT" "mc-failure-detector.sh"
assert_contains "Dry-run shows reason" "$OUTPUT" "absorbed by heartbeat-v3"
assert_contains "Dry-run warns no changes" "$OUTPUT" "No changes were made"

# Verify nothing was actually changed
assert_file_exists "Original script still exists" "${TEST_DIR}/scripts/mc-failure-detector.sh"
assert_file_not_exists "Archive not created in dry-run" "${TEST_DIR}/scripts/archive/mc-failure-detector.sh"
assert_file_not_exists "Deprecation log not created in dry-run" "${TEST_DIR}/logs/deprecation-log.txt"

# ── Test 4: Actual deprecation (non-dry-run, isolated workspace) ────────
echo ""
echo "── Test 4: Actual deprecation in isolated workspace ──"

setup_fixture

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$DEPRECATOR" \
    --script mc-failure-detector.sh \
    --reason "absorbed by heartbeat-v3 Phase 4" 2>&1) || true

assert_contains "Shows completion" "$OUTPUT" "Complete"
assert_file_exists "Script archived" "${TEST_DIR}/scripts/archive/mc-failure-detector.sh"
assert_file_contains "Archive has deprecation header" "${TEST_DIR}/scripts/archive/mc-failure-detector.sh" "DEPRECATED"
assert_file_contains "Archive has reason" "${TEST_DIR}/scripts/archive/mc-failure-detector.sh" "absorbed by heartbeat-v3 Phase 4"
assert_file_exists "Deprecation log created" "${TEST_DIR}/logs/deprecation-log.txt"
assert_file_contains "Log mentions the script" "${TEST_DIR}/logs/deprecation-log.txt" "mc-failure-detector.sh"

# ── Test 5: Idempotency — running twice is safe ─────────────────────────
echo ""
echo "── Test 5: Idempotency — running twice ──"

# Don't re-setup — run on already-deprecated script
OUTPUT2=$(WORKSPACE="$TEST_DIR" bash "$DEPRECATOR" \
    --script mc-failure-detector.sh \
    --reason "absorbed by heartbeat-v3 Phase 4" 2>&1) || true

assert_contains "Second run detects already archived" "$OUTPUT2" "already archived"

# Verify archive still has correct content (not double-wrapped)
HEADER_COUNT=$(grep -c "^# DEPRECATED:" "${TEST_DIR}/scripts/archive/mc-failure-detector.sh")
((TOTAL++)) || true
if [[ "$HEADER_COUNT" -eq 1 ]]; then
    echo "  ✅ PASS: Archive not double-wrapped on re-run"
    ((PASS++)) || true
else
    echo "  ❌ FAIL: Archive has ${HEADER_COUNT} deprecation headers (expected 1)"
    ((FAIL++)) || true
fi

# ── Test 6: Non-existent script ──────────────────────────────────────────
echo ""
echo "── Test 6: Non-existent script ──"

OUTPUT=$(WORKSPACE="$TEST_DIR" bash "$DEPRECATOR" \
    --script nonexistent-script.sh \
    --reason "test" \
    --dry-run 2>&1) || true

assert_contains "Handles missing script gracefully" "$OUTPUT" "not found"
assert_not_contains "No crash" "$OUTPUT" "unbound variable"

# ── Test 7: Inventory update ─────────────────────────────────────────────
echo ""
echo "── Test 7: Cron inventory update ──"

setup_fixture

WORKSPACE="$TEST_DIR" bash "$DEPRECATOR" \
    --script mc-failure-detector.sh \
    --reason "absorbed by heartbeat-v3 Phase 4" 2>&1 >/dev/null || true

# Check that inventory was updated
assert_file_contains "Inventory marks script as deprecated" "${TEST_DIR}/docs/cron-inventory.md" "DEPRECIADO"

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
