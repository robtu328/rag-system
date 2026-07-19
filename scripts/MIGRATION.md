# Migrating to another server

Two scripts handle moving this whole system — code, secrets, and data — to a
new server: `migrate_export.sh` (run here) and `migrate_import.sh` (runs on
the new server, bundled inside the zip `migrate_export.sh` produces).

## What gets moved

| Item | Included? | Notes |
|---|---|---|
| Application code | Yes | `backend/`, `frontend/`, `nginx/`, `docker-compose.yml`, etc. |
| `.env` (secrets) | Yes | API key, JWT secret, DB password — copied as-is |
| Postgres data | Yes | Users, documents, groups, pre-computed summaries |
| Qdrant data | Yes | Document chunk embeddings |
| Uploaded files | Yes | Raw PDFs/docs on disk |
| Model cache (BGE-M3, reranker) | **No** | ~7GB, re-downloads automatically on first use on the new server — not worth transferring |
| `.git`, `__pycache__`, logs, certbot certs | No | Excluded, regenerated/not needed |

## Export (run on this server)

```bash
cd /home/robtu/network/rag-system
./scripts/migrate_export.sh
```

This will:
1. Run `docker compose down` — **brief outage** (~20s) while volumes are backed up.
2. Tar each data volume (Postgres, Qdrant, uploads) into `volumes/*.tar.gz`.
3. Copy the project code + `.env` into the bundle.
4. Copy `migrate_import.sh` into the bundle as `install.sh`, so the zip is
   self-contained.
5. Zip everything into `rag-system-migration-<timestamp>.zip` in the parent
   directory of the project.
6. Run `docker compose up -d` again to restore service.

Output: `../rag-system-migration-<timestamp>.zip` (roughly 15-25MB, depending
on how many documents you've uploaded — this does **not** include the model
cache).

Get that zip file onto the new server however fits your setup (`scp`, USB,
etc.) — the script doesn't do this part.

## Import (run on the new server)

Prerequisites on the new server: Ubuntu (or similar), and either an internet
connection for the script to install Docker/NVIDIA Container Toolkit itself,
or have those already installed.

```bash
unzip rag-system-migration-<timestamp>.zip
cd rag-system-migration-<timestamp>
./install.sh
```

Optionally pass an install directory (defaults to `~/rag-system`):

```bash
./install.sh /opt/rag-system
```

This will:
1. Install Docker if missing (asks for confirmation first; needs `sudo`).
2. Install NVIDIA Container Toolkit if a GPU is detected and it's missing
   (asks first).
3. Copy the code + `.env` to the install directory.
4. Create the data volumes and restore Postgres/Qdrant/uploads into them
   **before** the first `docker compose up`, so containers start with your
   existing data instead of initializing empty.
5. Build and start the stack (`docker compose up -d --build`).
6. Poll `/api/health` until the backend responds.

After it finishes, visit `http://<new-server>/` and log in with your
existing account — no need to recreate users or re-upload documents.

## Known caveats

- **GPU driver mismatch**: `backend/Dockerfile` installs a CUDA 12.8 (cu128)
  torch wheel, matched to this server's RTX 5090 (Blackwell). If the new
  server has a different GPU generation or older driver, edit the torch
  install line in `backend/Dockerfile` before `install.sh` builds the image
  (or before rerunning `docker compose up -d --build` afterward).
- **No GPU on the new server**: the script proceeds CPU-only — embeddings
  and reranking will be noticeably slower, but everything still works.
- **Domain/HTTPS**: if you had Let's Encrypt certs set up via certbot, those
  are excluded from the bundle (tied to the old server/domain) — follow the
  "Enabling HTTPS" section in the main `README.md` again on the new server.
- **Volume naming**: `install.sh` fixes the Docker Compose project name to
  `rag-system` (via `COMPOSE_PROJECT_NAME`) regardless of what directory you
  install into, so the restored volumes always line up with what
  `docker-compose.yml` expects.
