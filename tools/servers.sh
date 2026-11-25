#!/usr/bin/env bash
# llama-server control by config.yaml + config.local.yaml.
#   tools/servers.sh start  <model> [--server main] [--extra "-ctk q8_0 -ctv q8_0"] [--dry-run]
#   tools/servers.sh switch <model> [--server main]
#   tools/servers.sh stop   [--server main]
#   tools/servers.sh status [--server main]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${IMPASSE_PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
cd "$ROOT"
exec "$PY" -m harness.servers "$@"
