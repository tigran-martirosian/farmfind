"""Deterministic GrazeCart-style category/listing, product-detail, and bundle parser.

Example Farm B, Example Farm C, and Example Farm D use GrazeCart-style listing pages
rather than Example Farm A's WooCommerce variant flow. This module parses their pages
from HTML strings only (no Playwright, no network), so every branch is offline
testable with fixtures.

Rules enforced here:
* Pricing-locked cards ("Sign up for pricing", "Log in to see price") are captured
  for diagnostics but never become purchasable variants.
* Only product-detail pages with real prices/order controls yield variants.
* Bundle unit_price and computed_savings are computed deterministically. The site's
  own savings text is stored as reported_savings_text and never trusted for math.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

from .models import (
    CapturedProductPage,
    CapturedProductVariant,
    CapturedVariantOption,
    GrazeCartProductCard,
)
from .package_parser import parse_price as parse_offer_price
from .package_parser import parse_price_fact, parse_quantity, parse_savings
from .offer_validation import (
    extract_orderable_offers,
    find_product_cards,
    inspect_card_options,
    inspect_product_detail,
    interpret_offer_facts,
    observe_source_page,
    recover_incomplete_offer,
    report_fetch_coverage,
    validate_offer,
)

GRAZECART_VENDORS = {"vendor_b", "vendor_c", "vendor_d"}

# Product types the optimizer actually supports. GrazeCart cheese subtypes are
# mapped into the app's current single cheese bucket.
SUPPORTED_CANONICAL = {"cow_milk", "sheep_milk", "cream", "butter", "eggs", "cheese"}

PRICING_LOCK_TEXT = [
    "sign up for pricing",
    "log in to see price",
    "login to see price",
    "sign in to see price",
    "sign up to see price",
    "register to see price",
    "become a member",
    "price after login",
    "contact for pricing",
]

_CHEESE_WORDS = [
    "cheese",
    "cheddar",
    "gouda",
    "colby",
    "swiss",
    "gruyere",
    "parmesan",
    "mozzarella",
    "provolone",
    "feta",
]

_UNSUPPORTED_WORDS = [
    "ghee",
    "soap",
    "colostrum",
    "produce",
    "chocolate milk",
    "tallow",
    "lard",
    "candle",
    "lotion",
    "bone broth",
]


def _strip_tags(value: str | None) -> str:
    if not value:
        return ""
    text = html_lib.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_price(value: str | None) -> float | None:
    return parse_offer_price(value)


def _first(pattern: str, text: str, group: int = 1) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(group) if match else None


def _class_text(block: str, class_name: str) -> str | None:
    raw_data = _first(
        rf'<[^>]*class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</',
        block,
    )
    return _strip_tags(raw_data) or None


def _itemprop_text(block: str, prop_name: str) -> str | None:
    raw_data = _first(
        rf'<[^>]*itemprop=["\']{re.escape(prop_name)}["\'][^>]*>(.*?)</',
        block,
    )
    if raw_data:
        return _strip_tags(raw_data) or None
    content = _first(
        rf'<[^>]*itemprop=["\']{re.escape(prop_name)}["\'][^>]*content=["\']([^"\']+)["\']',
        block,
    )
    return _strip_tags(content) or None


def _tag_text(block: str, tag_name: str) -> str | None:
    raw_data = _first(rf"<{tag_name}\b[^>]*>(.*?)</{tag_name}>", block)
    return _strip_tags(raw_data) or None


def _all_class_text(block: str, class_name: str) -> list[str]:
    matches = re.findall(
        rf'<[^>]*class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</',
        block,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [text for text in (_strip_tags(item) for item in matches) if text]


# --- Inference -------------------------------------------------------------


def infer_product_type(text: str | None) -> str | None:
    """Deterministic product-type inference for a title/label.

    Returns one of the SUPPORTED_CANONICAL values, or None for unsupported
    products (ghee/soap/etc)."""
    if not text:
        return None
    t = text.lower()
    if any(word in t for word in _UNSUPPORTED_WORDS):
        return None
    if "egg" in t:
        return "eggs"
    if any(word in t for word in _CHEESE_WORDS):
        return "cheese"
    if "cream" in t:
        return "cream"
    if "butter" in t and "buttermilk" not in t:
        return "butter"
    if "sheep" in t and "milk" in t:
        return "sheep_milk"
    if "milk" in t:
        return "cow_milk"
    return None


def infer_size(product_type: str | None, text: str | None) -> tuple[float, str] | None:
    """Generic package-size inference for a supported product family."""
    if not product_type or not text:
        return None
    fact = parse_quantity(text, product_type)
    if fact.total_equivalent_quantity is None or fact.total_equivalent_unit is None:
        return None
    return fact.total_equivalent_quantity, fact.total_equivalent_unit


def _infer_packaging(text: str) -> str | None:
    t = text.lower()
    if "glass" in t:
        return "glass"
    if "plastic" in t:
        return "plastic"
    return None


def _infer_storage(text: str) -> str | None:
    return "frozen" if "frozen" in text.lower() else None


ORDER_BUTTON_TEXT = [
    "add to cart",
    "add to order",
    "select option",
    "select options",
    "sold out",
]


def _order_button_text(block: str) -> str | None:
    text = _strip_tags(block).lower()
    for marker in ORDER_BUTTON_TEXT:
        if marker in text:
            return marker.title()
    return None


def _stock_status(block: str) -> str:
    return "out_of_stock" if "sold out" in _strip_tags(block).lower() else "in_stock"


def _price_text(block: str) -> tuple[str | None, bool]:
    text = _strip_tags(block)
    gift = re.search(r"gift\s+amount\s*:?\s*(\$\s?\d+(?:\.\d{2})?)", text, flags=re.IGNORECASE)
    if gift:
        return gift.group(1), True
    item_price = _itemprop_text(block, "price")
    if item_price and parse_price(item_price) is not None:
        if "$" not in item_price:
            item_price = f"${item_price}"
        return item_price, False
    class_price = _class_text(block, "price")
    if class_price and parse_price(class_price) is not None:
        return class_price, False
    match = re.search(r"\$\s?\d+(?:\.\d{2})?", text)
    return (match.group(0), False) if match else (None, False)


# --- Category / listing parsing -------------------------------------------


def _product_listing_sections(html: str) -> list[str]:
    sections = re.findall(
        r'<section\b[^>]*(?:itemtype=["\']https?://schema\.org/Product["\']|id=["\']product_[^"\']+["\'])[^>]*>.*?</section>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [section for section in sections if "productlisting__" in section.lower()]


def _product_card_blocks(html: str) -> list[str]:
    blocks = [
        block
        for _, block in re.findall(
            r'<(li|div|article)[^>]*class=["\'][^"\']*\bproduct-card\b[^"\']*["\'][^>]*>(.*?)</\1>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]
    return blocks + _product_listing_sections(html)


def _card_title(block: str) -> str | None:
    return (
        _itemprop_text(block, "name")
        or _class_text(block, "product-title")
        or _first(r'<a\b[^>]*\btitle=["\']([^"\']+)["\']', block)
        or _first(r'<img\b[^>]*\balt=["\']([^"\']+)["\']', block)
        or _tag_text(block, "h3")
        or _tag_text(block, "h2")
    )


def _card_subtitle(block: str) -> str | None:
    return (
        _class_text(block, "product-subtitle")
        or _itemprop_text(block, "description")
    )


def _card_secondary_line(block: str) -> str | None:
    return (
        _class_text(block, "product-secondary")
        or _class_text(block, "product-size")
        or _class_text(block, "subtitle")
        or _class_text(block, "description")
    )


def _card_url(block: str) -> str | None:
    return (
        _first(r'<meta\b[^>]*itemprop=["\']url["\'][^>]*content=["\']([^"\']+)["\']', block)
        or _first(r'<a\b[^>]*href=["\']([^"\']+)["\']', block)
    )


def parse_category_cards(html: str) -> list[GrazeCartProductCard]:
    cards: list[GrazeCartProductCard] = []
    for block in _product_card_blocks(html):
        title = _card_title(block)
        if not title:
            continue
        subtitle = _card_subtitle(block)
        secondary = _card_secondary_line(block)
        full_text = _strip_tags(block)
        option_rows = _collect_option_rows(block)
        hidden_option_text = " ".join(
            " ".join(str(value) for value in row.values() if value)
            for row in option_rows
        ) or None
        combined_text = " ".join(item for item in [title, subtitle, secondary, full_text, hidden_option_text] if item)
        price_text, gift_amount_visible = _price_text(block)
        price = parse_price(price_text) if price_text else None
        block_lower = block.lower()
        locked_marker = _class_text(block, "price-locked")
        button_text = _order_button_text(block)
        pricing_locked = bool(locked_marker) or (
            price is None and any(marker in block_lower for marker in PRICING_LOCK_TEXT)
        )
        product_type = infer_product_type(combined_text)
        size = infer_size(product_type, combined_text)
        href = _card_url(block)
        card = GrazeCartProductCard(
            title=title,
            subtitle=subtitle,
            secondary_line=secondary,
            description=_itemprop_text(block, "description"),
            product_url=html_lib.unescape(href) if href else None,
            brand=_class_text(block, "brand") or _class_text(block, "vendor"),
            badges=_all_class_text(block, "badge"),
            price_text=price_text if price is not None else None,
            price=price,
            pricing_locked=pricing_locked,
            stock_status=_stock_status(block),
            order_button_text=button_text,
            full_text=full_text,
            hidden_option_text=hidden_option_text,
            option_rows=option_rows,
            html_snippet=block[:2500],
            savings_text=_class_text(block, "reported-savings") or _class_text(block, "savings"),
            needs_variant_detail=bool(button_text and "select option" in button_text.lower() and size is None),
            gift_amount_visible=gift_amount_visible,
            inferred_product_type=product_type,
            inferred_package_size=size[0] if size else None,
            inferred_unit=size[1] if size else None,
            supported=product_type in SUPPORTED_CANONICAL,
        )
        if product_type is None:
            card.warnings.append("Unsupported product type; captured for diagnostics only.")
        cards.append(card)
    return cards


def _variant_from_card(card: GrazeCartProductCard) -> CapturedProductVariant | None:
    if card.pricing_locked or card.price is None or not card.order_button_text:
        return None
    label = " ".join(item for item in [card.title, card.subtitle, card.secondary_line, card.full_text] if item)
    product_type = card.inferred_product_type
    supported = product_type in SUPPORTED_CANONICAL
    warnings = list(card.warnings)
    if card.order_button_text and "select option" in card.order_button_text.lower():
        warnings.append("needs_variant_detail: Select Option card may require detail-page variant data.")
    if product_type is not None and not supported:
        warnings.append(f"Product type '{product_type}' is not supported by the optimizer yet; needs review.")
    if product_type is None:
        warnings.append("Unsupported product type; needs review.")
    quantity = parse_quantity(label, product_type)
    price_fact = parse_price_fact(card.price_text)
    return CapturedProductVariant(
        product_title=card.title,
        attributes=[CapturedVariantOption(name="Option", value=label)],
        price_text=card.price_text,
        price=card.price,
        total_price=card.price,
        option_label=label,
        stock_status=card.stock_status,
        inferred_product_type=product_type if supported else None,
        inferred_package_size=quantity.total_equivalent_quantity or card.inferred_package_size,
        inferred_unit=quantity.total_equivalent_unit or card.inferred_unit,
        inferred_packaging=_infer_packaging(label),
        inferred_storage_state=_infer_storage(label),
        confidence=0.86 if (supported and (quantity.total_equivalent_quantity or card.inferred_package_size) and card.price is not None) else 0.55,
        quantity_evidence=quantity.__dict__,
        price_evidence=price_fact.__dict__,
        discovery_source="category_card",
        incomplete_discovery=quantity.missing_reason is not None,
        incomplete_reason=quantity.missing_reason,
        warnings=warnings,
    )


def _variants_from_card_option_panel(card: GrazeCartProductCard) -> list[CapturedProductVariant]:
    rows = card.option_rows or _collect_option_rows(card.html_snippet or "")
    variants: list[CapturedProductVariant] = []
    for row in rows:
        variants.append(
            _build_variant(
                title=card.title,
                label=" ".join(
                    item
                    for item in [row.get("label"), card.subtitle]
                    if item
                ),
                total_price=parse_price(row.get("price_text")),
                price_text=row.get("price_text"),
                reported_savings_text=row.get("reported_savings_text"),
            ).model_copy(update={"discovery_source": "option_row"}))
    return variants


def variants_from_category_cards(cards: list[GrazeCartProductCard]) -> list[CapturedProductVariant]:
    variants: list[CapturedProductVariant] = []
    for card in cards:
        option_variants = _variants_from_card_option_panel(card)
        if option_variants:
            variants.extend(option_variants)
            continue
        if variant := _variant_from_card(card):
            variants.append(variant)
    _apply_bundle_math(variants)
    return variants


# --- Product-detail parsing ------------------------------------------------


def _detail_title(html: str) -> str:
    return _class_text(html, "product-title") or "Unknown Product"


def has_order_controls(html: str) -> bool:
    lower = html.lower()
    return any(
        marker in lower
        for marker in [
            "add to cart",
            "add to order",
            "select option",
            "select options",
            "sold out",
            "add-to-order",
            "add-to-cart",
            "update order",
            "single_add_to_cart_button",
        ]
    )


def _parse_option_rows(html: str) -> list[dict]:
    rows: list[dict] = []
    for block in re.findall(
        r'<(?:label|div|tr)[^>]*class="[^"]*\boption-row\b[^"]*"[^>]*>(.*?)</(?:label|div|tr)>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        label = _class_text(block, "option-label")
        price_text = _class_text(block, "option-price")
        if not label or price_text is None:
            continue
        reported = _class_text(block, "reported-savings")
        if reported is None:
            data_reported = _first(r'data-reported-savings="([^"]+)"', block)
            reported = f"${data_reported}" if data_reported else None
        rows.append({"label": label, "price_text": price_text, "reported_savings_text": reported})
    for block in re.findall(
        r"<tr\b[^>]*>.*?</tr>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        if "varianttitle" not in block.lower():
            continue
        label = (
            _first(r'<span\b[^>]*\bclass="[^"]*\bvariantTitle\b[^"]*"[^>]*\btitle="([^"]+)"', block)
            or _class_text(block, "variantTitle")
        )
        if not label:
            continue
        money = re.findall(r"\$\s?[\d,]+(?:\.\d{2})?", _strip_tags(block))
        if not money:
            continue
        reported = money[1] if len(money) > 1 else None
        rows.append(
            {
                "label": label,
                "price_text": money[0],
                "reported_savings_text": reported,
            }
        )
    return rows


def _parse_select_options(html: str) -> list[dict]:
    """Parse bundle/variant options exposed as <select><option> dropdowns.

    GrazeCart 'Select Option' controls often render bundles as dropdown options
    like "4 Gallon Bundle - $42.80 (Save $3.00)". Any <option> whose text carries
    a price becomes a purchasable option."""
    rows: list[dict] = []
    for block in re.findall(r"<select\b[^>]*>(.*?)</select>", html, flags=re.IGNORECASE | re.DOTALL):
        for option_html in re.findall(r"<option\b[^>]*>(.*?)</option>", block, flags=re.IGNORECASE | re.DOTALL):
            text = _strip_tags(option_html)
            price_match = re.search(r"\$\s?[\d,]+\.\d{2}", text)
            if not price_match:
                continue
            label = text[: price_match.start()].strip(" -–—:·|")
            if not label:
                continue
            savings = re.search(r"save\s*(\$\s?[\d,]+\.\d{2})", text, flags=re.IGNORECASE)
            rows.append(
                {
                    "label": label,
                    "price_text": price_match.group(0),
                    "reported_savings_text": savings.group(1) if savings else None,
                }
            )
    return rows


def _collect_option_rows(html: str) -> list[dict]:
    """All purchasable options from both option-row blocks and select dropdowns."""
    rows = _parse_option_rows(html) + _parse_select_options(html)
    deduped: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = f"{row['label'].strip().lower()}|{(row['price_text'] or '').strip()}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _build_variant(
    *,
    title: str,
    label: str,
    total_price: float | None,
    price_text: str | None,
    reported_savings_text: str | None,
) -> CapturedProductVariant:
    product_type = infer_product_type(label) or infer_product_type(title)
    offer_text = " ".join(item for item in [title, label, price_text or "", reported_savings_text or ""] if item)
    quantity = parse_quantity(label, product_type)
    if quantity.total_equivalent_quantity is None:
        quantity = parse_quantity(offer_text, product_type)
    package_size = quantity.total_equivalent_quantity
    unit = quantity.total_equivalent_unit
    price_fact = parse_price_fact(price_text)
    savings = parse_savings(reported_savings_text or offer_text, price_fact)
    supported = product_type in SUPPORTED_CANONICAL
    is_bundle = quantity.is_bundle_or_pack

    unit_price = (
        round(total_price / package_size, 4)
        if total_price is not None and package_size
        else None
    )
    warnings: list[str] = []
    if product_type is not None and not supported:
        warnings.append(
            f"Product type '{product_type}' is not supported by the optimizer yet; needs review."
        )

    variant = CapturedProductVariant(
        product_title=title,
        attributes=[CapturedVariantOption(name="Option", value=label)],
        price_text=price_text,
        price=total_price,
        total_price=total_price,
        unit_price=unit_price,
        option_label=label,
        is_bundle=is_bundle,
        bundle_unit=unit,
        reported_savings_text=reported_savings_text,
        computed_savings=savings.savings_amount if savings.savings_type == "computed_regular_minus_sale" else None,
        stock_status="in_stock" if total_price is not None else "unknown",
        inferred_product_type=product_type if supported else None,
        inferred_package_size=package_size,
        inferred_unit=unit,
        inferred_packaging=_infer_packaging(label) or _infer_packaging(title),
        confidence=0.9 if (supported and total_price is not None and package_size) else 0.55,
        quantity_evidence=quantity.__dict__,
        price_evidence=price_fact.__dict__,
        savings_evidence=savings.__dict__,
        discovery_source="option_row",
        incomplete_discovery=quantity.missing_reason is not None,
        incomplete_reason=quantity.missing_reason,
        warnings=warnings,
    )
    return variant


def _bundle_math_key(variant: CapturedProductVariant) -> tuple[str, str | None, str | None]:
    title = re.sub(r"\s+", " ", (variant.product_title or "").lower()).strip()
    return title, variant.inferred_product_type, variant.inferred_unit


def _apply_bundle_math(variants: list[CapturedProductVariant]) -> None:
    groups: dict[tuple[str, str | None, str | None], list[CapturedProductVariant]] = {}
    for variant in variants:
        if variant.total_price is None or not variant.inferred_package_size:
            continue
        groups.setdefault(_bundle_math_key(variant), []).append(variant)

    for priced in groups.values():
        if not priced:
            continue
        base = min(priced, key=lambda variant: variant.inferred_package_size or 0.0)
        base_package = base.inferred_package_size or 1.0
        base_unit_price = round((base.total_price or 0.0) / base_package, 4)

        for variant in priced:
            package_size = variant.inferred_package_size or 0.0
            variant.base_unit_price = base_unit_price
            variant.bundle_quantity = (
                round(package_size / base_package, 4) if base_package else None
            )
            if (variant.bundle_quantity or 0) > 1:
                variant.is_bundle = True
            expected = base_unit_price * package_size
            variant.computed_savings = round(expected - (variant.total_price or 0.0), 2)
            # Site-reported savings are display evidence only. They may be rounded,
            # promotional, or scoped to a different storefront base row.
            if parse_price(variant.reported_savings_text) is not None:
                variant.savings_mismatch_warning = False


def parse_detail_variants(html: str, title: str | None = None) -> list[CapturedProductVariant]:
    resolved_title = title or _detail_title(html)
    rows = _collect_option_rows(html)
    variants: list[CapturedProductVariant] = []
    if rows:
        for row in rows:
            variants.append(
                _build_variant(
                    title=resolved_title,
                    label=row["label"],
                    total_price=parse_price(row["price_text"]),
                    price_text=row["price_text"],
                    reported_savings_text=row["reported_savings_text"],
                )
            )
    else:
        # No selectable options: a single purchasable variant from the base price,
        # but only when a real order control and price are present.
        base_price_text = _class_text(html, "price")
        base_price = parse_price(base_price_text)
        if base_price is not None and has_order_controls(html):
            variants.append(
                _build_variant(
                    title=resolved_title,
                    label=resolved_title,
                    total_price=base_price,
                    price_text=base_price_text,
                    reported_savings_text=None,
                )
            )
    _apply_bundle_math(variants)
    return variants


# --- Page-kind detection + assembly ---------------------------------------


def detect_page_kind(html: str) -> str:
    lower = html.lower()
    if re.search(r'class="[^"]*\bproduct-card\b', lower) or "productlisting__" in lower:
        return "category_listing"
    if re.search(r'class="[^"]*\bproduct-title\b', lower) and (
        has_order_controls(lower) or 'class="option-row' in lower or 'class="price' in lower
    ):
        return "product_detail"
    return "unknown"


def is_pricing_locked(html: str) -> bool:
    lower = html.lower()
    if re.search(r"\$\s?\d", html) or has_order_controls(html):
        return False
    return any(marker in lower for marker in PRICING_LOCK_TEXT)


def build_grazecart_captured_page(
    html: str,
    vendor_id: str,
    page_id: str,
    source_url: str,
    *,
    final_url: str | None = None,
    text: str = "",
    artifact_dir=None,
) -> CapturedProductPage:
    page_kind = detect_page_kind(html)
    cards: list[GrazeCartProductCard] = []
    variants: list[CapturedProductVariant] = []
    warnings: list[str] = []

    if page_kind == "category_listing":
        cards = parse_category_cards(html)
        find_product_cards(CapturedProductPage(
            vendor_id=vendor_id,
            page_id=page_id,
            source_url=source_url,
            final_url=final_url or source_url,
            page_kind="category_listing",
            product_cards=cards,
            captured_at=datetime.now(timezone.utc).isoformat(),
        ))
        for card in cards:
            inspect_card_options(card)
        locked_cards = [card for card in cards if card.pricing_locked]
        if cards and len(locked_cards) == len(cards):
            warnings.append(
                "All product cards are pricing-locked; captured for diagnostics only, "
                "no purchasable variants were created."
            )
        else:
            variants = variants_from_category_cards(cards)
    elif page_kind == "product_detail":
        if is_pricing_locked(html):
            warnings.append(
                "Product detail is pricing-locked; no purchasable variants were created."
            )
        else:
            variants = parse_detail_variants(html)
            inspect_product_detail(CapturedProductPage(
                vendor_id=vendor_id,
                page_id=page_id,
                source_url=source_url,
                final_url=final_url or source_url,
                product_title=_detail_title(html),
                page_kind="product_detail",
                description_text=text[:3000],
                captured_at=datetime.now(timezone.utc).isoformat(),
            ))
            if not variants and has_order_controls(html):
                warnings.append("Order controls were visible but no orderable offers were extracted.")

    pricing_locked = is_pricing_locked(html) or bool(
        cards and all(card.pricing_locked for card in cards)
    )
    if not variants and not cards:
        warnings.append("No GrazeCart product cards or purchasable variants detected.")

    title = None
    if page_kind == "product_detail":
        title = _detail_title(html)

    captured = CapturedProductPage(
        vendor_id=vendor_id,
        page_id=page_id,
        source_url=source_url,
        final_url=final_url or source_url,
        product_title=title,
        page_kind=page_kind if page_kind != "unknown" else "category_listing",
        base_price_text=_class_text(html, "price"),
        description_text=text[:3000],
        variants=variants,
        product_cards=cards,
        pricing_locked=pricing_locked,
        captured_at=datetime.now(timezone.utc).isoformat(),
        warnings=warnings,
        coverage_report={
            "source_pages_completed": 1,
            "final_urls_reached": [final_url or source_url],
            "product_cards_found": len(cards),
            "product_detail_links_found": len([card for card in cards if card.product_url]),
            "detail_pages_fetched": 1 if page_kind == "product_detail" else 0,
            "option_groups_found": len(_collect_option_rows(html)) if page_kind == "product_detail" else 0,
            "orderable_offers_extracted": len(variants),
            "variants_extracted": len(variants),
            "bundle_pack_case_offers_found": len([variant for variant in variants if variant.is_bundle]),
            "incomplete_discoveries": [
                variant.incomplete_reason
                for variant in variants
                if variant.incomplete_discovery and variant.incomplete_reason
            ] + warnings,
            "parser_warnings": warnings,
        },
    )
    observe_source_page(captured)
    captured.variants = extract_orderable_offers(captured)
    recovered_variants: list[CapturedProductVariant] = []
    recovery_attempts = 0
    recovery_successes = 0
    for variant in captured.variants:
        interpret_offer_facts(
            " ".join(item for item in [variant.product_title, variant.option_label, variant.price_text] if item),
            variant.inferred_product_type,
        )
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
            "card_subtitles_captured": len([card for card in captured.product_cards if card.subtitle or card.secondary_line]),
            "option_buttons_found": len([card for card in captured.product_cards if card.order_button_text and "option" in card.order_button_text.lower()]),
            "option_panels_expanded": 0,
            "option_rows_extracted": sum(len(_collect_option_rows(card.html_snippet or "")) for card in captured.product_cards)
            + (len(_collect_option_rows(html)) if page_kind == "product_detail" else 0),
            "savings_facts_extracted": len([variant for variant in captured.variants if variant.savings_evidence and (variant.savings_evidence.get("savings_amount") is not None or variant.savings_evidence.get("savings_percent") is not None)]),
            "incomplete_offers": len([variant for variant in captured.variants if variant.incomplete_discovery]),
            "recovery_attempts": recovery_attempts,
            "recovery_successes": recovery_successes,
            "incomplete_evidence_checkpoints": {
                variant.option_label or variant.product_title: variant.evidence_checkpoints
                for variant in captured.variants
                if variant.incomplete_discovery
            },
        }
    )
    captured.coverage_report = report_fetch_coverage(captured)
    if artifact_dir is not None:
        import json
        from pathlib import Path

        path = Path(artifact_dir)
        path.mkdir(parents=True, exist_ok=True)
        captured.raw_html_path = str(path / "page.html")
        captured.observation_path = str(path / "observation.json")
        (path / "variant_snapshots.json").write_text(
            json.dumps(captured.model_dump(), indent=2), encoding="utf-8"
        )
    return captured
