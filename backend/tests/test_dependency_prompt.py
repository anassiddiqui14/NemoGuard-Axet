"""Dependency prompts without a live model."""
import unittest

from app.domain.agents.dependency_agent import DependencyAgent
from app.domain.incident_knowledge import context_as_text


class DependencyPromptTests(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, response, context=""):
        agent = DependencyAgent.__new__(DependencyAgent)

        async def fake_llm(*args):
            return response.copy()

        agent.call_llm_with_tools = fake_llm
        return await agent.analyze("Possible failure", reference_context=context)

    async def test_empty_impacts_prompt_for_dependencies(self):
        result = await self.run_agent({"finding": "Unknown impact.", "impacts": []})
        self.assertIn("Can you describe any dependencies", result["finding"])

    async def test_existing_dependency_avoids_redundant_prompt(self):
        context = context_as_text({"items": [{"kind": "DEPENDENCY", "content": "Job A feeds B."}], "history": []})
        result = await self.run_agent({"finding": "Unknown impact.", "impacts": []}, context)
        self.assertNotIn("Can you describe any dependencies", result["finding"])

    async def test_failure_is_not_reported_as_no_dependencies(self):
        result = await self.run_agent({"error": "lookup failed"})
        self.assertEqual(result, {"error": "lookup failed"})


if __name__ == "__main__":
    unittest.main()
