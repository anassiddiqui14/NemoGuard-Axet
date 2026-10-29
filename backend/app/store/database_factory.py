"""
Database backend factory -- Phase 1 of the aXet Cloud SQLite migration.

NEMOGUARD_DB_BACKEND selects the backend EXPLICITLY:
  - "postgres" (default, and the ONLY behavior for existing local/
    standalone deployments that have never set this variable at all --
    PostgreSQL remains the default, unconditionally).
  - "sqlite" (explicit opt-in, used by aXet Cloud).

Deliberately does NOT infer "sqlite" just because POSTGRES_URL happens to
be unset -- an unset/misconfigured POSTGRES_URL in what was meant to be a
Postgres deployment must surface as a real connection failure, never as a
silent, surprising switch to a different database engine.
"""
import os

from app.store.sqlite_database import SqliteDatabase

_VALID_BACKENDS = {"postgres", "sqlite"}

DEFAULT_SQLITE_PATH = "/internal-storage-files/files/nemoguard/nemoguard.db"
DEFAULT_POSTGRES_URL = "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"


def get_database():
    """
    Returns a PostgresDatabase or SqliteDatabase instance based on
    NEMOGUARD_DB_BACKEND. Raises ValueError for any value other than the
    two explicitly supported backends, rather than silently guessing --
    an operator who mistypes the env var (e.g. "sqlite3", "pg",
    "postgresql") must see a clear startup error, not have their
    deployment silently fall back to Postgres (or worse, to SQLite when
    Postgres was actually intended).
    """
    backend = os.getenv("NEMOGUARD_DB_BACKEND", "postgres").strip().lower()

    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"Invalid NEMOGUARD_DB_BACKEND={backend!r}. "
            f"Must be one of: {sorted(_VALID_BACKENDS)}."
        )

    if backend == "sqlite":
        return SqliteDatabase(
            os.getenv("NEMOGUARD_SQLITE_PATH", DEFAULT_SQLITE_PATH)
        )

    # Imported lazily (not at module load time) so a pure-SQLite
    # deployment (e.g. aXet Cloud) never requires psycopg2 to be
    # installed at all -- only importing PostgresDatabase when the
    # Postgres backend is actually selected.
    from app.store.postgres_database import PostgresDatabase
    return PostgresDatabase(
        os.getenv("POSTGRES_URL", DEFAULT_POSTGRES_URL)
    )


def get_db_backend_name() -> str:
    """Returns the currently configured backend name ("postgres" or
    "sqlite") without constructing a database instance -- used by the
    /api/v2/status endpoint to report runtime state without leaking any
    connection string/path."""
    return os.getenv("NEMOGUARD_DB_BACKEND", "postgres").strip().lower()
