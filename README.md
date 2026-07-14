# Knowledge system — RAG over your document pool

A self-hosted question-answering system: point it at a pool of documents
(PDF/docx/txt/html), it chunks and embeds them locally, and answers questions
grounded in that content via the Claude API. Runs entirely on your own Linux
box, reachable remotely over HTTPS, with multi-user auth and per-document
access control.

## Architecture

```
Document pool → ingestion (parse, chunk, embed with BGE-M3)
              → Qdrant (vector store) + Postgres (users, groups, ACL)
              → RAG API (hybrid retrieval + rerank + Claude)
              → Nginx (TLS, reverse proxy)
              → Web UI (remote, multi-user)
```

- **Embeddings**: BAAI/bge-m3 — multilingual (handles mixed Chinese/English
  content well), dense + sparse in one model, runs on your GPU.
- **Reranker**: BAAI/bge-reranker-v2-m3 — cross-encoder pass over the top
  candidates before they reach Claude, improves precision noticeably.
- **Vector store**: Qdrant, with hybrid (dense+sparse) search fused via RRF,
  filtered by group membership for access control.
- **Auth**: JWT-based, Postgres-backed users/groups. Documents are tagged with
  one or more groups; users only retrieve from groups they belong to. Admins
  see everything.
- **Answering**: Claude is instructed to answer only from retrieved excerpts
  and cite the source filename, reducing hallucination risk.

## Prerequisites

- Linux host (Ubuntu 22.04+ recommended) with Docker and Docker Compose v2
- NVIDIA GPU + `nvidia-container-toolkit` installed, if you want local
  embedding/reranking to run on GPU (recommended — CPU works but is much
  slower for bulk ingestion)
- A domain name pointed at this machine, if you want a real HTTPS cert
  (Let's Encrypt) rather than just LAN/VPN access
- An Anthropic API key

## Quick start

1. **Copy environment file and fill in secrets**

   ```bash
   cp .env.example .env
   # edit .env: set ANTHROPIC_API_KEY, JWT_SECRET, POSTGRES_PASSWORD,
   # BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD, DATABASE_URL to match
   # POSTGRES_PASSWORD, etc.
   ```

2. **(GPU only) enable GPU passthrough**

   Uncomment the `deploy.resources.reservations.devices` block for the
   `backend` service in `docker-compose.yml`, and swap the CPU-only torch
   install line in `backend/Dockerfile` for the CUDA wheel matching your
   driver (see the comment there).

3. **Start everything**

   ```bash
   docker compose up -d --build
   docker compose logs -f backend   # watch first-time model download
   ```

   On first request, the backend downloads BGE-M3 and the reranker
   (a few GB) into the `model_cache` volume — this only happens once.

4. **Log in**

   Visit `http://<your-server>/` (or your domain once TLS is set up) and sign
   in with the bootstrap admin credentials from `.env`. Change that password
   immediately by creating a proper admin account and disabling the bootstrap
   one, or simply rotate `BOOTSTRAP_ADMIN_PASSWORD` and restart.

5. **Create groups and users**

   As admin, use `POST /api/auth/register` (via the API directly, or extend
   the frontend with a small admin panel) to create accounts and assign them
   to groups, e.g. `dcas-cert`, `cv-research`, `public`. A document tagged
   `dcas-cert` is only retrievable by users in that group.

6. **Load documents**

   - Through the UI: Documents tab → choose file → optionally specify groups
     → Upload.
   - In bulk, from the command line:

     ```bash
     python scripts/ingest_cli.py \
       --api-url http://localhost/api \
       --email admin@example.com --password <password> \
       --folder /path/to/document/pool \
       --groups dcas-cert,public
     ```

   Processing happens in the background; refresh the Documents tab to see
   status move from `pending` → `processing` → `ready` (or `failed`, with an
   error message you can inspect via `docker compose logs backend`).

7. **Ask questions**

   Chat tab → type a question. Answers cite the source filename and chunk;
   the sources panel under each answer shows exactly which excerpts were used.

## Enabling HTTPS for remote access

```bash
# one-time cert issuance (adjust domain + email)
docker run -it --rm \
  -v $(pwd)/certbot/conf:/etc/letsencrypt \
  -v $(pwd)/certbot/www:/var/www/certbot \
  -p 80:80 \
  certbot/certbot certonly --standalone \
  -d knowledge.yourcompany.com --email you@yourcompany.com --agree-tos

# then edit nginx/nginx.conf: uncomment the HTTPS server block and the
# redirect block, set server_name to your domain, and:
docker compose restart nginx
```

Set up renewal with a cron job or systemd timer running `certbot renew`
followed by `docker compose restart nginx`.

If this is strictly for internal/org use rather than public internet, it's
simpler and more secure to skip public HTTPS entirely and instead expose only
port 80 on your existing FortiGate VPN, so remote access requires the VPN
tunnel first.

## Access control model

- **Groups** are the unit of access control (e.g. team, project, clearance
  level). A user can belong to multiple groups.
- **Documents** are tagged with one or more groups at upload time.
- **Retrieval filtering** happens inside Qdrant at query time — a user's
  search is scoped to `groups` matching their own, via a payload index, so
  this stays fast even as the corpus grows into the hundreds of thousands of
  chunks.
- **Admins** bypass the group filter and search/see everything.

This is deliberately simple (group-based, not per-document ACLs with
individual grants) because it scales cleanly with your document count. If you
need finer-grained per-document sharing later, the `document_groups` table in
`backend/app/models.py` can be extended with a parallel per-user grants table
without changing the retrieval filtering logic much.

## Scaling notes (10K–100K+ documents)

- Qdrant's HNSW index and payload filtering handle this range comfortably on
  a single node; you likely don't need distributed Qdrant unless you're
  pushing well past a million chunks.
- Postgres needs no special tuning at this scale.
- The main bottleneck at ingestion time is the embedding model — batch
  uploads through `ingest_cli.py` during off-hours, or increase
  `EMBEDDING_MODEL` batch size in `embeddings.py` if you have GPU headroom to
  spare.
- Background processing currently uses FastAPI's `BackgroundTasks`, which is
  fine for moderate upload volume. If you're ingesting continuously at high
  volume, swap this for a real task queue (Celery + Redis, or RQ) — the
  `_process_document` function in `backend/app/routers/documents.py` is
  already isolated so this is a small refactor, not a rewrite.

## What's intentionally left simple for you to extend

- **Admin UI for user/group management** — currently done via direct API
  calls (`/auth/register`); a small admin panel in the frontend would be a
  natural next step.
- **Streaming responses** — the chat endpoint currently returns the full
  answer at once; swapping to Claude's streaming API + Server-Sent Events
  would improve perceived latency for longer answers.
- **Conversation persistence** — chat history currently lives only in the
  browser tab; if you want saved conversation threads, add a `conversations`
  / `messages` table and wire the frontend to load/save them.
