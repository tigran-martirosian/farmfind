"""Tests for Vendor C package parsing, colostrum/whey, sheep milk, tags."""
from __future__ import annotations

import pytest

from app.fetcher.import_candidates import _refreshed_candidate
from app.schemas import ImportedProductCandidate


def _refresh(name, *, vendor="vendor_c", page="dairy", price=8.0,
             product_type=None, packaging=None, storage_state="unknown"):
    c = ImportedProductCandidate(
        import_id="x", vendor_id=vendor, source_page_id=page, name=name, raw_text=name,
        product_type=product_type, detected_price=price, packaging=packaging,
        storage_state=storage_state, stock_status="in_stock", needs_review=False,
    )
    return _refreshed_candidate(c)


# --- Vendor C Qt/Pt parsing --------------------------------------------


@pytest.mark.parametrize(
    "name,qty,unit,disp",
    [
        ("Sheep Milk, 1 Qt. (Glass)", 0.25, "gallon", "1 quart"),
        ("Milk, 1 Qt. (Plastic)", 0.25, "gallon", "1 quart"),
        ("Milk, 1 Qt. (Glass)", 0.25, "gallon", "1 quart"),
        ("Light Cream, 1 Pt. (Glass)", 1.0, "pint", "1 pint"),
        ("Heavy Cream, 1 Pt. (Glass)", 1.0, "pint", "1 pint"),
        ("Unsalted Cottage Cheese, 1 Pt. (Plastic)", 1.0, "pint", "1 pint"),
        ("Unsalted Cottage Cheese, 1 Pt. (Glass)", 1.0, "pint", "1 pint"),
        ("Goat Cottage Cheese, Unsalted, Pt. (Glass)", 1.0, "pint", "1 pint"),
    ],
)
def test_vendor_c_qt_pt_package_parsing(name, qty, unit, disp):
    r = _refresh(name)
    assert r.detected_package_size == qty
    assert r.detected_unit.value == unit
    assert r.package_display == disp


# --- sheep milk clean ---------------------------------------------


def test_sheep_milk_is_ready_without_generic_warnings():
    r = _refresh("Sheep's Milk 1 gallon plastic", vendor="vendor_a", page="sheep_milk",
                 product_type="sheep_milk", price=15.0, packaging="plastic")
    assert r.product_type.value == "sheep_milk"
    assert r.species == "sheep"
    assert r.needs_review is False
    assert r.eligibility_reason is None
    assert "Parser confidence is below review threshold." not in r.warnings


# --- colostrum / whey ---------------------------------------------


def test_colostrum_gets_type_group_and_tag():
    r = _refresh("Cow Colostrum: quart in plastic", vendor="vendor_d", page="dairy_page", price=14.0)
    assert r.relevant_type == "colostrum"
    assert r.species == "cow"
    assert r.detected_package_size == 2.0 and r.detected_unit.value == "pint"
    assert r.package_display == "1 quart"
    assert "colostrum" in r.display_tags
    assert r.eligibility_status == "needs_review"


def test_colostrum_pint_and_frozen_rules():
    pint = _refresh("Cow Colostrum: pint in plastic", vendor="vendor_d", page="dairy_page")
    assert pint.package_display == "1 pint"
    frozen = _refresh("Colostrum (Regular)-Frozen", vendor="vendor_d", page="dairy_page")
    assert frozen.eligibility_status == "excluded"
    assert "colostrum" in frozen.display_tags


def test_whey_gets_type_group_and_tag():
    r = _refresh("Whey: quart in plastic", vendor="vendor_d", page="dairy_page")
    assert r.relevant_type == "whey"
    assert "whey" in r.display_tags
    assert r.eligibility_status == "needs_review"


# --- details do not duplicate package -----------------------------


def test_details_do_not_duplicate_package_tokens():
    milk = _refresh("Milk 1 gallon plastic", vendor="vendor_a", page="milk",
                    product_type="cow_milk", packaging="plastic")
    assert milk.display_tags == ["plastic"]
    chilled = _refresh("Milk quart glass chilled", vendor="vendor_a", page="milk",
                       product_type="cow_milk", packaging="glass", storage_state="refrigerated")
    assert "glass" in chilled.display_tags and "chilled" in chilled.display_tags
    assert not any(t in {"gallon", "1 gallon", "quart", "1 quart", "pint"} for t in chilled.display_tags)
