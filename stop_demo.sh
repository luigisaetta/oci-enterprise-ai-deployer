#!/usr/bin/env bash
# Author: L. Saetta
# Version: 0.9.0
# Last modified: 2026-05-11
# License: MIT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "ERROR: compose.yaml not found in ${SCRIPT_DIR}" >&2
  exit 1
fi

cd "${SCRIPT_DIR}"

echo "Stopping OCI Enterprise AI Deployer demo..."
docker compose -f "${COMPOSE_FILE}" down "$@"

echo
echo "Demo stopped."
