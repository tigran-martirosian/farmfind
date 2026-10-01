"""Manual Playwright login and private auth-state storage."""
from __future__ import annotations

from .storage import AUTH_DIR, auth_state_path


def save_manual_login(auth_state_label: str, login_url: str) -> str:
    """Open a headed browser so the user can log in, then save storage state."""
    from playwright.sync_api import sync_playwright

    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    output_path = auth_state_path(auth_state_label)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(login_url)
        input("Log in manually in the browser, then press Enter here to save auth state...")
        context.storage_state(path=str(output_path))
        browser.close()
    return str(output_path)
