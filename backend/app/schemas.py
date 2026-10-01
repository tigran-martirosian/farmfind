"""API request/response schemas (the shapes crossing the HTTP boundary)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .models import CanonicalProduct, NormalizedUnit, PackageUnit, Product


class ImportedAttribute(BaseModel):
    label: str
    value: str


class NormalizedProduct(Product):
    """A product enriched with normalized quantity/unit and per-unit price."""

    normalized_quantity: float | None = None
    normalized_unit: NormalizedUnit
    unit_price: float | None = None
    excluded: bool = False
    exclusion_reason: str | None = None
    # Display fields: clean human-readable name + only order-relevant tags.
    display_name: str | None = None
    display_tags: list[str] = []


PackagingPreference = Literal["any", "prefer_glass", "glass_only", "avoid_plastic"]
StoragePreference = Literal["any", "fresh_only", "prefer_fresh", "allow_frozen"]
SaltPreference = Literal["any", "salted", "unsalted"]
StockStatus = Literal["in_stock", "out_of_stock", "unknown"]
CatalogMode = Literal["imported_only", "demo_only", "imported_plus_demo"]


class ImportedProductCandidate(BaseModel):
    import_id: str
    vendor_id: str
    source_page_id: str | None = None
    source_artifact_path: str | None = None
    original_import_id: str | None = None
    review_status: Literal["pending", "approved", "rejected"] = "pending"
    product_type: CanonicalProduct | None = None
    name: str
    detected_price: float | None = Field(default=None, ge=0)
    detected_package_size: float | None = Field(default=None, gt=0)
    detected_unit: PackageUnit | None = None
    packaging: Literal["glass", "plastic"] | None = None
    storage_state: str = "unknown"
    stock_status: StockStatus = "unknown"
    stock_quantity: int | None = None
    parser_confidence: float | None = Field(default=None, ge=0, le=1)
    raw_text: str = ""
    source_url: str | None = None
    screenshot_path: str | None = None
    needs_review: bool = True
    missing_fields: list[str] = []
    warnings: list[str] = []
    # Display fields. attributes holds only the options the source page
    # actually exposed (Size/Container/Option are filtered out as redundant).
    attributes: list[ImportedAttribute] = []
    display_name: str | None = None
    display_tags: list[str] = []
    species: str | None = None
    # A meaningful product family for items the optimizer schema does not (yet)
    # support (e.g. colostrum, whey). Used for grouping/typing when product_type
    # is None. Not an optimizer product type.
    relevant_type: str | None = None
    display_product_type: str | None = None
    package_display: str | None = None
    processing_tags: list[str] = []
    freshness_status: str | None = None
    salt_status: str | None = None
    eligibility_status: Literal["eligible", "excluded", "needs_review"] = "eligible"
    eligibility_reason: str | None = None
    exclusion_reason: str | None = None
    review_notes: list[str] = []
    evidence_checkpoints: list[str] = []
    validation_reasons: list[str] = []
    quantity_evidence: dict | None = None


class ReviewedImportDecision(BaseModel):
    import_id: str
    approved: bool
    edited_product: Product | None = None
    reviewer_notes: str = ""


class OptimizeItem(BaseModel):
    canonical_product: CanonicalProduct | None = None
    product_type: CanonicalProduct | None = None
    quantity: float = Field(gt=0)
    unit: PackageUnit
    packaging_preference: PackagingPreference | None = None
    storage_preference: StoragePreference | None = None
    salt_preference: SaltPreference | None = None

    @model_validator(mode="after")
    def set_product_aliases(self) -> "OptimizeItem":
        if self.canonical_product is None and self.product_type is None:
            raise ValueError("canonical_product or product_type is required")
        if self.canonical_product is None:
            self.canonical_product = self.product_type
        if self.product_type is None:
            self.product_type = self.canonical_product
        return self


class OptimizeRequest(BaseModel):
    items: list[OptimizeItem]
    include_delivery: bool = False
    allow_overbuy_percent: float = Field(default=0.0, ge=0)
    excluded_vendor_ids: list[str] = []
    excluded_product_ids: list[str] = []
    required_vendor_ids: list[str] = []
    max_vendors: int | None = Field(default=None, ge=1)
    avoid_pickup_required: bool = False
    fulfillment_mode: Literal[
        "best",
        "mixed",
        "delivery_only",
        "pickup_only",
        "ups_only",
        "farm_truck_only",
        "no_pickup",
    ] = "best"
    allowed_fulfillment_methods: list[
        Literal["pickup_dropoff", "ups_shipping", "farm_truck_delivery"]
    ] = ["pickup_dropoff", "ups_shipping", "farm_truck_delivery"]
    enforce_minimum_order: bool = True
    cost_per_mile: float = Field(default=0.67, ge=0)
    vehicle_mpg: float | None = Field(default=None, gt=0)
    fuel_price_per_gallon: float | None = Field(default=None, ge=0)
    use_round_trip_pickup_cost: bool = True
    max_pickup_distance_miles: float | None = Field(default=None, ge=0)
    use_subscription_pricing: bool = False
    packaging_preference: PackagingPreference = "any"
    include_returnable_deposits_in_total: bool = False
    storage_preference: StoragePreference = "any"
    salt_preference: SaltPreference = "any"
    catalog_mode: CatalogMode = "imported_only"
    use_imported_products: bool = False

    @model_validator(mode="after")
    def derive_cost_per_mile_from_vehicle_inputs(self) -> "OptimizeRequest":
        if self.vehicle_mpg is not None and self.fuel_price_per_gallon is not None:
            self.cost_per_mile = self.fuel_price_per_gallon / self.vehicle_mpg
        return self

    @model_validator(mode="after")
    def resolve_legacy_import_flag(self) -> "OptimizeRequest":
        if self.use_imported_products and self.catalog_mode == "imported_only":
            self.catalog_mode = "imported_plus_demo"
        return self


class CartLine(BaseModel):
    """One package type selected for a category (a category may need several)."""

    product_id: str
    product_name: str
    package_count: int
    package_size: float  # normalized quantity per package
    package_unit: PackageUnit
    line_quantity: float  # package_count * package_size, in normalized units
    bulk_deal: bool
    item_cost: float


class SelectedPackage(BaseModel):
    """Package details in the combination-output shape."""

    product_id: str
    product_name: str
    package_count: int
    package_quantity: float
    package_unit: NormalizedUnit
    listed_package_quantity: float | None = None
    listed_package_unit: PackageUnit | None = None
    price_each: float
    regular_price_each: float
    effective_price_each: float
    subscription_applied: bool = False
    subscription_savings: float = 0.0
    line_total: float
    packaging: str | None = None
    container_deposit: float = 0.0
    container_returnable: bool = False
    storage_state: str = "unknown"
    stock_status: StockStatus = "in_stock"
    pickup_dropoff_available: bool = True
    ups_shipping_available: bool = True
    farm_truck_delivery_available: bool = False


class SelectedItem(BaseModel):
    """The chosen fulfillment for a single requested category."""

    canonical_product: CanonicalProduct
    vendor_id: str
    vendor: str
    vendor_name: str
    packages: list[SelectedPackage]
    requested_quantity: float
    normalized_unit: NormalizedUnit
    lines: list[CartLine]
    purchased_quantity: float
    total_quantity_purchased: float
    overbuy_quantity: float
    overbuy_amount: float
    overbuy_percent: float
    regular_item_total: float = 0.0
    subscription_savings: float = 0.0
    returnable_deposits: float = 0.0
    item_total: float
    item_cost: float
    pickup_required: bool
    packaging_preference: PackagingPreference = "any"
    storage_preference: StoragePreference = "any"
    salt_preference: SaltPreference = "any"
    notes: list[str] = []


class UnavailableItem(BaseModel):
    canonical_product: CanonicalProduct
    requested_quantity: float
    unit: PackageUnit
    reason: str


class DeliveryCharge(BaseModel):
    vendor_id: str
    vendor_name: str
    fee: float


class HandlingCharge(BaseModel):
    vendor_id: str
    vendor_name: str
    fee: float


class VendorBreakdown(BaseModel):
    vendor_id: str
    vendor_name: str
    fulfillment_method: Literal[
        "pickup_dropoff", "ups_shipping", "farm_truck_delivery"
    ]
    item_subtotal: float
    fulfillment_cost: float
    vendor_total: float
    package_count: int = 0
    cold_item_count: int = 0
    quote_source: Literal[
        "rule_based_estimate",
        "default_vendor_estimate",
        "captured_checkout_quote",
        "unavailable",
        "not_configured",
    ] = "default_vendor_estimate"
    quote_status: Literal[
        "estimated",
        "captured",
        "unavailable",
        "action_required",
        "stale",
    ] = "estimated"
    minimum_order: float
    minimum_met: bool
    amount_short: float
    returnable_deposits: float = 0.0
    warnings: list[str] = []
    pickup_location_id: str | None = None
    pickup_location_name: str | None = None
    pickup_address: str | None = None
    pickup_distance_miles: float | None = None
    pickup_travel_cost: float = 0.0
    pickup_fee: float = 0.0
    pickup_handling_fee: float = 0.0
    pickup_vendor_fees: float = 0.0
    ups_shipping_fee: float = 0.0
    ups_packing_fee: float = 0.0
    ups_cooler_fee: float = 0.0
    per_package_fee: float = 0.0
    per_cold_item_fee: float = 0.0
    farm_truck_delivery_fee: float = 0.0


class CatalogDiagnostics(BaseModel):
    approved_loaded_count: int = 0
    optimizer_ready_count: int = 0
    products_by_vendor: dict[str, int] = {}
    products_by_type: dict[str, int] = {}
    excluded_by_reason: dict[str, int] = {}


class CartPlan(BaseModel):
    rank: int
    selected_items: list[SelectedItem]
    delivery_charges: list[DeliveryCharge]
    handling_charges: list[HandlingCharge]
    item_cost_total: float
    delivery_cost_total: float
    handling_fee_total: float
    vendor_breakdowns: list[VendorBreakdown] = []
    regular_product_subtotal: float = 0.0
    product_subtotal: float = 0.0
    subscription_savings: float = 0.0
    fulfillment_cost_total: float = 0.0
    returnable_deposits: float = 0.0
    total_estimated_cost: float
    total_due_today: float = 0.0
    vendor_ids: list[str]
    reason_lost: str | None = None
    warnings: list[str] = []
    pickup_required: bool = False
    notes: list[str] = []


class OptimizeResponse(BaseModel):
    selected_items: list[SelectedItem]
    unavailable_items: list[UnavailableItem]
    delivery_charges: list[DeliveryCharge]
    handling_charges: list[HandlingCharge] = []
    item_cost_total: float
    delivery_cost_total: float
    handling_fee_total: float = 0.0
    vendor_breakdowns: list[VendorBreakdown] = []
    regular_product_subtotal: float = 0.0
    product_subtotal: float = 0.0
    subscription_savings: float = 0.0
    fulfillment_cost_total: float = 0.0
    returnable_deposits: float = 0.0
    total_estimated_cost: float
    total_due_today: float = 0.0
    alternative_carts: list[CartPlan] = []
    warnings: list[str] = []
    notes: list[str] = []
    catalog_diagnostics: CatalogDiagnostics = CatalogDiagnostics()
