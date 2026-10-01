"""Safe, deterministic shipping-quote capture from a vendor cart/checkout summary.

This reads shipping/cooler/handling/subtotal/total from a cart or
checkout-summary HTML snapshot. It is intentionally READ-ONLY and safety-first:

* It never clicks Place Order / Pay / Submit Order / Confirm Purchase.
* If the only way to reach shipping is a page that exposes a final-confirmation
  button, it returns ``unsafe_checkout_boundary`` instead of proceeding.
* It never touches saved address/payment settings and never submits payment.

Everything here is offline testable with HTML fixtures (no Playwright/network).
"""
from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .detection import (
    CAPTCHA_MARKERS,
    detected_markers,
    has_real_price_or_order_controls,
    pricing_lock_markers,
    store_access_markers,
)
from .models import ShippingOption, ShippingQuote

# Final-confirmation controls we must never cross. Their presence means we are at
# (or one click from) an order/payment boundary, so we stop and report.
UNSAFE_CHECKOUT_MARKERS = [
    "place order",
    "place your order",
    "submit order",
    "confirm purchase",
    "confirm order",
    "confirm and pay",
    "complete order",
    "complete purchase",
    "pay now",
    "submit payment",
    "authorize payment",
]

CART_EMPTY_MARKERS = [
    "your cart is empty",
    "cart is empty",
    "no items in your cart",
    "shopping cart is empty",
    "your basket is empty",
]

LOGIN_MARKERS = ["sign in", "log in", "login", "forgot password"]
LOGIN_URL_PATTERNS = ["/sign-in", "/signin", "/login", "/account/login", "/my-account"]


def _strip_tags(value: str | None) -> str:
    if not value:
        return ""
    text = html_lib.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has_password_input(html: str) -> bool:
    return bool(re.search(r"<input\b[^>]*type\s*=\s*['\"]?password['\"]?", html, flags=re.IGNORECASE))


def _amount_for_label(html: str, labels: list[str]) -> float | None:
    """Find the first $ amount that follows one of ``labels`` (across tags)."""
    for label in labels:
        match = re.search(
            rf"\b{re.escape(label)}\b[^$<]*(?:<[^>]*>\s*)*\$\s?([\d,]+\.\d{{2}})",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def parse_shipping_options(html: str) -> list[ShippingOption]:
    options: list[ShippingOption] = []
    for block in re.findall(
        r'<(?:label|div|tr)[^>]*class="[^"]*\bshipping-option\b[^"]*"[^>]*>(.*?)</(?:label|div|tr)>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        name = _strip_tags(_class_inner(block, "option-name")) or _strip_tags(_class_inner(block, "shipping-name"))
        if not name:
            continue
        price_text = _class_inner(block, "option-price") or _class_inner(block, "shipping-price")
        price_match = re.search(r"\$\s?([\d,]+\.\d{2})", price_text or "")
        selected = bool(re.search(r"\bchecked\b|\bselected\b", block, flags=re.IGNORECASE))
        options.append(
            ShippingOption(
                name=name,
                price=float(price_match.group(1).replace(",", "")) if price_match else None,
                selected=selected,
            )
        )
    return options


def _class_inner(block: str, class_name: str) -> str | None:
    match = re.search(
        rf'<[^>]*class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</',
        block,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else None


def _shipping_method_name(html: str, options: list[ShippingOption]) -> str | None:
    selected = next((option for option in options if option.selected), None)
    if selected:
        return selected.name
    if options:
        return options[0].name
    match = re.search(
        r"(UPS Ground|UPS \w+(?: \w+)?|FedEx \w+|USPS [\w ]+?|Farm Truck Delivery|Local Delivery|Home Delivery|Pickup)",
        html,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _order_cycle_note(html: str) -> str | None:
    match = re.search(
        r"((?:order (?:cutoff|cycle|by)|next delivery|delivery window|cutoff)[^<]{0,80})",
        html,
        flags=re.IGNORECASE,
    )
    return _strip_tags(match.group(1)) if match else None


def cart_line_items(html: str) -> list[str]:
    """Titles of items currently in the cart (used to detect unrelated items)."""
    items: list[str] = []
    for block in re.findall(
        r'<(?:li|div|tr)[^>]*class="[^"]*\bcart-item\b[^"]*"[^>]*>(.*?)</(?:li|div|tr)>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        title = _strip_tags(_class_inner(block, "cart-item-title") or _class_inner(block, "item-title"))
        if title:
            items.append(title)
    return items


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _pre_extraction_status(html: str, final_url: str) -> tuple[str, str | None] | None:
    """Return (status, action_required) for a blocking gate, or None to proceed.

    Ordering follows the same precedence as page detection: captcha, login,
    pricing-lock, location/order-cycle, then the unsafe checkout boundary.
    """
    lower_html = html.lower()
    lower_url = (final_url or "").lower()

    if detected_markers(html, CAPTCHA_MARKERS):
        return ("captcha_required", "captcha_required")

    is_login_url = any(pattern in lower_url for pattern in LOGIN_URL_PATTERNS)
    if is_login_url or _has_password_input(html) or (
        not has_real_price_or_order_controls(html)
        and any(marker in lower_html for marker in LOGIN_MARKERS)
    ):
        # Only treat login words as a wall when there is no real cart content.
        if is_login_url or _has_password_input(html) or not has_real_price_or_order_controls(html):
            return ("login_required", "login_required")

    if pricing_lock_markers(html) and not has_real_price_or_order_controls(html):
        return ("pricing_locked", "pricing_locked")

    if store_access_markers(html):
        return ("location_or_order_cycle_required", "location_or_order_cycle_required")

    if detected_markers(html, UNSAFE_CHECKOUT_MARKERS):
        return ("unsafe_checkout_boundary", "unsafe_checkout_boundary")

    if detected_markers(html, CART_EMPTY_MARKERS):
        return ("cart_unavailable", "cart_unavailable")

    return None


_NEXT_ACTIONS = {
    "quote_available": "Review the captured shipping quote. No order was placed and no payment was submitted.",
    "login_required": "Log the vendor account in, then retry the shipping quote.",
    "captcha_required": "Clear the captcha/blocking challenge in the persistent browser profile, then retry.",
    "pricing_locked": "Pricing is locked behind an account/membership; unlock pricing before requesting a quote.",
    "store_access_required": "Set up store access before requesting a quote.",
    "location_or_order_cycle_required": "Select a delivery location / open an order cycle in the vendor account, then retry.",
    "cart_unavailable": "The vendor cart was empty or unavailable; add the intended items and retry.",
    "checkout_step_required": "Shipping is only shown after a checkout step; a human must advance safely before a quote can be read.",
    "unsafe_checkout_boundary": "Stopped before the checkout/payment confirmation step for safety. No order was placed.",
    "quote_unavailable": "No shipping/total lines were visible on the cart page; a real quote could not be captured.",
    "failed": "Shipping quote capture failed; inspect the saved artifacts and retry.",
}


def build_shipping_quote_from_html(
    html: str,
    vendor_id: str,
    source_url: str = "",
    *,
    final_url: str | None = None,
    account_authenticated: bool = True,
    fetch_ready: bool = True,
    intended_item_signatures: list[str] | None = None,
    shipping_quote_source: str = "cart_summary",
    artifact_dir=None,
) -> ShippingQuote:
    quote = ShippingQuote(
        vendor_id=vendor_id,
        shipping_quote_source=shipping_quote_source,
        quote_captured_at=datetime.now(timezone.utc).isoformat(),
    )

    gate = _pre_extraction_status(html, final_url or source_url)
    if gate is not None:
        quote.shipping_quote_status, quote.action_required = gate  # type: ignore[assignment]
    else:
        # Detect unrelated pre-existing cart items. We never delete them silently.
        items = cart_line_items(html)
        if intended_item_signatures is not None and items:
            intended = {_normalize(sig) for sig in intended_item_signatures}
            unrelated = [item for item in items if _normalize(item) not in intended]
            if unrelated:
                quote.unrelated_cart_items = True
                quote.quote_warnings.append(
                    "Vendor cart already contains unrelated items "
                    f"({', '.join(unrelated)}); they were left untouched and not deleted."
                )

        options = parse_shipping_options(html)
        quote.shipping_options = options
        quote.selected_shipping_method = _shipping_method_name(html, options)

        selected_option = next((option for option in options if option.selected), None)
        quote.shipping_price = (
            (selected_option.price if selected_option else None)
            or (options[0].price if options else None)
            or _amount_for_label(html, ["shipping", "ups ground", "ups shipping", "delivery fee"])
        )
        quote.cooler_fee = _amount_for_label(html, ["cooler fee", "cooler", "box fee", "insulated box"])
        quote.handling_fee = _amount_for_label(html, ["handling fee", "handling", "packing fee", "packing"])
        quote.cart_subtotal = _amount_for_label(html, ["subtotal", "sub total", "items subtotal"])
        quote.cart_tax = _amount_for_label(html, ["sales tax", "estimated tax", "tax"])
        quote.cart_quote_total = _amount_for_label(
            html, ["order total", "grand total", "cart total", "quote total", "total"]
        )
        quote.order_cycle_note = _order_cycle_note(html)

        has_quote = any(
            value is not None
            for value in (quote.shipping_price, quote.cart_quote_total, quote.cart_subtotal)
        ) or bool(options)
        quote.shipping_quote_status = "quote_available" if has_quote else "quote_unavailable"
        quote.action_required = None if has_quote else "quote_unavailable"

    # Readiness flags derive from the final status so callers get a truthful,
    # machine-readable readiness view.
    quote.account_authenticated = account_authenticated and quote.shipping_quote_status not in {
        "login_required",
        "captcha_required",
    }
    quote.fetch_ready = fetch_ready and quote.shipping_quote_status not in {
        "login_required",
        "captcha_required",
        "pricing_locked",
        "store_access_required",
        "location_or_order_cycle_required",
    }
    quote.recommended_next_action = _NEXT_ACTIONS.get(quote.shipping_quote_status)
    _save_artifacts(quote, html, artifact_dir)
    return quote


def _save_artifacts(quote: ShippingQuote, html: str, artifact_dir) -> None:
    if artifact_dir is None:
        return
    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    # Safe HTML snapshot + metadata only. No cookies/auth/payment data is written.
    (path / "cart.html").write_text(html, encoding="utf-8")
    quote.quote_artifact_path = str(path / "shipping_quote.json")
    (path / "shipping_quote.json").write_text(
        json.dumps(quote.model_dump(), indent=2), encoding="utf-8"
    )
