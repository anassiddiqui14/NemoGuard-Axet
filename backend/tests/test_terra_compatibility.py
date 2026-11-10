"""Verify model-specific token arguments without external inference calls."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.domain.agents.base_agent import BaseAgent, completion_sampling, completion_token_limit
from app.domain.agents.commander_live_agent import CommanderLiveAgent


def completion(content='{"ok": true}', tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls)
    )])


def make_client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class TerraCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_model_specific_token_limit(self):
        self.assertEqual(completion_token_limit("gpt-6.1-terra", 42), {"max_completion_tokens": 42})
        self.assertEqual(completion_token_limit("other-model", 42), {"max_tokens": 42})
        self.assertEqual(completion_sampling("gpt-6.1-terra", 0.3, 0.95), {})
        self.assertEqual(completion_sampling("other-model", 0.3, 0.95), {"temperature": 0.3, "top_p": 0.95})

    async def test_json_and_tool_calls_use_terra_parameter(self):
        agent = BaseAgent.__new__(BaseAgent)
        agent.model = "gpt-6.1-terra"
        agent.agent_name = "Watcher"
        agent.system_prompt = "test"
        create = AsyncMock(return_value=completion())
        agent.client = make_client(create)
        self.assertEqual(await agent.call_llm_json("test"), {"ok": True})
        self.assertEqual(create.await_args.kwargs["max_completion_tokens"], 16384)
        self.assertNotIn("max_tokens", create.await_args.kwargs)
        self.assertNotIn("temperature", create.await_args.kwargs)
        self.assertEqual(await agent.call_llm_with_tools("test", [], AsyncMock()), {"ok": True})
        self.assertEqual(create.await_args.kwargs["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", create.await_args.kwargs)
        self.assertNotIn("temperature", create.await_args.kwargs)

    async def test_tool_budget_fallback_uses_terra_parameter(self):
        agent = BaseAgent.__new__(BaseAgent)
        agent.model = "gpt-6.1-terra"
        agent.agent_name = "Watcher"
        agent.system_prompt = "test"
        tool = SimpleNamespace(id="call", function=SimpleNamespace(name="lookup", arguments="{}"))
        create = AsyncMock(side_effect=[completion(None, [tool])] * 5 + [completion()])
        agent.client = make_client(create)
        self.assertEqual(await agent.call_llm_with_tools("test", [], AsyncMock(return_value="ok")), {"ok": True})
        self.assertEqual(create.await_count, 6)
        for call in create.await_args_list:
            self.assertEqual(call.kwargs["max_completion_tokens"], 4096)
            self.assertNotIn("max_tokens", call.kwargs)
            self.assertNotIn("temperature", call.kwargs)

    async def test_commander_calls_use_terra_parameter(self):
        agent = CommanderLiveAgent.__new__(CommanderLiveAgent)
        agent.model = "gpt-6.1-terra"
        agent.agent_name = "Commander"
        agent.system_prompt = "test"
        create = AsyncMock(return_value=completion("Answer"))
        agent.client = make_client(create)
        self.assertEqual(await agent._answer_with_optional_tools("test"), "Answer")
        self.assertEqual(create.await_args.kwargs["max_completion_tokens"], 384)
        self.assertNotIn("max_tokens", create.await_args.kwargs)
        self.assertNotIn("temperature", create.await_args.kwargs)
        self.assertNotIn("top_p", create.await_args.kwargs)
        await agent.react_to_guidance("incident", {}, "guidance", False, False)
        self.assertEqual(create.await_args.kwargs["max_completion_tokens"], 180)
        self.assertNotIn("max_tokens", create.await_args.kwargs)
        self.assertNotIn("temperature", create.await_args.kwargs)
        self.assertNotIn("top_p", create.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()
