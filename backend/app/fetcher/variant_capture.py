"""Product page variant capture for reviewable import staging."""
from __future__ import annotations

import html
import itertools
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    CapturedProductPage,
    CapturedProductVariant,
    CapturedVariantOption,
)
from .observation import detect_prices, detect_stock_texts, observe_vendor_page, readable_dropdown_name
from .offer_validation import extract_orderable_offers, recover_incomplete_offer, report_fetch_coverage, validate_offer
from .package_parser import parse_quantity


def parse_price(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _strip_tags(value: str | None) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def stock_from_text(text: str | None, is_in_stock: bool | None = None) -> tuple[str, int | None]:
    cleaned = _strip_tags(text) or text or ""
    if is_in_stock is False:
        return "out_of_stock", None
    if is_in_stock is True:
        status = "in_stock"
    else:
        lower = cleaned.lower()
        status = "out_of_stock" if "out of stock" in lower else "in_stock" if "in stock" in lower else "unknown"
    match = re.search(r"(\d+)\s+in stock", cleaned, flags=re.IGNORECASE)
    return status, int(match.group(1)) if match else None


def normalize_attribute_name(name: str) -> str:
    cleaned = name.replace("attribute_", "").replace("pa_", "").replace("-", " ").replace("_", " ").strip()
    if cleaned.lower() == "chilled":
        return "Chilled?"
    return cleaned.title()


# Map a known vendor page id straight to a product family. Title-based inference
# is used as a fallback when the page id is not one of these.
_PAGE_FAMILY = {
    "cow_milk": "milk",
    "milk": "milk",
    "cream": "cream",
    "cow_butter": "butter",
    "farm_eggs": "eggs",
    "cheese": "cheese",
}

_FAMILY_PRODUCT_TYPE = {
    "milk": "cow_milk",
    "cream": "cream",
    "butter": "butter",
    "eggs": "eggs",
    "cheese": "cheese",
}


def _product_family(page_id: str, title: str, description: str) -> str | None:
    if page_id in _PAGE_FAMILY:
        return _PAGE_FAMILY[page_id]
    strong = title
    if "egg" in strong:
        return "eggs"
    if "cream" in strong:
        return "cream"
    if "butter" in strong and "buttermilk" not in strong:
        return "butter"
    if "cheese" in strong:
        return "cheese"
    if "milk" in strong:
        return "milk"
    text = f"{title} {description}"
    if "egg" in text:
        return "eggs"
    if "cream" in text:
        return "cream"
    if "butter" in text and "buttermilk" not in text:
        return "butter"
    if "cheese" in text:
        return "cheese"
    if "milk" in text:
        return "milk"
    return None


def _milk_size(search: str) -> tuple[float, str] | None:
    if "quart" in search:
        return (0.25, "gallon")
    if "half gallon" in search:
        return (0.5, "gallon")
    if "gallon" in search:
        return (1.0, "gallon")
    if "pint" in search:
        return (0.125, "gallon")
    return None


def _cream_size(search: str) -> tuple[float, str] | None:
    # Cream is measured in pints by the optimizer.
    if "half gallon" in search:
        return (4.0, "pint")
    if "gallon" in search:
        return (8.0, "pint")
    if "quart" in search:
        return (2.0, "pint")
    if "pint" in search:
        return (1.0, "pint")
    return None


def _butter_size(search: str) -> tuple[float, str] | None:
    if "half pound" in search or "1/2 pound" in search or "half lb" in search:
        return (0.5, "lb")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:lbs?|pounds?|#)\b", search)
    if match:
        return (float(match.group(1)), "lb")
    oz = re.search(r"(\d+(?:\.\d+)?)\s*oz\b", search)
    if oz:
        return (float(oz.group(1)) / 16.0, "lb")
    if "pound" in search or re.search(r"\blb\b", search):
        return (1.0, "lb")
    return None


def _egg_size(search: str) -> tuple[float, str] | None:
    # Eggs are measured in individual count. Dozen parsing is generic: "N dozen"
    # and "case of N dozen" both resolve to N * 12.
    match = re.search(r"(\d+(?:\.\d+)?)\s*dozen", search)
    if match:
        return (float(match.group(1)) * 12.0, "count")
    if "half dozen" in search:
        return (6.0, "count")
    if "dozen" in search:
        return (12.0, "count")
    count = re.search(r"(\d+)\s*(?:count|ct|eggs)\b", search)
    if count:
        return (float(count.group(1)), "count")
    return None


def _cheese_size(search: str) -> tuple[float, str] | None:
    # Only infer a weight-based size for cheese. Never invent a lb conversion
    # from pint/quart volume units.
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:lbs?|pounds?)\b", search)
    if match:
        return (float(match.group(1)), "lb")
    oz = re.search(r"(\d+(?:\.\d+)?)\s*oz\b", search)
    if oz:
        return (float(oz.group(1)) / 16.0, "lb")
    return None


_FAMILY_SIZE_PARSER = {
    "milk": _milk_size,
    "cream": _cream_size,
    "butter": _butter_size,
    "eggs": _egg_size,
    "cheese": _cheese_size,
    "cheese": _cheese_size,
}


def infer_product_fields(
    vendor_id: str,
    page_id: str,
    product_title: str | None,
    attributes: list[CapturedVariantOption],
    description_text: str,
) -> dict:
    title = (product_title or "").lower()
    description = description_text.lower()
    values = {item.name.lower().rstrip("?"): item.value.lower() for item in attributes}
    combined_values = " ".join(values.values())
    search = f"{title} {combined_values}"
    inferred: dict = {}

    family = _product_family(page_id, title, description)
    if family is None:
        return inferred

    product_type = _FAMILY_PRODUCT_TYPE.get(family)
    if product_type is not None:
        inferred["inferred_product_type"] = product_type

    quantity = parse_quantity(search, product_type)
    if (
        quantity.total_equivalent_quantity is not None
        and quantity.total_equivalent_unit is not None
        and not (product_type == "cheese" and quantity.total_equivalent_unit == "pint")
    ):
        inferred["inferred_package_size"] = quantity.total_equivalent_quantity
        inferred["inferred_unit"] = quantity.total_equivalent_unit
    else:
        size = _FAMILY_SIZE_PARSER.get(family, lambda _search: None)(search)
        if size is not None:
            inferred["inferred_package_size"], inferred["inferred_unit"] = size

    if "glass" in search:
        inferred["inferred_packaging"] = "glass"
    elif "plastic" in search:
        inferred["inferred_packaging"] = "plastic"

    chilled = values.get("chilled")
    if chilled == "yes":
        inferred["inferred_storage_state"] = "refrigerated"
    elif chilled == "no":
        inferred["inferred_storage_state"] = "fresh"

    return inferred


def _embedded_variations(page) -> list[dict]:
    raw_data = ""
    try:
        raw_data = page.locator("form.variations_form").first.get_attribute("data-product_variations", timeout=1000) or ""
    except Exception:
        raw_data = ""
    if not raw_data:
        try:
            page_html = page.content()
        except Exception:
            page_html = ""
        match = re.search(r"data-product_variations\s*=\s*(['\"])(.*?)\1", page_html, flags=re.DOTALL | re.IGNORECASE)
        raw_data = match.group(2) if match else ""
    if not raw_data:
        return []
    try:
        decoded = html.unescape(raw_data)
        return json.loads(decoded)
    except json.JSONDecodeError:
        # Some stores leave escaped slashes/entities in odd forms. Try one more
        # normalized pass before giving up to dropdown enumeration.
        try:
            return json.loads(html.unescape(raw_data).replace("\\/", "/"))
        except json.JSONDecodeError:
            return []


def _option_dict(option) -> dict:
    if isinstance(option, dict):
        text = str(option.get("text") or option.get("label") or option.get("value") or "").strip()
        value = str(option.get("value") or text).strip()
        return {"text": text, "value": value}
    text = str(option).strip()
    return {"text": text, "value": text}


def _dropdowns_from_html(page_html: str) -> list[dict]:
    dropdowns: list[dict] = []
    label_by_for = {
        match.group("for"): _strip_tags(match.group("body")) or ""
        for match in re.finditer(
            r"<label\b[^>]*for=[\"'](?P<for>[^\"']+)[\"'][^>]*>(?P<body>.*?)</label>",
            page_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    }
    for match in re.finditer(r"<select\b(?P<attrs>[^>]*)>(?P<body>.*?)</select>", page_html, flags=re.IGNORECASE | re.DOTALL):
        attrs = match.group("attrs")
        body = match.group("body")
        name_match = re.search(r"\bname=[\"']([^\"']+)[\"']", attrs, flags=re.IGNORECASE)
        id_match = re.search(r"\bid=[\"']([^\"']+)[\"']", attrs, flags=re.IGNORECASE)
        name = name_match.group(1) if name_match else ""
        select_id = id_match.group(1) if id_match else ""
        options = []
        for option_match in re.finditer(r"<option\b(?P<attrs>[^>]*)>(?P<text>.*?)</option>", body, flags=re.IGNORECASE | re.DOTALL):
            option_attrs = option_match.group("attrs")
            value_match = re.search(r"\bvalue=[\"']([^\"']*)[\"']", option_attrs, flags=re.IGNORECASE)
            value = html.unescape(value_match.group(1)) if value_match else ""
            text = _strip_tags(option_match.group("text")) or ""
            if value and text and text.lower() != "choose an option":
                options.append({"text": text, "value": value})
        selector_hint = f"#{select_id}" if select_id else f'select[name="{name}"]'
        dropdowns.append(
            {
                "label": label_by_for.get(select_id) or readable_dropdown_name("", name) or name,
                "name": name,
                "selector_hint": selector_hint,
                "options": options,
            }
        )
    return dropdowns


def _dropdowns(page) -> list[dict]:
    try:
        result = page.evaluate(
            """() => Array.from(document.querySelectorAll('select')).map((el) => {
              const label = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
              const rowLabel = el.closest('tr')?.querySelector('th.label label, .label label, label');
              return {
                label: ((label || rowLabel)?.innerText || el.name || '').trim(),
                name: el.name || el.getAttribute('data-attribute_name') || '',
                selector_hint: el.id ? '#' + CSS.escape(el.id) : 'select[name="' + (el.name || '').replace(/"/g, '\\"') + '"]',
                options: Array.from(el.options).map((opt) => ({
                  text: opt.text.trim(),
                  value: opt.value
                })).filter((opt) => opt.value && opt.text && opt.text.toLowerCase() !== 'choose an option')
              };
            })"""
        )
        dropdowns = result.get("dropdowns", []) if isinstance(result, dict) else result
    except Exception:
        dropdowns = []
    if not dropdowns:
        try:
            dropdowns = _dropdowns_from_html(page.content())
        except Exception:
            dropdowns = []
    normalized: list[dict] = []
    for dropdown in dropdowns or []:
        options = [_option_dict(option) for option in dropdown.get("options", [])]
        options = [option for option in options if option["text"] and option["value"] and option["text"].lower() != "choose an option"]
        normalized.append(
            {
                "label": readable_dropdown_name(dropdown.get("label"), dropdown.get("name")) or dropdown.get("name", ""),
                "name": dropdown.get("name", ""),
                "selector_hint": dropdown.get("selector_hint"),
                "options": options,
            }
        )
    return normalized


def _product_title(page, observation) -> str:
    return observation.detected_product_title or "Unknown Product"


def _option_text(dropdowns: list[dict], attribute_name: str, raw_value: str) -> str:
    raw_value_lower = raw_value.lower()
    for dropdown in dropdowns:
        if dropdown.get("name") != attribute_name:
            continue
        for option in dropdown.get("options", []):
            if str(option.get("value", "")).lower() == raw_value_lower:
                return str(option.get("text") or raw_value).strip()
    return raw_value.replace("-", " ").title()


def _variant_attribute_combinations(raw_data: dict, dropdowns: list[dict]) -> tuple[list[list[CapturedVariantOption]], bool]:
    raw_attrs = raw_data.get("attributes") or {}
    groups: list[list[CapturedVariantOption]] = []
    expanded_wildcard = False
    for key, value in raw_attrs.items():
        label = next(
            (
                dropdown.get("label") or normalize_attribute_name(key)
                for dropdown in dropdowns
                if dropdown.get("name") == key
            ),
            normalize_attribute_name(key),
        )
        if value:
            groups.append([CapturedVariantOption(name=label, value=_option_text(dropdowns, key, str(value)))])
            continue
        # WooCommerce uses an empty attribute value to mean "any option". Expand
        # it so review/import candidates still show the actual selected option.
        options = next((dropdown.get("options", []) for dropdown in dropdowns if dropdown.get("name") == key), [])
        if options:
            expanded_wildcard = True
            groups.append([
                CapturedVariantOption(name=label, value=str(option.get("text") or option.get("value")))
                for option in options
            ])
    if not groups:
        return ([], expanded_wildcard)
    return ([list(combo) for combo in itertools.product(*groups)], expanded_wildcard)


def _variant_from_embedded(
    raw_data: dict,
    product_title: str,
    vendor_id: str,
    page_id: str,
    description: str,
    dropdowns: list[dict],
) -> list[CapturedProductVariant]:
    attr_combinations, expanded_wildcard = _variant_attribute_combinations(raw_data, dropdowns)
    if not attr_combinations:
        attr_combinations = [[]]
    stock_text = _strip_tags(raw_data.get("availability_html"))
    stock_status, stock_quantity = stock_from_text(raw_data.get("availability_html"), raw_data.get("is_in_stock"))
    price_text = _strip_tags(raw_data.get("price_html")) or str(raw_data.get("display_price") or "")
    price = parse_price(raw_data.get("display_price") or raw_data.get("price_html"))
    variants: list[CapturedProductVariant] = []
    for attrs in attr_combinations:
        warnings = ["Wildcard WooCommerce attribute expanded from dropdown options."] if expanded_wildcard else []
        variant = CapturedProductVariant(
            variant_id=str(raw_data.get("variation_id")) if raw_data.get("variation_id") else None,
            product_title=product_title,
            attributes=attrs,
            price_text=price_text,
            price=price,
            regular_price=parse_price(raw_data.get("display_regular_price")),
            stock_text=stock_text,
            stock_quantity=stock_quantity,
            stock_status=stock_status,
            sku=raw_data.get("sku"),
            raw_variation_data=raw_data,
            confidence=0.9 if price is not None else 0.65,
            warnings=warnings,
        )
        variants.append(variant.model_copy(update=infer_product_fields(vendor_id, page_id, product_title, attrs, description)))
    return variants


def _select_dropdown(page, selector: str | None, value: str) -> None:
    if not selector:
        return
    try:
        page.select_option(selector, value=value, timeout=1500)
    except Exception:
        try:
            page.locator(selector).select_option(value, timeout=1500)
        except Exception:
            return
    try:
        page.wait_for_timeout(300)
    except Exception:
        return


def _fallback_dropdown_variants(page, vendor_id: str, page_id: str, title: str, text: str, dropdowns: list[dict]) -> list[CapturedProductVariant]:
    option_groups = [
        (dropdown.get("label") or normalize_attribute_name(dropdown.get("name", "")), dropdown)
        for dropdown in dropdowns
        if dropdown.get("options")
    ]
    variants: list[CapturedProductVariant] = []
    for combo in itertools.product(*[group[1]["options"] for group in option_groups]) if option_groups else []:
        attrs: list[CapturedVariantOption] = []
        for index, option in enumerate(combo):
            label, dropdown = option_groups[index]
            attrs.append(CapturedVariantOption(name=label, value=str(option.get("text") or option.get("value"))))
            _select_dropdown(page, dropdown.get("selector_hint"), str(option.get("value") or option.get("text")))
        try:
            selected_text = page.locator("body").inner_text(timeout=1500)
        except Exception:
            selected_text = text
        price_text = next(iter(detect_prices(selected_text)), None) or next(iter(detect_prices(text)), None)
        stock_text = next(iter(detect_stock_texts(selected_text)), None) or next(iter(detect_stock_texts(text)), None)
        stock_status, stock_quantity = stock_from_text(stock_text)
        variant = CapturedProductVariant(
            product_title=title,
            attributes=attrs,
            price_text=price_text,
            price=parse_price(price_text),
            stock_text=stock_text,
            stock_quantity=stock_quantity,
            stock_status=stock_status,
            selected_html_excerpt=selected_text[:1500],
            confidence=0.7 if price_text else 0.45,
            warnings=["Variant generated from dropdown enumeration fallback."],
        )
        variants.append(variant.model_copy(update=infer_product_fields(vendor_id, page_id, title, attrs, selected_text)))
    return variants


def _page_html(page) -> str:
    try:
        return page.content()
    except Exception:
        return ""


def _page_url(page) -> str:
    try:
        return getattr(page, "url", "") or ""
    except Exception:
        return ""


def capture_product_variants(page, vendor_id: str, page_id: str, artifact_dir) -> CapturedProductPage:
    from .grazecart import GRAZECART_VENDORS, build_grazecart_captured_page

    artifact_path = Path(artifact_dir)
    artifact_path.mkdir(parents=True, exist_ok=True)

    # GrazeCart-style vendors use category/listing + detail pages, not the
    # WooCommerce variant flow below. Parse them deterministically from HTML.
    if vendor_id in GRAZECART_VENDORS:
        try:
            text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            text = ""
        return build_grazecart_captured_page(
            _page_html(page),
            vendor_id,
            page_id,
            _page_url(page),
            text=text,
            artifact_dir=artifact_path,
        )

    observation = observe_vendor_page(page, vendor_id, page_id, artifact_path)
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        text = ""
    title = _product_title(page, observation)
    dropdowns = _dropdowns(page)
    variants: list[CapturedProductVariant] = []
    embedded = _embedded_variations(page)
    if embedded:
        for raw_data in embedded:
            variants.extend(_variant_from_embedded(raw_data, title, vendor_id, page_id, text, dropdowns))
    else:
        variants = _fallback_dropdown_variants(page, vendor_id, page_id, title, text, dropdowns)

    captured = CapturedProductPage(
        vendor_id=vendor_id,
        page_id=page_id,
        source_url=observation.source_url or "",
        final_url=observation.final_url,
        product_title=title,
        page_kind=observation.page_kind,
        base_price_text=next(iter(observation.detected_prices), None),
        description_text=text[:3000],
        attributes_available=[dropdown.label or dropdown.name or "" for dropdown in observation.detected_dropdowns],
        variants=variants,
        raw_html_path=str(artifact_path / "page.html"),
        text_path=str(artifact_path / "page.txt"),
        screenshot_viewport_path=str(artifact_path / "screenshot_viewport.png"),
        screenshot_full_path=str(artifact_path / "screenshot_full.png"),
        observation_path=str(artifact_path / "observation.json"),
        captured_at=datetime.now(timezone.utc).isoformat(),
        warnings=[] if variants else ["No variants detected."],
        coverage_report={
            "source_pages_completed": 1,
            "final_urls_reached": [_page_url(page)],
            "product_cards_found": 0,
            "product_detail_links_found": 0,
            "detail_pages_fetched": 1 if observation.page_kind == "product_detail" else 0,
            "option_groups_found": len(dropdowns),
            "orderable_offers_extracted": len(variants),
            "variants_extracted": len(variants),
            "bundle_pack_case_offers_found": len([variant for variant in variants if variant.is_bundle]),
            "incomplete_discoveries": [variant.incomplete_reason for variant in variants if variant.incomplete_reason],
            "parser_warnings": [] if variants else ["No variants detected."],
        },
    )
    captured.variants = extract_orderable_offers(captured)
    recovered_variants: list[CapturedProductVariant] = []
    recovery_attempts = 0
    recovery_successes = 0
    for variant in captured.variants:
        initial = validate_offer(variant)
        if initial.complete:
            recovered_variants.append(
                variant.model_copy(
                    update={
                        "evidence_checkpoints": initial.evidence_checkpoints,
                        "validation_reasons": [],
                        "incomplete_discovery": False,
                        "incomplete_reason": None,
                    }
                )
            )
            continue
        recovery_attempts += 1
        recovered, result = recover_incomplete_offer(captured, variant)
        if result.complete:
            recovery_successes += 1
        recovered_variants.append(recovered)
    captured.variants = recovered_variants
    captured.coverage_report.update(
        {
            "recovery_attempts": recovery_attempts,
            "recovery_successes": recovery_successes,
            "incomplete_offers": len([variant for variant in captured.variants if variant.incomplete_discovery]),
            "evidence_checkpoints_checked": sorted(
                {
                    checkpoint
                    for variant in captured.variants
                    for checkpoint in variant.evidence_checkpoints
                }
            ),
        }
    )
    captured.coverage_report = report_fetch_coverage(captured)
    (artifact_path / "variant_snapshots.json").write_text(
        json.dumps(captured.model_dump(), indent=2),
        encoding="utf-8",
    )
    return captured
