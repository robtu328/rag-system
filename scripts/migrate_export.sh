#!/usr/bin/env bash
# Package this RAG system (code + secrets + data) into a single self-contained
# zip for moving to another server. Run from the project root:
#   ./scripts/migrate_export.sh
#
# What it does:
#   1. Stops the stack (docker compose down) for a consistent data backup —
#      this is a brief outage, everything comes back up at the end.
#   2. Tars each data volume (postgres, qdrant, uploaded files).
#   3. Copies the project code + .env (secrets) into the bundle.
#   4. Bundles install.sh (migrate_import.sh) alongside it so the zip is
#      self-contained — just copy it to the new server and run install.sh.
#   5. Zips everything up and restarts the stack.
#
# NOT included: the model_cache volume (BGE-M3 + reranker weights, ~7GB) —
# it re-downloads automatically on first request on the new server, so
# shipping it over just wastes transfer time/space.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BUNDLE_NAME="rag-system-migration-${TIMESTAMP}"
WORKDIR="/tmp/${BUNDLE_NAME}"
OUT_ZIP="${PROJECT_DIR}/../${BUNDLE_NAME}.zip"

cd "$PROJECT_DIR"

if [ ! -f ".env" ]; then
  echo "ERROR: .env not found in $PROJECT_DIR — run this from the project root." >&2
  exit 1
fi

find_volume() {
  # Resolves the actual docker volume name (compose project-name prefix can
  # vary), e.g. suffix "pg_data" -> "rag-system_pg_data".
  docker volume ls --format '{{.Name}}' | grep -E "_$1\$" | head -1
}

echo "==> Bundle: $BUNDLE_NAME"
mkdir -p "$WORKDIR/volumes" "$WORKDIR/project"

echo "==> Stopping the stack for a consistent backup (brief outage)..."
docker compose down

cleanup() {
  echo "==> Restarting the stack..."
  docker compose up -d
}
trap cleanup EXIT

echo "==> Backing up data volumes..."
for VOL in pg_data qdrant_data upload_data; do
  FULL_VOL="$(find_volume "$VOL")"
  if [ -z "$FULL_VOL" ]; then
    echo "    WARNING: volume for '$VOL' not found, skipping" >&2
    continue
  fi
  echo "    - $FULL_VOL -> volumes/${VOL}.tar.gz"
  docker run --rm \
    -v "${FULL_VOL}:/from:ro" \
    -v "$WORKDIR/volumes:/to" \
    alpine sh -c "cd /from && tar czf /to/${VOL}.tar.gz ."
done

echo "==> Copying project files (code + .env)..."
rsync -a \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'certbot/conf' \
  --exclude 'certbot/www' \
  --exclude 'dock*.log' \
  --exclude '.claude' \
  "$PROJECT_DIR"/ "$WORKDIR/project/"

echo "==> Adding install script..."
cp "$SCRIPT_DIR/migrate_import.sh" "$WORKDIR/install.sh"
chmod +x "$WORKDIR/install.sh"

cat > "$WORKDIR/MANIFEST.txt" <<EOF
RAG system migration bundle
Created: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Source host: $(hostname)
Docker: $(docker --version 2>/dev/null || echo "unknown")

Contents:
  install.sh          - run this on the new server
  project/             - application code + .env (secrets)
  volumes/pg_data.tar.gz       - Postgres data (users, docs, summaries)
  volumes/qdrant_data.tar.gz   - Qdrant vector data
  volumes/upload_data.tar.gz   - raw uploaded document files

NOT included: model_cache (embedding/reranker model weights, ~7GB) —
these re-download automatically on first use on the new server.

To restore: copy this zip to the new server, unzip it, then run:
  cd ${BUNDLE_NAME} && ./install.sh
EOF

echo "==> Creating zip..."
( cd /tmp && zip -rq "${BUNDLE_NAME}.zip" "$BUNDLE_NAME" )
mv "/tmp/${BUNDLE_NAME}.zip" "$OUT_ZIP"
rm -rf "$WORKDIR"

echo ""
echo "==> Done: $(cd "$(dirname "$OUT_ZIP")" && pwd)/$(basename "$OUT_ZIP")"
du -h "$OUT_ZIP"
