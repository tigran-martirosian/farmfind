"""Deterministic page status detection for login and block states."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .models import FetchStatus, VendorPageTarget

CAPTCHA_MARKERS = [
    "recaptcha",
    "reCAPTCHA verification failed",
    "g-recaptcha",
    "hcaptcha",
    "verify you are human",
    "captcha verification failed",
    "checking your browser",
    "cloudflare",
    "access denied",
    "unusual traffic",
    "captcha",
    "please contact support",
]
TWO_FACTOR_MARKERS = [
    "two-factor authentication",
    "two factor authentication",
    "2fa",
    "verification code",
    "one-time code",
    "one time code",
    "otp",
    "authenticator",
    "authenticator app",
    "security code",
]

PRODUCT_MARKERS = [
    "milk",
    "add to cart",
    "single_add_to_cart_button",
    "variations_form",
    "data-product_variations",
    "attribute_pa_size",
    "attribute_size",
    "attribute_pa_container",
    "attribute_container",
    "attribute_chilled",
    "chilled?",
    "woocommerce-product-add-to-cart",
    "product_title",
]

STRONG_LOGIN_WALL_TEXT = [
    "please log in to view",
    "you must be logged in",
    "log in to view",
    "sign in to view",
    "members must log in",
    "login required",
]

# Markers that mean a listing/detail page is hiding prices behind an account or
# membership. These must NOT be treated as a purchasable page. GrazeCart and
# similar stores show these when logged out or before pricing is unlocked.
PRICING_LOCK_MARKERS = [
    "sign up for pricing",
    "sign up to see price",
    "sign up to see pricing",
    "sign up to view price",
    "log in to see price",
    "login to see price",
    "log in to see pricing",
    "log in for pricing",
    "sign in to see price",
    "sign in for pricing",
    "register to see price",
    "become a member to see",
    "membership required for pricing",
    "contact for pricing",
    "call for pricing",
    "price available after login",
    "prices shown after login",
]

# Markers that mean the account is authenticated but the store still needs a
# location, pickup site, or an open order cycle before products are purchasable.
STORE_ACCESS_MARKERS = [
    "choose your location",
    "select your location",
    "select a location",
    "select your store",
    "select a pickup",
    "choose a pickup",
    "choose your pickup",
    "select your pickup",
    "start your order",
    "enter your zip",
    "enter your zip code",
    "order cycle",
    "ordering is closed",
    "ordering is currently closed",
    "store is closed",
    "next delivery",
    "delivery zone",
    "no active order",
]

# Controls that indicate a real, purchasable product/order surface.
ORDER_CONTROL_MARKERS = [
    "add to cart",
    "add to order",
    "add to basket",
    "select option",
    "select options",
    "sold out",
    "single_add_to_cart_button",
    "woocommerce-product-add-to-cart",
    "update order",
    "in your cart",
    "quantity",
    "qty",
]

LOGIN_URL_PATTERNS = ["/sign-in", "/signin", "/login", "/my-account", "/account/login"]


@dataclass(frozen=True)
class FetchDetection:
    status: FetchStatus
    classification_reason: str
    detected_product_markers: list[str]
    detected_login_markers: list[str]
    detected_captcha_markers: list[str]
    detected_two_factor_markers: list[str]


def detected_markers(html: str, markers: list[str]) -> list[str]:
    lower_html = html.lower()
    return [marker for marker in markers if marker.lower() in lower_html]


def two_factor_challenge_markers(html: str) -> list[str]:
    lower_html = html.lower()
    explicit = detected_markers(html, TWO_FACTOR_MARKERS)
    code_input = bool(
        re.search(
            r"<input\b[^>]*(?:name|id|autocomplete|placeholder)\s*=\s*['\"][^'\"]*"
            r"(?:otp|one[-\s]?time|verification[-\s]?code|security[-\s]?code|authenticator|2fa)"
            r"[^'\"]*['\"]",
            html,
            flags=re.IGNORECASE,
        )
    )
    if code_input:
        explicit.append("security code input")
    if "verify" in lower_html and not explicit:
        return []
    return sorted(set(explicit))


def _has_password_input(html: str) -> bool:
    return bool(re.search(r"<input\b[^>]*type\s*=\s*['\"]?password['\"]?", html, flags=re.IGNORECASE))


def _has_visible_login_form(html: str) -> bool:
    lower_html = html.lower()
    if not _has_password_input(html):
        return False
    return any(marker in lower_html for marker in ["log in", "login", "sign in", "username", "email"])


def _has_required_markers(target: VendorPageTarget, html: str) -> bool:
    if not target.required_text_markers:
        return False
    lower_html = html.lower()
    return all(marker.lower() in lower_html for marker in target.required_text_markers)


def _strong_product_markers(html: str) -> list[str]:
    return detected_markers(html, PRODUCT_MARKERS)


def _has_product_detail_signal(html: str, final_url: str) -> bool:
    lower_html = html.lower()
    lower_url = final_url.lower()
    if "/product/" in lower_url and any(
        marker in lower_html
        for marker in [
            "milk",
            "product_title",
            "woocommerce-product",
            "variations_form",
            "single_add_to_cart_button",
            "add to cart",
            "data-product_variations",
        ]
    ):
        return True
    if "form" in lower_html and "variations_form" in lower_html:
        return True
    if "add to cart" in lower_html and re.search(r"\$\s?\d", html):
        return True
    return False


STRONG_ORDER_CONTROLS = [
    "add to cart",
    "add to order",
    "add to basket",
    "select option",
    "select options",
    "sold out",
    "single_add_to_cart_button",
    "woocommerce-product-add-to-cart",
    "update order",
]


def _has_real_price(html: str) -> bool:
    return bool(re.search(r"\$\s?\d", html))


def _has_order_controls(html: str) -> bool:
    lower_html = html.lower()
    return any(marker in lower_html for marker in STRONG_ORDER_CONTROLS)


def has_real_price_or_order_controls(html: str) -> bool:
    """A page is purchasable only when it exposes a real price or an order control.

    Product *words* (milk, eggs, cheese, "add to cart" text in a locked template)
    are not enough, because logged-out GrazeCart cards contain product words but no real
    price and no working order control.
    """
    return _has_real_price(html) or _has_order_controls(html)


def has_priced_product_cards(html: str) -> bool:
    lower = html.lower()
    has_card = (
        "product-card" in lower
        or "product grid" in lower
        or "product-grid" in lower
        or "productlisting__" in lower
        or "schema.org/product" in lower
    )
    return bool(has_card and re.search(r"\$\s?\d", html) and _has_order_controls(html))


def pricing_lock_markers(html: str) -> list[str]:
    return detected_markers(html, PRICING_LOCK_MARKERS)


def store_access_markers(html: str) -> list[str]:
    return detected_markers(html, STORE_ACCESS_MARKERS)


# The passive reCAPTCHA v3 badge/disclaimer appears on many fully-usable pages
# (including logged-in WooCommerce accounts). It must never read as a captcha wall.
_BENIGN_CAPTCHA_PHRASES = [
    "protected by recaptcha",
    "recaptcha and the google privacy policy",
    "grecaptcha-badge",
    "this site is protected by recaptcha",
]


def strong_captcha_markers(html: str) -> list[str]:
    """Public, badge-safe captcha detection: a REAL captcha/blocking wall only."""
    cleaned = html
    for phrase in _BENIGN_CAPTCHA_PHRASES:
        cleaned = re.sub(re.escape(phrase), " ", cleaned, flags=re.IGNORECASE)
    return _strong_captcha_markers(cleaned)


def _strong_captcha_markers(html: str) -> list[str]:
    """Return captcha/block markers that look like an actual wall.

    WordPress/WooCommerce product pages can include reCAPTCHA JavaScript, CSS,
    or plugin class names even when the page is fully usable. Strip scripts and
    styles before looking for visible captcha text so those static dependencies
    do not turn a real product page into action_required.
    """
    lower_html = html.lower()
    strong: list[str] = []
    for marker in [
        "captcha verification failed",
        "recaptcha verification failed",
        "please contact support",
        "verify you are human",
        "checking your browser",
        "unusual traffic",
        "access denied",
        "cf-challenge",
        "cf-browser-verification",
        "hcaptcha-box",
    ]:
        if marker in lower_html:
            strong.append(marker)
    if re.search(r"<iframe\b[^>]+(?:recaptcha|hcaptcha)", html, flags=re.IGNORECASE):
        strong.append("captcha iframe")

    html_without_static_assets = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    html_without_static_assets = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        html_without_static_assets,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # A product page can contain recaptcha scripts. Treat g-recaptcha/hcaptcha as
    # blocking only when it appears as an actual widget/container or visible text.
    if re.search(
        r"class\s*=\s*['\"][^'\"]*(?:g-recaptcha|h-captcha)[^'\"]*['\"]",
        html_without_static_assets,
        flags=re.IGNORECASE,
    ):
        strong.append("captcha widget")
    visible_text = re.sub(r"<[^>]+>", " ", html_without_static_assets)
    if re.search(r"\b(?:g-recaptcha|hcaptcha|captcha)\b", visible_text, flags=re.IGNORECASE):
        strong.append("captcha marker")
    return sorted(set(strong))


def classify_fetch_status(
    target: VendorPageTarget,
    html: str,
    final_url: str,
    error_message: str | None = None,
) -> FetchDetection:
    if error_message:
        return FetchDetection(
            status="failed",
            classification_reason=f"Fetch error: {error_message}",
            detected_product_markers=[],
            detected_login_markers=[],
            detected_captcha_markers=[],
            detected_two_factor_markers=[],
        )

    lower_html = html.lower()
    lower_url = final_url.lower()
    product_markers = _strong_product_markers(html)
    captcha_markers = _strong_captcha_markers(html)
    two_factor_markers = two_factor_challenge_markers(html)
    login_markers = detected_markers(html, target.login_text_markers)
    required_markers_present = _has_required_markers(target, html)
    product_detail_present = required_markers_present or _has_product_detail_signal(html, final_url)

    is_login_url = target.requires_login and any(
        pattern.lower() in lower_url for pattern in target.login_url_patterns + LOGIN_URL_PATTERNS
    )
    login_wall_present = target.requires_login and (
        _has_visible_login_form(html) or any(marker in lower_html for marker in STRONG_LOGIN_WALL_TEXT)
    )
    has_real_controls = has_real_price_or_order_controls(html)

    if captcha_markers:
        return FetchDetection(
            status="captcha_required",
            classification_reason="Strong captcha/blocking wall markers found.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )
    if has_priced_product_cards(html):
        return FetchDetection(
            status="success",
            classification_reason="Priced product cards with order buttons found.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )
    if two_factor_markers:
        return FetchDetection(
            status="action_required",
            classification_reason="Two-factor/authentication challenge markers found.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )
    if is_login_url:
        return FetchDetection(
            status="login_required",
            classification_reason="Final URL is a configured login/account URL.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )
    if login_wall_present:
        return FetchDetection(
            status="login_required",
            classification_reason="Visible login wall/form markers found.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )

    # Pricing-locked: a listing/detail page advertises products but hides prices
    # behind an account/membership and exposes no real price or order control.
    # This must be action_required, never a fetch success.
    pricing_markers = pricing_lock_markers(html)
    if pricing_markers and not has_real_controls:
        return FetchDetection(
            status="pricing_locked",
            classification_reason="Pricing-lock markers found with no real price or order controls.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )

    # Store-access/location/order-cycle required: the account is authenticated but
    # the store still needs a location/pickup/open order cycle before purchasing.
    store_markers = store_access_markers(html)
    if store_markers and not has_real_controls:
        return FetchDetection(
            status="store_access_required",
            classification_reason="Store location/order-cycle markers found with no order controls.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )

    # Real product content wins over weak header/footer/login words such as
    # "Log Out", "My Account", or "Cart" only after true auth/blocking
    # states have been ruled out.
    if product_detail_present:
        reason = (
            "Required product markers found."
            if required_markers_present
            else "Product-detail HTML signals found."
        )
        return FetchDetection(
            status="success",
            classification_reason=reason,
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )
    if target.requires_login and login_markers:
        return FetchDetection(
            status="login_required",
            classification_reason="Login markers found and no product-detail markers were present.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )
    if required_markers_present:
        return FetchDetection(
            status="success",
            classification_reason="Required markers found.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )
    if target.required_text_markers:
        missing = [marker for marker in target.required_text_markers if marker.lower() not in lower_html]
        return FetchDetection(
            status="failed",
            classification_reason=f"Missing required marker(s): {', '.join(missing)}.",
            detected_product_markers=product_markers,
            detected_login_markers=login_markers,
            detected_captcha_markers=captcha_markers,
            detected_two_factor_markers=two_factor_markers,
        )
    return FetchDetection(
        status="success",
        classification_reason="No blocking markers found and no required markers configured.",
        detected_product_markers=product_markers,
        detected_login_markers=login_markers,
        detected_captcha_markers=captcha_markers,
        detected_two_factor_markers=two_factor_markers,
    )


def detect_fetch_status(
    target: VendorPageTarget,
    html: str,
    final_url: str,
    error_message: str | None = None,
) -> FetchStatus:
    return classify_fetch_status(target, html, final_url, error_message).status
