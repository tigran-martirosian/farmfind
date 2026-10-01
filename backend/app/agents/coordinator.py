"""Coordinator interface and the rule-based implementation (no model)."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from .bus import MessageBus
from .cart_agent import CartAgent
from .catalog_agent import CatalogAgent
from .delivery_agent import DeliveryAgent
from .distance_agent import DistanceAgent
from .geo import Geocoder
from .messages import (
    CartQuery,
    CartResult,
    CatalogQuery,
    DeliveryQuery,
    DistanceQuery,
    ShoppingRequest,
)
from .replies import (
    ASK_FOR_ITEMS,
    ASK_FOR_LOCATION,
    compose_reply,
    product_label,
    unknown_items_note,
    unknown_location_question,
)
from .request_parser import ParsedMessage, parse_message


class ConversationState(BaseModel):
    """What is remembered between turns of one conversation."""

    conversation_id: str
    request: ShoppingRequest = ShoppingRequest()
    awaiting: Literal["items", "location"] | None = None
    history: list[dict[str, str]] = []


class ChatTurn(BaseModel):
    reply: str
    follow_up_question: str | None = None
    recommendation: CartResult | None = None
    request: ShoppingRequest
    coordinator: str


class Coordinator(Protocol):
    name: str

    def handle(self, message: str, state: ConversationState, bus: MessageBus) -> ChatTurn: ...


class RuleBasedCoordinator:
    """Parses the message with fixed rules, then asks the agents in a fixed order."""

    name = "rule_based"

    def __init__(self, geocoder: Geocoder):
        self.geocoder = geocoder

    def handle(self, message: str, state: ConversationState, bus: MessageBus) -> ChatTurn:
        parsed = parse_message(
            message,
            known_places=self.geocoder.known_places(),
            expecting_location=state.awaiting == "location",
        )
        request = _merge(state.request, parsed)
        state.request = request
        bus.log.record(
            "user", self.name, "parse_request", message, result_summary=_describe(request, parsed)
        )

        if not request.items:
            question = ASK_FOR_ITEMS
            if parsed.unknown_items:
                question = f"{unknown_items_note(parsed.unknown_items)} {question}"
            return self._ask(state, "items", question)
        if not request.location:
            return self._ask(state, "location", ASK_FOR_LOCATION)

        catalog = bus.request(self.name, CatalogAgent.name, CatalogQuery(items=request.items))
        where = bus.request(self.name, DistanceAgent.name, DistanceQuery(location=request.location))
        if not where.recognised:
            request.location = None
            return self._ask(
                state,
                "location",
                unknown_location_question(where.location, where.known_locations),
            )
        request.location = where.location
        state.awaiting = None

        suppliers = [vendor.vendor_id for vendor in catalog.vendors if vendor.supplies]
        if not suppliers:
            names = ", ".join(product_label(item.product.value) for item in request.items)
            return ChatTurn(
                reply=f"No vendor in the catalog has {names} in stock.",
                request=request,
                coordinator=self.name,
            )
        delivery = bus.request(
            self.name,
            DeliveryAgent.name,
            DeliveryQuery(location=request.location, vendor_ids=suppliers),
        )
        cart = bus.request(
            self.name,
            CartAgent.name,
            CartQuery(**request.model_dump(), delivery=delivery),
        )
        return ChatTurn(
            reply=compose_reply(request, cart, parsed.unknown_items),
            recommendation=cart,
            request=request,
            coordinator=self.name,
        )

    def _ask(self, state: ConversationState, awaiting: str, question: str) -> ChatTurn:
        state.awaiting = awaiting
        return ChatTurn(
            reply=question,
            follow_up_question=question,
            request=state.request,
            coordinator=self.name,
        )


def _merge(previous: ShoppingRequest, parsed: ParsedMessage) -> ShoppingRequest:
    """A message that names items starts a new list; anything it leaves out
    (location, preferences) carries over from earlier turns."""
    return ShoppingRequest(
        items=parsed.items or previous.items,
        location=parsed.location or previous.location,
        fulfillment=parsed.fulfillment or previous.fulfillment,
        max_pickup_miles=parsed.max_pickup_miles or previous.max_pickup_miles,
        max_vendors=parsed.max_vendors or previous.max_vendors,
    )


def _describe(request: ShoppingRequest, parsed: ParsedMessage) -> str:
    items = ", ".join(
        f"{item.quantity:g} {item.unit.value} {product_label(item.product.value)}"
        for item in request.items
    )
    text = f"items: {items or 'none'}; location: {request.location or 'missing'}"
    if request.fulfillment != "any":
        text += f"; fulfillment: {request.fulfillment}"
    if parsed.unknown_items:
        text += f"; not recognised: {', '.join(parsed.unknown_items)}"
    return text
