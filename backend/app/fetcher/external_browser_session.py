"""External Chrome handoff for CAPTCHA-sensitive vendor sessions.

This does not solve or bypass CAPTCHA. It opens a normal Chrome process with a
dedicated FarmFind vendor profile, waits for the user to log in manually, then
verifies that same browser session through Chrome DevTools Protocol.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .account_connections import classify_connection_result, connection_config_or_default, update_connection_status
from .models import ExternalBrowserSessionResult, VendorConnectionConfig, VendorPageTarget
from .services import GRAZECART_READINESS_PAGE
from .storage import external_browser_profile_path, load_vendor_account_configs, load_vendor_pages, safe_path_part

EXTERNAL_HANDOFF_PORT = 9223


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def external_profile_label(vendor_id: str) -> str:
    return f"farmfind_external_{safe_path_part(vendor_id)}"


def vendor_debug_port(vendor_id: str) -> int:
    """Stable per-vendor CDP port for UI-launched login browsers."""
    vendor_ids = sorted({config.vendor_id for config in load_vendor_account_configs()})
    try:
        return EXTERNAL_HANDOFF_PORT + vendor_ids.index(vendor_id)
    except ValueError:
        return EXTERNAL_HANDOFF_PORT + (sum(ord(char) for char in vendor_id) % 100)


def resolve_vendor_handoff(vendor_id: str) -> tuple[VendorConnectionConfig, VendorPageTarget]:
    config = connection_config_or_default(vendor_id)
    targets = [target for target in load_vendor_pages() if target.vendor_id == vendor_id]
    if not targets:
        raise ValueError(f"No vendor_pages.json entries are configured for {vendor_id}.")
    readiness_page_id = GRAZECART_READINESS_PAGE.get(vendor_id, targets[0].page_id)
    target = next((item for item in targets if item.page_id == readiness_page_id), targets[0])
    return config, target


def chrome_executable() -> str:
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("google-chrome"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError("Could not find Chrome. Install Chrome or pass --chrome-path.")


def launch_profile_chrome(
    *,
    profile_path: Path,
    url: str,
    port: int,
    chrome_path: str | None = None,
) -> subprocess.Popen:
    profile_path.mkdir(parents=True, exist_ok=True)
    executable = chrome_path or chrome_executable()
    args = [
        executable,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_path}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    return subprocess.Popen(args)  # noqa: S603 - command is argv-only, no shell.


def launch_external_chrome(
    *,
    vendor_id: str,
    url: str,
    port: int,
    chrome_path: str | None = None,
) -> subprocess.Popen:
    return launch_profile_chrome(
        profile_path=external_browser_profile_path(vendor_id),
        url=url,
        port=port,
        chrome_path=chrome_path,
    )


def _page_snapshot(page) -> tuple[str, str]:
    html = page.content()
    final_url = getattr(page, "url", "") or ""
    return html, final_url


def verify_browser_session(
    *,
    vendor_id: str,
    port: int,
    target: VendorPageTarget,
    config: VendorConnectionConfig,
    profile_label: str,
    profile_ready_path: Path,
) -> ExternalBrowserSessionResult:
    from playwright.sync_api import sync_playwright

    warnings: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target.url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("load", timeout=7000)
            except Exception:
                warnings.append("Timed out waiting for full page load; verified DOM content instead.")
            html, final_url = _page_snapshot(page)
            status, action_required = classify_connection_result(target, html, final_url, config)
        finally:
            browser.close()

    connected = status == "connected"
    if connected:
        profile_ready_path.mkdir(parents=True, exist_ok=True)
    update_connection_status(
        vendor_id,
        status=status,
        action_required=action_required,
        notes=(
            "Connected through browser session verification."
            if connected
            else "Browser session verification did not reach a fetch-ready page."
        ),
        connected=connected,
        error_message=None if connected else "Browser session verification did not reach fetch-ready state.",
    )
    return ExternalBrowserSessionResult(
        vendor_id=vendor_id,
        auth_strategy=config.auth_strategy,
        profile_label=profile_label,
        account_authenticated=connected or action_required in {"pricing_locked", "store_access_required"},
        fetch_ready=connected,
        action_required=None if connected else action_required or "failed",
        recommended_next_action=recommended_next_action(vendor_id, connected, action_required),
        verified_at=now_iso(),
        warnings=warnings,
    )


def verify_external_session(
    *,
    vendor_id: str,
    port: int,
    target: VendorPageTarget,
    config: VendorConnectionConfig,
) -> ExternalBrowserSessionResult:
    return verify_browser_session(
        vendor_id=vendor_id,
        port=port,
        target=target,
        config=config,
        profile_label=external_profile_label(vendor_id),
        profile_ready_path=external_browser_profile_path(vendor_id),
    )


def recommended_next_action(
    vendor_id: str,
    connected: bool,
    action_required: str | None,
) -> str:
    if connected:
        return "External Chrome session verified. Rerun vendor fetch with force=true to capture products."
    if action_required == "captcha_required":
        return (
            "Login failed due to CAPTCHA. Use external Chrome session handoff for this vendor, "
            "complete login manually, then rerun verification/fetch."
        )
    if action_required == "login_required":
        return "Log in manually in the external Chrome window, then rerun external session verification."
    if action_required == "pricing_locked":
        return "Account is authenticated but pricing is locked; unlock pricing in the vendor account, then retry."
    if action_required == "store_access_required":
        return "Select the required location/pickup/order cycle in the vendor account, then retry."
    return f"External Chrome session verification for {vendor_id} failed; inspect the vendor page and retry."


def interactive_setup(
    *,
    vendor_id: str,
    port: int,
    chrome_path: str | None = None,
    wait_for_user=input,
) -> ExternalBrowserSessionResult:
    config, target = resolve_vendor_handoff(vendor_id)
    start_url = config.account_url or config.login_url or target.url
    launch_external_chrome(vendor_id=vendor_id, url=start_url, port=port, chrome_path=chrome_path)
    print(f"Opened Chrome for {vendor_id} using profile {external_profile_label(vendor_id)}.")
    print("Log in manually in the opened Chrome window. Complete CAPTCHA/2FA normally.")
    print("When the account/store page is fully loaded, return here and press Enter.")
    wait_for_user()
    return verify_external_session(vendor_id=vendor_id, port=port, target=target, config=config)


def main() -> None:
    parser = argparse.ArgumentParser(description="External Chrome session handoff for a vendor.")
    parser.add_argument("--vendor-id", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--chrome-path")
    args = parser.parse_args()
    result = interactive_setup(vendor_id=args.vendor_id, port=args.port, chrome_path=args.chrome_path)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
