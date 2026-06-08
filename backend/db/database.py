"""
DB — sync SQLite. Writes to /tmp/ so it works on Streamlit Cloud (read-only repo).
"""
import os
from contextlib import contextmanager
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from loguru import logger

# /tmp is always writable — on Streamlit Cloud, repo dir is read-only
DB_PATH = os.getenv("DB_PATH", "/tmp/autocrypto.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"
os.environ["DATABASE_URL"] = DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def get_session():
    """Yields session. Commits on exit, rolls back on error."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db():
    from backend.models.db_models import Base
    Base.metadata.create_all(bind=engine)
    logger.info(f"DB ready at {DB_PATH}")


def check_db() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
