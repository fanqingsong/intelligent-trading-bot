#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.dev.yml"

cd "${ROOT_DIR}"

mkdir -p "${ROOT_DIR}/data"

if [[ ! -f "${ROOT_DIR}/configs/config-dev.jsonc" ]]; then
  echo "Creating configs/config-dev.jsonc from A-share sample..."
  cp "${ROOT_DIR}/configs/config-ashare-1d.jsonc" "${ROOT_DIR}/configs/config-dev.jsonc"
  sed -i 's|"data_folder": "[^"]*"|"data_folder": "/app/data"|' "${ROOT_DIR}/configs/config-dev.jsonc" \
    || sed -i '' 's|"data_folder": "[^"]*"|"data_folder": "/app/data"|' "${ROOT_DIR}/configs/config-dev.jsonc"
fi

echo "Starting intelligent-trading-bot microservices (dev, hot-reload)..."
docker compose -f "${COMPOSE_FILE}" up -d --build

echo ""
echo "Services started:"
echo "  Web UI:       http://localhost:5174"
echo "  API:          http://localhost:8000"
echo "  API docs:     http://localhost:8000/docs"
echo "  Pipeline:     http://localhost:8001"
echo "  Prefect UI:   http://localhost:4200"
echo "  MLflow:       http://localhost:5000"
echo "  Kedro-Viz:    http://localhost:4141"
echo "  Redis:        localhost:6379"
echo "  Postgres:     localhost:5433 (itb/itb)"
echo ""
echo "Optional: migrate existing CSV tables into Postgres:"
echo "  DATABASE_URL=postgresql+psycopg://itb:itb@localhost:5433/itb \\"
echo "    python scripts/migrate_csv_to_postgres.py --seed-watchlist"
echo ""
echo "Jobs: Prefect itb-kedro-job + scheduled itb-daily-predict (see docs/prefect.md)."
echo "Hot reload is enabled for api / pipeline / web / viz."
echo "Stop with: bin/stop_dev.sh"
