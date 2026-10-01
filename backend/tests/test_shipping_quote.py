"""Safe shipping-quote capture (offline fixtures/mocks only)."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.fetcher.models import ShippingQuote, VendorPageTarget
from app.fetcher.services import capture_shipping_quote
from app.fetcher.shipping_quote import build_shipping_quote_from_html
from app.main import app


def _cart_html(*, extra: str = "") -> str:
    return f"""
    <div class="cart-summary">
      <li class="cart-item"><span class="cart-item-title">1 GALLON Milk</span></li>
      <div class="row"><span>Subtotal</span><span>$45.00</span></div>
      <label class="shipping-option"><input type="radio" name="ship" checked>
        <span class="option-name">UPS Ground</span><span class="option-price">$14.50</span></label>
      <label class="shipping-option"><input type="radio" name="ship">
        <span class="option-name">Farm Truck Delivery</span><span class="option-price">$9.00</span></label>
      <div class="row"><span>Cooler fee</span><span>$8.00</span></div>
      <div class="row"><span>Handling fee</span><span>$3.50</span></div>
      <div class="row"><span>Sales tax</span><span>$2.10</span></div>
      <div class="row"><span>Order total</span><span>$73.10</span></div>
      <a class="btn" href="/checkout">Proceed to checkout</a>
      {extra}
    </div>
    """


# --- 1. Safe quote available -----------------------------------------------


def test_quote_available_extracts_totals_and_saves_artifacts(tmp_path):
    quote = build_shipping_quote_from_html(
        _cart_html(),
        "vendor_b",
        "https://x/cart",
        intended_item_signatures=["1 GALLON Milk"],
        artifact_dir=tmp_path / "q",
    )
    assert quote.shipping_quote_status == "quote_available"
    assert quote.selected_shipping_method == "UPS Ground"
    assert quote.shipping_price == 14.50
    assert quote.cooler_fee == 8.00
    assert quote.handling_fee == 3.50
    assert quote.cart_subtotal == 45.00
    assert quote.cart_tax == 2.10
    assert quote.cart_quote_total == 73.10
    assert {option.name for option in quote.shipping_options} == {"UPS Ground", "Farm Truck Delivery"}
    assert quote.unrelated_cart_items is False
    # artifact metadata saved
    assert (tmp_path / "q" / "cart.html").exists()
    assert (tmp_path / "q" / "shipping_quote.json").exists()
    saved = json.loads((tmp_path / "q" / "shipping_quote.json").read_text(encoding="utf-8"))
    assert saved["cart_quote_total"] == 73.10


# --- 2. Unsafe checkout boundary -------------------------------------------


def test_unsafe_checkout_boundary_stops_before_final_confirmation():
    for marker in ["Place Order", "Submit Order", "Confirm Purchase", "Pay Now"]:
        html = _cart_html(extra=f'<button class="final">{marker}</button>')
        quote = build_shipping_quote_from_html(html, "vendor_b")
        assert quote.shipping_quote_status == "unsafe_checkout_boundary"
        assert quote.action_required == "unsafe_checkout_boundary"
        # Nothing was extracted/claimed as a usable quote past the boundary.
        assert quote.cart_quote_total is None
        assert "safety" in (quote.recommended_next_action or "").lower()


# --- 3. Login / captcha / pricing / location gates -------------------------


def test_login_required_from_password_input():
    quote = build_shipping_quote_from_html("<form><input type='password'></form>", "vendor_b")
    assert quote.shipping_quote_status == "login_required"
    assert quote.account_authenticated is False
    assert quote.fetch_ready is False


def test_login_required_from_login_url():
    quote = build_shipping_quote_from_html("<div>Cart</div>", "vendor_b", final_url="https://x/login")
    assert quote.shipping_quote_status == "login_required"


def test_captcha_required():
    quote = build_shipping_quote_from_html("<div class='g-recaptcha'>verify you are human</div>", "vendor_b")
    assert quote.shipping_quote_status == "captcha_required"


def test_pricing_locked():
    quote = build_shipping_quote_from_html("<div>Sign up for pricing</div>", "vendor_b")
    assert quote.shipping_quote_status == "pricing_locked"


def test_location_or_order_cycle_required():
    quote = build_shipping_quote_from_html(
        "<div>Please select your location to start your order.</div>", "vendor_b"
    )
    assert quote.shipping_quote_status == "location_or_order_cycle_required"
    assert quote.fetch_ready is False


def test_cart_unavailable_when_empty():
    quote = build_shipping_quote_from_html("<div>Your cart is empty</div>", "vendor_b")
    assert quote.shipping_quote_status == "cart_unavailable"


def test_quote_unavailable_when_no_totals():
    quote = build_shipping_quote_from_html("<div>Some cart page with no money lines</div>", "vendor_b")
    assert quote.shipping_quote_status == "quote_unavailable"


# --- 4. Existing cart conflict ---------------------------------------------


def test_unrelated_cart_items_are_warned_not_deleted():
    quote = build_shipping_quote_from_html(
        _cart_html(),
        "vendor_b",
        intended_item_signatures=["Cream — 1 pint"],  # does not match cart's milk
    )
    assert quote.unrelated_cart_items is True
    assert any("unrelated items" in warning for warning in quote.quote_warnings)
    assert any("not deleted" in warning for warning in quote.quote_warnings)
    # Still a read-only parse, so a quote can still be produced.
    assert quote.shipping_quote_status == "quote_available"


# --- 5. No secrets ----------------------------------------------------------


def test_quote_metadata_never_exposes_secrets():
    leaky = _cart_html(
        extra=(
            "<script>document.cookie='session=abc'</script>"
            "<input type='password' value='hunter2'>"
            "<meta name='payment_token' content='tok_123'>"
        )
    )
    # A password input pushes this to login_required, but regardless the quote
    # object must never carry secrets.
    quote = build_shipping_quote_from_html(leaky, "vendor_b")
    body = quote.model_dump_json().lower()
    assert "hunter2" not in body
    assert "session=abc" not in body
    assert "tok_123" not in body
    assert "cookie" not in body
    assert ".browser_profiles" not in body


# --- 6. Service + endpoint --------------------------------------------------


def test_capture_shipping_quote_service_uses_mocked_cart(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.services.SHIPPING_QUOTES_DIR", tmp_path / "quotes")
    monkeypatch.setattr(
        "app.fetcher.services._cart_target_for",
        lambda vendor_id: VendorPageTarget(
            vendor_id=vendor_id, page_id="cart", url="https://x/cart", requires_login=True
        ),
    )
    monkeypatch.setattr(
        "app.fetcher.services._load_cart_html",
        lambda vendor_id, cart_url: (_cart_html(), "https://x/cart"),
    )
    quote = capture_shipping_quote("vendor_b", intended_item_signatures=["1 GALLON Milk"])
    assert quote.shipping_quote_status == "quote_available"
    assert quote.cart_quote_total == 73.10
    assert quote.quote_artifact_path is not None


def test_capture_shipping_quote_no_cart_configured_is_cart_unavailable(monkeypatch):
    monkeypatch.setattr("app.fetcher.services._cart_target_for", lambda vendor_id: None)
    quote = capture_shipping_quote("vendor_b")
    assert quote.shipping_quote_status == "cart_unavailable"
    assert quote.action_required == "cart_unavailable"


def test_shipping_quote_endpoint_returns_safe_metadata(monkeypatch):
    monkeypatch.setattr(
        "app.main.capture_shipping_quote",
        lambda vendor_id: ShippingQuote(
            vendor_id=vendor_id,
            shipping_quote_status="quote_available",
            selected_shipping_method="UPS Ground",
            shipping_price=14.5,
            cooler_fee=8.0,
            cart_quote_total=73.1,
        ),
    )
    response = TestClient(app).post("/vendor-connections/vendor_b/shipping-quote")
    assert response.status_code == 200
    body = response.text.lower()
    assert "quote_available" in body
    assert "ups ground" in body
    assert "password" not in body
    assert "cookie" not in body
    assert ".browser_profiles" not in body
