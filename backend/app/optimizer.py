"""Deterministic cart optimizer coordinator."""
from __future__ import annotations

from itertools import product as iterproduct

from .explanations import reason_lost
from .filters import (
    category_options,
    has_pickup_required,
    missing_required_vendor_ids,
    plan_has_non_plastic_for_relevant,
    plan_satisfies_prefer_glass,
    plan_satisfies_prefer_fresh,
    prefer_within_ten_percent,
)
from .fulfillment import build_cart_plan
from .models import Product, Vendor
from .normalizer import NormalizationError, normalize_quantity
from .schemas import (
    OptimizeRequest,
    OptimizeResponse,
    SelectedItem,
    UnavailableItem,
)


def _apply_cart_constraints(
    plans,
    request: OptimizeRequest,
    required_vendor_ids: set[str],
    delivery_only: bool,
    pickup_only: bool,
    notes: list[str],
):
    constrained = plans

    if request.max_vendors is not None:
        next_plans = [
            plan for plan in constrained if len(plan.vendor_ids) <= request.max_vendors
        ]
        if next_plans:
            constrained = next_plans
        else:
            notes.append(f"No valid cart could satisfy max_vendors={request.max_vendors}.")

    if required_vendor_ids:
        next_plans = [
            plan
            for plan in constrained
            if not missing_required_vendor_ids(plan, required_vendor_ids)
        ]
        if next_plans:
            constrained = next_plans
        else:
            missing = ", ".join(sorted(required_vendor_ids))
            notes.append(f"Required vendor constraint could not be satisfied: {missing}.")

    if request.avoid_pickup_required and not delivery_only and not pickup_only:
        delivery_plans = [plan for plan in constrained if not has_pickup_required(plan)]
        if delivery_plans:
            constrained = delivery_plans
        elif constrained:
            notes.append("Pickup-only fallback used because no valid delivery-only cart exists.")

    return constrained


def _apply_preference_constraints(plans, request: OptimizeRequest, notes: list[str]):
    constrained = plans

    if any(
        item.packaging_preference == "avoid_plastic"
        for plan in constrained
        for item in plan.selected_items
    ):
        non_plastic_plans = [
            plan for plan in constrained if plan_has_non_plastic_for_relevant(plan)
        ]
        if non_plastic_plans:
            constrained = non_plastic_plans
        elif constrained:
            notes.append("Plastic packaging used because no non-plastic option is available.")
    if any(
        item.packaging_preference == "prefer_glass"
        for plan in constrained
        for item in plan.selected_items
    ):
        constrained = prefer_within_ten_percent(
            constrained,
            plan_satisfies_prefer_glass,
            "Glass option was more than 10% higher, so the cheaper packaging was selected.",
            notes,
        )

    if any(
        item.storage_preference == "prefer_fresh"
        for plan in constrained
        for item in plan.selected_items
    ):
        constrained = prefer_within_ten_percent(
            constrained,
            plan_satisfies_prefer_fresh,
            "Fresh option was more than 10% higher, so the cheaper storage option was selected.",
            notes,
        )

    return constrained


def _rank_plans(plans):
    plans.sort(
        key=lambda plan: (
            plan.total_estimated_cost,
            plan.item_cost_total,
            len(plan.vendor_ids),
            tuple(plan.vendor_ids),
        )
    )
    return [
        plan.model_copy(update={"rank": rank})
        for rank, plan in enumerate(plans[:4], start=1)
    ]


def optimize(
    request: OptimizeRequest,
    products: list[Product],
    vendors: list[Vendor],
) -> OptimizeResponse:
    vendor_map = {v.id: v for v in vendors}
    excluded_vendor_ids = set(request.excluded_vendor_ids)
    excluded_product_ids = set(request.excluded_product_ids)
    required_vendor_ids = set(request.required_vendor_ids) - excluded_vendor_ids
    delivery_only = request.fulfillment_mode == "delivery_only"
    pickup_only = request.fulfillment_mode == "pickup_only"

    option_groups: list[list[SelectedItem]] = []
    unavailable: list[UnavailableItem] = []
    constraint_notes: list[str] = []

    for item in request.items:
        canonical = item.canonical_product
        if canonical is None:
            continue
        packaging_preference = item.packaging_preference or request.packaging_preference
        storage_preference = item.storage_preference or request.storage_preference
        salt_preference = item.salt_preference or request.salt_preference
        try:
            requested_normalized = normalize_quantity(
                canonical, item.quantity, item.unit
            )
        except NormalizationError:
            constraint_notes.append(
                f"No unit conversion exists for {canonical.value} "
                f"requested as {item.quantity:g} {item.unit.value}."
            )
            unavailable.append(
                UnavailableItem(
                    canonical_product=canonical,
                    requested_quantity=item.quantity,
                    unit=item.unit,
                    reason="invalid_requested_unit",
                )
            )
            continue
        options = category_options(
            canonical,
            requested_normalized,
            products,
            vendor_map,
            request.allow_overbuy_percent,
            excluded_vendor_ids,
            excluded_product_ids,
            delivery_only,
            pickup_only,
            request,
            packaging_preference,
            storage_preference,
            salt_preference,
        )
        if not options:
            constraint_notes.append(
                f"No valid imported/demo product matched {canonical.value} "
                f"for {item.quantity:g} {item.unit.value}."
            )
            unavailable.append(
                UnavailableItem(
                    canonical_product=canonical,
                    requested_quantity=item.quantity,
                    unit=item.unit,
                    reason="no_valid_product_within_overbuy_limit",
                )
            )
        else:
            option_groups.append(options)

    raw_plans = []
    for selected in iterproduct(*option_groups):
        plan = build_cart_plan(
            list(selected),
            vendor_map,
            request.include_delivery,
            pickup_only,
            request,
            rank=0,
        )
        if plan is not None:
            raw_plans.append(plan)
    if option_groups and not raw_plans and not unavailable:
        for item in request.items:
            canonical = item.canonical_product
            if canonical is None:
                continue
            unavailable.append(
                UnavailableItem(
                    canonical_product=canonical,
                    requested_quantity=item.quantity,
                    unit=item.unit,
                    reason="no_valid_cart_plan_with_requested_constraints",
                )
            )

    constrained_plans = _apply_cart_constraints(
        raw_plans,
        request,
        required_vendor_ids,
        delivery_only,
        pickup_only,
        constraint_notes,
    )
    constrained_plans = _apply_preference_constraints(
        constrained_plans,
        request,
        constraint_notes,
    )

    plans = _rank_plans(constrained_plans)
    winner = (
        plans[0]
        if plans
        else build_cart_plan([], vendor_map, request.include_delivery, pickup_only, request, rank=1)
    )
    alternatives = [
        plan.model_copy(update={"reason_lost": reason_lost(plan, winner)})
        for plan in plans[1:]
    ]

    return OptimizeResponse(
        selected_items=winner.selected_items,
        unavailable_items=unavailable,
        delivery_charges=winner.delivery_charges,
        handling_charges=winner.handling_charges,
        item_cost_total=winner.item_cost_total,
        delivery_cost_total=winner.delivery_cost_total,
        handling_fee_total=winner.handling_fee_total,
        vendor_breakdowns=winner.vendor_breakdowns,
        regular_product_subtotal=winner.regular_product_subtotal,
        product_subtotal=winner.product_subtotal,
        subscription_savings=winner.subscription_savings,
        fulfillment_cost_total=winner.fulfillment_cost_total,
        returnable_deposits=winner.returnable_deposits,
        total_estimated_cost=winner.total_estimated_cost,
        total_due_today=winner.total_due_today,
        alternative_carts=alternatives,
        warnings=[*constraint_notes, *winner.warnings],
        notes=[*constraint_notes, *winner.notes],
    )
