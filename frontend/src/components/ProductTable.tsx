import type { NormalizedProduct, Vendor } from "../types";

interface Props {
  products: NormalizedProduct[];
  vendors: Vendor[];
  excludedVendorIds: string[];
  excludedProductIds: string[];
  onExcludeVendor: (vendorId: string) => void;
  onIncludeVendor: (vendorId: string) => void;
  onExcludeProduct: (productId: string) => void;
  onIncludeProduct: (productId: string) => void;
}

function vendorName(vendors: Vendor[], id: string): string {
  return vendors.find((v) => v.id === id)?.name ?? id;
}

// Group/sort by vendor -> product_type -> name -> package size (desc) -> packaging.
// Uses only known values; never surfaces "unknown" placeholders.
function sortProducts(products: NormalizedProduct[], vendors: Vendor[]): NormalizedProduct[] {
  return [...products].sort((a, b) => {
    const vendor =
      vendorName(vendors, a.vendor_id).localeCompare(vendorName(vendors, b.vendor_id));
    if (vendor !== 0) return vendor;
    const type = a.canonical_product.localeCompare(b.canonical_product);
    if (type !== 0) return type;
    const nameA = a.display_name ?? a.product_name;
    const nameB = b.display_name ?? b.product_name;
    const name = nameA.localeCompare(nameB);
    if (name !== 0) return name;
    const sizeA = a.normalized_quantity ?? a.package_quantity ?? 0;
    const sizeB = b.normalized_quantity ?? b.package_quantity ?? 0;
    if (sizeA !== sizeB) return sizeB - sizeA; // larger package first
    return (a.packaging ?? "").localeCompare(b.packaging ?? "");
  });
}

export default function ProductTable({
  products,
  vendors,
  excludedVendorIds,
  excludedProductIds,
  onExcludeVendor,
  onIncludeVendor,
  onExcludeProduct,
  onIncludeProduct,
}: Props) {
  return (
    <div className="card">
      <h2>Product Comparison</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Vendor</th>
              <th>Product</th>
              <th>Category</th>
              <th>Package</th>
              <th>Price</th>
              <th>Unit Price</th>
              <th>Stock</th>
              <th>Bulk</th>
              <th>Delivery</th>
              <th>Details</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {sortProducts(products, vendors).map((p) => {
              const vendorExcluded = excludedVendorIds.includes(p.vendor_id);
              const productExcluded = excludedProductIds.includes(p.id);
              const uiExcluded = vendorExcluded || productExcluded;
              return (
              <tr key={p.id} className={p.excluded || uiExcluded ? "row-excluded" : ""}>
                <td>
                  {vendorName(vendors, p.vendor_id)}
                  <div>
                    {vendorExcluded ? (
                      <button
                        type="button"
                        className="inline-link-button"
                        onClick={() => onIncludeVendor(p.vendor_id)}
                      >
                        Re-include farm
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="inline-link-button"
                        onClick={() => onExcludeVendor(p.vendor_id)}
                      >
                        Exclude farm
                      </button>
                    )}
                  </div>
                </td>
                <td title={p.raw_text || undefined}>{p.display_name ?? p.product_name}</td>
                <td>{p.canonical_product}</td>
                <td>
                  {p.package_quantity} {p.package_unit}
                </td>
                <td>${p.price.toFixed(2)}</td>
                <td>
                  {p.unit_price != null
                    ? `$${p.unit_price.toFixed(2)}/${p.normalized_unit}`
                    : "-"}
                </td>
                <td>{p.in_stock ? "In stock" : "Out"}</td>
                <td>{p.bulk_deal ? "bulk" : ""}</td>
                <td>{p.delivery_available ? "Yes" : "Pickup"}</td>
                <td>
                  {/* Only order-relevant/source-exposed tags; no "unknown" noise. */}
                  {p.display_tags.length
                    ? p.display_tags.map((tag) => (
                        <span key={tag} className="tag tag-ok">
                          {tag}
                        </span>
                      ))
                    : "-"}
                  {p.container_returnable &&
                    ` (+$${p.container_deposit.toFixed(2)} deposit)`}
                </td>
                <td>
                  {productExcluded && (
                    <span className="tag tag-excluded">excluded by filter</span>
                  )}
                  {vendorExcluded && (
                    <span className="tag tag-excluded">farm excluded</span>
                  )}
                  {!uiExcluded && p.excluded ? (
                    <span className="tag tag-excluded">{p.exclusion_reason}</span>
                  ) : null}
                  {!uiExcluded && !p.excluded ? (
                    <span className="tag tag-ok">ok</span>
                  ) : null}
                </td>
                <td>
                  {productExcluded ? (
                    <button
                      type="button"
                      className="secondary-button table-action-button"
                      onClick={() => onIncludeProduct(p.id)}
                    >
                      Re-include product
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="secondary-button table-action-button"
                      onClick={() => onExcludeProduct(p.id)}
                    >
                      Exclude product
                    </button>
                  )}
                </td>
              </tr>
            )})}
          </tbody>
        </table>
      </div>
    </div>
  );
}
