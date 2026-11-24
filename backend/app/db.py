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
    "projects": {
        "iteration": "INTEGER NOT NULL DEFAULT 0",
        "user_facing": "BOOLEAN NOT NULL DEFAULT 1",
    },
    "agent_messages": {
        "iteration": "INTEGER NOT NULL DEFAULT 0",
        "meta": "TEXT",  # D-028 (JSON dict; NULL = plain-text question)
    },
    "tasks": {
        "category": "TEXT NOT NULL DEFAULT 'feature'",
        "iteration": "INTEGER NOT NULL DEFAULT 0",
        "supersedes": "TEXT NOT NULL DEFAULT '[]'",  # D-024 (JSON list)
    },
    "approvals": {
        "iteration": "INTEGER NOT NULL DEFAULT 0",
        "artifact_versions": "TEXT",  # D-023 (JSON dict; NULL = no snapshot)
    },
}

# The build phase (task execution, attempts, logs, mid-build feedback) was
# removed in favour of a handoff prompt. Best-effort cleanup so an existing dev
# DB matches the new schema; each statement is a no-op on fresh/already-migrated DBs.
_DROP_TABLES = ["task_attempts", "execution_logs", "user_feedback"]
_DROP_COLUMNS: dict[str, list[str]] = {
    "tasks": ["status", "attempts", "max_attempts"],
}
# SQLite refuses DROP COLUMN on a column an index still references.
_DROP_INDEXES: dict[str, list[str]] = {
    "tasks": ["ix_tasks_project_status", "ix_tasks_project_number"],  # the latter duplicated the unique index
}

# Rows written before the pivot can carry a ProjectState that no longer exists;
# SQLAlchemy raises LookupError when it loads them, so rewrite them first.
# A BUILDING project had approved specs and a task plan, which today is COMPLETED
# (the handoff prompt for it is regenerated at startup — see Runner.resume).
_STATE_REMAP: dict[str, str] = {"BUILDING": "COMPLETED"}

# Artifacts written by the removed build phase; they are not specs.
_DROP_ARTIFACT_SLUGS = ["delivery-report.md"]


def ensure_columns(engine) -> None:
    """Migrate an existing DB in-place: add columns, drop the build-phase
    leftovers, and rewrite values the current models can no longer represent.

    SQLite >=3.35 supports DROP COLUMN; older versions raise, which we swallow —
    a stale extra column is harmless, and fresh installs never hit this path.
    """
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

    with engine.begin() as conn:
        for old, new in _STATE_REMAP.items():
            conn.execute(
                text("UPDATE projects SET state = :new WHERE state = :old"), {"new": new, "old": old}
            )
        if inspector.has_table("artifacts") and inspector.has_table("artifact_versions"):
            for slug in _DROP_ARTIFACT_SLUGS:
                conn.execute(
                    text(
                        "DELETE FROM artifact_versions WHERE artifact_id IN "
                        "(SELECT id FROM artifacts WHERE slug = :slug)"
                    ),
                    {"slug": slug},
                )
        if inspector.has_table("artifacts"):
            for slug in _DROP_ARTIFACT_SLUGS:
                conn.execute(text("DELETE FROM artifacts WHERE slug = :slug"), {"slug": slug})
        for table, indexes in _DROP_INDEXES.items():
            for name in indexes:
                conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        for table in _DROP_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))

    if inspector.has_table("tasks"):
        cols = {c["name"] for c in inspector.get_columns("tasks")}
        with engine.begin() as conn:
            for name in _DROP_COLUMNS["tasks"]:
                if name in cols:
                    try:
                        conn.execute(text(f"ALTER TABLE tasks DROP COLUMN {name}"))
                    except Exception:  # noqa: BLE001 - older SQLite; harmless leftover
                        pass


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
