"""Deterministic browser page observation utilities."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .detection import CAPTCHA_MARKERS, detected_markers, two_factor_challenge_markers
from .models import (
    BrowserPageObservation,
    DetectedButton,
    DetectedDropdown,
    DetectedForm,
    DetectedInput,
    DetectedLink,
    PageKind,
)

FINAL_SUBMIT_TEXT = [
    "place order",
    "submit order",
    "pay now",
    "complete purchase",
    "confirm purchase",
    "buy now",
    "finalize order",
    "purchase",
]
LOGIN_MARKERS = ["sign in", "log in", "login", "password", "forgot password"]
PRODUCT_MARKERS = [
    "add to cart",
    "size",
    "container",
    "chilled?",
    "stock",
    "sku",
    "$",
    "variations_form",
    "attribute_pa_size",
    "attribute_pa_container",
    "attribute_chilled",
]


def _safe(call, default):
    try:
        return call()
    except Exception:
        return default


def page_text(page) -> str:
    return _safe(lambda: page.locator("body").inner_text(timeout=3000), "") or _safe(
        lambda: page.content(),
        "",
    )


def page_title(page) -> str | None:
    return _safe(lambda: page.title(), None)


def page_url(page) -> str | None:
    return _safe(lambda: page.url, None)


def detect_prices(text: str) -> list[str]:
    return sorted(set(re.findall(r"\$\s?\d+(?:\.\d{2})?(?:\s?[–-]\s?\$\s?\d+(?:\.\d{2})?)?", text)))


def detect_stock_texts(text: str) -> list[str]:
    patterns = [
        r"\b\d+\s+in stock\b\.?,?",
        r"\bin stock\b\.?,?",
        r"\bout of stock\b\.?,?",
        r"\bavailable\b\.?,?",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return sorted(set(item.strip() for item in found))


def normalize_attribute_name(name: str | None) -> str:
    cleaned = (name or "").replace("attribute_", "").replace("pa_", "")
    cleaned = cleaned.replace("-", " ").replace("_", " ").strip()
    if cleaned.lower() == "chilled":
        return "Chilled?"
    return cleaned.title()


def readable_dropdown_name(label: str | None, name: str | None) -> str | None:
    label = (label or "").strip()
    if label:
        return label
    normalized = normalize_attribute_name(name)
    return normalized or None


def infer_page_kind(text: str, url: str | None, dom: dict | None = None) -> PageKind:
    dom = dom or {}
    lower = f"{url or ''} {text}".lower()
    button_text = " ".join(item.get("text", "") for item in dom.get("buttons", []))
    dropdown_names = " ".join(
        f"{item.get('label', '')} {item.get('name', '')}"
        for item in dom.get("dropdowns", [])
    )
    forms = " ".join(item.get("selector_hint", "") for item in dom.get("forms", []))
    combined = f"{lower} {button_text} {dropdown_names} {forms}".lower()

    # Product signals must win over generic authenticated header/footer links such
    # as My Account, Cart, and Log Out.
    if any(
        marker in combined
        for marker in [
            "add to cart",
            "single_add_to_cart_button",
            "variations_form",
            "attribute_pa_size",
            "attribute_size",
            "attribute_pa_container",
            "attribute_container",
            "attribute_chilled",
            "woocommerce-product-add-to-cart",
        ]
    ) or ("/product/" in combined and re.search(r"\$\s?\d", combined)):
        return "product_detail"
    if any(marker in combined for marker in ["checkout", "billing details"]):
        return "checkout"
    if "/cart" in combined or "shopping cart" in combined or "cart totals" in combined:
        return "cart"
    if any(marker in combined for marker in ["my account", "orders", "dashboard", "logout"]):
        return "account"
    if any(marker in combined for marker in LOGIN_MARKERS):
        return "login"
    if "shop" in combined or "category" in combined:
        return "category_listing"
    return "unknown"


def _dom_snapshot(page) -> dict:
    return _safe(
        lambda: page.evaluate(
            """() => {
              const text = (el) => (el.innerText || el.textContent || '').trim();
              const selector = (el) => {
                if (el.id) return '#' + CSS.escape(el.id);
                if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name.replace(/"/g, '\\"') + '"]';
                if (el.className && typeof el.className === 'string') {
                  const firstClass = el.className.split(/\\s+/).filter(Boolean)[0];
                  if (firstClass) return el.tagName.toLowerCase() + '.' + firstClass;
                }
                return el.tagName.toLowerCase();
              };
              const labelFor = (el) => {
                if (el.id) {
                  const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                  if (label) return text(label);
                }
                const parentLabel = el.closest('label');
                if (parentLabel) return text(parentLabel);
                const rowLabel = el.closest('tr')?.querySelector('th.label label, .label label, label');
                if (rowLabel) return text(rowLabel);
                return el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
              };
              return {
                product_titles: Array.from(document.querySelectorAll('h1.product_title, .product_title, .elementor-widget-woocommerce-product-title h1')).map(text).filter(Boolean).slice(0, 5),
                headings: Array.from(document.querySelectorAll('h1,h2')).map(text).filter(Boolean).slice(0, 20),
                buttons: Array.from(document.querySelectorAll('button,input[type=submit],a.button,.single_add_to_cart_button')).map((el) => ({
                  text: text(el) || el.value || '',
                  selector_hint: selector(el)
                })).filter((b) => b.text).slice(0, 80),
                links: Array.from(document.querySelectorAll('a[href]')).map((el) => ({
                  text: text(el),
                  href: el.href,
                  selector_hint: selector(el)
                })).filter((l) => l.text).slice(0, 120),
                dropdowns: Array.from(document.querySelectorAll('select')).map((el) => ({
                  label: labelFor(el),
                  name: el.name || el.getAttribute('data-attribute_name') || '',
                  selector_hint: selector(el),
                  options: Array.from(el.options).map((opt) => opt.text.trim()).filter((value) => value && value.toLowerCase() !== 'choose an option')
                })),
                inputs: Array.from(document.querySelectorAll('input,textarea')).map((el) => ({
                  label: labelFor(el),
                  name: el.name || '',
                  type: el.type || el.tagName.toLowerCase(),
                  selector_hint: selector(el)
                })),
                forms: Array.from(document.querySelectorAll('form')).map((el) => ({
                  selector_hint: selector(el),
                  inputs: Array.from(el.querySelectorAll('input,textarea')).map((input) => ({
                    label: labelFor(input),
                    name: input.name || '',
                    type: input.type || input.tagName.toLowerCase(),
                    selector_hint: selector(input)
                  }))
                }))
              };
            }"""
        ),
        {},
    )


def _dom_text_blob(dom: dict) -> str:
    pieces: list[str] = []
    for key in ["product_titles", "headings"]:
        pieces.extend(str(item) for item in dom.get(key, []))
    for key in ["buttons", "links", "dropdowns", "inputs", "forms"]:
        for item in dom.get(key, []):
            if isinstance(item, dict):
                pieces.extend(str(value) for value in item.values() if isinstance(value, str))
                options = item.get("options")
                if isinstance(options, list):
                    pieces.extend(str(option) for option in options)
    return " ".join(pieces)


def _product_title(dom: dict, title: str | None) -> str | None:
    for item in dom.get("product_titles", []):
        if str(item).strip():
            return str(item).strip()
    for item in dom.get("headings", []):
        text = str(item).strip()
        if text and text.lower() not in {"example farm a", "shop", "description", "additional information", "related products"}:
            return text
    if title and " – " in title:
        return title.split(" – ", 1)[0].strip()
    return title


def _normalize_dropdowns(dom: dict) -> list[DetectedDropdown]:
    dropdowns: list[DetectedDropdown] = []
    for item in dom.get("dropdowns", []):
        label = readable_dropdown_name(item.get("label"), item.get("name"))
        options = [
            option
            for option in item.get("options", [])
            if str(option).strip() and str(option).strip().lower() != "choose an option"
        ]
        dropdowns.append(
            DetectedDropdown(
                label=label,
                name=item.get("name"),
                selector_hint=item.get("selector_hint"),
                options=options,
            )
        )
    return dropdowns


def observe_vendor_page(
    page,
    vendor_id: str,
    page_id: str,
    artifact_dir: str | Path | None = None,
) -> BrowserPageObservation:
    text = page_text(page)
    url = page_url(page)
    title = page_title(page)
    dom = _dom_snapshot(page)
    dom_blob = _dom_text_blob(dom)
    product_title = _product_title(dom, title)
    buttons = [
        DetectedButton(
            text=item.get("text", ""),
            selector_hint=item.get("selector_hint"),
            is_dangerous_final_submit=any(
                blocked in item.get("text", "").lower() for blocked in FINAL_SUBMIT_TEXT
            ),
        )
        for item in dom.get("buttons", [])
    ]
    observation = BrowserPageObservation(
        vendor_id=vendor_id,
        page_id=page_id,
        source_url=url,
        final_url=url,
        page_title=title,
        page_kind=infer_page_kind(text, url, dom),
        visible_text_excerpt=text.strip()[:1500],
        detected_product_title=product_title,
        detected_prices=detect_prices(text + " " + dom_blob),
        detected_stock_texts=detect_stock_texts(text + " " + dom_blob),
        detected_buttons=buttons,
        detected_links=[DetectedLink(**item) for item in dom.get("links", [])],
        detected_dropdowns=_normalize_dropdowns(dom),
        detected_forms=[DetectedForm(**item) for item in dom.get("forms", [])],
        detected_inputs=[DetectedInput(**item) for item in dom.get("inputs", [])],
        detected_product_markers=detected_markers(text + " " + dom_blob, PRODUCT_MARKERS),
        detected_login_markers=detected_markers(text, LOGIN_MARKERS),
        detected_captcha_markers=detected_markers(text, CAPTCHA_MARKERS),
        detected_two_factor_markers=two_factor_challenge_markers(text),
    )
    if artifact_dir is not None:
        artifact_path = Path(artifact_dir)
        artifact_path.mkdir(parents=True, exist_ok=True)
        observation = observation.model_copy(
            update={
                "html_path": str(artifact_path / "page.html"),
                "text_path": str(artifact_path / "page.txt"),
                "screenshot_viewport_path": str(artifact_path / "screenshot_viewport.png"),
                "screenshot_full_path": str(artifact_path / "screenshot_full.png"),
            }
        )
        (artifact_path / "observation.json").write_text(
            json.dumps(observation.model_dump(), indent=2),
            encoding="utf-8",
        )
    return observation
