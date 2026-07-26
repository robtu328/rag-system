#!/usr/bin/env python3
"""
Exports a document's raw parsed text so it can be read directly (e.g. by a
Claude Code session) instead of sending it to the Anthropic API for summary
generation. This is an alternative to the automatic API-based summary
generation in routers/documents.py — both paths write to the same
documents.summary column, so use whichever fits: the automatic path for
unattended uploads, this one when a Claude Code session is available and you
want to avoid the metered API call.

Run inside the backend container:

    docker compose exec -T backend python export_doc_text.py --pending
    docker compose exec -T backend python export_doc_text.py <document_id> > doc.txt
"""
import sys
from pathlib import Path

from app.database import SessionLocal
from app.ingestion import parse_document
from app.models import Document

UPLOAD_DIR = Path("/data/uploads")


def list_pending():
    db = SessionLocal()
    try:
        docs = (
            db.query(Document)
            .filter(Document.status == "ready", Document.summary.is_(None))
            .all()
        )
        for d in docs:
            print(f"{d.id}\t{d.filename}")
    finally:
        db.close()


def export_text(document_id: str):
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            print(f"No document with id {document_id}", file=sys.stderr)
            sys.exit(1)
        path = next(UPLOAD_DIR.glob(f"{document_id}.*"), None)
        if not path:
            print(f"Source file not found on disk for {document_id}", file=sys.stderr)
            sys.exit(1)
        sys.stdout.write(parse_document(path))
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--pending":
        list_pending()
    elif len(sys.argv) == 2:
        export_text(sys.argv[1])
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
