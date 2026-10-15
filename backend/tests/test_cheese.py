from app.models import CanonicalProduct, PackageUnit, Product, Vendor
from app.normalizer import OTHER_CHEESE, cheese_kind, normalize_product
from app.optimizer import optimize
from app.schemas import OptimizeItem, OptimizeRequest


def _cheese(id: str, name: str, price: float, unsalted: bool = True, unit=PackageUnit.lb) -> Product:
    return Product(id=id, vendor_id="farm", product_name=name, canonical_product=CanonicalProduct.cheese,
                   price=price, package_quantity=1, package_unit=unit, is_unsalted=unsalted)


FARM = Vendor(id="farm", name="Farm", pickup_address="1 Farm Rd", pickup_dropoff_available=True)


def test_kind_comes_from_the_listing_name():
    assert cheese_kind(_cheese("a", "1 lb. Unsalted Dutch Cheddar Cheese", 16)) == "Cheddar"
    assert cheese_kind(_cheese("b", "Goat Cottage Cheese, Unsalted, Pt. (Glass)", 13.5)) == "Cottage"
    assert cheese_kind(_cheese("c", "Unsalted Baby Swiss (1lb)", 12.85)) == "Swiss"
    assert cheese_kind(_cheese("d", "No-Salt Cheese", 10)) == OTHER_CHEESE
    butter = Product(id="e", vendor_id="farm", product_name="Cheddar-colored butter",
                     canonical_product=CanonicalProduct.butter, price=9, package_quantity=1, package_unit=PackageUnit.lb)
    assert cheese_kind(butter) is None


def test_cheese_by_the_pint_is_priced_per_pound():
    cottage = normalize_product(_cheese("a", "Cottage Cheese, 1 Pt.", 6.6, unit=PackageUnit.pint))

    assert (cottage.excluded, cottage.unit_price, cottage.variety) == (False, 6.6, "Cottage")


def _cart(products: list[Product], item: OptimizeItem):
    return optimize(OptimizeRequest(items=[item]), products, [FARM])


def test_a_chosen_kind_skips_cheaper_cheese_of_another_kind():
    products = [_cheese("swiss", "Baby Swiss", 9.0), _cheese("cheddar", "Cheddar", 12.0)]

    cart = _cart(products, OptimizeItem(canonical_product="cheese", quantity=1, unit="lb", variety="Cheddar"))

    assert cart.selected_items[0].packages[0].product_id == "cheddar"


def test_unsalted_cheese_skips_salted_cheese():
    products = [_cheese("salted", "Cheddar", 8.0, unsalted=False), _cheese("unsalted", "Unsalted Cheddar", 12.0)]

    cart = _cart(products, OptimizeItem(canonical_product="cheese", quantity=1, unit="lb", salt_preference="unsalted"))

    assert cart.selected_items[0].packages[0].product_id == "unsalted"
