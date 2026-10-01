"""Deterministic display formatting (no 'unknown' noise)."""
from __future__ import annotations

import pytest

from app.display import (
    clean_attribute_label,
    clean_base_name,
    display_name,
    filter_attributes,
    friendly_package_label,
    human_package,
    visible_tags,
)


@pytest.mark.parametrize(
    "quantity,unit,expected",
    [
        (1.0, "gallon", "1 gallon"),
        (0.5, "gallon", "1/2 gallon"),
        (0.25, "gallon", "quart"),
        (0.125, "gallon", "pint"),
        (4.0, "gallon", "4 gallon"),
        (1.0, "pint", "1 pint"),
        (2.0, "pint", "2 pint"),
        (1.0, "lb", "1 lb"),
        (0.5, "lb", "8 oz"),
        (5.0, "lb", "5 lb"),
        (12.0, "count", "1 dozen"),
        (36.0, "count", "3 dozen"),
        (96.0, "count", "case of 8 dozen"),
    ],
)
def test_human_package(quantity, unit, expected):
    assert human_package(quantity, unit) == expected


def test_display_name_examples_are_clean():
    assert (
        display_name("Milk", quantity=1, unit="gallon", packaging="plastic", storage_state="refrigerated")
        == "Milk — 1 gal, plastic, chilled"
    )
    assert (
        display_name("Milk", quantity=0.5, unit="gallon", packaging="plastic", storage_state="fresh")
        == "Milk — 1/2 gal, plastic, not chilled"
    )
    assert display_name("Cream", quantity=1, unit="pint", packaging="glass") == "Cream — pint, glass"
    assert display_name("Cream", quantity=2, unit="pint", packaging="glass") == "Cream — quart, glass"
    assert display_name("Cream", quantity=4, unit="pint", packaging="glass") == "Cream — half gallon, glass"
    assert display_name("Butter", quantity=1, unit="lb") == "Butter — 1 lb"
    assert display_name("Farm Eggs", quantity=96, unit="count") == "Farm Eggs — case of 8 dozen"


def test_friendly_package_label_examples():
    assert friendly_package_label(1, "gallon") == "1 gal"
    assert friendly_package_label(0.5, "gallon") == "1/2 gal"
    assert friendly_package_label(1, "pint") == "pint"
    assert friendly_package_label(2, "pint", product_name="Cream") == "quart"
    assert friendly_package_label(4, "pint", product_name="Cream") == "half gallon"


def test_display_name_strips_attribute_noise_and_size_tokens():
    ugly = "Milk Size: Half Gallon, Container: Glass, Chilled?: Yes"
    assert clean_base_name(ugly) == "Milk"
    assert clean_base_name("8 oz Cheddar Cheese") == "Cheddar Cheese"
    assert clean_base_name("1 GALLON Milk") == "Milk"


def test_display_name_never_shows_unknown():
    # storage/packaging unknown -> no ", unknown chilled" / ", unknown"
    name = display_name("Vendor B Cheddar Cheese", quantity=0.5, unit="lb", packaging=None, storage_state="unknown")
    assert name == "Vendor B Cheddar Cheese — 8 oz"
    assert "unknown" not in name


def test_visible_tags_drop_unknown_values():
    assert visible_tags(packaging=None, storage_state="unknown") == []
    assert visible_tags(packaging="glass", storage_state="refrigerated") == [
        "glass",
        "chilled",
    ]
    assert visible_tags(attributes=[{"name": "attribute_chilled", "value": "No"}]) == ["not chilled"]
    assert visible_tags(attributes=[{"name": "attribute_use-rennet", "value": "Yes (Like Cheddar)"}]) == [
        "cheddar-style rennet"
    ]
    assert visible_tags(attributes=[{"name": "attribute_fat-content", "value": "Full Fat"}]) == ["full fat"]


def test_attribute_label_cleanup():
    assert clean_attribute_label("attribute_pa_size") == "Size"
    assert clean_attribute_label("attribute_pa_container") == "Container"
    assert clean_attribute_label("attribute_chilled") == "Chilled"
    assert clean_attribute_label("attribute_use-rennet") == "Rennet"
    assert clean_attribute_label("attribute_fat-content") == "Fat content"


def test_filter_attributes_keeps_only_exposed_meaningful_values():
    attrs = [
        {"name": "Size", "value": "Half Gallon"},        # redundant -> dropped
        {"name": "Container", "value": "Glass"},          # redundant -> dropped
        {"name": "Option", "value": "1 GALLON Milk"}, # redundant -> dropped
        {"name": "Rennet", "value": "Yes"},               # exposed -> kept
        {"name": "Fat content", "value": "Full Fat"},     # exposed -> kept
        {"name": "Chilled?", "value": "unknown"},         # unknown -> dropped
    ]
    kept = filter_attributes(attrs)
    assert kept == [
        {"label": "Rennet", "value": "Yes"},
        {"label": "Fat content", "value": "Full Fat"},
    ]


def test_cheese_display_uses_source_exposed_attributes_without_raw_keys():
    attrs = [
        {"name": "attribute_pa_size", "value": "Pint"},
        {"name": "attribute_pa_container", "value": "Glass"},
        {"name": "attribute_use-rennet", "value": "No"},
        {"name": "attribute_fat-content", "value": "Full Fat"},
    ]
    name = display_name("Cheese", packaging="glass", attributes=attrs)
    assert name == "Cheese — pint glass, full fat, no rennet"
    tags = visible_tags(packaging="glass", attributes=attrs)
    assert tags == ["glass", "pint", "full fat", "no rennet"]
    assert not any("attribute_" in item for item in [name, *tags])


def test_grazecart_single_option_exposes_no_extra_attributes():
    # A GrazeCart variant only carries an "Option" pseudo-attribute -> no chips.
    assert filter_attributes([{"name": "Option", "value": "1 lb Vendor B Gouda Cheese"}]) == []
