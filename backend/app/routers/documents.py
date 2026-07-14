import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.embeddings import embed_texts
from app.ingestion import chunk_text, hash_bytes, parse_document
from app.models import Document, Group, User
from app.schemas import DocumentOut
from app.vectorstore import delete_document as vs_delete_document
from app.vectorstore import upsert_chunks

router = APIRouter(prefix="/documents", tags=["documents"])

UPLOAD_DIR = Path("/data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _process_document(document_id: str, path: Path, filename: str, groups: list[str]):
    from app.database import SessionLocal

    db = SessionLocal()
    doc = db.query(Document).filter(Document.id == document_id).first()
    try:
        doc.status = "processing"
        db.commit()

        text = parse_document(path)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("No extractable text found in document")

        embedded = embed_texts(chunks)
        upsert_chunks(
            document_id=document_id,
            filename=filename,
            groups=groups,
            chunks=chunks,
            dense_vecs=embedded["dense"],
            sparse_vecs=embedded["sparse"],
        )

        doc.num_chunks = len(chunks)
        doc.status = "ready"
        db.commit()
    except Exception as e:  # noqa: BLE001
        doc.status = "failed"
        doc.error_message = str(e)
        db.commit()
    finally:
        db.close()


@router.post("/upload", response_model=DocumentOut)
def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    group_names: str = "",  # comma-separated, e.g. "dcas-cert,public"
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".pdf", ".docx", ".txt", ".md", ".html", ".htm"):
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    data = file.file.read()
    content_hash = hash_bytes(data)

    existing = db.query(Document).filter(Document.content_hash == content_hash).first()
    if existing:
        raise HTTPException(status_code=409, detail="An identical document already exists")

    requested_groups = [g.strip() for g in group_names.split(",") if g.strip()]
    if not requested_groups:
        # Default: private to the uploader's own groups, or "public" if they have none
        requested_groups = user.group_names or ["public"]

    # Non-admins may only tag documents with groups they themselves belong to
    if not user.is_admin:
        disallowed = set(requested_groups) - set(user.group_names)
        if disallowed:
            raise HTTPException(
                status_code=403,
                detail=f"You cannot assign documents to groups you don't belong to: {disallowed}",
            )

    groups = []
    for name in requested_groups:
        group = db.query(Group).filter(Group.name == name).first()
        if not group:
            group = Group(name=name)
            db.add(group)
            db.flush()
        groups.append(group)

    doc = Document(
        filename=file.filename,
        content_hash=content_hash,
        uploaded_by=user.id,
        status="pending",
        groups=groups,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    dest_path = UPLOAD_DIR / f"{doc.id}{suffix}"
    with open(dest_path, "wb") as f:
        f.write(data)

    background_tasks.add_task(_process_document, doc.id, dest_path, file.filename, requested_groups)

    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.is_admin:
        return db.query(Document).order_by(Document.created_at.desc()).all()

    allowed = set(user.group_names)
    docs = db.query(Document).all()
    return [d for d in docs if allowed.intersection(d.group_names)]


@router.delete("/{document_id}")
def delete_document_route(
    document_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not user.is_admin and doc.uploaded_by != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this document")

    vs_delete_document(document_id)
    db.delete(doc)
    db.commit()

    upload_path = next(UPLOAD_DIR.glob(f"{document_id}.*"), None)
    if upload_path:
        upload_path.unlink(missing_ok=True)

    return {"status": "deleted"}
