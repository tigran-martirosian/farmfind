"""Tests for eligibility, product-type inference and salt detection."""
from __future__ import annotations

import pytest

from app.fetcher.import_candidates import _refreshed_candidate
from app.schemas import ImportedProductCandidate


def _refresh(name, page_id="dairy", raw_text=None):
    candidate = ImportedProductCandidate(
        import_id="x",
        vendor_id="vendor_c",
        source_page_id=page_id,
        name=name,
        raw_text=raw_text if raw_text is not None else name,
        detected_price=8.0,
        needs_review=False,
    )
    return _refreshed_candidate(candidate)


# --- Product-type inference not polluted by page slug ---------------


@pytest.mark.parametrize(
    "name,page,expected_type",
    [
        ("Fresh Unsalted Butter, 12 oz Glass", "butter_cheese", "butter"),
        ("Heavy Cream, 1 Pt. Glass", "butter_cheese", "cream"),
        ("Light Cream, 1 Pt. Glass", "dairy_page", "cream"),
        ("Milk, 1 Qt. Plastic", "butter_cheese", "cow_milk"),
    ],
)
def test_product_type_ignores_polluted_page_slug(name, page, expected_type):
    result = _refresh(name, page_id=page)
    assert result.product_type is not None
    assert result.product_type.value == expected_type


@pytest.mark.parametrize("name", ["WB Eggnog", "Egg Custard", "Mayonnaise", "Vanilla Protein Shake"])
def test_prepared_foods_are_excluded_not_eggs(name):
    result = _refresh(name, page_id="eggs")
    assert result.product_type is None
    assert result.eligibility_status == "excluded"
    assert result.exclusion_reason == "prepared_or_flavored"


# --- Cheese eligibility --------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Gouda Cheese", "eligible"),
        ("Baby Swiss", "eligible"),
        ("Cheddar Cheese", "eligible"),
        ("Unsalted Baby Swiss", "eligible"),
        ("Garlic Herb Cheese", "excluded"),
        ("Cheese", "eligible"),
    ],
)
def test_cheese_eligibility_does_not_depend_on_salt_label(name, expected):
    result = _refresh(name, page_id="cheese")
    assert result.eligibility_status == expected
    if expected == "excluded":
        assert result.exclusion_reason == "flavored"


def test_no_accepted_cheese_shows_salt_status_unknown():
    result = _refresh("Unsalted Cottage Cheese", page_id="cheese")
    assert result.eligibility_status == "eligible"
    assert result.salt_status == "unsalted"
    assert "salt status unknown" not in [tag.lower() for tag in result.display_tags]


# --- Salt detection ------------------------------------------------


def test_unsalted_is_not_tagged_salted():
    result = _refresh("Fresh Unsalted Butter, 12 oz Glass", page_id="dairy")
    assert result.salt_status == "unsalted"
    assert "salted" not in result.processing_tags


def test_salted_butter_is_detected_and_stays_eligible():
    result = _refresh("Salted Spring Butter", page_id="dairy")
    assert result.salt_status == "salted"
    assert "salted" in result.processing_tags
    assert result.eligibility_status == "eligible"


# --- The reCAPTCHA badge must not read as a captcha wall -------------


def test_recaptcha_badge_is_not_a_captcha_wall():
    from app.fetcher.detection import strong_captcha_markers

    logged_in = (
        "<div class='grecaptcha-badge'></div>"
        "<p>This site is protected by reCAPTCHA and the Google Privacy Policy and "
        "Terms of Service apply.</p><h1>My Account</h1><p>Hello, logged-in user</p>"
    )
    assert strong_captcha_markers(logged_in) == []


def test_real_captcha_challenge_is_detected():
    from app.fetcher.detection import strong_captcha_markers

    wall = "<div class='g-recaptcha'></div><p>Please verify you are human before continuing.</p>"
    assert strong_captcha_markers(wall)


def test_connection_captcha_ignores_passive_badge():
    from app.fetcher.account_connections import real_captcha_markers
    from app.fetcher.models import VendorConnectionConfig

    config = VendorConnectionConfig(
        vendor_id="vendor_a",
        auth_state_label="vendor_a",
        login_url="https://example-farm-a.test/sign-in/",
        captcha_markers=["recaptcha", "g-recaptcha", "hcaptcha", "verify you are human", "captcha"],
    )
    badge_text = "This site is protected by reCAPTCHA. My Account Orders Logout"
    assert real_captcha_markers(badge_text, badge_text, config) == []
