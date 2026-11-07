"""Tests for operator-initiated alert re-correlation between incidents."""
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


def token(role, tenant_id="default_tenant", workspace_id="default_workspace"):
    return create_access_token({
        "sub": "test-operator", "roles": [role],
        "tenant_id": tenant_id, "workspace_id": workspace_id,
    })


class RecorrelationTests(unittest.TestCase):
    def setUp(self):
        import app.api.main as api
        from app.store.sqlite_database import SqliteDatabase
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.tmp.name) / "test.db"))
        self.db.init_schema()
        self.db_patch = patch.object(api, "get_database", return_value=self.db)
        self.db_patch.start()
        self.client = TestClient(app)

        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            for incident_id in ("INC-A", "INC-B"):
                conn.execute(
                    "INSERT INTO incident (incident_id, tenant_id, title, status, severity, detected_at, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (incident_id, "default_tenant", f"Incident {incident_id}", "INVESTIGATING", "SEV_2", now, now, now),
                )
            conn.execute(
                "INSERT INTO alert (alert_id, opened_ts, severity, alert_type, source_system, message, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                ("ALT-1", now, "high", "TEST", "Datadog", "Synthetic alert", "open"),
            )
            conn.execute(
                "INSERT INTO incident_alert (incident_id, alert_id, correlation_score, correlation_reasons_json, added_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                ("INC-A", "ALT-1", 0.9, '["initial watcher correlation"]', now),
            )

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()
        self.tmp.cleanup()

    def url(self, incident_id="INC-A", alert_id="ALT-1"):
        return f"/api/v2/incidents/{incident_id}/alerts/{alert_id}/recorrelate"

    def test_requires_operator_role(self):
        body = {"target_incident_id": "INC-B", "reason": "Wrong correlation"}
        viewer = {"Authorization": "Bearer " + token("viewer")}
        self.assertEqual(self.client.post(self.url(), json=body, headers=viewer).status_code, 403)

    def test_moves_alert_and_audits_both_incidents(self):
        operator = {"Authorization": "Bearer " + token("operator")}
        body = {"target_incident_id": "INC-B", "reason": "Belongs to a different job failure"}
        response = self.client.post(self.url(), json=body, headers=operator)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["to_incident_id"], "INC-B")

        with self.db.get_connection() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM incident_alert WHERE incident_id=%s AND alert_id=%s", ("INC-A", "ALT-1"),
            ).fetchone())
            moved = conn.execute(
                "SELECT correlation_score FROM incident_alert WHERE incident_id=%s AND alert_id=%s",
                ("INC-B", "ALT-1"),
            ).fetchone()
            self.assertAlmostEqual(moved[0], 0.9)

            events_a = conn.execute(
                "SELECT event_type, event_summary FROM audit_event WHERE incident_id=%s", ("INC-A",),
            ).fetchall()
            events_b = conn.execute(
                "SELECT event_type, event_summary FROM audit_event WHERE incident_id=%s", ("INC-B",),
            ).fetchall()
            self.assertTrue(any(e[0] == "ALERT_RECORRELATED" and "moved to incident INC-B" in e[1] for e in events_a))
            self.assertTrue(any(e[0] == "ALERT_RECORRELATED" and "moved from incident INC-A" in e[1] for e in events_b))

    def test_rejects_missing_reason_and_same_incident_target(self):
        operator = {"Authorization": "Bearer " + token("operator")}
        self.assertEqual(self.client.post(self.url(), json={"target_incident_id": "INC-B", "reason": " "}, headers=operator).status_code, 400)
        self.assertEqual(self.client.post(self.url(), json={"target_incident_id": "INC-A", "reason": "same"}, headers=operator).status_code, 400)

    def test_rejects_alert_not_correlated_to_source(self):
        operator = {"Authorization": "Bearer " + token("operator")}
        body = {"target_incident_id": "INC-A", "reason": "test"}
        response = self.client.post(f"/api/v2/incidents/INC-B/alerts/ALT-1/recorrelate", json=body, headers=operator)
        self.assertEqual(response.status_code, 404)

    def test_rejects_duplicate_correlation_to_target(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident_alert (incident_id, alert_id, correlation_score, correlation_reasons_json, added_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                ("INC-B", "ALT-1", 0.5, '["already there"]', now),
            )
        operator = {"Authorization": "Bearer " + token("operator")}
        body = {"target_incident_id": "INC-B", "reason": "test"}
        self.assertEqual(self.client.post(self.url(), json=body, headers=operator).status_code, 409)

    def test_cross_tenant_incident_is_not_found(self):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id, tenant_id, title, status, severity, detected_at, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                ("INC-OTHER", "other_tenant", "Other tenant incident", "INVESTIGATING", "SEV_2",
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
        operator = {"Authorization": "Bearer " + token("operator")}
        body = {"target_incident_id": "INC-OTHER", "reason": "test"}
        self.assertEqual(self.client.post(self.url(), json=body, headers=operator).status_code, 404)


if __name__ == "__main__":
    unittest.main()
