from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrate existing tables: add new columns if missing
        await _migrate(conn)


async def _migrate(conn: "AsyncConnection") -> None:  # type: ignore[name-defined]  # noqa: F821
    """Add missing columns to existing tables. Safe to run multiple times."""
    import sqlalchemy

    migrations = [
        ("habits", "shame_enabled", "BOOLEAN DEFAULT 0"),
        ("custom_shame_messages", None, None),  # new table, handled by create_all
    ]
    for table, column, col_type in migrations:
        if column is None:
            continue
        try:
            await conn.execute(sqlalchemy.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        except Exception:
            pass  # Column already exists
