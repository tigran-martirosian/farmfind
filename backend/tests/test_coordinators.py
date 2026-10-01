"""Tests for both coordinators. The model-driven one gets a scripted client, so no network."""
from __future__ import annotations

from app.agents.coordinator import ConversationState, RuleBasedCoordinator
from app.agents.geo import GazetteerGeocoder
from app.agents.llm_coordinator import LLMCoordinator, tool_definitions
from app.agents.messages import CartQuery
from app.agents.replies import compose_reply, reply_problems
from tests.agent_helpers import (
    EXAMPLE_ITEMS,
    EXAMPLE_QUESTION,
    ScriptedClient,
    demo_bus,
    response,
    text,
    tool_use,
    trace_of,
)

BUILD_CART = tool_use("build_cart", {"items": EXAMPLE_ITEMS, "location": "Exampleville"})
GOOD_REPLY = "Order everything from Example Farm G and pick it up at the farm gate: $74.54 in total."


def _state() -> ConversationState:
    return ConversationState(conversation_id="c1")


def _rule_based() -> RuleBasedCoordinator:
    return RuleBasedCoordinator(GazetteerGeocoder.from_file())


def _llm(client, **kwargs) -> LLMCoordinator:
    return LLMCoordinator(client=client, model="test-model", fallback=_rule_based(), **kwargs)



def test_rule_based_runs_the_fixed_pipeline_and_writes_the_answer():
    bus = demo_bus()

    turn = _rule_based().handle(EXAMPLE_QUESTION, _state(), bus)

    assert turn.coordinator == "rule_based"
    assert turn.follow_up_question is None
    assert turn.recommendation.total == 74.54
    assert turn.reply.startswith("Best order for Exampleville: $74.54 total ($66.50 items + $8.04 fulfillment).")
    assert "Example Farm G, pickup at Samplebury Farm Gate (6 mi)" in turn.reply
    assert "Alternatives:\n- $92.75 from Example Farm E (pickup)" in turn.reply
    assert reply_problems(turn.reply, turn.recommendation) == []
    assert trace_of(bus) == [
        ("user", "rule_based", "parse_request"),
        ("rule_based", "catalog_agent", "find_suppliers"),
        ("rule_based", "distance_agent", "measure_distances"),
        ("rule_based", "delivery_agent", "check_fulfillment"),
        ("delivery_agent", "distance_agent", "measure_distances"),
        ("rule_based", "cart_agent", "build_cart"),
    ]


def test_rule_based_asks_one_question_when_the_location_is_missing_then_continues():
    coordinator, state = _rule_based(), _state()

    first = coordinator.handle("2 gallons of milk and 1 lb of butter", state, demo_bus())

    assert first.recommendation is None
    assert first.follow_up_question == first.reply
    assert first.reply.count("?") == 1
    assert "Which town" in first.reply

    second = coordinator.handle("Exampleville", state, demo_bus())

    assert second.follow_up_question is None
    assert [item.product.value for item in second.request.items] == ["cow_milk", "butter"]
    assert second.request.location == "Exampleville"
    assert second.recommendation.feasible is True


def test_rule_based_asks_again_for_an_unknown_town_and_lists_the_known_ones():
    coordinator, state = _rule_based(), _state()
    bus = demo_bus()

    turn = coordinator.handle("1 gallon of milk. I live in Nowhereville.", state, bus)

    assert turn.recommendation is None
    assert "I don't recognise 'Nowhereville'" in turn.follow_up_question
    assert "Exampleville, Samplebury" in turn.follow_up_question
    assert ("rule_based", "cart_agent", "build_cart") not in trace_of(bus)

    assert coordinator.handle("Samplebury", state, demo_bus()).recommendation.feasible is True


def test_rule_based_asks_for_items_and_names_what_it_cannot_match():
    turn = _rule_based().handle("2 lb of yogurt, I live in Exampleville", _state(), demo_bus())

    assert turn.recommendation is None
    assert turn.follow_up_question.startswith("I could not match yogurt")
    assert "What would you like to order?" in turn.follow_up_question


def test_rule_based_mentions_unknown_items_next_to_the_cart():
    turn = _rule_based().handle("1 gallon of milk and 2 lb of yogurt in Exampleville", _state(), demo_bus())

    assert turn.recommendation.feasible is True
    assert turn.reply.endswith("I could not match yogurt; FarmFind covers milk, cream, butter, cheese and eggs.")



def test_reply_validation_accepts_only_amounts_from_the_cart_result():
    cart = demo_bus().request("test", "cart_agent", CartQuery(items=EXAMPLE_ITEMS, location="Exampleville"))

    assert reply_problems(compose_reply(CartQuery(items=EXAMPLE_ITEMS), cart, []), cart) == []
    assert reply_problems("That comes to $74.54, with $8.04 for the trip.", cart) == []
    assert reply_problems("That comes to $61.23.", cart) == [
        "$61.23 does not appear in the cart result",
        "the reply does not state the cart total $74.54",
    ]
    assert reply_problems("Milk is about $5 a gallon.", None) == ["$5 does not appear in the cart result"]
    assert reply_problems("Which town are you in?", None) == []



def test_reply_lists_cart_warnings():
    cart = demo_bus().request("test", "cart_agent", CartQuery(items=EXAMPLE_ITEMS, location="Exampleville"))
    warned = cart.cart.model_copy(update={"warnings": ["No valid cart could satisfy max_vendors=1."]})
    cart = cart.model_copy(update={"cart": warned})

    reply = compose_reply(CartQuery(items=EXAMPLE_ITEMS), cart, [])

    assert "No valid cart could satisfy max_vendors=1." in reply.splitlines()


def test_llm_tools_are_the_four_agents_and_hide_the_delivery_field():
    tools = {tool["name"]: tool["input_schema"] for tool in tool_definitions()}

    assert list(tools) == ["find_suppliers", "measure_distances", "check_fulfillment", "build_cart"]
    assert set(tools["build_cart"]["properties"]) == {
        "items",
        "location",
        "fulfillment",
        "max_pickup_miles",
        "max_vendors",
    }


def test_llm_runs_the_models_tool_calls_and_returns_its_validated_reply():
    client = ScriptedClient(
        response(tool_use("find_suppliers", {"items": EXAMPLE_ITEMS}, "call_a")),
        response(text("Checking the cart."), BUILD_CART),
        response(text(GOOD_REPLY)),
    )
    bus, state = demo_bus(), _state()

    turn = _llm(client).handle(EXAMPLE_QUESTION, state, bus)

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
    assert len(client.requests) == 3
    assert client.requests[0]["model"] == "test-model"
    # Each tool result goes back in a user message that answers the call id.
    second = client.requests[1]["messages"]
    assert second[-2]["role"] == "assistant"
    assert second[-1]["content"][0]["tool_use_id"] == "call_a"
    assert '"catalog_mode":"demo_only"' in second[-1]["content"][0]["content"]


def test_llm_sends_earlier_turns_as_history():
    state = _state()
    state.history = [
        {"role": "user", "content": "2 gallons of milk"},
        {"role": "assistant", "content": "Which town are you in?"},
    ]
    client = ScriptedClient(response(text("Which products would you like?")))

    turn = _llm(client).handle("Exampleville", state, demo_bus())

    assert [m["content"] for m in client.requests[0]["messages"]] == [
        "2 gallons of milk",
        "Which town are you in?",
        "Exampleville",
    ]
    assert turn.follow_up_question == "Which products would you like?"
    assert turn.recommendation is None


def test_llm_stops_at_the_step_limit_and_hands_over_to_the_rule_based_coordinator():
    client = ScriptedClient(response(tool_use("find_suppliers", {"items": EXAMPLE_ITEMS})))
    bus = demo_bus()

    turn = _llm(client, max_steps=3).handle(EXAMPLE_QUESTION, _state(), bus)

    assert len(client.requests) == 3
    assert turn.coordinator == "rule_based"
    assert turn.recommendation.total == 74.54
    fall_back = next(entry for entry in bus.log.entries if entry.action == "fall_back")
    assert (fall_back.sender, fall_back.target) == ("llm", "rule_based")
    assert fall_back.result_summary == "no answer within 3 steps"


def test_llm_returns_invalid_tool_calls_to_the_model_as_errors():
    client = ScriptedClient(
        response(
            tool_use("build_cart", {"items": [{"product": "yogurt", "quantity": 1, "unit": "lb"}]}, "bad_input"),
            tool_use("place_order", {"confirm": True}, "bad_tool"),
        ),
        response(text("I can only help with milk, cream, butter, cheese and eggs. What would you like?")),
    )
    bus = demo_bus()

    turn = _llm(client).handle("1 lb of yogurt in Exampleville", _state(), bus)

    results = client.requests[1]["messages"][-1]["content"]
    assert [result["tool_use_id"] for result in results] == ["bad_input", "bad_tool"]
    assert all(result["is_error"] for result in results)
    assert "items.0.product" in results[0]["content"]
    assert results[1]["content"] == "Invalid tool call 'place_order'."
    assert [entry.result_status for entry in bus.log.entries] == ["error", "error"]
    assert turn.coordinator == "llm"
    assert turn.recommendation is None
    assert turn.follow_up_question == turn.reply


def test_llm_ignores_a_delivery_result_supplied_by_the_model():
    forged = {"location": "Exampleville", "recognised": True, "vendors": []}
    client = ScriptedClient(
        response(tool_use("build_cart", {"items": EXAMPLE_ITEMS, "location": "Exampleville", "delivery": forged})),
        response(text(GOOD_REPLY)),
    )
    bus = demo_bus()

    turn = _llm(client).handle(EXAMPLE_QUESTION, _state(), bus)

    assert ("cart_agent", "delivery_agent", "check_fulfillment") in trace_of(bus)
    assert turn.recommendation.total == 74.54


def test_llm_reply_with_a_wrong_total_is_sent_back_once_and_then_accepted():
    client = ScriptedClient(
        response(BUILD_CART),
        response(text("Your best order costs $61.23 from Example Farm G.")),
        response(text(GOOD_REPLY)),
    )
    bus = demo_bus()

    turn = _llm(client).handle(EXAMPLE_QUESTION, _state(), bus)

    correction = client.requests[2]["messages"][-1]
    assert correction["role"] == "user"
    assert "$61.23 does not appear in the cart result" in correction["content"]
    assert turn.reply == GOOD_REPLY
    assert [e.result_status for e in bus.log.entries if e.action == "validate_reply"] == ["error", "ok"]


def test_llm_reply_that_stays_wrong_is_replaced_by_the_templated_answer():
    client = ScriptedClient(
        response(BUILD_CART),
        response(text("Your best order costs $61.23 from Example Farm G.")),
        response(text("Sorry, it is $62.34.")),
    )
    bus = demo_bus()

    turn = _llm(client).handle(EXAMPLE_QUESTION, _state(), bus)

    assert len(client.requests) == 3
    assert turn.coordinator == "llm"
    assert turn.reply.startswith("Best order for Exampleville: $74.54 total")
    assert "$61.23" not in turn.reply and "$62.34" not in turn.reply
    assert [e.result_status for e in bus.log.entries if e.action == "validate_reply"] == ["error", "error"]


def test_llm_quoting_prices_without_a_cart_falls_back():
    client = ScriptedClient(response(text("Milk is about $5 a gallon, so roughly $10.")))

    turn = _llm(client).handle(EXAMPLE_QUESTION, _state(), demo_bus())

    assert turn.coordinator == "rule_based"
    assert turn.recommendation.total == 74.54


def test_llm_api_errors_and_refusals_fall_back():
    class ApiDown(Exception):
        pass

    bus = demo_bus()
    turn = _llm(ScriptedClient(ApiDown()), api_errors=(ApiDown,)).handle(EXAMPLE_QUESTION, _state(), bus)
    assert turn.coordinator == "rule_based"
    assert bus.log.entries[0].result_summary == "API error: ApiDown"

    bus = demo_bus()
    refused = ScriptedClient(response(stop_reason="refusal"))
    assert _llm(refused).handle(EXAMPLE_QUESTION, _state(), bus).coordinator == "rule_based"
    assert bus.log.entries[0].result_summary == "model stopped: refusal"
