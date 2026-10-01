"""Coordinator that lets a Claude model choose which agents to call and word the reply."""
from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel

from .bus import AgentRequestError, MessageBus
from .cart_agent import CartAgent
from .catalog_agent import CatalogAgent
from .coordinator import ChatTurn, ConversationState, Coordinator
from .delivery_agent import DeliveryAgent
from .distance_agent import DistanceAgent
from .messages import CartResult, CatalogQuery, DeliveryQuery, DistanceQuery, ShoppingRequest
from .replies import compose_reply, reply_problems

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000
MAX_STEPS = 8

SYSTEM_PROMPT = """\
You are the ordering assistant for FarmFind, which compares farm shops that sell \
milk, cream, butter, cheese and eggs.

Work out what the user wants to buy (products, quantities, units), where they \
are, and any preference (pickup or delivery, a pickup distance limit, a maximum \
number of vendors). Then use the tools:
- find_suppliers: which vendors stock the items, with unit prices.
- measure_distances: resolve the user's town and get distances to vendors.
- check_fulfillment: pickup, shipping and delivery options per vendor for that town.
- build_cart: the cheapest cart plus alternatives. It checks fulfillment for the \
location itself, so call it once you know the items and the town.

If the items or the town are missing, or a tool reports that the location is not \
recognised, ask the user one short question instead of guessing.

In your final reply, recommend the best cart from build_cart, say how each \
vendor's order is fulfilled, state the total, and mention the alternatives briefly. \
Quote dollar amounts only as they appear in tool results. Do not calculate new \
amounts such as differences or savings, and do not estimate a price yourself. \
Plain text only, no Markdown: no asterisks, headings or tables."""

class AgentTool(BaseModel):
    """One agent exposed to the model as a tool, named after the agent's action."""

    agent: str
    name: str
    description: str
    input_model: type[BaseModel]


# The CartAgent tool takes a ShoppingRequest: the agent's own input model also
# accepts a delivery result, which only the DeliveryAgent may supply.
TOOLS = [
    AgentTool(
        agent=CatalogAgent.name,
        name=CatalogAgent.action,
        description="List the vendors that stock the requested items, with stock and unit prices.",
        input_model=CatalogQuery,
    ),
    AgentTool(
        agent=DistanceAgent.name,
        name=DistanceAgent.action,
        description="Resolve a town name to coordinates and measure the distance to vendors. "
        "Reports when the town is not recognised.",
        input_model=DistanceQuery,
    ),
    AgentTool(
        agent=DeliveryAgent.name,
        name=DeliveryAgent.action,
        description="List the pickup, shipping and farm-truck delivery options each vendor "
        "offers for a town, with base fees and order minimums.",
        input_model=DeliveryQuery,
    ),
    AgentTool(
        agent=CartAgent.name,
        name=CartAgent.action,
        description="Build the cheapest cart for the items, plus alternatives. "
        "All prices, fees and totals come from this tool.",
        input_model=ShoppingRequest,
    ),
]


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_model.model_json_schema(),
        }
        for tool in TOOLS
    ]


class LLMCoordinator:
    name = "llm"

    def __init__(
        self,
        client: Any,
        model: str,
        fallback: Coordinator,
        max_steps: int = MAX_STEPS,
        api_errors: tuple[type[Exception], ...] = (),
    ):
        self.client = client
        self.model = model
        self.fallback = fallback
        self.max_steps = max_steps
        self.api_errors = api_errors
        self._tools = {tool.name: tool for tool in TOOLS}

    def handle(self, message: str, state: ConversationState, bus: MessageBus) -> ChatTurn:
        messages: list[dict[str, Any]] = [*state.history, {"role": "user", "content": message}]
        tools = tool_definitions()
        cart: CartResult | None = None
        corrected = False

        for _ in range(self.max_steps):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=messages,
                )
            except self.api_errors as exc:
                return self._fall_back(f"API error: {type(exc).__name__}", message, state, bus)
            if response.stop_reason in {"refusal", "max_tokens"}:
                return self._fall_back(f"model stopped: {response.stop_reason}", message, state, bus)

            messages.append({"role": "assistant", "content": response.content})
            tool_calls = [block for block in response.content if block.type == "tool_use"]
            if tool_calls:
                results = []
                for call in tool_calls:
                    result = self._call_agent(call, bus)
                    if isinstance(result, CartResult):
                        cart = result
                        state.request = ShoppingRequest.model_validate(call.input)
                    results.append(_tool_result(call.id, result))
                messages.append({"role": "user", "content": results})
                continue

            reply = "\n".join(block.text for block in response.content if block.type == "text").strip()
            if not reply:
                return self._fall_back("model returned no text", message, state, bus)
            problems = self._check_reply(reply, cart, bus)
            if not problems:
                return self._finish(reply, cart, state)
            if corrected:
                break
            corrected = True
            messages.append({"role": "user", "content": correction_request(problems)})
        else:
            return self._fall_back(f"no answer within {self.max_steps} steps", message, state, bus)

        return self._templated(cart, message, state, bus)

    def _check_reply(self, reply: str, cart: CartResult | None, bus: MessageBus) -> list[str]:
        """Check the model's reply against the cart result and note the outcome in the trace."""
        problems = reply_problems(reply, cart)
        if problems:
            bus.log.record(
                self.name, self.name, "validate_reply", reply, "error", error_message="; ".join(problems)
            )
        elif cart is not None:
            bus.log.record(
                self.name, self.name, "validate_reply", result_summary="amounts match the cart result"
            )
        return problems

    def _templated(
        self, cart: CartResult | None, message: str, state: ConversationState, bus: MessageBus
    ) -> ChatTurn:
        """The model could not produce a reply that matches the cart, so the
        deterministic template writes it instead."""
        if cart is None:
            return self._fall_back("reply quoted prices without a cart", message, state, bus)
        return self._finish(compose_reply(state.request, cart, []), cart, state)

    def _call_agent(self, call: Any, bus: MessageBus) -> BaseModel | str:
        """Run one tool call. A string result is an error message for the model."""
        tool = self._tools.get(call.name)
        if tool is None or not isinstance(call.input, dict):
            error = f"Invalid tool call {call.name!r}."
            bus.log.record(self.name, call.name, "unknown", str(call.input), "error", error_message=error)
            return error
        # Only the fields the tool declares are passed on to the agent.
        payload = {key: value for key, value in call.input.items() if key in tool.input_model.model_fields}
        try:
            return bus.request(self.name, tool.agent, payload)
        except AgentRequestError as exc:
            return str(exc)

    def _finish(self, reply: str, cart: CartResult | None, state: ConversationState) -> ChatTurn:
        return ChatTurn(
            reply=reply,
            follow_up_question=reply if cart is None and "?" in reply else None,
            recommendation=cart,
            request=state.request,
            coordinator=self.name,
        )

    def _fall_back(
        self, reason: str, message: str, state: ConversationState, bus: MessageBus
    ) -> ChatTurn:
        bus.log.record(self.name, self.fallback.name, "fall_back", result_summary=reason)
        return self.fallback.handle(message, state, bus)


def correction_request(problems: list[str]) -> str:
    return (
        "Your reply was not sent because: "
        + "; ".join(problems)
        + ". Rewrite it using only amounts that appear in the build_cart result."
    )


def _tool_result(tool_use_id: str, result: BaseModel | str) -> dict[str, Any]:
    if isinstance(result, str):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": result, "is_error": True}
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": result.model_dump_json()}


def create_llm_coordinator(fallback: Coordinator) -> LLMCoordinator:
    """Build the coordinator from the environment. Raises ImportError when the
    optional `anthropic` package is not installed."""
    import anthropic

    return LLMCoordinator(
        client=anthropic.Anthropic(),
        model=os.environ.get("FARMFIND_MODEL") or DEFAULT_MODEL,
        fallback=fallback,
        api_errors=(anthropic.APIConnectionError, anthropic.APIStatusError),
    )
