from datetime import datetime, timezone

import pytest

from app.fetcher import import_candidates, pull
from app.fetcher.import_candidates import load_staged_products
from app.fetcher.models import FetchMetadata, VendorPageTarget
from app.fetcher.uddermilk import build_uddermilk_captured_page
from tests.test_uddermilk import _product, _row


@pytest.fixture(autouse=True)
def private_files(tmp_path, monkeypatch):
    monkeypatch.setattr(import_candidates, "IMPORT_CANDIDATES_DIR", tmp_path / "import_candidates")
    monkeypatch.setattr(import_candidates, "STAGED_PRODUCTS_PATH", tmp_path / "staged_products.json")
    monkeypatch.setattr(pull, "PULLS_PATH", tmp_path / "pulls.json")
    monkeypatch.setattr(pull, "PAGE_PAUSE_SECONDS", 0)


def _target(page_id: str) -> VendorPageTarget:
    return VendorPageTarget(vendor_id="farm_u", page_id=page_id, url=f"https://farm-u.test/category/{page_id}")


def _page(tmp_path, page_id: str, html: str) -> FetchMetadata:
    artifact_dir = tmp_path / "fetches" / page_id
    captured = build_uddermilk_captured_page(html, "farm_u", page_id, f"https://farm-u.test/category/{page_id}", artifact_dir=artifact_dir)
    return FetchMetadata(
        vendor_id="farm_u",
        page_id=page_id,
        url=captured.source_url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        requires_login=False,
        status="success",
        variant_count=len(captured.variants),
        variant_snapshots_path=str(artifact_dir / "variant_snapshots.json"),
    )


def _milk_page(price: str, available: bool = True) -> str:
    return "<html>" + _product("1", "( Raw) Cow Milk", _row("660", "Half gallon A2A2 Milk", price, available)) + "</html>"


def _approve_everything_pending():
    for candidate in import_candidates.load_import_candidates("farm_u"):
        import_candidates.approve_import_candidate(candidate.import_id)


def test_first_pull_leaves_new_products_for_review(tmp_path):
    record = pull.pull_vendor("farm_u", [_target("cow")], lambda target: _page(tmp_path, "cow", _milk_page("6.50")))

    assert record["result"] == "ok"
    assert record["last_good_pull_at"] == record["last_attempt_at"]
    assert (record["updated"], record["new"]) == (0, 1)
    assert load_staged_products() == []


def test_known_product_gets_new_price_and_stock_without_review(tmp_path):
    pull.pull_vendor("farm_u", [_target("cow")], lambda target: _page(tmp_path, "cow", _milk_page("6.50")))
    _approve_everything_pending()

    record = pull.pull_vendor("farm_u", [_target("cow")], lambda target: _page(tmp_path, "cow", _milk_page("7.00", available=False)))

    assert (record["updated"], record["price_changes"], record["new"]) == (1, 1, 0)
    [product] = load_staged_products()
    assert (product.price, product.in_stock) == (7.0, False)
    pending = [c for c in import_candidates.load_import_candidates("farm_u") if c.review_status == "pending"]
    assert pending == []


def test_product_listed_on_two_pages_is_one_candidate(tmp_path):
    pages = {"cow": _milk_page("6.50"), "guernsey": _milk_page("6.50").replace("( Raw) Cow Milk", "( Raw) Milk")}

    record = pull.pull_vendor(
        "farm_u", [_target("cow"), _target("guernsey")], lambda target: _page(tmp_path, target.page_id, pages[target.page_id])
    )

    assert record["new"] == 1


def test_signed_out_page_keeps_the_last_good_pull_and_names_the_fix(tmp_path):
    first = pull.pull_vendor("farm_u", [_target("cow")], lambda target: _page(tmp_path, "cow", _milk_page("6.50")))
    pull.write_json_atomic(pull.PULLS_PATH, {"farm_u": first})

    def signed_out(target):
        return FetchMetadata(
            vendor_id="farm_u", page_id="cow", url=target.url, fetched_at=datetime.now(timezone.utc).isoformat(),
            requires_login=True, status="action_required", action_required="login_required",
        )

    record = pull.pull_vendor("farm_u", [_target("cow")], signed_out)

    assert record["result"] == "signed_out"
    assert record["last_good_pull_at"] == first["last_good_pull_at"]
    assert "python -m app.fetcher.login_by_hand --vendor-id farm_u" in pull.describe("farm_u", record)


def test_known_product_the_farm_stopped_listing_goes_out_of_stock(tmp_path):
    pull.pull_vendor("farm_u", [_target("cow")], lambda target: _page(tmp_path, "cow", _milk_page("6.50")))
    _approve_everything_pending()
    other = "<html>" + _product("2", "( Raw) Cream", _row("700", "Heavy Cream (Pint)", "12.00")) + "</html>"

    record = pull.pull_vendor("farm_u", [_target("cow")], lambda target: _page(tmp_path, "cow", other))

    assert record["gone"] == 1
    [product] = load_staged_products()
    assert product.in_stock is False


def test_bundle_option_never_overwrites_the_single_piece_price(tmp_path):
    single = _row("660", "Half gallon A2A2 Milk", "6.50")
    pull.pull_vendor("farm_u", [_target("cow")], lambda target: _page(tmp_path, "cow", "<html>" + _product("1", "( Raw) Cow Milk", single) + "</html>"))
    _approve_everything_pending()
    both = single + _row("661", "4 Piece Bundle of Half gallon A2A2 Milk", "24.00")

    pull.pull_vendor("farm_u", [_target("cow")], lambda target: _page(tmp_path, "cow", "<html>" + _product("1", "( Raw) Cow Milk", both) + "</html>"))

    [product] = load_staged_products()
    assert product.price == 6.5


def test_two_prices_for_one_known_product_change_nothing(tmp_path):
    pull.pull_vendor("farm_u", [_target("cow")], lambda target: _page(tmp_path, "cow", _milk_page("6.50")))
    _approve_everything_pending()
    pages = {"cow": _milk_page("6.50"), "other": _milk_page("26.00").replace('"660"', '"661"').replace("[660]", "[661]")}

    record = pull.pull_vendor(
        "farm_u", [_target("cow"), _target("other")], lambda target: _page(tmp_path, target.page_id, pages[target.page_id])
    )

    assert (record["updated"], record["unclear"]) == (0, 1)
    [product] = load_staged_products()
    assert product.price == 6.5
    assert "unclear" in pull.describe("farm_u", record)
