"""Workspace model selection tests without external inference calls."""
import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain import model_settings
from app.domain.agents.base_agent import BaseAgent
from app.store.sqlite_database import SqliteDatabase


class ModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.temp.name) / "test.db"))
        self.db.init_schema()
        model_settings.initialize(self.db)
        with self.db.get_connection() as conn:
            for incident, workspace in (("one", "ws-a"), ("two", "ws-b"), ("unscoped", None)):
                conn.execute(
                    "INSERT INTO incident (incident_id,tenant_id,workspace_id,title,status,severity,detected_at,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (incident, "tenant-a", workspace, "Test", "PLAN_READY", "high", "now", "now", "now"),
                )

    def tearDown(self):
        self.temp.cleanup()

    async def test_agent_call_uses_scoped_override_and_other_workspace_keeps_default(self):
        model_settings.set_override(self.db, "tenant-a", "ws-a", "rca", "provider/model-a", "admin")
        create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
        ))
        with patch.dict("os.environ", {"AXET_MODEL": "default-model"}):
            agent = BaseAgent("RCA_Agent", "test")
        agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        model_settings.apply_override(agent, "rca", model_settings.for_incident(self.db, "one"))
        self.assertEqual(await agent.call_llm_json("test"), {"ok": True})
        self.assertEqual(create.await_args.kwargs["model"], "provider/model-a")

        with patch.dict("os.environ", {"AXET_MODEL": "default-model"}):
            other = BaseAgent("RCA_Agent", "test")
        other.client = agent.client
        model_settings.apply_override(other, "rca", model_settings.for_incident(self.db, "two"))
        await other.call_llm_json("test")
        self.assertEqual(create.await_args.kwargs["model"], "default-model")
        self.assertEqual(model_settings.for_incident(self.db, "unscoped"), {})

    async def test_commander_reuse_does_not_leak_model_between_workspaces(self):
        from app.domain.commander import conversation

        class FakeAgent:
            def __init__(self):
                self.model = "default-model"

            async def ask(self, incident_id, context, question, history=None):
                return self.model

        model_settings.set_override(self.db, "tenant-a", "ws-a", "commander", "workspace-model", "admin")
        with patch.object(conversation, "CommanderLiveAgent", FakeAgent):
            service = conversation.CommanderConversation(db=self.db)
            first = await service.ask("one", "tenant-a", "Question?", "operator")
            second = await service.ask("two", "tenant-a", "Question?", "operator")
        self.assertEqual(first["answer"], "workspace-model")
        self.assertEqual(second["answer"], "default-model")
        self.assertEqual(service.agent.model, "default-model")

    async def test_concurrent_updates_have_consistent_audit_chain(self):
        barrier = threading.Barrier(2)

        def update(value):
            barrier.wait(timeout=5)
            model_settings.set_override(self.db, "tenant-a", "ws-a", "rca", value, "admin")

        def run_updates():
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(update, value) for value in ("model-a", "model-b")]
                for future in futures:
                    future.result(timeout=15)

        import asyncio
        await asyncio.to_thread(run_updates)
        with self.db.get_connection() as conn:
            rows = [tuple(row) for row in conn.execute(
                "SELECT old_model,new_model FROM workspace_model_audit "
                "WHERE tenant_id=%s AND workspace_id=%s AND operation=%s",
                ("tenant-a", "ws-a", "rca"),
            ).fetchall()]
        self.assertEqual(len(rows), 2)
        initial = next(row for row in rows if row[0] is None)
        subsequent = next(row for row in rows if row[0] is not None)
        self.assertEqual(initial[1], subsequent[0])
        self.assertEqual(model_settings.get_overrides(self.db, "tenant-a", "ws-a")["rca"], subsequent[1])

    async def test_investigation_snapshot_maps_each_agent(self):
        agents = SimpleNamespace(
            rca_agent=SimpleNamespace(model="default"),
            dep_agent=SimpleNamespace(model="default"),
            runbook_agent=SimpleNamespace(model="default"),
            critic=SimpleNamespace(model="default"),
        )
        model_settings.apply_investigation(agents, {
            "rca": "r", "dependency": "d", "runbook": "b", "critic": "c",
        })
        self.assertEqual(
            [agents.rca_agent.model, agents.dep_agent.model, agents.runbook_agent.model, agents.critic.model],
            ["r", "d", "b", "c"],
        )


if __name__ == "__main__":
    unittest.main()
