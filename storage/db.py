"""
Database connection layer.

Uses SQLite by default (no external service needed). Switch to Postgres by
setting DATABASE_URL in your .env file, e.g.:
    DATABASE_URL=postgresql+psycopg2://user:pass@localhost/unc_transit
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./unc_transit.db")

# connect_args only applies to SQLite — needed for multi-threaded FastAPI
_connect_args = {"check_same_thread": False} if _DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(_DATABASE_URL, connect_args=_connect_args, echo=False)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that yields a DB session and ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables declared in storage.models (idempotent)."""
    from storage import models  # noqa: F401 — registers ORM metadata
    Base.metadata.create_all(bind=engine)
    print(f"[storage] Database initialised → {_DATABASE_URL}")
