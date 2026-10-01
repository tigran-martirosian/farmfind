"""Read-only GrazeCart interaction diagnostics.

This harness is for local, logged-in debugging. It captures what the browser can
see before and after opening option panels, but it refuses to click cart/order
mutation controls.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

from .external_browser_session import vendor_debug_port
from .package_parser import parse_savings
from .storage import DATA_DIR, load_vendor_pages, safe_path_part
from .vendor_fetcher import _port_is_listening

DIAGNOSTICS_DIR = DATA_DIR / "diagnostics" / "grazecart"

SAFE_OPTION_TEXT = ("select option", "select options", "choose option", "choose options")
UNSAFE_CART_TEXT = (
    "add to order",
    "add to cart",
    "add order",
    "cart",
    "checkout",
    "place order",
    "submit order",
    "pay now",
    "complete purchase",
    "confirm purchase",
)
SECRET_ATTRS = (
    "wire:snapshot",
    "wire:effects",
    "csrf",
    "token",
    "authorization",
    "password",
    "cookie",
    "session",
)


def _strip_tags(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<script\b.*?</script>", " ", value)
    text = re.sub(r"(?is)<style\b.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _compact_html(value: str, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def redact_secrets(value: str) -> str:
    """Remove obvious auth/session-bearing attributes before writing snippets."""
    redacted = value
    for attr in SECRET_ATTRS:
        redacted = re.sub(
            rf"(?is)\s{re.escape(attr)}\s*=\s*(['\"]).*?\1",
            f' {attr}="[redacted]"',
            redacted,
        )
    redacted = re.sub(
        r"(?is)\s(?:data-[\w:-]*token|data-[\w:-]*secret|data-[\w:-]*session)\s*=\s*(['\"]).*?\1",
        ' data-secret="[redacted]"',
        redacted,
    )
    return redacted


def button_texts_from_html(html: str) -> list[str]:
    matches = re.findall(
        r"(?is)<(?:button|a)\b[^>]*>(.*?)</(?:button|a)>",
        html,
    )
    texts = [_strip_tags(match) for match in matches]
    return [text for text in texts if text]


def is_safe_option_text(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in SAFE_OPTION_TEXT) and not any(
        blocked in lower for blocked in UNSAFE_CART_TEXT
    )


def is_unsafe_cart_text(text: str) -> bool:
    lower = text.lower()
    return any(blocked in lower for blocked in UNSAFE_CART_TEXT)


def detected_actions(button_texts: list[str], detail_links: list[str]) -> list[str]:
    actions: set[str] = set()
    for text in button_texts:
        lower = text.lower()
        if is_safe_option_text(text):
            actions.add("select_option" if "select" in lower else "choose_options")
        elif any(blocked in lower for blocked in ("add to order", "add to cart")):
            actions.add("direct_add_to_order")
        else:
            actions.add("unknown")
    if detail_links:
        actions.add("detail_link")
    return sorted(actions) or ["unknown"]


def _detail_links_from_html(block: str) -> list[str]:
    links: list[str] = []
    for href, text in re.findall(r"(?is)<a\b[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", block):
        label = _strip_tags(text).lower()
        if href and not href.startswith("#") and not any(blocked in label for blocked in UNSAFE_CART_TEXT):
            links.append(href)
    return list(dict.fromkeys(links))


def _title_from_block(block: str) -> str:
    for pattern in [
        r"(?is)<h[1-6]\b[^>]*>(.*?)</h[1-6]>",
        r"(?is)<[^>]*class=['\"][^'\"]*(?:title|name|productListing__title)[^'\"]*['\"][^>]*>(.*?)</[^>]+>",
        r"(?is)<a\b[^>]*>(.*?)</a>",
    ]:
        match = re.search(pattern, block)
        if match:
            text = _strip_tags(match.group(1))
            if text:
                return text
    return ""


def _price_text(text: str) -> str | None:
    match = re.search(r"\$\s?\d+(?:\.\d{2})?", text)
    return match.group(0).replace(" ", "") if match else None


def _candidate_blocks(html: str) -> list[str]:
    patterns = [
        r"(?is)<(?:article|li|section|div)\b[^>]*class=['\"][^'\"]*(?:product|card|listing|item)[^'\"]*['\"][^>]*>.*?</(?:article|li|section|div)>",
        r"(?is)<form\b[^>]*>.*?</form>",
    ]
    blocks: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, html):
            text = _strip_tags(match).lower()
            if "$" in text or any(marker in text for marker in SAFE_OPTION_TEXT + ("add to order", "add to cart")):
                blocks.append(match)
    deduped: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        key = _strip_tags(block)[:500]
        if key and key not in seen:
            seen.add(key)
            deduped.append(block)
    return deduped


def extract_cards_from_html(html: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for index, block in enumerate(_candidate_blocks(html)):
        text = _strip_tags(block)
        buttons = button_texts_from_html(block)
        links = _detail_links_from_html(block)
        cards.append(
            {
                "card_index": index,
                "title": _title_from_block(block),
                "subtitle": "",
                "full_visible_text": text,
                "price_text": _price_text(text),
                "button_texts": buttons,
                "detail_links": links,
                "detected_actions": detected_actions(buttons, links),
                "safe_html_snippet": _compact_html(redact_secrets(block)),
                "bounding_box": None,
            }
        )
    return cards


def extract_option_rows_from_html(html: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    patterns = [
        r"(?is)<tr\b[^>]*>.*?</tr>",
        r"(?is)<li\b[^>]*class=['\"][^'\"]*(?:option|modifier|variant|choice)[^'\"]*['\"][^>]*>.*?</li>",
        r"(?is)<div\b[^>]*class=['\"][^'\"]*(?:option|modifier|variant|choice|row)[^'\"]*['\"][^>]*>.*?</div>",
    ]
    matches: list[tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, html):
            matches.append((match.start(), match.group(0)))
    seen: set[str] = set()
    for _, block in sorted(matches, key=lambda item: item[0]):
        text = _strip_tags(block)
        lower = text.lower()
        if not text or text in seen:
            continue
        if "$" not in text and not any(marker in lower for marker in ("save", "sold out", "selected", "default")):
            continue
        seen.add(text)
        savings = parse_savings(text).raw_text
        rows.append(
            {
                "row_index": len(rows),
                "label": re.sub(r"\$\s?\d+(?:\.\d{2})?.*", "", text).strip(" -"),
                "price": _price_text(text),
                "savings": savings,
                "stock_orderability": "sold_out" if "sold out" in lower else "orderable",
                "selected_default": any(marker in lower for marker in ("selected", "default", "checked")),
                "full_row_text": text,
                "safe_html_snippet": _compact_html(redact_secrets(block), limit=1500),
            }
        )
    return rows


@dataclass
class DiagnosticTarget:
    vendor_id: str
    page_id: str
    url: str


def resolve_target(vendor_id: str, page_id: str | None, url: str | None) -> DiagnosticTarget:
    if url:
        return DiagnosticTarget(vendor_id=vendor_id, page_id=page_id or "direct_url", url=url)
    if not page_id:
        raise ValueError("--page-id is required when --url is not provided.")
    target = next(
        (
            item
            for item in load_vendor_pages()
            if item.vendor_id == vendor_id and item.page_id == page_id
        ),
        None,
    )
    if target is None:
        raise ValueError(f"No configured vendor page found for {vendor_id}/{page_id}.")
    return DiagnosticTarget(vendor_id=vendor_id, page_id=page_id, url=target.url)


def output_dir_for(vendor_id: str, stamp: str | None = None) -> Path:
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    return DIAGNOSTICS_DIR / safe_path_part(vendor_id) / stamp


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_screenshot(page, path: Path, full_page: bool = True) -> str | None:
    try:
        page.screenshot(path=str(path), full_page=full_page)
        return str(path)
    except Exception:
        return None


def _card_locator(page):
    selectors = [
        "article",
        "li[class*='product']",
        "div[class*='productListing']",
        "div[class*='product-card']",
        "div[class*='product']",
        "form",
    ]
    return page.locator(", ".join(selectors)).filter(has_text=re.compile(r"\$|select option|choose option|add to order|add to cart", re.I))


def _safe_option_button(card_locator):
    return card_locator.locator("button, a, [role=button]").filter(
        has_text=re.compile(r"^(?:\s*(?:select|choose)\s+options?\s*)$", re.I)
    )


def _first_locator(locator):
    first = getattr(locator, "first", None)
    return first() if callable(first) else first


def _extract_live_cards(page) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    loc = _card_locator(page)
    count = min(loc.count(), 100)
    for index in range(count):
        card = loc.nth(index)
        try:
            html = card.evaluate("node => node.outerHTML")
            text = card.inner_text(timeout=1500)
        except Exception:
            continue
        parsed = extract_cards_from_html(html)
        record = parsed[0] if parsed else {
            "card_index": index,
            "title": "",
            "subtitle": "",
            "full_visible_text": re.sub(r"\s+", " ", text).strip(),
            "price_text": _price_text(text),
            "button_texts": [],
            "detail_links": [],
            "detected_actions": ["unknown"],
            "safe_html_snippet": _compact_html(redact_secrets(html)),
            "bounding_box": None,
        }
        record["card_index"] = index
        try:
            record["bounding_box"] = card.bounding_box()
        except Exception:
            record["bounding_box"] = None
        cards.append(record)
    return cards


def _capture_option_panels(page, cards: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    panels: list[dict[str, Any]] = []
    loc = _card_locator(page)
    count = min(loc.count(), len(cards), 100)
    for index in range(count):
        card_record = cards[index]
        if not any(action in card_record.get("detected_actions", []) for action in ("select_option", "choose_options")):
            continue
        panel = {
            "card_index": index,
            "title": card_record.get("title"),
            "option_panel_detected_before_click": False,
            "hidden_dom_rows_before_click": [],
            "click_attempted": False,
            "click_selector_used": "button/a text exactly Select Option(s) or Choose Option(s)",
            "panel_opened": False,
            "option_rows_count": 0,
            "row_texts": [],
            "rows": [],
            "add_to_order_buttons_present_and_avoided": False,
            "error": None,
        }
        try:
            card = loc.nth(index)
            try:
                card_html = card.evaluate("node => node.outerHTML")
            except Exception:
                card_html = ""
            hidden_rows = extract_option_rows_from_html(card_html)
            panel["option_panel_detected_before_click"] = bool(hidden_rows)
            panel["hidden_dom_rows_before_click"] = hidden_rows
            if hidden_rows:
                panel["rows"] = hidden_rows
                panel["option_rows_count"] = len(hidden_rows)
                panel["row_texts"] = [row["full_row_text"] for row in hidden_rows]
            button = _first_locator(_safe_option_button(card))
            if button is None:
                panel["error"] = "No safe Select/Choose Options button locator was returned for this card."
                panels.append(panel)
                continue
            if button.count() == 0:
                panel["error"] = "No safe Select/Choose Options button was found inside this card locator."
                panels.append(panel)
                continue
            before_text = page.locator("body").inner_text(timeout=2000)
            panel["add_to_order_buttons_present_and_avoided"] = bool(
                re.search(r"add\s+to\s+(?:order|cart)", before_text, flags=re.I)
            )
            panel["click_attempted"] = True
            button.click(timeout=3000)
            page.wait_for_timeout(900)
            after_html = page.content()
            try:
                after_card_html = card.evaluate("node => node.outerHTML")
            except Exception:
                after_card_html = ""
            rows = hidden_rows or extract_option_rows_from_html(after_card_html)
            if not rows:
                rows = extract_option_rows_from_html(after_html)
            after_text = page.locator("body").inner_text(timeout=2000)
            panel["panel_opened"] = before_text != after_text or bool(rows)
            if rows:
                panel["rows"] = rows
                panel["option_rows_count"] = len(rows)
                panel["row_texts"] = [row["full_row_text"] for row in rows]
            (output_dir / f"card_{index}_after.html").write_text(
                redact_secrets(after_html),
                encoding="utf-8",
            )
            _safe_screenshot(page, output_dir / f"card_{index}_after.png")
        except Exception as exc:  # noqa: BLE001 - diagnostics should continue.
            panel["error"] = str(exc)
        panels.append(panel)
    return panels


def _inspect_detail_pages(page, cards: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    original_url = page.url
    for card in cards:
        links = card.get("detail_links") or []
        if not links:
            continue
        href = links[0]
        detail = {"card_index": card["card_index"], "url": href, "option_rows_count": 0, "rows": [], "error": None}
        try:
            page.goto(href, wait_until="domcontentloaded")
            page.wait_for_timeout(700)
            html = page.content()
            rows = extract_option_rows_from_html(html)
            detail["option_rows_count"] = len(rows)
            detail["rows"] = rows
            (output_dir / f"detail_{card['card_index']}.html").write_text(redact_secrets(html), encoding="utf-8")
            _safe_screenshot(page, output_dir / f"detail_{card['card_index']}.png")
        except Exception as exc:  # noqa: BLE001
            detail["error"] = str(exc)
        details.append(detail)
        try:
            page.goto(original_url, wait_until="domcontentloaded")
        except Exception:
            pass
    return details


def run_diagnostics(
    *,
    vendor_id: str,
    page_id: str | None = None,
    url: str | None = None,
    inspect_detail: bool = False,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    target = resolve_target(vendor_id, page_id, url)
    output_dir = output_dir or output_dir_for(vendor_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, str]] = []

    from playwright.sync_api import sync_playwright

    port = vendor_debug_port(vendor_id)
    if not _port_is_listening(port):
        raise RuntimeError(
            f"Chrome debug port {port} is not listening for {vendor_id}. "
            "Open/verify the vendor login browser first, then rerun diagnostics."
        )

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
        except TypeError:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(target.url, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            before_html = page.content()
            (output_dir / "page_before.html").write_text(redact_secrets(before_html), encoding="utf-8")
            _safe_screenshot(page, output_dir / "screenshot_before.png")
            cards = _extract_live_cards(page)
            panels = _capture_option_panels(page, cards, output_dir)
            after_html = page.content()
            (output_dir / "page_after.html").write_text(redact_secrets(after_html), encoding="utf-8")
            _safe_screenshot(page, output_dir / "screenshot_after.png")
            detail_pages = _inspect_detail_pages(page, cards, output_dir) if inspect_detail else []
        except Exception as exc:  # noqa: BLE001
            errors.append({"error": str(exc)})
            cards = []
            panels = []
            detail_pages = []
        finally:
            try:
                page.close()
            except Exception:
                pass
            browser.close()

    safe_snippets = [
        {"card_index": card["card_index"], "safe_html_snippet": card.get("safe_html_snippet", "")}
        for card in cards
    ]
    report = {
        "vendor_id": target.vendor_id,
        "page_id": target.page_id,
        "url": target.url,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "cards_found": len(cards),
        "select_option_cards": sum(
            1 for card in cards if any(action in card.get("detected_actions", []) for action in ("select_option", "choose_options"))
        ),
        "option_panels_opened": sum(1 for panel in panels if panel.get("panel_opened")),
        "option_rows_extracted": sum(panel.get("option_rows_count", 0) for panel in panels),
        "failures": sum(1 for panel in panels if panel.get("error")) + len(errors),
        "hard_safety_rules": {
            "clicked_only": list(SAFE_OPTION_TEXT),
            "never_clicked": list(UNSAFE_CART_TEXT),
            "checkout_or_payment": "not implemented",
        },
        "detail_pages_inspected": len(detail_pages),
    }
    _write_json(output_dir / "cards.json", cards)
    _write_json(output_dir / "option_panels.json", panels)
    _write_json(output_dir / "errors.json", errors)
    _write_json(output_dir / "safe_snippets.json", safe_snippets)
    _write_json(output_dir / "report.json", {**report, "option_panels": panels, "detail_pages": detail_pages})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only GrazeCart interaction diagnostics.")
    parser.add_argument("--vendor-id", required=True)
    parser.add_argument("--page-id")
    parser.add_argument("--url")
    parser.add_argument("--inspect-detail", action="store_true")
    args = parser.parse_args()
    report = run_diagnostics(
        vendor_id=args.vendor_id,
        page_id=args.page_id,
        url=args.url,
        inspect_detail=args.inspect_detail,
    )
    print("Diagnostic complete:")
    print(f"- report: {Path(report['output_dir']) / 'report.json'}")
    print(f"- cards found: {report['cards_found']}")
    print(f"- select-option cards: {report['select_option_cards']}")
    print(f"- option panels opened: {report['option_panels_opened']}")
    print(f"- option rows extracted: {report['option_rows_extracted']}")
    print(f"- failures: {report['failures']}")
    print("\nFiles written:")
    print("- report.json")
    print("- option_panels.json")
    print("- safe_snippets.json")
    print("- screenshot_after.png if the report shows missed/empty rows")


if __name__ == "__main__":
    main()
