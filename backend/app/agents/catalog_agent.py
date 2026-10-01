"""CatalogAgent: which vendors can supply the requested items, and at what price."""
from __future__ import annotations

from dataclasses import dataclass

from ..data_loader import load_catalog
from ..models import Product, Vendor
from ..normalizer import normalize_product
from ..schemas import CatalogMode
from .bus import MessageBus
from .messages import CatalogQuery, CatalogResult, Offer, VendorSupply


@dataclass(frozen=True)
class Catalog:
    mode: CatalogMode
    products: list[Product]
    vendors: list[Vendor]


def load_active_catalog(mode: CatalogMode | None = None) -> Catalog:
    """The approved-imports catalog, or the demo samples while nothing has been
    approved yet. An explicit mode is used as given."""
    if mode is None:
        products, vendors = load_catalog("imported_only")
        if products:
            return Catalog("imported_only", products, vendors)
        mode = "demo_only"
    products, vendors = load_catalog(mode)
    return Catalog(mode, products, vendors)


class CatalogAgent:
    name = "catalog_agent"
    action = "find_suppliers"
    input_model = CatalogQuery

    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def handle(self, query: CatalogQuery, bus: MessageBus) -> CatalogResult:
        wanted = list(dict.fromkeys(item.product for item in query.items))
        vendors = []
        for vendor in self.catalog.vendors:
            offers = {product.value: self._offers(vendor.id, product) for product in wanted}
            if not any(offers.values()):
                continue
            vendors.append(
                VendorSupply(
                    vendor_id=vendor.id,
                    vendor_name=vendor.name,
                    supplies=[
                        product
                        for product in wanted
                        if any(offer.in_stock for offer in offers[product.value])
                    ],
                    offers=offers,
                )
            )
        supplied = {product for vendor in vendors for product in vendor.supplies}
        return CatalogResult(
            catalog_mode=self.catalog.mode,
            vendors=vendors,
            unsupplied=[product for product in wanted if product not in supplied],
        )

    def _offers(self, vendor_id: str, product) -> list[Offer]:
        offers = []
        for listing in self.catalog.products:
            if listing.vendor_id != vendor_id or listing.canonical_product != product:
                continue
            normalized = normalize_product(listing)
            if normalized.normalized_quantity is None:
                continue
            offers.append(
                Offer(
                    product_id=listing.id,
                    name=normalized.display_name or listing.product_name,
                    package=f"{listing.package_quantity:g} {listing.package_unit.value}",
                    price=listing.price,
                    unit_price=normalized.unit_price,
                    unit=normalized.normalized_unit,
                    in_stock=listing.in_stock,
                )
            )
        return sorted(offers, key=lambda offer: (not offer.in_stock, offer.unit_price or 0.0))
