from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.models import User
from app.rag import answer_query
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest, user: User = Depends(get_current_user)):
    # Admins search across all groups; regular users are scoped to their own groups.
    allowed_groups = None if user.is_admin else (user.group_names or ["public"])

    result = answer_query(
        query=payload.query,
        history=payload.history,
        allowed_groups=allowed_groups,
        document_id=payload.document_id,
    )
    return result
