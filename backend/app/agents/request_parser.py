"""Deterministic parser: a chat message becomes items, a location and preferences."""
from __future__ import annotations

import re

from pydantic import BaseModel

from ..models import CanonicalProduct, PackageUnit
from .messages import FulfillmentPreference, RequestedItem

NUMBER_WORDS = {
    "half a": 0.5, "half an": 0.5, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip

# Longer spellings first so "half gallon" wins over "gallon".
UNIT_WORDS = {
    "half gallons": PackageUnit.half_gallon, "half gallon": PackageUnit.half_gallon,
    "half dozen": PackageUnit.half_dozen,
    "gallons": PackageUnit.gallon, "gallon": PackageUnit.gallon, "gal": PackageUnit.gallon,
    "quarts": PackageUnit.quart, "quart": PackageUnit.quart, "qt": PackageUnit.quart,
    "pints": PackageUnit.pint, "pint": PackageUnit.pint, "pt": PackageUnit.pint,
    "pounds": PackageUnit.lb, "pound": PackageUnit.lb, "lbs": PackageUnit.lb, "lb": PackageUnit.lb,
    "ounces": PackageUnit.oz, "ounce": PackageUnit.oz, "oz": PackageUnit.oz,
    "dozen": PackageUnit.dozen, "doz": PackageUnit.dozen,
}  # fmt: skip

DEFAULT_UNIT = {
    CanonicalProduct.cow_milk: PackageUnit.gallon,
    CanonicalProduct.sheep_milk: PackageUnit.gallon,
    CanonicalProduct.cream: PackageUnit.pint,
    CanonicalProduct.butter: PackageUnit.lb,
    CanonicalProduct.cheese: PackageUnit.lb,
    CanonicalProduct.eggs: PackageUnit.dozen,
}

OTHER_MILKS = {"goat", "goat's", "almond", "oat", "soy", "coconut", "rice"}
FILLER_WORDS = {"of", "the", "some", "fresh", "farm", "please"}

_QUANTITY = (
    r"(?P<qty>\d+(?:\.\d+)?|(?:"
    + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
    + r")\b)"
)
_UNIT = r"(?P<unit>" + "|".join(re.escape(unit) for unit in UNIT_WORDS) + r")"
ITEM_RE = re.compile(
    rf"\b{_QUANTITY}\s*-?\s*(?:{_UNIT}\b\.?\s*)?(?:of\s+)?(?P<noun>[a-z][a-z' ]*)", re.IGNORECASE
)
PRODUCT_RE = re.compile(r"\b(?:[a-z']+\s+)?(?:milk|cream|butter|cheese|eggs?)\b(?:\s+cheese)?", re.IGNORECASE)
NOUN_END_RE = re.compile(r"\s+(?:in|from|for|to|at|near|i|delivered|shipped)\b", re.IGNORECASE)
SEGMENT_RE = re.compile(r"[,;!?\n]|\.(?:\s|$)|\s+(?:and|plus|&)\s+", re.IGNORECASE)
LOCATION_RE = re.compile(
    r"\b(?:live in|living in|i'?m in|i am in|located in|based in|"
    r"deliver(?:ed|y)? to|ship(?:ped|ping)? to)\s+([^.,;!?\n]+)",
    re.IGNORECASE,
)
LOCATION_STOP_RE = re.compile(r"\s+(?:and|what|which|so|please|can|could|i)\b.*", re.IGNORECASE)
WITHIN_MILES_RE = re.compile(r"\bwithin\s+(\d+(?:\.\d+)?)\s*(?:miles?|mi)\b", re.IGNORECASE)
ONE_VENDOR_RE = re.compile(r"\b(?:one|single|1|same)\s+(?:vendor|farm|shop|store)\b", re.IGNORECASE)
NO_PICKUP_RE = re.compile(r"\b(?:no|without|can'?t|cannot|can not)\s+pick\s?-?\s?up\b", re.IGNORECASE)
PICKUP_RE = re.compile(r"\bpick\s?-?\s?up\b", re.IGNORECASE)
DELIVERY_RE = re.compile(r"\b(?:deliver\w*|ship\w*)\b", re.IGNORECASE)


class ParsedMessage(BaseModel):
    items: list[RequestedItem] = []
    unknown_items: list[str] = []
    location: str | None = None
    fulfillment: FulfillmentPreference | None = None
    max_pickup_miles: float | None = None
    max_vendors: int | None = None


def parse_message(
    message: str, known_places: list[str] | None = None, expecting_location: bool = False
) -> ParsedMessage:
    """known_places lets a bare town name ("Exampleville") count as a location.
    expecting_location treats an item-free reply as the answer to "which town?"."""
    parsed = ParsedMessage(
        max_vendors=1 if ONE_VENDOR_RE.search(message) else None,
        fulfillment=_fulfillment(message),
    )
    within = WITHIN_MILES_RE.search(message)
    if within:
        parsed.max_pickup_miles = float(within.group(1))

    text = WITHIN_MILES_RE.sub(" ", message)
    for segment in SEGMENT_RE.split(text):
        _parse_segment(segment, parsed)

    parsed.location = _location(message, known_places or [])
    if parsed.location is None and expecting_location and not parsed.items:
        answer = re.sub(r"^\s*(?:in|near|at)\s+", "", message.strip(" .!?\n"), flags=re.IGNORECASE)
        parsed.location = answer or None
    return parsed


def _parse_segment(segment: str, parsed: ParsedMessage) -> None:
    for match in ITEM_RE.finditer(segment):
        unit = UNIT_WORDS.get((match.group("unit") or "").lower())
        product = _product(match.group("noun"))
        if product is not None:
            parsed.items.append(
                RequestedItem(
                    product=product,
                    quantity=_quantity(match.group("qty")),
                    unit=unit or _unit_without_word(product, match.group("qty")),
                )
            )
            return
        if unit is not None:
            # A quantity and unit of something FarmFind does not carry.
            noun = NOUN_END_RE.split(match.group("noun"))[0]
            words = [word for word in noun.lower().split() if word not in FILLER_WORDS]
            if words:
                parsed.unknown_items.append(" ".join(words[:2]))
            return
    # A product named without a quantity ("I need milk") means one default unit.
    for mention in PRODUCT_RE.finditer(segment):
        product = _product(mention.group(0))
        if product is not None and all(item.product != product for item in parsed.items):
            parsed.items.append(RequestedItem(product=product, quantity=1, unit=DEFAULT_UNIT[product]))


def _quantity(text: str) -> float:
    return float(NUMBER_WORDS.get(" ".join(text.lower().split()), text))


def _unit_without_word(product: CanonicalProduct, quantity: str) -> PackageUnit:
    """"12 eggs" counts eggs; "a dozen" or "3 gallons" never reach here."""
    if product == CanonicalProduct.eggs and quantity.lower() not in {"a", "an"}:
        return PackageUnit.count
    return DEFAULT_UNIT[product]


def _product(noun: str) -> CanonicalProduct | None:
    words = noun.lower().split()
    for index, word in enumerate(words[:4]):
        before = words[index - 1] if index else ""
        after = words[index + 1] if index + 1 < len(words) else ""
        if word == "milk":
            if before in {"sheep", "sheep's"}:
                return CanonicalProduct.sheep_milk
            return None if before in OTHER_MILKS else CanonicalProduct.cow_milk
        if word == "cream":
            return None if before in {"ice", "sour"} or after == "cheese" else CanonicalProduct.cream
        if word == "butter":
            return CanonicalProduct.butter
        if word == "cheese":
            return CanonicalProduct.cheese
        if word in {"egg", "eggs"}:
            return CanonicalProduct.eggs
    return None


def _fulfillment(message: str) -> FulfillmentPreference | None:
    if NO_PICKUP_RE.search(message):
        return "delivery"
    wants_pickup = bool(PICKUP_RE.search(message))
    wants_delivery = bool(DELIVERY_RE.search(message))
    if wants_pickup == wants_delivery:
        return "any" if wants_pickup else None
    return "pickup" if wants_pickup else "delivery"


def _location(message: str, known_places: list[str]) -> str | None:
    stated = LOCATION_RE.search(message)
    if stated:
        place = LOCATION_STOP_RE.sub("", stated.group(1)).strip()
        if place:
            return " ".join(place.split()[:3])
    for place in known_places:
        if re.search(rf"\b{re.escape(place)}\b", message, re.IGNORECASE):
            return place
    return None
