"""Core domain models: enums, vendors, and product listings."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


ImportSourceType = Literal["manual", "imported", "mock"]


class CanonicalProduct(str, Enum):
    cow_milk = "cow_milk"
    sheep_milk = "sheep_milk"
    cream = "cream"
    butter = "butter"
    cheese = "cheese"
    eggs = "eggs"


class PackageUnit(str, Enum):
    gallon = "gallon"
    half_gallon = "half_gallon"
    quart = "quart"
    pint = "pint"
    lb = "lb"
    oz = "oz"
    count = "count"
    dozen = "dozen"
    half_dozen = "half_dozen"


class NormalizedUnit(str, Enum):
    gallon = "gallon"
    pint = "pint"
    lb = "lb"
    count = "count"


class Vendor(BaseModel):
    class PickupLocation(BaseModel):
        id: str
        name: str
        address: str
        distance_miles: float = 0.0
        pickup_fee: float = 0.0
        pickup_handling_fee: float = 0.0
        notes: str = ""

    id: str
    name: str
    pickup_address: str
    delivery_available: bool = False
    delivery_fee: float = 0.0
    handling_fee: float = 0.0
    packing_fee: float = 0.0
    cooler_fee: float = 0.0
    service_fee: float = 0.0
    minimum_order: float = 0.0
    pickup_dropoff_available: bool | None = None
    ups_shipping_available: bool | None = None
    farm_truck_delivery_available: bool = False
    pickup_locations: list[PickupLocation] = []
    ups_shipping_fee: float = 0.0
    ups_packing_fee: float = 0.0
    ups_cooler_fee: float = 0.0
    farm_truck_delivery_fee: float = 0.0
    per_package_fee: float = 0.0
    per_cold_item_fee: float = 0.0
    free_shipping_threshold: float | None = None
    minimum_order_pickup_dropoff: float | None = None
    minimum_order_ups: float | None = None
    minimum_order_farm_truck_delivery: float | None = None
    source_type: ImportSourceType = "mock"
    website_url: str | None = None
    login_required: bool = False
    supports_authenticated_fetch: bool = False
    last_fetched_at: str | None = None
    auth_state_label: str | None = None
    fetch_notes: str | None = None
    notes: str = ""


class Product(BaseModel):
    """A vendor listing, before normalization."""

    id: str
    vendor_id: str
    product_name: str
    canonical_product: CanonicalProduct
    price: float = Field(ge=0)
    package_quantity: float
    package_unit: PackageUnit
    in_stock: bool = True
    is_unsalted: bool = True
    delivery_available: bool = True
    pickup_available: bool = True
    pickup_dropoff_available: bool | None = None
    ups_shipping_available: bool | None = None
    farm_truck_delivery_available: bool | None = None
    subscription_available: bool = False
    subscription_discount_percent: float = 0.0
    subscription_price: float | None = None
    packaging: Literal["glass", "plastic"] | None = None
    container_deposit: float = 0.0
    container_returnable: bool = False
    storage_state: str = "unknown"
    bulk_deal: bool = False
    source_type: ImportSourceType = "mock"
    source_vendor_page: str | None = None
    source_url: str | None = None
    imported_at: str | None = None
    parser_confidence: float | None = Field(default=None, ge=0, le=1)
    needs_review: bool = False
    raw_text: str | None = None
    missing_fields: list[str] = []
    notes: str = ""
