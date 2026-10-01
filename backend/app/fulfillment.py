"""Fulfillment method availability, costs, and vendor breakdowns."""
from __future__ import annotations

from .models import Product, Vendor
from .schemas import (
    CartPlan,
    DeliveryCharge,
    HandlingCharge,
    OptimizeRequest,
    SelectedItem,
    VendorBreakdown,
)

EPS = 1e-9
FULFILLMENT_METHODS = ("pickup_dropoff", "ups_shipping", "farm_truck_delivery")


def vendor_pickup_available(vendor: Vendor) -> bool:
    if vendor.pickup_dropoff_available is not None:
        return vendor.pickup_dropoff_available
    return True


def vendor_ups_available(vendor: Vendor) -> bool:
    return vendor.delivery_available if vendor.ups_shipping_available is None else vendor.ups_shipping_available


def product_method_available(product: Product, vendor: Vendor, method: str) -> bool:
    if method == "pickup_dropoff":
        if product.pickup_dropoff_available is not None:
            return product.pickup_dropoff_available
        return product.pickup_available and vendor_pickup_available(vendor)
    if method == "ups_shipping":
        if product.ups_shipping_available is not None:
            return product.ups_shipping_available
        return product.delivery_available and vendor_ups_available(vendor)
    if product.farm_truck_delivery_available is not None:
        return product.farm_truck_delivery_available
    return vendor.farm_truck_delivery_available


def allowed_methods(request: OptimizeRequest) -> set[str]:
    requested = set(request.allowed_fulfillment_methods)
    mode = request.fulfillment_mode
    if mode in {"best", "mixed"}:
        return requested
    if mode == "pickup_only":
        return {"pickup_dropoff"} & requested
    if mode in {"ups_only", "delivery_only"}:
        return {"ups_shipping"} & requested
    if mode == "farm_truck_only":
        return {"farm_truck_delivery"} & requested
    if mode == "no_pickup":
        return {"ups_shipping", "farm_truck_delivery"} & requested
    return requested


def shared_vendor_fees(vendor: Vendor) -> float:
    return vendor.handling_fee + vendor.packing_fee + vendor.cooler_fee + vendor.service_fee


def selected_package_count(items: list[SelectedItem]) -> int:
    return sum(pkg.package_count for item in items for pkg in item.packages)


def selected_cold_item_count(items: list[SelectedItem]) -> int:
    return sum(
        pkg.package_count
        for item in items
        for pkg in item.packages
        if pkg.storage_state in {"frozen", "refrigerated", "chilled", "cold"}
    )


def line_based_fees(vendor: Vendor, package_count: int, cold_item_count: int) -> float:
    return (
        package_count * vendor.per_package_fee
        + cold_item_count * vendor.per_cold_item_fee
    )


def quote_metadata(vendor: Vendor, fulfillment: float, line_fee: float) -> tuple[str, str, list[str]]:
    configured = any(
        value
        for value in [
            vendor.delivery_fee,
            vendor.handling_fee,
            vendor.packing_fee,
            vendor.cooler_fee,
            vendor.service_fee,
            vendor.ups_shipping_fee,
            vendor.ups_packing_fee,
            vendor.ups_cooler_fee,
            vendor.farm_truck_delivery_fee,
            vendor.minimum_order,
            vendor.minimum_order_pickup_dropoff or 0.0,
            vendor.minimum_order_ups or 0.0,
            vendor.minimum_order_farm_truck_delivery or 0.0,
            vendor.per_package_fee,
            vendor.per_cold_item_fee,
            line_fee,
        ]
    )
    source = "rule_based_estimate" if configured or fulfillment > EPS else "default_vendor_estimate"
    warnings: list[str] = []
    if source == "default_vendor_estimate":
        warnings.append("Fulfillment cost uses a default estimate because vendor quote rules are not configured.")
    else:
        warnings.append("Fulfillment cost is an estimate from configured vendor rules, not a captured checkout quote.")
    return source, "estimated", warnings


def minimum_for(vendor: Vendor, method: str) -> float:
    if method == "pickup_dropoff":
        return vendor.minimum_order_pickup_dropoff if vendor.minimum_order_pickup_dropoff is not None else vendor.minimum_order
    if method == "ups_shipping":
        return vendor.minimum_order_ups if vendor.minimum_order_ups is not None else vendor.minimum_order
    return (
        vendor.minimum_order_farm_truck_delivery
        if vendor.minimum_order_farm_truck_delivery is not None
        else vendor.minimum_order
    )


def travel_cost(distance: float, request: OptimizeRequest) -> float:
    multiplier = 2 if request.use_round_trip_pickup_cost else 1
    return distance * multiplier * request.cost_per_mile


def build_cart_plan(
    selected: list[SelectedItem],
    vendors: dict[str, Vendor],
    include_delivery: bool,
    pickup_only: bool,
    request: OptimizeRequest,
    rank: int,
) -> CartPlan | None:
    """Price a whole cart, charging vendor-level fulfillment costs once."""
    vendor_ids = sorted({item.vendor_id for item in selected})
    delivery_charges: list[DeliveryCharge] = []
    handling_charges: list[HandlingCharge] = []
    vendor_breakdowns: list[VendorBreakdown] = []
    notes: list[str] = []
    warnings: list[str] = []
    methods = allowed_methods(request)

    for vendor_id in vendor_ids:
        vendor = vendors[vendor_id]
        vendor_items = [item for item in selected if item.vendor_id == vendor_id]
        vendor_item_total = sum(item.item_cost for item in vendor_items)
        vendor_deposit_total = sum(item.returnable_deposits for item in vendor_items)
        package_count = selected_package_count(vendor_items)
        cold_item_count = selected_cold_item_count(vendor_items)
        selected_line_fee = line_based_fees(vendor, package_count, cold_item_count)
        valid_breakdowns: list[VendorBreakdown] = []

        def all_items_support(method: str) -> bool:
            attr = {
                "pickup_dropoff": "pickup_dropoff_available",
                "ups_shipping": "ups_shipping_available",
                "farm_truck_delivery": "farm_truck_delivery_available",
            }[method]
            return all(
                getattr(pkg, attr)
                for item in vendor_items
                for pkg in item.packages
            )

        def add_breakdown(breakdown: VendorBreakdown) -> None:
            if not breakdown.minimum_met:
                msg = (
                    f"Add ${breakdown.amount_short:.2f} more from {vendor.name} "
                    f"to meet {breakdown.fulfillment_method.replace('_', ' ')} minimum."
                )
                breakdown.warnings.append(msg)
                if request.enforce_minimum_order:
                    return
            valid_breakdowns.append(breakdown)

        if "pickup_dropoff" in methods and vendor_pickup_available(vendor) and all_items_support("pickup_dropoff"):
            if (
                vendor.pickup_dropoff_available is None
                and vendor.delivery_available
                and request.include_delivery
                and request.fulfillment_mode not in {"pickup_only"}
            ):
                pass
            else:
                pickup_locations = vendor.pickup_locations or [
                    Vendor.PickupLocation(
                        id="farm_pickup",
                        name="Farm pickup",
                        address=vendor.pickup_address,
                    )
                ]
                for location in pickup_locations:
                    if (
                        request.max_pickup_distance_miles is not None
                        and location.distance_miles > request.max_pickup_distance_miles
                    ):
                        continue
                    travel = travel_cost(location.distance_miles, request)
                    vendor_fees = shared_vendor_fees(vendor)
                    fulfillment = (
                        location.pickup_fee
                        + location.pickup_handling_fee
                        + vendor_fees
                        + selected_line_fee
                        + travel
                    )
                    quote_source, quote_status, quote_warnings = quote_metadata(
                        vendor, fulfillment, selected_line_fee
                    )
                    minimum = minimum_for(vendor, "pickup_dropoff")
                    amount_short = max(0.0, minimum - vendor_item_total)
                    add_breakdown(
                        VendorBreakdown(
                            vendor_id=vendor.id,
                            vendor_name=vendor.name,
                            fulfillment_method="pickup_dropoff",
                            item_subtotal=round(vendor_item_total, 2),
                            fulfillment_cost=round(fulfillment, 2),
                            vendor_total=round(vendor_item_total + fulfillment, 2),
                            package_count=package_count,
                            cold_item_count=cold_item_count,
                            quote_source=quote_source,
                            quote_status=quote_status,
                            minimum_order=round(minimum, 2),
                            minimum_met=amount_short <= EPS,
                            amount_short=round(amount_short, 2),
                            returnable_deposits=round(vendor_deposit_total, 2),
                            pickup_location_id=location.id,
                            pickup_location_name=location.name,
                            pickup_address=location.address,
                            pickup_distance_miles=location.distance_miles,
                            pickup_travel_cost=round(travel, 2),
                            pickup_fee=location.pickup_fee,
                            pickup_handling_fee=location.pickup_handling_fee,
                            pickup_vendor_fees=round(vendor_fees, 2),
                            per_package_fee=round(package_count * vendor.per_package_fee, 2),
                            per_cold_item_fee=round(cold_item_count * vendor.per_cold_item_fee, 2),
                            warnings=quote_warnings,
                        )
                    )

        if "ups_shipping" in methods and vendor_ups_available(vendor) and all_items_support("ups_shipping"):
            ups_shipping_fee = vendor.ups_shipping_fee or vendor.delivery_fee
            if (
                vendor.free_shipping_threshold is not None
                and vendor_item_total >= vendor.free_shipping_threshold
            ):
                ups_shipping_fee = 0.0
            fulfillment = (
                ups_shipping_fee
                + vendor.ups_packing_fee
                + vendor.ups_cooler_fee
                + shared_vendor_fees(vendor)
                + selected_line_fee
            )
            quote_source, quote_status, quote_warnings = quote_metadata(
                vendor, fulfillment, selected_line_fee
            )
            minimum = minimum_for(vendor, "ups_shipping")
            amount_short = max(0.0, minimum - vendor_item_total)
            add_breakdown(
                VendorBreakdown(
                    vendor_id=vendor.id,
                    vendor_name=vendor.name,
                    fulfillment_method="ups_shipping",
                    item_subtotal=round(vendor_item_total, 2),
                    fulfillment_cost=round(fulfillment, 2),
                    vendor_total=round(vendor_item_total + fulfillment, 2),
                    package_count=package_count,
                    cold_item_count=cold_item_count,
                    quote_source=quote_source,
                    quote_status=quote_status,
                    minimum_order=round(minimum, 2),
                    minimum_met=amount_short <= EPS,
                    amount_short=round(amount_short, 2),
                    returnable_deposits=round(vendor_deposit_total, 2),
                    warnings=quote_warnings,
                    ups_shipping_fee=ups_shipping_fee,
                    ups_packing_fee=vendor.ups_packing_fee,
                    ups_cooler_fee=vendor.ups_cooler_fee,
                    per_package_fee=round(package_count * vendor.per_package_fee, 2),
                    per_cold_item_fee=round(cold_item_count * vendor.per_cold_item_fee, 2),
                )
            )

        if "farm_truck_delivery" in methods and vendor.farm_truck_delivery_available and all_items_support("farm_truck_delivery"):
            farm_truck_delivery_fee = vendor.farm_truck_delivery_fee
            if (
                vendor.free_shipping_threshold is not None
                and vendor_item_total >= vendor.free_shipping_threshold
            ):
                farm_truck_delivery_fee = 0.0
            fulfillment = farm_truck_delivery_fee + vendor.handling_fee + vendor.service_fee + selected_line_fee
            quote_source, quote_status, quote_warnings = quote_metadata(
                vendor, fulfillment, selected_line_fee
            )
            minimum = minimum_for(vendor, "farm_truck_delivery")
            amount_short = max(0.0, minimum - vendor_item_total)
            add_breakdown(
                VendorBreakdown(
                    vendor_id=vendor.id,
                    vendor_name=vendor.name,
                    fulfillment_method="farm_truck_delivery",
                    item_subtotal=round(vendor_item_total, 2),
                    fulfillment_cost=round(fulfillment, 2),
                    vendor_total=round(vendor_item_total + fulfillment, 2),
                    package_count=package_count,
                    cold_item_count=cold_item_count,
                    quote_source=quote_source,
                    quote_status=quote_status,
                    minimum_order=round(minimum, 2),
                    minimum_met=amount_short <= EPS,
                    amount_short=round(amount_short, 2),
                    returnable_deposits=round(vendor_deposit_total, 2),
                    warnings=quote_warnings,
                    per_package_fee=round(package_count * vendor.per_package_fee, 2),
                    per_cold_item_fee=round(cold_item_count * vendor.per_cold_item_fee, 2),
                    farm_truck_delivery_fee=farm_truck_delivery_fee,
                )
            )

        if not valid_breakdowns:
            return None
        chosen = min(valid_breakdowns, key=lambda b: (b.fulfillment_cost, b.vendor_total))
        vendor_breakdowns.append(chosen)
        warnings.extend(chosen.warnings)
        if chosen.fulfillment_method == "pickup_dropoff" and not pickup_only and not vendor.delivery_available:
            notes.append(f"{vendor.name} does not deliver; pickup required.")
        if vendor.handling_fee:
            handling_charges.append(
                HandlingCharge(vendor_id=vendor.id, vendor_name=vendor.name, fee=vendor.handling_fee)
            )
        delivery_charges.append(
            DeliveryCharge(
                vendor_id=vendor.id,
                vendor_name=vendor.name,
                fee=chosen.fulfillment_cost - vendor.handling_fee,
            )
        )

    product_subtotal = round(sum(item.item_cost for item in selected), 2)
    regular_product_subtotal = round(sum(item.regular_item_total for item in selected), 2)
    subscription_savings = round(sum(item.subscription_savings for item in selected), 2)
    fulfillment_cost_total = round(sum(v.fulfillment_cost for v in vendor_breakdowns), 2)
    returnable_deposits = round(sum(item.returnable_deposits for item in selected), 2)
    delivery_cost_total = round(sum(charge.fee for charge in delivery_charges), 2)
    handling_fee_total = round(sum(charge.fee for charge in handling_charges), 2)
    estimated = product_subtotal + fulfillment_cost_total
    if request.include_returnable_deposits_in_total:
        estimated += returnable_deposits
    total = round(estimated, 2)
    total_due_today = round(product_subtotal + fulfillment_cost_total + returnable_deposits, 2)

    return CartPlan(
        rank=rank,
        selected_items=selected,
        delivery_charges=delivery_charges,
        handling_charges=handling_charges,
        vendor_breakdowns=vendor_breakdowns,
        item_cost_total=product_subtotal,
        delivery_cost_total=delivery_cost_total,
        handling_fee_total=handling_fee_total,
        regular_product_subtotal=regular_product_subtotal,
        product_subtotal=product_subtotal,
        subscription_savings=subscription_savings,
        fulfillment_cost_total=fulfillment_cost_total,
        returnable_deposits=returnable_deposits,
        total_estimated_cost=total,
        total_due_today=total_due_today,
        vendor_ids=vendor_ids,
        warnings=warnings,
        pickup_required=any(b.fulfillment_method == "pickup_dropoff" for b in vendor_breakdowns),
        notes=notes,
    )
