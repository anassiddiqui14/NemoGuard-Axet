"""Tests for scoped incident knowledge and extraction."""
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from app.domain import incident_knowledge as knowledge
from app.store.sqlite_database import SqliteDatabase


class IncidentKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = SqliteDatabase(str(Path(self.temp.name) / "test.db"))
        self.db.init_schema()
        knowledge.initialize(self.db)
        for incident, tenant, workspace, job in [
            ("one", "tenant-a", "workspace-a", "job-1"),
            ("two", "tenant-a", "workspace-a", "job-1"),
            ("other", "tenant-a", "workspace-b", "job-1"),
        ]:
            with self.db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO incident (incident_id,tenant_id,workspace_id,primary_job_id,"
                    "title,status,severity,detected_at,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (incident, tenant, workspace, job, "Test", "PLAN_READY", "high", "now", "now", "now"),
                )

    def test_pending_is_local_approved_is_scoped_and_rejection_excluded(self):
        identifier = knowledge.submit(
            self.db, "tenant-a", "workspace-a", "one", "job-1",
            "SOP", "Procedure", "Investigate the failure.", "operator",
        )
        self.assertEqual(len(knowledge.context_for_incident(self.db, "one")["items"]), 1)
        self.assertEqual(knowledge.context_for_incident(self.db, "two")["items"], [])
        self.assertFalse(knowledge.review(self.db, "tenant-a", "workspace-b", identifier, "APPROVED", "admin"))
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", identifier, "APPROVED", "admin"))
        self.assertEqual(knowledge.context_for_incident(self.db, "two")["items"][0]["knowledge_id"], identifier)
        self.assertEqual(knowledge.context_for_incident(self.db, "other")["items"], [])
        rejected = knowledge.submit(
            self.db, "tenant-a", "workspace-a", "one", "job-1",
            "DEPENDENCY", "Dependency", "Job B depends on job A.", "operator",
        )
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", rejected, "REJECTED", "admin"))
        self.assertFalse(knowledge.review(self.db, "tenant-a", "workspace-a", rejected, "APPROVED", "admin"))
        self.assertNotIn(rejected, [item["knowledge_id"] for item in knowledge.context_for_incident(self.db, "one")["items"]])

    def test_reference_pages_are_complete_and_scoped(self):
        content = "a" * 99990 + "FINAL-STEP"
        item = knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "job-1",
                                "SOP", "Long procedure", content, "operator")
        pieces = []
        offset = 0
        while offset is not None:
            page = knowledge.reference_page(self.db, "one", item, offset)
            pieces.append(page["content"])
            offset = page["next_offset"]
        self.assertEqual("".join(pieces), content)
        self.assertIsNone(knowledge.reference_page(self.db, "two", item))
        self.assertIsNone(knowledge.reference_page(self.db, "other", item))
        with self.assertRaises(ValueError):
            knowledge.reference_page(self.db, "one", item, -1)
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", item, "APPROVED", "admin"))
        self.assertEqual(knowledge.reference_page(self.db, "two", item)["content"], content[:12000])
        self.assertIsNone(knowledge.reference_page(self.db, "other", item))
        rejected = knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "job-1",
                                    "SOP", "Rejected", "Do not use", "operator")
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", rejected, "REJECTED", "admin"))
        self.assertIsNone(knowledge.reference_page(self.db, "one", rejected))

    def test_reference_limit_is_reported_in_agent_context(self):
        for index in range(20):
            knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "job-1",
                             "SOP", f"Procedure {index}", "Check and verify.", "operator")
        context = knowledge.context_for_incident(self.db, "one")
        self.assertEqual(len(context["items"]), 20)
        self.assertFalse(context["items_truncated"])
        prompt = json.loads(knowledge.context_as_text(context).split("\n", 1)[1])
        self.assertFalse(prompt["items_truncated"])

        knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "job-1",
                         "SOP", "Extra procedure", "Check and verify.", "operator")
        context = knowledge.context_for_incident(self.db, "one")
        self.assertEqual(len(context["items"]), 20)
        self.assertTrue(context["items_truncated"])
        prompt = json.loads(knowledge.context_as_text(context).split("\n", 1)[1])
        self.assertTrue(prompt["items_truncated"])

    def test_reference_context_reports_budget_truncation(self):
        context = {"items": [
            {"knowledge_id": "first", "content": "x" * knowledge.MAX_CONTEXT_PER_REFERENCE},
            {"knowledge_id": "second", "content": "y" * knowledge.MAX_CONTEXT_PER_REFERENCE},
            {"knowledge_id": "third", "content": "omitted"},
        ]}
        prompt = json.loads(knowledge.context_as_text(context).split("\n", 1)[1])
        self.assertEqual(len(prompt["items"]), 3)
        self.assertFalse(prompt["items"][0]["content_truncated"])
        self.assertTrue(prompt["items"][1]["content_truncated"])
        self.assertTrue(prompt["items"][2]["content_truncated"])
        self.assertEqual(prompt["items"][2]["content"], "")
        self.assertFalse(prompt["items_truncated"])

    def test_job_identity_requires_scoped_execution_or_unambiguous_approval(self):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET primary_job_id=NULL WHERE incident_id=%s", ("two",))
        self.assertIsNone(knowledge.context_for_incident(self.db, "two")["job_key"])
        first = knowledge.submit(self.db, "tenant-a", "workspace-a", "two", "job-1",
                                 "DEPENDENCY", "Dependency", "Downstream job.", "operator")
        self.assertIsNone(knowledge.context_for_incident(self.db, "two")["job_key"])
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", first, "APPROVED", "admin"))
        self.assertEqual(knowledge.context_for_incident(self.db, "two")["job_key"], "job-1")
        second = knowledge.submit(self.db, "tenant-a", "workspace-a", "two", "job-2",
                                  "DEPENDENCY", "Another dependency", "Other job.", "operator")
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", second, "APPROVED", "admin"))
        self.assertIsNone(knowledge.context_for_incident(self.db, "two")["job_key"])

    def test_history_does_not_override_scoped_execution_job_with_approved_label(self):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO job (tenant_id,workspace_id,job_id,job_name,platform,domain,stage,schedule,"
                "criticality,default_duration_sec,owner_team,retry_policy,active) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("tenant-a", "workspace-a", "job-2", "Job 2", "test", "test", "test", "test", 1, 1, "test", "test", 1),
            )
            conn.execute(
                "INSERT INTO execution (tenant_id,workspace_id,run_id,job_id,scheduled_ts,start_ts,status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                ("tenant-a", "workspace-a", "run-2", "job-2", "now", "now", "failed"),
            )
            conn.execute(
                "UPDATE incident SET primary_job_id=NULL,primary_run_id=%s WHERE incident_id=%s",
                ("run-2", "one"),
            )
        item = knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "job-1",
                                "DEPENDENCY", "Proposed mapping", "A depends on B", "operator")
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", item, "APPROVED", "admin"))
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status='RESOLVED' WHERE incident_id=%s", ("one",))
        self.assertEqual(knowledge.context_for_incident(self.db, "two")["history"], [])

    def test_utf8_docx_and_invalid_files(self):
        encode = lambda data: base64.b64encode(data).decode("ascii")
        self.assertEqual(knowledge.extract_document("sop.txt", encode(b"Step one")), "Step one")
        from docx import Document
        doc = Document()
        doc.add_paragraph("Step one")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Step two"
        table.cell(0, 1).text = "Verify"
        doc.add_paragraph("Step three")
        output = io.BytesIO()
        doc.save(output)
        extracted = knowledge.extract_document("sop.docx", encode(output.getvalue()))
        self.assertIn("Step one", extracted)
        self.assertIn("Step two", extracted)
        self.assertLess(extracted.index("Step one"), extracted.index("Step two"))
        self.assertLess(extracted.index("Step two"), extracted.index("Step three"))
        with self.assertRaises(ValueError):
            knowledge.extract_document("sop.exe", encode(b"bad"))
        with self.assertRaises(ValueError):
            knowledge.extract_document("sop.pdf", encode(b"bad"))
        with self.assertRaises(ValueError):
            knowledge.extract_document("sop.txt", encode(b" "))
        with self.assertRaises(ValueError):
            knowledge.extract_document("sop.txt", "not base64!")

    def test_sample_pdf_extracts_text(self):
        path = Path(__file__).resolve().parents[2] / "data" / "runbook_docs" / "RB_PIPELINE_STEP_FAILURE_DOC.pdf"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        self.assertTrue(knowledge.extract_document(path.name, encoded).strip())

    def test_history_verification_requires_matching_plan_and_steps(self):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status='RESOLVED' WHERE incident_id=%s", ("one",))
            conn.execute(
                "INSERT INTO agent_run (agent_run_id,incident_id,agent_name,objective,status) VALUES (%s,%s,%s,%s,%s)",
                ("agent", "one", "agent", "test", "COMPLETED"),
            )
            conn.execute(
                "INSERT INTO action_plan (action_plan_id,incident_id,agent_run_id,plan_version,status,overall_risk,rationale,expected_outcome,rollback_summary,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("plan", "one", "agent", 1, "EXECUTED", "LOW", "test", "test", "test", "now"),
            )
            conn.execute(
                "INSERT INTO action_step (action_step_id,action_plan_id,sequence_no,action_type,tool_name,risk_level,requires_approval,parameters_json,preconditions_json,expected_postconditions_json,status,capability_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("step", "plan", 1, "Check", "manual", "LOW", 0, "{}", "{}", "{}", "SUCCEEDED", "cap"),
            )
        self.assertEqual(knowledge.context_for_incident(self.db, "two")["history"][0]["verification_status"], "UNKNOWN")
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO verification_result (verification_id,incident_id,action_plan_id,check_name,status,expected_json,actual_json,evidence_ids_json,checked_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("check", "one", "plan", "cap verification", "PASSED", "{}", "{}", "[]", "now"),
            )
        history = knowledge.context_for_incident(self.db, "two")["history"][0]
        self.assertEqual(history["verification_status"], "PASSED")
        self.assertEqual(history["executed_steps"][0]["action"], "Check")
        with self.db.get_connection() as conn:
            conn.execute("UPDATE verification_result SET check_name=%s WHERE verification_id=%s", ("unrelated", "check"))
        self.assertEqual(knowledge.context_for_incident(self.db, "two")["history"][0]["verification_status"], "UNKNOWN")

    def test_history_requires_resolved_and_does_not_cross_workspaces(self):
        self.assertFalse(knowledge.record_history(self.db, "one"))
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status='RESOLVED', resolution_summary='Manual repair' WHERE incident_id=%s", ("one",))
        self.assertTrue(knowledge.record_history(self.db, "one"))
        history = knowledge.context_for_incident(self.db, "two")["history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["verification_status"], "UNKNOWN")
        self.assertEqual(history[0]["resolution"], "Manual repair")
        self.assertEqual(knowledge.context_for_incident(self.db, "other")["history"], [])


if __name__ == "__main__":
    unittest.main()
