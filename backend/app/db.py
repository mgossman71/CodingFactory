"""SQLAlchemy engine/session setup for SQLite (WAL)."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


# create_all() does not add columns to existing tables; live DBs need this.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "projects": {"iteration": "INTEGER NOT NULL DEFAULT 0"},
    "agent_messages": {"iteration": "INTEGER NOT NULL DEFAULT 0"},
}


def ensure_columns(engine) -> None:
    """Add missing columns in-place (SQLite ADD COLUMN is cheap and safe)."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    for table, columns in _ADDITIVE_COLUMNS.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        with engine.begin() as conn:
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
