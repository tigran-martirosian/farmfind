"""Parser for Udder Milk's own shop (not GrazeCart, not WooCommerce).

Its category pages list every product as a heading followed by a table of
packages with a price each; prices show without signing in, only buying needs
an account. A package marked "( Not Available )" is out of stock. Parsed from
HTML strings only, so every branch is offline testable.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .grazecart import _UNSUPPORTED_WORDS, _apply_bundle_math, _build_variant, _strip_tags, infer_product_type, parse_price
from .models import CapturedProductPage, CapturedProductVariant

# Dairy and egg products here that are not one of the six tracked types, even
# though their names say milk, cream, butter or egg.
_NOT_TRACKED_WORDS = _UNSUPPORTED_WORDS + [
    "yogurt",
    "yougurt",
    "kefir",
    "keifer",
    "butter milk",
    "buttermilk",
    "ice cream",
    "eggnog",
    "custard",
    "pudding",
    "parfait",
    "whey",
    "sour cream",
    "clotted",
    "fraiche",
    "cottage",
    "coffee",
    "egg white",
    "egg yolk",
]

_PRODUCT_HEADING = re.compile(r'<h3\b[^>]*\bid="product-id-(\d+)"[^>]*>(.*?)</h3>', re.IGNORECASE | re.DOTALL)
_PACKAGE_ROW = re.compile(
    r'<tr>\s*<td class="product-package">(.*?)</td>\s*<td[^>]*>(.*?)</td>(.*?)</tr>\s*(?=<tr>|</tbody>)',
    re.IGNORECASE | re.DOTALL,
)
_BADGE = re.compile(r'<span class="category-index-badge">.*?</span>', re.IGNORECASE | re.DOTALL)
_HELP_BLOCK = re.compile(r'<div class="form-group.*?<div class="help-block"></div>\s*</div>', re.IGNORECASE | re.DOTALL)


def _clean_title(raw_heading: str) -> str:
    # Headings carry the shop's own ordering marks ("-* ( Raw) Milk", '"""" Eggs').
    title = _strip_tags(_BADGE.sub("", raw_heading))
    return re.sub(r'^[\s\-*=:~^"\'.]+', "", title).strip()


def parse_category_products(html: str) -> list[tuple[str, list[dict]]]:
    """(product title, package rows) for every product block on a category page."""
    headings = list(_PRODUCT_HEADING.finditer(html))
    products: list[tuple[str, list[dict]]] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(html)
        block = html[heading.end():end]
        rows = []
        for package_cell, price_cell, rest in _PACKAGE_ROW.findall(block):
            label = _strip_tags(_BADGE.sub("", _HELP_BLOCK.sub("", package_cell)))
            price_text = _strip_tags(price_cell) or None
            if not label or parse_price(price_text) is None:
                continue
            package_id = re.search(r'packageid\]"[^>]*value="(\d+)"', package_cell)
            rows.append(
                {
                    # The same package keeps its id on every category page it is listed on.
                    "package_id": package_id.group(1) if package_id else None,
                    "label": label,
                    "price_text": price_text,
                    "available": "not available" not in _strip_tags(rest).lower(),
                }
            )
        products.append((_clean_title(heading.group(2)), rows))
    return products


def product_type(title: str, label: str) -> str | None:
    """The product name decides the type; the package label only when the name says nothing.
    Labels often name the milk the product is made from ("Salted (Pint) (Jersey Milk)")."""
    text = f"{title} {label}".lower()
    if any(word in text for word in _NOT_TRACKED_WORDS):
        return None
    kind = infer_product_type(title) or infer_product_type(label)
    if kind == "cow_milk" and ("buffalo" in text or "goat" in text):
        return None
    return kind


def _fix_pound_spelling(label: str) -> str:
    # The shop often writes pounds with a capital I: "(1/2Ib)".
    return re.sub(r"(?<=[\d\s(])Ib\b", "lb", label)


def parse_category_variants(html: str) -> list[CapturedProductVariant]:
    variants: list[CapturedProductVariant] = []
    for title, rows in parse_category_products(html):
        for row in rows:
            label = _fix_pound_spelling(row["label"])
            variant = _build_variant(
                title=title,
                label=label,
                total_price=parse_price(row["price_text"]),
                price_text=row["price_text"],
                reported_savings_text=None,
                product_type=product_type(title, label),
            )
            variant.stock_status = "in_stock" if row["available"] else "out_of_stock"
            variant.variant_id = row["package_id"]
            variant.discovery_source = "category_table"
            variants.append(variant)
    _apply_bundle_math(variants)
    return variants


def build_uddermilk_captured_page(
    html: str,
    vendor_id: str,
    page_id: str,
    source_url: str,
    *,
    final_url: str | None = None,
    text: str = "",
    artifact_dir=None,
) -> CapturedProductPage:
    products = parse_category_products(html)
    variants = parse_category_variants(html)
    warnings = [] if products else ["No Udder Milk product blocks found on the page."]
    captured = CapturedProductPage(
        vendor_id=vendor_id,
        page_id=page_id,
        source_url=source_url,
        final_url=final_url or source_url,
        page_kind="category_listing",
        description_text=text[:3000],
        variants=variants,
        captured_at=datetime.now(timezone.utc).isoformat(),
        warnings=warnings,
        coverage_report={
            "source_pages_completed": 1,
            "final_urls_reached": [final_url or source_url],
            "product_cards_found": len(products),
            "orderable_offers_extracted": len(variants),
            "variants_extracted": len(variants),
            "out_of_stock_offers": len([variant for variant in variants if variant.stock_status == "out_of_stock"]),
            "parser_warnings": warnings,
        },
    )
    if artifact_dir is not None:
        path = Path(artifact_dir)
        path.mkdir(parents=True, exist_ok=True)
        captured.raw_html_path = str(path / "page.html")
        (path / "variant_snapshots.json").write_text(json.dumps(captured.model_dump(), indent=2), encoding="utf-8")
    return captured
