#!/bin/sh
set -eu

PGHOST="${MLFLOW_PGHOST:-postgres}"
PGUSER="${MLFLOW_PGUSER:-itb}"
PGPASSWORD="${MLFLOW_PGPASSWORD:-itb}"
PGDATABASE="${MLFLOW_PGDATABASE:-mlflow}"
export PGPASSWORD

BACKEND_URI="${MLFLOW_BACKEND_STORE_URI:-postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:5432/${PGDATABASE}}"
ARTIFACT_ROOT="${MLFLOW_ARTIFACT_ROOT:-/mlruns}"

echo "Waiting for Postgres at ${PGHOST}..."
i=0
until pg_isready -h "$PGHOST" -U "$PGUSER" -d postgres >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "Postgres not ready after 60s" >&2
    exit 1
  fi
  sleep 1
done

exists="$(psql -h "$PGHOST" -U "$PGUSER" -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='${PGDATABASE}'" || true)"
if [ "$exists" != "1" ]; then
  echo "Creating database ${PGDATABASE}..."
  psql -h "$PGHOST" -U "$PGUSER" -d postgres -c "CREATE DATABASE ${PGDATABASE}"
fi

echo "Starting MLflow with backend ${BACKEND_URI%%@*}@${PGHOST}:5432/${PGDATABASE}"
exec mlflow server \
  --backend-store-uri "$BACKEND_URI" \
  --default-artifact-root "$ARTIFACT_ROOT" \
  --host 0.0.0.0 \
  --port 5000 \
  --allowed-hosts '*'
