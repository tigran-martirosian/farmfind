import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.bus import MessageBus
from app.agents.cart_agent import locate_catalog
from app.agents.catalog_agent import Catalog
from app.agents.delivery_agent import DeliveryAgent
from app.agents.distance_agent import DistanceAgent
from app.agents.geo import (
    ChainGeocoder,
    Coordinates,
    Drive,
    GazetteerGeocoder,
    OnlineGeocoder,
    OsrmRouter,
    VendorSite,
)
from app.agents.messages import DeliveryQuery, DistanceQuery
from app.main import app
from app.models import Product, Vendor
from app.optimizer import optimize
from app.schemas import OptimizeItem, OptimizeRequest


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_zip_code_is_looked_up_by_zip():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/us/08540"
        return httpx.Response(
            200,
            json={"places": [{"place name": "Princeton", "state abbreviation": "NJ",
                              "latitude": "40.3487", "longitude": "-74.6590"}]},
        )

    place = OnlineGeocoder(_client(handler)).geocode("08540")

    assert place.name == "Princeton, NJ 08540"
    assert place.coordinates == Coordinates(lat=40.3487, lon=-74.659)


def test_town_name_is_looked_up_by_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "Princeton, NJ"
        return httpx.Response(
            200,
            json=[{"lat": "40.35", "lon": "-74.66", "name": "Princeton",
                   "address": {"town": "Princeton", "ISO3166-2-lvl4": "US-NJ"}}],
        )

    place = OnlineGeocoder(_client(handler)).geocode("Princeton, NJ")

    assert place.name == "Princeton, NJ"


def test_unknown_town_and_unknown_zip_are_not_guessed():
    geocoder = OnlineGeocoder(
        _client(lambda request: httpx.Response(404) if "zippopotam" in str(request.url) else httpx.Response(200, json=[]))
    )

    assert geocoder.geocode("00000") is None
    assert geocoder.geocode("Nowhere Town") is None


def test_places_outside_nj_ny_pa_are_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        if "zippopotam" in str(request.url):
            return httpx.Response(
                200,
                json={"places": [{"place name": "Beverly Hills", "state abbreviation": "CA",
                                  "latitude": "34.09", "longitude": "-118.41"}]},
            )
        return httpx.Response(
            200,
            json=[
                {"lat": "37.68", "lon": "-97.33", "name": "Princeton",
                 "address": {"city": "Princeton", "ISO3166-2-lvl4": "US-KS"}},
                {"lat": "40.35", "lon": "-74.66", "name": "Princeton",
                 "address": {"town": "Princeton", "ISO3166-2-lvl4": "US-NJ"}},
            ],
        )

    geocoder = OnlineGeocoder(_client(handler))

    assert geocoder.geocode("90210") is None
    assert geocoder.geocode("Princeton").name == "Princeton, NJ"


def test_sample_towns_are_found_offline_before_the_online_lookup():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("online lookup should not be called")

    chain = ChainGeocoder([GazetteerGeocoder.from_file(), OnlineGeocoder(_client(handler))])

    assert chain.geocode("Exampleville").name == "Exampleville"
    assert chain.known_places() == []


def test_drive_table_becomes_miles_and_minutes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": "Ok", "durations": [[0, 1800, None]], "distances": [[0, 32186.9, None]]}
        )

    drives = OsrmRouter(_client(handler)).drives(
        Coordinates(lat=0, lon=0), [Coordinates(lat=1, lon=1), Coordinates(lat=2, lon=2)]
    )

    assert drives == [Drive(miles=20.0, minutes=30), None]


class FixedRouter:
    def __init__(self, minutes: float):
        self.minutes = minutes

    def drives(self, origin, destinations):
        return [Drive(miles=self.minutes, minutes=self.minutes) for _ in destinations]


def _farm_vendor() -> Vendor:
    return Vendor(id="farm", name="Farm", pickup_address="1 Farm Rd", pickup_dropoff_available=True,
                  ups_shipping_available=True, ups_shipping_fee=25.0)


def test_a_farm_with_no_pickup_points_is_measured_at_the_farm():
    sites = {"farm": VendorSite(lat=30.1, lon=-40.0)}
    catalog = Catalog("demo_only", [], [_farm_vendor()])
    bus = MessageBus()
    bus.register(DistanceAgent(GazetteerGeocoder.from_file(), sites, FixedRouter(35)))
    bus.register(DeliveryAgent(catalog, sites))

    delivery = bus.request("test", DeliveryAgent.name, DeliveryQuery(location="Exampleville"))

    pickup = delivery.vendors[0].options[0]
    assert (pickup.pickup_location_id, pickup.drive_minutes, pickup.distance_miles) == ("farm_pickup", 35, 35)


def test_distance_agent_without_router_gives_straight_line_miles_only():
    sites = {"farm": VendorSite(lat=30.1, lon=-40.0)}
    bus = MessageBus()
    bus.register(DistanceAgent(GazetteerGeocoder.from_file(), sites))

    result = bus.request("test", DistanceAgent.name, DistanceQuery(location="Exampleville", vendor_ids=["farm"]))

    assert result.vendors[0].farm_miles == pytest.approx(6.9, abs=0.1)
    assert result.vendors[0].farm_minutes is None


@pytest.mark.parametrize("minutes,method", [(35, "pickup_dropoff"), (55, "ups_shipping")])
def test_pickup_is_offered_only_within_the_drive_limit(monkeypatch, minutes, method):
    monkeypatch.setattr("app.agents.cart_agent.load_vendor_sites", lambda: {"farm": VendorSite(lat=30.1, lon=-40.0)})
    eggs = Product(id="eggs", vendor_id="farm", product_name="Eggs", canonical_product="eggs",
                   price=6.0, package_quantity=12, package_unit="count")
    catalog = Catalog("demo_only", [eggs], [_farm_vendor()])

    products, vendors, place = locate_catalog(catalog, "Exampleville", GazetteerGeocoder.from_file(), ShortDrive(minutes))
    cart = optimize(OptimizeRequest(items=[OptimizeItem(canonical_product="eggs", quantity=12, unit="count")]),
                    products, vendors)

    assert place == "Exampleville"
    assert cart.vendor_breakdowns[0].fulfillment_method == method


class ShortDrive:
    """One mile away, so pickup is cheaper than shipping whenever it is allowed."""

    def __init__(self, minutes: float):
        self.minutes = minutes

    def drives(self, origin, destinations):
        return [Drive(miles=1, minutes=self.minutes) for _ in destinations]


def test_optimize_with_an_unknown_place_says_so():
    response = TestClient(app).post(
        "/optimize",
        json={"items": [{"canonical_product": "eggs", "quantity": 12, "unit": "count"}],
              "catalog_mode": "demo_only", "location": "Atlantis"},
    )

    assert response.status_code == 422
    assert "Atlantis" in response.json()["detail"]


def test_optimize_with_a_sample_town_reports_where_it_measured_from():
    response = TestClient(app).post(
        "/optimize",
        json={"items": [{"canonical_product": "eggs", "quantity": 12, "unit": "count"}],
              "catalog_mode": "demo_only", "location": "Exampleville"},
    )

    assert response.status_code == 200
    assert response.json()["location"] == "Exampleville"
