"""Farms that charge for shipping or pickup delivery by the pound."""
import pytest

from app.models import Product, Vendor
from app.optimizer import optimize
from app.schemas import OptimizeItem, OptimizeRequest

MILK = Product(id="milk", vendor_id="farm", product_name="Milk", canonical_product="cow_milk",
               price=10.0, package_quantity=1, package_unit="gallon")


def _cart(vendor: Vendor, gallons: int, mode: str):
    request = OptimizeRequest(items=[OptimizeItem(canonical_product="cow_milk", quantity=gallons, unit="gallon")],
                              fulfillment_mode=mode)
    return optimize(request, [MILK], [vendor]).vendor_breakdowns[0]


def test_ups_by_the_pound_adds_the_estimated_weight_to_the_base_fee():
    vendor = Vendor(id="farm", name="Farm", pickup_address="", pickup_dropoff_available=False,
                    ups_shipping_available=True, ups_shipping_fee=20.0, ups_rate_per_lb=1.0)

    breakdown = _cart(vendor, 2, "ups_only")

    assert breakdown.estimated_weight_lb == pytest.approx(17.2)
    assert breakdown.ups_weight_fee == pytest.approx(17.2)
    assert breakdown.fulfillment_cost == pytest.approx(37.2)


def test_free_shipping_over_the_threshold_drops_the_weight_fee_too():
    vendor = Vendor(id="farm", name="Farm", pickup_address="", pickup_dropoff_available=False,
                    ups_shipping_available=True, ups_shipping_fee=20.0, ups_rate_per_lb=1.0,
                    free_shipping_threshold=30.0)

    assert _cart(vendor, 3, "ups_only").fulfillment_cost == 0.0


@pytest.mark.parametrize("gallons,fee", [(1, 4.3), (3, 0.0)])
def test_pickup_delivery_by_the_pound_is_free_over_the_farms_amount(gallons, fee):
    vendor = Vendor(id="farm", name="Farm", pickup_address="1 Farm Rd", pickup_dropoff_available=True,
                    ups_shipping_available=False, pickup_rate_per_lb=0.5, free_pickup_threshold=25.0)

    breakdown = _cart(vendor, gallons, "pickup_only")

    assert breakdown.pickup_weight_fee == pytest.approx(fee)
