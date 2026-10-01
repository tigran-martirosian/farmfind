"""Import-review staging safety + imported_only catalog behavior."""
from __future__ import annotations

import json

import pytest

from app.data_loader import load_catalog
from app.models import Product
from app.fetcher.grazecart import build_grazecart_captured_page
from app.fetcher.import_candidates import (
    approve_import_candidate,
    create_import_candidates_from_variant_snapshot,
    load_import_candidates,
    load_staged_products,
    reject_import_candidate,
    SUPERSEDED_BY_OPTION_ROW_TOTAL_PRICE,
)
from app.schemas import ImportedProductCandidate


def _candidate_dict(import_id: str, **updates):
    data = ImportedProductCandidate(
        import_id=import_id,
        vendor_id="vendor_a",
        source_page_id="cream",
        source_url="https://example-farm-a.test/product/cream/",
        name="Cream attribute_pa_size: Quart, attribute_pa_container: Glass",
        product_type="cream",
        detected_price=10.0,
        detected_package_size=2,
        detected_unit="pint",
        packaging="glass",
        stock_status="in_stock",
        parser_confidence=0.9,
        needs_review=False,
    ).model_dump(mode="json")
    data.update(updates)
    return data


def _write_candidate_run(root, vendor_id: str, run_name: str, candidates: list[dict]):
    path = root / vendor_id / run_name
    path.mkdir(parents=True)
    (path / "candidates.json").write_text(json.dumps(candidates, indent=2), encoding="utf-8")
    return path / "candidates.json"


def _milk_detail():
    return (
        '<h1 class="product-title">1 GALLON Milk</h1>'
        '<span class="price">$11.45</span>'
        '<form class="add-to-order">'
        '<label class="option-row"><span class="option-label">1 GALLON Milk</span>'
        '<span class="option-price">$11.45</span></label>'
        '<button class="add-to-order-button">Add to Order</button></form>'
    )


def _make_candidates(tmp_path, monkeypatch, *, html=None, page_id="milk"):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path / "cands")
    monkeypatch.setattr("app.fetcher.import_candidates.STAGED_PRODUCTS_PATH", tmp_path / "staged.json")
    build_grazecart_captured_page(
        html or _milk_detail(), "vendor_b", page_id, "https://x/milk", artifact_dir=tmp_path / page_id
    )
    create_import_candidates_from_variant_snapshot(tmp_path / page_id / "variant_snapshots.json")
    return load_import_candidates("vendor_b")


def test_approved_candidate_cannot_be_staged_twice(tmp_path, monkeypatch):
    candidate = _make_candidates(tmp_path, monkeypatch)[0]
    assert candidate.needs_review is False

    approve_import_candidate(candidate.import_id)
    assert len(load_staged_products()) == 1


def test_staged_approved_duplicate_is_hidden_from_default_review(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    staged = tmp_path / "staged.json"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    monkeypatch.setattr("app.fetcher.import_candidates.STAGED_PRODUCTS_PATH", staged)
    staged.write_text(
        json.dumps(
            [
                Product(
                    id="approved_milk",
                    vendor_id="vendor_b",
                    product_name="1 GALLON Milk Option: 1 GALLON Milk",
                    canonical_product="cow_milk",
                    price=11.45,
                    package_quantity=1,
                    package_unit="gallon",
                    source_type="imported",
                ).model_dump(mode="json")
            ]
        ),
        encoding="utf-8",
    )
    row = _candidate_dict(
        "new_same_milk",
        vendor_id="vendor_b",
        source_page_id="milk",
        name="1 GALLON Milk Option: 1 GALLON Milk",
        product_type="cow_milk",
        detected_price=11.45,
        detected_package_size=1,
        detected_unit="gallon",
        packaging=None,
    )
    _write_candidate_run(cands, "vendor_b", "milk_2026-07-07T040000", [row])

    assert load_import_candidates("vendor_b") == []
    debug = load_import_candidates("vendor_b", include_excluded=True)
    assert debug[0].exclusion_reason == "already_approved_duplicate"


def test_needs_review_candidate_is_not_optimizer_eligible(tmp_path, monkeypatch):
    # Colostrum has no optimizer product_type -> needs_review, missing field.
    colostrum = (
        '<h1 class="product-title">Cow Colostrum Pint</h1><span class="price">$14.00</span>'
        '<form class="add-to-order"><label class="option-row">'
        '<span class="option-label">Cow Colostrum Pint</span>'
        '<span class="option-price">$14.00</span></label>'
        '<button class="add-to-order-button">Add to Order</button></form>'
    )
    candidate = _make_candidates(tmp_path, monkeypatch, html=colostrum, page_id="milk_cheese")[0]
    assert candidate.needs_review is True

    with pytest.raises(ValueError, match="needs review"):
        approve_import_candidate(candidate.import_id)
    assert load_staged_products() == []


def test_rejected_candidate_is_ignored(tmp_path, monkeypatch):
    candidate = _make_candidates(tmp_path, monkeypatch)[0]
    reject_import_candidate(candidate.import_id)

    with pytest.raises(ValueError, match="Rejected"):
        approve_import_candidate(candidate.import_id)
    assert load_staged_products() == []


def test_imported_only_includes_approved_real_import_and_excludes_demo(tmp_path, monkeypatch):
    candidate = _make_candidates(tmp_path, monkeypatch)[0]
    approve_import_candidate(candidate.import_id)

    products, vendors = load_catalog("imported_only")
    ids = {product.id for product in products}
    assert candidate.import_id in ids
    # No demo vendors leak into imported_only.
    demo_names = {"Example Farm E", "Example Farm F", "Example Farm G"}
    assert not {vendor.name for vendor in vendors} & demo_names


def test_candidate_carries_clean_display_name_and_no_unknown_tags(tmp_path, monkeypatch):
    candidate = _make_candidates(tmp_path, monkeypatch)[0]
    assert candidate.display_name == "Milk — 1 gal"
    assert all("unknown" not in tag for tag in candidate.display_tags)
    # GrazeCart single-option product exposes no extra attribute chips.
    assert candidate.attributes == []


def test_stale_pending_candidate_is_hidden_when_newer_equivalent_exists(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    stale = _candidate_dict(
        "old_cream",
        product_type=None,
        detected_package_size=None,
        detected_unit=None,
        display_name=None,
        needs_review=True,
        missing_fields=["product_type", "detected_package_size", "detected_unit"],
    )
    fresh = _candidate_dict("new_cream")
    _write_candidate_run(cands, "vendor_a", "cream_2026-07-07T010000_old", [stale])
    _write_candidate_run(cands, "vendor_a", "cream_2026-07-07T020000_new", [fresh])

    candidates = load_import_candidates("vendor_a")

    assert [candidate.import_id for candidate in candidates] == ["new_cream"]
    assert candidates[0].needs_review is False


def test_approved_and_rejected_old_candidates_remain_visible(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    approved = _candidate_dict("approved_old", review_status="approved")
    rejected = _candidate_dict("rejected_old", review_status="rejected")
    fresh = _candidate_dict("pending_new")
    _write_candidate_run(cands, "vendor_a", "cream_2026-07-07T010000_old", [approved, rejected])
    _write_candidate_run(cands, "vendor_a", "cream_2026-07-07T020000_new", [fresh])

    ids = {candidate.import_id: candidate.review_status for candidate in load_import_candidates("vendor_a")}

    # Cross-status dedupe: approved beats rejected/pending for the same
    # variant, so only the approved representative remains visible.
    assert ids == {"approved_old": "approved"}


def test_clean_approved_equivalent_hides_stale_pending(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    stale = _candidate_dict(
        "old_pending_cream",
        product_type=None,
        detected_package_size=None,
        detected_unit=None,
        needs_review=True,
        missing_fields=["product_type", "detected_package_size", "detected_unit"],
    )
    approved = _candidate_dict("approved_cream", review_status="approved")
    _write_candidate_run(cands, "vendor_a", "cream_2026-07-07T010000_old", [stale])
    _write_candidate_run(cands, "vendor_a", "cream_2026-07-07T020000_new", [approved])

    candidates = load_import_candidates("vendor_a")

    assert [candidate.import_id for candidate in candidates] == ["approved_cream"]


def test_stale_pending_candidate_cannot_be_approved_from_default_loader(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    monkeypatch.setattr("app.fetcher.import_candidates.STAGED_PRODUCTS_PATH", tmp_path / "staged.json")
    stale = _candidate_dict(
        "old_cream",
        product_type=None,
        detected_package_size=None,
        detected_unit=None,
        needs_review=True,
    )
    fresh = _candidate_dict("new_cream")
    _write_candidate_run(cands, "vendor_a", "cream_2026-07-07T010000_old", [stale])
    _write_candidate_run(cands, "vendor_a", "cream_2026-07-07T020000_new", [fresh])

    with pytest.raises(FileNotFoundError):
        approve_import_candidate("old_cream")
    assert load_staged_products() == []


def test_loader_revalidates_missing_fields_and_rebuilds_display(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    row = _candidate_dict(
        "needs_display_refresh",
        missing_fields=["product_type", "detected_package_size", "detected_unit"],
        display_name=None,
        display_tags=[],
    )
    _write_candidate_run(cands, "vendor_a", "cream_2026-07-07T020000_new", [row])

    candidate = load_import_candidates("vendor_a")[0]

    assert candidate.missing_fields == []
    assert candidate.display_name == "Cream — quart, glass"


def test_loader_deterministically_revalidates_old_vendor_a_rows(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    rows = [
        _candidate_dict(
            "old_eggs",
            source_page_id="farm_eggs",
            name="Farm Eggs attribute_pa_size: Case of 16 Dozen",
            product_type=None,
            detected_package_size=None,
            detected_unit=None,
            missing_fields=["product_type", "detected_package_size", "detected_unit"],
            needs_review=True,
        ),
        _candidate_dict(
            "old_butter",
            source_page_id="cow_butter",
            name="Butter attribute_pa_size: 1 pound",
            product_type=None,
            detected_package_size=None,
            detected_unit=None,
            missing_fields=["product_type", "detected_package_size", "detected_unit"],
            needs_review=True,
        ),
        _candidate_dict(
            "old_cream",
            source_page_id="cream",
            name="Cream attribute_pa_size: Half Gallon",
            product_type=None,
            detected_package_size=None,
            detected_unit=None,
            missing_fields=["product_type", "detected_package_size", "detected_unit"],
            needs_review=True,
        ),
    ]
    _write_candidate_run(cands, "vendor_a", "old_2026-07-07T010000", rows)

    by_id = {candidate.import_id: candidate for candidate in load_import_candidates("vendor_a")}

    assert by_id["old_eggs"].product_type.value == "eggs"
    assert by_id["old_eggs"].detected_package_size == 192
    assert by_id["old_eggs"].detected_unit.value == "count"
    assert by_id["old_eggs"].missing_fields == []
    assert by_id["old_butter"].product_type.value == "butter"
    assert by_id["old_butter"].detected_package_size == 1
    assert by_id["old_butter"].detected_unit.value == "lb"
    assert by_id["old_butter"].missing_fields == []
    assert by_id["old_cream"].product_type.value == "cream"
    assert by_id["old_cream"].detected_package_size == 4
    assert by_id["old_cream"].detected_unit.value == "pint"
    assert by_id["old_cream"].missing_fields == []


def test_cheese_display_rebuild_uses_volume_options_without_optimizer_size(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    cheese = _candidate_dict(
        "cheese_pint_full_no_rennet",
        source_page_id="cheese",
        source_url="https://example-farm-a.test/product/cheese/",
        name=(
            "Cheese attribute_pa_container: Glass, attribute_pa_size: Pint, "
            "attribute_use-rennet: No, attribute_fat-content: Full Fat"
        ),
        raw_text=(
            "Cheese\nattribute_pa_container: Glass, attribute_pa_size: Pint, "
            "attribute_use-rennet: No, attribute_fat-content: Full Fat\n7 in stock"
        ),
        product_type="cheese",
        detected_package_size=None,
        detected_unit=None,
        packaging="glass",
        needs_review=False,
        missing_fields=[],
        display_name=None,
        display_tags=[],
    )
    _write_candidate_run(cands, "vendor_a", "cheese_2026-07-07T020000_new", [cheese])

    candidate = load_import_candidates("vendor_a")[0]

    assert candidate.display_name == "Cheese — pint glass, full fat, no rennet"
    # Details must not duplicate the size (it stays in Package + name).
    assert {"glass", "full fat", "no rennet"} <= set(candidate.display_tags)
    assert "pint" not in candidate.display_tags
    assert candidate.detected_package_size == 1
    assert candidate.detected_unit.value == "pint"
    assert candidate.needs_review is False
    assert "detected_package_size" not in candidate.missing_fields
    assert "detected_unit" not in candidate.missing_fields
    assert "product_type" not in candidate.missing_fields
    visible = " ".join([candidate.display_name or "", *candidate.display_tags])
    assert not any(
        raw_key in visible
        for raw_key in [
            "attribute_pa_size",
            "attribute_pa_container",
            "attribute_chilled",
            "attribute_use-rennet",
            "attribute_fat-content",
        ]
    )


def test_cheese_display_rebuild_handles_quart_skim_cheddar_rennet(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    cheese = _candidate_dict(
        "cheese_quart_skim_cheddar",
        source_page_id="cheese",
        name=(
            "Cheese attribute_pa_size: Quart, attribute_pa_container: Glass, "
            "attribute_fat-content: Skim, attribute_use-rennet: Yes (Like Cheddar)"
        ),
        product_type="cheese",
        detected_package_size=None,
        detected_unit=None,
        packaging="glass",
        needs_review=False,
    )
    _write_candidate_run(cands, "vendor_a", "cheese_2026-07-07T020000_new", [cheese])

    candidate = load_import_candidates("vendor_a")[0]

    assert candidate.display_name == "Cheese — quart glass, skim, cheddar-style rennet"
    assert {"skim", "cheddar-style rennet"} <= set(candidate.display_tags)
    assert "quart" not in candidate.display_tags  # unit token belongs to Package, not Details
    assert candidate.detected_package_size == 2
    assert candidate.detected_unit.value == "pint"
    assert candidate.needs_review is False


def test_excluded_products_are_hidden_from_normal_import_review(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    rows = [
        _candidate_dict("chocolate", name="Chocolate Milk", product_type="cow_milk"),
        _candidate_dict("tumbler", name="DM Branded Tumbler Glass", product_type="cow_milk"),
        _candidate_dict("yogurt", name="Cow Yogurt, Cream Top", product_type="cream"),
        _candidate_dict("ghee", name="Ghee", product_type="butter"),
        _candidate_dict("milk", name="Milk", product_type="cow_milk", detected_package_size=1, detected_unit="gallon"),
    ]
    _write_candidate_run(cands, "vendor_a", "mixed_2026-07-07T030000", rows)

    candidates = load_import_candidates("vendor_a")

    assert [candidate.import_id for candidate in candidates] == ["milk"]
    assert candidates[0].eligibility_status == "eligible"


def test_whey_is_relevant_needs_review_not_excluded(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    row = _candidate_dict(
        "whey",
        name="Whey: quart in plastic",
        product_type=None,
        detected_package_size=None,
        detected_unit=None,
        packaging="plastic",
        missing_fields=[],
        needs_review=False,
    )
    _write_candidate_run(cands, "vendor_d", "dairy_page_2026-07-07T030000", [row])

    candidate = load_import_candidates("vendor_d")[0]

    assert candidate.eligibility_status == "needs_review"
    assert candidate.display_product_type == "whey"
    assert candidate.package_display == "1 quart"
    assert candidate.needs_review is True
    assert "unsupported_optimizer_product_type" in candidate.review_notes


def test_vendor_a_milk_options_do_not_parse_price_as_package(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    rows = [
        _candidate_dict(
            "milk_gallon_glass",
            source_page_id="milk",
            name="Milk attribute_pa_size: Gallon, attribute_pa_container: Glass, attribute_chilled?: Yes",
            product_type="cow_milk",
            detected_price=15.00,
            detected_package_size=None,
            detected_unit=None,
            raw_text="current price is $15.00 original price was $18.00",
        ),
        _candidate_dict(
            "milk_quart_glass",
            source_page_id="milk",
            name="Milk attribute_pa_size: Quart, attribute_pa_container: Glass, attribute_chilled?: Yes",
            product_type="cow_milk",
            detected_price=5.00,
            detected_package_size=None,
            detected_unit=None,
            raw_text="current price is $5.00",
        ),
        _candidate_dict(
            "milk_gallon_plastic",
            source_page_id="milk",
            name="Milk attribute_pa_size: Gallon, attribute_pa_container: Plastic",
            product_type="cow_milk",
            detected_price=10.00,
            detected_package_size=None,
            detected_unit=None,
            raw_text="current price is $10.00",
        ),
    ]
    _write_candidate_run(cands, "vendor_a", "milk_2026-07-08T010000", rows)

    by_id = {candidate.import_id: candidate for candidate in load_import_candidates("vendor_a")}

    assert by_id["milk_gallon_glass"].detected_package_size == 1
    assert by_id["milk_gallon_glass"].detected_unit.value == "gallon"
    assert by_id["milk_quart_glass"].detected_package_size == 0.25
    assert by_id["milk_quart_glass"].detected_unit.value == "gallon"
    assert by_id["milk_gallon_plastic"].detected_package_size == 1
    assert by_id["milk_gallon_plastic"].detected_unit.value == "gallon"
    assert all(candidate.detected_unit.value != "lb" for candidate in by_id.values())


def test_product_type_precedence_ignores_broad_context_pollution(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    rows = [
        _candidate_dict(
            "cream",
            source_page_id="dairy_page",
            name="Cream attribute_pa_size: Pint",
            product_type="cow_milk",
            detected_package_size=None,
            detected_unit=None,
            raw_text="Milk gallon Duck Eggs flavored yogurt",
        ),
        _candidate_dict(
            "butter",
            source_page_id="dairy_page",
            name="Butter attribute_pa_size: 1 pound",
            product_type="cow_milk",
            detected_package_size=None,
            detected_unit=None,
            raw_text="Milk gallon",
        ),
        _candidate_dict(
            "brown_eggs",
            source_page_id="eggs",
            name="Brown Eggs attribute_pa_size: 1 dozen",
            product_type=None,
            detected_package_size=None,
            detected_unit=None,
            raw_text="Duck Eggs Quail Eggs flavored",
        ),
        _candidate_dict(
            "duck_eggs",
            source_page_id="eggs",
            name="Duck Eggs attribute_pa_size: 1 dozen",
            product_type=None,
            detected_package_size=None,
            detected_unit=None,
        ),
        _candidate_dict(
            "quail_eggs",
            source_page_id="eggs",
            name="Quail Eggs attribute_pa_size: 1 dozen",
            product_type=None,
            detected_package_size=None,
            detected_unit=None,
        ),
    ]
    _write_candidate_run(cands, "vendor_a", "mixed_2026-07-08T020000", rows)

    by_id = {candidate.import_id: candidate for candidate in load_import_candidates("vendor_a")}

    assert by_id["cream"].product_type.value == "cream"
    assert by_id["cream"].detected_unit.value == "pint"
    assert by_id["butter"].product_type.value == "butter"
    assert by_id["butter"].detected_unit.value == "lb"
    assert by_id["brown_eggs"].product_type.value == "eggs"
    assert by_id["brown_eggs"].species == "chicken"
    assert "flavored" not in by_id["brown_eggs"].display_tags
    assert by_id["duck_eggs"].species == "duck"
    assert by_id["quail_eggs"].species == "quail"


def test_invalid_package_and_type_combos_are_not_ready(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    rows = [
        _candidate_dict(
            "milk_lb",
            name="Milk 15 lb",
            product_type="cow_milk",
            detected_package_size=15,
            detected_unit="lb",
        ),
        _candidate_dict(
            "cream_as_milk",
            name="Cream Pint",
            product_type="cow_milk",
            detected_package_size=1,
            detected_unit="pint",
        ),
        _candidate_dict(
            "butter_as_milk",
            name="Butter 1 lb",
            product_type="cow_milk",
            detected_package_size=1,
            detected_unit="lb",
        ),
    ]
    _write_candidate_run(cands, "vendor_a", "bad_2026-07-08T030000", rows)

    by_id = {candidate.import_id: candidate for candidate in load_import_candidates("vendor_a")}

    assert by_id["milk_lb"].needs_review is True
    assert "invalid_package_for_product_type" in by_id["milk_lb"].missing_fields
    assert by_id["cream_as_milk"].product_type.value == "cream"
    assert by_id["butter_as_milk"].product_type.value == "butter"


def test_debug_evidence_notes_are_hidden_from_normal_details(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    row = _candidate_dict(
        "debuggy",
        review_notes=[
            "evidence_checkpoints=product_title,card_full_text",
            "recovery_checked=detail_text; stopped=missing_quantity",
            "current price is $26.00",
            "quantity_evidence_not_found_after_full_inspection",
        ],
    )
    _write_candidate_run(cands, "vendor_a", "debug_2026-07-08T040000", [row])

    candidate = load_import_candidates("vendor_a")[0]

    joined = " ".join(candidate.review_notes + candidate.display_tags)
    assert "evidence_checkpoints" not in joined
    assert "recovery_checked" not in joined
    assert "current price" not in joined
    assert "quantity_evidence_not_found_after_full_inspection" in candidate.review_notes


def test_colostrum_display_type_is_not_missing(tmp_path, monkeypatch):
    candidate = _make_candidates(
        tmp_path,
        monkeypatch,
        html=(
            '<h1 class="product-title">Cow Colostrum</h1>'
            '<span class="price">$14.00</span>'
            '<form class="add-to-order">'
            '<label class="option-row"><span class="option-label">Cow Colostrum Quart</span>'
            '<span class="option-price">$14.00</span></label>'
            '<button>Add to Order</button></form>'
        ),
        page_id="colostrum",
    )[0]

    assert candidate.product_type is None
    assert candidate.relevant_type == "colostrum"
    assert candidate.display_product_type == "colostrum"


def test_sheep_milk_preserves_species_and_type(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    row = _candidate_dict(
        "sheep_milk",
        source_page_id="sheep_milk",
        name="Sheep Milk 1/2 gallon",
        product_type=None,
        detected_package_size=None,
        detected_unit=None,
    )
    _write_candidate_run(cands, "vendor_a", "sheep_milk_2026-07-07T030000", [row])

    candidate = load_import_candidates("vendor_a")[0]

    assert candidate.product_type == "sheep_milk"
    assert candidate.species == "sheep"
    assert candidate.detected_package_size == 0.5
    assert candidate.package_display == "1/2 gallon"


def test_egg_count_and_fraction_package_display_are_readable(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    rows = [
        _candidate_dict(
            "quail",
            name="Quail Eggs: 18 per pack",
            product_type=None,
            detected_package_size=None,
            detected_unit=None,
        ),
        _candidate_dict(
            # Unsalted so it stays eligible; this row exercises 1/3 lb display.
            "camembert",
            name="Unsalted Camembert Cheese: 1/3lb wheel",
            product_type=None,
            detected_package_size=None,
            detected_unit=None,
        ),
    ]
    _write_candidate_run(cands, "vendor_d", "dairy_page_2026-07-07T030000", rows)
    by_id = {candidate.import_id: candidate for candidate in load_import_candidates("vendor_d")}

    assert by_id["quail"].product_type == "eggs"
    assert by_id["quail"].detected_package_size == 18
    assert by_id["quail"].package_display == "18 count"
    assert by_id["camembert"].product_type == "cheese"
    assert by_id["camembert"].package_display == "1/3 lb"
    visible = " ".join([by_id["camembert"].display_name or "", *by_id["camembert"].display_tags])
    assert "0.3333333333333333" not in visible


def test_option_row_bundles_do_not_create_base_price_hybrids(tmp_path, monkeypatch):
    candidates = _make_candidates(
        tmp_path,
        monkeypatch,
        html=(
            '<main>'
            '<section id="product_10" itemtype="https://schema.org/Product" class="productListing__grid">'
            '<h3 itemprop="name">Quart Milk</h3><button>Select Option</button>'
            '<div id="variantDropdown_10"><table><tbody>'
            '<tr><td><span class="variantTitle" title="Quart Milk">Quart Milk</span></td><td>$4.10</td><td>---</td></tr>'
            '<tr><td><span class="variantTitle" title="6 Pk. Quart Milk">6 Pk. Quart Milk</span></td><td>$21.60</td><td>$3.00</td></tr>'
            '</tbody></table></div></section>'
            '<section id="product_495" itemtype="https://schema.org/Product" class="productListing__grid">'
            '<h3 itemprop="name">1/2 Gallon Milk</h3><button>Select Option</button>'
            '<div id="variantDropdown_495"><table><tbody>'
            '<tr><td><span class="variantTitle" title="1/2 Gallon Milk">1/2 Gallon Milk</span></td><td>$6.75</td><td>---</td></tr>'
            '<tr><td><span class="variantTitle" title="6 - 1/2 Gallon Bundle Milk">6 - 1/2 Gallon Bundle Milk</span></td><td>$37.50</td><td>$3.00</td></tr>'
            '</tbody></table></div></section>'
            '<section id="product_20" itemtype="https://schema.org/Product" class="productListing__grid">'
            '<h3 itemprop="name">8 oz Cheddar Cheese</h3><button>Select Option</button>'
            '<div id="variantDropdown_20"><table><tbody>'
            '<tr><td><span class="variantTitle" title="8 oz Cheddar Cheese">8 oz Cheddar Cheese</span></td><td>$8.40</td><td>---</td></tr>'
            '<tr><td><span class="variantTitle" title="6 Pk 8 oz Cheddar Cheese">6 Pk 8 oz Cheddar Cheese</span></td><td>$48.90</td><td>$1.50</td></tr>'
            '</tbody></table></div></section>'
            '</main>'
        ),
    )

    prices_by_package = {(c.package_display, round(c.detected_price or 0, 2)) for c in candidates}

    assert ("6 x 1 quart", 21.60) in prices_by_package
    assert ("6 x 1 quart", 4.10) not in prices_by_package
    assert ("6 x 1/2 gallon", 37.50) in prices_by_package
    assert ("6 x 1/2 gallon", 6.75) not in prices_by_package
    assert ("6 x 8 oz", 48.90) in prices_by_package
    assert ("6 x 8 oz", 8.40) not in prices_by_package


def test_pending_base_price_bundle_hybrids_are_superseded_but_base_rows_remain(tmp_path, monkeypatch):
    cands = tmp_path / "cands"
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", cands)
    source_blob = (
        "Product Price Savings Quart Milk $4.10 --- "
        "6 Pk. Quart Milk $21.60 $3.00"
    )
    rows = [
        _candidate_dict(
            "base_quart",
            vendor_id="vendor_b",
            source_page_id="milk",
            name="Quart Milk Option: Quart Milk -",
            raw_text="Quart Milk",
            product_type="cow_milk",
            detected_price=4.10,
            detected_package_size=0.25,
            detected_unit="gallon",
            packaging=None,
        ),
        _candidate_dict(
            "correct_bundle",
            vendor_id="vendor_b",
            source_page_id="milk",
            name="Quart Milk Option: 6 Pk. Quart Milk -",
            raw_text="6 Pk. Quart Milk $21.60",
            product_type="cow_milk",
            detected_price=21.60,
            detected_package_size=1.5,
            detected_unit="gallon",
            packaging=None,
        ),
        _candidate_dict(
            "hybrid_bundle",
            vendor_id="vendor_b",
            source_page_id="milk",
            name="Quart Milk Option: Quart Milk - " + source_blob,
            raw_text=source_blob,
            product_type="cow_milk",
            detected_price=4.10,
            detected_package_size=0.25,
            detected_unit="gallon",
            packaging=None,
        ),
    ]
    _write_candidate_run(cands, "vendor_b", "milk_2026-07-09T041949", rows)

    visible = {candidate.import_id: candidate for candidate in load_import_candidates("vendor_b")}
    all_rows = {candidate.import_id: candidate for candidate in load_import_candidates("vendor_b", include_excluded=True)}

    assert "base_quart" in visible
    assert visible["base_quart"].package_display == "1 quart"
    assert "correct_bundle" in visible
    assert visible["correct_bundle"].package_display == "6 x 1 quart"
    assert "hybrid_bundle" not in visible
    assert all_rows["hybrid_bundle"].exclusion_reason == SUPERSEDED_BY_OPTION_ROW_TOTAL_PRICE


def test_bundle_display_package_is_separate_from_normalized_total(tmp_path, monkeypatch):
    candidates = _make_candidates(
        tmp_path,
        monkeypatch,
        html=(
            '<main>'
            '<section id="product_12" itemtype="https://schema.org/Product" class="productListing__grid">'
            '<h3 itemprop="name">Pint Milk</h3><button>Select Option</button>'
            '<div id="variantDropdown_12"><table><tbody>'
            '<tr><td><span class="variantTitle" title="6 Pk Pint Milk">6 Pk Pint Milk</span></td><td>$15.60</td><td>$1.50</td></tr>'
            '</tbody></table></div></section>'
            '</main>'
        ),
    )

    bundle = candidates[0]
    assert bundle.package_display == "6 x 1 pint"
    assert bundle.display_name.endswith("6 x 1 pint")
    assert bundle.detected_package_size == 0.75
    assert bundle.detected_unit.value == "gallon"
