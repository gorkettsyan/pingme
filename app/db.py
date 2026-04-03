from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine
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


async def _migrate(conn: AsyncConnection) -> None:
    """Add missing columns to existing tables. Safe to run multiple times."""
    import logging

    import sqlalchemy

    logger = logging.getLogger(__name__)

    migrations = [
        ("habits", "shame_enabled", "BOOLEAN DEFAULT 0"),
        ("custom_shame_messages", None, None),  # new table, handled by create_all
        ("goals", None, None),  # new table, handled by create_all
        ("goals", "unit", "VARCHAR(50) NOT NULL DEFAULT 'times'"),
        ("goal_progress", None, None),  # new table, handled by create_all
    ]
    for table, column, col_type in migrations:
        if column is None:
            continue
        try:
            await conn.execute(sqlalchemy.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            logger.info("Migration: added column %s.%s", table, column)
        except Exception:
            pass  # Column already exists

    # SQLite doesn't support ALTER COLUMN, so recreate goals table to make target_count nullable
    await _migrate_goals_nullable_target(conn, logger)


async def _migrate_goals_nullable_target(conn: AsyncConnection, logger: "logging.Logger") -> None:
    """Recreate goals table to make target_count nullable. SQLite requires table rebuild for this."""
    import sqlalchemy

    # Check if goals table exists and if target_count is NOT NULL
    try:
        result = await conn.execute(sqlalchemy.text("PRAGMA table_info(goals)"))
        columns = result.all()
    except Exception:
        return  # Table doesn't exist yet

    if not columns:
        return

    # Find target_count column — column[3] is the notnull flag
    for col in columns:
        if col[1] == "target_count" and col[3] == 1:  # notnull=1 means NOT NULL
            break
    else:
        return  # Already nullable or column not found

    logger.info("Migration: rebuilding goals table to make target_count nullable")
    try:
        await conn.execute(sqlalchemy.text("""
            CREATE TABLE goals_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(100),
                name VARCHAR(255) NOT NULL,
                description TEXT,
                target_count INTEGER,
                daily_quota INTEGER NOT NULL DEFAULT 1,
                unit VARCHAR(50) NOT NULL DEFAULT 'times',
                deadline DATE,
                reminder_time TIME,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                apscheduler_job_id VARCHAR(255)
            )
        """))
        await conn.execute(sqlalchemy.text("INSERT INTO goals_new SELECT * FROM goals"))
        await conn.execute(sqlalchemy.text("DROP TABLE goals"))
        await conn.execute(sqlalchemy.text("ALTER TABLE goals_new RENAME TO goals"))
        await conn.execute(sqlalchemy.text("CREATE INDEX ix_goals_user_id ON goals (user_id)"))
        logger.info("Migration: goals table rebuilt successfully")
    except Exception:
        logger.exception("Migration: failed to rebuild goals table")
