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
