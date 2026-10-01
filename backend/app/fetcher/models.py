"""Models for configured vendor-page fetches and stored fetch metadata."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


FetchStatus = Literal[
    "success",
    "skipped_recent",
    "action_required",
    "auth_expired",
    "login_required",
    "captcha_required",
    "pricing_locked",
    "store_access_required",
    "manual_capture_required",
    "failed",
]
CaptureMethod = Literal[
    "playwright",
    "manual_import",
    "manual_browser",
    "credential_browser_login",
    "cdp_active_page",
    "cdp_controlled_fetch_page",
]
SessionMode = Literal["storage_state", "persistent_profile"]
AuthStrategy = Literal["credential_browser_login", "external_browser_handoff"]
CredentialStatus = Literal["configured", "not_configured", "needs_update"]
ConnectionStatus = Literal[
    "not_connected",
    "connected",
    "expired",
    "action_required",
    "failed",
]
ConnectionActionRequired = Literal[
    "login_required",
    "manual_login_required",
    "captcha_required",
    "two_factor_required",
    "credential_update_required",
    "reconnect_required",
    "verification_pending",
    "browser_not_found",
    "pricing_locked",
    "store_access_required",
    "location_or_order_cycle_required",
    "unsupported_layout",
    "parser_zero_candidates",
    "unsupported_vendor_parser",
    "fetch_failed",
    "connected_account_only",
    "cart_unavailable",
    "failed",
]


class VendorPageTarget(BaseModel):
    vendor_id: str
    page_id: str
    url: str
    requires_login: bool = False
    auth_state_label: str | None = None
    login_url_patterns: list[str] = []
    login_text_markers: list[str] = ["sign in", "log in", "login"]
    required_text_markers: list[str] = []


class FetchMetadata(BaseModel):
    vendor_id: str
    page_id: str
    url: str
    fetched_at: str
    requires_login: bool
    auth_state_label: str | None = None
    status: FetchStatus
    action_required: ConnectionActionRequired | None = None
    connection_status: ConnectionStatus | None = None
    capture_method: CaptureMethod = "playwright"
    http_status: int | None = None
    title: str | None = None
    page_title: str | None = None
    final_url: str | None = None
    source_url: str | None = None
    imported_at: str | None = None
    screenshot_path: str | None = None
    screenshot_viewport_path: str | None = None
    screenshot_full_path: str | None = None
    html_path: str | None = None
    text_path: str | None = None
    metadata_path: str | None = None
    error_message: str | None = None
    content_hash: str | None = None
    classification_reason: str | None = None
    page_kind: str | None = None
    product_title: str | None = None
    detected_product_markers: list[str] = []
    detected_login_markers: list[str] = []
    detected_captcha_markers: list[str] = []
    detected_two_factor_markers: list[str] = []
    detected_dropdowns: list[str] = []
    variant_count: int | None = None
    observation_path: str | None = None
    variant_snapshots_path: str | None = None
    text_excerpt: str | None = None
    recommended_next_action: str | None = None
    notes: str | None = None
    warnings: list[str] = []
    capture_mode: str | None = None
    selected_tab_url: str | None = None
    page_evidence: dict = {}
    coverage_report: dict = {}
    parser_status: str | None = None
    variant_count_total: int | None = None
    candidates_created_total: int | None = None


class VendorAccountConnection(BaseModel):
    vendor_id: str
    auth_state_label: str
    session_mode: SessionMode = "persistent_profile"
    browser_channel: str = "chrome"
    credential_status: CredentialStatus = "not_configured"
    credential_username_hint: str | None = None
    credential_storage_provider: str | None = None
    connection_status: ConnectionStatus = "not_connected"
    last_connected_at: str | None = None
    last_checked_at: str | None = None
    last_successful_fetch_at: str | None = None
    session_verified: bool = False
    store_fetch_ready: bool = False
    parser_ready: bool = False
    action_required: ConnectionActionRequired | None = None
    notes: str | None = None
    error_message: str | None = None
    login_url: str | None = None
    account_url: str | None = None
    created_at: str
    updated_at: str


class VendorConnectionConfig(BaseModel):
    vendor_id: str
    auth_state_label: str
    vendor_name: str | None = None
    login_url: str
    account_url: str | None = None
    session_mode: SessionMode = "persistent_profile"
    auth_strategy: AuthStrategy = "credential_browser_login"
    captcha_sensitive: bool = False
    browser_channel: str = "chrome"
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    username_selectors: list[str] = []
    password_selectors: list[str] = []
    submit_selectors: list[str] = []
    login_success_markers: list[str] = []
    login_failure_markers: list[str] = []
    captcha_markers: list[str] = []
    two_factor_markers: list[str] = []
    post_login_check_url: str | None = None
    min_fetch_interval_hours: int = 24


class ExternalBrowserSessionResult(BaseModel):
    """Secret-free result from a user-driven external Chrome session handoff."""

    vendor_id: str
    auth_strategy: AuthStrategy = "external_browser_handoff"
    profile_label: str
    account_authenticated: bool = False
    fetch_ready: bool = False
    action_required: ConnectionActionRequired | None = None
    recommended_next_action: str | None = None
    verified_at: str
    warnings: list[str] = []


LoginSessionStatus = Literal[
    "launched",
    "already_open",
    "already_connected",
    "verification_pending",
    "connected",
    "action_required",
    "failed",
]


class LoginSessionResult(BaseModel):
    """Secret-free result for a UI-launched manual login/session refresh."""

    vendor_id: str
    status: LoginSessionStatus
    action_required: ConnectionActionRequired | None = None
    recommended_next_action: str | None = None
    profile_label: str | None = None
    manual_command: str | None = None
    warnings: list[str] = []


class VendorCredentialMetadata(BaseModel):
    vendor_id: str
    username_hint: str | None = None
    credential_status: CredentialStatus = "not_configured"
    credential_storage_provider: str = "os_keyring"
    updated_at: str | None = None


class FetchServiceResult(BaseModel):
    status: str
    vendor_id: str
    page_id: str | None = None
    connection_status: ConnectionStatus | None = None
    action_required: ConnectionActionRequired | None = None
    html_path: str | None = None
    screenshot_path: str | None = None
    metadata_path: str | None = None
    content_hash: str | None = None
    warnings: list[str] = []
    error_message: str | None = None


class VendorFetchSummary(BaseModel):
    """Honest, machine-readable result of a POST .../fetch run.

    Future agents can read this directly instead of guessing from logs/HTML:
    whether the vendor is fetch-ready, why not, what was captured, and how many
    pending import candidates were actually created.
    """

    vendor_id: str
    connection_status: str | None = None
    action_required: str | None = None
    account_authenticated: bool = False
    fetch_ready: bool = False

    pages_attempted: int = 0
    pages_successful: int = 0
    pages_action_required: int = 0
    pages_failed: int = 0

    product_cards_found: int = 0
    detail_links_found: int = 0
    detail_pages_fetched: int = 0
    option_groups_found: int = 0
    bundle_pack_case_offers_found: int = 0
    excluded_count: int = 0
    deduped_already_approved_count: int = 0
    skipped_urls: list[dict] = []
    incomplete_discoveries: list[str] = []
    parser_warnings: list[str] = []

    variant_count_total: int = 0
    candidates_created_total: int = 0

    candidates_by_page: dict[str, int] = {}
    empty_candidate_pages: list[str] = []
    pricing_locked_pages: list[str] = []

    artifact_paths: dict[str, dict[str, str | None]] = {}

    warnings: list[str] = []
    recommended_next_action: str | None = None


FetchJobState = Literal[
    "queued",
    "running",
    "success",
    "failed",
    "action_required",
]


class FetchJobStatus(BaseModel):
    job_id: str
    vendor_id: str
    status: FetchJobState = "queued"
    current_step: str = "Queued"
    source_pages_attempted: int = 0
    source_pages_done: int = 0
    product_cards_found: int = 0
    detail_links_found: int = 0
    detail_pages_fetched: int = 0
    option_groups_found: int = 0
    variants_extracted: int = 0
    candidates_written: int = 0
    excluded_count: int = 0
    incomplete_discoveries: list[str] = []
    started_at: str | None = None
    finished_at: str | None = None
    message: str = "Fetch started"
    error_code: str | None = None
    summary: VendorFetchSummary | None = None


ShippingQuoteStatus = Literal[
    "quote_available",
    "login_required",
    "captcha_required",
    "pricing_locked",
    "store_access_required",
    "location_or_order_cycle_required",
    "cart_unavailable",
    "checkout_step_required",
    "quote_unavailable",
    "unsafe_checkout_boundary",
    "failed",
]


class ShippingOption(BaseModel):
    name: str
    price: float | None = None
    selected: bool = False


class ShippingQuote(BaseModel):
    """Safe shipping/cooler/handling quote captured from a vendor cart/checkout
    summary WITHOUT ever placing an order or submitting payment.

    Contains no secrets: never cookies, passwords, payment tokens, card data, or
    auth-storage paths.
    """

    vendor_id: str
    shipping_quote_source: str = "cart_summary"
    shipping_quote_status: ShippingQuoteStatus = "quote_unavailable"
    account_authenticated: bool = False
    fetch_ready: bool = False
    action_required: str | None = None
    shipping_options: list[ShippingOption] = []
    selected_shipping_method: str | None = None
    shipping_price: float | None = None
    cooler_fee: float | None = None
    handling_fee: float | None = None
    cart_subtotal: float | None = None
    cart_tax: float | None = None
    cart_quote_total: float | None = None
    order_cycle_note: str | None = None
    unrelated_cart_items: bool = False
    quote_captured_at: str | None = None
    quote_artifact_path: str | None = None
    quote_warnings: list[str] = []
    recommended_next_action: str | None = None


class VendorConnectionMetadata(BaseModel):
    """Sanitized vendor workflow metadata for UI debugging."""

    vendor_id: str
    display_name: str | None = None
    auth_strategy: AuthStrategy = "credential_browser_login"
    credentials_configured: bool = False
    credential_status: CredentialStatus = "not_configured"
    connection_status: ConnectionStatus = "not_connected"
    action_required: ConnectionActionRequired | None = None
    last_connected: str | None = None
    last_checked: str | None = None
    last_fetch: str | None = None
    latest_fetch_status: str | None = None
    latest_fetch_reason: str | None = None
    session_verified: bool = False
    store_fetch_ready: bool = False
    parser_ready: bool = False
    import_candidates_created: int = 0
    fetch_ready: bool = False
    pages_successful: int = 0
    variant_count_total: int = 0
    candidates_created_total: int = 0
    empty_candidate_pages: list[str] = []
    quote_configured: bool = False
    latest_quote_status: str | None = None
    latest_quote_reason: str | None = None
    recommended_next_action: str | None = None
    profile_label: str | None = None
    notes: str | None = None
    error_message: str | None = None


class FetchArtifacts(BaseModel):
    metadata: FetchMetadata
    html: str = ""
    text: str = ""
    screenshot_bytes: bytes | None = Field(default=None, exclude=True)
    screenshot_viewport_bytes: bytes | None = Field(default=None, exclude=True)
    screenshot_full_bytes: bytes | None = Field(default=None, exclude=True)


PageKind = Literal[
    "product_detail",
    "category_listing",
    "cart",
    "checkout",
    "account",
    "login",
    "unknown",
]
StockStatus = Literal["in_stock", "out_of_stock", "unknown"]


class DetectedButton(BaseModel):
    text: str
    selector_hint: str | None = None
    is_dangerous_final_submit: bool = False


class DetectedDropdown(BaseModel):
    label: str | None = None
    name: str | None = None
    selector_hint: str | None = None
    options: list[str] = []


class DetectedInput(BaseModel):
    label: str | None = None
    name: str | None = None
    type: str | None = None
    selector_hint: str | None = None


class DetectedForm(BaseModel):
    selector_hint: str | None = None
    inputs: list[DetectedInput] = []


class DetectedLink(BaseModel):
    text: str
    href: str | None = None
    selector_hint: str | None = None


class BrowserPageObservation(BaseModel):
    vendor_id: str
    page_id: str
    source_url: str | None = None
    final_url: str | None = None
    page_title: str | None = None
    page_kind: PageKind = "unknown"
    visible_text_excerpt: str = ""
    detected_product_title: str | None = None
    detected_prices: list[str] = []
    detected_stock_texts: list[str] = []
    detected_buttons: list[DetectedButton] = []
    detected_links: list[DetectedLink] = []
    detected_dropdowns: list[DetectedDropdown] = []
    detected_forms: list[DetectedForm] = []
    detected_inputs: list[DetectedInput] = []
    detected_product_markers: list[str] = []
    detected_login_markers: list[str] = []
    detected_captcha_markers: list[str] = []
    detected_two_factor_markers: list[str] = []
    screenshot_viewport_path: str | None = None
    screenshot_full_path: str | None = None
    html_path: str | None = None
    text_path: str | None = None
    warnings: list[str] = []


class CapturedVariantOption(BaseModel):
    name: str
    value: str


class OfferEvidenceBundle(BaseModel):
    """Central evidence contract for one orderable offer.

    This is the read-only bundle future vendor agents should reason over. It
    intentionally carries safe page/card/detail/option text, never cookies,
    tokens, auth headers, or browser profile paths.
    """

    vendor_id: str | None = None
    source_page_id: str | None = None
    source_url: str | None = None
    final_url: str | None = None
    discovery_source: str | None = None
    product_card_title: str | None = None
    product_card_subtitle: str | None = None
    product_card_secondary_text: str | None = None
    product_card_text: str | None = None
    product_card_hidden_option_text: str | None = None
    product_card_price_text: str | None = None
    product_card_button_text: str | None = None
    product_card_badges: list[str] = []
    product_detail_url: str | None = None
    product_detail_title: str | None = None
    product_detail_text: str | None = None
    option_label: str | None = None
    option_row_text: str | None = None
    option_price_text: str | None = None
    option_savings_text: str | None = None
    embedded_json_text: str | None = None
    related_link_texts: list[str] = []
    evidence_checkpoints_seen: list[str] = []
    parser_warnings: list[str] = []
    identity_text: str | None = None
    package_text: str | None = None
    price_text: str | None = None
    classification_text: str | None = None
    context_text: str | None = None


class OfferRecoveryTrace(BaseModel):
    attempted: bool = False
    succeeded: bool = False
    checked_sources: list[str] = []
    raw_text_snippets: dict[str, str] = {}
    missing_fields: list[str] = []
    stopped_reason: str | None = None


class CapturedProductVariant(BaseModel):
    variant_id: str | None = None
    product_title: str
    attributes: list[CapturedVariantOption] = []
    price_text: str | None = None
    price: float | None = None
    regular_price: float | None = None
    sale_price: float | None = None
    stock_text: str | None = None
    stock_quantity: int | None = None
    stock_status: StockStatus = "unknown"
    sku: str | None = None
    raw_variation_data: dict | None = None
    selected_html_excerpt: str | None = None
    inferred_product_type: str | None = None
    inferred_package_size: float | None = None
    inferred_unit: str | None = None
    inferred_packaging: str | None = None
    inferred_storage_state: str | None = None
    confidence: float = 0.5
    # GrazeCart-style selectable/bundle option fields. total_price is what the
    # website charges for the option; unit_price/computed_savings are computed by
    # us deterministically. reported_savings_text is the site's own claim and is
    # never used for optimizer math.
    option_label: str | None = None
    is_bundle: bool = False
    bundle_quantity: float | None = None
    bundle_unit: str | None = None
    total_price: float | None = None
    unit_price: float | None = None
    base_unit_price: float | None = None
    reported_savings_text: str | None = None
    computed_savings: float | None = None
    savings_mismatch_warning: bool = False
    quantity_evidence: dict | None = None
    price_evidence: dict | None = None
    savings_evidence: dict | None = None
    discovery_source: str | None = None
    incomplete_discovery: bool = False
    incomplete_reason: str | None = None
    evidence_checkpoints: list[str] = []
    validation_reasons: list[str] = []
    recovery_attempted: bool = False
    recovery_succeeded: bool = False
    offer_evidence: OfferEvidenceBundle | None = None
    recovery_trace: OfferRecoveryTrace | None = None
    warnings: list[str] = []


class GrazeCartProductCard(BaseModel):
    """A diagnostic product card scraped from a GrazeCart-style listing page.

    Cards (especially pricing-locked ones) are captured for diagnostics only.
    They never become purchasable variants/candidates unless a product-detail
    page exposes real prices/order controls.
    """

    title: str
    subtitle: str | None = None
    secondary_line: str | None = None
    description: str | None = None
    product_url: str | None = None
    brand: str | None = None
    badges: list[str] = []
    price_text: str | None = None
    price: float | None = None
    pricing_locked: bool = False
    stock_status: StockStatus = "unknown"
    order_button_text: str | None = None
    full_text: str | None = None
    hidden_option_text: str | None = None
    option_rows: list[dict] = []
    html_snippet: str | None = None
    savings_text: str | None = None
    needs_variant_detail: bool = False
    gift_amount_visible: bool = False
    inferred_product_type: str | None = None
    inferred_package_size: float | None = None
    inferred_unit: str | None = None
    supported: bool = True
    warnings: list[str] = []


class CapturedProductPage(BaseModel):
    vendor_id: str
    page_id: str
    source_url: str
    final_url: str | None = None
    product_title: str | None = None
    page_kind: PageKind = "unknown"
    base_price_text: str | None = None
    description_text: str = ""
    category_text: str = ""
    attributes_available: list[str] = []
    variants: list[CapturedProductVariant] = []
    product_cards: list[GrazeCartProductCard] = []
    pricing_locked: bool = False
    raw_html_path: str | None = None
    text_path: str | None = None
    screenshot_viewport_path: str | None = None
    screenshot_full_path: str | None = None
    observation_path: str | None = None
    captured_at: str
    warnings: list[str] = []
    coverage_report: dict = {}
