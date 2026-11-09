"""API authorization and isolation tests for incident knowledge."""
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["JWT_SECRET"] = "isolated-knowledge-test-secret"
os.environ["AXET_FLOWS_AUTH"] = "false"

from fastapi.testclient import TestClient
from app.api.auth import create_access_token
from app.api.main import app
from app.domain import incident_knowledge
from app.store.sqlite_database import SqliteDatabase


def headers(role, workspace="ws-a", tenant="tenant-a"):
    token = create_access_token({
        "sub": "test-user",
        "roles": [role],
        "tenant_id": tenant,
        "workspace_id": workspace,
    })
    return {"Authorization": "Bearer " + token}


class KnowledgeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.temp.name) / "test.db"))
        self.db.init_schema()
        incident_knowledge.initialize(self.db)
        with self.db.get_connection() as conn:
            for incident, workspace in (("first", "ws-a"), ("second", "ws-a"), ("other", "ws-b")):
                conn.execute(
                    "INSERT INTO incident (incident_id,tenant_id,workspace_id,primary_job_id,title,status,severity,detected_at,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (incident, "tenant-a", workspace, "job-1", "Test", "PLAN_READY", "high", "now", "now", "now"),
                )
        self.db_patch = patch("app.api.incident_knowledge.get_database", return_value=self.db)
        self.db_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_submission_requires_operator_and_review_requires_admin(self):
        payload = {
            "kind": "SOP", "job_key": "job-1", "title": "Recovery",
            "filename": "sop.txt", "file_base64": base64.b64encode(b"Inspect logs.").decode(),
        }
        self.assertEqual(self.client.post("/api/v2/incidents/first/knowledge", headers=headers("viewer"), json=payload).status_code, 403)
        submitted = self.client.post("/api/v2/incidents/first/knowledge", headers=headers("operator"), json=payload)
        self.assertEqual(submitted.status_code, 201, submitted.text)
        identifier = submitted.json()["knowledge_id"]
        path = f"/api/v2/admin/incident-knowledge/{identifier}/review"
        self.assertEqual(self.client.post(path, headers=headers("operator"), json={"decision": "APPROVED"}).status_code, 403)
        self.assertEqual(self.client.post(path, headers=headers("admin", "ws-b"), json={"decision": "APPROVED"}).status_code, 404)
        self.assertEqual(self.client.get("/api/v2/incidents/second/knowledge", headers=headers("operator")).json()["items"], [])
        self.assertEqual(self.client.post(path, headers=headers("admin"), json={"decision": "APPROVED"}).status_code, 200)
        other_context = self.client.get("/api/v2/incidents/second/knowledge", headers=headers("operator"))
        self.assertEqual(other_context.json()["items"][0]["knowledge_id"], identifier)
        self.assertEqual(self.client.get("/api/v2/incidents/other/knowledge", headers=headers("operator")).status_code, 404)
        self.assertEqual(self.client.get("/api/v2/incidents/other/knowledge", headers=headers("operator", "ws-b")).json()["items"], [])
        self.assertEqual(self.client.post(path, headers=headers("admin"), json={"decision": "REJECTED"}).status_code, 404)

    def test_commander_can_submit_but_cannot_approve(self):
        payload = {"kind": "DEPENDENCY", "job_key": "job-1", "title": "Downstream",
                   "content": "Job B consumes the output of job A."}
        response = self.client.post("/api/v2/incidents/first/knowledge",
                                    headers=headers("commander"), json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        review = self.client.post(
            f"/api/v2/admin/incident-knowledge/{response.json()['knowledge_id']}/review",
            headers=headers("commander"), json={"decision": "APPROVED"},
        )
        self.assertEqual(review.status_code, 403)

    def test_reinvestigation_requires_role_and_workspace(self):
        from app.domain.commander import conversation
        calls = []

        class FakeConversation:
            async def guide(self, *args, **kwargs):
                calls.append((args, kwargs))
                return {"reinvestigation_triggered": True}

        url = "/api/v2/incidents/first/knowledge/reinvestigate"
        with patch.object(conversation, "CommanderConversation", FakeConversation):
            self.assertEqual(self.client.post(url, headers=headers("viewer")).status_code, 403)
            self.assertEqual(self.client.post(url, headers=headers("operator", "ws-b")).status_code, 404)
            response = self.client.post(url, headers=headers("commander"))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["reinvestigation_triggered"])
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1]["is_fact"])
        self.assertEqual(calls[0][0][0], "first")

    def test_workspace_assignment_is_admin_only_audited_and_cannot_reassign(self):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET workspace_id=NULL WHERE incident_id=%s", ("first",))
        get_url = "/api/v2/incidents/first/knowledge"
        assign_url = "/api/v2/admin/incidents/first/assign-workspace"
        self.assertEqual(self.client.get(get_url, headers=headers("operator")).status_code, 409)
        self.assertEqual(self.client.post(assign_url, headers=headers("operator"), json={"reason": "Ownership confirmed"}).status_code, 403)
        self.assertEqual(self.client.post(assign_url, headers=headers("admin", tenant="other-tenant"), json={"reason": "Ownership confirmed"}).status_code, 409)
        self.assertEqual(self.client.post(assign_url, headers=headers("admin"), json={"reason": "  "}).status_code, 400)
        response = self.client.post(assign_url, headers=headers("admin"), json={"reason": "Ownership confirmed"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(get_url, headers=headers("operator")).status_code, 200)
        self.assertEqual(self.client.post(assign_url, headers=headers("admin", "ws-b"), json={"reason": "Change owner"}).status_code, 409)
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT workspace_id FROM incident WHERE incident_id=%s", ("first",)).fetchone()
            event = conn.execute("SELECT actor_id,event_type,event_summary FROM audit_event WHERE incident_id=%s AND event_type=%s", ("first", "WORKSPACE_ASSIGNED")).fetchone()
        self.assertEqual(row[0], "ws-a")
        self.assertIsNotNone(event)
        self.assertIn("Ownership confirmed", event[2])

    def test_invalid_upload_rejected_without_creating_submission(self):
        response = self.client.post(
            "/api/v2/incidents/first/knowledge",
            headers=headers("operator"),
            json={"kind": "SOP", "job_key": "job-1", "title": "Broken", "filename": "bad.pdf", "file_base64": "bm90IHBkZg=="},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(incident_knowledge.list_items(self.db, "tenant-a", "ws-a"), [])


if __name__ == "__main__":
    unittest.main()
