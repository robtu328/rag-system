#!/usr/bin/env python3
"""
Generates a document's structured summary via the local `claude` CLI
(non-interactive `-p` mode) instead of the Anthropic API. `claude -p` bills
against your Claude Code subscription, not the metered ANTHROPIC_API_KEY in
.env — this is the scripted version of the export_doc_text.py / set_summary.py
manual workflow.

The automatic API-based path in routers/documents.py is untouched and keeps
running on every upload independently; this is an alternative way to fill in
(or overwrite) documents.summary without spending API tokens.

Requires: the `claude` CLI installed and logged in on this host, and the
backend container running (uses `docker compose exec` for DB/text access).

Usage:
    python scripts/claude_code_summarize.py --pending          # do all pending
    python scripts/claude_code_summarize.py <document_id>      # do one
"""
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SECTION_CHARS = 80_000        # chars per claude -p call, mirrors the API path's sectioning fix
MAX_TOTAL_CHARS = 700_000     # same safety cap as MAX_FULL_DOC_CHARS in rag.py

SECTION_PROMPT = """You produce a comprehensive, structured extract of one part of a larger \
document for later use as retrieval context, so it must be exhaustive:

1. Do not omit any distinct item, requirement, clause, section heading, or fact from this part \
— err on the side of completeness over brevity.
2. Preserve the document's own numbering/section structure where present, so items can still \
be traced back to their source location.
3. Use plain text with clear headings and numbered/bulleted lists, not prose summarization.
4. This is only one part of the document — extract only what's in the text given to you, don't \
speculate about other parts.
5. Output ONLY the extract itself — no preamble, no "Here is the extract:", no closing remarks.

Document: {filename} — part {part} of {total}"""


def run(cmd: list[str], input_text: str | None = None, **kwargs) -> str:
    result = subprocess.run(
        cmd, input=input_text, capture_output=True, text=True, encoding="utf-8", **kwargs
    )
    if result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def list_pending() -> list[tuple[str, str]]:
    out = run(["docker", "compose", "exec", "-T", "backend", "python", "export_doc_text.py", "--pending"])
    pending = []
    for line in out.splitlines():
        if "\t" in line:
            doc_id, filename = line.split("\t", 1)
            pending.append((doc_id, filename))
    return pending


def export_text(document_id: str) -> str:
    return run(["docker", "compose", "exec", "-T", "backend", "python", "export_doc_text.py", document_id])


def set_summary(document_id: str, summary: str):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "python", "set_summary.py", document_id],
        input=summary, capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"Failed to save summary: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"  {result.stdout.strip()}")


def summarize_via_claude_code(filename: str, text: str) -> str:
    text = text[:MAX_TOTAL_CHARS]
    sections = [text[i:i + SECTION_CHARS] for i in range(0, len(text), SECTION_CHARS)] or [""]

    parts = []
    for i, section in enumerate(sections):
        print(f"    part {i + 1}/{len(sections)} ({len(section)} chars)...")
        prompt = SECTION_PROMPT.format(filename=filename, part=i + 1, total=len(sections))
        output = run(["claude", "-p", prompt], input_text=section)
        parts.append(output.strip())

    return "\n\n".join(parts)


def process_one(document_id: str, filename: str):
    print(f"==> {filename} ({document_id})")
    text = export_text(document_id)
    summary = summarize_via_claude_code(filename, text)
    set_summary(document_id, summary)


def ensure_helper_scripts():
    # docker cp'd files don't survive a container rebuild/recreate, so copy
    # these in fresh every run — cheap, and keeps this script self-sufficient.
    for name in ("export_doc_text.py", "set_summary.py"):
        run(["docker", "cp", str(SCRIPT_DIR / name), f"rag_backend:/app/{name}"])


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    ensure_helper_scripts()

    if sys.argv[1] == "--pending":
        pending = list_pending()
        if not pending:
            print("No documents pending a summary.")
            return
        print(f"Found {len(pending)} pending document(s)")
        for doc_id, filename in pending:
            process_one(doc_id, filename)
    else:
        document_id = sys.argv[1]
        # filename isn't known yet — export_doc_text.py will error out cleanly if the id is bad
        process_one(document_id, document_id)


if __name__ == "__main__":
    main()
