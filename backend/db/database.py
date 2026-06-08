"""
DB — sync SQLite. Engine created lazily so /tmp path is used correctly
regardless of when services import this module.
"""
import os
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from loguru import logger

# Always /tmp — writable on Streamlit Cloud
DB_PATH = "/tmp/autocrypto.db"

_engine = None
_Session = None


def _get_engine():
    global _engine, _Session
    if _engine is None:
        url = f"sqlite:///{DB_PATH}"
        _engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            echo=False,
        )
        _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
        logger.info(f"DB engine created: {url}")
    return _engine, _Session


@contextmanager
def get_session():
    _, Session = _get_engine()
    s = Session()
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
    engine, _ = _get_engine()
    Base.metadata.create_all(bind=engine)
    logger.info(f"DB tables ready at {DB_PATH}")


def check_db() -> bool:
    try:
        engine, _ = _get_engine()
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
