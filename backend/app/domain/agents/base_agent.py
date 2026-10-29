import os
import json
from typing import Dict, Any

from openai import AsyncOpenAI


class BaseAgent:
    def __init__(self, agent_name: str, system_prompt: str, model: str = ""):
        self.agent_name = agent_name
        self.system_prompt = system_prompt

        # The model configured in the aXet Python Agent node is authoritative.
        # Existing per-agent NVIDIA model arguments are intentionally ignored
        # whenever AXET_MODEL is available.
        self.model = os.environ.get("AXET_MODEL") or model

        base_url = (
            os.environ.get("AXET_LLM_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
        )
        api_key = os.environ.get("OPENAI_API_KEY")
        project_id = os.environ.get("AXET_PROJECT_ID")
        user_id = os.environ.get("AXET_USER_ID")
        backend_jwt = os.environ.get("DEPTAPPS_WEB_BACKEND_JWT_TOKEN")

        missing = [
            name
            for name, value in {
                "AXET_LLM_BASE_URL": base_url,
                "OPENAI_API_KEY": api_key,
                "AXET_PROJECT_ID": project_id,
                "AXET_USER_ID": user_id,
                "DEPTAPPS_WEB_BACKEND_JWT_TOKEN": backend_jwt,
                "AXET_MODEL": self.model,
            }.items()
            if not value
        ]

        if missing:
            print(
                f"WARNING [{agent_name}]: Missing aXet LLM configuration: "
                + ", ".join(missing)
            )

        headers = {}

        if user_id:
            headers["x-axet-user-id"] = user_id

        if project_id:
            headers["x-axet-project-id"] = project_id

        if backend_jwt:
            headers["X-Auth-Token"] = f"Bearer {backend_jwt}"

        self.client = AsyncOpenAI(
            base_url=base_url or "http://127.0.0.1",
            api_key=api_key or "unset",
            default_headers=headers,
            timeout=120.0,
        )

    @staticmethod
    def _parse_json_content(content: str) -> Dict[str, Any]:
        content = (content or "").strip()

        if content.startswith("```json"):
            content = content[7:].lstrip()
        elif content.startswith("```"):
            content = content[3:].lstrip()

        if content.endswith("```"):
            content = content[:-3].rstrip()

        try:
            return json.loads(content)
        except Exception as parse_e:
            return {
                "error": (
                    f"{str(parse_e)}. "
                    "LLM response was not valid JSON or may have been truncated."
                )
            }

    async def call_llm_json(self, prompt: str) -> Dict[str, Any]:
        """Call the NTT aXet-managed LLM and return a JSON response."""
        try:
            print(
                f"[{self.agent_name}] Calling aXet LLM "
                f"using model {self.model}..."
            )

            completion = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": self.system_prompt,
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.1,
                max_tokens=16384,
                stream=False,
            )

            content = completion.choices[0].message.content or ""

            if content:
                print(content, flush=True)

            print(f"[{self.agent_name}] aXet LLM call complete.")

            return self._parse_json_content(content)

        except Exception as e:
            print(f"{self.agent_name} aXet LLM Error: {e}")
            return {"error": str(e)}

    async def call_llm_with_tools(
        self,
        prompt: str,
        tools: list,
        execute_tool_fn,
    ) -> Dict[str, Any]:
        """
        Call the aXet LLM iteratively and execute requested tools.

        Tool-budget exhaustion fix: previously, if the LAST allowed
        iteration (max_iterations) itself returned tool_calls, those tools
        WERE executed and their results appended to `messages`, but the
        `while` loop then exited on its next condition check WITHOUT ever
        giving the model a turn to synthesize a final answer from that
        freshly-fetched evidence -- the method fell straight through to a
        generic "LLM exceeded maximum allowed tool iterations" error, even
        though the model may have been perfectly able to produce a valid
        final response given one more turn. This surfaced concretely as a
        false SAFETY_VALIDATION_FAILED for the Grounding Critic: the critic
        never actually rendered a safety judgment, yet the caller treated
        the resulting error dict as if "passed" were False.

        Fix: when the loop's tool budget is exhausted AND the final
        iteration's response still contained tool_calls (meaning those
        tools were executed and their results appended), make exactly ONE
        additional LLM call with NO `tools` parameter at all (so the
        OpenAI-compatible API cannot return tool_calls again), explicitly
        telling the model the diagnostic tool budget is exhausted and it
        must produce its final answer now from the evidence already
        gathered. The full existing message history (system prompt, user
        prompt, every prior assistant/tool turn including the final tool
        round's results) is reused verbatim; only one new user message is
        appended. The response is parsed through the same
        `_parse_json_content()` used everywhere else, so malformed JSON on
        this forced final turn still degrades gracefully into the same
        existing error-dict shape callers already handle. `stream=False`
        is preserved, matching the rest of this already-proven-reliable
        aXet integration.
        """
        messages = [
            {
                "role": "system",
                "content": self.system_prompt,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        try:
            iteration_count = 0
            max_iterations = 5
            tool_budget_exhausted_with_pending_tools = False

            while iteration_count < max_iterations:
                iteration_count += 1

                print(
                    f"[{self.agent_name}] Calling aXet LLM with "
                    f"{len(tools)} tools... "
                    f"(Iter {iteration_count}/{max_iterations})"
                )

                completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=0.1,
                    max_tokens=4096,
                    stream=False,
                )

                choice = completion.choices[0]
                message = choice.message

                if getattr(message, "tool_calls", None):
                    tool_calls_dict = []

                    for tool_call in message.tool_calls:
                        tool_calls_dict.append(
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.function.name,
                                    "arguments": tool_call.function.arguments,
                                },
                            }
                        )

                    messages.append(
                        {
                            "role": "assistant",
                            "content": message.content,
                            "tool_calls": tool_calls_dict,
                        }
                    )

                    for tool_call in message.tool_calls:
                        print(
                            f"[{self.agent_name}] Executing tool: "
                            f"{tool_call.function.name}"
                        )

                        result = await execute_tool_fn(
                            tool_call.function.name,
                            tool_call.function.arguments,
                        )

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": str(result),
                            }
                        )

                    # If this was the LAST allowed iteration and the model
                    # still requested tools (now executed above), fall
                    # through after the loop to the forced final synthesis
                    # turn rather than silently erroring.
                    if iteration_count >= max_iterations:
                        tool_budget_exhausted_with_pending_tools = True

                else:
                    print(
                        f"\n[{self.agent_name}] "
                        "aXet LLM tool execution complete."
                    )

                    return self._parse_json_content(
                        message.content or ""
                    )

            if tool_budget_exhausted_with_pending_tools:
                print(
                    f"[{self.agent_name}] Tool budget exhausted after "
                    f"{max_iterations} iterations with pending tool "
                    "results -- requesting one final tools-disabled "
                    "synthesis turn."
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The diagnostic tool budget for this request has been "
                            "exhausted -- you may NOT call any more tools. Using "
                            "only the evidence already gathered in this conversation "
                            "(including the most recent tool results above), produce "
                            "your final answer now, in the exact format required by "
                            "your system prompt."
                        ),
                    }
                )
                final_completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=4096,
                    stream=False,
                )
                final_message = final_completion.choices[0].message

                # Defensive: if the model somehow still attempts a tool
                # call (it should be structurally unable to, since no
                # `tools` were supplied), do not silently accept it as a
                # valid final answer -- treat it the same as "no content".
                if getattr(final_message, "tool_calls", None):
                    print(
                        f"[{self.agent_name}] Forced final synthesis turn "
                        "unexpectedly returned tool_calls despite no tools "
                        "being supplied; treating as incomplete."
                    )
                    return {
                        "error": (
                            "LLM attempted another tool call after the tool "
                            "budget was exhausted; no valid final answer was "
                            "produced."
                        )
                    }

                return self._parse_json_content(final_message.content or "")

            return {
                "error": (
                    "LLM exceeded maximum allowed tool iterations "
                    f"({max_iterations})."
                )
            }

        except Exception as e:
            print(f"{self.agent_name} aXet LLM Error: {e}")
            return {"error": str(e)}
