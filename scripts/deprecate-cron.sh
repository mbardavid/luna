#!/usr/bin/env bash
# deprecate-cron.sh — Safe cron deprecation helper
#
# Handles the full deprecation lifecycle for a cron script:
#   1. Backup current crontab
#   2. Comment out the cron line
#   3. Archive the script to scripts/archive/
#   4. Clean associated state files
#   5. Update docs/cron-inventory.md
#   6. Log to logs/deprecation-log.txt
#
# Idempotent: running twice on the same script is safe.
#
# Usage:
#   bash scripts/deprecate-cron.sh --script NAME --reason TEXT [--dry-run] [--help]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${WORKSPACE:-$(dirname "$SCRIPT_DIR")}"
ARCHIVE_DIR="${WORKSPACE}/scripts/archive"
LOG_DIR="${WORKSPACE}/logs"
DEPRECATION_LOG="${LOG_DIR}/deprecation-log.txt"
CRON_INVENTORY="${WORKSPACE}/docs/cron-inventory.md"

# Args
TARGET_SCRIPT=""
REASON=""
DRY_RUN=0

# ── Arg parsing ──────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $(basename "$0") --script NAME --reason TEXT [--dry-run] [--help]

Safely deprecate a cron-managed script.

Required:
  --script NAME    Script filename (e.g., mc-failure-detector.sh)
  --reason TEXT    Reason for deprecation (e.g., "absorbed by heartbeat-v3 Phase 4")

Options:
  --dry-run        Show what would happen without making changes
  --help           Show this help

Actions performed:
  1. Backup crontab to /tmp/crontab-backup-<epoch>.txt
  2. Comment out matching cron line (prepend #DEPRECATED#)
  3. Archive script to scripts/archive/
  4. Clean associated state files in /tmp/
  5. Update docs/cron-inventory.md (mark as deprecated)
  6. Log action to logs/deprecation-log.txt

Safety:
  - Idempotent: re-running on already-deprecated scripts is a no-op
  - --dry-run to preview before committing
  - Crontab backup before any modification
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --script)
            TARGET_SCRIPT="${2:-}"
            shift 2
            ;;
        --reason)
            REASON="${2:-}"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
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

if [[ -z "$TARGET_SCRIPT" ]]; then
    echo "ERROR: --script is required" >&2
    usage
fi

if [[ -z "$REASON" ]]; then
    echo "ERROR: --reason is required" >&2
    usage
fi

# ── Utility functions ────────────────────────────────────────────────────────

log_action() {
    local msg="$1"
    local timestamp
    timestamp="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    local entry="[${timestamp}] ${msg}"

    echo "$entry"

    if [[ "$DRY_RUN" -eq 0 ]]; then
        mkdir -p "$(dirname "$DEPRECATION_LOG")"
        echo "$entry" >> "$DEPRECATION_LOG"
    fi
}

dry_prefix() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[DRY-RUN] "
    fi
}

# ── Pre-flight checks ───────────────────────────────────────────────────────

echo "═══════════════════════════════════════════════════════════════"
echo "  Cron Deprecation Helper"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "$(dry_prefix)Script:  ${TARGET_SCRIPT}"
echo "$(dry_prefix)Reason:  ${REASON}"
echo "$(dry_prefix)Workspace: ${WORKSPACE}"
echo ""

# Check if script exists (in scripts/ or heartbeat-v3/scripts/)
SCRIPT_PATH=""
for candidate in \
    "${WORKSPACE}/scripts/${TARGET_SCRIPT}" \
    "${WORKSPACE}/heartbeat-v3/scripts/${TARGET_SCRIPT}" \
    "${WORKSPACE}/${TARGET_SCRIPT}"; do
    if [[ -f "$candidate" ]]; then
        SCRIPT_PATH="$candidate"
        break
    fi
done

# Check if already archived
ALREADY_ARCHIVED=0
if [[ -f "${ARCHIVE_DIR}/${TARGET_SCRIPT}" ]]; then
    ALREADY_ARCHIVED=1
    echo "$(dry_prefix)ℹ️  Script already archived at ${ARCHIVE_DIR}/${TARGET_SCRIPT}"
fi

if [[ -z "$SCRIPT_PATH" && "$ALREADY_ARCHIVED" -eq 0 ]]; then
    echo "$(dry_prefix)⚠️  Script '${TARGET_SCRIPT}' not found in workspace. Continuing with cron/state cleanup..."
fi

# ── Step 1: Backup crontab ──────────────────────────────────────────────────

echo ""
echo "── Step 1: Crontab backup ──"

CRONTAB_BACKUP="/tmp/crontab-backup-$(date +%s).txt"

if crontab -l &>/dev/null; then
    if [[ "$DRY_RUN" -eq 0 ]]; then
        crontab -l > "$CRONTAB_BACKUP"
        log_action "BACKUP: crontab saved to ${CRONTAB_BACKUP}"
    else
        echo "$(dry_prefix)Would backup crontab to ${CRONTAB_BACKUP}"
    fi
else
    echo "$(dry_prefix)No crontab found — skipping backup"
fi

# ── Step 2: Comment out cron line ────────────────────────────────────────────

echo ""
echo "── Step 2: Comment out cron line ──"

CRON_FOUND=0
CRON_ALREADY_COMMENTED=0

if crontab -l &>/dev/null; then
    # Check if the script appears in crontab
    if crontab -l | grep -q "$TARGET_SCRIPT"; then
        # Check if already commented out
        if crontab -l | grep "$TARGET_SCRIPT" | grep -q "^#"; then
            CRON_ALREADY_COMMENTED=1
            echo "$(dry_prefix)ℹ️  Cron entry already commented out"
        else
            CRON_FOUND=1
            if [[ "$DRY_RUN" -eq 0 ]]; then
                # Comment out the line with #DEPRECATED# prefix
                crontab -l | sed "/$TARGET_SCRIPT/s/^/#DEPRECATED# /" | crontab -
                log_action "CRON: commented out cron entry for ${TARGET_SCRIPT}"
            else
                echo "$(dry_prefix)Would comment out cron entry:"
                crontab -l | grep "$TARGET_SCRIPT" | head -1
            fi
        fi
    else
        echo "$(dry_prefix)ℹ️  No cron entry found for ${TARGET_SCRIPT}"
    fi
else
    echo "$(dry_prefix)No crontab available"
fi

# ── Step 3: Archive script ───────────────────────────────────────────────────

echo ""
echo "── Step 3: Archive script ──"

if [[ "$ALREADY_ARCHIVED" -eq 1 ]]; then
    echo "$(dry_prefix)ℹ️  Already archived — skipping"
elif [[ -n "$SCRIPT_PATH" ]]; then
    if [[ "$DRY_RUN" -eq 0 ]]; then
        mkdir -p "$ARCHIVE_DIR"
        # Add deprecation header before archiving
        {
            echo "# DEPRECATED: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
            echo "# Reason: ${REASON}"
            echo "# Original location: ${SCRIPT_PATH}"
            echo "#"
            cat "$SCRIPT_PATH"
        } > "${ARCHIVE_DIR}/${TARGET_SCRIPT}"
        chmod +x "${ARCHIVE_DIR}/${TARGET_SCRIPT}"
        log_action "ARCHIVE: ${SCRIPT_PATH} → ${ARCHIVE_DIR}/${TARGET_SCRIPT}"
    else
        echo "$(dry_prefix)Would archive ${SCRIPT_PATH} → ${ARCHIVE_DIR}/${TARGET_SCRIPT}"
    fi
else
    echo "$(dry_prefix)ℹ️  No script to archive (not found)"
fi

# ── Step 4: Clean state files ────────────────────────────────────────────────

echo ""
echo "── Step 4: Clean state files ──"

# Map script names to their known state files
declare -A STATE_FILES=(
    ["mc-failure-detector.sh"]="/tmp/.mc-failure-detector-state.json"
    ["mc-stale-task-detector.sh"]="/tmp/.mc-stale-task-detector-state.json"
    ["mc-description-watchdog.sh"]="/tmp/.mc-description-watchdog-state.json"
    ["gateway-health-sentinel.sh"]="/tmp/.gateway-health-sentinel.json"
    ["pmm-status-updater.sh"]="/tmp/.pmm-status-updater-state.json"
    ["cron-health-check.sh"]="/tmp/.cron-health-check-state.json"
    ["escalation-recovery.sh"]="/tmp/.escalation-recovery-state.json"
)

# Also check for lock files with similar pattern
SCRIPT_BASE="${TARGET_SCRIPT%.sh}"

STATE_CLEANED=0
for state_path in "${STATE_FILES[$TARGET_SCRIPT]:-}" "/tmp/.${SCRIPT_BASE}-state.json" "/tmp/.${SCRIPT_BASE}.lock" "/tmp/.${SCRIPT_BASE}.json"; do
    if [[ -z "$state_path" ]]; then
        continue
    fi
    if [[ -f "$state_path" ]]; then
        if [[ "$DRY_RUN" -eq 0 ]]; then
            rm -f "$state_path"
            log_action "STATE: removed ${state_path}"
        else
            echo "$(dry_prefix)Would remove state file: ${state_path}"
        fi
        ((STATE_CLEANED++)) || true
    fi
done

if [[ "$STATE_CLEANED" -eq 0 ]]; then
    echo "$(dry_prefix)ℹ️  No state files found for ${TARGET_SCRIPT}"
fi

# ── Step 5: Update cron-inventory.md ─────────────────────────────────────────

echo ""
echo "── Step 5: Update cron-inventory.md ──"

if [[ -f "$CRON_INVENTORY" ]]; then
    # Check if already listed in deprecated section
    if grep -q "$TARGET_SCRIPT" "$CRON_INVENTORY" 2>/dev/null; then
        # Check if it's in the deprecated table already
        if sed -n '/Scripts Depreciados/,$ p' "$CRON_INVENTORY" | grep -q "$TARGET_SCRIPT" 2>/dev/null; then
            echo "$(dry_prefix)ℹ️  Already listed in deprecated section of cron-inventory.md"
        else
            # In active section — mark as deprecated
            if [[ "$DRY_RUN" -eq 0 ]]; then
                TODAY="$(date -u '+%Y-%m-%d')"
                # Add to deprecated table
                DEPRECATED_LINE="| \`${TARGET_SCRIPT}\` | ${REASON} | ${TODAY} |"

                # Insert before the last --- line in the deprecated table section
                if grep -q "Scripts Depreciados" "$CRON_INVENTORY"; then
                    # Append to the deprecated table
                    sed -i "/^## Scripts Depreciados/,/^---/{
                        /^---/{
                            i\\${DEPRECATED_LINE}
                        }
                    }" "$CRON_INVENTORY" 2>/dev/null || {
                        # Fallback: just append before the final ---
                        sed -i "/^\*Estado final/i\\${DEPRECATED_LINE}" "$CRON_INVENTORY" 2>/dev/null || true
                    }
                fi

                # Comment out from active table (change status to DEPRECIADO)
                sed -i "/${TARGET_SCRIPT}/s/\*\*ATIVO\*\*/~~DEPRECIADO~~/" "$CRON_INVENTORY" 2>/dev/null || true

                log_action "INVENTORY: updated ${CRON_INVENTORY} — marked ${TARGET_SCRIPT} as deprecated"
            else
                echo "$(dry_prefix)Would update ${CRON_INVENTORY} — mark ${TARGET_SCRIPT} as deprecated"
            fi
        fi
    else
        echo "$(dry_prefix)ℹ️  ${TARGET_SCRIPT} not found in cron-inventory.md"
        if [[ "$DRY_RUN" -eq 0 ]]; then
            TODAY="$(date -u '+%Y-%m-%d')"
            DEPRECATED_LINE="| \`${TARGET_SCRIPT}\` | ${REASON} | ${TODAY} |"
            # Try to add to deprecated table
            if grep -q "Scripts Depreciados" "$CRON_INVENTORY"; then
                sed -i "/^\*Estado final/i\\${DEPRECATED_LINE}" "$CRON_INVENTORY" 2>/dev/null || true
                log_action "INVENTORY: added ${TARGET_SCRIPT} to deprecated section"
            fi
        else
            echo "$(dry_prefix)Would add ${TARGET_SCRIPT} to deprecated section"
        fi
    fi
else
    echo "$(dry_prefix)⚠️  ${CRON_INVENTORY} not found"
fi

# ── Step 6: Summary ─────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Deprecation $(if [[ "$DRY_RUN" -eq 1 ]]; then echo "Preview"; else echo "Complete"; fi)"
echo "═══════════════════════════════════════════════════════════════"
echo ""

ACTIONS_TAKEN=0

if [[ "$CRON_FOUND" -eq 1 ]]; then
    echo "  ✅ Cron entry commented out"
    ((ACTIONS_TAKEN++)) || true
elif [[ "$CRON_ALREADY_COMMENTED" -eq 1 ]]; then
    echo "  ℹ️  Cron entry already commented (no-op)"
else
    echo "  ➖ No cron entry found"
fi

if [[ -n "$SCRIPT_PATH" && "$ALREADY_ARCHIVED" -eq 0 ]]; then
    echo "  ✅ Script archived to ${ARCHIVE_DIR}/${TARGET_SCRIPT}"
    ((ACTIONS_TAKEN++)) || true
elif [[ "$ALREADY_ARCHIVED" -eq 1 ]]; then
    echo "  ℹ️  Script already archived (no-op)"
else
    echo "  ➖ No script to archive"
fi

if [[ "$STATE_CLEANED" -gt 0 ]]; then
    echo "  ✅ ${STATE_CLEANED} state file(s) cleaned"
    ((ACTIONS_TAKEN++)) || true
else
    echo "  ➖ No state files to clean"
fi

echo "  ✅ Inventory/log updated"

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo ""
    echo "  ⚠️  DRY-RUN: No changes were made. Re-run without --dry-run to execute."
fi

echo ""
log_action "DEPRECATE: ${TARGET_SCRIPT} — ${REASON} (actions=${ACTIONS_TAKEN}, dry_run=${DRY_RUN})"
echo "Done."
