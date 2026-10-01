"""CartAgent: runs the deterministic optimizer with what the other agents found."""
from __future__ import annotations

from ..models import Product, Vendor
from ..optimizer import optimize
from ..schemas import OptimizeItem, OptimizeRequest, OptimizeResponse
from ..services import catalog_diagnostics, optimizer_ready_products
from .bus import MessageBus
from .catalog_agent import Catalog
from .delivery_agent import DeliveryAgent
from .messages import CartQuery, CartResult, DeliveryQuery, DeliveryResult, FulfillmentChoice

FULFILLMENT_MODE = {"any": "best", "pickup": "pickup_only", "delivery": "no_pickup"}

# Chat requests are approximate ("3 dozen eggs"), so a larger pack may cover
# them when it is cheaper.
OVERBUY_PERCENT = 25.0


class CartAgent:
    name = "cart_agent"
    action = "build_cart"
    input_model = CartQuery

    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def handle(self, query: CartQuery, bus: MessageBus) -> CartResult:
        if not query.items:
            return CartResult(feasible=False, problem="No items were requested.")
        delivery = query.delivery
        if delivery is None and query.location:
            delivery = bus.request(
                self.name, DeliveryAgent.name, DeliveryQuery(location=query.location)
            )
        if delivery is not None and not delivery.recognised:
            return CartResult(
                feasible=False, problem=f"Location not recognised: {delivery.location!r}."
            )

        products, vendors = self.catalog.products, self.catalog.vendors
        if delivery is not None:
            products, vendors = apply_delivery(products, vendors, delivery)
        request = OptimizeRequest(
            items=[
                OptimizeItem(canonical_product=item.product, quantity=item.quantity, unit=item.unit)
                for item in query.items
            ],
            fulfillment_mode=FULFILLMENT_MODE[query.fulfillment],
            max_pickup_distance_miles=query.max_pickup_miles,
            max_vendors=query.max_vendors,
            allow_overbuy_percent=OVERBUY_PERCENT,
            catalog_mode=self.catalog.mode,
        )
        excluded_by_reason: dict[str, int] = {}
        ready = optimizer_ready_products(products, excluded_by_reason)
        cart = optimize(request, ready, vendors).model_copy(
            update={"catalog_diagnostics": catalog_diagnostics(products, ready, excluded_by_reason)}
        )
        return CartResult(
            feasible=bool(cart.selected_items) and not cart.unavailable_items,
            total=cart.total_estimated_cost,
            fulfillment=_fulfillment_choices(cart),
            cart=cart,
        )


def apply_delivery(
    products: list[Product], vendors: list[Vendor], delivery: DeliveryResult
) -> tuple[list[Product], list[Vendor]]:
    """Copy the catalog with this user's pickup distances, and with farm-truck
    delivery switched off for vendors whose route does not reach them."""
    by_vendor = {vendor.vendor_id: vendor for vendor in delivery.vendors}
    out_of_range: set[str] = set()
    adjusted_vendors = []
    for vendor in vendors:
        found = by_vendor.get(vendor.id)
        if found is None:
            adjusted_vendors.append(vendor)
            continue
        miles = {
            option.pickup_location_id: option.distance_miles
            for option in found.options
            if option.pickup_location_id and option.distance_miles is not None
        }
        update: dict = {
            "pickup_locations": [
                location.model_copy(
                    update={"distance_miles": miles.get(location.id, location.distance_miles)}
                )
                for location in vendor.pickup_locations
            ]
        }
        truck = next(o for o in found.options if o.method == "farm_truck_delivery")
        if vendor.farm_truck_delivery_available and not truck.available:
            update["farm_truck_delivery_available"] = False
            out_of_range.add(vendor.id)
        adjusted_vendors.append(vendor.model_copy(update=update))
    # A product-level flag overrides the vendor's, so it has to be cleared too.
    adjusted_products = [
        product.model_copy(update={"farm_truck_delivery_available": False})
        if product.vendor_id in out_of_range
        else product
        for product in products
    ]
    return adjusted_products, adjusted_vendors


def _fulfillment_choices(cart: OptimizeResponse) -> list[FulfillmentChoice]:
    return [
        FulfillmentChoice(
            vendor_id=breakdown.vendor_id,
            vendor_name=breakdown.vendor_name,
            method=breakdown.fulfillment_method,
            cost=breakdown.fulfillment_cost,
            pickup_location_name=breakdown.pickup_location_name,
            distance_miles=breakdown.pickup_distance_miles,
        )
        for breakdown in cart.vendor_breakdowns
    ]
