"""Product pricing and selected-item construction helpers."""
from __future__ import annotations

from .display import display_name as build_display_name
from .fulfillment import product_method_available
from .models import CanonicalProduct, Product, Vendor
from .normalizer import NORMALIZED_UNIT
from .schemas import (
    CartLine,
    OptimizeRequest,
    SelectedItem,
    SelectedPackage,
)

EPS = 1e-9


def effective_price(product: Product, request: OptimizeRequest) -> tuple[float, bool, float]:
    if not request.use_subscription_pricing or not product.subscription_available:
        return product.price, False, 0.0
    if product.subscription_price is not None:
        effective = product.subscription_price
    elif product.subscription_discount_percent:
        effective = product.price * (1.0 - product.subscription_discount_percent / 100.0)
    else:
        return product.price, False, 0.0
    effective = round(effective, 4)
    return effective, effective < product.price - EPS, round(product.price - effective, 4)


def build_selected_item(
    canonical: CanonicalProduct,
    requested_normalized: float,
    vendor: Vendor,
    combo: list[tuple[Product, int, float]],
    cost: float,
    pickup_only: bool,
    request: OptimizeRequest,
    packaging_preference: str,
    storage_preference: str,
    salt_preference: str,
) -> SelectedItem:
    """Build the public selected-item shape from one package combination."""
    def product_display_name(prod: Product) -> str:
        if " " not in prod.product_name and "_" in prod.product_name:
            return prod.product_name
        return build_display_name(
            prod.product_name,
            quantity=prod.package_quantity,
            unit=getattr(prod.package_unit, "value", prod.package_unit),
            packaging=prod.packaging,
            storage_state=None,
        )

    lines = [
        CartLine(
            product_id=prod.id,
            product_name=product_display_name(prod),
            package_count=count,
            package_size=round(qty, 4),
            package_unit=prod.package_unit,
            line_quantity=round(count * qty, 4),
            bulk_deal=prod.bulk_deal,
            item_cost=round(count * effective_price(prod, request)[0], 2),
        )
        for prod, count, qty in combo
    ]
    packages_out: list[SelectedPackage] = []
    regular_item_total = 0.0
    subscription_savings = 0.0
    returnable_deposits = 0.0
    for prod, count, qty in combo:
        effective, subscription_applied, savings_each = effective_price(prod, request)
        line_total = count * effective
        regular_item_total += count * prod.price
        subscription_savings += count * savings_each
        if prod.container_returnable:
            returnable_deposits += count * prod.container_deposit
        packages_out.append(
            SelectedPackage(
                product_id=prod.id,
                product_name=product_display_name(prod),
                package_count=count,
                package_quantity=round(qty, 4),
                package_unit=NORMALIZED_UNIT[canonical],
                listed_package_quantity=prod.package_quantity,
                listed_package_unit=prod.package_unit,
                price_each=effective,
                regular_price_each=prod.price,
                effective_price_each=effective,
                subscription_applied=subscription_applied,
                subscription_savings=round(count * savings_each, 2),
                line_total=round(line_total, 2),
                packaging=prod.packaging,
                container_deposit=prod.container_deposit,
                container_returnable=prod.container_returnable,
                storage_state=prod.storage_state,
                stock_status="in_stock" if prod.in_stock else "out_of_stock",
                pickup_dropoff_available=product_method_available(prod, vendor, "pickup_dropoff"),
                ups_shipping_available=product_method_available(prod, vendor, "ups_shipping"),
                farm_truck_delivery_available=product_method_available(prod, vendor, "farm_truck_delivery"),
            )
        )
    total_qty = sum(line.line_quantity for line in lines)
    overbuy = max(0.0, total_qty - requested_normalized)
    overbuy_pct = (
        overbuy / requested_normalized * 100.0 if requested_normalized else 0.0
    )
    notes: list[str] = []
    pickup_required = not (
        any(product_method_available(prod, vendor, "ups_shipping") for prod, _, _ in combo)
        or any(product_method_available(prod, vendor, "farm_truck_delivery") for prod, _, _ in combo)
    )
    if any(line.bulk_deal for line in lines):
        notes.append("Bulk deal applied.")

    return SelectedItem(
        canonical_product=canonical,
        vendor_id=vendor.id,
        vendor=vendor.name,
        vendor_name=vendor.name,
        packages=packages_out,
        requested_quantity=round(requested_normalized, 4),
        normalized_unit=NORMALIZED_UNIT[canonical],
        lines=lines,
        purchased_quantity=round(total_qty, 4),
        total_quantity_purchased=round(total_qty, 4),
        overbuy_quantity=round(overbuy, 4),
        overbuy_amount=round(overbuy, 4),
        overbuy_percent=round(overbuy_pct, 2),
        regular_item_total=round(regular_item_total, 2),
        subscription_savings=round(subscription_savings, 2),
        returnable_deposits=round(returnable_deposits, 2),
        item_total=round(cost, 2),
        item_cost=round(cost, 2),
        pickup_required=pickup_required,
        packaging_preference=packaging_preference,
        storage_preference=storage_preference,
        salt_preference=salt_preference,
        notes=notes,
    )
