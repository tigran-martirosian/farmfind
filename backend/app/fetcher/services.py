"""Deterministic fetch-agent service wrappers for future orchestration."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .account_connections import (
    check_vendor_connection,
    connection_config_or_default,
    connection_for_vendor,
    ensure_vendor_connected,
    login_all_vendors,
    login_vendor_with_saved_credentials,
)
from .account_connections import READINESS_PAGE_BY_VENDOR as GRAZECART_READINESS_PAGE
from .credentials import (
    get_vendor_credential_status,
    set_vendor_credentials,
)
from .import_candidates import create_import_candidates_from_variant_snapshot
from .models import FetchMetadata, FetchServiceResult, ShippingQuote, VendorConnectionMetadata, VendorFetchSummary, VendorPageTarget
from .shipping_quote import build_shipping_quote_from_html
from .storage import (
    DATA_DIR,
    latest_fetch_metadata,
    list_fetch_metadata,
    load_vendor_pages,
    safe_path_part,
    sanitize_reason,
)
from .vendor_fetcher import (
    fetch_all_connected_vendor_pages,
    fetch_vendor_page as fetch_target_page,
    shared_cdp_fetch_worker,
)

def service_result_from_metadata(metadata: FetchMetadata) -> FetchServiceResult:
    return FetchServiceResult(
        status=metadata.status,
        vendor_id=metadata.vendor_id,
        page_id=metadata.page_id,
        connection_status=metadata.connection_status,
        action_required=metadata.action_required,
        html_path=metadata.html_path,
        screenshot_path=metadata.screenshot_path,
        metadata_path=metadata.metadata_path,
        content_hash=metadata.content_hash,
        warnings=metadata.warnings,
        error_message=metadata.error_message,
    )


def fetch_vendor_page(
    vendor_id: str,
    page_id: str,
    force_refresh: bool = False,
) -> FetchServiceResult:
    target = next(
        (
            target
            for target in load_vendor_pages()
            if target.vendor_id == vendor_id and target.page_id == page_id
        ),
        None,
    )
    if target is None:
        return FetchServiceResult(
            status="failed",
            vendor_id=vendor_id,
            page_id=page_id,
            error_message="Vendor page is not configured.",
        )
    return service_result_from_metadata(
        fetch_target_page(target, force_refresh=force_refresh)
    )


def fetch_all_vendor_pages(
    vendor_id: str | None = None,
    force_refresh: bool = False,
) -> list[FetchServiceResult]:
    targets = load_vendor_pages()
    if vendor_id is not None:
        targets = [target for target in targets if target.vendor_id == vendor_id]
    return [
        service_result_from_metadata(metadata)
        for metadata in fetch_all_connected_vendor_pages(
            targets,
            force_refresh=force_refresh,
        )
    ]


def get_latest_vendor_fetch(vendor_id: str, page_id: str) -> FetchServiceResult | None:
    metadata = latest_fetch_metadata(vendor_id, page_id)
    return service_result_from_metadata(metadata) if metadata else None


def _artifact_paths_for(metadata: FetchMetadata) -> dict[str, str | None]:
    return {
        "page_html": metadata.html_path,
        "screenshot": metadata.screenshot_path or metadata.screenshot_full_path,
        "metadata": metadata.metadata_path,
        "observation": metadata.observation_path,
        "variant_snapshots": metadata.variant_snapshots_path,
    }


def _count_candidates_created(variant_snapshots_path: str) -> int:
    """Create import candidates from a captured snapshot and return how many rows
    were actually written. Never treats an empty candidates.json as success."""
    candidates_path = create_import_candidates_from_variant_snapshot(variant_snapshots_path)
    try:
        rows = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(rows) if isinstance(rows, list) else 0


def _persist_candidate_count(metadata: FetchMetadata, count: int) -> None:
    if not metadata.metadata_path:
        return
    path = Path(metadata.metadata_path)
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
        raw_data["candidates_created_total"] = count
        path.write_text(json.dumps(raw_data, indent=2), encoding="utf-8")
    except Exception:
        return


def _order_targets(targets: list[VendorPageTarget], readiness_page_id: str) -> list[VendorPageTarget]:
    verify = [target for target in targets if target.page_id == readiness_page_id]
    rest = [target for target in targets if target.page_id != readiness_page_id]
    return (verify + rest) if verify else targets


def _account_authenticated(metadata: FetchMetadata) -> bool:
    # pricing_locked / store_access_required mean the account IS authenticated but
    # the store is still gated. A bare login/captcha wall means it is not.
    if metadata.action_required in {"pricing_locked", "store_access_required"}:
        return True
    return metadata.connection_status == "connected" or metadata.status == "success"


def _record_page(summary: VendorFetchSummary, page_id: str, metadata: FetchMetadata) -> None:
    summary.pages_attempted += 1
    summary.artifact_paths[page_id] = _artifact_paths_for(metadata)
    variant_count = metadata.variant_count or 0
    coverage = metadata.coverage_report or {}
    summary.product_cards_found += int(coverage.get("product_cards_found") or 0)
    summary.detail_links_found += int(coverage.get("product_detail_links_found") or 0)
    summary.detail_pages_fetched += int(coverage.get("detail_pages_fetched") or 0)
    summary.option_groups_found += int(coverage.get("option_groups_found") or 0)
    summary.bundle_pack_case_offers_found += int(coverage.get("bundle_pack_case_offers_found") or 0)
    summary.excluded_count += int(coverage.get("excluded_count") or 0)
    summary.deduped_already_approved_count += int(coverage.get("deduped_already_approved_count") or 0)
    summary.skipped_urls.extend(coverage.get("skipped_urls") or [])
    summary.incomplete_discoveries.extend(coverage.get("incomplete_discoveries") or [])
    summary.parser_warnings.extend(coverage.get("parser_warnings") or [])

    if metadata.status == "success":
        summary.pages_successful += 1
        summary.variant_count_total += variant_count
        if variant_count > 0 and metadata.variant_snapshots_path:
            created = _count_candidates_created(metadata.variant_snapshots_path)
            _persist_candidate_count(metadata, created)
            summary.candidates_by_page[page_id] = created
            summary.candidates_created_total += created
            if created == 0:
                summary.empty_candidate_pages.append(page_id)
                summary.warnings.append(
                    f"{page_id}: fetched page artifacts, but no import candidates were created: "
                    "captured variants could not be turned into candidates."
                )
        else:
            summary.candidates_by_page[page_id] = 0
            summary.empty_candidate_pages.append(page_id)
            summary.warnings.append(
                f"{page_id}: fetched page artifacts, but no import candidates were created: "
                "no purchasable variants were parsed from the page."
            )
        return

    if metadata.status == "failed":
        summary.pages_failed += 1
        return

    # Everything else (login_required, captcha_required, pricing_locked,
    # store_access_required, two_factor, skipped) is an action_required page.
    summary.pages_action_required += 1
    if metadata.action_required == "pricing_locked":
        summary.pricing_locked_pages.append(page_id)


def _not_ready_action_required(metadata: FetchMetadata, account_authenticated: bool) -> str:
    if metadata.action_required:
        return metadata.action_required
    if account_authenticated:
        return "connected_account_only"
    return "failed"


def _summary_next_action(summary: VendorFetchSummary) -> str:
    if summary.candidates_created_total > 0:
        return (
            f"Review and approve the {summary.candidates_created_total} new pending import "
            "candidate(s) in Import Review. Approved candidates then appear in the imported_only catalog."
        )
    reasons = "; ".join(summary.warnings) or "no import candidates were created."
    return f"Fetched page artifacts, but no import candidates were created: {reasons}"


def run_vendor_fetch_summary(vendor_id: str, force: bool = True) -> VendorFetchSummary:
    """Honest fetch orchestration for one vendor.

    Re-checks real fetch readiness (never trusting stale saved metadata), captures
    configured pages only when ready, auto-creates pending import candidates from
    usable captures, and returns a truthful machine-readable summary. It never
    auto-approves products — user approval still gates the imported_only catalog.
    """
    summary = VendorFetchSummary(vendor_id=vendor_id)
    targets = [target for target in load_vendor_pages() if target.vendor_id == vendor_id]
    if not targets:
        summary.action_required = "failed"
        summary.warnings.append("No vendor pages are configured for this vendor.")
        summary.recommended_next_action = (
            "Add vendor_pages.json entries for this vendor before fetching."
        )
        return summary

    readiness_page_id = GRAZECART_READINESS_PAGE.get(vendor_id, targets[0].page_id)
    ordered = _order_targets(targets, readiness_page_id)
    verify_target = ordered[0]

    # Two-level readiness: fetching the verification page re-runs live detection,
    # so a logged-out or pricing-locked store is caught even if saved metadata
    # still says "connected".
    with shared_cdp_fetch_worker(vendor_id):
        verify_metadata = fetch_target_page(verify_target, force_refresh=force)
        remaining_metadata: list[tuple[str, FetchMetadata]] = []
        if verify_metadata.status == "success":
            for target in ordered[1:]:
                remaining_metadata.append(
                    (target.page_id, fetch_target_page(target, force_refresh=force))
                )
    summary.account_authenticated = _account_authenticated(verify_metadata)
    summary.connection_status = verify_metadata.connection_status

    if verify_metadata.status != "success":
        summary.fetch_ready = False
        _record_page(summary, verify_target.page_id, verify_metadata)
        summary.action_required = _not_ready_action_required(
            verify_metadata, summary.account_authenticated
        )
        summary.recommended_next_action = (
            verify_metadata.recommended_next_action
            or "Resolve the vendor connection issue, then retry the fetch."
        )
        if summary.action_required == "pricing_locked":
            summary.warnings.append(
                f"{verify_target.page_id}: prices are hidden behind an account/membership; "
                "no import candidates were created."
            )
        elif summary.action_required == "connected_account_only":
            summary.warnings.append(
                "Vendor account is authenticated, but the configured store page did not expose "
                "real prices or order controls; no import candidates were created."
            )
        return summary

    # Fetch-ready: capture the verification page (already fetched) plus the rest.
    summary.fetch_ready = True
    summary.connection_status = verify_metadata.connection_status or "connected"
    _record_page(summary, verify_target.page_id, verify_metadata)
    for page_id, metadata in remaining_metadata:
        _record_page(summary, page_id, metadata)

    if summary.candidates_created_total == 0:
        summary.action_required = (
            "parser_zero_candidates" if summary.pages_successful else "unsupported_vendor_parser"
        )
        summary.fetch_ready = False
    summary.recommended_next_action = _summary_next_action(summary)
    return summary


# --- safe shipping-quote capture ---------------------------------

SHIPPING_QUOTES_DIR = DATA_DIR / "shipping_quotes"


def _cart_target_for(vendor_id: str) -> VendorPageTarget | None:
    """A configured cart/checkout-summary page for this vendor, if any."""
    return next(
        (
            target
            for target in load_vendor_pages()
            if target.vendor_id == vendor_id and target.page_id in {"cart", "checkout", "cart_summary"}
        ),
        None,
    )


def _latest_quote(vendor_id: str) -> ShippingQuote | None:
    root = SHIPPING_QUOTES_DIR / safe_path_part(vendor_id)
    if not root.exists():
        return None
    paths = sorted(root.rglob("shipping_quote.json"), reverse=True)
    for path in paths:
        try:
            return ShippingQuote(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return None


def _import_candidate_count(vendor_id: str) -> int:
    root = DATA_DIR / "import_candidates" / safe_path_part(vendor_id)
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("candidates.json"):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rows, list):
            total += len(rows)
    return total


def _latest_fetch_blocker(latest: FetchMetadata | None) -> str | None:
    if latest is None:
        return None
    if latest.action_required:
        return latest.action_required
    if latest.status == "failed":
        return "fetch_failed"
    if latest.status == "success" and not (latest.variant_count or 0):
        return "parser_zero_candidates"
    return None


def vendor_connection_metadata(vendor_id: str) -> VendorConnectionMetadata:
    config = connection_config_or_default(vendor_id)
    connection = connection_for_vendor(vendor_id)
    credential = get_vendor_credential_status(vendor_id)
    fetches = list_fetch_metadata(vendor_id)
    latest = fetches[0] if fetches else None
    quote = _latest_quote(vendor_id)
    pages_successful = sum(1 for item in fetches if item.status == "success")
    variant_count_total = sum(item.variant_count or 0 for item in fetches if item.status == "success")
    candidate_count = _import_candidate_count(vendor_id)
    empty_candidate_pages = [
        item.page_id
        for item in fetches
        if item.status == "success" and not (item.variant_count or 0)
    ]
    latest_blocker = _latest_fetch_blocker(latest)
    effective_action = connection.action_required or latest_blocker
    session_verified = connection.session_verified or connection.connection_status == "connected"
    store_fetch_ready = connection.store_fetch_ready and effective_action not in {
        "login_required",
        "manual_login_required",
        "captcha_required",
        "two_factor_required",
        "store_access_required",
        "location_or_order_cycle_required",
        "pricing_locked",
        "fetch_failed",
    }
    parser_ready = bool(connection.parser_ready or candidate_count > 0 or variant_count_total > 0) and effective_action not in {
        "parser_zero_candidates",
        "unsupported_vendor_parser",
    }
    fetch_ready = bool(session_verified and store_fetch_ready and parser_ready)
    if effective_action in {"login_required", "manual_login_required", "captcha_required", "reconnect_required", "two_factor_required"}:
        next_action = "Open login browser, complete the vendor login manually, then Verify session."
    elif effective_action in {"store_access_required", "location_or_order_cycle_required"}:
        next_action = "Complete the vendor store location/pickup/order-cycle step in the login browser, then Verify session."
    elif effective_action in {"parser_zero_candidates", "unsupported_vendor_parser"}:
        next_action = "Fetch can reach pages, but no import candidates were created; parser/import support needs attention."
    elif fetch_ready:
        next_action = "Fetch now."
    elif effective_action == "credential_update_required":
        next_action = "Update saved credentials, then verify the session."
    else:
        next_action = connection.notes or "Check connection."
    return VendorConnectionMetadata(
        vendor_id=vendor_id,
        display_name=config.vendor_name,
        auth_strategy=config.auth_strategy,
        credentials_configured=credential.credential_status == "configured",
        credential_status=credential.credential_status,
        connection_status=connection.connection_status,
        action_required=effective_action,
        last_connected=connection.last_connected_at,
        last_checked=connection.last_checked_at,
        last_fetch=connection.last_successful_fetch_at or (latest.fetched_at if latest else None),
        latest_fetch_status=latest.status if latest else None,
        latest_fetch_reason=sanitize_reason(
            (latest.classification_reason or latest.error_message or latest.notes) if latest else None
        ),
        session_verified=session_verified,
        store_fetch_ready=store_fetch_ready,
        parser_ready=parser_ready,
        import_candidates_created=candidate_count,
        fetch_ready=fetch_ready,
        pages_successful=pages_successful,
        variant_count_total=variant_count_total,
        candidates_created_total=candidate_count,
        empty_candidate_pages=empty_candidate_pages,
        quote_configured=_cart_target_for(vendor_id) is not None,
        latest_quote_status=quote.shipping_quote_status if quote else None,
        latest_quote_reason=(quote.recommended_next_action or quote.action_required) if quote else None,
        recommended_next_action=next_action,
        profile_label=f"farmfind_external_{safe_path_part(vendor_id)}" if config.auth_strategy == "external_browser_handoff" or config.captcha_sensitive else f"farmfind_profile_{safe_path_part(vendor_id)}",
        notes=connection.notes,
        error_message=connection.error_message,
    )


def _load_cart_html(vendor_id: str, cart_url: str) -> tuple[str, str] | None:
    """Live, READ-ONLY navigation to a vendor cart/checkout-summary page.

    Never clicks Place Order / Pay / Submit Order / Confirm Purchase and never
    touches saved address/payment settings. Not exercised by the offline test
    suite (Playwright); tests monkeypatch this function.
    """
    from playwright.sync_api import sync_playwright

    from .account_connections import connection_config_or_default
    from .storage import persistent_profile_path

    config = connection_config_or_default(vendor_id)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(persistent_profile_path(vendor_id)),
            headless=True,
            channel=config.browser_channel or None,
        )
        try:
            page = context.new_page()
            page.goto(cart_url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("load", timeout=5000)
            except Exception:
                pass
            return page.content(), page.url
        finally:
            context.close()


def _quote_artifact_dir(vendor_id: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    return SHIPPING_QUOTES_DIR / safe_path_part(vendor_id) / f"{stamp}_{uuid4().hex[:8]}"


def capture_shipping_quote(
    vendor_id: str,
    *,
    intended_item_signatures: list[str] | None = None,
) -> ShippingQuote:
    """Safely capture a shipping/cooler/handling quote for one vendor.

    Reads the vendor cart/checkout summary only; never places an order or submits
    payment. Returns a truthful, secret-free ShippingQuote (readiness gates like
    login/pricing/location are derived from the real cart-page state)."""
    cart_target = _cart_target_for(vendor_id)
    if cart_target is None:
        return ShippingQuote(
            vendor_id=vendor_id,
            shipping_quote_status="cart_unavailable",
            action_required="cart_unavailable",
            quote_captured_at=datetime.now(timezone.utc).isoformat(),
            recommended_next_action=(
                "No vendor cart/checkout page is configured in vendor_pages.json; add a cart, checkout, or cart_summary page before quotes can work."
            ),
        )
    try:
        loaded = _load_cart_html(vendor_id, cart_target.url)
    except Exception as exc:  # noqa: BLE001 - quote failures become metadata.
        return ShippingQuote(
            vendor_id=vendor_id,
            shipping_quote_status="failed",
            action_required="failed",
            quote_captured_at=datetime.now(timezone.utc).isoformat(),
            quote_warnings=[f"Cart navigation failed: {exc}"],
            recommended_next_action="Inspect the vendor connection and retry the shipping quote.",
        )
    if loaded is None:
        return ShippingQuote(
            vendor_id=vendor_id,
            shipping_quote_status="cart_unavailable",
            action_required="cart_unavailable",
            quote_captured_at=datetime.now(timezone.utc).isoformat(),
            recommended_next_action="The vendor cart could not be loaded; add the intended items and retry.",
        )
    html, final_url = loaded
    return build_shipping_quote_from_html(
        html,
        vendor_id,
        source_url=cart_target.url,
        final_url=final_url,
        intended_item_signatures=intended_item_signatures,
        artifact_dir=_quote_artifact_dir(vendor_id),
    )
