"""Tests for the rule-based chat message parser."""
from __future__ import annotations

import pytest

from app.agents.request_parser import parse_message
from tests.agent_helpers import EXAMPLE_QUESTION

TOWNS = ["Exampleville", "Samplebury", "Demoton"]


def _items(message: str, **kwargs) -> list[tuple[str, float, str]]:
    parsed = parse_message(message, **kwargs)
    return [(item.product.value, item.quantity, item.unit.value) for item in parsed.items]


@pytest.mark.parametrize(
    "message, expected",
    [
        (EXAMPLE_QUESTION, [("cow_milk", 2, "gallon"), ("butter", 2, "lb"), ("eggs", 3, "dozen")]),
        ("Two gallons of milk and a dozen eggs please", [("cow_milk", 2, "gallon"), ("eggs", 1, "dozen")]),
        (
            "12 eggs, half a gallon of sheep milk, 8 oz cheese",
            [("eggs", 12, "count"), ("sheep_milk", 0.5, "gallon"), ("cheese", 8, "oz")],
        ),
        ("1.5 lbs butter plus 2 quarts of cream", [("butter", 1.5, "lb"), ("cream", 2, "quart")]),
        ("can I get 3 half gallons of whole milk?", [("cow_milk", 3, "half_gallon")]),
        ("I need milk and butter", [("cow_milk", 1, "gallon"), ("butter", 1, "lb")]),
        ("What is the weather like?", []),
    ],
)
def test_items_quantities_and_units(message, expected):
    assert _items(message) == expected


def test_location_from_several_phrasings():
    assert parse_message(EXAMPLE_QUESTION).location == "Exampleville"
    assert parse_message("I'm in Samplebury and want 1 lb of cheese").location == "Samplebury"
    assert parse_message("i live in exampleville").location == "exampleville"
    assert parse_message("2 gallons of milk, Demoton", known_places=TOWNS).location == "Demoton"


def test_missing_location_is_left_empty():
    parsed = parse_message("2 gallons of milk and 1 lb of butter", known_places=TOWNS)

    assert parsed.location is None
    assert len(parsed.items) == 2


def test_unknown_town_is_kept_as_typed_for_the_distance_agent_to_reject():
    assert parse_message("I live in Nowhereville. 1 gallon of milk", known_places=TOWNS).location == "Nowhereville"


@pytest.mark.parametrize(
    "message, unknown, items",
    [
        ("2 lb of yogurt and 1 gallon of milk", ["yogurt"], [("cow_milk", 1, "gallon")]),
        ("1 quart of goat milk", ["goat milk"], []),
        ("2 pints of ice cream", ["ice cream"], []),
        ("1 quart of buttermilk", ["buttermilk"], []),
    ],
)
def test_unknown_items_are_reported_not_substituted(message, unknown, items):
    parsed = parse_message(message)

    assert parsed.unknown_items == unknown
    assert _items(message) == items


def test_preferences():
    parsed = parse_message("pickup only, within 10 miles, from one farm")
    assert (parsed.fulfillment, parsed.max_pickup_miles, parsed.max_vendors) == ("pickup", 10.0, 1)

    assert parse_message("I can't pick up, 2 gallons of milk").fulfillment == "delivery"
    shipped = parse_message("1 lb of butter delivered to Exampleville")
    assert (shipped.fulfillment, shipped.location) == ("delivery", "Exampleville")
    assert parse_message("pickup or delivery, whichever is cheaper").fulfillment == "any"
    assert parse_message(EXAMPLE_QUESTION).fulfillment is None


def test_a_bare_answer_counts_as_the_location_only_when_one_was_asked_for():
    assert parse_message("Nowhereville").location is None
    assert parse_message("Nowhereville", expecting_location=True).location == "Nowhereville"
    assert parse_message("in Mockford.", expecting_location=True).location == "Mockford"
