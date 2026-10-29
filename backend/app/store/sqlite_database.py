"""
SqliteDatabase -- Phase 1 of the aXet Cloud SQLite migration.

Mirrors the PUBLIC surface of PostgresDatabase (get_connection(),
init_schema(), apply_pending_migrations(), table_exists(), get_stats(),
reset(), close()) so call sites that already go through
`app.store.database_factory.get_database()` do not need to branch on
backend type.

Key design choice: rather than rewriting 130+ call sites' `%s`
placeholders to `?`, this module provides a thin DB-API-compatible
connection/cursor wrapper (`_SqliteConnectionWrapper` /
`_SqliteCursorWrapper`) that accepts the EXACT SAME `conn.execute(query,
params)` / `with conn.cursor() as cur:` calling convention already used
everywhere against PostgresDatabase, and transparently rewrites ONLY
real `%s` parameter placeholders to SQLite's `?` before delegating to the
real `sqlite3` cursor. This is intentionally NOT a general SQL dialect
translator -- Postgres-only constructs (ILIKE, FOR UPDATE, pg_tables,
cursor_factory=DictCursor, ON CONFLICT...RETURNING, etc.) are NOT
rewritten here and must be handled explicitly at each call site (see
Phase 1 report for the specific sites that needed such handling).
"""
import os
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Optional, Sequence


def _rewrite_placeholders(query: str) -> str:
    """
    Converts psycopg2-style `%s` positional parameter placeholders to
    SQLite's `?` placeholder, WITHOUT touching:
      - `%%` (a literal percent sign escaped the DB-API way -- becomes a
        single literal `%` after this rewrite, matching psycopg2's own
        escaping convention so existing queries that happen to use it
        behave identically under both backends);
      - `%` appearing inside SQL string literals (e.g. a LIKE pattern
        like '%foo%' written directly in the query text rather than
        passed as a bound parameter) -- this function only ever replaces
        the exact two-character sequence `%s`, which cannot occur as a
        meaningful fragment of a LIKE-pattern literal (a literal `%s`
        inside quotes would be a highly unusual coincidence, and no
        query in this codebase does that -- confirmed by inspection of
        every raw query string during the Phase 1 database-call-site
        audit). No attempt is made to parse SQL deeply; this is a
        narrow, intentionally simple token substitution, not a general
        SQL dialect translator.
    """
    # Protect literal %% first so it round-trips through the %s pass
    # unaffected, then restore it afterward.
    placeholder = "\x00ESCAPED_PERCENT\x00"
    query = query.replace("%%", placeholder)
    query = query.replace("%s", "?")
    query = query.replace(placeholder, "%")
    return query


class _SqliteCursorWrapper:
    """
    Wraps a real sqlite3.Cursor so existing call sites written against
    PostgresDatabase's ConnectionWrapper keep working unmodified:
      - `cur.execute(query, params)` -- rewrites %s -> ? first.
      - `cur.fetchone()` / `cur.fetchall()` -- delegate directly (sqlite3
        rows already support `row[0]`-style positional access via the
        default row_factory, and the connection sets
        `row.row_factory = sqlite3.Row` so `dict(zip(cols, row))`-style
        code in the existing API routes also keeps working since
        sqlite3.Row supports iteration like a tuple).
      - `cur.description` -- sqlite3.Cursor already exposes this with the
        same (name, ...) tuple shape psycopg2 uses for `col[0]` access.
      - `cur.rowcount` -- delegates directly; sqlite3 populates this for
        INSERT/UPDATE/DELETE the same way psycopg2 does.
      - Supports `with conn.cursor() as cur:` via __enter__/__exit__.
    """

    def __init__(self, raw_cursor: sqlite3.Cursor):
        self._cursor = raw_cursor

    def execute(self, query: str, params: Optional[Sequence[Any]] = None):
        rewritten = _rewrite_placeholders(query)
        if params is None:
            self._cursor.execute(rewritten)
        else:
            self._cursor.execute(rewritten, tuple(params))
        return self

    def executemany(self, query: str, seq_of_params: Sequence[Sequence[Any]]):
        rewritten = _rewrite_placeholders(query)
        self._cursor.executemany(rewritten, [tuple(p) for p in seq_of_params])
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size: int = 1):
        return self._cursor.fetchmany(size)

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class _SqliteConnectionWrapper:
    """
    Wraps a real sqlite3.Connection to expose the exact same surface
    PostgresDatabase.ConnectionWrapper does:
      - `conn.execute(query, vars)` -> returns a cursor-like object
        (matching the existing call-site pattern `cursor =
        conn.execute(...)` used throughout backend/app/api/main.py and
        backend/app/domain/**/*.py).
      - `conn.cursor(*args, **kwargs)` -- `*args/**kwargs` are accepted
        and IGNORED (e.g. `cursor_factory=...` from the few call sites
        that still pass it) since this wrapper's cursor already returns
        sqlite3.Row-based rows; those specific call sites are handled
        explicitly elsewhere in Phase 1 rather than by silently
        swallowing the Postgres-only kwarg here.
      - `conn.commit()` / `conn.rollback()` / `conn.close()`.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def execute(self, query: str, vars: Optional[Sequence[Any]] = None):
        cur = _SqliteCursorWrapper(self.conn.cursor())
        cur.execute(query, vars)
        return cur

    def cursor(self, *args, **kwargs):
        return _SqliteCursorWrapper(self.conn.cursor())

    def executemany(self, query: str, seq_of_params: Sequence[Sequence[Any]]):
        rewritten = _rewrite_placeholders(query)
        self.conn.executemany(rewritten, [tuple(p) for p in seq_of_params])

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


class SqliteDatabase:
    """
    SQLite-backed implementation of the same public surface
    PostgresDatabase exposes, for aXet Cloud deployments where no
    PostgreSQL service is available and the only persistent storage is a
    mounted file path (e.g. /internal-storage-files/files/nemoguard/).

    `db_path` here is a real filesystem path (unlike PostgresDatabase,
    where `db_path` is actually a connection string) -- the parent
    directory is created automatically if it does not already exist.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        parent_dir = os.path.dirname(db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            wrapper = _SqliteConnectionWrapper(conn)
            yield wrapper
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def table_exists(self, table_name: str) -> bool:
        """
        SQLite-native equivalent of PostgresDatabase's pg_tables check --
        see app.store.database_factory / backend/app/api/main.py's
        startup schema-exists probe, which now calls this method instead
        of embedding dialect-specific catalog SQL directly.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            return cursor.fetchone() is not None

    def init_schema(self):
        """
        Idempotently initializes the CURRENT-STATE SQLite schema (NOT the
        Postgres migration files, which contain syntax SQLite cannot
        execute -- see backend/app/store/schema_sqlite.sql's own header
        comment for the full rationale). Safe to call on every startup:
        every statement in schema_sqlite.sql uses `CREATE TABLE IF NOT
        EXISTS` / `CREATE INDEX IF NOT EXISTS`.
        """
        schema_path = os.path.join(os.path.dirname(__file__), "schema_sqlite.sql")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        with self.get_connection() as conn:
            conn.conn.executescript(schema_sql)

    def apply_pending_migrations(self):
        """
        No-op by design for Phase 1: the Postgres `migrations/*.sql`
        files are never executed against SQLite (they contain syntax --
        DO $$ blocks, ALTER TABLE ... DROP CONSTRAINT, multi-column ADD
        COLUMN IF NOT EXISTS, SERIAL, TIMESTAMPTZ, TEXT[] -- that SQLite
        cannot parse at all). schema_sqlite.sql already represents the
        full current-state schema with all Postgres migrations already
        folded in. Future SQLite-specific schema changes should be
        tracked via the `nemoguard_schema_metadata` table (see
        schema_sqlite.sql) and a dedicated SQLite migration runner added
        here when that need actually arises -- not before.
        """
        return

    def get_stats(self) -> dict:
        tables = [
            "job", "dependency", "business_asset", "asset_dependency",
            "execution", "log_event", "alert", "incident",
            "incident_alert", "evidence", "approval", "audit_event",
        ]
        stats = {}
        with self.get_connection() as conn:
            for table in tables:
                try:
                    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[table] = cursor.fetchone()[0]
                except sqlite3.Error:
                    stats[table] = 0
        return stats

    def reset(self):
        """
        Mirrors PostgresDatabase.reset()'s table list exactly (minus the
        Postgres-specific `CASCADE` keyword, which SQLite does not
        support -- SQLite's `DROP TABLE` already implicitly cascades via
        `PRAGMA foreign_keys`/FK enforcement at the row level, not at
        DROP time, so dropping tables in reverse-dependency order here is
        sufficient and matches the existing drop order already used by
        PostgresDatabase.reset()).
        """
        with self.get_connection() as conn:
            conn.execute("PRAGMA foreign_keys = OFF;")
            tables = [
                "audit_event", "approval", "incident_evidence", "incident_alert",
                "incident", "alert", "log_event", "execution",
                "asset_dependency", "business_asset", "dependency", "job",
            ]
            for table in tables:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute("PRAGMA foreign_keys = ON;")
        self.init_schema()

    def close(self):
        pass
