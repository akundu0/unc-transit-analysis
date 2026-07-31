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

# Pool settings tuned for Supabase free tier (max 50 connections shared across clients)
_pool_kwargs = {}
if not _DATABASE_URL.startswith("sqlite"):
    _pool_kwargs = {
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 300,   # recycle connections every 5 min (Supabase idle timeout)
        "pool_pre_ping": True, # verify connection is alive before using it
    }

engine = create_engine(_DATABASE_URL, connect_args=_connect_args, echo=False, **_pool_kwargs)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def _safe_url() -> str:
    """Return DATABASE_URL with password masked for safe logging."""
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", _DATABASE_URL)


def get_db():
    """FastAPI dependency that yields a DB session and ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Apply Alembic migrations to bring the DB schema to head.

    Falls back to create_all() if Alembic is not installed or migration
    files are missing (e.g. in a minimal test environment).
    """
    try:
        from alembic import command
        from alembic.config import Config
        import os

        ini_path = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
        if os.path.exists(ini_path):
            alembic_cfg = Config(ini_path)
            alembic_cfg.set_main_option("sqlalchemy.url", _DATABASE_URL)
            command.upgrade(alembic_cfg, "head")
            print(f"[storage] Alembic migrations applied → {_safe_url()}")
            return
    except Exception as exc:
        print(f"[storage] Alembic migration skipped ({exc}), falling back to create_all")

    from storage import models  # noqa: F401 — registers ORM metadata
    Base.metadata.create_all(bind=engine)
    print(f"[storage] Database initialised (create_all) → {_safe_url()}")
