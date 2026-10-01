"""Tests for page observation, variants, and imports."""
from __future__ import annotations

import json

from app.fetcher.import_candidates import (
    approve_import_candidate,
    create_import_candidates_from_variant_snapshot,
    edit_import_candidate,
    load_import_candidates,
    reject_import_candidate,
)
from app.fetcher.observation import observe_vendor_page
from app.fetcher.variant_capture import capture_product_variants, infer_product_fields
from app.fetcher.models import CapturedVariantOption


class FakeLocator:
    def __init__(self, text="", attr=None):
        self.text = text
        self.attr = attr
        self.first = self

    def inner_text(self, timeout=None):
        return self.text

    def get_attribute(self, name, timeout=None):
        return self.attr


class FakeProductPage:
    url = "https://example-farm-a.test/product/cow-milk/"

    def __init__(self, text: str, dom: dict, html: str = "<html></html>"):
        self.text = text
        self.dom = dom
        self.html = html

    def locator(self, selector):
        if selector == "body":
            return FakeLocator(self.text)
        if selector == "form.variations_form":
            return FakeLocator(attr=None)
        return FakeLocator("")

    def title(self):
        return "Milk"

    def content(self):
        return self.html

    def evaluate(self, script):
        return self.dom


def _vendor_a_dom():
    return {
        "headings": ["Milk"],
        "buttons": [{"text": "Add to Cart", "selector_hint": "button.single_add_to_cart_button"}],
        "links": [{"text": "Log Out", "href": "/logout", "selector_hint": "a"}],
        "dropdowns": [
            {"label": "Size", "name": "attribute_size", "selector_hint": "#size", "options": ["Gallon", "Half Gallon", "Quart"]},
            {"label": "Container", "name": "attribute_container", "selector_hint": "#container", "options": ["Glass", "Plastic"]},
            {"label": "Chilled?", "name": "attribute_chilled", "selector_hint": "#chilled", "options": ["Yes", "No"]},
        ],
        "inputs": [{"label": "Password", "name": "password", "type": "password", "selector_hint": "#password"}],
        "forms": [],
    }


def test_observation_detects_vendor_a_product_controls(tmp_path):
    page = FakeProductPage(
        "Milk $10.00 7 in stock. Size Container Chilled? Add to Cart",
        _vendor_a_dom(),
    )

    observation = observe_vendor_page(page, "vendor_a", "cow_milk", tmp_path)

    assert observation.detected_product_title == "Milk"
    assert observation.page_kind == "product_detail"
    assert "$10.00" in observation.detected_prices
    assert len(observation.detected_dropdowns) == 3
    assert observation.detected_buttons[0].text == "Add to Cart"
    assert (tmp_path / "observation.json").exists()
    assert "password" in [item.type for item in observation.detected_inputs]


def test_infer_vendor_a_milk_fields():
    attrs = [
        CapturedVariantOption(name="Size", value="Half Gallon"),
        CapturedVariantOption(name="Container", value="Glass"),
        CapturedVariantOption(name="Chilled?", value="Yes"),
    ]

    inferred = infer_product_fields("vendor_a", "cow_milk", "Whole Milk", attrs, "milk")

    assert inferred["inferred_product_type"] == "cow_milk"
    assert inferred["inferred_package_size"] == 0.5
    assert inferred["inferred_unit"] == "gallon"
    assert inferred["inferred_packaging"] == "glass"
    assert inferred["inferred_storage_state"] == "refrigerated"


def _infer(page_id, title, size, container=None, description=None):
    attrs = [CapturedVariantOption(name="Size", value=size)]
    if container:
        attrs.append(CapturedVariantOption(name="Container", value=container))
    return infer_product_fields("vendor_a", page_id, title, attrs, description or title)


def test_infer_cream_uses_pint_units():
    assert _infer("cream", "Cream", "Pint")["inferred_product_type"] == "cream"
    for size, pints in [("Pint", 1), ("Quart", 2), ("Half Gallon", 4), ("Gallon", 8)]:
        inferred = _infer("cream", "Cream", size)
        assert inferred["inferred_product_type"] == "cream"
        assert inferred["inferred_unit"] == "pint"
        assert inferred["inferred_package_size"] == pints
    

def test_infer_butter_uses_lb_units_and_generic_case_bundles():
    assert _infer("cow_butter", "Butter", "1 lb")["inferred_product_type"] == "butter"
    for size, lbs in [("1 lb", 1), ("1 Pound", 1), ("Half Pound", 0.5), ("8 oz", 0.5), ("Case of 12 lb", 12)]:
        inferred = _infer("cow_butter", "Butter", size)
        assert inferred["inferred_product_type"] == "butter"
        assert inferred["inferred_unit"] == "lb"
        assert inferred["inferred_package_size"] == lbs


def test_infer_farm_eggs_dozen_parsing_is_generic():
    for size, count in [("1 Dozen", 12), ("3 Dozen", 36), ("Case of 8 Dozen", 96), ("Case of 16 Dozen", 192)]:
        inferred = _infer("farm_eggs", "Farm Eggs", size)
        assert inferred["inferred_product_type"] == "eggs"
        assert inferred["inferred_unit"] == "count"
        assert inferred["inferred_package_size"] == count


def test_infer_cheese_fields():
    inferred = _infer("cheese", "Cheese", "8 oz", container="Glass")
    assert inferred["inferred_product_type"] == "cheese"
    assert inferred["inferred_unit"] == "lb"
    assert inferred["inferred_package_size"] == 0.5
    assert inferred["inferred_packaging"] == "glass"


def test_infer_cheese_does_not_invent_lb_from_volume_units():
    inferred = _infer("cheese", "Cheese", "Quart")
    # product_type must still be present even though size/unit cannot be inferred.
    assert inferred["inferred_product_type"] == "cheese"
    assert "inferred_package_size" not in inferred
    assert "inferred_unit" not in inferred


def test_infer_packaging_glass_and_plastic_maps():
    assert _infer("cream", "Cream", "Pint", container="Glass")["inferred_packaging"] == "glass"
    assert _infer("cream", "Cream", "Pint", container="Plastic")["inferred_packaging"] == "plastic"


def test_cream_candidate_with_price_is_not_needs_review():
    from app.schemas import ImportedProductCandidate
    from app.services import validate_import_candidate

    inferred = _infer("cream", "Cream", "Quart", container="Glass")
    candidate = validate_import_candidate(
        ImportedProductCandidate(
            import_id="vendor_a_cream_1",
            vendor_id="vendor_a",
            source_page_id="cream",
            name="Cream Quart Glass",
            product_type=inferred["inferred_product_type"],
            detected_price=8.5,
            detected_package_size=inferred["inferred_package_size"],
            detected_unit=inferred["inferred_unit"],
            packaging=inferred.get("inferred_packaging"),
            parser_confidence=0.9,
            needs_review=False,
        )
    )
    assert candidate.needs_review is False
    assert candidate.missing_fields == []


def test_cheese_candidate_missing_size_needs_review_but_has_product_type():
    from app.schemas import ImportedProductCandidate
    from app.services import validate_import_candidate

    inferred = _infer("cheese", "Cheese", "Quart")
    candidate = validate_import_candidate(
        ImportedProductCandidate(
            import_id="vendor_a_cheese_1",
            vendor_id="vendor_a",
            source_page_id="cheese",
            name="Cheese Quart",
            product_type=inferred["inferred_product_type"],
            detected_price=12.0,
            parser_confidence=0.6,
            needs_review=False,
        )
    )
    assert candidate.product_type.value == "cheese"
    assert candidate.needs_review is True
    assert "detected_package_size" in candidate.missing_fields
    assert "product_type" not in candidate.missing_fields


def test_dropdown_variant_capture_creates_combinations(tmp_path):
    page = FakeProductPage(
        "Milk $10.00 in stock. Size Container Chilled? Add to Cart",
        _vendor_a_dom(),
    )

    captured = capture_product_variants(page, "vendor_a", "cow_milk", tmp_path)

    assert captured.product_title == "Milk"
    assert len(captured.variants) == 12
    assert (tmp_path / "variant_snapshots.json").exists()
    assert {variant.inferred_packaging for variant in captured.variants} == {"glass", "plastic"}


def test_import_candidates_from_variant_snapshot_and_review(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "candidates")
    monkeypatch.setattr("app.fetcher.import_candidates.STAGED_PRODUCTS_PATH", tmp_path / "staged_products.json")
    page = FakeProductPage("Milk $10.00 in stock. Size Container Chilled?", _vendor_a_dom())
    capture_product_variants(page, "vendor_a", "cow_milk", tmp_path / "capture")
    path = tmp_path / "capture" / "variant_snapshots.json"

    candidates_path = create_import_candidates_from_variant_snapshot(path)
    candidates = load_import_candidates("vendor_a")

    assert candidates_path.exists()
    assert candidates[0].product_type.value == "cow_milk"
    assert candidates[0].detected_package_size in {1, 0.5, 0.25}
    assert candidates[0].needs_review is False

    edited = edit_import_candidate(candidates[0].import_id, {"needs_review": False})
    product = approve_import_candidate(edited.import_id)
    rejected = reject_import_candidate(candidates[1].import_id)

    assert product.source_type == "imported"
    assert rejected.review_status == "rejected"


def test_two_page_runs_do_not_overwrite_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "candidates")
    monkeypatch.setattr("app.fetcher.import_candidates.STAGED_PRODUCTS_PATH", tmp_path / "staged_products.json")
    milk = FakeProductPage("Milk $10.00 in stock. Size Container Chilled?", _vendor_a_dom())
    capture_product_variants(milk, "vendor_a", "cow_milk", tmp_path / "milk")
    cream = FakeProductPage("Cream $8.00 in stock. Size", _vendor_a_dom())
    capture_product_variants(cream, "vendor_a", "cream", tmp_path / "cream")

    milk_path = create_import_candidates_from_variant_snapshot(tmp_path / "milk" / "variant_snapshots.json")
    cream_path = create_import_candidates_from_variant_snapshot(tmp_path / "cream" / "variant_snapshots.json")

    assert milk_path.exists()
    assert cream_path.exists()
    assert milk_path != cream_path
    pages = {candidate.source_page_id for candidate in load_import_candidates("vendor_a")}
    assert {"cow_milk", "cream"} <= pages


def test_missing_price_candidate_needs_review(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "candidates")
    page = FakeProductPage("Milk in stock. Size Container Chilled?", _vendor_a_dom())
    capture_product_variants(page, "vendor_a", "cow_milk", tmp_path / "capture")

    create_import_candidates_from_variant_snapshot(tmp_path / "capture" / "variant_snapshots.json")
    candidates = load_import_candidates("vendor_a")

    assert candidates[0].needs_review is True
    assert "detected_price" in candidates[0].missing_fields


def test_embedded_woocommerce_variation_expands_chilled_wildcard(tmp_path):
    variations = json.dumps([
        {
            "variation_id": 5875,
            "attributes": {
                "attribute_pa_size": "quart",
                "attribute_pa_container": "glass",
                "attribute_chilled": "",
            },
            "availability_html": '<p class="stock in-stock">7 in stock</p>',
            "display_price": 5,
            "display_regular_price": 5,
            "is_in_stock": True,
        }
    ])
    page = FakeProductPage(
        "Milk $5.00 in stock. Size Container Chilled? Add to Cart",
        _vendor_a_dom(),
        html=f'<form class="variations_form cart" data-product_variations=\'{variations}\'></form>',
    )

    captured = capture_product_variants(page, "vendor_a", "cow_milk", tmp_path)

    assert len(captured.variants) == 2
    assert {variant.inferred_storage_state for variant in captured.variants} == {"refrigerated", "fresh"}
    assert all(variant.inferred_package_size == 0.25 for variant in captured.variants)
    assert (tmp_path / "variant_snapshots.json").exists()
