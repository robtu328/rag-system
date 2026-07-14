from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


# --- Auth ---

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    group_names: list[str] = []


class UserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str]
    is_admin: bool
    group_names: list[str]

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# --- Documents ---

class DocumentOut(BaseModel):
    id: str
    filename: str
    status: str
    num_chunks: int
    group_names: list[str]
    created_at: datetime

    class Config:
        from_attributes = True


# --- Chat ---

class ChatTurn(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    query: str
    history: list[ChatTurn] = []
    document_id: Optional[str] = None


class SourceChunk(BaseModel):
    document_id: str
    filename: str
    chunk_index: int
    text: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]
