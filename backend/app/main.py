import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text as sql_text

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import User
from app.routers import auth, chat, documents
from app.vectorstore import ensure_collection

app = FastAPI(title="Knowledge System API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your actual frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(chat.router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    _run_migrations()
    ensure_collection()
    _bootstrap_admin()


def _run_migrations():
    """
    create_all() only creates missing tables, not missing columns on tables
    that already exist. There's no Alembic here yet, so new columns get a
    one-line, idempotent ALTER TABLE instead.
    """
    with engine.begin() as conn:
        conn.execute(sql_text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary TEXT"))


def _bootstrap_admin():
    """
    Creates a first admin user from env vars if no admin exists yet, so there's
    a way into the system on a fresh deployment without a chicken-and-egg
    problem (register requires an admin token).
    """
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL")
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
    if not email or not password:
        return

    db = SessionLocal()
    try:
        if db.query(User).filter(User.is_admin.is_(True)).first():
            return
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            existing.is_admin = True
            db.commit()
            return
        admin = User(
            email=email,
            hashed_password=hash_password(password),
            full_name="Administrator",
            is_admin=True,
        )
        db.add(admin)
        db.commit()
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
