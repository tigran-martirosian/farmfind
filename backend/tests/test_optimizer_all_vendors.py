"""Approved products from all vendors are optimizer-eligible."""
from __future__ import annotations

from app.data_loader import _default_imported_vendor, _imported_vendors_for
from app.models import CanonicalProduct, PackageUnit, Product


def _staged(vendor_id, name, price):
    return Product(
        id=f"{vendor_id}_{name}",
        vendor_id=vendor_id,
        product_name=name,
        canonical_product=CanonicalProduct.cow_milk,
        price=price,
        package_quantity=1.0,
        package_unit=PackageUnit.gallon,
        in_stock=True,
        source_type="imported",
        needs_review=False,
    )


def test_missing_vendor_is_synthesized_with_usable_fulfillment():
    vendor = _default_imported_vendor("vendor_b")
    assert vendor.id == "vendor_b"
    assert vendor.name  # human-readable, not empty
    # At least one fulfillment method must be available so products are eligible.
    assert vendor.pickup_dropoff_available or vendor.ups_shipping_available


def test_all_approved_vendors_get_records(monkeypatch):
    vendor_ids = {"vendor_a", "vendor_b", "vendor_c", "vendor_d"}
    vendors = _imported_vendors_for(vendor_ids)
    assert {v.id for v in vendors} == vendor_ids


def test_optimizer_can_select_non_vendor_a_when_cheaper(monkeypatch):
    from app import data_loader

    products = [
        _staged("vendor_a", "Milk", 12.0),
        _staged("vendor_c", "Milk", 6.0),
    ]
    monkeypatch.setattr(data_loader, "load_approved_imported_products", lambda: products)

    prods, vendors = data_loader.load_catalog("imported_only")
    vendor_ids = {v.id for v in vendors}
    assert "vendor_c" in vendor_ids
    assert "vendor_a" in vendor_ids
    # Both vendors' milk is available to the optimizer inventory.
    assert {p.vendor_id for p in prods} == {"vendor_a", "vendor_c"}
