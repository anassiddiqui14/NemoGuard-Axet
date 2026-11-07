"""Focused tests for the V22 guarded incident-cancel API."""
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ["JWT_SECRET"] = "isolated-test-secret"
os.environ["AXET_FLOWS_AUTH"] = "false"

from fastapi.testclient import TestClient
from app.api.main import app
from app.api.auth import create_access_token


def token(role: str, tenant_id: str = "default_tenant", workspace_id: str = "default_workspace") -> str:
    return create_access_token({
        "sub": "test-user",
        "roles": [role],
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
    })


class IncidentCancelTests(unittest.TestCase):
    def setUp(self):
        import app.api.main as api
        from app.store.sqlite_database import SqliteDatabase

        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.tmp.name) / "test.db"))
        self.db.init_schema()
        self.db_patch = patch.object(api, "get_database", return_value=self.db)
        self.db_patch.start()
        self.client = TestClient(app)

        now = "2026-11-07T00:00:00+00:00"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, tenant_id, workspace_id, title, status, severity, detected_at, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("INC-CANCEL", "default_tenant", "default_workspace", "Awaiting approval", "AWAITING_APPROVAL", "SEV_2", now, now, now),
            )
            conn.execute(
                "INSERT INTO incident (incident_id, tenant_id, workspace_id, title, status, severity, detected_at, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("INC-OTHER-WS", "default_tenant", "other_workspace", "Other workspace", "AWAITING_APPROVAL", "SEV_2", now, now, now),
            )

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_commander_can_cancel_at_approval_checkpoint(self):
        headers = {"Authorization": "Bearer " + token("commander")}
        response = self.client.post(
            "/api/v2/incidents/INC-CANCEL/cancel",
            json={"reason": "Operator chose not to execute the proposed recovery"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CANCELLED")
        with self.db.get_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT status FROM incident WHERE incident_id=%s", ("INC-CANCEL",)).fetchone()[0],
                "CANCELLED",
            )
            event = conn.execute(
                "SELECT event_type, event_summary FROM audit_event WHERE incident_id=%s ORDER BY created_at DESC LIMIT 1",
                ("INC-CANCEL",),
            ).fetchone()
            self.assertEqual(event[0], "INCIDENT_CANCELLED")
            self.assertIn("Operator chose not to execute", event[1])

    def test_cancel_requires_commander_role(self):
        headers = {"Authorization": "Bearer " + token("operator")}
        response = self.client.post(
            "/api/v2/incidents/INC-CANCEL/cancel",
            json={"reason": "No"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_cancel_is_workspace_scoped(self):
        headers = {"Authorization": "Bearer " + token("commander")}
        response = self.client.post(
            "/api/v2/incidents/INC-OTHER-WS/cancel",
            json={"reason": "Should not cross workspaces"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_cancel_rejects_unsafe_state(self):
        now = "2026-11-07T00:00:00+00:00"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, tenant_id, workspace_id, title, status, severity, detected_at, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("INC-EXEC", "default_tenant", "default_workspace", "Already executing", "EXECUTING", "SEV_2", now, now, now),
            )
        headers = {"Authorization": "Bearer " + token("commander")}
        response = self.client.post(
            "/api/v2/incidents/INC-EXEC/cancel",
            json={"reason": "Too late"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
