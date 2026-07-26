#!/usr/bin/env python3
"""
Sets a document's pre-computed summary directly from stdin, bypassing the
Anthropic API — the write side of the Claude-Code-driven summary workflow
(pair with export_doc_text.py). The automatic API-based path in
routers/documents.py still runs on every upload independently; this just
lets you overwrite/fill in a summary yourself without spending API tokens.

Run inside the backend container:

    docker compose exec -T backend python set_summary.py <document_id> < summary.txt
"""
import sys

from app.database import SessionLocal
from app.models import Document


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    document_id = sys.argv[1]
    summary = sys.stdin.read()
    if not summary.strip():
        print("Empty summary on stdin, aborting.", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            print(f"No document with id {document_id}", file=sys.stderr)
            sys.exit(1)
        doc.summary = summary
        db.commit()
        print(f"Updated summary for {doc.filename} ({len(summary)} chars)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
