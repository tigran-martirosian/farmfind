"""Deterministic GrazeCart-style parsing (offline fixtures only)."""
from __future__ import annotations

import json

import pytest

from app.fetcher.grazecart import (
    GRAZECART_VENDORS,
    build_grazecart_captured_page,
    infer_product_type,
    infer_size,
    parse_category_cards,
    parse_detail_variants,
    variants_from_category_cards,
)
from app.fetcher.import_candidates import (
    create_import_candidates_from_variant_snapshot,
    load_import_candidates,
)
from app.fetcher.variant_capture import capture_product_variants


# --- Fixtures (inline offline HTML) ----------------------------------------


def _card(title, *, price=None, locked=False, href="/p/x", brand="Example Farm B"):
    price_html = (
        f'<span class="price">{price}</span>'
        if price
        else '<span class="price-locked">Sign up for pricing</span>'
        if locked
        else ""
    )
    return (
        f'<li class="product-card"><a class="product-link" href="{href}">'
        f'<h3 class="product-title">{title}</h3></a>'
        f'<span class="brand">{brand}</span><span class="badge">A2A2</span>{price_html}</li>'
    )


def _priced_card(title, *, price, button="Add to Order", subtitle="", brand="Example Farm B"):
    subtitle_html = f'<span class="product-subtitle">{subtitle}</span>' if subtitle else ""
    return (
        '<li class="product-card">'
        f'<h3 class="product-title">{title}</h3>{subtitle_html}'
        f'<span class="brand">{brand}</span><span class="price">{price}</span>'
        f'<button>{button}</button></li>'
    )


def _gift_card(title, *, price, button="Add to Order", subtitle="", brand="Example Farm D"):
    subtitle_html = f'<span class="product-subtitle">{subtitle}</span>' if subtitle else ""
    return (
        '<li class="product-card">'
        f'<h3 class="product-title">{title}</h3>{subtitle_html}'
        f'<span class="brand">{brand}</span><span>Gift Amount</span><span>{price}</span>'
        f'<button>{button}</button></li>'
    )


def _category(cards):
    return "<main class='product-grid'>" + "".join(cards) + "</main>"


def _option(label, price, reported=None):
    reported_html = f'<span class="reported-savings">Save {reported}</span>' if reported else ""
    return (
        '<label class="option-row"><input type="radio" name="opt">'
        f'<span class="option-label">{label}</span>'
        f'<span class="option-price">{price}</span>{reported_html}</label>'
    )


def _detail(title, base_price, options):
    return (
        f'<h1 class="product-title">{title}</h1>'
        f'<span class="price">{base_price}</span>'
        '<form class="add-to-order">'
        + "".join(options)
        + '<input type="number" class="quantity" value="1">'
        '<button class="add-to-order-button">Add to Order</button></form>'
    )


VENDOR_B_MILK_LOCKED = _category(
    [
        _card("1 GALLON Milk", locked=True),
        _card("1/2 Gallon GLASS Milk", locked=True),
        _card("Quart Milk", locked=True),
        _card("Pint Milk", locked=True),
    ]
)

VENDOR_B_MILK_DETAIL = _detail(
    "1 GALLON Milk",
    "$11.45",
    [
        _option("1 GALLON Milk", "$11.45"),
        _option("4 GALLON Bundle Milk", "$42.80", reported="$3.00"),
        _option("8 GALLON Bundle Milk", "$83.60", reported="$8.00"),
    ],
)

VENDOR_B_CHEESE_CATEGORY = _category(
    [
        _card("8 oz Cheddar Cheese", price="$8.50"),
        _card("1 lb Vendor B Gouda Cheese", price="$14.00"),
        _card("1 lb Vendor B Cheddar Cheese", price="$12.00"),
        _card("Grass-fed Ghee 8 oz", price="$16.00"),
    ]
)

VENDOR_C_DAIRY_LOCKED = _category(
    [
        _card("1 Gallon Milk", locked=True, brand="Example Farm C"),
        _card("Pint Cream", locked=True, brand="Example Farm C"),
    ]
)

VENDOR_C_DAIRY_DETAIL = _detail(
    "Half Gallon Sheep Milk",
    "$18.00",
    [_option("Half Gallon Sheep Milk", "$18.00")],
)

VENDOR_D_EGGS_DETAIL = _detail(
    "Soy-Free Large Eggs",
    "$7.00",
    [
        _option("1 Dozen Large Eggs", "$7.00"),
        _option("Case of 8 Dozen Large Eggs", "$52.00", reported="$4.00"),
    ],
)

VENDOR_D_DAIRY_LOCKED = _category(
    [
        _card("1 lb Butter", locked=True, brand="Example Farm D"),
        _card("Dairy Milk Gallon", locked=True, brand="Example Farm D"),
    ]
)

VENDOR_B_PRICED_CARDS = _category(
    [
        _priced_card("12 oz Colostrum (Regular)-Frozen", price="$16.95", button="Sold Out"),
        _priced_card("1/2 Gallon GLASS Milk", price="$9.85", button="Add to Order"),
        _priced_card("1/2 Gallon Milk", price="$6.75", button="Select Option"),
    ]
)

VENDOR_C_PRICED_CARDS = _category(
    [
        _priced_card("Milk, 1/2 Gal. (Plastic)", price="$6.25", button="Add to Cart", brand="Example Farm C"),
        _priced_card("Lemon Greek Yogurt, 1 Qt. (Glass)", price="$19.30", button="Select Option", brand="Example Farm C"),
        _priced_card("Salted Frozen Spring Butter 12 oz (Glass)", price="$17.25", button="Add to Cart", brand="Example Farm C"),
    ]
)

VENDOR_D_GIFT_AMOUNT_CARDS = _category(
    [
        _gift_card("4 Gallon Bundle Cow Milk in Plastic", price="$44.60", button="Select Option"),
        _gift_card("Camembert Cheese: 1/3lb wheel", price="$11.55", button="Add to Order"),
        _gift_card("Cheddar Cheese: 1 pound", price="$13.50", button="Select Option"),
        _gift_card("Colby Cheese: 1 pound", price="$13.20", button="Add to Order"),
        _gift_card("Cottage Cheese: pint", price="$10.95", button="Add to Order"),
        _gift_card("Cow Colostrum: pint in plastic", price="$14.03", button="Sold Out"),
    ]
)


# --- 1. Pricing-locked Vendor B milk category -----------------------------


def test_vendor_b_pricing_locked_category_extracts_cards_but_no_variants(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    captured = build_grazecart_captured_page(
        VENDOR_B_MILK_LOCKED, "vendor_b", "milk", "https://x/milk",
        artifact_dir=tmp_path / "locked",
    )
    assert captured.page_kind == "category_listing"
    assert len(captured.product_cards) == 4
    assert all(card.pricing_locked for card in captured.product_cards)
    assert captured.pricing_locked is True
    assert captured.variants == []

    out = create_import_candidates_from_variant_snapshot(tmp_path / "locked" / "variant_snapshots.json")
    assert json.loads(out.read_text(encoding="utf-8")) == []


def test_pricing_locked_capture_via_capture_product_variants(tmp_path):
    class FakePage:
        url = "https://x/milk"

        def content(self):
            return VENDOR_B_MILK_LOCKED

        def locator(self, selector):
            class L:
                def inner_text(self, timeout=None):
                    return "Milk Sign up for pricing"

            return L()

    captured = capture_product_variants(FakePage(), "vendor_b", "milk", tmp_path / "cap")
    assert captured.variants == []
    assert len(captured.product_cards) == 4
    assert (tmp_path / "cap" / "variant_snapshots.json").exists()


def test_vendor_b_visible_priced_cards_parse_milk_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    captured = build_grazecart_captured_page(
        VENDOR_B_PRICED_CARDS, "vendor_b", "milk", "https://x/milk", artifact_dir=tmp_path / "vendor_b_cards"
    )
    assert captured.pricing_locked is False
    assert len(captured.product_cards) == 3
    assert len(captured.variants) == 3
    milk_variants = [variant for variant in captured.variants if variant.inferred_product_type == "cow_milk"]
    assert len(milk_variants) == 2
    assert {variant.price for variant in milk_variants} == {9.85, 6.75}
    assert all(variant.inferred_package_size == 0.5 for variant in milk_variants)
    assert any(variant.stock_status == "out_of_stock" for variant in captured.variants)

    create_import_candidates_from_variant_snapshot(tmp_path / "vendor_b_cards" / "variant_snapshots.json")
    candidates = load_import_candidates("vendor_b")
    assert any(c.product_type and c.product_type.value == "cow_milk" for c in candidates)
    assert not any("Colostrum" in c.name for c in candidates)


def test_vendor_c_visible_priced_cards_parse_milk_and_butter_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    captured = build_grazecart_captured_page(
        VENDOR_C_PRICED_CARDS,
        "vendor_c",
        "dairy",
        "https://example-farm-c.test/dairy",
        artifact_dir=tmp_path / "vendor_c_cards",
    )
    assert captured.pricing_locked is False
    by_type = {variant.inferred_product_type: variant for variant in captured.variants if variant.inferred_product_type}
    assert by_type["cow_milk"].price == 6.25
    assert by_type["cow_milk"].inferred_package_size == 0.5
    assert by_type["cow_milk"].inferred_packaging == "plastic"
    assert by_type["butter"].price == 17.25
    assert by_type["butter"].inferred_package_size == 0.75
    assert by_type["butter"].inferred_packaging == "glass"
    assert by_type["butter"].inferred_storage_state == "frozen"


def test_vendor_d_gift_amount_cards_parse_as_prices_and_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    captured = build_grazecart_captured_page(
        VENDOR_D_GIFT_AMOUNT_CARDS,
        "vendor_d",
        "dairy_page",
        "https://example-farm-d.test/dairy",
        artifact_dir=tmp_path / "vendor_d_cards",
    )
    assert captured.pricing_locked is False
    assert len(captured.variants) == 6
    milk = next(variant for variant in captured.variants if variant.inferred_product_type == "cow_milk")
    assert milk.price == 44.60
    assert milk.inferred_package_size == 4.0
    assert milk.inferred_unit == "gallon"
    assert milk.inferred_packaging == "plastic"
    camembert = next(variant for variant in captured.variants if "Camembert" in variant.product_title)
    assert camembert.price == 11.55
    assert camembert.inferred_product_type == "cheese"
    assert round(camembert.inferred_package_size or 0, 3) == 0.333
    colostrum = next(variant for variant in captured.variants if "Colostrum" in variant.product_title)
    assert colostrum.price == 14.03
    assert colostrum.stock_status == "out_of_stock"
    assert colostrum.inferred_product_type is None

    create_import_candidates_from_variant_snapshot(tmp_path / "vendor_d_cards" / "variant_snapshots.json")
    candidates = load_import_candidates("vendor_d")
    assert any(c.product_type and c.product_type.value == "cow_milk" and c.detected_price == 44.60 for c in candidates)
    # The plain cheese cards (Camembert/Cheddar/Colby/Cottage) are surfaced as
    # eligible cheese candidates.
    assert any(c.product_type and c.product_type.value == "cheese" for c in candidates)
    assert any(c.product_type is None and c.needs_review for c in candidates)


# --- 2. Logged-in Vendor B milk with bundles ------------------------------


def test_vendor_b_milk_detail_parses_bundles_and_unit_price():
    variants = parse_detail_variants(VENDOR_B_MILK_DETAIL)
    assert len(variants) == 3
    by_size = {v.inferred_package_size: v for v in variants}
    assert set(by_size) == {1.0, 4.0, 8.0}
    assert all(v.inferred_unit == "gallon" and v.inferred_product_type == "cow_milk" for v in variants)

    assert by_size[1.0].unit_price == 11.45
    assert by_size[1.0].is_bundle is False
    assert by_size[4.0].total_price == 42.80
    assert by_size[4.0].unit_price == 10.70
    assert by_size[4.0].is_bundle is True
    assert by_size[4.0].bundle_quantity == 4.0
    assert by_size[8.0].unit_price == 10.45

    # Reported savings are stored but not trusted; computed values match here.
    assert by_size[4.0].reported_savings_text == "Save $3.00"
    assert by_size[4.0].computed_savings == 3.0
    assert by_size[8.0].computed_savings == 8.0
    assert all(v.savings_mismatch_warning is False for v in variants)


def test_vendor_b_milk_detail_creates_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    captured = build_grazecart_captured_page(
        VENDOR_B_MILK_DETAIL, "vendor_b", "milk", "https://x/p/1gal",
        artifact_dir=tmp_path / "detail",
    )
    assert captured.page_kind == "product_detail"
    assert len(captured.variants) == 3

    create_import_candidates_from_variant_snapshot(tmp_path / "detail" / "variant_snapshots.json")
    candidates = load_import_candidates("vendor_b")
    assert len(candidates) == 3
    assert all(c.product_type.value == "cow_milk" for c in candidates)
    assert all(c.needs_review is False for c in candidates)
    prices = {c.detected_package_size: c.detected_price for c in candidates}
    assert prices == {1.0: 11.45, 4.0: 42.80, 8.0: 83.60}
    assert 45.8 not in prices


def test_reported_savings_mismatch_stays_annotation_only():
    html = _detail(
        "1 GALLON Milk",
        "$11.45",
        [
            _option("1 GALLON Milk", "$11.45"),
            _option("8 GALLON Bundle Milk", "$83.60", reported="$25.00"),
        ],
    )
    variants = parse_detail_variants(html)
    bundle = next(v for v in variants if v.inferred_package_size == 8.0)
    assert bundle.computed_savings == 8.0
    assert bundle.reported_savings_text == "Save $25.00"
    assert bundle.savings_mismatch_warning is False
    assert not any("does not match computed" in w for w in bundle.warnings)


def test_bundle_math_is_scoped_to_product_title():
    html = (
        '<main>'
        '<section id="product_1" itemtype="https://schema.org/Product" class="productListing__grid">'
        '<h3 itemprop="name">Quart Milk</h3><button>Select Option</button>'
        '<div id="variantDropdown_1"><table><tbody>'
        '<tr><td><span class="variantTitle" title="Quart Milk">Quart Milk</span></td><td>$4.10</td><td>---</td></tr>'
        '<tr><td><span class="variantTitle" title="6 Pk. Quart Milk">6 Pk. Quart Milk</span></td><td>$21.60</td><td>$3.00</td></tr>'
        '</tbody></table></div></section>'
        '<section id="product_2" itemtype="https://schema.org/Product" class="productListing__grid">'
        '<h3 itemprop="name">Pint Milk</h3><button>Select Option</button>'
        '<div id="variantDropdown_2"><table><tbody>'
        '<tr><td><span class="variantTitle" title="Pint Milk">Pint Milk</span></td><td>$2.85</td><td>---</td></tr>'
        '<tr><td><span class="variantTitle" title="6 Pk Pint Milk">6 Pk Pint Milk</span></td><td>$15.60</td><td>$2.00</td></tr>'
        '</tbody></table></div></section>'
        '</main>'
    )

    variants = variants_from_category_cards(parse_category_cards(html))
    by_label = {variant.option_label: variant for variant in variants}

    assert by_label["6 Pk. Quart Milk"].inferred_package_size == 1.5
    assert by_label["6 Pk Pint Milk"].inferred_package_size == 0.75
    assert by_label["6 Pk. Quart Milk"].total_price == 21.60
    assert by_label["6 Pk Pint Milk"].total_price == 15.60
    assert by_label["6 Pk. Quart Milk"].bundle_quantity == 6
    assert by_label["6 Pk Pint Milk"].bundle_quantity == 6
    assert all(variant.savings_mismatch_warning is False for variant in variants)


# --- 3. Vendor B cheese category ----------------------------------------------


def test_vendor_b_cheese_category_inference():
    cards = parse_category_cards(VENDOR_B_CHEESE_CATEGORY)
    by_title = {card.title: card for card in cards}
    unsalted = by_title["8 oz Cheddar Cheese"]
    assert unsalted.inferred_product_type == "cheese"
    assert unsalted.inferred_package_size == 0.5
    assert unsalted.inferred_unit == "lb"
    assert unsalted.supported is True

    gouda = by_title["1 lb Vendor B Gouda Cheese"]
    assert gouda.inferred_product_type == "cheese"
    assert gouda.inferred_package_size == 1.0
    assert gouda.supported is True

    ghee = by_title["Grass-fed Ghee 8 oz"]
    assert ghee.inferred_product_type is None
    assert ghee.supported is False
    assert ghee.warnings


def test_unsupported_product_detail_does_not_break_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    html = _detail("Cow Colostrum Pint", "$14.00", [_option("Cow Colostrum Pint", "$14.00")])
    build_grazecart_captured_page(
        html, "vendor_b", "milk_cheese", "https://x/colostrum", artifact_dir=tmp_path / "gouda"
    )
    create_import_candidates_from_variant_snapshot(tmp_path / "gouda" / "variant_snapshots.json")
    candidates = load_import_candidates("vendor_b")
    assert len(candidates) == 1
    assert candidates[0].product_type is None
    assert candidates[0].needs_review is True
    assert "product_type" in candidates[0].missing_fields


# --- 4. Vendor C dairy fixtures -------------------------------------------------


def test_vendor_c_pricing_locked_creates_no_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    captured = build_grazecart_captured_page(
        VENDOR_C_DAIRY_LOCKED, "vendor_c", "dairy", "https://example-farm-c.test/dairy",
        artifact_dir=tmp_path / "vendor_c",
    )
    assert len(captured.product_cards) == 2
    assert captured.pricing_locked is True
    assert captured.variants == []
    out = create_import_candidates_from_variant_snapshot(tmp_path / "vendor_c" / "variant_snapshots.json")
    assert json.loads(out.read_text(encoding="utf-8")) == []


def test_vendor_c_sheep_milk_detail_parses_supported_product(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    captured = build_grazecart_captured_page(
        VENDOR_C_DAIRY_DETAIL, "vendor_c", "sheep_dairy", "https://example-farm-c.test/sheep",
        artifact_dir=tmp_path / "sheep",
    )
    assert len(captured.variants) == 1
    variant = captured.variants[0]
    assert variant.inferred_product_type == "sheep_milk"
    assert variant.inferred_package_size == 0.5
    assert variant.inferred_unit == "gallon"

    create_import_candidates_from_variant_snapshot(tmp_path / "sheep" / "variant_snapshots.json")
    candidates = load_import_candidates("vendor_c")
    assert candidates[0].product_type.value == "sheep_milk"
    assert candidates[0].needs_review is False


# --- 5. Example Farm D dairy fixtures -------------------------------------------------


def test_vendor_d_pricing_locked_creates_no_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    captured = build_grazecart_captured_page(
        VENDOR_D_DAIRY_LOCKED, "vendor_d", "dairy_page", "https://example-farm-d.test/dairy",
        artifact_dir=tmp_path / "vendor_d",
    )
    assert len(captured.product_cards) == 2
    assert captured.variants == []
    out = create_import_candidates_from_variant_snapshot(tmp_path / "vendor_d" / "variant_snapshots.json")
    assert json.loads(out.read_text(encoding="utf-8")) == []


def test_vendor_d_eggs_detail_parses_dozen_bundles(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    captured = build_grazecart_captured_page(
        VENDOR_D_EGGS_DETAIL, "vendor_d", "eggs", "https://example-farm-d.test/eggs",
        artifact_dir=tmp_path / "eggs",
    )
    sizes = {v.inferred_package_size for v in captured.variants}
    assert sizes == {12.0, 96.0}
    assert all(v.inferred_product_type == "eggs" and v.inferred_unit == "count" for v in captured.variants)

    create_import_candidates_from_variant_snapshot(tmp_path / "eggs" / "variant_snapshots.json")
    candidates = load_import_candidates("vendor_d")
    assert {c.detected_package_size for c in candidates} == {12.0, 96.0}
    assert all(c.product_type.value == "eggs" for c in candidates)


# --- 6. Candidate loader across multiple vendors ---------------------------


def test_import_candidates_load_across_multiple_grazecart_vendors(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    specs = [
        ("vendor_b", "milk", VENDOR_B_MILK_DETAIL),
        ("vendor_c", "sheep_dairy", VENDOR_C_DAIRY_DETAIL),
        ("vendor_d", "eggs", VENDOR_D_EGGS_DETAIL),
    ]
    for vendor_id, page_id, html in specs:
        build_grazecart_captured_page(
            html, vendor_id, page_id, f"https://x/{vendor_id}", artifact_dir=tmp_path / vendor_id
        )
        create_import_candidates_from_variant_snapshot(tmp_path / vendor_id / "variant_snapshots.json")

    all_candidates = load_import_candidates()
    vendor_ids = {c.vendor_id for c in all_candidates}
    assert {"vendor_b", "vendor_c", "vendor_d"} <= vendor_ids


def test_two_grazecart_page_runs_do_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    build_grazecart_captured_page(
        VENDOR_B_MILK_DETAIL, "vendor_b", "milk", "https://x/milk", artifact_dir=tmp_path / "m"
    )
    eggs_html = _detail("Large Eggs", "$7.00", [_option("1 Dozen Large Eggs", "$7.00")])
    build_grazecart_captured_page(
        eggs_html, "vendor_b", "eggs", "https://x/eggs", artifact_dir=tmp_path / "e"
    )
    p1 = create_import_candidates_from_variant_snapshot(tmp_path / "m" / "variant_snapshots.json")
    p2 = create_import_candidates_from_variant_snapshot(tmp_path / "e" / "variant_snapshots.json")
    assert p1 != p2
    pages = {c.source_page_id for c in load_import_candidates("vendor_b")}
    assert {"milk", "eggs"} <= pages


# --- Inference unit rules (spec Section 5) ---------------------------------


@pytest.mark.parametrize(
    "title,expected_type,expected_size,expected_unit",
    [
        ("Quart Milk", "cow_milk", 0.25, "gallon"),
        ("1/2 Gallon Milk", "cow_milk", 0.5, "gallon"),
        ("4 GALLON Bundle Milk", "cow_milk", 4.0, "gallon"),
        ("Pint Cream", "cream", 1.0, "pint"),
        ("Half Gallon Cream", "cream", 4.0, "pint"),
        ("8 oz Butter", "butter", 0.5, "lb"),
        ("2 lb Butter", "butter", 2.0, "lb"),
        ("Case of 12 Dozen Large Eggs", "eggs", 144.0, "count"),
        ("5 lb Cheddar", "cheese", 5.0, "lb"),
        ("8 oz Cheddar Cheese", "cheese", 0.5, "lb"),
    ],
)
def test_inference_unit_rules(title, expected_type, expected_size, expected_unit):
    assert infer_product_type(title) == expected_type
    assert infer_size(expected_type, title) == (expected_size, expected_unit)


def test_cheese_pint_quart_not_converted_to_lb():
    assert infer_size("cheese", "Cottage Cheese Quart") == (2.0, "pint")
    assert infer_size("cheese", "Unsalted Cheese Pint") == (1.0, "pint")


def test_future_unseen_bundle_and_case_counts_parse_without_special_branch(tmp_path):
    milk_html = _detail(
        "Milk",
        "$11.00",
        [
            _option("1 Gallon Milk", "$11.00"),
            _option("10 Gallon Bundle Milk", "$98.00", reported="$12.00"),
        ],
    )
    egg_html = _detail(
        "Large Eggs",
        "$7.00",
        [_option("Case of 20 Dozen Large Eggs", "$120.00", reported="$20.00")],
    )

    milk = build_grazecart_captured_page(
        milk_html, "vendor_b", "milk", "https://x/milk", artifact_dir=tmp_path / "milk"
    )
    eggs = build_grazecart_captured_page(
        egg_html, "vendor_d", "eggs", "https://x/eggs", artifact_dir=tmp_path / "eggs"
    )

    bundle = next(variant for variant in milk.variants if variant.is_bundle)
    egg_case = eggs.variants[0]
    assert bundle.inferred_package_size == 10
    assert bundle.inferred_unit == "gallon"
    assert bundle.reported_savings_text == "Save $12.00"
    assert egg_case.inferred_package_size == 240
    assert egg_case.inferred_unit == "count"
    assert egg_case.is_bundle is True
    assert milk.coverage_report["bundle_pack_case_offers_found"] == 1
    assert eggs.coverage_report["option_groups_found"] == 1


def test_grazecart_price_values_do_not_multiply_bundle_quantity(tmp_path):
    vendor_b = _detail(
        "Milk",
        "$6.75",
        [
            _option("1/2 Gallon Milk Plastic", "$6.75"),
            _option("4 Gallon Bundle Milk Plastic", "$11.45"),
        ],
    )
    vendor_d = _detail(
        "Cow Milk",
        "$12.82",
        [_option("4 Gallon Cow Milk in Plastic", "$12.82")],
    )

    vendor_b_capture = build_grazecart_captured_page(
        vendor_b, "vendor_b", "milk", "https://x/vendor_b", artifact_dir=tmp_path / "vendor_b_price"
    )
    vendor_d_capture = build_grazecart_captured_page(
        vendor_d, "vendor_d", "dairy_page", "https://x/vendor_d", artifact_dir=tmp_path / "vendor_d_price"
    )

    vendor_b_sizes = sorted(variant.inferred_package_size for variant in vendor_b_capture.variants)
    assert vendor_b_sizes == [0.5, 4.0]
    assert all(size not in vendor_b_sizes for size in [3.0, 45.8])
    assert vendor_d_capture.variants[0].inferred_package_size == 4.0
    assert vendor_d_capture.variants[0].inferred_package_size != 51.28


def test_grazecart_vendor_set():
    assert GRAZECART_VENDORS == {"vendor_b", "vendor_c", "vendor_d"}


def test_select_dropdown_bundle_options_are_parsed():
    """Bundle options exposed as a <select> dropdown are captured fully."""
    html = (
        '<h1 class="product-title">1 GALLON Milk</h1>'
        '<span class="price">$11.45</span>'
        '<form class="add-to-order">'
        '<select name="bundle">'
        '<option>1 GALLON Milk - $11.45</option>'
        '<option>4 GALLON Bundle Milk - $42.80 (Save $3.00)</option>'
        '<option>8 GALLON Bundle Milk - $83.60 (Save $8.00)</option>'
        '</select>'
        '<button class="add-to-order-button">Add to Order</button></form>'
    )
    variants = parse_detail_variants(html)
    assert len(variants) == 3
    by_size = {v.inferred_package_size: v for v in variants}
    assert set(by_size) == {1.0, 4.0, 8.0}
    assert by_size[4.0].total_price == 42.80
    assert by_size[4.0].unit_price == 10.70
    assert by_size[4.0].is_bundle is True
    assert by_size[4.0].reported_savings_text == "$3.00"
    assert by_size[4.0].computed_savings == 3.0
    assert all(v.inferred_product_type == "cow_milk" for v in variants)


def test_hidden_variant_dropdown_table_rows_are_parsed_from_category_card():
    html = (
        '<section id="product_1398" itemtype="https://schema.org/Product" class="productListing__grid">'
        '<h3 itemprop="name">Large Brown Eggs</h3>'
        '<div class="productListing__addToCart--grid">'
        '<button class="variants-dropdown-toggle" type="button">Select Option</button>'
        '<div id="variantDropdown_1398" style="display: none;">'
        '<form><table><tbody>'
        '<tr><td><span class="variantTitle" title="Large Brown Eggs">Large Brown Eggs</span></td><td>$9.45</td><td>---</td></tr>'
        '<tr><td><span class="variantTitle" title="8 Dozen Bundle Large Brown Eggs">8 Dozen Bundle Large Brown Eggs</span></td><td>$71.60</td><td>$4.00</td></tr>'
        '<tr><td><span class="variantTitle" title="15 Dozen Bundle Large Brown Eggs">15 Dozen Bundle Large Brown Eggs</span></td><td>$130.50</td><td>$11.25</td></tr>'
        '</tbody></table><button type="button" wire:click="add">Add to Order</button></form>'
        '</div></div></section>'
    )

    variants = variants_from_category_cards(parse_category_cards(html))

    assert [variant.option_label for variant in variants] == [
        "Large Brown Eggs",
        "8 Dozen Bundle Large Brown Eggs",
        "15 Dozen Bundle Large Brown Eggs",
    ]
    assert [variant.total_price for variant in variants] == [9.45, 71.60, 130.50]
    assert variants[1].reported_savings_text == "$4.00"
    assert variants[2].reported_savings_text == "$11.25"
    assert variants[1].is_bundle is True



def test_card_subtitle_recovery_fills_quantity_without_no_quantity_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    html = _category(
        [
            _priced_card(
                "Brown Eggs",
                subtitle="1 dozen CORN & SOY FREE eggs",
                price="$8.00",
                button="Add to Order",
            )
        ]
    )
    captured = build_grazecart_captured_page(
        html,
        "vendor_b",
        "eggs",
        "https://x/eggs",
        artifact_dir=tmp_path / "eggs",
    )

    assert captured.variants[0].inferred_package_size == 12
    assert "card_subtitle" not in captured.variants[0].validation_reasons
    create_import_candidates_from_variant_snapshot(tmp_path / "eggs" / "variant_snapshots.json")
    candidate = load_import_candidates("vendor_b")[0]
    assert candidate.detected_package_size == 12
    assert "no_quantity_evidence" not in candidate.missing_fields
    assert "card_subtitles_captured" in captured.coverage_report
    evidence = captured.variants[0].offer_evidence
    assert evidence is not None
    assert evidence.product_card_title == "Brown Eggs"
    assert evidence.product_card_subtitle == "1 dozen CORN & SOY FREE eggs"
    assert "1 dozen" in (evidence.product_card_text or "")
    assert "card_subtitle" in captured.variants[0].evidence_checkpoints
    assert captured.coverage_report["offer_traces"][0]["complete"] is True


def test_category_card_option_panel_rows_become_separate_offers(tmp_path):
    html = (
        '<main><li class="product-card">'
        '<h3 class="product-title">Brown Eggs</h3>'
        '<span class="product-subtitle">1 dozen CORN & SOY FREE eggs</span>'
        '<button>Select Option</button>'
        '<div class="option-row"><span class="option-label">1 Dozen Brown Eggs</span>'
        '<span class="option-price">$8.00</span></div>'
        '<div class="option-row"><span class="option-label">8 Dozen Bundle Brown Eggs</span>'
        '<span class="option-price">$60.00</span><span class="reported-savings">Save $4.00</span></div>'
        '<div class="option-row"><span class="option-label">15 Dozen Bundle Brown Eggs</span>'
        '<span class="option-price">$108.00</span><span class="reported-savings">Save $12.00</span></div>'
        '</li></main>'
    )

    captured = build_grazecart_captured_page(
        html,
        "vendor_b",
        "eggs",
        "https://x/eggs",
        artifact_dir=tmp_path / "eggs_panel",
    )

    sizes = sorted(variant.inferred_package_size for variant in captured.variants)
    assert sizes == [12.0, 96.0, 180.0]
    assert captured.coverage_report["option_buttons_found"] == 1
    assert captured.coverage_report["option_rows_extracted"] == 3
    assert captured.coverage_report["savings_facts_extracted"] == 2
    assert all(variant.offer_evidence and variant.offer_evidence.option_row_text for variant in captured.variants)
    assert all("Add to" not in (variant.offer_evidence.option_row_text or "") for variant in captured.variants)


def test_no_quantity_evidence_only_after_all_recovery_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    html = _category(
        [
            _priced_card(
                "Brown Eggs",
                subtitle="CORN & SOY FREE eggs",
                price="$8.00",
                button="Add to Order",
            )
        ]
    )
    captured = build_grazecart_captured_page(
        html,
        "vendor_b",
        "eggs",
        "https://x/eggs",
        artifact_dir=tmp_path / "missing_qty",
    )
    variant = captured.variants[0]

    assert variant.incomplete_discovery is True
    assert "quantity_evidence_not_found_after_full_inspection" in variant.validation_reasons
    assert variant.recovery_trace is not None
    assert {"card_title", "card_subtitle", "card_full_text", "option_text", "detail_text", "embedded_variation_json"} <= set(
        variant.recovery_trace.checked_sources
    )

    create_import_candidates_from_variant_snapshot(tmp_path / "missing_qty" / "variant_snapshots.json")
    candidate = load_import_candidates("vendor_b")[0]
    assert "no_quantity_evidence" not in candidate.missing_fields
    assert "quantity_evidence_not_found_after_full_inspection" in candidate.missing_fields
    assert not any("recovery_checked=" in note for note in candidate.review_notes)
    assert variant.recovery_trace.checked_sources
