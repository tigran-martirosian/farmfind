"""Load vendors and products from local JSON files (swap for a DB later)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from .models import Product, Vendor

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CatalogMode = Literal["imported_only", "demo_only", "imported_plus_demo"]

DEMO_PRODUCTS_FILE = "products.json"
DEMO_VENDORS_FILE = "vendors.json"
IMPORTED_VENDORS_FILE = "imported_vendors.json"


def _load(filename: str) -> list[dict]:
    with (DATA_DIR / filename).open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_demo_vendors() -> list[Vendor]:
    """Sample/demo vendors used for tests and explicit demo catalog mode."""
    return [Vendor(**row) for row in _load(DEMO_VENDORS_FILE)]


@lru_cache(maxsize=1)
def load_demo_products() -> list[Product]:
    """Sample/demo products used for tests and explicit demo catalog mode."""
    return [Product(**row) for row in _load(DEMO_PRODUCTS_FILE)]


@lru_cache(maxsize=1)
def load_imported_vendors() -> list[Vendor]:
    """Real vendor fulfillment metadata for approved imported products."""
    path = DATA_DIR / IMPORTED_VENDORS_FILE
    if not path.exists():
        return []
    return [Vendor(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def _default_imported_vendor(vendor_id: str) -> Vendor:
    """Minimal vendor record for an approved vendor missing from
    imported_vendors.json, so its approved products are optimizer-eligible.

    Fulfillment fees are conservative defaults (clearly estimated) until real
    vendor fulfillment metadata is configured."""
    name = vendor_id.replace("_", " ").title()
    website: str | None = None
    try:
        from .fetcher.storage import vendor_account_config

        config = vendor_account_config(vendor_id)
        if config is not None:
            name = getattr(config, "vendor_name", None) or name
            website = getattr(config, "login_url", None) or getattr(config, "account_url", None)
    except Exception:
        pass
    return Vendor(
        id=vendor_id,
        name=name,
        pickup_address="",
        pickup_dropoff_available=True,
        ups_shipping_available=True,
        source_type="imported",
        website_url=website,
        login_required=True,
        supports_authenticated_fetch=True,
        notes="Auto-generated vendor record; fulfillment fees are estimated/default until configured.",
    )


def _imported_vendors_for(product_vendor_ids: set[str]) -> list[Vendor]:
    """Every approved-product vendor gets a Vendor record (synthesized if the
    imported_vendors.json catalog does not have one yet)."""
    by_id = {vendor.id: vendor for vendor in load_imported_vendors()}
    return [by_id.get(vendor_id) or _default_imported_vendor(vendor_id) for vendor_id in sorted(product_vendor_ids)]


def load_approved_imported_products() -> list[Product]:
    """Approved staged imports eligible for the optimizer."""
    from .fetcher.import_candidates import load_staged_products

    return [
        product
        for product in load_staged_products()
        if not product.needs_review and product.in_stock
    ]


def load_catalog(catalog_mode: CatalogMode) -> tuple[list[Product], list[Vendor]]:
    """Resolve product and vendor catalogs for the requested source mode."""
    demo_products = load_demo_products()
    demo_vendors = load_demo_vendors()
    imported_products = load_approved_imported_products()
    imported_vendors = load_imported_vendors()

    if catalog_mode == "demo_only":
        return demo_products, demo_vendors

    if catalog_mode == "imported_only":
        vendor_ids = {product.vendor_id for product in imported_products}
        return imported_products, _imported_vendors_for(vendor_ids)

    products = imported_products + demo_products
    vendor_map = {vendor.id: vendor for vendor in demo_vendors}
    for vendor in _imported_vendors_for({product.vendor_id for product in imported_products}):
        vendor_map.setdefault(vendor.id, vendor)
    return products, list(vendor_map.values())


# Short names for the demo catalog loaders.
def load_vendors() -> list[Vendor]:
    return load_demo_vendors()


def load_products() -> list[Product]:
    return load_demo_products()
