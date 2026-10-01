"""Offline tests for external Chrome session handoff."""
from __future__ import annotations

from pathlib import Path

from app.fetcher.external_browser_session import (
    external_profile_label,
    interactive_setup,
    recommended_next_action,
    resolve_vendor_handoff,
    vendor_debug_port,
    verify_external_session,
)
from app.fetcher.account_connections import connected_profile_ready
from app.fetcher.account_connections import check_vendor_connection
from app.fetcher.models import VendorConnectionConfig, VendorPageTarget
from app.fetcher.storage import external_browser_profile_path, persistent_profile_path
from app.fetcher.vendor_fetcher import _controlled_cdp_fetch_page


def test_resolves_vendor_c_and_vendor_d_vendor_handoff_config():
    vendor_c_config, vendor_c_target = resolve_vendor_handoff("vendor_c")
    vendor_d_config, vendor_d_target = resolve_vendor_handoff("vendor_d")

    assert vendor_c_config.auth_state_label == "vendor_c"
    assert vendor_c_config.login_url == "https://example-farm-c.test/login"
    assert vendor_c_config.account_url == "https://example-farm-c.test/account"
    assert vendor_c_config.auth_strategy == "external_browser_handoff"
    assert vendor_c_target.page_id == "dairy"

    assert vendor_d_config.auth_state_label == "vendor_d"
    assert vendor_d_config.login_url == "https://example-farm-d.test/login"
    assert vendor_d_config.account_url == "https://example-farm-d.test/account"
    assert vendor_d_config.auth_strategy == "external_browser_handoff"
    assert vendor_d_target.page_id == "dairy_page"


def test_external_profile_path_is_app_dedicated_not_default_chrome():
    path = external_browser_profile_path("vendor_c")

    assert "external_browser_profiles" in path.parts
    assert path.name == "vendor_c"
    lower = str(path).lower()
    assert "user data\\default" not in lower
    assert "google\\chrome\\user data" not in lower


def test_profile_label_does_not_expose_profile_path():
    label = external_profile_label("vendor_c")

    assert label == "farmfind_external_vendor_c"
    assert "\\" not in label
    assert "/" not in label


def test_external_handoff_ports_are_stable_per_vendor():
    assert vendor_debug_port("vendor_c") == 9225
    assert vendor_debug_port("vendor_d") == 9226
    assert vendor_debug_port("vendor_c") != vendor_debug_port("vendor_d")


def test_interactive_setup_waits_for_user_confirmation_before_verification(monkeypatch):
    calls: list[str] = []
    config = VendorConnectionConfig(
        vendor_id="vendor_c",
        auth_state_label="vendor_c",
        login_url="https://example-farm-c.test/login",
        account_url="https://example-farm-c.test/account",
        auth_strategy="external_browser_handoff",
    )
    target = VendorPageTarget(
        vendor_id="vendor_c",
        page_id="dairy",
        url="https://example-farm-c.test/shop/dairy",
        requires_login=True,
    )
    monkeypatch.setattr(
        "app.fetcher.external_browser_session.resolve_vendor_handoff",
        lambda vendor_id: (config, target),
    )
    monkeypatch.setattr(
        "app.fetcher.external_browser_session.launch_external_chrome",
        lambda **kwargs: calls.append("launch"),
    )

    def fake_verify(**kwargs):
        calls.append("verify")
        from app.fetcher.models import ExternalBrowserSessionResult
        from app.fetcher.external_browser_session import now_iso

        return ExternalBrowserSessionResult(
            vendor_id=kwargs["vendor_id"],
            profile_label="farmfind_external_vendor_c",
            account_authenticated=True,
            fetch_ready=True,
            verified_at=now_iso(),
        )

    monkeypatch.setattr("app.fetcher.external_browser_session.verify_external_session", fake_verify)

    def confirm():
        calls.append("confirm")

    result = interactive_setup(vendor_id="vendor_c", port=9223, wait_for_user=confirm)

    assert calls == ["launch", "confirm", "verify"]
    assert result.fetch_ready is True


class FakePage:
    def __init__(self, html: str, url: str = "https://example-farm-c.test/shop/dairy"):
        self.html = html
        self.url = url
        self.visited: list[str] = []
        self.closed = False

    def goto(self, url, wait_until=None):
        self.visited.append(url)
        self.url = url

    def evaluate(self, script, arg=None):
        return None

    def close(self):
        self.closed = True

    def wait_for_load_state(self, state, timeout=None):
        return None

    def content(self):
        return self.html


class FakeContext:
    def __init__(self, page):
        self.pages = [page]

    def new_page(self):
        page = FakePage("<main></main>", url="about:blank")
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [FakeContext(page)]
        self.closed = False

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, page):
        self.page = page
        self.connected_url = None

    def connect_over_cdp(self, url):
        self.connected_url = url
        return FakeBrowser(self.page)


class FakePlaywright:
    def __init__(self, page):
        self.chromium = FakeChromium(page)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_cdp_verification_can_be_mocked_and_updates_connection(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.storage.EXTERNAL_BROWSER_PROFILES_DIR", tmp_path / "external_profiles")
    page = FakePage("<main><h1>Dairy</h1><span>$12.00</span><button>Add to Cart</button></main>")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePlaywright(page))
    config = VendorConnectionConfig(
        vendor_id="vendor_c",
        auth_state_label="vendor_c",
        login_url="https://example-farm-c.test/login",
        login_success_markers=["account"],
        auth_strategy="external_browser_handoff",
    )
    target = VendorPageTarget(
        vendor_id="vendor_c",
        page_id="dairy",
        url="https://example-farm-c.test/shop/dairy",
        requires_login=True,
        required_text_markers=["Dairy"],
    )

    result = verify_external_session(vendor_id="vendor_c", port=9223, target=target, config=config)

    assert page.visited == ["https://example-farm-c.test/shop/dairy"]
    assert result.account_authenticated is True
    assert result.fetch_ready is True
    assert result.action_required is None


def test_controlled_cdp_fetch_page_does_not_navigate_login_tab():
    login_page = FakePage("<main>Account</main>", url="https://example-farm-a.test/my-account")
    browser = FakeBrowser(login_page)
    target = VendorPageTarget(
        vendor_id="vendor_a",
        page_id="cow_milk",
        url="https://example-farm-a.test/product-category/cow-milk",
        requires_login=True,
    )

    fetch_page = _controlled_cdp_fetch_page(browser, target)
    fetch_page.goto(target.url, wait_until="domcontentloaded")

    assert login_page.visited == []
    assert login_page.closed is False
    assert fetch_page is not login_page
    assert fetch_page.visited[-1] == target.url


def test_successful_external_verification_persists_fetch_usable_state(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.storage.EXTERNAL_BROWSER_PROFILES_DIR", tmp_path / "external_profiles")
    monkeypatch.setattr("app.fetcher.storage.BROWSER_PROFILES_DIR", tmp_path / "persistent_profiles")
    page = FakePage("<main><h1>Dairy</h1><span>$12.00</span><button>Add to Cart</button></main>")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePlaywright(page))
    config = VendorConnectionConfig(
        vendor_id="vendor_c",
        auth_state_label="vendor_c",
        login_url="https://example-farm-c.test/login",
        auth_strategy="external_browser_handoff",
        captcha_sensitive=True,
    )
    target = VendorPageTarget(
        vendor_id="vendor_c",
        page_id="dairy",
        url="https://example-farm-c.test/shop/dairy",
        requires_login=True,
        required_text_markers=["Dairy"],
    )

    result = verify_external_session(vendor_id="vendor_c", port=9223, target=target, config=config)

    assert result.fetch_ready is True
    assert connected_profile_ready("vendor_c") is True
    assert external_browser_profile_path("vendor_c").exists()
    assert not persistent_profile_path("vendor_c").exists()


def test_check_connection_for_external_vendor_verifies_open_cdp_session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.storage.EXTERNAL_BROWSER_PROFILES_DIR", tmp_path / "external_profiles")
    page = FakePage("<main><h1>Dairy</h1><span>$12.00</span><button>Add to Cart</button></main>")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePlaywright(page))

    connection = check_vendor_connection("vendor_c")

    assert page.visited == ["https://example-farm-c.test/shop/dairy"]
    assert connection.connection_status == "connected"
    assert connection.action_required is None
    assert connected_profile_ready("vendor_c") is True


def test_captcha_result_is_secret_free_and_recommends_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr("app.fetcher.storage.VENDOR_CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr("app.fetcher.storage.EXTERNAL_BROWSER_PROFILES_DIR", tmp_path / "external_profiles")
    page = FakePage("<main>The captcha verification failed. Please contact support. Dairy</main>")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePlaywright(page))
    config = VendorConnectionConfig(
        vendor_id="vendor_c",
        auth_state_label="vendor_c",
        login_url="https://example-farm-c.test/login",
        auth_strategy="external_browser_handoff",
        captcha_sensitive=True,
    )
    target = VendorPageTarget(
        vendor_id="vendor_c",
        page_id="dairy",
        url="https://example-farm-c.test/shop/dairy",
        requires_login=True,
        required_text_markers=["Dairy"],
    )

    result = verify_external_session(vendor_id="vendor_c", port=9223, target=target, config=config)
    payload = result.model_dump_json()

    assert result.fetch_ready is False
    assert result.action_required == "captcha_required"
    assert "external Chrome session handoff" in (result.recommended_next_action or "")
    forbidden = ["cookie", "password", "token", "payment", "card", str(Path("external_browser_profiles"))]
    assert not any(item in payload.lower() for item in forbidden)


def test_recommended_next_action_for_captcha_points_to_handoff_not_retry():
    action = recommended_next_action("vendor_d", False, "captcha_required")

    assert "external Chrome session handoff" in action
    assert "retry credential" not in action.lower()
