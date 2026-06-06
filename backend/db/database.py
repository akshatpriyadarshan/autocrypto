"""DB engine — SQLite for local, Postgres for prod."""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from loguru import logger

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/autocrypto.db")
is_sqlite = DATABASE_URL.startswith("sqlite")
engine_kwargs = {"echo": False}
if is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update({"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True})

engine = create_async_engine(DATABASE_URL, **engine_kwargs)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession,
                                        expire_on_commit=False, autoflush=False)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db():
    from backend.models.db_models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("DB initialised")

async def check_db() -> bool:
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
