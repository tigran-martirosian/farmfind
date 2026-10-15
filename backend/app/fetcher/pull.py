"""Pull every farm's prices in one go.

    python -m app.fetcher.pull                        # every farm
    python -m app.fetcher.pull --vendor-id udder_milk

Reads each farm's pages in a hidden browser. Products already approved get
today's price and stock by themselves; new products wait in the import review.
A farm whose pages all read well gets today as its last good pull (pulls.json
in the farms folder, shown as last_fetched_at by /vendors). A farm that wants
a sign-in is named, with the command that fixes it.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.schemas import ImportedProductCandidate

from . import import_candidates
from .import_candidates import (
    create_import_candidates_from_variant_snapshot,
    known_product_key,
    load_staged_products,
)
from .account_connections import login_vendor_with_saved_credentials
from .credentials import get_vendor_credential_status
from .models import FetchMetadata, VendorPageTarget
from .storage import DATA_DIR, load_vendor_pages, read_json_list_resilient, vendor_account_config, write_json_atomic

PULLS_PATH = DATA_DIR / "pulls.json"
PAGE_PAUSE_SECONDS = 3  # one farm's pages are read a few seconds apart; Healthfully answers 429 when hit fast


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_pulls() -> dict[str, dict]:
    if not PULLS_PATH.exists():
        return {}
    return json.loads(PULLS_PATH.read_text(encoding="utf-8"))


def page_problem(metadata: FetchMetadata) -> str | None:
    """None when the page read well, else what went wrong in a few words."""
    if metadata.status == "success" and metadata.variant_count:
        return None
    if metadata.status == "success":
        return "no products found on the page"
    if metadata.action_required in {"login_required", "manual_login_required", "captcha_required", "pricing_locked"}:
        return "signed out"
    return metadata.error_message or metadata.classification_reason or metadata.status


def apply_pull(vendor_id: str, candidate_paths: list[Path], farm_complete: bool) -> dict[str, int]:
    """Update approved products from this pull's candidates.

    A candidate that matches an approved product updates its price and stock and
    leaves the review. Two different prices for one approved product in the same
    pull update nothing: the product is reported as unclear and its candidates
    stay in the review. When every page of the farm read well, approved products
    the farm no longer lists are marked out of stock."""
    from app.services import normalize_imported_product

    staged = load_staged_products()
    known = {known_product_key(product): index for index, product in enumerate(staged) if product.vendor_id == vendor_id}
    matches: dict[int, list[tuple[dict, object]]] = {}
    new_keys: set[tuple] = set()
    files = [(path, read_json_list_resilient(path)) for path in candidate_paths]
    for _, rows in files:
        for row in rows:
            product = normalize_imported_product(ImportedProductCandidate(**row))
            if product is None:
                continue  # not a tracked product, or incomplete: the review shows it
            key = known_product_key(product)
            if key in known:
                matches.setdefault(known[key], []).append((row, product))
            else:
                new_keys.add(key)

    counts = {"updated": 0, "price_changes": 0, "unclear": 0, "new": len(new_keys), "gone": 0}
    pulled_at = now_iso()
    for index, found in matches.items():
        if len({round(product.price, 2) for _, product in found}) > 1:
            # A bundle and its single piece can share a key: keep the offer named exactly as approved.
            name = " ".join(staged[index].product_name.lower().split())
            found = [(row, product) for row, product in found if " ".join(product.product_name.lower().split()) == name]
        if not found or len({round(product.price, 2) for _, product in found}) > 1:
            counts["unclear"] += 1
            continue
        product = found[0][1]
        current = staged[index]
        if round(current.price, 2) != round(product.price, 2):
            counts["price_changes"] += 1
        staged[index] = current.model_copy(
            update={
                "price": product.price,
                "in_stock": any(item.in_stock for _, item in found),
                "source_url": product.source_url or current.source_url,
                "imported_at": pulled_at,
            }
        )
        counts["updated"] += 1
        for row, _ in found:
            row["review_status"] = "approved"
    for path, rows in files:
        write_json_atomic(path, rows)
    if farm_complete:
        for index in set(known.values()) - set(matches):
            if staged[index].in_stock:
                staged[index] = staged[index].model_copy(update={"in_stock": False, "imported_at": pulled_at})
                counts["gone"] += 1
    write_json_atomic(import_candidates.STAGED_PRODUCTS_PATH, [product.model_dump(mode="json") for product in staged])
    return counts


def pull_vendor(vendor_id: str, targets: list[VendorPageTarget], fetch_page) -> dict:
    problems: dict[str, str] = {}
    candidate_paths: list[Path] = []
    seen_variant_ids: set[str] = set()
    for number, target in enumerate(targets):
        if number:
            time.sleep(PAGE_PAUSE_SECONDS)
        metadata = fetch_page(target)
        problem = page_problem(metadata)
        if problem is not None:
            problems[target.page_id] = problem
            continue
        snapshot = Path(metadata.variant_snapshots_path)
        candidate_paths.append(create_import_candidates_from_variant_snapshot(snapshot, seen_variant_ids))
    farm_complete = not problems
    counts = apply_pull(vendor_id, candidate_paths, farm_complete) if candidate_paths else {}
    attempt_at = now_iso()
    previous = load_pulls().get(vendor_id, {})
    if farm_complete:
        result = "ok"
    elif "signed out" in problems.values():
        result = "signed_out"
    else:
        result = "failed"
    return {
        "last_good_pull_at": attempt_at if farm_complete else previous.get("last_good_pull_at"),
        "last_attempt_at": attempt_at,
        "result": result,
        "pages_ok": len(targets) - len(problems),
        "pages_total": len(targets),
        "problems": problems,
        **counts,
    }


def signs_in_by_itself(vendor_id: str) -> bool:
    """A farm whose login has no human check and whose password is saved.
    GrazeCart shops check for a human at login, so they always go to login_by_hand."""
    config = vendor_account_config(vendor_id)
    return (
        config is not None
        and config.auth_strategy == "credential_browser_login"
        and config.platform != "grazecart"
        and get_vendor_credential_status(vendor_id).credential_status == "configured"
    )


def describe(vendor_id: str, record: dict) -> str:
    line = f"{vendor_id}: {record['pages_ok']}/{record['pages_total']} pages"
    if "updated" in record:
        line += (
            f", {record['updated']} known products updated ({record['price_changes']} new prices),"
            f" {record['new']} new for review, {record['gone']} no longer listed"
        )
        if record["unclear"]:
            line += f", {record['unclear']} unclear (two prices for one product; left as they were, see the review)"
    for page_id, problem in record["problems"].items():
        line += f"\n  {page_id}: {problem}"
    if record["result"] == "signed_out":
        line += f"\n  sign in: python -m app.fetcher.login_by_hand --vendor-id {vendor_id}"
    return line


def main() -> None:
    from .vendor_fetcher import fetch_vendor_page

    parser = argparse.ArgumentParser(description="Pull every farm's prices and stock.")
    parser.add_argument("--vendor-id", action="append", help="Farm to pull (repeatable). Default: every farm.")
    args = parser.parse_args()

    by_vendor: dict[str, list[VendorPageTarget]] = {}
    for target in load_vendor_pages():
        if target.has_products and (not args.vendor_id or target.vendor_id in args.vendor_id):
            by_vendor.setdefault(target.vendor_id, []).append(target)
    unknown = set(args.vendor_id or []) - set(by_vendor)
    if unknown:
        raise SystemExit(f"No pages are configured for: {', '.join(sorted(unknown))}")

    def fetch(target: VendorPageTarget) -> FetchMetadata:
        return fetch_vendor_page(target, force_refresh=True)

    for vendor_id, targets in by_vendor.items():
        record = pull_vendor(vendor_id, targets, fetch)
        if record["result"] == "signed_out" and signs_in_by_itself(vendor_id):
            print(f"{vendor_id}: signed out, signing in with the saved password...")
            if login_vendor_with_saved_credentials(vendor_id).connection_status == "connected":
                record = pull_vendor(vendor_id, targets, fetch)
        pulls = load_pulls()
        pulls[vendor_id] = record
        write_json_atomic(PULLS_PATH, pulls)
        print(describe(vendor_id, record))


if __name__ == "__main__":
    main()
