"""Shared fixtures for the agent-layer tests: demo catalog wiring, a scripted
model client and a stand-in for the Claude Agent SDK."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.agents.catalog_agent import load_active_catalog
from app.agents.chat import build_bus
from app.agents.geo import GazetteerGeocoder, load_vendor_sites

EXAMPLE_QUESTION = (
    "I need 2 gallons of milk, 2 lb of butter and 3 dozen eggs, "
    "I live in Exampleville. What is my best order?"
)
EXAMPLE_ITEMS = [
    {"product": "cow_milk", "quantity": 2, "unit": "gallon"},
    {"product": "butter", "quantity": 2, "unit": "lb"},
    {"product": "eggs", "quantity": 3, "unit": "dozen"},
]


def demo_bus():
    return build_bus(load_active_catalog("demo_only"), GazetteerGeocoder.from_file(), load_vendor_sites())


def trace_of(bus) -> list[tuple[str, str, str]]:
    return [(entry.sender, entry.target, entry.action) for entry in bus.log.entries]


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def tool_use(name: str, tool_input, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=call_id)


def response(*blocks, stop_reason: str | None = None) -> SimpleNamespace:
    calls_tool = any(block.type == "tool_use" for block in blocks)
    return SimpleNamespace(
        content=list(blocks), stop_reason=stop_reason or ("tool_use" if calls_tool else "end_turn")
    )


class FakeResult:
    def __init__(self, subtype: str = "success", result: str | None = None, is_error: bool = False):
        self.subtype, self.result, self.is_error = subtype, result, is_error


class FakeAgentSDK:
    """Stands in for the claude_agent_sdk module. The script is a list of steps:
    a (tool name, input) pair is run through the registered tool handler, the way
    the SDK does it, and a string or a FakeResult ends the run."""

    ResultMessage = FakeResult

    class ClaudeSDKError(Exception):
        pass

    def __init__(self, *steps, connect_error: Exception | None = None, delay_s: float = 0):
        self.steps = list(steps)
        self.connect_error = connect_error
        self.delay_s = delay_s
        self.options = None
        self.prompts: list[str] = []
        self.tool_results: list[dict] = []

    def tool(self, name, description, input_schema):
        return lambda handler: SimpleNamespace(name=name, input_schema=input_schema, handler=handler)

    def create_sdk_mcp_server(self, name, version="1.0.0", tools=None):
        return {tool.name: tool for tool in tools}

    def ClaudeAgentOptions(self, **kwargs):
        return SimpleNamespace(**kwargs)

    def ClaudeSDKClient(self, options):
        self.options = options
        return self

    async def __aenter__(self):
        if self.connect_error:
            raise self.connect_error
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        self.prompts.append(prompt)
        await asyncio.sleep(self.delay_s)

    async def receive_response(self):
        (tools,) = self.options.mcp_servers.values()
        for turn in range(len(self.steps)):
            if turn == self.options.max_turns:
                yield FakeResult("error_max_turns", is_error=True)
                return
            step = self.steps.pop(0)
            if isinstance(step, tuple):
                name, tool_input = step
                self.tool_results.append(await tools[name].handler(tool_input))
                continue
            yield FakeResult(result=step) if isinstance(step, str) else step
            return


class ScriptedClient:
    """Stands in for the Anthropic client: returns canned responses in order,
    repeating the last one, and keeps a copy of every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        index = min(len(self.requests), len(self.responses)) - 1
        reply = self.responses[index]
        if isinstance(reply, Exception):
            raise reply
        return reply
