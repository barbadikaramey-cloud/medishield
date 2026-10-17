import sys
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from medishield.core.config import settings


class Base(DeclarativeBase):
    """Base declarative class for all MediShield SQLAlchemy models."""


def prepare_sqlite_schema(metadata) -> None:
    """
    Cross-Dialect Schema Bootstrap Hook (ADR-008).
    Removes Postgres-specific DDL syntax (e.g. server defaults with ::jsonb)
    to allow SQLite compatibility.
    """
    from sqlalchemy import JSON
    from sqlalchemy.dialects.postgresql import JSONB

    @event.listens_for(metadata, "before_create")
    def receive_before_create(target, connection, **kw):
        if connection.engine.dialect.name == "sqlite":
            for table in target.tables.values():
                for column in table.columns:
                    if isinstance(column.type, JSONB):
                        column.type = JSON()
                    if column.server_default is not None and hasattr(column.server_default.arg, "text"):
                        # Clear postgres-specific defaults for SQLite
                        arg_text = column.server_default.arg.text
                        if "::jsonb" in arg_text or "uuid_generate_v4()" in arg_text:
                            column.server_default = None


# Determine if we should fallback to SQLite
_use_sqlite = False

if settings.ENVIRONMENT in ("development", "test"):
    # Probe postgres reachability via raw socket to avoid psycopg2 hang
    import socket
    from urllib.parse import urlparse
    
    url = urlparse(settings.DATABASE_SYNC_URL)
    host = url.hostname or "localhost"
    port = url.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1.0):
            pass
    except (OSError, ConnectionRefusedError) as e:
        import os
        if os.environ.get('MEDISHIELD_STRICT_EVAL') == '1':
            raise RuntimeError('Strict eval mode active: SQLite fallback is forbidden.')
        _use_sqlite = True
        sys.stderr.write(f"PostgreSQL unreachable, falling back to SQLite (ADR-008): {e}\n")
elif settings.ENVIRONMENT in ("staging", "production"):
    # Fail fast if unreachable in prod
    import socket
    from urllib.parse import urlparse
    url = urlparse(settings.DATABASE_SYNC_URL)
    host = url.hostname or "localhost"
    port = url.port or 5432
    with socket.create_connection((host, port), timeout=5.0):
        pass

# Setup Engines
if _use_sqlite:
    sqlite_db_path = Path("data/medishield.db")
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_url = f"sqlite:///{sqlite_db_path.absolute().as_posix()}"
    async_sqlite_url = f"sqlite+aiosqlite:///{sqlite_db_path.absolute().as_posix()}"

    sync_engine = create_engine(
        sqlite_url,
        echo=settings.LOG_LEVEL == "DEBUG",
        connect_args={"check_same_thread": False},
    )
    async_engine = create_async_engine(
        async_sqlite_url,
        echo=settings.LOG_LEVEL == "DEBUG",
    )
    
    # Automatically bootstrap SQLite schema
    prepare_sqlite_schema(Base.metadata)
else:
    sync_engine = create_engine(
        settings.DATABASE_SYNC_URL,
        echo=settings.LOG_LEVEL == "DEBUG",
        pool_pre_ping=True,
    )
    async_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.LOG_LEVEL == "DEBUG",
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Instrument database engines with Logfire
from medishield.core.telemetry import instrument_db

instrument_db(sync_engine)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing an async database session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def get_sqlite_session_factory():
    """Returns a sessionmaker bound to the active sync engine."""
    return SyncSessionLocal


