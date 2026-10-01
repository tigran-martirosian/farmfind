"""Vendor account connection workflow using private persistent profiles."""
from __future__ import annotations

from datetime import datetime, timezone
import socket

from .models import (
    ConnectionActionRequired,
    ConnectionStatus,
    LoginSessionResult,
    VendorAccountConnection,
    VendorConnectionConfig,
    VendorPageTarget,
)
from .credentials import (
    get_saved_password,
    get_saved_username,
    get_vendor_credential_status,
    mark_vendor_credentials_need_update,
)
from .detection import (
    CAPTCHA_MARKERS,
    TWO_FACTOR_MARKERS,
    detect_fetch_status,
    detected_markers,
    has_real_price_or_order_controls,
    pricing_lock_markers,
    store_access_markers,
    strong_captcha_markers,
    two_factor_challenge_markers,
)

# Generic passive captcha terms do not count as a challenge by themselves,
# because the reCAPTCHA v3 badge embeds them on normal logged-in pages.
_GENERIC_PASSIVE_CAPTCHA = {"recaptcha", "g-recaptcha", "h-captcha", "hcaptcha", "captcha"}


def real_captcha_markers(html: str, text: str, config: "VendorConnectionConfig") -> list[str]:
    """Detect captcha markers for the connection path: a real wall or widget, plus
    any vendor-configured challenge phrase that is not a passive badge term."""
    markers = strong_captcha_markers(html or text)
    lower_text = (text or "").lower()
    for marker in config.captcha_markers or []:
        if marker.lower() in _GENERIC_PASSIVE_CAPTCHA:
            continue
        if marker.lower() in lower_text and marker not in markers:
            markers.append(marker)
    return markers
from .storage import (
    get_vendor_connection,
    load_vendor_account_configs,
    load_vendor_connections,
    load_vendor_pages,
    external_browser_profile_path,
    persistent_profile_path,
    save_vendor_connection,
    vendor_account_config,
    write_login_attempt_diagnostics,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connection_config_or_default(vendor_id: str) -> VendorConnectionConfig:
    config = vendor_account_config(vendor_id)
    if config is not None:
        return config
    return VendorConnectionConfig(
        vendor_id=vendor_id,
        auth_state_label=vendor_id,
        login_url="",
        session_mode="persistent_profile",
    )


def selectors_for(config: VendorConnectionConfig, field: str) -> list[str]:
    plural = getattr(config, f"{field}_selectors")
    single = getattr(config, f"{field}_selector")
    selectors = list(plural)
    if single:
        selectors.append(single)
    seen: set[str] = set()
    return [selector for selector in selectors if not (selector in seen or seen.add(selector))]


def find_first_visible_selector(page_or_frame, selectors: list[str], timeout_ms: int = 1000) -> str | None:
    for selector in selectors:
        locator = page_or_frame.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            if locator.is_visible(timeout=timeout_ms):
                return selector
        except Exception:
            continue
    return None


def find_login_frame_and_selectors(page, config: VendorConnectionConfig) -> tuple[object, dict[str, str | None]]:
    username_selectors = selectors_for(config, "username")
    password_selectors = selectors_for(config, "password")
    submit_selectors = selectors_for(config, "submit")
    fields = {
        "username": find_first_visible_selector(page, username_selectors),
        "password": find_first_visible_selector(page, password_selectors),
        "submit": find_first_visible_selector(page, submit_selectors),
    }
    if fields["username"] and fields["password"] and fields["submit"]:
        return page, fields
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        frame_fields = {
            "username": find_first_visible_selector(frame, username_selectors),
            "password": find_first_visible_selector(frame, password_selectors),
            "submit": find_first_visible_selector(frame, submit_selectors),
        }
        if frame_fields["username"] and frame_fields["password"] and frame_fields["submit"]:
            return frame, frame_fields
    return page, fields


def is_browser_closed_error(exc: Exception) -> bool:
    text = f"{exc.__class__.__name__}: {exc}"
    return (
        "TargetClosedError" in text
        or "Target page, context or browser has been closed" in text
        or "Browser was closed" in text
    )


def safe_page_title(page) -> str | None:
    try:
        return page.title() if hasattr(page, "title") else None
    except Exception:
        return None


def safe_page_url(page) -> str | None:
    try:
        return getattr(page, "url", None)
    except Exception:
        return None


def safe_page_content(page) -> str:
    try:
        return page.content()
    except Exception:
        return ""


def safe_page_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2000)
    except Exception:
        return safe_page_content(page)


def safe_screenshot(page, path=None) -> bytes | None:
    try:
        screenshot = page.screenshot(full_page=True)
    except Exception:
        return None
    if path is not None:
        try:
            path.write_bytes(screenshot)
        except Exception:
            return None
    return screenshot


def safe_detect_markers(page, markers) -> list[str]:
    text = safe_page_text(page)
    return detected_markers(text, markers)


def page_text_excerpt(page, limit: int = 1500) -> str:
    return safe_page_text(page).strip()[:limit]


def login_wall_markers(config: VendorConnectionConfig) -> list[str]:
    return [
        "sign in",
        "log in",
        "login",
        "password",
        "username",
        "forgot password",
        *config.login_failure_markers,
    ]


def verify_logged_in_state(page, config: VendorConnectionConfig) -> dict:
    def snapshot() -> dict:
        text = safe_page_text(page)
        return {
            "detected_success_markers": detected_markers(
                text,
                config.login_success_markers,
            ),
            "detected_login_markers": detected_markers(text, login_wall_markers(config)),
            "detected_captcha_markers": real_captcha_markers(safe_page_content(page), text, config),
            "detected_two_factor_markers": two_factor_challenge_markers(text)
            if not config.two_factor_markers
            else detected_markers(text, config.two_factor_markers),
            "final_url": safe_page_url(page),
            "page_title": safe_page_title(page),
            "text_excerpt": text.strip()[:1500],
        }

    result = snapshot()
    if result["detected_success_markers"]:
        return {"is_connected": True, **result}

    check_url = config.post_login_check_url or config.account_url
    if check_url:
        try:
            current_url = result["final_url"] or ""
            if current_url != check_url:
                page.goto(check_url, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("load", timeout=5000)
                except Exception:
                    pass
                result = snapshot()
        except Exception:
            result = snapshot()

    return {"is_connected": bool(result["detected_success_markers"]), **result}


def classify_page_safely(
    target: VendorPageTarget,
    page,
    config: VendorConnectionConfig,
) -> tuple[ConnectionStatus, ConnectionActionRequired | None, str | None]:
    verification = verify_logged_in_state(page, config)
    if not verification["text_excerpt"]:
        return (
            "action_required",
            "login_required",
            "Browser was closed before login could be verified.",
        )
    if verification["is_connected"]:
        return "connected", None, None
    if verification["detected_captcha_markers"]:
        return "action_required", "captcha_required", None
    if verification["detected_two_factor_markers"]:
        return "action_required", "two_factor_required", None
    bad_credential_markers = detected_markers(
        verification["text_excerpt"],
        config.login_failure_markers,
    )
    if bad_credential_markers:
        return "failed", "credential_update_required", None
    # Re-check the real page state so a page that is authenticated-but-locked is
    # not mislabeled as a plain login wall. Pricing-locked and store-access pages
    # are distinct action_required states.
    page_markup = f"{safe_page_content(page)}\n{verification['text_excerpt']}"
    if not has_real_price_or_order_controls(page_markup):
        if pricing_lock_markers(page_markup):
            return "action_required", "pricing_locked", None
        if store_access_markers(page_markup):
            return "action_required", "store_access_required", None
    return "action_required", "login_required", None


def login_attempt_diagnostics(
    vendor_id: str,
    config: VendorConnectionConfig,
    page,
    *,
    found_selectors: dict[str, str | None],
    error_message: str | None = None,
) -> dict:
    verification = verify_logged_in_state(page, config)
    metadata = {
        "vendor_id": vendor_id,
        "login_url": config.login_url,
        "account_url": config.account_url,
        "post_login_check_url": config.post_login_check_url,
        "final_url": safe_page_url(page),
        "page_title": safe_page_title(page),
        "attempted_username_selectors": selectors_for(config, "username"),
        "attempted_password_selectors": selectors_for(config, "password"),
        "attempted_submit_selectors": selectors_for(config, "submit"),
        "found_username_selector": found_selectors.get("username"),
        "found_password_selector": found_selectors.get("password"),
        "found_submit_selector": found_selectors.get("submit"),
        "detected_success_markers": verification["detected_success_markers"],
        "detected_login_markers": verification["detected_login_markers"],
        "detected_captcha_markers": verification["detected_captcha_markers"],
        "detected_two_factor_markers": verification["detected_two_factor_markers"],
        "text_excerpt": verification["text_excerpt"],
        "error_message": error_message,
        "screenshot_path": None,
    }
    screenshot = safe_screenshot(page)
    return write_login_attempt_diagnostics(vendor_id, metadata, screenshot)


def new_connection(
    vendor_id: str,
    *,
    status: ConnectionStatus = "not_connected",
    action_required: ConnectionActionRequired | None = "login_required",
    notes: str | None = None,
) -> VendorAccountConnection:
    config = connection_config_or_default(vendor_id)
    credential = get_vendor_credential_status(vendor_id)
    now = now_iso()
    return VendorAccountConnection(
        vendor_id=vendor_id,
        auth_state_label=config.auth_state_label,
        session_mode=config.session_mode,
        browser_channel=config.browser_channel,
        credential_status=credential.credential_status,
        credential_username_hint=credential.username_hint,
        credential_storage_provider=credential.credential_storage_provider,
        connection_status=status,
        action_required=action_required,
        login_url=config.login_url or None,
        account_url=config.account_url,
        notes=notes,
        created_at=now,
        updated_at=now,
    )


def connection_for_vendor(vendor_id: str) -> VendorAccountConnection:
    existing = get_vendor_connection(vendor_id)
    return with_credential_status(existing) if existing else new_connection(vendor_id)


def list_vendor_connections() -> list[VendorAccountConnection]:
    existing = {connection.vendor_id: connection for connection in load_vendor_connections()}
    for config in load_vendor_account_configs():
        existing.setdefault(config.vendor_id, new_connection(config.vendor_id))
    refreshed = [with_credential_status(connection) for connection in existing.values()]
    return sorted(refreshed, key=lambda connection: connection.vendor_id)


def with_credential_status(connection: VendorAccountConnection) -> VendorAccountConnection:
    credential = get_vendor_credential_status(connection.vendor_id)
    return connection.model_copy(
        update={
            "credential_status": credential.credential_status,
            "credential_username_hint": credential.username_hint,
            "credential_storage_provider": credential.credential_storage_provider,
        }
    )


def update_connection_status(
    vendor_id: str,
    *,
    status: ConnectionStatus,
    action_required: ConnectionActionRequired | None = None,
    notes: str | None = None,
    connected: bool = False,
    fetched: bool = False,
    session_verified: bool | None = None,
    store_fetch_ready: bool | None = None,
    parser_ready: bool | None = None,
    error_message: str | None = None,
) -> VendorAccountConnection:
    existing = connection_for_vendor(vendor_id)
    credential = get_vendor_credential_status(vendor_id)
    now = now_iso()
    return save_vendor_connection(
        existing.model_copy(
            update={
                "credential_status": credential.credential_status,
                "credential_username_hint": credential.username_hint,
                "credential_storage_provider": credential.credential_storage_provider,
                "connection_status": status,
                "action_required": action_required,
                "session_verified": (
                    (status == "connected" or action_required in {"pricing_locked", "store_access_required", "location_or_order_cycle_required"})
                    if session_verified is None
                    else session_verified
                ),
                "store_fetch_ready": (status == "connected" and action_required is None)
                if store_fetch_ready is None
                else store_fetch_ready,
                "parser_ready": existing.parser_ready if parser_ready is None else parser_ready,
                "notes": notes,
                "error_message": error_message,
                "last_checked_at": now,
                "last_connected_at": now if connected else existing.last_connected_at,
                "last_successful_fetch_at": now
                if fetched
                else existing.last_successful_fetch_at,
                "updated_at": now,
            }
        )
    )


def classify_connection_result(
    target: VendorPageTarget,
    html: str,
    final_url: str,
    config: VendorConnectionConfig | None = None,
) -> tuple[ConnectionStatus, ConnectionActionRequired | None]:
    if config is not None:
        if real_captcha_markers(html, html, config):
            return "action_required", "captcha_required"
        if detect_fetch_status(target, html, final_url) == "success":
            return "connected", None
        if (
            detected_markers(html, config.two_factor_markers)
            if config.two_factor_markers
            else two_factor_challenge_markers(html)
        ):
            return "action_required", "two_factor_required"
        if detected_markers(html, config.login_failure_markers):
            return "failed", "credential_update_required"
        if detected_markers(html, config.login_success_markers):
            return "connected", None
    fetch_status = detect_fetch_status(target, html, final_url)
    if fetch_status == "success":
        return "connected", None
    if fetch_status == "captcha_required":
        return "action_required", "captcha_required"
    if fetch_status == "pricing_locked":
        return "action_required", "pricing_locked"
    if fetch_status == "store_access_required":
        return "action_required", "store_access_required"
    if fetch_status == "auth_expired":
        return "expired", "reconnect_required"
    return "action_required", "login_required"


def connection_check_url(vendor_id: str) -> str | None:
    config = connection_config_or_default(vendor_id)
    if config.post_login_check_url:
        return config.post_login_check_url
    target = check_target_for_vendor(vendor_id)
    return target.url if target is not None else config.account_url


def check_target_for_vendor(vendor_id: str) -> VendorPageTarget | None:
    return next(
        (target for target in load_vendor_pages() if target.vendor_id == vendor_id),
        None,
    )


READINESS_PAGE_BY_VENDOR = {
    "vendor_b": "milk",
    "vendor_c": "dairy",
    "vendor_d": "dairy_page",
}


def readiness_target_for_vendor(vendor_id: str) -> VendorPageTarget | None:
    if check_target_for_vendor(vendor_id) is None:
        return None
    targets = [target for target in load_vendor_pages() if target.vendor_id == vendor_id]
    if not targets:
        return None
    readiness_page_id = READINESS_PAGE_BY_VENDOR.get(vendor_id, targets[0].page_id)
    return next((target for target in targets if target.page_id == readiness_page_id), targets[0])


def session_profile_path(vendor_id: str):
    config = connection_config_or_default(vendor_id)
    if config.auth_strategy == "external_browser_handoff" or config.captcha_sensitive:
        return external_browser_profile_path(vendor_id)
    return persistent_profile_path(vendor_id)


def connected_profile_ready(vendor_id: str) -> bool:
    connection = get_vendor_connection(vendor_id)
    return bool(
        connection
        and connection.session_mode == "persistent_profile"
        and connection.connection_status == "connected"
        and connection.store_fetch_ready
        and session_profile_path(vendor_id).exists()
    )


def persistent_profile_label(vendor_id: str) -> str:
    return f"farmfind_profile_{vendor_id}"


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def launch_login_session(vendor_id: str, *, force_open: bool = False) -> LoginSessionResult:
    """Open a headed local browser for the user to refresh login manually.

    This never returns cookies, credentials, auth headers, or profile paths. The
    existing Check button verifies the session after the user completes login.
    """
    config = connection_config_or_default(vendor_id)
    connection = connection_for_vendor(vendor_id)
    if not force_open and connection.connection_status == "connected" and connected_profile_ready(vendor_id):
        return LoginSessionResult(
            vendor_id=vendor_id,
            status="already_connected",
            profile_label=persistent_profile_label(vendor_id),
            recommended_next_action="Session is already connected. Use Check connection to verify if needed.",
        )
    if config.auth_strategy == "external_browser_handoff" or config.captcha_sensitive:
        from .external_browser_session import (
            external_profile_label,
            launch_external_chrome,
            resolve_vendor_handoff,
            vendor_debug_port,
        )

        port = vendor_debug_port(vendor_id)
        try:
            handoff_config, target = resolve_vendor_handoff(vendor_id)
            start_url = handoff_config.account_url or handoff_config.login_url or target.url
            launch_external_chrome(vendor_id=vendor_id, url=start_url, port=port)
            update_connection_status(
                vendor_id,
                status="action_required",
                action_required="manual_login_required",
                notes="Opened external Chrome for manual login/session refresh.",
            )
            return LoginSessionResult(
                vendor_id=vendor_id,
                status="launched",
                action_required="manual_login_required",
                profile_label=external_profile_label(vendor_id),
                recommended_next_action="Complete login/CAPTCHA in the opened browser, then click Check connection.",
            )
        except Exception as exc:  # noqa: BLE001 - UI needs a safe fallback.
            command = (
                f"python -m app.fetcher.external_browser_session --vendor-id {vendor_id} "
                f"--port {port}"
            )
            return LoginSessionResult(
                vendor_id=vendor_id,
                status="failed",
                action_required="manual_login_required",
                profile_label=external_profile_label(vendor_id),
                manual_command=command,
                recommended_next_action="Manual launch unavailable from the API. Run the shown command, complete login, then click Check connection.",
                warnings=[str(exc)],
            )

    start_url = config.account_url or config.login_url or connection_check_url(vendor_id)
    if not start_url:
        return LoginSessionResult(
            vendor_id=vendor_id,
            status="failed",
            action_required="login_required",
            profile_label=persistent_profile_label(vendor_id),
            recommended_next_action="No login/account URL is configured for this vendor.",
        )
    try:
        from .external_browser_session import launch_profile_chrome, vendor_debug_port

        profile_path = session_profile_path(vendor_id)
        launch_profile_chrome(
            profile_path=profile_path,
            url=start_url,
            port=vendor_debug_port(vendor_id),
        )
        update_connection_status(
            vendor_id,
            status="action_required",
            action_required="login_required",
            notes="Opened headed browser for manual login/session refresh.",
        )
        return LoginSessionResult(
            vendor_id=vendor_id,
            status="launched",
            action_required="login_required",
            profile_label=persistent_profile_label(vendor_id),
            recommended_next_action="Complete login in the opened browser, then click Check connection.",
        )
    except Exception as exc:  # noqa: BLE001 - UI needs a safe fallback.
        command = f"python -m app.fetcher.account_connections --vendor-id {vendor_id}"
        return LoginSessionResult(
            vendor_id=vendor_id,
            status="failed",
            action_required="login_required",
            profile_label=persistent_profile_label(vendor_id),
            manual_command=command,
            recommended_next_action="Manual launch unavailable from the API. Run the shown command, complete login, then click Check connection.",
            warnings=[str(exc)],
        )


def connect_vendor_account(vendor_id: str) -> VendorAccountConnection:
    """Compatibility wrapper for the credential-based login command."""
    return login_vendor_with_saved_credentials(vendor_id)


def login_vendor_with_saved_credentials(vendor_id: str) -> VendorAccountConnection:
    """Log into a vendor using saved OS-keyring credentials and a persistent profile."""
    from playwright.sync_api import sync_playwright

    config = connection_config_or_default(vendor_id)
    credential = get_vendor_credential_status(vendor_id)
    password = get_saved_password(vendor_id)
    username = get_saved_username(vendor_id)
    if credential.credential_status != "configured" or not password or not username:
        return update_connection_status(
            vendor_id,
            status="action_required",
            action_required="credential_update_required",
            notes="Saved credentials are missing or need an update.",
        )
    if not config.login_url:
        return update_connection_status(
            vendor_id,
            status="failed",
            action_required="login_required",
            notes="No login URL configured.",
        )
    if not (
        selectors_for(config, "username")
        and selectors_for(config, "password")
        and selectors_for(config, "submit")
    ):
        return update_connection_status(
            vendor_id,
            status="failed",
            action_required="credential_update_required",
            notes="Vendor login selectors are incomplete.",
        )
    profile_path = session_profile_path(vendor_id)
    profile_path.mkdir(parents=True, exist_ok=True)
    target = check_target_for_vendor(vendor_id) or VendorPageTarget(
        vendor_id=vendor_id,
        page_id="account",
        url=config.login_url,
        requires_login=True,
        auth_state_label=config.auth_state_label,
    )
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=False,
            channel=config.browser_channel or None,
        )
        page = context.new_page()
        found_selectors = {"username": None, "password": None, "submit": None}
        error_message: str | None = None
        status: ConnectionStatus = "action_required"
        action_required: ConnectionActionRequired | None = "login_required"
        try:
            page.goto(config.login_url, wait_until="domcontentloaded")
            status, action_required, verify_error = classify_page_safely(target, page, config)
            if status == "connected":
                context.close()
                return update_connection_status(
                    vendor_id,
                    status="connected",
                    action_required=None,
                    notes="Already connected through persistent browser profile.",
                    connected=True,
                    error_message=None,
                )
            if verify_error:
                error_message = verify_error
                raise RuntimeError(verify_error)
            login_frame, found_selectors = find_login_frame_and_selectors(page, config)
            if not (
                found_selectors["username"]
                and found_selectors["password"]
                and found_selectors["submit"]
            ):
                error_message = "Could not find configured login selectors."
                login_attempt_diagnostics(
                    vendor_id,
                    config,
                    page,
                    found_selectors=found_selectors,
                    error_message=error_message,
                )
                print(
                    "Could not find login fields automatically. Please log in manually "
                    "in the opened browser, then press Enter."
                )
                print("If you are already logged in, leave the browser open and press Enter.")
                print("Do not close the browser before pressing Enter.")
                input()
            else:
                login_frame.fill(found_selectors["username"], username, timeout=5000)
                login_frame.fill(found_selectors["password"], password, timeout=5000)
                login_frame.click(found_selectors["submit"], timeout=5000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    page.wait_for_load_state("load", timeout=15000)
            status, action_required, verify_error = classify_page_safely(target, page, config)
            if verify_error:
                error_message = verify_error
        except Exception as exc:  # noqa: BLE001 - login failures become metadata.
            error_message = (
                "Browser was closed before login could be verified."
                if is_browser_closed_error(exc)
                else str(exc)
            )
            login_attempt_diagnostics(
                vendor_id,
                config,
                page,
                found_selectors=found_selectors,
                error_message=error_message,
            )
            if error_message == "Browser was closed before login could be verified.":
                status = "action_required"
                action_required = "login_required"
            else:
                print(
                    "Could not complete login automatically. Please log in manually in "
                    "the opened browser, then press Enter."
                )
                print("If you are already logged in, leave the browser open and press Enter.")
                print("Do not close the browser before pressing Enter.")
                try:
                    input()
                    status, action_required, verify_error = classify_page_safely(target, page, config)
                    if verify_error:
                        error_message = verify_error
                except Exception as manual_exc:  # noqa: BLE001 - user may close the browser.
                    error_message = (
                        "Browser was closed before login could be verified."
                        if is_browser_closed_error(manual_exc)
                        else str(manual_exc)
                    )
                    status = "action_required"
                    action_required = "login_required"
        if action_required in {"captcha_required", "two_factor_required"}:
            login_attempt_diagnostics(
                vendor_id,
                config,
                page,
                found_selectors=found_selectors,
                error_message=f"Human action required: {action_required}.",
            )
            input(
                "Human action required in browser. Complete CAPTCHA/2FA/login "
                "challenge, then press Enter."
            )
            status, action_required, verify_error = classify_page_safely(target, page, config)
            if verify_error:
                error_message = verify_error
        if status != "connected" and error_message:
            status = "action_required"
            action_required = "login_required"
        if status != "connected":
            login_attempt_diagnostics(
                vendor_id,
                config,
                page,
                found_selectors=found_selectors,
                error_message=error_message or "Login markers did not show success.",
            )
        try:
            context.close()
        except Exception:
            pass
    if status == "failed" and action_required == "credential_update_required":
        mark_vendor_credentials_need_update(vendor_id)
    return update_connection_status(
        vendor_id,
        status=status,
        action_required=action_required,
        notes="Connected through credential browser login."
        if status == "connected"
        else "Credential login did not complete successfully.",
        connected=status == "connected",
        error_message=None
        if status == "connected"
        else error_message or "Login markers did not show success.",
    )


def check_vendor_connection(vendor_id: str) -> VendorAccountConnection:
    """Check a saved persistent profile against a configured vendor page."""
    from playwright.sync_api import sync_playwright

    config = connection_config_or_default(vendor_id)
    if config.auth_strategy == "external_browser_handoff" or config.captcha_sensitive:
        try:
            from .external_browser_session import (
                resolve_vendor_handoff,
                vendor_debug_port,
                verify_external_session,
            )

            handoff_config, target = resolve_vendor_handoff(vendor_id)
            port = vendor_debug_port(vendor_id)
            result = verify_external_session(
                vendor_id=vendor_id,
                port=port,
                target=target,
                config=handoff_config,
            )
            return connection_for_vendor(vendor_id).model_copy(
                update={
                    "notes": result.recommended_next_action,
                    "error_message": None if result.fetch_ready else "External session verification did not reach fetch-ready state.",
                }
            )
        except Exception as exc:
            # If the headed browser is not currently open on the handoff port,
            # fall back to a headless check against the same external profile.
            if _port_is_listening(vendor_debug_port(vendor_id)):
                return update_connection_status(
                    vendor_id,
                    status="action_required",
                    action_required="manual_login_required",
                    notes="Could not attach to the opened browser session; leave the vendor browser open and verify again.",
                    error_message=str(exc),
                )
    else:
        try:
            from .external_browser_session import vendor_debug_port, verify_browser_session

            port = vendor_debug_port(vendor_id)
            if _port_is_listening(port):
                readiness_target = readiness_target_for_vendor(vendor_id)
                check_url = connection_check_url(vendor_id)
                target = readiness_target or VendorPageTarget(
                    vendor_id=vendor_id,
                    page_id="account",
                    url=check_url or config.login_url,
                    requires_login=True,
                    auth_state_label=config.auth_state_label,
                )
                result = verify_browser_session(
                    vendor_id=vendor_id,
                    port=port,
                    target=target,
                    config=config,
                    profile_label=persistent_profile_label(vendor_id),
                    profile_ready_path=session_profile_path(vendor_id),
                )
                return connection_for_vendor(vendor_id).model_copy(
                    update={
                        "notes": result.recommended_next_action,
                        "error_message": None if result.fetch_ready else "Browser session verification did not reach fetch-ready state.",
                    }
                )
        except Exception:
            pass

    readiness_target = readiness_target_for_vendor(vendor_id)
    check_url = connection_check_url(vendor_id)
    target = readiness_target or VendorPageTarget(
        vendor_id=vendor_id,
        page_id="account",
        url=check_url or config.login_url,
        requires_login=True,
        auth_state_label=config.auth_state_label,
    )
    profile_path = session_profile_path(vendor_id)
    target_url = target.url or check_url
    if target_url is None:
        return update_connection_status(
            vendor_id,
            status="failed",
            action_required="login_required",
            notes="No vendor page configured for connection check.",
        )
    profile_path.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=True,
                channel=config.browser_channel or None,
            )
            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("load", timeout=5000)
            except Exception:
                pass
            if readiness_target is not None:
                status, action_required = classify_connection_result(
                    target,
                    safe_page_content(page),
                    safe_page_url(page) or "",
                    config,
                )
                error_message = None if status == "connected" else "Readiness page did not reach fetch-ready state."
            else:
                status, action_required, error_message = classify_page_safely(
                    target,
                    page,
                    config,
                )
            try:
                context.close()
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001 - connection checks return metadata.
        status = "action_required"
        action_required = "login_required"
        error_message = (
            "Browser was closed before login could be verified."
            if is_browser_closed_error(exc)
            else str(exc)
        )
    return update_connection_status(
        vendor_id,
        status=status,
        action_required=action_required,
        notes="Connection check completed.",
        connected=status == "connected",
        session_verified=status == "connected" or action_required in {"pricing_locked", "store_access_required"},
        store_fetch_ready=status == "connected" and action_required is None,
        error_message=error_message if status != "connected" else None,
    )


def ensure_vendor_connected(vendor_id: str) -> VendorAccountConnection:
    connection = connection_for_vendor(vendor_id)
    if connection.connection_status == "connected" and session_profile_path(vendor_id).exists():
        return check_vendor_connection(vendor_id)
    config = connection_config_or_default(vendor_id)
    if config.auth_strategy == "external_browser_handoff" or config.captcha_sensitive:
        return update_connection_status(
            vendor_id,
            status="action_required",
            action_required="manual_login_required",
            notes=(
                "This vendor is CAPTCHA-sensitive. Use external Chrome session handoff, "
                "complete login manually, then rerun verification/fetch."
            ),
        )
    credential = get_vendor_credential_status(vendor_id)
    if credential.credential_status != "configured":
        return update_connection_status(
            vendor_id,
            status="action_required",
            action_required="credential_update_required",
            notes="Saved credentials are required before connecting this vendor.",
        )
    return login_vendor_with_saved_credentials(vendor_id)


def login_all_vendors() -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {
        "connected": [],
        "action_required": [],
        "failed": [],
        "missing_credentials": [],
    }
    for config in load_vendor_account_configs():
        credential = get_vendor_credential_status(config.vendor_id)
        if credential.credential_status != "configured":
            summary["missing_credentials"].append(config.vendor_id)
            update_connection_status(
                config.vendor_id,
                status="action_required",
                action_required="credential_update_required",
                notes="Credentials are not configured.",
            )
            continue
        try:
            connection = login_vendor_with_saved_credentials(config.vendor_id)
        except Exception as exc:  # noqa: BLE001 - continue across vendors.
            summary["failed"].append(config.vendor_id)
            update_connection_status(
                config.vendor_id,
                status="failed",
                action_required=None,
                notes="Login command failed.",
                error_message=str(exc),
            )
            continue
        if connection.connection_status == "connected":
            summary["connected"].append(config.vendor_id)
        elif connection.connection_status == "failed":
            summary["failed"].append(config.vendor_id)
        else:
            summary["action_required"].append(config.vendor_id)
    return summary
