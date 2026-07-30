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
            print(f"[storage] Alembic migrations applied → {_DATABASE_URL}")
            return
    except Exception as exc:
        print(f"[storage] Alembic migration skipped ({exc}), falling back to create_all")

    from storage import models  # noqa: F401 — registers ORM metadata
    Base.metadata.create_all(bind=engine)
    print(f"[storage] Database initialised (create_all) → {_DATABASE_URL}")
