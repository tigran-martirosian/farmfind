from app.fetcher.uddermilk import build_uddermilk_captured_page, parse_category_variants, product_type


def _row(package_id: str, label: str, price: str, available: bool = True) -> str:
    quantity = "" if available else '<span style="font-size:9px;">( Not Available )</span>'
    return f"""
        <tr>
            <td class="product-package">
                <div class="form-group field-temporyorder-{package_id}-packageid required">
                    <input type="hidden" name="Temporyorder[{package_id}][packageid]" value="{package_id}">
                    <div class="help-block"></div>
                </div>
                <span class="category-index-badge">1</span>
                {label}
            </td>
            <td>
                $ {price}
            </td>
            <td><table><tbody><tr><td>{quantity}</td></tr></tbody></table></td>
            <td><a class="btn btn-sm btn-primary" href="/login">Login to buy</a></td>
        </tr>"""


def _product(product_id: str, title: str, rows: str) -> str:
    return f"""
        <div class="ct-blog-item-title text-center">
            <h3 id="product-id-{product_id}"><span class="category-index-badge">1</span> {title} </h3>
        </div>
        <div class="table-responsive"><table class="table product-order">
            <thead><tr><th class="product-package">Package</th><th class="product-price">Price</th></tr></thead>
            <tbody>
                <tr><td><img src="/upload/x.png"></td><td colspan="3"><p>About this product.</p></td></tr>
                {rows}
            </tbody>
        </table></div>"""


PAGE = (
    "<html><body>"
    + _product("1", "-* ( Raw) Cow Milk", _row("660", "Glass half gal (Guernsey) A2A2 Milk", "9.00") + _row("700", "Gallon A2A2 Milk", "11.50", available=False))
    + _product("2", "( Raw ) Grass fed butter ( A2 A2)", _row("801", "- Salted ( PINT) (Jersy Milk )", "22.00"))
    + _product("3", "( Raw) Sheep Cheese", _row("902", "Sheep Feta Salted (1/2Ib)", "16.00"))
    + _product("4", "(Raw) Drinkable Yogurt ( Guernsey Milk Glass)", _row("903", "Strawberry Drinkable Yogurt (Pint )", "5.00"))
    + "</body></html>"
)


def test_category_page_yields_one_offer_per_package_with_stock_and_package_id():
    variants = parse_category_variants(PAGE)

    milk = [variant for variant in variants if variant.product_title == "( Raw) Cow Milk"]
    assert [(variant.variant_id, variant.price, variant.stock_status) for variant in milk] == [
        ("660", 9.0, "in_stock"),
        ("700", 11.5, "out_of_stock"),
    ]
    assert milk[0].inferred_product_type == "cow_milk"
    assert (milk[0].inferred_package_size, milk[0].inferred_unit) == (0.5, "gallon")


def test_product_name_decides_the_type_over_the_label():
    butter = next(variant for variant in parse_category_variants(PAGE) if variant.variant_id == "801")

    assert butter.inferred_product_type == "butter"
    assert (butter.inferred_package_size, butter.inferred_unit) == (1.0, "pint")


def test_pounds_written_with_a_capital_i_are_read_as_pounds():
    feta = next(variant for variant in parse_category_variants(PAGE) if variant.variant_id == "902")

    assert feta.inferred_product_type == "cheese"
    assert (feta.inferred_package_size, feta.inferred_unit) == (0.5, "lb")


def test_products_outside_the_six_types_are_kept_untyped():
    assert product_type("(Raw) Drinkable Yogurt ( Guernsey Milk Glass)", "Strawberry (Pint)") is None
    assert product_type("100% Guernsey Eggnog", "MAPLE (Pint)") is None
    assert product_type("( Raw) Cow Milk", "Buffalo Milk (Quart)") is None
    assert product_type("Organic Pasture Eggs", "Eggs ( Dozen)") == "eggs"


def test_captured_page_reports_products_and_out_of_stock_offers():
    captured = build_uddermilk_captured_page(PAGE, "udder_milk", "cow_dairy", "https://uddermilk.test/category/cow-dairy")

    assert captured.page_kind == "category_listing"
    assert captured.coverage_report["product_cards_found"] == 4
    assert captured.coverage_report["out_of_stock_offers"] == 1
    assert captured.warnings == []


def test_page_without_product_blocks_warns():
    captured = build_uddermilk_captured_page("<html><body>Shop</body></html>", "udder_milk", "eggs", "https://uddermilk.test/category/eggs")

    assert captured.variants == []
    assert captured.warnings == ["No Udder Milk product blocks found on the page."]
