"""Product option and cart-plan filtering helpers."""
from __future__ import annotations

from .combinations import best_combination
from .fulfillment import allowed_methods, product_method_available
from .models import CanonicalProduct, Product, Vendor
from .normalizer import normalize_product
from .pricing import EPS, build_selected_item
from .schemas import CartPlan, OptimizeRequest, SelectedItem


def category_options(
    canonical: CanonicalProduct,
    requested_normalized: float,
    products: list[Product],
    vendors: dict[str, Vendor],
    max_overbuy_pct: float,
    excluded_vendor_ids: set[str],
    excluded_product_ids: set[str],
    delivery_only: bool,
    pickup_only: bool,
    request: OptimizeRequest,
    packaging_preference: str,
    storage_preference: str,
    salt_preference: str,
) -> list[SelectedItem]:
    """Return each vendor's cheapest package combination for one category."""
    by_vendor: dict[str, list[tuple[Product, float]]] = {}
    for p in products:
        if p.canonical_product != canonical:
            continue
        if p.vendor_id in excluded_vendor_ids or p.id in excluded_product_ids:
            continue
        vendor = vendors[p.vendor_id]
        if not any(product_method_available(p, vendor, method) for method in allowed_methods(request)):
            continue
        if delivery_only and not product_method_available(p, vendor, "ups_shipping"):
            continue
        if pickup_only and not product_method_available(p, vendor, "pickup_dropoff"):
            continue
        if packaging_preference == "glass_only" and p.packaging != "glass":
            continue
        if canonical == CanonicalProduct.butter and (
            (salt_preference == "salted" and p.is_unsalted) or (salt_preference == "unsalted" and not p.is_unsalted)
        ):
            continue
        if storage_preference == "fresh_only" and p.storage_state == "frozen":
            continue
        norm = normalize_product(p)
        if norm.excluded or norm.normalized_quantity is None:
            continue
        by_vendor.setdefault(p.vendor_id, []).append((p, norm.normalized_quantity))

    options: list[SelectedItem] = []

    def add_option(
        canonical: CanonicalProduct,
        requested_normalized: float,
        vendor: Vendor,
        packages: list[tuple[Product, float]],
    ) -> None:
        result = best_combination(packages, requested_normalized, max_overbuy_pct, request)
        if result is None:
            return
        cost, combo = result
        combo_ids = {product.id for product, _, _ in combo}
        existing_ids = [
            {line.product_id for line in option.lines}
            for option in options
            if option.vendor_id == vendor.id
        ]
        if combo_ids in existing_ids:
            return
        options.append(
            build_selected_item(
                canonical,
                requested_normalized,
                vendor,
                combo,
                cost,
                pickup_only,
                request,
                packaging_preference,
                storage_preference,
                salt_preference,
            )
        )

    for vendor_id, packages in by_vendor.items():
        vendor = vendors[vendor_id]
        if packaging_preference == "avoid_plastic":
            non_plastic = [
                package for package in packages if package[0].packaging != "plastic"
            ]
            if non_plastic:
                add_option(canonical, requested_normalized, vendor, non_plastic)
                continue

        add_option(canonical, requested_normalized, vendor, packages)

        if packaging_preference == "prefer_glass":
            glass = [package for package in packages if package[0].packaging == "glass"]
            if glass:
                add_option(canonical, requested_normalized, vendor, glass)

        if storage_preference == "prefer_fresh":
            fresh = [package for package in packages if package[0].storage_state != "frozen"]
            if fresh:
                add_option(canonical, requested_normalized, vendor, fresh)

    return sorted(options, key=lambda item: (item.item_cost, item.vendor_name))


def missing_required_vendor_ids(plan: CartPlan, required_vendor_ids: set[str]) -> set[str]:
    return required_vendor_ids - set(plan.vendor_ids)


def has_pickup_required(plan: CartPlan) -> bool:
    return any(item.pickup_required for item in plan.selected_items)


def relevant_packaged_item(item: SelectedItem) -> bool:
    return any(pkg.packaging is not None for pkg in item.packages)


def plan_satisfies_prefer_glass(plan: CartPlan) -> bool:
    return any(
        relevant_packaged_item(item)
        and item.packaging_preference == "prefer_glass"
        and any(pkg.packaging == "glass" for pkg in item.packages)
        for item in plan.selected_items
    )


def plan_has_non_plastic_for_relevant(plan: CartPlan) -> bool:
    return any(
        relevant_packaged_item(item)
        and item.packaging_preference == "avoid_plastic"
        and any(pkg.packaging != "plastic" for pkg in item.packages)
        for item in plan.selected_items
    )


def plan_satisfies_prefer_fresh(plan: CartPlan) -> bool:
    return any(
        item.storage_preference == "prefer_fresh"
        and not any(pkg.storage_state == "frozen" for pkg in item.packages)
        for item in plan.selected_items
    )


def prefer_within_ten_percent(
    plans: list[CartPlan],
    predicate,
    note: str,
    notes: list[str],
) -> list[CartPlan]:
    if not plans:
        return plans
    cheapest = min(plan.total_estimated_cost for plan in plans)
    preferred = [
        plan for plan in plans
        if predicate(plan) and plan.total_estimated_cost <= cheapest * 1.1 + EPS
    ]
    if preferred:
        return preferred
    if any(predicate(plan) for plan in plans):
        notes.append(note)
    return plans
