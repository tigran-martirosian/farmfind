"""Reply text built from agent results, and the check that a reply only quotes
amounts the CartAgent actually produced."""
from __future__ import annotations

import re

from ..schemas import CartPlan
from .messages import CartResult, FulfillmentChoice, ShoppingRequest

METHOD_LABELS = {
    "pickup_dropoff": "pickup",
    "ups_shipping": "UPS shipping",
    "farm_truck_delivery": "farm truck delivery",
}
PRODUCT_LABELS = {"cow_milk": "milk", "sheep_milk": "sheep milk"}
DOLLAR_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")

ASK_FOR_ITEMS = (
    "What would you like to order? For example: 2 gallons of milk, 1 lb of butter and a dozen eggs."
)
ASK_FOR_LOCATION = "Which town are you in? I need it to work out pickup distances and delivery options."


def money(value: float) -> str:
    return f"${value:.2f}"


def product_label(product: str) -> str:
    return PRODUCT_LABELS.get(product, product.replace("_", " "))


def unknown_items_note(unknown_items: list[str]) -> str:
    return (
        f"I could not match {', '.join(unknown_items)}; "
        "FarmFind covers milk, cream, butter, cheese and eggs."
    )


def unknown_location_question(location: str, known_locations: list[str]) -> str:
    question = f"I don't recognise {location!r} as a location."
    if known_locations:
        question += f" The sample data covers {', '.join(known_locations)}."
    return question + " Which town are you in?"


def compose_reply(request: ShoppingRequest, result: CartResult, unknown_items: list[str]) -> str:
    """The templated answer. Every figure comes straight from the cart result."""
    if result.cart is None:
        return result.problem or "I could not build a cart."
    cart = result.cart
    lines: list[str] = []
    if cart.selected_items:
        where = f" for {request.location}" if request.location else ""
        lines.append(
            f"Best order{where}: {money(cart.total_estimated_cost)} total "
            f"({money(cart.item_cost_total)} items + {money(cart.fulfillment_cost_total)} fulfillment)."
        )
        for choice in result.fulfillment:
            packages = "; ".join(
                f"{package.package_count} x {_short_name(package.product_name)} "
                f"({money(package.line_total)})"
                for item in cart.selected_items
                if item.vendor_id == choice.vendor_id
                for package in item.packages
            )
            lines.append(
                f"- {choice.vendor_name}, {_describe(choice)}: {packages}. "
                f"Fulfillment {money(choice.cost)}."
            )
    else:
        lines.append("I could not build a cart for this request.")
    for item in cart.unavailable_items:
        lines.append(
            f"Not available: {item.requested_quantity:g} {item.unit.value} of "
            f"{product_label(item.canonical_product.value)} ({item.reason.replace('_', ' ')})."
        )
    if cart.alternative_carts:
        lines.append("Alternatives:")
        lines.extend(_alternative_line(plan) for plan in cart.alternative_carts)
    lines.extend(cart.warnings)
    if unknown_items:
        lines.append(unknown_items_note(unknown_items))
    return "\n".join(lines)


def _describe(choice: FulfillmentChoice) -> str:
    label = METHOD_LABELS[choice.method]
    if choice.method == "pickup_dropoff" and choice.pickup_location_name:
        label += f" at {choice.pickup_location_name}"
        if choice.distance_miles is not None:
            label += f" ({choice.distance_miles:g} mi)"
    return label


def _alternative_line(plan: CartPlan) -> str:
    vendors = ", ".join(
        f"{breakdown.vendor_name} ({METHOD_LABELS[breakdown.fulfillment_method]})"
        for breakdown in plan.vendor_breakdowns
    )
    reason = f": {plan.reason_lost}" if plan.reason_lost else ""
    return f"- {money(plan.total_estimated_cost)} from {vendors}{reason}."


def _short_name(display_name: str) -> str:
    """Display names repeat the package size after an em dash; drop that part."""
    return display_name.split(" — ")[0]


def reply_problems(reply: str, result: CartResult | None) -> list[str]:
    """Reasons a reply must not be sent: it quotes a dollar amount the cart
    result does not contain, or leaves out the total of a feasible cart."""
    allowed = _amounts_in(result.model_dump(mode="json")) if result else set()
    problems = []
    for text in DOLLAR_RE.findall(reply):
        if round(float(text.replace(",", "")), 2) not in allowed:
            problems.append(f"${text} does not appear in the cart result")
    if result and result.cart and result.cart.selected_items:
        if money(result.total) not in reply.replace(" ", "").replace(",", ""):
            problems.append(f"the reply does not state the cart total {money(result.total)}")
    return problems


def _amounts_in(value) -> set[float]:
    """Every number in the result, plus amounts quoted inside its text fields."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {round(float(value), 2)}
    if isinstance(value, str):
        return {round(float(text.replace(",", "")), 2) for text in DOLLAR_RE.findall(value)}
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return set().union(*(_amounts_in(item) for item in value)) if value else set()
    return set()
