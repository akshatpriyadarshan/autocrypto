"""
DB engine — synchronous SQLite via SQLAlchemy.
Pure sync: no aiosqlite, no anyio, no asyncio.
Works on Python 3.11–3.14.
"""
import os
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from loguru import logger

_raw = os.getenv("DATABASE_URL", "sqlite:///./data/autocrypto.db")
DATABASE_URL = _raw.replace("+aiosqlite", "")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
    # Use NullPool for SQLite to avoid thread issues
    poolclass=None,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,   # manual flush only
    autocommit=False,
)


@contextmanager
def get_session():
    """
    Context manager yielding a DB session.
    Commits on clean exit, rolls back on exception.
    Callers must NOT call db.commit() themselves —
    that causes double-commit errors on Python 3.14.
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    """Create all tables on startup."""
    from backend.models.db_models import Base
    Base.metadata.create_all(bind=engine)
    logger.info("DB ready (sync SQLite)")


def check_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
