"""Example Farm A fetch state — no leaked secrets, profile-conflict is
actionable (not a stale 'failed')."""
from __future__ import annotations

from app.fetcher.models import VendorPageTarget
from app.fetcher.storage import sanitize_reason


def test_sanitize_reason_strips_browser_logs_and_profile_path():
    raw_data = (
        "Fetch failed before classification: BrowserType.launch_persistent_context: "
        "Target page, context or browser has been closed\n"
        "Browser logs:\n\n<launching> "
        + r"C:\Program Files\Google\Chrome\chrome.exe "
        + r"--user-data-dir=C:\Users\x\backend\.browser_profiles\vendor_a about:blank"
    )
    cleaned = sanitize_reason(raw_data)
    assert "browser_profiles" not in cleaned.lower()
    assert "user-data-dir" not in cleaned.lower()
    assert "chrome.exe" not in cleaned.lower()
    assert cleaned.startswith("Fetch failed before classification")


class _RaisingPlaywright:
    def __init__(self, message):
        self.message = message
        self.chromium = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def launch_persistent_context(self, **kwargs):
        raise RuntimeError(self.message)

    def launch(self, **kwargs):
        raise RuntimeError(self.message)


def test_profile_conflict_is_actionable_not_failed(tmp_path, monkeypatch):
    from app.fetcher import vendor_fetcher

    target = VendorPageTarget(
        vendor_id="vendor_a",
        page_id="milk",
        url="https://example-farm-a.test/product/milk/",
        requires_login=True,
        auth_state_label="vendor_a",
    )
    monkeypatch.setattr("app.fetcher.storage.FETCHES_DIR", tmp_path / "fetches")
    monkeypatch.setattr(vendor_fetcher, "latest_success_is_recent", lambda t: False)
    monkeypatch.setattr(vendor_fetcher, "connected_profile_ready", lambda vid: True)
    monkeypatch.setattr(vendor_fetcher, "vendor_account_config", lambda vid: None)
    monkeypatch.setattr(vendor_fetcher, "_fetch_grazecart_via_cdp", lambda target, output_dir: None)
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _RaisingPlaywright(
            "BrowserType.launch_persistent_context: Opening in existing browser session. "
            "This usually means that the profile is already in use."
        ),
    )

    metadata = vendor_fetcher.fetch_vendor_page(target, force_refresh=True)

    assert metadata.status == "action_required"
    assert metadata.action_required == "manual_login_required"
    assert "browser_profiles" not in (metadata.error_message or "").lower()
    assert "close" in (metadata.recommended_next_action or "").lower()
