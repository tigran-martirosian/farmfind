"""Generic product-offer quantity, price, and savings interpretation.

This module intentionally parses expressions compositionally instead of
memorizing vendor/product-specific examples. Site-specific parsers feed unparsed
title/option/detail text here before UI cleanup so future bundle/case/pack
counts can be understood from evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re


_UNICODE_FRACTIONS = {
    "½": "1/2",
    "¼": "1/4",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
}

_NUMBER_WORDS = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "eleven": 11.0,
    "twelve": 12.0,
    "thirteen": 13.0,
    "fourteen": 14.0,
    "fifteen": 15.0,
    "sixteen": 16.0,
    "seventeen": 17.0,
    "eighteen": 18.0,
    "nineteen": 19.0,
    "twenty": 20.0,
    "half": 0.5,
    "dozen": 12.0,
}

_UNIT_ALIASES = {
    "gallon": "gallon",
    "gallons": "gallon",
    "gal": "gallon",
    "quart": "quart",
    "quarts": "quart",
    "qt": "quart",
    "pint": "pint",
    "pints": "pint",
    "pt": "pint",
    "ounce": "oz",
    "ounces": "oz",
    "oz": "oz",
    "pound": "lb",
    "pounds": "lb",
    "lb": "lb",
    "lbs": "lb",
    "count": "count",
    "ct": "count",
    "each": "each",
    "dozen": "dozen",
}

_PACK_WORDS = {"pack", "pk", "case", "bundle", "bulk", "box", "tray"}


@dataclass
class QuantityFact:
    raw_text: str
    item_count: float | None = None
    unit_quantity: float | None = None
    unit_name: str | None = None
    total_equivalent_quantity: float | None = None
    total_equivalent_unit: str | None = None
    display_text: str | None = None
    is_bundle_or_pack: bool = False
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    missing_reason: str | None = None


@dataclass
class PriceFact:
    raw_price_text: str | None = None
    current_price: float | None = None
    regular_price: float | None = None
    sale_price: float | None = None
    unit_price: float | None = None
    currency: str = "USD"
    evidence: list[str] = field(default_factory=list)


@dataclass
class SavingsFact:
    raw_text: str | None = None
    savings_amount: float | None = None
    savings_percent: float | None = None
    savings_type: str = "unknown"
    evidence: list[str] = field(default_factory=list)


@dataclass
class ProductEvidence:
    vendor_id: str | None = None
    source_page_id: str | None = None
    source_url: str | None = None
    final_url: str | None = None
    product_card_title: str | None = None
    product_card_text: str | None = None
    product_detail_url: str | None = None
    product_detail_title: str | None = None
    product_detail_text: str | None = None
    option_label: str | None = None
    option_text: str | None = None
    safe_text_snippet: str | None = None
    discovery_source: str | None = None


@dataclass
class OrderableOffer:
    parent_product_name: str
    offer_name: str
    option_label: str | None
    raw_offer_text: str
    product_type_hint: str | None
    species_hint: str | None
    stock_signal: str | None
    price: PriceFact
    quantity: QuantityFact
    savings: SavingsFact
    evidence_fields_used: list[str]
    confidence: float
    incomplete_flags: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


def _clean(text: str | None) -> str:
    value = text or ""
    for old, new in _UNICODE_FRACTIONS.items():
        value = value.replace(old, f" {new} ")
    value = value.replace("-", " ")
    value = re.sub(r"\bgal\.?\b", "gallon", value, flags=re.IGNORECASE)
    value = re.sub(r"\bqt\.?\b", "quart", value, flags=re.IGNORECASE)
    value = re.sub(r"\bpt\.?\b", "pint", value, flags=re.IGNORECASE)
    value = re.sub(r"\blbs?\.?\b", "lb", value, flags=re.IGNORECASE)
    value = re.sub(r"\boz\.?\b", "oz", value, flags=re.IGNORECASE)
    value = re.sub(r"\bct\.?\b", "count", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip().lower()


def _strip_price_noise(text: str | None) -> str:
    value = text or ""
    value = re.sub(r"\$\s*[\d,]+(?:\.\d{1,2})?", " ", value)
    value = re.sub(
        r"\b(?:original\s+price|current\s+price|regular\s+price|sale\s+price|"
        r"original|current|regular|sale|was|now|save|savings?|you save)\b"
        r"\s*(?:is|was|:)?\s*[\d,]+(?:\.\d{1,2})?",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\b(?:evidence_checkpoints|recovery_checked|stopped)\s*=\s*[^,\n]+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def _number(value: str | None) -> float | None:
    if not value:
        return None
    token = value.strip().lower()
    if token in _NUMBER_WORDS:
        return _NUMBER_WORDS[token]
    if "/" in token:
        parts = token.split("/", 1)
        try:
            return float(parts[0]) / float(parts[1])
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        return float(token)
    except ValueError:
        return None


def _fmt_number(value: float | None) -> str:
    if value is None:
        return "?"
    if abs(value - 0.5) < 0.0001:
        return "1/2"
    if abs(value - (1 / 3)) < 0.0001:
        return "1/3"
    if abs(value - 0.25) < 0.0001:
        return "1/4"
    return f"{value:g}"


def _canonical_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    return _UNIT_ALIASES.get(unit.strip().lower().rstrip("."), unit.strip().lower())


def _to_optimizer_unit(product_type: str | None, quantity: float, unit: str) -> tuple[float, str]:
    unit = _canonical_unit(unit) or unit
    if unit == "dozen":
        return quantity * 12.0, "count"
    if product_type in {"cow_milk", "sheep_milk"}:
        if unit == "gallon":
            return quantity, "gallon"
        if unit == "quart":
            return quantity / 4.0, "gallon"
        if unit == "pint":
            return quantity / 8.0, "gallon"
    if product_type in {"cream", "whey", "colostrum"}:
        if unit == "gallon":
            return quantity * 8.0, "pint"
        if unit == "quart":
            return quantity * 2.0, "pint"
        if unit == "pint":
            return quantity, "pint"
    if product_type in {"cheese"} and unit in {"quart", "pint"}:
        if unit == "quart":
            return quantity * 2.0, "pint"
        return quantity, "pint"
    if product_type in {"butter", "cheese"}:
        if unit == "lb":
            return quantity, "lb"
        if unit == "oz":
            return quantity / 16.0, "lb"
    if product_type == "eggs":
        if unit == "count":
            return quantity, "count"
        if unit == "dozen":
            return quantity * 12.0, "count"
    return quantity, unit


def _unit_pattern() -> str:
    return r"gallons?|gal|quarts?|qt|pints?|pt|ounces?|oz|pounds?|lbs?|lb|count|ct|dozen|each"


def _number_pattern() -> str:
    return r"\d+(?:\.\d+)?|\d+\s*/\s*\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|half"


def _unit_phrase(match) -> tuple[float, str, str] | None:
    qty = _number(re.sub(r"\s+", "", match.group("qty")))
    unit = _canonical_unit(match.group("unit"))
    if qty is None or unit is None:
        return None
    return qty, unit, match.group(0)


def parse_quantity(text: str, product_type: str | None = None) -> QuantityFact:
    raw_text = text or ""
    quantity_text = _strip_price_noise(raw_text)
    t = _clean(quantity_text)
    evidence: list[str] = []
    is_bundle = any(word in t for word in _PACK_WORDS | {"multi pack", "family pack", "value pack", "savings"})

    num = _number_pattern()
    unit = _unit_pattern()

    # case/pack of N dozen/count/units
    patterns = [
        rf"\b(?P<pack>case|pack|pk|bundle|box|tray)\s+of\s+(?P<count>{num})\s+(?P<unit>dozen|count|ct)\b",
        rf"\b(?P<count>{num})\s+(?P<unit>dozen|count|ct)\s+(?P<pack>case|pack|pk|bundle|box|tray)\b",
        rf"\b(?P<count>{num})\s+per\s+(?P<pack>pack|pk|case|box|tray)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, t)
        if match:
            count = _number(re.sub(r"\s+", "", match.group("count")))
            unit_name = _canonical_unit(match.groupdict().get("unit") or "count")
            if count is not None and unit_name is not None:
                total, total_unit = _to_optimizer_unit(product_type, count, unit_name)
                display = f"{_fmt_number(count)} {unit_name}"
                item_count = count * 12.0 if unit_name == "dozen" else count
                return QuantityFact(
                    raw_text=raw_text,
                    item_count=item_count,
                    unit_quantity=1.0,
                    unit_name=unit_name,
                    total_equivalent_quantity=total,
                    total_equivalent_unit=total_unit,
                    display_text=display,
                    is_bundle_or_pack=True,
                    confidence=0.92,
                    evidence=[match.group(0)],
                )

    # N-pack rows where the inner unit appears after the pack marker:
    # "6 Pk Quart", "6 Pk 8 oz", "4 Pk ... 1lb bag".
    pack_inner = re.search(
        rf"\b(?P<count>{num})\s*(?:pack|pk|case|bundle|box|tray)\.?\b(?P<tail>.{{0,120}})",
        t,
    )
    if pack_inner:
        count = _number(re.sub(r"\s+", "", pack_inner.group("count") or ""))
        tail = pack_inner.group("tail") or ""
        inner = re.search(rf"\b(?P<qty>{num})?\s*(?P<unit>{unit})\b", tail)
        if count is not None and inner:
            qty = _number(re.sub(r"\s+", "", inner.groupdict().get("qty") or "")) or 1.0
            unit_name = _canonical_unit(inner.group("unit"))
            if unit_name:
                total, total_unit = _to_optimizer_unit(product_type, count * qty, unit_name)
                display = f"{_fmt_number(count)} x {_fmt_number(qty)} {unit_name}"
                return QuantityFact(
                    raw_text=raw_text,
                    item_count=count,
                    unit_quantity=qty,
                    unit_name=unit_name,
                    total_equivalent_quantity=total,
                    total_equivalent_unit=total_unit,
                    display_text=display,
                    is_bundle_or_pack=True,
                    confidence=0.9,
                    evidence=[pack_inner.group(0).strip()],
                )

    # N x size unit, N half-gallons, pack of N bottles of X, N jars each X.
    composition_patterns = [
        rf"\b(?P<count>{num})\s*(?:x|×)\s*(?P<qty>{num})\s*(?P<unit>{unit})\b",
        rf"\b(?P<count>{num})\s+(?P<qty>{num})\s*(?P<unit>{unit})s?\s*(?:pack|pk|case|bundle|box|tray)\b",
        rf"\b(?:pack|case|bundle|box)\s+of\s+(?P<count>{num})\s+(?:bottles?|jars?|units?|pieces?)\s+(?:of|each)?\s*(?P<qty>{num})?\s*(?P<unit>{unit})?\b",
        rf"\b(?P<count>{num})\s*(?:pack|pk|case|bundle)\s+(?:of\s+)?(?P<qty>{num})?\s*(?P<unit>{unit})?\b",
    ]
    for pattern in composition_patterns:
        match = re.search(pattern, t)
        if not match:
            continue
        count = _number(re.sub(r"\s+", "", match.group("count") or ""))
        unit_name = _canonical_unit(match.groupdict().get("unit"))
        qty = _number(re.sub(r"\s+", "", match.groupdict().get("qty") or ""))
        if count is None:
            continue
        if qty is not None and unit_name:
            total, total_unit = _to_optimizer_unit(product_type, count * qty, unit_name)
            display = f"{_fmt_number(count)} x {_fmt_number(qty)} {unit_name}"
            return QuantityFact(
                raw_text=raw_text,
                item_count=count,
                unit_quantity=qty,
                unit_name=unit_name,
                total_equivalent_quantity=total,
                total_equivalent_unit=total_unit,
                display_text=display,
                is_bundle_or_pack=True,
                confidence=0.9,
                evidence=[match.group(0)],
            )
        return QuantityFact(
            raw_text=raw_text,
            item_count=count,
            display_text=f"{_fmt_number(count)} pack",
            is_bundle_or_pack=True,
            confidence=0.5,
            evidence=[match.group(0)],
            missing_reason="no_unit_size_evidence",
        )

    # Simple size/unit.
    simple = re.search(rf"\b(?P<qty>{num})\s*(?P<unit>{unit})\b", t)
    if simple:
        parsed = _unit_phrase(simple)
        if parsed:
            qty, unit_name, raw_data = parsed
            total, total_unit = _to_optimizer_unit(product_type, qty, unit_name)
            display_qty = qty
            display_unit = unit_name
            if unit_name == "dozen":
                display_qty = qty
            display = f"{_fmt_number(display_qty)} {display_unit}"
            item_count = None
            unit_quantity = qty
            if is_bundle and qty > 1 and unit_name not in {"count", "dozen"}:
                item_count = qty
                unit_quantity = 1.0
                display = f"{_fmt_number(qty)} x 1 {display_unit}"
            if display_unit == "count":
                display = f"{_fmt_number(total)} count"
            return QuantityFact(
                raw_text=raw_text,
                item_count=item_count,
                unit_quantity=unit_quantity,
                unit_name=unit_name,
                total_equivalent_quantity=total,
                total_equivalent_unit=total_unit,
                display_text=display,
                is_bundle_or_pack=is_bundle,
                confidence=0.82,
                evidence=[raw_data],
            )

    # Bare unit words with implied one.
    bare = re.search(rf"\b(?P<unit>{unit})\b", t)
    if bare:
        unit_name = _canonical_unit(bare.group("unit"))
        if unit_name:
            total, total_unit = _to_optimizer_unit(product_type, 1.0, unit_name)
            return QuantityFact(
                raw_text=raw_text,
                unit_quantity=1.0,
                unit_name=unit_name,
                total_equivalent_quantity=total,
                total_equivalent_unit=total_unit,
                display_text=f"1 {unit_name}",
                is_bundle_or_pack=is_bundle,
                confidence=0.65,
                evidence=[bare.group(0)],
            )

    return QuantityFact(
        raw_text=raw_text,
        is_bundle_or_pack=is_bundle,
        confidence=0.0,
        evidence=evidence,
        missing_reason="no_quantity_evidence",
    )


def parse_price(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)|(?:^|\s)([\d,]+(?:\.\d{2}))", str(value))
    if not match:
        return None
    raw_data = match.group(1) or match.group(2)
    return float(raw_data.replace(",", ""))


def parse_price_fact(text: str | None) -> PriceFact:
    raw_data = text or ""
    prices = [float(item.replace(",", "")) for item in re.findall(r"\$\s*([\d,]+(?:\.\d{1,2})?)", raw_data)]
    lower = raw_data.lower()
    regular = None
    sale = None
    current = prices[-1] if prices else None
    reg_match = re.search(r"(?:regular|was|reg\.?)\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", lower)
    sale_match = re.search(r"(?:sale|now)\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", lower)
    if reg_match:
        regular = float(reg_match.group(1).replace(",", ""))
    if sale_match:
        sale = float(sale_match.group(1).replace(",", ""))
        current = sale
    return PriceFact(
        raw_price_text=raw_data if prices or regular or sale else None,
        current_price=current,
        regular_price=regular,
        sale_price=sale,
        evidence=[raw_data] if raw_data and (prices or regular or sale) else [],
    )


def parse_savings(text: str | None, price: PriceFact | None = None) -> SavingsFact:
    raw_data = text or ""
    lower = raw_data.lower()
    amount = None
    percent = None
    savings_type = "unknown"
    if match := re.search(r"(?:save|savings?|you save)\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", lower):
        amount = float(match.group(1).replace(",", ""))
        savings_type = "explicit_text"
    if match := re.search(r"(\d+(?:\.\d+)?)\s*%\s*off", lower):
        percent = float(match.group(1))
        savings_type = "explicit_text"
    if amount is None and price and price.regular_price is not None and price.current_price is not None:
        amount = round(price.regular_price - price.current_price, 2)
        savings_type = "computed_regular_minus_sale"
    return SavingsFact(
        raw_text=raw_data if amount is not None or percent is not None else None,
        savings_amount=amount,
        savings_percent=percent,
        savings_type=savings_type,
        evidence=[raw_data] if raw_data and (amount is not None or percent is not None) else [],
    )


def understand_offer(
    *,
    parent_product_name: str,
    offer_name: str | None = None,
    option_label: str | None = None,
    raw_offer_text: str,
    product_type_hint: str | None = None,
    species_hint: str | None = None,
    stock_signal: str | None = None,
) -> OrderableOffer:
    price = parse_price_fact(raw_offer_text)
    savings = parse_savings(raw_offer_text, price)
    quantity = parse_quantity(raw_offer_text, product_type_hint)
    incomplete = []
    if quantity.missing_reason:
        incomplete.append(quantity.missing_reason)
    if price.current_price is None:
        incomplete.append("no_price_evidence")
    confidence = 0.5
    if price.current_price is not None:
        confidence += 0.2
    if quantity.total_equivalent_quantity is not None:
        confidence += 0.2
    if not incomplete:
        confidence += 0.1
    return OrderableOffer(
        parent_product_name=parent_product_name,
        offer_name=offer_name or option_label or parent_product_name,
        option_label=option_label,
        raw_offer_text=raw_offer_text,
        product_type_hint=product_type_hint,
        species_hint=species_hint,
        stock_signal=stock_signal,
        price=price,
        quantity=quantity,
        savings=savings,
        evidence_fields_used=["raw_offer_text"],
        confidence=min(confidence, 1.0),
        incomplete_flags=incomplete,
        skipped_reason=None if not incomplete else ",".join(incomplete),
    )
