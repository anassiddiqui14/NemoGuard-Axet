"""Isolated tests for reviewed email evidence ingestion."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.store.sqlite_database import SqliteDatabase
from app.domain import orchestrator as module
from app.domain.agents import agent_tools


class FakeWatcher:
    result = {}

    async def analyze(self, payload, active_incidents):
        return self.result


class ReviewedIngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.tmp.name) / "test.db"))
        self.db.init_schema()
        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE webhook_delivery (delivery_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL)")
            conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alert(alert_id), document_kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, submitted_by TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.patches = [
            patch.object(module, "get_database", return_value=self.db),
            patch.object(agent_tools, "get_database", return_value=self.db),
            patch.object(module, "WatcherAgent", FakeWatcher),
        ]
        for item in self.patches:
            item.start()
        self.obj = module.IncidentOrchestrator()
        FakeWatcher.result = {
            "is_valid": True,
            "normalized_alert": {"severity": "high", "alert_type": "TEST", "source_system": "Email", "message": "Synthetic failure"},
        }
        self.documents = [
            {"kind": "EMAIL_BODY", "title": "Test email", "content": "A batch failed."},
            {"kind": "EMAIL_ATTACHMENT", "title": "session.log", "content": "started\nERROR 1023 serialization violation\nstopped"},
        ]

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def ingest(self, key="event-1", digest="hash", docs=None):
        return asyncio.run(self.obj.process_webhook(
            {"message": "Synthetic failure"}, key, digest,
            reviewed_documents=self.documents if docs is None else docs,
            submitted_by="operator-1",
        ))

    def test_new_incident_retry_and_evidence(self):
        result = self.ingest()
        self.assertEqual(result["status"], "ingested_and_incident_created")
        retry = self.ingest()
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["alert_id"], result["alert_id"])
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM reviewed_alert_document").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM evidence WHERE incident_id=%s AND authority=%s", (result["incident_id"], "LOW")).fetchone()[0], 2)
        evidence = json.loads(agent_tools.query_reviewed_email_evidence(result["incident_id"], "1023"))
        self.assertIn("unverified", evidence["source"])
        self.assertEqual(evidence["matches"][0]["line"], 2)
        self.assertIn("1023", evidence["matches"][0]["text"])
        self.assertEqual(self.ingest(digest="changed")["status"], "idempotency_conflict")

    def test_outlook_scope_is_persisted_and_blocks_cross_workspace_correlation(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            for ident, workspace in (("other", "workspace-b"), ("unassigned", None), ("same", "workspace-a")):
                conn.execute(
                    "INSERT INTO incident (incident_id,tenant_id,workspace_id,title,status,severity,detected_at,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (ident, "tenant-a", workspace, "Existing", "PLAN_READY", "high", now, now, now),
                )
        seen = []

        class ScopedWatcher:
            async def analyze(self, payload, active_incidents):
                seen.extend(item["incident_id"] for item in active_incidents)
                return {
                    "is_valid": True,
                    "correlated_incident_id": "other",
                    "normalized_alert": {
                        "severity": "high", "alert_type": "TEST",
                        "source_system": "Email", "message": "Synthetic failure",
                    },
                }

        with patch.object(module, "WatcherAgent", ScopedWatcher):
            result = asyncio.run(self.obj.process_webhook(
                {"message": "Synthetic failure"}, "scoped", "digest",
                reviewed_documents=self.documents, submitted_by="automatic-outlook-bridge",
                trusted_scope=("tenant-a", "workspace-a"),
            ))
        self.assertEqual(seen, ["same"])
        self.assertEqual(result["status"], "ingested_and_incident_created")
        with self.db.get_connection() as conn:
            incident = conn.execute(
                "SELECT tenant_id,workspace_id FROM incident WHERE incident_id=%s",
                (result["incident_id"],),
            ).fetchone()
            alert = conn.execute(
                "SELECT tenant_id,workspace_id FROM alert WHERE alert_id=%s",
                (result["alert_id"],),
            ).fetchone()
        self.assertEqual(tuple(incident), ("tenant-a", "workspace-a"))
        self.assertEqual(tuple(alert), ("tenant-a", "workspace-a"))

    def test_correlated_incident_gets_evidence_without_new_plan(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO incident (incident_id,title,status,severity,detected_at,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", ("existing", "Existing", "PLAN_READY", "SEV_2", now, now, now))
        FakeWatcher.result["correlated_incident_id"] = "existing"
        result = self.ingest()
        self.assertEqual(result["status"], "ingested_and_correlated")
        self.assertEqual(result["incident_id"], "existing")
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM evidence WHERE incident_id=%s", ("existing",)).fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM action_plan WHERE incident_id=%s", ("existing",)).fetchone()[0], 0)

    def test_automatic_email_is_unreviewed_and_plan_requires_review(self):
        result = asyncio.run(self.obj.process_webhook(
            {"source": "Outlook email", "message": "Synthetic failure"},
            "outlook:test", "hash",
            reviewed_documents=[{"kind": "EMAIL_BODY", "title": "Alert", "content": "Reported failure"}],
            submitted_by="automatic-outlook-bridge",
        ))
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT source_system, authority, metadata_json FROM evidence WHERE incident_id=%s",
                (result["incident_id"],),
            ).fetchone()
        self.assertEqual(row[0], "Automatic Outlook email (unreviewed)")
        self.assertEqual(row[1], "LOW")
        self.assertFalse(json.loads(row[2])["operator_reviewed"])
        evidence = json.loads(agent_tools.query_reviewed_email_evidence(result["incident_id"]))
        self.assertFalse(evidence["matches"][0]["operator_reviewed"])
        plan = self.obj.save_agent_findings(
            result["incident_id"],
            {"hypotheses": [{"statement": "Possible source data issue", "confidence": 0.8}],
             "evidence": [], "impacts": [], "action_plan": {"steps": []}},
            critic_status="PASSED",
        )
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM action_plan WHERE action_plan_id=%s",
                (plan["plan_id"],),
            ).fetchone()[0], "NEEDS_REVIEW")
            self.assertEqual(conn.execute(
                "SELECT status FROM hypothesis WHERE incident_id=%s",
                (result["incident_id"],),
            ).fetchone()[0], "UNVERIFIED")
            incident = conn.execute(
                "SELECT actual_root_cause, rca_confidence FROM incident WHERE incident_id=%s",
                (result["incident_id"],),
            ).fetchone()
            self.assertIsNone(incident[0])
            self.assertIsNone(incident[1])

    def test_unsafe_fallback_does_not_create_plan_or_hypothesis(self):
        result = self.ingest()
        outcome = self.obj.triage_fallback(result["incident_id"])
        self.assertIn("error", outcome)
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM action_plan").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM hypothesis").fetchone()[0], 0)
            row = conn.execute(
                "SELECT actual_root_cause, rca_confidence FROM incident WHERE incident_id=%s",
                (result["incident_id"],),
            ).fetchone()
            self.assertIsNone(row[0])
            self.assertIsNone(row[1])

    def test_primary_investigation_failure_does_not_create_fallback_plan(self):
        from app.domain.agents import langgraph_investigator

        class FailingInvestigator:
            async def investigate(self, *args, **kwargs):
                raise RuntimeError("RCA could not complete")

        result = self.ingest()
        incident_id = result["incident_id"]
        with patch.object(langgraph_investigator, "LangGraphInvestigator", FailingInvestigator):
            triage = self.obj.triage_incident(incident_id)
        self.assertFalse(triage["saved_plan"])
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM action_plan WHERE incident_id=%s", (incident_id,)
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM hypothesis WHERE incident_id=%s", (incident_id,)
            ).fetchone()[0], 0)

    def test_rca_failure_logs_safe_category_and_stops_investigation(self):
        from app.domain.agents.langgraph_investigator import LangGraphInvestigator

        class FailingRCA:
            async def analyze(self, *args, **kwargs):
                return {"error": "LLM response was not valid JSON; sensitive synthetic content"}

        class UnusedAgent:
            async def analyze(self, *args, **kwargs):
                raise AssertionError("Downstream agents must not run after RCA failure")

        investigator = LangGraphInvestigator.__new__(LangGraphInvestigator)
        investigator.rca_agent = FailingRCA()
        investigator.dep_agent = UnusedAgent()
        investigator.runbook_agent = UnusedAgent()
        investigator.critic = UnusedAgent()
        investigator.graph = investigator._build_graph()
        with patch("builtins.print") as output:
            with self.assertRaisesRegex(RuntimeError, "RCA could not complete"):
                asyncio.run(investigator.investigate("synthetic"))
        logged = " ".join(str(call) for call in output.call_args_list)
        self.assertIn("invalid_model_json", logged)
        self.assertNotIn("sensitive synthetic content", logged)

    def test_substituted_plan_never_inherits_passed_safety_verdict(self):
        from app.domain.agents.langgraph_investigator import LangGraphInvestigator

        class Agent:
            def __init__(self, result):
                self.result = result

            async def analyze(self, *args, **kwargs):
                return self.result

        investigator = LangGraphInvestigator.__new__(LangGraphInvestigator)
        investigator.rca_agent = Agent({"finding": "Unverified hypothesis"})
        investigator.dep_agent = Agent({"impacts": []})
        investigator.runbook_agent = Agent({"steps": [{"action": "Manual review", "tool": "manual"}]})
        investigator.critic = Agent({"passed": True, "final_plan": {"steps": []}})
        investigator.graph = investigator._build_graph()
        state = asyncio.run(investigator.investigate("synthetic"))
        self.assertEqual(state["critic_status"], "INCONCLUSIVE")
        self.assertFalse(state["critic_passed"])
        self.assertEqual(state["final_plan"]["steps"][0]["action"], "Manual review")

    def test_reference_context_reaches_all_investigation_agents(self):
        from app.domain.agents.langgraph_investigator import LangGraphInvestigator

        calls = {}

        class Agent:
            def __init__(self, name, result):
                self.name = name
                self.result = result

            async def analyze(self, *args, **kwargs):
                calls[self.name] = kwargs
                return self.result

        investigator = LangGraphInvestigator.__new__(LangGraphInvestigator)
        investigator.rca_agent = Agent("rca", {"finding": "Possible failure"})
        investigator.dep_agent = Agent("dependency", {"impacts": []})
        investigator.runbook_agent = Agent("runbook", {"steps": []})
        investigator.critic = Agent("critic", {
            "passed": True,
            "final_plan": {"steps": [{"action": "Review", "tool": "manual", "risk": "LOW"}]},
        })
        investigator.graph = investigator._build_graph()
        asyncio.run(investigator.investigate(
            "synthetic", feedback_context="Operator guidance",
            reference_context="Untrusted reference KN-123",
        ))
        for name in ("rca", "dependency", "runbook", "critic"):
            self.assertEqual(calls[name]["reference_context"], "Untrusted reference KN-123")
        self.assertEqual(calls["rca"]["feedback_context"], "Operator guidance")
        self.assertEqual(calls["critic"]["feedback_context"], "Operator guidance")

    def test_read_only_dispatcher_rejects_write_tools(self):
        with patch.object(agent_tools, "execute_tool_call") as execute:
            result = asyncio.run(agent_tools.execute_read_only_tool_call(
                "cleanup_partial_write", '{"dry_run": false}'
            ))
        self.assertIn("not permitted", result)
        execute.assert_not_called()

    def test_explicit_reinvestigation_wording(self):
        from app.domain.commander.conversation import _is_materially_state_changing
        self.assertTrue(_is_materially_state_changing("start re investigation"))
        self.assertTrue(_is_materially_state_changing("please re-investigate"))
        self.assertFalse(_is_materially_state_changing("what is the current status?"))

    def test_reinvestigation_receives_prior_guidance_and_restores_on_failure(self):
        from app.domain.agents import langgraph_investigator

        result = self.ingest()
        incident_id = result["incident_id"]
        self.obj.save_agent_findings(
            incident_id,
            {"hypotheses": [], "evidence": [], "impacts": [],
             "action_plan": {"steps": []}},
            critic_status="INCONCLUSIVE",
        )
        with self.db.get_connection() as conn:
            for revision, text in ((1, "Possible column length issue"), (2, "start re investigation")):
                conn.execute(
                    "INSERT INTO commander_guidance (guidance_id, incident_id, created_at, "
                    "source_channel, guidance_type, authority, text, guidance_revision, triggered_reinvestigation) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (f"g{revision}", incident_id, "now", "UI", "INVESTIGATION_DIRECTION",
                     "HUMAN_OBSERVATION", text, revision, 0),
                )
        captured = {}

        class FailingInvestigator:
            async def investigate(self, *args, **kwargs):
                captured["context"] = kwargs["feedback_context"]
                raise RuntimeError("synthetic failure")

        with patch.object(langgraph_investigator, "LangGraphInvestigator", FailingInvestigator):
            outcome = asyncio.run(self.obj.reinvestigate_from_commander_guidance(
                incident_id, "start re investigation", False, "g2"
            ))
        self.assertIn("error", outcome)
        self.assertIn("Possible column length issue", captured["context"])
        self.assertIn("start re investigation", captured["context"])
        self.assertIn("HUMAN_OBSERVATION", captured["context"])
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM incident WHERE incident_id=%s", (incident_id,)
            ).fetchone()[0], "PLAN_READY")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM action_plan WHERE incident_id=%s", (incident_id,)
            ).fetchone()[0], 1)

    def test_reinvestigation_restores_status_when_investigator_initialization_fails(self):
        from app.domain.agents import langgraph_investigator

        incident_id = self.ingest()["incident_id"]
        self.obj.save_agent_findings(
            incident_id,
            {"hypotheses": [], "evidence": [], "impacts": [], "action_plan": {"steps": []}},
            critic_status="INCONCLUSIVE",
        )
        with patch.object(langgraph_investigator, "LangGraphInvestigator", side_effect=RuntimeError("initialization failed")):
            result = asyncio.run(self.obj.reinvestigate_from_commander_guidance(
                incident_id, "re-investigate", False, "test-guidance"
            ))
        self.assertIn("error", result)
        with self.db.get_connection() as conn:
            status = conn.execute("SELECT status FROM incident WHERE incident_id=%s", (incident_id,)).fetchone()[0]
        self.assertEqual(status, "PLAN_READY")

    def test_email_recovery_does_not_auto_resolve(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id,title,status,severity,detected_at,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                ("existing", "Existing", "PLAN_READY", "SEV_2", now, now, now),
            )
        FakeWatcher.result["correlated_incident_id"] = "existing"
        FakeWatcher.result["is_recovery_signal"] = True
        with patch.dict("os.environ", {"NEMOGUARD_WEBHOOK_AUTO_RESOLVE": "true"}):
            result = self.ingest()
        self.assertEqual(result["status"], "ingested_and_correlated")
        with self.db.get_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT status FROM incident WHERE incident_id=%s", ("existing",)).fetchone()[0],
                "PLAN_READY",
            )

    def test_document_failure_rolls_back_alert_and_receipt(self):
        with self.assertRaises(KeyError):
            self.ingest(docs=[{"kind": "EMAIL_ATTACHMENT"}])
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM alert").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM webhook_delivery").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM reviewed_alert_document").fetchone()[0], 0)

    def test_submitted_sop_reaches_investigation_and_requires_review(self):
        from app.domain import incident_knowledge
        from app.domain.agents import langgraph_investigator

        result = self.ingest(docs=[])
        incident_id = result["incident_id"]
        incident_knowledge.initialize(self.db)
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE incident SET tenant_id=%s, workspace_id=%s, primary_job_id=%s, status='DETECTED' WHERE incident_id=%s",
                ("tenant-a", "workspace-a", "job-a", incident_id),
            )
        knowledge_id = incident_knowledge.submit(
            self.db, "tenant-a", "workspace-a", incident_id, "job-a",
            "SOP", "Recovery procedure", "Check the queue before restarting.", "operator",
        )
        captured = {}

        class Investigator:
            async def investigate(self, *args, **kwargs):
                captured["reference_context"] = kwargs["reference_context"]
                return {
                    "rca_result": {"finding": "Possible queue issue", "confidence": 0.1},
                    "impact_result": {"impacts": []},
                    "runbook_result": {"steps": []},
                    "critic_status": "PASSED",
                    "final_plan": {
                        "rationale": "Review queue",
                        "expected_outcome": "Queue inspected",
                        "steps": [{"action": "Check queue", "tool": "manual", "risk": "LOW"}],
                    },
                }

        with patch.object(langgraph_investigator, "LangGraphInvestigator", Investigator):
            triage = self.obj.triage_incident(incident_id)
        self.assertTrue(triage["saved_plan"])
        self.assertIn(knowledge_id, captured["reference_context"])
        self.assertIn("Check the queue before restarting.", captured["reference_context"])
        self.assertIn("Pending items are operator-submitted", captured["reference_context"])
        with self.db.get_connection() as conn:
            plan = conn.execute(
                "SELECT status,rationale FROM action_plan WHERE incident_id=%s ORDER BY created_at DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
        self.assertEqual(plan[0], "NEEDS_REVIEW")
        self.assertIn("REFERENCE REVIEW REQUIRED", plan[1])

    def test_commander_reinvestigation_uses_pending_sop(self):
        from unittest.mock import AsyncMock
        from app.domain import incident_knowledge
        from app.domain.agents import langgraph_investigator
        from app.domain.commander import conversation

        incident_id = self.ingest(docs=[])["incident_id"]
        incident_knowledge.initialize(self.db)
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE incident SET tenant_id=%s,workspace_id=%s,primary_job_id=%s WHERE incident_id=%s",
                ("tenant-a", "workspace-a", "job-a", incident_id),
            )
        self.obj.save_agent_findings(
            incident_id, {"hypotheses": [], "evidence": [], "impacts": [],
                          "action_plan": {"rationale": "Initial", "steps": []}},
            critic_status="INCONCLUSIVE",
        )
        item_id = incident_knowledge.submit(
            self.db, "tenant-a", "workspace-a", incident_id, "job-a",
            "SOP", "Procedure", "Inspect the queue.", "operator",
        )
        captured = {}

        class Investigator:
            async def investigate(self, *args, **kwargs):
                captured["reference"] = kwargs["reference_context"]
                return {
                    "rca_result": {"finding": "Possible issue", "confidence": 0.1},
                    "impact_result": {"impacts": []},
                    "runbook_result": {"finding": "Review " + item_id, "steps": []},
                    "critic_status": "PASSED",
                    "final_plan": {
                        "rationale": "Inspect queue",
                        "expected_outcome": "Queue inspected",
                        "steps": [{"action": "Inspect queue", "tool": "manual", "risk": "LOW"}],
                    },
                }

        async def request():
            conv = conversation.CommanderConversation(db=self.db)
            conv.agent.react_to_guidance = AsyncMock(return_value="Request recorded.")
            result = await conv.guide(
                incident_id, "tenant-a", "Please re-investigate using the submitted incident references.",
                "operator", guidance_type="INVESTIGATION_DIRECTION", is_fact=False,
            )
            self.assertTrue(result["reinvestigation_triggered"])
            await asyncio.gather(*list(conversation._BACKGROUND_TASKS))

        with patch.object(langgraph_investigator, "LangGraphInvestigator", Investigator):
            asyncio.run(request())
        self.assertIn(item_id, captured["reference"])
        self.assertIn("Inspect the queue.", captured["reference"])
        with self.db.get_connection() as conn:
            plan = conn.execute(
                "SELECT status,rationale FROM action_plan WHERE incident_id=%s ORDER BY plan_version DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
        self.assertEqual(plan[0], "NEEDS_REVIEW")
        self.assertIn(item_id, plan[1])

    def test_truncated_approved_sop_keeps_plan_in_review(self):
        from app.domain import incident_knowledge
        from app.domain.agents import langgraph_investigator

        incident_id = self.ingest(docs=[])["incident_id"]
        incident_knowledge.initialize(self.db)
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE incident SET tenant_id=%s, workspace_id=%s, primary_job_id=%s, status='DETECTED' WHERE incident_id=%s",
                ("tenant-a", "workspace-a", "job-a", incident_id),
            )
        for number in range(2):
            item_id = incident_knowledge.submit(
                self.db, "tenant-a", "workspace-a", incident_id, "job-a",
                "SOP", f"Long procedure {number}", "x" * 70000, "operator",
            )
            self.assertTrue(incident_knowledge.review(
                self.db, "tenant-a", "workspace-a", item_id, "APPROVED", "admin",
            ))

        class Investigator:
            async def investigate(self, *args, **kwargs):
                return {
                    "rca_result": {"finding": "Unknown"},
                    "impact_result": {"impacts": []},
                    "runbook_result": {"steps": []},
                    "critic_status": "PASSED",
                    "final_plan": {"rationale": "Review", "steps": [
                        {"action": "Manual review", "tool": "manual", "risk": "LOW"}
                    ]},
                }

        with patch.object(langgraph_investigator, "LangGraphInvestigator", Investigator):
            self.assertTrue(self.obj.triage_incident(incident_id)["saved_plan"])
        with self.db.get_connection() as conn:
            status, rationale = conn.execute(
                "SELECT status,rationale FROM action_plan WHERE incident_id=%s",
                (incident_id,),
            ).fetchone()
        self.assertEqual(status, "NEEDS_REVIEW")
        self.assertIn("context limits", rationale)

    def test_complete_long_sop_reaches_investigator(self):
        from app.domain import incident_knowledge
        from app.domain.agents import langgraph_investigator

        incident_id = self.ingest(docs=[])["incident_id"]
        incident_knowledge.initialize(self.db)
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE incident SET tenant_id=%s, workspace_id=%s, primary_job_id=%s, status='DETECTED' WHERE incident_id=%s",
                ("tenant-a", "workspace-a", "job-a", incident_id),
            )
        content = "x" * 9000 + " END-OF-PROCEDURE"
        item_id = incident_knowledge.submit(
            self.db, "tenant-a", "workspace-a", incident_id, "job-a",
            "SOP", "Long procedure", content, "operator",
        )
        self.assertTrue(incident_knowledge.review(
            self.db, "tenant-a", "workspace-a", item_id, "APPROVED", "admin",
        ))
        captured = {}

        class Investigator:
            async def investigate(self, *args, **kwargs):
                captured["reference"] = kwargs["reference_context"]
                return {
                    "rca_result": {"finding": "Unknown"},
                    "impact_result": {"impacts": []},
                    "runbook_result": {"steps": []},
                    "critic_status": "PASSED",
                    "final_plan": {"rationale": "Review", "steps": [
                        {"action": "Manual review", "tool": "manual", "risk": "LOW"}
                    ]},
                }

        with patch.object(langgraph_investigator, "LangGraphInvestigator", Investigator):
            self.assertTrue(self.obj.triage_incident(incident_id)["saved_plan"])
        reference = json.loads(captured["reference"].split("\n", 1)[1])
        self.assertEqual(reference["items"][0]["content"], content)
        self.assertFalse(reference["items"][0]["content_truncated"])
        with self.db.get_connection() as conn:
            status = conn.execute(
                "SELECT status FROM action_plan WHERE incident_id=%s", (incident_id,)
            ).fetchone()[0]
        self.assertEqual(status, "PENDING_APPROVAL")

    def test_approved_sop_is_available_to_a_new_investigation(self):
        from app.domain import incident_knowledge
        from app.domain.agents import langgraph_investigator

        incident_id = self.ingest(docs=[])["incident_id"]
        incident_knowledge.initialize(self.db)
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE incident SET tenant_id=%s, workspace_id=%s, primary_job_id=%s, status='DETECTED' WHERE incident_id=%s",
                ("tenant-a", "workspace-a", "job-a", incident_id),
            )
        item_id = incident_knowledge.submit(
            self.db, "tenant-a", "workspace-a", incident_id, "job-a",
            "SOP", "Recovery", "Inspect the queue.", "operator",
        )
        self.assertTrue(incident_knowledge.review(
            self.db, "tenant-a", "workspace-a", item_id, "APPROVED", "admin",
        ))
        captured = {}

        class Investigator:
            async def investigate(self, *args, **kwargs):
                captured["reference"] = kwargs["reference_context"]
                return {
                    "rca_result": {"finding": "Possible queue issue", "confidence": 0.1},
                    "impact_result": {"impacts": []},
                    "runbook_result": {"finding": f"Review procedure {item_id}", "steps": []},
                    "critic_status": "INCONCLUSIVE",
                    "final_plan": {
                        "rationale": "Inspect the queue",
                        "expected_outcome": "Queue inspected",
                        "steps": [{"action": "Inspect queue", "tool": "manual", "risk": "LOW"}],
                    },
                }

        with patch.object(langgraph_investigator, "LangGraphInvestigator", Investigator):
            self.assertTrue(self.obj.triage_incident(incident_id)["saved_plan"])
        self.assertIn(item_id, captured["reference"])
        self.assertIn("Inspect the queue.", captured["reference"])
        self.assertIn('"status": "APPROVED"', captured["reference"])
        with self.db.get_connection() as conn:
            plan = conn.execute(
                "SELECT status,rationale FROM action_plan WHERE incident_id=%s ORDER BY created_at DESC LIMIT 1",
                (incident_id,),
            ).fetchone()
        self.assertEqual(plan[0], "NEEDS_REVIEW")
        self.assertIn(item_id, plan[1])

    def test_email_plan_remains_needs_review(self):
        result = self.ingest()
        output = self.obj.save_agent_findings(
            result["incident_id"],
            {"evidence": [{"type": "Log", "excerpt": "synthetic"}], "hypotheses": [], "impacts": [], "action_plan": {"steps": []}},
            critic_status="PASSED",
        )
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT status, rationale FROM action_plan WHERE action_plan_id=%s", (output["plan_id"],)).fetchone()
            self.assertEqual(row[0], "NEEDS_REVIEW")
            self.assertIn("UNVERIFIED EMAIL EVIDENCE", row[1])


if __name__ == "__main__":
    unittest.main()
