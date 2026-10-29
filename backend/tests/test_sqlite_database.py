"""
Phase 1 SQLite backend tests -- focused coverage for SqliteDatabase,
database_factory, and the %s->?  compatibility wrapper, using a temporary
.db file (never :memory:, so persistence-across-reopen can actually be
verified).

Run with: pytest backend/tests/test_sqlite_database.py -v
"""
import json
import os
import sys
import tempfile
import unittest

# Make `app.*` imports resolve the same way the live app does.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.store.sqlite_database import SqliteDatabase
from app.store.database_factory import get_database, get_db_backend_name


class TestSqliteDatabaseBootstrap(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "subdir", "nemoguard_test.db")
        self.db = SqliteDatabase(self.db_path)

    def test_parent_directory_created_automatically(self):
        self.assertTrue(os.path.isdir(os.path.dirname(self.db_path)))

    def test_schema_bootstrap_idempotent(self):
        self.db.init_schema()
        self.db.init_schema()  # must not raise on second call
        self.assertTrue(self.db.table_exists("incident"))

    def test_table_exists_false_for_unknown_table(self):
        self.db.init_schema()
        self.assertFalse(self.db.table_exists("not_a_real_table"))

    def test_journal_mode_is_not_wal(self):
        """
        aXet Cloud regression guard: WAL mode requires -shm/-wal sidecar
        files that are NOT reliably supported on the aXet Cloud mounted
        storage path, which previously caused a hard startup failure
        (sqlite3.OperationalError: database is locked) directly from
        `PRAGMA journal_mode = WAL`. get_connection() must never set WAL
        (or any other journal mode) -- SQLite's default rollback journal
        (normally reported as "delete") must remain in effect.
        """
        self.db.init_schema()
        with self.db.get_connection() as conn:
            cursor = conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
        self.assertNotEqual(mode.lower(), "wal")

    def test_no_wal_sidecar_files_created(self):
        """
        Confirms no -wal / -shm sidecar files appear next to the main
        .db file after normal use -- the concrete, file-system-level
        proof that WAL mode is not active (sidecar files are WAL mode's
        defining artifact; their absence is the real regression guard,
        independent of what PRAGMA journal_mode happens to report).
        """
        self.db.init_schema()
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, "
                "created_at, updated_at, version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-WAL-1", "T", "DETECTED", "SEV-1", "2026-01-01T00:00:00Z",
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 1),
            )
        self.assertFalse(os.path.exists(self.db_path + "-wal"))
        self.assertFalse(os.path.exists(self.db_path + "-shm"))

    def test_repeated_sequential_connections_do_not_require_wal(self):
        """
        Exercises many short-lived, sequential get_connection() calls
        (each opens and fully closes its own connection, as every real
        call site in this codebase already does) to confirm this pattern
        works correctly without WAL mode -- the concurrency model this
        class relies on for aXet Cloud's single-process deployment
        target is "one connection open at a time, closed promptly," not
        WAL's multi-reader/single-writer model.
        """
        self.db.init_schema()
        for i in range(25):
            with self.db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO audit_event (audit_event_id, actor_type, actor_id, "
                    "event_type, event_summary, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (f"AUD-WAL-{i}", "SYSTEM", "test", "TEST_EVENT", "seq", "2026-01-01T00:00:00Z"),
                )
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM audit_event WHERE actor_id = %s", ("test",))
            self.assertEqual(cursor.fetchone()[0], 25)

    def test_schema_metadata_version_row_present(self):
        self.db.init_schema()
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT value FROM nemoguard_schema_metadata WHERE key = %s",
                ("sqlite_schema_version",),
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "1")


class TestSqliteDatabaseCrud(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "nemoguard_test.db")
        self.db = SqliteDatabase(self.db_path)
        self.db.init_schema()

    def _insert_job(self, conn, job_id="JOB-1"):
        conn.execute(
            "INSERT INTO job (job_id, job_name, platform, domain, stage, schedule, "
            "criticality, default_duration_sec, owner_team, retry_policy, active) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (job_id, "Test Job", "Simulator", "Test", "Ingest", "@daily", 1, 60, "DataOps", "none", 1),
        )

    def test_alert_insert_and_read(self):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO alert (alert_id, run_id, opened_ts, severity, alert_type, "
                "source_system, message, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("ALT-1", None, "2026-01-01T00:00:00Z", "high", "TEST_ALERT", "Datadog", "boom", "open"),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT alert_id, severity, message FROM alert WHERE alert_id = %s", ("ALT-1",))
            row = cursor.fetchone()
        self.assertEqual(row[0], "ALT-1")
        self.assertEqual(row[1], "high")
        self.assertEqual(row[2], "boom")

    def test_incident_insert_read_update(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, "
                "created_at, updated_at, summary, version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-1", "Test incident", "INVESTIGATING", "SEV-1", now, now, now, "summary", 1),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT status FROM incident WHERE incident_id = %s", ("INC-1",))
            self.assertEqual(cursor.fetchone()[0], "INVESTIGATING")

        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status = %s WHERE incident_id = %s", ("RESOLVED", "INC-1"))
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT status FROM incident WHERE incident_id = %s", ("INC-1",))
            self.assertEqual(cursor.fetchone()[0], "RESOLVED")

    def test_incident_alert_relationship(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-2", "T", "INVESTIGATING", "SEV-1", now, now, now, 1),
            )
            conn.execute(
                "INSERT INTO alert (alert_id, opened_ts, severity, alert_type, source_system, message, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("ALT-2", now, "high", "X", "Datadog", "m", "open"),
            )
            conn.execute(
                "INSERT INTO incident_alert (incident_id, alert_id, correlation_score, correlation_reasons_json, added_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                ("INC-2", "ALT-2", 1.0, json.dumps(["test"]), now),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT a.alert_id FROM alert a JOIN incident_alert ia ON a.alert_id = ia.alert_id "
                "WHERE ia.incident_id = %s",
                ("INC-2",),
            )
            rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "ALT-2")

    def test_evidence_insert(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-3", "T", "INVESTIGATING", "SEV-1", now, now, now, 1),
            )
            conn.execute(
                "INSERT INTO evidence (evidence_id, incident_id, evidence_type, source_system, title, excerpt, collected_at, authority) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("EVD-1", "INC-3", "Log", "System", "Title", "excerpt text", now, "MEDIUM"),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT title FROM evidence WHERE evidence_id = %s", ("EVD-1",))
            self.assertEqual(cursor.fetchone()[0], "Title")

    def test_action_plan_and_step(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-4", "T", "PLAN_READY", "SEV-1", now, now, now, 1),
            )
            conn.execute(
                "INSERT INTO action_plan (action_plan_id, incident_id, agent_run_id, plan_version, status, "
                "overall_risk, rationale, expected_outcome, rollback_summary, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("PLN-1", "INC-4", "SYSTEM", 1, "PENDING_APPROVAL", "LOW", "r", "o", "rollback", now),
            )
            conn.execute(
                "INSERT INTO action_step (action_step_id, action_plan_id, sequence_no, action_type, tool_name, "
                "risk_level, requires_approval, parameters_json, preconditions_json, expected_postconditions_json, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("STP-1", "PLN-1", 1, "restart", "restart_tool", "LOW", 0, "{}", "{}", "{}", "PENDING"),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM action_step WHERE action_plan_id = %s", ("PLN-1",)
            )
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_approval_insert(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-5", "T", "PLAN_READY", "SEV-1", now, now, now, 1),
            )
            conn.execute(
                "INSERT INTO action_plan (action_plan_id, incident_id, agent_run_id, plan_version, status, "
                "overall_risk, rationale, expected_outcome, rollback_summary, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("PLN-2", "INC-5", "SYSTEM", 1, "PENDING_APPROVAL", "LOW", "r", "o", "rollback", now),
            )
            conn.execute(
                "INSERT INTO approval (approval_id, incident_id, action_plan_id, requested_at, expires_at, "
                "decision, decided_at, approver_id, plan_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("APP-1", "INC-5", "PLN-2", now, now, "approve", now, "user1", "hash123"),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT decision FROM approval WHERE approval_id = %s", ("APP-1",))
            self.assertEqual(cursor.fetchone()[0], "approve")

    def test_audit_event_insert(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-6", "T", "INVESTIGATING", "SEV-1", now, now, now, 1),
            )
            conn.execute(
                "INSERT INTO audit_event (audit_event_id, incident_id, actor_type, actor_id, event_type, "
                "event_summary, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("AUD-1", "INC-6", "SYSTEM", "Commander", "TRIAGE_STARTED", "Triage started", now),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT event_type FROM audit_event WHERE incident_id = %s ORDER BY created_at ASC",
                ("INC-6",),
            )
            rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "TRIAGE_STARTED")

    def test_commander_status_update_and_messages(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at, "
                "tenant_id, version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-7", "T", "PLAN_READY", "SEV-1", now, now, now, "default_tenant", 1),
            )
            conn.execute(
                "INSERT INTO commander_status_update (update_id, incident_id, source_audit_event_id, update_type, "
                "created_at, headline, summary, severity, notify_external) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("CUPD-1", "INC-7", "AUD-X", "PLAN_READY", now, "Plan ready", "summary", None, 0),
            )
            conn.execute(
                "INSERT INTO incident_message (message_id, incident_id, role, intent, content, created_by, "
                "created_at, evidence_refs_json, guidance_id, guidance_revision) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("MSG-1", "INC-7", "OPERATOR", "ASK", "What happened?", "user1", now, "[]", None, None),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT headline FROM commander_status_update WHERE incident_id = %s", ("INC-7",)
            )
            self.assertEqual(cursor.fetchone()[0], "Plan ready")
            cursor = conn.execute(
                "SELECT content FROM incident_message WHERE incident_id = %s", ("INC-7",)
            )
            self.assertEqual(cursor.fetchone()[0], "What happened?")

    def test_commander_status_update_unique_constraint_idempotent(self):
        """Re-inserting the same source_audit_event_id must be a no-op
        (ON CONFLICT DO NOTHING via the unique index), matching
        StatusProjector.project_audit_event()'s idempotency contract."""
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-8", "T", "PLAN_READY", "SEV-1", now, now, now, 1),
            )
            conn.execute(
                "INSERT INTO commander_status_update (update_id, incident_id, source_audit_event_id, update_type, "
                "created_at, headline, summary, severity, notify_external) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (source_audit_event_id) DO NOTHING",
                ("CUPD-2", "INC-8", "AUD-DUP", "PLAN_READY", now, "First", None, None, 0),
            )
            cursor = conn.execute(
                "INSERT INTO commander_status_update (update_id, incident_id, source_audit_event_id, update_type, "
                "created_at, headline, summary, severity, notify_external) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (source_audit_event_id) DO NOTHING",
                ("CUPD-3", "INC-8", "AUD-DUP", "PLAN_READY", now, "Second", None, None, 0),
            )
            self.assertEqual(cursor.rowcount, 0)
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM commander_status_update WHERE source_audit_event_id = %s",
                ("AUD-DUP",),
            )
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_platform_user_roles_json_serialization(self):
        """Confirms platform_user.roles round-trips as a JSON array TEXT
        column under SQLite -- matching app.api.auth.serialize_roles()/
        deserialize_roles()'s expected on-disk representation."""
        now = "2026-01-01T00:00:00Z"
        roles_json = json.dumps(["viewer", "commander"])
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO platform_user (user_id, email, password_hash, display_name, roles, "
                "tenant_id, workspace_id, is_active, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("USR-1", "test@example.com", "hash", "Test User", roles_json, "default_tenant", "default_workspace", 1, now),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT roles FROM platform_user WHERE user_id = %s", ("USR-1",))
            raw = cursor.fetchone()[0]
        parsed = json.loads(raw)
        self.assertEqual(parsed, ["viewer", "commander"])

    def test_platform_user_default_roles(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO platform_user (user_id, email, password_hash, created_at) VALUES (%s, %s, %s, %s)",
                ("USR-2", "default@example.com", "hash", now),
            )
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT roles FROM platform_user WHERE user_id = %s", ("USR-2",))
            raw = cursor.fetchone()[0]
        self.assertEqual(json.loads(raw), ["viewer"])

    def test_triage_atomic_begin_immediate_transaction_path(self):
        """Exercises the same BEGIN IMMEDIATE pattern used by
        triage_scheduler.schedule_triage() under SQLite: a check-then-set
        inside one atomic transaction must commit together."""
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-9", "T", "DETECTED", "SEV-1", now, now, now, 1),
            )

        raw_conn = self.db.get_connection()
        conn = raw_conn.__enter__()
        conn.conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("SELECT status FROM incident WHERE incident_id = %s", ("INC-9",))
        status = cursor.fetchone()[0]
        self.assertEqual(status, "DETECTED")
        conn.execute("UPDATE incident SET status = %s WHERE incident_id = %s", ("INVESTIGATING", "INC-9"))
        raw_conn.__exit__(None, None, None)  # commits

        with self.db.get_connection() as conn2:
            cursor = conn2.execute("SELECT status FROM incident WHERE incident_id = %s", ("INC-9",))
            self.assertEqual(cursor.fetchone()[0], "INVESTIGATING")

    def test_persistence_across_reopen(self):
        """Confirms data survives closing and reopening a fresh
        SqliteDatabase instance against the same file path -- i.e. this
        is real on-disk persistence, not an in-memory connection that
        would lose state."""
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ("INC-10", "Persisted", "DETECTED", "SEV-1", now, now, now, 1),
            )

        reopened = SqliteDatabase(self.db_path)
        with reopened.get_connection() as conn:
            cursor = conn.execute("SELECT title FROM incident WHERE incident_id = %s", ("INC-10",))
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Persisted")


class TestDatabaseFactory(unittest.TestCase):
    def setUp(self):
        self._orig_backend = os.environ.get("NEMOGUARD_DB_BACKEND")
        self._orig_path = os.environ.get("NEMOGUARD_SQLITE_PATH")

    def tearDown(self):
        if self._orig_backend is not None:
            os.environ["NEMOGUARD_DB_BACKEND"] = self._orig_backend
        else:
            os.environ.pop("NEMOGUARD_DB_BACKEND", None)
        if self._orig_path is not None:
            os.environ["NEMOGUARD_SQLITE_PATH"] = self._orig_path
        else:
            os.environ.pop("NEMOGUARD_SQLITE_PATH", None)

    def test_defaults_to_postgres_backend_name(self):
        os.environ.pop("NEMOGUARD_DB_BACKEND", None)
        self.assertEqual(get_db_backend_name(), "postgres")

    def test_explicit_sqlite_backend_returns_sqlite_database(self):
        tmpdir = tempfile.mkdtemp()
        os.environ["NEMOGUARD_DB_BACKEND"] = "sqlite"
        os.environ["NEMOGUARD_SQLITE_PATH"] = os.path.join(tmpdir, "factory_test.db")
        self.assertEqual(get_db_backend_name(), "sqlite")
        db = get_database()
        self.assertIsInstance(db, SqliteDatabase)
        db.init_schema()
        self.assertTrue(db.table_exists("incident"))

    def test_invalid_backend_raises_value_error(self):
        os.environ["NEMOGUARD_DB_BACKEND"] = "mysql"
        with self.assertRaises(ValueError):
            get_database()


class TestBootstrapAdmin(unittest.TestCase):
    """
    Covers app.api.main._bootstrap_admin_if_configured(), which is the
    only way to provision a first platform_user account into a brand-new
    SQLite file when AXET_FLOWS_AUTH=true (otherwise nobody could ever
    sign in, since _get_axet_user() has nothing to match against).
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "bootstrap_test.db")
        self.db = SqliteDatabase(self.db_path)
        self.db.init_schema()
        self._orig_email = os.environ.get("NEMOGUARD_BOOTSTRAP_ADMIN_EMAIL")

    def tearDown(self):
        if self._orig_email is not None:
            os.environ["NEMOGUARD_BOOTSTRAP_ADMIN_EMAIL"] = self._orig_email
        else:
            os.environ.pop("NEMOGUARD_BOOTSTRAP_ADMIN_EMAIL", None)

    def test_noop_when_email_not_configured(self):
        os.environ.pop("NEMOGUARD_BOOTSTRAP_ADMIN_EMAIL", None)
        from app.api.main import _bootstrap_admin_if_configured
        _bootstrap_admin_if_configured(self.db)
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM platform_user")
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_creates_admin_account_once(self):
        os.environ["NEMOGUARD_BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
        from app.api.main import _bootstrap_admin_if_configured
        _bootstrap_admin_if_configured(self.db)
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT email, roles, is_active FROM platform_user WHERE email = %s",
                ("admin@example.com",),
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "admin@example.com")
        self.assertEqual(json.loads(row[1]), ["admin"])
        self.assertEqual(row[2], 1)

    def test_idempotent_second_call_does_not_duplicate(self):
        os.environ["NEMOGUARD_BOOTSTRAP_ADMIN_EMAIL"] = "admin2@example.com"
        from app.api.main import _bootstrap_admin_if_configured
        _bootstrap_admin_if_configured(self.db)
        _bootstrap_admin_if_configured(self.db)
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM platform_user WHERE email = %s",
                ("admin2@example.com",),
            )
            self.assertEqual(cursor.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
