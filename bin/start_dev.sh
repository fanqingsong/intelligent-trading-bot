#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.dev.yml"
HASH_DIR="${ROOT_DIR}/.cache"
HASH_FILE="${HASH_DIR}/dev-image-deps.hash"

cd "${ROOT_DIR}"

mkdir -p "${ROOT_DIR}/data" "${HASH_DIR}"

if [[ ! -f "${ROOT_DIR}/configs/config-dev.jsonc" ]]; then
  echo "Creating configs/config-dev.jsonc from A-share sample..."
  cp "${ROOT_DIR}/configs/config-ashare-1d.jsonc" "${ROOT_DIR}/configs/config-dev.jsonc"
  sed -i 's|"data_folder": "[^"]*"|"data_folder": "/app/data"|' "${ROOT_DIR}/configs/config-dev.jsonc" \
    || sed -i '' 's|"data_folder": "[^"]*"|"data_folder": "/app/data"|' "${ROOT_DIR}/configs/config-dev.jsonc"
fi

FORCE_BUILD=0
for arg in "$@"; do
  case "${arg}" in
    --build|-b) FORCE_BUILD=1 ;;
    -h|--help)
      echo "Usage: bin/start_dev.sh [--build|-b]"
      echo "  Default: start containers; rebuild images only when Dockerfiles/requirements change"
      echo "  --build: force docker compose build"
      exit 0
      ;;
  esac
done

# Fingerprint of layers that actually install OS/Python/Node deps.
deps_fingerprint() {
  # Stable hash of files that affect image dependency layers.
  cat \
    "${ROOT_DIR}/requirements.txt" \
    "${ROOT_DIR}/requirements-services.txt" \
    "${ROOT_DIR}/requirements-ml.txt" \
    "${ROOT_DIR}/docker/Dockerfile.api" \
    "${ROOT_DIR}/docker/Dockerfile.pipeline" \
    "${ROOT_DIR}/docker/Dockerfile.web" \
    "${ROOT_DIR}/docker/Dockerfile.mlflow" \
    "${ROOT_DIR}/frontend/package.json" \
    "${ROOT_DIR}/frontend/package-lock.json" \
    2>/dev/null | md5sum | awk '{print $1}'
}

CURRENT_HASH="$(deps_fingerprint)"
PREV_HASH=""
if [[ -f "${HASH_FILE}" ]]; then
  PREV_HASH="$(cat "${HASH_FILE}")"
fi

NEED_BUILD=0
if [[ "${FORCE_BUILD}" -eq 1 ]]; then
  NEED_BUILD=1
  echo "Force rebuild requested (--build)."
elif [[ "${CURRENT_HASH}" != "${PREV_HASH}" ]]; then
  NEED_BUILD=1
  if [[ -z "${PREV_HASH}" ]]; then
    echo "No previous deps fingerprint; building images..."
  else
    echo "Dockerfiles/requirements/package-lock changed; rebuilding images..."
  fi
elif ! docker compose -f "${COMPOSE_FILE}" images -q 2>/dev/null | grep -q .; then
  NEED_BUILD=1
  echo "Project images missing; building..."
fi

# BuildKit enables Dockerfile cache mounts (pip wheel cache across rebuilds).
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

echo "Starting intelligent-trading-bot microservices (dev, hot-reload)..."
if [[ "${NEED_BUILD}" -eq 1 ]]; then
  docker compose -f "${COMPOSE_FILE}" up -d --build
  echo "${CURRENT_HASH}" > "${HASH_FILE}"
else
  echo "Deps unchanged — skipping image rebuild (pass --build to force)."
  docker compose -f "${COMPOSE_FILE}" up -d
fi

echo ""
echo "Services started:"
echo "  Web UI:       http://localhost:5174"
echo "  API:          http://localhost:8000"
echo "  API docs:     http://localhost:8000/docs"
echo "  Pipeline:     http://localhost:8001"
echo "  Prefect UI:   http://localhost:4200"
echo "  MLflow:       http://localhost:5000"
echo "  Redis:        localhost:6379"
echo "  Postgres:     localhost:5433 (itb/itb)"
echo ""
echo "Optional: migrate existing CSV tables into Postgres:"
echo "  DATABASE_URL=postgresql+psycopg://itb:itb@localhost:5433/itb \\"
echo "    python scripts/migrate_csv_to_postgres.py --seed-watchlist"
echo ""
echo "Jobs: Prefect itb-kedro-job + scheduled itb-daily-predict (see docs/prefect.md)."
echo "Hot reload is enabled for api / pipeline / web."
echo "Stop with: bin/stop_dev.sh"
