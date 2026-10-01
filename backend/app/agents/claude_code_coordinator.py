"""The Claude coordinator without an API key: it reaches the model through the
Claude Code sign-in on this computer, using the Claude Agent SDK.

The SDK runs the tool loop itself. The four agents are handed to it as
in-process tools, so the model still chooses the calls and the bus still
checks and records each one."""
from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace
from typing import Any

from .bus import MessageBus
from .coordinator import ChatTurn, ConversationState, Coordinator
from .llm_coordinator import (
    DEFAULT_MODEL,
    MAX_STEPS,
    SYSTEM_PROMPT,
    TOOLS,
    AgentTool,
    LLMCoordinator,
    correction_request,
)
from .messages import CartResult, ShoppingRequest

logger = logging.getLogger(__name__)

TOOL_SERVER = "farmfind"
TIMEOUT_S = 180.0  # for one chat turn; a live turn took 6 to 19 seconds


class _NoAnswer(Exception):
    """The model run ended without a usable reply; the text says why."""


class ClaudeCodeCoordinator(LLMCoordinator):
    def __init__(
        self,
        sdk: Any,
        model: str,
        fallback: Coordinator,
        max_steps: int = MAX_STEPS,
        timeout_s: float = TIMEOUT_S,
    ):
        super().__init__(
            client=None,
            model=model,
            fallback=fallback,
            max_steps=max_steps,
            api_errors=(sdk.ClaudeSDKError,),
        )
        self.sdk = sdk
        self.timeout_s = timeout_s

    def handle(self, message: str, state: ConversationState, bus: MessageBus) -> ChatTurn:
        carts: list[CartResult] = []
        try:
            turn = asyncio.run(
                asyncio.wait_for(self._converse(message, state, bus, carts), self.timeout_s)
            )
        except _NoAnswer as exc:
            return self._fall_back(str(exc), message, state, bus)
        except TimeoutError:
            reason = f"no answer within {self.timeout_s:g} seconds"
            return self._fall_back(reason, message, state, bus)
        except Exception as exc:
            # Not only ClaudeSDKError: the SDK also raises plain Exception (a control
            # request that times out, an error relayed from the stream). Whatever
            # breaks the model run, the rule-based coordinator answers the turn.
            logger.exception("The model run failed; using the rule-based coordinator.")
            return self._fall_back(f"SDK error: {type(exc).__name__}", message, state, bus)
        return turn or self._templated(carts[-1] if carts else None, message, state, bus)

    async def _converse(
        self, message: str, state: ConversationState, bus: MessageBus, carts: list[CartResult]
    ) -> ChatTurn | None:
        """One model run plus at most one correction. None means the reply stayed wrong."""
        tools = [self._sdk_tool(tool, state, bus, carts) for tool in TOOLS]
        options = self.sdk.ClaudeAgentOptions(
            model=self.model,
            system_prompt=SYSTEM_PROMPT,
            mcp_servers={TOOL_SERVER: self.sdk.create_sdk_mcp_server(TOOL_SERVER, tools=tools)},
            # None of Claude Code's own tools (files, shell, web), none of the user's
            # Claude Code settings and none of their own MCP servers: the model gets
            # the four agents only.
            tools=[],
            allowed_tools=[f"mcp__{TOOL_SERVER}__{tool.name}" for tool in TOOLS],
            setting_sources=[],
            strict_mcp_config=True,
            max_turns=self.max_steps,
        )
        prompt = _prompt(state.history, message)
        async with self.sdk.ClaudeSDKClient(options=options) as client:
            for _ in range(2):
                reply = await self._ask(client, prompt)
                cart = carts[-1] if carts else None
                problems = self._check_reply(reply, cart, bus)
                if not problems:
                    return self._finish(reply, cart, state)
                prompt = correction_request(problems)
        return None

    async def _ask(self, client: Any, prompt: str) -> str:
        await client.query(prompt)
        result = None
        async for item in client.receive_response():
            if isinstance(item, self.sdk.ResultMessage):
                result = item
        if result is None:
            raise _NoAnswer("model returned no result")
        if result.subtype == "error_max_turns":
            raise _NoAnswer(f"no answer within {self.max_steps} steps")
        if result.is_error or result.subtype != "success":
            raise _NoAnswer(f"model stopped: {result.subtype}")
        reply = (result.result or "").strip()
        if not reply:
            raise _NoAnswer("model returned no text")
        return reply

    def _sdk_tool(
        self, tool: AgentTool, state: ConversationState, bus: MessageBus, carts: list[CartResult]
    ) -> Any:
        @self.sdk.tool(tool.name, tool.description, tool.input_model.model_json_schema())
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            result = self._call_agent(SimpleNamespace(name=tool.name, input=args), bus)
            if isinstance(result, str):
                return {"content": [{"type": "text", "text": result}], "is_error": True}
            if isinstance(result, CartResult):
                carts.append(result)
                state.request = ShoppingRequest.model_validate(args)
            return {"content": [{"type": "text", "text": result.model_dump_json()}]}

        return run


def _prompt(history: list[dict[str, str]], message: str) -> str:
    """Each turn is a fresh model session, so earlier turns go in as a transcript."""
    if not history:
        return message
    earlier = "\n".join(
        f"{'User' if turn['role'] == 'user' else 'Assistant'}: {turn['content']}" for turn in history
    )
    return f"Earlier in this conversation:\n{earlier}\n\nNew message from the user:\n{message}"


def create_claude_code_coordinator(fallback: Coordinator) -> ClaudeCodeCoordinator:
    """Build the coordinator from the environment. Raises ImportError when the
    optional `claude-agent-sdk` package is not installed."""
    import claude_agent_sdk

    return ClaudeCodeCoordinator(
        sdk=claude_agent_sdk,
        model=os.environ.get("FARMFIND_MODEL") or DEFAULT_MODEL,
        fallback=fallback,
    )
