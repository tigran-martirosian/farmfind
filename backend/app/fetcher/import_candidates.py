"""Create and review product import candidates from captured variants."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.display import display_name as build_display_name
from app.display import clean_base_name, filter_attributes, friendly_attribute_value, visible_tags
from app.models import Product
from app.schemas import ImportedAttribute, ImportedProductCandidate

from .models import CapturedProductPage
from .offer_validation import REQUIRED_CHECKPOINTS, build_offer_evidence, recover_incomplete_offer, validate_offer
from .package_parser import parse_quantity
from .storage import (
    DATA_DIR,
    FETCHES_DIR,
    read_json_list_resilient,
    safe_path_part,
    write_json_atomic,
)

IMPORT_CANDIDATES_DIR = DATA_DIR / "import_candidates"
STAGED_PRODUCTS_PATH = DATA_DIR / "staged_products.json"
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{6}")
_RAW_ATTRIBUTE_RE = re.compile(
    r"(attribute_[\w-]+|chilled\??|size|container|use-rennet|fat[\s-]?content)\s*:\s*([^,\n]+)",
    flags=re.IGNORECASE,
)
_SIZE_TEXT_RE = re.compile(
    r"\b((?:case|pack|pk|bundle|box|tray)\s+of\s+\d+\s+(?:dozen|count|ct)|"
    r"\d+\s+(?:dozen|count|ct)\s+(?:case|pack|pk|bundle|box|tray)|"
    r"\d+\s*(?:x|×)\s*(?:\d+\s*/\s*\d+|\d+(?:\.\d+)?|half)\s*(?:gallons?|gal\.?|quarts?|qt\.?|pints?|pt\.?|pounds?|lbs?|lb|oz|ounces?)|"
    r"\d+\s+(?:half|1/2)\s*(?:gallons?|gal)|"
    r"\d+\s+per\s+pack|half\s+gallon|1/2\s*(?:gallon|gal)|"
    r"\d+(?:\.\d+)?\s*(?:gallons?|gal\.?|quarts?|qt\.?|pints?|pt\.?|pounds?|lbs?|lb|oz|ounces?|count|ct)|"
    r"\d+\s*/\s*\d+\s*(?:lb|lbs|pound|pounds)?|quart|pint|dozen|"
    r"1\s+pound|1\s+lb|\d+\s+dozen)\b",
    flags=re.IGNORECASE,
)

# Default "plain products only" exclusions. A listing whose text contains one of
# the keywords is excluded, and the key is recorded as the exclusion reason.
# "frozen", "ice_cream", "sour_cream", "cultured" and "ghee" apply to every
# listing; "flavored" applies to butter and cheese.
PLAIN_ONLY_EXCLUDED_KEYWORDS = {
    "frozen": ("frozen", "freeze dried", "freeze-dried"),
    "ice_cream": ("ice cream",),
    "sour_cream": ("sour cream",),
    "cultured": ("yogurt", "kefir", "cultured", "fermented"),
    "ghee": ("ghee",),
    "flavored": (
        "chocolate", "vanilla", "garlic", "herb", "herbed", "honey", "cinnamon", "pepper", "peppercorn",
        "tomato", "basil", "smoked", "spicy", "yogurt cheese", "flavored",
    ),
}
SUPERSEDED_BY_OPTION_ROW_TOTAL_PRICE = "superseded_by_option_row_total_price"

PLAIN_CHEESE_WORDS = {
    "cheddar",
    "colby",
    "gouda",
    "gruyere",
    "mozzarella",
    "swiss",
    "camembert",
    "cottage cheese",
}

# Prepared / composite foods that must never be classified as a plain single-
# ingredient product. Checked before "egg"/"milk"/"cream" substring matching so
# "Eggnog"/"Egg Custard" do not become eggs and "Protein Shake" is not a drink.
PREPARED_EXCLUDED = {
    "eggnog",
    "egg nog",
    "custard",
    "mayonnaise",
    "mayo",
    "protein shake",
    "pudding",
}


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def candidate_output_dir(vendor_id: str, stamp: str | None = None) -> Path:
    return IMPORT_CANDIDATES_DIR / safe_path_part(vendor_id) / (stamp or now_stamp())


def latest_variant_snapshot(vendor_id: str, page_id: str) -> Path | None:
    root = FETCHES_DIR / safe_path_part(vendor_id) / safe_path_part(page_id)
    if not root.exists():
        return None
    paths = sorted(root.glob("*/variant_snapshots.json"), reverse=True)
    return paths[0] if paths else None


def create_import_candidates_from_variant_snapshot(
    variant_snapshots_path: str | Path,
) -> Path:
    from app.services import validate_import_candidate

    path = Path(variant_snapshots_path)
    captured = CapturedProductPage(**json.loads(path.read_text(encoding="utf-8")))
    # Include page_id and a short UUID so two page runs that land in the same
    # second do not overwrite each other's candidates.json.
    stamp = f"{safe_path_part(captured.page_id)}_{now_stamp()}_{uuid4().hex[:8]}"
    output_dir = candidate_output_dir(captured.vendor_id, stamp)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[ImportedProductCandidate] = []
    for index, variant in enumerate(captured.variants, start=1):
        if variant.offer_evidence is None:
            variant = variant.model_copy(update={"offer_evidence": build_offer_evidence(captured, variant)})
        validation = validate_offer(variant)
        if not validation.complete:
            variant, validation = recover_incomplete_offer(captured, variant)
        else:
            variant = variant.model_copy(
                update={
                    "evidence_checkpoints": validation.evidence_checkpoints,
                    "validation_reasons": [],
                    "incomplete_discovery": False,
                    "incomplete_reason": None,
                }
            )
        attrs = ", ".join(f"{item.name}: {item.value}" for item in variant.attributes)
        evidence = variant.offer_evidence
        evidence_text = "\n".join(
            item
            for item in [
                evidence.identity_text if evidence else None,
                evidence.package_text if evidence else None,
                evidence.classification_text if evidence else None,
            ]
            if item
        )
        raw_text = "\n".join(
            item
            for item in [captured.product_title or "", attrs, variant.stock_text or "", evidence_text]
            if item
        )
        package_display = _quantity_display_from_evidence(variant.quantity_evidence)
        missing_reasons = list(validation.missing_reasons)
        if (
            variant.discovery_source == "option_row"
            and _is_bundle_display(package_display)
            and variant.price is None
            and "missing_price_from_option_row" not in missing_reasons
        ):
            missing_reasons.append("missing_price_from_option_row")
        # Keep only the attributes the source page exposed, without the redundant Size, Container and Option ones.
        exposed_attributes = [
            ImportedAttribute(**item) for item in filter_attributes(list(variant.attributes))
        ]
        candidate = ImportedProductCandidate(
            import_id=f"{captured.vendor_id}_{captured.page_id}_{index}_{now_stamp()}",
            vendor_id=captured.vendor_id,
            source_page_id=captured.page_id,
            source_url=captured.final_url or captured.source_url,
            source_artifact_path=str(path),
            product_type=variant.inferred_product_type,
            name=f"{variant.product_title} {attrs}".strip(),
            detected_price=variant.price,
            detected_package_size=variant.inferred_package_size,
            detected_unit=variant.inferred_unit,
            packaging=variant.inferred_packaging,
            storage_state=variant.inferred_storage_state or "unknown",
            stock_status=variant.stock_status,
            stock_quantity=variant.stock_quantity,
            parser_confidence=variant.confidence,
            raw_text=raw_text,
            screenshot_path=captured.screenshot_full_path,
            needs_review=False,
            missing_fields=missing_reasons,
            warnings=variant.warnings,
            review_notes=[note for note in [variant.incomplete_reason] if note],
            evidence_checkpoints=variant.evidence_checkpoints,
            validation_reasons=missing_reasons,
            attributes=exposed_attributes,
            package_display=package_display,
            quantity_evidence=variant.quantity_evidence,
            display_name=_display_name_with_package(
                variant.product_title,
                package_display=package_display,
                quantity=variant.inferred_package_size,
                unit=variant.inferred_unit,
                packaging=variant.inferred_packaging,
                storage_state=variant.inferred_storage_state,
            ),
            display_tags=visible_tags(
                packaging=variant.inferred_packaging,
                storage_state=variant.inferred_storage_state,
                attributes=list(variant.attributes),
            ),
        )
        candidates.append(validate_import_candidate(candidate))
    output_path = output_dir / "candidates.json"
    write_json_atomic(output_path, [candidate.model_dump(mode="json") for candidate in candidates])
    return output_path


def _candidate_files(vendor_id: str | None = None) -> list[Path]:
    root = IMPORT_CANDIDATES_DIR / safe_path_part(vendor_id) if vendor_id else IMPORT_CANDIDATES_DIR
    if not root.exists():
        return []
    return sorted(root.rglob("candidates.json"), reverse=True)


def _file_freshness(path: Path) -> str:
    haystack = " ".join(part for part in path.parts[-4:])
    matches = _TIMESTAMP_RE.findall(haystack)
    return max(matches) if matches else f"{path.stat().st_mtime_ns:020d}"


def _raw_attributes(candidate: ImportedProductCandidate) -> list[ImportedAttribute]:
    seen: set[tuple[str, str]] = set()
    attrs: list[ImportedAttribute] = []
    for attribute in candidate.attributes:
        key = (attribute.label.lower(), attribute.value.lower())
        seen.add(key)
        attrs.append(attribute)
    for text in [candidate.name, candidate.raw_text]:
        for raw_label, value in _RAW_ATTRIBUTE_RE.findall(text or ""):
            label = raw_label.strip()
            cleaned = friendly_attribute_value(label, value.strip())
            if not cleaned:
                continue
            key = (label.lower(), value.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            attrs.append(ImportedAttribute(label=label, value=value.strip()))
    return attrs


def _attribute_value(candidate: ImportedProductCandidate, label: str) -> str | None:
    wanted = label.strip().lower()
    for attribute in _raw_attributes(candidate):
        clean = attribute.label.strip().lower()
        if clean == wanted:
            return attribute.value
        if clean.startswith("attribute_") and clean.endswith(wanted.replace(" ", "-")):
            return attribute.value
    return None


def _source_text(candidate: ImportedProductCandidate) -> str:
    attr_text = " ".join(f"{attr.label}: {attr.value}" for attr in _raw_attributes(candidate))
    return " ".join(
        item
        for item in [
            candidate.name or "",
            attr_text,
        ]
        if item
    )


def _classify_text(candidate: ImportedProductCandidate) -> str:
    """Text used for product-type/eligibility/salt/species classification.

    Deliberately excludes source_page_id: a listing page slug like
    "butter_cheese" or "dairy_page" must not make a butter/cream product read
    as cheese (this also protects against fetch tab-reuse cross-contamination)."""
    attr_text = " ".join(f"{attr.label}: {attr.value}" for attr in _raw_attributes(candidate))
    return " ".join(item for item in [candidate.name or "", attr_text] if item)


def _value(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = re.sub(r"\s+", " ", str(item).strip())
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _normal_review_notes(items: list[str]) -> list[str]:
    blocked_prefixes = (
        "evidence_checkpoints=",
        "recovery_checked=",
        "original price",
        "current price",
        "regular price",
        "sale price",
    )
    return [
        item
        for item in _dedupe(items)
        if not item.lower().startswith(blocked_prefixes)
    ]


def _contains(text: str, *words: str) -> bool:
    return any(word in text for word in words)


def _species(text: str, product_type: str | None) -> str:
    if "sheep" in text:
        return "sheep"
    if "duck" in text:
        return "duck"
    if "quail" in text:
        return "quail"
    if "egg" in text:
        return "chicken"
    if product_type in {"cow_milk", "cream", "butter", "cheese"} or "cow" in text:
        return "cow"
    return "unknown"


def _processing_tags(text: str) -> list[str]:
    tags: list[str] = []
    # "salted" must not match inside "unsalted" or "no-salt", so check for a whole word.
    salted = bool(re.search(r"(?<!un)\bsalted\b", text)) and not _contains(text, "unsalted", "no-salt", "no salt")
    for tag, markers in {
        "frozen": PLAIN_ONLY_EXCLUDED_KEYWORDS["frozen"],
        "cultured": PLAIN_ONLY_EXCLUDED_KEYWORDS["cultured"],
        "flavored": PLAIN_ONLY_EXCLUDED_KEYWORDS["flavored"],
        "cooked": ["cooked", "baked", "roasted"],
    }.items():
        if any(marker in text for marker in markers):
            tags.append(tag)
    if salted:
        tags.append("salted")
    return tags


def _package_from_text(product_type: str | None, text: str) -> tuple[float | None, str | None, str | None]:
    effective_type = product_type
    lower = text.lower()
    if effective_type is None:
        if "whey" in lower:
            effective_type = "whey"
        elif "colostrum" in lower:
            effective_type = "colostrum"
    option_text = text
    if "option:" in lower:
        option_text = re.split(r"option\s*:", text, maxsplit=1, flags=re.IGNORECASE)[1]
    fact = parse_quantity(option_text, effective_type)
    if fact.total_equivalent_quantity is None:
        fact = parse_quantity(text, effective_type)
    return fact.total_equivalent_quantity, fact.total_equivalent_unit, fact.display_text


def _quantity_display_from_evidence(evidence: dict | None) -> str | None:
    display = (evidence or {}).get("display_text")
    return str(display) if display else None


def _is_bundle_display(display: str | None) -> bool:
    value = (display or "").lower()
    return " x " in value or "bundle" in value or "case of" in value


def _display_name_with_package(
    name: str,
    *,
    package_display: str | None,
    quantity: float | None,
    unit: str | None,
    packaging: str | None,
    storage_state: str | None,
    attributes: list | None = None,
) -> str:
    if not _is_bundle_display(package_display):
        return build_display_name(
            name,
            quantity=quantity,
            unit=unit,
            packaging=packaging,
            storage_state=storage_state,
            attributes=attributes,
        )
    base = clean_base_name(name) or name.strip()
    parts = [package_display]
    if packaging:
        parts.append(packaging)
    return f"{base} — {', '.join(parts)}"


def _matching_package_prices(text: str, product_type: str | None, package_display: str | None) -> list[float]:
    if not text or not _is_bundle_display(package_display):
        return []
    matches = list(re.finditer(r"\$\s*([\d,]+(?:\.\d{1,2})?)", text))
    prices: list[float] = []
    for match in matches:
        start = max(0, match.start() - 160)
        snippet = text[start : match.start()]
        fact = parse_quantity(snippet, product_type)
        if fact.display_text == package_display:
            prices.append(float(match.group(1).replace(",", "")))
    return prices


def _is_base_price_bundle_hybrid(candidate: ImportedProductCandidate, package_display: str | None) -> bool:
    if candidate.review_status == "approved" or not _is_bundle_display(package_display):
        return False
    if candidate.detected_price is None:
        return False
    text = " ".join(item for item in [candidate.name, candidate.raw_text] if item)
    matching_prices = _matching_package_prices(text, _value(candidate.product_type), package_display)
    if not matching_prices:
        return False
    price = round(candidate.detected_price, 2)
    return any(round(match, 2) != price for match in matching_prices)


_UNIT_TOKEN_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?\s*)?(?:half\s+gallon|1/2\s+gallon|gallons?|quarts?|pints?|"
    r"lbs?|oz|ounces?|dozen|count|case of\s+\d+\s+dozen)$",
    flags=re.IGNORECASE,
)


def _is_unit_token(tag: str) -> bool:
    return bool(_UNIT_TOKEN_RE.match((tag or "").strip()))


def _relevant_type(text: str) -> str | None:
    """A meaningful family for optimizer-unsupported products (colostrum/whey)."""
    if "colostrum" in text:
        return "colostrum"
    if re.search(r"\bwhey\b", text):
        return "whey"
    return None


def _is_flavored_milk(text: str) -> bool:
    """Chocolate/flavored milk in any common spelling (incl. the "Choc." abbrev)."""
    if "milk chocolate" in text:
        return True
    if re.search(r"\bchoc(?:\.|olate)?\s*milk", text):
        return True
    if re.search(r"\bflavou?red\s+milk", text):
        return True
    return False


def _candidate_product_type(text: str, existing: str | None) -> str | None:
    if _contains(text, "tumbler", "merch", "t-shirt", "shirt", "mug"):
        return None
    if _contains(text, *PREPARED_EXCLUDED):
        return None
    if "colostrum" in text:
        return None
    if re.search(r"\bwhey\b", text):
        return None
    if "egg" in text:
        return "eggs"
    if "sheep" in text and "milk" in text:
        return "sheep_milk"
    if _is_flavored_milk(text):
        return None
    if "milk" in text:
        return "cow_milk"
    if "cheese" in text or any(word in text for word in PLAIN_CHEESE_WORDS):
        return "cheese"
    if _contains(text, "sour cream", "ice cream", "yogurt", "kefir"):
        return None
    if "cream" in text:
        return "cream"
    if "butter" in text and "buttermilk" not in text:
        return "butter"
    return existing


def _eligibility(text: str, product_type: str | None) -> tuple[str, str | None, str | None, list[str]]:
    notes: list[str] = []
    if _contains(text, "tumbler", "merch", "t-shirt", "shirt", "mug"):
        return "excluded", "non_food_merchandise", "non_food_merchandise", notes
    if _contains(text, *PREPARED_EXCLUDED):
        return "excluded", "prepared_or_flavored", "prepared_or_flavored", notes
    if _is_flavored_milk(text):
        return "excluded", "flavored_milk", "flavored_milk", notes
    for reason, keywords in PLAIN_ONLY_EXCLUDED_KEYWORDS.items():
        if reason == "flavored":
            continue
        if _contains(text, *keywords):
            return "excluded", reason, reason, notes
    if "butter" in text or "cheese" in text or any(word in text for word in PLAIN_CHEESE_WORDS) or product_type == "cheese":
        if _contains(text, *PLAIN_ONLY_EXCLUDED_KEYWORDS["flavored"]):
            return "excluded", "flavored", "flavored", notes
    if "colostrum" in text:
        return "needs_review", "unsupported_optimizer_product_type", None, notes
    if re.search(r"\bwhey\b", text):
        return "needs_review", "unsupported_optimizer_product_type", None, notes
    if product_type is None:
        return "excluded", "irrelevant_or_unsupported_product", "irrelevant_or_unsupported_product", notes
    # Eligible items carry no reason, so the page does not show a review label for them.
    return "eligible", None, None, notes


def _deterministic_product_type(candidate: ImportedProductCandidate):
    # Classify from the product name/text only; the page slug is an unreliable
    # hint (and can be cross-contaminated by tab reuse), used only as a fallback.
    text = _classify_text(candidate).lower()
    page_id = (candidate.source_page_id or "").lower()
    existing = _value(candidate.product_type)
    if _relevant_type(text):
        return None
    inferred = _candidate_product_type(text, existing)
    if inferred is not None:
        return inferred
    if "farm_eggs" in page_id or "farm eggs" in text:
        return "eggs"
    if "cow_butter" in page_id or "butter" in text or "cow butter" in text:
        return "butter"
    if "cream" in page_id or "cream" in text:
        return "cream"
    if "cheese" in page_id or "no-salt cheese" in text:
        return "cheese"
    return inferred


def _deterministic_size_text(candidate: ImportedProductCandidate) -> str:
    attr_size = _attribute_value(candidate, "size")
    if attr_size:
        return attr_size
    match = _SIZE_TEXT_RE.search(_source_text(candidate))
    return match.group(1) if match else ""


def _deterministic_package(candidate: ImportedProductCandidate) -> tuple[float | None, str | None]:
    evidence = candidate.quantity_evidence or {}
    if evidence.get("total_equivalent_quantity") is not None and evidence.get("total_equivalent_unit") is not None:
        return evidence.get("total_equivalent_quantity"), evidence.get("total_equivalent_unit")
    product_type = _deterministic_product_type(candidate)
    text = _source_text(candidate)
    parsed_quantity, parsed_unit, _ = _package_from_text(_value(product_type), text)
    if parsed_quantity is not None and parsed_unit is not None:
        return parsed_quantity, parsed_unit
    if candidate.detected_package_size is not None and candidate.detected_unit is not None:
        return candidate.detected_package_size, _value(candidate.detected_unit)
    size = _deterministic_size_text(candidate)
    fact = parse_quantity(size, _value(product_type)) if size else None
    if fact and fact.total_equivalent_quantity is not None and fact.total_equivalent_unit is not None:
        return fact.total_equivalent_quantity, fact.total_equivalent_unit
    return (
        candidate.detected_package_size,
        _value(candidate.detected_unit),
    )


def _deterministic_packaging(candidate: ImportedProductCandidate) -> str | None:
    if candidate.packaging is not None:
        return candidate.packaging
    container = (_attribute_value(candidate, "container") or "").strip().lower()
    if container in {"glass", "plastic"}:
        return container
    return candidate.packaging


def _refreshed_candidate(candidate: ImportedProductCandidate) -> ImportedProductCandidate:
    from app.services import validate_import_candidate

    raw_attrs = _raw_attributes(candidate)
    source_text = _source_text(candidate)
    # Classification (type/eligibility/salt/species/processing) must ignore the
    # page slug so it is not polluted by category names or tab-reuse.
    source_lower = _classify_text(candidate).lower()
    product_type = _deterministic_product_type(candidate)
    detected_package_size, detected_unit = _deterministic_package(candidate)
    _, _, parsed_package_display = _package_from_text(_value(product_type), source_text)
    package_display = _quantity_display_from_evidence(candidate.quantity_evidence) or parsed_package_display
    if package_display is None and detected_package_size is not None and detected_unit is not None:
        package_display = build_display_name(
            "",
            quantity=detected_package_size,
            unit=detected_unit,
        ).strip(" â€”,") or None
    packaging = _deterministic_packaging(candidate)
    eligibility_status, eligibility_reason, exclusion_reason, eligibility_notes = _eligibility(source_lower, _value(product_type))
    if _is_base_price_bundle_hybrid(candidate, package_display):
        eligibility_status = "excluded"
        eligibility_reason = SUPERSEDED_BY_OPTION_ROW_TOTAL_PRICE
        exclusion_reason = SUPERSEDED_BY_OPTION_ROW_TOTAL_PRICE
        eligibility_notes.append(SUPERSEDED_BY_OPTION_ROW_TOTAL_PRICE)
    species = _species(source_lower, _value(product_type))
    processing_tags = _processing_tags(source_lower)
    freshness_status = "frozen" if "frozen" in processing_tags else "fresh_or_unspecified"
    salt_status = (
        "unsalted"
        if any(marker in source_lower for marker in ["unsalted", "no-salt", "no salt"])
        else "salted"
        if "salted" in source_lower
        else "unknown"
    )
    use_package_display_only = _is_bundle_display(package_display) or (
        _value(product_type) == "cheese" and package_display
    )
    display_quantity = None if use_package_display_only else detected_package_size
    display_unit = None if use_package_display_only else detected_unit
    display_name = _display_name_with_package(
        candidate.name,
        package_display=package_display,
        quantity=display_quantity,
        unit=display_unit,
        packaging=packaging,
        storage_state=candidate.storage_state,
        attributes=raw_attrs,
    )
    relevant_type = _relevant_type(source_lower)
    display_product_type = _value(product_type) or relevant_type
    display_tags = visible_tags(
        packaging=packaging,
        storage_state=candidate.storage_state,
        attributes=raw_attrs,
    )
    # Details must not duplicate the Package column: drop package_display and any
    # bare unit token (gallon/quart/pint/lb/oz/"N dozen"/"case of N dozen").
    display_tags = [tag for tag in display_tags if tag != package_display and not _is_unit_token(tag)]
    # Colostrum/whey (optimizer-unsupported) still get a clear type + species tag.
    if relevant_type:
        prepend = [
            tag
            for tag in [relevant_type, species if species and species != "unknown" else None]
            if tag and tag not in display_tags
        ]
        display_tags = prepend + display_tags
    if eligibility_status == "needs_review" and eligibility_reason:
        display_tags.append(eligibility_reason.replace("_", " "))
    filtered = [
        ImportedAttribute(label=item["label"], value=friendly_attribute_value(item["label"], item["value"]) or item["value"])
        for item in filter_attributes(raw_attrs)
    ]
    review_notes = _normal_review_notes(list(candidate.review_notes) + eligibility_notes)
    warnings = _dedupe(list(candidate.warnings))
    missing_fields = list(candidate.missing_fields)
    if _value(product_type) == "eggs" and detected_package_size is None:
        checked = set(candidate.evidence_checkpoints)
        reason = (
            "quantity_evidence_not_found_after_full_inspection"
            if set(REQUIRED_CHECKPOINTS) <= checked
            else "quantity_inspection_incomplete"
        )
        if "quantity_evidence_not_found_after_full_inspection" not in missing_fields:
            missing_fields.append(reason)
        review_notes.append(reason)
    if eligibility_status == "needs_review" and eligibility_reason:
        review_notes.append(eligibility_reason)
    data = candidate.model_dump(mode="json")
    data.update(
        {
            "product_type": product_type,
            "detected_package_size": detected_package_size,
            "detected_unit": detected_unit,
            "packaging": packaging,
            "display_name": display_name,
            "display_tags": _dedupe(display_tags),
            # Display tags now carry the cleaned source-exposed choices. Keeping
            # this empty prevents the UI from rendering unformatted "Rennet: No" chips.
            "attributes": filtered if not display_tags else [],
            "species": species,
            "relevant_type": relevant_type,
            "display_product_type": display_product_type,
            "package_display": package_display,
            "processing_tags": processing_tags,
            "freshness_status": freshness_status,
            "salt_status": salt_status,
            "eligibility_status": eligibility_status,
            "eligibility_reason": eligibility_reason,
            "exclusion_reason": exclusion_reason,
            "review_notes": _normal_review_notes(review_notes),
            "warnings": warnings,
            "missing_fields": _dedupe(missing_fields),
            "evidence_checkpoints": _dedupe(list(candidate.evidence_checkpoints)),
            "validation_reasons": _dedupe(list(candidate.validation_reasons)),
        }
    )
    refreshed = ImportedProductCandidate(**data)
    return validate_import_candidate(refreshed)


def _variant_key(candidate: ImportedProductCandidate) -> tuple:
    raw_attrs = _raw_attributes(candidate)
    attr_signature = tuple(
        sorted(
            (
                attr.label.strip().lower(),
                attr.value.strip().lower().replace("_", " ").replace("-", " "),
            )
            for attr in raw_attrs
        )
    )
    fallback_signature = (
        _value(candidate.product_type),
        candidate.species,
        candidate.detected_price,
        candidate.detected_package_size,
        _value(candidate.detected_unit),
        candidate.package_display,
        candidate.packaging,
        candidate.storage_state,
        candidate.stock_status,
    )
    # Key on product identity only, not on source_page_id or source_url.
    # When a fetch reuses the same active tab across configured pages, the same
    # product otherwise appears once per polluted category page; keying on the
    # product collapses those cross-page duplicates into one.
    return (
        candidate.vendor_id,
        clean_base_name(candidate.name).lower(),
        _value(candidate.product_type) or candidate.relevant_type or candidate.display_product_type,
        candidate.species,
        candidate.detected_package_size,
        _value(candidate.detected_unit),
        candidate.package_display,
        candidate.packaging,
        candidate.detected_price,
        candidate.stock_status,
        attr_signature or fallback_signature,
    )


def _identity_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").lower().replace("-", " ")).strip()


def _approved_product_key(product: Product) -> tuple:
    return (
        product.vendor_id,
        _identity_text(clean_base_name(product.product_name)),
        _value(product.canonical_product),
        product.package_quantity,
        _value(product.package_unit),
        product.packaging,
        product.storage_state,
        round(product.price, 2),
    )


def _candidate_approved_key(candidate: ImportedProductCandidate) -> tuple:
    return (
        candidate.vendor_id,
        _identity_text(clean_base_name(candidate.name)),
        _value(candidate.product_type) or candidate.relevant_type or candidate.display_product_type,
        candidate.detected_package_size,
        _value(candidate.detected_unit),
        candidate.packaging,
        candidate.storage_state,
        round(candidate.detected_price, 2) if candidate.detected_price is not None else None,
    )


def _approved_product_keys() -> set[tuple]:
    if (
        IMPORT_CANDIDATES_DIR != DATA_DIR / "import_candidates"
        and STAGED_PRODUCTS_PATH == DATA_DIR / "staged_products.json"
    ):
        return set()
    return {_approved_product_key(product) for product in load_staged_products()}


def _candidate_needs_review(candidate: ImportedProductCandidate) -> bool:
    return candidate.needs_review or candidate.eligibility_status == "needs_review"


def _status_rank(candidate: ImportedProductCandidate) -> int:
    """Representative-selection priority: approved > ready > needs_review > rejected."""
    if candidate.review_status == "approved":
        return 4
    if candidate.review_status == "rejected":
        return 1
    if candidate.review_status == "pending":
        return 2 if _candidate_needs_review(candidate) else 3
    return 0


def _hide_superseded_pending(
    rows: list[tuple[ImportedProductCandidate, str]],
    *,
    include_excluded: bool = False,
) -> list[ImportedProductCandidate]:
    """Collapse duplicates across all statuses to one representative per variant.

    Rows with the same product identity (see _variant_key) keep the highest-ranked one
    (approved beats ready beats needs_review beats rejected); ties break on the
    freshest capture. Excluded/diagnostic rows are hidden unless requested."""
    best: dict[tuple, tuple[tuple, ImportedProductCandidate]] = {}
    order: list[tuple] = []
    approved_keys = _approved_product_keys()
    for index, (candidate, freshness) in enumerate(rows):
        if candidate.review_status == "pending" and _candidate_approved_key(candidate) in approved_keys:
            if include_excluded:
                candidate = candidate.model_copy(
                    update={
                        "eligibility_status": "excluded",
                        "exclusion_reason": "already_approved_duplicate",
                        "eligibility_reason": "already_approved_duplicate",
                        "review_notes": _dedupe(candidate.review_notes + ["already_approved_duplicate"]),
                    }
                )
            else:
                continue
        if candidate.eligibility_status == "excluded" and not include_excluded:
            continue
        key = _variant_key(candidate)
        score = (_status_rank(candidate), freshness, index)
        current = best.get(key)
        if current is None:
            order.append(key)
            best[key] = (score, candidate)
        elif score > current[0]:
            best[key] = (score, candidate)
    return [best[key][1] for key in order]


def load_import_candidates(
    vendor_id: str | None = None,
    *,
    include_excluded: bool = False,
) -> list[ImportedProductCandidate]:
    rows_with_freshness: list[tuple[ImportedProductCandidate, str]] = []
    for path in _candidate_files(vendor_id):
        freshness = _file_freshness(path)
        for row in read_json_list_resilient(path):
            try:
                candidate = _refreshed_candidate(ImportedProductCandidate(**row))
            except Exception:
                continue
            rows_with_freshness.append((candidate, freshness))
    return _hide_superseded_pending(rows_with_freshness, include_excluded=include_excluded)


def _save_candidate_update(updated: ImportedProductCandidate) -> ImportedProductCandidate:
    for path in _candidate_files(updated.vendor_id):
        rows = read_json_list_resilient(path)
        changed = False
        for index, row in enumerate(rows):
            if row.get("import_id") == updated.import_id:
                rows[index] = updated.model_dump(mode="json")
                changed = True
                break
        if changed:
            write_json_atomic(path, rows)
            return updated
    raise FileNotFoundError(f"Import candidate not found: {updated.import_id}")


def get_import_candidate(import_id: str) -> ImportedProductCandidate:
    for candidate in load_import_candidates():
        if candidate.import_id == import_id:
            return candidate
    raise FileNotFoundError(f"Import candidate not found: {import_id}")


def load_staged_products() -> list[Product]:
    products: list[Product] = []
    for row in read_json_list_resilient(STAGED_PRODUCTS_PATH):
        try:
            products.append(Product(**row))
        except Exception:
            # Skip rows that do not validate, so one bad row cannot break the approval and optimizer pipeline.
            continue
    return products


def save_staged_product(product: Product) -> Product:
    products = [item for item in load_staged_products() if item.id != product.id]
    products.append(product)
    write_json_atomic(
        STAGED_PRODUCTS_PATH,
        [item.model_dump(mode="json") for item in products],
    )
    return product


def approve_import_candidate(import_id: str) -> Product:
    from app.services import normalize_imported_product, validate_import_candidate

    candidate = get_import_candidate(import_id)
    # Prevent duplicate approval/staging: an already-approved candidate must not
    # be staged a second time.
    if candidate.review_status == "approved":
        raise ValueError("Candidate is already approved.")
    if candidate.review_status == "rejected":
        raise ValueError("Rejected candidate cannot be approved without restore.")
    checked = validate_import_candidate(candidate)
    if checked.needs_review:
        raise ValueError("Candidate needs review before approval.")
    product = normalize_imported_product(checked)
    if product is None:
        raise ValueError("Candidate cannot be normalized.")
    product = product.model_copy(
        update={
            "source_type": "imported",
            "imported_at": now_iso(),
            "needs_review": False,
            "notes": f"original_import_id={checked.import_id}; source_artifact_path={checked.source_artifact_path or ''}",
        }
    )
    save_staged_product(product)
    _save_candidate_update(checked.model_copy(update={"review_status": "approved", "needs_review": False}))
    return product


def reject_import_candidate(import_id: str) -> ImportedProductCandidate:
    candidate = get_import_candidate(import_id)
    return _save_candidate_update(candidate.model_copy(update={"review_status": "rejected"}))


def edit_import_candidate(import_id: str, updates: dict) -> ImportedProductCandidate:
    from app.services import validate_import_candidate

    candidate = get_import_candidate(import_id)
    update = {key: value for key, value in updates.items() if key in ImportedProductCandidate.model_fields}
    return _save_candidate_update(validate_import_candidate(candidate.model_copy(update=update)))
