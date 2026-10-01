"""Configured vendor page fetching using Playwright storage state."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import re
import socket
import threading
from urllib.parse import urlparse

from .account_connections import (
    connected_profile_ready,
    connection_for_vendor,
    ensure_vendor_connected,
    readiness_target_for_vendor,
    session_profile_path,
    update_connection_status,
)
from .detection import classify_fetch_status, detect_fetch_status
from .models import FetchArtifacts, FetchMetadata, FetchStatus, VendorPageTarget
from .observation import observe_vendor_page
from .storage import (
    content_hash,
    fetch_output_dir,
    latest_fetch_metadata,
    sanitize_reason,
    vendor_account_config,
    write_fetch_artifacts,
)
from .variant_capture import capture_product_variants

# Vendors fetched by attaching to the user's already-open, manually-logged-in
# CDP browser (no second profile launch). Membership is data-driven from each
# vendor's auth_strategy so the login-browser and fetch strategies stay in sync.
GRAZECART_VENDOR_HOSTS = {
    "vendor_b": "example-farm-b.test",
    "vendor_c": "example-farm-c.test",
    "vendor_d": "example-farm-d.test",
    "vendor_a": "example-farm-a.test",
}

_WORKER_STATE = threading.local()


class _CdpWorker:
    def __init__(self, vendor_id: str, playwright, browser, page):
        self.vendor_id = vendor_id
        self.playwright = playwright
        self.browser = browser
        self.page = page


@contextmanager
def shared_cdp_fetch_worker(vendor_id: str):
    """Reuse one controlled CDP page for all captures in a vendor job."""
    if not _uses_cdp_attach(vendor_id):
        yield None
        return
    from playwright.sync_api import sync_playwright
    from .external_browser_session import vendor_debug_port

    port = vendor_debug_port(vendor_id)
    if not _port_is_listening(port):
        yield None
        return

    manager = sync_playwright()
    playwright = manager.__enter__()
    browser = None
    page = None
    previous = getattr(_WORKER_STATE, "cdp_worker", None)
    try:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
        except TypeError:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            page.goto("about:blank", wait_until="domcontentloaded")
            page.evaluate("(title) => { document.title = title; }", f"FarmFind Fetch - {vendor_id}")
        except Exception:
            pass
        worker = _CdpWorker(vendor_id, playwright, browser, page)
        _WORKER_STATE.cdp_worker = worker
        yield worker
    finally:
        _WORKER_STATE.cdp_worker = previous
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        manager.__exit__(None, None, None)


def _uses_cdp_attach(vendor_id: str) -> bool:
    # Every vendor's "Open login browser" launches Chrome with a debug port
    # (external handoff OR credential session profile). Fetch attaches to that
    # already-open, logged-in browser for any known vendor host; it only actually
    # connects when the port is listening (see _port_is_listening below).
    return vendor_id in GRAZECART_VENDOR_HOSTS


def build_metadata(
    target: VendorPageTarget,
    *,
    status: FetchStatus,
    html: str = "",
    http_status: int | None = None,
    title: str | None = None,
    final_url: str | None = None,
    error_message: str | None = None,
) -> FetchMetadata:
    return FetchMetadata(
        vendor_id=target.vendor_id,
        page_id=target.page_id,
        url=target.url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        requires_login=target.requires_login,
        auth_state_label=target.auth_state_label,
        status=status,
        capture_method="credential_browser_login",
        http_status=http_status,
        title=title,
        page_title=title,
        final_url=final_url,
        source_url=target.url,
        error_message=error_message,
        content_hash=content_hash(html) if html else None,
    )


def latest_success_is_recent(target: VendorPageTarget) -> bool:
    latest = latest_fetch_metadata(target.vendor_id, target.page_id)
    if latest is None or latest.status != "success":
        return False
    config = vendor_account_config(target.vendor_id)
    interval_hours = config.min_fetch_interval_hours if config else 24
    try:
        fetched_at = datetime.fromisoformat(latest.fetched_at)
    except ValueError:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - fetched_at
    return age.total_seconds() < interval_hours * 3600


def action_required_metadata(
    target: VendorPageTarget,
    *,
    action_required: str = "reconnect_required",
    connection_status: str = "action_required",
    notes: str | None = None,
) -> FetchMetadata:
    reason = notes or "Vendor account connection is required before fetching."
    return build_metadata(
        target,
        status="action_required",
        final_url=target.url,
        error_message=None,
    ).model_copy(
        update={
            "action_required": action_required,
            "connection_status": connection_status,
            "classification_reason": reason,
            "recommended_next_action": reason,
            "notes": reason,
            "warnings": ["No connected vendor browser profile was available."],
        }
    )


def skipped_recent_metadata(target: VendorPageTarget) -> FetchMetadata:
    latest = latest_fetch_metadata(target.vendor_id, target.page_id)
    return build_metadata(
        target,
        status="skipped_recent",
        final_url=target.url,
    ).model_copy(
        update={
            "connection_status": connection_for_vendor(target.vendor_id).connection_status,
            "notes": "A successful fetch already exists inside the configured interval.",
            "recommended_next_action": "Use --force to refresh the browser capture.",
            "html_path": latest.html_path if latest else None,
            "screenshot_path": latest.screenshot_path if latest else None,
            "metadata_path": latest.metadata_path if latest else None,
            "content_hash": latest.content_hash if latest else None,
        }
    )


def _status_to_metadata_status(status: FetchStatus) -> FetchStatus:
    if status in {
        "captcha_required",
        "auth_expired",
        "login_required",
        "pricing_locked",
        "store_access_required",
    }:
        return "action_required"
    return status


def _action_required_for(status: FetchStatus) -> str | None:
    if status == "captcha_required":
        return "captcha_required"
    if status == "auth_expired":
        return "reconnect_required"
    if status == "login_required":
        return "login_required"
    if status == "pricing_locked":
        return "pricing_locked"
    if status == "store_access_required":
        return "store_access_required"
    if status == "action_required":
        return "two_factor_required"
    return None


def _connection_status_for(target: VendorPageTarget, status: FetchStatus) -> str | None:
    if status in {
        "captcha_required",
        "login_required",
        "action_required",
        "pricing_locked",
        "store_access_required",
    }:
        return "action_required"
    if status == "auth_expired":
        return "expired"
    if target.requires_login and status == "success":
        return "connected"
    return None


def _recommended_next_action(metadata_status: FetchStatus, action_required: str | None, reason: str) -> str:
    if metadata_status == "success":
        return "Review observation.json and variant_snapshots.json, then stage import candidates if the variant data looks correct."
    if action_required == "login_required":
        return "Reconnect or refresh the saved vendor login session, then rerun capture_vendor_page with --force."
    if action_required == "captcha_required":
        return "Login failed due to CAPTCHA. Use external Chrome session handoff for this vendor, complete login manually, then rerun verification/fetch."
    if action_required == "two_factor_required":
        return "Complete the two-factor challenge in the persistent browser profile, then rerun with --force."
    if action_required == "pricing_locked":
        return "Prices are hidden behind an account/membership on this page. Confirm the vendor login unlocks pricing, then rerun with --force. Do not stage import candidates from this locked page."
    if action_required == "store_access_required":
        return "The store still needs a location/pickup or an open order cycle before products are purchasable. Set that up in the persistent browser profile, then rerun with --force."
    if metadata_status == "failed":
        return f"Inspect page.html/page.txt and update required markers or selectors. Reason: {reason}"
    return reason


def _wait_for_vendor_product_content(page, target: VendorPageTarget) -> None:
    for state in ["load", "networkidle"]:
        try:
            page.wait_for_load_state(state, timeout=7000)
        except Exception:
            pass
    selectors = [
        "body",
        "h1.product_title",
        ".product_title",
        "form.variations_form",
        "button.single_add_to_cart_button",
        "select[name^='attribute_']",
        "[itemtype='https://schema.org/Product']",
        ".productListing__price--grid",
        ".variants-dropdown-toggle",
        ".productListing__addToCartButton--grid",
    ]
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=2500)
            return
        except Exception:
            continue
    if target.required_text_markers:
        for marker in target.required_text_markers:
            try:
                page.get_by_text(marker, exact=False).first.wait_for(timeout=1500)
                return
            except Exception:
                continue


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def _path(url: str) -> str:
    return (urlparse(url).path or "").rstrip("/").lower()


def _score_cdp_page(page, target: VendorPageTarget) -> int:
    url = getattr(page, "url", "") or ""
    if not url or url == "about:blank":
        return -100
    if _host(url) != _host(target.url):
        return -100
    score = 10
    page_path = _path(url)
    target_path = _path(target.url)
    if page_path == target_path:
        score += 100
    elif target_path and (page_path.startswith(target_path) or target_path.startswith(page_path)):
        score += 60
    if "/store" in page_path:
        score += 20
    if "/account" in page_path:
        score -= 10
    return score


def _best_cdp_page(browser, target: VendorPageTarget):
    pages = [page for context in browser.contexts for page in context.pages]
    scored = sorted(((_score_cdp_page(page, target), page) for page in pages), key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return context.pages[0] if context.pages else context.new_page()


def _controlled_cdp_fetch_page(browser, target: VendorPageTarget):
    """Create a dedicated tab in the authenticated CDP context for full crawls."""
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    try:
        page.goto("about:blank", wait_until="domcontentloaded")
        page.evaluate(
            "(title) => { document.title = title; }",
            f"FarmFind Fetch - {target.vendor_id}",
        )
    except Exception:
        pass
    return page


def _is_usable_store_tab(url: str, target: VendorPageTarget) -> bool:
    return _host(url) == _host(target.url) and "/store" in _path(url)


def _wait_for_cdp_product_content(page) -> None:
    for state in ["domcontentloaded", "load"]:
        try:
            page.wait_for_load_state(state, timeout=2500)
        except Exception:
            pass
    for selector in [
        "[itemtype='https://schema.org/Product']",
        ".productListing__price--grid",
        ".variants-dropdown-toggle",
        ".productListing__addToCartButton--grid",
        "body",
    ]:
        try:
            page.wait_for_selector(selector, timeout=1000)
            return
        except Exception:
            continue


def _grazecart_page_evidence(html: str, text: str) -> dict:
    haystack = f"{html}\n{text}"
    lower = haystack.lower()
    return {
        "product_listing_blocks": lower.count("productlisting__"),
        "schema_product_sections": len(
            re.findall(r'itemtype=["\']https?://schema\.org/Product["\']', html, flags=re.IGNORECASE)
        ),
        "has_dollar_price": bool(re.search(r"\$\s?\d", haystack)),
        "has_gift_amount": "gift amount" in lower,
        "has_order_button": any(
            marker in lower
            for marker in ["add to cart", "add to order", "select option", "select options", "sold out"]
        ),
        "has_login_form": bool(re.search(r"<input\b[^>]*type\s*=\s*['\"]?password", html, flags=re.IGNORECASE)),
        "has_captcha": any(marker in lower for marker in ["captcha", "recaptcha", "hcaptcha"]),
        "has_pricing_lock": any(
            marker in lower
            for marker in ["sign up for pricing", "log in to see price", "login to see price", "sign in to see price"]
        ),
    }


def _metadata_for_cdp_capture(
    target: VendorPageTarget,
    *,
    html: str,
    text: str,
    title: str | None,
    final_url: str,
    selected_tab_url: str,
    variants,
    detection,
) -> FetchMetadata:
    evidence = _grazecart_page_evidence(html, text)
    priced_orderable = (
        (evidence["has_dollar_price"] or evidence["has_gift_amount"])
        and evidence["has_order_button"]
    )
    variant_count = len(variants.variants) if variants is not None else 0
    parser_status = "parsed" if variant_count else "parser_zero_candidates"

    status: FetchStatus = "success" if (variant_count or priced_orderable) else "action_required"
    action_required = None
    connection_status = "connected" if target.requires_login else None
    reason = "Captured dedicated CDP fetch page."
    if evidence["has_captcha"] and not priced_orderable:
        action_required = "captcha_required"
        connection_status = "action_required"
        reason = "CAPTCHA marker visible and no priced orderable product listings were captured."
    elif evidence["has_login_form"] and not priced_orderable:
        action_required = "login_required"
        connection_status = "action_required"
        reason = "Login form visible and no priced orderable product listings were captured."
    elif evidence["has_pricing_lock"] and not priced_orderable:
        action_required = "pricing_locked"
        connection_status = "action_required"
        reason = "Pricing is locked behind an account or membership."
    elif not variant_count and not priced_orderable:
        action_required = "parser_zero_candidates"
        reason = "Active browser page has product/order evidence, but no supported variants were parsed."

    return build_metadata(
        target,
        status=status,
        html=html,
        title=title,
        final_url=final_url,
    ).model_copy(
        update={
            "capture_method": "cdp_controlled_fetch_page",
            "capture_mode": "cdp_controlled_fetch_page",
            "selected_tab_url": selected_tab_url,
            "action_required": action_required,
            "connection_status": connection_status,
            "classification_reason": reason,
            "detected_product_markers": detection.detected_product_markers if detection else [],
            "detected_login_markers": detection.detected_login_markers if detection else [],
            "detected_captcha_markers": detection.detected_captcha_markers if detection else [],
            "detected_two_factor_markers": detection.detected_two_factor_markers if detection else [],
            "page_evidence": evidence,
            "parser_status": parser_status,
            "variant_count_total": variant_count,
            "recommended_next_action": _recommended_next_action(status, action_required, reason),
        }
    )


def _capture_cdp_page_with_worker(
    target: VendorPageTarget,
    output_dir: Path,
    page,
    *,
    close_page: bool,
) -> FetchMetadata:
    html = ""
    text = ""
    screenshot_viewport_bytes: bytes | None = None
    screenshot_full_bytes: bytes | None = None
    try:
        selected_tab_url = getattr(page, "url", "") or "about:blank"
        page.goto(target.url, wait_until="domcontentloaded")
        _wait_for_cdp_product_content(page)
        html = page.content()
        try:
            text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            text = ""
        try:
            screenshot_viewport_bytes = page.screenshot(full_page=False)
            screenshot_full_bytes = page.screenshot(full_page=True)
        except Exception:
            pass
        final_url = getattr(page, "url", "") or target.url
        title = page.title()
        detection = classify_fetch_status(target, html, final_url)
        variants = capture_product_variants(page, target.vendor_id, target.page_id, output_dir)
        metadata = _metadata_for_cdp_capture(
            target,
            html=html,
            text=text,
            title=title,
            final_url=final_url,
            selected_tab_url=selected_tab_url,
            variants=variants,
            detection=detection,
        )
        metadata = _metadata_with_observation_and_variants(
            metadata,
            output_dir=output_dir,
            variants=variants,
            detection=detection,
            text=text,
        )
        if target.requires_login and metadata.status == "success":
            update_connection_status(
                target.vendor_id,
                status="connected",
                fetched=True,
                connected=True,
                session_verified=True,
                store_fetch_ready=True,
                parser_ready=bool(metadata.variant_count and metadata.variant_count > 0),
                notes="Authenticated CDP browser fetch succeeded.",
            )
        return write_fetch_artifacts(
            target,
            FetchArtifacts(
                metadata=metadata,
                html=html,
                text=text,
                screenshot_bytes=screenshot_full_bytes,
                screenshot_viewport_bytes=screenshot_viewport_bytes,
                screenshot_full_bytes=screenshot_full_bytes,
            ),
            output_dir=output_dir,
        )
    finally:
        if close_page:
            try:
                page.close()
            except Exception:
                pass


def _fetch_grazecart_via_cdp(target: VendorPageTarget, output_dir: Path) -> FetchMetadata | None:
    if not _uses_cdp_attach(target.vendor_id):
        return None
    expected_host = GRAZECART_VENDOR_HOSTS.get(target.vendor_id)
    if expected_host and _host(target.url) != expected_host:
        return None

    shared_worker = getattr(_WORKER_STATE, "cdp_worker", None)
    if shared_worker is not None and shared_worker.vendor_id == target.vendor_id:
        return _capture_cdp_page_with_worker(target, output_dir, shared_worker.page, close_page=False)

    from playwright.sync_api import sync_playwright
    from .external_browser_session import vendor_debug_port

    port = vendor_debug_port(target.vendor_id)
    if not _port_is_listening(port):
        return None

    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
            except TypeError:
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            try:
                page = _controlled_cdp_fetch_page(browser, target)
                return _capture_cdp_page_with_worker(target, output_dir, page, close_page=True)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - CDP failures become normal metadata.
        html = ""
        text = ""
        metadata = build_metadata(target, status="failed", html=html, error_message=str(exc)).model_copy(
            update={
                "capture_method": "cdp_controlled_fetch_page",
                "capture_mode": "cdp_controlled_fetch_page",
                "classification_reason": f"CDP controlled fetch page failed before classification: {exc}",
                "recommended_next_action": "Leave the vendor browser open after login, then rerun Fetch.",
                "text_excerpt": text.strip()[:1500] if text else None,
            }
        )
        return write_fetch_artifacts(
            target,
            FetchArtifacts(
                metadata=metadata,
                html=html,
                text=text,
                screenshot_bytes=None,
                screenshot_viewport_bytes=None,
                screenshot_full_bytes=None,
            ),
            output_dir=output_dir,
        )


def _metadata_with_observation_and_variants(
    metadata: FetchMetadata,
    *,
    output_dir: Path,
    observation=None,
    variants=None,
    detection=None,
    text: str = "",
) -> FetchMetadata:
    update: dict = {
        "text_excerpt": text.strip()[:1500] if text else None,
    }
    if detection is not None:
        update.update(
            {
                "classification_reason": detection.classification_reason,
                "detected_product_markers": detection.detected_product_markers,
                "detected_login_markers": detection.detected_login_markers,
                "detected_captcha_markers": detection.detected_captcha_markers,
                "detected_two_factor_markers": detection.detected_two_factor_markers,
            }
        )
    if observation is not None:
        dropdown_labels = [
            dropdown.label or dropdown.name or ""
            for dropdown in observation.detected_dropdowns
            if dropdown.label or dropdown.name
        ]
        update.update(
            {
                "page_kind": observation.page_kind,
                "product_title": observation.detected_product_title,
                "detected_dropdowns": dropdown_labels,
                "observation_path": str(output_dir / "observation.json"),
            }
        )
    if variants is not None:
        coverage = getattr(variants, "coverage_report", {}) or {}
        update.update(
            {
                "variant_count": len(variants.variants),
                "variant_snapshots_path": str(output_dir / "variant_snapshots.json"),
                "product_title": variants.product_title or update.get("product_title"),
                "page_kind": variants.page_kind or update.get("page_kind"),
                "coverage_report": coverage,
            }
        )
    action_required = metadata.action_required
    update["recommended_next_action"] = _recommended_next_action(
        metadata.status,
        action_required,
        update.get("classification_reason") or metadata.error_message or "No additional reason available.",
    )
    return metadata.model_copy(update=update)


def fetch_vendor_page(target: VendorPageTarget, force_refresh: bool = False) -> FetchMetadata:
    from playwright.sync_api import sync_playwright

    html = ""
    text = ""
    screenshot_bytes: bytes | None = None
    screenshot_viewport_bytes: bytes | None = None
    screenshot_full_bytes: bytes | None = None
    metadata: FetchMetadata
    output_dir = fetch_output_dir(target)
    if not force_refresh and latest_success_is_recent(target):
        return write_fetch_artifacts(target, FetchArtifacts(metadata=skipped_recent_metadata(target)))
    cdp_metadata = _fetch_grazecart_via_cdp(target, output_dir)
    if cdp_metadata is not None:
        return cdp_metadata
    if target.requires_login and not connected_profile_ready(target.vendor_id):
        connection = ensure_vendor_connected(target.vendor_id)
        if connection.connection_status != "connected":
            return write_fetch_artifacts(
                target,
                FetchArtifacts(
                    metadata=action_required_metadata(
                        target,
                        action_required=connection.action_required or "login_required",
                        connection_status=connection.connection_status,
                        notes=connection.notes,
                    ),
                    html="",
                ),
            )
    try:
        with sync_playwright() as playwright:
            if target.requires_login:
                config = vendor_account_config(target.vendor_id)
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(session_profile_path(target.vendor_id)),
                    headless=True,
                    channel=config.browser_channel if config else None,
                )
                browser = None
            else:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context()
            try:
                page = context.new_page()
                response = page.goto(target.url, wait_until="domcontentloaded")
                _wait_for_vendor_product_content(page, target)
                html = page.content()
                try:
                    text = page.locator("body").inner_text(timeout=5000)
                except Exception:
                    text = ""
                screenshot_viewport_bytes = page.screenshot(full_page=False)
                screenshot_full_bytes = page.screenshot(full_page=True)
                screenshot_bytes = screenshot_full_bytes
                final_url = page.url
                detection = classify_fetch_status(target, html, final_url)
                detected_status = detection.status
                metadata_status = _status_to_metadata_status(detected_status)
                metadata = build_metadata(
                    target,
                    status=metadata_status,
                    html=html,
                    http_status=response.status if response else None,
                    title=page.title(),
                    final_url=final_url,
                ).model_copy(
                    update={
                        "action_required": _action_required_for(detected_status),
                        "connection_status": _connection_status_for(target, detected_status),
                        "detected_login_markers": detection.detected_login_markers,
                        "detected_captcha_markers": detection.detected_captcha_markers,
                        "detected_two_factor_markers": detection.detected_two_factor_markers,
                        "detected_product_markers": detection.detected_product_markers,
                        "classification_reason": detection.classification_reason,
                    }
                )

                observation = None
                variants = None
                try:
                    observation = observe_vendor_page(page, target.vendor_id, target.page_id, output_dir)
                except Exception as exc:  # noqa: BLE001 - observation must not destroy artifacts.
                    metadata = metadata.model_copy(
                        update={"warnings": metadata.warnings + [f"Observation failed: {exc}"]}
                    )

                should_capture_variants = metadata.status == "success" and (
                    (observation is not None and observation.page_kind == "product_detail")
                    or (observation is not None and observation.page_kind == "category_listing" and target.vendor_id in {"vendor_b", "vendor_c", "vendor_d"})
                    or "variations_form" in html.lower()
                    or "product-card" in html.lower()
                    or "select[name^='attribute_']" in html.lower()
                    or "data-product_variations" in html.lower()
                )
                if should_capture_variants:
                    try:
                        variants = capture_product_variants(page, target.vendor_id, target.page_id, output_dir)
                    except Exception as exc:  # noqa: BLE001 - keep product page capture successful but warn.
                        metadata = metadata.model_copy(
                            update={"warnings": metadata.warnings + [f"Variant capture failed: {exc}"]}
                        )
                metadata = _metadata_with_observation_and_variants(
                    metadata,
                    output_dir=output_dir,
                    observation=observation,
                    variants=variants,
                    detection=detection,
                    text=text,
                )
                readiness_target = readiness_target_for_vendor(target.vendor_id)
                if (
                    target.requires_login
                    and metadata.status == "success"
                    and readiness_target is not None
                    and target.page_id == readiness_target.page_id
                ):
                    update_connection_status(
                        target.vendor_id,
                        status="connected",
                        fetched=True,
                        connected=True,
                        session_verified=True,
                        store_fetch_ready=True,
                        parser_ready=bool(metadata.variant_count and metadata.variant_count > 0),
                        notes="Authenticated fetch succeeded.",
                    )
            finally:
                context.close()
                if browser is not None:
                    browser.close()
    except Exception as exc:  # noqa: BLE001 - fetch failures become metadata.
        safe_exc = sanitize_reason(str(exc))
        raw_data = str(exc).lower()
        # The login browser and the fetch both use the same persistent profile;
        # Chromium refuses to open it twice. This is not a permanent failure, so
        # report a clear reason instead of a stale "failed".
        profile_in_use = any(
            marker in raw_data
            for marker in (
                "existing browser session",
                "already in use",
                "profile is already",
                "target page, context or browser has been closed",
                "target closed",
            )
        )
        if profile_in_use:
            metadata = build_metadata(
                target,
                status="action_required",
                html=html,
                error_message="The vendor login browser is open on this profile.",
            ).model_copy(
                update={
                    "action_required": "manual_login_required",
                    "connection_status": "action_required",
                    "classification_reason": "Login browser is holding the vendor profile; cannot open a second session.",
                    "recommended_next_action": (
                        "The login browser is still open on this vendor profile. Finish logging in and "
                        "close that browser window, then click Fetch again."
                    ),
                    "text_excerpt": text.strip()[:1500] if text else None,
                }
            )
        else:
            metadata = build_metadata(
                target,
                status="failed",
                html=html,
                error_message=safe_exc,
            ).model_copy(
                update={
                    "classification_reason": sanitize_reason(f"Fetch failed before classification: {safe_exc}"),
                    "recommended_next_action": "Inspect the browser/login diagnostics and rerun with --force after fixing the fetch error.",
                    "text_excerpt": text.strip()[:1500] if text else None,
                }
            )
    if metadata.metadata_path:
        return metadata
    return write_fetch_artifacts(
        target,
        FetchArtifacts(
            metadata=metadata,
            html=html,
            text=text,
            screenshot_bytes=screenshot_bytes,
            screenshot_viewport_bytes=screenshot_viewport_bytes,
            screenshot_full_bytes=screenshot_full_bytes,
        ),
        output_dir=output_dir,
    )


def fetch_all_connected_vendor_pages(
    targets: list[VendorPageTarget],
    force_refresh: bool = False,
) -> list[FetchMetadata]:
    results: list[FetchMetadata] = []
    for target in targets:
        results.append(fetch_vendor_page(target, force_refresh=force_refresh))
    return results


def fetch_vendor_page_with_connection(target: VendorPageTarget) -> FetchMetadata:
    return fetch_vendor_page(target)
