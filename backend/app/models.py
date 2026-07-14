import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Table, Text, Integer
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


# Many-to-many: which groups a user belongs to
user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", UUID(as_uuid=False), ForeignKey("users.id"), primary_key=True),
    Column("group_id", UUID(as_uuid=False), ForeignKey("groups.id"), primary_key=True),
)

# Many-to-many: which groups can access a document
document_groups = Table(
    "document_groups",
    Base.metadata,
    Column("document_id", UUID(as_uuid=False), ForeignKey("documents.id"), primary_key=True),
    Column("group_id", UUID(as_uuid=False), ForeignKey("groups.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    groups = relationship("Group", secondary=user_groups, back_populates="users")

    @property
    def group_names(self):
        return [g.name for g in self.groups]


class Group(Base):
    """
    A group represents an access scope, e.g. 'dcas-cert', 'cv-research', 'public'.
    Documents are tagged with one or more groups; users see only documents in
    groups they belong to. Admins bypass this filter entirely.
    """
    __tablename__ = "groups"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)

    users = relationship("User", secondary=user_groups, back_populates="groups")
    documents = relationship("Document", secondary=document_groups, back_populates="groups")


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    filename = Column(String, nullable=False)
    content_hash = Column(String, unique=True, index=True, nullable=False)  # dedupe on re-upload
    source_path = Column(String, nullable=True)
    uploaded_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    num_chunks = Column(Integer, default=0)
    status = Column(String, default="pending")  # pending | processing | ready | failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    groups = relationship("Group", secondary=document_groups, back_populates="documents")

    @property
    def group_names(self):
        return [g.name for g in self.groups]
