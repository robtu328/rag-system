#!/usr/bin/env bash
# Restores a RAG system migration bundle on a new server.
# This script is bundled INSIDE the zip produced by migrate_export.sh — you
# can't run it before unzipping (it needs project/ and volumes/ next to it),
# so the full restore is two commands:
#
#   unzip rag-system-migration-*.zip
#   cd rag-system-migration-* && ./install.sh
#
# What it does:
#   1. Installs Docker + NVIDIA Container Toolkit if missing (asks first).
#   2. Copies project/ (code + .env) to the target install directory.
#   3. Creates the data volumes and restores Postgres/Qdrant/uploads into
#      them BEFORE the first `docker compose up`, so containers start with
#      data already in place instead of initializing empty.
#   4. Brings the stack up and does a health check.
#
# model_cache (embedding/reranker weights) is NOT restored — it downloads
# fresh on first use (a few GB, one-time, same as first deployment).

set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${1:-$HOME/rag-system}"
PROJECT_NAME="rag-system"   # fixes volume naming regardless of INSTALL_DIR's actual folder name

# Some minimal servers (this one included) ship wget but not curl.
fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1"
  else
    wget -qO- "$1"
  fi
}

if [ ! -d "$BUNDLE_DIR/project" ] || [ ! -d "$BUNDLE_DIR/volumes" ]; then
  echo "ERROR: run this from inside the unzipped bundle (expects ./project and ./volumes here)." >&2
  exit 1
fi

echo "==> Installing to: $INSTALL_DIR"
echo "==> Compose project name (fixed): $PROJECT_NAME"
echo ""

# --- 1. Docker ---
if ! command -v docker >/dev/null 2>&1; then
  read -r -p "Docker not found. Install it now? [y/N] " REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    fetch https://get.docker.com | sudo sh || {
      echo "get.docker.com unreachable — install Docker manually, then rerun this script." >&2
      exit 1
    }
    sudo usermod -aG docker "$USER"
    echo "Docker installed. Log out/in (or 'newgrp docker') then rerun this script."
    exit 0
  else
    echo "Docker is required. Exiting." >&2
    exit 1
  fi
fi

# --- 2. NVIDIA Container Toolkit (only if a GPU is present) ---
if command -v nvidia-smi >/dev/null 2>&1; then
  if ! dpkg -l 2>/dev/null | grep -q nvidia-container-toolkit; then
    read -r -p "GPU detected but nvidia-container-toolkit not installed. Install it now? [y/N] " REPLY
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
      distribution=$(. /etc/os-release; echo "$ID$VERSION_ID")
      fetch https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
      fetch https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
      sudo apt-get update -qq
      sudo apt-get install -y nvidia-container-toolkit
      sudo nvidia-ctk runtime configure --runtime=docker
      sudo systemctl restart docker
    else
      echo "Skipping — GPU passthrough won't work until this is installed (see docker-compose.yml)."
    fi
  fi
  echo "NOTE: backend/Dockerfile installs a CUDA 12.8 (cu128) torch wheel. If this GPU's driver"
  echo "      doesn't support that, edit the torch install line in backend/Dockerfile before building."
else
  echo "No GPU detected (nvidia-smi not found) — will run CPU-only (slower embeddings)."
fi
echo ""

# --- 3. Copy project files ---
echo "==> Copying project files..."
mkdir -p "$INSTALL_DIR"
cp -a "$BUNDLE_DIR/project/." "$INSTALL_DIR/"
cd "$INSTALL_DIR"
chmod 600 .env

# --- 4. Restore data volumes (before first `compose up`) ---
echo "==> Restoring data volumes..."
export COMPOSE_PROJECT_NAME="$PROJECT_NAME"
for VOL in pg_data qdrant_data upload_data; do
  TARBALL="$BUNDLE_DIR/volumes/${VOL}.tar.gz"
  FULL_VOL="${PROJECT_NAME}_${VOL}"
  if [ ! -f "$TARBALL" ]; then
    echo "    WARNING: $TARBALL missing, skipping $FULL_VOL" >&2
    continue
  fi
  echo "    - $FULL_VOL"
  docker volume create "$FULL_VOL" >/dev/null
  docker run --rm \
    -v "${FULL_VOL}:/to" \
    -v "$TARBALL:/tarball.tar.gz:ro" \
    alpine sh -c "cd /to && tar xzf /tarball.tar.gz"
done

# --- 5. Bring the stack up ---
echo "==> Starting the stack (this also builds images — may take a while)..."
docker compose up -d --build

echo "==> Waiting for backend to come up..."
for i in $(seq 1 30); do
  if curl -fsS http://localhost/api/health >/dev/null 2>&1 || wget -qO- http://localhost/api/health >/dev/null 2>&1; then
    echo "==> Backend is up."
    break
  fi
  sleep 2
done

echo ""
echo "==> Done. Installed at: $INSTALL_DIR"
echo "    Check: docker compose ps"
echo "    Logs:  docker compose logs -f backend"
echo "    Then visit http://<this-server>/ and log in with your existing account."
