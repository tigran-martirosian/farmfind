"""Deterministic, human-readable display formatting for imported products.

Display names and attribute tags must only show values that are actually
relevant to selecting/ordering an item. Internal model fields that default to
"unknown" (chilled, rennet, fat content, container, ...) must NOT leak into the UI
as "unknown chilled" noise. Everything here is pure and offline-testable.
"""
from __future__ import annotations

import re

# Values that mean "the source page did not expose this attribute". They are
# never shown to the user.
UNKNOWN_VALUES = {"", "unknown", "none", "n/a", "na", "unspecified", "-"}

# Attribute labels that are already reflected elsewhere in the display (package
# size / packaging / the base name) and so are redundant as extra chips.
_REDUNDANT_ATTR_LABELS = {"size", "container", "option", "package", "packaging"}

# Source WooCommerce-style attribute name -> clean label.
_ATTR_LABEL_MAP = {
    "attribute_pa_size": "Size",
    "attribute_size": "Size",
    "attribute_pa_container": "Container",
    "attribute_container": "Container",
    "attribute_chilled": "Chilled",
    "chilled?": "Chilled",
    "attribute_use-rennet": "Rennet",
    "use-rennet": "Rennet",
    "attribute_fat-content": "Fat content",
    "fat-content": "Fat content",
}


def is_known(value) -> bool:
    return value is not None and str(value).strip().lower() not in UNKNOWN_VALUES


def clean_attribute_label(label: str | None) -> str:
    if not label:
        return ""
    key = label.strip().lower()
    if key in _ATTR_LABEL_MAP:
        return _ATTR_LABEL_MAP[key]
    cleaned = re.sub(r"^attribute_", "", label.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.replace("pa_", "").replace("-", " ").replace("_", " ").strip()
    cleaned = cleaned.rstrip("?")
    return cleaned[:1].upper() + cleaned[1:] if cleaned else ""


def _num(value: float) -> str:
    return f"{value:g}"


def human_package(quantity: float | None, unit: str | None, *, product_name: str | None = None) -> str:
    """Format a package size the way a shopper would read it.

    Examples: 1.0 gallon -> "1 gallon", 0.5 gallon -> "1/2 gallon",
    0.25 gallon -> "quart", 0.5 lb -> "8 oz", 96 count -> "case of 8 dozen".
    """
    if quantity is None or not is_known(unit):
        return ""
    q = float(quantity)
    u = str(unit).strip().lower()

    if u == "gallon":
        return {1.0: "1 gallon", 0.5: "1/2 gallon", 0.25: "quart", 0.125: "pint"}.get(
            q, f"{_num(q)} gallon"
        )
    if u == "half_gallon":
        gallons = q * 0.5
        return "1/2 gallon" if gallons == 0.5 else f"{_num(gallons)} gallon"
    if u == "quart":
        return "quart" if q == 1 else f"{_num(q)} quart"
    if u == "pint":
        if product_name and "cream" in product_name.lower():
            if q == 2:
                return "quart"
            if q == 4:
                return "half gallon"
        return "1 pint" if q == 1 else f"{_num(q)} pint"
    if u == "lb":
        if q < 1:
            return f"{_num(q * 16)} oz"
        return f"{_num(q)} lb"
    if u == "oz":
        return f"{_num(q)} oz"
    if u in {"count", "dozen", "half_dozen"}:
        count = q * 12 if u == "dozen" else q * 6 if u == "half_dozen" else q
        if count % 12 == 0:
            dozens = int(count // 12)
            if dozens == 1:
                return "1 dozen"
            if dozens >= 4:
                return f"case of {dozens} dozen"
            return f"{dozens} dozen"
        return f"{_num(count)} count"
    return f"{_num(q)} {u}"


def friendly_package_label(quantity: float | None, unit: str | None, *, product_name: str | None = None) -> str:
    label = human_package(quantity, unit, product_name=product_name)
    if label == "1 gallon":
        return "1 gal"
    if label == "1/2 gallon":
        return "1/2 gal"
    if label == "1 pint":
        return "pint"
    return label


def packaging_label(packaging: str | None) -> str | None:
    if not is_known(packaging):
        return None
    value = str(packaging).strip().lower()
    return value if value in {"glass", "plastic"} else None


def storage_label(storage_state: str | None) -> str | None:
    """Chilled or not chilled, only when the site exposed the choice."""
    if not is_known(storage_state):
        return None
    value = str(storage_state).strip().lower()
    if value in {"refrigerated", "chilled", "cold"}:
        return "chilled"
    if value in {"fresh", "not_chilled", "not chilled", "room_temp", "shelf_stable"}:
        return "not chilled"
    return None


_SIZE_PREFIX = re.compile(
    r"^(?:case of\s+)?[\d/.]*\s*"
    r"(?:gallon|gal|quart|qt|pint|pt|dozen|doz|oz|ounce|ounces|lb|lbs|pound|pounds|"
    r"bundle|glass|plastic)s?\b\.?",
    flags=re.IGNORECASE,
)


def _strip_leading_size_tokens(name: str) -> str:
    previous = None
    current = name.strip()
    while current and current != previous:
        previous = current
        current = _SIZE_PREFIX.sub("", current, count=1).strip(" ,-")
    return current or name.strip()


# Only cut at a *known* attribute label so we never mistake a product word
# ("Milk") for the start of an attribute segment.
_ATTR_CUT = re.compile(
    r"\s(?:size|container|chilled\??|rennet|fat[\s-]?content|option|package|packaging|"
    r"use-?rennet|attribute_[\w-]+)\s*:\s",
    flags=re.IGNORECASE,
)


def clean_base_name(name: str | None) -> str:
    """Strip concatenated attribute segments ("Size: Half Gallon, ...") and
    leading size/packaging tokens so the base product name reads cleanly."""
    if not name:
        return ""
    match = _ATTR_CUT.search(name)
    base = name[: match.start()] if match else name
    base = _strip_leading_size_tokens(base)
    return re.sub(r"\s+", " ", base).strip(" ,-")


def _attribute_pairs(attributes: list | None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for attribute in attributes or []:
        if isinstance(attribute, dict):
            raw_label = attribute.get("label") or attribute.get("name")
            value = attribute.get("value")
        else:
            raw_label = getattr(attribute, "label", None) or getattr(attribute, "name", None)
            value = getattr(attribute, "value", None)
        if not is_known(value):
            continue
        label = clean_attribute_label(raw_label).strip().lower()
        if label:
            pairs.append((label, str(value).strip()))
    return pairs


def friendly_attribute_value(label: str, value: str) -> str | None:
    label_key = clean_attribute_label(label).strip().lower()
    value_key = str(value).strip().lower().replace("_", " ").replace("-", " ")
    value_key = re.sub(r"\s+", " ", value_key)
    if value_key in UNKNOWN_VALUES:
        return None
    if label_key == "chilled":
        if value_key in {"yes", "true", "chilled", "refrigerated"}:
            return "chilled"
        if value_key in {"no", "false", "not chilled", "fresh"}:
            return "not chilled"
        return None
    if label_key == "rennet":
        if value_key in {"no", "none", "without rennet"}:
            return "no rennet"
        if "cheddar" in value_key or value_key in {"yes", "true"}:
            return "cheddar-style rennet"
        return "rennet"
    if label_key == "fat content":
        if value_key in {"full fat", "full"}:
            return "full fat"
        if value_key == "skim":
            return "skim"
        return None
    if label_key in {"size", "container"}:
        return value_key
    return str(value).strip()


def attribute_detail_tags(attributes: list | None) -> list[str]:
    tagged: list[tuple[int, str]] = []
    order = {
        "size": 10,
        "container": 20,
        "fat content": 30,
        "rennet": 40,
        "chilled": 50,
    }
    for label, value in _attribute_pairs(attributes):
        tag = friendly_attribute_value(label, value)
        if tag and tag not in [item[1] for item in tagged]:
            tagged.append((order.get(label, 100), tag))
    return [tag for _, tag in sorted(tagged, key=lambda item: item[0])]


def display_name(
    name: str | None,
    *,
    quantity: float | None = None,
    unit: str | None = None,
    packaging: str | None = None,
    storage_state: str | None = None,
    attributes: list | None = None,
) -> str:
    """Clean display name: base product + package + only known packaging/chilled."""
    base = clean_base_name(name) or (name or "").strip()
    parts: list[str] = []
    package = friendly_package_label(quantity, unit, product_name=base)
    if package:
        parts.append(package)
    attr_tags = attribute_detail_tags(attributes)
    packaging_tag = packaging_label(packaging)
    if not package and packaging_tag and attr_tags and attr_tags[0] in {"pint", "quart"}:
        parts.append(f"{attr_tags[0]} {packaging_tag}")
        attr_tags = [tag for tag in attr_tags[1:] if tag != packaging_tag]
    elif packaging_tag:
        parts.append(packaging_tag)
    chilled_tag = storage_label(storage_state)
    if chilled_tag:
        parts.append(chilled_tag)
    for tag in attr_tags:
        if tag not in parts:
            parts.append(tag)
    if not parts:
        return base
    return f"{base} — {', '.join(parts)}"


def visible_tags(
    *,
    packaging: str | None = None,
    storage_state: str | None = None,
    attributes: list | None = None,
) -> list[str]:
    """Order-relevant tags only. Never emits "unknown ..." placeholders."""
    tags: list[str] = []
    packaging_tag = packaging_label(packaging)
    if packaging_tag:
        tags.append(packaging_tag)
    chilled_tag = storage_label(storage_state)
    if chilled_tag:
        tags.append(chilled_tag)
    for tag in attribute_detail_tags(attributes):
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def filter_attributes(attributes: list) -> list[dict]:
    """Keep only exposed, non-redundant attributes with a clean label.

    ``attributes`` items may be dicts ({"name"/"label", "value"}) or objects with
    ``name``/``value``. Redundant labels (Size, Container, Option) and unknown
    values are dropped so the UI never shows "unknown rennet"-style noise."""
    result: list[dict] = []
    for attribute in attributes:
        if isinstance(attribute, dict):
            raw_label = attribute.get("label") or attribute.get("name")
            value = attribute.get("value")
        else:
            raw_label = getattr(attribute, "label", None) or getattr(attribute, "name", None)
            value = getattr(attribute, "value", None)
        if not is_known(value):
            continue
        label = clean_attribute_label(raw_label)
        if not label or label.strip().lower() in _REDUNDANT_ATTR_LABELS:
            continue
        result.append({"label": label, "value": str(value).strip()})
    return result
