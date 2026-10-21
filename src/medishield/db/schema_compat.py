"""Compatibility migrations for SQLite databases created before Alembic adoption."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Connection, inspect, text
from sqlalchemy.sql.schema import Column, Table

from medishield.db.models import Base


def _sqlite_column_definition(column: Column, connection: Connection) -> str:
    column_type = column.type.dialect_impl(connection.dialect)
    definition = column_type.compile(dialect=connection.dialect)
    if not column.nullable:
        definition += " NOT NULL"
        if column.type.python_type is bool:
            definition += " DEFAULT 0"
    return definition


def _missing_columns(table: Table, connection: Connection) -> Iterable[Column]:
    existing = {column["name"] for column in inspect(connection).get_columns(table.name)}
    return (column for column in table.columns if column.name not in existing)


def prepare_sqlite_schema(connection: Connection | None = None) -> None:
    """Bring legacy SQLite tables up to the current model schema.

    SQLite's ``create_all`` only creates missing tables; it does not add columns
    to tables created by an older application version. This compatibility step
    is intentionally idempotent and runs before startup seeding.
    """
    if connection is None:
        from medishield.db.session import sync_engine

        with sync_engine.begin() as managed_connection:
            prepare_sqlite_schema(managed_connection)
        return

    if connection.dialect.name != "sqlite":
        return

    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        for column in _missing_columns(table, connection):
            definition = _sqlite_column_definition(column, connection)
            connection.execute(
                text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN '
                    f'"{column.name}" {definition}'
                )
            )
