"""Human-assisted capture helpers for member-gated or CAPTCHA-protected pages."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import CaptureMethod, FetchArtifacts, FetchMetadata, VendorPageTarget
from .storage import content_hash, write_fetch_artifacts


def metadata_for_manual_capture(
    *,
    vendor_id: str,
    page_id: str,
    source_url: str,
    html: str,
    capture_method: CaptureMethod,
    screenshot_bytes: bytes | None = None,
    title: str | None = None,
    final_url: str | None = None,
    notes: str | None = None,
) -> FetchArtifacts:
    now = datetime.now(timezone.utc).isoformat()
    metadata = FetchMetadata(
        vendor_id=vendor_id,
        page_id=page_id,
        url=source_url,
        source_url=source_url,
        fetched_at=now,
        imported_at=now,
        requires_login=False,
        status="success",
        capture_method=capture_method,
        title=title,
        final_url=final_url or source_url,
        content_hash=content_hash(html),
        notes=notes,
    )
    return FetchArtifacts(
        metadata=metadata,
        html=html,
        screenshot_bytes=screenshot_bytes,
    )


def import_manual_capture(
    *,
    vendor_id: str,
    page_id: str,
    source_url: str,
    html_path: str,
    screenshot_path: str | None = None,
    notes: str | None = None,
) -> FetchMetadata:
    html_file = Path(html_path)
    if not html_file.exists():
        raise FileNotFoundError(f"HTML file not found: {html_path}")
    html = html_file.read_text(encoding="utf-8")
    screenshot_bytes = None
    if screenshot_path:
        screenshot_file = Path(screenshot_path)
        if not screenshot_file.exists():
            raise FileNotFoundError(f"Screenshot file not found: {screenshot_path}")
        screenshot_bytes = screenshot_file.read_bytes()
    target = VendorPageTarget(vendor_id=vendor_id, page_id=page_id, url=source_url)
    artifacts = metadata_for_manual_capture(
        vendor_id=vendor_id,
        page_id=page_id,
        source_url=source_url,
        html=html,
        capture_method="manual_import",
        screenshot_bytes=screenshot_bytes,
        notes=notes,
    )
    return write_fetch_artifacts(target, artifacts)


def capture_current_browser_page(
    *,
    vendor_id: str,
    page_id: str,
    url: str,
    notes: str | None = None,
) -> FetchMetadata:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url)
        input("Complete login/CAPTCHA/navigation manually, then press Enter to capture...")
        html = page.content()
        screenshot_bytes = page.screenshot(full_page=True)
        target = VendorPageTarget(vendor_id=vendor_id, page_id=page_id, url=url)
        artifacts = metadata_for_manual_capture(
            vendor_id=vendor_id,
            page_id=page_id,
            source_url=url,
            html=html,
            capture_method="manual_browser",
            screenshot_bytes=screenshot_bytes,
            title=page.title(),
            final_url=page.url,
            notes=notes,
        )
        metadata = write_fetch_artifacts(target, artifacts)
        browser.close()
        return metadata
