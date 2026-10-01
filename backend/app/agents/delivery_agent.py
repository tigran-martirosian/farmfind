"""DeliveryAgent: which fulfillment methods each vendor offers this user, and
what they cost before per-package fees."""
from __future__ import annotations

from ..fulfillment import (
    minimum_for,
    shared_vendor_fees,
    travel_cost,
    vendor_pickup_available,
    vendor_ups_available,
)
from ..models import Vendor
from ..schemas import OptimizeRequest
from .bus import MessageBus
from .catalog_agent import Catalog
from .distance_agent import DistanceAgent
from .geo import VendorSite
from .messages import (
    DeliveryQuery,
    DeliveryResult,
    DistanceQuery,
    FulfillmentOption,
    VendorDelivery,
    VendorDistance,
)

# Pickup travel is priced with the optimizer's default cost per mile and round trip.
TRAVEL_DEFAULTS = OptimizeRequest(items=[])


class DeliveryAgent:
    name = "delivery_agent"
    action = "check_fulfillment"
    input_model = DeliveryQuery

    def __init__(self, catalog: Catalog, sites: dict[str, VendorSite]):
        self.catalog = catalog
        self.sites = sites

    def handle(self, query: DeliveryQuery, bus: MessageBus) -> DeliveryResult:
        vendors = [
            vendor
            for vendor in self.catalog.vendors
            if not query.vendor_ids or vendor.id in query.vendor_ids
        ]
        distances = bus.request(
            self.name,
            DistanceAgent.name,
            DistanceQuery(location=query.location, vendor_ids=[vendor.id for vendor in vendors]),
        )
        if not distances.recognised:
            return DeliveryResult(location=query.location, recognised=False)
        by_vendor = {distance.vendor_id: distance for distance in distances.vendors}
        return DeliveryResult(
            location=distances.location,
            recognised=True,
            vendors=[
                VendorDelivery(
                    vendor_id=vendor.id,
                    vendor_name=vendor.name,
                    options=self._options(vendor, by_vendor[vendor.id]),
                )
                for vendor in vendors
            ],
        )

    def _options(self, vendor: Vendor, distance: VendorDistance) -> list[FulfillmentOption]:
        return [
            *self._pickup_options(vendor, distance),
            self._shipping_option(vendor),
            self._farm_truck_option(vendor, distance),
        ]

    def _pickup_options(self, vendor: Vendor, distance: VendorDistance) -> list[FulfillmentOption]:
        if not vendor_pickup_available(vendor):
            return [
                FulfillmentOption(
                    method="pickup_dropoff", available=False, note="Vendor does not offer pickup."
                )
            ]
        # Same default the optimizer uses for a vendor with no pickup points listed.
        locations = vendor.pickup_locations or [
            Vendor.PickupLocation(id="farm_pickup", name="Farm pickup", address=vendor.pickup_address)
        ]
        options = []
        for location in locations:
            measured = location.id in distance.pickup_miles
            miles = distance.pickup_miles.get(location.id, location.distance_miles)
            fee = (
                location.pickup_fee
                + location.pickup_handling_fee
                + shared_vendor_fees(vendor)
                + travel_cost(miles, TRAVEL_DEFAULTS)
            )
            options.append(
                FulfillmentOption(
                    method="pickup_dropoff",
                    available=True,
                    base_fee=round(fee, 2),
                    minimum_order=minimum_for(vendor, "pickup_dropoff"),
                    distance_miles=miles,
                    pickup_location_id=location.id,
                    pickup_location_name=location.name,
                    note="Includes the round-trip travel cost."
                    if measured
                    else "Uses the distance configured for this pickup point.",
                )
            )
        return options

    def _shipping_option(self, vendor: Vendor) -> FulfillmentOption:
        if not vendor_ups_available(vendor):
            return FulfillmentOption(
                method="ups_shipping", available=False, note="Vendor does not ship."
            )
        fee = (
            (vendor.ups_shipping_fee or vendor.delivery_fee)
            + vendor.ups_packing_fee
            + vendor.ups_cooler_fee
            + shared_vendor_fees(vendor)
        )
        note = ""
        if vendor.free_shipping_threshold is not None:
            note = f"Shipping fee waived from ${vendor.free_shipping_threshold:.2f} of items."
        return FulfillmentOption(
            method="ups_shipping",
            available=True,
            base_fee=round(fee, 2),
            minimum_order=minimum_for(vendor, "ups_shipping"),
            note=note,
        )

    def _farm_truck_option(self, vendor: Vendor, distance: VendorDistance) -> FulfillmentOption:
        if not vendor.farm_truck_delivery_available:
            return FulfillmentOption(
                method="farm_truck_delivery", available=False, note="Vendor has no delivery route."
            )
        site = self.sites.get(vendor.id)
        radius = site.farm_truck_radius_miles if site else None
        miles = distance.farm_miles
        if radius is not None and miles is not None and miles > radius:
            return FulfillmentOption(
                method="farm_truck_delivery",
                available=False,
                distance_miles=miles,
                note=f"Outside the {radius:g} mile delivery radius.",
            )
        fee = vendor.farm_truck_delivery_fee + vendor.handling_fee + vendor.service_fee
        return FulfillmentOption(
            method="farm_truck_delivery",
            available=True,
            base_fee=round(fee, 2),
            minimum_order=minimum_for(vendor, "farm_truck_delivery"),
            distance_miles=miles,
            note=f"Within the {radius:g} mile delivery radius." if radius is not None else "",
        )
