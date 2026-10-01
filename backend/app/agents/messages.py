"""Typed messages the agents and the coordinator exchange."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..models import CanonicalProduct, NormalizedUnit, PackageUnit
from ..schemas import CatalogMode, OptimizeResponse
from .geo import Coordinates

FulfillmentPreference = Literal["any", "pickup", "delivery"]
FulfillmentMethod = Literal["pickup_dropoff", "ups_shipping", "farm_truck_delivery"]


class RequestedItem(BaseModel):
    product: CanonicalProduct
    quantity: float = Field(gt=0)
    unit: PackageUnit


class ShoppingRequest(BaseModel):
    """What the user asked for, as structured data."""

    items: list[RequestedItem] = []
    location: str | None = None
    fulfillment: FulfillmentPreference = "any"
    max_pickup_miles: float | None = Field(default=None, ge=0)
    max_vendors: int | None = Field(default=None, ge=1)


class AgentResult(BaseModel):
    def summary(self) -> str:
        """One line for the agent trace."""
        raise NotImplementedError



class CatalogQuery(BaseModel):
    items: list[RequestedItem]


class Offer(BaseModel):
    product_id: str
    name: str
    package: str
    price: float
    unit_price: float | None
    unit: NormalizedUnit
    in_stock: bool


class VendorSupply(BaseModel):
    vendor_id: str
    vendor_name: str
    supplies: list[CanonicalProduct]
    offers: dict[str, list[Offer]]


class CatalogResult(AgentResult):
    catalog_mode: CatalogMode
    vendors: list[VendorSupply]
    unsupplied: list[CanonicalProduct]

    def summary(self) -> str:
        suppliers = [vendor.vendor_name for vendor in self.vendors if vendor.supplies]
        text = f"{len(suppliers)} vendor(s) in stock ({self.catalog_mode}): {', '.join(suppliers) or 'none'}"
        if self.unsupplied:
            text += f"; nobody has {', '.join(product.value for product in self.unsupplied)}"
        return text



class DistanceQuery(BaseModel):
    location: str
    vendor_ids: list[str] = []


class VendorDistance(BaseModel):
    vendor_id: str
    farm_miles: float | None = None
    pickup_miles: dict[str, float] = {}


class DistanceResult(AgentResult):
    location: str
    recognised: bool
    coordinates: Coordinates | None = None
    vendors: list[VendorDistance] = []
    known_locations: list[str] = []

    def summary(self) -> str:
        if not self.recognised:
            return f"location not recognised: {self.location!r}"
        located = [vendor for vendor in self.vendors if vendor.farm_miles is not None]
        if not located:
            return f"{self.location} resolved to ({self.coordinates.lat:.3f}, {self.coordinates.lon:.3f})"
        miles = ", ".join(f"{vendor.vendor_id} {vendor.farm_miles:.1f} mi" for vendor in located)
        return f"{self.location}: {miles}"



class DeliveryQuery(BaseModel):
    location: str
    vendor_ids: list[str] = Field(
        default=[], description="Vendors to check; empty means every vendor in the catalog."
    )


class FulfillmentOption(BaseModel):
    method: FulfillmentMethod
    available: bool
    base_fee: float | None = Field(
        default=None, description="Per-order cost before any per-package fees."
    )
    minimum_order: float = 0.0
    distance_miles: float | None = None
    pickup_location_id: str | None = None
    pickup_location_name: str | None = None
    note: str = ""


class VendorDelivery(BaseModel):
    vendor_id: str
    vendor_name: str
    options: list[FulfillmentOption]


class DeliveryResult(AgentResult):
    location: str
    recognised: bool
    vendors: list[VendorDelivery] = []

    def summary(self) -> str:
        if not self.recognised:
            return f"location not recognised: {self.location!r}"
        parts = []
        for vendor in self.vendors:
            methods = sorted({option.method for option in vendor.options if option.available})
            parts.append(f"{vendor.vendor_id}: {', '.join(methods) or 'nothing available'}")
        return "; ".join(parts)



class CartQuery(ShoppingRequest):
    delivery: DeliveryResult | None = None


class FulfillmentChoice(BaseModel):
    vendor_id: str
    vendor_name: str
    method: FulfillmentMethod
    cost: float
    pickup_location_name: str | None = None
    distance_miles: float | None = None


class CartResult(AgentResult):
    """The recommendation: `cart` holds the best cart and its alternatives in
    the same shape `POST /optimize` returns."""

    feasible: bool
    total: float = 0.0
    fulfillment: list[FulfillmentChoice] = []
    cart: OptimizeResponse | None = None
    problem: str | None = None

    def summary(self) -> str:
        if self.cart is None:
            return self.problem or "no cart"
        vendors = ", ".join(choice.vendor_name for choice in self.fulfillment) or "no vendor"
        text = f"best cart ${self.total:.2f} from {vendors}; {len(self.cart.alternative_carts)} alternative(s)"
        if self.cart.unavailable_items:
            missing = ", ".join(item.canonical_product.value for item in self.cart.unavailable_items)
            text += f"; unavailable: {missing}"
        return text
