#!/usr/bin/env bash
# ==============================================================================
# Redwood Retail: Single-Command Teardown & Resource Destruction
# Wraps ./deploy.sh --teardown to cleanly tear down all cloud infrastructure.
# ==============================================================================
set -euo pipefail

REDWOOD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$REDWOOD_DIR/deploy.sh" --teardown "$@"
