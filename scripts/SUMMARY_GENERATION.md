# From upload to summary: full walkthrough

This covers the complete path for one document: uploading it, getting it
chunked/embedded, then generating its structured summary (used for
full-document-mode questions like "list all X") — via either of the two
available paths.

## The two summary paths

| | Automatic (API) | Claude Code |
|---|---|---|
| When it runs | Right after upload, unattended | Whenever you ask me to run it |
| Cost | Metered `ANTHROPIC_API_KEY` calls | Your Claude Code subscription |
| Script | `backend/app/rag.py` → `generate_document_summary` (built into ingestion) | `scripts/claude_code_summarize.py` |
| Good for | Uploads that happen when nobody's watching | Batches you want to run cost-consciously, or review before saving |

Both write to the same `documents.summary` column — running one after the
other just overwrites with the latest result, nothing conflicts.

## Step 1 — Upload the document

**Web UI:** Documents tab → choose file → optionally type group names
(comma-separated) → Upload.

**CLI (bulk):**
```bash
python scripts/ingest_cli.py \
  --api-url http://localhost/api \
  --email <your email> --password <your password> \
  --folder /path/to/document/pool \
  --groups dcas-cert,public
```

## Step 2 — Wait for base processing

Every upload goes through parsing → chunking → embedding automatically and
locally (no API cost, runs on GPU). Watch status move `pending` →
`processing` → `ready` in the Documents tab, or check from the command line:

```bash
docker compose exec -T postgres psql -U rag -d rag_knowledge \
  -c "SELECT filename, status, num_chunks FROM documents ORDER BY created_at DESC LIMIT 5;"
```

A document must be `ready` before it can be summarized — chunks/embeddings
are the input the summary step reads from.

**By default, summary generation is skipped at upload** — the document
becomes `ready` with `summary` still `NULL`, and stays that way until you run
step 3 below (or select **Summary: automatic** at upload time instead, which
runs the API-based path during this same background step and fills in
`summary` on its own by the time status hits `ready`).

## Step 3 — Generate (or regenerate) the summary via Claude Code

Ask me to run this, or run it yourself:

```bash
# See what's missing a summary
docker compose exec -T backend python export_doc_text.py --pending

# Process everything that's pending
python scripts/claude_code_summarize.py --pending

# Or just one document
python scripts/claude_code_summarize.py <document_id>
```

What happens: it exports the document's raw parsed text, sections it (to
avoid the model cutting off partway through on large documents — this bit
matters, see the note below), runs each section through `claude -p` to
produce a structured, exhaustive extract, then saves the combined result via
`set_summary.py`.

## Step 4 — Verify it worked

```bash
docker compose exec -T postgres psql -U rag -d rag_knowledge \
  -c "SELECT filename, length(summary) FROM documents WHERE id='<document_id>';"
```

A healthy summary is typically 5-15% the size of the raw document text for
dense technical specs — much smaller than the original, but not tiny. If
`length` comes back `0` or `NULL`, generation didn't complete; rerun step 3.

## Step 5 — Use it

In the Chat tab, set the **Scope** dropdown to that specific document (not
"All documents"), then ask your question. This is what routes the question
to full-document mode, which uses the summary as context instead of doing a
similarity search over top-k chunks — the only way to reliably get complete
answers to "list all X" style questions.

## Why sectioning matters (a real bug we hit)

Early on, summary generation used a single call with a fixed output budget.
For a large document (178 chunks / ~267K characters), the extract got cut
off after only the table of contents — it never reached the actual content,
and "list all requirements" came back honestly saying the list wasn't
exhaustive, because it wasn't. Both the automatic path and
`claude_code_summarize.py` now split large documents into sections and
extract each independently, so no single section's output budget has to
cover the whole document. If you ever see a summary that looks suspiciously
short or cuts off mid-sentence, that's the failure mode to check for —
rerun generation rather than trusting a partial extract.
