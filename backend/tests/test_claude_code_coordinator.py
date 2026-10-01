"""Tests for the coordinator that reaches the model through the Claude Code
sign-in. A stand-in replaces the Claude Agent SDK, so no sign-in and no network."""
from __future__ import annotations

from app.agents.claude_code_coordinator import ClaudeCodeCoordinator
from app.agents.coordinator import ConversationState, RuleBasedCoordinator
from app.agents.geo import GazetteerGeocoder
from app.agents.llm_coordinator import SYSTEM_PROMPT
from tests.agent_helpers import (
    EXAMPLE_ITEMS,
    EXAMPLE_QUESTION,
    FakeAgentSDK,
    FakeResult,
    demo_bus,
    trace_of,
)

BUILD_CART = ("build_cart", {"items": EXAMPLE_ITEMS, "location": "Exampleville"})
GOOD_REPLY = "Order everything from Example Farm G and pick it up at the farm gate: $74.54 in total."
WRONG_REPLY = "Your best order costs $61.23 from Example Farm G."


def _state() -> ConversationState:
    return ConversationState(conversation_id="c1")


def _coordinator(sdk: FakeAgentSDK, **kwargs) -> ClaudeCodeCoordinator:
    fallback = RuleBasedCoordinator(GazetteerGeocoder.from_file())
    return ClaudeCodeCoordinator(sdk=sdk, model="test-model", fallback=fallback, **kwargs)


def test_runs_the_models_tool_calls_through_the_bus_and_returns_its_validated_reply():
    sdk = FakeAgentSDK(("find_suppliers", {"items": EXAMPLE_ITEMS}), BUILD_CART, GOOD_REPLY)
    bus, state = demo_bus(), _state()

    turn = _coordinator(sdk).handle(EXAMPLE_QUESTION, state, bus)

    assert turn.coordinator == "llm"
    assert turn.reply == GOOD_REPLY
    assert turn.recommendation.total == 74.54
    assert state.request.location == "Exampleville"
    assert trace_of(bus) == [
        ("llm", "catalog_agent", "find_suppliers"),
        ("llm", "cart_agent", "build_cart"),
        ("cart_agent", "delivery_agent", "check_fulfillment"),
        ("delivery_agent", "distance_agent", "measure_distances"),
        ("llm", "llm", "validate_reply"),
    ]
    assert sdk.prompts == [EXAMPLE_QUESTION]
    assert '"catalog_mode":"demo_only"' in sdk.tool_results[0]["content"][0]["text"]


def test_the_model_gets_the_four_agents_and_none_of_claude_codes_own_tools():
    sdk = FakeAgentSDK("Which town are you in?")

    _coordinator(sdk).handle("2 gallons of milk", _state(), demo_bus())

    options = sdk.options
    assert options.tools == []
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.allowed_tools == [
        "mcp__farmfind__find_suppliers",
        "mcp__farmfind__measure_distances",
        "mcp__farmfind__check_fulfillment",
        "mcp__farmfind__build_cart",
    ]
    assert (options.model, options.max_turns, options.system_prompt) == ("test-model", 8, SYSTEM_PROMPT)
    build_cart = options.mcp_servers["farmfind"]["build_cart"]
    assert "delivery" not in build_cart.input_schema["properties"]


def test_earlier_turns_are_sent_as_a_transcript():
    state = _state()
    state.history = [
        {"role": "user", "content": "2 gallons of milk"},
        {"role": "assistant", "content": "Which town are you in?"},
    ]
    sdk = FakeAgentSDK("Which products would you like?")

    turn = _coordinator(sdk).handle("Exampleville", state, demo_bus())

    assert sdk.prompts == [
        "Earlier in this conversation:\n"
        "User: 2 gallons of milk\n"
        "Assistant: Which town are you in?\n\n"
        "New message from the user:\nExampleville"
    ]
    assert turn.follow_up_question == "Which products would you like?"
    assert turn.recommendation is None


def test_invalid_tool_input_goes_back_to_the_model_as_an_error():
    bad_input = ("build_cart", {"items": [{"product": "yogurt", "quantity": 1, "unit": "lb"}]})
    sdk = FakeAgentSDK(bad_input, "I can only help with milk, cream, butter, cheese and eggs. What would you like?")
    bus = demo_bus()

    turn = _coordinator(sdk).handle("1 lb of yogurt in Exampleville", _state(), bus)

    assert sdk.tool_results[0]["is_error"] is True
    assert "items.0.product" in sdk.tool_results[0]["content"][0]["text"]
    assert [entry.result_status for entry in bus.log.entries] == ["error"]
    assert turn.coordinator == "llm"
    assert turn.follow_up_question == turn.reply


def test_reply_with_a_wrong_total_is_sent_back_once_and_then_accepted():
    sdk = FakeAgentSDK(BUILD_CART, WRONG_REPLY, GOOD_REPLY)
    bus = demo_bus()

    turn = _coordinator(sdk).handle(EXAMPLE_QUESTION, _state(), bus)

    assert len(sdk.prompts) == 2
    assert "$61.23 does not appear in the cart result" in sdk.prompts[1]
    assert turn.reply == GOOD_REPLY
    assert [e.result_status for e in bus.log.entries if e.action == "validate_reply"] == ["error", "ok"]


def test_reply_that_stays_wrong_is_replaced_by_the_templated_answer():
    sdk = FakeAgentSDK(BUILD_CART, WRONG_REPLY, "Sorry, it is $62.34.")

    turn = _coordinator(sdk).handle(EXAMPLE_QUESTION, _state(), demo_bus())

    assert len(sdk.prompts) == 2
    assert turn.coordinator == "llm"
    assert turn.reply.startswith("Best order for Exampleville: $74.54 total")
    assert "$61.23" not in turn.reply and "$62.34" not in turn.reply


def test_quoting_prices_without_a_cart_falls_back():
    sdk = FakeAgentSDK("Milk is about $5 a gallon, so roughly $10.")

    turn = _coordinator(sdk).handle(EXAMPLE_QUESTION, _state(), demo_bus())

    assert turn.coordinator == "rule_based"
    assert turn.recommendation.total == 74.54


def test_step_limit_hands_over_to_the_rule_based_coordinator():
    sdk = FakeAgentSDK(*[("find_suppliers", {"items": EXAMPLE_ITEMS})] * 5)
    bus = demo_bus()

    turn = _coordinator(sdk, max_steps=3).handle(EXAMPLE_QUESTION, _state(), bus)

    assert len(sdk.tool_results) == 3
    assert turn.coordinator == "rule_based"
    assert turn.recommendation.total == 74.54
    fall_back = next(entry for entry in bus.log.entries if entry.action == "fall_back")
    assert (fall_back.sender, fall_back.target) == ("llm", "rule_based")
    assert fall_back.result_summary == "no answer within 3 steps"


def test_sdk_errors_and_error_results_fall_back():
    # Not signed in, or the Claude Code command is missing.
    bus = demo_bus()
    sdk = FakeAgentSDK(connect_error=FakeAgentSDK.ClaudeSDKError("not signed in"))
    assert _coordinator(sdk).handle(EXAMPLE_QUESTION, _state(), bus).coordinator == "rule_based"
    assert bus.log.entries[0].result_summary == "SDK error: ClaudeSDKError"

    # The SDK raises plain Exception for some failures, such as a control request timing out.
    bus = demo_bus()
    sdk = FakeAgentSDK(connect_error=Exception("Control request timeout: initialize"))
    assert _coordinator(sdk).handle(EXAMPLE_QUESTION, _state(), bus).coordinator == "rule_based"
    assert bus.log.entries[0].result_summary == "SDK error: Exception"

    bus = demo_bus()
    sdk = FakeAgentSDK(FakeResult("error_during_execution", is_error=True))
    assert _coordinator(sdk).handle(EXAMPLE_QUESTION, _state(), bus).coordinator == "rule_based"
    assert bus.log.entries[0].result_summary == "model stopped: error_during_execution"

    # A model run that hangs.
    bus = demo_bus()
    sdk = FakeAgentSDK(GOOD_REPLY, delay_s=5)
    turn = _coordinator(sdk, timeout_s=0.05).handle(EXAMPLE_QUESTION, _state(), bus)
    assert turn.coordinator == "rule_based"
    assert bus.log.entries[0].result_summary == "no answer within 0.05 seconds"
