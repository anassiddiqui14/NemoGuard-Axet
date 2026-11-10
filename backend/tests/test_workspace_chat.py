"""Workspace chat authorization, isolation, and provider behavior."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["JWT_SECRET"] = "isolated-workspace-chat-test-secret"
os.environ["AXET_FLOWS_AUTH"] = "false"

from fastapi.testclient import TestClient
from app.api.auth import create_access_token
from app.api.main import app
from app.domain import model_settings
from app.store import alert_inbox
from datetime import datetime, timezone
from app.store.sqlite_database import SqliteDatabase


def headers(workspace="ws-a", tenant="tenant-a"):
    token = create_access_token({
        "sub": "user-a", "roles": ["viewer"], "tenant_id": tenant, "workspace_id": workspace,
    })
    return {"Authorization": "Bearer " + token}


class WorkspaceChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.tmp.name) / "test.db"))
        self.db.init_schema()
        model_settings.initialize(self.db)
        with self.db.get_connection() as conn:
            for incident, tenant, workspace in (
                ("allowed", "tenant-a", "ws-a"),
                ("other-workspace", "tenant-a", "ws-b"),
                ("other-tenant", "tenant-b", "ws-a"),
                ("unassigned", "tenant-a", None),
            ):
                conn.execute(
                    "INSERT INTO incident (incident_id,tenant_id,workspace_id,title,status,severity,detected_at,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (incident, tenant, workspace, incident, "PLAN_READY", "high", "now", "now", "now"),
                )
        self.db_patch = patch("app.api.workspace_chat.get_database", return_value=self.db)
        self.db_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_authentication_and_validation(self):
        self.assertIn(self.client.post("/api/v2/workspace-chat", json={"question": "Hello"}).status_code, (401, 403))
        self.assertEqual(self.client.post("/api/v2/workspace-chat", headers=headers(), json={"question": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/api/v2/workspace-chat", headers=headers(), json={"question": "x" * 2001}).status_code, 422)
        self.assertEqual(self.client.post("/api/v2/workspace-chat", headers=headers(), json={"question": "Hi", "history": [{"role": "system", "content": "x"}]}).status_code, 422)

    def test_scoped_context_and_model_are_used_without_tools(self):
        model_settings.set_override(self.db, "tenant-a", "ws-a", "commander", "workspace-model", "admin")
        create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="A helpful answer."))]
        ))
        close = AsyncMock()

        class FakeAgent:
            def __init__(self, *_):
                self.model = "default-model"
                self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=close)

        with patch("app.domain.agents.base_agent.BaseAgent", FakeAgent):
            response = self.client.post(
                "/api/v2/workspace-chat", headers=headers(),
                json={"question": "What needs attention?", "history": [{"role": "user", "content": "Hello"}]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], "A helpful answer.")
        self.assertEqual(response.headers["cache-control"], "no-store")
        close.assert_awaited_once()
        args = create.await_args.kwargs
        self.assertEqual(args["model"], "workspace-model")
        self.assertNotIn("tools", args)
        context = args["messages"][1]["content"]
        self.assertIn("allowed", context)
        self.assertNotIn("other-workspace", context)
        self.assertNotIn("other-tenant", context)
        self.assertNotIn("unassigned", context)
        self.assertEqual(args["messages"][-1]["content"], "What needs attention?")

    def test_today_count_is_scoped_and_requires_inbox_permission(self):
        alert_inbox.initialize(self.db)
        today = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            for key, tenant, workspace, subject in (
                ("matching", "tenant-a", "ws-a", "Transcript error"),
                ("different", "tenant-a", "ws-a", "Other job"),
                ("other", "tenant-a", "ws-b", "Transcript error"),
                ("foreign", "tenant-b", "ws-a", "Transcript error"),
            ):
                conn.execute(
                    "INSERT INTO outlook_inbox (delivery_key,payload_hash,tenant_id,workspace_id,subject,body,state,received_at,updated_at,result_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (key, "hash", tenant, workspace, subject, "", "received", today, today, "{}"),
                )
        prompt = {"question": "how many transcript related errors I have received today?", "timezone": "UTC"}
        self.assertEqual(self.client.post("/api/v2/workspace-chat", headers=headers(), json=prompt).status_code, 403)
        token = create_access_token({"sub": "operator", "roles": ["operator"], "tenant_id": "tenant-a", "workspace_id": "ws-a"})
        response = self.client.post("/api/v2/workspace-chat", headers={"Authorization": "Bearer " + token}, json=prompt)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["answer"].startswith("1 Outlook bridge message recorded"))
        self.assertIn("not verified errors", response.json()["answer"])

    def test_today_count_uses_local_day_across_daylight_saving(self):
        alert_inbox.initialize(self.db)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 3, 8, 16, 0, tzinfo=timezone.utc).astimezone(tz)

        with self.db.get_connection() as conn:
            for key, received in (
                ("before", "2026-03-08T04:59:59+00:00"),
                ("start", "2026-03-08T05:00:00+00:00"),
                ("last", "2026-03-09T03:59:59+00:00"),
                ("after", "2026-03-09T04:00:00+00:00"),
            ):
                conn.execute(
                    "INSERT INTO outlook_inbox (delivery_key,payload_hash,tenant_id,workspace_id,subject,body,state,received_at,updated_at,result_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (key, "hash", "tenant-a", "ws-a", "transcript error", "", "received", received, received, "{}"),
                )
        token = create_access_token({"sub": "operator", "roles": ["operator"], "tenant_id": "tenant-a", "workspace_id": "ws-a"})
        auth = {"Authorization": "Bearer " + token}
        with patch("app.api.workspace_chat.datetime", FixedDatetime):
            response = self.client.post(
                "/api/v2/workspace-chat", headers=auth,
                json={"question": "how many transcript related errors today?", "timezone": "America/New_York"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["answer"].startswith("2 Outlook bridge messages"))
        self.assertIn("2026-03-08", response.json()["answer"])
        bad = self.client.post(
            "/api/v2/workspace-chat", headers=auth,
            json={"question": "how many transcript related errors today?", "timezone": "Not/AZone"},
        )
        self.assertEqual(bad.status_code, 400)

    def test_provider_errors_do_not_leak_details(self):
        model_settings.set_override(self.db, "tenant-a", "ws-a", "commander", "workspace-model", "admin")

        class BrokenAgent:
            def __init__(self, *_):
                raise RuntimeError("sensitive-provider-details")

        with patch("app.domain.agents.base_agent.BaseAgent", BrokenAgent):
            response = self.client.post("/api/v2/workspace-chat", headers=headers(), json={"question": "Hello"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sensitive-provider-details", response.text)


if __name__ == "__main__":
    unittest.main()
