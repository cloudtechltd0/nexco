# database.py — Engine initialisation, session factory, and FastAPI dependency

import os
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


# ─── DATABASE CONFIG ──────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./telecom.db"
)


# ─── AUTO FIX POSTGRES DRIVER ──────────────────────────────────────────────────
# Ensures compatibility with asyncpg for Neon / Postgres

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+asyncpg://"
    )


# ─── ENGINE CONFIG (FIXES CACHE + NEON ISSUES) ─────────────────────────────────

engine_args = {
    "echo": False,

    # IMPORTANT: reduces asyncpg prepared statement issues
    "pool_pre_ping": True,
    "pool_recycle": 1800,

    # FIX: disables aggressive statement caching issues on some Postgres setups
    "query_cache_size": 0,
}

# SQLite requires special connect args
if "sqlite" in DATABASE_URL:
    engine_args["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(DATABASE_URL, **engine_args)


# ─── SESSION FACTORY ──────────────────────────────────────────────────────────

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ─── BASE MODEL ────────────────────────────────────────────────────────────────

Base = declarative_base()


# ─── INITIALISE DATABASE (DEV ONLY) ────────────────────────────────────────────

async def init_db() -> None:
    """
    Creates all tables at startup.
    Use Alembic migrations in production.
    """
    async with engine.begin() as conn:
        import models  # ensures all tables are registered
        await conn.run_sync(Base.metadata.create_all)


# ─── FASTAPI DB DEPENDENCY ────────────────────────────────────────────────────

async def get_db():
    """
    Yields an async database session per request.
    Ensures rollback on failure and proper cleanup.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()