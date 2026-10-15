"""Deterministic normalization for dairy & egg listings.

Every canonical product has one internal unit. Listings arrive in mixed package
units (oz, quart, dozen, ...) and are converted to that internal unit so prices
become directly comparable.
"""
from __future__ import annotations

from .display import display_name as build_display_name
from .display import visible_tags
from .models import CanonicalProduct, NormalizedUnit, PackageUnit, Product
from .schemas import NormalizedProduct

# Target normalized unit per canonical product.
NORMALIZED_UNIT: dict[CanonicalProduct, NormalizedUnit] = {
    CanonicalProduct.cow_milk: NormalizedUnit.gallon,
    CanonicalProduct.sheep_milk: NormalizedUnit.gallon,
    CanonicalProduct.cream: NormalizedUnit.pint,
    CanonicalProduct.butter: NormalizedUnit.lb,
    CanonicalProduct.cheese: NormalizedUnit.lb,
    CanonicalProduct.eggs: NormalizedUnit.count,
}

# Factor to multiply package_quantity by to reach the normalized unit.
# Keyed by canonical product because "quart"/"gallon" resolve differently for
# milk (-> gallons) than for cream (-> pints).
CONVERSIONS: dict[CanonicalProduct, dict[PackageUnit, float]] = {
    CanonicalProduct.cow_milk: {
        PackageUnit.gallon: 1.0,
        PackageUnit.half_gallon: 0.5,
        PackageUnit.quart: 0.25,
        PackageUnit.pint: 0.125,
    },
    CanonicalProduct.sheep_milk: {
        PackageUnit.gallon: 1.0,
        PackageUnit.half_gallon: 0.5,
        PackageUnit.quart: 0.25,
        PackageUnit.pint: 0.125,
    },
    CanonicalProduct.cream: {
        PackageUnit.pint: 1.0,
        PackageUnit.quart: 2.0,
        PackageUnit.half_gallon: 4.0,
        PackageUnit.gallon: 8.0,
    },
    CanonicalProduct.butter: {
        PackageUnit.lb: 1.0,
        PackageUnit.oz: 1.0 / 16.0,
    },
    # Soft cheese (cottage, fresh farm cheese) is sold by the pint; a pint is
    # counted as about a pound.
    CanonicalProduct.cheese: {
        PackageUnit.lb: 1.0,
        PackageUnit.oz: 1.0 / 16.0,
        PackageUnit.pint: 1.0,
        PackageUnit.quart: 2.0,
    },
    CanonicalProduct.eggs: {
        PackageUnit.count: 1.0,
        PackageUnit.dozen: 12.0,
        PackageUnit.half_dozen: 6.0,
    },
}

# Cheese kinds a shopper can pick, matched in the listing name in this order.
CHEESE_KINDS: list[tuple[str, str]] = [
    ("cottage", "Cottage"),
    ("cream cheese", "Cream cheese"),
    ("cheddar", "Cheddar"),
    ("swiss", "Swiss"),
    ("gouda", "Gouda"),
    ("colby", "Colby"),
    ("jack", "Monterey Jack"),
    ("parmesan", "Parmesan"),
    ("havarti", "Havarti"),
    ("feta", "Feta"),
    ("mozzarella", "Mozzarella"),
    ("ricotta", "Ricotta"),
    ("farmer", "Farmer cheese"),
    ("quark", "Quark"),
    ("kefir", "Kefir cheese"),
    ("gruyere", "Gruyere"),
    ("brie", "Brie"),
    ("blue", "Blue"),
]
OTHER_CHEESE = "Other cheese"


def cheese_kind(product: Product) -> str | None:
    """The kind of a cheese listing ("Cheddar"), None for anything that is not cheese."""
    if product.canonical_product != CanonicalProduct.cheese:
        return None
    name = product.product_name.casefold()
    return next((kind for word, kind in CHEESE_KINDS if word in name), OTHER_CHEESE)


class NormalizationError(ValueError):
    """Raised when a listing cannot be normalized."""


def conversion_factor(canonical: CanonicalProduct, unit: PackageUnit) -> float:
    try:
        return CONVERSIONS[canonical][unit]
    except KeyError as exc:  # unit not valid for this category
        raise NormalizationError(
            f"No conversion for {canonical.value} in unit {unit.value}"
        ) from exc


def normalize_quantity(
    canonical: CanonicalProduct, quantity: float, unit: PackageUnit
) -> float:
    """Convert a quantity in an arbitrary package unit to the normalized unit."""
    return quantity * conversion_factor(canonical, unit)


def exclusion_reason(product: Product) -> str | None:
    """Return why a product is excluded from optimization, or None if valid."""
    if product.package_quantity <= 0 or product.price < 0:
        return "invalid_listing"
    if product.package_unit not in CONVERSIONS[product.canonical_product]:
        return "invalid_unit"
    if not product.in_stock:
        return "out_of_stock"
    return None


def normalize_product(product: Product) -> NormalizedProduct:
    """Attach normalized quantity, unit, and per-unit price to a product."""
    reason = exclusion_reason(product)
    normalized_unit = NORMALIZED_UNIT[product.canonical_product]

    normalized_quantity: float | None = None
    unit_price: float | None = None
    # Structural problems mean we cannot compute a number at all; stock
    # exclusions still get normalized values (useful for display).
    if reason not in {"invalid_listing", "invalid_unit"}:
        normalized_quantity = normalize_quantity(
            product.canonical_product, product.package_quantity, product.package_unit
        )
        if normalized_quantity > 0:
            unit_price = round(product.price / normalized_quantity, 4)

    return NormalizedProduct(
        **product.model_dump(),
        normalized_quantity=normalized_quantity,
        normalized_unit=normalized_unit,
        unit_price=unit_price,
        excluded=reason is not None,
        exclusion_reason=reason,
        variety=cheese_kind(product),
        display_name=build_display_name(
            product.product_name,
            quantity=product.package_quantity,
            unit=product.package_unit.value,
            packaging=product.packaging,
            storage_state=product.storage_state,
        ),
        display_tags=visible_tags(
            packaging=product.packaging,
            storage_state=product.storage_state,
        ),
    )
