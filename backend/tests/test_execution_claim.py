"""Execution claim tests using a temporary database and mocked actions."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.capabilities import execution_engine, intent_mapper, plan_compiler
from app.capabilities.models import VerificationStatus
from app.domain import orchestrator
from app.store.sqlite_database import SqliteDatabase


class ExecutionClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = SqliteDatabase(str(Path(self.tmp.name) / "test.db"))
        self.db.init_schema()
        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO incident (incident_id,title,status,severity,detected_at,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                ("inc", "Test", "EXECUTING", "high", "now", "now", "now"),
            )
            conn.execute(
                "INSERT INTO agent_run (agent_run_id,incident_id,agent_name,objective,status) VALUES (%s,%s,%s,%s,%s)",
                ("agent", "inc", "test", "test", "COMPLETED"),
            )
            conn.execute(
                "INSERT INTO action_plan (action_plan_id,incident_id,agent_run_id,plan_version,status,overall_risk,rationale,expected_outcome,rollback_summary,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("plan", "inc", "agent", 1, "APPROVED", "LOW", "review", "expected", "rollback", "now"),
            )
            conn.execute(
                "INSERT INTO action_step (action_step_id,action_plan_id,sequence_no,action_type,tool_name,risk_level,requires_approval,parameters_json,preconditions_json,expected_postconditions_json,status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("step", "plan", 1, "test", "manual", "LOW", 0, "{}", "{}", "{}", "PENDING"),
            )
        self.db_patch = patch.object(orchestrator, "get_database", return_value=self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.obj = orchestrator.IncidentOrchestrator()
        self.action = SimpleNamespace(capability_id="mock", capability_version="1", idempotency_key="key")
        self.compiled = SimpleNamespace(actions=[self.action], plan_hash="compiled")

    def run_with(self, effect):
        with patch.object(intent_mapper, "action_steps_to_intents", return_value=[object()]), \
             patch.object(plan_compiler, "compile_plan", return_value=self.compiled), \
             patch.object(execution_engine, "execute_compiled_action", side_effect=effect):
            return self.obj.execute_plan("inc", "plan")

    def status(self):
        with self.db.get_connection() as conn:
            return conn.execute("SELECT status FROM action_plan WHERE action_plan_id=%s", ("plan",)).fetchone()[0]

    def test_failure_after_claim_stays_claimed_and_cannot_retry(self):
        calls = []
        def fail(action):
            calls.append(action)
            raise RuntimeError("synthetic external failure")
        with self.assertRaises(RuntimeError):
            self.run_with(fail)
        self.assertEqual(self.status(), "EXECUTING")
        with self.assertRaises(ValueError):
            self.run_with(fail)
        self.assertEqual(len(calls), 1)

    def test_failed_action_is_not_reported_as_verified_recovery(self):
        now = datetime.now(timezone.utc)
        record = SimpleNamespace(
            result=SimpleNamespace(status=SimpleNamespace(value="FAILED"), started_at=now,
                                   raw_result={}, error_message="synthetic"),
            verification=SimpleNamespace(status=VerificationStatus.PASSED, details={}),
            capability_id="mock",
        )
        self.run_with(lambda action: record)
        self.assertEqual(self.status(), "FAILED")
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM incident WHERE incident_id=%s", ("inc",)).fetchone()[0], "FAILED")

    def test_empty_plan_is_rejected(self):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM action_step WHERE action_plan_id=%s", ("plan",))
        with self.assertRaisesRegex(ValueError, "without steps"):
            self.obj.execute_plan("inc", "plan")
        self.assertEqual(self.status(), "APPROVED")


if __name__ == "__main__":
    unittest.main()
