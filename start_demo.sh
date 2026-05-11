#!/usr/bin/env bash
# Author: L. Saetta
# Version: 0.9.0
# Last modified: 2026-05-11
# License: MIT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/compose.env.example"

read_env_value() {
  local key="$1"

  if [[ ! -f "${ENV_FILE}" ]]; then
    return 1
  fi

  awk -F= -v key="${key}" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      value = substr($0, index($0, "=") + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      print value
      exit
    }
  ' "${ENV_FILE}"
}

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "ERROR: compose.yaml not found in ${SCRIPT_DIR}" >&2
  exit 1
fi

cd "${SCRIPT_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "WARNING: .env not found. Docker Compose will use default values."
  if [[ -f "${ENV_EXAMPLE}" ]]; then
    echo "         To customize the demo, copy compose.env.example to .env."
  fi
fi

WEB_PORT="$(read_env_value DEPLOYER_WEB_PORT || true)"
API_PORT="$(read_env_value DEPLOYER_API_PORT || true)"
WEB_PORT="${WEB_PORT:-3000}"
API_PORT="${API_PORT:-8100}"

export DOCKER_SOCKET="${DOCKER_SOCKET:-/var/run/docker.sock}"
export DEPLOYER_DOCKER_HOST="${DEPLOYER_DOCKER_HOST:-unix:///var/run/docker.sock}"

echo "Starting OCI Enterprise AI Deployer demo with Docker Compose..."
docker compose -f "${COMPOSE_FILE}" up -d --build "$@"

echo
docker compose -f "${COMPOSE_FILE}" ps
echo
echo "Demo started."
echo "Web UI: http://localhost:${WEB_PORT}"
echo "API health: http://localhost:${API_PORT}/health"
