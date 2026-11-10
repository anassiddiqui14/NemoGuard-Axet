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

    def test_no_sop_decision_is_scoped_durable_and_idempotent(self):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status='DETECTED' WHERE incident_id=%s", ("one",))
        self.assertFalse(knowledge.confirm_no_sop(self.db, "tenant-a", "workspace-b", "one", "operator"))
        self.assertTrue(knowledge.confirm_no_sop(self.db, "tenant-a", "workspace-a", "one", "operator"))
        self.assertTrue(knowledge.confirm_no_sop(self.db, "tenant-a", "workspace-a", "one", "operator"))
        with self.db.get_connection() as conn:
            decision = conn.execute(
                "SELECT decision,decided_by FROM incident_sop_decision WHERE incident_id=%s", ("one",),
            ).fetchone()
            messages = conn.execute(
                "SELECT role,intent,created_by FROM incident_message WHERE incident_id=%s", ("one",),
            ).fetchall()
            conn.execute("UPDATE incident SET status='INVESTIGATING' WHERE incident_id=%s", ("one",))
        self.assertEqual(tuple(decision), ("NO_SOP", "operator"))
        self.assertEqual([tuple(row) for row in messages], [("OPERATOR", "SOP_DECISION", "operator")])
        self.assertFalse(knowledge.confirm_no_sop(self.db, "tenant-a", "workspace-a", "one", "operator"))

    def test_admin_edit_and_archive_are_scoped_and_audited(self):
        item = knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "job-1",
                                "SOP", "Original", "Original procedure.", "operator")
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", item, "APPROVED", "admin"))
        self.assertFalse(knowledge.manage_item(
            self.db, "tenant-a", "workspace-b", item, "admin", "Correction",
            title="Edited", job_key="job-1", content="Updated procedure."))
        self.assertTrue(knowledge.manage_item(
            self.db, "tenant-a", "workspace-a", item, "admin", "Correction",
            title="Edited", job_key="job-1", content="Updated procedure."))
        self.assertEqual(knowledge.context_for_incident(self.db, "two")["items"], [])
        edited = knowledge.list_items(self.db, "tenant-a", "workspace-a")[0]
        self.assertEqual(edited["status"], "PENDING")
        self.assertIsNone(edited["reviewed_by"])
        self.assertEqual(edited["content"], "Updated procedure.")
        self.assertTrue(knowledge.manage_item(
            self.db, "tenant-a", "workspace-a", item, "admin", "Obsolete", archive=True))
        self.assertEqual(knowledge.context_for_incident(self.db, "one")["items"], [])
        self.assertFalse(knowledge.manage_item(
            self.db, "tenant-a", "workspace-a", item, "admin", "Again", archive=True))
        with self.db.get_connection() as conn:
            changes = conn.execute(
                "SELECT action,previous_json FROM incident_knowledge_change "
                "WHERE knowledge_id=%s ORDER BY created_at", (item,),
            ).fetchall()
        self.assertEqual([row[0] for row in changes], ["EDIT", "ARCHIVE"])
        self.assertIn("Original procedure.", changes[0][1])
        self.assertIn("Updated procedure.", changes[1][1])

    def test_approval_invalidates_workspace_plans_and_blocks_active_work(self):
        item = knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "job-1",
                                "SOP", "Procedure", "Inspect logs.", "operator")
        with self.db.get_connection() as conn:
            for incident in ("one", "two", "other"):
                conn.execute(
                    "INSERT INTO agent_run (agent_run_id,incident_id,agent_name,objective,status) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    ("agent-" + incident, incident, "test", "test", "COMPLETED"),
                )
                conn.execute(
                    "INSERT INTO action_plan (action_plan_id,incident_id,agent_run_id,plan_version,status,"
                    "overall_risk,rationale,expected_outcome,rollback_summary,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    ("plan-" + incident, incident, "agent-" + incident, 1,
                     "PENDING_APPROVAL", "LOW", "Review", "Expected", "Rollback", "now"),
                )
            conn.execute("UPDATE incident SET status='INVESTIGATING' WHERE incident_id=%s", ("two",))
        with self.assertRaises(ValueError):
            knowledge.review(self.db, "tenant-a", "workspace-a", item, "APPROVED", "admin")
        self.assertEqual(knowledge.list_items(self.db, "tenant-a", "workspace-a")[0]["status"], "PENDING")
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status='PLAN_READY' WHERE incident_id=%s", ("two",))
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", item, "APPROVED", "admin"))
        with self.db.get_connection() as conn:
            statuses = dict(conn.execute(
                "SELECT incident_id,status FROM action_plan ORDER BY incident_id"
            ).fetchall())
        self.assertEqual(statuses, {"one": "NEEDS_REVIEW", "two": "NEEDS_REVIEW",
                                    "other": "PENDING_APPROVAL"})

    def test_document_change_invalidates_workspace_plans_and_blocks_active_work(self):
        item = knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "job-1",
                                "SOP", "Original", "Original procedure.", "operator")
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", item, "APPROVED", "admin"))
        with self.db.get_connection() as conn:
            for incident in ("one", "two", "other"):
                conn.execute(
                    "INSERT INTO agent_run (agent_run_id,incident_id,agent_name,objective,status) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    ("agent-" + incident, incident, "test", "test", "COMPLETED"),
                )
                conn.execute(
                    "INSERT INTO action_plan (action_plan_id,incident_id,agent_run_id,plan_version,status,"
                    "overall_risk,rationale,expected_outcome,rollback_summary,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    ("plan-" + incident, incident, "agent-" + incident, 1,
                     "PENDING_APPROVAL", "LOW", "Review", "Expected", "Rollback", "now"),
                )
            conn.execute("UPDATE incident SET status='INVESTIGATING' WHERE incident_id=%s", ("two",))
        with self.assertRaises(ValueError):
            knowledge.manage_item(self.db, "tenant-a", "workspace-a", item, "admin", "Correction",
                                  title="Edited", job_key="job-1", content="Updated procedure.")
        self.assertEqual(knowledge.list_items(self.db, "tenant-a", "workspace-a")[0]["content"],
                         "Original procedure.")
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status='PLAN_READY' WHERE incident_id=%s", ("two",))
        self.assertTrue(knowledge.manage_item(
            self.db, "tenant-a", "workspace-a", item, "admin", "Correction",
            title="Edited", job_key="job-1", content="Updated procedure."))
        with self.db.get_connection() as conn:
            statuses = dict(conn.execute(
                "SELECT incident_id,status FROM action_plan ORDER BY incident_id"
            ).fetchall())
        self.assertEqual(statuses, {"one": "NEEDS_REVIEW", "two": "NEEDS_REVIEW",
                                    "other": "PENDING_APPROVAL"})

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

    def test_approved_sop_reused_from_linked_email_without_asserting_job_identity(self):
        item = knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "wkfl_load_MDM_NSF",
                                "SOP", "Recovery", "Verify input before recovery.", "operator")
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", item, "APPROVED", "admin"))
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET primary_job_id=NULL WHERE incident_id=%s", ("two",))
            conn.execute("UPDATE incident SET primary_job_id=NULL WHERE incident_id=%s", ("other",))
            conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT, document_kind TEXT, title TEXT, content TEXT, submitted_by TEXT, created_at TEXT)")
            for suffix, incident in (("a", "two"), ("b", "other")):
                conn.execute("INSERT INTO alert (alert_id,tenant_id,workspace_id,opened_ts,severity,alert_type,source_system,message,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             ("alert-" + suffix, "tenant-a", "workspace-a" if suffix == "a" else "workspace-b", "now", "high", "TEST", "Outlook", "failure", "open"))
                conn.execute("INSERT INTO incident_alert (incident_id,alert_id,correlation_score,correlation_reasons_json,added_at) VALUES (%s,%s,%s,%s,%s)",
                             (incident, "alert-" + suffix, 1, "[]", "now"))
                conn.execute("INSERT INTO reviewed_alert_document VALUES (%s,%s,%s,%s,%s,%s,%s)",
                             ("doc-" + suffix, "alert-" + suffix, "EMAIL_BODY", "failure", "Workflow wkfl_load_MDM_NSF failed.", "automatic-outlook-bridge", "now"))
        context = knowledge.context_for_incident(self.db, "two")
        self.assertEqual(context["job_match_source"], "unverified_email_identifier")
        self.assertEqual([item_["knowledge_id"] for item_ in context["items"]], [item])
        self.assertEqual(knowledge.reference_page(self.db, "two", item)["content"], "Verify input before recovery.")
        self.assertEqual(knowledge.context_for_incident(self.db, "other")["items"], [])
        self.assertIsNone(knowledge.reference_page(self.db, "other", item))
        with self.db.get_connection() as conn:
            self.assertIsNone(conn.execute("SELECT primary_job_id FROM incident WHERE incident_id=%s", ("two",)).fetchone()[0])

    def test_alert_matching_requires_one_exact_approved_scoped_identifier(self):
        first = knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "wkfl_load_MDM_NSF",
                                 "SOP", "Recovery", "Review the source.", "operator")
        second = knowledge.submit(self.db, "tenant-a", "workspace-a", "one", "wkfl_OTHER",
                                  "SOP", "Other", "Review another workflow.", "operator")
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", first, "APPROVED", "admin"))
        text = "Workflow wkfl_load_MDM_NSF failed."
        self.assertEqual(knowledge.match_approved_sop_job(self.db, "tenant-a", "workspace-a", text),
                         "wkfl_load_MDM_NSF")
        self.assertIsNone(knowledge.match_approved_sop_job(self.db, "tenant-a", "workspace-b", text))
        self.assertIsNone(knowledge.match_approved_sop_job(self.db, "tenant-a", "workspace-a",
                                                           "wkfl_load_MDM_NSF-extra"))
        self.assertTrue(knowledge.review(self.db, "tenant-a", "workspace-a", second, "APPROVED", "admin"))
        self.assertIsNone(knowledge.match_approved_sop_job(
            self.db, "tenant-a", "workspace-a", "wkfl_load_MDM_NSF and wkfl_OTHER failed"
        ))

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
