#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/.." && pwd)"

exec python3 "${WORKSPACE}/heartbeat-v3/scripts/validate_autonomy_architecture.py" "$@"
