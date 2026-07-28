#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.dev.yml"

cd "${ROOT_DIR}"

mkdir -p "${ROOT_DIR}/data"

if [[ ! -f "${ROOT_DIR}/configs/config-dev.jsonc" ]]; then
  echo "Creating configs/config-dev.jsonc from sample..."
  cp "${ROOT_DIR}/configs/config-sample-1h.jsonc" "${ROOT_DIR}/configs/config-dev.jsonc"
  # Point data_folder to local ./data for docker mounts
  sed -i 's|"data_folder": "C:/DATA_ITB"|"data_folder": "/app/data"|' "${ROOT_DIR}/configs/config-dev.jsonc" \
    || sed -i '' 's|"data_folder": "C:/DATA_ITB"|"data_folder": "/app/data"|' "${ROOT_DIR}/configs/config-dev.jsonc"
fi

echo "Starting intelligent-trading-bot microservices (dev, hot-reload)..."
docker compose -f "${COMPOSE_FILE}" up -d --build

echo ""
echo "Services started:"
echo "  Web UI:    http://localhost:5173"
echo "  API:       http://localhost:8000"
echo "  API docs:  http://localhost:8000/docs"
echo "  Pipeline:  http://localhost:8001"
echo "  Trader:    http://localhost:8002"
echo "  Redis:     localhost:6379"
echo ""
echo "Hot reload is enabled for api / pipeline / trader / web."
echo "Stop with: bin/stop_dev.sh"
