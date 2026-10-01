"""Diagnostic/excluded candidates hidden by default, shown on request."""
from __future__ import annotations

import json

from app.fetcher.import_candidates import load_import_candidates
from app.schemas import ImportedProductCandidate


def _row(import_id: str, **updates) -> dict:
    data = ImportedProductCandidate(
        import_id=import_id,
        vendor_id="vendor_c",
        source_page_id="dairy",
        name="Milk 1/2 Gal Plastic",
        product_type="cow_milk",
        detected_price=8.0,
        detected_package_size=0.5,
        detected_unit="gallon",
        stock_status="in_stock",
        needs_review=False,
        eligibility_status="eligible",
    ).model_dump(mode="json")
    data.update(updates)
    return data


def _write(root, run, rows):
    path = root / "vendor_c" / run
    path.mkdir(parents=True)
    (path / "candidates.json").write_text(json.dumps(rows), encoding="utf-8")


def test_excluded_hidden_by_default_shown_when_requested(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path)
    eligible = _row("vendor_c_milk")
    excluded = _row(
        "vendor_c_frozen_butter",
        name="Salted Frozen Spring Butter",
        product_type=None,
        detected_package_size=None,
        detected_unit=None,
    )
    _write(tmp_path, "dairy_2026-07-07T010000_x", [eligible, excluded])

    default_ids = {c.import_id for c in load_import_candidates("vendor_c")}
    assert default_ids == {"vendor_c_milk"}  # excluded/diagnostic hidden

    all_ids = {
        c.import_id
        for c in load_import_candidates("vendor_c", include_excluded=True)
    }
    assert all_ids == {"vendor_c_milk", "vendor_c_frozen_butter"}  # revealed for debugging


def test_choc_milk_abbreviation_is_excluded_flavored_milk(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path)
    _write(
        tmp_path,
        "dairy_2026-07-07T010000_x",
        [_row("vendor_c_choc", name="Choc. Milk", raw_text="Choc. Milk")],
    )

    default_ids = {c.import_id for c in load_import_candidates("vendor_c")}
    assert default_ids == set()  # excluded by default

    with_excluded = load_import_candidates("vendor_c", include_excluded=True)
    row = next(c for c in with_excluded if c.import_id == "vendor_c_choc")
    assert row.eligibility_status == "excluded"
    assert row.exclusion_reason == "flavored_milk"
    assert row.product_type is None  # not cow_milk


def test_loader_re_evaluates_stale_stored_classification(tmp_path, monkeypatch):
    """A stored candidate saved by an older parser with wrong/stale
    fields is re-classified on read (not trusted as-is)."""
    monkeypatch.setattr("app.fetcher.import_candidates.IMPORT_CANDIDATES_DIR", tmp_path)
    # Stored as an ELIGIBLE cow_milk with a stale label, but the name is a plain
    # ice cream -> must be re-excluded on read.
    stale = _row(
        "stale_ice_cream",
        name="Vanilla Ice Cream",
        product_type="cow_milk",
        eligibility_status="eligible",
        exclusion_reason=None,
    )
    _write(tmp_path, "cheese_2026-07-07T010000_x", [stale])

    default = load_import_candidates("vendor_c")
    assert default == []  # re-classified as excluded, hidden by default

    row = next(
        c for c in load_import_candidates("vendor_c", include_excluded=True)
        if c.import_id == "stale_ice_cream"
    )
    assert row.eligibility_status == "excluded"
    assert row.exclusion_reason == "ice_cream"
    assert row.product_type.value == "cream"  # cream, not cow_milk
