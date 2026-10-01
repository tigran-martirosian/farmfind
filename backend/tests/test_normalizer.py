"""Tests for deterministic normalization (conversions + exclusions)."""
import math

from app.models import CanonicalProduct, PackageUnit, Product
from app.normalizer import normalize_product, normalize_quantity


def _product(**overrides) -> Product:
    base = dict(
        id="x",
        vendor_id="v",
        product_name="test",
        canonical_product=CanonicalProduct.eggs,
        price=10.0,
        package_quantity=1,
        package_unit=PackageUnit.dozen,
    )
    base.update(overrides)
    return Product(**base)


def test_dozen_eggs_to_12_count():
    assert normalize_quantity(CanonicalProduct.eggs, 1, PackageUnit.dozen) == 12


def test_15_dozen_case_to_180_count():
    assert normalize_quantity(CanonicalProduct.eggs, 15, PackageUnit.dozen) == 180


def test_30_count_tray_to_30_count():
    assert normalize_quantity(CanonicalProduct.eggs, 30, PackageUnit.count) == 30


def test_8oz_butter_to_half_lb():
    assert normalize_quantity(CanonicalProduct.butter, 8, PackageUnit.oz) == 0.5


def test_8oz_cheese_to_half_lb():
    assert normalize_quantity(CanonicalProduct.cheese, 8, PackageUnit.oz) == 0.5


def test_quart_milk_to_quarter_gallon():
    assert normalize_quantity(CanonicalProduct.cow_milk, 1, PackageUnit.quart) == 0.25


def test_half_gallon_milk_to_half_gallon():
    got = normalize_quantity(CanonicalProduct.cow_milk, 1, PackageUnit.half_gallon)
    assert got == 0.5


def test_quart_cream_to_two_pints():
    assert normalize_quantity(CanonicalProduct.cream, 1, PackageUnit.quart) == 2


def test_unit_price_computation():
    # 1 dozen eggs at $11 -> 12 count -> ~0.9167 / egg
    p = _product(canonical_product=CanonicalProduct.eggs, price=11.0,
                 package_quantity=1, package_unit=PackageUnit.dozen)
    norm = normalize_product(p)
    assert norm.normalized_quantity == 12
    assert math.isclose(norm.unit_price, 11 / 12, rel_tol=1e-4)
    assert norm.excluded is False


def test_salted_butter_is_not_excluded():
    p = _product(canonical_product=CanonicalProduct.butter, is_unsalted=False,
                 package_quantity=1, package_unit=PackageUnit.lb)
    norm = normalize_product(p)
    assert norm.excluded is False


def test_cheese_is_not_excluded_for_salt():
    p = _product(canonical_product=CanonicalProduct.cheese, is_unsalted=False,
                 package_quantity=1, package_unit=PackageUnit.lb)
    norm = normalize_product(p)
    assert norm.excluded is False


def test_out_of_stock_excluded_but_still_normalized():
    p = _product(canonical_product=CanonicalProduct.cow_milk, in_stock=False,
                 package_quantity=1, package_unit=PackageUnit.gallon)
    norm = normalize_product(p)
    assert norm.excluded is True
    assert norm.exclusion_reason == "out_of_stock"
    assert norm.normalized_quantity == 1  # display value still available
