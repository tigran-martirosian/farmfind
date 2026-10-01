"""Capability tests for generic product-offer understanding."""
from __future__ import annotations

from app.fetcher.package_parser import parse_price_fact, parse_quantity, parse_savings, understand_offer


def test_arbitrary_gallon_bundle_count_is_not_hardcoded():
    fact = parse_quantity("10 gallon bundle milk - Save $12", "cow_milk")

    assert fact.total_equivalent_quantity == 10
    assert fact.total_equivalent_unit == "gallon"
    assert fact.is_bundle_or_pack is True
    assert fact.display_text == "10 x 1 gallon"


def test_arbitrary_dozen_case_count_is_not_hardcoded():
    fact = parse_quantity("Case of 20 dozen pastured eggs", "eggs")

    assert fact.total_equivalent_quantity == 240
    assert fact.total_equivalent_unit == "count"
    assert fact.item_count == 240
    assert fact.is_bundle_or_pack is True
    assert fact.display_text == "20 dozen"


def test_arbitrary_count_pack_and_half_gallon_composition():
    count = parse_quantity("24 count egg pack", "eggs")
    half_gallons = parse_quantity("3 half-gallon pack milk", "cow_milk")

    assert count.total_equivalent_quantity == 24
    assert count.total_equivalent_unit == "count"
    assert half_gallons.item_count == 3
    assert half_gallons.unit_quantity == 0.5
    assert half_gallons.total_equivalent_quantity == 1.5
    assert half_gallons.total_equivalent_unit == "gallon"


def test_pack_rows_preserve_inner_unit_semantics():
    quart = parse_quantity("6 Pk. Quart Milk", "cow_milk")
    pint = parse_quantity("6 Pk Pint Milk", "cow_milk")
    shredded = parse_quantity("4 Pk Shredded Mozzarella Cheese Resealable 1lb. bag", "cheese")
    five_pack = parse_quantity("5 Pk. 1 lb. Cheese Bundle 5 lb. Blocks Assorted Variety", "cheese")

    assert quart.total_equivalent_quantity == 1.5
    assert quart.total_equivalent_unit == "gallon"
    assert pint.total_equivalent_quantity == 0.75
    assert pint.total_equivalent_unit == "gallon"
    assert shredded.total_equivalent_quantity == 4
    assert shredded.total_equivalent_unit == "lb"
    assert five_pack.total_equivalent_quantity == 5
    assert five_pack.total_equivalent_unit == "lb"


def test_savings_and_regular_sale_prices_are_evidence_based():
    price = parse_price_fact("Regular $60.00 sale $48.00")
    savings = parse_savings("Regular $60.00 sale $48.00", price)
    explicit = parse_savings("Buy 5 save $12")

    assert price.regular_price == 60
    assert price.current_price == 48
    assert savings.savings_amount == 12
    assert savings.savings_type == "computed_regular_minus_sale"
    assert explicit.savings_amount == 12
    assert explicit.savings_type == "explicit_text"


def test_orderable_offer_marks_missing_quantity_precisely():
    offer = understand_offer(
        parent_product_name="Large Eggs",
        option_label="Large Eggs",
        raw_offer_text="Large Eggs $7.00 Add to Cart",
        product_type_hint="eggs",
        offer_name="Large Eggs",
        stock_signal="in_stock",
    )

    assert "no_quantity_evidence" in offer.incomplete_flags
    assert offer.price.current_price == 7


def test_quantity_parser_strips_price_noise_before_package_parsing():
    gallon = parse_quantity("gallon container: glass chilled?: yes $15.00", "cow_milk")
    price_then_bundle = parse_quantity("$11.45 4 gallon", "cow_milk")
    price_multiply_bundle = parse_quantity("$12.82 x 4 gallon", "cow_milk")
    current_price_only = parse_quantity("current price is $26.00", "cow_milk")
    original_price_only = parse_quantity("original price was $30.00", "cow_milk")

    assert gallon.total_equivalent_quantity == 1
    assert gallon.total_equivalent_unit == "gallon"
    assert price_then_bundle.total_equivalent_quantity == 4
    assert price_then_bundle.total_equivalent_unit == "gallon"
    assert price_multiply_bundle.total_equivalent_quantity == 4
    assert price_multiply_bundle.total_equivalent_unit == "gallon"
    assert current_price_only.total_equivalent_quantity is None
    assert original_price_only.total_equivalent_quantity is None
