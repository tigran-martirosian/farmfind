"""Human-readable cart comparison explanations."""
from __future__ import annotations

from .pricing import EPS
from .schemas import CartPlan


def reason_lost(plan: CartPlan, winner: CartPlan) -> str:
    """Short comparison note for alternative-cart display."""
    item_delta = plan.item_cost_total - winner.item_cost_total
    fee_delta = (
        plan.delivery_cost_total
        + plan.handling_fee_total
        - winner.delivery_cost_total
        - winner.handling_fee_total
    )
    if item_delta < -EPS and fee_delta > EPS:
        return "cheaper items, but higher vendor fees"
    if item_delta > EPS and fee_delta < -EPS:
        return "cheaper vendor fees, but higher item subtotal"
    if len(plan.vendor_ids) > len(winner.vendor_ids):
        return "uses more vendors, adding vendor-level costs"
    if len(plan.vendor_ids) < len(winner.vendor_ids):
        return "uses fewer vendors, but item subtotal is higher"
    return "higher full cart total"
