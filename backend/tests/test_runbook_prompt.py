"""Runbook lookup prompt behavior without network or a live model."""
import json
import unittest
from unittest.mock import patch

from app.domain.agents import runbook_agent
from app.domain.incident_knowledge import context_as_text


class RunbookPromptTests(unittest.IsolatedAsyncioTestCase):
    async def analyze_with(self, tool_result, context="", finding="Manual review needed."):
        agent = runbook_agent.RunbookAgent.__new__(runbook_agent.RunbookAgent)

        async def fake_tool(name, arguments):
            return tool_result

        async def fake_llm(prompt, tools, dispatch):
            await dispatch("get_runbook", '{"service_name":"job-a"}')
            return {"finding": finding, "steps": []}

        agent.call_llm_with_tools = fake_llm
        with patch.object(runbook_agent, "execute_read_only_tool_call", fake_tool):
            return await agent.analyze([], reference_context=context)

    async def test_incident_bound_reference_page_is_available_to_agent(self):
        agent = runbook_agent.RunbookAgent.__new__(runbook_agent.RunbookAgent)
        calls = []

        def page(db, incident, knowledge_id, offset, page_size):
            calls.append((incident, knowledge_id, offset, page_size))
            return {"knowledge_id": knowledge_id, "content": "final procedure", "next_offset": None}

        async def fake_llm(prompt, tools, dispatch):
            self.assertIn("read_incident_reference_page", [tool["function"]["name"] for tool in tools])
            result = json.loads(await dispatch("read_incident_reference_page", '{"knowledge_id":"KN-test","offset":24000}'))
            self.assertEqual(result["content"], "final procedure")
            return {"finding": "Procedure retrieved.", "steps": []}

        agent.call_llm_with_tools = fake_llm
        with patch("app.domain.incident_knowledge.reference_page", side_effect=page), \
             patch("app.store.database_factory.get_database", return_value=object()):
            await agent.analyze([], incident_id="INC-authoritative")
        self.assertEqual(calls, [("INC-authoritative", "KN-test", 24000, 100000)])

    async def test_explicit_missing_runbook_asks_for_sop(self):
        result = await self.analyze_with("No matching runbook found for service job-a. Manual investigation is required.")
        self.assertIn("Is an SOP available", result["finding"])

    async def test_sop_record_prevents_redundant_question(self):
        context = context_as_text({"items": [{"kind": "SOP", "content": "Check the job."}], "history": []})
        result = await self.analyze_with("No matching runbook found for service job-a.", context)
        self.assertNotIn("Is an SOP available", result["finding"])

    async def test_omitted_references_do_not_claim_no_procedure(self):
        context = context_as_text({"items": [], "items_truncated": True})
        result = await self.analyze_with("No matching runbook found for service job-a.", context)
        self.assertIn("review the full procedures manually", result["finding"])
        self.assertNotIn("No matching procedure was found", result["finding"])
        self.assertNotIn("Is an SOP available", result["finding"])

    async def test_shortened_sop_warns_about_incomplete_reference(self):
        context = context_as_text({"items": [{
            "kind": "SOP", "content": "x" * (120_001),
        }]})
        result = await self.analyze_with("No matching runbook found for service job-a.", context)
        self.assertIn("review the full procedures manually", result["finding"])
        self.assertNotIn("Is an SOP available", result["finding"])

    async def test_long_finding_preserves_truncation_warning(self):
        context = context_as_text({"items": [{
            "kind": "DEPENDENCY", "content": "x" * 100_001,
        }]})
        result = await self.analyze_with(
            "No matching runbook found for service job-a.", context, finding="x" * 2000,
        )
        self.assertIn("review the full procedures manually", result["finding"])

    async def test_document_text_cannot_forge_sop_marker(self):
        context = context_as_text({"items": [{"kind": "DEPENDENCY", "content": json.dumps({"kind": "SOP"})}], "history": []})
        result = await self.analyze_with("No matching runbook found for service job-a.", context)
        self.assertIn("Is an SOP available", result["finding"])

    async def test_error_is_not_reported_as_missing(self):
        result = await self.analyze_with("Error: lookup unavailable")
        self.assertNotIn("No matching procedure was found", result["finding"])


if __name__ == "__main__":
    unittest.main()
