"""Log into farms whose sign-in has a human check, one Chrome window at a time.

Opens each farm's login page in its own saved FarmFind profile. You sign in
like on any website (tick "Remember me" if the farm offers it). The command
notices the sign-in by itself, checks that member prices show, closes the
window and moves to the next farm. It never fills in or solves a check.

    python -m app.fetcher.login_by_hand                      # every farm that is signed out
    python -m app.fetcher.login_by_hand --vendor-id cwh_ministry
"""
from __future__ import annotations

import argparse
import re
import time

from .account_connections import (
    _port_is_listening,
    _same_page,
    check_vendor_connection,
    session_profile_path,
)
from .external_browser_session import launch_profile_chrome, resolve_vendor_handoff, verify_browser_session, vendor_debug_port
from .storage import load_vendor_account_configs, load_vendor_pages

POLL_SECONDS = 3
_LOGOUT_LINK = re.compile(r"<a\b[^>]*href=[\"'][^\"']*(?:logout|log-out|sign-out|signout)", re.IGNORECASE)


def page_shows_signed_in(visible_text: str, html: str, url: str, login_url: str) -> bool:
    """Signed in: off the login page, no visible "Sign in" or locked prices, and a sign-out
    link in the page (often tucked in an account menu, so it need not be visible)."""
    text = " ".join(visible_text.lower().split())
    if _same_page(url, login_url) or any(word in text for word in ("sign in", "log in", "sign up for pricing")):
        return False
    return any(word in text for word in ("sign out", "log out", "logout")) or bool(_LOGOUT_LINK.search(html))


def _signed_in_somewhere(browser, login_url: str) -> bool:
    for context in browser.contexts:
        for page in context.pages:
            try:
                visible_text = page.locator("body").inner_text(timeout=2000)
                html = page.content()
            except Exception:
                continue
            if page_shows_signed_in(visible_text, html, page.url, login_url):
                return True
    return False


def _close_chrome(browser) -> None:
    # Browser.close over DevTools lets Chrome write its cookies before it exits.
    try:
        browser.new_browser_cdp_session().send("Browser.close")
    except Exception:
        pass


def login_one(vendor_id: str, timeout_minutes: int) -> str:
    from playwright.sync_api import sync_playwright

    config, target = resolve_vendor_handoff(vendor_id)
    port = vendor_debug_port(vendor_id)
    process = None
    if _port_is_listening(port):
        # A window from an earlier run is still open: carry on with it.
        print(f"{config.vendor_name or vendor_id}: using the Chrome window that is already open.")
    else:
        process = launch_profile_chrome(
            profile_path=session_profile_path(vendor_id),
            url=config.login_url,
            port=port,
        )
        print(f"{config.vendor_name or vendor_id}: sign in in the Chrome window that opened.")
    deadline = time.monotonic() + timeout_minutes * 60

    def window_closed() -> bool:
        return process.poll() is not None if process is not None else not _port_is_listening(port)

    while not _port_is_listening(port):
        if time.monotonic() > deadline or window_closed():
            return "window closed before sign-in"
        time.sleep(0.5)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        while True:
            if window_closed():
                return "window closed before sign-in"
            if time.monotonic() > deadline:
                _close_chrome(browser)
                return f"no sign-in within {timeout_minutes} minutes"
            if _signed_in_somewhere(browser, config.login_url):
                break
            time.sleep(POLL_SECONDS)
    result = verify_browser_session(
        vendor_id=vendor_id,
        port=port,
        target=target,
        config=config,
        profile_label=vendor_id,
        profile_ready_path=session_profile_path(vendor_id),
    )
    with sync_playwright() as playwright:
        _close_chrome(playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}"))
    if process is not None:
        process.wait(timeout=30)
    if result.fetch_ready:
        return "signed in, member prices visible"
    return f"signed in, but the store page says: {result.action_required}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign into farms by hand, one window at a time.")
    parser.add_argument("--vendor-id", action="append", help="Farm to sign into (repeatable). Default: every signed-out farm.")
    parser.add_argument("--timeout-minutes", type=int, default=10)
    args = parser.parse_args()

    # A farm whose prices are public (no page needs a login) is never asked for a sign-in.
    login_vendors = {target.vendor_id for target in load_vendor_pages() if target.requires_login}
    vendor_ids = args.vendor_id or [
        config.vendor_id
        for config in load_vendor_account_configs()
        if config.vendor_id in login_vendors
        and check_vendor_connection(config.vendor_id).connection_status != "connected"
    ]
    if not vendor_ids:
        print("Every farm is already signed in.")
        return
    for vendor_id in vendor_ids:
        print(f"{vendor_id}: {login_one(vendor_id, args.timeout_minutes)}")


if __name__ == "__main__":
    main()
