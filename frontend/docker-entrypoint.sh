#!/bin/sh
set -eu

# Named volume web_node_modules persists across image rebuilds and can drift
# behind package-lock.json. Reinstall when the lockfile hash changes.
LOCK_FILE="package-lock.json"
HASH_FILE="node_modules/.package-lock-hash"

if [ -f "$LOCK_FILE" ]; then
  CURRENT="$(md5sum "$LOCK_FILE" | awk '{print $1}')"
else
  CURRENT="$(md5sum package.json | awk '{print $1}')"
fi

NEED_INSTALL=0
if [ ! -d node_modules ] || [ ! -f "$HASH_FILE" ]; then
  NEED_INSTALL=1
elif [ "$(cat "$HASH_FILE")" != "$CURRENT" ]; then
  NEED_INSTALL=1
fi

if [ "$NEED_INSTALL" -eq 1 ]; then
  echo "[web] Syncing npm dependencies (lockfile changed or node_modules missing)..."
  npm install
  mkdir -p node_modules
  echo "$CURRENT" > "$HASH_FILE"
fi

exec "$@"
