"""
DB engine — synchronous SQLite via SQLAlchemy.
Using sync engine avoids the anyio thread-limiter bug on Python 3.14
(TypeError: cannot create weak reference to NoneType in anyio._backends._asyncio).
Streamlit is sync anyway — no real benefit to async DB calls here.
"""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from loguru import logger

# Use sync SQLite URL (no +aiosqlite)
_raw = os.getenv("DATABASE_URL", "sqlite:///./data/autocrypto.db")
DATABASE_URL = _raw.replace("+aiosqlite", "")  # strip async driver if present

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def get_session():
    """Sync context manager — use as: with get_session() as db: ..."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    """Create all tables."""
    from backend.models.db_models import Base
    Base.metadata.create_all(bind=engine)
    logger.info("DB initialised (sync SQLite)")


def check_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
