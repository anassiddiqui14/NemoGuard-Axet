"""Email preview authorization and input tests; no real messages or network."""
import os
import sys
import tempfile
from pathlib import Path
import types
import unittest
from unittest.mock import patch, AsyncMock

os.environ["JWT_SECRET"] = "isolated-test-secret"
os.environ["AXET_FLOWS_AUTH"] = "false"

from fastapi.testclient import TestClient
from app.api.main import app
from app.api.auth import create_access_token

MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


class FakeMessage:
    def __init__(self, data, delayAttachments=False):
        assert isinstance(data, bytes)
        assert delayAttachments is True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    subject = "Example subject"
    body = "Example body"
    attachments = [
        types.SimpleNamespace(longFilename="session.log", shortFilename=None, data=b"ERROR 1023"),
        types.SimpleNamespace(longFilename="malware.exe", shortFilename=None, data=b"binary"),
    ]


def token(role):
    return create_access_token({"sub": "test", "roles": [role]})


class PreviewTests(unittest.TestCase):
    def setUp(self):
        import app.api.main as api
        from app.store.sqlite_database import SqliteDatabase
        from app.store import alert_inbox
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.tmp.name) / "inbox.db"))
        alert_inbox.initialize(self.db)
        self.db_patch = patch.object(api, "get_database", return_value=self.db)
        self.db_patch.start()
        self.client = TestClient(app)
        self.env = patch.dict(os.environ, {"NEMOGUARD_WEBHOOK_SECRET": "test-webhook-secret"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.client.close()
        self.db_patch.stop()
        self.tmp.cleanup()

    def post(self, data, role=None, secret=None):
        headers = {}
        if role:
            headers["Authorization"] = "Bearer " + token(role)
        if secret:
            headers["X-NemoGuard-Webhook-Secret"] = secret
        return self.client.post("/api/v2/ingest/email-preview", content=data, headers=headers)

    def test_authentication_role_and_secret(self):
        self.assertEqual(self.post(MAGIC).status_code, 401)
        self.assertEqual(self.post(MAGIC, "viewer", "test-webhook-secret").status_code, 403)
        self.assertEqual(self.post(MAGIC, "operator", "wrong").status_code, 401)

    def test_invalid_and_oversized(self):
        self.assertEqual(self.post(b"not-msg", "operator", "test-webhook-secret").status_code, 400)
        self.assertEqual(self.post(MAGIC + b"x" * (5 * 1024 * 1024), "operator", "test-webhook-secret").status_code, 413)

    def test_replay_requires_operator_and_webhook_secret(self):
        import app.api.main as api
        payload = {"source": "Outlook email export", "monitor_name": "failure", "message": "fabricated"}
        url = "/api/v2/ingest/email-replay"
        headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "test"}
        self.assertEqual(self.client.post(url, json=payload, headers=headers).status_code, 401)
        viewer = {**headers, "Authorization": "Bearer " + token("viewer")}
        self.assertEqual(self.client.post(url, json=payload, headers=viewer).status_code, 403)
        operator = {**headers, "Authorization": "Bearer " + token("operator")}
        wrong_secret = {**operator, "X-NemoGuard-Webhook-Secret": "wrong"}
        self.assertEqual(self.client.post(url, json=payload, headers=wrong_secret).status_code, 401)
        with patch.object(api, "ingest_webhook", new_callable=AsyncMock, return_value={"status": "ingested"}) as ingest:
            response = self.client.post(url, json=payload, headers=operator)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ingested")
        ingest.assert_awaited_once()

    def test_outlook_bridge_auth_validation_and_provenance(self):
        import app.api.main as api
        url = "/api/v2/ingest/outlook-bridge"
        payload = {"source": "Outlook email", "monitor_name": "Test", "message": "Synthetic failure"}
        headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "outlook:test"}
        self.assertEqual(self.client.post(url, json=payload).status_code, 401)
        self.assertEqual(self.client.post(url, json={**payload, "message": ""}, headers=headers).status_code, 400)
        self.assertEqual(self.client.post(url, json={**payload, "source": "other"}, headers=headers).status_code, 400)

        async def fake_ingest(payload, request):
            self.assertEqual(request.state.reviewed_by, "automatic-outlook-bridge")
            self.assertEqual(request.state.reviewed_documents[0]["content"], "Synthetic failure")
            return {"status": "ingested"}

        with patch.object(api, "ingest_webhook", side_effect=fake_ingest) as ingest:
            response = self.client.post(url, json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ingested")
        ingest.assert_awaited_once()

    def test_outlook_bridge_persists_evidence_and_deduplicates(self):
        from app.domain import orchestrator as domain
        from app.domain import triage_scheduler
        from app.store.sqlite_database import SqliteDatabase

        class Watcher:
            async def analyze(self, payload, active_incidents):
                return {
                    "is_valid": True,
                    "normalized_alert": {
                        "severity": "high", "alert_type": "TEST",
                        "source_system": "Outlook", "message": "Reported failure",
                    },
                }

        with tempfile.TemporaryDirectory() as directory:
            db = SqliteDatabase(str(Path(directory) / "test.db"))
            db.init_schema()
            with db.get_connection() as conn:
                conn.execute("CREATE TABLE webhook_delivery (delivery_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL)")
                conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alert(alert_id), document_kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, submitted_by TEXT NOT NULL, created_at TEXT NOT NULL)")
            payload = {"source": "Outlook email", "monitor_name": "Synthetic job", "message": "Synthetic failure"}
            headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "outlook:integration"}
            from app.store import alert_inbox
            import app.api.main as api
            alert_inbox.initialize(db)
            with patch.object(api, "get_database", return_value=db), \
                 patch.object(domain, "get_database", return_value=db), \
                 patch.object(domain, "WatcherAgent", Watcher), \
                 patch.object(triage_scheduler, "schedule_triage_for_new_incident", new_callable=AsyncMock, return_value={"status": "QUEUED_DIRECT"}) as schedule:
                first = self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers)
                second = self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["status"], "ingested_and_incident_created")
            self.assertTrue(second.json()["duplicate"])
            self.assertEqual(second.json()["incident_id"], first.json()["incident_id"])
            schedule.assert_awaited_once()
            with db.get_connection() as conn:
                row = conn.execute(
                    "SELECT source_system, authority, metadata_json FROM evidence WHERE incident_id=%s",
                    (first.json()["incident_id"],),
                ).fetchone()
                self.assertEqual(row[0], "Automatic Outlook email (unreviewed)")
                self.assertEqual(row[1], "LOW")
                self.assertIn('"operator_reviewed": false', row[2])
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviewed_alert_document").fetchone()[0], 1)

    def test_inbox_retains_ignored_and_rejects_conflicts(self):
        import app.api.main as api
        payload = {"source": "Outlook email", "monitor_name": "Ignored", "message": "Synthetic body"}
        headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "outlook:ignored"}
        with patch.object(api, "ingest_webhook", new_callable=AsyncMock, return_value={"status": "ignored", "reasoning": "noise"}):
            self.assertEqual(self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/v2/ingest/outlook-bridge", json={**payload, "message": "different"}, headers=headers).status_code, 409)
        self.assertEqual(self.client.get("/api/v2/outlook-inbox").status_code, 401)
        viewer = {"Authorization": "Bearer " + token("viewer")}
        self.assertEqual(self.client.get("/api/v2/outlook-inbox", headers=viewer).status_code, 403)
        operator = {"Authorization": "Bearer " + token("operator")}
        result = self.client.get("/api/v2/outlook-inbox", headers=operator)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["items"][0]["state"], "needs_review")
        detail = self.client.get("/api/v2/outlook-inbox/outlook:ignored", headers=operator)
        self.assertEqual(detail.json()["body"], "Synthetic body")
        other = create_access_token({"sub": "other", "roles": ["operator"], "tenant_id": "other", "workspace_id": "default_workspace"})
        self.assertEqual(self.client.get("/api/v2/outlook-inbox/outlook:ignored", headers={"Authorization": "Bearer " + other}).status_code, 404)

    def test_inbox_review_is_audited_and_tenant_scoped(self):
        import app.api.main as api
        payload = {"source": "Outlook email", "monitor_name": "Alert", "message": "Synthetic body"}
        headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "outlook:review"}
        with patch.object(api, "ingest_webhook", new_callable=AsyncMock, return_value={"status": "ignored"}):
            self.assertEqual(self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers).status_code, 200)
        url = "/api/v2/outlook-inbox/outlook:review/reviews"
        review = {"assessment": "alert", "reason": "Requires investigation"}
        viewer = {"Authorization": "Bearer " + token("viewer")}
        operator = {"Authorization": "Bearer " + token("operator")}
        other = create_access_token({"sub": "other", "roles": ["operator"], "tenant_id": "other", "workspace_id": "default_workspace"})
        self.assertEqual(self.client.post(url, json=review, headers=viewer).status_code, 403)
        self.assertEqual(self.client.post(url, json=review, headers={"Authorization": "Bearer " + other}).status_code, 404)
        self.assertEqual(self.client.post(url, json={**review, "reason": " "}, headers=operator).status_code, 400)
        self.assertEqual(self.client.post(url, json=review, headers=operator).status_code, 200)
        item = self.client.get("/api/v2/outlook-inbox/outlook:review", headers=operator).json()
        self.assertEqual(item["result"]["status"], "ignored")
        self.assertEqual(item["reviews"][0]["assessment"], "alert")
        self.assertEqual(item["reviews"][0]["actor_id"], "test")

    def test_promote_ignored_email_is_audited_and_not_duplicated(self):
        import app.api.main as api
        self.db.init_schema()
        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alert(alert_id), document_kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, submitted_by TEXT NOT NULL, created_at TEXT NOT NULL)")
        payload = {"source": "Outlook email", "monitor_name": "Failure", "message": "Synthetic body"}
        headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "outlook:promote"}
        with patch.object(api, "ingest_webhook", new_callable=AsyncMock, return_value={"status": "ignored"}):
            self.assertEqual(self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers).status_code, 200)
        operator = {"Authorization": "Bearer " + token("operator")}
        viewer = {"Authorization": "Bearer " + token("viewer")}
        url = "/api/v2/outlook-inbox/outlook:promote/promote"
        data = {"severity": "high", "reason": "Requires investigation"}
        self.assertEqual(self.client.post(url, json=data, headers=viewer).status_code, 403)
        self.assertEqual(self.client.post(url, json={**data, "severity": "invalid"}, headers=operator).status_code, 400)
        result = self.client.post(url, json=data, headers=operator)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.post(url, json=data, headers=operator).status_code, 409)
        detail = self.client.get("/api/v2/outlook-inbox/outlook:promote", headers=operator).json()
        self.assertEqual(detail["state"], "promoted")
        self.assertEqual(detail["result"]["status"], "ignored")
        self.assertEqual(detail["promotion"]["alert_id"], result.json()["alert_id"])
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviewed_alert_document").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM incident").fetchone()[0], 0)

    def test_promotion_rolls_back_when_document_insert_fails(self):
        import app.api.main as api
        self.db.init_schema()
        payload = {"source": "Outlook email", "monitor_name": "Failure", "message": "Synthetic body"}
        headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "outlook:rollback"}
        with patch.object(api, "ingest_webhook", new_callable=AsyncMock, return_value={"status": "ignored"}):
            self.assertEqual(self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers).status_code, 200)
        operator = {"Authorization": "Bearer " + token("operator")}
        other = create_access_token({"sub": "other", "roles": ["operator"], "tenant_id": "other", "workspace_id": "default_workspace"})
        url = "/api/v2/outlook-inbox/outlook:rollback/promote"
        data = {"severity": "high", "reason": "Requires investigation"}
        self.assertEqual(self.client.post(url, json=data, headers={"Authorization": "Bearer " + other}).status_code, 404)
        with self.assertRaises(Exception):
            self.client.post(url, json=data, headers=operator)
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT state FROM outlook_inbox WHERE delivery_key=%s", ("outlook:rollback",)).fetchone()[0], "needs_review")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM outlook_inbox_promotion").fetchone()[0], 0)

    def test_bridge_heartbeat_authentication_and_scope(self):
        url = "/api/v2/ingest/outlook-bridge-heartbeat"
        counts = {"pending": 2, "blocked": 1, "baseline": 4}
        secret = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret"}
        self.assertEqual(self.client.post(url, json=counts).status_code, 401)
        self.assertEqual(self.client.post(url, json={**counts, "pending": -1}, headers=secret).status_code, 400)
        self.assertEqual(self.client.post(url, json=counts, headers=secret).status_code, 200)
        operator = {"Authorization": "Bearer " + token("operator")}
        report = self.client.get("/api/v2/outlook-bridge-health", headers=operator)
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()["last_report"]["pending"], 2)
        self.assertEqual(report.json()["last_report"]["blocked"], 1)
        other = create_access_token({"sub": "other", "roles": ["operator"], "tenant_id": "other", "workspace_id": "default_workspace"})
        self.assertIsNone(self.client.get("/api/v2/outlook-bridge-health", headers={"Authorization": "Bearer " + other}).json()["last_report"])

    def test_failure_retry_and_late_error_preserve_success(self):
        import app.api.main as api
        from fastapi import HTTPException
        from app.store import alert_inbox
        payload = {"source": "Outlook email", "monitor_name": "Retry", "message": "Synthetic body"}
        headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "outlook:retry"}
        with patch.object(api, "ingest_webhook", new_callable=AsyncMock, side_effect=HTTPException(status_code=500)):
            self.assertEqual(self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers).status_code, 500)
        with patch.object(api, "ingest_webhook", new_callable=AsyncMock, return_value={"status": "ingested", "alert_id": "alert-1"}):
            self.assertEqual(self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers).status_code, 200)
        alert_inbox.record_result(self.db, "outlook:retry", {"status": "error"})
        operator = {"Authorization": "Bearer " + token("operator")}
        items = self.client.get("/api/v2/outlook-inbox", headers=operator).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["state"], "ingested")
        self.assertEqual(items[0]["result"]["alert_id"], "alert-1")

    def test_operator_retry_processes_original_delivery_once(self):
        import app.api.main as api
        from app.domain import orchestrator as domain
        from app.domain import triage_scheduler

        self.db.init_schema()
        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE webhook_delivery (delivery_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL)")
            conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alert(alert_id), document_kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, submitted_by TEXT NOT NULL, created_at TEXT NOT NULL)")

        class Watcher:
            calls = 0

            async def analyze(self, payload, active_incidents):
                Watcher.calls += 1
                if Watcher.calls == 1:
                    return {"error": "model unavailable"}
                return {"is_valid": True, "normalized_alert": {
                    "severity": "high", "alert_type": "TEST",
                    "source_system": "Outlook", "message": "Reported failure",
                }}

        payload = {"source": "Outlook email", "monitor_name": "Retry", "message": "Synthetic body", "type": "Email alert"}
        headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "outlook:operator-retry"}
        operator = {"Authorization": "Bearer " + token("operator")}
        viewer = {"Authorization": "Bearer " + token("viewer")}
        url = "/api/v2/outlook-inbox/outlook:operator-retry/retry"
        with patch.object(domain, "get_database", return_value=self.db), \
             patch.object(domain, "WatcherAgent", Watcher), \
             patch.object(triage_scheduler, "schedule_triage_for_new_incident", new_callable=AsyncMock, return_value={"status": "QUEUED_DIRECT"}):
            self.assertEqual(self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers).status_code, 500)
            self.assertEqual(self.client.post(url, headers=viewer).status_code, 403)
            response = self.client.post(url, headers=operator)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ingested_and_incident_created")
            self.assertEqual(self.client.post(url, headers=operator).status_code, 409)
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM incident").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT actor_id FROM outlook_inbox_retry").fetchone()[0], "test")

    def test_inbox_retains_classification_failure(self):
        import app.api.main as api
        from fastapi import HTTPException
        payload = {"source": "Outlook email", "monitor_name": "Failure", "message": "Synthetic body"}
        headers = {"X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "outlook:failure"}
        with patch.object(api, "ingest_webhook", new_callable=AsyncMock, side_effect=HTTPException(status_code=500, detail="failed")):
            self.assertEqual(self.client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers).status_code, 500)
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT state, body FROM outlook_inbox WHERE delivery_key=%s", ("outlook:failure",)).fetchone()
        self.assertEqual(tuple(row), ("failed", "Synthetic body"))

    def test_replay_rejects_unsupported_and_oversized_attachments(self):
        url = "/api/v2/ingest/email-replay"
        headers = {"Authorization": "Bearer " + token("operator"), "X-NemoGuard-Webhook-Secret": "test-webhook-secret", "Idempotency-Key": "test"}
        payload = {"monitor_name": "failure", "message": "body", "attachments": [{"name": "bad.exe", "text": "content"}]}
        self.assertEqual(self.client.post(url, json=payload, headers=headers).status_code, 400)
        payload["attachments"] = [{"name": "huge.log", "text": "x" * 65537}]
        self.assertEqual(self.client.post(url, json=payload, headers=headers).status_code, 400)
        payload["attachments"] = [{"name": "ok.log", "text": "x"}] * 4
        self.assertEqual(self.client.post(url, json=payload, headers=headers).status_code, 400)

    def test_preview_does_not_ingest(self):
        parser = types.SimpleNamespace(Message=FakeMessage)
        with patch.dict(sys.modules, {"extract_msg": parser}):
            response = self.post(MAGIC, "operator", "test-webhook-secret")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["subject"], "Example subject")
        self.assertEqual(response.json()["body"], "Example body")
        self.assertEqual(response.json()["attachments"][0]["text"], "ERROR 1023")
        self.assertIsNone(response.json()["attachments"][1]["text"])
        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
