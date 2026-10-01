"""Tests for the cart optimizer (package selection, overbuy, delivery)."""
import json

from fastapi.testclient import TestClient

from app.main import app
from app.models import CanonicalProduct, PackageUnit, Product, Vendor
from app.optimizer import optimize
from app.schemas import ImportedProductCandidate, OptimizeItem, OptimizeRequest
from app.services import normalize_imported_product, optimize_cart, optimizer_ready_products, validate_import_candidate


def _vendor(vid="v1", delivery=True, fee=8.0, minimum=0.0, handling=0.0) -> Vendor:
    return Vendor(
        id=vid,
        name=vid.upper(),
        pickup_address="addr",
        delivery_available=delivery,
        delivery_fee=fee,
        handling_fee=handling,
        minimum_order=minimum,
    )


def _p(pid, canonical, price, qty, unit, vendor="v1", **kw) -> Product:
    return Product(
        id=pid,
        vendor_id=vendor,
        product_name=pid,
        canonical_product=canonical,
        price=price,
        package_quantity=qty,
        package_unit=unit,
        **kw,
    )


def _optimize(items, products, vendors, include_delivery=False, overbuy=0.0, **kw):
    req = OptimizeRequest(
        items=items,
        include_delivery=include_delivery,
        allow_overbuy_percent=overbuy,
        **kw,
    )
    return optimize(req, products, vendors)


def _service_optimize(monkeypatch, products, vendors, items, **updates):
    monkeypatch.setattr("app.services.load_catalog", lambda catalog_mode: (products, vendors))
    request = OptimizeRequest(
        items=items,
        include_delivery=True,
        allow_overbuy_percent=updates.pop("overbuy", 0.0),
        **updates,
    )
    return optimize_cart(request)


def _default_frontend_payload(**updates):
    payload = {
        "items": [
            {
                "product_type": "cow_milk",
                "quantity": 2,
                "unit": "gallon",
                "packaging_preference": "any",
                "salt_preference": "any",
                "storage_preference": "any",
            },
            {
                "product_type": "cream",
                "quantity": 4,
                "unit": "pint",
                "packaging_preference": "any",
                "salt_preference": "any",
                "storage_preference": "any",
            },
            {
                "product_type": "butter",
                "quantity": 5,
                "unit": "lb",
                "packaging_preference": "any",
                "salt_preference": "any",
                "storage_preference": "any",
            },
            {
                "product_type": "cheese",
                "quantity": 2,
                "unit": "lb",
                "packaging_preference": "any",
                "salt_preference": "any",
                "storage_preference": "any",
            },
            {
                "product_type": "eggs",
                "quantity": 36,
                "unit": "count",
                "packaging_preference": "any",
                "salt_preference": "any",
                "storage_preference": "any",
            },
        ],
        "include_delivery": True,
        "allow_overbuy_percent": 20,
        "excluded_vendor_ids": [],
        "excluded_product_ids": [],
        "required_vendor_ids": [],
        "max_vendors": None,
        "avoid_pickup_required": False,
        "fulfillment_mode": "best",
        "allowed_fulfillment_methods": [
            "pickup_dropoff",
            "ups_shipping",
            "farm_truck_delivery",
        ],
        "enforce_minimum_order": True,
        "cost_per_mile": 0.67,
        "vehicle_mpg": 25,
        "fuel_price_per_gallon": 4,
        "use_round_trip_pickup_cost": True,
        "max_pickup_distance_miles": None,
        "use_subscription_pricing": False,
        "packaging_preference": "any",
        "include_returnable_deposits_in_total": False,
        "storage_preference": "any",
        "salt_preference": "any",
        "catalog_mode": "demo_only",
    }
    payload.update(updates)
    return payload


def test_default_frontend_payload_with_mpg_fields_optimizes():
    response = TestClient(app).post("/optimize", json=_default_frontend_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["selected_items"]
    assert all(
        item["canonical_product"] != "sheep_milk"
        for item in data["unavailable_items"]
    )


def test_invalid_vehicle_mpg_zero_returns_validation_error():
    response = TestClient(app).post(
        "/optimize",
        json=_default_frontend_payload(vehicle_mpg=0),
    )
    assert response.status_code == 422


def test_old_cost_per_mile_payload_still_optimizes_without_vehicle_fields():
    payload = _default_frontend_payload()
    payload.pop("vehicle_mpg")
    payload.pop("fuel_price_per_gallon")
    response = TestClient(app).post("/optimize", json=payload)
    assert response.status_code == 200
    assert response.json()["selected_items"]


def test_mock_models_accept_optional_import_metadata_defaults():
    vendor = _vendor()
    product = _p("m", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon)
    assert vendor.source_type == "mock"
    assert vendor.login_required is False
    assert product.source_type == "mock"
    assert product.needs_review is False
    assert product.missing_fields == []


def test_import_candidate_validation_marks_missing_fields_for_review():
    candidate = ImportedProductCandidate(
        import_id="import-1",
        vendor_id="v1",
        name="Milk listing",
        raw_text="Milk - price missing",
    )
    checked = validate_import_candidate(candidate)
    assert checked.needs_review is True
    assert {"product_type", "detected_price", "detected_package_size", "detected_unit"} <= set(
        checked.missing_fields
    )
    assert normalize_imported_product(checked) is None


def test_normalize_imported_product_builds_product_when_candidate_is_complete():
    candidate = ImportedProductCandidate(
        import_id="import-2",
        vendor_id="v1",
        product_type=CanonicalProduct.cow_milk,
        name="Cow Milk - 1 Gallon",
        detected_price=10.0,
        detected_package_size=1.0,
        detected_unit=PackageUnit.gallon,
        stock_status="in_stock",
        parser_confidence=0.9,
        raw_text="Cow Milk - 1 Gallon $10",
        source_url="https://example.test/milk",
        needs_review=False,
    )
    product = normalize_imported_product(candidate)
    assert product is not None
    assert product.source_type == "imported"
    assert product.source_url == "https://example.test/milk"
    assert product.canonical_product == CanonicalProduct.cow_milk


def test_bulk_pack_wins_on_exact_match():
    products = [
        _p("b1", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb),
        _p("b5", CanonicalProduct.butter, 42.0, 5, PackageUnit.lb, bulk_deal=True),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=5, unit=PackageUnit.lb)],
        products, [_vendor()],
    )
    sel = res.selected_items[0]
    assert sel.item_cost == 42.0  # 5 lb pack beats 5x1lb ($45)
    assert sel.overbuy_amount == 0.0
    assert sel.lines[0].product_id == "b5"


def test_bulk_pack_rejected_by_overbuy_limit():
    products = [
        _p("b1", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb),
        _p("b5", CanonicalProduct.butter, 42.0, 5, PackageUnit.lb, bulk_deal=True),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products, [_vendor()], overbuy=20.0,
    )
    sel = res.selected_items[0]
    assert sel.item_cost == 9.0  # 5 lb pack would be 400% overbuy -> rejected
    assert sel.lines[0].product_id == "b1"


def test_cheese_chooses_2lb_package_when_cheapest():
    products = [
        _p("c_half", CanonicalProduct.cheese, 8.0, 8, PackageUnit.oz),
        _p("c_1", CanonicalProduct.cheese, 14.0, 1, PackageUnit.lb),
        _p("c_2", CanonicalProduct.cheese, 26.0, 2, PackageUnit.lb),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cheese, quantity=2, unit=PackageUnit.lb)],
        products, [_vendor()],
    )
    sel = res.selected_items[0]
    assert sel.item_cost == 26.0
    assert {line.product_id: line.package_count for line in sel.lines} == {"c_2": 1}
    assert sel.packages[0].product_name == "c_2"
    assert sel.packages[0].package_quantity == 2


def test_cheese_chooses_four_half_lb_packages_when_cheapest():
    products = [
        _p("c_half", CanonicalProduct.cheese, 7.0, 8, PackageUnit.oz),
        _p("c_1", CanonicalProduct.cheese, 15.0, 1, PackageUnit.lb),
        _p("c_2", CanonicalProduct.cheese, 32.0, 2, PackageUnit.lb),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cheese, quantity=2, unit=PackageUnit.lb)],
        products, [_vendor()],
    )
    sel = res.selected_items[0]
    assert sel.item_cost == 28.0
    assert {line.product_id: line.package_count for line in sel.lines} == {"c_half": 4}
    assert sel.purchased_quantity == 2
    assert sel.item_total == 28.0


def test_bulk_pack_allowed_when_overbuy_limit_is_high_enough():
    products = [
        _p("b1", CanonicalProduct.butter, 50.0, 1, PackageUnit.lb),
        _p("b5", CanonicalProduct.butter, 42.0, 5, PackageUnit.lb, bulk_deal=True),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products, [_vendor()], overbuy=500.0,
    )
    sel = res.selected_items[0]
    assert sel.item_cost == 42.0
    assert sel.lines[0].product_id == "b5"
    assert sel.overbuy_quantity == 4.0
    assert sel.overbuy_percent == 400.0


def test_unsalted_preference_skips_cheaper_salted_butter():
    products = [
        _p("b1", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb),
        _p("bs", CanonicalProduct.butter, 5.0, 1, PackageUnit.lb, is_unsalted=False),
    ]
    items = [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)]
    res = _optimize(items, products, [_vendor()], salt_preference="unsalted")
    assert res.selected_items[0].lines[0].product_id == "b1"
    res = _optimize(items, products, [_vendor()])
    assert res.selected_items[0].lines[0].product_id == "bs"  # default "any" takes the cheaper one
    res = _optimize(items, products, [_vendor()], salt_preference="salted")
    assert res.selected_items[0].lines[0].product_id == "bs"


def test_cheese_is_chosen_by_price_only():
    products = [
        _p("c2", CanonicalProduct.cheese, 26.0, 2, PackageUnit.lb),
        _p("cs", CanonicalProduct.cheese, 10.0, 1, PackageUnit.lb, is_unsalted=False),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cheese, quantity=2, unit=PackageUnit.lb)],
        products, [_vendor()],
    )
    assert res.selected_items[0].lines[0].product_id == "cs"



def test_out_of_stock_ignored():
    products = [
        _p("m_oos", CanonicalProduct.cow_milk, 5.0, 1, PackageUnit.gallon, in_stock=False),
        _p("m_ok", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products, [_vendor()],
    )
    assert res.selected_items[0].lines[0].product_id == "m_ok"


def test_out_of_stock_products_never_appear_in_selected_packages():
    products = [
        _p("m_oos", CanonicalProduct.cow_milk, 1.0, 1, PackageUnit.gallon, in_stock=False),
        _p("m_ok", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products, [_vendor()],
    )
    selected_names = [pkg.product_name for item in res.selected_items for pkg in item.packages]
    assert selected_names == ["m_ok"]
    assert "m_oos" not in selected_names


def test_sheep_milk_unavailable_when_no_vendor_has_it():
    products = [_p("m_ok", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon)]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.sheep_milk, quantity=1, unit=PackageUnit.gallon)],
        products, [_vendor()],
    )
    assert res.selected_items == []
    assert res.unavailable_items[0].canonical_product == CanonicalProduct.sheep_milk


def test_best_egg_combination_within_overbuy():
    # Need 36 eggs. Dozen ($11) x3 = $33 @0% overbuy beats 1 tray + 1 dozen ($36).
    products = [
        _p("e_doz", CanonicalProduct.eggs, 11.0, 1, PackageUnit.dozen),
        _p("e_tray", CanonicalProduct.eggs, 25.0, 30, PackageUnit.count, bulk_deal=True),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.eggs, quantity=36, unit=PackageUnit.count)],
        products, [_vendor()], overbuy=20.0,
    )
    sel = res.selected_items[0]
    assert sel.item_cost == 33.0
    assert sel.total_quantity_purchased == 36
    assert sel.overbuy_amount == 0.0


def test_egg_combination_chooses_tray_plus_dozen_when_cheaper():
    # Make dozens expensive so tray+dozen wins the combination search.
    products = [
        _p("e_doz", CanonicalProduct.eggs, 20.0, 1, PackageUnit.dozen),
        _p("e_tray", CanonicalProduct.eggs, 25.0, 30, PackageUnit.count, bulk_deal=True),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.eggs, quantity=36, unit=PackageUnit.count)],
        products, [_vendor()], overbuy=20.0,
    )
    sel = res.selected_items[0]
    ids = {line.product_id: line.package_count for line in sel.lines}
    assert ids == {"e_tray": 1, "e_doz": 1}  # 42 count, $45, within 20% overbuy
    assert sel.item_cost == 45.0


def test_delivery_fee_not_double_counted_same_vendor():
    products = [
        _p("b1", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb, vendor="v1"),
        _p("m1", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="v1"),
    ]
    res = _optimize(
        [
            OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb),
            OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon),
        ],
        products, [_vendor(vid="v1", fee=8.0)], include_delivery=True,
    )
    assert len(res.delivery_charges) == 1  # one vendor, one fee
    assert res.delivery_cost_total == 8.0


def test_delivery_fee_affects_full_cart_selection():
    products = [
        _p("milk_v1", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="v1"),
        _p("butter_v1", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="v1"),
        _p("butter_v2", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb, vendor="v2"),
    ]
    res = _optimize(
        [
            OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon),
            OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb),
        ],
        products,
        [_vendor(vid="v1", fee=8.0), _vendor(vid="v2", fee=8.0)],
        include_delivery=True,
    )
    selected = {item.canonical_product: item.vendor_id for item in res.selected_items}
    assert selected == {
        CanonicalProduct.cow_milk: "v1",
        CanonicalProduct.butter: "v1",
    }
    assert res.item_cost_total == 20.0
    assert res.delivery_cost_total == 8.0
    assert res.total_estimated_cost == 28.0
    assert res.alternative_carts[0].item_cost_total == 19.0
    assert res.alternative_carts[0].delivery_cost_total == 16.0
    assert res.alternative_carts[0].total_estimated_cost == 35.0


def test_handling_fee_charged_once_per_vendor_and_affects_selection():
    products = [
        _p("milk_v1", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="v1"),
        _p("butter_v1", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="v1"),
        _p("butter_v2", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb, vendor="v2"),
    ]
    res = _optimize(
        [
            OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon),
            OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb),
        ],
        products,
        [
            _vendor(vid="v1", fee=0.0, handling=3.0),
            _vendor(vid="v2", fee=0.0, handling=3.0),
        ],
        include_delivery=False,
    )
    selected = {item.canonical_product: item.vendor_id for item in res.selected_items}
    assert selected[CanonicalProduct.butter] == "v1"
    assert len(res.handling_charges) == 1
    assert res.handling_fee_total == 3.0
    assert res.total_estimated_cost == 23.0


def test_pickup_only_vendor_marked_in_full_cart_plan():
    products = [
        _p("butter_pickup", CanonicalProduct.butter, 8.0, 1, PackageUnit.lb, vendor="pickup"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products,
        [_vendor(vid="pickup", delivery=False)],
        include_delivery=True,
    )
    assert res.selected_items[0].pickup_required is True
    assert "pickup required" in res.notes[0]


def test_excluded_vendor_ids_prevents_vendor_in_best_cart():
    products = [
        _p("butter_v1", CanonicalProduct.butter, 8.0, 1, PackageUnit.lb, vendor="v1"),
        _p("butter_v2", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="v2"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products,
        [_vendor(vid="v1"), _vendor(vid="v2")],
        excluded_vendor_ids=["v1"],
    )
    assert res.selected_items[0].vendor_id == "v2"


def test_excluded_product_ids_prevents_package_selection():
    products = [
        _p("b5", CanonicalProduct.butter, 42.0, 5, PackageUnit.lb, bulk_deal=True),
        _p("b1", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=5, unit=PackageUnit.lb)],
        products,
        [_vendor()],
        excluded_product_ids=["b5"],
    )
    assert {line.product_id: line.package_count for line in res.selected_items[0].lines} == {"b1": 5}


def test_max_vendors_one_returns_single_vendor_plan():
    products = [
        _p("milk_v1", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="v1"),
        _p("butter_v1", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="v1"),
        _p("milk_v2", CanonicalProduct.cow_milk, 1.0, 1, PackageUnit.gallon, vendor="v2"),
        _p("butter_v3", CanonicalProduct.butter, 1.0, 1, PackageUnit.lb, vendor="v3"),
    ]
    res = _optimize(
        [
            OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon),
            OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb),
        ],
        products,
        [_vendor(vid="v1"), _vendor(vid="v2"), _vendor(vid="v3")],
        max_vendors=1,
    )
    assert {item.vendor_id for item in res.selected_items} == {"v1"}


def test_delivery_only_rejects_pickup_only_vendors():
    products = [
        _p("butter_pickup", CanonicalProduct.butter, 8.0, 1, PackageUnit.lb, vendor="pickup"),
        _p("butter_delivery", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="delivery"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products,
        [_vendor(vid="pickup", delivery=False), _vendor(vid="delivery", delivery=True)],
        fulfillment_mode="delivery_only",
    )
    assert res.selected_items[0].vendor_id == "delivery"


def test_pickup_only_ignores_delivery_fees():
    products = [
        _p("milk_v1", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="v1"),
        _p("butter_v1", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="v1"),
        _p("butter_v2", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb, vendor="v2"),
    ]
    res = _optimize(
        [
            OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon),
            OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb),
        ],
        products,
        [_vendor(vid="v1", fee=8.0), _vendor(vid="v2", fee=8.0)],
        include_delivery=True,
        fulfillment_mode="pickup_only",
    )
    selected = {item.canonical_product: item.vendor_id for item in res.selected_items}
    assert selected[CanonicalProduct.butter] == "v2"
    assert res.delivery_cost_total == 0.0
    assert res.total_estimated_cost == 19.0


def test_avoid_pickup_required_prefers_delivery_when_valid_cart_exists():
    products = [
        _p("butter_pickup", CanonicalProduct.butter, 8.0, 1, PackageUnit.lb, vendor="pickup"),
        _p("butter_delivery", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="delivery"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products,
        [_vendor(vid="pickup", delivery=False), _vendor(vid="delivery", delivery=True)],
        avoid_pickup_required=True,
    )
    assert res.selected_items[0].vendor_id == "delivery"
    assert res.notes == []


def test_required_vendor_ids_forces_vendor_when_possible():
    products = [
        _p("butter_v1", CanonicalProduct.butter, 8.0, 1, PackageUnit.lb, vendor="v1"),
        _p("butter_v2", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="v2"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products,
        [_vendor(vid="v1"), _vendor(vid="v2")],
        required_vendor_ids=["v2"],
    )
    assert res.selected_items[0].vendor_id == "v2"


def test_pickup_only_with_avoid_pickup_does_not_reject_pickup_vendors():
    products = [
        _p("butter_pickup", CanonicalProduct.butter, 8.0, 1, PackageUnit.lb, vendor="pickup"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products,
        [_vendor(vid="pickup", delivery=False)],
        include_delivery=True,
        fulfillment_mode="pickup_only",
        avoid_pickup_required=True,
    )
    assert res.selected_items[0].vendor_id == "pickup"
    assert res.delivery_cost_total == 0.0


def test_pickup_only_with_avoid_pickup_behaves_like_pickup_only_without_warning():
    products = [
        _p("butter_pickup", CanonicalProduct.butter, 8.0, 1, PackageUnit.lb, vendor="pickup"),
        _p("butter_delivery", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="delivery"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products,
        [_vendor(vid="pickup", delivery=False), _vendor(vid="delivery", delivery=True)],
        include_delivery=True,
        fulfillment_mode="pickup_only",
        avoid_pickup_required=True,
    )
    assert res.selected_items[0].vendor_id == "pickup"
    assert res.notes == []
    assert res.selected_items[0].notes == []


def test_delivery_only_never_returns_pickup_only_vendor():
    products = [
        _p("milk_delivery", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="delivery"),
        _p("butter_delivery", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb, vendor="delivery"),
        _p("milk_pickup", CanonicalProduct.cow_milk, 1.0, 1, PackageUnit.gallon, vendor="pickup"),
        _p("butter_pickup", CanonicalProduct.butter, 1.0, 1, PackageUnit.lb, vendor="pickup"),
    ]
    res = _optimize(
        [
            OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon),
            OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb),
        ],
        products,
        [_vendor(vid="delivery", delivery=True), _vendor(vid="pickup", delivery=False)],
        fulfillment_mode="delivery_only",
    )
    assert {item.vendor_id for item in res.selected_items} == {"delivery"}


def test_mixed_avoid_pickup_falls_back_only_when_needed_with_warning():
    products = [
        _p("butter_pickup", CanonicalProduct.butter, 8.0, 1, PackageUnit.lb, vendor="pickup"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        products,
        [_vendor(vid="pickup", delivery=False)],
        avoid_pickup_required=True,
    )
    assert res.selected_items[0].vendor_id == "pickup"
    assert any("Pickup-only fallback" in note for note in res.notes)


def test_pickup_only_rejects_ups_and_farm_truck_only_vendors():
    products = [
        _p("m_pickup", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="pickup"),
        _p("m_ups", CanonicalProduct.cow_milk, 1.0, 1, PackageUnit.gallon, vendor="ups",
           pickup_dropoff_available=False, ups_shipping_available=True),
        _p("m_truck", CanonicalProduct.cow_milk, 1.0, 1, PackageUnit.gallon, vendor="truck",
           pickup_dropoff_available=False, farm_truck_delivery_available=True),
    ]
    vendors = [
        _vendor(vid="pickup", delivery=False),
        _vendor(vid="ups", delivery=True),
        _vendor(vid="truck", delivery=False),
    ]
    vendors[0].pickup_dropoff_available = True
    vendors[1].pickup_dropoff_available = False
    vendors[1].ups_shipping_available = True
    vendors[2].pickup_dropoff_available = False
    vendors[2].farm_truck_delivery_available = True
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products, vendors, fulfillment_mode="pickup_only",
    )
    assert res.selected_items[0].vendor_id == "pickup"


def test_ups_only_rejects_pickup_and_farm_truck_only_vendors():
    products = [
        _p("m_pickup", CanonicalProduct.cow_milk, 1.0, 1, PackageUnit.gallon, vendor="pickup",
           ups_shipping_available=False),
        _p("m_ups", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="ups",
           ups_shipping_available=True),
        _p("m_truck", CanonicalProduct.cow_milk, 1.0, 1, PackageUnit.gallon, vendor="truck",
           ups_shipping_available=False, farm_truck_delivery_available=True),
    ]
    vendors = [_vendor(vid="pickup", delivery=False), _vendor(vid="ups"), _vendor(vid="truck", delivery=False)]
    vendors[1].ups_shipping_available = True
    vendors[2].farm_truck_delivery_available = True
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products, vendors, fulfillment_mode="ups_only",
    )
    assert res.selected_items[0].vendor_id == "ups"
    assert res.vendor_breakdowns[0].fulfillment_method == "ups_shipping"


def test_pickup_cost_uses_round_trip_distance_and_fees():
    vendor = _vendor(vid="v1", delivery=False)
    vendor.pickup_dropoff_available = True
    vendor.pickup_locations = [
        Vendor.PickupLocation(
            id="drop", name="Drop", address="A", distance_miles=10,
            pickup_fee=3, pickup_handling_fee=2,
        )
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        [_p("m", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon)],
        [vendor], fulfillment_mode="pickup_only", cost_per_mile=1.0,
    )
    breakdown = res.vendor_breakdowns[0]
    assert breakdown.pickup_travel_cost == 20.0
    assert breakdown.fulfillment_cost == 25.0


def test_pickup_cost_uses_one_way_distance_when_round_trip_disabled_and_cheapest_location():
    vendor = _vendor(vid="v1", delivery=False)
    vendor.pickup_dropoff_available = True
    vendor.pickup_locations = [
        Vendor.PickupLocation(id="far", name="Far", address="A", distance_miles=20),
        Vendor.PickupLocation(id="near", name="Near", address="B", distance_miles=5),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        [_p("m", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon)],
        [vendor], fulfillment_mode="pickup_only", cost_per_mile=1.0,
        use_round_trip_pickup_cost=False,
    )
    assert res.vendor_breakdowns[0].pickup_location_id == "near"
    assert res.vendor_breakdowns[0].pickup_travel_cost == 5.0


def test_pickup_cost_derives_cost_per_mile_from_mpg_and_fuel_price():
    vendor = _vendor(vid="v1", delivery=False)
    vendor.pickup_dropoff_available = True
    vendor.pickup_locations = [
        Vendor.PickupLocation(id="drop", name="Drop", address="A", distance_miles=10)
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        [_p("m", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon)],
        [vendor],
        fulfillment_mode="pickup_only",
        cost_per_mile=99.0,
        vehicle_mpg=20.0,
        fuel_price_per_gallon=4.0,
    )
    assert res.vendor_breakdowns[0].pickup_travel_cost == 4.0


def test_pickup_cost_uses_mpg_for_one_way_when_round_trip_disabled():
    vendor = _vendor(vid="v1", delivery=False)
    vendor.pickup_dropoff_available = True
    vendor.pickup_locations = [
        Vendor.PickupLocation(id="drop", name="Drop", address="A", distance_miles=10)
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        [_p("m", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon)],
        [vendor],
        fulfillment_mode="pickup_only",
        use_round_trip_pickup_cost=False,
        cost_per_mile=99.0,
        vehicle_mpg=20.0,
        fuel_price_per_gallon=4.0,
    )
    assert res.vendor_breakdowns[0].pickup_travel_cost == 2.0


def test_minimum_order_warning_when_not_enforced():
    vendor = _vendor(vid="v1", delivery=True)
    vendor.ups_shipping_available = True
    vendor.minimum_order_ups = 75.0
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        [_p("b", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb)],
        [vendor], fulfillment_mode="ups_only", enforce_minimum_order=False,
    )
    assert res.vendor_breakdowns[0].amount_short == 65.0
    assert any("Add $65.00 more" in warning for warning in res.warnings)


def test_subscription_discount_and_fixed_price():
    products = [
        _p("b_discount", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb,
           subscription_available=True, subscription_discount_percent=10),
        _p("b_fixed", CanonicalProduct.butter, 20.0, 1, PackageUnit.lb,
           subscription_available=True, subscription_discount_percent=50,
           subscription_price=7.0),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=2, unit=PackageUnit.lb)],
        products, [_vendor()], use_subscription_pricing=True,
    )
    assert res.product_subtotal == 14.0
    assert res.subscription_savings == 26.0


def test_glass_only_rejects_plastic_and_deposits_are_separate():
    products = [
        _p("plastic", CanonicalProduct.cow_milk, 5.0, 1, PackageUnit.gallon, packaging="plastic"),
        _p("glass", CanonicalProduct.cow_milk, 6.0, 1, PackageUnit.gallon,
           packaging="glass", container_deposit=2.0, container_returnable=True),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products, [_vendor()], packaging_preference="glass_only",
    )
    assert res.selected_items[0].lines[0].product_id == "glass"
    assert res.total_estimated_cost == 6.0
    assert res.returnable_deposits == 2.0
    assert res.total_due_today == 8.0


def test_fresh_only_rejects_frozen_products():
    products = [
        _p("frozen_milk", CanonicalProduct.cow_milk, 1.0, 1, PackageUnit.gallon, storage_state="frozen"),
        _p("fresh_milk", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, storage_state="fresh"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products, [_vendor()], storage_preference="fresh_only",
    )
    assert res.selected_items[0].lines[0].product_id == "fresh_milk"


def test_item_salt_preference_applies_to_butter_only():
    products = [
        _p("butter_salted", CanonicalProduct.butter, 1.0, 1, PackageUnit.lb, is_unsalted=False),
        _p("butter_ok", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb),
        _p("cheese_salted", CanonicalProduct.cheese, 1.0, 1, PackageUnit.lb, is_unsalted=False),
        _p("cheese_ok", CanonicalProduct.cheese, 10.0, 1, PackageUnit.lb),
    ]
    res = _optimize(
        [
            OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb, salt_preference="unsalted"),
            OptimizeItem(canonical_product=CanonicalProduct.cheese, quantity=1, unit=PackageUnit.lb),
        ],
        products,
        [_vendor()],
    )
    selected_ids = {line.product_id for item in res.selected_items for line in item.lines}
    assert selected_ids == {"butter_ok", "cheese_salted"}


def test_farm_truck_not_selected_without_vendor_support_even_if_product_supports_it():
    products = [
        _p(
            "milk_truck",
            CanonicalProduct.cow_milk,
            1.0,
            1,
            PackageUnit.gallon,
            farm_truck_delivery_available=True,
            pickup_dropoff_available=False,
            ups_shipping_available=False,
        )
    ]
    vendor = _vendor(delivery=False)
    vendor.pickup_dropoff_available = False
    vendor.ups_shipping_available = False
    vendor.farm_truck_delivery_available = False
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products,
        [vendor],
        fulfillment_mode="farm_truck_only",
    )
    assert res.selected_items == []
    assert res.unavailable_items[0].canonical_product == CanonicalProduct.cow_milk


def test_mixed_mode_compares_pickup_ups_and_farm_truck_methods():
    pickup_vendor = _vendor(vid="pickup", delivery=False)
    pickup_vendor.pickup_dropoff_available = True
    pickup_vendor.pickup_locations = [
        Vendor.PickupLocation(id="p", name="Pickup", address="A", distance_miles=20)
    ]
    ups_vendor = _vendor(vid="ups", delivery=True)
    ups_vendor.pickup_dropoff_available = False
    ups_vendor.ups_shipping_available = True
    ups_vendor.ups_shipping_fee = 3.0
    truck_vendor = _vendor(vid="truck", delivery=True)
    truck_vendor.pickup_dropoff_available = False
    truck_vendor.ups_shipping_available = False
    truck_vendor.farm_truck_delivery_available = True
    truck_vendor.farm_truck_delivery_fee = 1.0
    products = [
        _p("milk_pickup", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="pickup"),
        _p("milk_ups", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, vendor="ups", pickup_dropoff_available=False),
        _p(
            "milk_truck",
            CanonicalProduct.cow_milk,
            10.0,
            1,
            PackageUnit.gallon,
            vendor="truck",
            pickup_dropoff_available=False,
            ups_shipping_available=False,
            farm_truck_delivery_available=True,
        ),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products,
        [pickup_vendor, ups_vendor, truck_vendor],
        fulfillment_mode="best",
    )
    assert res.selected_items[0].vendor_id == "truck"
    assert res.vendor_breakdowns[0].fulfillment_method == "farm_truck_delivery"


def test_max_pickup_distance_rejects_too_far_locations():
    vendor = _vendor(delivery=False)
    vendor.pickup_dropoff_available = True
    vendor.pickup_locations = [
        Vendor.PickupLocation(id="far", name="Far", address="A", distance_miles=25),
        Vendor.PickupLocation(id="near", name="Near", address="B", distance_miles=4),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        [_p("milk", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon)],
        [vendor],
        fulfillment_mode="pickup_only",
        max_pickup_distance_miles=5,
    )
    assert res.vendor_breakdowns[0].pickup_location_id == "near"


def test_ups_cost_includes_ups_and_shared_fees_but_not_pickup_travel():
    vendor = _vendor(delivery=True, handling=2.0)
    vendor.ups_shipping_available = True
    vendor.pickup_dropoff_available = True
    vendor.ups_shipping_fee = 10.0
    vendor.ups_packing_fee = 3.0
    vendor.ups_cooler_fee = 4.0
    vendor.packing_fee = 5.0
    vendor.cooler_fee = 6.0
    vendor.service_fee = 7.0
    vendor.pickup_locations = [
        Vendor.PickupLocation(id="far", name="Far", address="A", distance_miles=100)
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        [_p("butter", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb)],
        [vendor],
        fulfillment_mode="ups_only",
        cost_per_mile=10.0,
    )
    assert res.vendor_breakdowns[0].fulfillment_cost == 37.0
    assert res.vendor_breakdowns[0].pickup_travel_cost == 0.0
    assert res.vendor_breakdowns[0].quote_source == "rule_based_estimate"
    assert res.vendor_breakdowns[0].quote_status == "estimated"


def test_fulfillment_estimate_uses_selected_package_counts():
    vendor = _vendor(delivery=True)
    vendor.ups_shipping_available = True
    vendor.pickup_dropoff_available = False
    vendor.ups_shipping_fee = 10.0
    vendor.per_package_fee = 1.25
    vendor.per_cold_item_fee = 2.0
    products = [
        _p(
            "cream",
            CanonicalProduct.cream,
            5.0,
            1,
            PackageUnit.pint,
            storage_state="refrigerated",
        )
    ]

    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cream, quantity=2, unit=PackageUnit.pint)],
        products,
        [vendor],
        fulfillment_mode="ups_only",
    )

    breakdown = res.vendor_breakdowns[0]
    selected_package = res.selected_items[0].packages[0]
    assert selected_package.stock_status == "in_stock"
    assert selected_package.listed_package_quantity == 1.0
    assert selected_package.listed_package_unit == PackageUnit.pint
    assert breakdown.package_count == 2
    assert breakdown.cold_item_count == 2
    assert breakdown.per_package_fee == 2.5
    assert breakdown.per_cold_item_fee == 4.0
    assert breakdown.fulfillment_cost == 16.5
    assert breakdown.quote_source == "rule_based_estimate"
    assert any("not a captured checkout quote" in warning for warning in breakdown.warnings)


def test_minimum_order_enforced_rejects_below_minimum_plan():
    vendor = _vendor(delivery=True)
    vendor.ups_shipping_available = True
    vendor.minimum_order_ups = 75.0
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        [_p("butter", CanonicalProduct.butter, 10.0, 1, PackageUnit.lb)],
        [vendor],
        fulfillment_mode="ups_only",
        enforce_minimum_order=True,
    )
    assert res.selected_items == []
    assert res.total_estimated_cost == 0.0


def test_returnable_deposits_can_be_included_in_estimated_total():
    products = [
        _p(
            "glass",
            CanonicalProduct.cow_milk,
            6.0,
            1,
            PackageUnit.gallon,
            packaging="glass",
            container_deposit=2.0,
            container_returnable=True,
        )
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products,
        [_vendor()],
        include_returnable_deposits_in_total=True,
    )
    assert res.returnable_deposits == 2.0
    assert res.total_estimated_cost == 8.0
    assert res.total_due_today == 8.0


def test_avoid_plastic_prefers_non_plastic_when_available():
    products = [
        _p("plastic", CanonicalProduct.cow_milk, 5.0, 1, PackageUnit.gallon, packaging="plastic"),
        _p("glass", CanonicalProduct.cow_milk, 6.0, 1, PackageUnit.gallon, packaging="glass"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products,
        [_vendor()],
        packaging_preference="avoid_plastic",
    )
    assert res.selected_items[0].lines[0].product_id == "glass"


def test_prefer_glass_choose_glass_when_within_ten_percent():
    products = [
        _p("plastic", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, packaging="plastic"),
        _p("glass", CanonicalProduct.cow_milk, 10.5, 1, PackageUnit.gallon, packaging="glass"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products,
        [_vendor()],
        packaging_preference="prefer_glass",
    )
    assert res.selected_items[0].lines[0].product_id == "glass"


def test_prefer_fresh_chooses_fresh_within_ten_percent():
    products = [
        _p("frozen_milk", CanonicalProduct.cow_milk, 10.0, 1, PackageUnit.gallon, storage_state="frozen"),
        _p("fresh_milk", CanonicalProduct.cow_milk, 10.5, 1, PackageUnit.gallon, storage_state="fresh"),
    ]
    res = _optimize(
        [OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products,
        [_vendor()],
        storage_preference="prefer_fresh",
    )
    assert res.selected_items[0].lines[0].product_id == "fresh_milk"


def test_item_level_glass_only_does_not_force_glass_for_unrelated_items():
    products = [
        _p("milk_plastic", CanonicalProduct.cow_milk, 5.0, 1, PackageUnit.gallon, packaging="plastic"),
        _p("milk_glass", CanonicalProduct.cow_milk, 6.0, 1, PackageUnit.gallon, packaging="glass"),
        _p("eggs_plain", CanonicalProduct.eggs, 10.0, 1, PackageUnit.dozen, packaging="plastic"),
    ]
    res = _optimize(
        [
            OptimizeItem(
                product_type=CanonicalProduct.cow_milk,
                quantity=1,
                unit=PackageUnit.gallon,
                packaging_preference="glass_only",
            ),
            OptimizeItem(product_type=CanonicalProduct.eggs, quantity=12, unit=PackageUnit.count),
        ],
        products,
        [_vendor()],
    )
    selected_ids = {line.product_id for item in res.selected_items for line in item.lines}
    assert "milk_glass" in selected_ids
    assert "eggs_plain" in selected_ids


def test_item_level_salt_preference_does_not_reject_unrelated_eggs():
    products = [
        _p("butter_salted", CanonicalProduct.butter, 5.0, 1, PackageUnit.lb, is_unsalted=False),
        _p("butter_plain", CanonicalProduct.butter, 6.0, 1, PackageUnit.lb),
        _p("eggs_plain", CanonicalProduct.eggs, 10.0, 1, PackageUnit.dozen),
    ]
    res = _optimize(
        [
            OptimizeItem(
                product_type=CanonicalProduct.butter,
                quantity=1,
                unit=PackageUnit.lb,
                salt_preference="unsalted",
            ),
            OptimizeItem(product_type=CanonicalProduct.eggs, quantity=12, unit=PackageUnit.count),
        ],
        products,
        [_vendor()],
    )
    selected_ids = {line.product_id for item in res.selected_items for line in item.lines}
    assert "butter_plain" in selected_ids
    assert "eggs_plain" in selected_ids


def test_item_level_any_packaging_can_pick_cheapest_despite_global_default():
    products = [
        _p("milk_plastic", CanonicalProduct.cow_milk, 5.0, 1, PackageUnit.gallon, packaging="plastic"),
        _p("milk_glass", CanonicalProduct.cow_milk, 6.0, 1, PackageUnit.gallon, packaging="glass"),
    ]
    res = _optimize(
        [
            OptimizeItem(
                product_type=CanonicalProduct.cow_milk,
                quantity=1,
                unit=PackageUnit.gallon,
                packaging_preference="any",
            )
        ],
        products,
        [_vendor()],
        packaging_preference="glass_only",
    )
    assert res.selected_items[0].lines[0].product_id == "milk_plastic"


def test_global_default_preference_applies_when_item_missing_preference():
    products = [
        _p("milk_plastic", CanonicalProduct.cow_milk, 5.0, 1, PackageUnit.gallon, packaging="plastic"),
        _p("milk_glass", CanonicalProduct.cow_milk, 6.0, 1, PackageUnit.gallon, packaging="glass"),
    ]
    res = _optimize(
        [OptimizeItem(product_type=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon)],
        products,
        [_vendor()],
        packaging_preference="glass_only",
    )
    assert res.selected_items[0].lines[0].product_id == "milk_glass"


def test_item_level_preference_overrides_global_default():
    products = [
        _p("milk_plastic", CanonicalProduct.cow_milk, 5.0, 1, PackageUnit.gallon, packaging="plastic"),
        _p("milk_glass", CanonicalProduct.cow_milk, 6.0, 1, PackageUnit.gallon, packaging="glass"),
    ]
    res = _optimize(
        [
            OptimizeItem(
                product_type=CanonicalProduct.cow_milk,
                quantity=1,
                unit=PackageUnit.gallon,
                packaging_preference="any",
            )
        ],
        products,
        [_vendor()],
        packaging_preference="glass_only",
    )
    assert res.selected_items[0].packaging_preference == "any"
    assert res.selected_items[0].lines[0].product_id == "milk_plastic"


DEMO_VENDOR_NAMES = {
    "Example Farm E",
    "Example Farm F",
    "Example Farm G",
}


def _vendor_names_from_response(data: dict) -> set[str]:
    names: set[str] = set()
    for item in data.get("selected_items", []):
        names.add(item["vendor_name"])
    for cart in data.get("alternative_carts", []):
        for item in cart.get("selected_items", []):
            names.add(item["vendor_name"])
    return names


def _write_staged_products(path, products):
    path.write_text(
        json.dumps([product.model_dump(mode="json") for product in products]),
        encoding="utf-8",
    )


def _imported_vendor_a_product(pid, canonical, qty, unit, price=10.0, **updates):
    in_stock = updates.pop("in_stock", True)
    needs_review = updates.pop("needs_review", False)
    return _p(
        pid,
        canonical,
        price,
        qty,
        unit,
        vendor="vendor_a",
        source_type="imported",
        in_stock=in_stock,
        needs_review=needs_review,
        **updates,
    )


def test_products_imported_only_returns_current_imported_schema(tmp_path, monkeypatch):
    staged_path = tmp_path / "staged_products.json"
    monkeypatch.setattr("app.fetcher.import_candidates.STAGED_PRODUCTS_PATH", staged_path)
    _write_staged_products(
        staged_path,
        [
            _imported_vendor_a_product(
                "hf_milk_schema",
                CanonicalProduct.cow_milk,
                1,
                PackageUnit.gallon,
            )
        ],
    )

    response = TestClient(app).get("/products", params={"catalog_mode": "imported_only"})

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data[0]["product_name"] == "hf_milk_schema"
    assert data[0]["canonical_product"] == "cow_milk"
    assert data[0]["package_quantity"] == 1.0
    assert data[0]["package_unit"] == "gallon"
    assert data[0]["in_stock"] is True
    assert data[0]["source_type"] == "imported"


def test_optimizer_ready_products_filters_and_dedupes_current_import_schema():
    duplicate_a = _imported_vendor_a_product(
        "milk_a",
        CanonicalProduct.cow_milk,
        1,
        PackageUnit.gallon,
        price=12.0,
        packaging="glass",
    )
    duplicate_b = _imported_vendor_a_product(
        "milk_b",
        CanonicalProduct.cow_milk,
        1,
        PackageUnit.gallon,
        price=10.0,
        packaging="glass",
    )
    needs_review = _imported_vendor_a_product(
        "cream_review",
        CanonicalProduct.cream,
        1,
        PackageUnit.pint,
        needs_review=True,
    )
    missing = _imported_vendor_a_product(
        "butter_missing",
        CanonicalProduct.butter,
        1,
        PackageUnit.lb,
        missing_fields=["package_unit"],
    )
    out = _imported_vendor_a_product(
        "butter_oos",
        CanonicalProduct.butter,
        1,
        PackageUnit.lb,
        in_stock=False,
    )
    invalid_unit = _imported_vendor_a_product(
        "cheese_pint",
        CanonicalProduct.cheese,
        1,
        PackageUnit.pint,
    )

    excluded = {}
    ready = optimizer_ready_products(
        [duplicate_a, duplicate_b, needs_review, missing, out, invalid_unit],
        excluded,
    )

    assert [product.id for product in ready] == ["milk_b"]
    assert ready[0].canonical_product == CanonicalProduct.cow_milk
    assert ready[0].package_quantity == 1
    assert ready[0].package_unit == PackageUnit.gallon
    assert ready[0].in_stock is True
    assert excluded["deduped_equivalent"] == 1
    assert excluded["needs_review"] == 1
    assert excluded["missing_required_fields"] == 1
    assert excluded["out_of_stock"] == 1
    assert excluded["invalid_unit"] == 1


def test_optimize_imported_only_current_schema_milk_cream_butter(tmp_path, monkeypatch):
    staged_path = tmp_path / "staged_products.json"
    monkeypatch.setattr("app.fetcher.import_candidates.STAGED_PRODUCTS_PATH", staged_path)
    _write_staged_products(
        staged_path,
        [
            _imported_vendor_a_product("hf_milk", CanonicalProduct.cow_milk, 1, PackageUnit.gallon),
            _imported_vendor_a_product("hf_cream", CanonicalProduct.cream, 1, PackageUnit.pint),
            _imported_vendor_a_product("hf_butter", CanonicalProduct.butter, 1, PackageUnit.lb),
        ],
    )

    for item in [
        {"product_type": "cow_milk", "quantity": 1, "unit": "gallon"},
        {"product_type": "cream", "quantity": 1, "unit": "pint"},
        {"product_type": "butter", "quantity": 1, "unit": "lb"},
    ]:
        response = TestClient(app).post(
            "/optimize",
            json={"items": [item], "catalog_mode": "imported_only"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["selected_items"]
        assert data["unavailable_items"] == []
        assert not _vendor_names_from_response(data) & DEMO_VENDOR_NAMES

    response = TestClient(app).post(
        "/optimize",
        json={
            "items": [
                {"product_type": "cow_milk", "quantity": 1, "unit": "gallon"},
                {"product_type": "cream", "quantity": 1, "unit": "pint"},
                {"product_type": "butter", "quantity": 1, "unit": "lb"},
            ],
            "catalog_mode": "imported_only",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert {item["canonical_product"] for item in data["selected_items"]} == {
        "cow_milk",
        "cream",
        "butter",
    }
    assert data["unavailable_items"] == []
    assert data["catalog_diagnostics"]["approved_loaded_count"] == 3
    assert data["catalog_diagnostics"]["optimizer_ready_count"] == 3
    assert data["catalog_diagnostics"]["products_by_vendor"] == {"vendor_a": 3}
    assert data["catalog_diagnostics"]["products_by_type"] == {
        "butter": 1,
        "cow_milk": 1,
        "cream": 1,
    }
    assert not _vendor_names_from_response(data) & DEMO_VENDOR_NAMES


def test_imported_only_no_cart_includes_unavailable_warning(tmp_path, monkeypatch):
    staged_path = tmp_path / "staged_products.json"
    monkeypatch.setattr("app.fetcher.import_candidates.STAGED_PRODUCTS_PATH", staged_path)
    _write_staged_products(
        staged_path,
        [_imported_vendor_a_product("hf_milk", CanonicalProduct.cow_milk, 1, PackageUnit.gallon)],
    )

    response = TestClient(app).post(
        "/optimize",
        json={
            "items": [{"product_type": "cream", "quantity": 1, "unit": "pint"}],
            "catalog_mode": "imported_only",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["selected_items"] == []
    assert data["unavailable_items"][0]["canonical_product"] == "cream"
    assert data["unavailable_items"][0]["reason"] == "no_approved_products"
    assert "cream" in " ".join(data["warnings"])
    assert data["catalog_diagnostics"]["approved_loaded_count"] == 1
    assert data["catalog_diagnostics"]["optimizer_ready_count"] == 1


def test_imported_only_does_not_load_demo_products(tmp_path, monkeypatch):
    # Isolate from whatever real products happen to be staged so this test
    # deterministically asserts the demo-exclusion behavior (not the current
    # contents of staged_products.json).
    monkeypatch.setattr(
        "app.fetcher.import_candidates.STAGED_PRODUCTS_PATH",
        tmp_path / "staged_products.json",
    )
    response = TestClient(app).post(
        "/optimize",
        json=_default_frontend_payload(catalog_mode="imported_only"),
    )
    assert response.status_code == 200
    data = response.json()
    assert not _vendor_names_from_response(data) & DEMO_VENDOR_NAMES
    assert len(data["selected_items"]) == 0
    assert {item["canonical_product"] for item in data["unavailable_items"]} == {
        "cow_milk",
        "cream",
        "butter",
        "cheese",
        "eggs",
    }


def test_demo_only_loads_demo_products():
    response = TestClient(app).post(
        "/optimize",
        json=_default_frontend_payload(catalog_mode="demo_only"),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["selected_items"]
    assert _vendor_names_from_response(data) & DEMO_VENDOR_NAMES


def test_imported_plus_demo_loads_both(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.fetcher.import_candidates.STAGED_PRODUCTS_PATH",
        tmp_path / "staged_products.json",
    )
    staged = _p(
        "hf_milk",
        CanonicalProduct.cow_milk,
        10.0,
        1,
        PackageUnit.gallon,
        vendor="vendor_a",
        source_type="imported",
        in_stock=True,
        needs_review=False,
    )
    (tmp_path / "staged_products.json").write_text(
        f'[{staged.model_dump_json()}]',
        encoding="utf-8",
    )
    response = TestClient(app).post(
        "/optimize",
        json={
            "items": [
                {
                    "product_type": "cow_milk",
                    "quantity": 1,
                    "unit": "gallon",
                }
            ],
            "include_delivery": False,
            "catalog_mode": "imported_plus_demo",
        },
    )
    assert response.status_code == 200
    data = response.json()
    vendor_names = _vendor_names_from_response(data)
    assert "Example Farm A" in vendor_names or "vendor_a" in {
        item["vendor_id"] for item in data["selected_items"]
    }
    assert vendor_names & DEMO_VENDOR_NAMES or len(data["selected_items"]) >= 1


def test_approved_imported_product_can_be_selected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.fetcher.import_candidates.STAGED_PRODUCTS_PATH",
        tmp_path / "staged_products.json",
    )
    staged = _p(
        "hf_milk_gal",
        CanonicalProduct.cow_milk,
        10.0,
        1,
        PackageUnit.gallon,
        vendor="vendor_a",
        source_type="imported",
        in_stock=True,
        needs_review=False,
    )
    (tmp_path / "staged_products.json").write_text(
        f'[{staged.model_dump_json()}]',
        encoding="utf-8",
    )
    response = TestClient(app).post(
        "/optimize",
        json={
            "items": [
                {
                    "product_type": "cow_milk",
                    "quantity": 1,
                    "unit": "gallon",
                }
            ],
            "include_delivery": False,
            "catalog_mode": "imported_only",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["selected_items"]) == 1
    assert data["selected_items"][0]["canonical_product"] == "cow_milk"
    assert data["selected_items"][0]["vendor_id"] == "vendor_a"
    assert data["unavailable_items"] == []


def test_rejected_and_needs_review_imported_products_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.fetcher.import_candidates.STAGED_PRODUCTS_PATH",
        tmp_path / "staged_products.json",
    )
    approved = _p(
        "hf_ok",
        CanonicalProduct.cow_milk,
        10.0,
        1,
        PackageUnit.gallon,
        vendor="vendor_a",
        source_type="imported",
        in_stock=True,
        needs_review=False,
    )
    needs_review = _p(
        "hf_review",
        CanonicalProduct.cream,
        8.0,
        1,
        PackageUnit.pint,
        vendor="vendor_a",
        source_type="imported",
        in_stock=True,
        needs_review=True,
    )
    out_of_stock = _p(
        "hf_oos",
        CanonicalProduct.eggs,
        6.0,
        12,
        PackageUnit.count,
        vendor="vendor_a",
        source_type="imported",
        in_stock=False,
        needs_review=False,
    )
    (tmp_path / "staged_products.json").write_text(
        json.dumps(
            [
                approved.model_dump(mode="json"),
                needs_review.model_dump(mode="json"),
                out_of_stock.model_dump(mode="json"),
            ]
        ),
        encoding="utf-8",
    )
    response = TestClient(app).post(
        "/optimize",
        json={
            "items": [
                {"product_type": "cow_milk", "quantity": 1, "unit": "gallon"},
                {"product_type": "cream", "quantity": 1, "unit": "pint"},
                {"product_type": "eggs", "quantity": 12, "unit": "count"},
            ],
            "include_delivery": False,
            "catalog_mode": "imported_only",
        },
    )
    assert response.status_code == 200
    data = response.json()
    selected_types = {item["canonical_product"] for item in data["selected_items"]}
    unavailable_types = {item["canonical_product"] for item in data["unavailable_items"]}
    assert selected_types == {"cow_milk"}
    assert unavailable_types == {"cream", "eggs"}
    assert data["catalog_diagnostics"]["approved_loaded_count"] == 1
    assert data["catalog_diagnostics"]["optimizer_ready_count"] == 1


def test_missing_imported_match_returns_unavailable_not_demo_substitute(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.fetcher.import_candidates.STAGED_PRODUCTS_PATH",
        tmp_path / "staged_products.json",
    )
    staged = _p(
        "hf_milk_only",
        CanonicalProduct.cow_milk,
        10.0,
        1,
        PackageUnit.gallon,
        vendor="vendor_a",
        source_type="imported",
        in_stock=True,
        needs_review=False,
    )
    (tmp_path / "staged_products.json").write_text(
        f'[{staged.model_dump_json()}]',
        encoding="utf-8",
    )
    response = TestClient(app).post(
        "/optimize",
        json={
            "items": [
                {"product_type": "cow_milk", "quantity": 1, "unit": "gallon"},
                {"product_type": "cream", "quantity": 1, "unit": "pint"},
            ],
            "include_delivery": False,
            "catalog_mode": "imported_only",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["selected_items"]) == 1
    assert data["selected_items"][0]["canonical_product"] == "cow_milk"
    unavailable = data["unavailable_items"]
    assert len(unavailable) == 1
    assert unavailable[0]["canonical_product"] == "cream"
    assert unavailable[0]["requested_quantity"] == 1.0
    assert unavailable[0]["unit"] == "pint"
    assert not _vendor_names_from_response(data) & DEMO_VENDOR_NAMES


def test_legacy_use_imported_products_flag_maps_to_imported_plus_demo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.fetcher.import_candidates.STAGED_PRODUCTS_PATH",
        tmp_path / "staged_products.json",
    )
    response = TestClient(app).post(
        "/optimize",
        json=_default_frontend_payload(
            catalog_mode="imported_only",
            use_imported_products=True,
        ),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["selected_items"]
    assert _vendor_names_from_response(data) & DEMO_VENDOR_NAMES


def test_service_unavailable_reason_is_out_of_stock_when_all_matches_oos(monkeypatch):
    products = [
        _p("eggs_oos", CanonicalProduct.eggs, 12.0, 12, PackageUnit.count, in_stock=False),
    ]
    result = _service_optimize(
        monkeypatch,
        products,
        [_vendor()],
        [OptimizeItem(canonical_product=CanonicalProduct.eggs, quantity=36, unit=PackageUnit.count)],
    )

    assert result.unavailable_items[0].reason == "all_matches_out_of_stock"


def test_service_unavailable_reason_is_no_unit_mapping_for_volume_cheese(monkeypatch):
    products = [
        _p("cheese_pint", CanonicalProduct.cheese, 15.0, 1, PackageUnit.pint),
    ]
    result = _service_optimize(
        monkeypatch,
        products,
        [_vendor()],
        [OptimizeItem(canonical_product=CanonicalProduct.cheese, quantity=2, unit=PackageUnit.lb)],
    )

    assert result.unavailable_items[0].reason == "no_unit_mapping"


def test_service_unavailable_reason_uses_overbuy_only_for_unit_compatible_stock(monkeypatch):
    products = [
        _p("butter_2lb", CanonicalProduct.butter, 22.0, 2, PackageUnit.lb),
    ]
    result = _service_optimize(
        monkeypatch,
        products,
        [_vendor()],
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
        overbuy=0,
    )

    assert result.unavailable_items[0].reason == "no_valid_product_within_overbuy_limit"


def test_butter_glass_only_selects_in_stock_glass_variant(monkeypatch):
    products = [
        _p("butter_plastic", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb, packaging="plastic"),
        _p("butter_glass", CanonicalProduct.butter, 12.0, 1, PackageUnit.lb, packaging="glass"),
    ]

    default = _service_optimize(
        monkeypatch,
        products,
        [_vendor()],
        [OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb)],
    )
    assert default.selected_items[0].packages[0].product_id == "butter_plastic"

    glass = _service_optimize(
        monkeypatch,
        products,
        [_vendor()],
        [
            OptimizeItem(
                canonical_product=CanonicalProduct.butter,
                quantity=1,
                unit=PackageUnit.lb,
                packaging_preference="glass_only",
            )
        ],
    )
    assert glass.selected_items[0].packages[0].product_id == "butter_glass"


def test_requested_packaging_unavailable_is_not_reported_as_overbuy(monkeypatch):
    products = [
        _p("butter_plastic", CanonicalProduct.butter, 9.0, 1, PackageUnit.lb, packaging="plastic"),
    ]
    result = _service_optimize(
        monkeypatch,
        products,
        [_vendor()],
        [
            OptimizeItem(
                canonical_product=CanonicalProduct.butter,
                quantity=1,
                unit=PackageUnit.lb,
                packaging_preference="glass_only",
            )
        ],
    )

    assert result.unavailable_items[0].reason == "requested_packaging_unavailable"


def test_unsatisfiable_max_vendors_is_dropped_with_a_warning():
    products = [
        _p("milk_v1", CanonicalProduct.cow_milk, 5.0, 1, PackageUnit.gallon, vendor="v1"),
        _p("butter_v2", CanonicalProduct.butter, 5.0, 1, PackageUnit.lb, vendor="v2"),
    ]
    res = _optimize(
        [
            OptimizeItem(canonical_product=CanonicalProduct.cow_milk, quantity=1, unit=PackageUnit.gallon),
            OptimizeItem(canonical_product=CanonicalProduct.butter, quantity=1, unit=PackageUnit.lb),
        ],
        products,
        [_vendor(vid="v1"), _vendor(vid="v2")],
        max_vendors=1,
    )
    assert {item.vendor_id for item in res.selected_items} == {"v1", "v2"}
    assert any("No valid cart could satisfy max_vendors=1" in warning for warning in res.warnings)
