"""Unit tests for the task agents, the message bus and the audit log."""
from __future__ import annotations

import pytest

from app.agents.audit_log import AuditLog
from app.agents.bus import AgentRequestError
from app.agents.cart_agent import apply_delivery
from app.agents.catalog_agent import Catalog, CatalogAgent, load_active_catalog
from app.agents.distance_agent import DistanceAgent
from app.agents.geo import Coordinates, GazetteerGeocoder, Place, haversine_miles, load_vendor_sites
from app.agents.messages import CartQuery, CatalogQuery, DeliveryQuery, DistanceQuery
from app.data_loader import load_demo_products, load_demo_vendors
from tests.agent_helpers import EXAMPLE_ITEMS, demo_bus, trace_of


def _options(result, vendor_id: str, method: str):
    vendor = next(vendor for vendor in result.vendors if vendor.vendor_id == vendor_id)
    return [option for option in vendor.options if option.method == method]



def test_catalog_agent_lists_suppliers_with_stock_and_unit_prices():
    items = [
        {"product": "cow_milk", "quantity": 1, "unit": "gallon"},
        {"product": "sheep_milk", "quantity": 1, "unit": "quart"},
    ]
    result = demo_bus().request("test", "catalog_agent", CatalogQuery(items=items))

    assert result.catalog_mode == "demo_only"
    supplies = {vendor.vendor_id: [p.value for p in vendor.supplies] for vendor in result.vendors}
    assert supplies == {
        "vendor_e": ["cow_milk"],
        "vendor_f": ["cow_milk", "sheep_milk"],
        "vendor_g": ["cow_milk"],
    }
    vendor_f = next(vendor for vendor in result.vendors if vendor.vendor_id == "vendor_f")
    offers = {offer.product_id: offer for offer in vendor_f.offers["cow_milk"]}
    assert offers["bs_cow_half"].unit_price == 12.0
    assert offers["bs_cow_half"].unit == "gallon"
    assert offers["bs_cow_1gal_oos"].in_stock is False
    assert result.unsupplied == []


def test_catalog_agent_reports_items_nobody_stocks():
    catalog = Catalog(
        "demo_only",
        [p for p in load_demo_products() if p.canonical_product != "cheese"],
        load_demo_vendors(),
    )
    query = CatalogQuery(items=[{"product": "cheese", "quantity": 1, "unit": "lb"}])

    result = CatalogAgent(catalog).handle(query, demo_bus())

    assert result.vendors == []
    assert [product.value for product in result.unsupplied] == ["cheese"]
    assert "nobody has cheese" in result.summary()


def test_active_catalog_falls_back_to_demo_samples_when_nothing_is_approved(monkeypatch):
    monkeypatch.setattr("app.data_loader.load_approved_imported_products", lambda: [])
    assert load_active_catalog().mode == "demo_only"

    approved = [load_demo_products()[0].model_copy(update={"source_type": "imported"})]
    monkeypatch.setattr("app.data_loader.load_approved_imported_products", lambda: approved)
    catalog = load_active_catalog()
    assert catalog.mode == "imported_only"
    assert catalog.products == approved



def test_haversine_one_degree_of_latitude_is_about_69_miles():
    assert haversine_miles(Coordinates(lat=30, lon=-40), Coordinates(lat=31, lon=-40)) == pytest.approx(69.1, abs=0.1)


def test_distance_agent_measures_farm_and_pickup_distances():
    query = DistanceQuery(location="exampleville", vendor_ids=["vendor_e", "vendor_g", "vendor_x"])

    result = demo_bus().request("test", "distance_agent", query)

    assert result.recognised is True
    assert result.location == "Exampleville"
    by_vendor = {vendor.vendor_id: vendor for vendor in result.vendors}
    assert by_vendor["vendor_e"].pickup_miles == {"example_farm": 18.0, "town_dropoff": 12.5}
    assert by_vendor["vendor_g"].farm_miles == 6.0
    assert by_vendor["vendor_x"].farm_miles is None


def test_distance_agent_does_not_guess_an_unknown_location():
    result = demo_bus().request("test", "distance_agent", DistanceQuery(location="Nowhereville"))

    assert result.recognised is False
    assert result.coordinates is None
    assert "Exampleville" in result.known_locations
    assert result.summary() == "location not recognised: 'Nowhereville'"


def test_distance_agent_accepts_any_geocoder():
    class FixedGeocoder:
        def geocode(self, place):
            return Place(name="Anywhere", coordinates=Coordinates(lat=29.9132, lon=-40.0))

        def known_places(self):
            return []

    agent = DistanceAgent(FixedGeocoder(), load_vendor_sites())

    result = agent.handle(DistanceQuery(location="whatever", vendor_ids=["vendor_g"]), demo_bus())

    assert result.location == "Anywhere"
    assert result.vendors[0].farm_miles == 0.0



def test_delivery_agent_prices_each_method_and_asks_the_distance_agent():
    bus = demo_bus()

    result = bus.request("test", "delivery_agent", DeliveryQuery(location="Exampleville"))

    assert trace_of(bus) == [
        ("test", "delivery_agent", "check_fulfillment"),
        ("delivery_agent", "distance_agent", "measure_distances"),
    ]
    gate = _options(result, "vendor_g", "pickup_dropoff")[0]
    assert (gate.available, gate.distance_miles, gate.base_fee) == (True, 6.0, 8.04)
    assert _options(result, "vendor_g", "ups_shipping")[0].available is False
    shipping = _options(result, "vendor_e", "ups_shipping")[0]
    assert (shipping.base_fee, shipping.minimum_order) == (29.0, 75.0)
    assert [o.pickup_location_id for o in _options(result, "vendor_e", "pickup_dropoff")] == [
        "example_farm",
        "town_dropoff",
    ]
    truck = _options(result, "vendor_h", "farm_truck_delivery")[0]
    assert truck.available is True
    assert truck.base_fee == 17.0


def test_delivery_agent_drops_farm_truck_outside_the_delivery_radius():
    result = demo_bus().request("test", "delivery_agent", DeliveryQuery(location="Testerfield"))

    truck = _options(result, "vendor_h", "farm_truck_delivery")[0]
    assert truck.available is False
    assert "Outside the 30 mile delivery radius" in truck.note


def test_delivery_agent_reports_an_unknown_location():
    result = demo_bus().request("test", "delivery_agent", DeliveryQuery(location="Nowhereville"))

    assert result.recognised is False
    assert result.vendors == []



def test_cart_agent_returns_best_cart_and_alternatives_for_the_example():
    bus = demo_bus()

    result = bus.request("test", "cart_agent", CartQuery(items=EXAMPLE_ITEMS, location="Exampleville"))

    assert result.feasible is True
    assert result.total == 74.54
    assert [(c.vendor_id, c.method, c.cost) for c in result.fulfillment] == [
        ("vendor_g", "pickup_dropoff", 8.04)
    ]
    assert result.cart.total_estimated_cost == result.total
    assert len(result.cart.alternative_carts) == 3
    assert result.cart.catalog_diagnostics.optimizer_ready_count > 0
    # With no delivery result supplied, the agent asks the DeliveryAgent itself.
    assert trace_of(bus)[1:] == [
        ("cart_agent", "delivery_agent", "check_fulfillment"),
        ("delivery_agent", "distance_agent", "measure_distances"),
    ]


def test_cart_agent_uses_the_users_distances_not_the_configured_ones():
    near = demo_bus().request("test", "cart_agent", CartQuery(items=EXAMPLE_ITEMS, location="Exampleville"))
    far = demo_bus().request("test", "cart_agent", CartQuery(items=EXAMPLE_ITEMS, location="Mockford"))

    assert far.total > near.total
    pickups = [c for c in far.fulfillment if c.method == "pickup_dropoff"]
    assert all(choice.distance_miles > 40 for choice in pickups)


def test_cart_agent_respects_fulfillment_preference_and_pickup_limit():
    delivery_only = demo_bus().request(
        "test", "cart_agent", CartQuery(items=EXAMPLE_ITEMS, location="Exampleville", fulfillment="delivery")
    )
    assert all(choice.method != "pickup_dropoff" for choice in delivery_only.fulfillment)

    short_trip = demo_bus().request(
        "test", "cart_agent", CartQuery(items=EXAMPLE_ITEMS, location="Exampleville", max_pickup_miles=5)
    )
    assert all(choice.method != "pickup_dropoff" for choice in short_trip.fulfillment)


def test_cart_agent_reports_problems_instead_of_guessing():
    unknown = demo_bus().request("test", "cart_agent", CartQuery(items=EXAMPLE_ITEMS, location="Nowhereville"))
    assert (unknown.feasible, unknown.cart) == (False, None)
    assert "not recognised" in unknown.problem

    empty = demo_bus().request("test", "cart_agent", CartQuery(items=[]))
    assert empty.problem == "No items were requested."


def test_apply_delivery_switches_off_out_of_range_farm_truck_and_sets_distances():
    delivery = demo_bus().request("test", "delivery_agent", DeliveryQuery(location="Testerfield"))

    products, vendors = apply_delivery(load_demo_products(), load_demo_vendors(), delivery)

    vendor_h = next(vendor for vendor in vendors if vendor.id == "vendor_h")
    assert vendor_h.farm_truck_delivery_available is False
    frozen_cream = next(product for product in products if product.id == "mr_cream_pint_frozen")
    assert frozen_cream.farm_truck_delivery_available is False
    vendor_g = next(vendor for vendor in vendors if vendor.id == "vendor_g")
    assert vendor_g.pickup_locations[0].distance_miles > 90
    assert load_demo_vendors()[2].pickup_locations[0].distance_miles == 6.0



def test_bus_records_request_and_short_result():
    bus = demo_bus()
    bus.request("test", "distance_agent", DistanceQuery(location="Samplebury"))

    entry = bus.log.entries[0]
    assert (entry.step, entry.sender, entry.target, entry.action) == (1, "test", "distance_agent", "measure_distances")
    assert '"location":"Samplebury"' in entry.input_value
    assert entry.result_status == "ok"
    assert entry.result_summary.startswith("Samplebury resolved to")
    assert entry.run_id == bus.log.run_id


def test_bus_rejects_unknown_agents_and_invalid_payloads():
    bus = demo_bus()

    with pytest.raises(AgentRequestError, match="No agent named 'price_agent'"):
        bus.request("test", "price_agent", {})
    with pytest.raises(AgentRequestError, match="items.0.product"):
        bus.request("test", "catalog_agent", {"items": [{"product": "yogurt", "quantity": 1, "unit": "lb"}]})

    assert [entry.result_status for entry in bus.log.entries] == ["error", "error"]
    assert "items.0.product" in bus.log.entries[1].error_message


def test_audit_log_redacts_passwords_and_truncates_long_input():
    log = AuditLog("run-1")

    secret = log.record("a", "b", "fill_input", "password=secret")
    long = log.record("a", "b", "note", "x" * 1000)

    assert secret.input_value == "[redacted]"
    assert len(long.input_value) == 303
    assert [entry.step for entry in log.entries] == [1, 2]


def test_gazetteer_lists_only_the_fictional_sample_towns():
    assert GazetteerGeocoder.from_file().known_places() == [
        "Exampleville",
        "Samplebury",
        "Demoton",
        "Mockford",
        "Testerfield",
    ]
