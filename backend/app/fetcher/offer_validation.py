"""Evidence-first offer validation and recovery helpers.

These functions are intentionally tool-like: future vendor agents can call the
same deterministic steps when deciding whether an offer is complete enough to
write as an import candidate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .models import (
    CapturedProductPage,
    CapturedProductVariant,
    OfferEvidenceBundle,
    OfferRecoveryTrace,
)
from .package_parser import parse_price_fact, parse_quantity, parse_savings


REQUIRED_CHECKPOINTS = [
    "product_title",
    "variant_attributes",
    "card_title",
    "card_subtitle",
    "card_full_text",
    "card_hidden_option_text",
    "detail_title",
    "detail_text",
    "option_text",
    "embedded_variation_json",
]


@dataclass
class OfferValidationResult:
    complete: bool
    missing_reasons: list[str] = field(default_factory=list)
    evidence_checkpoints: list[str] = field(default_factory=list)
    recovery_actions: list[str] = field(default_factory=list)


def observe_source_page(captured: CapturedProductPage) -> dict:
    return {
        "vendor_id": captured.vendor_id,
        "source_page_id": captured.page_id,
        "source_url": captured.source_url,
        "final_url": captured.final_url,
        "page_kind": captured.page_kind,
        "description_text": captured.description_text,
        "category_text": captured.category_text,
    }


def find_product_cards(captured: CapturedProductPage):
    return captured.product_cards


def inspect_card_options(card) -> list[str]:
    if not card:
        return []
    return [text for text in [card.hidden_option_text, card.savings_text] if text]


def inspect_product_detail(captured: CapturedProductPage) -> list[str]:
    return [
        text
        for text in [
            captured.product_title,
            captured.description_text,
            captured.category_text,
        ]
        if text
    ]


def interpret_offer_facts(text: str, product_type: str | None):
    quantity = parse_quantity(text, product_type)
    price = parse_price_fact(text)
    savings = parse_savings(text, price)
    return quantity, price, savings


def _clean_snippet(text: str | None, limit: int = 700) -> str:
    return " ".join((text or "").split())[:limit]


def _join_scoped(*items: str | None, limit: int = 2000) -> str | None:
    text = _clean_snippet(" ".join(item for item in items if item), limit)
    return text or None


def _strip_price_text(text: str | None) -> str:
    value = text or ""
    value = re.sub(r"\$\s*[\d,]+(?:\.\d{1,2})?", " ", value)
    value = re.sub(
        r"\b(?:original|current|regular|sale|was|now|save|savings?|you save)\b\s*:?\s*[\d,]+(?:\.\d{1,2})?",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _json_text(raw_data: dict | None) -> str | None:
    if not raw_data:
        return None
    return _clean_snippet(" ".join(str(value) for value in raw_data.values()), 2000) or None


def _variant_text(variant: CapturedProductVariant) -> str:
    attrs = " ".join(f"{item.name}: {item.value}" for item in variant.attributes)
    raw_json = " ".join(str(value) for value in (variant.raw_variation_data or {}).values())[:2000]
    return " ".join(
        item
        for item in [
            variant.product_title,
            variant.option_label,
            attrs,
            variant.price_text,
            variant.stock_text,
            variant.selected_html_excerpt,
            raw_json,
        ]
        if item
    )


def _matching_card(captured: CapturedProductPage, variant: CapturedProductVariant):
    v_title = (variant.product_title or "").lower()
    v_option = (variant.option_label or "").lower()
    for card in captured.product_cards:
        haystack = " ".join(
            item for item in [card.title, card.subtitle, card.full_text] if item
        ).lower()
        if v_title and v_title in haystack:
            return card
        if v_option and (v_option in haystack or haystack in v_option):
            return card
    return None


def build_offer_evidence(
    captured: CapturedProductPage,
    variant: CapturedProductVariant,
) -> OfferEvidenceBundle:
    card = _matching_card(captured, variant)
    attrs = " ".join(f"{item.name}: {item.value}" for item in variant.attributes)
    option_row = " ".join(
        item
        for item in [
            variant.option_label,
            attrs,
        ]
        if item
    )
    identity_text = _join_scoped(variant.product_title, variant.option_label, attrs)
    package_text = _strip_price_text(
        _join_scoped(
            variant.option_label,
            attrs,
            card.subtitle if card else None,
            card.secondary_line if card else None,
            card.hidden_option_text if card else None,
            option_row,
        )
    )
    price_text = _join_scoped(
        variant.price_text,
        None if variant.discovery_source == "option_row" else card.price_text if card else None,
        variant.reported_savings_text,
    )
    classification_text = _join_scoped(variant.product_title, variant.option_label, attrs)
    context_text = _join_scoped(
        card.full_text if card else None,
        captured.product_title,
        captured.description_text,
        captured.category_text,
        variant.selected_html_excerpt,
        _json_text(variant.raw_variation_data),
        limit=3000,
    )
    checkpoints = list(
        dict.fromkeys(
            [
                *variant.evidence_checkpoints,
                "product_title",
                "variant_attributes",
                "option_text",
                "embedded_variation_json",
            ]
        )
    )
    if card:
        checkpoints = list(
            dict.fromkeys(
                [
                    *checkpoints,
                    "card_title",
                    "card_subtitle",
                    "card_secondary_text",
                    "card_full_text",
                    "card_hidden_option_text",
                    "card_price_text",
                    "card_button_text",
                ]
            )
        )
    checkpoints = list(dict.fromkeys([*checkpoints, "detail_title", "detail_text"]))
    return OfferEvidenceBundle(
        vendor_id=captured.vendor_id,
        source_page_id=captured.page_id,
        source_url=captured.source_url,
        final_url=captured.final_url,
        discovery_source=variant.discovery_source,
        product_card_title=card.title if card else None,
        product_card_subtitle=card.subtitle if card else None,
        product_card_secondary_text=card.secondary_line if card else None,
        product_card_text=card.full_text if card else None,
        product_card_hidden_option_text=card.hidden_option_text if card else None,
        product_card_price_text=card.price_text if card else None,
        product_card_button_text=card.order_button_text if card else None,
        product_card_badges=card.badges if card else [],
        product_detail_url=card.product_url if card else None,
        product_detail_title=captured.product_title,
        product_detail_text=captured.description_text or captured.category_text,
        option_label=variant.option_label,
        option_row_text=_clean_snippet(option_row, 1500) or None,
        option_price_text=variant.price_text,
        option_savings_text=variant.reported_savings_text,
        embedded_json_text=_json_text(variant.raw_variation_data),
        evidence_checkpoints_seen=checkpoints,
        parser_warnings=list(dict.fromkeys([*variant.warnings, *captured.warnings])),
        identity_text=identity_text,
        package_text=package_text or None,
        price_text=price_text,
        classification_text=classification_text,
        context_text=context_text,
    )


def _evidence_texts(captured: CapturedProductPage, variant: CapturedProductVariant) -> list[tuple[str, str]]:
    evidence = variant.offer_evidence or build_offer_evidence(captured, variant)
    return [
        ("product_title", variant.product_title or ""),
        ("variant_attributes", _variant_text(variant)),
        ("card_title", evidence.product_card_title or ""),
        ("card_subtitle", evidence.product_card_subtitle or ""),
        ("card_secondary_text", evidence.product_card_secondary_text or ""),
        ("card_full_text", evidence.product_card_text or ""),
        ("card_hidden_option_text", evidence.product_card_hidden_option_text or ""),
        ("detail_title", evidence.product_detail_title or ""),
        ("detail_text", evidence.product_detail_text or ""),
        ("option_text", evidence.option_label or ""),
        ("option_row_text", evidence.option_row_text or ""),
        ("embedded_variation_json", evidence.embedded_json_text or ""),
    ]


def _scoped_recovery_texts(evidence: OfferEvidenceBundle, *, include_context: bool = False) -> list[tuple[str, str]]:
    texts = [
        ("identity_text", evidence.identity_text or ""),
        ("package_text", evidence.package_text or ""),
        ("classification_text", evidence.classification_text or ""),
    ]
    if include_context:
        texts.append(("context_text", evidence.context_text or ""))
    return texts


def extract_orderable_offers(captured: CapturedProductPage) -> list[CapturedProductVariant]:
    return [
        variant.model_copy(
            update={"offer_evidence": variant.offer_evidence or build_offer_evidence(captured, variant)}
        )
        for variant in captured.variants
    ]


def _recovery_actions(missing: list[str]) -> list[str]:
    actions: list[str] = []
    if any(reason in missing for reason in ["missing_quantity", "quantity_evidence_not_found_after_full_inspection"]):
        actions.extend(
            [
                "recheck_title",
                "recheck_subtitle",
                "recheck_full_card_text",
                "recheck_hidden_option_dom",
                "recheck_option_rows",
                "recheck_detail_text",
                "recheck_embedded_variation_json",
            ]
        )
    if "missing_price" in missing or "missing_price_from_option_row" in missing:
        actions.extend(["recheck_card_price", "recheck_option_price", "recheck_detail_price", "recheck_embedded_variation_json"])
    if "missing_product_type" in missing:
        actions.extend(["recheck_title", "recheck_option_label", "recheck_detail_text"])
    return list(dict.fromkeys(actions))


def validate_offer(variant: CapturedProductVariant) -> OfferValidationResult:
    missing: list[str] = []
    evidence = variant.offer_evidence
    checkpoints = list(
        dict.fromkeys(
            [
                *variant.evidence_checkpoints,
                *((evidence.evidence_checkpoints_seen if evidence else []) or []),
            ]
        )
    )
    product_type = variant.inferred_product_type
    if not variant.product_title:
        missing.append("missing_product_name")
    if product_type is None and not any(
        marker in (variant.product_title or "").lower()
        for marker in ["whey", "colostrum"]
    ):
        missing.append("missing_product_type")
    if variant.inferred_package_size is None or variant.inferred_unit is None:
        if set(REQUIRED_CHECKPOINTS) <= set(checkpoints):
            missing.append("quantity_evidence_not_found_after_full_inspection")
        else:
            missing.append("missing_quantity")
    if variant.price is None:
        missing.append("missing_price_from_option_row" if variant.discovery_source == "option_row" else "missing_price")
    if not variant.option_label and not variant.attributes:
        missing.append("missing_offer_identity")
    return OfferValidationResult(
        complete=not missing,
        missing_reasons=missing,
        evidence_checkpoints=checkpoints,
        recovery_actions=_recovery_actions(missing),
    )


def recover_incomplete_offer(
    captured: CapturedProductPage,
    variant: CapturedProductVariant,
) -> tuple[CapturedProductVariant, OfferValidationResult]:
    checkpoints: list[str] = []
    snippets: dict[str, str] = {}
    evidence = variant.offer_evidence or build_offer_evidence(captured, variant)
    for checkpoint, text in [*_evidence_texts(captured, variant), *_scoped_recovery_texts(evidence)]:
        checkpoints.append(checkpoint)
        if text:
            snippet = _clean_snippet(text)
            snippets[checkpoint] = snippet
    package_text = _strip_price_text(evidence.package_text or "")
    identity_text = evidence.identity_text or ""
    classification_text = evidence.classification_text or identity_text
    product_type = variant.inferred_product_type
    if product_type is None:
        lower = classification_text.lower()
        if "whey" in lower:
            product_type = "whey"
        elif "colostrum" in lower:
            product_type = "colostrum"
    quantity = parse_quantity(package_text, product_type)
    if quantity.total_equivalent_quantity is None and not package_text.strip():
        # Context is a last-resort quantity fallback only when the scoped fields
        # have no package text at all. This preserves full-inspection traces
        # without allowing prices/detail blobs to override explicit options.
        for checkpoint, text in _scoped_recovery_texts(evidence, include_context=True):
            checkpoints.append(checkpoint)
            if text:
                snippets.setdefault(checkpoint, _clean_snippet(text))
        quantity = parse_quantity(_strip_price_text(evidence.context_text or ""), product_type)
    price = parse_price_fact(evidence.price_text or variant.price_text)
    savings = parse_savings(evidence.price_text or variant.reported_savings_text or "", price)

    update = {
        "evidence_checkpoints": list(dict.fromkeys([*variant.evidence_checkpoints, *evidence.evidence_checkpoints_seen, *checkpoints])),
        "recovery_attempted": True,
        "offer_evidence": evidence.model_copy(
            update={"evidence_checkpoints_seen": list(dict.fromkeys([*evidence.evidence_checkpoints_seen, *checkpoints]))}
        ),
    }
    if variant.inferred_package_size is None and quantity.total_equivalent_quantity is not None:
        update["inferred_package_size"] = quantity.total_equivalent_quantity
        update["inferred_unit"] = quantity.total_equivalent_unit
        update["quantity_evidence"] = quantity.__dict__
    if variant.price is None and variant.discovery_source != "option_row" and price.current_price is not None:
        update["price"] = price.current_price
        update["total_price"] = price.current_price
        update["price_evidence"] = price.__dict__
    if variant.savings_evidence is None and (savings.savings_amount is not None or savings.savings_percent is not None):
        update["savings_evidence"] = savings.__dict__
    recovered = variant.model_copy(update=update)
    result = validate_offer(recovered)
    stopped_reason = None if result.complete else ",".join(result.missing_reasons)
    trace = OfferRecoveryTrace(
        attempted=True,
        succeeded=result.complete,
        checked_sources=result.evidence_checkpoints,
        raw_text_snippets=snippets,
        missing_fields=result.missing_reasons,
        stopped_reason=stopped_reason,
    )
    recovered = recovered.model_copy(
        update={
            "validation_reasons": result.missing_reasons,
            "recovery_succeeded": result.complete,
            "incomplete_discovery": not result.complete,
            "incomplete_reason": ",".join(result.missing_reasons) if result.missing_reasons else None,
            "recovery_trace": trace,
        }
    )
    return recovered, result


def write_import_candidate():
    """Marker function for the candidate-writing boundary.

    Actual filesystem writes remain in import_candidates.py; keeping this named
    boundary makes the extraction pipeline easier for future agents to inspect.
    """
    raise NotImplementedError("Use create_import_candidates_from_variant_snapshot.")


def report_fetch_coverage(captured: CapturedProductPage) -> dict:
    trace = []
    for variant in captured.variants:
        evidence = variant.offer_evidence
        recovery = variant.recovery_trace
        trace.append(
            {
                "offer": variant.option_label or variant.product_title,
                "discovery_source": variant.discovery_source,
                "complete": not variant.validation_reasons,
                "evidence_checkpoints": list(dict.fromkeys(variant.evidence_checkpoints + ((evidence.evidence_checkpoints_seen if evidence else []) or []))),
                "missing_fields": list(variant.validation_reasons),
                "recovery_attempted": variant.recovery_attempted,
                "recovery_succeeded": variant.recovery_succeeded,
                "checked_sources": recovery.checked_sources if recovery else [],
                "stopped_reason": recovery.stopped_reason if recovery else None,
            }
        )
    coverage = dict(captured.coverage_report or {})
    coverage["offer_traces"] = trace
    coverage["evidence_checkpoints_checked"] = sorted(
        {
            checkpoint
            for item in trace
            for checkpoint in item.get("evidence_checkpoints", [])
        }
    )
    return coverage
