"""Tests for vendor page fetcher foundation."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.fetcher.account_connections import (
    classify_connection_result,
    connection_for_vendor,
    find_first_visible_selector,
    find_login_frame_and_selectors,
    login_vendor_with_saved_credentials,
    new_connection,
    safe_detect_markers,
    safe_page_title,
    safe_page_text,
    safe_page_url,
    verify_logged_in_state,
    check_vendor_connection,
    update_connection_status,
)
from app.fetcher.credentials import (
    delete_vendor_credentials,
    get_vendor_credential_status,
    list_vendor_credentials,
    set_vendor_credentials,
)
from app.fetcher.models import (
    FetchArtifacts,
    FetchMetadata,
    VendorCredentialMetadata,
    VendorConnectionConfig,
    VendorAccountConnection,
    VendorPageTarget,
)
from app.fetcher.grazecart import build_grazecart_captured_page
from app.fetcher.manual_capture import import_manual_capture
from app.fetcher.storage import (
    VENDOR_PAGES_PATH,
    auth_state_path,
    content_hash,
    fetch_output_dir,
    load_vendor_pages,
    load_vendor_account_configs,
    login_attempt_output_dir,
    persistent_profile_path,
    write_fetch_artifacts,
)
from app.fetcher.vendor_fetcher import detect_fetch_status, fetch_all_connected_vendor_pages, fetch_vendor_page
from app.main import app


def _target(**updates) -> VendorPageTarget:
    data = {
        "vendor_id": "miller_dairy",
        "page_id": "products",
        "url": "https://example.test/products",
        "requires_login": True,
        "auth_state_label": "miller_dairy",
        "login_url_patterns": ["/login"],
        "login_text_markers": ["sign in", "log in"],
        "required_text_markers": ["Products"],
    }
    data.update(updates)
    return VendorPageTarget(**data)


def _metadata(target: VendorPageTarget) -> FetchMetadata:
    from datetime import datetime, timezone

    return FetchMetadata(
        vendor_id=target.vendor_id,
        page_id=target.page_id,
        url=target.url,
        # Use a current timestamp so "recently fetched" skip logic is not brittle
        # to the calendar date rolling past the fetch-interval window.
        fetched_at=datetime.now(timezone.utc).isoformat(),
        requires_login=target.requires_login,
        auth_state_label=target.auth_state_label,
        status="success",
        title="Products",
        final_url=target.url,
    )


def test_vendor_pages_config_loading(tmp_path):
    path = tmp_path / "vendor_pages.json"
    path.write_text(
        json.dumps(
            [
                {
                    "vendor_id": "miller_dairy",
                    "page_id": "products",
                    "url": "https://example.test/products",
                    "requires_login": True,
                    "auth_state_label": "miller_dairy",
                }
            ]
        ),
        encoding="utf-8",
    )
    targets = load_vendor_pages(path)
    assert len(targets) == 1
    assert targets[0].vendor_id == "miller_dairy"
    assert targets[0].requires_login is True


def test_real_vendor_pages_config_has_first_four_farms():
    targets = load_vendor_pages(VENDOR_PAGES_PATH)
    assert len(targets) == 21
    expected_ids = {
        "vendor_a",
        "vendor_c",
        "vendor_d",
        "vendor_b",
    }
    assert {target.vendor_id for target in targets} == expected_ids
    assert all(target.requires_login for target in targets)
    assert all(target.auth_state_label == target.vendor_id for target in targets)
    for target in targets:
        assert {"/login", "sign-in", "signin", "account"} <= set(target.login_url_patterns)
        assert {"sign in", "log in", "login", "password"} <= set(target.login_text_markers)
        assert target.required_text_markers


def test_content_hash_generation_is_stable():
    assert content_hash("hello") == content_hash(b"hello")
    assert content_hash("hello") != content_hash("goodbye")


def test_vendor_connection_metadata_model_defaults():
    connection = new_connection("vendor_a")
    assert isinstance(connection, VendorAccountConnection)
    assert connection.vendor_id == "vendor_a"
    assert connection.auth_state_label == "vendor_a"
    assert connection.session_mode == "persistent_profile"
    assert connection.connection_status == "not_connected"
    assert connection.action_required == "login_required"
    assert connection.login_url == "https://example-farm-a.test/sign-in/"


def test_persistent_profile_path_generation_and_gitignore():
    profile_path = persistent_profile_path("vendor_a")
    assert ".browser_profiles" in profile_path.parts
    assert profile_path.name == "vendor_a"
    gitignore = (VENDOR_PAGES_PATH.parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert "backend/.browser_profiles/" in gitignore


def test_connection_classification_statuses():
    target = _target()
    assert classify_connection_result(target, "<h1>Products</h1>", target.url) == (
        "connected",
        None,
    )
    assert classify_connection_result(target, "<button>Sign in</button>", target.url) == (
        "action_required",
        "login_required",
    )
    assert classify_connection_result(target, "<div>g-recaptcha</div>", target.url) == (
        "action_required",
        "captcha_required",
    )


def test_login_required_detection_from_login_url_and_html():
    target = _target()
    assert detect_fetch_status(target, "<h1>Products</h1>", "https://example.test/login") == "login_required"
    assert detect_fetch_status(target, "<button>Sign in</button>", target.url) == "login_required"


def test_captcha_marker_detection_marks_captcha_required():
    target = _target()
    html = "<html><div class='g-recaptcha'>Verify you are human</div></html>"
    assert detect_fetch_status(target, html, target.url) == "captcha_required"


def test_required_marker_missing_is_failed():
    target = _target(required_text_markers=["Products"])
    assert detect_fetch_status(target, "<h1>Welcome</h1>", target.url) == "failed"


def test_product_page_with_log_out_header_still_classifies_success():
    target = _target(required_text_markers=["Milk", "$", "Size", "Container", "Chilled?", "Add to Cart"])
    html = """
    <header><a>Log Out</a></header>
    <main>
      <h1>Milk</h1>
      <p>$9.50</p>
      <label>Size</label>
      <label>Container</label>
      <label>Chilled?</label>
      <button>Add to Cart</button>
    </main>
    """
    assert detect_fetch_status(target, html, target.url) == "success"


def test_login_form_classifies_login_required():
    target = _target(required_text_markers=["Milk"])
    html = "<form><input name='username'><input type='password'><button>Log in</button></form>"
    assert detect_fetch_status(target, html, target.url) == "login_required"


def test_missing_required_markers_without_login_wall_is_failed():
    target = _target(required_text_markers=["Milk", "Add to Cart"])
    html = "<main><h1>Unavailable</h1><p>Farm product page</p></main>"
    assert detect_fetch_status(target, html, target.url) == "failed"


def test_pricing_locked_category_page_is_not_success():
    target = _target(required_text_markers=[])
    html = """
    <main>
      <div class="product-card"><h3>1 GALLON Milk</h3>
        <span>Sign up for pricing</span></div>
      <div class="product-card"><h3>Quart Milk</h3>
        <span>Sign up for pricing</span></div>
    </main>
    """
    assert detect_fetch_status(target, html, "https://grazecart.test/milk") == "pricing_locked"


def test_priced_product_cards_with_order_buttons_are_success():
    target = _target(required_text_markers=[])
    html = """
    <main class="product-grid">
      <div class="product-card"><h3>1/2 Gallon Milk</h3><span>$9.85</span><button>Add to Order</button></div>
      <div class="product-card"><h3>Butter 12 oz</h3><span>$17.25</span><button>Select Option</button></div>
      <div class="product-card"><h3>Colostrum</h3><span>$16.95</span><button>Sold Out</button></div>
    </main>
    <footer>Please select your location to start your order.</footer>
    """
    assert detect_fetch_status(target, html, "https://grazecart.test/milk") == "success"


def test_vendor_d_gift_amount_product_cards_are_success():
    target = _target(required_text_markers=[])
    html = """
    <main class="product-grid">
      <div class="product-card">
        <h3>4 Gallon Bundle Cow Milk in Plastic</h3>
        <span>Gift Amount</span><span>$44.60</span>
        <button>Select Option</button>
      </div>
      <div class="product-card">
        <h3>Camembert Cheese: 1/3lb wheel</h3>
        <span>Gift Amount</span><span>$11.55</span>
        <button>Add to Order</button>
      </div>
    </main>
    """
    assert detect_fetch_status(target, html, "https://example-farm-d.test/dairy") == "success"


def _listing_card(title: str, price: str, button: str, description: str = "", availability: str = "InStock") -> str:
    return f"""
    <section itemscope itemtype="https://schema.org/Product" class="tw-flex tw-flex-col">
      <a href="/store/product/item" title="{title}" class="productListing__photoLink--grid">
        <h3 itemprop="name">{title}</h3>
      </a>
      <div itemprop="description"><p>{description}</p></div>
      <div class="productListing__price--grid" itemprop="offers" itemscope itemtype="https://schema.org/Offer">
        <link itemprop="availability" href="https://schema.org/{availability}">
        <div class="productListing__salePrice">$<span itemprop="price">{price}</span></div>
      </div>
      <div class="productListing__addToCart--grid">
        <button class="btn productListing__addToCartButton--grid variants-dropdown-toggle">{button}</button>
      </div>
    </section>
    """


def test_vendor_b_product_listing_markup_creates_milk_variants():
    html = _listing_card(
        "1/2 Gallon GLASS Milk",
        "9.85",
        "Add to Order",
        "1/2 Gallon Glass - Includes bottle deposit",
    ) + _listing_card(
        "1/2 Gallon Milk",
        "6.75",
        "Select Option",
        "1/2 Gallon",
    )

    captured = build_grazecart_captured_page(html, "vendor_b", "milk", "https://example-farm-b.test/shop/milk")

    assert len(captured.variants) == 2
    assert {variant.inferred_product_type for variant in captured.variants} == {"cow_milk"}
    assert {variant.inferred_package_size for variant in captured.variants} == {0.5}
    assert captured.variants[0].inferred_packaging == "glass"
    assert captured.variants[1].stock_status == "in_stock"
    assert "needs_variant_detail" in " ".join(captured.variants[1].warnings)


def test_vendor_c_product_listing_markup_creates_milk_and_butter_variants():
    html = _listing_card(
        "Milk, 1/2 Gal. (Plastic)",
        "6.25",
        "Add to Cart",
    ) + _listing_card(
        "Salted Frozen Spring Butter 12 oz (Glass)",
        "17.25",
        "Select Option",
    )

    captured = build_grazecart_captured_page(html, "vendor_c", "dairy", "https://example-farm-c.test/shop/dairy")

    variants = {variant.inferred_product_type: variant for variant in captured.variants}
    assert variants["cow_milk"].inferred_package_size == 0.5
    assert variants["cow_milk"].inferred_packaging == "plastic"
    assert variants["butter"].inferred_package_size == 0.75
    assert variants["butter"].inferred_unit == "lb"
    assert variants["butter"].inferred_packaging == "glass"
    assert variants["butter"].inferred_storage_state == "frozen"


def test_vendor_d_product_listing_markup_parses_gift_amount_prices_and_cheese():
    html = """
    <section itemscope itemtype="https://schema.org/Product" class="tw-flex tw-flex-col">
      <h3 itemprop="name">4 Gallon Bundle Cow Milk in Plastic</h3>
      <div class="productListing__salePrice">Gift Amount $<span itemprop="price">44.60</span></div>
      <button class="variants-dropdown-toggle">Select Option</button>
    </section>
    <section itemscope itemtype="https://schema.org/Product" class="tw-flex tw-flex-col">
      <h3 itemprop="name">Cheddar Cheese: 1 pound</h3>
      <div class="productListing__salePrice">Gift Amount $<span itemprop="price">13.50</span></div>
      <button class="variants-dropdown-toggle">Select Option</button>
    </section>
    <section itemscope itemtype="https://schema.org/Product" class="tw-flex tw-flex-col">
      <h3 itemprop="name">Colby Cheese: 1 pound</h3>
      <div class="productListing__salePrice">Gift Amount $<span itemprop="price">13.20</span></div>
      <button>Add to Order</button>
    </section>
    """

    captured = build_grazecart_captured_page(html, "vendor_d", "dairy_page", "https://example-farm-d.test/shop/dairy")

    assert [variant.price for variant in captured.variants] == [44.60, 13.50, 13.20]
    assert captured.variants[0].inferred_product_type == "cow_milk"
    assert captured.variants[0].inferred_package_size == 4.0
    assert captured.variants[1].inferred_product_type == "cheese"
    assert captured.variants[1].inferred_package_size == 1.0


def test_sold_out_product_listing_maps_to_out_of_stock_without_blocking_supported_cards():
    html = _listing_card("12 oz Colostrum (Regular)-Frozen", "16.95", "Sold Out", availability="OutOfStock") + _listing_card(
        "1/2 Gallon GLASS Milk",
        "9.85",
        "Add to Order",
    )

    captured = build_grazecart_captured_page(html, "vendor_b", "milk", "https://example-farm-b.test/shop/milk")

    assert any(variant.stock_status == "out_of_stock" for variant in captured.variants)
    assert any(variant.inferred_product_type == "cow_milk" for variant in captured.variants)


def test_product_listing_markup_with_prices_and_buttons_classifies_success():
    target = _target(required_text_markers=[])
    html = _listing_card("1/2 Gallon Milk", "6.75", "Select Option")

    assert detect_fetch_status(target, html, "https://example-farm-b.test/shop/milk") == "success"


def test_vendor_c_product_listing_with_verify_text_is_not_two_factor_required():
    target = _target(
        vendor_id="vendor_c",
        required_text_markers=[],
        url="https://example-farm-c.test/shop/dairy",
    )
    html = _listing_card("Milk, 1/2 Gal. (Plastic)", "6.25", "Add to Cart") + "<footer>Verify your account details.</footer>"

    assert detect_fetch_status(target, html, "https://example-farm-c.test/shop/dairy") == "success"


def test_real_otp_code_input_triggers_two_factor_required():
    target = _target(required_text_markers=[])
    html = '<form><label>Verification code</label><input name="otp_code"><button>Submit</button></form>'

    assert detect_fetch_status(target, html, "https://example.test/account") == "action_required"


def test_store_access_required_when_location_needed_and_no_controls():
    target = _target(required_text_markers=[])
    html = "<main><h2>Please select your location to start your order.</h2></main>"
    assert detect_fetch_status(target, html, target.url) == "store_access_required"


def test_product_words_with_real_price_and_cart_beats_pricing_footer():
    target = _target(required_text_markers=[])
    html = """
    <main>
      <h1>1 GALLON Milk</h1>
      <p class="price">$11.45</p>
      <button class="single_add_to_cart_button">Add to Cart</button>
    </main>
    <footer>New here? Sign up for pricing.</footer>
    """
    assert detect_fetch_status(target, html, "https://grazecart.test/product/milk") == "success"


def test_login_url_with_pricing_and_product_words_is_login_required():
    target = _target(required_text_markers=[])
    html = "<main><h1>Milk</h1><span>Sign up for pricing</span></main>"
    assert detect_fetch_status(target, html, "https://grazecart.test/login") == "login_required"


def test_captcha_beats_pricing_lock():
    target = _target(required_text_markers=[])
    html = "<div class='g-recaptcha'>Verify you are human</div><span>Sign up for pricing</span>"
    assert detect_fetch_status(target, html, target.url) == "captcha_required"


def test_captcha_failure_text_beats_login_2fa_and_product_markers():
    target = _target(required_text_markers=["Dairy"])
    html = """
    <main>
      <h1>Dairy</h1>
      <button>Add to Cart</button>
      <form><input type="password"><button>Log in</button></form>
      <p>Verification code</p>
      <p>The captcha verification failed. Please contact support.</p>
    </main>
    """
    assert detect_fetch_status(target, html, target.url) == "captcha_required"


def test_recaptcha_failure_text_marks_captcha_required():
    target = _target(required_text_markers=["Browse Our Food"])
    html = "<main>Browse Our Food reCAPTCHA verification failed. Please contact support.</main>"
    assert detect_fetch_status(target, html, target.url) == "captcha_required"


def test_pricing_locked_maps_to_connection_action_required():
    target = _target(required_text_markers=[])
    html = "<main><h3>Milk</h3><span>Sign up for pricing</span></main>"
    assert classify_connection_result(target, html, target.url) == (
        "action_required",
        "pricing_locked",
    )


def test_fetcher_returns_reconnect_required_without_connection(tmp_path, monkeypatch):
    target = _target(vendor_id="vendor_a", auth_state_label="vendor_a")
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: FakeKeyring())
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.account_connections.get_vendor_connection", lambda vendor_id: None)
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)

    metadata = fetch_vendor_page(target)

    assert metadata.status == "action_required"
    assert metadata.action_required == "credential_update_required"
    assert metadata.connection_status == "action_required"
    assert metadata.html_path is not None
    assert (tmp_path / "fetches").exists()


def test_batch_fetch_continues_when_one_vendor_requires_reconnect(tmp_path, monkeypatch):
    targets = [
        _target(vendor_id="vendor_a", auth_state_label="vendor_a"),
        _target(vendor_id="vendor_b", auth_state_label="vendor_b"),
    ]
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: FakeKeyring())
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.account_connections.get_vendor_connection", lambda vendor_id: None)
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)

    results = fetch_all_connected_vendor_pages(targets)

    assert len(results) == 2
    assert {result.vendor_id for result in results} == {
        "vendor_a",
        "vendor_b",
    }
    assert all(result.status == "action_required" for result in results)


def test_fetch_storage_paths_and_metadata_creation(tmp_path):
    target = _target()
    output_dir = fetch_output_dir(target, "2026-07-06T181500")
    assert str(output_dir).endswith("miller_dairy\\products\\2026-07-06T181500") or str(output_dir).endswith("miller_dairy/products/2026-07-06T181500")

    metadata = write_fetch_artifacts(
        target,
        FetchArtifacts(
            metadata=_metadata(target),
            html="<html><title>Products</title></html>",
            text="Products",
            screenshot_viewport_bytes=b"viewport",
            screenshot_full_bytes=b"full",
        ),
        output_dir=tmp_path / "fetch",
    )
    assert metadata.content_hash == content_hash("<html><title>Products</title></html>")
    assert metadata.html_path is not None
    assert metadata.text_path is not None
    assert metadata.screenshot_path is not None
    assert metadata.screenshot_viewport_path is not None
    assert metadata.screenshot_full_path is not None
    assert (tmp_path / "fetch" / "page.html").exists()
    assert (tmp_path / "fetch" / "page.txt").read_text(encoding="utf-8") == "Products"
    assert (tmp_path / "fetch" / "screenshot.png").exists()
    assert (tmp_path / "fetch" / "screenshot_viewport.png").read_bytes() == b"viewport"
    assert (tmp_path / "fetch" / "screenshot_full.png").read_bytes() == b"full"
    assert (tmp_path / "fetch" / "metadata.json").exists()


def test_all_configured_pages_can_create_metadata_artifacts(tmp_path):
    targets = load_vendor_pages(VENDOR_PAGES_PATH)
    for target in targets:
        metadata = write_fetch_artifacts(
            target,
            FetchArtifacts(
                metadata=_metadata(target),
                html=f"<html><title>{target.page_id}</title></html>",
            ),
            output_dir=tmp_path / target.vendor_id / target.page_id,
        )
        assert metadata.vendor_id == target.vendor_id
        assert metadata.page_id == target.page_id
        assert metadata.html_path is not None
        assert "storage_state" not in metadata.model_dump()


def test_auth_state_path_is_private_and_not_exposed_by_fetch_api(tmp_path):
    path = auth_state_path("miller_dairy")
    assert ".auth" in path.parts
    response = TestClient(app).get("/fetches")
    assert response.status_code == 200
    body = response.text
    assert ".auth" not in body
    assert "storage_state" not in body


def test_vendor_connections_api_exposes_metadata_only():
    response = TestClient(app).get("/vendor-connections")
    assert response.status_code == 200
    body = response.text
    assert "vendor_a" in body
    assert ".browser_profiles" not in body
    assert ".auth" not in body
    assert "cookie" not in body.lower()


def test_vendor_connection_detail_default_is_safe_metadata_only():
    response = TestClient(app).get("/vendor-connections/vendor_a")
    assert response.status_code == 200
    data = response.json()
    assert data["vendor_id"] == "vendor_a"
    assert data["session_mode"] == "persistent_profile"
    body = response.text.lower()
    assert ".browser_profiles" not in body
    assert ".auth" not in body
    assert "cookie" not in body
    assert "credential_status" in body
    assert "password" not in body


def test_vendor_connection_metadata_is_useful_and_secret_free():
    response = TestClient(app).get("/vendor-connections/vendor_a/metadata")
    assert response.status_code == 200
    data = response.json()
    body = response.text.lower()
    assert data["vendor_id"] == "vendor_a"
    assert data["auth_strategy"] in {"credential_browser_login", "external_browser_handoff"}
    assert "credentials_configured" in data
    assert "quote_configured" in data
    assert "fetch_ready" in data
    forbidden = ["cookie", "password", "token", ".auth", "browser_profiles", "external_browser_profiles"]
    assert not any(item in body for item in forbidden)


def test_vendor_connection_metadata_not_ready_when_latest_fetch_store_blocked(monkeypatch):
    from app.fetcher.services import vendor_connection_metadata

    connection = VendorAccountConnection(
        vendor_id="vendor_b",
        auth_state_label="vendor_b",
        connection_status="connected",
        session_verified=True,
        store_fetch_ready=True,
        parser_ready=True,
        created_at="2026-07-07T00:00:00+00:00",
        updated_at="2026-07-07T00:00:00+00:00",
    )
    monkeypatch.setattr("app.fetcher.services.connection_for_vendor", lambda vendor_id: connection)
    monkeypatch.setattr(
        "app.fetcher.services.connection_config_or_default",
        lambda vendor_id: VendorConnectionConfig(
            vendor_id=vendor_id,
            auth_state_label=vendor_id,
            vendor_name="Example Farm B",
            login_url="https://example.test/login",
        ),
    )
    monkeypatch.setattr(
        "app.fetcher.services.get_vendor_credential_status",
        lambda vendor_id: VendorCredentialMetadata(vendor_id=vendor_id, credential_status="configured"),
    )
    monkeypatch.setattr(
        "app.fetcher.services.list_fetch_metadata",
        lambda vendor_id: [
            _summary_meta(
                vendor_id,
                "milk",
                status="action_required",
                action_required="store_access_required",
                connection_status="action_required",
                classification_reason="Store location/order-cycle markers found with no order controls.",
            )
        ],
    )
    monkeypatch.setattr("app.fetcher.services._import_candidate_count", lambda vendor_id: 0)
    monkeypatch.setattr("app.fetcher.services._latest_quote", lambda vendor_id: None)
    monkeypatch.setattr("app.fetcher.services._cart_target_for", lambda vendor_id: None)

    metadata = vendor_connection_metadata("vendor_b")

    assert metadata.fetch_ready is False
    assert metadata.store_fetch_ready is False
    assert metadata.action_required == "store_access_required"
    assert "order-cycle" in (metadata.latest_fetch_reason or "")


def test_vendor_connection_metadata_not_ready_when_latest_fetch_zero_candidates(monkeypatch):
    from app.fetcher.services import vendor_connection_metadata

    connection = VendorAccountConnection(
        vendor_id="vendor_d",
        auth_state_label="vendor_d",
        connection_status="connected",
        session_verified=True,
        store_fetch_ready=True,
        parser_ready=False,
        created_at="2026-07-07T00:00:00+00:00",
        updated_at="2026-07-07T00:00:00+00:00",
    )
    monkeypatch.setattr("app.fetcher.services.connection_for_vendor", lambda vendor_id: connection)
    monkeypatch.setattr(
        "app.fetcher.services.connection_config_or_default",
        lambda vendor_id: VendorConnectionConfig(
            vendor_id=vendor_id,
            auth_state_label=vendor_id,
            vendor_name="Example Farm D",
            login_url="https://example.test/login",
            auth_strategy="external_browser_handoff",
        ),
    )
    monkeypatch.setattr(
        "app.fetcher.services.get_vendor_credential_status",
        lambda vendor_id: VendorCredentialMetadata(vendor_id=vendor_id, credential_status="configured"),
    )
    monkeypatch.setattr(
        "app.fetcher.services.list_fetch_metadata",
        lambda vendor_id: [
            _summary_meta(
                vendor_id,
                "dairy_page",
                status="success",
                connection_status="connected",
                variant_count=0,
                classification_reason="Required product markers found.",
            )
        ],
    )
    monkeypatch.setattr("app.fetcher.services._import_candidate_count", lambda vendor_id: 0)
    monkeypatch.setattr("app.fetcher.services._latest_quote", lambda vendor_id: None)
    monkeypatch.setattr("app.fetcher.services._cart_target_for", lambda vendor_id: None)

    metadata = vendor_connection_metadata("vendor_d")

    assert metadata.fetch_ready is False
    assert metadata.parser_ready is False
    assert metadata.action_required == "parser_zero_candidates"
    assert metadata.import_candidates_created == 0


def test_login_session_endpoint_launches_safe_credential_browser(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.storage.BROWSER_PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr("app.fetcher.external_browser_session.chrome_executable", lambda: "chrome.exe")
    monkeypatch.setattr(
        "app.fetcher.external_browser_session.subprocess.Popen",
        lambda args: calls.append(args),
    )

    response = TestClient(app).post("/vendor-connections/vendor_a/login-session")

    assert response.status_code == 200
    data = response.json()
    body = response.text.lower()
    assert data["vendor_id"] == "vendor_a"
    assert data["status"] == "launched"
    assert data["action_required"] == "login_required"
    assert data["profile_label"] == "farmfind_profile_vendor_a"
    assert calls
    assert any("--remote-debugging-port=" in arg for arg in calls[0])
    forbidden = ["cookie", "password", "token", ".auth", ".browser_profiles"]
    assert not any(item in body for item in forbidden)


def test_login_session_force_open_launches_even_when_saved_connected(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.storage.BROWSER_PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr("app.fetcher.external_browser_session.chrome_executable", lambda: "chrome.exe")
    monkeypatch.setattr(
        "app.fetcher.external_browser_session.subprocess.Popen",
        lambda args: calls.append(args),
    )
    update_connection_status(
        "vendor_a",
        status="connected",
        connected=True,
        session_verified=True,
        store_fetch_ready=True,
        parser_ready=True,
    )

    response = TestClient(app).post("/vendor-connections/vendor_a/login-session?force_open=true")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "launched"
    assert calls
    assert "cookie" not in response.text.lower()


def test_login_session_endpoint_uses_external_handoff_without_exposing_profile_path(tmp_path, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.storage.EXTERNAL_BROWSER_PROFILES_DIR", tmp_path / "external_profiles")
    monkeypatch.setattr(
        "app.fetcher.external_browser_session.launch_external_chrome",
        lambda **kwargs: calls.append(kwargs),
    )

    response = TestClient(app).post("/vendor-connections/vendor_c/login-session")

    assert response.status_code == 200
    data = response.json()
    body = response.text.lower()
    assert data["status"] == "launched"
    assert data["action_required"] == "manual_login_required"
    assert data["profile_label"] == "farmfind_external_vendor_c"
    assert calls and calls[0]["vendor_id"] == "vendor_c"
    forbidden = ["cookie", "password", "token", "external_browser_profiles", str(tmp_path).lower()]
    assert not any(item in body for item in forbidden)


def test_manual_capture_import_copies_html_and_screenshot(tmp_path, monkeypatch):
    html_path = tmp_path / "cow_milk.html"
    screenshot_path = tmp_path / "cow_milk.png"
    fetch_root = tmp_path / "fetches"
    html_path.write_text("<html><h1>Cow Milk</h1></html>", encoding="utf-8")
    screenshot_path.write_bytes(b"png")
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", fetch_root)

    metadata = import_manual_capture(
        vendor_id="vendor_a",
        page_id="cow_milk",
        source_url="https://example-farm-a.test/product/cow-milk/",
        html_path=str(html_path),
        screenshot_path=str(screenshot_path),
        notes="Saved by user after manual login.",
    )

    assert metadata.status == "success"
    assert metadata.capture_method == "manual_import"
    assert metadata.imported_at is not None
    assert metadata.content_hash == content_hash("<html><h1>Cow Milk</h1></html>")
    assert metadata.html_path is not None
    assert metadata.screenshot_path is not None
    assert "vendor_a" in metadata.html_path
    assert "cow_milk" in metadata.html_path


def test_manual_capture_import_screenshot_is_optional(tmp_path, monkeypatch):
    html_path = tmp_path / "cow_milk.html"
    html_path.write_text("<html><h1>Cow Milk</h1></html>", encoding="utf-8")
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")

    metadata = import_manual_capture(
        vendor_id="vendor_a",
        page_id="cow_milk",
        source_url="https://example-farm-a.test/product/cow-milk/",
        html_path=str(html_path),
    )

    assert metadata.capture_method == "manual_import"
    assert metadata.screenshot_path is None
    assert metadata.html_path is not None


def test_manual_capture_import_missing_html_fails_cleanly(tmp_path):
    missing = tmp_path / "missing.html"
    try:
        import_manual_capture(
            vendor_id="vendor_a",
            page_id="cow_milk",
            source_url="https://example-farm-a.test/product/cow-milk/",
            html_path=str(missing),
        )
    except FileNotFoundError as exc:
        assert "HTML file not found" in str(exc)
    else:
        raise AssertionError("missing HTML should raise FileNotFoundError")


def test_fetches_api_exposes_metadata_without_cookie_or_auth_contents(tmp_path, monkeypatch):
    html_path = tmp_path / "cow_milk.html"
    html_path.write_text("<html><script>document.cookie='secret'</script></html>", encoding="utf-8")
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    import_manual_capture(
        vendor_id="vendor_a",
        page_id="cow_milk",
        source_url="https://example-farm-a.test/product/cow-milk/",
        html_path=str(html_path),
    )

    response = TestClient(app).get("/fetches")
    assert response.status_code == 200
    body = response.text.lower()
    assert "manual_import" in body
    assert "cookie" not in body
    assert ".auth" not in body
    assert "storage_state" not in body


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def get_password(self, service, username):
        return self.values.get((service, username))

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


def test_credential_status_without_exposing_password(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")

    metadata = set_vendor_credentials(
        "vendor_a",
        "person@example.test",
        password="super-secret",
    )
    status = get_vendor_credential_status("vendor_a")

    assert metadata.credential_status == "configured"
    assert status.credential_status == "configured"
    assert status.username_hint == "pe***@example.test"
    dumped = status.model_dump_json()
    assert "super-secret" not in dumped
    assert "person@example.test" not in dumped


def test_credentials_list_never_exposes_passwords(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")

    set_vendor_credentials("vendor_a", "person@example.test", "super-secret")
    dumped = json.dumps([item.model_dump() for item in list_vendor_credentials()])

    assert "vendor_a" in dumped
    assert "pe***@example.test" in dumped
    assert "super-secret" not in dumped
    assert "person@example.test" not in dumped


def test_explicit_credentials_delete_removes_safe_metadata(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")

    set_vendor_credentials("vendor_a", "person@example.test", "super-secret")
    metadata = delete_vendor_credentials("vendor_a")

    assert metadata.credential_status == "not_configured"
    assert metadata.username_hint is None
    assert fake_keyring.get_password("farmfind.vendor-login", "vendor_a") is None


def test_missing_credentials_returns_credential_update_required(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: FakeKeyring())
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.account_connections.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json", raising=False)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")

    connection = update_connection_status(
        "vendor_a",
        status="action_required",
        action_required="credential_update_required",
    )

    assert connection.connection_status == "action_required"
    assert connection.action_required == "credential_update_required"
    assert connection.credential_status == "not_configured"


def test_vendor_login_config_parsing_has_credential_login_fields():
    configs = load_vendor_account_configs()
    assert {config.vendor_id for config in configs} == {
        "vendor_a",
        "vendor_c",
        "vendor_d",
        "vendor_b",
    }
    for config in configs:
        assert config.session_mode == "persistent_profile"
        assert config.browser_channel == "chrome"
        assert config.username_selectors
        assert config.password_selectors
        assert config.submit_selectors
        assert config.login_success_markers
        assert config.login_failure_markers
        assert config.captcha_markers
        assert config.two_factor_markers
        assert config.min_fetch_interval_hours == 24


def test_login_result_success_updates_connection_status(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: FakeKeyring())
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")

    connection = update_connection_status(
        "vendor_a",
        status="connected",
        connected=True,
        notes="Connected through credential browser login.",
    )

    assert connection.connection_status == "connected"
    assert connection.action_required is None
    assert connection.last_connected_at is not None


def test_captcha_and_2fa_markers_return_action_required():
    target = _target()
    config = load_vendor_account_configs()[0]

    assert classify_connection_result(target, "<div>g-recaptcha</div>", target.url, config) == (
        "action_required",
        "captcha_required",
    )
    assert classify_connection_result(target, "<div>Verification code</div>", target.url, config) == (
        "action_required",
        "two_factor_required",
    )


def test_non_bad_credential_failures_keep_credential_configured(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    for action_required in ["captcha_required", "two_factor_required", "login_required"]:
        update_connection_status(
            "vendor_a",
            status="action_required",
            action_required=action_required,
            error_message="Non-credential login issue.",
        )
        assert get_vendor_credential_status("vendor_a").credential_status == "configured"


def test_failed_login_updates_connection_status(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: FakeKeyring())
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")

    connection = update_connection_status(
        "vendor_a",
        status="failed",
        action_required=None,
        error_message="Invalid credentials.",
    )

    assert connection.connection_status == "failed"
    assert connection.error_message == "Invalid credentials."


def test_fetch_skips_recently_fetched_pages(tmp_path, monkeypatch):
    target = _target(vendor_id="vendor_a", auth_state_label="vendor_a")
    latest = _metadata(target)
    monkeypatch.setattr("app.fetcher.vendor_fetcher.latest_fetch_metadata", lambda vendor_id, page_id=None: latest)
    monkeypatch.setattr("app.fetcher.vendor_fetcher.vendor_account_config", lambda vendor_id: None)
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")

    metadata = fetch_vendor_page(target)

    assert metadata.status == "skipped_recent"
    assert metadata.capture_method == "credential_browser_login"


def test_force_bypasses_skipped_recent(tmp_path, monkeypatch):
    target = _target(vendor_id="vendor_a", auth_state_label="vendor_a")
    latest = _metadata(target)
    monkeypatch.setattr("app.fetcher.vendor_fetcher.latest_fetch_metadata", lambda vendor_id, page_id=None: latest)
    monkeypatch.setattr("app.fetcher.vendor_fetcher.connected_profile_ready", lambda vendor_id: False)
    monkeypatch.setattr(
        "app.fetcher.vendor_fetcher.ensure_vendor_connected",
        lambda vendor_id: new_connection(
            vendor_id,
            status="action_required",
            action_required="credential_update_required",
        ),
    )
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")

    metadata = fetch_vendor_page(target, force_refresh=True)

    assert metadata.status == "action_required"
    assert metadata.action_required == "credential_update_required"


def test_fetch_metadata_uses_credential_browser_login_capture_method(tmp_path):
    target = _target()
    metadata = write_fetch_artifacts(
        target,
        FetchArtifacts(
            metadata=_metadata(target).model_copy(
                update={"capture_method": "credential_browser_login"}
            ),
            html="<html><h1>Products</h1></html>",
        ),
        output_dir=tmp_path / "fetch",
    )

    assert metadata.capture_method == "credential_browser_login"
    assert metadata.metadata_path is not None


def test_vendor_fetch_api_starts_safe_background_job(monkeypatch):
    import time

    from app.fetcher.models import VendorFetchSummary
    from app.fetcher.fetch_jobs import _reset_for_tests

    _reset_for_tests()
    monkeypatch.setattr(
        "app.fetcher.fetch_jobs.run_vendor_fetch_summary",
        lambda vendor_id, force=False: VendorFetchSummary(
            vendor_id=vendor_id,
            connection_status="action_required",
            action_required="login_required",
            fetch_ready=False,
            pages_attempted=1,
            pages_action_required=1,
            candidates_created_total=0,
            recommended_next_action="Resolve the vendor connection issue, then retry the fetch.",
        ),
    )

    response = TestClient(app).post("/vendor-connections/vendor_a/fetch")

    assert response.status_code == 200
    payload = response.json()
    assert payload["vendor_id"] == "vendor_a"
    assert payload["status"] in {"queued", "running", "action_required"}
    client = TestClient(app)
    job = client.get(f"/vendor-connections/vendor_a/fetch-jobs/{payload['job_id']}")
    for _ in range(20):
        if job.json()["status"] == "action_required":
            break
        time.sleep(0.05)
        job = client.get(f"/vendor-connections/vendor_a/fetch-jobs/{payload['job_id']}")
    assert job.status_code == 200
    body = job.text.lower()
    assert "login_required" in body
    assert "password" not in body
    assert "cookie" not in body
    assert ".browser_profiles" not in body


def test_duplicate_vendor_fetch_returns_existing_running_job(monkeypatch):
    import time

    from app.fetcher.fetch_jobs import _reset_for_tests

    _reset_for_tests()

    def slow_summary(vendor_id, force=False):
        from app.fetcher.models import VendorFetchSummary

        time.sleep(0.25)
        return VendorFetchSummary(vendor_id=vendor_id, fetch_ready=True)

    monkeypatch.setattr("app.fetcher.fetch_jobs.run_vendor_fetch_summary", slow_summary)
    client = TestClient(app)

    first = client.post("/vendor-connections/vendor_a/fetch").json()
    second = client.post("/vendor-connections/vendor_a/fetch").json()

    assert first["job_id"] == second["job_id"]
    assert second["status"] in {"queued", "running"}


class FakeLocator:
    def __init__(self, visible: bool, text: str = ""):
        self.visible = visible
        self.text = text

    @property
    def first(self):
        return self

    def wait_for(self, state, timeout):
        if not self.visible:
            raise TimeoutError("not visible")

    def is_visible(self, timeout=None):
        return self.visible

    def inner_text(self, timeout=None):
        return self.text


class FakeFrame:
    def __init__(self, visible_selectors=None):
        self.visible_selectors = set(visible_selectors or [])
        self.filled = {}
        self.clicked = []

    def locator(self, selector):
        text = self._html if selector == "body" and hasattr(self, "_html") else ""
        return FakeLocator(selector in self.visible_selectors, text=text)

    def fill(self, selector, value, timeout=None):
        if selector not in self.visible_selectors:
            raise TimeoutError(selector)
        self.filled[selector] = value

    def click(self, selector, timeout=None):
        if selector not in self.visible_selectors:
            raise TimeoutError(selector)
        self.clicked.append(selector)


class FakePage(FakeFrame):
    url = "https://example.test/login"

    def __init__(self, visible_selectors=None, html="<html></html>", frames=None):
        super().__init__(visible_selectors)
        self._html = html
        self.main_frame = self
        self.frames = [self] + list(frames or [])

    def goto(self, url, wait_until=None):
        self.url = url

    def wait_for_load_state(self, state, timeout=None):
        return None

    def content(self):
        return self._html

    def title(self):
        return "Login"

    def screenshot(self, full_page=True):
        return b"png"


class ClosedPage(FakePage):
    def _closed(self):
        raise RuntimeError("Target page, context or browser has been closed")

    @property
    def url(self):
        self._closed()

    @url.setter
    def url(self, value):
        self._url = value

    def content(self):
        self._closed()

    def title(self):
        self._closed()

    def screenshot(self, full_page=True):
        self._closed()

    def locator(self, selector):
        self._closed()


class ClosingAfterGotoPage(FakePage):
    def __init__(self):
        super().__init__(visible_selectors=["#user", "#pass", "#submit"])
        self.closed = False

    def goto(self, url, wait_until=None):
        self.url = url
        self.closed = True

    def content(self):
        if self.closed:
            raise RuntimeError("Target page, context or browser has been closed")
        return self._html

    def locator(self, selector):
        if self.closed:
            raise RuntimeError("Target page, context or browser has been closed")
        return super().locator(selector)


class UrlContentPage(FakePage):
    def __init__(self, content_by_url, visible_selectors=None):
        super().__init__(visible_selectors=visible_selectors)
        self.content_by_url = content_by_url

    def goto(self, url, wait_until=None):
        self.url = url
        self._html = self.content_by_url.get(url, self._html)


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.pages = [page]

    def new_page(self):
        return self.page

    def close(self):
        return None


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [FakeContext(page)]

    def close(self):
        return None


class FakeSyncPlaywright:
    def __init__(self, page):
        self.page = page
        self.chromium = self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def launch_persistent_context(self, **kwargs):
        return FakeContext(self.page)

    def connect_over_cdp(self, url):
        return FakeBrowser(self.page)


def _login_config(**updates):
    data = {
        "vendor_id": "vendor_a",
        "auth_state_label": "vendor_a",
        "login_url": "https://example-farm-a.test/sign-in/",
        "username_selectors": ["#missing-user", "#user"],
        "password_selectors": ["#missing-pass", "#pass"],
        "submit_selectors": ["#missing-submit", "#submit"],
        "login_success_markers": ["my account", "logout"],
        "login_failure_markers": ["invalid"],
        "captcha_markers": ["captcha"],
        "two_factor_markers": ["verification code"],
    }
    data.update(updates)
    return VendorConnectionConfig(**data)


def test_selector_fallback_picks_first_available_selector():
    page = FakePage(visible_selectors=["#second"])

    selector = find_first_visible_selector(page, ["#first", "#second", "#third"], timeout_ms=1)

    assert selector == "#second"


def test_iframe_selector_helper_finds_fields_in_frame():
    frame = FakeFrame(visible_selectors=["#user", "#pass", "#submit"])
    page = FakePage(visible_selectors=[], frames=[frame])
    config = _login_config()

    selected_frame, fields = find_login_frame_and_selectors(page, config)

    assert selected_frame is frame
    assert fields == {"username": "#user", "password": "#pass", "submit": "#submit"}


def test_login_diagnostics_metadata_includes_attempted_selectors(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    page = FakePage(html="<html>invalid</html>")
    config = _login_config()

    from app.fetcher.account_connections import login_attempt_diagnostics

    metadata = login_attempt_diagnostics(
        "vendor_a",
        config,
        page,
        found_selectors={"username": None, "password": None, "submit": None},
        error_message="Could not find configured login selectors.",
    )

    assert metadata["attempted_username_selectors"] == ["#missing-user", "#user"]
    assert metadata["attempted_password_selectors"] == ["#missing-pass", "#pass"]
    assert metadata["attempted_submit_selectors"] == ["#missing-submit", "#submit"]
    assert metadata["screenshot_path"].endswith("screenshot.png")
    assert "login_attempts" in metadata["metadata_path"]


def test_missing_selectors_returns_action_required_instead_of_crashing(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: _login_config())
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    page = FakePage(html="<html>Please sign in</html>")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = login_vendor_with_saved_credentials("vendor_a")

    assert connection.connection_status == "action_required"
    assert connection.action_required == "login_required"
    assert "Could not find configured login selectors" in connection.error_message


def test_manual_login_fallback_can_mark_connected_after_success_markers(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: _login_config())
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    page = FakePage(html="<html>My Account Logout</html>")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = login_vendor_with_saved_credentials("vendor_a")

    assert connection.connection_status == "connected"
    assert connection.action_required is None
    assert connection.error_message is None


def test_already_logged_in_page_returns_connected_without_filling_selectors(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: _login_config())
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    page = FakePage(
        visible_selectors=["#user", "#pass", "#submit"],
        html="<html>My Account Logout</html>",
    )
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = login_vendor_with_saved_credentials("vendor_a")

    assert connection.connection_status == "connected"
    assert connection.action_required is None
    assert page.filled == {}
    assert page.clicked == []


def test_already_logged_in_text_marker_returns_connected(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: _login_config(login_success_markers=["already logged in"]))
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    page = FakePage(html="<html>You are already logged in.</html>")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = login_vendor_with_saved_credentials("vendor_a")

    assert connection.connection_status == "connected"
    assert connection.action_required is None


def test_verify_logged_in_state_uses_account_url_fallback():
    config = _login_config(
        account_url="https://example.test/account",
        post_login_check_url=None,
        login_success_markers=["my account", "orders"],
    )
    page = UrlContentPage(
        {
            "https://example.test/login": "<html>Sign in Password</html>",
            "https://example.test/account": "<html>My Account Orders</html>",
        }
    )
    page.goto("https://example.test/login")

    result = verify_logged_in_state(page, config)

    assert result["is_connected"] is True
    assert "my account" in result["detected_success_markers"]
    assert result["final_url"] == "https://example.test/account"


def test_login_fields_missing_but_account_url_success_returns_connected(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    config = _login_config(
        account_url="https://example.test/account",
        post_login_check_url=None,
        login_success_markers=["my account", "orders"],
    )
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: config)
    page = UrlContentPage(
        {
            config.login_url: "<html>Sign in Password</html>",
            config.account_url: "<html>My Account Orders</html>",
        },
        visible_selectors=[],
    )
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = login_vendor_with_saved_credentials("vendor_a")

    assert connection.connection_status == "connected"
    assert connection.action_required is None
    assert page.filled == {}


def test_selector_failure_manual_enter_success_markers_returns_connected(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: _login_config(account_url=None, post_login_check_url=None))
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    page = FakePage(html="<html>Sign in Password</html>")

    def complete_manual_login(prompt=""):
        page._html = "<html>My Account Orders</html>"
        return ""

    monkeypatch.setattr("builtins.input", complete_manual_login)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = login_vendor_with_saved_credentials("vendor_a")

    assert connection.connection_status == "connected"
    assert connection.action_required is None


def test_diagnostics_returns_fallback_title_url_when_page_is_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    page = ClosedPage()
    config = _login_config()

    from app.fetcher.account_connections import login_attempt_diagnostics

    metadata = login_attempt_diagnostics(
        "vendor_a",
        config,
        page,
        found_selectors={"username": None, "password": None, "submit": None},
        error_message="Browser was closed before login could be verified.",
    )

    assert safe_page_title(page) is None
    assert safe_page_url(page) is None
    assert safe_page_text(page) == ""
    assert safe_detect_markers(page, config.login_success_markers) == []
    assert metadata["page_title"] is None
    assert metadata["final_url"] is None
    assert metadata["screenshot_path"] is None
    assert metadata["error_message"] == "Browser was closed before login could be verified."


def test_closing_browser_during_login_returns_structured_action_required(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: _login_config())
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    page = ClosingAfterGotoPage()
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = login_vendor_with_saved_credentials("vendor_a")

    assert connection.connection_status == "action_required"
    assert connection.action_required == "login_required"
    assert connection.error_message == "Browser was closed before login could be verified."


def test_check_vendor_connection_marks_connected_from_account_url(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    config = _login_config(
        account_url="https://example.test/account",
        post_login_check_url="https://example.test/account",
        login_success_markers=["my account", "orders"],
    )
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: config)
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    page = UrlContentPage({"https://example.test/account": "<html>My Account Orders</html>"})
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = check_vendor_connection("vendor_a")

    assert connection.connection_status == "connected"
    assert connection.action_required is None


def test_shared_cdp_worker_reuses_one_controlled_page_and_preserves_login_tab(monkeypatch, tmp_path):
    from app.fetcher import vendor_fetcher

    class WorkerPage:
        def __init__(self, url="about:blank"):
            self.url = url
            self.goto_calls = []
            self.closed = False

        def goto(self, url, wait_until=None):
            self.url = url
            self.goto_calls.append(url)

        def evaluate(self, script, value):
            return None

        def close(self):
            self.closed = True

    class WorkerContext:
        def __init__(self):
            self.login_page = WorkerPage("https://example-farm-c.test/account")
            self.worker_page = WorkerPage()
            self.pages = [self.login_page]
            self.new_page_calls = 0

        def new_page(self):
            self.new_page_calls += 1
            return self.worker_page

    class WorkerBrowser:
        def __init__(self, context):
            self.contexts = [context]
            self.closed = False

        def close(self):
            self.closed = True

    class WorkerPlaywright:
        def __init__(self, context):
            self.context = context
            self.chromium = self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def connect_over_cdp(self, url, timeout=None):
            return WorkerBrowser(self.context)

    context = WorkerContext()
    monkeypatch.setattr(vendor_fetcher, "_port_is_listening", lambda port: True)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: WorkerPlaywright(context))
    calls = []

    def fake_capture(target, output_dir, page, close_page):
        calls.append((target.page_id, page, close_page))
        return FetchMetadata(
            vendor_id=target.vendor_id,
            page_id=target.page_id,
            url=target.url,
            fetched_at="2026-07-08T00:00:00+00:00",
            requires_login=True,
            status="success",
        )

    monkeypatch.setattr(vendor_fetcher, "_capture_cdp_page_with_worker", fake_capture)
    target_one = VendorPageTarget(
        vendor_id="vendor_c",
        page_id="dairy",
        url="https://example-farm-c.test/shop/product/dairy",
        requires_login=True,
        auth_state_label="vendor_c",
    )
    target_two = target_one.model_copy(update={"page_id": "eggs", "url": "https://example-farm-c.test/shop/product/eggs"})

    with vendor_fetcher.shared_cdp_fetch_worker("vendor_c"):
        vendor_fetcher._fetch_grazecart_via_cdp(target_one, tmp_path / "one")
        vendor_fetcher._fetch_grazecart_via_cdp(target_two, tmp_path / "two")

    assert context.new_page_calls == 1
    assert [call[0] for call in calls] == ["dairy", "eggs"]
    assert {id(call[1]) for call in calls} == {id(context.worker_page)}
    assert all(call[2] is False for call in calls)
    assert context.login_page.url == "https://example-farm-c.test/account"
    assert context.worker_page.closed is True


def test_check_vendor_connection_returns_login_required_from_login_wall(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    config = _login_config(
        account_url="https://example.test/account",
        post_login_check_url="https://example.test/account",
    )
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: config)
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    page = UrlContentPage({"https://example.test/account": "<html>Sign in Password</html>"})
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = check_vendor_connection("vendor_a")

    assert connection.connection_status == "action_required"
    assert connection.action_required == "login_required"
    assert get_vendor_credential_status("vendor_a").credential_status == "configured"


def test_grazecart_fetch_uses_open_cdp_context_and_writes_artifacts(tmp_path, monkeypatch):
    html = _listing_card("Milk, 1/2 Gal. (Plastic)", "6.25", "Add to Cart")
    page = FakePage(html=html)
    page.url = "https://example-farm-c.test/shop/dairy"
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    monkeypatch.setattr("app.fetcher.vendor_fetcher._port_is_listening", lambda port: True)
    monkeypatch.setattr("app.fetcher.external_browser_session.vendor_debug_port", lambda vendor_id: 9223)
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")

    metadata = fetch_vendor_page(
        VendorPageTarget(
            vendor_id="vendor_c",
            page_id="dairy",
            url="https://example-farm-c.test/shop/dairy",
            requires_login=True,
            auth_state_label="vendor_c",
        ),
        force_refresh=True,
    )

    assert metadata.status == "success"
    assert metadata.action_required is None
    assert metadata.capture_method == "cdp_controlled_fetch_page"
    assert metadata.selected_tab_url == "about:blank"
    assert metadata.variant_count == 1
    assert metadata.variant_snapshots_path is not None
    assert metadata.html_path and metadata.html_path.endswith("page.html")
    payload = metadata.model_dump_json()
    assert "manual_login_required" not in payload
    assert "cookie" not in payload.lower()
    assert "password" not in payload.lower()
    assert ".browser_profiles" not in payload


def test_check_vendor_connection_returns_captcha_required(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    config = _login_config(
        account_url="https://example.test/account",
        post_login_check_url="https://example.test/account",
    )
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: config)
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    page = UrlContentPage({"https://example.test/account": "<html>captcha challenge</html>"})
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = check_vendor_connection("vendor_a")

    assert connection.connection_status == "action_required"
    assert connection.action_required == "captcha_required"
    assert get_vendor_credential_status("vendor_a").credential_status == "configured"


def test_check_vendor_connection_returns_pricing_locked(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    config = _login_config(
        account_url="https://grazecart.test/milk",
        post_login_check_url="https://grazecart.test/milk",
    )
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: config)
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)
    page = UrlContentPage(
        {"https://grazecart.test/milk": "<html><h3>Milk</h3><span>Sign up for pricing</span></html>"}
    )
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    set_vendor_credentials("vendor_a", "person@example.test", "secret")

    connection = check_vendor_connection("vendor_a")

    assert connection.connection_status == "action_required"
    assert connection.action_required == "pricing_locked"


def test_check_vendor_connection_reverifies_stale_connected_status(tmp_path, monkeypatch):
    fake_keyring = FakeKeyring()
    monkeypatch.setattr("app.fetcher.credentials._keyring", lambda: fake_keyring)
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.account_connections.persistent_profile_path", lambda vendor_id: tmp_path / "profiles" / vendor_id)
    config = _login_config(
        account_url="https://example.test/account",
        post_login_check_url="https://example.test/account",
    )
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: config)
    monkeypatch.setattr("app.fetcher.account_connections.check_target_for_vendor", lambda vendor_id: None)

    # Seed a stale "connected" record; a fresh check against a logged-out page
    # must not trust it.
    update_connection_status("vendor_a", status="connected", connected=True, notes="stale")

    page = UrlContentPage({"https://example.test/account": "<html>Sign in Password</html>"})
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))

    connection = check_vendor_connection("vendor_a")

    assert connection.connection_status == "action_required"
    assert connection.action_required == "login_required"


def test_external_handoff_vendor_does_not_retry_credential_login(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(
        "app.fetcher.account_connections.persistent_profile_path",
        lambda vendor_id: tmp_path / "profiles" / vendor_id,
    )
    config = _login_config(
        vendor_id="vendor_c",
        auth_state_label="vendor_c",
        auth_strategy="external_browser_handoff",
        captcha_sensitive=True,
    )
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: config)

    connection = __import__(
        "app.fetcher.account_connections",
        fromlist=["ensure_vendor_connected"],
    ).ensure_vendor_connected("vendor_c")

    assert connection.connection_status == "action_required"
    assert connection.action_required == "manual_login_required"
    assert "external Chrome session handoff" in (connection.notes or "")


def test_vendor_c_fetch_uses_verified_external_session_instead_of_manual_login(tmp_path, monkeypatch):
    from app.fetcher.external_browser_session import verify_external_session

    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.storage.EXTERNAL_BROWSER_PROFILES_DIR", tmp_path / "external_profiles")
    monkeypatch.setattr("app.fetcher.storage.BROWSER_PROFILES_DIR", tmp_path / "persistent_profiles")
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr("app.fetcher.vendor_fetcher.fetch_output_dir", lambda target: tmp_path / "fetches" / target.page_id)
    config = _login_config(
        vendor_id="vendor_c",
        auth_state_label="vendor_c",
        auth_strategy="external_browser_handoff",
        captcha_sensitive=True,
    )
    monkeypatch.setattr("app.fetcher.account_connections.vendor_account_config", lambda vendor_id: config)
    monkeypatch.setattr("app.fetcher.vendor_fetcher.vendor_account_config", lambda vendor_id: config)
    page = UrlContentPage(
        {
            "https://example-farm-c.test/shop/dairy": (
                "<main><h1>Dairy</h1><span>$12.00</span><button>Add to Cart</button></main>"
            )
        }
    )
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright(page))
    target = VendorPageTarget(
        vendor_id="vendor_c",
        page_id="dairy",
        url="https://example-farm-c.test/shop/dairy",
        requires_login=True,
        auth_state_label="vendor_c",
        required_text_markers=["Dairy"],
    )

    verification = verify_external_session(
        vendor_id="vendor_c",
        port=9223,
        target=target,
        config=config,
    )
    metadata = fetch_vendor_page(target, force_refresh=True)

    assert verification.fetch_ready is True
    assert metadata.status == "success"
    assert metadata.connection_status == "connected"
    assert metadata.action_required is None


# --- run_vendor_fetch_summary (offline, mocked) -------------------

from app.fetcher.services import run_vendor_fetch_summary


def _summary_target(vendor_id, page_id):
    return VendorPageTarget(
        vendor_id=vendor_id,
        page_id=page_id,
        url=f"https://x.test/{page_id}",
        requires_login=True,
        auth_state_label=vendor_id,
    )


def _summary_meta(vendor_id, page_id, **updates):
    data = {
        "vendor_id": vendor_id,
        "page_id": page_id,
        "url": f"https://x.test/{page_id}",
        "fetched_at": "2026-07-06T00:00:00+00:00",
        "requires_login": True,
        "status": "success",
    }
    data.update(updates)
    return FetchMetadata(**data)


def _never_create(_path):
    raise AssertionError("create_import_candidates_from_variant_snapshot should not be called")


def test_fetch_summary_pricing_locked_is_not_ready(monkeypatch):
    targets = [_summary_target("vendor_b", "milk"), _summary_target("vendor_b", "store")]
    monkeypatch.setattr("app.fetcher.services.load_vendor_pages", lambda: targets)
    calls = []

    def fake_fetch(target, force_refresh=False):
        calls.append(target.page_id)
        return _summary_meta(
            "vendor_b",
            "milk",
            status="action_required",
            action_required="pricing_locked",
            connection_status="action_required",
            variant_count=0,
            recommended_next_action="Prices are hidden behind an account/membership.",
        )

    monkeypatch.setattr("app.fetcher.services.fetch_target_page", fake_fetch)
    monkeypatch.setattr("app.fetcher.services.create_import_candidates_from_variant_snapshot", _never_create)

    summary = run_vendor_fetch_summary("vendor_b", force=True)

    assert summary.fetch_ready is False
    assert summary.action_required == "pricing_locked"
    assert summary.candidates_created_total == 0
    assert summary.pricing_locked_pages == ["milk"]
    assert summary.pages_action_required == 1
    # Not fetch-ready: remaining pages must not be captured.
    assert calls == ["milk"]


def test_fetch_summary_login_required_is_not_ready(monkeypatch):
    targets = [_summary_target("vendor_b", "milk")]
    monkeypatch.setattr("app.fetcher.services.load_vendor_pages", lambda: targets)
    monkeypatch.setattr(
        "app.fetcher.services.fetch_target_page",
        lambda target, force_refresh=False: _summary_meta(
            "vendor_b",
            "milk",
            status="action_required",
            action_required="login_required",
            connection_status="action_required",
            variant_count=0,
        ),
    )
    monkeypatch.setattr("app.fetcher.services.create_import_candidates_from_variant_snapshot", _never_create)

    summary = run_vendor_fetch_summary("vendor_b", force=True)

    assert summary.fetch_ready is False
    assert summary.account_authenticated is False
    assert summary.action_required == "login_required"
    assert summary.candidates_created_total == 0


def test_fetch_summary_captcha_required_is_not_ready_and_recommends_handoff(monkeypatch):
    targets = [_summary_target("vendor_c", "dairy")]
    monkeypatch.setattr("app.fetcher.services.load_vendor_pages", lambda: targets)
    monkeypatch.setattr(
        "app.fetcher.services.fetch_target_page",
        lambda target, force_refresh=False: _summary_meta(
            "vendor_c",
            "dairy",
            status="action_required",
            action_required="captcha_required",
            connection_status="action_required",
            variant_count=0,
            recommended_next_action=(
                "Login failed due to CAPTCHA. Use external Chrome session handoff for this vendor, "
                "complete login manually, then rerun verification/fetch."
            ),
        ),
    )
    monkeypatch.setattr("app.fetcher.services.create_import_candidates_from_variant_snapshot", _never_create)

    summary = run_vendor_fetch_summary("vendor_c", force=True)

    assert summary.fetch_ready is False
    assert summary.account_authenticated is False
    assert summary.action_required == "captcha_required"
    assert summary.candidates_created_total == 0
    assert "external Chrome session handoff" in (summary.recommended_next_action or "")


def test_fetch_summary_fetch_ready_creates_candidates(tmp_path, monkeypatch):
    targets = [
        _summary_target("vendor_b", "milk"),
        _summary_target("vendor_b", "milk_cheese"),
    ]
    monkeypatch.setattr("app.fetcher.services.load_vendor_pages", lambda: targets)
    milk_snap = tmp_path / "milk.json"
    milk_snap.write_text("{}", encoding="utf-8")
    cheese_snap = tmp_path / "cheese.json"
    cheese_snap.write_text("{}", encoding="utf-8")

    def fake_fetch(target, force_refresh=False):
        if target.page_id == "milk":
            return _summary_meta(
                "vendor_b", "milk", status="success", connection_status="connected",
                variant_count=3, variant_snapshots_path=str(milk_snap),
            )
        return _summary_meta(
            "vendor_b", "milk_cheese", status="success", connection_status="connected",
            variant_count=2, variant_snapshots_path=str(cheese_snap),
        )

    def fake_create(path):
        count = 3 if str(path) == str(milk_snap) else 2
        out = tmp_path / (str(path).replace("\\", "_").replace("/", "_").replace(":", "_") + ".candidates.json")
        out.write_text(json.dumps([{"i": i} for i in range(count)]), encoding="utf-8")
        return out

    monkeypatch.setattr("app.fetcher.services.fetch_target_page", fake_fetch)
    monkeypatch.setattr("app.fetcher.services.create_import_candidates_from_variant_snapshot", fake_create)

    summary = run_vendor_fetch_summary("vendor_b", force=True)

    assert summary.fetch_ready is True
    assert summary.pages_successful == 2
    assert summary.variant_count_total == 5
    assert summary.candidates_created_total == 5
    assert summary.candidates_by_page == {"milk": 3, "milk_cheese": 2}
    assert summary.empty_candidate_pages == []
    action = summary.recommended_next_action.lower()
    assert "review" in action and "approve" in action


def test_fetch_summary_success_but_zero_candidates_is_explained(monkeypatch):
    targets = [_summary_target("vendor_b", "milk")]
    monkeypatch.setattr("app.fetcher.services.load_vendor_pages", lambda: targets)
    monkeypatch.setattr(
        "app.fetcher.services.fetch_target_page",
        lambda target, force_refresh=False: _summary_meta(
            "vendor_b", "milk", status="success", connection_status="connected", variant_count=0,
        ),
    )
    monkeypatch.setattr("app.fetcher.services.create_import_candidates_from_variant_snapshot", _never_create)

    summary = run_vendor_fetch_summary("vendor_b", force=True)

    assert summary.fetch_ready is False
    assert summary.action_required == "parser_zero_candidates"
    assert summary.pages_successful == 1
    assert summary.candidates_created_total == 0
    assert "milk" in summary.empty_candidate_pages
    assert any("no import candidates were created" in warning for warning in summary.warnings)


def test_fetch_summary_response_shape_is_stable(monkeypatch):
    targets = [_summary_target("vendor_b", "milk")]
    monkeypatch.setattr("app.fetcher.services.load_vendor_pages", lambda: targets)
    monkeypatch.setattr(
        "app.fetcher.services.fetch_target_page",
        lambda target, force_refresh=False: _summary_meta(
            "vendor_b", "milk", status="action_required", action_required="login_required",
            connection_status="action_required", variant_count=0,
        ),
    )
    monkeypatch.setattr("app.fetcher.services.create_import_candidates_from_variant_snapshot", _never_create)

    summary = run_vendor_fetch_summary("vendor_b", force=True)
    keys = set(summary.model_dump().keys())
    required = {
        "vendor_id", "connection_status", "action_required", "fetch_ready",
        "pages_attempted", "pages_successful", "pages_action_required", "pages_failed",
        "variant_count_total", "candidates_created_total", "candidates_by_page",
        "empty_candidate_pages", "pricing_locked_pages", "artifact_paths",
        "warnings", "recommended_next_action",
    }
    assert required <= keys


def test_fetch_summary_no_pages_configured(monkeypatch):
    monkeypatch.setattr("app.fetcher.services.load_vendor_pages", lambda: [])
    summary = run_vendor_fetch_summary("unknown_vendor", force=True)
    assert summary.fetch_ready is False
    assert summary.action_required == "failed"
    assert summary.candidates_created_total == 0
