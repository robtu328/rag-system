from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Document, User
from app.rag import answer_query
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Admins search across all groups; regular users are scoped to their own groups.
    allowed_groups = None if user.is_admin else (user.group_names or ["public"])

    # Pre-computed extract, if this document has one — real ACL enforcement
    # still happens inside answer_query via the group-filtered Qdrant lookup,
    # so fetching this here (pre-ACL-check) is safe even if it's unused.
    document_summary = None
    if payload.document_id:
        doc = db.query(Document).filter(Document.id == payload.document_id).first()
        document_summary = doc.summary if doc else None

    result = answer_query(
        query=payload.query,
        history=payload.history,
        allowed_groups=allowed_groups,
        document_id=payload.document_id,
        document_summary=document_summary,
        answer_language=payload.answer_language,
    )
    return result
